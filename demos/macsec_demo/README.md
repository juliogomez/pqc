# Post-quantum MACsec, end to end

MACsec's GCM-AES-256 is already quantum-safe. The key that feeds it might not be.
[`macsec_demo.ipynb`](macsec_demo.ipynb) shows where ML-KEM fits in three acts:

- **Act 1:** EAP-TLS in containers (`hostapd`, `wpa_supplicant`, OpenSSL 3.5).
  `X25519MLKEM768` with an ordinary ECDSA certificate, the same identity as ML-DSA-65
  (14 EAPOL frames become 54), and the silent fallback to TLS 1.2 when the 1.3 gate is off.
- **Act 2:** a pre-shared key you type on R1 and R2, then proof from the counters that
  every ping is encrypted out and validated in.
- **Act 3:** Ansible swaps your key for EAP-TLS (a local CA on R2, certificates over
  SCEP), puts hybrid ML-KEM into that handshake, and proves `x448_mlkem768` from the
  router's own trace.

First time? Build your lab once with [`demos/README.md`](../README.md): prerequisites,
credentials, underlay. MACsec needs a direct cable between R1 and R2
([the MACsec link](#the-macsec-link) below); R3 isn't involved.

## What this demo adds

| What | Where | How |
|---|---|---|
| A direct R1 to R2 wire, pinging | R1 and R2 | [the MACsec link](#the-macsec-link), plus `macsec_interface` and `macsec_command` in your `lab.yml` |
| `switchport mode access` on a switchport end | R1 or R2 | without it, IOS XE offers no dot1x on the port |
| Clocks that agree | R1 and R2 | NTP, or the notebook's `clocks(fix=True)` (`clock set` from the other router) |
| No `access-session pqc-type` / `tls-version` lines | R1 and R2 | the notebook's `setup()` cell. Set on one end only, EAP-TLS fails with `Cred Fail` |

**Two cells change state on purpose.** `setup()` configures the starting state.
`mlkem_proof()` raises the `smd` EAP trace level on R1, forces one re-authentication
(the link drops for about 40 s) and puts the trace level back. Everything else on the
routers only reads, or is a playbook.

**Your CAK stays out of the notebook.** Act 2 has you run `openssl rand -hex 32` in your
own terminal and paste the key into the routers. `psk_config()` prints the config with a
placeholder, so no key ever lands in a saved output cell.

**Act 3 leaves some things behind on purpose.** Teardown removes the MACsec, dot1x and
subscriber policy config but keeps `aaa new-model`, the AAA method lines, the CA on R2,
both trustpoints and the `username R1` / `username R2` entries. Taking those away is a
lockout risk for a cleanup step.

<a id="the-macsec-link"></a>

## The MACsec link

MACsec is hop by hop, so it needs the physical R1 to R2 wire, not a routed path. It can
be the wire your underlay already runs on: the demo protects it peer to peer, and the
underlay keeps running over it, encrypted.

Which keyword turns MACsec on depends on the port, and IOS doesn't warn you:

| Port | Keyword | Why |
|---|---|---|
| Switchport (`switchport access vlan 12`) | `macsec network-link` | plain `macsec` here is access mode, which reports Secured and encrypts nothing |
| Routed port (`no switchport`) | `macsec` | `macsec network-link` is `% Unrecognized`; plain `macsec` is the peer-to-peer mode |

Tell the notebook and the playbooks with `macsec_command` in your `lab.yml`, e.g.
`{r1: macsec}` for a routed R1. Switchports get `macsec network-link` without it.

Two more rules, both measured on a C8355-G2 `FiveGigabitEthernet0/0/0` (routed) to a
C8235-G2 `TwoGigabitEthernet0/0/0` (switchport). That pair secures with a pre-shared key
and with EAP-TLS, hybrid ML-KEM included:

- **A switchport end needs `switchport mode access`**, not just the access VLAN. At its
  default (`dynamic auto`) IOS XE offers no `dot1x` or `authentication` commands, so
  EAP-TLS fails. The VLAN stays the same.
- **A routed end gets no `access-session closed`** for EAP-TLS. The playbook handles it.

**Heads up:** turning MACsec on or off bounces that link for a few seconds. Anything
riding it (the IPsec tunnel, for one) drops briefly and comes back.

## Open it

From the repo root:

```bash
source demos/.venv/bin/activate
jupyter lab demos/macsec_demo/macsec_demo.ipynb
```

Start with its "Before each run" section. No Docker? Jump to the "Starting at Act 2?"
cell; it loads its own helpers and puts the link in Act 2's starting state.

## Change it

Don't edit the `.ipynb` by hand. It's generated:

```bash
python3 build_notebook.py
```

The helpers it calls live in `macsec_lab.py` (the container lab, `check()`, `setup()`,
`clocks()`, `psk_config()`, `proof()`, `mlkem_proof()`) and [`../common/`](../common/)
(routers, playbooks, the notebook builder).
