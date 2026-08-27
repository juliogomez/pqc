# IPsec / IKEv2 on IOS XE

> **Pre-req:** this doc assumes you have reviewed the container labs on
> [IPsec key exchange](../../learn/ipsec/key-exchange/README.md) and
> [IPsec authentication](../../learn/ipsec/authentication/README.md). Same progression,
> same RFCs, now with Cisco CLI: classical baseline, then PPK, then native ML-KEM
hybrid, then a phased migration across a hub and two spokes.

## Network design

R2 acts as a Layer 3 transit router. It just forwards packets between R1 and R3. The IPsec
tunnel runs end-to-end between R1 and R3, traversing R2 as an intermediate hop. This
emulates a real WAN where crypto endpoints are not directly connected.

## Exercise 1: Classical IKEv2 baseline

First, set up the underlay. Create VLANs and SVIs for L3 routing.

**On R1:**

```
vlan 12
 name R1-to-R2

interface TwoGigabitEthernet0/0/0
 switchport mode access
 switchport access vlan 12

interface Vlan12
 ip address 10.0.12.1 255.255.255.252
 no shutdown

ip route 10.0.23.0 255.255.255.252 10.0.12.2
```

**On R2 (transit):**

```
vlan 12
 name R1-to-R2
vlan 23
 name R2-to-R3

interface TwoGigabitEthernet0/0/0
 switchport mode access
 switchport access vlan 12

interface TwoGigabitEthernet0/0/1
 switchport mode access
 switchport access vlan 23

interface Vlan12
 ip address 10.0.12.2 255.255.255.252
 no shutdown

interface Vlan23
 ip address 10.0.23.1 255.255.255.252
 no shutdown

ip routing
```

**On R3:**

```
vlan 23
 name R2-to-R3

interface TwoGigabitEthernet0/0/0
 switchport mode access
 switchport access vlan 23

interface Vlan23
 ip address 10.0.23.2 255.255.255.252
 no shutdown

ip route 10.0.12.0 255.255.255.252 10.0.23.1
```

Verify end-to-end reachability:

```
R1# traceroute 10.0.23.2
  1 10.0.12.2 0 msec 0 msec 0 msec
  2 10.0.23.2 4 msec 0 msec *
```

Two hops. R2 is forwarding. Now build the tunnel.

**On R1:**

```
! --- Proposal: what algorithms to use for the IKE negotiation itself ---
crypto ikev2 proposal CLASSICAL-PROPOSAL
 encryption aes-cbc-256
 integrity sha512
 group 20                                   ! DH group for key exchange (ECDH-384)

! --- Policy: which proposal(s) to offer, in priority order ---
crypto ikev2 policy CLASSICAL-POLICY
 proposal CLASSICAL-PROPOSAL

! --- Keyring: stores credentials to authenticate the peer ---
! --- (this is the same service ML-DSA will provide once available, see below) ---
crypto ikev2 keyring CLASSICAL-KEYRING
 peer R3
  address 10.0.23.2
  pre-shared-key C1sco12345psk              ! proves identity, NOT used as encryption key

! --- Profile: matches the remote peer and selects the keyring for authentication ---
crypto ikev2 profile CLASSICAL-PROFILE
 match identity remote address 10.0.23.2 255.255.255.255
 authentication remote pre-share
 authentication local pre-share
 keyring local CLASSICAL-KEYRING

! --- Transform-set: what algorithms to use for the actual data encryption (ESP) ---
crypto ipsec transform-set CLASSICAL-TS esp-gcm 256
 mode tunnel

! --- IPsec profile: binds transform-set + ikev2 profile, applied to the tunnel ---
crypto ipsec profile CLASSICAL-IPSEC
 set transform-set CLASSICAL-TS
 set ikev2-profile CLASSICAL-PROFILE

interface Tunnel0
 ip address 192.168.100.1 255.255.255.0
 tunnel source Vlan12
 tunnel destination 10.0.23.2
 tunnel mode ipsec ipv4
 tunnel protection ipsec profile CLASSICAL-IPSEC
```

**On R3:** Same but mirrored (peer address 10.0.12.1, tunnel IP 192.168.100.2, tunnel
destination 10.0.12.1).

**Verify:**

```
R1# show crypto ikev2 sa
Tunnel-id Local                 Remote                fvrf/ivrf            Status
1         10.0.12.1/500         10.0.23.2/500         none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: PSK, Auth verify: PSK
      Life/Active Time: 86400/8 sec

R1# ping 192.168.100.2
!!!!!
Success rate is 100 percent (5/5), round-trip min/avg/max = 1/1/4 ms
```

This is your classical baseline. 

A few things to clarify:

**ECDH-384 (group 20) vs X25519:** Both are elliptic curve Diffie-Hellman algorithms for
key exchange. They do the same job: two peers agree on a shared secret without sending it
over the wire. X25519 uses Curve25519 (255-bit security). ECDH-384 uses the
[NIST P-384](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-186.pdf)
curve (384-bit security). In the [container lab](../../learn/ipsec/key-exchange/README.md#exercise-1-observe-a-hybrid-handshake), strongSwan used X25519. IOS XE uses
group 20 (ECDH-384) because it's natively supported and gives a higher security margin.
From a PQC perspective, both are equally "classical" and equally vulnerable to Shor's
algorithm.

**About IKE authentication:** The `pre-shared-key` in the keyring is for **authentication
only**. It proves that R1 is really talking to R3 (and not an attacker). It has nothing to
do with the actual tunnel encryption key. The tunnel key is derived from the DH exchange
(group 20). Think of it this way: DH generates the secret, PSK proves who you're talking
to. They are independent functions. You could swap PSK for certificates and the tunnel
encryption wouldn't change at all. [Further down](#authentication-the-ml-dsa-roadmap) we
talk about ML-DSA, which provides this same authentication service but with a quantum-safe
algorithm instead of a shared password.

Note: this IKE `pre-shared-key` (PSK) has nothing to do with the PPK (Postquantum
Preshared Key) we'll configure in Exercise 2. Different names, different purposes. PSK
authenticates peers. PPK protects the key derivation against quantum attacks.

## Exercise 2: Post-Quantum Pre-shared Key (PPK)

If your protocols do not support post-quantum algorithms yet, you can still make your tunnel quantum-safe. A Post-quantum Preshared Key (PPK) can be mixed
into the IKEv2 key derivation. Of course that PPK will have to be provisioned, either manually of from an external system that communicates out-of-band (e.g. QKD). That way, even if a quantum computer breaks the DH exchange in the
future (by solving the discrete log problem), the session keys remain protected because
they also depend on a secret that was never sent over the wire (the PPK).

The PPK config goes inside the keyring peer, not as a separate command. We will provision it manually, so add this to **both
R1 and R3**:

```
crypto ikev2 keyring CLASSICAL-KEYRING
 peer R3
  ppk manual id PPK-R1R3 key hex 48656C6C6F506F737451756172746E756D required

crypto ikev2 profile CLASSICAL-PROFILE
 keyring ppk CLASSICAL-KEYRING
```

The `required` keyword means the tunnel will NOT come up without PPK. Both sides must have
the same PPK ID and key.

Clear the existing SA and let it renegotiate:

```
R1# clear crypto ikev2 sa
R1# ping 192.168.100.2
!!!!!
```

**Verify:**

```
R1# show crypto ikev2 sa detailed | include Quantum
      Quantum-safe Encryption using Manual PPK
```

There it is: same RFC 8784, same concept as the [container lab](../../learn/ipsec/key-exchange/README.md#exercise-3-an-alternate-path-to-quantum-safety-rfc-8784-ppk), just with the Cisco CLI.

The stats also confirm it:

```
R1# show crypto ikev2 stats | include Quantum
Sessions with Quantum Resistance: 1        Manual: 1        Dynamic: 0
```

## Exercise 3: Native ML-KEM hybrid

IOS XE natively supports ML-KEM for your IPsec tunnels, just enabling fragmentation and adding one line in the proposal on **both R1 and R3**:

```
crypto ikev2 fragmentation mtu 1400

crypto ikev2 proposal CLASSICAL-PROPOSAL
 pqc mlkem768 optional
```

The `optional` keyword means: use ML-KEM if the peer supports it, fall back to classical
if it doesn't. The `fragmentation` command is needed because ML-KEM adds about 1.5 KB to
the IKE exchange, same problem as in the container lab. Same solution too: IKEv2
fragmentation splits the oversized messages into smaller pieces that fit in a
single UDP datagram.

Clear the SA:

```
R1# clear crypto ikev2 sa
R1# ping 192.168.100.2
....!
```

Yeah, first few pings may timeout while the larger handshake completes. 

Check the tunnel details:

```
R1# show crypto ikev2 sa detailed
Tunnel-id Local                 Remote                fvrf/ivrf            Status
1         10.0.12.1/500         10.0.23.2/500         none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: PSK, Auth verify: PSK, QR
      PQC Key Exchange: ML-KEM-768
      Life/Active Time: 86400/2 sec
      ...
      IETF Std Fragmentation  enabled.
      Quantum-safe Encryption using PQC: ML-KEM-768
      ...
      Quantum-safe Encryption using Manual PPK
```

Both PPK **and** ML-KEM are active simultaneously. 

(Note: the `QR` flag in the SA summary line
means "Quantum Resistant" and appears when PPK is configured.)

## Exercise 4: Phased migration

This is the real-world scenario. You have a Cisco network with multiple sites. You can't upgrade
everything at once. So you start with the hub, then upgrade spokes one by one.

This requires a topology change. In exercises 1-3 the tunnel was R1-to-R3 directly (with
R2 just forwarding). Now R2 becomes a VPN hub: it terminates two separate tunnels (one to
R1, one to R3). That means:

- Remove the existing R1-R3 tunnel config on both sides
- Create two new tunnel interfaces on R2 (Tunnel0 toward R1, Tunnel1 toward R3)
- Reconfigure R1 and R3 so their tunnel destinations point to R2 (not each other)
- New keyrings and profiles on all three routers to match the new peer relationships

The key difference is in the **proposals**. The hub (R2) and already-upgraded spoke (R1)
include `pqc mlkem768 optional`. The legacy spoke (R3) does not.

**On R2 (hub), the proposal has ML-KEM optional:**

```
crypto ikev2 proposal HUB-PROPOSAL
 pqc mlkem768 optional
 encryption aes-cbc-256
 integrity sha512
 group 20
```

**R1 (upgraded spoke) also has ML-KEM:**

```
crypto ikev2 proposal PQC-PROPOSAL
 pqc mlkem768 optional
 encryption aes-cbc-256
 integrity sha512
 group 20
```

**R3 (legacy spoke) stays classical:**

```
crypto ikev2 proposal SPOKE-PROPOSAL
 encryption aes-cbc-256
 integrity sha512
 group 20
```

Now check the hub:

```
R2# show crypto ikev2 sa
Tunnel-id Local                 Remote                fvrf/ivrf            Status
1         10.0.12.2/500         10.0.12.1/500         none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: PSK, Auth verify: PSK
      PQC Key Exchange: ML-KEM-768
      Life/Active Time: 86400/19 sec

Tunnel-id Local                 Remote                fvrf/ivrf            Status
2         10.0.23.1/500         10.0.23.2/500         none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: PSK, Auth verify: PSK
      Life/Active Time: 86400/18 sec
```

The R2-R1 tunnel negotiated **ML-KEM-768** (both support it). The R2-R3 tunnel stays
classical (R3 doesn't have `pqc` in its proposal, so it falls back to plain DH group 20).

Now upgrade R3:

```
R3(config)# crypto ikev2 proposal SPOKE-PROPOSAL
R3(config-ikev2-proposal)# pqc mlkem768 optional

R2# clear crypto ikev2 sa
```

After renegotiation, both tunnels show ML-KEM:

```
R2# show crypto ikev2 sa
Tunnel-id Local                 Remote                fvrf/ivrf            Status
1         10.0.12.2/500         10.0.12.1/500         none/none            READY
      ...
      PQC Key Exchange: ML-KEM-768

Tunnel-id Local                 Remote                fvrf/ivrf            Status
2         10.0.23.1/500         10.0.23.2/500         none/none            READY
      ...
      PQC Key Exchange: ML-KEM-768
```

That's the migration strategy: upgrade the hub first with `optional`, then roll out to
spokes at your own pace. Once everything is upgraded, you can change `optional` to
`required` to enforce PQC everywhere and reject classical-only peers.

---

## Authentication: the ML-DSA roadmap

This parallels the [IPsec authentication lab](../../learn/ipsec/authentication/README.md) where you
used ML-DSA certificates with strongSwan.

IOS XE 26.1 authenticates IKEv2 peers with PSK or classical certificates (RSA/ECDSA).
**ML-DSA certificate authentication is planned for IOS XE 26.2.** When it arrives, you'll
be able to generate ML-DSA certificates and use them for IKEv2 peer authentication,
completing the quantum-safe picture: ML-KEM for key exchange, ML-DSA for authentication.

The key exchange is already quantum-safe with native ML-KEM (Exercise 3), so
harvest-now-decrypt-later is covered. What remains classical is the *identity proof*: an
attacker with a future quantum computer could forge an RSA/ECDSA signature to impersonate
a peer, but only in a *live session* (there's no retroactive forgery). That's a real risk,
but it's forward-looking, not retroactive, so it can wait for 26.2 without the same
urgency.

