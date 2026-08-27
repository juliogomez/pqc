# Post-Quantum Cryptography on Cisco IOS XE

If you've done the [container labs](../../learn/README.md), you already know the concepts:
hybrid key exchange, PPK, ML-KEM, IKE fragmentation, large KEM ciphertexts. This is where
you run all of it on real Cisco hardware and find out which parts the platform can actually
do today. The concepts are the same across both environments. The RFCs don't change just because
you're on a different platform. What changes is the CLI and how the implementation handles
things like fragmentation and licensing.

The target platform is the **Cisco 8000 Series Secure Router** (C8235-G2 in our case) running **IOS XE
26.1**. Three of them, wired back-to-back, with the "advantage" license that unlocks
all crypto features without needing a separate HSECK9 key.

## Lab topology

```
        ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
        │      R1      │          │      R2      │          │      R3      │
        │   (Spoke-1)  │          │ (Hub/Transit)│          │   (Spoke-2)  │
        │              │          │              │          │              │
        │  Vlan12      │          │  Vlan12      │          │              │
        │  10.0.12.1   │          │  10.0.12.2   │          │              │
        │              │  VLAN 12 │              │          │              │
        │      Tw0/0/0 ├──────────┤ Tw0/0/0      │          │              │
        │              │  2.5 Gb  │              │          │              │
        │              │          │  Vlan23      │          │  Vlan23      │
        │              │          │  10.0.23.1   │          │  10.0.23.2   │
        │              │          │              │  VLAN 23 │              │
        │              │          │      Tw0/0/1 ├──────────┤ Tw0/0/0      │
        │              │          │              │  2.5 Gb  │              │
        └──────────────┘          └──────────────┘          └──────────────┘

```

Every doc below shares this topology. The IPsec doc builds the underlay configs from scratch.

## The docs

This is the recommended reading order:

| # | Doc | What you do |
|---|-----|-------------|
| 1 | [**IPsec / IKEv2**](ipsec.md) | Classical baseline, then RFC 8784 PPK, then native ML-KEM-768 hybrid, then a phased hub-and-spoke migration |
| 2 | [**SSH**](ssh.md) | Enable a PQ hybrid KEX on the SSH server and prove it from your laptop |
| 3 | [**MACsec**](macsec.md) | PSK-based MKA end to end, then EAP-TLS with ML-KEM on a local CA (no RADIUS needed) |
| 4 | [**TLS**](tls.md) | Probe the management HTTPS server with an ML-KEM group |

## Support summary

| Protocol | Category | PQ Feature | Status on IOS XE 26.1 |
|----------|----------|-----------|----------------------|
| IPsec | Key Exchange | ML-KEM-768 hybrid IKEv2 | Working |
| IPsec | Key Exchange | RFC 8784 PPK | Working |
| IPsec | Authentication | ML-DSA authentication | Roadmap (26.2) |
| SSH | Key Exchange | mlkem768x25519-sha256 KEX | Working |
| SSH | Authentication | ML-DSA host/user keys | Not available (draft RFC) |
| MACsec | Key Exchange | PSK-based MKA + GCM-AES-256 | Working |
| MACsec | Key Exchange | ML-KEM EAP-TLS MKA | Working |
| MACsec | Authentication | ML-DSA certificates | Not available |
| TLS | Key Exchange | ML-KEM hybrid key exchange | Not available |
| TLS | Authentication | ML-DSA certificate auth | Not available |
