# Secure Boot on Cisco C9300 Smart Switches

The other docs in this series are about protocols you *configure*: MACsec policies,
IKEv2 proposals, SSH KEX algorithms, TLS cipher suites. This one is different.

Secure boot isn't something you turn on. It's on the moment the switch gets power.
A hardware anchor validates code before a single frame ever forwards.

So why is there an exercise? Because you can *see* it. IOS XE exposes the full
signature chain, the boot-stage hashes, and the device identity certificates. You can
verify all of it from the CLI, extract the certs, and check them against Cisco's
published PKI.

One thing to set expectations up front: **the C9300 boot chain is entirely classical
today**. The [C8000 secure boot doc](../c8000/secure-boot.md) tells a different story,
where the bottom two boot layers use LDWM (a hash-based, quantum-resistant signature
scheme). The C9300 doesn't have that. Every layer here is 2048-bit RSA. That's worth
knowing, and understanding *why* requires looking at what's actually running.

Everything below was captured on a **C9350-48HX** running **IOS XE 26.2**.


## How the boot chain works

Every C9300 ships with a hardware trust anchor (ACT-2 Lite) that stores cryptographic
keys and the root of trust for the entire boot sequence. The chain has four stages,
and each one validates the next before handing off:

```
Hardware Anchor (ACT-2 Lite) ──verifies──▶ Microloader ──verifies──▶ ROMMON ──verifies──▶ IOS XE + packages
```

If any stage fails verification, the switch refuses to boot. No frames forward. No
CLI. Nothing.

### ACT-2 Lite vs TAm

The C8000 G2 routers use a TAm (Trust Anchor module) chip. The C9300 uses ACT-2 Lite.
Both serve the same purpose (hardware root of trust, SUDI storage, boot chain
anchoring), but they're different hardware with different capabilities. The biggest
difference for PQC: the C8000's TAm signs the microloader and ROMMON with LDWM, a
hash-based scheme that's quantum-resistant. ACT-2 Lite on the C9300 uses HMAC-SHA256
for the microloader and RSA for everything above it.


## Exercise 1: Map the boot chain and find the algorithms

This command shows every layer of the boot sequence, who signed it, and what
algorithm was used:

```
show software authenticity running
```

The output lists every `.pkg` file first (they're all the same: 2048-bit RSA + SHA512,
verified by `rp_base`). The interesting parts are at the bottom. Here's what our
C9350 shows, condensed to the three layers that matter:

```
SYSTEM IMAGE
------------
Image type                    : Special
    Signer Information
        Common Name           : CiscoSystems
        Organization Unit     : IOS-XE
        Organization Name     : CiscoSystems
    Certificate Serial Number : 69F7F562
    Hash Algorithm            : SHA512
    Signature Algorithm       : 2048-bit RSA
    Key Version               : A

    Verifier Information
        Verifier Name         : ROMMON
        Verifier Version      : System Bootstrap, Version 26.1.1r[FC4]

ROMMON
------
Image type                    : Special
    Signer Information
        Common Name           : CiscoSystems
        Organization Unit     : IOS-XE
        Organization Name     : CiscoSystems
    Certificate Serial Number : 69F7F562
    Hash Algorithm            : SHA512
    Signature Algorithm       : 2048-bit RSA
    Key Version               : A

    Verifier Information
        Verifier Name         : Microloader
        Verifier Version      : MA1118R07.1007152025

Microloader
-----------
Image type                    : Release
    Signer Information
        Common Name           : CiscoSystems
        Organization Name     : CiscoSystems
    Certificate Serial Number : c1ae12a2e27f620f9b0e617624813ae9
    Hash Algorithm            : HMAC-SHA256
    Verifier Information
        Verifier Name         : Hardware Anchor
        Verifier Version      : F01418R24.01b5f7b782025-02-07
```

Read it bottom-up, because that's the order things actually execute:

| Boot stage | Verified by | Algorithm | Quantum-resistant? |
|------------|-------------|-----------|-------------------|
| Microloader | Hardware Anchor (ACT-2 Lite) | HMAC-SHA256 | See below |
| ROMMON (bootloader) | Microloader | 2048-bit RSA + SHA512 | No |
| IOS XE system image | ROMMON | 2048-bit RSA + SHA512 | No |
| All packages (.pkg) | IOS XE | 2048-bit RSA + SHA512 | No |

### What about that HMAC-SHA256?

The microloader layer is the one that looks different. HMAC-SHA256 is a *symmetric*
Message Authentication Code, not a digital signature. It uses a shared secret key
between the hardware anchor and the microloader, rather than a public/private key pair.

That matters for quantum resistance in an unexpected way. Shor's algorithm (the quantum
threat to RSA and ECDSA) attacks the mathematical relationship between public and
private keys. HMAC doesn't have that structure: there's no public key for an attacker
to work with. An attacker would need Grover's algorithm against the 256-bit HMAC key,
which gives a 128-bit quantum work factor. That's solid.

So the microloader verification is *technically* quantum-resistant by accident of being
symmetric, but it's not a signature in the cryptographic sense. You can't independently
verify an HMAC without knowing the secret key. The hardware anchor and the microloader
share that key internally; you can't extract it and check it yourself. It's more like
a hardware integrity seal than a verifiable chain of trust.

Compare that with the C8000, where the same layer uses LDWM (a proper hash-based
digital signature). LDWM is quantum-resistant *and* publicly verifiable. That's a
meaningful difference in the trust model.

### Comparison with C8000

Here's the same table for the C8000 G2 router, side by side:

| Boot stage | C9350 (ACT-2 Lite) | C8235-G2 (TAm) |
|------------|-------------------|-----------------|
| Microloader ← Hardware | HMAC-SHA256 | **LDWM** (hash-based signature) |
| ROMMON ← Microloader | 2048-bit RSA | **LDWM** (hash-based signature) |
| IOS XE ← ROMMON | 2048-bit RSA | 2048-bit RSA |
| Packages ← IOS XE | 2048-bit RSA | 2048-bit RSA |

The C8000 has two layers of quantum-resistant LDWM signatures. The C9300 has zero. The
HMAC layer is symmetric and survives Shor's, but the RSA layers above it don't.


## Exercise 2: Verify boot integrity measurements

The previous exercise told you *what algorithms* protect each layer. This one gives you
the *hashes* of what actually booted, signed by the device so you can prove the
measurements are genuine.

```
show platform integrity sign nonce 99999
```

Pick any nonce you like. The nonce is included in the signature, which prevents replay
attacks: someone can't record the output from a known-good boot and play it back later
after tampering with the image.

```
Platform: C9350-48HX
Boot 0 Version: MA1118R07.1007152025
Boot 0 Hash: C1AE12A2E27F620F9B0E617624813AE95702557A06FBFA86B182E7A3EBB66EC5
Boot Loader Version: System Bootstrap, Version 26.1.1r[FC4], RELEASE SOFTWARE (P)
Boot Loader Hash: 48EB4A9FDE11083DC3E6C1B5AB17153ADEC562CA11D45CB2C84D7FFDE74566E2
OS Version: BLD_V262_THROTTLE_LATEST_20260504_003842
OS Hashes:
cisco9k-rpboot.BLD_V262_THROTTLE_LATEST_20260504_003842.SSA.pkg: 4C961F7405...34C5C0
cisco9k-webui.BLD_V262_THROTTLE_LATEST_20260504_003842.SSA.pkg: CFF32821E3...E9A32B
cisco9k-srdriver.BLD_V262_THROTTLE_LATEST_20260504_003842.SSA.pkg: 9EB2CE2CD4...E39E45
cisco9k-wlc.BLD_V262_THROTTLE_LATEST_20260504_003842.SSA.pkg: F55FD08CA6...A1B06
cisco9k-lni.BLD_V262_THROTTLE_LATEST_20260504_003842.SSA.pkg: 836E3A6A01...3C08E
cisco9k-cc_srdriver.BLD_V262_THROTTLE_LATEST_20260504_003842.SSA.pkg: 7C38692C83...853654
cisco9k-guestshell.BLD_V262_THROTTLE_LATEST_20260504_003842.SSA.pkg: 3C49144DE5...A67F8A
cisco9k-rpbase.BLD_V262_THROTTLE_LATEST_20260504_003842.SSA.pkg: 1B6CC3E68C...0BF54C
PCR0: B66F85EDD49B328C99C3D1CD3E62A80F8EA0591DB345061985601A421C8A1462
PCR8: F26BFFA4C8C12B90178D861CEF9E73EEC5068002B0BAA71FAD932B2EE6DE81AB
Signature version: 1
Signature:
5C1867DE7DD42BEE...3AE7526
```

What you're looking at:

| Field | What it means |
|-------|---------------|
| Boot 0 Hash | SHA-256 hash of the microloader |
| Boot Loader Hash | SHA-256 hash of ROMMON |
| OS Hashes | SHA-512 hash of each installed .pkg file |
| PCR0, PCR8 | Platform Configuration Register values (aggregate measurements of the boot chain) |
| Signature | RSA signature over the entire output including your nonce |

You can compare these hashes against Cisco-published Known Good Values (KGVs) for your
software release. If something doesn't match, either the image isn't genuine Cisco code
or it's been modified after signing. The
[Cisco PKI Index](https://www.cisco.com/security/pki/) publishes the root and
subordinate CA certificates you'd need to verify the signature programmatically.


## Exercise 3: Inspect device identity (SUDI)

Every C9300 has a Secure Unique Device Identifier (SUDI) burned into the ACT-2 Lite
chip at manufacturing. It's an X.509 certificate chain that cryptographically binds the
device's serial number and product ID to Cisco's PKI. This is how Zero Touch
Provisioning (ZTP), Plug-and-Play (PnP), and other onboarding mechanisms know they're
talking to a genuine Cisco device and not a counterfeit.

```
show platform sudi certificate sign nonce 99999
```

The output is three PEM-encoded certificates plus a signature:

**Certificate 1: Cisco Root CA 2099**

The same root CA used on C8000 routers. Self-signed, RSA 2048, valid 2016-2099.

**Certificate 2: High Assurance SUDI CA**

Intermediate CA, signed by Root CA 2099. Issues per-device SUDI certificates.

**Certificate 3: Device SUDI (this specific switch)**

The device certificate encodes the Product ID and Serial Number in the Subject field:

```
Subject: serialNumber = PID:C9350-48HX SN:FVH2952L1JT
         O = Cisco
         OU = ACT-2 Lite SUDI
         CN = Q5CA-4AQR-L9YS
```

Notice the OU: **ACT-2 Lite SUDI**. On a C8000, that reads `ACT-2 SUDI` (no "Lite").
Different hardware anchor, different certificate issuance path, same trust chain root.

The chain is: **Cisco Root CA 2099** → **High Assurance SUDI CA** → **Device cert
(PID:C9350-48HX)**. You can verify the first two certificates match what Cisco
publishes at [https://www.cisco.com/security/pki/](https://www.cisco.com/security/pki/).

All three certificates use **RSA 2048 with SHA-256**. That's classical crypto.
PQC-signed SUDI certificates (using ML-DSA-87) are on Cisco's roadmap but not deployed
yet. When they arrive, the device will carry both a classical and a PQC SUDI, so
verifiers that don't support PQC yet can still authenticate the device.

> **Security Review:** These are manufacturing-installed identity certificates. The
> RSA 2048 key strength and SHA-256 signature algorithm meet current security
> requirements. The self-signed Root CA 2099 is intentional: it's Cisco's manufacturing
> trust anchor, explicitly configured in every device's hardware. The validity window
> (2016-2099) is typical for long-lived hardware identity anchors.


## The PQC migration picture

Here's where the C9350 stands today, compared with the C8000:

| Layer | C9350 algorithm | C8235-G2 algorithm | Quantum-resistant? |
|-------|----------------|-------------------|--------------------|
| Hardware → Microloader | HMAC-SHA256 | LDWM | C9350: symmetric (yes*) / C8000: yes |
| Microloader → ROMMON | 2048-bit RSA | LDWM | C9350: no / C8000: yes |
| ROMMON → IOS XE image | 2048-bit RSA | 2048-bit RSA | No |
| IOS XE → packages | 2048-bit RSA | 2048-bit RSA | No |
| SUDI certificates | RSA 2048 | RSA 2048 | No |

\* HMAC-SHA256 is symmetric and immune to Shor's, but it's not a publicly verifiable
signature. See [Exercise 1](#what-about-that-hmac-sha256) for why that distinction
matters.

The C8000 has LDWM (hash-based signatures) protecting the two lowest boot layers.
Those are the hardest to update: the TAm and microloader are burned into hardware, so
if a quantum computer could forge their signatures, you'd need to physically replace
the chip. Getting PQC there first was the right call.

The C9300 doesn't have that luxury yet. Its ACT-2 Lite hardware anchor uses HMAC for
the microloader (which survives Shor's by being symmetric) and RSA for everything
above it. The ROMMON layer, which the C8000 already protects with LDWM, is still RSA
on the C9300.

What's coming:

- **ML-DSA-87 image signing** will replace RSA at the IOS XE and package layers. This
  is a software update, so it can arrive in a future IOS XE release without hardware
  changes.
- **PQC-signed SUDI certificates** (ML-DSA-87) will provide quantum-resistant device
  identity alongside the existing classical SUDI.
- **LMS/LDWM at the boot layers** depends on ACT-2 Lite hardware evolution. The C8000's
  TAm already has it; whether ACT-2 Lite gets the same capability is a hardware
  generation question.

The practical takeaway: on the C9300, the entire boot chain from ROMMON up is
classical RSA today. The microloader HMAC is symmetric and survives quantum, but it's
the only layer that does. Everything configurable (protocols, key exchange, MACsec,
IPsec) can already use ML-KEM and ML-DSA. The boot chain itself is waiting for the
next round of updates.


## References

- [Cisco Post-Quantum Trust Anchors White Paper](https://www.cisco.com/c/dam/en_us/about/doing_business/trust-center/docs/post-quantum-trust-anchors-wp.pdf)
- [Cisco Catalyst 9300 Series Data Sheet](https://www.cisco.com/c/en/us/products/collateral/switches/catalyst-9300-series-switches/nb-06-cat9300-ser-data-sheet-cte-en.html)
- [Cisco PKI Index](https://www.cisco.com/security/pki/) (for verifying SUDI certificate chains)
- [RFC 8554: LMS Hash-Based Signatures](https://datatracker.ietf.org/doc/html/rfc8554)
- [NIST SP 800-208: Recommendation for Stateful Hash-Based Signature Schemes](https://csrc.nist.gov/publications/detail/sp/800-208/final)
- [C8000 Secure Boot doc](../c8000/secure-boot.md) (same exercises, different results)
