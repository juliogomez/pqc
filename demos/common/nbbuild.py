"""Shared notebook builder for the hardware demos in demos/.

Each demo's build_notebook.py declares its cells with md() / code() / step(),
copies its Act 1 cells verbatim from ../containers_demo/PQC_DEMOS.ipynb with
act1_section(), and writes raw nbformat v4 JSON with write(). No third-party
packages.

write() won't overwrite a notebook someone edited by hand since its last build
(it compares a fingerprint of the cells), unless you pass --force. Once a
notebook is hand-edited, the notebook is the file to change, not its builder.

The preflight blocks (containers, underlay, automation, laptop clean up) are
the same in every demo, so they live here too: one wording, one place to fix it.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

DEMOS = Path(__file__).resolve().parent.parent
SOURCE_NB = DEMOS / "containers_demo" / "PQC_DEMOS.ipynb"


def _src(lines: tuple[str, ...]) -> list[str]:
    parts = "\n".join(lines).split("\n")
    return [p + "\n" for p in parts[:-1]] + [parts[-1]]


def md(*lines: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": _src(lines)}


def code(*lines: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "execution_count": None,
            "outputs": [], "source": _src(lines)}


def step(demo: str, *lines: str) -> dict:
    """The heading that opens every demo: what it is, what to do, what to look for."""
    return md(f"### {demo}", "", *lines)


def act1_section(anchor: str) -> list[dict]:
    """The learner notebook's cells after ``<a id="anchor">``, up to the next anchor or ``---``.

    The anchor cell itself is skipped (the demo's own step() replaces it). Ids,
    outputs and execution counts are stripped, so the copy starts clean.
    """
    cells = json.loads(SOURCE_NB.read_text())["cells"]

    def text(c: dict) -> str:
        return "".join(c["source"])

    marker = f'<a id="{anchor}">'
    start = next((i for i, c in enumerate(cells) if marker in text(c)), None)
    if start is None:
        raise SystemExit(f"{SOURCE_NB.name}: no cell contains {marker!r}; did the notebook change?")
    out = []
    for c in cells[start + 1:]:
        body = text(c).lstrip()
        if c["cell_type"] == "markdown" and (body.startswith("---") or "<a id=" in body):
            break
        c = {k: v for k, v in c.items() if k != "id"}
        if c["cell_type"] == "code":
            c["outputs"], c["execution_count"] = [], None
        out.append(c)
    return out


def find_helpers(demo: str) -> tuple[str, ...]:
    """Lines that put containers_demo/, common/ and the demo's folder on sys.path.

    Works whichever folder Jupyter started in (the repo, demos/, or any demo
    folder), so the Setup cell and the "Starting at Act 2" cell can each be the
    first cell a reader runs.
    """
    return (
        "_here = pathlib.Path.cwd()",
        "_demos = next((p for p in (_here.parent, _here, _here / 'demos') if (p / 'common' / 'lab.py').exists()), None)",
        "if _demos is None:",
        "    raise RuntimeError('Start Jupyter inside the repo (see demos/README.md) so the helpers can be found.')",
        f"for _p in ('containers_demo', 'common', '{demo}'):",
        "    sys.path.insert(0, str(_demos / _p))",
    )


# --------------------------------------------------------------------------- #
# Blocks every demo shares                                                     #
# --------------------------------------------------------------------------- #


def containers_cells(build_note: str) -> list[dict]:
    """Preflight, part 1: build (first time), start, readiness board."""
    return [
        md(
            "### Containers",
            "",
            f"**First time on this machine only:** build this demo's labs. {build_note}",
        ),
        code(
            "# host: build the Act 1 images (first time on this machine)",
            "labs.build()",
        ),
        code(
            "# host: bring this demo's stacks up, then show the readiness board",
            "labs.up()",
        ),
        md(
            "If anything in Act 1 sticks later, `labs.panic()` stops stray daemons and",
            "captures without taking the stacks down.",
            "",
            "The board below should be all green. No Docker? The container rows fail, and",
            "that's fine if you start at Act 2.",
        ),
        code(
            "# host: Act 1 containers up, Act 2/3 prerequisites in place",
            "labs.status()",
        ),
    ]


def underlay_cells() -> list[dict]:
    """Preflight, part 2: what the router cells think your lab looks like."""
    return [
        md(
            '<a id="underlay"></a>',
            "### Your lab, as the notebook sees it",
            "",
            "Every router cell reads interfaces and addresses from `demos/common/lab.yml`,",
            "or the repo's `topology.yml` for anything it leaves out. Is this what you",
            "built? If not, see [Build your lab](../README.md#build-your-lab).",
        ),
        code(
            "# laptop: underlay and MACsec link, as the router cells see them",
            "underlay()",
        ),
    ]


def automation_cells() -> list[dict]:
    """Preflight, part 4: can the playbooks reach the routers?"""
    return [
        md(
            "### Automation",
            "",
            "`verify.yml` only reads (NETCONF and `show`), so it's safe at any time. It proves",
            "the playbooks can reach all three routers and prints their crypto posture.",
        ),
        code(
            "# Ansible: read-only posture report across r1, r2, r3",
            "playbook('verify.yml');",
        ),
        md(
            "**`Could not open socket to <router>:830`?** NETCONF isn't on. `bootstrap.yml`",
            "turns it on (that's a config change, so it's not part of the read-only checks)",
            "and waits for port 830 to answer. Run it, then rerun `verify.yml`.",
        ),
        code(
            "# Ansible: enable NETCONF on r1, r2, r3 (only if verify.yml can't reach port 830)",
            "playbook('ansible-playbook bootstrap.yml');",
        ),
    ]


def recap_note() -> str:
    return ("Why can the panel say 1 when the recap says 2? Every playbook ends with `verify.yml`, "
            "whose posture report is forced to `changed` so it stays on screen. The panel subtracts "
            "it and counts config changes only, which is what the captured runs show.\n\n"
            "These cells keep the `PLAY RECAP` on screen. **Heads up:** in a plain terminal "
            "the repo's `pqc_lab` callback hides the recap when a run succeeds. Run them from "
            "here, or prefix `ANSIBLE_STDOUT_CALLBACK=default` in a terminal (plus "
            "`-e @../../../demos/common/lab.yml`; see "
            "[Running a playbook from a terminal](../README.md#run-a-playbook-from-a-terminal)).")


def laptop_cleanup_cell(what: str) -> dict:
    return code(
        f"# host: Act 1 stacks down, generated keys and certs removed, {what} back to the committed state",
        "labs.cleanup()",
    )


def _fingerprint(cells: list[dict]) -> str:
    """Hash of cell types and sources only: running cells (outputs, counts, ids) doesn't change it."""
    body = [(c.get("cell_type"), "".join(c.get("source", []))) for c in cells]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()


def _hand_edited(out: Path, new_print: str) -> bool:
    """True if ``out`` has cell changes the last build didn't make."""
    if not out.exists():
        return False
    try:
        old = json.loads(out.read_text())
    except ValueError:
        return True
    have = _fingerprint(old.get("cells", []))
    built = old.get("metadata", {}).get("nbbuild", {}).get("fingerprint")
    return have not in (built, new_print)


def write(out: Path, cells: list[dict], note: str = "") -> None:
    fingerprint = _fingerprint(cells)
    if _hand_edited(out, fingerprint) and "--force" not in sys.argv:
        raise SystemExit(
            f"{out.name} has edits the builder didn't make, so it won't overwrite them.\n"
            f"Edit the notebook itself instead, or rerun with --force to throw those edits away.")
    nb = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "nbbuild": {"fingerprint": fingerprint},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }
    text = json.dumps(nb, indent=1, ensure_ascii=False)
    if "\u2014" in text:
        raise SystemExit("em dash found (writing-style rule)")
    out.write_text(text + "\n")
    n_code = sum(c["cell_type"] == "code" for c in cells)
    print(f"wrote {out.name}: {len(cells)} cells ({n_code} code){', ' + note if note else ''}")
