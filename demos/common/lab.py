"""Shared runtime for the hardware demos in demos/ (ipsec, macsec, ssh, tls).

Builds on ../containers_demo/pqc_demo.py (same entity handles, same panels) and adds
what every hardware demo needs:

* ``r1`` / ``r2`` / ``r3``: callable router handles. ``r1("show crypto ikev2 sa")``
  runs ONE read-only exec command through Ansible's ``cisco.ios.ios_command``
  and shows the real device output. Credentials, management addresses and
  topology come from demos/common/lab.yml, key by key falling back to
  deploy/c8000/automation/group_vars/all/; nothing here prints the credentials.
* ``router_check(...)``: expected vs measured, many show commands, one panel.
* ``playbook("tls-pq.yml")``: runs a playbook from the automation folder with its
  own venv, times it, keeps the PLAY RECAP visible (the repo's pqc_lab callback
  hides it on success) and draws a per-router recap panel.
* ``ContainerLabs``: build, start, check and clean up the Act 1 container labs.
* Laptop probes (``ssh_kex_probe``, ``tls_probe``, ``laptop_tools``): what your
  laptop actually negotiates with a router, without logging in.

Router handles never send configuration. Every change in Act 2 is typed by you,
or made by a playbook you run on purpose.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pqc_demo import (  # noqa: F401  (re-exported for the notebooks)
    BLUE, CYAN, GREEN, MAGENTA, MIST, NAVY, ORANGE, RED, SLATE, LAB_DIRS,
    REPO_ROOT, Result, _emit_cmd, _emit_text, compose, container_status, dexec,
    lab_up, pkill_in, result, run_host, wait_healthy,
)

AUTOMATION_DIR = REPO_ROOT / "deploy" / "c8000" / "automation"
ANSIBLE_BIN = AUTOMATION_DIR / "venv" / "bin"
# The reader's one file (credentials, addresses, topology). Every playbook and
# router cell gets it as "-e @lab.yml", so each key in it beats the repo's
# group_vars; keys it leaves out come from the repo's files below.
LOCAL_LAB = Path(__file__).resolve().parent / "lab.yml"
REPO_LAB = AUTOMATION_DIR / "group_vars" / "all" / "lab.yml"
REPO_TOPOLOGY = AUTOMATION_DIR / "group_vars" / "all" / "topology.yml"
ROUTERS = ("r1", "r2", "r3")
PING_OK = r"Success rate is ([1-9]\d|100) percent"


def _yaml(path: Path, key: str) -> tuple[object, str]:
    """One top-level key of a YAML file, parsed by the automation venv (the notebook venv has no PyYAML)."""
    try:
        proc = subprocess.run(
            [str(ANSIBLE_BIN / "python"), "-c",
             "import json, sys, yaml; print(json.dumps((yaml.safe_load(open(sys.argv[1])) or {}).get(sys.argv[2])))",
             str(path), key], capture_output=True, text=True, timeout=30)
    except OSError:
        return None, f"no automation venv at {ANSIBLE_BIN.parent}; the router acts need it"
    if proc.returncode != 0:
        return None, f"can't read {key} from {path}: {proc.stderr.strip()[-300:]}"
    return json.loads(proc.stdout), ""


def _lab_var(key: str, repo_file: Path) -> tuple[object, Path | None, str]:
    """(value, file it came from, error): demos/common/lab.yml first, then ``repo_file``."""
    err = ""
    for path in (LOCAL_LAB, repo_file):
        if path.exists():
            value, err = _yaml(path, key)
            if value is not None:
                return value, path, ""
    return None, None, err


def _load_topology() -> tuple[dict, str, Path]:
    """(lab_topology, why not, the file it came from)."""
    topo, src, err = _lab_var("lab_topology", REPO_TOPOLOGY)
    src = src or REPO_TOPOLOGY
    if err or not isinstance(topo, dict):
        return {}, err or f"no lab_topology in {LOCAL_LAB.name} or {REPO_TOPOLOGY.name}", src
    missing = [h for h in ROUTERS if not {"underlay_interface", "underlay_address"} <= set(topo.get(h, {}))]
    if missing:
        return {}, f"{src.name}: no underlay_interface/underlay_address for {', '.join(missing)}", src
    return topo, "", src


TOPOLOGY, TOPOLOGY_ERROR, TOPOLOGY_FILE = _load_topology()
UNDERLAY_IF = {h: TOPOLOGY[h]["underlay_interface"] for h in ROUTERS if h in TOPOLOGY}
UNDERLAY_IP = {h: TOPOLOGY[h]["underlay_address"] for h in ROUTERS if h in TOPOLOGY}


def _topology_var(key: str) -> object:
    """A top-level topology variable: demos/common/lab.yml wins, the repo's topology.yml fills in."""
    return _lab_var(key, REPO_TOPOLOGY)[0]


def _local_lab_args() -> list[str]:
    """The "-e @lab.yml" every ansible / ansible-playbook call gets, when the file exists."""
    return ["-e", f"@{LOCAL_LAB}"] if LOCAL_LAB.exists() else []


MACSEC_IF: dict[str, str] = _topology_var("macsec_interface") or {} if not TOPOLOGY_ERROR else {}


def _macsec_ca_url() -> str:
    """The CA's SCEP URL. Derived from R2's address toward the other MACsec end, as topology.yml does."""
    url = _topology_var("macsec_ca_url")
    if isinstance(url, str) and url and "{{" not in url:
        return url
    client = next((h for h in MACSEC_IF if h != "r2"), "")
    ip = next((p["local_address"] for p in TOPOLOGY.get("r2", {}).get("peers", []) if p["name"] == client), "")
    return f"http://{ip}:80" if ip else ""


MACSEC_CA_URL: str = _macsec_ca_url() if not TOPOLOGY_ERROR else ""
_MACSEC_CMD_MAP: dict[str, str] = _topology_var("macsec_command") or {} if not TOPOLOGY_ERROR else {}
MACSEC_CMD: dict[str, str] = {h: _MACSEC_CMD_MAP.get(h, "macsec network-link") for h in MACSEC_IF}


def _r2_legs() -> list[tuple[str, str, str]]:
    """R2's (interface, address, peer) per spoke: the hub has one underlay leg per peer."""
    return [(p["source_interface"], p["local_address"], p["name"]) for p in TOPOLOGY.get("r2", {}).get("peers", [])]


def _topology_ok() -> bool:
    if TOPOLOGY_ERROR:
        _emit_text(f"Can't run the router cells yet: {TOPOLOGY_ERROR}")
    return not TOPOLOGY_ERROR


def underlay() -> None:
    """What the router cells use as each router's underlay, and where it came from."""
    if not _topology_ok():
        return
    print(f"From {TOPOLOGY_FILE.relative_to(REPO_ROOT)}:\n")
    print(f"  R1  {UNDERLAY_IF['r1']:<28} {UNDERLAY_IP['r1']}")
    for ifc, ip, peer in _r2_legs():
        print(f"  R2  {ifc:<28} {ip:<15} toward {peer.upper()}")
    print(f"  R3  {UNDERLAY_IF['r3']:<28} {UNDERLAY_IP['r3']}")
    if MACSEC_IF:
        print(f"\nMACsec link: R1 {MACSEC_IF.get('r1', '?')} <-> R2 {MACSEC_IF.get('r2', '?')}"
              + (f", CA at {MACSEC_CA_URL}" if MACSEC_CA_URL else ""))
        for h, ifc in MACSEC_IF.items():
            print(f"  {h.upper()}  interface {ifc}: {MACSEC_CMD[h]}")


_HOST_RE = re.compile(r"^(\S+) \| (SUCCESS|CHANGED|FAILED!|UNREACHABLE!)(?: \| rc=\d+)? => ", re.M)
_RECAP_RE = re.compile(
    r"^(\S+)\s+:\s+ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)", re.M)


def _ansible_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{ANSIBLE_BIN}{os.pathsep}{env.get('PATH', '')}"
    env["ANSIBLE_NOCOLOR"] = "1"
    env["ANSIBLE_FORCE_COLOR"] = "0"
    env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# Read-only router access                                                      #
# --------------------------------------------------------------------------- #


def _clean_err(msg: str) -> str:
    """Turn ios_command's b'...' error repr into the router's own '%' line."""
    text = msg
    if text[:2] in ('b"', "b'"):
        try:
            text = ast.literal_eval(text).decode(errors="replace")
        except (ValueError, SyntaxError):
            pass
    lines = [ln.strip() for ln in text.replace("\\r\\n", "\n").splitlines() if ln.strip()]
    pct = [ln for ln in lines if ln.startswith("%")]
    return (pct[0] if pct else (lines[-1] if lines else msg))[:160]


def _rejected(err: str) -> bool:
    return err.startswith("%")


def _adhoc(host: str, commands: list[str], timeout: int = 180) -> tuple[bool, list[str], str]:
    """Run exec commands on one router. Returns (ok, outputs, error)."""
    args = [
        str(ANSIBLE_BIN / "ansible"), host,
        "-c", "ansible.netcommon.network_cli",
        "-m", "cisco.ios.ios_command",
        "-a", json.dumps({"commands": commands}),
        *_local_lab_args(),
    ]
    try:
        proc = subprocess.run(args, cwd=AUTOMATION_DIR, env=_ansible_env(),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, [], f"timed out after {timeout}s"
    except FileNotFoundError:
        return False, [], f"no Ansible at {ANSIBLE_BIN}; see the automation README"
    m = _HOST_RE.search(proc.stdout)
    if not m:
        return False, [], (proc.stderr or proc.stdout).strip()[-400:] or f"rc={proc.returncode}"
    try:
        payload, _ = json.JSONDecoder().raw_decode(proc.stdout[m.end():])
    except ValueError:
        return False, [], proc.stdout[m.end():].strip()[:400]
    if m.group(2) not in ("SUCCESS", "CHANGED"):
        return False, [], _clean_err(str(payload.get("msg", payload)))
    return True, [str(s) for s in payload.get("stdout", [])], ""


class Router:
    """``r1("show crypto ikev2 sa")``: one read-only exec command, real output."""

    def __init__(self, name: str, accent: str) -> None:
        self.name = name
        self.label = name.upper()
        self.accent = accent

    def __call__(self, command: str, show: bool = True, timeout: int = 180) -> Result:
        if command.strip().lower().startswith(("conf", "config")):
            raise ValueError("router handles are read-only; type configuration in the router tab")
        ok, outs, err = _adhoc(self.name, [command], timeout=timeout)
        res = Result([self.name, command], 0 if ok else 1, outs[0] if outs else "", err)
        if show:
            _emit_cmd(self.label, self.accent, "(read-only, via Ansible)", command, res)
        return res


r1 = Router("r1", MAGENTA)
r2 = Router("r2", ORANGE)
r3 = Router("r3", BLUE)


@dataclass
class Check:
    """One expectation: ``pattern`` must (or must not) appear in ``command``'s output."""

    host: str
    command: str
    label: str
    expected: str
    pattern: str
    present: bool = True


def router_check(title: str, subtitle: str, checks: list[Check], banner: str | None = None) -> bool:
    """Run every check (one Ansible call per router, in parallel) and draw a panel."""
    by_host: dict[str, list[str]] = {}
    for c in checks:
        cmds = by_host.setdefault(c.host, [])
        if c.command not in cmds:
            cmds.append(c.command)
    with ThreadPoolExecutor(max_workers=len(by_host) or 1) as pool:
        futures = {h: pool.submit(_adhoc, h, cmds) for h, cmds in by_host.items()}
        batch = {h: f.result() for h, f in futures.items()}
    # (host, command) -> (ok, output, error)
    replies: dict[tuple[str, str], tuple[bool, str, str]] = {}
    retry = []
    for h, (ok, outs, err) in batch.items():
        if ok:
            replies.update({(h, cmd): (True, out, "") for cmd, out in zip(by_host[h], outs)})
        elif _rejected(err) and len(by_host[h]) > 1:
            retry += [(h, cmd) for cmd in by_host[h]]
        else:
            replies.update({(h, cmd): (False, "", f"unreachable: {err}") for cmd in by_host[h]})
    # One rejected command fails ios_command's whole batch, so rerun that router's
    # commands one at a time. Sequential per router: parallel logins to one box
    # get refused ("Authentication failed") once the vty limit is hit.
    def _one_by_one(h: str) -> None:
        for cmd in by_host[h]:
            ok, outs, err = _adhoc(h, [cmd])
            replies[(h, cmd)] = (ok, outs[0] if ok and outs else "",
                                 "" if ok else (f"rejected: {err}" if _rejected(err) else f"unreachable: {err}"))
    hosts_to_retry = sorted({h for h, _ in retry})
    if hosts_to_retry:
        _emit_text(f"a command was rejected on {', '.join(h.upper() for h in hosts_to_retry)}; "
                   "rechecking one command at a time…")
        with ThreadPoolExecutor(max_workers=len(hosts_to_retry)) as pool:
            list(pool.map(_one_by_one, hosts_to_retry))
    rows = []
    all_ok = True
    for c in checks:
        ok, out, err = replies[(c.host, c.command)]
        label = f"{c.host.upper()}  {c.label}"
        if not ok:
            rows.append((label, c.expected, err, False))
            all_ok = False
            continue
        hits = [ln.strip() for ln in out.splitlines() if re.search(c.pattern, ln, re.I)]
        passed = bool(hits) == c.present
        if hits:
            measured = hits[0] if len(hits) == 1 else f"{hits[0]}  (+{len(hits) - 1} more)"
        else:
            measured = "(not present)"
        rows.append((label, c.expected, measured, passed))
        all_ok &= passed
    result(title, subtitle, rows=rows,
           banner=banner or ("All checks pass." if all_ok else "Something isn't in the expected state. Fix it before you go on."),
           banner_color=GREEN if all_ok else RED, accent=BLUE)
    return all_ok


# --------------------------------------------------------------------------- #
# Playbooks                                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class PlayRun:
    command: str
    rc: int = -1
    text: str = ""
    elapsed: float = 0.0
    recap: dict[str, dict[str, int]] = field(default_factory=dict)
    _proc: subprocess.Popen | None = None
    _log: Path | None = None
    _start: float = 0.0

    @property
    def ok(self) -> bool:
        return self.rc == 0


def _split(command: str) -> list[str]:
    parts = command.split()
    if parts and parts[0] == "ansible-playbook":
        parts = parts[1:]
    return [str(ANSIBLE_BIN / "ansible-playbook"), *parts, *_local_lab_args()]


def _parse_recap(text: str) -> dict[str, dict[str, int]]:
    tail = text[text.rfind("PLAY RECAP"):] if "PLAY RECAP" in text else ""
    return {m[0]: {"ok": int(m[1]), "changed": int(m[2]), "unreachable": int(m[3]), "failed": int(m[4])}
            for m in _RECAP_RE.findall(tail)}


_TASK_LINE = re.compile(r"^TASK \[(.*?)\] \*+$")
_CHANGED_LINE = re.compile(r"^changed: \[(\S+?)\]")


def _report_changes(text: str) -> dict[str, int]:
    """Per host, how many "changed" came from verify.yml's report tasks.

    Every playbook here ends with verify.yml, and its "Verify | ... posture" report
    is forced to changed so it stays on screen. That isn't a config change.
    """
    counts: dict[str, int] = {}
    task = ""
    for ln in text.splitlines():
        m = _TASK_LINE.match(ln)
        if m:
            task = m[1]
            continue
        m = _CHANGED_LINE.match(ln)
        if m and re.search(r"Verify \| .*posture", task):
            counts[m[1]] = counts.get(m[1], 0) + 1
    return counts


def _show_run(run: PlayRun, tail: int, expect_changed: int | None, expect_refusal: bool) -> PlayRun:
    lines = run.text.rstrip().splitlines()
    body = "\n".join(lines[-tail:]) if len(lines) > tail else run.text
    if len(lines) > tail:
        body = f"… ({len(lines) - tail} earlier lines hidden; full log: run.text)\n" + body
    res = Result(run.command.split(), run.rc, body, "")
    note = ", a refusal is the expected result" if expect_refusal else ""
    _emit_cmd("Ansible", SLATE, "(deploy/c8000/automation)", f"{run.command}    # {run.elapsed:.0f} s{note}", res)
    rows = []
    report = _report_changes(run.text)
    for host in ROUTERS:
        st = run.recap.get(host)
        if st is None:
            continue
        config = st["changed"] - report.get(host, 0)
        shown = str(config) + (f"  (recap {st['changed']}, incl. verify's report)" if report.get(host) else "")
        if expect_refusal:
            rows.append((f"{host}  config changes", "0", shown, config == 0))
        elif expect_changed is not None:
            rows.append((f"{host}  config changes", str(expect_changed), shown,
                         config == expect_changed and st["failed"] == 0 and st["unreachable"] == 0))
        else:
            clean = st["failed"] == 0 and st["unreachable"] == 0
            problems = ", ".join(p for p in (
                f"{st['failed']} task(s) failed" if st["failed"] else "",
                "unreachable" if st["unreachable"] else "") if p)
            rows.append((f"{host}  playbook ran", "without errors",
                         "without errors" if clean else problems, clean))
    if expect_refusal:
        refused = run.rc != 0 and "Nothing has been changed" in run.text
        rows.append(("refused, nothing changed", "yes", "yes" if refused else "no", refused))
        good = refused and all(r[3] for r in rows)
    else:
        good = run.ok and all(r[3] for r in rows)
    result(run.command, f"rc={run.rc}, {run.elapsed:.0f} s", rows=rows,
           banner=("Refused, and said why." if expect_refusal else "Done.") if good
           else "Not what we expected. Read the output above; deploy/c8000/automation/captured/ has a good run to compare.",
           banner_color=GREEN if good else RED, accent=ORANGE)
    return run


def playbook(command: str, *, expect_changed: int | None = None, expect_refusal: bool = False,
             tail: int = 45, timeout: int = 1200) -> PlayRun:
    """Run ``ansible-playbook …`` from the automation folder and show the outcome."""
    run = PlayRun(command if command.startswith("ansible-playbook") else f"ansible-playbook {command}")
    start = time.time()
    try:
        proc = subprocess.run(_split(run.command), cwd=AUTOMATION_DIR, capture_output=True, text=True,
                              timeout=timeout, env=_ansible_env(ANSIBLE_STDOUT_CALLBACK="default"))
        run.rc, run.text = proc.returncode, proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else "")
    except subprocess.TimeoutExpired as exc:
        run.rc, run.text = 124, (exc.stdout or "") + f"\ntimed out after {timeout}s"
    run.elapsed = time.time() - start
    run.recap = _parse_recap(run.text)
    return _show_run(run, tail, expect_changed, expect_refusal)


def playbook_bg(command: str) -> PlayRun:
    """Start a playbook in the background (keeps the kernel free). Pair with playbook_wait()."""
    run = PlayRun(command if command.startswith("ansible-playbook") else f"ansible-playbook {command}")
    yml = next((t for t in run.command.split() if t.endswith(".yml")), "playbook")
    name = re.sub(r"\W+", "_", yml)
    run._log = Path(f"/tmp/pqc_demos_{name}.log")
    log = open(run._log, "w")
    run._start = time.time()
    run._proc = subprocess.Popen(_split(run.command), cwd=AUTOMATION_DIR, stdout=log, stderr=subprocess.STDOUT,
                                 text=True, env=_ansible_env(ANSIBLE_STDOUT_CALLBACK="default"))
    _emit_text(f"started in the background: {run.command}\n  live log: tail -f {run._log}")
    return run


def playbook_wait(run: PlayRun, *, expect_changed: int | None = None, tail: int = 45,
                  timeout: int = 1200) -> PlayRun:
    """Wait for a playbook_bg() run, then show it like playbook() would."""
    assert run._proc is not None and run._log is not None, "pass the object playbook_bg() returned"
    if run._proc.poll() is None:
        _emit_text(f"still running ({time.time() - run._start:.0f} s so far), waiting…")
    try:
        run.rc = run._proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        run.rc = 124
    # The wait cell may run long after the playbook ended, so stop the clock at its last log write.
    run.elapsed = min(time.time(), run._log.stat().st_mtime) - run._start
    run.text = run._log.read_text()
    run.recap = _parse_recap(run.text)
    return _show_run(run, tail, expect_changed, False)


def _configure(host: str, lines: list[str], parents: list[str] | None = None,
               timeout: int = 180) -> tuple[bool, bool, str]:
    """Push config lines with ios_config. Only the demos' setup() helpers use this.

    Returns (ok, changed, error).
    """
    args_ = {"lines": lines, **({"parents": parents} if parents else {})}
    args = [str(ANSIBLE_BIN / "ansible"), host, "-c", "ansible.netcommon.network_cli",
            "-m", "cisco.ios.ios_config", "-a", json.dumps(args_)]
    try:
        proc = subprocess.run(args, cwd=AUTOMATION_DIR, env=_ansible_env(),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, False, f"timed out after {timeout}s"
    m = _HOST_RE.search(proc.stdout)
    if not m:
        return False, False, (proc.stderr or proc.stdout).strip()[-400:]
    try:
        payload, _ = json.JSONDecoder().raw_decode(proc.stdout[m.end():])
    except ValueError:
        return False, False, proc.stdout[m.end():].strip()[:400]
    if m.group(2) == "FAILED!" or m.group(2) == "UNREACHABLE!":
        return False, False, _clean_err(str(payload.get("msg", payload)))
    return True, bool(payload.get("changed")), ""


def running_lines(host: str, pattern: str) -> list[str]:
    """Lines of the running config matching ``pattern`` (an IOS include regex), for snapshots."""
    ok, outs, err = _adhoc(host, [f"show running-config | include {pattern}"])
    if not ok:
        raise RuntimeError(f"{host}: {err}")
    return [ln.rstrip() for ln in outs[0].splitlines() if ln.strip() and not ln.startswith("Building")]


# --------------------------------------------------------------------------- #
# Act 1 container labs                                                         #
# --------------------------------------------------------------------------- #


def automation_rows() -> list[tuple[str, str, str, bool]]:
    """Readiness rows for the router acts: Ansible venv, lab.yml, topology."""
    have_ansible = (ANSIBLE_BIN / "ansible-playbook").exists()
    rows = [("Act 2/3  Ansible venv", "present", "present" if have_ansible else "missing", have_ansible)]
    for key in ("lab_mgmt", "lab_user", "lab_password"):
        value, src, _ = _lab_var(key, REPO_LAB)
        rows.append((f"Act 2/3  {key}", "in demos/common/lab.yml or group_vars",
                     str(src.relative_to(REPO_ROOT)) if src else "missing (cp demos/common/lab.example.yml lab.yml)",
                     value is not None))
    return rows + [
        ("Act 2/3  underlay topology", "readable",
         TOPOLOGY_ERROR or str(TOPOLOGY_FILE.relative_to(REPO_ROOT)), not TOPOLOGY_ERROR),
    ]


@dataclass
class ContainerLabs:
    """The container labs one demo's Act 1 needs: build, up, status, panic, cleanup."""

    labs: tuple[str, ...]
    containers: tuple[tuple[str, str], ...]          # (demo label, container name)
    healthy: tuple[str, ...] = ()                    # wait for these after "up"
    after_build: Callable[[], None] | None = None    # keygen/certgen, extra images
    panic_steps: Callable[[], None] | None = None    # stop stray daemons, keep stacks up
    down_files: dict[str, list[str]] = field(default_factory=dict)
    generated: tuple[str, ...] = ()                  # repo-relative paths the labs create
    restore: tuple[str, ...] = ()                    # repo-relative dirs to git restore
    extra_rows: Callable[[], list[tuple[str, str, str, bool]]] | None = None

    def build(self) -> None:
        """Build (and warm) this demo's images. The first run compiles from source."""
        for lab in self.labs:
            _emit_text(f"  building {lab}…")
            res = compose(lab, "build")
            _emit_text("    ok" if res.ok else f"    FAILED\n{res.text[-800:]}")
        if self.after_build:
            self.after_build()
        _emit_text("Build complete.")

    def up(self) -> bool:
        """Start this demo's stacks and show the readiness board."""
        for lab in self.labs:
            res = lab_up(lab)
            if not res.ok:
                _emit_text(f"[{lab}] up failed:\n{res.text.strip()[-600:]}")
        for name in self.healthy:
            wait_healthy(name)
        return self.status()

    def status(self) -> bool:
        """Containers for Act 1, plus what the router acts need on this laptop."""
        rows = []
        for demo, name in self.containers:
            st = container_status(name)
            rows.append((f"{demo}  {name}", "running", st, st == "running"))
        rows += automation_rows()
        if self.extra_rows:
            rows += self.extra_rows()
        ok = all(r[3] for r in rows)
        result("Laptop readiness", "containers for Act 1, automation for Acts 2 and 3", rows=rows,
               banner="Laptop is ready." if ok else "Not ready yet.", banner_color=GREEN if ok else RED)
        return ok

    def panic(self) -> None:
        """Stop stray daemons and captures in this demo's containers; leave the stacks up."""
        if self.panic_steps:
            self.panic_steps()
        _emit_text("Panic reset done: stray processes stopped, stacks still up.")

    def cleanup(self) -> None:
        """Containers down, generated keys and certs removed, lab configs back to the committed state."""
        for lab in self.labs:
            compose(lab, "down", files=self.down_files.get(lab))
        for rel in self.restore:
            run_host(["git", "restore", rel], cwd=REPO_ROOT)
        for rel in self.generated:
            run_host(["rm", "-rf", str(REPO_ROOT / rel)])
        dirs = sorted({LAB_DIRS[lab] for lab in self.labs})
        configs = [str(REPO_ROOT / d / "config") for d in dirs if (REPO_ROOT / d / "config").is_dir()]
        if configs:
            run_host(["find", *configs, "-mindepth", "1", "-type", "d", "-empty", "-delete"])
        status = run_host(["git", "status", "--short", *dirs], cwd=REPO_ROOT)
        _emit_text(f"Cleanup complete. {', '.join(dirs)} clean." if not status.out.strip()
                   else f"Cleanup done, but these still differ:\n{status.out}")


# --------------------------------------------------------------------------- #
# Laptop probes: what YOUR client negotiates with a router                     #
# --------------------------------------------------------------------------- #


def _mgmt(host: str) -> str:
    """Management address from lab.yml (only lab_mgmt is read; credentials stay put)."""
    mgmt, _, err = _lab_var("lab_mgmt", REPO_LAB)
    if err or not isinstance(mgmt, dict) or host not in mgmt:
        raise RuntimeError(err or f"no lab_mgmt.{host} in demos/common/lab.yml or group_vars/all/lab.yml")
    return str(mgmt[host])


def _version(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except OSError:
        return ""
    return (proc.stdout + proc.stderr).strip().splitlines()[0] if (proc.stdout + proc.stderr).strip() else ""


def laptop_tools() -> list[tuple[str, str, str, bool]]:
    """Rows: does this laptop's OpenSSH offer ML-KEM, and is its OpenSSL 3.5 or newer?"""
    rows = []
    ssh_kex = subprocess.run(["ssh", "-Q", "kex"], capture_output=True, text=True).stdout \
        if shutil.which("ssh") else ""
    has_mlkem = "mlkem768x25519-sha256" in ssh_kex
    rows.append(("laptop  ssh -Q kex", "mlkem768x25519-sha256",
                 "offered" if has_mlkem else (_version(["ssh", "-V"]) or "no ssh") + " (no ML-KEM)", has_mlkem))
    ver = _version(["openssl", "version"])
    m = re.search(r"OpenSSL (\d+)\.(\d+)", ver)
    new_enough = bool(m) and (int(m[1]), int(m[2])) >= (3, 5)
    rows.append(("laptop  openssl version", "OpenSSL 3.5 or newer", ver or "no openssl", new_enough))
    return rows


_KEX_LINE = re.compile(r"debug1: kex: algorithm: (\S+)")
_HOSTKEY_LINE = re.compile(r"debug1: kex: host key algorithm: (\S+)")
_PROPOSAL = re.compile(r"(local client|peer server) KEXINIT proposal\s*\ndebug2: KEX algorithms: (\S+)")
_KEX_MARKERS = ("ext-info-", "kex-strict-")


def ssh_kex_probe(host: str = "r1", kex: str | None = None, clean_config: bool = False,
                  expect: str | None = "mlkem768x25519-sha256", expect_offer: bool | None = None,
                  show: bool = True) -> dict:
    """Open an SSH connection from this laptop, stop after key exchange, report what was agreed.

    ``-o PreferredAuthentications=none`` with ``BatchMode`` means no password is
    ever sent: the client finishes the key exchange, asks for "none", is refused
    and exits. ``kex`` pins the client's offer (``-o KexAlgorithms=...``).
    ``clean_config`` adds ``-F none``: OpenSSH's built-in defaults, ignoring
    ~/.ssh/config and the system-wide ssh_config (and any vendor crypto policy).
    ``expect`` is the KEX you should end up with (None: whatever your client picks);
    ``expect_offer`` is whether the router should offer ML-KEM at all.
    """
    if expect_offer is None and expect == "mlkem768x25519-sha256":
        expect_offer = True
    args = ["ssh", "-vv", "-o", "BatchMode=yes", "-o", "PreferredAuthentications=none",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10"]
    if clean_config:
        args[1:1] = ["-F", "none"]
    if kex:
        args += ["-o", f"KexAlgorithms={kex}"]
    shown = ("ssh -vv" + (" -F none" if clean_config else "") + " -o BatchMode=yes -o PreferredAuthentications=none"
             + (f" -o KexAlgorithms={kex}" if kex else "") + f" <{host.upper()} mgmt>")
    try:
        proc = subprocess.run([*args, "-l", "kexprobe", _mgmt(host)], capture_output=True, text=True, timeout=30)
        text = proc.stderr
    except subprocess.TimeoutExpired:
        text = "timed out after 30 s"
    offers: dict[str, list[str]] = {}
    for side, algs in _PROPOSAL.findall(text):
        offers.setdefault(side, [a for a in algs.split(",") if not a.startswith(_KEX_MARKERS)])
    agreed = _KEX_LINE.search(text)
    hostkey = _HOSTKEY_LINE.search(text)
    # A failed negotiation logs "kex: algorithm: (no match)".
    agreed = agreed if agreed and not agreed[1].startswith("(") else None
    out = {"kex": agreed[1] if agreed else "", "hostkey": hostkey[1] if hostkey else "",
           "client_offers": offers.get("local client", []), "server_offers": offers.get("peer server", [])}
    lines = [f"client offers:  {', '.join(out['client_offers']) or '?'}",
             f"server offers:  {', '.join(out['server_offers']) or '?'}",
             f"agreed KEX:     {out['kex'] or '(none)'}",
             f"host key:       {out['hostkey'] or '?'}"]
    lines += [ln.strip() for ln in text.splitlines()
              if re.search(r"no matching key exchange|Permission denied|Connection (refused|timed out)|timed out", ln)]
    if not show:
        return out
    _emit_cmd("laptop", CYAN, "(your OpenSSH client, no login)", shown, Result(args, 0, "\n".join(lines), ""))
    server_pq = "mlkem768x25519-sha256" in out["server_offers"]
    rows = [("negotiated KEX", expect or "(any)", out["kex"] or "(handshake failed)",
             bool(out["kex"]) and (expect is None or out["kex"] == expect)),
            ("router offers mlkem768x25519-sha256", "(either)" if expect_offer is None else
             ("yes" if expect_offer else "no"), "yes" if server_pq else "no",
             expect_offer is None or server_pq == expect_offer),
            ("client's first choice", "(your config)", out["client_offers"][0] if out["client_offers"] else "?", True),
            ("host key algorithm", "rsa-sha2-512 (still classical)", out["hostkey"] or "?", bool(out["hostkey"]))]
    result(f"SSH key exchange with {host.upper()}", "your laptop -> IOS XE SSH server, stopped before login",
           rows=rows, accent=CYAN)
    return out


_MSG_LINE = re.compile(r"^\s{4}(\w+), Length=(\d+)")
_KEY_SHARE_EXT = re.compile(r"extension_type=key_share\(51\), length=(\d+)")
_GROUP_LINE = re.compile(r"NamedGroup: (\S+)")


def _hello_shares(trace: str) -> list[tuple[str, int, list[str]]]:
    """(message, key_share extension bytes, groups in it) for every ClientHello/ServerHello."""
    hellos: list[tuple[str, int, list[str]]] = []
    msg, in_share = "", False
    for ln in trace.splitlines():
        m = _MSG_LINE.match(ln)
        if m:
            msg, in_share = m[1], False
            continue
        if msg not in ("ClientHello", "ServerHello"):
            continue
        ks = _KEY_SHARE_EXT.search(ln)
        if ks:
            hellos.append((msg, int(ks[1]), []))
            in_share = True
        elif in_share and "extension_type=" in ln:
            in_share = False
        elif in_share and _GROUP_LINE.search(ln):
            hellos[-1][2].append(_GROUP_LINE.search(ln)[1])
    return hellos


def tls_probe(host: str = "r1", groups: str | None = None, trace: bool = False,
              expect: str | None = "X25519MLKEM768", show: bool = True) -> dict:
    """``openssl s_client`` from this laptop to the router's HTTPS server: which group won?

    ``groups`` pins the client's offer (``-groups``). ``expect="refused"`` passes
    when the server turns the client away. ``trace`` reads the
    key_share sizes off each hello and spots a HelloRetryRequest (a ServerHello
    whose key_share names a group but carries no key: 2 bytes).
    """
    base = ["openssl", "s_client", "-connect", f"{_mgmt(host)}:443", "-tls1_3"]
    flags = ["-trace"] if trace else ["-brief"]
    if groups:
        flags += ["-groups", groups]
    shown = f"openssl s_client -connect <{host.upper()} mgmt>:443 -tls1_3 " + " ".join(flags)
    try:
        proc = subprocess.run([*base, *flags], input="", capture_output=True, text=True, timeout=30)
        text = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        text = "timed out after 30 s"
    except FileNotFoundError:
        text = "no openssl on PATH"
    group = re.search(r"Negotiated TLS1\.3 group: (\S+)", text)
    temp = re.search(r"(?:Peer|Server) Temp Key: ([^\n]+)", text)
    name = group[1] if group and group[1] != "<NULL>" else ""
    if not name and temp:
        named = re.search(r"(X25519MLKEM768|SecP\w+MLKEM\w+|secp\d+r1|X25519|X448)", temp[1], re.I)
        name = named[1] if named else temp[1].strip()
    hellos = _hello_shares(text) if trace else []
    hrr = any(msg == "ServerHello" and size == 2 for msg, size, _ in hellos)
    out = {"group": name, "hrr": hrr, "hellos": hellos}
    if trace:
        lines = [f"{msg:<12} key_share {size:>5} B  {', '.join(g) or '?'}"
                 + ("   <- HelloRetryRequest: 'try again with this group'" if msg == "ServerHello" and size == 2 else "")
                 for msg, size, g in hellos]
        lines.append(f"agreed group: {name or '(handshake failed)'}")
    else:
        keep = r"Protocol version|Ciphersuite|Negotiated TLS1\.3 group|Peer Temp Key|Server Temp Key|Signature type"
        lines = [ln.strip() for ln in text.splitlines() if re.search(keep, ln)]
    lines += [ln.strip() for ln in text.splitlines() if re.search(r"errno|handshake failure|timed out|no openssl", ln)]
    if not show:
        return out
    _emit_cmd("laptop", CYAN, "(your OpenSSL client)", shown, Result(base, 0, "\n".join(lines), ""))
    if expect == "refused":
        rows = [("handshake", "refused", f"completed ({name})" if name else "refused", not name)]
    else:
        rows = [("negotiated group", expect or "(any)", name or "(handshake failed)",
                 bool(name) and (expect is None or name.lower() == expect.lower()))]
    if trace:
        first = next((size for msg, size, _ in hellos if msg == "ClientHello"), 0)
        rows.append(("ClientHello key_share", "bytes on the wire", f"{first} B" if first else "?", bool(first)))
        rows.append(("HelloRetryRequest", "yes if the router refused every offered share",
                     "yes" if hrr else "no", True))
    result(f"TLS 1.3 handshake with {host.upper()}:443", "your laptop -> IOS XE HTTPS server",
           rows=rows, accent=CYAN)
    return out
