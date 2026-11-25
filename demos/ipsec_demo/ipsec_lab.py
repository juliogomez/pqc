"""Helpers for ipsec_demo.ipynb, the runnable post-quantum IPsec walkthrough.

Everything generic (router handles, checks, playbooks, container labs) lives in
../common/lab.py. This file adds the IPsec parts: the two container labs Act 1
uses, the Tunnel0 addressing, and check() / setup() for Act 2's starting state.

Router handles (r1, r2, r3) and checks never send configuration. Changes go through
configure() and cli(), which run the demo's CLI over SSH and show the session the way
a terminal would, or through a playbook. Every cell that changes a router says so.
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
    GREEN, RED, Check, ContainerLabs, _adhoc, _emit_text, _r2_legs, _topology_ok, compose,
    container_status, dexec, pkill_in, playbook, result, router_check,
)
from pqc_demo import IPsecAuthDemo, Result, _emit_cmd

TUNNEL0_IP = {"r1": "192.168.100.1", "r3": "192.168.100.2"}
_HANDLES = {"r1": lab.r1, "r2": lab.r2, "r3": lab.r3}


def _cli_lines(parts: tuple) -> list[str]:
    """Blocks or single lines in, CLI lines out. Blank lines and "! comments" are dropped."""
    lines = []
    for part in parts:
        for ln in (part if isinstance(part, (list, tuple)) else str(part).splitlines()):
            ln = re.sub(r"\s+!.*$", "", ln.rstrip())
            if ln.strip() and not ln.lstrip().startswith("!"):
                lines.append(ln)
    return lines


def _session(host: str, mode: str, parts: tuple, confirm: bool, show: bool) -> Result:
    lines = _cli_lines(parts)
    script = Path(__file__).resolve().parent / "router_cli.py"
    req = json.dumps({"host": host, "mode": mode, "lines": lines, "confirm": confirm})
    try:
        proc = subprocess.run([str(ANSIBLE_BIN / "python"), str(script), req],
                              capture_output=True, text=True, timeout=600)
        res = Result([host, mode, *lines], proc.returncode, proc.stdout, proc.stderr.strip()[-800:])
    except subprocess.TimeoutExpired:
        res = Result([host, mode, *lines], 1, "", "timed out after 600 s")
    if show:
        h = _HANDLES[host]
        if mode == "config":
            where, summary = "(over SSH, CONFIGURES the router)", f"configure terminal ({len(lines)} lines)"
        else:
            where, summary = "(over SSH)", " ; ".join(lines)
        _emit_cmd(h.label, h.accent, where, summary, res)
    return res


def configure(host: str, *lines, show: bool = True) -> Result:
    """Paste config on one router: configure terminal, your lines, end. Shows the session."""
    return _session(host, "config", lines, False, show)


def cli(host: str, *lines, confirm: bool = False, show: bool = True) -> Result:
    """Exec-mode commands on one router (show, ping, clear, ...). Shows the session.

    A line ending in " ?" shows the router's help for it. confirm=True answers "yes"
    when a command asks [yes/no].
    """
    return _session(host, "exec", lines, confirm, show)


def underlay() -> None:
    """What the router cells use as each router's underlay, and where it came from."""
    lab.underlay()
    if UNDERLAY_IP:
        print(f"\nTunnel0 runs R1 {UNDERLAY_IP['r1']} <-> R3 {UNDERLAY_IP['r3']}, "
              f"inside {TUNNEL0_IP['r1']} / {TUNNEL0_IP['r3']}.")


def rekey(rounds: int = 4, show: bool = True) -> bool:
    """R1: clear the IKEv2 SA, then ping Tunnel0 until the new SA answers.

    After the clear, IOS took 2 to 34 s to re-initiate in our runs, so one 20-ping run
    (40 s of 2 s timeouts) can end with no reply at all. Each extra round adds 20 pings.
    """
    cli("r1", "clear crypto ikev2 sa", show=show)
    return ping_tunnel(rounds, show)


def ping_tunnel(rounds: int = 4, show: bool = True) -> bool:
    """R1: ping R3's Tunnel0 address, 20 at a time, until it answers."""
    for _ in range(rounds):
        res = cli("r1", f"ping {TUNNEL0_IP['r3']} repeat 20", show=show)
        if re.search(_PING_OK, res.out):
            return True
    _emit_text(f"Tunnel0 didn't answer after {rounds * 20} pings. Check the SA with "
               "cli('r1', 'show crypto ikev2 sa detailed').")
    return False


# Demo 5: one EPC frame line, then its hex rows. Byte 0x3C (row 0030, 13th byte) is the
# IKE exchange type, byte 0x3D the flags (0x20 = response).
_EPC_FRAME = re.compile(r"^\s*\d+\s+(\d+)\s+[\d.]+\s+(\S+)\s+->\s+(\S+)", re.M)
_EPC_ROW30 = re.compile(r"^\s*0030:\s+((?:[0-9A-F]{8}\s){3}[0-9A-F]{8})", re.M)
IKE_EXCHANGES = {34: "IKE_SA_INIT", 43: "IKE_INTERMEDIATE", 35: "IKE_AUTH", 37: "INFORMATIONAL"}
DEMO5_BYTES = {34: (488, 555), 43: (1322, 1226), 35: (682, 346)}


def _epc_frames(text: str) -> list[tuple[int, int, bool]]:
    """(size, exchange type, is_response) per captured frame."""
    frames, starts = [], [m for m in _EPC_FRAME.finditer(text)]
    for i, m in enumerate(starts):
        body = text[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        row = _EPC_ROW30.search(body)
        if row:
            raw = bytes.fromhex(row.group(1).replace(" ", ""))
            frames.append((int(m.group(1)), raw[12], bool(raw[13] & 0x20)))
    return frames


def capture_handshake(show_buffer: bool = False) -> bool:
    """Demo 5: capture R1's rekey on R2 with EPC and weigh every IKE exchange.

    CONFIGURES R2 for the duration (a capture point named CAP) and removes it at the end.
    """
    ifc = next((i for i, _, peer in _r2_legs() if peer == "r1"), None)
    if ifc is None:
        _emit_text("No R2 interface toward R1 in your lab file; see underlay().")
        return False
    cli("r2", f"monitor capture CAP interface {ifc} both", "monitor capture CAP match ipv4 protocol udp any any",
        "monitor capture CAP buffer size 20", "monitor capture CAP start")
    try:
        up = rekey()
    finally:
        buf = cli("r2", "monitor capture CAP stop", "show monitor capture CAP buffer detailed",
                  "no monitor capture CAP", show=show_buffer)
    frames = _epc_frames(buf.out)
    sizes: dict[int, list[int | None]] = {}
    for size, xtype, response in frames:
        slot = sizes.setdefault(xtype, [None, None])
        if slot[int(response)] is None:
            slot[int(response)] = size
    rows = []
    for xtype, (req, resp) in DEMO5_BYTES.items():
        got = sizes.get(xtype, [None, None])
        measured = (f"{got[0]} + {got[1]} = {got[0] + got[1]:,} B" if None not in got else
                    "not captured")
        rows.append((f"R2  {IKE_EXCHANGES[xtype]} ({xtype})", f"{req} + {resp} = {req + resp:,} B", measured,
                     got == [req, resp]))
    seen = ", ".join(f"{IKE_EXCHANGES.get(x, x)} x{sum(1 for f in frames if f[1] == x)}"
                     for x in dict.fromkeys(f[1] for f in frames))
    ok = up and all(r[3] for r in rows)
    result("Demo 5: the handshake, weighed on R2", f"EPC on {ifc}, {len(frames)} frames: {seen or 'none'}",
           rows=rows, banner="Same bytes as our runs. ML-KEM is the IKE_INTERMEDIATE row." if ok else
           "Different from our runs. Rerun with capture_handshake(show_buffer=True) to see every frame.",
           banner_color=GREEN if ok else RED, accent=BLUE)
    return ok


def mldsa_config(h: str) -> list[str]:
    """Demo 7's config for one router (ipsec.md, Exercise 5), with this lab's underlay addresses."""
    peer = "r3" if h == "r1" else "r1"
    return [
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
    ]


def mldsa_snippets() -> None:
    """Demo 7's config for R1 and R3, printed, with this lab's underlay addresses."""
    if not _topology_ok():
        return
    for h in ("r1", "r3"):
        print(f"! ---- {h.upper()} ----")
        print("\n".join(mldsa_config(h)) + "\n")


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


def hub_spoke_check() -> bool:
    """Read-only: Act 3's hub and spoke is up, and still classical (Demo 8 adds ML-KEM).

    Tunnel numbers and addresses come from lab_topology, so this follows your lab file.
    """
    if not _topology_ok():
        return False
    topo, checks = lab.TOPOLOGY, []
    for h in ROUTERS:
        for p in topo[h].get("peers", []):
            if "tunnel_id" not in p:
                continue
            tun = f"Tunnel{p['tunnel_id']}"
            far = next(q for q in topo[p["name"]]["peers"] if q["name"] == h)
            checks += [
                Check(h, f"show interfaces {tun} | include line protocol", f"{tun} to {p['name'].upper()}",
                      "up, line protocol up", r"is up, line protocol is up"),
                Check(h, f"ping {far['tunnel_address']} source {tun}", f"ping {p['name'].upper()} over {tun}",
                      "pings succeed", _PING_OK),
            ]
    for p in topo["r2"].get("peers", []):
        checks.append(Check("r2", "show crypto ikev2 sa", f"IKEv2 SA with {p['name'].upper()}", "READY",
                            rf"{re.escape(p['local_address'])}/500\s+{re.escape(p['remote_address'])}/500\s+\S+\s+READY"))
    checks.append(Check("r2", "show crypto ikev2 sa detailed", "still classical (Demo 8 adds ML-KEM)", "absent",
                        r"PQC Key Exchange", present=False))
    return router_check("Act 3: hub and spoke", "R2 the hub, R1 and R3 the spokes, read-only", checks)


check = act2_check
setup = act2_setup
