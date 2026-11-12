# The demo notebook

Want to rerun the container demos from the talk without typing a single command? This is
it. [`PQC_DEMOS.ipynb`](PQC_DEMOS.ipynb) drives the same Docker labs that live under
[`learn/`](../../learn/README.md), one cell per action, and draws a small panel at the end of
each step: who talks to whom, and the value you should see next to the value that was
actually measured on the wire.

Nothing here is a simulation. Every cell runs a real command inside a real container
(`swanctl`, `tcpdump`, `openssl`, `hostapd`, `sshd`), and the numbers come from real
packet captures.

## What's inside

| Section | What it shows | Same commands as |
|---------|---------------|------------------|
| IPsec / IKEv2 key exchange | Hybrid X25519 + ML-KEM-768, the extra `IKE_INTERMEDIATE` round trip, IKE fragmentation | [ipsec/key-exchange](../../learn/ipsec/key-exchange/README.md) |
| IPsec / IKEv2 authentication | ECDSA certificates, then ML-DSA-44 certificates fragmenting `IKE_AUTH` | [ipsec/authentication](../../learn/ipsec/authentication/README.md) |
| TLS 1.3 key exchange | `X25519MLKEM768` key shares, still one round trip | [tls/key-exchange](../../learn/tls/key-exchange/README.md) |
| TLS 1.3 authentication | Mutual TLS with ECDSA, then ML-DSA-65 certificates | [tls/authentication](../../learn/tls/authentication/README.md) |
| MACsec / EAP-TLS | The certificate turning into EAPOL frames, and the silent downgrade | [macsec](../../learn/macsec/README.md) |
| SSH | PQ key exchange by default, the loud downgrade, composite ML-DSA keys | [ssh](../../learn/ssh/README.md) |

The talk only uses the two IPsec sections. The rest is there because once the stacks are
built, it costs you nothing to keep going.

## Prerequisites

- **Docker**, running, with Docker Compose v2 (`docker compose`, not `docker-compose`).
- **Python 3.10 or newer.**
- **Disk and patience for the first build.** The images compile strongSwan, OpenSSH and
  hostapd from source, so the first `preflight_build()` takes a while. After that it's
  cached.

## Run it

From the repo root:

```bash
python3 -m venv demos/.venv
source demos/.venv/bin/activate
pip install -r demos/requirements.txt
jupyter lab demos/containers_demo/PQC_DEMOS.ipynb
```

Using VS Code or Cursor instead? Open `PQC_DEMOS.ipynb`, pick the `demos/.venv`
interpreter as the kernel (`pip install ipykernel` is all it needs), and you're set.

Then, in the notebook:

1. Run the **Setup** cells. `preflight_build()` is one-time per machine;
   `preflight_up()` brings every lab up so each demo only costs you the handshake.
2. Jump to any section from the table of contents and walk it top to bottom, one cell at a
   time. Read the comment, run the cell, read the output.
3. When you're done, run `cleanup_all()`. It takes the containers down and deletes every
   certificate and key the labs generated, so `git status` is clean again.

Something stuck? `panic_reset()` kills leftover daemons and captures without tearing the
stacks down.

## How it works

The notebook stays thin on purpose. All the logic lives in [`pqc_demo.py`](pqc_demo.py),
which gives you one handle per entity: `initiator("...")` runs a command inside the IKEv2
initiator container, `tls_server("...")` inside the TLS server, `host("...")` on your
machine. A trailing `&` backgrounds a command so it survives into the next cell.

A few guardrails worth knowing about:

- It only ever execs into an allow-listed set of lab containers.
- It never uses `shell=True` on your host.
- The keys and certificates it generates are throwaway lab material, and private keys are
  never printed.
- **Heads up:** if bringing a lab up fails with a Docker subnet overlap, it runs
  `docker network prune -f` once and retries. That removes *every* unused Docker network on
  your machine, not just the lab's.

Import it from plain `python3` and it still works: the panels degrade to text, which is
handy if you'd rather script the demos than click through them.

## Want the real thing?

The notebook is Stage 1. When you're ready to run the same IPsec handshakes on Cisco
routers, head to [IPsec on IOS XE](../../deploy/c8000/ipsec.md) and its
[Ansible automation](../../deploy/c8000/automation/README.md).
