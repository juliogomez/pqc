"""Helpers for ipsec_demo.ipynb, the runnable post-quantum IPsec walkthrough.

Everything generic (router handles, checks, playbooks, container labs) lives in
../common/lab.py. This file adds the IPsec parts: the two container labs Act 1
uses, the Tunnel0 addressing, and check() / setup() for Act 2's starting state.

Router handles never send configuration. Every change in Act 2 is typed by you,
or made by a playbook you run on purpose. setup() is the one exception, and it
says so.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import lab
from lab import *  # noqa: F401,F403  (r1/r2/r3, playbook, router_check, ... for the notebook)
from lab import (
    ANSIBLE_BIN, PING_OK, REPO_ROOT, ROUTERS, TOPOLOGY_FILE, UNDERLAY_IF, UNDERLAY_IP,
    Check, ContainerLabs, _adhoc, _emit_text, _r2_legs, _topology_ok, compose, container_status,
    dexec, pkill_in, playbook, router_check,
)
from pqc_demo import IPsecAuthDemo

TUNNEL0_IP = {"r1": "192.168.100.1", "r3": "192.168.100.2"}


def underlay() -> None:
    """What the router cells use as each router's underlay, and where it came from."""
    lab.underlay()
    if UNDERLAY_IP:
        print(f"\nTunnel0 runs R1 {UNDERLAY_IP['r1']} <-> R3 {UNDERLAY_IP['r3']}, "
              f"inside {TUNNEL0_IP['r1']} / {TUNNEL0_IP['r3']}.")


def mldsa_snippets() -> None:
    """Demo 7's paste for R1 and R3 (ipsec.md, Exercise 5), with this lab's underlay addresses."""
    if not _topology_ok():
        return
    for h, peer in (("r1", "r3"), ("r3", "r1")):
        print(f"! ---- {h.upper()} ----")
        print("\n".join([
            "crypto ikev2 profile MLDSA-PROFILE",
            f" match identity remote address {UNDERLAY_IP[peer]} 255.255.255.255",
            f" identity local address {UNDERLAY_IP[h]}",
            " authentication local mldsa-sig",
            " authentication remote mldsa-sig",
            " pki trustpoint TP-MLDSA65",
            " dpd 30 5 periodic",
            "crypto ipsec profile MLDSA-IPSEC",
            " set transform-set CLASSICAL-TS",
            " set ikev2-profile MLDSA-PROFILE",
            "interface Tunnel0",
            " tunnel protection ipsec profile MLDSA-IPSEC",
            " no shutdown",
        ]) + "\n")


# --------------------------------------------------------------------------- #
# Act 1 container labs                                                         #
# --------------------------------------------------------------------------- #


def _after_build() -> None:
    # --no-cache: Dockerfile.mldsa fetches strongSwan master at ML_DSA_REF. The default
    # is a pinned commit, but if ML_DSA_REF is overridden to a moving ref like `master`,
    # Docker would otherwise keep reusing the first checkout it cached.
    _emit_text("  building ipsec-auth (experimental ML-DSA, strongSwan master)…")
    res = compose("ipsec-auth", "build", "--no-cache", files=IPsecAuthDemo.MLDSA_FILES)
    _emit_text("    ok" if res.ok else f"    FAILED\n{res.text[-800:]}")
    compose("ipsec-auth", "run", "--rm", "certgen", "ecdsa")


def _panic() -> None:
    if container_status("ike-initiator") == "running":
        dexec("ike-initiator", "swanctl --terminate --ike pqc-tunnel 2>/dev/null || true; true")
        pkill_in("ike-initiator", "tcpdump")
    if container_status("ike-auth-initiator") == "running":
        dexec("ike-auth-initiator", "swanctl --terminate --ike auth-tunnel 2>/dev/null || true; true")


labs = ContainerLabs(
    labs=("ipsec", "ipsec-auth"),
    containers=(("Demo 1", "ike-initiator"), ("Demo 1", "ike-responder"),
                ("Demo 2", "ike-auth-initiator"), ("Demo 2", "ike-auth-responder")),
    healthy=("ike-responder", "ike-auth-responder"),
    after_build=_after_build,
    panic_steps=_panic,
    down_files={"ipsec-auth": IPsecAuthDemo.MLDSA_FILES},
    restore=("learn/ipsec/key-exchange/config",),
    generated=tuple(f"learn/ipsec/authentication/config/{side}/{d}"
                    for side in ("initiator", "responder") for d in ("private", "x509", "x509ca")),
)


# --------------------------------------------------------------------- act 2 state

_PQAUTO_IPSEC = r"^crypto (ikev2|ipsec) \S+ PQAUTO-|^interface Tunnel10[12]\b"
_PING_OK = PING_OK


def _endpoints() -> tuple[tuple[str, str, str], ...]:
    """(router, its underlay address, the far end's) for the two tunnel endpoints."""
    return (("r1", UNDERLAY_IP["r1"], UNDERLAY_IP["r3"]), ("r3", UNDERLAY_IP["r3"], UNDERLAY_IP["r1"]))


def act2_check() -> bool:
    """Read-only: is the lab in Act 2's starting state? (About 15 seconds.)"""
    if not _topology_ok():
        return False
    checks = []
    for h, ip, peer in _endpoints():
        ifc = UNDERLAY_IF[h]
        checks += [
            Check(h, f"show ip interface {ifc} | include Internet address", f"{ifc} address", ip,
                  rf"Internet address is {re.escape(ip)}/"),
            Check(h, f"ping {peer} source {ifc}", "underlay to the far end", "pings succeed", _PING_OK),
            Check(h, "show running-config | include ^crypto ikev2 proposal", "Exercise 1 proposal",
                  "CLASSICAL-PROPOSAL", r"CLASSICAL-PROPOSAL"),
            Check(h, "show running-config | include pqc mlkem|crypto ikev2 fragmentation",
                  "no PQC yet (Demo 4 adds it)", "absent", r"pqc mlkem|fragmentation", present=False),
            # Only IPsec objects count: MACsec automation (e.g. a PQAUTO-PEER trustpoint) can stay.
            Check(h, "show running-config | include PQAUTO-|^interface Tunnel10", "no IPsec automation objects",
                  "absent", _PQAUTO_IPSEC, present=False),
            Check(h, "show running-config | include MLDSA-PROFILE|MLDSA-IPSEC",
                  "no ML-DSA profile (Demo 7 pastes it)", "absent", r"MLDSA-(PROFILE|IPSEC)", present=False),
            Check(h, "show crypto pki trustpoints | include Trustpoint", "ML-DSA trustpoint imported",
                  "TP-MLDSA65", r"TP-MLDSA65"),
            Check(h, "show running-config | include ^service internal", "service internal (early 26.2 builds)",
                  "service internal", r"^service internal"),
        ]
    checks += [
        Check("r1", "ping 192.168.100.2", "Tunnel0 passes traffic", "pings succeed", _PING_OK),
        Check("r1", "show crypto ikev2 sa", "classical SA, PSK", "Auth sign: PSK", r"Auth sign: PSK"),
        Check("r1", "show crypto key mypubkey all | include Key name", "no leftover Demo 6 key",
              "absent", r"PQC-DEMO", present=False),
        *(Check("r2", f"show ip interface {ifc} | include Internet address", f"{ifc} address (to {peer.upper()})",
                ip, rf"Internet address is {re.escape(ip)}/") for ifc, ip, peer in _r2_legs()),
        Check("r2", "show running-config | include PQAUTO-|^interface Tunnel10", "no IPsec automation objects",
              "absent", _PQAUTO_IPSEC, present=False),
    ]
    return router_check("Act 2 starting state", "R1-R2-R3, read-only", checks)


def act2_setup() -> bool:
    """Configure the lab into Act 2's starting state from wherever it is, then check it.

    Idempotent: on a lab that's already there it only reads (about a minute); after a
    full session it takes 3 to 4 minutes.
    It never touches the underlay; that's a one-time job.
    """
    start = time.time()

    # 1. The underlay is a precondition, not something this builds.
    if not _topology_ok():
        return False
    for h, _, peer in _endpoints():
        ok, outs, err = _adhoc(h, [f"ping {peer} source {UNDERLAY_IF[h]}"])
        if not (ok and re.search(_PING_OK, outs[0])):
            _emit_text(f"{h.upper()} can't reach {peer} over {UNDERLAY_IF[h]}. Fix the underlay first "
                       "(../README.md, \"Build your lab\"), then rerun this cell.\n"
                       f"{err or outs[0].strip()}")
            return False
    _emit_text("Underlay: R1 and R3 reach each other through R2.")

    # 2. Act 3 leftovers. The baseline teardown deletes CLASSICAL-* too; step 3 rebuilds them.
    leftovers = [h for h in ROUTERS
                 if (r := _adhoc(h, ["show running-config | include PQAUTO-|^interface Tunnel10"]))[0]
                 and re.search(_PQAUTO_IPSEC, r[1][0], re.M)]
    if leftovers:
        _emit_text(f"Act 3 objects found on {', '.join(h.upper() for h in leftovers)}: tearing them down "
                   "(about 3 minutes).")
        playbook("ansible-playbook ipsec-pq-mlkem.yml -e state=absent", tail=8)
        playbook("ansible-playbook ipsec-baseline.yml -e state=absent", tail=8)
    else:
        _emit_text("Act 3 objects: none.")

    # 3. R1 and R3: service internal, ML-DSA trustpoint, Demos 4/6/7 undone, Exercise 1.
    script = Path(__file__).resolve().parent / "router_setup.py"
    proc = subprocess.run([str(ANSIBLE_BIN / "python"), str(script),
                           json.dumps({h: {"interface": UNDERLAY_IF[h], "address": UNDERLAY_IP[h]} for h in ("r1", "r3")})],
                          capture_output=True, text=True, timeout=900)
    _emit_text((proc.stdout + proc.stderr).strip() or f"rc={proc.returncode}")
    if proc.returncode != 0:
        return False

    # 4. NETCONF for Act 3. Pushes nothing if it's already on.
    playbook("ansible-playbook bootstrap.yml", tail=8)

    # 5. Tunnel0 can take up to ~25 s to renegotiate after a change; let it settle before checking.
    for _ in range(3):
        ok, outs, _ = _adhoc("r1", ["ping 192.168.100.2 repeat 20"])
        if ok and re.search(_PING_OK, outs[0]):
            break
    _emit_text(f"Setup finished in {time.time() - start:.0f} s. Checking:")
    return act2_check()


check = act2_check
setup = act2_setup
