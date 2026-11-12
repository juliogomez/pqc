# Post-quantum TLS, end to end

Is your router's HTTPS server post-quantum? On IOS XE 26.2 it already is, with no
configuration at all. [`tls_demo.ipynb`](tls_demo.ipynb) shows you that, then makes you
prove it, in three acts:

- **Act 1:** OpenSSL in containers. A hybrid X25519MLKEM768 key exchange, then ML-DSA
  certificates, measured on the wire.
- **Act 2:** `openssl s_client` from your laptop to R1's HTTPS server. Read the
  negotiated group, then steer it with `ip http secure-pqc-type` and watch a
  HelloRetryRequest happen.
- **Act 3:** the same knob on all three routers, with Ansible over NETCONF.

First time? Build your lab once with [`demos/README.md`](../README.md): prerequisites and
credentials. TLS is the lightest demo there: it only needs each router's management
address, reachable on port 443 from your laptop. No underlay, no tunnels.

## What this demo adds

| What | Where | How |
|---|---|---|
| OpenSSL 3.5 or newer | your laptop | `openssl version`. Older versions have never heard of `X25519MLKEM768` |
| HTTPS server on | every router | `ip http secure-server`, with the router's own self-signed certificate |
| A pinned, classical HTTPS trustpoint | any router with the IPsec demo's `TP-MLDSA65` | the notebook's `setup()` cell, or `ip http secure-trustpoint <self-signed trustpoint>` |

Why pin the trustpoint? Changing `ip http secure-pqc-type` reloads the HTTPS server, and
an unpinned server picks its certificate again. On a router that carries an ML-DSA
trustpoint it can pick that one, which 26.2's HTTPS server can't serve: every handshake
then fails with `alert internal error`. We hit exactly that on R1. Pinning the
self-signed trustpoint prevents it.

## Open it

From the repo root:

```bash
source demos/.venv/bin/activate
jupyter lab demos/tls_demo/tls_demo.ipynb
```

Start with its "Before each run" section. No Docker? Jump to the "Starting at Act 2?"
cell; it loads its own helpers and puts the routers in Act 2's starting state.

## Change it

Don't edit the `.ipynb` by hand. It's generated:

```bash
python3 build_notebook.py
```

The helpers it calls live in `tls_lab.py` (the TLS labs, `check()`, `setup()`,
`probe_all()`) and [`../common/`](../common/) (routers, playbooks, `tls_probe()`, the
notebook builder).
