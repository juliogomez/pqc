#!/usr/bin/env python3
"""Generate tls_demo.ipynb, a runnable post-quantum TLS walkthrough.

    python3 build_notebook.py

A declarative cell list written as raw nbformat v4 JSON (see ../common/nbbuild.py).
The runtime lives in tls_lab.py (TLS specifics), ../common/lab.py (routers,
playbooks, laptop probes) and ../containers_demo/pqc_demo.py (containers, panels).

Act 1 is copied verbatim from ../containers_demo/PQC_DEMOS.ipynb (the "kex-tls"
and "auth-tls" sections). Regenerate this file whenever that notebook changes.
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

OUT = HERE / "tls_demo.ipynb"
KEX_CELLS = act1_section("kex-tls")
AUTH_CELLS = act1_section("auth-tls")
FIND_HELPERS = find_helpers("tls_demo")

CELLS: list[dict] = [
    md(
        "# Post-quantum TLS, end to end",
        "## Containers, router CLI, Ansible",
        "",
        "Is the router's own HTTPS server post-quantum? On IOS XE 26.2 it already is, out",
        "of the box. This notebook shows you that, then makes you *prove* it: first",
        "between two OpenSSL containers, then from your laptop to a real router, then on",
        "every router at once with Ansible.",
        "",
        "**First time?** Build your lab with [`demos/README.md`](../README.md)",
        "(prerequisites, credentials). TLS only needs each router's management address:",
        "no underlay, no tunnels. This notebook checks the rest in",
        "[Before each run](#preflight).",
        "",
        "Four kinds of cell:",
        "",
        "- **Act 1 cells** drive OpenSSL in containers and draw the handshake as it happens.",
        "- **Laptop probes** (`tls_probe(...)`) run `openssl s_client` from *your* laptop",
        "  against a router's HTTPS server and report what was negotiated. They only",
        "  connect; nothing is configured.",
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
        "- [Act 1: containers](#act1): Demo 1 and Demo 2",
        "- [Starting at Act 2?](#act2-setup): set up the routers without the containers",
        "- [Act 2: the router's HTTPS server, from your laptop](#act2): Demos 3 to 5",
        "- [Act 3: Ansible](#act3): Demo 6",
        "- [Clean up](#after)",
    ),

    # ------------------------------------------------------------------ setup
    md(
        '<a id="setup"></a>',
        "## Setup",
        "",
        "Run this once per kernel. It finds `pqc_demo.py` in `demos/containers_demo/`, the",
        "shared helpers in `demos/common/` and `tls_lab.py` next to this notebook,",
        "whichever folder Jupyter started in.",
    ),
    code(
        "import sys, pathlib, re",
        *FIND_HELPERS,
        "from pqc_demo import *  # noqa: F401,F403  containers, panels",
        "from tls_lab import *   # noqa: F401,F403  routers, playbooks, probes, labs",
        "",
        "# Ladders for the Act 1 panels (same objects the learner notebook uses).",
        "tls = TLSDemo(); tls_auth = TLSAuthDemo()",
        "",
        "set_presenter_mode(True)",
        "print('Repo:       ', REPO_ROOT)",
        "print('Automation: ', AUTOMATION_DIR)",
        "print('Containers:  tls_server tls_client | mtls_server mtls_client | host')",
        "print('Routers:     r1 r2 r3 (read-only)   Laptop: tls_probe()   Playbooks: playbook()')",
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
    *containers_cells("Two small images on Debian's OpenSSL 3.5, so it's quick."),
    md(
        "The last row is your laptop's OpenSSL. Acts 2 and 3 run `openssl s_client` from",
        "here, and anything older than 3.5 has never heard of `X25519MLKEM768`.",
    ),
    md(
        "### Routers: is the lab in Act 2's starting state?",
        "",
        "The target: **every router's HTTPS server is on, at the 26.2 default (`PQC type:",
        "all`), with no `ip http secure-pqc-type` line, serving a classical certificate.**",
        "A router that carries the IPsec demo's ML-DSA trustpoint also needs its HTTPS",
        "trustpoint pinned; Demo 4 explains why.",
        "",
        "This cell only reads (about 15 seconds). Starting from scratch?",
        "[`setup()`](#act2-setup) fixes every row except a server that's off.",
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
        "| HTTPS server | `ip http secure-server`. It needs a certificate; the router's own self-signed one is fine here |",
        "| PQC type, pqc-type line | [`setup()`](#act2-setup), or `no ip http secure-pqc-type` |",
        "| classical certificate, trustpoint pinned | [`setup()`](#act2-setup), or `ip http secure-trustpoint <your self-signed trustpoint>` (`show crypto pki trustpoints` lists them) |",
        "",
        "Want the raw output behind a row? Ask the router directly:",
    ),
    code(
        "# R1: any single read-only command, real output",
        "r1('show ip http server secure status');",
    ),
    *automation_cells(),

    # ------------------------------------------------------------------- act 1
    md(
        "---",
        '<a id="act1"></a>',
        "## Act 1: learn it in containers",
        "",
        "An OpenSSL server and client on one Docker network, with `tcpdump` on the",
        "server. One cell per action: read the comment, run it, read the output.",
    ),
    step("Demo 1: hybrid key exchange, no extra round trip",
        "What does ML-KEM change in a TLS 1.3 handshake? Look for:",
        "",
        "- `Negotiated TLS1.3 group: X25519MLKEM768`",
        "- key shares of **1216 B** in the ClientHello and **1120 B** in the ServerHello",
        "- the classical contrast: **32 B** each way, same single round trip",
        "",
        "So the cost is bytes, not a new flight. Keep those two numbers in mind; Act 2",
        "measures them again on the router."),
    *KEX_CELLS,
    step("Demo 2: post-quantum certificates",
        "The key exchange is post-quantum; the certificate signature isn't. What happens",
        "when it is? Look for:",
        "",
        "- the DER sizes: ML-DSA-65 is about **15x** the ECDSA leaf",
        "- mutual TLS with ECDSA: `ecdsa_secp256r1_sha256`, about **2.5 KB** read",
        "- the same handshake with ML-DSA-65: `mldsa65`, about **16 KB** read",
        "",
        "On TCP that's just a longer handshake, no fragmentation to manage (unlike IKEv2)."),
    *AUTH_CELLS,
    md(
        "Something stuck? This stops stray listeners and captures without taking the",
        "stacks down:",
    ),
    code(
        "# host: stop stray s_server / tcpdump processes, keep the stacks up",
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
        "1. removes any `ip http secure-pqc-type` line, back to the default (`all`)",
        "2. if the router carries the ML-DSA trustpoint `TP-MLDSA65`, pins the HTTPS",
        "   server to its classical self-signed trustpoint (Demo 4 says why)",
        "3. NETCONF on all three, for Act 3",
        "4. the same read-only check as the pre-flight",
        "",
        "**This cell configures the routers**, unlike every other router cell here. It",
        "never turns the HTTPS server on for you: that needs a certificate decision, and",
        "that's yours. On a lab that's already in shape it only reads.",
    ),
    code(
        "# R1, R2, R3: CONFIGURES the Act 2 starting state, then checks it",
        "# (loads the helpers first, so this works as the first cell you run)",
        "import sys, pathlib",
        *FIND_HELPERS,
        "from tls_lab import *  # noqa: F401,F403  routers, playbooks, probes",
        "",
        "setup()",
    ),

    # ------------------------------------------------------------------- act 2
    md(
        "---",
        '<a id="act2"></a>',
        "## Act 2: the router's HTTPS server, from your laptop",
        "",
        "**You type every router command in this act at the R1 CLI.** The probes run",
        "`openssl s_client` from your laptop to R1's management address on port 443, so",
        "what you see is what any TLS 1.3 client gets from that router.",
    ),
    step("Demo 3: it's already post-quantum",
        "**R1:**",
        "",
        "```",
        "show ip http server secure status",
        "```",
        "",
        "Look for `HTTP secure server PQC type: all` (new in 26.2, and the reason this works",
        "with no config) and `HTTP secure server ECDHE curve: secp384r1` (that one comes back",
        "in Demo 4). Now ask from the laptop:"),
    code(
        "# laptop -> R1:443: what does a default OpenSSL client negotiate?",
        "tls_probe('r1');",
    ),
    md(
        "`Negotiated TLS1.3 group: X25519MLKEM768` is the whole result: the same hybrid",
        "group the containers agreed on, now spoken by the router. Then read",
        "`Signature type: rsa_pss_rsae_sha256`. The key exchange is post-quantum, the",
        "certificate is still classical RSA, and on 26.2 the HTTPS server can't serve an",
        "ML-DSA one.",
        "",
        "Same bytes as Act 1? Read them off the handshake:",
    ),
    code(
        "# laptop -> R1:443: key_share sizes per hello (openssl s_client -trace)",
        "tls_probe('r1', trace=True);",
    ),
    md(
        "Measured on IOS XE 26.2: the ClientHello's `key_share` extension is **1258 B**",
        "(OpenSSL offers two shares: 1216 B of X25519MLKEM768 plus 32 B of x25519, plus",
        "headers). The ServerHello's is **1124 B**: the 1120 B share from Act 1, plus its",
        "header. Same numbers, real router.",
    ),
    step("Demo 4: steer the key exchange",
        "`all` isn't the only setting. Force the server classical. **R1:**",
        "",
        "```",
        "conf t",
        " ip http secure-pqc-type non-pqc",
        "end",
        "```",
        "",
        "**Heads up: pin the HTTPS trustpoint first.** Changing this knob reloads the",
        "HTTPS server, and an unpinned server picks its certificate again. On our R1, which",
        "carries the IPsec demo's ML-DSA trustpoint, it picked `TP-MLDSA65`, which 26.2",
        "can't serve: every handshake then died with `alert internal error`. That's why",
        "`check()` wants `ip http secure-trustpoint` on any router with `TP-MLDSA65`, and",
        "why `setup()` sets it.",
        "",
        "Now connect again with the same client offering the same groups:"),
    code(
        "# laptop -> R1:443: same client, the server now refuses both offered shares",
        "tls_probe('r1', trace=True, expect='secp384r1');",
    ),
    md(
        "Read that exchange carefully, because it's a textbook **HelloRetryRequest**. The",
        "client offers X25519MLKEM768 and x25519. The server wants neither, and answers",
        "with a bare 2-byte `key_share` naming `secp384r1` (the `ECDHE curve` from Demo 3).",
        "The client retries with a 103 B P-384 share, and the handshake finishes",
        "classically. That retry is a whole extra round trip, the price of a server that",
        "won't take what the client offered first.",
    ),
    code(
        "# R1: the knob is set (read-only)",
        "router_check('Demo 4: classical only', 'R1', [",
        "    Check('r1', 'show ip http server secure status', 'PQC type', 'non-pqc', r'PQC type: non-pqc'),",
        "    Check('r1', 'show running-config | include ^ip http secure-', 'config line', 'ip http secure-pqc-type non-pqc', r'secure-pqc-type non-pqc'),",
        "    Check('r1', 'show ip http server secure status', 'still a classical certificate', 'Self or TP-self-signed-...', r'server trustpoint: (?!TP-MLDSA65\\b)\\S+'),",
        "])",
    ),
    step("Demo 5: fail closed",
        "`non-pqc` falls back. What if you want the opposite: refuse anyone who isn't",
        "post-quantum? **R1:**",
        "",
        "```",
        "conf t",
        " ip http secure-pqc-type pqc",
        "end",
        "```",
        "",
        "Then try the same default client:"),
    code(
        "# laptop -> R1:443: the default client offers hybrid and x25519",
        "tls_probe('r1', expect='refused');",
    ),
    md(
        "**It fails** (`alert handshake failure`), even though the client offered a hybrid",
        "post-quantum group. `pqc` means *pure* ML-KEM, and OpenSSL's default list doesn't",
        "offer that. Ask for it explicitly:",
    ),
    code(
        "# laptop -> R1:443: offer pure ML-KEM-768 only",
        "tls_probe('r1', groups='MLKEM768', expect='MLKEM768');",
    ),
    md(
        "Here's all four settings against four client offers, measured from an OpenSSL 3.6",
        "client against R1 (`tls_probe('r1', groups=...)` reproduces any cell):",
        "",
        "| `ip http secure-pqc-type` | default client | `X25519` only | `secp384r1` only | `MLKEM768` only |",
        "|---|---|---|---|---|",
        "| `all` (the default) | X25519MLKEM768 | refused | secp384r1 | MLKEM768 |",
        "| `hybrid` | X25519MLKEM768 | refused | refused | refused |",
        "| `non-pqc` | secp384r1, after a HelloRetryRequest | refused | secp384r1 | refused |",
        "| `pqc` | refused | refused | refused | MLKEM768 |",
        "",
        "Notice the `X25519` column: refused every time. The server's only classical group",
        "is its `ECDHE curve`, `secp384r1`, so a client that offers nothing but x25519 never",
        "gets in, whatever the PQC setting.",
        "",
        "When would you set one? `non-pqc` for a client that chokes on a big ClientHello,",
        "`hybrid` or `pqc` to *require* post-quantum and fail closed. `all` is the sane",
        "default, and it's what ships.",
        "",
        "**Put it back on R1.** Either line works; `all` is the default, so neither one",
        "leaves anything in the running config:",
        "",
        "```",
        "conf t",
        " no ip http secure-pqc-type",
        "end",
        "```",
    ),
    code(
        "# laptop -> R1:443: back to the hybrid default",
        "tls_probe('r1');",
    ),

    # ------------------------------------------------------------------- act 3
    md(
        "---",
        '<a id="act3"></a>',
        "## Act 3: now do it to the fabric",
        "",
        "One router by hand is fine. What about fifty? `ip http secure-pqc-type` is one",
        "modelled YANG leaf, so `tls-pq.yml` is the smallest playbook in the automation",
        "layer: one NETCONF edit per router, no CLI at all.",
        "",
        "Run `check()` first if you skipped Act 2: the playbook reloads every HTTPS",
        "server, and the Demo 4 trap applies to all of them.",
    ),
    step("Demo 6: Ansible states the posture, then proves the knob",
        "Run it twice. The first run writes `secure-pqc-type all` on every router and",
        "saves; the second finds nothing to do. Why push the default? So the posture is",
        "*stated* in config, not an accident of the release.",
        "",
        "Captured run (`captured/09-tls-pq/`): `changed=1` on each router, then",
        "`changed=0`. Each run takes about 40 s (37 to 41 s on 2026-10-29).",
        "",
        recap_note()),
    code(
        "# Ansible: state the HTTPS posture (pq_https_pqc_type, default 'all') on r1, r2, r3",
        "playbook('ansible-playbook tls-pq.yml', expect_changed=1);",
    ),
    code(
        "# Ansible: same playbook again; nothing to do is the point",
        "playbook('ansible-playbook tls-pq.yml', expect_changed=0);",
    ),
    code(
        "# laptop -> R1, R2, R3: every router negotiates the hybrid group",
        "probe_all();",
    ),
    md(
        "Now prove the knob does something, fleet-wide. The playbook reads",
        "`pq_https_pqc_type` from `group_vars/all/pqc.yml`; override it for one run:",
    ),
    code(
        "# Ansible: force every HTTPS server classical",
        "playbook('ansible-playbook tls-pq.yml -e pq_https_pqc_type=non-pqc', expect_changed=1);",
    ),
    code(
        "# laptop -> R1, R2, R3: HelloRetryRequest to secp384r1 everywhere",
        "probe_all(expect='secp384r1', trace=True);",
    ),

    # ------------------------------------------------------------------- after
    md(
        "---",
        '<a id="after"></a>',
        "## Clean up",
        "",
        "Remove the leaf on all three routers. Teardown deletes it rather than writing a",
        "value, so each box goes back to its own default (`all`):",
    ),
    code(
        "# Ansible: remove ip http secure-pqc-type from r1, r2, r3",
        "playbook('ansible-playbook tls-pq.yml -e state=absent', expect_changed=1);",
    ),
    code(
        "# R1, R2, R3: back in the starting state, and negotiating hybrid again",
        "check() and probe_all();",
    ),
    md(
        "The pinned `ip http secure-trustpoint` stays. It's what keeps the next restart",
        "from picking the ML-DSA trustpoint, so leave it unless that trustpoint is gone.",
        "",
        "On the laptop: containers down, generated certificates removed, `learn/tls` back",
        "to the committed state.",
    ),
    laptop_cleanup_cell("learn/tls"),
    md(
        "**Run it again?** Start from [Before each run](#preflight). Clean up leaves the",
        "routers in the starting state, so `check()` should be green straight away.",
    ),
]


def main() -> None:
    write(OUT, CELLS, f"Act 1 copied from {SOURCE_NB.name}: {len(KEX_CELLS)} + {len(AUTH_CELLS)} cells")


if __name__ == "__main__":
    main()
