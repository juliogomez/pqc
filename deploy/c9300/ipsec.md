# IPsec on C9300 Smart Switches

> **Pre-req:** this doc assumes you're familiar with IKEv2 fundamentals. If you've worked
> through the [C8000 IPsec labs](../c8000/ipsec.md), you already know the building
> blocks: proposals, policies, keyrings, profiles, transform-sets, and SVTI tunnels.
> Same CLI, different platform.

Why IPsec on an access switch? Because not every site has a dedicated router. A C9300
does switching, routing, and VPN in one box. Branch offices, retail locations, campus
building interconnects: all cases where the switch *is* the WAN edge.

Every exercise here has a matching
[Ansible playbook](automation/README.md#ipsec) that pushes the same config over
NETCONF. Do the CLI first, reach for the playbooks after.

## Feature status

The IPsec and ML-KEM CLI is available across the Catalyst 9300 family starting in
**IOS XE 26.2** (on 26.1 the commands don't exist). 

## What you'll build

Four exercises, same progression as the
[C8000 IPsec doc](../c8000/ipsec.md) but on a Catalyst 9300 switch:

| Exercise | What it does | Quantum-safe? |
|----------|-------------|---------------|
| 1 | Classical IKEv2 baseline (PSK + ECDH) | No |
| 2 | Add a Post-Quantum Pre-shared Key | Yes (key derivation) |
| 3 | ML-KEM PQC key exchange | Yes (key exchange) |
| 4 | ML-DSA certificate authentication | Yes (key exchange + identity) |

Exercise 1 builds a standard tunnel to prove the data plane works. Exercise 2 mixes
a pre-shared secret into the key derivation so a future quantum computer can't
retroactively break the recorded DH exchange. Exercise 3 replaces the PPK workaround
with native ML-KEM: one line in the IKEv2 proposal, no out-of-band key distribution.
Exercise 4 swaps PSK authentication for ML-DSA certificates, closing the last
classical gap: the identity proof itself is now quantum-safe.

## Prerequisites

### Platform

Cisco's
[IPsec config guide](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/sec/b_26x_sec_9300_cg/configuring_ipsec.html)
lists the **Catalyst 9300X** as the supported platform. We've verified it working
end to end (control plane and data plane) on **C9350** switches as well, both
switch-to-switch and switch-to-router (C8000).

### IOS XE version

The IPsec and ML-KEM CLI requires **IOS XE 26.2 or later** .

```
show version | include Software
```

### HSEC license

Hardware crypto acceleration (and IPsec in general on Catalyst 9000) requires an
active **HSEC** (High Security) license.

```
show license summary
```

You need `C9K HSEC` with status `AUTHORIZED` or `IN USE`. 

### IP routing

The C9300 ships in Layer 2 mode by default. IPsec needs Layer 3 forwarding:

```
show run | include ^ip routing
```

If `ip routing` isn't there, configure it from global config mode.


## Network design

IPsec tunnels run over any routed path: a WAN, an MPLS
backbone, the internet, or multiple L3 hops between sites. The switches don't need to
be directly connected. As long as each switch can reach the other's tunnel endpoint IP
through the routing table, the tunnel works.

This is exactly the scenario IPsec is built for: protecting traffic across multiple L3
hops where you don't control every link in the path. If the switches were directly
connected, you'd use MACsec instead (Layer 2, hop-by-hop encryption, no routing needed).

Each switch sources the tunnel from Loopback110. The tunnel overlay uses a separate
subnet (`192.168.1.0/24`), with Switch-A at `.1` and Switch-B at `.2`.

### Set up the underlay

A number of routers handle transit routing between site switches. On each switch,
configure a routed uplink toward the nearest router and make the Loopback110 tunnel endpoint reachable from the remote switch. In production you'd use a dynamic routing
protocol (OSPF, BGP), but static routes keep the lab simple.

**Verify reachability:**

```
Switch-A# ping 110.0.1.2 source 110.0.1.1
!!!!!
Success rate is 100 percent (5/5)
```

If this doesn't work, nothing else will. Fix the underlay before moving on.

## Exercise 1: classical IKEv2 baseline

Build a standard IKEv2 tunnel with classical cryptography. 

**On Switch-A:**

```
! --- Proposal: algorithms for the IKE negotiation ---
crypto ikev2 proposal CLASSICAL-PROPOSAL
 encryption aes-cbc-256
 integrity sha512
 group 21                                   ! ECDH-521 for key exchange

! --- Policy: which proposal to offer ---
crypto ikev2 policy CLASSICAL-POLICY
 proposal CLASSICAL-PROPOSAL

! --- Keyring: PSK to authenticate the peer ---
crypto ikev2 keyring CLASSICAL-KEYRING
 peer SWITCH-B
  address 110.0.1.2
  pre-shared-key LabPskAB

! --- Profile: match the remote peer, select auth method ---
crypto ikev2 profile CLASSICAL-PROFILE
 match identity remote address 110.0.1.2 255.255.255.255
 authentication remote pre-share
 authentication local pre-share
 keyring local CLASSICAL-KEYRING
 dpd 10 2 periodic

! --- Transform-set: ESP algorithms for data encryption ---
crypto ipsec transform-set CLASSICAL-TS esp-gcm 256
 mode tunnel

! --- IPsec profile: binds transform-set + IKEv2 profile ---
crypto ipsec profile CLASSICAL-IPSEC
 set transform-set CLASSICAL-TS
 set ikev2-profile CLASSICAL-PROFILE

! --- Tunnel interface ---
interface Tunnel0
 ip address 192.168.1.1 255.255.255.0
 tunnel source Loopback110
 tunnel mode ipsec ipv4
 tunnel destination 110.0.1.2
 tunnel protection ipsec profile CLASSICAL-IPSEC
```

**On Switch-B:** Same structure, mirrored. The differences:

| Parameter | Switch-A | Switch-B |
|-----------|----------|----------|
| Peer address in keyring | `110.0.1.2` | `110.0.1.1` |
| Match identity | `110.0.1.2` | `110.0.1.1` |
| Tunnel IP | `192.168.1.1` | `192.168.1.2` |
| Tunnel destination | `110.0.1.2` | `110.0.1.1` |

### Verify

Check the IKEv2 SA:

```
show crypto ikev2 sa
```

You should see `Status: READY` with the negotiated algorithms:

```
Tunnel-id Local                 Remote                fvrf/ivrf            Status
1         110.0.1.1/500        110.0.1.2/500        none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:21, Auth sign: PSK, Auth verify: PSK
      Life/Active Time: 86400/15 sec
```

Ping across the tunnel:

```
ping 192.168.1.2 source 192.168.1.1
!!!!!
Success rate is 100 percent (5/5)
```

Verify the interface counters are moving (the `packets output` and `packets input`
numbers should increase with each ping):

```
show interface Tunnel0 | include packets
     10 packets input, 1370 bytes, 0 no buffer
     15 packets output, 1710 bytes, 0 underruns
```

> **Why not `show crypto ipsec sa | include pkts`?** On C9350 switches, the crypto is
> offloaded to the Silicon One ASIC. The software counters (`#pkts encaps`) stay at
> zero because the hardware handles encryption directly. The interface-level counters
> reflect the actual traffic.

That's your classical baseline. Two things to note:

**DH group 21 (ECDH-521):** This is the strongest classical Diffie-Hellman group
available on IOS XE. It's what a quantum computer running Shor's algorithm would break.
Exercise 2 adds a PPK to protect the key derivation, and Exercise 3 adds ML-KEM on top.

**PSK for authentication:** The `pre-shared-key` in the keyring proves identity (Switch-A
is really talking to Switch-B). It has nothing to do with the tunnel encryption key.
The encryption key comes from the DH exchange. Think of it this way: DH generates the
secret, PSK proves who you're talking to. A PSK is fine for a lab, but at scale you
want certificates. And if the certificates use RSA or ECDSA, that identity proof is
forgeable by a future quantum computer. ML-DSA certificates fix that;
[Exercise 4](#exercise-4-ml-dsa-certificate-authentication) walks you through it
on the C9300.

## Exercise 2: Post-Quantum Pre-shared Key (PPK)

You don't need ML-KEM to make your tunnel quantum-safe. A
Post-quantum Preshared Key (PPK) gets mixed into the IKEv2 key derivation
([RFC 8784](https://datatracker.ietf.org/doc/html/rfc8784)). Even if a quantum
computer breaks the DH exchange in the future, the session keys remain protected
because they also depend on a secret that was never sent over the wire.

The trade-off? You need to provision and rotate that secret out of band on every peer
pair. That's manageable for a handful of tunnels, less so for hundreds. Exercise 3
solves that with ML-KEM, which needs no pre-shared material at all.

The PPK config goes inside the keyring peer block. Add this on **both switches**:

```
crypto ikev2 keyring CLASSICAL-KEYRING
 peer SWITCH-B
  ppk manual id PPK-AB key hex 48656C6C6F506F737451756172746E756D required
```

And tell the IKEv2 profile to use the keyring for PPK:

```
crypto ikev2 profile CLASSICAL-PROFILE
 keyring ppk CLASSICAL-KEYRING
```

The `required` keyword means the tunnel will NOT come up without PPK. Both sides must
have the same PPK ID and key. On Switch-B, the peer name is `SWITCH-A` but the PPK ID
and key are identical.

Clear the existing SA and let it renegotiate:

```
clear crypto ikev2 sa
```

### Verify

```
show crypto ikev2 sa detail | include Quantum
      Quantum-safe Encryption using Manual PPK
```

There it is: the key derivation now depends on the PPK. Same RFC 8784.

The stats also confirm it:

```
show crypto ikev2 stats | include Quantum
Sessions with Quantum Resistance: 1        Manual: 1        Dynamic: 0
```

### Remove PPK before Exercise 3

Exercise 3 replaces the PPK approach with native ML-KEM. Remove the PPK first:

```
crypto ikev2 profile CLASSICAL-PROFILE
 no keyring ppk CLASSICAL-KEYRING

crypto ikev2 keyring CLASSICAL-KEYRING
 peer SWITCH-B
  no ppk manual id PPK-AB
```

Clear the SA again:

```
clear crypto ikev2 sa
```

## Exercise 3: ML-KEM PQC

Now make the key exchange quantum-safe without the key-distribution headache. You're
adding ML-KEM as a second key exchange on top of the classical ECDH. IOS XE performs a
*hybrid* exchange: both the classical group and the ML-KEM run in the same IKEv2
handshake, and the final session key depends on both. If either algorithm holds, the
tunnel stays safe.

The only change is in the IKEv2 proposal. Everything else (keyring, profile,
transform-set, tunnel) stays exactly the same.

**On both switches:**

```
crypto ikev2 proposal CLASSICAL-PROPOSAL
 pqc mlkem768
```

That's it. One line. The proposal now includes ML-KEM-768 alongside the existing
ECDH group 21. IOS XE negotiates both automatically.

> **Why `mlkem768`?** ML-KEM-768 is the recommended baseline for general use (roughly
> equivalent to AES-192 security strength). ML-KEM-512 exists for constrained
> environments, and ML-KEM-1024 for high-assurance requirements. All three are supported
> on the C9300.

### The optional keyword

If you're migrating a network where some peers support ML-KEM and some don't, you can
mark PQC as optional:

```
crypto ikev2 proposal MIGRATION-PROPOSAL
 pqc mlkem768 optional
 encryption aes-cbc-256
 integrity sha512
 group 21
```

With `optional`, the switch will attempt ML-KEM but fall back to classical-only if the
peer doesn't support it. Without `optional`, the tunnel won't come up unless both sides
negotiate ML-KEM. Use `optional` during migration, remove it once all peers are upgraded.

### Verify

Check the IKEv2 SA:

```
show crypto ikev2 sa
```

The output now includes a `PQC Key Exchange` line:

```
Tunnel-id Local                 Remote                fvrf/ivrf            Status
1         110.0.1.1/500        110.0.1.2/500        none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:21, Auth sign: PSK, Auth verify: PSK
      PQC Key Exchange: ML-KEM-768
      Life/Active Time: 86400/8 sec
```

For more detail:

```
show crypto ikev2 sa detail
```

Look for the `Quantum-safe Encryption using PQC` line:

```
      Quantum-safe Encryption using PQC: ML-KEM-768
```

That confirms the key exchange used both ECDH-521 and ML-KEM-768. The session key
is derived from both, making it resistant to both classical and quantum attacks.

Tunnel pings should still work:

```
ping 192.168.1.2 source 192.168.1.1
```

The `PQC Key Exchange: ML-KEM-768` line in the SA output is the proof that the
quantum-safe handshake completed successfully.


## Exercise 4: ML-DSA certificate authentication

Exercises 1 to 3 made the *key exchange* quantum-safe and left the *identity proof*
classical. A PSK is fine in a lab, but at scale you want certificates. And an RSA or
ECDSA certificate is forgeable by a future quantum computer. ML-DSA (FIPS 204) closes
that gap.

Let's test ML-DSA switch-to-switch on C9350s alongside ML-KEM. The CLI is identical to what the
[C8000 doc covers in Exercise 5](../c8000/ipsec.md#exercise-5-ml-dsa-certificate-authentication).

### Build the PKI

Use the same script as the C8000 exercise. It builds a root CA and per-device identity
certificates with ML-DSA-65 keys. You need OpenSSL 3.5+ on your workstation.

```
$ cd deploy/c8000/mldsa-certs
$ ./gen-mldsa-certs.sh
```

Edit the script's `ROUTERS` array first to match your switch hostnames and tunnel-source
IPs. The SAN in each certificate must match the `identity local address` you'll configure
later. See the
[C8000 PKI walkthrough](../c8000/ipsec.md#build-the-pki) for the full explanation of
certificate sizes, SANs, and why they matter.

### Import the bundles

Copy the PKCS#12 bundles to each switch and import them:

```
$ scp -O mldsa-pki/mldsa65-sw1.p12 ansible@<SW1-mgmt-ip>:flash:/mldsa65-sw1.p12
```

On each switch:

```
SW1(config)# crypto pki import TP-MLDSA65 pkcs12 flash:/mldsa65-sw1.p12 password cisco123
% Importing pkcs12...
CRYPTO_PKI: Imported PKCS12 file successfully.
```

> **`service internal` on 26.2.** If the import fails with
> `status = 65535: Unknown reason`, enable `service internal` in global config and retry.
> Without it, the parser doesn't recognize ML-DSA key material. 

Disable CRL checking (the lab CA has no CRL distribution point):

```
SW1(config)# crypto pki trustpoint TP-MLDSA65
SW1(ca-trustpoint)# revocation-check none
```

Confirm the trustpoint:

```
SW1# show crypto pki certificates verbose TP-MLDSA65
Certificate
  Status: Available
  Version: 3
  Certificate Usage: Signature
  Issuer:
    cn=PQC-LAB-ROOT-mldsa65
  Subject:
    Name: SW1-mldsa65
    cn=SW1-mldsa65
  Subject Key Info:
    Public Key Algorithm: ML-DSA
    Public Key Size: (1974 bytes)
  Signature Algorithm: ML-DSA-65
```

`Public Key Algorithm: ML-DSA` and 1,974 bytes. Same numbers OpenSSL printed.

### Enable fragmentation

ML-DSA-65 certificates are ~5.6 KB (vs ~920 B for RSA-2048). The `IKE_AUTH` exchange
fragments heavily. Without fragmentation, the handshake never completes.

```
crypto ikev2 fragmentation mtu 1400
```

If you already added this in Exercise 3, you're covered.

### Swap PSK for ML-DSA

Create a new IKEv2 profile and IPsec profile for ML-DSA. On **Switch-A**:

```
crypto ikev2 profile MLDSA-PROFILE
 match identity remote address 110.0.1.2 255.255.255.255
 identity local address 110.0.1.1
 authentication local mldsa-sig
 authentication remote mldsa-sig
 pki trustpoint TP-MLDSA65
 dpd 30 5 periodic

crypto ipsec profile MLDSA-IPSEC
 set transform-set CLASSICAL-TS
 set ikev2-profile MLDSA-PROFILE
```

On **Switch-B**, mirror the addresses: match `110.0.1.1`, identity `110.0.1.2`.

The `identity local address` must match the SAN in the certificate. If it doesn't, the
peer logs `%CRYPTO-6-IKMP_NO_ID_CERT_ADDR_MATCH` on every negotiation.

Now apply the profile to a tunnel. If you're reusing Tunnel0 from the earlier exercises,
swap the IPsec profile:

```
interface Tunnel0
 tunnel protection ipsec profile MLDSA-IPSEC
```

The router shuts the interface when you change tunnel protection. Bring it back:

```
interface Tunnel0
 no shutdown
```

> **Profile collisions.** If you still have the PSK-based `CLASSICAL-PROFILE` matching
> the same remote peer, it can intercept the negotiation before `MLDSA-PROFILE` gets a
> chance. Either shut down the tunnel using the old profile or use a different tunnel
> endpoint pair for the ML-DSA tunnel.

### Verify

```
SW1# ping 192.168.1.2 source 192.168.1.1
!!!!!
Success rate is 100 percent (5/5)

SW1# show crypto ikev2 sa detailed
Tunnel-id Local                 Remote                fvrf/ivrf            Status
1         110.0.1.1/500        110.0.1.2/500        none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:21, Auth sign: MLDSA, Auth verify: MLDSA
      PQC Key Exchange: ML-KEM-768
      ...
      Quantum-safe Encryption using PQC: ML-KEM-768
      IETF Std Fragmentation MTU in use: 1372 bytes.
```

That's the whole picture on one line: **`Auth sign: MLDSA, Auth verify: MLDSA`** next to
**`PQC Key Exchange: ML-KEM-768`**. Both key exchange and identity are quantum-safe.

The session view confirms with the `Q` capability flag:

```
SW1# show crypto session detail
Interface: Tunnel0
Profile: MLDSA-PROFILE
Session status: UP-ACTIVE
  IKEv2 SA: local 110.0.1.1/500 remote 110.0.1.2/500 Active
          Capabilities:DFUQ connid:1 lifetime:23:59:04
```

`Q` = quantum-safe encryption. `F` = IKE fragmentation.

For the handshake overhead analysis (fragment counts, byte sizes, comparison with RSA
and ECDSA), see
[C8000 Exercise 6](../c8000/ipsec.md#exercise-6-what-ml-dsa-actually-costs). The
numbers are the same on the C9300 since ML-DSA overhead is in the IKEv2 signaling,
not in the data plane.

## Switch-to-router interop

Everything in this doc works the same when one end is a C8000 router instead of a
second switch. We tested classical, ML-KEM, and ML-DSA between a C9350 and a
C8235-G2 on 26.2: all working end to end.

See the [switch-to-router interop doc](../switch-router-interop.md#ipsec) for the
full feature compatibility table.

## Teardown

Remove the configuration in reverse dependency order. On both switches:

```
configure terminal
 no interface Tunnel0
 no crypto ipsec profile MLDSA-IPSEC
 no crypto ikev2 profile MLDSA-PROFILE
 no crypto ipsec profile CLASSICAL-IPSEC
 no crypto ikev2 profile CLASSICAL-PROFILE
 no crypto ikev2 keyring CLASSICAL-KEYRING
 no crypto ikev2 policy CLASSICAL-POLICY
 no crypto ikev2 proposal CLASSICAL-PROPOSAL
 no crypto ipsec transform-set CLASSICAL-TS
end
```

If you imported ML-DSA certificates, also remove the trustpoint:

```
configure terminal
 no crypto pki trustpoint TP-MLDSA65
end
```

Verify nothing is left:

```
show crypto ikev2 sa
show crypto ipsec sa
show run | section ^crypto ikev2
show run | section ^crypto pki trustpoint
```

All four should return empty.

## The automated version

All four exercises exist as Ansible playbooks in
[`automation/`](automation/README.md#ipsec): classical baseline, PPK, and ML-KEM as
separate overlays you add and remove independently.

```bash
cd deploy/c9300/automation
ansible-playbook ipsec-baseline.yml   # Exercise 1
ansible-playbook ipsec-pq-ppk.yml     # Exercise 2
ansible-playbook ipsec-pq-mlkem.yml   # Exercise 3
```

Config goes over NETCONF; exec-mode verbs (`clear crypto ikev2 sa`) still go over CLI.
[DESIGN.md](automation/DESIGN.md) has the design rationale and the honest accounting of
what NETCONF can and can't express.

## Reference config

Full working config combining the underlay and Exercises 3-4 (ML-KEM + ML-DSA) for both
switches:
[`device-configs/c9300-ipsec-pqc-reference.txt`](device-configs/c9300-ipsec-pqc-reference.txt).
Copy, adjust IPs and interface names for your topology.
