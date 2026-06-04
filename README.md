# Post-Quantum IKEv2 Labs

A pair of hands-on labs that take a real IKEv2/IPsec VPN all the way to **post-quantum**, one half of the handshake at a time. You'll spin up containers, run real strongSwan and OpenSSL, capture packets, and measure the trade-offs with your own eyes — no hand-waving.

A secure VPN handshake rests on two pillars, and quantum computers threaten both:

- **Key exchange** decides the shared secret. It's vulnerable to *"harvest now, decrypt later"* — an attacker records your traffic today and decrypts it once a quantum computer arrives. This is the urgent one.
- **Authentication** proves who's on the other end. Its quantum deadline is different (and sneakier): your long-lived trust anchors must outlive the threat, even though a live handshake only has to resist forgery up to the moment it's verified.

Each lab tackles one pillar, end to end.

## The labs

| # | Lab | What you'll do | Tools |
|---|-----|----------------|-------|
| 1 | [Key exchange: Can Your VPN Survive a Quantum Computer!?](key-exchange/README.md) | Negotiate a hybrid **X25519 + ML-KEM-768** key exchange over a real IKEv2 handshake (RFC 9370), capture it, and compare classical vs hybrid packet for packet. | strongSwan (stable `ml` plugin) |
| 2 | [Authentication: Who Goes There!?](authentication/README.md) | Generate **ML-DSA / SLH-DSA** keys and certificates, weigh the size explosion, then mutually authenticate an IKEv2 tunnel — classical ECDSA first, then post-quantum ML-DSA. | OpenSSL 3.5+, strongSwan (`ml-dsa` branch) |

Start with **Lab 1** — it introduces the containers, strongSwan, and the hybrid handshake that Lab 2 builds on. Each lab's README is self-contained and run from its own directory.

## Prerequisites

- **Docker** with Compose v2 (`docker compose ...`). That's it — everything else compiles or runs inside throwaway containers.

## Repository layout

```text
.
├── README.md              # you are here
├── LICENSE
├── docker/                # shared build assets for the strongSwan images
│   ├── Dockerfile         # stable strongSwan 6.0.6 (native ML-KEM) — both labs
│   ├── Dockerfile.mldsa   # experimental ml-dsa branch — Lab 2, Stage 2 (Exercise 5)
│   └── entrypoint.sh
├── key-exchange/          # Lab 1 — ML-KEM hybrid key exchange (PSK auth)
│   ├── README.md
│   ├── docker-compose.yml
│   └── config/{initiator,responder}/swanctl.conf
└── authentication/        # Lab 2 — ML-DSA mutual authentication
    ├── README.md
    ├── docker-compose.yml         # Stage 1 (Exercise 4): classical ECDSA cert auth
    ├── docker-compose.mldsa.yml   # Stage 2 (Exercise 5): post-quantum ML-DSA override
    ├── gen-certs.sh               # mints the lab CA + leaf certs
    └── config/{initiator,responder}/swanctl.conf
```

## A note on lab security

These are **labs**, not production templates. They deliberately keep authentication trivial where it's not the subject (Lab 1 uses a hardcoded throwaway PSK) and generate unencrypted keys for convenience. Never reuse the keys, certs, or PSKs here, and never commit secrets — the [.gitignore](.gitignore) already excludes the credentials `gen-certs.sh` produces at runtime.

## License

Released under the terms in [LICENSE](LICENSE).
