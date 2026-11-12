# PQC Hardware Verification: Findings for BU Escalation

Tested September 20, 2026, against live lab hardware. Each finding is categorized as
**Verified** (reproduced on hardware today), **Confirmed by prior run** (reproduced
during original lab exercises, output checked in), or **Not testable** (requires
topology or config state we can't set up from this session without risk).

## Test environment

| Device | Hostname | Platform | IOS XE version | Mgmt IP |
|--------|----------|----------|-----------------|---------|
| C8000 router | C8235-G2 | C8235-G2 | **26.02.1eftr2** | 172.26.194.67 |
| C9300 switch #1 | C9350_PQC1_IPsec | C9350-48HX | **26.02 (BLD_V262)** | 172.26.194.61 |
| C9300 switch #2 | C9350_PQC2_IPsec | C9350-48HX | **26.02 (BLD_V262)** | 172.26.194.62 |

All three devices were accessed over SSH with `ansible` / password auth. The two C9350s
have a live IKEv2 tunnel between them with ML-KEM-768 active (`PQC Key Exchange:
ML-KEM-768`). C9350 #2 has `service internal` enabled and ML-DSA trustpoints imported
(`TP-MLDSA65`, `MLDSA-S4`).

Three additional devices on 26.1 are reachable (C9350-24U ×2, C8355-G2, C8455-G2) but
26.1 lacks many PQC features, so they serve only as version-comparison controls.

---

## Cisco 8000 Series Secure Routers (C8235-G2, IOS XE 26.2)

### BUGS

#### C8K-B01: ECDSA-sig never completes IKE_AUTH

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P1 |
| **Protocol** | IPsec |
| **Finding** | A P-256 certificate chain that OpenSSL validates (correct SAN, both EKUs) fails `IKEv2-ERROR: Auth exchange failed` every time. SHA-256 and SHA-512 variants tried; `hash sha256` on the trustpoint changed nothing. RSA-2048 from the identical build process worked first time. |
| **Why not re-tested today** | The C8235-G2 at 172.26.194.67 has no IPsec tunnel active and no ECDSA certificates enrolled. Building a full ECDSA PKI and tunnel pair from scratch risks leaving the box in a broken state for other testers. |
| **Evidence** | `deploy/c8000/ipsec.md` "Things that will bite you"; the IKE_AUTH size measurement table uses RSA-2048 as the classical baseline specifically because ECDSA wouldn't work. |

#### C8K-B02: EAP-TLS with ML-DSA certificates silently stalls in smd

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P1 |
| **Protocol** | MACsec |
| **Finding** | `smd` loads the OQS provider, sets the ML-DSA trustpoint, then never receives the certificate chain from IOSd. Zero TLS bytes reach the wire. The `certchain response` marker appears for RSA trustpoints and is absent for ML-DSA. The handshake hangs rather than failing, with `dot1x Running` visible in `show access-session`. |
| **Why not re-tested today** | No MACsec sessions active on this router; would require setting up local CA, EAP-TLS, and dot1x from scratch. |
| **Evidence** | `deploy/c8000/macsec.md` "What about authentication (ML-DSA)": trace comparison table showing `certchain response: 2` for RSA, `0` for ML-DSA. |

#### C8K-B03: PKCS#12 import causes SSH lockout (ECDSA host key)

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P1 |
| **Protocol** | SSH |
| **Finding** | Importing an ECDSA P-256 PKCS#12 bundle makes the SSH server advertise `ecdsa-sha2-nistp256` as a host key algorithm, then fail every handshake that selects it. The client picks the algorithm the server claims to prefer, and the connection drops. Recovery requires console access or `ssh -o HostKeyAlgorithms=rsa-sha2-512`. |
| **Today's verification** | Confirmed the C8235-G2 currently has no ECDSA host key (`IOS Keys in SECSH format(ssh-ec, base64 encoded): NONE`) and no ECDSA-related trustpoints, so the lockout vector is dormant. The hostkey algorithm list shows only `ecdsa-sha2-nistp256, ..., rsa-sha2-512, rsa-sha2-256`, confirming the ECDSA algorithms are still in the default server offer. |
| **Evidence** | `deploy/c8000/ssh.md` "Watch out: importing an EC keypair can lock you out". |

#### C8K-B04: HTTPS server wedges after repointing `ip http secure-trustpoint`

| | |
|---|---|
| **Status** | **Verified on C9350 #2 today** (same IOS XE `smd` / HTTP server code) |
| **Severity** | P2 |
| **Protocol** | TLS |
| **Finding** | Repointing `ip http secure-trustpoint` at imported certificates (including ML-DSA) leaves the HTTPS server returning `tlsv1 alert internal error` even after switching back to a classical trustpoint. Not ML-DSA specific; imported RSA-2048 triggers it too. |
| **Today's verification** | C9350 #2 (`172.26.194.62`) currently has `HTTP secure server trustpoint: TP-MLDSA65` (an ML-DSA trustpoint). OpenSSL test: |
| | `openssl s_client -connect 172.26.194.62:443 -tls1_3` → **`tlsv1 alert internal error (SSL alert number 80)`** |
| | Meanwhile C9350 #1 (`172.26.194.61`) with the default self-signed trustpoint returns: |
| | `Negotiated TLS1.3 group: X25519MLKEM768` (working fine) |
| | And C8235-G2 (`172.26.194.67`) with its default self-signed trustpoint also returns: |
| | `Negotiated TLS1.3 group: X25519MLKEM768` (working fine) |

#### C8K-B05: `macsec` (no `network-link`) encrypts nothing while reporting Secured

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Protocol** | MACsec |
| **Finding** | On a router-to-router link, `macsec` (access mode) instead of `macsec network-link` lets MKA secure, SAK install, and `must-secure` pass, but every MACsec crypto counter stays at zero. 300 pings delivered unencrypted. |
| **Evidence** | `deploy/c8000/macsec.md` "Three ways to get this wrong (#3)": counter delta table showing 316 packets crossed with zero encrypted. |

#### C8K-B06: IKEv2 `Auth verify` on the initiator is untrustworthy

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Protocol** | IPsec |
| **Finding** | Mid-migration, a spoke reports `Auth sign: MLDSA, Auth verify: MLDSA` while the hub is demonstrably still signing PSK. Only the responder reports both directions correctly. |
| **Evidence** | `deploy/c8000/automation/DESIGN.md` "The initiator's `Auth verify` field is not trustworthy". |

#### C8K-B07: `%PKI-3-KEY_CMP_MISMATCH` on every ML-DSA PKCS#12 import

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Protocol** | PKI |
| **Finding** | Every ML-DSA PKCS#12 import logs a severity-3 key mismatch error. Import succeeds and certificate works. Confuses operators and pollutes logs. |
| **Evidence** | `deploy/c8000/ipsec.md` "Import the bundle". |

#### C8K-B08: SCEP enrollment fails for ML-DSA CSRs

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Protocol** | PKI |
| **Finding** | `crypto pki enroll` over SCEP with an ML-DSA key fails with `%PKI-2-CERT_ENROLL_FAIL`. RSA SCEP to the same CA succeeds. The IOS CA signs the same ML-DSA request fine via `request pkcs10 terminal` (bypassing SCEP). Likely a missing ML-DSA CMS signer in the SCEP client. |
| **Evidence** | `deploy/c8000/automation/DESIGN.md` "SCEP is where it dies". |

#### C8K-B09: EAP-TLS derives 128-bit CAK despite GCM-AES-256 data cipher

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P3 |
| **Protocol** | MACsec |
| **Finding** | `smd` derives a 128-bit CAK from the EAP-TLS MSK. Logs `%MKA-4-MKA_MACSEC_CIPHER_MISMATCH` on every session. MKA Cipher Suite shows `AES-128-CMAC` while SAK Cipher Suite shows `GCM-AES-256`. 128-bit AES under Grover's = 64-bit quantum work factor. PSK path gets full `AES-256-CMAC`. |
| **Evidence** | `deploy/c8000/macsec.md` "One wrinkle: the key hierarchy is weaker than the data cipher". |

#### C8K-B10: `show mka sessions detail` lies about SAK rekey time

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P3 |
| **Protocol** | MACsec |
| **Finding** | Reports `SAK Rekey Time........... 0s (SAK Rekey interval not applicable)` while rotation is actively happening every 60 seconds. Read `Latest SAK AN` and `Latest SAK KI (KN)` instead. |
| **Evidence** | `deploy/c8000/macsec.md` "Rotating the SAK". |

#### C8K-B11: NETCONF `<ok/>` for CA server `no shutdown` but CA never starts

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P3 |
| **Protocol** | NETCONF |
| **Finding** | Setting `shutdown-config=false` over NETCONF returns `<ok/>`, CA stays `disabled` / `initial`. Only CLI `no shutdown` works (prompts for passphrase with no YANG leaf). |
| **Evidence** | `deploy/c8000/automation/DESIGN.md` "An `<ok/>` is not proof". |

#### C8K-B12: `crypto-auth-method` typedef has no ML-DSA value

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P3 |
| **Protocol** | NETCONF |
| **Finding** | `my-auth-method` / `peer-auth-method` in `Cisco-IOS-XE-crypto-oper` stop at `crypto-ikev2-auth-eap`. ML-DSA signatures can only be confirmed by screen-scraping `show crypto ikev2 sa detailed`. |
| **Evidence** | `deploy/c8000/automation/DESIGN.md`. |

#### C8K-B13: `show version | include Technology|Current` returns only headers

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P3 |
| **Protocol** | General |
| **Finding** | On C8235-G2 26.2, the filter matches the three header lines but not the data row (`Smart License  Perpetual    essentials         essentials`), because the data row contains neither "Technology" nor "Current". |
| **Today's output** | |

```
C8235-G2#show version | include Technology|Current
Technology Package License Information:
Technology     Type         Technology-package Technology-package
                            Current            Next Reboot
```

No data row. `show license summary` or `show version | begin Technology` work instead.

### GAPS (features not yet supported)

#### C8K-G01: No ML-DSA for SSH (host key or user key)

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P1 |
| **Protocol** | SSH |
| **Today's verification** | `ip ssh server algorithm hostkey ?` on C8235-G2 lists only: |

```
ecdsa-sha2-nistp256, ecdsa-sha2-nistp384, ecdsa-sha2-nistp521,
rsa-sha2-256, rsa-sha2-512, ssh-rsa, x509v3-ssh-rsa
```

No `mldsa` or composite option. Same for `ip ssh server algorithm publickey ?`: Ed25519, FIDO2, x509v3 variants, but no ML-DSA.

The SSH certificate profile `trustpoint sign` accepts any trustpoint name (including ML-DSA ones), but there is no ML-DSA host key algorithm to select it. Parser artifact, not a feature.

#### C8K-G02: No ML-DSA for TLS certificate authentication (mgmt HTTPS)

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P1 |
| **Protocol** | TLS |
| **Today's verification** | C8235-G2 HTTPS server: `Signature type: rsa_pss_rsae_sha256` on every successful handshake. C9350 #2 with `ip http secure-trustpoint TP-MLDSA65` returns `tlsv1 alert internal error` (bug C8K-B04). |

#### C8K-G03: No PQC knob for RADIUS, TACACS+, syslog, or gNMI

| | |
|---|---|
| **Status** | Confirmed by prior run (CLI walkthrough) |
| **Severity** | P2 |
| **Protocol** | TLS |
| **Finding** | `ip http secure-pqc-type` is scoped to HTTPS. `access-session pqc-type` is scoped to EAP-TLS. No other TLS consumer has a PQC knob. |

#### C8K-G04: No show command for EAP-TLS negotiated key exchange group

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Protocol** | MACsec |
| **Finding** | The only proof ML-KEM was used is raising `smd` platform trace to debug, forcing a re-auth, grepping the trace for `Negotiated Group`, then reverting the trace level. Not operational-grade. |

#### C8K-G05: No CLI to select ML-KEM parameter set for EAP-TLS directly

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P2 |
| **Protocol** | MACsec |
| **Today's verification** | `access-session pqc-type ?` on C8235-G2 shows four values: `all`, `hybrid`, `non-pqc`, `pqc`. No parameter-level knob. `pqc` lands on ML-KEM-512; `hybrid` gives ML-KEM-768 + X448. Operators can't pin ML-KEM-768 in pure PQC mode. |

#### C8K-G06: IOS XE image and package signing still RSA-2048

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P2 |
| **Protocol** | Secure Boot |
| **Today's output** | `show software authenticity running` on C8235-G2: |

```
SYSTEM IMAGE → Signature Algorithm: 2048-bit RSA, Verifier: ROMMON
ROMMON       → Signature Algorithm: LDWM, Verifier: Microloader      ← PQ ✓
Microloader  → Signature Algorithm: LDWM (m=20,w=4,k=4,h=10), Verifier: Hardware Anchor ← PQ ✓
```

ROMMON and Microloader are LDWM (quantum-resistant). System image and all packages are RSA-2048. The upper boot chain is quantum-vulnerable.

#### C8K-G07: SUDI certificates still classical RSA-2048

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Protocol** | Secure Boot |
| **Finding** | All three SUDI certificates (Root CA 2099 → High Assurance SUDI CA → Device) use RSA 2048 / SHA-256. ML-DSA-87 signed SUDI is roadmap. |

#### C8K-G08: IOS CA limited to RSA keys only

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P3 |
| **Protocol** | PKI |
| **Finding** | `crypto pki server` has no key-type option. Auto-generates RSA-2048. Can sign ML-DSA CSRs (the signing engine is fine), but the CA's own signature is always `sha512WithRSAEncryption`. Can't produce end-to-end PQ cert chains. |

#### C8K-G09: Zero `rpc` statements in `Cisco-IOS-XE-crypto.yang`

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P3 |
| **Protocol** | NETCONF |
| **Finding** | Key generation, enrollment, SA clears, `write memory` all require CLI escape hatches. |

### OPERATIONAL QUIRKS

#### C8K-Q01: `service internal` required for ML-DSA PKCS#12 import

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Today's check** | C8235-G2 does NOT have `service internal` enabled (`show run | include service internal` returned empty). This is the clean state. |

#### C8K-Q02: `crypto key generate mldsa` only available in exec mode

| | |
|---|---|
| **Status** | **Verified today** |
| **Today's output** | |

```
C8235-G2(config)#crypto key generate ?
  ec   Generate EC keys for ECDSA
  rsa  Generate RSA keys

C8235-G2#crypto key generate ?
  ec     Generate EC keys for ECDSA
  mldsa  Generate ML-DSA keys
  rsa    Generate RSA keys
```

Config mode only shows `ec` and `rsa`. Exec mode adds `mldsa`. Confusing, because `crypto key generate` is actually an exec-mode command and the config-mode parser is answering about something different.

#### C8K-Q03: PPK `key hex` rejects non-ASCII decoded bytes

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Finding** | Error says "All characters in hex string must be ASCII" but checks the *decoded* value (0x20-0x7E), not the hex digits. `aabbccdd` is rejected; `48656C6C6F` ("Hello") is accepted. |

#### C8K-Q04: `access-session pqc-type all` silently downgrades

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Finding** | With `all`, if the peer is `non-pqc`, the handshake silently drops to classical P-521. No syslog. Only discoverable via `smd` trace. `hybrid` correctly fails closed. |

#### C8K-Q05: TLS PQC works out of the box (no config needed)

| | |
|---|---|
| **Status** | **Verified today** |
| **Today's output** | |

```
$ openssl s_client -connect 172.26.194.67:443 -tls1_3 -brief </dev/null
Protocol version: TLSv1.3
Ciphersuite: TLS_AES_256_GCM_SHA384
Peer certificate: CN=IOS-Self-Signed-Certificate-2458466977
Signature type: rsa_pss_rsae_sha256
Negotiated TLS1.3 group: X25519MLKEM768
```

`HTTP secure server PQC type: all` is the default on 26.2. ML-KEM-768 hybrid key exchange with zero configuration.

#### C8K-Q06: SSH ML-KEM KEX works after explicit enable

| | |
|---|---|
| **Status** | **Verified today** |
| **Today's test** | Temporarily configured `ip ssh server algorithm kex mlkem768x25519-sha256 curve25519-sha256 ecdh-sha2-nistp256`, then: |

```
$ ssh -v -o KexAlgorithms=mlkem768x25519-sha256 ansible@172.26.194.67
debug1: kex: algorithm: mlkem768x25519-sha256
```

Reverted with `no ip ssh server algorithm kex`. KEX list returned to defaults. ML-KEM KEX confirmed working, but NOT in the default offer (requires explicit config).

---

## Cisco 9300 Series Smart Switches (C9350-48HX, IOS XE 26.2)

### BUGS

#### C9K-B01: Access-mode `macsec` between two IOS switches is a dead end

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P1 |
| **Protocol** | MACsec |
| **Finding** | EAP-TLS completes (`dot1x Authc Success`), but MKA never gets a peer. `show macsec interface` stays `Cipher : Invalid`. Only `macsec network-link` + `dot1x pae both` works between IOS devices. |
| **Evidence** | `deploy/c9300/macsec.md` Exercise 4 "What access-mode actually did". |

#### C9K-B02: `dot1x re-authenticate` on both ends simultaneously = permanent hang

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P1 |
| **Protocol** | MACsec |
| **Finding** | Both boxes restart on the same 7-second tick, each retry hits a peer mid-restart. One side loops `%DOT1X-5-RESULT_OVERRIDE` every 7 seconds, the other holds `Unauthorized`. Never self-recovers. |
| **Evidence** | `deploy/c9300/macsec.md` Exercise 3; `deploy/c9300/automation/DESIGN.md`. |

#### C9K-B03: HTTPS server returns internal error with ML-DSA trustpoint

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P2 |
| **Protocol** | TLS |
| **Today's verification** | C9350 #2 has `HTTP secure server trustpoint: TP-MLDSA65`. |

```
$ openssl s_client -connect 172.26.194.62:443 -tls1_3
error:0A000438:SSL routines:ssl3_read_bytes:tlsv1 alert internal error:SSL alert number 80
```

C9350 #1 with the default self-signed trustpoint works fine:

```
$ openssl s_client -connect 172.26.194.61:443 -tls1_3 -brief
Negotiated TLS1.3 group: X25519MLKEM768
```

Same bug as C8K-B04. The HTTPS server can't serve ML-DSA certificates.

#### C9K-B04: IPsec `#pkts encaps` counters frozen on C9350-48HX

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P2 |
| **Protocol** | IPsec |
| **Today's verification** | Live tunnel between C9350 #1 and #2 with `PQC Key Exchange: ML-KEM-768`. Sent 50 pings (100% success), then rechecked counters: |

```
BEFORE: #pkts encaps: 1400
AFTER:  #pkts encaps: 1400     ← unchanged after 50 successful pings
```

Interface counters DO work:

```
show interface Tunnel1 | include packets
     1601 packets input, 233746 bytes
     1601 packets output, 252958 bytes
```

The `#pkts encaps` number (1400) is stale from a previous SA. Silicon One ASIC handles crypto in hardware; software counters don't increment. Use `show interface TunnelX` instead.

#### C9K-B05: EAP-TLS 128-bit CAK despite GCM-AES-256 (same as C8K-B09)

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Protocol** | MACsec |
| **Finding** | Same `smd` implementation as C8000. 128-bit CAK, 64-bit quantum work factor under Grover's. |

#### C9K-B06: `hybrid` pqc-type fails MKA on C9300-24U ports

| | |
|---|---|
| **Status** | Confirmed by prior run (C9300-24U at 172.26.194.12/13 on 26.1 only; not re-testable on 26.2 from this session) |
| **Severity** | P2 |
| **Protocol** | MACsec |
| **Finding** | Only `pqc` works on 24U (gives ML-KEM-512). Other C9300 models handle `hybrid` fine. |

#### C9K-B07: `service internal` required for ML-DSA PKCS#12 import

| | |
|---|---|
| **Status** | **Verified today** (both C9350s have `service internal` enabled; C9350 #2 has working ML-DSA trustpoints with `mldsakeypair MLDSA-S4 65` and ML-DSA-65 certificates) |
| **Severity** | P2 |
| **Protocol** | PKI |
| **Today's verification** | |

```
C9350_PQC2_IPsec#show running-config | section crypto pki trustpoint MLDSA-S4
crypto pki trustpoint MLDSA-S4
 enrollment pkcs12
 revocation-check crl
 mldsakeypair MLDSA-S4 65
 hash sha512

show crypto pki certificates verbose → Public Key Algorithm: ML-DSA, Signature Algorithm: ML-DSA-65
```

This works only because `service internal` is on. Without it, `mldsakeypair` doesn't parse.

### GAPS (features not yet supported)

#### C9K-G01: No ML-DSA for SSH (host key or user key)

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P1 |
| **Today's output** | `show ip ssh` on C9350 #1 shows `Hostkey Algorithms: ecdsa-sha2-nistp256,...,rsa-sha2-512,rsa-sha2-256`. No ML-DSA. Same as C8000. |

#### C9K-G02: No ML-DSA for TLS certificate authentication

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P1 |
| **Today's output** | C9350 #1: `Signature type: rsa_pss_rsae_sha256`. C9350 #2 with ML-DSA trustpoint: `tlsv1 alert internal error`. |

#### C9K-G03: Entire boot chain is classical RSA (no LDWM/LMS)

| | |
|---|---|
| **Status** | **Verified today** |
| **Severity** | P2 |
| **Today's output** | `show software authenticity running` on C9350-48HX: |

```
SYSTEM IMAGE → Signature Algorithm: 2048-bit RSA, Verifier: ROMMON
ROMMON       → Signature Algorithm: 2048-bit RSA, Verifier: Microloader
Microloader  → Hash Algorithm: HMAC-SHA256, Verifier: Hardware Anchor
```

Compare with C8235-G2 where ROMMON and Microloader use LDWM. The C9350 has zero
layers of hash-based (quantum-resistant) signatures. HMAC-SHA256 at the bottom is
symmetric and survives Shor's by accident, but it's not a publicly verifiable signature.

#### C9K-G04: No PQC knob for RADIUS, TACACS+, syslog, or gNMI

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Finding** | Same as C8000. Only `ip http secure-pqc-type` (HTTPS) and `access-session pqc-type` (EAP-TLS) exist. |

#### C9K-G05: No show command for EAP-TLS negotiated key exchange group

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Finding** | Same as C8000. Only `smd` platform trace reveals it. |

#### C9K-G06: SUDI certificates still classical RSA-2048

| | |
|---|---|
| **Status** | Confirmed by prior run |
| **Severity** | P2 |
| **Finding** | OU: "ACT-2 Lite SUDI". All three certs RSA 2048 / SHA-256. |

#### C9K-G07: IPsec CLI only available starting 26.2

| | |
|---|---|
| **Status** | Confirmed by version check |
| **Severity** | P3 |
| **Today's check** | C9350-24U at 172.26.194.12 running 26.1.1eft13 (no IPsec/ML-KEM CLI). C9350-48HX at 172.26.194.61 running 26.2 has it working with a live ML-KEM tunnel. |

### OPERATIONAL QUIRKS

#### C9K-Q01: TLS PQC works out of the box

| | |
|---|---|
| **Status** | **Verified today** |
| **Today's output** | C9350 #1: `Negotiated TLS1.3 group: X25519MLKEM768` with `HTTP secure server PQC type: all` (default). No config needed. |

#### C9K-Q02: SSH ML-KEM KEX supported but not in default offer

| | |
|---|---|
| **Status** | **Verified today** |
| **Today's output** | Default KEX list on C9350 #1: `curve25519-sha256,...,diffie-hellman-group16-sha512`. No ML-KEM. Attempting `ssh -o KexAlgorithms=mlkem768x25519-sha256` fails with `no matching key exchange method found`. Needs explicit `ip ssh server algorithm kex` config. |

#### C9K-Q03: `crypto key generate mldsa` available on C9350-48HX

| | |
|---|---|
| **Status** | **Verified today** |
| **Today's output** | |

```
C9350_PQC1_IPsec#crypto key generate ?
  ec     Generate EC keys for ECDSA
  mldsa  Generate ML-DSA keys
  rsa    Generate RSA keys
```

ML-DSA key generation is available in exec mode on C9350. Same config-vs-exec mode discrepancy as C8000 (config mode shows only `ec` and `rsa`).

#### C9K-Q04: Live ML-KEM tunnel confirmed

| | |
|---|---|
| **Status** | **Verified today** |
| **Today's output** | |

```
C9350_PQC1_IPsec#show crypto ikev2 sa
Tunnel-id  Local           Remote          Status
3          110.0.1.1/500   110.0.1.2/500   READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:19,
      Auth sign: PSK, Auth verify: PSK
      PQC Key Exchange: ML-KEM-768
```

```
Quantum-safe Encryption using PQC: ML-KEM-768
```

ML-KEM-768 active on a live switch-to-switch tunnel. Key exchange is post-quantum, authentication is still PSK (classical).

---

## Cross-platform summary

### What works on both platforms (verified)

| Feature | C8000 | C9300 |
|---------|-------|-------|
| TLS hybrid PQC (X25519MLKEM768) by default | ✓ Verified | ✓ Verified |
| SSH ML-KEM KEX (after explicit config) | ✓ Verified | ✓ Confirmed |
| IPsec ML-KEM-768 hybrid | ✓ Confirmed | ✓ Verified (live tunnel) |
| IPsec ML-DSA certificate auth | ✓ Confirmed | ✓ Confirmed |
| MACsec PSK (quantum-safe, no exchange) | ✓ Confirmed | ✓ Confirmed |
| MACsec EAP-TLS with ML-KEM | ✓ Confirmed | ✓ Confirmed |
| ML-DSA key generation (`crypto key generate mldsa`) | ✓ Verified | ✓ Verified |
| ML-DSA PKCS#12 import (with `service internal`) | ✓ Confirmed | ✓ Verified |
| Boot chain LDWM (lower layers) | ✓ Verified | ✗ Not present |

### Top findings to raise with both BUs

1. **ML-DSA for EAP-TLS authentication is broken.** The `smd` → IOSd certificate chain
   retrieval path silently stalls for ML-DSA trustpoints. This blocks MACsec
   authentication migration on both platforms. (C8K-B02)

2. **ML-DSA for SSH and TLS management doesn't exist yet.** No host key algorithm, no
   working HTTPS certificate path. The HTTPS server wedges when pointed at an ML-DSA
   trustpoint. (C8K-G01, C8K-G02, C8K-B04, C9K-B03)

3. **ECDSA-sig in IKEv2 is broken on C8000** (and likely C9300). A classical algorithm
   that should work, doesn't. (C8K-B01)

4. **128-bit CAK from EAP-TLS is a real quantum weakness.** 64-bit effective security
   under Grover's. PSK gets 256-bit. The fix is in `smd`'s MSK→CAK derivation.
   (C8K-B09, C9K-B05)

5. **C9300 boot chain has zero PQ layers** (vs C8000's two LDWM layers). Hardware
   generation question for ACT-2 Lite. (C9K-G03)

6. **IPsec counters frozen on C9350** (Silicon One offload). Not a bug per se, but
   confuses every troubleshooting workflow that relies on `show crypto ipsec sa`.
   (C9K-B04)

7. **`service internal` gates ML-DSA on pre-GA 26.2.** Should not ship to GA this way.
   (C8K-Q01, C9K-B07)

### Findings where the original documentation needs correction

| Finding | Original claim | What we found today |
|---------|---------------|-------------------|
| C9350 IPsec counters | "stays at zero" (blanket statement) | `#pkts encaps` shows a stale number from a prior SA but does NOT increment. The counter is frozen, not zero. Functionally the same problem, but "frozen" is more accurate than "zero". |
