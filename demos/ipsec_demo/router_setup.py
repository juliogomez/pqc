#!/usr/bin/env python3
"""Put R1 and R3 into Act 2's starting state, whatever state they're in now.

    deploy/c8000/automation/venv/bin/python router_setup.py \
        '{"r1": {"interface": "Vlan12", "address": "10.0.12.1"},
          "r3": {"interface": "Vlan23", "address": "10.0.23.2"}}'

session.act2_setup() calls this for you. It runs under the automation
venv because it needs paramiko: the PKCS#12 import asks a question on the CLI,
and this answers exactly that question and nothing else.

Per router, and only where something is missing or left over:

1. service internal (some early 26.2 builds won't take ML-DSA certificates without it)
2. the TP-MLDSA65 trustpoint, minted with deploy/c8000/mldsa-certs/gen-mldsa-certs.sh
   (IP SAN = the router's underlay address) and backdated a day (in case the routers'
   clocks run behind this machine's). If either end is missing it, or its certificate
   is for another address, both get a fresh pair from one root CA.
3. Demo 7 undone (MLDSA-IPSEC off Tunnel0, both ML-DSA profiles removed)
4. Demo 4 undone (no pqc line, no crypto ikev2 fragmentation)
5. Demo 6's PQC-DEMO key zeroized
6. Exercise 1 from ipsec.md, with this lab's tunnel source and peer address, and Tunnel0 up

The PSK comes from ipsec.md at run time, and the bundle password is random and
used once. Router credentials come from demos/common/lab.yml, falling back to
group_vars/all/lab.yml. None of them is printed.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paramiko
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
C8000 = REPO_ROOT / "deploy" / "c8000"
LAB: dict = {}
# Same order Ansible applies them: the repo's group_vars, then demos/common/lab.yml
# (passed as -e) replacing whole top-level keys.
for _lab_file in (C8000 / "automation" / "group_vars" / "all" / "lab.yml",
                  REPO_ROOT / "demos" / "common" / "lab.yml"):
    if _lab_file.exists():
        LAB.update(yaml.safe_load(_lab_file.read_text()) or {})
PROMPT = re.compile(r"[\w.-]+(\([^)]*\))?#\s*$")
TRUSTPOINT = "TP-MLDSA65"


def say(host: str, msg: str) -> None:
    print(f"{host.upper()}  {msg}", flush=True)


class Cli:
    """One interactive vty session. Waits for the prompt after every line."""

    def __init__(self, host: str, redact: tuple[str, ...] = ()):
        self.host, self.redact = host, tuple(r for r in redact if r)
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(LAB["lab_mgmt"][host], username=LAB["lab_user"], password=LAB["lab_password"],
                         look_for_keys=False, allow_agent=False, timeout=20)
        self.sh = self.ssh.invoke_shell(width=250)
        self._read(PROMPT)
        self.send("terminal length 0")

    def _read(self, rx: re.Pattern, timeout: int = 120) -> str:
        buf, t0 = "", time.time()
        while time.time() - t0 < timeout:
            if self.sh.recv_ready():
                buf += self.sh.recv(65535).decode(errors="replace")
            elif rx.search(buf):
                return buf
            else:
                time.sleep(0.2)
        raise SystemExit(f"{self.host}: no prompt after {timeout}s. Last output:\n{self._clean(buf[-600:])}")

    def _clean(self, text: str) -> str:
        for r in self.redact:
            text = text.replace(r, "<redacted>")
        return text

    def send(self, line: str, answer: tuple[str, str] | None = None) -> str:
        """Send one line. If ``answer`` is (regex, reply), that one prompt gets that one reply."""
        self.sh.send(line + "\n")
        if answer is None:
            return self._read(PROMPT)
        out = self._read(re.compile(answer[0] + "|" + PROMPT.pattern))
        if re.search(answer[0], out):
            self.sh.send(answer[1] + "\n")
            out += self._read(PROMPT)
        return out

    def show(self, command: str) -> str:
        """Command output only: the router's echo of the command and the prompt are dropped."""
        lines = self.send(command).replace("\r", "").split("\n")
        return "\n".join(ln for ln in lines[1:] if not PROMPT.match(ln.strip()))

    def configure(self, lines: list[str]) -> None:
        self.send("configure terminal")
        errors = []
        for line in lines:
            out = self.send(line)
            if re.search(r"^% (Invalid|Incomplete|Ambiguous)", out, re.M):
                errors.append(f"{line.strip()!r}: {out.strip().splitlines()[1] if len(out.strip().splitlines()) > 1 else out.strip()}")
        self.send("end")
        if errors:
            raise SystemExit(f"{self.host}: the router rejected:\n  " + "\n  ".join(self._clean(e) for e in errors))

    def close(self) -> None:
        self.ssh.close()


def exercise1(host: str, tunnel_source: str, peer_address: str) -> list[str]:
    """Exercise 1's R1 block from ipsec.md, mirrored for R3, with this lab's underlay."""
    text = (C8000 / "ipsec.md").read_text()
    start = text.index("## Exercise 1")
    block = text[start:].split("**On R1:**", 1)[1].split("```", 2)[1]
    lines = [re.sub(r"\s+!.*$", "", ln).rstrip() for ln in block.splitlines()]
    lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("!")]
    dest = next((m.group(1) for ln in lines if (m := re.match(r"^ tunnel destination (\S+)", ln))), None)
    if dest is None or not any(ln.startswith(" tunnel source ") for ln in lines):
        raise SystemExit("ipsec.md Exercise 1 changed shape: no tunnel source/destination line found")
    # ipsec.md's peer address shows up as the keyring peer, the identity match and the
    # tunnel destination. All three become this lab's far end.
    lines = [re.sub(rf"(?<![\d.]){re.escape(dest)}(?![\d.])", peer_address, ln) for ln in lines]
    if host == "r3":
        lines = [ln.replace("peer R3", "peer R1").replace("192.168.100.1 ", "192.168.100.2 ") for ln in lines]
    return [re.sub(r"^ tunnel source \S+", f" tunnel source {tunnel_source}", ln) for ln in lines]


def mint_bundles(workdir: Path, password: str, addresses: dict[str, str]) -> None:
    openssl = shutil.which("openssl") or "openssl"
    version = subprocess.run([openssl, "version"], capture_output=True, text=True).stdout
    if not re.match(r"OpenSSL (3\.([5-9]|\d\d)|[4-9])", version):
        raise SystemExit(f"gen-mldsa-certs.sh needs OpenSSL 3.5 or newer, found: {version.strip() or 'none'}")
    env = {**os.environ, "OUT": str(workdir), "PASS": password, "OPENSSL": openssl,
           "R1_SAN": addresses["r1"], "R3_SAN": addresses["r3"]}
    proc = subprocess.run([str(C8000 / "mldsa-certs" / "gen-mldsa-certs.sh")], env=env,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("gen-mldsa-certs.sh failed:\n" + proc.stderr.replace(password, "<redacted>")[-800:])
    backdate(openssl, workdir, password)


def backdate(openssl: str, workdir: Path, password: str) -> None:
    """Move notBefore back a day, keeping notAfter and every extension.

    The script's certificates are valid from the second they're minted. If a router's
    clock runs behind this machine's, IKE_AUTH fails with
    CERTIFICATE_INVALID_NOT_YET_VALID until the router clock catches up.
    """
    def run(*args: str) -> str:
        p = subprocess.run([openssl, *args], capture_output=True, text=True)
        if p.returncode != 0:
            raise SystemExit(f"openssl {args[0]} failed while backdating: {p.stderr.strip()[-400:]}")
        return p.stdout

    def end_of(crt: Path) -> str:
        raw = run("x509", "-in", str(crt), "-noout", "-enddate").split("=", 1)[1].strip()
        return datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z").strftime("%Y%m%d%H%M%SZ")

    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d%H%M%SZ")
    root, root_key = workdir / "mldsa65-root.crt", workdir / "mldsa65-root.key"
    run("x509", "-in", str(root), "-key", str(root_key), "-not_before", start, "-not_after", end_of(root),
        "-out", str(root))
    for rtr in ("r1", "r3"):
        crt, key = workdir / f"mldsa65-{rtr}.crt", workdir / f"mldsa65-{rtr}.key"
        run("x509", "-in", str(crt), "-CA", str(root), "-CAkey", str(root_key), "-set_serial",
            "0x" + secrets.token_hex(16), "-not_before", start, "-not_after", end_of(crt), "-out", str(crt))
        run("pkcs12", "-export", "-inkey", str(key), "-in", str(crt), "-certfile", str(root),
            "-name", f"mldsa65-{rtr}", "-out", str(workdir / f"mldsa65-{rtr}.p12"), "-passout", f"pass:{password}")


def upload(cli: Cli, src: Path, name: str) -> None:
    """scp sink protocol on its own SSH connection: IOS gives each session one channel."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(LAB["lab_mgmt"][cli.host], username=LAB["lab_user"], password=LAB["lab_password"],
                look_for_keys=False, allow_agent=False, timeout=20)
    ch = ssh.get_transport().open_session()
    ch.exec_command(f"scp -t bootflash:/{name}")

    def ack() -> None:
        b = ch.recv(1)
        if b != b"\x00":
            raise SystemExit(f"{cli.host}: scp refused: {b!r} {ch.recv(512)!r}")

    ack()
    data = src.read_bytes()
    ch.sendall(f"C0600 {len(data)} {name}\n".encode())
    ack()
    ch.sendall(data + b"\x00")
    ack()
    ch.close()
    ssh.close()


def trustpoint_problem(cli: Cli, address: str) -> str:
    """Why the trustpoint can't be used for this lab, or "" if it can."""
    if "Router Signature certificate configured" not in cli.show(f"show crypto pki trustpoints {TRUSTPOINT} status"):
        return "missing"
    sans = re.findall(r"IP Address: (\S+)", cli.show(f"show crypto pki certificates {TRUSTPOINT} | include IP Address"))
    if address not in sans:
        return f"certificate is for {', '.join(sans) or 'no IP address'}, not {address}"
    return ""


def ensure_trustpoint(cli: Cli, host: str, bundles: Path | None, password: str, address: str) -> None:
    """With bundles=None, keep what's there. Otherwise (re)import from this run's PKI."""
    if bundles is None:
        say(host, f"{TRUSTPOINT}: already imported")
        return
    if re.search(rf"^crypto pki trustpoint {TRUSTPOINT}\b", cli.show(f"show running-config | include ^crypto pki trustpoint {TRUSTPOINT}"), re.M):
        cli.send("configure terminal")
        cli.send(f"no crypto pki trustpoint {TRUSTPOINT}", answer=(r"\[yes/no\]:", "yes"))
        cli.send("end")
        say(host, f"{TRUSTPOINT}: removed the old one, so both ends share one new root CA")
    # Removing a trustpoint leaves its key pair behind, and the import would then stop
    # to ask whether to replace it. Zeroize it here, on purpose, instead.
    if re.search(rf"Key name: {TRUSTPOINT}\s*$", cli.show("show crypto key mypubkey all | include Key name"), re.M):
        cli.send(f"crypto key zeroize mldsa {TRUSTPOINT}", answer=(r"\[yes/no\]:", "yes"))
        say(host, f"{TRUSTPOINT}: zeroized the orphaned key pair")

    scp_was_on = "ip scp server enable" in cli.show("show running-config | include ^ip scp server")
    if not scp_was_on:
        cli.configure(["ip scp server enable"])
    name = f"mldsa65-{host}.p12"
    try:
        upload(cli, bundles / name, name)
        cli.send("configure terminal")
        out = cli.send(f"crypto pki import {TRUSTPOINT} pkcs12 bootflash:/{name} password {password}",
                       answer=(r"Source filename \[.*\]\?", ""))
        cli.send("end")
    finally:
        cli.sh.send("\x03")  # Ctrl-C: back out of any prompt the import left open
        time.sleep(1)
        cli.send("end")
        cli.send(f"delete /force bootflash:/{name}")
        if not scp_was_on:
            cli.configure(["no ip scp server enable"])
    if "Imported PKCS12 file successfully" not in out:
        raise SystemExit(f"{host}: the PKCS#12 import failed. Run 'debug crypto pki transactions' and import by hand "
                         "(README.md, \"ML-DSA trustpoints by hand\").")
    cli.configure([f"crypto pki trustpoint {TRUSTPOINT}", " revocation-check none"])
    say(host, f"{TRUSTPOINT}: imported (ML-DSA-65, IP SAN {address}), revocation-check none")


def reset_demos(cli: Cli, host: str) -> None:
    run = cli.show("show running-config | include MLDSA-|pqc mlkem|^crypto ikev2 fragmentation|tunnel protection")
    undo = []
    if "MLDSA-IPSEC" in run:
        undo += ["interface Tunnel0", " shutdown", " no tunnel protection ipsec profile MLDSA-IPSEC", "exit",
                 "no crypto ipsec profile MLDSA-IPSEC"]
    if "MLDSA-PROFILE" in run:
        undo += ["no crypto ikev2 profile MLDSA-PROFILE"]
    if "pqc mlkem" in run:
        undo += ["crypto ikev2 proposal CLASSICAL-PROPOSAL", " no pqc mlkem768 optional", "exit"]
    if re.search(r"^crypto ikev2 fragmentation", run, re.M):
        undo += ["no crypto ikev2 fragmentation"]
    if undo:
        cli.configure(undo)
        say(host, "undid Demo 4/7 leftovers: " + ", ".join(
            x for x, hit in (("ML-DSA profiles", "MLDSA-" in run), ("pqc mlkem768", "pqc mlkem" in run),
                             ("fragmentation", "fragmentation" in run)) if hit))
    else:
        say(host, "no Demo 4/7 leftovers")
    if "PQC-DEMO" in cli.show("show crypto key mypubkey all | include Key name"):
        cli.send("crypto key zeroize mldsa PQC-DEMO", answer=(r"\[yes/no\]:", "yes"))
        say(host, "zeroized the leftover PQC-DEMO key")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split("\n\n")[1])
    underlay = json.loads(sys.argv[1])
    addresses = {h: underlay[h]["address"] for h in ("r1", "r3")}
    password = secrets.token_hex(12)
    workdir = Path(tempfile.mkdtemp(prefix="pqc-pki-"))
    try:
        problems = {}
        for host in ("r1", "r3"):
            cli = Cli(host)
            try:
                if problem := trustpoint_problem(cli, addresses[host]):
                    problems[host] = problem
            finally:
                cli.close()
        bundles = None
        if problems:
            for host, problem in problems.items():
                say(host, f"{TRUSTPOINT}: {problem}")
            print("Minting one ML-DSA PKI for both ends", flush=True)
            mint_bundles(workdir, password, addresses)
            bundles = workdir
        for host in ("r1", "r3"):
            cli = Cli(host, redact=(password,))
            try:
                if not re.search(r"^service internal", cli.show("show running-config | include ^service internal"), re.M):
                    cli.configure(["service internal"])
                    say(host, "service internal: turned on")
                else:
                    say(host, "service internal: already on")
                ensure_trustpoint(cli, host, bundles, password, addresses[host])
                reset_demos(cli, host)
                source = underlay[host]["interface"]
                ex1 = exercise1(host, source, addresses["r3" if host == "r1" else "r1"])
                running = cli.show("show running-config | section ^crypto ikev2|^crypto ipsec|^interface Tunnel0$")
                have = {ln.strip() for ln in running.splitlines()}
                # The PSK line is skipped: the router may store it encrypted.
                missing = [ln for ln in ex1 if "pre-shared-key" not in ln and ln.strip() not in have]
                if missing or "shutdown" in have:
                    cli.configure(ex1 + [" no shutdown"])
                    say(host, f"Exercise 1 configured, tunnel source {source}, Tunnel0 up")
                else:
                    say(host, f"Exercise 1: already in place (tunnel source {source})")
            finally:
                cli.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
