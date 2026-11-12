"""Helpers for macsec_demo.ipynb, the runnable post-quantum MACsec walkthrough.

Everything generic (router handles, checks, playbooks, container labs) lives in
../common/lab.py. This file adds the MACsec parts: the container lab Act 1 uses,
check() / setup() for Act 2's starting state, the clock check EAP-TLS depends on,
the config you type in Act 2, and the proofs that the link really encrypts.

Router handles never send configuration. setup() is the one exception, and it
says so. mlkem_proof() and the clock fix run exec commands that change state
(trace levels, one re-authentication, the clock) and say so too.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor

from lab import *  # noqa: F401,F403  (r1/r2/r3, playbook, router_check, ... for the notebook)
from lab import (
    GREEN, ORANGE, RED, Check, ContainerLabs, MACSEC_CMD, MACSEC_IF, UNDERLAY_IP, _adhoc,
    _configure, _emit_text, _r2_legs, _topology_ok, compose, container_status, pkill_in,
    playbook, result, router_check,
)

PAIR = ("r1", "r2")
HAND_POLICY, HAND_KEYCHAIN = "MACSEC-POLICY", "MACSEC-KC"
# Global EAP-TLS knobs. Set on one end only, the two ends share no TLS group and
# EAP-TLS fails with "Cred Fail", so Act 3 starts from the default on both.
_PQ_LINES = "^access-session pqc-type|^access-session tls-version"


def _peer_ip(host: str) -> str:
    """The other end's underlay address on the MACsec wire."""
    if host == "r2":
        return UNDERLAY_IP.get("r1", "")
    return next((ip for _, ip, peer in _r2_legs() if peer == "r1"), "")


# --------------------------------------------------------------------------- #
# Act 1 container lab                                                          #
# --------------------------------------------------------------------------- #


def _panic() -> None:
    if container_status("macsec-authenticator") == "running":
        pkill_in("macsec-authenticator", "hostapd")
        pkill_in("macsec-authenticator", "tcpdump")
    if container_status("macsec-supplicant") == "running":
        pkill_in("macsec-supplicant", "wpa_supplicant")


labs = ContainerLabs(
    labs=("macsec",),
    containers=(("Demo 1", "macsec-authenticator"), ("Demo 1", "macsec-supplicant")),
    after_build=lambda: compose("macsec", "run", "--rm", "certgen", "ecdsa"),
    panic_steps=_panic,
    generated=tuple(f"learn/macsec/config/{f}" for f in (
        "authenticator/ca.crt", "authenticator/server.crt", "authenticator/server.key",
        "supplicant/ca.crt", "supplicant/client.crt", "supplicant/client.key")),
    restore=("learn/macsec/config",),
)


# --------------------------------------------------------------------------- #
# Clocks: EAP-TLS certificates are only as good as the clocks that check them  #
# --------------------------------------------------------------------------- #

_CLOCK = re.compile(r"(\*?)(\d{1,2}:\d{2}:\d{2})\.\d+ (\S+) \w{3} (\w{3}) (\d{1,2}) (\d{4})")
_TZ = re.compile(r"^clock timezone (\S+) (-?\d+)(?: (\d+))?", re.M)


def _read_clock(host: str) -> tuple[dt.datetime | None, bool, str]:
    """(time in UTC, authoritative?, raw "show clock") for one router."""
    ok, outs, err = _adhoc(host, ["show clock", "show running-config | include ^clock timezone"])
    if not ok:
        return None, False, err
    m = _CLOCK.search(outs[0])
    if not m:
        return None, False, outs[0].strip()
    star, hms, zone, mon, day, year = m.groups()
    local = dt.datetime.strptime(f"{hms} {mon} {day} {year}", "%H:%M:%S %b %d %Y")
    tz = _TZ.search(outs[1])
    offset = dt.timedelta()
    if tz and tz.group(1) == zone:
        hours = int(tz.group(2))
        offset = dt.timedelta(hours=hours, minutes=int(tz.group(3) or 0) * (1 if hours >= 0 else -1))
    return local - offset, not star, outs[0].strip()


def clocks(fix: bool = False, max_skew: int = 60) -> bool:
    """R1 and R2 clocks: both authoritative and within ``max_skew`` seconds of each other?

    ``fix=True`` CHANGES a clock: a router whose clock isn't authoritative gets
    ``clock set`` from the other one's time (an exec command, not config; it lasts
    until the router reloads). It never touches a router that runs NTP.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        reads = dict(zip(PAIR, pool.map(_read_clock, PAIR)))
    if fix:
        bad = [h for h in PAIR if reads[h][0] and not reads[h][1]]
        good = [h for h in PAIR if reads[h][0] and reads[h][1]]
        for h in bad:
            src = good[0] if good else None
            if src is None:
                _emit_text("Neither clock is authoritative. Set one by hand (clock set, or NTP), then rerun.")
                break
            utc_now, _, _ = _read_clock(src)
            ok, _, err = _adhoc(h, [utc_now.strftime("clock set %H:%M:%S %d %b %Y")])
            _emit_text(f"{h.upper()}: clock set from {src.upper()}" + ("" if ok else f"  FAILED: {err}"))
        if bad:
            with ThreadPoolExecutor(max_workers=2) as pool:
                reads = dict(zip(PAIR, pool.map(_read_clock, PAIR)))
    rows = []
    for h in PAIR:
        utc, auth, raw = reads[h]
        rows.append((f"{h.upper()}  show clock", "no leading *", raw, utc is not None and auth))
    times = [reads[h][0] for h in PAIR]
    skew = abs((times[0] - times[1]).total_seconds()) if all(times) else None
    rows.append(("R1 and R2 agree", f"within {max_skew} s", "(unreadable)" if skew is None else f"{skew:.0f} s apart",
                 skew is not None and skew <= max_skew))
    ok = all(r[3] for r in rows)
    result("Clocks on the MACsec pair", "EAP-TLS rejects a certificate the other side thinks isn't valid yet",
           rows=rows, banner="Clocks are good for EAP-TLS." if ok else "Fix the clocks before EAP-TLS (Act 3).",
           banner_color=GREEN if ok else ORANGE)
    return ok


# --------------------------------------------------------------------------- #
# Act 2 starting state                                                         #
# --------------------------------------------------------------------------- #


def check() -> bool:
    """Read-only: is the R1 to R2 link in Act 2's starting state? (About 20 seconds.)"""
    if not _topology_ok():
        return False
    checks = []
    for h in PAIR:
        ifc = MACSEC_IF[h]
        checks += [
            Check(h, f"show running-config interface {ifc}", f"{ifc}: no MACsec, MKA or dot1x",
                  "none", r"^\s*(macsec|mka |dot1x |access-session )", present=False),
            Check(h, "show mka sessions | include Total", "MKA sessions", "0",
                  r"Total MKA Sessions\.+\s*0\b"),
            Check(h, f"show running-config | include {_PQ_LINES}", "access-session pqc-type, tls-version",
                  "platform default (no line)", rf"^({_PQ_LINES})", present=False),
        ]
    checks.append(Check("r1", f"ping {_peer_ip('r1')} repeat 5", "R1 to R2 over the MACsec wire",
                        "Success rate is 100 percent", r"Success rate is (100|80) percent"))
    links = router_check("Act 2 starting state", "the R1 to R2 link, read-only", checks)
    return clocks() and links


def setup() -> bool:
    """CONFIGURES the R1 to R2 link back to Act 2's starting state, then checks it.

    Takes the hand-typed PSK from Act 2 off the interface and removes its
    MACSEC-POLICY / MACSEC-KC objects. Refuses to touch the automation's EAP-TLS
    config (that has its own teardown). Fixes a non-authoritative clock with
    ``clock set``. Then NETCONF on all three routers for Act 3.
    """
    if not _topology_ok():
        return False
    start = time.time()
    ok_all = True
    for h in PAIR:
        ifc, cmd = MACSEC_IF[h], MACSEC_CMD[h]
        ok, outs, err = _adhoc(h, [f"show running-config interface {ifc}",
                                   f"show running-config | include ^(key chain {HAND_KEYCHAIN}|mka policy {HAND_POLICY})",
                                   f"show running-config | include {_PQ_LINES}"])
        if not ok:
            _emit_text(f"{h.upper()}: can't read the config: {err}")
            ok_all = False
            continue
        intf, globals_, pq = outs
        if re.search(r"^\s*(dot1x |access-session )", intf, re.M):
            _emit_text(f"{h.upper()}: EAP-TLS is on {ifc}. Run the teardown in Clean up "
                       "(macsec-pq.yml, then macsec-eaptls.yml, both with -e state=absent), then rerun.")
            ok_all = False
            continue
        lines = [f"no mka pre-shared-key key-chain {HAND_KEYCHAIN}", f"no mka policy {HAND_POLICY}", f"no {cmd}"]
        if re.search(rf"^\s*mka pre-shared-key key-chain {HAND_KEYCHAIN}\b", intf, re.M):
            ok, _, err = _configure(h, lines, parents=[f"interface {ifc}"])
            _emit_text(f"{h.upper()}: hand PSK removed from {ifc}" + ("" if ok else f"  FAILED: {err}"))
            ok_all &= ok
        elif re.search(r"^\s*(macsec|mka )", intf, re.M):
            _emit_text(f"{h.upper()}: {ifc} has MACsec that isn't this demo's hand PSK. "
                       "If it's the automation's PSK, run macsec-psk.yml -e state=absent.")
            ok_all = False
            continue
        else:
            _emit_text(f"{h.upper()}: {ifc} already clean.")
        gone = [ln for ln in (f"no key chain {HAND_KEYCHAIN} macsec", f"no mka policy {HAND_POLICY}")
                if ln.split(" ", 1)[1].replace(" macsec", "") in globals_]
        gone += [f"no {ln.split()[0]} {ln.split()[1]}" for ln in pq.splitlines()
                 if ln.startswith("access-session ")]
        if gone:
            ok, _, err = _configure(h, gone)
            _emit_text(f"{h.upper()}: removed {', '.join(g[3:] for g in gone)}" + ("" if ok else f"  FAILED: {err}"))
            ok_all &= ok
    clocks(fix=True)
    if not ok_all:
        return False
    playbook("ansible-playbook bootstrap.yml", tail=8)
    _emit_text(f"Setup finished in {time.time() - start:.0f} s. Checking:")
    return check()


# --------------------------------------------------------------------------- #
# Act 2: the config you type, and the proof that it encrypts                   #
# --------------------------------------------------------------------------- #


def psk_config() -> None:
    """Print the exact Act 2 config for each end of your link (the CAK stays a placeholder)."""
    if not _topology_ok():
        return
    for h in PAIR:
        ifc, cmd = MACSEC_IF[h], MACSEC_CMD[h]
        note = "! routed port: plain macsec IS its peer-to-peer mode\n" if cmd == "macsec" else ""
        print(f"! ---- {h.upper()} ----\n"
              "conf t\n"
              f"mka policy {HAND_POLICY}\n"
              " macsec-cipher-suite gcm-aes-256\n"
              f"key chain {HAND_KEYCHAIN} macsec\n"
              " key 01\n"
              "  cryptographic-algorithm aes-256-cmac\n"
              "  key-string <your 64 hex characters, same on both routers>\n"
              "  lifetime local 00:00:00 Jan 1 2020 infinite\n"
              f"{note}"
              f"interface {ifc}\n"
              f" {cmd}\n"
              f" mka policy {HAND_POLICY}\n"
              f" mka pre-shared-key key-chain {HAND_KEYCHAIN}\n"
              "end\n")


def psk_teardown() -> None:
    """Print the exact Act 2 teardown for each end of your link."""
    if not _topology_ok():
        return
    for h in PAIR:
        print(f"! ---- {h.upper()} ----\n"
              "conf t\n"
              f"interface {MACSEC_IF[h]}\n"
              f" no mka pre-shared-key key-chain {HAND_KEYCHAIN}\n"
              f" no mka policy {HAND_POLICY}\n"
              f" no {MACSEC_CMD[h]}\n"
              "exit\n"
              f"no key chain {HAND_KEYCHAIN} macsec\n"
              f"no mka policy {HAND_POLICY}\n"
              "end\n")


def _counters(text: str) -> tuple[int, int, int]:
    def first(key: str) -> int:
        m = re.search(rf"{key}:\s+(\d+)", text)
        return int(m.group(1)) if m else -1
    return first("Out Pkts Encrypted"), first("In Pkts OK"), first("In Pkts Invalid")


def _state(host: str) -> dict:
    ifc = MACSEC_IF[host]
    ok, outs, err = _adhoc(host, ["show mka sessions", f"show macsec status interface {ifc}",
                                  f"show macsec statistics interface {ifc}"])
    if not ok:
        return {"err": err}
    secured = re.search(r"Secured Sessions\.+\s*(\d+)", outs[0])
    ckn = re.search(r"\sSecured\s+([0-9A-Fa-f]+)\s*$", outs[0], re.M)
    cipher = re.search(r"Cipher:\s+(\S+)", outs[1])
    return {"secured": int(secured.group(1)) if secured else 0, "ckn": ckn.group(1) if ckn else "",
            "cipher": cipher.group(1) if cipher else "(none)",
            "tx": bool(re.search(r"Transmitting:\s+TRUE", outs[1])), "counters": _counters(outs[2])}


def _wait_secured(timeout: int = 150) -> float | None:
    """Seconds until both ends report one secured MKA session, or None."""
    start = time.time()
    while time.time() - start < timeout:
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(_state, PAIR))
        if all(s.get("secured") == 1 for s in states):
            return time.time() - start
        time.sleep(5)
    return None


def proof(key_source: str = "psk", pings: int = 20) -> bool:
    """Read-only plus one ping: is the link secured, and does every ping get encrypted?

    Reads both ends' MACsec counters, sends ``pings`` 1400-byte pings from R1 to
    R2 over the wire, reads them again. ``key_source`` is "psk" (CKN 01) or "eap"
    (a 32-hex-character CKN derived from the EAP-TLS handshake).
    """
    if not _topology_ok():
        return False
    waited = _wait_secured()
    with ThreadPoolExecutor(max_workers=2) as pool:
        before = dict(zip(PAIR, pool.map(_state, PAIR)))
    ok_ping, outs, err = _adhoc("r1", [f"ping {_peer_ip('r1')} repeat {pings} size 1400"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        after = dict(zip(PAIR, pool.map(_state, PAIR)))
    rate = re.search(r"Success rate is (\d+) percent \((\d+)/(\d+)\)", outs[0] if ok_ping else "")
    sent_ok = int(rate.group(2)) if rate else 0
    want_ckn = "01" if key_source == "psk" else "32 hex characters"
    rows = [("MKA secured on both ends", "yes", "yes" if waited is not None else "no (gave up after 150 s)",
             waited is not None),
            (f"R1 ping {_peer_ip('r1')} x{pings}, 1400 B", f"{pings}/{pings}",
             f"{rate.group(2)}/{rate.group(3)}" if rate else (err or "(no reply)"), sent_ok >= pings - 1)]
    for h in PAIR:
        b, a = before[h], after[h]
        if "err" in a or "err" in b:
            rows.append((f"{h.upper()}  MACsec state", "readable", a.get("err") or b.get("err"), False))
            continue
        ckn_ok = a["ckn"] == "01" if key_source == "psk" else bool(re.fullmatch(r"[0-9A-Fa-f]{32}", a["ckn"]))
        d_out, d_in = a["counters"][0] - b["counters"][0], a["counters"][1] - b["counters"][1]
        rows += [(f"{h.upper()}  cipher", "GCM-AES-256", a["cipher"], a["cipher"] == "GCM-AES-256"),
                 (f"{h.upper()}  CKN (the key source)", want_ckn, a["ckn"] or "(none)", ckn_ok),
                 (f"{h.upper()}  encrypted out, during the pings", f"+{sent_ok} or a few more", f"+{d_out}",
                  sent_ok <= d_out <= sent_ok + 6),
                 (f"{h.upper()}  validated in, during the pings", f"+{sent_ok} or a few more", f"+{d_in}",
                  sent_ok <= d_in <= sent_ok + 6),
                 (f"{h.upper()}  invalid in (total)", "0", str(a["counters"][2]), a["counters"][2] == 0)]
    ok = all(r[3] for r in rows)
    result("MACsec on the R1 to R2 wire", f"{key_source.upper()} keyed, counters read before and after",
           rows=rows, banner=("Every ping was encrypted on the way out and validated on the way in."
                              if ok else "Not what we expected."),
           banner_color=GREEN if ok else RED)
    return ok


_TRACE_ON = ["set platform software trace smd R0 eap debug", "set platform software trace smd R0 eap-all debug"]
_TRACE_OFF = ["set platform software trace smd R0 eap notice", "set platform software trace smd R0 eap-all notice"]
_TLS_FACTS = "Using PQC type:|Negotiated Group|Negotiated TLS version|Selected ciphersuite"


def mlkem_proof(host: str = "r1", expect_group: str = "x448_mlkem768") -> bool:
    """Prove which key exchange the EAP-TLS handshake used, from the smd trace.

    CHANGES STATE for a few seconds: raises the smd EAP trace level, forces one fresh
    handshake with ``clear access-session interface`` (the link drops and re-secures,
    about 5 to 30 seconds), reads the trace, and puts the trace level back to notice.
    """
    if not _topology_ok():
        return False
    ifc = MACSEC_IF[host]
    before = _state(host).get("ckn", "")
    ok, _, err = _adhoc(host, _TRACE_ON)
    if not ok:
        _emit_text(f"{host.upper()}: couldn't raise the trace level: {err}")
        return False
    try:
        start = time.time()
        _adhoc(host, [f"clear access-session interface {ifc}"])
        time.sleep(5)
        waited = _wait_secured()
        ok, outs, err = _adhoc(host, [f"show logging process smd internal start last 2 minutes | include {_TLS_FACTS}"])
    finally:
        _adhoc(host, _TRACE_OFF)
    after = _state(host).get("ckn", "")
    text = outs[0] if ok else ""

    def last(key: str) -> str:
        hits = re.findall(rf"{key}\s*:?\s*(\S+)\s*$", text, re.M)
        return hits[-1] if hits else "(not in the trace)"

    group, pqc, tls = last(r"Negotiated Group \(key exchange algorithm\)"), last("Using PQC type"), last("Negotiated TLS version")
    rows = [("re-secured after the fresh handshake", "yes",
             f"yes, {time.time() - start:.0f} s" if waited is not None else "no", waited is not None),
            ("CKN changed (fresh MSK)", "yes", f"{before[:8]}... -> {after[:8]}..." if before and after else "(unread)",
             bool(before and after and before != after)),
            ("access-session pqc-type", "hybrid" if expect_group.startswith("x448") else "(any)", pqc, pqc != "(not in the trace)"),
            ("negotiated group", expect_group, group, group == expect_group),
            ("TLS version", "TLSv1.3", tls, tls == "TLSv1.3")]
    good = all(r[3] for r in rows)
    result(f"{host.upper()}: what the EAP-TLS handshake negotiated", "from the smd trace, trace level restored",
           rows=rows, banner=(f"ML-KEM was used: {group}." if good and "mlkem" in group else
                              "Classical key exchange." if good else "Not what we expected."),
           banner_color=GREEN if good and "mlkem" in group else (ORANGE if good else RED))
    return good
