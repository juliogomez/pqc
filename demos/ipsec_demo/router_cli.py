#!/usr/bin/env python3
"""Run CLI lines on one router over SSH and print the session, like a terminal.

    deploy/c8000/automation/venv/bin/python router_cli.py \
        '{"host": "r1", "mode": "config", "lines": ["crypto ikev2 fragmentation mtu 1400"]}'

ipsec_lab.cli() and ipsec_lab.configure() call this for you. mode "exec" sends
each line at the enable prompt, mode "config" wraps them in configure terminal /
end. A line ending in " ?" shows the router's help for it, then clears the line.
A [yes/no] question gets "yes" when "confirm" is true, and stops the run
otherwise.

Exit code 1 if the router rejected a line (% Invalid, % Incomplete, % Ambiguous).
Credentials come from the lab file, the same way router_setup.py reads them, and
are never printed.
"""
from __future__ import annotations

import json
import re
import sys
import time

from router_setup import LAB, PROMPT, Cli

REJECTED = re.compile(r"^% (Invalid|Incomplete|Ambiguous|Unrecognized)", re.M)
QUESTION = r"\[yes/no\]\s*:?\s*$|\[confirm\]\s*$"


def _tidy(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text.replace("\r", ""))
    text = re.sub(r".\x08", "", text)
    return text.replace("\x08", "")


def _help(cli: Cli, line: str) -> str:
    """Type the line with a trailing ?, collect the help, then erase the line (Ctrl-U)."""
    cli.sh.send(line.rstrip("?").rstrip() + " ?")
    time.sleep(2)
    out = ""
    while cli.sh.recv_ready():
        out += cli.sh.recv(65535).decode(errors="replace")
        time.sleep(0.3)
    cli.sh.send("\x15\n")
    prompt = _tidy(cli._read(PROMPT)).rstrip().split("\n")[-1]
    # The router redraws the prompt and the half-typed line after the help; drop that redraw.
    lines = _tidy(out).rstrip().split("\n")
    return "\n".join(lines[:-1] if len(lines) > 1 else lines).rstrip() + "\n\n" + prompt


def run(host: str, mode: str, lines: list[str], confirm: bool) -> int:
    cli = Cli(host, redact=tuple(str(LAB.get(k, "")) for k in ("lab_password", "lab_ikev2_psk", "lab_ppk_hex")))
    rejected: list[str] = []
    try:
        transcript = _tidy(cli.send("")).lstrip("\n")
        if mode == "config":
            transcript += _tidy(cli.send("configure terminal"))
        for line in lines:
            if line.rstrip().endswith("?"):
                transcript += _help(cli, line)
                continue
            cli.sh.send(line + "\n")
            out = cli._read(re.compile(QUESTION + "|" + PROMPT.pattern), timeout=180)
            if re.search(QUESTION, out):
                if not confirm:
                    cli.sh.send("no\n")
                    out += cli._read(PROMPT)
                    transcript += _tidy(out)
                    rejected.append(f"{line.strip()!r} asked for confirmation (pass confirm=True)")
                    break
                cli.sh.send("yes\n" if "yes/no" in out else "\n")
                out += cli._read(PROMPT, timeout=180)
            transcript += _tidy(out)
            if REJECTED.search(out):
                rejected.append(line.strip())
        if mode == "config":
            transcript += _tidy(cli.send("end"))
    finally:
        cli.close()
    print(cli._clean(transcript).strip("\n"))
    if rejected:
        print("\nREJECTED: " + "; ".join(cli._clean(r) for r in rejected))
        return 1
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split("\n\n")[1])
    req = json.loads(sys.argv[1])
    sys.exit(run(req["host"], req.get("mode", "exec"), req["lines"], bool(req.get("confirm"))))


if __name__ == "__main__":
    main()
