#!/usr/bin/env python3
"""Generate macsec_demo.ipynb, a runnable post-quantum MACsec walkthrough.

    python3 build_notebook.py

A declarative cell list written as raw nbformat v4 JSON (see ../common/nbbuild.py).
The runtime lives in macsec_lab.py (MACsec specifics), ../common/lab.py (routers,
playbooks) and ../containers_demo/pqc_demo.py (containers, panels).

Act 1 is copied verbatim from ../containers_demo/PQC_DEMOS.ipynb (the "macsec"
section). Regenerate this file whenever that notebook changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
from nbbuild import (  # noqa: E402
    SOURCE_NB, act1_section, automation_cells, code, containers_cells, find_helpers,
    laptop_cleanup_cell, md, recap_note, step, underlay_cells, write,
)

OUT = HERE / "macsec_demo.ipynb"
MACSEC_CELLS = act1_section("macsec")
FIND_HELPERS = find_helpers("macsec_demo")

CELLS: list[dict] = [
    md(
        "# Post-quantum MACsec, end to end",
        "## Containers, router CLI, Ansible",
        "",
        "MACsec encrypts a single wire with GCM-AES-256. That part is already",
        "quantum-safe. So where's the post-quantum problem? In **how the key gets there**.",
        "Type it in by hand and you have a static secret on two boxes forever. Derive it",
        "from an EAP-TLS handshake and it's fresh every session, but that handshake has a",
        "key exchange in it, and a classical key exchange is exactly what *harvest now,",
        "decrypt later* goes after.",
        "",
        "This notebook walks that path three ways: EAP-TLS in containers first, then a",
        "hand-typed MACsec link between two real IOS XE 26.2 routers, then Ansible",
        "replacing the hand-typed key with EAP-TLS and putting ML-KEM into it.",
        "",
        "**First time?** Build your lab with [`demos/README.md`](../README.md)",
        "(prerequisites, credentials, underlay) and this demo's",
        "[MACsec link](README.md#the-macsec-link) notes.",
        "MACsec needs a **direct cable** between R1 and R2. This notebook checks the rest",
        "in [Before each run](#preflight).",
        "",
        "Four kinds of cell:",
        "",
        "- **Act 1 cells** drive `hostapd` and `wpa_supplicant` in containers and draw the",
        "  handshake as it happens.",
        "- **Router checks** (`r1(...)`, `check()`, `proof()`) run read-only `show`",
        "  commands and one ping through Ansible. In Act 2 you type every change yourself,",
        "  and the checks confirm you got the result.",
        "- **Two cells change state on purpose**, and say so: `setup()` configures, and",
        "  `mlkem_proof()` raises a trace level and forces one re-authentication.",
        "- **Playbook cells** (`playbook(...)`) run Act 3 from `deploy/c8000/automation`",
        "  with its own venv and keep the `PLAY RECAP` visible.",
    ),
    md(
        "## Contents",
        "",
        "- [Setup](#setup)",
        "- [Before each run](#preflight): containers, your lab, routers, automation",
        "- [Act 1: containers](#act1): Demo 1",
        "- [Starting at Act 2?](#act2-setup): set up the routers without the containers",
        "- [Act 2: MACsec by hand](#act2): Demos 2 and 3",
        "- [Act 3: Ansible](#act3): Demos 4 and 5",
        "- [Clean up](#after)",
    ),

    # ------------------------------------------------------------------ setup
    md(
        '<a id="setup"></a>',
        "## Setup",
        "",
        "Run this once per kernel. It finds `pqc_demo.py` in `demos/containers_demo/`, the",
        "shared helpers in `demos/common/` and `macsec_lab.py` next to this notebook,",
        "whichever folder Jupyter started in.",
    ),
    code(
        "import sys, pathlib, re",
        *FIND_HELPERS,
        "from pqc_demo import *    # noqa: F401,F403  containers, panels",
        "from macsec_lab import *  # noqa: F401,F403  routers, playbooks, proofs, labs",
        "",
        "# Ladder for the Act 1 panels (same object the learner notebook uses).",
        "macsec = MACsecDemo()",
        "",
        "set_presenter_mode(True)",
        "print('Repo:       ', REPO_ROOT)",
        "print('Automation: ', AUTOMATION_DIR)",
        "print('Containers:  authenticator supplicant | host')",
        "print('Routers:     r1 r2 (read-only)   Proofs: proof() mlkem_proof()   Playbooks: playbook()')",
    ),

    # -------------------------------------------------------------- preflight
    md(
        "---",
        '<a id="preflight"></a>',
        "## Before each run",
        "",
        "Most failures are leftovers from the last run, so check everything before you",
        "start. If a check fails, the fix is in the table below or in",
        "[Build your lab](../README.md#build-your-lab).",
    ),
    *containers_cells("It builds hostapd and wpa_supplicant against OpenSSL 3.5, so give it a few minutes."),
    *underlay_cells(),
    md(
        "Look at the MACsec lines. A switchport gets `macsec network-link`; a routed port",
        "(`no switchport`) rejects that and takes plain `macsec`, which is its",
        "peer-to-peer mode. Your `macsec_command` setting decides which one each router",
        "gets, and every cell below prints the right one for your lab.",
    ),
    md(
        "### Routers: is the link in Act 2's starting state?",
        "",
        "The target: **no MACsec, MKA or dot1x on either end of the R1 to R2 wire, the",
        "global EAP-TLS knobs at their default, and R1 can ping R2 across it.** The same",
        "cell checks the clocks, because Act 3's certificates are only as good as the",
        "clocks that check them.",
        "",
        "This cell only reads (about 20 seconds). Starting from scratch?",
        "[`setup()`](#act2-setup) fixes most rows.",
    ),
    code(
        "# R1, R2: read-only checks of the Act 2 starting state, plus the clocks",
        "check()",
    ),
    md(
        "**If a row fails**, here's where the fix lives:",
        "",
        "| Row | Fix |",
        "|---|---|",
        "| no MACsec, MKA or dot1x | [`setup()`](#act2-setup) removes this demo's hand PSK. Anything else: run the [Clean up](#after) playbooks |",
        "| MKA sessions | same as above: something is still keying the link |",
        "| access-session pqc-type, tls-version | [`setup()`](#act2-setup), or `no access-session pqc-type` and `no access-session tls-version` |",
        "| R1 to R2 over the MACsec wire | the cable, the VLAN and the addressing: [the MACsec link](README.md#the-macsec-link) |",
        "| show clock, agree | `clocks(fix=True)` (or `setup()`) sets a free-running clock from the other router. Better: NTP on both |",
        "",
        "Want the raw output behind a row? Ask the router directly:",
    ),
    code(
        "# R1: any single read-only command, real output",
        "r1(f'show running-config interface {MACSEC_IF[\"r1\"]}');",
    ),
    *automation_cells(),

    # ------------------------------------------------------------------- act 1
    md(
        "---",
        '<a id="act1"></a>',
        "## Act 1: learn it in containers",
        "",
        "An EAP authenticator (`hostapd`) and a supplicant (`wpa_supplicant`) on a veth",
        "pair, with `tcpdump` on the authenticator. That's the same 802.1X conversation",
        "the routers have in Act 3, minus the MACsec data plane. One cell per action:",
        "read the comment, run it, read the output.",
    ),
    step("Demo 1: EAP-TLS with hybrid ML-KEM, then with a post-quantum certificate",
        "What does an EAP-TLS handshake look like when both ends run OpenSSL 3.5? Look for:",
        "",
        "- `X25519MLKEM768` as the TLS group with an ordinary ECDSA certificate:",
        "  **14 EAPOL frames**",
        "- the same identity reissued as ML-DSA-65: **54 frames**, because a bigger",
        "  certificate needs more EAP fragments",
        "- (optional) drop the TLS 1.3 gate and the handshake falls back to classical",
        "  TLS 1.2 **without a single error**",
        "",
        "Keep the last one in mind. Act 3 pins TLS 1.3 on the routers for exactly this",
        "reason."),
    *MACSEC_CELLS,
    md(
        "Something stuck? This stops stray `hostapd`, `wpa_supplicant` and capture",
        "processes without taking the stacks down:",
    ),
    code(
        "# host: stop stray hostapd / wpa_supplicant / tcpdump, keep the stacks up",
        "labs.panic()",
    ),

    # ------------------------------------------------- act 2 without the containers
    md(
        "---",
        '<a id="act2-setup"></a>',
        "## Starting at Act 2? Set up the routers",
        "",
        "Skipping the containers? Start here. This cell loads its own helpers, so you",
        "don't need the Setup cell at the top. Then, on R1 and R2:",
        "",
        "1. removes this demo's hand-typed PSK (`MACSEC-POLICY`, `MACSEC-KC`) if it's there",
        "2. resets `access-session pqc-type` and `tls-version` to the default",
        "3. **refuses** if EAP-TLS or some other MACsec is on the link: that has its own",
        "   teardown, in [Clean up](#after)",
        "4. sets a free-running clock from the other router's (`clock set`, never on a box",
        "   that runs NTP)",
        "5. NETCONF on all three, for Act 3",
        "6. the same read-only check as the pre-flight",
        "",
        "**This cell configures the routers**, unlike every other router cell here. On a",
        "lab that's already in shape it only reads.",
    ),
    code(
        "# R1, R2: CONFIGURES the Act 2 starting state, then checks it",
        "# (loads the helpers first, so this works as the first cell you run)",
        "import sys, pathlib",
        *FIND_HELPERS,
        "from macsec_lab import *  # noqa: F401,F403  routers, playbooks, proofs",
        "",
        "setup()",
    ),

    # ------------------------------------------------------------------- act 2
    md(
        "---",
        '<a id="act2"></a>',
        "## Act 2: MACsec by hand",
        "",
        "**You type every router command in this act, on R1 and on R2.** MACsec is",
        "hop-by-hop, so both ends of the wire need the same config. **The link goes dark**",
        "between the two: with only R1 done, R1 to R2 pings measured 0/10. It came back",
        "secured 10 s after R2's config. Reaching R2 over its management address? Then",
        "you won't notice.",
    ),
    step("Demo 2: a pre-shared key on both ends",
        "First, the key. It's the *CAK*, and on this path it's the root of everything,",
        "so make a real one. **In your own terminal, not in this notebook** (that way it",
        "never ends up in a saved output cell):",
        "",
        "```",
        "openssl rand -hex 32",
        "```",
        "",
        "64 hex characters, 256 bits. Now print the config for *your* lab. It has your",
        "interface names and the right MACsec keyword for each end; only the key is a",
        "placeholder:"),
    code(
        "# laptop: the Act 2 config for R1 and R2 (nothing is sent)",
        "psk_config()",
    ),
    md(
        "Paste each block into its router, with your key in place of the placeholder. Same",
        "key on both, or MKA never finds a peer.",
        "",
        "Then watch it come up. **R1:**",
        "",
        "```",
        "show mka sessions",
        "```",
        "",
        "Expect `Secured` with CKN `01` within about 10 seconds. Is it *really*",
        "encrypting? `Secured` alone doesn't prove it; there's a well-known trap where it",
        "says secured and every counter stays at zero. So count:",
    ),
    code(
        "# R1, R2: secured? Then 20 pings across the wire, counters read before and after",
        "proof('psk')",
    ),
    md(
        "Each ping should add one to `encrypted out` on R1 and one to `validated in` on R2,",
        "and the echo replies do the reverse. A few extra are fine: MKA's own keepalives",
        "ride the same SA.",
        "",
        "So is this post-quantum? The data plane is: GCM-AES-256 with a 256-bit key",
        "stands up to Grover. The problem is operational. That key never changes unless",
        "you type a new one, on both ends, and nothing in the protocol helps you.",
    ),
    step("Demo 3: take it off again",
        "Act 3 builds a different key source on the same interface, and the two fight over",
        "it, so take your hand config off. Same deal, both routers:"),
    code(
        "# laptop: the Act 2 teardown for R1 and R2 (nothing is sent)",
        "psk_teardown()",
    ),
    md(
        "Expect pings to drop right after (measured 1/5 straight away): the two ends stop",
        "encrypting a few seconds apart. Give it ten seconds, then:",
    ),
    code(
        "# R1, R2: back in the starting state (read-only)",
        "check()",
    ),

    # ------------------------------------------------------------------- act 3
    md(
        "---",
        '<a id="act3"></a>',
        "## Act 3: let EAP-TLS make the key",
        "",
        "What if the key made itself? `macsec-eaptls.yml` builds EAP-TLS MACsec: a local",
        "CA on R2, an RSA identity certificate on each router (enrolled over SCEP), the",
        "AAA and 802.1X stack, and the interface config, **last**, because that's what",
        "closes the port. Every session now gets a fresh CAK from a TLS 1.3 handshake.",
        "",
        "Then `macsec-pq.yml` is the one-line change that puts ML-KEM into that handshake:",
        "`access-session pqc-type hybrid` plus `tls-version 1.3` on both ends.",
        "",
        "**Heads up before you run it:**",
        "",
        "- it turns on `aaa new-model`. On a box with no AAA yet, the role also adds",
        "  `aaa authentication login default local` and `aaa authorization exec default",
        "  local`, so your local login keeps working the way it did",
        "- a switchport end must be `switchport mode access`; the role refuses otherwise",
        "- a routed end gets no `access-session closed`. With it, EAP-TLS succeeds and",
        "  authorization then fails with `Linksec Failure`, forever. The switchport end",
        "  keeps it",
        "- teardown leaves `aaa new-model`, the AAA method lines, the CA, the trustpoints",
        "  and the `username R1` / `username R2` entries in place. Removing them is a",
        "  lockout risk for a cleanup step, so that's left to you",
    ),
    step("Demo 4: Ansible keys the link with EAP-TLS",
        "Measured: 215 s with the CA and both certificates already in place. A first run",
        "also creates the CA and enrols both routers, so give it longer. Then comes a cold",
        "start where both ends try to authenticate each other at once. The cell starts it",
        "in the background; the next one waits.",
        "",
        recap_note()),
    code(
        "# Ansible: local CA on R2, certificates, AAA, EAP-TLS MACsec on R1 and R2",
        "eaptls = playbook_bg('ansible-playbook macsec-eaptls.yml')",
    ),
    code(
        "# Ansible: wait for it and show the recap",
        "playbook_wait(eaptls);",
    ),
    md(
        "Same proof as Act 2. The difference is the CKN: `01` was the name you typed; now",
        "it's 32 hex characters derived from the EAP-TLS session.",
    ),
    code(
        "# R1, R2: secured with an EAP-derived key? Then 20 pings, counters before and after",
        "proof('eap')",
    ),
    step("Demo 5: ML-KEM inside the handshake",
        "Classical TLS 1.3 so far. `macsec-pq.yml` sets `pqc-type hybrid` and pins TLS 1.3",
        "on both ends, then forces one fresh handshake so the new setting takes effect",
        "(about 60 s).",
        "Why `hybrid` and not `pqc`? On 26.2, `pqc` lands on ML-KEM-512 while `hybrid` gets",
        "ML-KEM-768 with X448 mixed in. `deploy/c8000/macsec.md` has the table."),
    code(
        "# Ansible: access-session pqc-type hybrid + tls-version 1.3 on R1 and R2",
        "playbook('ansible-playbook macsec-pq.yml');",
    ),
    md(
        "Did that handshake actually use ML-KEM? The router doesn't say in any `show`",
        "command. It says in a debug trace, so this cell **changes state for a moment**:",
        "it raises the `smd` EAP trace level on R1, clears the session (the link drops and",
        "re-secures, 40 s measured), reads the negotiated group, and puts the trace level",
        "back.",
    ),
    code(
        "# R1: force one fresh EAP-TLS handshake and read what it negotiated",
        "mlkem_proof('r1')",
    ),
    md(
        "`x448_mlkem768` is the proof. A recorded session is now protected by ML-KEM-768",
        "as well as X448, and both would have to fall.",
        "",
        "What's still classical? The certificates. EAP-TLS here authenticates with",
        "RSA-2048, and 26.2 has no ML-DSA for 802.1X. That matters less than it sounds:",
        "forging a signature needs a quantum computer *during* the handshake, while",
        "harvest now, decrypt later only needs a recording.",
        "",
        "Last, the automation's own read-only check of the MACsec pair:",
    ),
    code(
        "# Ansible: read-only MACsec verification on R1 and R2",
        "playbook('ansible-playbook verify.yml -e verify_macsec_only=true');",
    ),

    # ------------------------------------------------------------------- after
    md(
        "---",
        '<a id="after"></a>',
        "## Clean up",
        "",
        "Reverse order: the PQ settings first, then EAP-TLS. The second one opens the port",
        "back up, so expect R1 to R2 pings to come back within a few seconds of it",
        "finishing.",
    ),
    code(
        "# Ansible: access-session pqc-type and tls-version back to the default",
        "playbook('ansible-playbook macsec-pq.yml -e state=absent');",
    ),
    code(
        "# Ansible: EAP-TLS MACsec off R1 and R2 (the CA and trustpoints stay)",
        "playbook('ansible-playbook macsec-eaptls.yml -e state=absent');",
    ),
    code(
        "# R1, R2: back in the starting state",
        "check()",
    ),
    md(
        "On the laptop: containers down, generated certificates removed, `learn/macsec`",
        "back to the committed state.",
    ),
    laptop_cleanup_cell("learn/macsec"),
    md(
        "**Run it again?** Start from [Before each run](#preflight). Clean up leaves the",
        "link in the starting state, so `check()` should be green straight away.",
    ),
]


def main() -> None:
    write(OUT, CELLS, f"Act 1 copied from {SOURCE_NB.name}: {len(MACSEC_CELLS)} cells")


if __name__ == "__main__":
    main()
