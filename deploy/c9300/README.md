# Post-Quantum Cryptography on Cisco 9300 Series Smart Switches

The [Cisco 8000 Series Secure Router labs](../c8000/README.md) cover the WAN side:
IPsec tunnels, SSH management, and the MACsec links *between routers*. This guide covers
the other half of the picture: **the access layer**, where a Cisco 9300 Series Smart
Switch connects endpoints to the network and hands traffic off to a WAN router.

The big PQC story here is **MACsec with ML-KEM key exchange**. The C9300 uses EAP-TLS 1.3
with ML-KEM to establish quantum-resistant session keys for MACsec encryption. That
secures the "first hop" before traffic even enters an IPsec tunnel.

The target platform is the **Cisco 9300 Series Smart Switches** running **IOS XE 26.2**
or later. Everything below was tested on 26.2. These docs write "C9300" as shorthand for
the platform in tables, paths, and command output.

## MACsec scenarios

Three deployment shapes matter for PQC on a C9300. The [MACsec guide](macsec.md) walks
through all three:

| Scenario | Where it runs | 802.1X shape |
|----------|---------------|--------------|
| **Host-to-switch** (LAN) | Downlink to a laptop, phone, or AP | Authenticator on the switch, supplicant on the host |
| **Switch-to-switch** (fabric) | any C9300 pair | symmetric config on both sides |
| **Switch-to-router** (uplink) | WAN uplink | Same network-link shape as switch-to-switch |

## The docs

| Doc | What you do |
|-----|-------------|
| [**MACsec**](macsec.md) | EAP-TLS MACsec with ML-KEM: host-to-switch, switch-to-switch, and switch-to-router, from classical baseline to quantum-safe |
| [**SSH**](ssh.md) | Enable ML-KEM hybrid key exchange on the switch's SSH server |
| [**TLS**](tls.md) | Management HTTPS with ML-KEM hybrid key exchange |
| [**IPsec**](ipsec.md) | IKEv2 IPsec with ML-KEM: classical baseline, then flip to quantum-safe key exchange |
| [**Secure Boot**](secure-boot.md) | Map the boot chain algorithms, verify integrity measurements, inspect the SUDI identity |

Ansible playbooks for all four protocols live under
[`automation/`](automation/README.md) ([operator guide](automation/README.md),
[design notes](automation/DESIGN.md)).


## Support summary

| Protocol | Category | PQ feature | Status |
|----------|----------|-----------|--------|
| MACsec | Key exchange | ML-KEM via EAP-TLS 1.3 | Supported |
| MACsec | Key exchange | PSK-based MKA | Supported |
| SSH | Key exchange | ML-KEM hybrid KEX | Supported |
| TLS | Key exchange | ML-KEM hybrid for mgmt HTTPS | Supported |
| TLS | Authentication | ML-DSA certificate auth | Roadmap |
| IPsec | Key exchange | ML-KEM in IKEv2 proposal | Supported |
| IPsec | Authentication | ML-DSA certificates | Supported |


## Example configs

Reference snapshots (sanitized, no secrets) live in [`device-configs/`](device-configs/):

| File | What it shows |
|------|---------------|
| [`9300-1-pqc-macsec.txt`](device-configs/9300-1-pqc-macsec.txt) | C9300 switch, switch-to-router PQC MACsec |
| [`c8355-g2-pqc-macsec.txt`](device-configs/c8355-g2-pqc-macsec.txt) | C8355 router, switch-to-router PQC MACsec peer |
| [`c8455-g2-pqc-macsec.txt`](device-configs/c8455-g2-pqc-macsec.txt) | C8455 router, PQC MACsec uplink + IKEv2 PQC |
| [`c9300-48hx-1-pqc-ipsec.txt`](device-configs/c9300-48hx-1-pqc-ipsec.txt) | C9300, IKEv2 PQC with ML-KEM |
| [`c9300-macsec-pqc-reference.txt`](device-configs/c9300-macsec-pqc-reference.txt) | C9300 switch-to-router PQC MACsec |
| [`c8000-macsec-pqc-reference.txt`](device-configs/c8000-macsec-pqc-reference.txt) | C8000 router side of PQC MACsec |
| [`c9300-ipsec-pqc-reference.txt`](device-configs/c9300-ipsec-pqc-reference.txt) | Generic IPsec PQC reference config |
