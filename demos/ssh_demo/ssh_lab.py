"""Helpers for ssh_demo.ipynb, the runnable post-quantum SSH walkthrough.

Everything generic (router handles, checks, playbooks, probes, container labs)
lives in ../common/lab.py. This file adds the SSH parts: the container lab Act 1
uses, check() / setup() for Act 2's starting state, and probe_all() for Act 3.

Router handles never send configuration. setup() is the one exception, and it
says so.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from lab import *  # noqa: F401,F403  (r1/r2/r3, playbook, router_check, ssh_kex_probe, ... for the notebook)
from lab import (
    GREEN, RED, ROUTERS, Check, ContainerLabs, _adhoc, _configure, _emit_text, compose,
    container_status, laptop_tools, pkill_in, playbook, result, router_check, ssh_kex_probe,
)

MLKEM = "mlkem768x25519-sha256"


# --------------------------------------------------------------------------- #
# Act 1 container lab                                                          #
# --------------------------------------------------------------------------- #


def _panic() -> None:
    if container_status("ssh-server") == "running":
        pkill_in("ssh-server", "sshd")
        pkill_in("ssh-server", "tcpdump")


labs = ContainerLabs(
    labs=("ssh",),
    containers=(("Demo 1", "ssh-server"), ("Demo 1", "ssh-client")),
    healthy=("ssh-server",),
    after_build=lambda: compose("ssh", "run", "--rm", "keygen", "ed25519"),
    panic_steps=_panic,
    generated=tuple(f"learn/ssh/config/{f}" for f in (
        "server/ssh_host_key", "server/ssh_host_key.pub", "server/authorized_keys",
        "client/id_key", "client/id_key.pub", "client/known_hosts")),
    extra_rows=laptop_tools,
)


# --------------------------------------------------------------------------- #
# Act 2 starting state                                                         #
# --------------------------------------------------------------------------- #


def check() -> bool:
    """Read-only: is every router's SSH server in Act 2's starting state? (About 15 seconds.)"""
    checks = []
    for h in ROUTERS:
        checks += [
            Check(h, "show ip ssh", "SSH server", "SSH Enabled - version 2.0", r"SSH Enabled - version 2\.0"),
            Check(h, "show running-config | include ^ip ssh server algorithm", "no KEX line (Demo 4 adds one)",
                  "absent", r"ip ssh server algorithm kex", present=False),
            Check(h, "show ip ssh", "KEX list (platform default)", "no ML-KEM yet",
                  r"KEX Algorithms:.*mlkem", present=False),
            Check(h, "show ip ssh", "no EC host key (the lockout trap)", "ssh-ec ... NONE",
                  r"ssh-ec, base64 encoded\): NONE"),
        ]
    return router_check("Act 2 starting state", "SSH server on R1, R2, R3, read-only", checks)


def setup() -> bool:
    """CONFIGURES each router's SSH server back to Act 2's starting state, then checks it.

    Removes any ``ip ssh server algorithm kex`` line, so the server offers its
    platform default (classical) list again. Leaves ``ip ssh server algorithm
    hostkey`` alone: if you pinned it, it's protecting you.
    """
    start = time.time()
    ok_all = True
    for h in ROUTERS:
        ok, outs, err = _adhoc(h, ["show running-config | include ^ip ssh server algorithm kex"])
        if not ok:
            _emit_text(f"{h.upper()}: can't read the SSH config: {err}")
            ok_all = False
            continue
        if not re.search(r"ip ssh server algorithm kex", outs[0]):
            _emit_text(f"{h.upper()}: already in place.")
            continue
        ok, _, err = _configure(h, ["no ip ssh server algorithm kex"])
        _emit_text(f"{h.upper()}: no ip ssh server algorithm kex" + ("" if ok else f"  FAILED: {err}"))
        ok_all &= ok
    if not ok_all:
        return False
    playbook("ansible-playbook bootstrap.yml", tail=8)
    _emit_text(f"Setup finished in {time.time() - start:.0f} s. Checking:")
    return check()


# --------------------------------------------------------------------------- #
# Act 3: what does every router offer, and what do you get?                    #
# --------------------------------------------------------------------------- #


def probe_all(expect_offer: bool = True) -> bool:
    """ssh_kex_probe() against R1, R2 and R3: your client as configured, then pinned to ML-KEM."""
    def one(h: str) -> tuple[dict, dict]:
        return (ssh_kex_probe(h, expect=None, show=False),
                ssh_kex_probe(h, kex=MLKEM, expect=None, show=False))

    with ThreadPoolExecutor(max_workers=len(ROUTERS)) as pool:
        outs = dict(zip(ROUTERS, pool.map(one, ROUTERS)))
    rows = []
    for h, (default, pinned) in outs.items():
        offers = MLKEM in default["server_offers"]
        rows.append((f"{h.upper()}  router offers {MLKEM}", "yes" if expect_offer else "no",
                     "yes" if offers else "no", offers == expect_offer))
        rows.append((f"{h.upper()}  your client, as configured", "(your config decides)",
                     default["kex"] or "(handshake failed)", bool(default["kex"])))
        rows.append((f"{h.upper()}  -o KexAlgorithms={MLKEM}", MLKEM if expect_offer else "refused",
                     pinned["kex"] or "refused", (pinned["kex"] == MLKEM) == expect_offer))
    ok = all(r[3] for r in rows)
    result("SSH key exchange from this laptop to every router", "stopped before login", rows=rows,
           banner=("Every router offers ML-KEM." if expect_offer else "No router offers ML-KEM: back to the default.")
           if ok else "Not what we expected.",
           banner_color=GREEN if ok else RED)
    return ok
