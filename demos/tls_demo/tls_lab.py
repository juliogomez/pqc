"""Helpers for tls_demo.ipynb, the runnable post-quantum TLS walkthrough.

Everything generic (router handles, checks, playbooks, probes, container labs)
lives in ../common/lab.py. This file adds the TLS parts: the two container labs
Act 1 uses, check() / setup() for Act 2's starting state, and probe_all() for
Act 3.

Router handles never send configuration. setup() is the one exception, and it
says so.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from lab import *  # noqa: F401,F403  (r1/r2/r3, playbook, router_check, tls_probe, ... for the notebook)
from lab import (
    GREEN, RED, ROUTERS, Check, ContainerLabs, _adhoc, _configure, _emit_text, compose,
    container_status, laptop_tools, pkill_in, playbook, result, router_check, tls_probe,
)

# The IPsec demo imports this ML-DSA trustpoint on R1 and R3. The 26.2 HTTPS server
# can't serve it, and an unpinned server can pick it up when it restarts.
MLDSA_TRUSTPOINT = "TP-MLDSA65"
_STATUS = "show ip http server secure status"


# --------------------------------------------------------------------------- #
# Act 1 container labs                                                         #
# --------------------------------------------------------------------------- #


def _panic() -> None:
    for c in ("tls-server", "tls-client", "tls-auth-server", "tls-auth-client"):
        if container_status(c) == "running":
            pkill_in(c, "s_server")
            pkill_in(c, "tcpdump")


labs = ContainerLabs(
    labs=("tls", "tls-auth"),
    containers=(("Demo 1", "tls-server"), ("Demo 1", "tls-client"),
                ("Demo 2", "tls-auth-server"), ("Demo 2", "tls-auth-client")),
    healthy=("tls-auth-server",),
    after_build=lambda: compose("tls-auth", "run", "--rm", "certgen", "ecdsa"),
    panic_steps=_panic,
    generated=tuple(f"learn/tls/authentication/config/{side}/{f}"
                    for side, files in (("server", ("ca.crt", "server.crt", "server.key")),
                                        ("client", ("ca.crt", "client.crt", "client.key")))
                    for f in files),
    extra_rows=laptop_tools,
)


# --------------------------------------------------------------------------- #
# Act 2 starting state                                                         #
# --------------------------------------------------------------------------- #


def _https_state(host: str) -> dict:
    """What the HTTPS server serves, what's pinned, and which trustpoints exist."""
    ok, outs, err = _adhoc(host, [
        _STATUS,
        "show running-config | include ^ip http secure-",
        "show crypto pki trustpoints | include Trustpoint",
    ])
    if not ok:
        return {"error": err}
    status, running, tps = outs
    served = re.search(r"HTTP secure server trustpoint: (\S+)", status)
    pinned = re.search(r"^ip http secure-trustpoint (\S+)", running, re.M)
    return {
        "error": "",
        "enabled": "HTTP secure server status: Enabled" in status,
        "pqc_type": (re.search(r"PQC type: (\S+)", status) or [None, "?"])[1],
        "pqc_line": re.search(r"^ip http secure-pqc-type \S+", running, re.M) is not None,
        "served": served[1] if served else "",
        "pinned": pinned[1] if pinned else "",
        "trustpoints": re.findall(r"Trustpoint (\S+?):", tps),
    }


def _states() -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=len(ROUTERS)) as pool:
        return dict(zip(ROUTERS, pool.map(_https_state, ROUTERS)))


def check() -> bool:
    """Read-only: is every router's HTTPS server in Act 2's starting state? (About 15 seconds.)"""
    states = _states()
    checks = []
    for h in ROUTERS:
        checks += [
            Check(h, _STATUS, "HTTPS server", "Enabled", r"HTTP secure server status: Enabled"),
            Check(h, _STATUS, "PQC type (the 26.2 default)", "all", r"PQC type: all"),
            Check(h, "show running-config | include ^ip http secure-", "no pqc-type line (Demo 4 adds one)",
                  "absent", r"secure-pqc-type", present=False),
            Check(h, _STATUS, "serves a classical certificate", f"any trustpoint but {MLDSA_TRUSTPOINT}",
                  rf"server trustpoint: (?!{MLDSA_TRUSTPOINT}\b)\S+"),
        ]
        if MLDSA_TRUSTPOINT in states[h].get("trustpoints", []):
            checks.append(Check(h, "show running-config | include ^ip http secure-",
                                f"trustpoint pinned (carries {MLDSA_TRUSTPOINT})", "ip http secure-trustpoint",
                                r"^ip http secure-trustpoint \S+"))
    return router_check("Act 2 starting state", "HTTPS server on R1, R2, R3, read-only", checks)


def _classical_trustpoint(state: dict) -> str:
    """The trustpoint to pin: the one being served if it's classical, else the self-signed one."""
    if state["served"] and state["served"] != MLDSA_TRUSTPOINT:
        return state["served"]
    for tp in state["trustpoints"]:
        if tp == "Self" or tp.startswith("TP-self-signed-"):
            return tp
    return ""


def setup() -> bool:
    """CONFIGURES each router's HTTPS server into Act 2's starting state, then checks it.

    Removes any ``ip http secure-pqc-type`` line (back to the default, ``all``) and,
    on a router that carries an ML-DSA trustpoint, pins the HTTPS server to a
    classical one. Never turns the HTTPS server on: that needs a certificate
    decision, which is yours.
    """
    start = time.time()
    ok_all = True
    for h, st in _states().items():
        if st["error"]:
            _emit_text(f"{h.upper()}: can't read the HTTPS server state: {st['error']}")
            ok_all = False
            continue
        if not st["enabled"]:
            _emit_text(f"{h.upper()}: the HTTPS server is off. Turn it on yourself (ip http secure-server), "
                       "then rerun this cell.")
            ok_all = False
            continue
        lines = []
        if st["pqc_line"]:
            lines.append("no ip http secure-pqc-type")
        if MLDSA_TRUSTPOINT in st["trustpoints"] and not st["pinned"]:
            tp = _classical_trustpoint(st)
            if not tp:
                _emit_text(f"{h.upper()}: carries {MLDSA_TRUSTPOINT} but no self-signed trustpoint to pin. "
                           "Pin a classical one yourself (ip http secure-trustpoint <name>).")
                ok_all = False
                continue
            lines.insert(0, f"ip http secure-trustpoint {tp}")
        if not lines:
            _emit_text(f"{h.upper()}: already in place.")
            continue
        ok, changed, err = _configure(h, lines)
        _emit_text(f"{h.upper()}: {'; '.join(lines)}" + ("" if ok else f"  FAILED: {err}"))
        ok_all &= ok
    if not ok_all:
        return False
    playbook("ansible-playbook bootstrap.yml", tail=8)
    time.sleep(5)
    _emit_text(f"Setup finished in {time.time() - start:.0f} s. Checking:")
    return check()


# --------------------------------------------------------------------------- #
# Act 3: what does every router negotiate now?                                 #
# --------------------------------------------------------------------------- #


def probe_all(expect: str = "X25519MLKEM768", trace: bool = False) -> bool:
    """tls_probe() against R1, R2 and R3, one panel. ``trace`` also flags HelloRetryRequests."""
    with ThreadPoolExecutor(max_workers=len(ROUTERS)) as pool:
        outs = dict(zip(ROUTERS, pool.map(lambda h: tls_probe(h, trace=trace, expect=expect, show=False), ROUTERS)))
    rows = []
    for h, out in outs.items():
        got = out["group"] or "(handshake failed)"
        rows.append((f"{h.upper()}  negotiated group", expect, got, got.lower() == expect.lower()))
        if trace:
            rows.append((f"{h.upper()}  HelloRetryRequest", "(either)", "yes" if out["hrr"] else "no", True))
    ok = all(r[3] for r in rows)
    result("TLS 1.3 from this laptop to every router", "openssl s_client -tls1_3, port 443", rows=rows,
           banner="Every router negotiates what the playbook asked for." if ok else "Not what we expected.",
           banner_color=GREEN if ok else RED)
    return ok
