# IPsec on IOS XE

> **Pre-req:** this doc assumes you have reviewed the container labs on
> [IPsec key exchange](../../learn/ipsec/key-exchange/README.md) and
> [IPsec authentication](../../learn/ipsec/authentication/README.md). Same progression,
> same RFCs, now with Cisco CLI: classical baseline, then PPK, then native ML-KEM
hybrid, then ML-DSA certificate authentication, then a phased migration across a hub and
two spokes.

Everything below was run on three Cisco C8235-G2 routers on **IOS XE 26.2**.

## Network design

R2 acts as a Layer 3 transit router. It just forwards packets between R1 and R3. The IPsec
tunnel runs end-to-end between R1 and R3, traversing R2 as an intermediate hop. This
emulates a real WAN where crypto endpoints are not directly connected.

## Exercise 1: Classical IKEv2 baseline

Set up the [underlay](README.md#set-up-the-underlay) first if you haven't already. You need
end-to-end reachability between R1 and R3 before the tunnel will come up.

Now build the tunnel.

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
! --- (Exercise 5 replaces this keyring with ML-DSA certificates) ---
crypto ikev2 keyring CLASSICAL-KEYRING
 peer R3
  address 10.0.23.2
  pre-shared-key LabPskR1R3                 ! proves identity, NOT used as encryption key

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
encryption wouldn't change at all.
[Exercise 5](#exercise-5-ml-dsa-certificate-authentication) does exactly that swap, using
ML-DSA certificates so the identity proof is quantum-safe too.

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

PPK proved that you can protect the key derivation against quantum attacks without touching
the DH exchange itself. But it comes with the classic key-distribution headache: every peer
pair needs a shared secret provisioned and rotated out of band. Once the platform supports
ML-KEM natively, you don't need that extra secret any more. Remove the PPK first, then add
ML-KEM.

**Remove PPK from both R1 and R3:**

```
crypto ikev2 profile CLASSICAL-PROFILE
 no keyring ppk CLASSICAL-KEYRING

crypto ikev2 keyring CLASSICAL-KEYRING
 peer R3
  no ppk manual id PPK-R1R3
```

Now add ML-KEM. Starting with 26.1, IOS XE natively supports ML-KEM for your IPsec tunnels, just enabling fragmentation and adding one line in the proposal on **both R1 and R3**:

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
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: PSK, Auth verify: PSK
      PQC Key Exchange: ML-KEM-768
      Life/Active Time: 86400/9 sec
      ...
      IETF Std Fragmentation  enabled.
      Quantum-safe Encryption using PQC: ML-KEM-768
      IETF Std Fragmentation MTU in use: 1372 bytes.
```

ML-KEM is active. No `QR` flag on the summary line (that only shows up when PPK is
configured), no PPK line in the detailed view. Pure ML-KEM hybrid, no extra secrets needed.

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

## Exercise 5: ML-DSA certificate authentication

Exercises 1 to 4 made the *key exchange* quantum-safe and left the *identity proof*
classical. A PSK is fine in a lab, but at scale you want certificates rather than havint to manage per-tunnel pre-shared keys. And an RSA or ECDSA certificate is forgeable by a
future quantum computer. IOS XE 26.2 closes that gap with
ML-DSA (FIPS 204), the same signature scheme you used with strongSwan in the
[IPsec authentication lab](../../learn/ipsec/authentication/README.md).

This is the missing half of the picture, so let's go.

### The router can make the keys. Ask it in the right mode

Here's the first surprise, and it's a lesson about the CLI rather than about ML-DSA. Ask the
router in config mode and it looks like ML-DSA key generation doesn't exist:

```
R1(config)# crypto key generate ?
  ec   Generate EC keys for ECDSA
  rsa  Generate RSA keys
```

Only `ec` and `rsa`. Except `crypto key generate` is an **exec-mode** command, and the
config-mode parser is answering about something else entirely. At the exec prompt on the same
box:

```
R1# crypto key generate ?
  ec     Generate EC keys for ECDSA
  mldsa  Generate ML-DSA keys
  rsa    Generate RSA keys

R1# crypto key generate mldsa param 65 label PROBE-MLDSA65
The name for the keys will be: PROBE-MLDSA65
% Generating MLDSA-65 keys, keys will be non-exportable...[OK] (elapsed time was 0 seconds)
```

So 26.2 will happily mint you an ML-DSA-44, 65 or 87 key on the box, non-exportable by
default, and `crypto key zeroize mldsa <label>` removes it again.

Full syntax: `crypto key generate mldsa param {44|65|87} [label WORD] [exportable]`.

### Two paths from "key exists" to "ML-DSA tunnel is up"

Having a key is not the same as having a certificate. This repo uses **two different
enrollment paths** to get from "ML-DSA works on the box" to "ML-DSA IKEv2 tunnel is up".
They end in the same place on the wire; they do not use the same private key.

| | This exercise | [Automation lab](automation/README.md) |
|---|---|---|
| **Identity private key born** | On your workstation | On the router |
| **How the cert arrives** | PKCS#12 import (`crypto pki import`) | On-box CSR, signed by an external OpenSSL ML-DSA CA (your laptop) |
| **Why this path** | Fewest steps at three consoles; keeps the exercise focused on tunnels and handshake size | Repeatable and idempotent, private key never leaves the router |
| **Trade-off** | The bundle carries a key that was not born on the router | CA private key lives on your laptop / CA (`.lab-ca/`) |

The probe key above (`PROBE-MLDSA65`) is there to show the platform capability. **This
exercise does not enroll that key.** It imports a separate identity from
[`gen-mldsa-certs.sh`](mldsa-certs/gen-mldsa-certs.sh). The [automation role](automation/README.md)
generates a fresh on-box key per router and runs the CSR path documented in
[`automation/DESIGN.md`](automation/DESIGN.md#q1-the-ml-dsa-certificate-path).

#### This exercise: PKCS#12 import

[gen-mldsa-certs.sh](mldsa-certs/gen-mldsa-certs.sh) defines a root CA per parameter set on
your laptop, an identity certificate for each router, and a PKCS#12 bundle to import in one
shot. It's the quickest way to get three routers holding ML-DSA identities on this hardware.

#### Automation: on-box key + external ML-DSA CA

The Ansible playbook (`ipsec-pq-mldsa.yml`) does not import PKCS#12. Each router generates
its own ML-DSA key, issues a PKCS#10 CSR, and the playbook signs it with an OpenSSL ML-DSA
CA on your laptop. Only public material crosses the wire. See
[DESIGN.md](automation/DESIGN.md#two-workable-paths-and-why-the-external-ca-won) for why that
beat the alternative (a local IOS CA, which can only sign the leaf with RSA).

### Build the PKI

[gen-mldsa-certs.sh](mldsa-certs/gen-mldsa-certs.sh) does the whole job: a root CA per
parameter set, an identity certificate for R1, R2 and R3, and a PKCS#12 bundle for each.
You need OpenSSL 3.5 or newer, which has ML-DSA natively, in your computer.

```
$ cd deploy/ios-xe/mldsa-certs
$ ./gen-mldsa-certs.sh

=== OpenSSL 3.6.3 4 Nov 2025 (Library: OpenSSL 3.6.3 4 Nov 2025)
...
=== Artifact sizes in bytes (R1 identity, DER-encoded cert)
ALGORITHM          PUBKEY      PRIVKEY         CERT          P12
rsa-2048              294         1218          920         3698
ecdsa-p256             91          138          528         1827
mldsa44              1334         2626         4118        11504
mldsa65              1974         4098         5647        16032
mldsa87              2614         4962         7605        20816
```

Look at that table before you go any further, because it explains everything that happens
later. An ML-DSA-65 public key is **1,974 bytes against 294** for RSA-2048, and **against
91** for ECDSA P-256. The certificate carrying it is **5,647 bytes against 920**. That's
not a tweak, it's an order of magnitude, and IKEv2 has to carry it.

One detail in the script matters more than it looks: each identity certificate gets the
router's tunnel source address as a *Subject Alternative Name* (SAN). The SAN is an X.509
extension that lists the identities the certificate is valid for (IP addresses, DNS names,
email addresses). IKEv2 checks it when matching a peer's certificate against the identity
that peer claims.

```
subjectAltName=IP:10.0.12.1
```

Skip that and the tunnel still comes up, but the router complains
`%CRYPTO-6-IKMP_NO_ID_CERT_ADDR_MATCH` on every negotiation because the IKEv2 identity
(`identity local address`) has nothing in the certificate to match against. Put the
address in the SAN and the log stays clean.

### Import the bundle

> **Achtung!** If your bundle contains an ECDSA key, importing it can break SSH
> access to the router. Full details and fix are in [Things that will bite you](#things-that-will-bite-you) below.

Copy the bundle over and import it. The import is what creates the trustpoint:

```
$ scp -O mldsa-pki/mldsa65-r1.p12 admin@<R1-mgmt-ip>:bootflash:/mldsa65-r1.p12

R1(config)# crypto pki import TP-MLDSA65 pkcs12 bootflash:/mldsa65-r1.p12 password cisco123
% Importing pkcs12...
Source filename [mldsa65-r1.p12]?
Reading file from bootflash:/mldsa65-r1.p12
CRYPTO_PKI: Imported PKCS12 file successfully.
```

You will also see this in the log, on every single import:

```
%PKI-3-KEY_CMP_MISMATCH: Key in the certificate and stored key does not match for Trustpoint-TP-MLDSA65.
```

Ignore it. It's severity 3 and it looks alarming, but the import succeeds and the
certificate works.

Wrt that `password cisco123`: it protects the bundle for the few seconds it sits on
`bootflash:` and nothing else, and the bundle gets deleted right after import. It's the
script's default, and `PASS=... ./gen-mldsa-certs.sh` overrides it if you'd rather it
didn't land in your shell history. Don't reuse this pattern for a bundle that lives
anywhere longer than one import.

Check what the import generated:

```
R1# show running-config | section crypto pki trustpoint TP-MLDSA65
crypto pki trustpoint TP-MLDSA65
 enrollment pkcs12
 revocation-check crl
 mldsakeypair TP-MLDSA65 65
 hash sha512
```

Note `revocation-check crl`. That's the default: before trusting a peer's certificate, the
router tries to download the CA's *Certificate Revocation List* (CRL), a signed list of
serial numbers the CA has revoked early. Our lab certificates have no CRL distribution
point URL, so the router has nowhere to fetch from and the handshake stalls. Fix it:

```
R1(config)# crypto pki trustpoint TP-MLDSA65
R1(ca-trustpoint)# revocation-check none
```

Now confirm the router really parsed an ML-DSA certificate:

```
R1# show crypto pki certificates verbose TP-MLDSA65
Certificate
  Status: Available
  Version: 3
  Certificate Usage: Signature
  Issuer:
    cn=PQC-LAB-ROOT-mldsa65
  Subject:
    Name: R1-mldsa65
    IP Address: 10.0.12.1
    cn=R1-mldsa65
  Subject Key Info:
    Public Key Algorithm: ML-DSA
    Public Key Size: (1974 bytes)
  Signature Algorithm: ML-DSA-65
  X509v3 extensions:
    X509v3 Subject Alternative Name:
        IP Address : 10.0.12.1
    Extended Key Usage:
        Server Auth
        Client Auth
  Key Label: TP-MLDSA65
  Key storage device: private config
```

`Public Key Algorithm: ML-DSA`, and 1,974 bytes, which is exactly the number OpenSSL
printed for the DER public key. The router and your container agree.

Both `Server Auth` and `Client Auth` are needed. An IKEv2 peer is initiator on one
negotiation and responder on the next, so a client-only certificate fails half the time.

### Swap PSK for ML-DSA

Do this on **both R1 and R3**. Nothing about the key exchange changes: `pqc mlkem768
optional` from Exercise 3 stays exactly as it is.

```
crypto ikev2 profile MLDSA-PROFILE
 match identity remote address 10.0.23.2 255.255.255.255
 identity local address 10.0.12.1              ! must match the certificate's IP SAN
 authentication local mldsa-sig
 authentication remote mldsa-sig
 pki trustpoint TP-MLDSA65
 dpd 30 5 periodic

crypto ipsec profile MLDSA-IPSEC
 set transform-set CLASSICAL-TS
 set ikev2-profile MLDSA-PROFILE

interface Tunnel0
 tunnel protection ipsec profile MLDSA-IPSEC
```

On R3, mirror it: match `10.0.12.1`, `identity local address 10.0.23.2`.

The router warns you:

```
 Warning: MLDSA Auth packets are exetremely large. Please enable IKEv2 Fragmentation.
```

You already enabled fragmentation in Exercise 3, so you're covered. If you hadn't, the
handshake would simply never complete.

Then you get this:

```
%Shutting down Tunnel0 interface due to IPsec tunnel protection modification.
%Please run "no shutdown" after config change to bring up the interface.
```

Changing tunnel protection shuts the interface, so you need to bring it back:

```
R1(config)# interface Tunnel0
R1(config-if)# no shutdown
```

### Verify

```
R1# ping 192.168.100.2
!!!!!
Success rate is 100 percent (5/5), round-trip min/avg/max = 1/1/4 ms

R1# show crypto ikev2 sa detailed
Tunnel-id Local                 Remote                fvrf/ivrf            Status
2         10.0.12.1/500         10.0.23.2/500         none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: MLDSA, Auth verify: MLDSA
      PQC Key Exchange: ML-KEM-768
      ...
      IETF Std Fragmentation  enabled.
      Quantum-safe Encryption using PQC: ML-KEM-768
      IETF Std Fragmentation MTU in use: 1372 bytes.
```

There it is, on one line: **`Auth sign: MLDSA, Auth verify: MLDSA`** next to **`PQC Key
Exchange: ML-KEM-768`**. Both halves of the tunnel are quantum-safe. Key exchange resists
harvest-now-decrypt-later, authentication resists a future forged identity.

The session view says the same thing more compactly:

```
R1# show crypto session detail
Code: C - IKE Configuration mode, D - Dead Peer Detection
K - Keepalives, N - NAT-traversal, T - cTCP encapsulation
X - IKE Extended Authentication, F - IKE Fragmentation
R - IKE Auto Reconnect, U - IKE Dynamic Route Update
S - SIP VPN, E - Stronger IKE Encryption Enforced
Q - Quantum-safe Encryption

Interface: Tunnel0
Profile: MLDSA-PROFILE
Session status: UP-ACTIVE
  IKEv2 SA: local 10.0.12.1/500 remote 10.0.23.2/500 Active
          Capabilities:DFUQ connid:2 lifetime:23:59:04
```

Read the capability letters against the legend the command prints for you: `Q` is
quantum-safe encryption, `F` is IKE fragmentation, `D` is Dead Peer Detection and `U` is IKE dynamic route
update. `DFUQ`... you cannot make these things up...  :)  `DFUQ` is what a healthy post-quantum tunnel looks like here.

One more thing worth noticing: you configured `crypto ikev2 fragmentation mtu 1400` but
the SA reports `MTU in use: 1372 bytes`. That's not a bug. You tell the router the path
MTU (1400), and it subtracts the IPsec ESP encapsulation overhead (the bytes every
data-plane packet picks up at Layer 3 when it gets wrapped inside the tunnel) to arrive at
the largest payload it can actually forward. 

## Exercise 6: What ML-DSA actually costs

The warning said the auth packets are "exetremely large"...  :)  How large? Let's measure instead
of guessing.

R2 is the transit hop, so it sees every IKE packet without being a crypto endpoint. Use
Embedded Packet Capture there:

```
R2# monitor capture CAP interface Vlan12 both
R2# monitor capture CAP match ipv4 protocol udp any any
R2# monitor capture CAP buffer size 20
R2# monitor capture CAP start
```

Now, on R1, force a fresh negotiation, wait for it to settle, and stop the capture:

```
R1# clear crypto ikev2 sa
R1# ping 192.168.100.2

R2# monitor capture CAP stop
R2# show monitor capture CAP buffer detailed
```

You can repeat for each trustpoint by repointing the profile:

```
R1(config)# crypto ikev2 profile MLDSA-PROFILE
R1(config-ikev2-profile)# no pki trustpoint TP-MLDSA65
R1(config-ikev2-profile)# pki trustpoint TP-MLDSA87
```

### Where the bytes go

Three exchange types show up in the capture, and the IKEv2 header tells you everything. Byte `0x3C` of the UDP payload is the
exchange type:

| Value | Exchange | What it carries here |
|---|---|---|
| 34 | `IKE_SA_INIT` | the classical ECDH group 20 exchange |
| 43 | `IKE_INTERMEDIATE` | the ML-KEM-768 exchange |
| 35 | `IKE_AUTH` | the certificate and the ML-DSA signature |

Rember ML-KEM doesn't ride in IKE_SA_INIT. It gets its
own round trip in an IKE_INTERMEDIATE exchange, which is exactly the mechanism
RFC 9370 defines for additional key
exchanges. 

### The numbers

Measured on the wire at R2, one complete handshake each:

| Authentication | IKE_AUTH frames | IKE_AUTH bytes | Whole handshake | vs RSA-2048 |
|---|---|---|---|---|
| RSA-2048 | 3 | 3,462 | 7 frames / 7,111 B | 1.0x |
| ML-DSA-44 | 12 | 15,384 | 16 frames / 19,033 B | 4.4x |
| ML-DSA-65 | 16 | 20,752 | 20 frames / 24,401 B | 6.0x |
| ML-DSA-87 | 20 | 27,848 | 24 frames / 31,497 B | 8.0x |

`IKE_SA_INIT` is 2 frames and about 1.1 KB in every case: it only carries the classical
ECDH exchange (group 20), whose public value is 512 bytes. The ML-KEM `IKE_INTERMEDIATE`
exchange is also a constant 2 frames, but at 2,548 bytes it's already more than double
`IKE_SA_INIT`, because the ML-KEM-768 encapsulation key alone is 1,184 bytes. Neither
changes with the signature algorithm, though. All the *variable* growth is in `IKE_AUTH`,
which is what you'd expect: that's where the certificate and the signature travel.

The largest ML-DSA frame is 1,422 bytes in every run. That's the fragmentation MTU doing
its job. Instead of one enormous datagram you get a dozen or more well-behaved ones, which
is precisely why the router nags you about fragmentation.

**Choosing a parameter set:** ML-DSA-65 costs 35% more `IKE_AUTH` bytes than ML-DSA-44 and
buys you NIST security category 3 instead of 2. ML-DSA-87 costs another 34% on top for
category 5. Unless you have a specific mandate, 65 is the sensible default, which is also
what the [container labs](../../learn/ipsec/authentication/README.md) use.

**Is 20 KB a problem?** For a site-to-site tunnel that negotiates once and rekeys every 24
hours, no. For a hub terminating thousands of DMVPN spokes that all reconnect after a
power event, definitely something to consider when doing capacity planning. The cost is per-negotiation, not per-packet:
once the SA is up, ESP data plane traffic is unchanged.

## Exercise 7: Phased authentication migration

Exercise 4 migrated the *key exchange* across a hub and two spokes, and it was painless
because `pqc mlkem768 optional` negotiates opportunistically. Each peer offers ML-KEM, and
if the other end can't do it, the tunnel falls back to classical DH.

Authentication has no `optional`:

```
R1(config-ikev2-profile)# authentication local ?
  dynamic    Dynamically set local authentication method for responder
  eap        Extended Authentication Protocol
  ecdsa-sig  ECDSA Signature
  mldsa-sig  ML-DSA Signature
  pre-share  Pre-Shared Key
  rsa-sig    Rivest-Shamir-Adleman Signature
```

One method, no fallback. That difference drives the whole migration strategy, so this
exercise is about finding out what you can and cannot do.

### The topology

Same shape as Exercise 4. R2 becomes the hub with two tunnels, R1 is the already-migrated
spoke, R3 is the one still running classical certificates.

```
R1  ==== Tunnel1, ML-DSA-65 ====  R2  ==== Tunnel2, RSA-2048 ====  R3
```

Both tunnels keep `pqc mlkem768 optional`, so the key exchange is quantum-safe on both
legs throughout. Only the signatures differ.

**On R2 (hub), one profile per spoke:**

```
crypto ikev2 profile SPOKE-R1
 match identity remote address 10.0.12.1 255.255.255.255
 identity local address 10.0.12.2
 authentication local mldsa-sig
 authentication remote mldsa-sig
 pki trustpoint TP-MLDSA65

crypto ikev2 profile SPOKE-R3
 match identity remote address 10.0.23.2 255.255.255.255
 identity local address 10.0.23.1
 authentication local rsa-sig
 authentication remote rsa-sig
 pki trustpoint TP-RSA
```

The hub's certificate needs both tunnel source addresses in its SAN, because it presents
the same identity certificate on both legs:

```
subjectAltName=IP:10.0.12.2,IP:10.0.23.1
```

That is the reason why `gen-mldsa-certs.sh` gives R2 two IP SANs and the spokes one each.

**Verify the mixed state:**

```
R2# show crypto ikev2 sa
Tunnel-id Local                 Remote                fvrf/ivrf            Status
2         10.0.12.2/500         10.0.12.1/500         none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: MLDSA, Auth verify: MLDSA
      PQC Key Exchange: ML-KEM-768

Tunnel-id Local                 Remote                fvrf/ivrf            Status
1         10.0.23.1/500         10.0.23.2/500         none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: RSA, Auth verify: RSA
      PQC Key Exchange: ML-KEM-768
```

One hub, two tunnels, two different signature algorithms, both with ML-KEM-768. That's a
perfectly reasonable place to sit for months while you work through your estate.

### Can you cut over without an outage?

This is the question that matters operationally, and the answer is NO. Here's the attempt.

`authentication remote` is *additive*. Configure two methods and both stay listed:

```
R2(config-ikev2-profile)# authentication remote mldsa-sig

R2# show running-config | section crypto ikev2 profile SPOKE-R3
crypto ikev2 profile SPOKE-R3
 authentication remote rsa-sig
 authentication remote mldsa-sig
 authentication local rsa-sig
 pki trustpoint TP-RSA
 pki trustpoint TP-MLDSA65
```

So both ends can be told to *accept* either signature type, and the running tunnel stays
up while you do it. It looks like the setup for a clean make-before-break: teach both ends
to accept both, flip one side, flip the other.

It doesn't work. Flip only R3's `authentication local` to `mldsa-sig`, so R3 signs with
ML-DSA while R2 still signs with RSA, and the tunnel dies:

```
R3(config-ikev2-profile)# authentication local mldsa-sig

R3# clear crypto ikev2 sa
R3# ping 192.168.102.1
.....
Success rate is 0 percent (0/5)

R3# show logging | include IKEv2
IKEv2-ERROR:(SESSION ID = 71,SA ID = 1):: Auth exchange failed
```

Both peers listed both methods and it still failed. The two ends have to
present the *same* signature type, so **the authentication cutover is atomic per peer**.
Change `authentication local` on both ends and the tunnel comes straight back:

```
R2(config-ikev2-profile)# authentication local mldsa-sig

R2# show crypto ikev2 sa | include Auth sign
      Encr: ... DH Grp:20, Auth sign: MLDSA, Auth verify: MLDSA
      Encr: ... DH Grp:20, Auth sign: MLDSA, Auth verify: MLDSA
```

### What this means for planning

| | Key exchange (ML-KEM) | Authentication (ML-DSA) |
|---|---|---|
| Negotiated per session | yes, `optional` falls back | no, configured per profile |
| Mixed-version peers | fine, hub-first rollout works | fine, but needs one profile per peer |
| Cutting a peer over | no outage, just renegotiate | brief outage, both ends together |
| Rollout unit | the whole hub at once | one peer at a time |

The practical recipe: keep one IKEv2 profile per peer (or per migration group) on the hub,
stage the certificates everywhere first, then flip peers in maintenance windows. Don't
expect the `optional` trick from Exercise 4 to save you here. And once every peer is
migrated, drop the leftover `authentication remote rsa-sig` lines so a downgrade isn't
silently accepted.

## Things that will bite you

A few findings from running this on 26.2:

**`mldsakeypair` fails with "Invalid input" on early 26.2.x builds.** On some pre-GA
images the ML-DSA trustpoint CLI is gated behind `service internal`, a debug-only global
that should not be required once ML-DSA is GA. If you see this:

```
R1(config)# crypto pki trustpoint TP-MLDSA65
R1(ca-trustpoint)# mldsakeypair TP-MLDSA65 65
                    ^
% Invalid input detected at '^' marker.
```

turn it on for the session, retry, and turn it back off when you're done:

```
R1(config)# service internal
R1(config)# crypto pki trustpoint TP-MLDSA65
R1(ca-trustpoint)# mldsakeypair TP-MLDSA65 65
R1(ca-trustpoint)#
...
R1(config)# no service internal
```

The PKCS#12 import path in Exercise 5 above did not need this on the build we verified,
but the automation layer and any hand-edited `mldsakeypair` line can hit it on earlier
images.

**Importing a certificate can lock you out of SSH.** During testing, importing a
PKCS#12 bundle caused the router to start advertising a new SSH host key
algorithm and then fail every handshake that selected it:

```
$ ssh admin@<R1-mgmt-ip>
debug1: kex: host key algorithm: ecdsa-sha2-nistp256
Connection closed by 198.18.154.202 port 22
```

Console access saves you, or you get in by refusing the broken algorithm:

```
$ ssh -o HostKeyAlgorithms=rsa-sha2-512 admin@<R1-mgmt-ip>
```

Then pin it properly so it can't happen again:

```
R1(config)# ip ssh server algorithm hostkey rsa-sha2-512 rsa-sha2-256
```

If you're importing certificates over SSH into a box you can't reach a console on, pin the
host key algorithm *before* you start.

**`ecdsa-sig` did not work on this build.** A P-256 chain that OpenSSL validates fine, with
the IP SAN present and both EKUs set, never completed `IKE_AUTH`:

```
IKEv2-ERROR:(SESSION ID = 26,SA ID = 1):: Auth exchange failed
```

Signing with SHA-256 instead of SHA-512, and setting `hash sha256` on the trustpoint,
changed nothing. An RSA-2048 chain built by the identical process worked first time, which
is why the size table above uses RSA-2048 as the classical baseline rather than ECDSA.

**Clean up the bundles.** A PKCS#12 file holds the private key. Once the trustpoint is
imported, the bundle on `bootflash:` is a liability:

```
R1# delete /force bootflash:/mldsa65-r1.p12
R1# write memory
```

## The working configs

The full sanitized running configs for the end state are checked in, so you can diff yours
against something known to work:

| File | Role | Tunnels |
|---|---|---|
| [`R1-mldsa.txt`](device-configs/R1-mldsa.txt) | spoke 1 | Tunnel0 to R3, Tunnel1 to R2 |
| [`R2-mldsa-hub.txt`](device-configs/R2-mldsa-hub.txt) | hub | Tunnel1 to R1, Tunnel2 to R3 |
| [`R3-mldsa.txt`](device-configs/R3-mldsa.txt) | spoke 2 | Tunnel0 to R1, Tunnel2 to R2 |

All six tunnel legs report `Auth sign: MLDSA` and `PQC Key Exchange: ML-KEM-768`. Password
hashes, PSKs, PPKs, the chassis serial and the certificate bodies are stripped; rebuild the
PKI with `gen-mldsa-certs.sh` and everything else applies as-is.

## The automated version

Now that you know what these commands do, there's an
[Ansible layer](automation/README.md) that pushes the same intent over NETCONF: a baseline
tunnel, then PPK, ML-KEM or ML-DSA as separate overlays you add and remove independently.
Notice which parts of this doc it *can't* express as structured data, because that's the
interesting bit: the config is all modelled, and every exec-mode verb you typed above
(`clear crypto ikev2 sa`, `crypto pki enroll`) still goes over CLI.
[DESIGN.md](automation/DESIGN.md) has the honest accounting.

---

**Cleanup:** nothing here undoes itself, and `revocation-check none` is a setting you don't
want to leave on a real box. [Putting the routers back](README.md#putting-the-routers-back)
has the full teardown in dependency order. Do it after the other docs if you're carrying on,
since they build on this underlay. Next: [SSH](ssh.md).

