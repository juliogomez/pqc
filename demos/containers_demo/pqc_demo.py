#!/usr/bin/env python3
"""Presenter harness for the PQC "across your infrastructure" live demos.

This module powers ``PQC_DEMOS.ipynb``. It orchestrates the existing Docker
Compose labs (it never replaces the in-container tooling) and renders each
protocol as a clear two-entity exchange for the audience: two endpoint cards, a
message ladder with the current step highlighted, and a curated evidence table
that compares the value the room should see against what was actually measured
on the wire.

Design notes
------------
* No hard dependency on IPython/ipywidgets. When imported inside Jupyter it
  renders rich inline SVG/HTML; when imported by plain ``python3`` it degrades
  to readable text, so the logic stays testable outside a kernel.
* Every shell call uses an argument list (no ``shell=True``); the only place a
  string is handed to ``bash -lc`` is a fixed lab command running *inside* a
  known, allow-listed container.
* Long-lived captures/daemons (tcpdump, hostapd, sshd, s_server) are tracked by
  PID files written inside the container, so start/stop survives across separate
  notebook cells and kernel restarts. This replaces the runbook's reliance on a
  single interactive shell holding ``$!``.
* Generated certs/keys are lab-only and clearly labelled as such; private key
  material and derived session secrets are never printed.

The authoritative command source is each lab's README under ``learn/``.
"""

from __future__ import annotations

import html
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Repo layout                                                                  #
# --------------------------------------------------------------------------- #


def _find_repo_root(start: Path) -> Path:
    """Walk upwards until we find the directory holding all four lab trees.

    The lab trees live under ``learn/`` (see the Stage 1/Stage 2 restructure),
    not at the repo root.
    """
    for candidate in [start, *start.parents]:
        if all((candidate / "learn" / lab).is_dir() for lab in ("macsec", "ipsec", "ssh", "tls")):
            return candidate
    # Fallback: two levels up (…/demos/containers_demo/ -> repo root).
    return start.parent.parent


REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)

LAB_DIRS = {
    "tls": "learn/tls/key-exchange",
    "ssh": "learn/ssh",
    "macsec": "learn/macsec",
    "ipsec": "learn/ipsec/key-exchange",
    # Extended labs (run on demand, not part of the core stage flow).
    "tls-auth": "learn/tls/authentication",
    "ipsec-auth": "learn/ipsec/authentication",
}

# Only these containers may ever be targeted by an exec.
ALLOWED_CONTAINERS = {
    "tls-server",
    "tls-client",
    "ssh-server",
    "ssh-client",
    "macsec-authenticator",
    "macsec-supplicant",
    "ike-initiator",
    "ike-responder",
    # Extended labs
    "tls-auth-server",
    "tls-auth-client",
    "ike-auth-initiator",
    "ike-auth-responder",
}

FALLBACK_DIR = Path(__file__).resolve().parent / "demo-fallback"

# --------------------------------------------------------------------------- #
# Palette (Cisco brand colours, same as the slide deck)                        #
# --------------------------------------------------------------------------- #

NAVY = "#07182D"
CYAN = "#02C8FF"
BLUE = "#0A60FF"
MAGENTA = "#FF007F"
ORANGE = "#FF9000"
SLATE = "#525E6C"
MIST = "#B4B9C0"
CARD = "#F2F4F7"
GREEN = "#12B76A"
RED = "#E5484D"

# --------------------------------------------------------------------------- #
# Modes                                                                        #
# --------------------------------------------------------------------------- #

PRESENTER_MODE = True
_DEFAULT_TIMEOUT = 150


def set_presenter_mode(on: bool = True) -> None:
    """Presenter mode hides raw command noise; diagnostic mode shows everything."""
    global PRESENTER_MODE
    PRESENTER_MODE = bool(on)
    _emit_text(f"[mode] presenter_mode = {PRESENTER_MODE}")


# --------------------------------------------------------------------------- #
# Display plumbing (IPython optional)                                          #
# --------------------------------------------------------------------------- #


def _ipython_display():
    """Return IPython's (display, HTML) if we're in a notebook, else None."""
    try:
        from IPython import get_ipython  # type: ignore

        if get_ipython() is None:
            return None
        from IPython.display import HTML, display  # type: ignore

        return display, HTML
    except Exception:
        return None


_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(markup: str) -> str:
    text = markup.replace("</tr>", "\n").replace("</div>", "\n").replace("<br/>", "\n")
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def show_html(markup: str, text_fallback: str | None = None) -> None:
    """Render HTML in Jupyter, or a readable text version everywhere else."""
    disp = _ipython_display()
    if disp is not None:
        display, HTML = disp
        display(HTML(markup))
    else:
        _emit_text(text_fallback if text_fallback is not None else _html_to_text(markup))


def _emit_text(text: str) -> None:
    print(text)


# --------------------------------------------------------------------------- #
# Shell helpers                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class Result:
    args: list[str]
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def text(self) -> str:
        return (self.out + ("\n" + self.err if self.err else "")).strip()


def run_host(args: list[str], cwd: Path | None = None, timeout: int = _DEFAULT_TIMEOUT) -> Result:
    """Run a command on the host with an explicit argument list (no shell)."""
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        result = Result(args, proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as exc:
        result = Result(args, 124, exc.stdout or "", f"timed out after {timeout}s")
    _trace(result)
    return result


def compose(lab: str, *args: str, files: list[str] | None = None, timeout: int = 900) -> Result:
    """Run ``docker compose <args>`` in the given lab directory.

    ``files`` overrides the compose file set (e.g. the IPsec-auth ML-DSA
    override ``["docker-compose.yml", "docker-compose.mldsa.yml"]``).
    """
    if lab not in LAB_DIRS:
        raise ValueError(f"unknown lab {lab!r}; expected one of {sorted(LAB_DIRS)}")
    cwd = REPO_ROOT / LAB_DIRS[lab]
    file_args: list[str] = []
    for f in files or []:
        file_args += ["-f", f]
    return run_host(["docker", "compose", *file_args, *args], cwd=cwd, timeout=timeout)


def lab_up(lab: str, *extra: str, files: list[str] | None = None, build: bool = False) -> Result:
    """Bring a lab up, healing the recurring Docker subnet-overlap failure.

    Several labs pin their subnet; stale orphaned networks from earlier runs (or
    the unpinned MACsec network) can squat those ranges. If ``up`` fails with a
    pool-overlap error we prune unused networks and retry once.
    """
    args = ["up", "-d"] + (["--build"] if build else []) + list(extra)
    res = compose(lab, *args, files=files)
    if not res.ok and "overlap" in res.text.lower():
        _emit_text(f"[{lab}] subnet overlap; pruning orphaned networks and retrying…")
        run_host(["docker", "network", "prune", "-f"], timeout=60)
        res = compose(lab, *args, files=files)
    return res


def _require_container(name: str) -> None:
    if name not in ALLOWED_CONTAINERS:
        raise ValueError(f"container {name!r} is not in the allow-list")


def dexec(container: str, script: str, timeout: int = _DEFAULT_TIMEOUT) -> Result:
    """Run a fixed lab command inside a known container (non-interactive)."""
    _require_container(container)
    return run_host(
        ["docker", "exec", container, "bash", "-lc", script],
        timeout=timeout,
    )


def dexec_bg(container: str, script: str, pidfile: str, logfile: str) -> Result:
    """Start a long-lived process inside a container, tracked by a PID file.

    Survives across notebook cells: the transient exec shell exits immediately
    while the disowned child keeps running under the container's init.
    """
    _require_container(container)
    wrapped = (
        f"rm -f {pidfile}; "
        f"nohup {script} > {logfile} 2>&1 & "
        f"echo $! > {pidfile}; disown || true; "
        f"sleep 0.3; cat {pidfile}"
    )
    return dexec(container, wrapped, timeout=30)


def stop_bg(container: str, pidfile: str) -> Result:
    """Stop a process previously started with :func:`dexec_bg`."""
    _require_container(container)
    script = (
        f"if [ -f {pidfile} ]; then kill $(cat {pidfile}) 2>/dev/null || true; "
        f"rm -f {pidfile}; fi; true"
    )
    return dexec(container, script, timeout=30)


def pkill_in(container: str, pattern: str) -> Result:
    _require_container(container)
    return dexec(container, f"pkill -f {pattern!r} 2>/dev/null || true; true", timeout=30)


def container_status(name: str) -> str:
    """Return the container state string, or ``absent`` / ``error``."""
    res = run_host(
        ["docker", "inspect", "-f", "{{.State.Status}}", name], timeout=20
    )
    if not res.ok:
        return "absent"
    return res.out.strip() or "error"


def wait_healthy(name: str, target: str = "running", tries: int = 20, delay: float = 1.0) -> bool:
    for _ in range(tries):
        if container_status(name) == target:
            return True
        time.sleep(delay)
    return False


def _trace(result: Result) -> None:
    """In diagnostic mode, echo the command and its output."""
    if PRESENTER_MODE:
        return
    printable = " ".join(result.args)
    body = result.text
    if len(body) > 4000:
        body = body[:4000] + "\n… (truncated)"
    _emit_text(f"$ {printable}\n[rc={result.rc}]\n{body}\n")


# --------------------------------------------------------------------------- #
# Transparent command console                                                  #
#                                                                              #
# The notebook cells hold the REAL commands a learner would type. These are    #
# run through callable "peer" handles that echo the command verbatim (labelled #
# by which entity runs it) and show the real output, no opaque method names.  #
# --------------------------------------------------------------------------- #

_OUTPUT_MAX_LINES = 110
_OUTPUT_MAX_CHARS = 9000


def _clip_output(text: str) -> str:
    text = text.rstrip()
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) > _OUTPUT_MAX_LINES:
        head = lines[: _OUTPUT_MAX_LINES - 1]
        text = "\n".join(head) + f"\n… (+{len(lines) - _OUTPUT_MAX_LINES + 1} more lines)"
    if len(text) > _OUTPUT_MAX_CHARS:
        text = text[:_OUTPUT_MAX_CHARS] + "\n… (truncated)"
    return text


def _emit_cmd(label: str, accent: str, where: str, command: str, result: Result) -> None:
    """Show a labelled command line and its real output (HTML in Jupyter)."""
    body = _clip_output(result.text)
    rc_note = "" if result.ok else f"  (exit {result.rc})"
    prompt = f"{label} ▸ {where}".strip()
    disp = _ipython_display()
    if disp is not None:
        out_html = (
            f'<pre style="margin:2px 0 0 0;padding:8px 10px;background:{NAVY};'
            f'color:#D5DBE2;border-radius:6px;font-size:12px;line-height:1.45;'
            f'white-space:pre-wrap;overflow-x:auto">{html.escape(body) or "&nbsp;"}</pre>'
        )
        markup = (
            f'<div style="font-family:Helvetica,Arial,sans-serif;margin:8px 0;'
            f'background:{CARD};border-radius:8px;padding:6px 10px 8px 10px;'
            f'border:1px solid {MIST}">'
            f'<div style="font-size:12px;color:{accent};font-weight:bold;'
            f'letter-spacing:.02em">{html.escape(prompt)}</div>'
            f'<div style="font-family:SFMono-Regular,Menlo,Consolas,monospace;'
            f'font-size:13px;color:{NAVY};background:#FFFFFF;border-left:3px solid {accent};'
            f'padding:6px 10px;border-radius:6px;white-space:pre-wrap;margin-top:2px">'
            f'<span style="color:{accent}">$</span> {html.escape(command)}'
            f'<span style="color:{RED}">{html.escape(rc_note)}</span></div>'
            f'{out_html}</div>'
        )
        disp[0](disp[1](markup))
    else:
        _emit_text(f"{prompt}\n$ {command}{rc_note}")
        if body:
            _emit_text("    " + body.replace("\n", "\n    "))


class Peer:
    """A callable handle for one lab endpoint: ``authenticator('hostapd …')``.

    Runs the command inside that container (via the allow-listed ``dexec``),
    echoes it verbatim under the entity's label, and prints the real output.
    A trailing ``&`` backgrounds the command so it survives across cells
    (tcpdump, hostapd, sshd, s_server), mirroring the runbook.
    """

    def __init__(self, container: str, label: str, accent: str = BLUE) -> None:
        _require_container(container)
        self.container = container
        self.label = label
        self.accent = accent

    def __call__(self, command: str, timeout: int = _DEFAULT_TIMEOUT, show: bool = True) -> Result:
        stripped = command.rstrip()
        if stripped.endswith("&"):
            body = stripped[:-1].rstrip()
            # A packet capture must be live before the next cell's handshake fires,
            # so give tcpdump a longer settle; other daemons need only a moment.
            settle = 1.5 if body.lstrip().startswith("tcpdump") else 0.4
            tag = re.sub(r"\W+", "_", body.split()[0])[:24]
            pidfile, logfile = f"/tmp/peer_{tag}.pid", f"/tmp/peer_{tag}.log"
            res = dexec_bg(self.container, body, pidfile, logfile)
            time.sleep(settle)
            res.out = "↳ started in background"
        else:
            res = dexec(self.container, command, timeout=timeout)
        if show:
            _emit_cmd(self.label, self.accent, f"({self.container})", command, res)
        return res


class Host:
    """A callable handle for host-side commands (``docker compose …``)."""

    label = "Host"
    accent = SLATE

    def __call__(self, command: str, timeout: int = 900, show: bool = True) -> Result:
        res = run_host(["bash", "-lc", command], cwd=REPO_ROOT, timeout=timeout)
        if show:
            _emit_cmd(self.label, self.accent, "", command, res)
        return res


# Entity handles used by the notebook (names read naturally in the commands).
host = Host()
authenticator = Peer("macsec-authenticator", "Authenticator", BLUE)
supplicant = Peer("macsec-supplicant", "Supplicant", CYAN)
tls_server = Peer("tls-server", "TLS server", BLUE)
tls_client = Peer("tls-client", "TLS client", CYAN)
initiator = Peer("ike-initiator", "Initiator", MAGENTA)
responder = Peer("ike-responder", "Responder", BLUE)
ssh_server = Peer("ssh-server", "SSH server", BLUE)
ssh_client = Peer("ssh-client", "SSH client", CYAN)
mtls_server = Peer("tls-auth-server", "TLS server", BLUE)
mtls_client = Peer("tls-auth-client", "TLS client", CYAN)
auth_initiator = Peer("ike-auth-initiator", "Initiator", MAGENTA)
auth_responder = Peer("ike-auth-responder", "Responder", BLUE)


# --------------------------------------------------------------------------- #
# Evidence                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class Measurement:
    label: str
    expected: str
    measured: str
    ok: bool


class Evidence:
    """A small claimed-vs-measured table shown under the entities."""

    def __init__(self) -> None:
        self.rows: list[Measurement] = []

    def add(self, label: str, expected, measured, ok: bool | None = None) -> Measurement:
        expected_s = str(expected)
        measured_s = str(measured)
        if ok is None:
            ok = expected_s.strip().lower() in measured_s.strip().lower()
        row = Measurement(label, expected_s, measured_s, bool(ok))
        self.rows.append(row)
        return row

    def note(self, label: str, measured) -> Measurement:
        """Record an informational value (no pass/fail styling)."""
        row = Measurement(label, "", str(measured), True)
        self.rows.append(row)
        return row

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.rows)

    def reset(self) -> None:
        self.rows.clear()


# --------------------------------------------------------------------------- #
# Two-entity visualization                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class Entity:
    key: str  # "A" (left lane) or "B" (right lane)
    title: str
    subtitle: str = ""
    status: str = "idle"  # idle | active | ok | warn | error


@dataclass
class Step:
    """One message on the ladder.

    direction: ``r`` left->right, ``l`` right->left, ``sl`` self on left lane,
    ``sr`` self on right lane (local computation such as key derivation).
    """

    label: str
    direction: str = "r"
    tag: str = ""  # small byte/size annotation, e.g. "1216 B"


def _banner_html(banner: tuple[str, str] | None) -> str:
    """A callout with an OPAQUE tint (not alpha-blended) so it reads on any theme."""
    if not banner:
        return ""
    text, colour = banner
    return (
        f'<div style="margin:6px 0 2px 0;padding:8px 12px;border-radius:8px;'
        f'background:#FFFFFF;border:1px solid {MIST};border-left:4px solid {colour};'
        f'font-family:Helvetica,Arial,sans-serif;font-size:13.5px;'
        f'color:{NAVY}">{html.escape(text)}</div>'
    )


_STATUS_COLOR = {
    "idle": MIST,
    "active": CYAN,
    "ok": GREEN,
    "warn": ORANGE,
    "error": RED,
}


class Stage:
    """Holds the two entities, the ordered message ladder, and evidence."""

    def __init__(
        self,
        title: str,
        subtitle: str,
        left: Entity,
        right: Entity,
        steps: list[Step],
        accent: str = BLUE,
    ) -> None:
        self.title = title
        self.subtitle = subtitle
        self.left = left
        self.right = right
        self.steps = steps
        self.accent = accent
        self.current = -1
        self.evidence = Evidence()
        self.banner: tuple[str, str] | None = None  # (text, color)
        self._fragment_originals: dict[str, tuple[int, Step]] = {}
        self.focus: set[int] = set()  # steps to bold, independent of ``current``

    # -- mutation ---------------------------------------------------------- #

    def set_status(self, left: str | None = None, right: str | None = None) -> None:
        if left is not None:
            self.left.status = left
        if right is not None:
            self.right.status = right

    def set_banner(self, text: str | None, color: str = SLATE) -> None:
        self.banner = (text, color) if text else None

    def advance(self, to_index: int | None = None) -> None:
        self.current = (self.current + 1) if to_index is None else to_index

    def goto(self, label_prefix: str) -> None:
        for i, step in enumerate(self.steps):
            if step.label.startswith(label_prefix):
                self.current = i
                return

    def set_focus(self, spec: int | str | list[int | str] | None) -> None:
        """Mark which arrow(s) render bold, independent of ``current`` (which

        only governs the done/idle greying of everything up to "now"). ``spec``
        is an int index, a label prefix (matches every step whose label starts
        with it, handy for a fragmented message's several arrows), a list
        mixing both, or ``None`` to clear the focus and fall back to bolding
        whichever single step ``current`` points at.
        """
        if spec is None:
            self.focus = set()
            return
        items = spec if isinstance(spec, (list, tuple, set)) else [spec]
        idxs: set[int] = set()
        for item in items:
            if isinstance(item, int):
                idxs.add(item)
            else:
                idxs.update(i for i, s in enumerate(self.steps) if s.label.startswith(item))
        self.focus = idxs

    def retag(self, label_prefix: str, tag: str, *, direction: str | None = None) -> None:
        """Update the small size/annotation tag on every step matching

        ``label_prefix`` (optionally restricted to one ``direction``), for
        ladder slots whose meaning depends on which beat just ran (e.g. the
        auth method actually used this time).
        """
        for s in self.steps:
            if s.label.startswith(label_prefix) and (direction is None or s.direction == direction):
                s.tag = tag

    def set_fragments(self, label_prefix: str, tags: list[str]) -> int:
        """Expand the single ladder step whose label starts with ``label_prefix``
        into ``len(tags)`` numbered fragment arrows, one per real on-wire
        fragment, each tagged with that specific fragment's own size.

        Idempotent: re-running a cell that calls this again first collapses
        any previous expansion for this prefix before re-expanding, so
        replaying cells in Jupyter doesn't keep stacking fragments. Returns
        the index of the last fragment step, ready to hand straight to
        ``panel(..., step=...)``.
        """
        saved = self._fragment_originals.get(label_prefix)
        if saved is not None:
            idx, original = saved
            end = idx
            while end < len(self.steps) and self.steps[end].label.startswith(label_prefix):
                end += 1
            self.steps[idx:end] = [original]
        else:
            idx = next(i for i, s in enumerate(self.steps) if s.label.startswith(label_prefix))
            original = self.steps[idx]
        self._fragment_originals[label_prefix] = (idx, original)
        n = len(tags)
        self.steps[idx:idx + 1] = [
            Step(f"{original.label}: fragment {i + 1}/{n}", original.direction, tags[i])
            for i in range(n)
        ]
        return idx + n - 1

    # -- rendering --------------------------------------------------------- #

    def _svg(self) -> str:
        width = 780
        top = 12
        box_h = 58
        lane_top = top + box_h + 16
        step_h = 46
        n = len(self.steps)
        height = lane_top + step_h * (n + 0.5) + 14
        xa, xb = 165, 615
        box_w = 250

        def esc(text: str) -> str:
            return html.escape(text)

        parts = [
            f'<svg viewBox="0 0 {width} {int(height)}" width="100%" '
            f'style="max-width:820px;font-family:Helvetica,Arial,sans-serif">'
        ]

        # entity cards
        for ent, cx in ((self.left, xa), (self.right, xb)):
            bx = cx - box_w / 2
            colour = _STATUS_COLOR.get(ent.status, MIST)
            parts.append(
                f'<rect x="{bx:.0f}" y="{top}" rx="10" width="{box_w}" height="{box_h}" '
                f'fill="{CARD}" stroke="{colour}" stroke-width="2"/>'
            )
            parts.append(
                f'<circle cx="{bx + 18:.0f}" cy="{top + 20}" r="6" fill="{colour}"/>'
            )
            parts.append(
                f'<text x="{cx:.0f}" y="{top + 25}" text-anchor="middle" '
                f'font-size="16" font-weight="bold" fill="{NAVY}">{esc(ent.title)}</text>'
            )
            if ent.subtitle:
                parts.append(
                    f'<text x="{cx:.0f}" y="{top + 44}" text-anchor="middle" '
                    f'font-size="11.5" fill="{SLATE}">{esc(ent.subtitle)}</text>'
                )

        # lifelines
        lane_bottom = height - 10
        for cx in (xa, xb):
            parts.append(
                f'<line x1="{cx}" y1="{lane_top}" x2="{cx}" y2="{lane_bottom:.0f}" '
                f'stroke="{MIST}" stroke-width="1.5"/>'
            )

        # steps
        for i, step in enumerate(self.steps):
            y = lane_top + step_h * (i + 0.7)
            active = (i in self.focus) if self.focus else (i == self.current)
            done = (not active) and i <= self.current
            if active:
                line_c, text_c, text_w = self.accent, NAVY, "bold"
                parts.append(
                    f'<rect x="30" y="{y - step_h/2:.0f}" width="{width-60}" '
                    f'height="{step_h:.0f}" rx="8" fill="{self.accent}" '
                    f'fill-opacity="0.10"/>'
                )
            elif done:
                line_c, text_c, text_w = SLATE, NAVY, "normal"
            else:
                line_c, text_c, text_w = MIST, MIST, "normal"

            label = esc(step.label)
            tag = esc(step.tag)
            if step.direction in ("r", "l"):
                x1, x2 = (xa, xb) if step.direction == "r" else (xb, xa)
                marker = "url(#arrowA)" if active else (
                    "url(#arrowD)" if done else "url(#arrowF)"
                )
                parts.append(
                    f'<line x1="{x1}" y1="{y:.0f}" x2="{x2}" y2="{y:.0f}" '
                    f'stroke="{line_c}" stroke-width="{3 if active else 2}" '
                    f'marker-end="{marker}"/>'
                )
                parts.append(
                    f'<text x="{(xa+xb)/2:.0f}" y="{y - 8:.0f}" text-anchor="middle" '
                    f'font-size="12.5" font-weight="{text_w}" fill="{text_c}">{label}</text>'
                )
                if tag:
                    parts.append(
                        f'<text x="{(xa+xb)/2:.0f}" y="{y + 15:.0f}" '
                        f'text-anchor="middle" font-size="11" fill="{SLATE}">{tag}</text>'
                    )
            else:  # self step (local computation)
                cx = xa if step.direction == "sl" else xb
                side = -1 if step.direction == "sl" else 1
                parts.append(
                    f'<path d="M {cx} {y-10:.0f} q {side*46} 10 0 20" fill="none" '
                    f'stroke="{line_c}" stroke-width="2" stroke-dasharray="4 3" '
                    f'marker-end="{"url(#arrowA)" if active else "url(#arrowF)"}"/>'
                )
                anchor = "end" if side < 0 else "start"
                tx = cx + side * 54
                parts.append(
                    f'<text x="{tx:.0f}" y="{y + 5:.0f}" text-anchor="{anchor}" '
                    f'font-size="12" font-style="italic" font-weight="{text_w}" '
                    f'fill="{text_c}">{label}</text>'
                )

        # arrowhead marker defs
        defs = (
            '<defs>'
            f'<marker id="arrowA" markerWidth="10" markerHeight="10" refX="8" refY="3" '
            f'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="{self.accent}"/></marker>'
            f'<marker id="arrowD" markerWidth="10" markerHeight="10" refX="8" refY="3" '
            f'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="{SLATE}"/></marker>'
            f'<marker id="arrowF" markerWidth="10" markerHeight="10" refX="8" refY="3" '
            f'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="{MIST}"/></marker>'
            '</defs>'
        )
        parts.insert(1, defs)
        parts.append("</svg>")
        return "".join(parts)

    def _evidence_html(self) -> str:
        if not self.evidence.rows:
            return ""
        cells = []
        for row in self.evidence.rows:
            if row.expected:
                mark = (
                    f'<span style="color:{GREEN};font-weight:bold">PASS</span>'
                    if row.ok
                    else f'<span style="color:{RED};font-weight:bold">CHECK</span>'
                )
                exp = html.escape(row.expected)
            else:
                mark = f'<span style="color:{SLATE}">info</span>'
                exp = "&mdash;"
            cells.append(
                f'<tr>'
                f'<td style="padding:4px 12px;color:{NAVY}">{html.escape(row.label)}</td>'
                f'<td style="padding:4px 12px;color:{SLATE}">{exp}</td>'
                f'<td style="padding:4px 12px;color:{NAVY};font-weight:600">'
                f'{html.escape(row.measured)}</td>'
                f'<td style="padding:4px 12px">{mark}</td>'
                f'</tr>'
            )
        return (
            f'<table style="border-collapse:collapse;margin-top:10px;background:#FFFFFF;'
            f'border-radius:8px;font-family:Helvetica,Arial,sans-serif;font-size:13px">'
            f'<tr style="border-bottom:2px solid {MIST}">'
            f'<th style="text-align:left;padding:4px 12px;color:{SLATE}">Evidence</th>'
            f'<th style="text-align:left;padding:4px 12px;color:{SLATE}">Expected</th>'
            f'<th style="text-align:left;padding:4px 12px;color:{SLATE}">Measured</th>'
            f'<th style="text-align:left;padding:4px 12px;color:{SLATE}">&nbsp;</th></tr>'
            + "".join(cells)
            + "</table>"
        )

    def markup(self) -> str:
        banner_html = _banner_html(self.banner)
        markup = (
            f'<div style="max-width:840px;background:{CARD};border:1px solid {MIST};'
            f'border-radius:12px;padding:14px 18px 18px 18px;'
            f'font-family:Helvetica,Arial,sans-serif">'
            f'<div style="font-size:20px;font-weight:bold;color:{NAVY}">'
            f'{html.escape(self.title)}</div>'
            f'<div style="font-size:13px;color:{SLATE};margin-bottom:6px">'
            f'{html.escape(self.subtitle)}</div>'
            f'{banner_html}'
            f'{self._svg()}'
            f'{self._evidence_html()}'
            f'</div>'
        )
        return markup

    def render(self) -> None:
        show_html(self.markup(), text_fallback=self._text())

    def _text(self) -> str:
        lines = [f"== {self.title} ==", self.subtitle, ""]
        lines.append(f"[{self.left.title} | {self.left.status}]"
                     f"      [{self.right.title} | {self.right.status}]")
        if self.banner:
            lines.append(f"! {self.banner[0]}")
        lines.append("")
        arrows = {"r": "-->", "l": "<--", "sl": "(local)", "sr": "(local)"}
        for i, step in enumerate(self.steps):
            marker = ">>" if i == self.current else ("  " if i > self.current else "..")
            tag = f"  [{step.tag}]" if step.tag else ""
            lines.append(f" {marker} {arrows.get(step.direction,'--')} {step.label}{tag}")
        if self.evidence.rows:
            lines.append("")
            lines.append("Evidence (expected -> measured):")
            for row in self.evidence.rows:
                status = "PASS" if row.ok else "CHECK"
                if row.expected:
                    lines.append(f"   [{status}] {row.label}: {row.expected} -> {row.measured}")
                else:
                    lines.append(f"   [info] {row.label}: {row.measured}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Parsing helpers (shared by demos)                                            #
# --------------------------------------------------------------------------- #


def first_int(text: str, default: str = "?") -> str:
    m = re.search(r"\d+", text)
    return m.group(0) if m else default


def kex_len(text: str, default: str = "?") -> str:
    """Pull the byte length out of a tshark 'Key Share Entry: ... length: N' line.

    Group names like X25519MLKEM768 contain digits, so a plain first_int() would
    grab "25519" instead of the actual length; anchor on the "length:" label.
    """
    m = re.search(r"length:\s*(\d+)", text)
    return m.group(1) if m else default


def grep(text: str, pattern: str, flags: int = re.I) -> list[str]:
    return [ln for ln in text.splitlines() if re.search(pattern, ln, flags)]


def result_markup(
    title: str,
    subtitle: str,
    evidence: Evidence,
    banner: tuple[str, str] | None = None,
    accent: str = BLUE,
) -> str:
    """Build the HTML for a title + optional banner + evidence table."""
    banner_html = _banner_html(banner)
    stage = Stage(title, subtitle, Entity("A", ""), Entity("B", ""), [], accent=accent)
    stage.evidence = evidence
    return (
        f'<div style="max-width:840px;background:{CARD};border:1px solid {MIST};'
        f'border-radius:12px;padding:14px 18px 18px 18px;'
        f'font-family:Helvetica,Arial,sans-serif">'
        f'<div style="font-size:20px;font-weight:bold;color:{NAVY}">{html.escape(title)}</div>'
        f'<div style="font-size:13px;color:{SLATE};margin-bottom:6px">{html.escape(subtitle)}</div>'
        f'{banner_html}{stage._evidence_html()}</div>'
    )


def result_panel(
    title: str,
    subtitle: str,
    evidence: Evidence,
    banner: tuple[str, str] | None = None,
    accent: str = BLUE,
) -> None:
    """Render a title + optional banner + evidence table (no message ladder).

    Used for the labs that are not a two-entity exchange (e.g. the certificate
    size showcase).
    """
    _render_result(title, subtitle, evidence, banner, accent)


def _render_result(title, subtitle, evidence, banner, accent) -> None:
    markup = result_markup(title, subtitle, evidence, banner, accent)
    text_lines = [f"== {title} ==", subtitle]
    if banner:
        text_lines.append(f"! {banner[0]}")
    for row in evidence.rows:
        status = "PASS" if row.ok else "CHECK"
        if row.expected:
            text_lines.append(f"   [{status}] {row.label}: {row.expected} -> {row.measured}")
        else:
            text_lines.append(f"   [info] {row.label}: {row.measured}")
    show_html(markup, text_fallback="\n".join(text_lines))


def panel(
    stage: "Stage",
    *,
    step: int | str | None = None,
    focus: int | str | list[int | str] | None = None,
    status: tuple[str, str] | None = None,
    rows: list[tuple] | None = None,
    notes: list[tuple[str, str]] | None = None,
    banner: str | None = None,
    banner_color: str = BLUE,
    reset: bool = True,
) -> None:
    """Draw the two-entity summary for the current beat.

    Explicitly a *visualisation* call; the real work already happened in the
    command cells above it. ``rows`` are ``(label, expected, measured[, ok])``
    tuples; ``notes`` are ``(label, value)`` info rows. ``step`` moves the
    done/idle pointer (everything up to it renders as already-happened).
    ``focus`` bolds the arrow(s) this beat is actually about: an index, a
    label prefix (matches every step whose label starts with it, e.g. every
    fragment of a split message), or a list of either; defaults to ``step``
    so a panel that doesn't pass ``focus`` keeps bolding just the one step.
    """
    if reset:
        stage.evidence.reset()
    if step is not None:
        if isinstance(step, int):
            stage.advance(step)
        else:
            stage.goto(step)
    stage.set_focus(focus if focus is not None else step)
    if status is not None:
        stage.set_status(left=status[0], right=status[1])
    for row in rows or []:
        label, expected, measured = row[0], row[1], row[2]
        if len(row) > 3:
            stage.evidence.add(label, expected, measured, ok=row[3])
        else:
            stage.evidence.add(label, expected, measured)
    for note in notes or []:
        stage.evidence.note(note[0], note[1])
    stage.set_banner(banner, banner_color)
    stage.render()


def result(
    title: str,
    subtitle: str,
    *,
    rows: list[tuple] | None = None,
    notes: list[tuple[str, str]] | None = None,
    banner: str | None = None,
    banner_color: str = SLATE,
    accent: str = BLUE,
) -> None:
    """Draw an evidence panel with no message ladder (e.g. PKI/cert-size tables)."""
    ev = Evidence()
    for row in rows or []:
        label, expected, measured = row[0], row[1], row[2]
        if len(row) > 3:
            ev.add(label, expected, measured, ok=row[3])
        else:
            ev.add(label, expected, measured)
    for note in notes or []:
        ev.note(note[0], note[1])
    result_panel(title, subtitle, ev,
                 banner=(banner, banner_color) if banner else None, accent=accent)


def cert_size_table(container: str = "tls-server") -> dict[str, str]:
    """Measure DER cert sizes across classical + PQC signature algorithms.

    Runs inside any OpenSSL 3.5 container (default: the always-on ``tls-server``
    from the key-exchange stack), so it needs no network access. Returns a map
    of ``name -> bytes``.
    """
    script = (
        "cd /tmp && rm -f pqc_*.crt; "
        "for e in EC:ecdsa ML-DSA-44:mldsa44 ML-DSA-65:mldsa65 ML-DSA-87:mldsa87 "
        "SLH-DSA-SHA2-128s:slh128s; do "
        "a=${e%%:*}; n=pqc_${e##*:}; "
        "if [ \"$a\" = EC ]; then "
        "openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out $n.key 2>/dev/null; "
        "else openssl genpkey -algorithm $a -out $n.key 2>/dev/null; fi; "
        "openssl req -x509 -new -key $n.key -out $n.crt -days 365 -subj /CN=$n -outform DER 2>/dev/null; "
        "done; "
        "for f in /tmp/pqc_*.crt; do echo \"$(wc -c < $f) $(basename $f .crt | sed s/pqc_//)\"; done"
    )
    res = dexec(container, script, timeout=90)
    sizes: dict[str, str] = {}
    for line in res.out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            sizes[parts[1]] = parts[0]
    return sizes


# --------------------------------------------------------------------------- #
# Preflight / cleanup                                                          #
# --------------------------------------------------------------------------- #


def preflight_status() -> None:
    """Show a compact readiness board for every lab container."""
    rows = []
    groups = [
        ("IPsec", ["ike-initiator", "ike-responder"]),
        ("IPsec-Auth", ["ike-auth-initiator", "ike-auth-responder"]),
        ("TLS", ["tls-server", "tls-client"]),
        ("TLS-Auth", ["tls-auth-server", "tls-auth-client"]),
        ("MACsec", ["macsec-authenticator", "macsec-supplicant"]),
        ("SSH", ["ssh-server", "ssh-client"]),
    ]
    for lab, names in groups:
        for name in names:
            st = container_status(name)
            colour = GREEN if st == "running" else (ORANGE if st != "absent" else MIST)
            rows.append(
                f'<tr style="background:#FFFFFF">'
                f'<td style="padding:3px 12px;color:{SLATE}">{lab}</td>'
                f'<td style="padding:3px 12px;color:{NAVY}">{name}</td>'
                f'<td style="padding:3px 12px;color:{colour};font-weight:bold">{st}</td></tr>'
            )
    markup = (
        f'<div style="font-family:Helvetica,Arial,sans-serif;background:{CARD};'
        f'border:1px solid {MIST};border-radius:8px;padding:8px 12px;display:inline-block">'
        f'<div style="font-size:16px;font-weight:bold;color:{NAVY}">Lab readiness</div>'
        f'<table style="border-collapse:collapse;font-size:13px;background:#FFFFFF">{"".join(rows)}</table>'
        f'</div>'
    )
    show_html(markup)


def preflight_build() -> None:
    """Build + warm every lab image and seed the classical baseline creds.

    Every lab pins its own non-overlapping subnet (172.20-172.25.0.0/24), so
    all six can build and run at once with no cross-lab teardown needed.
    Building everything here means the demo cells never need `--build`.
    """
    _emit_text("Building images (first run compiles from source; be patient)…")
    for lab in ("tls", "ipsec", "ssh", "macsec", "tls-auth", "ipsec-auth"):
        _emit_text(f"  building {lab}…")
        res = compose(lab, "build")
        _emit_text("    ok" if res.ok else f"    FAILED\n{res.text[-800:]}")
    _emit_text("  building ipsec-auth (experimental ML-DSA, strongSwan master)…")
    # --no-cache: Dockerfile.mldsa fetches strongSwan master at ML_DSA_REF. The
    # default is a pinned commit, but if ML_DSA_REF is overridden to a moving
    # ref like `master`, Docker would otherwise keep reusing the first checkout
    # it cached.
    res = compose("ipsec-auth", "build", "--no-cache", files=IPsecAuthDemo.MLDSA_FILES)
    _emit_text("    ok" if res.ok else f"    FAILED\n{res.text[-800:]}")
    compose("ssh", "run", "--rm", "keygen", "ed25519")
    compose("macsec", "run", "--rm", "certgen", "ecdsa")
    compose("ipsec-auth", "run", "--rm", "certgen", "ecdsa")
    compose("tls-auth", "run", "--rm", "certgen", "ecdsa")
    _emit_text("Preflight build complete.")


def preflight_up() -> None:
    """Bring every stack up at once so the on-stage budget is only the handshake.

    Every lab pins its own subnet (no overlaps; see each docker-compose.yml),
    so there is no ordering dance and no lab ever has to stop another lab's
    stack to free address space.
    """
    for lab in ("ipsec", "ipsec-auth", "tls", "tls-auth", "ssh", "macsec"):
        res = lab_up(lab)
        if not res.ok:
            _emit_text(f"[{lab}] up failed:\n{res.text.strip()[-600:]}")
    for name in (
        "ike-responder", "ike-auth-responder",
        "macsec-authenticator", "macsec-supplicant",
        "tls-auth-server", "ssh-server",
    ):
        wait_healthy(name)
    preflight_status()


def cleanup_all() -> None:
    """Restore shipped defaults: containers down, generated lab creds removed."""
    compose("tls", "down")
    compose("ipsec", "down")
    run_host(["git", "restore", "config/"], cwd=REPO_ROOT / LAB_DIRS["ipsec"])
    compose("ssh", "down")
    for rel in (
        "config/server/ssh_host_key",
        "config/server/ssh_host_key.pub",
        "config/server/authorized_keys",
        "config/client/id_key",
        "config/client/id_key.pub",
        "config/client/known_hosts",
    ):
        run_host(["rm", "-f", str(REPO_ROOT / "learn" / "ssh" / rel)])
    compose("macsec", "down")
    for rel in (
        "config/authenticator/ca.crt",
        "config/authenticator/server.crt",
        "config/authenticator/server.key",
        "config/supplicant/ca.crt",
        "config/supplicant/client.crt",
        "config/supplicant/client.key",
    ):
        run_host(["rm", "-f", str(REPO_ROOT / "learn" / "macsec" / rel)])
    # Extended labs
    compose("tls-auth", "down")
    for rel in (
        "config/server/ca.crt", "config/server/server.crt", "config/server/server.key",
        "config/client/ca.crt", "config/client/client.crt", "config/client/client.key",
    ):
        run_host(["rm", "-f", str(REPO_ROOT / "learn" / "tls" / "authentication" / rel)])
    compose("ipsec-auth", "down", files=IPsecAuthDemo.MLDSA_FILES)
    for sub in ("initiator", "responder"):
        for d in ("private", "x509", "x509ca"):
            run_host(["rm", "-rf", str(REPO_ROOT / "learn" / "ipsec" / "authentication" / "config" / sub / d)])
    _emit_text("Cleanup complete. Containers down, lab configs/creds restored.")


def panic_reset() -> None:
    """Kill every stray demo process without tearing down the stacks."""
    for c in ("tls-server", "tls-client"):
        if container_status(c) == "running":
            pkill_in(c, "s_server")
    for c in ("ssh-server",):
        if container_status(c) == "running":
            pkill_in(c, "sshd")
    if container_status("macsec-authenticator") == "running":
        pkill_in("macsec-authenticator", "hostapd")
        pkill_in("macsec-authenticator", "tcpdump")
    if container_status("macsec-supplicant") == "running":
        pkill_in("macsec-supplicant", "wpa_supplicant")
    if container_status("ike-initiator") == "running":
        dexec("ike-initiator", "swanctl --terminate --ike pqc-tunnel 2>/dev/null || true; true")
        pkill_in("ike-initiator", "tcpdump")
    _emit_text("Panic reset done: stray processes cleared, stacks left up.")


# --------------------------------------------------------------------------- #
# Protocol demos                                                               #
# --------------------------------------------------------------------------- #


class TLSDemo:
    """Demo 3: TLS 1.3 hybrid X25519MLKEM768 on the wire (fastest, safest)."""

    SERVER, CLIENT = "tls-server", "tls-client"
    SERVER_IP = "172.22.0.2"
    PCAP = "/tmp/hybrid.pcap"

    def __init__(self) -> None:
        self.stage = Stage(
            "TLS 1.3: hybrid key exchange, no extra round trip",
            "One ClientHello / ServerHello carries ML-KEM; only the bytes grow.",
            Entity("A", "TLS client", "openssl s_client · 172.22.0.3"),
            Entity("B", "TLS server", "openssl s_server · 172.22.0.2"),
            [
                Step("ClientHello  (supported_groups + key_share)", "r", "1216 B hybrid share"),
                Step("ServerHello  (selected group + key_share)", "l", "1120 B hybrid share"),
                Step("combine X25519 + ML-KEM-768 → session keys", "sr"),
                Step("Application data  (1-RTT)", "r"),
            ],
            accent=CYAN,
        )

    def start_server(self, group: str = "X25519MLKEM768") -> None:
        self.stage.evidence.reset()
        self.stage.set_status(left="idle", right="active")
        self.stage.set_banner(None)
        self.stage.current = -1
        # lab-only throwaway EC P-256 server cert (self-signed; key never printed)
        dexec(
            self.SERVER,
            "test -f /tmp/server.key || { "
            "openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 "
            "-out /tmp/server.key >/dev/null 2>&1; "
            "openssl req -x509 -new -key /tmp/server.key -out /tmp/server.crt "
            "-days 365 -subj '/CN=tls-server.pqc.lab' >/dev/null 2>&1; }",
        )
        pkill_in(self.SERVER, "s_server")
        dexec_bg(
            self.SERVER,
            f"tcpdump -i any -U 'tcp port 4433' -w {self.PCAP}",
            "/tmp/tcpdump.pid",
            "/tmp/tcpdump.log",
        )
        dexec_bg(
            self.SERVER,
            "openssl s_server -accept 4433 -cert /tmp/server.crt -key /tmp/server.key "
            f"-tls1_3 -groups {group} -www",
            "/tmp/sserver.pid",
            "/tmp/sserver.log",
        )
        time.sleep(1)
        self.stage.render()

    def connect_client(self, group: str = "X25519MLKEM768") -> None:
        res = dexec(
            self.CLIENT,
            f"openssl s_client -connect {self.SERVER_IP}:4433 -groups {group} "
            "</dev/null 2>&1 | grep -iE 'Negotiated TLS1.3 group|Protocol|Cipher is'",
        )
        negotiated = next(
            (ln for ln in res.out.splitlines() if "Negotiated" in ln), res.text
        )
        self.stage.advance(1)  # highlight ServerHello (the selection)
        self.stage.set_status(left="active", right="ok")
        expected = "X25519MLKEM768" if "MLKEM" in group.upper() else "x25519"
        self.stage.evidence.add("Negotiated TLS 1.3 group", expected, negotiated)
        self.stage.render()

    def read_sizes(self) -> None:
        time.sleep(1)
        stop_bg(self.SERVER, "/tmp/tcpdump.pid")
        res = dexec(
            self.SERVER,
            f"tshark -r {self.PCAP} -V 2>/dev/null | grep -iE 'Key Share Entry: Group:'",
        )
        lengths = re.findall(r"Key Exchange length:\s*(\d+)", res.out)
        self.stage.advance(3)
        self.stage.set_status(left="ok", right="ok")
        if lengths:
            client_len = max(lengths, key=int)
            server_len = min(lengths, key=int) if len(lengths) > 1 else client_len
            self.stage.evidence.add("Client key share", "1216", f"{client_len} B",
                                    ok=client_len in ("1216", "32"))
            self.stage.evidence.add("Server key share", "1120", f"{server_len} B",
                                    ok=server_len in ("1120", "32"))
        else:
            self.stage.evidence.add("Key shares", "1216 / 1120", "not found", ok=False)
        self.stage.evidence.note("Round trips", "1 (no extra flight)")
        self.stage.render()

    def classical_compare(self) -> None:
        """Optional: rerun with x25519 → 32-byte shares, still one round trip."""
        pkill_in(self.SERVER, "s_server")
        self.start_server(group="x25519")
        self.connect_client(group="x25519")
        self.read_sizes()
        self.stage.set_banner("Classical x25519: 32 B shares, still 1 round trip.", SLATE)
        self.stage.render()

    def reset(self) -> None:
        stop_bg(self.SERVER, "/tmp/tcpdump.pid")
        stop_bg(self.SERVER, "/tmp/sserver.pid")
        pkill_in(self.SERVER, "s_server")
        _emit_text("TLS demo reset (listener + capture stopped).")


class SSHDemo:
    """Demo 4: default PQ KEX, a loud downgrade, and opt-in composite auth."""

    SERVER, CLIENT = "ssh-server", "ssh-client"
    COMPOSITE = "ssh-mldsa44-ed25519@openssh.com"

    def __init__(self) -> None:
        self.stage = Stage(
            "SSH: post-quantum key exchange, on by default",
            "Hybrid ML-KEM with zero config; modern OpenSSH warns on a classical fallback.",
            Entity("A", "SSH client", "ssh · 172.24.0.3"),
            Entity("B", "SSH server", "sshd · 172.24.0.2"),
            [
                Step("KEXINIT  (offers mlkem768x25519 first)", "r"),
                Step("KEXINIT  (server selects)", "l"),
                Step("KEX init  (client X25519 + ML-KEM pubkey)", "r", "1304 B"),
                Step("KEX reply  (server key + ML-KEM ciphertext + host key)", "l", "1716 B"),
                Step("publickey authentication", "r"),
            ],
            accent=BLUE,
        )

    def _start_sshd(self, extra: str = "") -> None:
        stop_bg(self.SERVER, "/tmp/sshd.pid")
        pkill_in(self.SERVER, "sshd")
        time.sleep(0.5)
        dexec_bg(
            self.SERVER,
            f"/usr/local/sbin/sshd -D -e -f /cfg/sshd_config {extra}".strip(),
            "/tmp/sshd.pid",
            "/tmp/sshd.log",
        )
        time.sleep(1)

    def start_server(self) -> None:
        self.stage.evidence.reset()
        self.stage.set_banner(None)
        self.stage.current = -1
        self._start_sshd()
        self.stage.set_status(left="idle", right="active")
        self.stage.render()

    def connect_default(self) -> None:
        res = dexec(
            self.CLIENT,
            "ssh -F /cfg/ssh_config -v ssh-server 'echo AUTH_OK' 2>&1 "
            "| grep -E 'kex: algorithm|host key algorithm|Authenticated to|AUTH_OK'",
        )
        kex = next((ln for ln in res.out.splitlines() if "kex: algorithm" in ln), "")
        self.stage.advance(4)
        self.stage.set_status(left="ok", right="ok")
        self.stage.evidence.add("Key-exchange algorithm", "mlkem768x25519-sha256", kex)
        self.stage.evidence.note("Client / server KEX bytes", "1304 B / 1716 B")
        self.stage.evidence.note("Round trips", "1 (same as classical)")
        self.stage.render()

    def downgrade(self) -> None:
        """Model an un-upgraded server (classical-only KEX) → loud client warning."""
        self._start_sshd(extra="-o KexAlgorithms=curve25519-sha256")
        res = dexec(
            self.CLIENT,
            "ssh -F /cfg/ssh_config ssh-server 'true' 2>&1 "
            "| grep -iE 'WARNING|post-quantum|store now' || true",
        )
        warned = bool(res.out.strip())
        self.stage.evidence.reset()
        self.stage.set_status(left="warn", right="warn")
        self.stage.set_banner(
            "Downgrade is LOUD: OpenSSH warns the session is not post-quantum "
            "(contrast MACsec's silent fallback).",
            ORANGE,
        )
        self.stage.evidence.add(
            "Downgrade warning shown", "WARNING", res.out.strip() or "(none)", ok=warned
        )
        self.stage.render()

    def restore_server(self) -> None:
        self._start_sshd()
        self.stage.set_status(left="idle", right="active")
        self.stage.set_banner(None)

    def composite_auth(self) -> None:
        """Reissue composite Ed25519+ML-DSA-44 keys and measure the wire cost."""
        compose("ssh", "run", "--rm", "keygen", "mldsa44-ed25519")
        alg = self.COMPOSITE
        self._start_sshd(
            extra=f"-o HostKeyAlgorithms={alg} -o PubkeyAcceptedAlgorithms={alg}"
        )
        pkill_in(self.SERVER, "tcpdump")
        dexec_bg(self.SERVER, "tcpdump -i any -U 'tcp port 22' -w /tmp/comp.pcap",
                 "/tmp/tcpdump.pid", "/tmp/tcpdump.log")
        time.sleep(1)
        res = dexec(
            self.CLIENT,
            f"ssh -F /cfg/ssh_config -o HostKeyAlgorithms={alg} "
            f"-o PubkeyAcceptedAlgorithms={alg} -v ssh-server 'echo PQ_AUTH_OK' 2>&1 "
            "| grep -iE 'host key algorithm|Server accepts key|PQ_AUTH_OK'",
        )
        time.sleep(1)
        stop_bg(self.SERVER, "/tmp/tcpdump.pid")
        sizes = dexec(
            self.SERVER,
            "tshark -r /tmp/comp.pcap -V 2>/dev/null "
            "| grep -iE 'Host key length|Host signature length'",
        )
        hostkey = re.search(r"Host key length:\s*(\d+)", sizes.out)
        hostsig = re.search(r"Host signature length:\s*(\d+)", sizes.out)
        self.stage.evidence.reset()
        self.stage.set_status(left="ok", right="ok")
        self.stage.set_banner(
            "Both pillars post-quantum: hybrid ML-KEM key exchange + composite "
            "ML-DSA authentication (experimental, opt-in).",
            MAGENTA,
        )
        self.stage.evidence.add(
            "Host key algorithm", self.COMPOSITE,
            next((ln for ln in res.out.splitlines() if "host key algorithm" in ln.lower()), ""),
        )
        self.stage.evidence.add("Composite host key", "1383",
                                f"{hostkey.group(1) if hostkey else '?'} B",
                                ok=bool(hostkey) and hostkey.group(1) == "1383")
        self.stage.evidence.add("Composite host signature", "2523",
                                f"{hostsig.group(1) if hostsig else '?'} B",
                                ok=bool(hostsig) and hostsig.group(1) == "2523")
        self.stage.render()

    def reset(self) -> None:
        stop_bg(self.SERVER, "/tmp/tcpdump.pid")
        stop_bg(self.SERVER, "/tmp/sshd.pid")
        pkill_in(self.SERVER, "sshd")
        _emit_text("SSH demo reset (sshd + capture stopped).")


class IPsecDemo:
    """Demo 2: hybrid IKEv2 adds a round trip (IKE_INTERMEDIATE) + fragments."""

    INITIATOR, RESPONDER = "ike-initiator", "ike-responder"
    PCAP = "/tmp/hybrid.pcap"

    def __init__(self) -> None:
        self.stage = Stage(
            "IPsec / IKEv2: ML-KEM enters via a new exchange",
            "RFC 9370 carries ML-KEM in IKE_INTERMEDIATE: one extra round trip, and fragmentation.",
            Entity("A", "Initiator", "strongSwan · 172.20.0.2"),
            Entity("B", "Responder", "strongSwan · 172.20.0.3"),
            [
                Step("IKE_SA_INIT  (X25519)", "r", "small"),
                Step("IKE_SA_INIT  (X25519)", "l"),
                Step("IKE_INTERMEDIATE  (ML-KEM pubkey)", "r", "~1252 B · fragmented"),
                Step("IKE_INTERMEDIATE  (ML-KEM ciphertext)", "l", "~1155 B"),
                Step("IKE_AUTH  (identity + Child SA)", "r"),
                Step("IKE_AUTH  (confirm)", "l"),
            ],
            accent=MAGENTA,
        )

    def run_hybrid(self) -> None:
        self.stage.evidence.reset()
        self.stage.set_banner(None)
        self.stage.current = -1
        self.stage.set_status(left="active", right="active")
        dexec(self.INITIATOR, "swanctl --terminate --ike pqc-tunnel 2>/dev/null || true; true")
        pkill_in(self.INITIATOR, "tcpdump")
        dexec_bg(
            self.INITIATOR,
            "tcpdump -i any --immediate-mode -U -n 'port 500 or port 4500' "
            f"-w {self.PCAP}",
            "/tmp/tcpdump.pid",
            "/tmp/tcpdump.log",
        )
        time.sleep(1)
        dexec(self.INITIATOR, "swanctl --initiate --child pqc-child", timeout=60)
        res = dexec(
            self.INITIATOR,
            "swanctl --list-sas | grep -iE 'ML_KEM|CURVE_25519' || true",
        )
        time.sleep(1)
        stop_bg(self.INITIATOR, "/tmp/tcpdump.pid")
        suite = res.text.strip() or "(no SA line)"
        self.stage.advance(5)
        self.stage.set_status(left="ok", right="ok")
        self.stage.evidence.add("IKE SA suite", "CURVE_25519/KE1_ML_KEM_768", suite)
        self.stage.evidence.note("Round trips", "3 (adds IKE_INTERMEDIATE)")
        self.stage.render()

    def show_fragments(self) -> None:
        res = dexec(
            self.INITIATOR,
            f"tcpdump -r {self.PCAP} -n -q 2>/dev/null | awk '{{print $NF}}' "
            "| grep -E '^[0-9]+$' | sort -n | tail -1",
        )
        largest = res.out.strip() or "?"
        self.stage.goto("IKE_INTERMEDIATE  (ML-KEM pubkey")
        self.stage.evidence.add(
            "Largest fragment (UDP payload)", "1252", f"{largest} B",
            ok=largest.isdigit() and abs(int(largest) - 1252) <= 40,
        )
        self.stage.set_banner(
            "The ML-KEM public key crosses the 1280 B IKE ceiling once headers are "
            "added, so IKE-layer fragmentation splits it.",
            SLATE,
        )
        self.stage.render()

    def terminate(self) -> None:
        dexec(self.INITIATOR, "swanctl --terminate --ike pqc-tunnel 2>/dev/null || true; true")

    def reset(self) -> None:
        self.terminate()
        stop_bg(self.INITIATOR, "/tmp/tcpdump.pid")
        pkill_in(self.INITIATOR, "tcpdump")
        _emit_text("IPsec demo reset (tunnel terminated, capture stopped).")


class MACsecDemo:
    """Demo 1: same handshake, bigger cert → many more EAPOL frames."""

    AUTH, SUPP = "macsec-authenticator", "macsec-supplicant"

    def __init__(self) -> None:
        self.stage = Stage(
            "MACsec / EAP-TLS: the cert size becomes visible frames",
            "The hybrid ML-KEM key exchange is negotiated inside the EAP-TLS handshake; here only the certificate changes.",
            Entity("A", "Authenticator", "hostapd EAP server · aut"),
            Entity("B", "Supplicant", "wpa_supplicant · sup"),
            [
                Step("EAPOL-Start", "l"),
                Step("EAP-Request/Identity", "r"),
                Step("EAP-Response/Identity", "l"),
                Step("EAP-TLS ClientHello  (hybrid ML-KEM)", "l", "1216 B share"),
                Step("ServerHello + Certificate  (fragmented)", "r"),
                Step("Client Certificate + Finished", "l"),
                Step("EAP-Success", "r"),
            ],
            accent=BLUE,
        )
        self._last_frames: str | None = None

    def _one_handshake(self, pcap: str) -> str:
        pkill_in(self.AUTH, "hostapd")
        pkill_in(self.AUTH, "tcpdump")
        time.sleep(1)
        dexec_bg(self.AUTH, f"tcpdump -i aut -U 'ether proto 0x888e' -w {pcap}",
                 "/tmp/tcpdump.pid", "/tmp/tcpdump.log")
        time.sleep(1)
        dexec_bg(self.AUTH, "hostapd -dd /cfg/hostapd.conf", "/tmp/hostapd.pid", "/tmp/h.log")
        time.sleep(1)
        dexec(self.SUPP,
              "pkill wpa_supplicant 2>/dev/null; rm -f /run/wpa/sup; true")
        dexec_bg(self.SUPP,
                 "wpa_supplicant -Dwired -isup -c /cfg/wpa_supplicant.conf -t -dd",
                 "/tmp/wpa.pid", "/tmp/w.log")
        time.sleep(6)
        stop_bg(self.AUTH, "/tmp/tcpdump.pid")
        time.sleep(0.5)
        frames = dexec(self.AUTH, f"tshark -r {pcap} 2>/dev/null | wc -l")
        pkill_in(self.AUTH, "hostapd")
        dexec(self.SUPP, "pkill wpa_supplicant 2>/dev/null; true")
        return frames.out.strip()

    def run_ecdsa(self) -> None:
        compose("macsec", "run", "--rm", "certgen", "ecdsa")
        self.stage.evidence.reset()
        self.stage.set_banner(None)
        self.stage.current = -1
        self.stage.set_status(left="active", right="active")
        frames = self._one_handshake("/tmp/ecdsa.pcap")
        self._last_frames = frames
        group = dexec(
            self.AUTH,
            "tshark -r /tmp/ecdsa.pcap -V 2>/dev/null "
            "| awk '/Handshake Type: Server Hello/{f=1} f' "
            "| grep -i 'Key Share Entry: Group:' | head -1",
        )
        self.stage.advance(6)
        self.stage.set_status(left="ok", right="ok")
        self.stage.evidence.add("EAPOL frames (ECDSA)", "14", frames,
                                ok=frames.isdigit() and abs(int(frames) - 14) <= 2)
        self.stage.evidence.add("ServerHello group", "X25519MLKEM768", group.text)
        self.stage.evidence.note("Client fragments (ECDSA)", "1")
        self.stage.render()

    def run_mldsa(self, kind: str = "ml-dsa-65") -> None:
        compose("macsec", "run", "--rm", "certgen", kind)
        frames = self._one_handshake("/tmp/mldsa.pcap")
        expected = "54" if kind == "ml-dsa-65" else "42"
        self.stage.advance(6)
        self.stage.set_status(left="ok", right="ok")
        self.stage.evidence.add(
            f"EAPOL frames ({kind})", expected, frames,
            ok=frames.isdigit() and abs(int(frames) - int(expected)) <= 3,
        )
        base = self._last_frames or "14"
        self.stage.set_banner(
            f"Only the certificate changed: EAPOL frames grew {base} → {frames}. "
            "The hybrid ML-KEM key exchange was on the whole time.",
            MAGENTA,
        )
        self.stage.evidence.note("Client fragments",
                                 "11" if kind == "ml-dsa-65" else "8")
        self.stage.render()

    def downgrade(self) -> None:
        """Optional: drop the TLS 1.3 gate → silent classical TLS 1.2 success.

        Uses the ECDSA cert on purpose: TLS 1.2 cannot use an ML-DSA certificate,
        so the point of a *silent* fallback only lands with a classical cert.
        """
        compose("macsec", "run", "--rm", "certgen", "ecdsa")
        dexec(self.AUTH, "sed -i 's/^tls_flags=/#tls_flags=/' /cfg/hostapd.conf")
        self._one_handshake("/tmp/down.pcap")
        server = dexec(
            self.AUTH,
            "tshark -r /tmp/down.pcap -V 2>/dev/null "
            "| awk '/Handshake Protocol: Server Hello/{f=1} f' "
            "| grep -iE 'Cipher Suite: TLS_ECDHE' | head -1",
        )
        dexec(self.AUTH, "sed -i 's/^#tls_flags=/tls_flags=/' /cfg/hostapd.conf")
        self.stage.evidence.reset()
        self.stage.set_status(left="warn", right="warn")
        self.stage.set_banner(
            "Silent downgrade: EAP still reports SUCCESS, but the server fell back "
            "to classical TLS 1.2, no ML-KEM. Only the negotiated group reveals it.",
            ORANGE,
        )
        self.stage.evidence.add("Fell back to TLS 1.2", "TLS 1.2 / 0xc02c",
                                server.text.replace("\n", "  ") or "(no ServerHello)",
                                ok="1.2" in server.text or "c02c" in server.text.lower())
        self.stage.render()

    def reset(self) -> None:
        pkill_in(self.AUTH, "hostapd")
        pkill_in(self.AUTH, "tcpdump")
        pkill_in(self.SUPP, "wpa_supplicant")
        dexec(self.AUTH, "sed -i 's/^#tls_flags=/tls_flags=/' /cfg/hostapd.conf 2>/dev/null || true; true")
        _emit_text("MACsec demo reset (hostapd/supplicant/capture stopped, gate restored).")


# --------------------------------------------------------------------------- #
# Extended labs                                                                #
# --------------------------------------------------------------------------- #


class TLSAuthDemo:
    """`tls/authentication`: mutual TLS with ECDSA vs ML-DSA certificates.

    Pins its own subnet (172.23.0.0/24), independent of every other lab.
    """

    SERVER, CLIENT = "tls-auth-server", "tls-auth-client"
    SERVER_IP = "172.23.0.2"

    def __init__(self) -> None:
        self.stage = Stage(
            "TLS 1.3: mutual authentication (ECDSA → ML-DSA)",
            "Hybrid ML-KEM key exchange stays on; only the certificate signature changes.",
            Entity("A", "TLS client", "presents client cert · 172.23.0.3"),
            Entity("B", "TLS server", "presents server cert · 172.23.0.2"),
            [
                Step("ClientHello  (hybrid X25519MLKEM768 key share)", "r"),
                Step("ServerHello + Certificate + CertificateRequest", "l", "signed cert"),
                Step("Client Certificate + CertificateVerify + Finished", "r", "client signs"),
                Step("Finished  (mutual auth complete)", "l"),
            ],
            accent=CYAN,
        )

    def start(self) -> None:
        """Bring up the mTLS stack (idempotent; preflight_up() already did this)."""
        res = lab_up("tls-auth")
        if not res.ok:
            _emit_text(f"[tls-auth] up failed:\n{res.text[-500:]}")
        wait_healthy(self.SERVER)

    def cert_sizes(self) -> None:
        sizes = cert_size_table(self.SERVER if container_status(self.SERVER) == "running"
                                else "tls-server")
        ev = Evidence()
        for name, exp in (("ecdsa", "378"), ("mldsa44", "3977"),
                          ("mldsa65", "5506"), ("mldsa87", "7464")):
            got = sizes.get(name, "?")
            ev.add(f"{name} cert (DER)", exp, f"{got} B",
                   ok=got.isdigit() and abs(int(got) - int(exp)) <= 60)
        result_panel(
            "TLS certificate sizes: the post-quantum jump",
            "Self-signed DER certs; ML-DSA-65 is ~15× the ECDSA leaf.",
            ev,
            banner=("On TCP these just make the handshake longer (no fragmentation, "
                    "unlike IKEv2).", SLATE),
            accent=CYAN,
        )

    def _handshake(self, alg: str) -> Result:
        compose("tls-auth", "run", "--rm", "certgen", alg)
        pkill_in(self.SERVER, "s_server")
        dexec_bg(
            self.SERVER,
            "openssl s_server -accept 4433 -cert /cfg/server.crt -key /cfg/server.key "
            "-CAfile /cfg/ca.crt -Verify 1 -tls1_3 -www",
            "/tmp/sserver.pid", "/tmp/sserver.log",
        )
        time.sleep(1)
        res = dexec(
            self.CLIENT,
            f"openssl s_client -connect {self.SERVER_IP}:4433 -cert /cfg/client.crt "
            "-key /cfg/client.key -CAfile /cfg/ca.crt </dev/null 2>&1 "
            "| grep -iE 'Verify return code|Peer signature type|SSL handshake has read'",
        )
        pkill_in(self.SERVER, "s_server")
        return res

    def run_ecdsa(self) -> None:
        self.stage.evidence.reset()
        self.stage.set_banner(None)
        self.stage.advance(3)
        res = self._handshake("ecdsa")
        sig = next((l for l in res.out.splitlines() if "signature type" in l.lower()), "")
        reads = re.search(r"read (\d+) bytes", res.out)
        verify = next((l for l in res.out.splitlines() if "Verify return code" in l), "")
        self.stage.set_status(left="ok", right="ok")
        self.stage.evidence.add("Peer signature type", "ecdsa_secp256r1_sha256", sig)
        self.stage.evidence.add("Verify return code", "0 (ok)", verify)
        self.stage.evidence.note("Handshake bytes read", f"{reads.group(1) if reads else '?'} B (~2.5 KB)")
        self.stage.render()

    def run_mldsa(self, kind: str = "ml-dsa-65") -> None:
        self.stage.evidence.reset()
        self.stage.advance(3)
        res = self._handshake(kind)
        sig = next((l for l in res.out.splitlines() if "signature type" in l.lower()), "")
        reads = re.search(r"read (\d+) bytes", res.out)
        verify = next((l for l in res.out.splitlines() if "Verify return code" in l), "")
        self.stage.set_status(left="ok", right="ok")
        self.stage.evidence.add("Peer signature type", "mldsa65", sig)
        self.stage.evidence.add("Verify return code", "0 (ok)", verify)
        self.stage.evidence.note("Handshake bytes read", f"{reads.group(1) if reads else '?'} B (~16 KB)")
        self.stage.set_banner(
            "Same handshake, same result; it just ships ~6× the bytes. Both halves "
            "now quantum-safe: ML-KEM key exchange + ML-DSA authentication.", MAGENTA)
        self.stage.render()

    def reset(self) -> None:
        pkill_in(self.SERVER, "s_server")
        _emit_text("TLS-auth demo reset (listener stopped).")


class IPsecAuthDemo:
    """`ipsec/authentication`: X.509 PKI + IKEv2 mutual certificate auth.

    ECDSA auth runs on stable strongSwan; the ML-DSA path uses unreleased
    strongSwan ``master``, pinned in Dockerfile.mldsa (first build compiles from
    source, several minutes).
    """

    INITIATOR, RESPONDER = "ike-auth-initiator", "ike-auth-responder"
    MLDSA_FILES = ["docker-compose.yml", "docker-compose.mldsa.yml"]

    def __init__(self) -> None:
        self.stage = Stage(
            "IPsec / IKEv2: mutual certificate authentication",
            "Post-quantum key exchange throughout; the signature goes ECDSA → ML-DSA.",
            Entity("A", "Initiator", "cert: initiator.pqc.lab · 172.21.0.2"),
            Entity("B", "Responder", "cert: responder.pqc.lab · 172.21.0.3"),
            [
                Step("IKE_SA_INIT  (X25519)", "r"),
                Step("IKE_SA_INIT  (X25519)", "l"),
                Step("IKE_INTERMEDIATE  (ML-KEM key share)", "r", "fragmented"),
                Step("IKE_INTERMEDIATE  (ML-KEM ciphertext)", "l"),
                Step("IKE_AUTH  (cert + signature)", "r"),
                Step("IKE_AUTH  (cert + signature)", "l"),
            ],
            accent=MAGENTA,
        )

    def cert_showcase(self) -> None:
        """8-algorithm signature/cert size table (classical → PQC)."""
        sizes = cert_size_table("tls-server")
        ev = Evidence()
        ev.note("ECDSA P-256", f"{sizes.get('ecdsa','?')} B  (classical baseline)")
        ev.note("ML-DSA-44", f"{sizes.get('mldsa44','?')} B  (NIST L2)")
        ev.note("ML-DSA-65", f"{sizes.get('mldsa65','?')} B  (NIST L3, default)")
        ev.note("ML-DSA-87", f"{sizes.get('mldsa87','?')} B  (NIST L5)")
        ev.note("SLH-DSA-128s", f"{sizes.get('slh128s','?')} B  (hash-based, conservative)")
        result_panel(
            "Signature & certificate sizes: the authentication tax",
            "Self-signed DER certs across classical and post-quantum signatures.",
            ev,
            banner=("Authentication's deadline differs from key exchange: no "
                    "'harvest now', but long-lived CA roots must migrate early.", SLATE),
            accent=MAGENTA,
        )

    def start(self, mldsa: bool = False) -> None:
        files = self.MLDSA_FILES if mldsa else None
        res = lab_up("ipsec-auth", files=files)
        if not res.ok:
            _emit_text(f"[ipsec-auth] up failed:\n{res.text[-500:]}")
        wait_healthy(self.RESPONDER)

    def _run(self, alg: str, files: list[str] | None, expect_auth: str) -> None:
        compose("ipsec-auth", "run", "--rm", "certgen", alg, files=files)
        dexec(self.RESPONDER, "swanctl --load-all")
        dexec(self.INITIATOR, "swanctl --load-all")
        dexec(self.INITIATOR, "swanctl --terminate --ike auth-tunnel 2>/dev/null || true; true")
        res = dexec(self.INITIATOR, "swanctl --initiate --child auth-child", timeout=180)
        auth_lines = [l for l in res.text.splitlines() if "authentication of" in l.lower()]
        # Prefer the peer's line ("responder…"), not "(myself)".
        auth = next((l for l in auth_lines if "(myself)" not in l), auth_lines[0] if auth_lines else "")
        sas = dexec(self.INITIATOR, "swanctl --list-sas | grep -iE 'ML_KEM|CURVE_25519' || true")
        self.stage.advance(5)
        self.stage.set_status(left="ok", right="ok")
        self.stage.evidence.add("Authentication method", expect_auth, auth.strip())
        self.stage.evidence.add("Key-exchange suite", "CURVE_25519/KE1_ML_KEM_768", sas.text.strip())

    def run_ecdsa(self) -> None:
        self.stage.evidence.reset()
        self.stage.set_banner(None)
        self._run("ecdsa", None, "ECDSA_WITH_SHA256_DER")
        self.stage.set_banner(
            "Today's posture: classical ECDSA authentication over a post-quantum "
            "ML-KEM key exchange (e.g. 5G fronthaul).", BLUE)
        self.stage.render()

    def run_mldsa(self) -> None:
        """Experimental: ML-DSA-44 mutual auth (needs the strongSwan master image)."""
        self.stage.evidence.reset()
        self._run("ml-dsa-44", self.MLDSA_FILES, "ML_DSA_44")
        self.stage.set_banner(
            "Frontier: both halves post-quantum (ML-KEM + ML-DSA). Experimental "
            "strongSwan master build; wire format still an IETF draft.", MAGENTA)
        self.stage.render()

    def show_fragments(self) -> None:
        res = run_host(["docker", "logs", self.INITIATOR], timeout=30)
        frags = [l for l in res.text.splitlines() if "splitting ike message" in l.lower()]
        self.stage.goto("IKE_AUTH  (cert + signature)")
        self.stage.evidence.add(
            "Fragmented IKE messages", "IKE_INTERMEDIATE + IKE_AUTH",
            " | ".join(f.split("]")[-1].strip() for f in frags[-2:]) or "(none)",
            ok=len(frags) >= 2)
        self.stage.set_banner(
            "The ML-DSA leaf cert + signature blow IKE_AUTH up to ~6.9 KB → 6 "
            "fragments (the ~1.2 KB ML-KEM split happens either way).", SLATE)
        self.stage.render()

    def reset(self) -> None:
        dexec(self.INITIATOR, "swanctl --terminate --ike auth-tunnel 2>/dev/null || true; true")
        _emit_text("IPsec-auth demo reset (tunnel terminated). Use cleanup_all() to tear down.")


# --------------------------------------------------------------------------- #
# Self-test (text-mode; no Docker required)                                    #
# --------------------------------------------------------------------------- #


def write_preview(path: Path | None = None) -> Path:
    """Write a static HTML preview of each demo panel (no kernel/Docker needed).

    Populates each stage with the values the live demos actually measured, so
    the presenter can eyeball the visuals before going on stage.
    """
    out = path or (Path(__file__).resolve().parent / "demo_console_preview.html")

    macsec = MACsecDemo()
    macsec.stage.advance(6)
    macsec.stage.set_status(left="ok", right="ok")
    macsec.stage.set_banner(
        "Only the certificate changed: EAPOL frames grew 14 → 54.", MAGENTA
    )
    macsec.stage.evidence.add("EAPOL frames (ECDSA)", "14", "14")
    macsec.stage.evidence.add("EAPOL frames (ML-DSA-65)", "54", "54")
    macsec.stage.evidence.add("ServerHello group", "X25519MLKEM768", "X25519MLKEM768")

    ipsec = IPsecDemo()
    ipsec.stage.advance(2)
    ipsec.stage.set_status(left="active", right="active")
    ipsec.stage.set_banner(
        "The ML-KEM public key crosses the 1280 B IKE ceiling, so it fragments.", SLATE
    )
    ipsec.stage.evidence.add("IKE SA suite", "CURVE_25519/KE1_ML_KEM_768",
                             "…/CURVE_25519/KE1_ML_KEM_768")
    ipsec.stage.evidence.add("Largest fragment", "1252", "1252 B")
    ipsec.stage.evidence.note("Round trips", "3 (adds IKE_INTERMEDIATE)")

    tls = TLSDemo()
    tls.stage.advance(3)
    tls.stage.set_status(left="ok", right="ok")
    tls.stage.evidence.add("Negotiated group", "X25519MLKEM768", "X25519MLKEM768")
    tls.stage.evidence.add("Client key share", "1216", "1216 B")
    tls.stage.evidence.add("Server key share", "1120", "1120 B")
    tls.stage.evidence.note("Round trips", "1 (no extra flight)")

    ssh = SSHDemo()
    ssh.stage.advance(4)
    ssh.stage.set_status(left="warn", right="warn")
    ssh.stage.set_banner(
        "Downgrade is LOUD: OpenSSH warns the session is not post-quantum.", ORANGE
    )
    ssh.stage.evidence.add("Key exchange", "mlkem768x25519-sha256", "mlkem768x25519-sha256")
    ssh.stage.evidence.add("Composite host key", "1383", "1383 B")
    ssh.stage.evidence.add("Composite host signature", "2523", "2523 B")

    tls_auth = TLSAuthDemo()
    tls_auth.stage.advance(3)
    tls_auth.stage.set_status(left="ok", right="ok")
    tls_auth.stage.set_banner(
        "Same mutual handshake, ML-DSA certs: ~2.5 KB → ~16 KB on the wire.", MAGENTA)
    tls_auth.stage.evidence.add("Peer signature type", "mldsa65", "mldsa65")
    tls_auth.stage.evidence.add("Verify return code", "0 (ok)", "0 (ok)")
    tls_auth.stage.evidence.note("Handshake bytes read", "16031 B (~16 KB)")

    ipsec_auth = IPsecAuthDemo()
    ipsec_auth.stage.advance(6)
    ipsec_auth.stage.set_status(left="ok", right="ok")
    ipsec_auth.stage.set_banner(
        "Frontier: ML-KEM key exchange + ML-DSA authentication, both post-quantum.",
        MAGENTA)
    ipsec_auth.stage.evidence.add("Authentication method", "ML_DSA_44", "ML_DSA_44")
    ipsec_auth.stage.evidence.add("Key-exchange suite", "CURVE_25519/KE1_ML_KEM_768",
                                  "…/CURVE_25519/KE1_ML_KEM_768")
    ipsec_auth.stage.evidence.note("IKE_AUTH fragments", "6.9 KB → 6")

    cert_ev = Evidence()
    for name, val in (("ECDSA P-256", "385 B"), ("ML-DSA-44", "3985 B"),
                      ("ML-DSA-65", "5514 B"), ("ML-DSA-87", "7472 B"),
                      ("SLH-DSA-128s", "8136 B")):
        cert_ev.note(name, val)

    cert_markup = result_markup(
        "Signature & certificate sizes: the authentication tax",
        "Self-signed DER certs across classical and post-quantum signatures.",
        cert_ev, accent=MAGENTA)

    ladder_panels = "".join(
        f'<section style="margin:26px 0">{demo.stage.markup()}</section>'
        for demo in (macsec, ipsec, tls, ssh, tls_auth, ipsec_auth)
    )
    panels = (
        ladder_panels
        + f'<section style="margin:26px 0">{cert_markup}</section>'
    )
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>PQC demo console preview</title></head>"
        f"<body style='background:{CARD};padding:24px'>"
        f"<h1 style='font-family:Helvetica,Arial,sans-serif;color:{NAVY}'>"
        "PQC Demo Console: panel preview</h1>"
        f"<p style='font-family:Helvetica,Arial,sans-serif;color:{SLATE}'>"
        "Static snapshot of the live two-entity panels (values are the measured "
        "results from a validated run).</p>"
        f"{panels}</body></html>"
    )
    out.write_text(page, encoding="utf-8")
    return out


def _selftest() -> None:
    stage = Stage(
        "Self-test ladder",
        "text-mode render without a kernel",
        Entity("A", "Client", "openssl s_client"),
        Entity("B", "Server", "openssl s_server"),
        [
            Step("ClientHello", "r", "1216 B"),
            Step("ServerHello", "l", "1120 B"),
            Step("derive key", "sl"),
        ],
        accent=CYAN,
    )
    stage.advance()  # -> 0
    stage.advance()  # -> 1
    stage.set_status(left="active", right="ok")
    stage.evidence.add("Negotiated group", "X25519MLKEM768", "X25519MLKEM768")
    stage.evidence.add("Client key share", "1216", "1216 B")
    stage.render()
    assert stage.evidence.all_ok
    assert "X25519MLKEM768" in stage._text()
    print("\n[selftest] REPO_ROOT =", REPO_ROOT)
    print("[selftest] ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        preflight_status()
