# MACsec on C9300 Smart Switches

> **Pre-req:** the container [MACsec lab](../../learn/macsec/README.md) covers the
> concepts: 802.1X, EAP-TLS, how ML-KEM fits inside a TLS 1.3 handshake, and why
> MACsec key derivation becomes quantum-safe when you use it. Do that first if you
> haven't. The [C8000 MACsec doc](../c8000/macsec.md) covers the same EAP-TLS mechanics
> on router-to-router links. This doc is self-contained for the three C9300 cases:
> host-to-switch, switch-to-switch, and switch-to-router. For a compact summary of
> CLI differences between the switch and router sides, see the
> [switch-to-router interop doc](../switch-router-interop.md#macsec).

Target platform: **C9300** switches on **IOS XE 26.2** or later.

## Three deployment scenarios

MACsec on a C9300 shows up in three places, and they don't all use the same 802.1X
shape:

```
Scenario A: host-to-switch (access mode)
  Endpoint    ◄──────►     C9300
  supplicant    LAN    authenticator

Scenario B: switch-to-router (network-link)
  C9300        ◄──────►     C8000
  both roles    uplink    both roles

Scenario C: switch-to-switch (network-link)
  C9300        ◄──────►     C9300
  both roles    fabric    both roles
```

**Scenario A: host-to-switch (h2s).** The switch authenticates an endpoint on a downlink port.
The C9300 is the authenticator, the endpoint/host is the supplicant. You'd see this on every
campus access port connecting a laptop, phone, or AP. Interface mode is `macsec` (access),
not `macsec network-link`. In *this* lab the endpoint is another C9300
standing in for that host. That's close enough to wire it, and it is not a NIC.

**Scenario B: switch-to-router (s2r).** The C9300 connects to a WAN router on an uplink. Both
sides run authenticator *and* supplicant. This is a common setup for
Quantum-safe encryption before traffic enters an IPsec tunnel.

**Scenario C: switch-to-switch (s2s).** Two C9300s encrypt the hop between them. Same
network-link config as Scenario B. This is
the campus/DC fabric case: leaf-to-leaf, closet-to-closet, or two switches acting as
the building interconnect.

Note: B and C are the same CLI. The PQC knob (`access-session pqc-type pqc`), the EAP-TLS 1.3
handshake, and ML-KEM are identical. What changes is which ports you put it on. 

MACsec is Layer 2 and IPsec is Layer 3, so they stack on the same wire. Underlay pings
between the link addresses get MACsec only. Tunnel pings get ESP first, then MACsec
on the hop. Don't apply `must-secure` until both sides have MACsec ready, or you'll
drop IKE while the session is still coming up. 

Every exercise here has a matching
[Ansible playbook](automation/README.md#macsec) that pushes the same config over
NETCONF. Do the CLI first, reach for the playbooks after.

## What you'll build

Four exercises that take you from a static key to quantum-safe dynamic keying, then
extend MACsec to an access port:

| Exercise | What it does | Quantum-safe? |
|----------|-------------|---------------|
| 1 | PSK MACsec on a network-link (no key exchange) | Yes |
| 2 | EAP-TLS MACsec with classical key exchange | No |
| 3 | EAP-TLS MACsec with ML-KEM key exchange | Yes |
| 4 | Host-to-switch MACsec with ML-KEM | Yes |

Exercise 1 proves the physical link and MACsec data plane work before you add PKI
(Public Key Infrastructure: certificates, trustpoints, the whole identity layer).
Exercise 2 adds certificate-based authentication with dynamic key derivation (still
classical ECDH). Exercise 3 flips one global command (`access-session pqc-type pqc`)
and the TLS 1.3 handshake switches to ML-KEM. Exercise 4 changes the topology: instead
of two equal peers on a fabric link, one side is the authenticator and the other is
the supplicant, which is how a campus access port works.

## Recommended path

| Exercise | Scenario | Key source | PQC |
|----------|----------|-----------|-----|
| 1 | B or C (network-link) | PSK | Manual |
| 2 | B or C (network-link) | EAP-TLS 1.3 | Classical |
| 3 | B or C (network-link) | EAP-TLS 1.3 | ML-KEM |
| 4 | A (host-to-switch) | EAP-TLS 1.3 | ML-KEM |

Why this order? Exercise 1 proves MACsec on any direct link before you add PKI. Exercises 2
and 3 are the same network-link EAP-TLS whether the peer is a router or the other switch.
Exercise 4 is the access-port shape, which is actually different.

## Exercise 1: PSK MACsec baseline (network-link)

This exercise proves MACsec works on a direct link before you add PKI or ML-KEM.
The key is pre-shared, so there's no key exchange at all. Use it to confirm the
physical link is clean and MACsec counters behave.

This works on both Scenario B (switch-to-router) and Scenario C (switch-to-switch).
The config is identical except for one keyword on the interface:

| | C9300 switch | C8000 router |
|-|---|---|
| **Interface keyword** | `macsec network-link` | `macsec` |
| **Why** | Distinguishes from access-port mode | Routed port is always peer-to-peer; no ambiguity |

Everything else (MKA policy, key chain, verification) is the same on both platforms.

### Configuration

**On both devices:**

```
mka policy PSK-POLICY
 macsec-cipher-suite gcm-aes-128

key chain MACSEC-PSK macsec
 key 01
  cryptographic-algorithm aes-128-cmac
  key-string <32 hex chars, same on both>
  lifetime local 00:00:00 Jan 1 2020 infinite
```

You can generate a valid key on your computer with `openssl rand -hex 16`.

**On both devices:**

```
interface <link-interface>
 macsec network-link              ! on a C8000 router: just "macsec"
 mka policy PSK-POLICY
 mka pre-shared-key key-chain MACSEC-PSK
```

### Verification

```
show mka sessions interface <link-interface>
```

You're looking for `Status` `Secured` and a CKN of `01`, which means PSK.

The show commands differ between platforms:

| What you want | C9300 switch | C8000 router |
|-|---|---|
| MACsec enabled + cipher | `show macsec interface <if>` | `show macsec status interface <if>` |
| Encrypted/decrypted counters | `show macsec interface <if>` (under SA Statistics) | `show macsec statistics interface <if>` |

Look for `Cipher` `GCM-AES-128` and `Encrypted Pkts` incrementing under the transmit SA.

Send pings across the link and read the counters before and after. The delta
should match the number of packets you sent.

### What this gives you

Quantum-safe (no key exchange to break), but no forward secrecy. The same CAK
is used for every session. If it leaks, all traffic is exposed. Exercise 2 fixes
that with EAP-TLS.

### Cleanup

On both devices:

```
interface <link-interface>
 no macsec network-link           
 no mka policy PSK-POLICY
 no mka pre-shared-key key-chain MACSEC-PSK
```

Verify `show mka sessions` displays 0.

---

## Exercise 2: Network-link EAP-TLS MACsec (classical)

This exercise sets up certificate-based MACsec on a network-link. The peer can be a
C8000 (Scenario B) or another C9300 (Scenario C). Both sides run `dot1x pae both`
so each acts as both authenticator and supplicant. The TLS handshake uses classical
key exchange (no ML-KEM yet; that's Exercise 3).

The CLI below is the same whether the peer is another C9300 switch or a C8000 router.
The only difference is the mode keyword on the router side: `macsec` instead of
`macsec network-link`.

### Why network-link?

The `macsec network-link` command tells MKA this is a peer-to-peer link between two
equal devices, not an access port authenticating a host. In network-link mode:

- Both ends need identity certificates that the peer trusts
- Both ends run `dot1x pae both`
- The config is the same on both sides

That's why switch-to-switch and switch-to-router share this exercise. Host-to-switch
does not; that's Exercise 4.

One spelling caveat for the router end. Only switches take the `network-link` keyword,
because only a switch port might have been an access port instead. On a C8000 the routed
port is peer-to-peer by definition, so the mode is plain `macsec` and `macsec
network-link` is rejected outright. Same mode, same behaviour, shorter command.

### Lab wiring

Pick one point-to-point link. IOS XE does **local** EAP-TLS on both boxes: each
device's session manager (smd) acts as the EAP server for the peer's supplicant
role. No external RADIUS.

### Step 1: certificates

This lab uses the simplest PKI model: each device generates a self-signed certificate,
and you manually import the peer's certificate so both sides trust each other. No CA
server and no SCEP required.

> **Production alternative:** for CA-signed certs with proper **EKUs** (Extended Key
> Usage for server and client), revocation checking, and automated enrollment,
> see the [appendix](#appendix-production-pki-with-ios-ca) or the
> [C8000 MACsec doc](../c8000/macsec.md).

**Find the self-signed certificate you already have.** Every IOS XE device generates a
persistent self-signed certificate on first boot. It lives inside a *trustpoint*, which
is IOS XE's container for a certificate, its private key, and the enrollment method
that produced it. The default one is called `TP-self-signed-<serial>`, and it's the
same trustpoint `ip http secure-trustpoint` uses for HTTPS. Find yours:

```
show running-config | include ^crypto pki trustpoint TP-self-signed-
show crypto pki certificates TP-self-signed-<serial>
```

You should see `Router Self-Signed Certificate` with `Status: Available`. It's RSA-2048,
SHA-512 signed, `CA:TRUE`, and good for ten years. Use it. Don't make another one.

> **Do not create a second `enrollment selfsigned` trustpoint.** IOS XE allows exactly
> one per device, and the slot is already taken. `crypto pki enroll` on a second one
> doesn't fail, it offers to delete the first:
>
> ```
> The router has already generated a Self Signed Certificate for
> trustpoint TP-self-signed-4130127353.
> If you continue the existing trustpoint and Self Signed Certificate
> will be deleted.
> ```
>
> Be careful! If you answer "yes" you take out the HTTPS trustpoint with it. Better to reuse what's there.

**Export your certificate.** `crypto pki export <tp> pem terminal` only exists in config mode, and even
there the shipped keypair is non-exportable. The certificate is in the running config
as a DER hex dump though, which is just as good:

```
show running-config | section crypto pki certificate chain TP-self-signed-<serial>
```

Copy the hex under `certificate self-signed <serial>`, up to but not including `quit`,
and turn it into PEM on your workstation:

```
printf %s <paste-the-hex-with-no-spaces> | xxd -r -p | openssl x509 -inform DER -outform PEM
```

That gives you the `-----BEGIN CERTIFICATE-----` block the peer needs.

**Import the peer's certificate.** First, grab the SHA-1 fingerprint of the cert you're
about to import (run this on your workstation or any box with OpenSSL):

```
openssl x509 -in peer-cert.pem -fingerprint -sha1 -noout
```

IOS only compares against MD5 or SHA-1 here, so SHA-1 is the better of the two on
offer. It's pinning a certificate you already hold, not verifying a signature.

On each device, create a trustpoint to hold the peer's cert. Pin the fingerprint so
IOS XE can validate the cert automatically:

```
crypto pki trustpoint Peer
 enrollment terminal
 fingerprint <sha1-hex-without-colons>
 revocation-check none
```

Then authenticate it:

```
crypto pki authenticate Peer
```

IOS XE prompts `Enter the base 64 encoded CA certificate`. Paste the **other** device's
PEM block, then type `quit` on a blank line. IOS XE validates the cert against your
pinned fingerprint and accepts it, with no "do you accept this certificate?" question.

> **Pinning is not optional on 26.x.** If you skip the `fingerprint` line, the import is
> rejected outright, with no chance to eyeball it and say yes:
>
> ```
> Trustpoint fingerprint must be supplied.
> Trustpoint CA certificate is rejected. Abort.
> % Error in saving certificate: status = FAIL
> ```

Repeat in the opposite direction: export from device B, import on device A.

After both imports, verify on each device:

```
show crypto pki trustpoints status
```

You should see `TP-self-signed-<serial>` and `Peer` both with certificates.

> **Why `enrollment terminal` for the peer?** Because the peer's certificate is one you
> didn't generate locally, and because `enrollment selfsigned` is not available to you a
> second time (see the warning at the top of this step).

### Step 2: AAA, EAP, and global MACsec settings

Same on both devices apart from the `username` in `dot1x credentials`:

```
aaa new-model
aaa authentication dot1x default local

dot1x system-auth-control

eap profile EAP-PROFILE
 method tls
 pki-trustpoint TP-self-signed-<serial>

dot1x credentials DOT1X-CREDS
 username <hostname>
 pki-trustpoint TP-self-signed-<serial>

mka policy mka_128
 macsec-cipher-suite gcm-aes-128
 key-server priority 5
 sak-rekey interval 200

access-session tls-version 1.3
```

**The subscriber control policy.** MACsec EAP-TLS requires an IBNS 2.0 (Identity-Based
Networking Services) control policy
that drives the authentication flow and enforces the linksec (MACsec) policy after
a successful handshake. Define this once per device:

```
policy-map type control subscriber DOT1X_POLICY_RADIUS
 event session-started match-all
  1 class always do-until-failure
   10 authenticate using dot1x both
 event authentication-failure match-all
  1 class always do-until-failure
   10 authentication-restart 7
 event authentication-success match-all
  1 class always do-until-failure
   10 activate service-template DEFAULT_LINKSEC_POLICY_MUST_SECURE
```

> **The first one of these converts the box, and asks first.** IOS XE holds either the
> old flat `authentication ...` interface commands or these CPL control policies, never
> both, and your first `policy-map type control subscriber` is what flips it:
>
> ```
> This operation will permanently convert all relevant authentication commands to
> their CPL control-policy equivalents. As this conversion is irreversible [...]
> Do you wish to continue? [yes]:
> ```
>
> Read the box for existing `authentication ...` interface commands before you answer,
> because it rewrites those too. On 26.x there's no `authentication convert-to
> new-style` escape hatch any more, only `authentication display`.

Three events, three actions:

1. **session-started**: kicks off mutual EAP-TLS.
2. **authentication-failure**: retries after 7 seconds.
3. **authentication-success**: activates `DEFAULT_LINKSEC_POLICY_MUST_SECURE`.

`DEFAULT_LINKSEC_POLICY_MUST_SECURE` is a **built-in** service template (you don't
define it; it's already on every IOS XE device). Its definition is one line:
`linksec policy must-secure`. That tells the switch to drop all non-EAPoL traffic until
MACsec is secured. Without this policy-map, dot1x authentication succeeds but MACsec
enforcement never kicks in.

The `pki-trustpoint` in the EAP profile tells EAP-TLS which **local identity cert**
to present during the handshake. The peer's certificate is trusted because you imported
it into the `Peer` trustpoint in Step 1: IOS XE's EAP stack validates the peer's
certificate against **all** authenticated trustpoints, not just the one named in the
profile.

The `username` in `dot1x credentials` is the EAP identity string sent during
authentication. It doesn't have to match the certificate's CN (the certificate itself
proves identity), but using the device hostname keeps things readable.

### Step 3: the interface

**Do not apply this until both devices have imported each other's certificates.** If
EAP-TLS cannot complete, the port stays unauthorized and you'll be debugging MACsec while the
real problem is missing trust.

The interface block is the same on both ends. Only the port name changes.


**On both devices:**

```
interface <link-interface>
 macsec network-link              
 access-session host-mode multi-host
 access-session port-control auto
 dot1x pae both
 dot1x authenticator eap profile EAP-PROFILE
 dot1x credentials DOT1X-CREDS
 dot1x supplicant eap profile EAP-PROFILE
 mka policy mka_128
 service-policy type control subscriber DOT1X_POLICY_RADIUS
```

The first time it has to come up takes a couple of minutes. Both ends authenticate each other at once, and
the cold start can churn before MKA settles. Later re-auths are much faster.

### Verification

```
show mka sessions
```

With EAP-TLS, the CKN is dynamically derived (a long hex string, not `01` like with
PSK). That's how you tell them apart at a glance.

```
show dot1x interface <interface> detail
```

Look for:
- `EAP Method = TLS`
- `Auth SM State = AUTHENTICATED`
- Both authenticator and supplicant sections present (because `pae both`)

```
show access-session
```

Should show an authorized session on the MACsec interface.

```
show macsec interface <interface>
```

Confirms MACsec is enabled and the cipher is what you configured.

---

## Exercise 3: Network-link MACsec with ML-KEM PQC

This is the exercise that matters. You take the working EAP-TLS MACsec from Exercise 2
and add ML-KEM key exchange. Same command on switch-to-router and switch-to-switch.
One global knob changes what the TLS 1.3 handshake negotiates.

### The PQC knob

One global command controls which TLS 1.3 key-exchange groups the device offers and
accepts during EAP-TLS authentication:

```
access-session pqc-type ?
  all      All pqc, non-pqc and hybrid algorithms will be supported
  hybrid   Combined PQC and NON-PQC algorithms
  non-pqc  Classic cryptographic algorithms
  pqc      Post-Quantum Cryptographic algorithms
```

Each mode maps to a set of groups:

| Mode | Key-exchange groups |
|------|-------------------|
| `pqc` | `mlkem512`, `mlkem768`, `mlkem1024` |
| `non-pqc` | `secp256r1`, `secp384r1`, `secp521r1` |
| `hybrid` | `p256_mlkem512`, `p384_mlkem768`, `p521_mlkem1024`, `x25519_mlkem512`, `x448_mlkem768`, `X25519MLKEM768`, `SecP256r1MLKEM768`, `SecP384r1MLKEM1024` |
| `all` | Everything above |

**TLS 1.3 is mandatory for PQC.** The `pqc` and `hybrid` modes require TLS 1.3
(`access-session tls-version 1.3` or `all`). If one side is locked to TLS 1.2, PQC
won't negotiate and authentication fails.

### Enable PQC

**On both devices:**

```
access-session pqc-type pqc
```

That's it. The EAP-TLS handshake now uses ML-KEM key exchange groups instead of
classical ECDH. The MACsec session should re-establish automatically on the next
reauthentication cycle, or you can force it:

```
dot1x re-authenticate interface <interface>
```

> **Force it from one end only.** On a switch-to-switch link both boxes run
> `dot1x pae both`, so each one's supplicant authenticates against the other one's
> authenticator. Clear or re-authenticate both at the same moment and they restart on
> the same `authentication-restart 7` tick, so every retry lands on a peer that is
> itself mid-restart. The link never comes back. You'll see one side loop this every
> seven seconds:
>
> ```
> %DOT1X-5-RESULT_OVERRIDE: Authentication result overridden for client
>   (xxxx.xxxx.xxxx) on Interface <link-interface>
> ```
>
> while the other sits `Unauthorized` with `dot1xSup: Authc Failed` in
> `show access-session interface <link-interface> details`. Clearing a single end is enough
> anyway: the whole pair renegotiates and both sides land on a fresh CKN.

Optionally, pin the MKA policy to ML-KEM-768 specifically:

```
mka policy mka_128
 pqc mlkem768
```

### Verification

```
show mka sessions
```

The session should be `Secured` again. The CKN will be different from the classical
session (new handshake, new derived key).

**Config verification.** Confirm both devices have PQC enabled:

```
show running-config all | include pqc-type
show running-config all | include tls-version
```

You should see `access-session pqc-type pqc` and `access-session tls-version 1.3`
(or `all`). If the knob is set and both ends authenticated, PQC was negotiated.

```
show dot1x interface <interface> detail
```

Look for `EAP Method = TLS` and `Auth SM State = AUTHENTICATED` on both the
authenticator and supplicant sections. This confirms the TLS handshake completed.

```
show access-session interface <interface> details
```

Should show `Security Policy: Must Secure` and `Security Status: Link Secured`, with
both `dot1x Authc Success` and `dot1xSup Authc Success`.

**Where's the ML-KEM proof?** Here's the gap. Unlike IKEv2 (where `show crypto ikev2
sa detailed` prints `AKE group: AKE1: MLKEM1024` right in the output), **no standard
show command on either platform reveals which key-exchange group MACsec's EAP-TLS
handshake actually negotiated.** The `show dot1x`, `show mka`, `show macsec`, and
`show access-session` outputs are identical whether the handshake used `mlkem512` or
`secp256r1`. 

**Definitive proof: the `smd` platform trace.** The session manager daemon logs the
negotiated TLS 1.3 group during the EAP-TLS handshake. To see it in your C9300 switch:

```
set platform software trace smd switch active R0 dot1x all debug
```

Force a re-authentication from one end (shut/no shut the interface, or
`dot1x re-authenticate interface <interface>` on one device only), then read the trace:

```
show logging process smd internal | include Negotiated Group
```

You should see:

```
Negotiated Group (key exchange algorithm):mlkem512
```

(or `mlkem768` / `mlkem1024` depending on what both sides support). If you see
`secp256r1` or another classical group, PQC did not negotiate. Check that
`access-session pqc-type pqc` is present and `access-session tls-version` includes
1.3 on both ends.

**Restore the trace level when you're done:**

```
set platform software trace smd switch active R0 dot1x all notice
```

### Comparing the modes

You can switch between modes and watch what changes:

| `access-session pqc-type` | What negotiates | TLS 1.3 required? |
|--------------------------|----------------|-------------------|
| `non-pqc` | `secp256r1`, `secp384r1`, etc. | No |
| `pqc` | `mlkem512`, `mlkem768`, `mlkem1024` | Yes |
| `hybrid` | `p256_mlkem512`, `X25519MLKEM768`, etc. | Yes |
| `all` | All of the above; peers pick the best match | Yes (for PQC/hybrid) |

In production, `hybrid` is the safest bet: you get ML-KEM protection against quantum
attacks, plus classical ECDH as a fallback layer. If the ML-KEM implementation had a
flaw, the classical component still protects you.

`pqc` (pure ML-KEM) is the strongest quantum posture but gives up the classical safety
net.

`all` is the most permissive: it lets the TLS negotiation pick the best common group.
Good for mixed environments during migration.

### What this gives you

The hop is now quantum-safe. An attacker capturing frames gets encrypted MACsec
whose session keys were derived through ML-KEM. Even with a future quantum computer,
that key exchange can't be broken.

On a switch-to-router link, this is the first hop before traffic enters a WAN IPsec
tunnel. On a switch-to-switch link, it
is the fabric hop. Different keys,
different protocols, both resistant.

**What about authentication (ML-DSA)?** The PQC knob covers *key exchange* only. The
EAP-TLS certificates that prove identity during the handshake are still classical
(RSA or ECDSA). The C8000 MACsec doc
[tested ML-DSA certificates for EAP-TLS](../c8000/macsec.md#what-about-authentication-ml-dsa)
and found it doesn't work on 26.2: `smd` accepts the ML-DSA trustpoint but stalls
before building a certificate chain. The same `smd` process runs on the C9300, so the
same limitation applies. ML-KEM protects the key derivation from
harvest-now-decrypt-later today; forging an RSA signature requires a quantum computer
*during the live session*, which is lower urgency but not zero.

### Cleanup

```
no access-session pqc-type

mka policy mka_128
 no pqc mlkem768
```

If you want to fully tear down Exercise 2 as well:

```
interface <interface>
 no macsec network-link          ! on the C8000 router: no macsec
 no access-session host-mode multi-host
 no access-session port-control auto
 no dot1x pae both
 no dot1x authenticator eap profile EAP-PROFILE
 no dot1x credentials DOT1X-CREDS
 no dot1x supplicant eap profile EAP-PROFILE
 no mka policy mka_128
```

---

## Exercise 4: Host-to-switch MACsec with ML-KEM PQC

Exercises 1-3 were network-link: two equal devices, identical config. A campus access
port is not that. The switch is the authenticator; the endpoint is a supplicant. The
interface keyword is `macsec`, not `macsec network-link`, and only one side runs
`dot1x pae authenticator`.

Pick a dedicated downlink port. Don't reuse a network-link port from Exercises 2-3;
that's Scenario C, not this one.

The host is still a C9300. That's the important catch, and it changes what actually
comes up. A laptop with wpa_supplicant is the [container lab](../../learn/macsec/README.md).
Here the "endpoint" speaks IOS.

### Isolate the link

If the downlink port shares a VLAN with other active ports, 802.1X can authenticate
the wrong MAC. Put the host-to-switch link in its own VLAN:

```
vlan 10
 name H2S-LAB

interface <downlink-port>               ! on the switch
 switchport mode access
 switchport access vlan 10

interface <host-port>                   ! on the host/endpoint
 switchport mode access
 switchport access vlan 10
```

Optional, so you can ping through MACsec later:

```
interface Vlan10
 ip address 10.10.10.1 255.255.255.0    ! .2 on the other end
 no shutdown
```

### Certificates

Same self-signed + peer import as Exercise 2. If the endpoint's `Self` trustpoint
has no certificate yet, enrol it before you import anything:

```
configure terminal
 crypto pki enroll Self
```

Answer `no` to serial number and IP in the subject, `yes` to generate the self-signed
cert. Then export each `Self` chain, fingerprint with SHA-1, and cross-import as a
`Peer` trustpoint on each side. Pinning is mandatory. See Step 1.

Both sides need `access-session tls-version 1.3` and `access-session pqc-type pqc`.

The authenticator looks the EAP identity up locally. Give the endpoint credentials a
name the switch knows, and bind it to the must-secure attribute list:

```
dot1x credentials EAPTLSCRED-IOSCA
 username HOST
 pki-trustpoint Self

username HOST aaa attribute list EAPTLSCRED-IOSCA
```

If the switch already has credentials for a network-link port (Exercises 2-3), don't
change those. Each interface references its own `dot1x credentials` profile.

### What access-mode actually did

We applied the textbook downlink CLI: `macsec` (not `network-link`), `dot1x pae
authenticator` on the switch, `dot1x pae supplicant` on the host.

EAP-TLS itself worked. The switch showed `User-Name: HOST` and `dot1x Authc Success`.
MKA never got a peer. `show macsec interface` stayed `Cipher : Invalid` with no secure
channels.

Access-mode `macsec` between two IOS switches is a dead end. The
PAE split is right for a laptop. It is not enough for MKA when the "host" is another
Catalyst.

### What works on this pair

Treat the link like Exercise 2: `macsec network-link` and `dot1x pae both` on
both sides, same EAP profile / MKA policy / `DOT1X_POLICY_RADIUS`. Keep VLAN 10.

**On both devices:**

```
interface <port>
 switchport access vlan 10
 switchport mode access
 macsec network-link
 access-session host-mode multi-host
 access-session port-control auto
 dot1x pae both
 dot1x authenticator eap profile Self
 dot1x credentials EAPTLSCRED-IOSCA
 dot1x supplicant eap profile Self
 mka policy mka_128
 service-policy type control subscriber DOT1X_POLICY_RADIUS
```

AAA, `eap profile Self`, `mka policy mka_128`, `access-session pqc-type pqc`, and
`DOT1X_POLICY_RADIUS` are the Exercise 2 objects. Don't rebuild them if they're
already there.

### Verification

From the switch side, after a couple of minutes:

```
show mka sessions
```

```
Total MKA Sessions....... 2
      Secured Sessions... 2
<downlink>  ...  Secured   CKN 4E7611E34B847326331E46601B2BA722
<uplink>    ...  Secured   CKN A970B958E2E455D8F3225196B30F3206
```

Two sessions is the point: the new downlink did **not** knock over any existing
network-link session.

```
show access-session interface <downlink-port> details
```

```
User-Name:  HOST
Status:  Authorized
Current Policy:  DOT1X_POLICY_RADIUS
Security Policy:  Must Secure
Security Status:  Link Secured
Method status list:
        dot1x           Authc Success
     dot1xSup           Authc Success
```

```
show macsec interface <downlink-port>
```

`Cipher : GCM-AES-128`, Transmit/Receive SCs `inUse`. Then ping through VLAN 10:

```
ping 10.10.10.2 repeat 20
```

Live result: `Success rate is 95 percent (19/20)`. First packet can drop while the SA
settles. Encrypted counters live under `SA Statistics` / `Encrypted Pkts` on this
platform (the SC aggregate can sit still). See the switch-pair verify notes if a delta reads
zero on a link that is clearly passing ping.

The host side should show the same CKN and `Secured`. The switch is the key
server (`Key-Server YES`).

### The real endpoint CLI

If the far end is a laptop, phone, or AP, *this* is what you put on the switch. No
credentials and no supplicant profile on the downlink. The endpoint brings
wpa_supplicant.

```
access-session pqc-type pqc
access-session tls-version 1.3

interface <downlink-port>
 switchport mode access
 macsec
 access-session host-mode multi-host
 access-session port-control auto
 dot1x pae authenticator
 dot1x authenticator eap profile EAP-PROFILE
 mka policy mka_128
 service-policy type control subscriber DOT1X_POLICY_HOST
```

Use a policy that authenticates **one** role, or success never attaches must-secure:

```
policy-map type control subscriber DOT1X_POLICY_HOST
 event session-started match-all
  1 class always do-until-failure
   10 authenticate using dot1x
 event authentication-failure match-all
  1 class always do-until-failure
   10 authentication-restart 7
 event authentication-success match-first
  1 class always do-until-failure
   10 activate service-template DEFAULT_LINKSEC_POLICY_MUST_SECURE
   20 authorize
```

`show dot1x interface <int> detail` should show `PAE = AUTHENTICATOR` only.

### Cleanup

Only the host-to-switch link. Do **not** `no access-session pqc-type` if other
interfaces still use it.

**On both devices:**

```
interface <port>
 no macsec network-link
 no access-session host-mode multi-host
 no access-session port-control auto
 no dot1x pae both
 no dot1x authenticator eap profile Self
 no dot1x credentials EAPTLSCRED-IOSCA
 no dot1x supplicant eap profile Self
 no mka policy mka_128
 no service-policy type control subscriber DOT1X_POLICY_RADIUS
 switchport access vlan 1

interface Vlan10
 shutdown
 no ip address

no vlan 10
no crypto pki trustpoint <peer-trustpoint>
no username HOST
no policy-map type control subscriber DOT1X_POLICY_HOST
```

Leave `Self`, `eap profile Self`, and `mka policy mka_128` if other interfaces still
use them.

---

## Reference: network-link PQC MACsec example

After Exercise 3 on the switch-to-router pair, the global and interface blocks are in
these files (peer cert import from Step 1 is omitted; see
[`device-configs/`](device-configs/) for full snapshots). The switch-to-switch
case uses the same objects on whichever port connects the two switches.

| File | Side |
|------|------|
| [`c9300-macsec-pqc-reference.txt`](device-configs/c9300-macsec-pqc-reference.txt) | C9300 switch |
| [`c8000-macsec-pqc-reference.txt`](device-configs/c8000-macsec-pqc-reference.txt) | C8000 router |

---

## Appendix: production PKI with IOS CA

The self-signed approach in Exercise 2 is quick, but in production you want CA-issued
certificates with proper EKUs, revocation checking, and automated enrollment. The
[C8000 MACsec doc](../c8000/macsec.md) walks through the full IOS CA + SCEP flow for
router-to-router links. The same model works for the switch-to-router case.

Here's the shape. One C8000 router runs the IOS CA; both the switch and the router
enroll over SCEP.

**On the C8000 router (CA host):**

```
ip http server
!
crypto pki server CA_Server
 no database archive
 grant auto
 eku server-auth client-auth
 no shutdown
```

`no shutdown` prompts for a passphrase. Enter one and confirm (pressing Return at the
prompt aborts: `% Aborted.`). Wait for `% Certificate Server enabled.`.

**Clock matters.** If `show clock` shows a leading `*`, the time was never set
authoritatively. The CA refuses to start (`% Time has not been set`). Fix it on both
devices with `clock set …` or NTP before proceeding.

**On both devices** (change `subject-name` and enrollment URL per device):

```
crypto pki trustpoint CA_TP
 enrollment url http://14.14.14.2:80
 subject-name CN=<hostname>
 revocation-check none
 rsakeypair CA_TP 2048
 hash sha512
```

The enrollment URL is the CA host's IP on whatever path the switch can reach. In a
point-to-point lab, that's the uplink IP (`14.14.14.2`). Enroll **before** you apply
MACsec on that port.

Pin the CA fingerprint (get it from `show crypto pki server | include fingerprint` on
the CA host), then enroll:

```
crypto pki trustpoint CA_TP
 fingerprint <hex-from-show-command>

crypto pki authenticate CA_TP
crypto pki enroll CA_TP
```

Confirm the identity cert landed with the right EKUs:

```
show crypto pki certificates verbose CA_TP
```

Look for `Status: Available` on the identity block, plus `Extended Key Usage: Client
Auth` and `Server Auth`.

Then use `CA_TP` instead of `Self` in the EAP profile and dot1x credentials:

```
eap profile EAP-PROFILE
 method tls
 pki-trustpoint CA_TP

dot1x credentials DOT1X-CREDS
 username SW1
 pki-trustpoint CA_TP
```

The interface config from Exercise 2 Step 3 stays exactly the same. Everything else
(MKA policy, PQC knob, `access-session tls-version 1.3`) is identical.

The [C8000 MACsec doc](../c8000/macsec.md) has the full troubleshooting trail for this
flow: clock/NTP issues, fingerprint pinning, `enrollment selfsigned` traps, port-lock
timing, and failure experiments.

## The automated version

All three scenarios (host-to-switch, switch-to-switch, switch-to-router) exist as
Ansible playbooks in [`automation/`](automation/README.md#macsec). The playbooks are
split the same way as this doc: PSK baseline, then EAP-TLS, then the PQ overlay on
top. Run them after you've built this by hand, because the ordering traps (port lock,
credential timing, `must-secure` deadlocks) are the whole reason the automation is
shaped the way it is.

```bash
cd deploy/c9300/automation
ansible-playbook macsec-psk.yml       # Exercise 1
ansible-playbook macsec-eaptls.yml    # Exercise 2
ansible-playbook macsec-pq.yml        # Exercise 3
```

Each playbook takes `-e macsec_play_hosts=macsec_s2s`, `macsec_s2r`, or `macsec_h2s`
to pick the scenario. [DESIGN.md](automation/DESIGN.md) explains the role structure
and why NETCONF handles the config but cert enrollment still goes over CLI.
