# MACsec / 802.1X on IOS XE

> **Pre-req:** this doc assumes you have gone through the container [MACsec lab](../../learn/macsec/README.md), where an 802.1X/EAP-TLS handshake carried ML-KEM and ML-DSA at Layer 2.

Every command and every piece of output below was run end to end on IOS XE 26.2 on a pair
of C8235-G2 routers, including the failure experiments, which were reproduced by breaking a
working setup and putting it back.

## Interface mode vs key source

MACsec on IOS XE has two independent dimensions:

**1. Interface mode** (how peers relate):

| | Network-link | Access |
|-|--------------|--------|
| **IOS XE keyword** | `macsec network-link` | `macsec` |
| **Topology** | Two equal network devices (router ↔ router, switch ↔ switch) | Host ↔ switch port (endpoint admission) |
| **dot1x roles** | Both ends run supplicant *and* authenticator (`dot1x pae both`) | One side is the supplicant, the other is the authenticator |
| **PKI** | Both peers need identity certs from the same (or cross-trusted) CA; each peer's cert needs *both* `server-auth` and `client-auth` EKUs (Extended Key Usage) because it plays both TLS roles | The supplicant needs a client cert; the authenticator needs a server cert (or delegates to a RADIUS/ISE backend) |
| **Config symmetry** | Identical on both ends (same policy-map, same EAP profile, same `dot1x pae both`) | Asymmetric: the authenticator has the control policy and EAP server config; the supplicant just has credentials |
| **Real-world use** | DC interconnects (leaf ↔ spine), L2VPN / Carrier Ethernet, WAN router-to-router, 5G fronthaul/backhaul | Campus access ports, endpoint admission (laptop → switch), wireless AP uplinks |

**2. Key source** (how peers agree on the key):

| | PSK | EAP-TLS |
|-|-----|--------|
| **How it works** | Same hex key configured on both ends | 802.1X handshake derives the MSK → CAK dynamically |
| **PQ story** | Quantum-safe: no key exchange to attack | Quantum-safe: ML-KEM inside TLS 1.3 protects the key derivation |
| **Requires** | Just the two routers | Identity certificates + a local CA (IOS XE does the EAP itself) |

These are independent choices. Both exercises below use **network-link** mode (router-to-router),
with different key sources:

- **Exercise 1:** network-link + PSK (quantum-safe, no key exchange to break)
- **Exercise 2:** network-link + EAP-TLS with ML-KEM (quantum-safe with dynamic keying)

The [container lab](../../learn/macsec/README.md) covers the complementary case: **access
mode** (a supplicant authenticating to an authenticator, like a host to a switch port). The
EAP-TLS handshake and the ML-KEM key exchange work the same way in both modes. What
differs is the config shape: network-link is symmetric (identical on both ends), access
mode is asymmetric (one authenticator, one supplicant), and each mode has different
certificate EKU requirements.

## Network design

MACsec is hop-by-hop encryption at Layer 2. You need a direct physical link between the
two encrypting peers. Both exercises below use the R1 Tw0/0/0 ↔ R2 Tw0/0/0 link (access
VLAN 12, with `Vlan12` SVIs at 10.0.12.1 and 10.0.12.2 so there is routed traffic to
watch). R2 is the natural choice for the local CA in Exercise 2 because it sits in the
middle of the topology.

### If you still have the IPsec tunnels up

Nothing to undo. MACsec needs the VLANs and SVIs from
[Set up the underlay](README.md#set-up-the-underlay), and the two
features sit at different layers on the same wire, so they compose without conflicting.
IPsec is Layer 3 and end-to-end between the crypto endpoints; MACsec is Layer 2 and
strictly hop-by-hop on one physical link. Neither knows the other exists.

What that means in practice depends on which traffic you send:

| Traffic | Encrypted by |
|---------|--------------|
| `ping 10.0.12.2 source Vlan12` (the verification pings below) | MACsec only. Both SVIs are on the same `/30`, so nothing routes into a tunnel |
| `ping 192.168.100.2 source Tunnel0` (the IPsec overlay) | Both. ESP wraps the payload first, then MACsec encrypts the whole frame for the R1→R2 hop |

For the tunnelled case R2 strips MACsec on ingress, forwards the ESP packet on its outer IP
without being able to read into it, and the R2→R3 hop carries ESP alone because there is no
MACsec configured there.

You don't have to take that on faith. With MACsec secured and Tunnel0 up, send 100 pings
down each path and watch both counter sets move independently. ESP comes from
`show crypto ipsec sa peer 10.0.23.2`, MACsec from `show macsec statistics interface Tw0/0/0`:

| 100 pings, 1000 bytes | ESP encaps / decaps | MACsec out / in |
|---|---|---|
| `ping 192.168.100.2 source Tunnel0` | **+100 / +100** | **+102 / +102** |
| `ping 10.0.12.2 source Vlan12` | +0 / +0 | +101 / +100 |

Tunnel traffic increments both, which is the double encryption. SVI traffic increments only
MACsec and never touches the IPsec SA. The couple of extra MACsec frames are MKA control
traffic, which keeps ticking in the background whatever you do.

Double-encrypting tunnel traffic buys you very little, since ESP already protects it end to
end. MACsec's value on this link is everything IPsec doesn't cover: the SVI-to-SVI traffic,
ARP, routing protocol hellos, CDP/LLDP, and the Layer 2 headers themselves. Watch the MTU
though. MACsec adds 24-32 bytes (SecTAG plus a 16-byte ICV) and ESP-GCM in tunnel mode adds
roughly 50, so stacking them costs about 85 bytes off a 1500-byte path. The `size 1400`
pings below fit comfortably; a full-size 1500-byte payload would not.

---

## Exercise 1: Network-link MACsec with PSK

This exercise demonstrates MACsec mechanics on IOS XE: how to configure it, verify
sessions, and confirm encryption is active. The key is pre-shared (manually provisioned),
so there is no key exchange protocol involved at all.

This is the MACsec equivalent of
[IPsec with PPK](ipsec.md#exercise-2-post-quantum-pre-shared-key-ppk). Both are
quantum-safe: the key agreement never touches a public-key exchange, so there is nothing
for a quantum computer to attack.
IPsec PPK *mixes* the pre-shared secret into a DH-derived key, so you get ephemeral
keying (compromise the PSK later and past sessions are still safe). MACsec PSK *is* the
key (no DH). If the PSK leaks, every session that used it is exposed. Both are
deployable today without waiting for ML-KEM.

Note: for router-to-router MACsec, use `macsec network-link`.
The plain `macsec` command is for access ports (host-facing). This matters more than it
looks, and Exercise 2 shows exactly how it bites you.

### Configuration

**On both R1 and R2:**

```
mka policy MACSEC-POLICY
 macsec-cipher-suite gcm-aes-256

! --- CKN is derived from the key ID (01 here), CAK from the key-string ---
key chain MACSEC-KC macsec
 key 01
  cryptographic-algorithm aes-256-cmac
  key-string <64 hex chars, same on both routers>
  lifetime local 00:00:00 Jan 1 2020 infinite
!
interface TwoGigabitEthernet0/0/0
 macsec network-link            ! peer-to-peer mode (not access)
 mka policy MACSEC-POLICY
 mka pre-shared-key key-chain MACSEC-KC
```

Generate the key-string with something like `openssl rand -hex 32`, and don't commit it
anywhere. It *is* the CAK.

26.2 will tell you as much, both at the prompt and in the log, the moment you paste it in:

```
%SYS-4-INSECURE_WARNING: Module: MACSEC - Command: key-string * - Reason: Sensitive
information like passwords and keys are stored using weak or no protection -
Remediation: Please consider migrating to a secure alternative such as Type-6 or Type-9
```

It's a fair complaint. A plaintext `key-string` sits readable in the running config, so
anyone who can `show run` has your CAK. Type-6 encryption (which needs a primary key
configured) is the production answer. For a lab on a link you're about to tear down it
doesn't matter, so this doc leaves it plaintext and takes the warning.

### Verification

```
R1# show mka sessions
Total MKA Sessions....... 1
      Secured Sessions... 1
      Pending Sessions... 0

Interface       Local-TxSCI          Policy-Name       Inherited          Key-Server
Port-ID         Peer-RxSCI           MACsec-Peers      Status             CKN
============================================================================================
Tw0/0/0         2481.3b87.54e0/0009  MACSEC-POLICY     NO                 NO
9               2481.3b87.5060/0009  1                 Secured            01

R1# show macsec status interface Tw0/0/0 | include Cipher:|Transmitting
  Cipher:                   GCM-AES-256
  Transmitting:             TRUE
```

The CKN is `01`, straight from the key ID you typed. Remember that: it's how you tell a
PSK session from an EAP-derived one at a glance.

Now prove the frames are actually encrypted, don't just trust the "Secured" status.

Read the counters *before* and *after* the traffic, not just after. MKA sends control frames
(MKPDUs) every couple of seconds from the moment the session secures, and those go through
the same MACsec engine, so any single snapshot is really telling you how long you waited
before typing the command. The delta is the number that means something:

```
R1# show macsec statistics interface Tw0/0/0
  Out Pkts Encrypted:       27
  In Pkts OK:               27

R1# ping 10.0.12.2 source Vlan12 repeat 20 size 1400
Success rate is 100 percent (20/20), round-trip min/avg/max = 1/1/4 ms

R1# show macsec statistics interface Tw0/0/0
  Out Pkts Encrypted:       47
  In Pkts OK:               47
  In Pkts Invalid:          0
```

Exactly 20 out encrypted, 20 in validated, zero invalid. Every ping went through the
crypto engine and every one came back verified.

### What this gives you (and what it doesn't)

This is quantum-safe. There is no public-key exchange for a quantum computer to break:
the CAK is a symmetric secret you provisioned on both ends, and the frame encryption
(GCM-AES-256) has a 128-bit quantum work factor under Grover, which is well out of reach.

What you *don't* get is forward secrecy. Every session uses the same static CAK, so if
that key ever leaks, all past and future traffic is exposed until you rotate it. Exercise 2
solves that: EAP-TLS derives a fresh CAK on every handshake, and ML-KEM makes that
derivation quantum-safe too.

### Cleaning up before Exercise 2

Exercise 2 uses the same interface with a completely different key source. Remove the PSK
overlay before you start, or the port will carry conflicting configs:

```
interface TwoGigabitEthernet0/0/0
 no macsec network-link
 no mka policy MACSEC-POLICY
 no mka pre-shared-key key-chain MACSEC-KC
```

You can leave the `mka policy MACSEC-POLICY` and `key chain MACSEC-KC` global objects in
place; they won't interfere once they're off the interface.

---

## Exercise 2: Post-quantum MACsec (EAP-TLS with ML-KEM)

This is the real PQ path. Instead of a static PSK, the two routers run a full
802.1X/EAP-TLS handshake to derive the MACsec keys. EAP-TLS runs a TLS 1.3 handshake
with ML-KEM for key exchange, and the resulting master key (MSK) feeds into MKA to
produce the per-frame encryption key (SAK). The entire key chain
(MSK → CAK → SAK) inherits quantum safety from that single handshake.

### Two routers and a cable

IOS XE does **local** EAP-TLS: one of the routers runs a small CA, both routers
enrol against it, and each router's own session manager (`smd`) acts as the EAP server
for its authenticator role.

You can see it in the trace, where the EAP authenticator hands off to a local method:

```
[eap-auth] (debug): Setting authentication mode: Local
[eap] (debug): Received context create from LL (AAA_LOCAL_EAP)
[eap-auth] (debug): SUCCESS for EAP method name: EAP-TLS
```

Don't go looking for those lines in `show logging`, they aren't there. EAP-TLS runs inside
`smd`, a separate process from IOSd, so it writes to the platform trace instead. You have to
raise the trace level first, then force a handshake, then read the buffer back:

```
R1# set platform software trace smd R0 eap debug
R1# set platform software trace smd R0 eap-all debug

! force a handshake, e.g. flap the link from the other end
R2(config-if)# shutdown
R2(config-if)# no shutdown

R1# show logging process smd internal start last 2 minutes | include authentication mode|AAA_LOCAL_EAP|EAP method name

! put the trace levels back when you're done
R1# set platform software trace smd R0 eap notice
R1# set platform software trace smd R0 eap-all notice
```

Come back to this once the config is in place. The same mechanism is what proves ML-KEM was
used, covered in [proving ML-KEM was actually used](#proving-ml-kem-was-actually-used).

That means the whole exercise needs exactly two routers and a cable.

### Step 1: the CA and the certificates

R2 runs the CA. Enrolment happens over SCEP, which the IOS HTTP server provides, so
`ip http server` has to be on.

**On R2 only:**

```
ip http server
!
crypto pki server CA_Server
 no database archive
 grant auto
 eku server-auth client-auth        ! <<< both EKUs, see below
 no shutdown
```

`no shutdown` will prompt you for a passphrase to protect the CA's private key. Enter
one (anything you'll remember) and confirm it. **Pressing Return at the password prompt
aborts the CA startup** ("% Aborted."), which is not obvious from the prompt text
("or type Return to exit" means exit the `no shutdown`, not "no passphrase"). Wait a few
seconds after `% Certificate Server enabled.` for the CA certificate to generate.

`eku server-auth client-auth` is not optional. Each router is simultaneously a TLS
*client* (its supplicant role) and a TLS *server* (its authenticator role), presenting the
same certificate for both. A cert with only `client-auth` will authenticate one direction
and fail the other.

**Set the clock first, or the CA won't start at all.** On a router whose clock was never
set authoritatively, `no shutdown` takes your passphrase and then refuses:

```
% Time has not been set. Cannot start the Certificate server
```

`show crypto pki server` then reports `Status: disabled, Time has not been set` and
`State: check failed`. The tell is in `show clock`: a leading `*` means the time is not
authoritative.

```
R2# show clock
*16:56:05.692 UTC Sat Aug 29 2026      <<< the asterisk is the problem
```

Fix it on both routers with `clock set <hh:mm:ss> <day> <month> <year>` (or point them at
NTP, which is what you'd do for real), then `shut` / `no shutdown` the server to retry. It
won't ask for the passphrase again, it kept the one you already gave it.

Do both routers, not just the CA. Certificate validity is absolute time, so clocks that
disagree produce "certificate not yet valid" failures during EAP-TLS, long after enrolment
appeared to succeed.

**On both routers** (the `subject-name` differs):

```
crypto pki trustpoint CA_TP
 enrollment url http://10.0.12.2:80
 subject-name CN=R1                 ! CN=R2 on the other router
 revocation-check none
 rsakeypair CA_TP 2048
 hash sha512
```

Before you authenticate, grab the CA's fingerprint so you can pin it in the trustpoint.
Without it, IOS XE rejects the CA cert ("Trustpoint fingerprint must be supplied"):

```
R2# show crypto pki server | include fingerprint
    CA cert fingerprint: 4E5658EA 99350A9A 5AFF2EB9 C8114485
```

Add it, with the spaces stripped (your fingerprint will differ):

```
crypto pki trustpoint CA_TP
 fingerprint 4E5658EA99350A9A5AFF2EB9C8114485
```

Then enrol:

```
crypto pki authenticate CA_TP       ! accept the CA fingerprint
crypto pki enroll CA_TP
```

`authenticate` checks the fingerprint you pinned against the one the CA presents and says so:

```
Trustpoint Fingerprint: 4E5658EA 99350A9A 5AFF2EB9 C8114485
Certificate validated - fingerprints matched.
Trustpoint CA certificate accepted.
```

`enroll` on 26.2 is quieter than you might expect. With `grant auto` on the CA it asks
nothing at all, no challenge password, no serial number or IP address questions, no final
confirmation. It just goes:

```
% Start certificate enrollment ..
% The subject name in the certificate will include: CN=R1
% The subject name in the certificate will include: bfl-cpoc-d14-8235-01
% Certificate request sent to Certificate Authority
```

Check what you got. The CN is the identity EAP will present, so it has to match the
`dot1x credentials` username you configure next:

```
R2# show crypto pki certificates CA_TP
Certificate
  Status: Available
  Certificate Serial Number (hex): 02
  Issuer:
    cn=CA_Server
  Subject:
    Name: bfl-cpoc-d14-8235-02.lab.local
    unstructuredname=bfl-cpoc-d14-8235-02.lab.local
    cn=R2
  Validity Date:
    start date: 16:58:04 UTC Aug 29 2026
    end   date: 16:58:04 UTC Aug 29 2027
```

Worth confirming the EKU landed, since it's the one CA setting you can't fix after the fact
without re-issuing. `show crypto pki certificates verbose CA_TP` spells it out:

```
    Extended Key Usage:
        Client Auth
        Server Auth
```

Authentication is still RSA-2048 here, and it stays that way on 26.2. See
[what about authentication](#what-about-authentication-ml-dsa) below for what changed and
what didn't.

### Step 2: AAA, EAP and the subscriber control policy

Symmetric on both routers apart from the identity strings (`R1` / `R2`):

```
! --- local AAA: dot1x authenticates and authorizes against the local box ---
aaa new-model
aaa authentication dot1x default local
aaa authorization network default local
aaa authorization credential-download default local
dot1x system-auth-control

! --- the link must be MACsec-protected before the port authorizes ---
aaa attribute list LINKSEC_POLICY
 attribute type linksec-policy must-secure
username R1 aaa attribute list LINKSEC_POLICY

! --- which certificate EAP-TLS presents, in both roles ---
eap profile EAP-PROFILE
 method tls
 pki-trustpoint CA_TP
!
dot1x credentials DOT1X-CREDS
 username R1
 pki-trustpoint CA_TP

! --- PQ knobs: pin TLS 1.3 and pick the key-exchange family ---
access-session tls-version 1.3
access-session pqc-type hybrid

! --- MACsec cipher: default policy would give you GCM-AES-128 ---
mka policy PQ-MACSEC-MKA
 macsec-cipher-suite gcm-aes-256

! --- the control policy that drives the session ---
policy-map type control subscriber PQ-MACSEC-POLICY
 event session-started match-all
  10 class always do-until-failure
   10 authenticate using dot1x both        ! <<< the 'both' is load-bearing
 event authentication-success match-all
  10 class always do-until-failure
   10 activate service-template DEFAULT_LINKSEC_POLICY_MUST_SECURE
```

### Step 3: the interface

**Do not apply this config until both routers have valid certificates** (check with
`show crypto pki certificates CA_TP`). `access-session port-control auto` with
`access-session closed` locks the port immediately. If EAP-TLS can't complete (no certs),
the port stays unauthorized, Vlan12 goes down, and you lose SCEP connectivity to the CA.
That's a deadlock you can only break by removing the interface config again.

Same on both routers:

```
interface TwoGigabitEthernet0/0/0
 macsec network-link                       ! <<< network-link, NOT plain macsec
 authentication periodic
 authentication timer reauthenticate 1800
 access-session host-mode multi-host
 access-session closed
 access-session port-control auto
 dot1x pae both
 dot1x authenticator eap profile EAP-PROFILE
 dot1x supplicant eap profile EAP-PROFILE
 dot1x credentials DOT1X-CREDS
 mka policy PQ-MACSEC-MKA
 service-policy type control subscriber PQ-MACSEC-POLICY
```

`dot1x pae both` means each router is simultaneously supplicant and authenticator, so the
config stays symmetric and you don't have to decide who initiates.

**Give the first bring-up a couple of minutes before you believe it's broken.** Because both
ends come up at once and each is trying to authenticate the other, the very first
negotiation churns: it secures, Auth-Mgr tears it down, it starts again with a fresh CKN,
sometimes hits a session timeout. Checking after 45 seconds showed `Total MKA Sessions 0`
and a session sitting at `Unauthorized` with *both* methods already reporting
`Authc Success`, which looks like a policy bug and isn't one. It settled on its own about
two minutes in and stayed up. Later flaps re-secure in about 5 seconds; it's only the cold
start that thrashes.

The full running configs are in [`device-configs/`](device-configs/) if you want to diff
yours against a known-good state.

### Three ways to get this wrong

I burned a lot of time on this, so here are the three lines that decide whether it works,
each verified by removing it from a working setup and watching the session break.

**1. `10 authenticate using dot1x both`**

Drop the `both` and everything *looks* fine at the authentication layer, but MKA never
secures:

```
! with "10 authenticate using dot1x"  (no 'both')
R1# show mka sessions
Total MKA Sessions....... 0
      Secured Sessions... 0

R1# show access-session interface Tw0/0/0 details | include Status:|dot1x
               Status:  Unauthorized
        dot1x           Authc Success
```

That method list is the diagnostic. `dot1x` succeeded, but `dot1xSup` is *absent
entirely*: only one PAE role is running. Without `both`, each router authenticates the
other independently and derives its own MSK, so the two ends never agree on a single
CAK/CKN. Watch the syslog and you see a fresh random CKN every cycle, forever:

```
17:33:42: %MKA-5-SESSION_START: (Tw0/0/0 : 2) MKA Session started ... CKN FABDD261881F3C4799118AE194BC737A
17:33:50: %MKA-4-KEEPALIVE_TIMEOUT: (Tw0/0/0 : 2) Peer has stopped sending MKPDUs ...
17:33:50: %MKA-4-SESSION_UNSECURED: (Tw0/0/0 : 2) MKA Session was stopped by MKA and not secured ...
17:34:50: %MKA-5-SESSION_START: (Tw0/0/0 : 2) MKA Session started ... CKN 79AE42EF42FFE10385126CDDDCC73532
17:34:58: %MKA-4-KEEPALIVE_TIMEOUT: (Tw0/0/0 : 2) Peer has stopped sending MKPDUs ...
17:35:58: %MKA-5-SESSION_START: (Tw0/0/0 : 2) MKA Session started ... CKN BABF64D0380ABBCCFB45E981DB038ED5
17:36:06: %MKA-4-KEEPALIVE_TIMEOUT: (Tw0/0/0 : 2) Peer has stopped sending MKPDUs ...
17:37:07: %MKA-5-SESSION_START: (Tw0/0/0 : 2) MKA Session started ... CKN 554217B1B91741322B42DCA00E651DD1
17:37:15: %MKA-4-KEEPALIVE_TIMEOUT: (Tw0/0/0 : 2) Peer has stopped sending MKPDUs ...
```

Eight seconds to keepalive timeout, sixty seconds until the next attempt, a different CKN
every time, and it will do that forever. Note there are no ICV or validation failures
logged: each side discards the peer's MKPDUs before validation because they belong to a CA
it doesn't know.

Put the `both` back and `dot1xSup` reappears, one CKN is agreed, and it secures in six
seconds:

```
17:37:58: %MKA-5-SESSION_START: (Tw0/0/0 : 2) MKA Session started ...
17:38:04: %MKA-5-SESSION_SECURED: (Tw0/0/0 : 2) MKA Session was secured ... CKN ED2809ABE379BAA98ABF38779EDC41C4
```

**2. `dot1x authenticator eap profile EAP-PROFILE` on *both* routers**

Easy to add the supplicant profile and forget the authenticator one. The failure is
readable, at least: the side missing it can't act as EAP server, so its authenticator role
fails while its supplicant role still succeeds.

```
! R2 without "dot1x authenticator eap profile"
R2# show mka sessions | include Total MKA|Secured
Total MKA Sessions....... 0
      Secured Sessions... 0

R2# show access-session interface Tw0/0/0 details | include Status:|dot1x
               Status:  Unauthorized
        dot1x           Authc Failed
     dot1xSup           Authc Success
```

Check the *other* router too, because that's where you'd probably start looking. R1 is
configured correctly and still can't come up, and its method list tells you why:

```
R1# show access-session interface Tw0/0/0 details | include Status:|dot1x
               Status:  Unauthorized
        dot1x           Authc Success
     dot1xSup           Running
```

`dot1xSup` stuck at `Running` means R1's supplicant is waiting for an EAP server that is
never going to answer. A supplicant parked at `Running` on a healthy router is a good hint
to go look at the peer's authenticator config.

**3. `macsec network-link`, not `macsec`**

This is the nastiest one, because status commands might not be clear enough. With `macsec` on a router-to-router switchport, MKA secures, the
session authorizes, the SAK installs, and nothing is encrypted:

```
! with plain "macsec"
R1# show mka sessions | include Secured
      Secured Sessions... 1
R1# show access-session interface Tw0/0/0 details | include Security Status
      Security Status:  Link Secured
```

Looks perfect. Then you send traffic and read the counters on R2, before and after:

```
                                  before ping    after 300 pings
R2# show interfaces Tw0/0/0
  packets input                       59583          59899      <<< +316, traffic crossed
R2# show macsec statistics interface Tw0/0/0
  Ingress Decrypted Octets:               0              0
  Egress Encrypted Octets:               48             48      <<< nothing encrypted
  Out Pkts Encrypted:                     1              1
  In Pkts OK:                             0              0
  Ingress Untag Pkts:                     0              0
  Ingress No Tag Pkts:                    0              0
```

The pings all succeeded, 300 for 300. And 316 packets crossed the wire without a single one
going through the MACsec engine: every crypto counter has a delta of exactly zero. Notice
`Ingress Untag Pkts` and `Ingress No Tag Pkts` stay at zero too, so the SecY isn't even
*inspecting* the arriving frames, let alone rejecting them. A `must-secure` policy reporting
`Link Secured` is protecting nothing here.

**Never accept `show mka sessions` as proof of encryption. Always check `show macsec
statistics`.**

Switching to `macsec network-link` (you have to `no macsec` first, IOS XE won't let you set
both) fixes it immediately, with the same dot1x config otherwise.

### Verification

The end state, on both routers:

```
R1# show mka sessions
Total MKA Sessions....... 1
      Secured Sessions... 1
      Pending Sessions... 0

Interface       Local-TxSCI          Policy-Name       Inherited          Key-Server
Port-ID         Peer-RxSCI           MACsec-Peers      Status             CKN
============================================================================================
Tw0/0/0         2481.3b87.54e0/0002  PQ-MACSEC-MKA     NO                 NO
2               2481.3b87.5060/0002  1                 Secured            99CDD89B62A61759656F9C8430FB9C9E
```

That CKN is the tell. Compare with Exercise 1:

| | PSK (Exercise 1) | EAP-TLS (Exercise 2) |
|-|-------------------|----------------------|
| CKN | `01`, from the key-chain key ID | 32 hex chars, derived from the EAP-TLS MSK, new on every re-auth |
| Policy-Name | `MACSEC-POLICY` | `PQ-MACSEC-MKA` |
| Key source | static config | dynamic (MSK from TLS 1.3 with ML-KEM) |

Flap the link and the CKN changes every time. That's the freshness you don't get from a PSK.

The session view shows both PAE roles succeeded and the linksec policy was applied:

```
R1# show access-session interface Tw0/0/0 details
          MAC Address:  2481.3b87.5060
            User-Name:  R2
               Status:  Authorized
               Domain:  DATA
       Oper host mode:  multi-host
     Oper control dir:  both
      Session timeout:  1800s (local), Remaining: 1759s
       Timeout action:  Reauthenticate
       Current Policy:  PQ-MACSEC-POLICY

Local Policies:
	Service Template: DEFAULT_LINKSEC_POLICY_MUST_SECURE (priority 150)
      Security Policy:  Must Secure
      Security Status:  Link Secured

Method status list:
       Method           State
        dot1x           Authc Success
     dot1xSup           Authc Success
```

Both method roles succeeded, and the `must-secure` template was applied by the
authentication-success event.

If you want to see the two PAE roles as separate machines rather than two rows in a list,
`show dot1x` breaks them out:

```
R1# show dot1x interface Tw0/0/0 detail
PAE                       = BOTH
QuietPeriod               = 60
SuppTimeout               = 30
TxPeriod                  = 30
EAP profile               = EAP-PROFILE
Dot1x Info for
--------------------------------------------
PAE                       = SUPPLICANT
StartPeriod               = 30
HeldPeriod                = 60
MaxStart                  = 3
Credentials profile       = DOT1X-CREDS

Dot1x Authenticator Client List
-------------------------------
EAP Method                = TLS
Supplicant                = 2481.3b87.5060
    Auth SM State         = AUTHENTICATED
```

`PAE = BOTH` up top, then a separate supplicant block with its own timers, then the
authenticator's client list showing the peer it authenticated and the method it used. This
is the view that makes failure experiment 1 obvious: without `both`, the supplicant block
and one of these lists simply aren't there.

And the data plane, which is the part that actually matters:

```
R1# show macsec status interface Tw0/0/0 | include Cipher:|Transmitting|Receiving
  Cipher:                   GCM-AES-256
  Transmitting:             TRUE
  Receiving:                TRUE

R1# show macsec statistics interface Tw0/0/0
  Out Pkts Encrypted:       22
  In Pkts OK:               25

R1# ping 10.0.12.2 source Vlan12 repeat 300 size 1400
Success rate is 100 percent (300/300), round-trip min/avg/max = 1/1/4 ms

R1# show macsec statistics interface Tw0/0/0
  Ingress Untag Pkts:       0
  Ingress No Tag Pkts:      0
  Ingress Bad Tag Pkts:     0
  Egress Untag Pkts:        0
  Out Pkts Encrypted:       325
  In Pkts OK:               328
  In Pkts Invalid:          0
  In Pkts Not Valid:        0
```

Same before-and-after read as Exercise 1, and the same reason for it. 303 out and 303 in
for 300 pings, the extra three being MKPDUs that happened to land inside the window.
Encrypted out, validated in, nothing untagged, nothing invalid. That's a real MACsec link
keyed by a post-quantum handshake.

### Proving ML-KEM was actually used

Everything above would look identical with classical ECDHE, so let's go get the evidence.

The EAP-TLS handshake runs in `smd` (the session manager), not in IOSd, which is why
`debug ssl openssl` and `debug eap all` produce nothing in `show logging`. You need the
platform trace instead:

```
! raise the trace level
R1# set platform software trace smd R0 eap debug
R1# set platform software trace smd R0 eap-all debug

! force a fresh handshake (flap the link from the other end)
R2(config-if)# shutdown
R2(config-if)# no shutdown

! read it back
R1# show logging process smd internal start last 2 minutes | include Using PQC type:|Negotiated Group|Negotiated TLS version|Selected ciphersuite
TLS:Using PQC type: hybrid
TLS:Negotiated Group (key exchange algorithm):x448_mlkem768
TLS:Negotiated TLS version:TLSv1.3
TLS:Selected ciphersuite:TLS_AES_128_GCM_SHA256
```

`Negotiated Group ... x448_mlkem768` is the proof. Remember to put the trace levels back
to `notice` when you're done.

### Which `pqc-type` should you pick?

`access-session pqc-type` takes four values. I set each one on both routers in turn,
flapped the link, and read the negotiated group out of the trace:

| `pqc-type` | Negotiated group | Post-quantum? |
|------------|------------------|---------------|
| `pqc` | `mlkem512` | Yes, but only ML-KEM-512 |
| `hybrid` | `x448_mlkem768` | Yes, ML-KEM-768 + X448 |
| `non-pqc` | `secp521r1` | No |
| `all` | `x448_mlkem768` | Yes, when both peers support it |

All four secured MKA, and all four negotiated TLS 1.3 with `TLS_AES_128_GCM_SHA256`.

`pqc` is the *weaker* PQ choice on this release: pure mode lands on
ML-KEM-512, while hybrid mode gets you ML-KEM-768. NIST's own guidance and most deployment
advice treat ML-KEM-768 as the baseline and ML-KEM-512 as something you would accept
deliberately. So **use `hybrid`**: you get the stronger lattice parameter
set *and* you keep a classical X448 exchange mixed in, which means a flaw in either
component alone doesn't sink the session. There is no CLI to select the ML-KEM parameter
set directly.

`all` behaves like `hybrid` when both ends are IOS XE, which makes it look like a safe
default. It isn't, and it's easy to demonstrate why. Leave R1 on `all`, set R2 to
`non-pqc`, flap the link, and read the trace:

| R1 | R2 | Negotiated group | MKA |
|---|---|---|---|
| `all` | `non-pqc` | `secp521r1` | **Secured** |
| `hybrid` | `non-pqc` | none | **0 secured sessions** |

With `all`, R1 quietly dropped to a classical P-521 exchange and brought the link up
looking exactly like a healthy session: `Secured`, `Link Secured`, GCM-AES-256 on the wire.
Nothing in the syslog says you just lost your post-quantum protection. You only find out by
reading the negotiated group out of the `smd` trace, which nobody does routinely.

With `hybrid`, the handshake simply fails. No group is negotiated, MKA never secures, and
the port stays unauthorized. That's the behaviour you want: **pin `hybrid`** and a peer that
can't do ML-KEM gets refused instead of silently downgraded. Use `all` only if you have a
mixed estate and you have consciously decided that a link coming up matters more than that
link being post-quantum.

Use `non-pqc` only as a troubleshooting tool, to confirm a failure isn't PQ-related.

### Rotating the SAK

Nothing above rotates the per-frame key. MKA will replace the SAK when the packet number
space runs out, but at anything short of line rate that's effectively never, so a
long-lived session keeps encrypting with the same SAK until the next re-authentication.

If you want time-based rotation, the MKA policy has an interval, off by default:

```
mka policy PQ-MACSEC-MKA
 sak-rekey interval 60          ! <30-65535> seconds, default 0 = never
```

Sixty seconds is a lab value; pick something sane for production. Changing the policy clears
active sessions on any interface using it, so expect a flap. Once it settles, the rotations
show up in the log:

```
17:26:51: %MKA-6-SAK_REKEY_SUCCESS: ... (new Latest AN/KN 1/2, Old AN/KN 0/1) ... CKN 8EFA96B09E4D8C58492CCB494D214566
17:27:53: %MKA-6-SAK_REKEY_SUCCESS: ... (new Latest AN/KN 2/3, Old AN/KN 1/2) ... CKN 8EFA96B09E4D8C58492CCB494D214566
17:28:55: %MKA-6-SAK_REKEY_SUCCESS: ... (new Latest AN/KN 3/4, Old AN/KN 2/3) ... CKN 8EFA96B09E4D8C58492CCB494D214566
```

Two things in there are worth noticing.

**The CKN doesn't change.** Same value across all three rotations. A SAK rekey is not a
re-authentication: the CAK (and the CKN that names it) survives, and only the per-frame key
is replaced, with the association number and key number stepping up each time. Fresh keying
material from a new ML-KEM handshake is what re-authentication gives you, and that's the
`authentication timer reauthenticate 1800` in the interface config.

**It's hitless.** 20,000 pings at 1400 bytes over 53 seconds, spanning a rotation
(`Latest SAK AN` went 2 to 3), came back `Success rate is 100 percent (20000/20000)`. The
old SAK stays installed for receive while the new one takes over transmit, so nothing is
dropped in the changeover.

One gotcha: don't look for confirmation in the session detail, because it lies.

```
R1# show mka sessions interface Tw0/0/0 detail | include SAK Rekey Time
SAK Rekey Time........... 0s (SAK Rekey interval not applicable)
```

That's the output *while* rotation is happening every 60 seconds. Read `Latest SAK AN` and
`Latest SAK KI (KN)` instead, or just watch the syslog.

### One wrinkle: the key hierarchy is weaker than the data cipher

Every successful session logged this warning, and it turns out to be telling the truth:

```
%MKA-4-MKA_MACSEC_CIPHER_MISMATCH: (Tw0/0/0 : 2) Lower strength MKA-cipher than
macsec-cipher for RxSCI 2481.3b87.5060/0002 ... CKN 99CDD89B62A61759656F9C8430FB9C9E
```

The session detail shows what it means:

```
R1# show mka sessions interface Tw0/0/0 detail | include Cipher Suite|EAP Role
EAP Role................. Supplicant
MKA Cipher Suite......... AES-128-CMAC          <<< protects the SAK distribution
SAK Cipher Suite......... 0080C20001000002 (GCM-AES-256)
```

Frames on the wire get GCM-AES-256, which is what you asked for. But the MKA layer that
distributes and protects those 256-bit SAKs runs AES-128-CMAC, because the CAK derived from
the EAP-TLS MSK is 128 bits. With EAP-TLS the CAK length comes from the MSK and there's no
knob for it on this release.

Exercise 1 is the control that proves this is about the key source and not the platform. Go
back and look at the PSK session, where `cryptographic-algorithm aes-256-cmac` in the key
chain sets the MKA cipher directly:

```
R1# show mka sessions interface Tw0/0/0 detail | include Cipher Suite|EAP Role
EAP Role................. NA
MKA Cipher Suite......... AES-256-CMAC          <<< matches the data cipher
SAK Cipher Suite......... 0080C20001000002 (GCM-AES-256)
```

Matched ciphers, and no `CIPHER_MISMATCH` warning anywhere in the log. So PSK gets you a
256-bit key hierarchy and EAP-TLS doesn't. That's a genuine trade-off against the freshness
and forward secrecy EAP-TLS buys you, not a reason to prefer one over the other.

So the effective security of the key hierarchy is 128-bit, not 256-bit. That's still well
beyond reach classically, and Grover's algorithm only reduces AES-128 to roughly 64-bit
quantum work factor against a *single* key that rotates every re-auth, so this is not a
practical break. Just don't claim "256-bit MACsec end to end" in a design document when the
key wrap is 128-bit.

### What about authentication? (ML-DSA)

The EAP-TLS handshake has two halves, just like everywhere else: key exchange and
authentication. `access-session pqc-type` covers *only* the key exchange. The
authentication half (the certificates that prove identity during EAP-TLS) is still
classical RSA, which is why `rsakeypair CA_TP 2048` is in the config above.

IKEv2 got ML-DSA on 26.2 ([ipsec.md](ipsec.md#exercise-5-ml-dsa-certificate-authentication)),
and the EAP profile happily takes an ML-DSA trustpoint:

```
R1(config)# eap profile EAP-PROFILE
R1(config-eap-profile)# method tls
R1(config-eap-profile)# pki-trustpoint TP-MLDSA65
R1(config-eap-profile)#
```

Don't read anything into that. `pki-trustpoint` accepts any trustpoint name, and the same
command accepts a classical RSA trustpoint identically. The parser proves nothing about
whether the TLS stack inside `smd` can sign with an ML-DSA key.

So I tested it. **It doesn't work.**

**Running the test without taking your link down.** The obvious version of this experiment
kills the port. You don't need MACsec to answer the signature question, though, so leave it
out: configure plain EAP-TLS in *open* mode, with no `access-session closed` and no
`macsec network-link`. Authentication runs, the port forwards regardless of the result, and
your IPsec underlay survives a failed handshake.

Import `mldsa65-r1.p12` and `mldsa65-r2.p12` as `TP-MLDSA65` on both ends
([gen-mldsa-certs.sh](mldsa-certs/gen-mldsa-certs.sh) builds them), point both
`eap profile` and `dot1x credentials` at that trustpoint, pin `access-session tls-version 1.3`,
then flap the link from the far end and read the trace. Run a classical trustpoint through
the same procedure first, because the interesting part is the *difference* between the two.

**What the trace shows.** `smd` accepts the ML-DSA trustpoint and gets as far as building
the SSL context:

```
TLS:Setting up TLS SSL context
CRYPTO_OPSSL: OQS provider loaded.
TLS:Using PQC type: hybrid
TLS:Setting default PKI trustpoint to TP-MLDSA65
TLS:Using Hybrid type
```

Then it stops. `smd` asks IOSd for the certificate chain and never gets an answer. With a
classical trustpoint the same request returns immediately and the handshake proceeds:

```
! RSA trustpoint
[tps-client] (note): Received IOS certchain response. seqnum 21, status 0, no of certs 2 key_name TP-RSA
TLS:SSL context created and initialised
TLS:tls_send: 1453 byte send requested by TLS library

! ML-DSA trustpoint
(nothing)
```

Counting markers across the two traces puts the cut point exactly:

| Trace marker | RSA-2048 | ML-DSA-65 |
|---|---|---|
| `Setting default PKI trustpoint` | 2 | 2 |
| `OQS provider loaded` | 2 | 2 |
| `certchain response` | 2 | **0** |
| `SSL context created and initialised` | 2 | **0** |
| `tls_send` | 7 | **0** |

Not one TLS byte reaches the wire. The session hangs rather than failing: `show
access-session` reports `dot1x Running`, and 30 seconds later `smd` retransmits its EAP
packet. IOSd logs nothing at all for the chain request, so this is a silent stall in the
certificate-retrieval path `smd` uses, not a policy rejection you can configure around.

Watch out for one red herring in that output. `CRYPTO_OPSSL: OQS provider loaded` appears
in **both** runs. That's ML-KEM for key exchange, which Exercise 2 already proved works.
It says nothing about ML-DSA.

**Verified status: not supported.** The trace is the primary evidence, and the control run
closes the loop on it. Exercise 2 above is that control: a clean SCEP-enrolled RSA-2048
certificate from the local CA completes EAP-TLS on 26.2, secures MKA, and passes encrypted
traffic. Same `smd`, same `eap profile`, same interface config, one difference. RSA gets its
certificate chain and finishes; ML-DSA never gets a chain at all.

The other thing that corroborates it: the enrolment model this exercise uses can't produce an
ML-DSA certificate in the first place. The router *can* generate an ML-DSA key on the box
(`crypto key generate mldsa`, exec mode, see
[ipsec.md](ipsec.md#the-router-can-make-the-keys-ask-it-in-the-right-mode)), but SCEP
enrolment of the resulting request fails with `%PKI-2-CERT_ENROLL_FAIL`, and the local
`crypto pki server` has no key-type option so it can only ever be RSA-keyed. Every trustpoint
in this exercise is enrolled over SCEP against that CA. So even with the `smd` stall fixed
you'd still be importing certificates minted somewhere else, which is a strong hint that
EAP-TLS wasn't in scope for ML-DSA on this release.

The threat model argument is the same as everywhere else in this repo: ML-KEM protects the
key derivation from harvest-now-decrypt-later today, while forging an RSA signature
requires a quantum computer *during the live session* with no retroactive damage. Lower
urgency, not zero.

The [container lab](../../learn/macsec/README.md#exercise-3-make-authentication-post-quantum-ml-dsa)
demonstrates the full ML-DSA path with wpa_supplicant/hostapd, so you can see what a
working answer looks like before you go hunting for one on the router.

## The automated version

All three exercises above exist as playbooks in [`automation/`](automation/README.md), split
the same way: PSK, then EAP-TLS, then the two-leaf PQ overlay on top. Reach for it after
you've built this by hand, because the ordering traps are the whole reason the automation is
shaped the way it is. The role enrols and verifies certificates *before* it touches the
interface, and it refuses outright to apply `access-session closed` to a router with no
certificate, which is exactly the deadlock warned about in
[Step 3](#step-3-the-interface).

---

**Cleanup:** this is the doc that leaves the most behind, because 802.1X touches the
interface, AAA, the EAP profile, a control policy and a local CA. If you raised the `smd`
trace levels to read the ML-KEM evidence, put them back to `notice`.
[Putting the routers back](README.md#the-surgical-way) removes it all in the right order,
which matters here: strip the interface before the objects it references, and do it over the
management interface rather than the link you're unsecuring. Next: [TLS](tls.md).