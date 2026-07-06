# Hands-on with Post-Quantum Cryptography for Network Infrastructure

A set of hands-on labs that take real network security protocols all the way to **post-quantum**, one piece of the handshake at a time, plus a foundations lab on the lattice math underneath it all. You'll spin up containers, run real strongSwan, wpa_supplicant/hostapd, and OpenSSL, capture packets, and measure the trade-offs with your own eyes, no hand-waving. Everything runs locally; the only thing you need is **Docker**.

Every secure handshake rests on two pillars, and quantum computers threaten both:

- **Key exchange** decides the shared secret. It's vulnerable to *"harvest now, decrypt later"*: an attacker records your traffic today and decrypts it once a quantum computer arrives. This is the urgent one. The fix is **ML-KEM**.
- **Authentication** proves who's on the other end. Its quantum deadline is different (and sneakier): there's no retroactive forgery, but your long-lived certificates and CAs must outlive the threat. The fix is **ML-DSA**.

The labs are grouped into four families. The three protocol families (IKEv2, TLS, and MACsec) each tackle both pillars: IKEv2 and TLS split them across two labs, while MACsec covers both in a single EAP-TLS handshake. The last family is the math foundation that explains *why* the post-quantum algorithms work.

---

## IKEv2 / IPsec (Layer 3 VPNs)

Take a real IKEv2/IPsec VPN tunnel post-quantum with **strongSwan**.

| Lab | What you'll do |
|-----|----------------|
| **[Key Exchange](ipsec/key-exchange/README.md)** | Negotiate a hybrid **DH + ML-KEM** key exchange over a real IKEv2 handshake, capture it, compare classical vs hybrid packet for packet, then reach quantum safety a different way with a **post-quantum preshared key**. |
| **[Authentication](ipsec/authentication/README.md)** | Generate **ML-DSA / SLH-DSA** keys and certificates, weigh the size explosion, then mutually authenticate an IKEv2 tunnel: classical ECDSA first, then post-quantum ML-DSA. |

Start with **Key Exchange**: it introduces the containers, strongSwan, and the hybrid handshake that the Authentication lab builds on.

## TLS 1.3 (the web's secure channel)

Take **TLS** post-quantum, the protocol behind HTTPS and most application traffic, using just **OpenSSL 3.5**. No strongSwan or EAP stack here, so these two make a good standalone pair if TLS is your world.

| Lab | What you'll do |
|-----|----------------|
| **[Key Exchange](tls/key-exchange/README.md)** | Run a real TLS 1.3 handshake that negotiates a hybrid **X25519 + ML-KEM** key exchange (**`X25519MLKEM768`**), capture it, and compare it to classical X25519 byte for byte. See why TLS needs **no extra round trip** for ML-KEM, unlike IKEv2. |
| **[Authentication](tls/authentication/README.md)** | Generate **ML-DSA** keys and certificates, weigh the size jump against classical **ECDSA**, then mutually authenticate a real TLS connection: ECDSA first, then post-quantum ML-DSA, and measure what the bigger certificates do to the handshake. |

Same two pillars as the other protocol families, this time at the application layer. Both run on stable OpenSSL 3.5, where post-quantum TLS authentication is a step ahead of its IKEv2 equivalent.

## MACsec / 802.1X (Layer 2 link encryption)

Take **MACsec (IEEE 802.1AE)** post-quantum, one layer down from IPsec, using EAP-TLS with **wpa_supplicant + hostapd**: essentially the same TLS 1.3 handshake from the TLS lab, now carried at Layer 2. MACsec's entire quantum exposure is in that EAP-TLS handshake, so the lab runs the whole post-quantum upgrade in userspace on plain Docker.

| Lab | What you'll do |
|-----|----------------|
| **[MACsec](macsec/README.md)** | Trace MACsec's key hierarchy (MSK → CAK → SAK), then run a real EAP-TLS handshake and prove, in the captured bytes, that it negotiates hybrid **`X25519MLKEM768`**; watch how easily it silently downgrades to classical TLS 1.2; then swap the certificates from classical ECDSA to post-quantum **ML-DSA** with a one-line reissue and measure the size cost as EAP fragments the handshake across 3-4x more EAPOL frames. |

Unlike the IKEv2 and TLS families, both pillars live in a **single** EAP-TLS handshake here, so this is one combined lab, at Layer 2 over a different control plane, a useful contrast for anyone running switching/access infrastructure.

## Module Lattices (the math foundation)

| Lab | What you'll do |
|-----|----------------|
| **[Module Lattices](module-lattices/README.md)** | *(Optional deep-dive on the math used by some post-quantum algorithms.)* Build a lattice from scratch, watch noise turn easy algebra into hard **LWE**, implement a baby **ML-KEM** over the real ring, then run a real lattice attack and watch its cost explode: the concrete reason a quantum computer can't break **ML-KEM or ML-DSA**. |

An optional deep-dive for when you want to understand the shared module-lattice foundation under both ML-KEM and ML-DSA, and *why* neither is breakable by a quantum computer.

Each lab's README is self-contained and run from its own directory.

---

## Prerequisites

These labs run entirely on **your own local workstation** (laptop or desktop): no cloud, no remote servers, no dedicated hardware. Everything happens inside throwaway Docker containers on your machine, so you can run, break, and rerun them as many times as you like.

- **Docker** with Compose v2 (`docker compose ...`). That's it: everything else compiles or runs inside throwaway containers.

## A note on lab security

These are **labs**, not production templates. They deliberately keep authentication trivial where it's not the subject (the IKEv2 key-exchange lab uses a hardcoded throwaway PSK) and generate unencrypted keys for convenience. Never reuse the keys, certs, or PSKs here, and never commit secrets; the [.gitignore](.gitignore) already excludes the credentials the cert generators produce at runtime.

## License

Released under the terms in [LICENSE](LICENSE).
