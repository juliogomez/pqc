#!/usr/bin/env python3
"""Generate ssh_demo.ipynb, a runnable post-quantum SSH walkthrough.

    python3 build_notebook.py

A declarative cell list written as raw nbformat v4 JSON (see ../common/nbbuild.py).
The runtime lives in ssh_lab.py (SSH specifics), ../common/lab.py (routers,
playbooks, laptop probes) and ../containers_demo/pqc_demo.py (containers, panels).

Act 1 is copied verbatim from ../containers_demo/PQC_DEMOS.ipynb (the "ssh"
section). Regenerate this file whenever that notebook changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
from nbbuild import (  # noqa: E402
    SOURCE_NB, act1_section, automation_cells, code, containers_cells, find_helpers,
    laptop_cleanup_cell, md, recap_note, step, write,
)

OUT = HERE / "ssh_demo.ipynb"
SSH_CELLS = act1_section("ssh")
FIND_HELPERS = find_helpers("ssh_demo")

CELLS: list[dict] = [
    md(
        "# Post-quantum SSH, end to end",
        "## Containers, router CLI, Ansible",
        "",
        "Is the SSH session you manage your routers over post-quantum? Probably not yet.",
        "This notebook shows what it takes: OpenSSH in containers first, where hybrid",
        "ML-KEM is the default, then one line on a real IOS XE 26.2 router, checked from",
        "your own laptop, then the same line on every router with Ansible.",
        "",
        "**First time?** Build your lab with [`demos/README.md`](../README.md)",
        "(prerequisites, credentials). SSH only needs each router's management address:",
        "no underlay, no tunnels. This notebook checks the rest in",
        "[Before each run](#preflight).",
        "",
        "Four kinds of cell:",
        "",
        "- **Act 1 cells** drive OpenSSH in containers and draw the handshake as it happens.",
        "- **Laptop probes** (`ssh_kex_probe(...)`) open an SSH connection from *your* laptop,",
        "  stop right after the key exchange (no password is ever sent) and report what was",
        "  agreed.",
        "- **Router checks** (`r1(...)`, `check()`) run read-only `show` commands through",
        "  Ansible. In Act 2 you type every change yourself, and the checks confirm you",
        "  got the result. The one exception is `setup()`, which configures and says so.",
        "- **Playbook cells** (`playbook(...)`) run Act 3 from `deploy/c8000/automation`",
        "  with its own venv and keep the `PLAY RECAP` visible.",
    ),
    md(
        "## Contents",
        "",
        "- [Setup](#setup)",
        "- [Before each run](#preflight): containers, routers, automation",
        "- [Act 1: containers](#act1): Demo 1",
        "- [Starting at Act 2?](#act2-setup): set up the routers without the containers",
        "- [Act 2: the router's SSH server, from your laptop](#act2): Demos 2 to 4",
        "- [Act 3: Ansible](#act3): Demo 5",
        "- [Clean up](#after)",
    ),

    # ------------------------------------------------------------------ setup
    md(
        '<a id="setup"></a>',
        "## Setup",
        "",
        "Run this once per kernel. It finds `pqc_demo.py` in `demos/containers_demo/`, the",
        "shared helpers in `demos/common/` and `ssh_lab.py` next to this notebook,",
        "whichever folder Jupyter started in.",
    ),
    code(
        "import sys, pathlib, re",
        *FIND_HELPERS,
        "from pqc_demo import *  # noqa: F401,F403  containers, panels",
        "from ssh_lab import *   # noqa: F401,F403  routers, playbooks, probes, labs",
        "",
        "# Ladder for the Act 1 panels (same object the learner notebook uses).",
        "ssh = SSHDemo()",
        "",
        "set_presenter_mode(True)",
        "print('Repo:       ', REPO_ROOT)",
        "print('Automation: ', AUTOMATION_DIR)",
        "print('Containers:  ssh_server ssh_client | host')",
        "print('Routers:     r1 r2 r3 (read-only)   Laptop: ssh_kex_probe()   Playbooks: playbook()')",
    ),

    # -------------------------------------------------------------- preflight
    md(
        "---",
        '<a id="preflight"></a>',
        "## Before each run",
        "",
        "Most failures are leftovers from the last run, so check everything before you",
        "start. If a check fails, the fix is in the table below or in",
        "the [one-time setup](../README.md).",
    ),
    *containers_cells("It compiles OpenSSH 10.4 from source, so give it a few minutes."),
    md(
        "The `ssh -Q kex` row is your laptop's OpenSSH. Acts 2 and 3 connect from here,",
        "and a client older than OpenSSH 9.9 doesn't know `mlkem768x25519-sha256`.",
    ),
    md(
        "### Routers: is the lab in Act 2's starting state?",
        "",
        "The target: **every router runs SSH v2 with its platform default KEX list, which",
        "has no ML-KEM in it, and no EC host key.** Why care about the host key? Because",
        "of a lockout trap that `ssh.md` describes: import an EC keypair and the server",
        "starts advertising an ECDSA host key it then can't use.",
        "",
        "This cell only reads (about 15 seconds). Starting from scratch?",
        "[`setup()`](#act2-setup) fixes the KEX rows.",
    ),
    code(
        "# R1, R2, R3: read-only checks of the Act 2 starting state",
        "check()",
    ),
    md(
        "**If a row fails**, here's where the fix lives:",
        "",
        "| Row | Fix |",
        "|---|---|",
        "| SSH server | `ip ssh version 2`, plus a key: `crypto key generate rsa modulus 2048` |",
        "| KEX line, KEX list | [`setup()`](#act2-setup), or `no ip ssh server algorithm kex` |",
        "| EC host key | `ip ssh server algorithm hostkey rsa-sha2-512 rsa-sha2-256` pins the host key to RSA (see `deploy/c8000/ssh.md`, \"Watch out\") |",
        "",
        "Want the raw output behind a row? Ask the router directly:",
    ),
    code(
        "# R1: any single read-only command, real output",
        "r1('show ip ssh | include KEX|Hostkey|version');",
    ),
    *automation_cells(),

    # ------------------------------------------------------------------- act 1
    md(
        "---",
        '<a id="act1"></a>',
        "## Act 1: learn it in containers",
        "",
        "An OpenSSH 10.4 server and client on one Docker network, with `tcpdump` on the",
        "server. One cell per action: read the comment, run it, read the output.",
    ),
    step("Demo 1: hybrid key exchange by default, and what happens without it",
        "What does modern OpenSSH do with zero config? Look for:",
        "",
        "- `kex: algorithm: mlkem768x25519-sha256` with no KEX setting anywhere",
        "- the KEX init and reply: **1304 B** and **1716 B** on the wire",
        "- a classical-only server: the client connects anyway, with a loud `** WARNING`",
        "  that the connection isn't post-quantum",
        "- (optional) composite `ssh-mldsa44-ed25519` keys, so authentication is",
        "  post-quantum too: a 1383 B host key and a 2523 B signature",
        "",
        "Keep the first line in mind. Act 2 asks whether a router does the same."),
    *SSH_CELLS,
    md(
        "Something stuck? This stops stray `sshd` and capture processes without taking",
        "the stack down:",
    ),
    code(
        "# host: stop stray sshd / tcpdump processes, keep the stack up",
        "labs.panic()",
    ),

    # ------------------------------------------------- act 2 without the containers
    md(
        "---",
        '<a id="act2-setup"></a>',
        "## Starting at Act 2? Set up the routers",
        "",
        "Skipping the containers? Start here. This cell loads its own helpers, so you",
        "don't need the Setup cell at the top. Then, on each router:",
        "",
        "1. removes any `ip ssh server algorithm kex` line, back to the platform default",
        "2. leaves `ip ssh server algorithm hostkey` alone: if you pinned it, it's protecting you",
        "3. NETCONF on all three, for Act 3",
        "4. the same read-only check as the pre-flight",
        "",
        "**This cell configures the routers**, unlike every other router cell here. On a",
        "lab that's already in shape it only reads.",
    ),
    code(
        "# R1, R2, R3: CONFIGURES the Act 2 starting state, then checks it",
        "# (loads the helpers first, so this works as the first cell you run)",
        "import sys, pathlib",
        *FIND_HELPERS,
        "from ssh_lab import *  # noqa: F401,F403  routers, playbooks, probes",
        "",
        "setup()",
    ),

    # ------------------------------------------------------------------- act 2
    md(
        "---",
        '<a id="act2"></a>',
        "## Act 2: the router's SSH server, from your laptop",
        "",
        "**You type every router command in this act at the R1 CLI.** The probes connect",
        "from your laptop to R1's management address and stop after the key exchange, so",
        "what you see is what your own `ssh admin@R1` gets.",
        "",
        "**Keep your current R1 session open** until a new one works. This act changes the",
        "crypto on the very connection you manage the box over.",
    ),
    step("Demo 2: what does the router offer today?",
        "**R1:**",
        "",
        "```",
        "show ip ssh | include KEX",
        "```",
        "",
        "That's the platform default: `curve25519-sha256` first, then ECDH and DH groups.",
        "No ML-KEM. Now ask from the laptop:"),
    code(
        "# laptop -> R1:22: what do you and the router agree on today?",
        "ssh_kex_probe('r1', expect=None, expect_offer=False);",
    ),
    md(
        "Classical, whatever your client prefers, because the router doesn't offer anything",
        "else. Read the `client offers` line too: your client already speaks",
        "`mlkem768x25519-sha256`. The router is the one holding it back.",
    ),
    step("Demo 3: one line on the router",
        "Configure the SSH server to offer ML-KEM first. The order is the server's",
        "preference; the classical entries are the fallback for clients that have never",
        "heard of ML-KEM. **R1:**",
        "",
        "```",
        "conf t",
        " ip ssh server algorithm kex mlkem768x25519-sha256 curve25519-sha256 ecdh-sha2-nistp256",
        "end",
        "show ip ssh | include KEX",
        "```",
        "",
        "Expect `KEX Algorithms:mlkem768x25519-sha256,curve25519-sha256,ecdh-sha2-nistp256`.",
        "26.2 has three hybrids (`mlkem768x25519-sha256`, `mlkem768nistp256-sha256`,",
        "`mlkem1024nistp384-sha384`) and no pure ML-KEM: a break in either half still",
        "leaves you the other."),
    code(
        "# R1: ML-KEM is offered first (read-only)",
        "router_check('Demo 3: ML-KEM offered', 'R1', [",
        "    Check('r1', 'show ip ssh', 'KEX offer order', 'mlkem768x25519-sha256 first', r'KEX Algorithms:mlkem768x25519-sha256,'),",
        "    Check('r1', 'show running-config | include ^ip ssh server algorithm', 'config line', 'ip ssh server algorithm kex mlkem768x25519-sha256 ...', r'algorithm kex mlkem768x25519-sha256'),",
        "])",
    ),
    md("Now connect again, exactly as you did in Demo 2:"),
    code(
        "# laptop -> R1:22: same client, the router now offers ML-KEM first",
        "ssh_kex_probe('r1', expect=None, expect_offer=True);",
    ),
    md(
        "Did you get `mlkem768x25519-sha256`? **On a Mac, probably not.** In SSH the",
        "*client's* order decides: the first algorithm on the client's list that the",
        "server also supports wins. macOS ships `/etc/ssh/crypto.conf` with",
        "`KexAlgorithms ^ecdh-sha2-nistp256`, which moves P-256 to the front of every",
        "connection's list. Look at `client's first choice` in the panel: the router",
        "offers ML-KEM first, and your client still picks P-256.",
        "",
        "On a Linux client with OpenSSH 9.9 or newer, and no system crypto policy",
        "reordering the list, the same probe gives ML-KEM straight away. Two ways to see",
        "it from here: OpenSSH's own defaults (`-F none` skips every config file), or",
        "pinning the algorithm the way `ssh.md` does.",
    ),
    code(
        "# laptop -> R1:22: OpenSSH's built-in defaults, no config files",
        "ssh_kex_probe('r1', clean_config=True);",
    ),
    code(
        "# laptop -> R1:22: ask for ML-KEM explicitly (ssh -o KexAlgorithms=...)",
        "ssh_kex_probe('r1', kex='mlkem768x25519-sha256');",
    ),
    md(
        "Want it in your everyday `ssh`? Add `KexAlgorithms mlkem768x25519-sha256,` plus",
        "your fallbacks to the router's `Host` entry in `~/.ssh/config`. That's the",
        "real-world lesson here: **upgrading the server is half the job; the client's",
        "policy decides what you actually get.**",
    ),
    step("Demo 4: the host key is still classical",
        "Every probe above said `host key algorithm: rsa-sha2-512`. The key exchange is",
        "post-quantum now; the router's proof of identity isn't. Can it be? **R1:**",
        "",
        "```",
        "conf t",
        " ip ssh server algorithm hostkey ?",
        "end",
        "```",
        "",
        "RSA and ECDSA, nothing else: no ML-DSA, no composite. Same for user keys",
        "(`ip ssh server algorithm publickey ?`). That's fine for now. Key exchange",
        "protects against *harvest now, decrypt later*, which starts the day someone",
        "records your session. Forging a host key needs a quantum computer in real time.",
        "",
        "**Put R1 back** before Act 3, so the playbook starts from the default too:",
        "",
        "```",
        "conf t",
        " no ip ssh server algorithm kex",
        "end",
        "```"),
    code(
        "# R1, R2, R3: back in the starting state (read-only)",
        "check()",
    ),

    # ------------------------------------------------------------------- act 3
    md(
        "---",
        '<a id="act3"></a>',
        "## Act 3: now do it to the fabric",
        "",
        "One router by hand is fine. What about fifty? `ssh-pq.yml` pushes the KEX list",
        "and a host key pin to every router as one NETCONF edit. The list comes from",
        "`pq_ssh_kex` in `group_vars/all/pqc.yml`: `mlkem768x25519-sha256`,",
        "`mlkem768nistp256-sha256`, `curve25519-sha256`, `ecdh-sha2-nistp256`. The pin is",
        "`rsa-sha2-512 rsa-sha2-256`, the fix for the EC host key trap.",
        "",
        "It's the one playbook that changes the crypto on the connection it manages the",
        "box over, so it refuses a list with no classical fallback unless you pass",
        "`-e ssh_pq_allow_no_fallback=true`.",
    ),
    step("Demo 5: Ansible offers ML-KEM everywhere",
        "Run it twice. The first run changes every router; the second finds nothing to",
        "do.",
        "",
        "Captured run (`captured/08-ssh-pq/`): `changed=1` on r2 and r3, then `changed=0`.",
        "(R1 already had the line from the hand exercise when that was captured; you put",
        "it back, so expect `changed=1` on all three.) Each run takes about 37 s.",
        "",
        recap_note()),
    code(
        "# Ansible: KEX list + host key pin on r1, r2, r3",
        "playbook('ansible-playbook ssh-pq.yml', expect_changed=1);",
    ),
    code(
        "# Ansible: same playbook again; nothing to do is the point",
        "playbook('ansible-playbook ssh-pq.yml', expect_changed=0);",
    ),
    code(
        "# laptop -> R1, R2, R3: each router offers ML-KEM; your config, then pinned",
        "probe_all();",
    ),
    md(
        "Same lesson as Demo 3, fleet-wide: every router offers ML-KEM, your client's",
        "policy decides whether you use it, and pinning it proves every box will.",
    ),

    # ------------------------------------------------------------------- after
    md(
        "---",
        '<a id="after"></a>',
        "## Clean up",
        "",
        "Teardown removes **both** lists, the KEX order and the host key pin, so each router",
        "goes back to its platform default. Pinned the host key yourself before this demo?",
        "Put it back afterwards.",
    ),
    code(
        "# Ansible: remove the KEX list and host key pin from r1, r2, r3",
        "playbook('ansible-playbook ssh-pq.yml -e state=absent', expect_changed=1);",
    ),
    code(
        "# R1, R2, R3: back in the starting state; nobody offers ML-KEM",
        "check() and probe_all(expect_offer=False);",
    ),
    md(
        "On the laptop: container down, generated keys removed, `learn/ssh` back to the",
        "committed state.",
    ),
    laptop_cleanup_cell("learn/ssh"),
    md(
        "**Run it again?** Start from [Before each run](#preflight). Clean up leaves the",
        "routers in the starting state, so `check()` should be green straight away.",
    ),
]


def main() -> None:
    write(OUT, CELLS, f"Act 1 copied from {SOURCE_NB.name}: {len(SSH_CELLS)} cells")


if __name__ == "__main__":
    main()
