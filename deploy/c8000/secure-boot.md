# Secure Boot on Cisco C8000 Secure Routers

The other docs in this series are about protocols you *configure*: IKEv2 tunnels, SSH
KEX algorithms, MACsec policies, TLS cipher suites. This one is different.

Secure boot isn't something you turn on. It's on the moment the router gets power.
The Trust Anchor module (TAm) starts validating code before a single packet ever
forwards. 

So why is there an exercise? Because you can *see* it. IOS XE exposes the full
signature chain, the boot-stage hashes, and the device identity certificates. You can
verify all of it from the CLI, extract the certs, and check them against Cisco's
published PKI. More importantly, the output reveals that **the C8235-G2 boot chain is already partially post-quantum**.

Everything below was captured on a **C8235-G2** running **IOS XE 26.2**.


## How the boot chain works

Every C8000 G2 Secure Router ships with a TAm chip: a tamper-resistant hardware
module that stores cryptographic keys, the device's unique identity (SUDI), and the
root of trust for the entire boot sequence.

The chain has four stages, and each one validates the next before handing off:

```
TAm (hardware root) ──verifies──▶ Microloader ──verifies──▶ ROMMON ──verifies──▶ IOS XE + packages
```

If any stage fails signature verification, the device refuses to boot. No packets
forward. No CLI. Nothing.


## Exercise 1: Map the boot chain and find the PQC split

This command shows every layer of the boot sequence, who signed it, and what
algorithm was used:

```
show software authenticity running
```

The output is long (every `.pkg` file gets its own block), but the interesting parts
are at the bottom. Here's what our C8235-G2 shows, condensed to the three layers that
matter:

```
SYSTEM IMAGE
------------
Image type                    : Production
    Signer Information
        Common Name           : CiscoSystems
        Organization Unit     : IOS-XE
        Organization Name     : CiscoSystems
    Certificate Serial Number : 6A855FA7
    Hash Algorithm            : SHA512
    Signature Algorithm       : 2048-bit RSA
    Key Version               : A

    Verifier Information
        Verifier Name         : ROMMON
        Verifier Version      : Version 17.18(4.1r).s1.cp

ROMMON
------
Image type                    : Production
    Signer Information
        Common Name           : CiscoSystems
        Organization Unit     : IOS-XE
        Organization Name     : CiscoSystems
    Certificate Serial Number : 3639434339313834
    Hash Algorithm            : SHA256
    Signature Algorithm       : LDWM
    Key Type                  : REL
    LDWM Algorithm            : SHA256_TRUNC_8
    LDWM Signature Type       : SIGNATURE_Y67
    LDWM W Parameter          : W_EIGHT
    LDWM MTS Parameter        : MTS_K4_H10
    LDWM APATH Parameter      : MTS_PATH_T30
    Verifier Information
        Verifier Name         : Microloader
        Verifier Version      : MK0001R01.0003312026

Microloader
-----------
Image type                    : Release
    Signer Information
        Common Name           : CiscoSystems
        Organization Name     : CiscoSystems
    Certificate Serial Number : fe5d8830eaeae61d32aebbc4f814e645
    Hash Algorithm            : SHA256
    Signature Algorithm       : LDWM (m=20, w=4, k=4, h=10)
    Verifier Information
        Verifier Name         : Hardware Anchor
        Verifier Version      : SDK112312.CISCOSTUB.D01.00
```

Read it bottom-up, because that's the order things actually execute:

| Boot stage | Verified by | Signature algorithm | Quantum-resistant? |
|------------|-------------|--------------------|--------------------|
| Microloader | Hardware Anchor (TAm) | **LDWM** (m=20, w=4, k=4, h=10) | Yes |
| ROMMON (bootloader) | Microloader | **LDWM** (SHA256, W_EIGHT, MTS_K4_H10) | Yes |
| IOS XE system image | ROMMON | 2048-bit RSA + SHA512 | No |
| All packages (.pkg) | IOS XE | 2048-bit RSA + SHA512 | No |

The bottom two layers are already post-quantum. The top two are still classical.


### What is LDWM?

LDWM stands for *Leighton-Diffie-Winternitz-Merkle*. It's a hash-based signature
scheme, and it's the direct ancestor of **LMS** (Leighton-Micali Signature, RFC 8554),
which NIST standardized in SP 800-208.

The security of hash-based signatures comes from a completely different place than RSA
or ECDSA. RSA relies on the hardness of factoring large integers. ECDSA relies on the
discrete logarithm problem over elliptic curves. Both of those fall to Shor's algorithm
on a quantum computer.

LDWM (and LMS) rely only on the security of the underlying hash function (SHA-256 in
this case). There is no known quantum algorithm that breaks hash functions
catastrophically. Grover's algorithm gives a quadratic speedup for brute-force search,
which means you need to double your hash output length to maintain the same security
margin. SHA-256 with a 256-bit output still gives you 128 bits of security against a
quantum adversary, which is plenty.

The tradeoff? Hash-based signatures are *stateful*: the signer has to track how many
signatures it's generated, because reusing a one-time key breaks the scheme. That's
fine for firmware signing at Cisco's build servers (they sign each image once). It would
be terrible for a protocol that signs packets on the fly, which is why IKEv2 and TLS
use ML-DSA instead.

Cisco's
[Post-Quantum Trust Anchors white paper](https://www.cisco.com/c/dam/en_us/about/doing_business/trust-center/docs/post-quantum-trust-anchors-wp.pdf)
explains this design choice:

> *We use LMS to sign firmware generated at Cisco so that the device can detect modified
> or inauthentic binary images. The post-quantum signature algorithm for general use is
> ML-DSA.*

LDWM has been in Cisco Trust Anchor modules since 2013. Newer G2 models
(C8211-G2, C8221-G2, C8221L-G2, C8225-G2) use the NIST-standardized LMS; the C8235-G2
uses the older LDWM. Same family, same quantum resistance, different vintage.


## Exercise 2: Verify boot integrity measurements

The previous exercise told you *what algorithms* protect each layer. This one gives you
the *hashes* of what actually booted, signed by the device so you can prove the
measurements are genuine.

```
show platform integrity sign nonce 12345
```

Pick any nonce you like. The nonce is included in the signature, which prevents replay
attacks: someone can't record the output from a known-good boot and play it back later
after tampering with the image.

Here's what we got (nonce `99999`):

```
Platform: C8235-G2
Boot 0 Version: MK0001R01.0003312026
Boot 0 Hash: FE5D8830EAEAE61D32AEBBC4F814E6450556CD64B9B85FE951D72BCBD18F46CD
Boot Loader Version: 17.18(4.1r).s1.cp
Boot Loader Hash: 4252D5DA49413EC53D70AEE417D24562E10C58BDB5C73F0B1E411B99670C81A2
OS Version: 26.02.01eftr2
OS Hashes:
c8kg2be-rpboot.26.02.01eftr2.SPA.pkg: D54F5F384E214819...AF1EF4BC
c8kg2be-mono-universalk9.26.02.01eftr2.SPA.pkg: 928945C756CE55E8...A9375790
c8kg2be-firmware_mirabile_mcu.26.02.01eftr2.SPA.pkg: 57C6C62D5D589DFC...CC854CAE
...  (one hash per installed package)
PCR0: 01AFB79396D483A1D857C84051BE769FE6572858327D109BC5957FC1BB2DC568
PCR8: C8B3B1D3532FC49D2A3383B9F1D1B8372280477412D186008DDC0F0EF3B03EAF
Signature version: 1
Signature:
9B757C080940A9D5...879202051E
```

What you're looking at:

| Field | What it means |
|-------|---------------|
| Boot 0 Hash | SHA-256 hash of the microloader |
| Boot Loader Hash | SHA-256 hash of ROMMON |
| OS Hashes | SHA-512 hash of each installed .pkg file |
| PCR0, PCR8 | Platform Configuration Register values (aggregate measurements of the boot chain, similar to TPM PCRs) |
| Signature | RSA signature over the entire output including your nonce |

You can compare these hashes against Cisco-published Known Good Values (KGVs) for your
software release. If something doesn't match, either the image isn't genuine Cisco code
or it's been modified after signing. The
[Cisco PKI Index](https://www.cisco.com/security/pki/) publishes the root and
subordinate CA certificates you'd need to verify the signature programmatically.


## Exercise 3: Inspect device identity (SUDI)

Every C8000 G2 router has a Secure Unique Device Identifier (SUDI) burned into the TAm
chip at manufacturing. It's an X.509 certificate chain that cryptographically binds the
device's serial number and product ID to Cisco's PKI. This is how Zero Touch
Provisioning (ZTP), Plug-and-Play (PnP), and other onboarding mechanisms know they're
talking to a genuine Cisco device and not a counterfeit.

```
show platform sudi certificate sign nonce 12345
```

The output is three PEM-encoded certificates plus a signature:

**Certificate 1: Cisco Root CA 2099**

```
-----BEGIN CERTIFICATE-----
MIIDITCCAgmgAwIBAgIJAZozWHjOFsHBMA0GCSqGSIb3DQEBCwUAMC0xDjAMBgNV
BAoTBUNpc2NvMRswGQYDVQQDExJDaXNjbyBSb290IENBIDIwOTkwIBcNMTYwODA5
...
-----END CERTIFICATE-----
```

**Certificate 2: High Assurance SUDI CA**

```
-----BEGIN CERTIFICATE-----
MIIEZzCCA0+gAwIBAgIJCmR1UkzYYXxiMA0GCSqGSIb3DQEBCwUAMC0xDjAMBgNV
BAoTBUNpc2NvMRswGQYDVQQDExJDaXNjbyBSb290IENBIDIwOTkwIBcNMTYwODEx
...
-----END CERTIFICATE-----
```

**Certificate 3: Device SUDI (this specific router)**

```
-----BEGIN CERTIFICATE-----
MIIEljCCA36gAwIBAgIKBygCklYnIZBxETANBgkqhkiG9w0BAQsFADAxMR8wHQYD
VQQDExZIaWdoIEFzc3VyYW5jZSBTVURJIENBMQ4wDAYDVQQKEwVDaXNjbzAgFw0y
...
-----END CERTIFICATE-----
```

The chain is: **Cisco Root CA 2099** → **High Assurance SUDI CA** → **Device cert
(PID:C8235-G2)**. You can verify the first two certificates match what Cisco publishes
at [https://www.cisco.com/security/pki/](https://www.cisco.com/security/pki/).

The device certificate encodes the Product ID and Serial Number in the Subject field,
so each router on a network of thousands can be uniquely identified.

All three certificates use **RSA 2048 with SHA-256**. That's classical crypto.
PQC-signed SUDI certificates (using ML-DSA-87) are on Cisco's roadmap but not deployed
yet. When they arrive, the device will carry both a classical and a PQC SUDI, so
verifiers that don't support PQC yet can still authenticate the device.

> **Security Review:** These are manufacturing-installed identity certificates. The
> RSA 2048 key strength and SHA-256 signature algorithm meet current security
> requirements. The self-signed Root CA 2099 is intentional: it's Cisco's manufacturing
> trust anchor, explicitly configured in every device's TAm. The validity window
> (2016-2099) is typical for long-lived hardware identity anchors.


## The PQC migration picture

Here's where things stand on the C8235-G2 today, and what's coming:

| Layer | Current algorithm | Quantum-resistant? | Roadmap |
|-------|------------------|--------------------|---------|
| TAm → Microloader | LDWM | Yes | Migrate to NIST LMS (RFC 8554) |
| Microloader → ROMMON | LDWM | Yes | Migrate to NIST LMS (RFC 8554) |
| ROMMON → IOS XE image | 2048-bit RSA | No | ML-DSA-87 image signing |
| IOS XE → packages | 2048-bit RSA | No | ML-DSA-87 image signing |
| SUDI certificates | RSA 2048 | No | ML-DSA-87 signed SUDI |
| CPU ↔ TAm bus | AES-GCM-256 | Yes (symmetric) | No change needed |

The bottom of the stack got PQC first because it's the hardest to update. The TAm and
microloader are burned into hardware; if a quantum computer could forge their signatures,
you'd need to physically replace the chip. LDWM has been protecting that layer since
2013.

The top of the stack (image signing, SUDI certs) is easier to update through software
releases, so it's further back in the queue. Cisco's roadmap calls for ML-DSA-87 image
signing and PQC-signed SUDI certificates on the G2 line. Some smaller models
(C8211-G2, C8221-G2, C8221L-G2, C8225-G2) already have NIST LMS at the bootloader
level; the C8235-G2 uses the older LDWM, which is the same hash-based signature family.

The practical takeaway: the part of the boot chain that's hardest to fix later is
already quantum-safe. The part that's easiest to update through a software release is
still classical, and that's by design.


## References

- [Cisco Post-Quantum Trust Anchors White Paper](https://www.cisco.com/c/dam/en_us/about/doing_business/trust-center/docs/post-quantum-trust-anchors-wp.pdf)
- [Cisco 8000 Series Secure Routers FAQ](https://www.cisco.com/c/en/us/products/collateral/networking/sdwan-routers/8000-series-secure-routers-faq.html)
- [Cisco 8000 Series Trustworthy Framework](https://www.cisco.com/c/en/us/products/routers/8000-series-routers/trustworthy-framework.html)
- [Cisco PKI Index](https://www.cisco.com/security/pki/) (for verifying SUDI certificate chains)
- [RFC 8554: LMS Hash-Based Signatures](https://datatracker.ietf.org/doc/html/rfc8554)
- [NIST SP 800-208: Recommendation for Stateful Hash-Based Signature Schemes](https://csrc.nist.gov/publications/detail/sp/800-208/final)
