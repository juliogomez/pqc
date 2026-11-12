# Post-quantum SSH, end to end

Is the SSH session you manage your routers over post-quantum? [`ssh_demo.ipynb`](ssh_demo.ipynb)
answers that in three acts:

- **Act 1:** OpenSSH 10.4 in containers. Hybrid `mlkem768x25519-sha256` with zero config,
  the loud warning a classical-only server gets, and composite ML-DSA keys.
- **Act 2:** one line on R1 (`ip ssh server algorithm kex ...`), checked from your own
  laptop. Then the surprise: your client's policy decides what you actually get.
- **Act 3:** the same KEX list, plus a host key pin, on all three routers with Ansible.

First time? Build your lab once with [`demos/README.md`](../README.md): prerequisites and
credentials. SSH only needs each router's management address, reachable on port 22 from
your laptop. No underlay, no tunnels.

## What this demo adds

| What | Where | How |
|---|---|---|
| OpenSSH 9.9 or newer | your laptop | `ssh -Q kex \| grep mlkem` should print `mlkem768x25519-sha256` |
| SSH v2 with an RSA host key | every router | `ip ssh version 2`, `crypto key generate rsa modulus 2048` |
| The platform default KEX list | every router | the notebook's `setup()` cell, or `no ip ssh server algorithm kex` |

The laptop probes never log in. They run `ssh -vv -o BatchMode=yes -o
PreferredAuthentications=none`, which finishes the key exchange, asks for no
authentication method at all, gets refused and exits. No password or key is ever sent.

**On a Mac?** macOS ships `/etc/ssh/crypto.conf` with `KexAlgorithms ^ecdh-sha2-nistp256`,
which puts P-256 first on every connection. Since the client's order decides, a Mac picks
`ecdh-sha2-nistp256` even when the router offers ML-KEM first. Act 2 shows that, then two
ways around it.

## Open it

From the repo root:

```bash
source demos/.venv/bin/activate
jupyter lab demos/ssh_demo/ssh_demo.ipynb
```

Start with its "Before each run" section. No Docker? Jump to the "Starting at Act 2?"
cell; it loads its own helpers and puts the routers in Act 2's starting state.

## Change it

Don't edit the `.ipynb` by hand. It's generated:

```bash
python3 build_notebook.py
```

The helpers it calls live in `ssh_lab.py` (the SSH lab, `check()`, `setup()`,
`probe_all()`) and [`../common/`](../common/) (routers, playbooks, `ssh_kex_probe()`, the
notebook builder).
