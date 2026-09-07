# Automation for Post-quantum IOS XE

Everything in the four protocol docs ([IPsec](../ipsec.md), [SSH](../ssh.md),
[MACsec](../macsec.md), [TLS](../tls.md)), pushed as structured data over NETCONF instead of
typed at three consoles.

This is the operator guide: what to install, what to fill in, what to run in what order, and
what goes wrong. If you want to know *why* it's built this way, and where it stops being
standards-based, read [DESIGN.md](DESIGN.md) instead.

**Do the CLI docs first.** These playbooks are for after you understand what the commands do,
not instead of understanding them. A failed assertion here is a lot easier to read if you've
seen the `show` output it's checking.

**No hardware? No worries.** [`captured/`](captured/) is the real output of running all of this on three
C8235-G2s: every unit applied, asserted, re-run for `changed=0` and torn down, plus the
operational evidence and the things that went wrong.
[`captured/README.md`](captured/README.md) indexes it. Credentials are scrubbed.

## Prerequisites

### On the routers

Three C8235-G2s (or equivalent) on IOS XE 26.2, with the `advantage` license so the crypto
features are unlocked without a separate HSECK9 key.

Checking which tier you're on is easy with `show license summary`, or
`show version | begin Technology` if you want the whole block.

**Basic connectivity is a requirement and it is not automated.** VLANs, SVIs, addresses and
routing have to exist before you run anything here. Build them from
[ipsec.md Exercise 1](../ipsec.md#exercise-1-classical-ikev2-baseline) or from
[`device-configs/`](../device-configs/). Nothing in this directory configures an SVI, an
access VLAN or a static route, on purpose: automating the underlay would mean owning the
thing you'd need working in order to fix a mistake.

Verify it yourself before going further:

```
R1# traceroute 10.0.23.2
  1 10.0.12.2 0 msec 0 msec 0 msec
  2 10.0.23.2 4 msec 0 msec *
```

You also want SSH reachability from wherever you run `ansible-playbook` to all three
management addresses, and an authoritative clock on every router. The PKI units refuse to run
without the clock, and they're right to: a CA whose clock was never set takes your passphrase
and then refuses to start.

```
R1# show clock
*16:56:05.692 UTC Sat Aug 29 2026      <<< the asterisk means "not authoritative", fix it
```

`clock set <hh:mm:ss> <day> <month> <year>` on each box, or point them at NTP, which is what
you'd do for real.

### On your computer

```bash
cd deploy/ios-xe/automation
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/ansible-galaxy collection install -r requirements.yml
```

`requirements.txt` gets you `ansible-core`, `ncclient` (for the NETCONF connection),
`paramiko` (for the CLI connection) and `lxml`. `requirements.yml` gets you
`cisco.ios`, `ansible.netcommon` and `ansible.utils`.

Two extra things, only if you're running specific units:

- **`ipsec-pq-mldsa` needs OpenSSL 3.5 or newer** on your computer, because that's where
  ML-DSA landed natively. The role checks with `openssl list -signature-algorithms` and tells
  you to point it elsewhere if your `openssl` is too old:
  `-e mldsa_openssl=/opt/homebrew/bin/openssl`.
- **`ssh-pq` needs an SSH client that knows ML-KEM.** Check with `ssh -Q kex | grep mlkem`
  before you change what the servers offer.

## Fill in `lab.yml`

The inventory goes in a file you create:

```bash
cp group_vars/all/lab.yml.example group_vars/all/lab.yml
vi group_vars/all/lab.yml
```

`lab.yml` is **gitignored**. `lab.yml.example` is not, so never put anything real in the
example template.

What you need to put in there: the three management addresses, the device login and enable password, the
IKEv2 pre-shared key, the RFC 8784 PPK as hex, the MACsec CAK as 64 hex characters, and the
passphrase for the local IOS CA. Every value shipped in the example is a throwaway lab value,
exactly like the ones printed in the protocol docs. Generate your own with
`openssl rand -hex 32` and don't reuse any of them anywhere you care about.

The other three files in `group_vars/all/` are tracked and you probably won't touch them:

| File | What it holds |
|---|---|
| `pqc.yml` | the post-quantum posture of the whole lab (read this one if you read only one) |
| `crypto.yml` | classical algorithms and object names, deliberately kept apart from the PQ intent |
| `topology.yml` | addresses, peers and interfaces, fixed by the repo |

## Step 0: bootstrap

```bash
ansible-playbook bootstrap.yml
```

This is the only unit that runs over SSH CLI, and it has to be because it's turning on NETCONF. You can't configure the transport over the transport...  :)  
So it uses `cisco.ios.ios_config` to push "netconf-yang" (and "restconf" because it costs nothing), then waits, then
opens a real NETCONF session and fetches the IKEv2 config subtree to prove the subsystem
actually answers.

It's idempotent, so multiple runs generate the same result. This means that when `ios_config` diffs against the running config, a second run pushes
nothing and reports `changed=false`. It also skips the wait entirely on that second run,
because there's nothing to wait for.

**Expect it to take over two minutes on first run.** The NETCONF subsystem takes about 135
seconds to come up on this platform (see [troubleshooting](#troubleshooting) below)

If you ever wanna disable it:

```bash
ansible-playbook bootstrap.yml -e state=absent
```

That removes the transport every other playbook here needs, so re-run `bootstrap.yml` before
anything else.

## How the lab is organized

Two big tracks, **no dependency between them**:

| Track | Where it runs | Jump to |
|---|---|---|
| **IPsec** | Hub-and-spoke overlay tunnels (r1, r2, r3) | [IPsec playbooks](#ipsec) |
| **MACsec** | R1↔R2 physical link (`Tw0/0/0` / Vlan12) | [MACsec playbooks](#macsec) |

You only need `bootstrap.yml` and `lab.yml` for either track. IPsec needs the full underlay
between all three routers; MACsec needs the R1–R2 link up. No IPsec playbook is required
before MACsec, and viceversa. This doc does IPsec before MACsec just because it is one tour of the lab, not because
one depends on the other.

The MACsec chain mirrors the IPsec PQ fork, but the mechanisms are not identical:

| Lab goal | IPsec | MACsec |
|---|---|---|
| **Setup:** classical key exchange | `ipsec-baseline.yml` | `macsec-eaptls.yml` |
| **Path A:** PQ with pre-shared key (out-of-band) | `ipsec-pq-ppk.yml` | `macsec-psk.yml` |
| **Path B:** PQ with ML-KEM (negotiated) | `ipsec-pq-mlkem.yml` | `macsec-pq.yml` (stacks on EAP-TLS) |

**Path A answers the same design question on both protocols:** avoid depending on ML-KEM by putting
strength in a secret you distribute out of band. The difference is how much negotiation still
happens:

- **PPK:** IKE still runs classical DH, and the PPK is **mixed with** the exchanged key. An attacker with the recording of that handshake cannot decrypt traffic without the PPK.
- **MACsec PSK:** there is **no handshake** on this path. Both ends already hold the CAK;
  MKA derives MACsec keys from it. Nothing negotiated can be recorded and used by an attacker.

**Path B:** classical ECDHE in the handshake, then ML-KEM layered on top. EAP-TLS also
carries certificate identity in that handshake; IPsec keeps authentication as a separate
fork (PSK → ML-DSA).

## The 9 units

Every unit is one playbook, and each one ends by importing `verify.yml` to report the
resulting posture. A playbook tears itself down with `-e state=absent`.

### IPsec

Hub-and-spoke overlay tunnels (r1 & r3 spokes, r2 hub).

| Order | Command | What to expect |
|---|---|---|
| 1 | `ansible-playbook ipsec-baseline.yml` | classical hub-and-spoke tunnels come up (AES-256, SHA-512, ECDH P-384), **all three routers in parallel** |
| 2a | `ansible-playbook ipsec-pq-ppk.yml` | a PPK is mixed into the key schedule |
| 2b | `ansible-playbook ipsec-pq-mlkem.yml` | native ML-KEM |
| 3 | `ansible-playbook ipsec-pq-mldsa.yml` | authentication swaps from pre-shared key to ML-DSA certificates, **one router at a time** |

Teardown in reverse to go back to the initial situation:

```bash
ansible-playbook ipsec-pq-mldsa.yml -e state=absent
ansible-playbook ipsec-pq-mlkem.yml -e state=absent     # or ipsec-pq-ppk.yml
ansible-playbook ipsec-baseline.yml -e state=absent
```

**2a and 2b are alternatives, not steps.** PPK and ML-KEM are two answers to the same
problem and this lab never stacks them. Each role probes for the other and refuses to run,
naming the exact command that clears the way. It won't auto-remove anything.

ML-DSA is **orthogonal** to that fork. It swaps how identity is proved and does not care
whether you chose PPK, ML-KEM, or neither for keying:

```
                    ┌─ PPK (2a) ───────┐
baseline ───────────┼─ ML-KEM (2b) ────┼─────────► ML-DSA
                    └─ neither ────────┘
                         ▲
                     pick one
```

| End state | How you get there |
|---|---|
| Classical everything | `baseline` only |
| PQ identity only (classical KEX) | `baseline` → `mldsa` |
| PPK, still PSK auth | `baseline` → `ppk` |
| PPK + ML-DSA | `baseline` → `ppk` → `mldsa` |
| ML-KEM, still PSK auth | `baseline` → `mlkem` |
| ML-KEM + ML-DSA (full stack) | `baseline` → `mlkem` → `mldsa`, or tear PPK first if you started there |

**Switching from PPK to ML-KEM while ML-DSA is already on** does not need a second ML-DSA run.
PPK teardown only touches the keyring, ML-KEM only touches the proposal.

`ipsec-pq-mldsa.yml` is the only playbook here with "serial: 1": it means that swapping authentication replaces a YANG choice rather than adding to it,
so between one end switching and the other following, the peers disagree and **the tunnel is
legitimately down**. Rolling one router at a time keeps that window to a single peer pair.
The operational check that asks "did ML-DSA actually authenticate this SA" is a separate play
afterwards, because it can't be answered until the loop has been all the way round. So as you can imagine it takes longer.

#### ML-DSA runtime

~10–15 minutes on a first apply. That is normal, not a hang. A clean apply on three routers measured about **nine minutes**
end-to-end in [`captured/04-ipsec-pq-mldsa/`](captured/04-ipsec-pq-mldsa/); your clock may
run longer if the boxes are loaded or the control node is slow.

Why it drags:

- **`serial: 1`** runs r2 → r1 → r3 **one at a time**, not in parallel.
- **Each router** does a full enrolment round trip: keygen on the box, trustpoint, CSR out,
  sign on your laptop, certificate back in.
- **Tunnel re-trigger retries** after each auth swap show as red `FAILED - RETRYING` lines.
  They are intentional and non-fatal (`failed_when: false`). While r2 has switched to ML-DSA
  but r1/r3 have not, the tunnel cannot come up; the hub also has **two** legs, so you see
  more retries there. **`failed=0` in PLAY RECAP is what matters**, not the retry noise.
- A **second run** will be much faster: `.lab-ca/` and the issued certificates already exist, so
  most tasks report `changed=0`.

That unit also stands up an ML-DSA CA on your computer in `.lab-ca/`. The routers generate
their own keys and their own CSRs; only public material crosses the wire.

`.lab-ca/` contains the CA private key in your laptop. Delete it after `ipsec-pq-mldsa.yml -e state=absent`


#### IPsec verification

**In Ansible**:

Every playbook ends with `verify.yml`. For a specific re-check later:

```bash
ansible-playbook verify.yml -e verify_ipsec_only=true
ansible-playbook verify.yml -e verify_ipsec_only=true -e verify_require_mlkem=true
ansible-playbook verify.yml -e verify_ipsec_only=true -e verify_require_mldsa=true
```

**On the router**:

| What | On-box command | Pass looks like |
|---|---|---|
| **ML-DSA** | `show crypto ikev2 sa detailed \| include Auth sign` | `Auth sign: MLDSA` |
| **ML-KEM** | `show crypto ikev2 sa detailed \| include PQC` | `PQC Key Exchange: ML-KEM-768` (and often `Quantum-safe Encryption using PQC: ML-KEM-768`) |
| **PPK** | `show crypto ikev2 stats \| include Quantum` | `Sessions with Quantum Resistance: 1` (or more) |


### MACsec

Runs on the R1 to R2 link only, since MACsec needs a direct physical wire. 

| Order | Command | What to expect |
|---|---|---|
| 1a | `ansible-playbook macsec-psk.yml` | **Path A:** static CAK, no negotiation. CKN is `01` |
| 1b | `ansible-playbook macsec-eaptls.yml` | **Setup for Path B:** EAP-TLS handshake + cert identity. CKN becomes 32 hex characters |
| 2 | `ansible-playbook macsec-pq.yml` | **Path B only:** ML-KEM inside the TLS handshake. Requires 1b, not 1a |

**1a and 1b are alternatives, not steps.** Don't stack PSK and EAP-TLS. If you run `macsec-psk.yml`, tear it down before running
`macsec-eaptls.yml`. The EAP-TLS playbook does not remove PSK for you. `macsec-pq.yml` stacks on `macsec-eaptls.yml` and cannot be run on a PSK-keyed link, because
a PSK session has no handshake for ML-KEM to go into. The playbook refuses to run because the leaves would apply, the config would look right, and nothing would change, which is much worse than an error.

Typical **Path A** (try PSK, then move on):

```bash
ansible-playbook macsec-psk.yml
ansible-playbook macsec-psk.yml -e state=absent
```

Typical **Path B** (EAP-TLS, then optionally ML-KEM):

```bash
ansible-playbook macsec-eaptls.yml
ansible-playbook macsec-pq.yml          # optional
```

Expect `macsec-eaptls.yml` to sit there for a couple of minutes. That's the EAP cold
start.

#### MACsec verification

**On the router**:

```
show mka sessions
show macsec status interface TwoGigabitEthernet0/0/0
show macsec statistics interface TwoGigabitEthernet0/0/0
ping 10.0.12.2 source Vlan12 repeat 5
show macsec statistics interface TwoGigabitEthernet0/0/0    ! Out Pkts Encrypted should move
```

A link can show **Secured** and still encrypt nothing (plain `macsec` instead of `macsec
network-link`). Treat **Secured** + **Transmitting: TRUE** + rising **Out Pkts Encrypted** as
the health bar. That is the same for PSK, EAP-TLS, and EAP-TLS + ML-KEM.

**In Ansible**:

Every MACsec apply ends with an operational proof play on r1 and r2, then `verify.yml` like
every other unit. For a
MACsec-only re-check later:

```bash
ansible-playbook verify.yml -e verify_macsec_only=true
ansible-playbook verify.yml -e verify_macsec_only=true -e verify_macsec_expect=absent
```

`verify_macsec_only` enforces secured+encrypting by default. Look for **`Verify | MACsec posture`**.

**Where am I?** CKN alone does not differentiate classical EAP-TLS from EAP-TLS + ML-KEM (both are
32 hex from the MSK). Use **`pqc_type`** for that (`show running-config | include ^access-session`). On EAP-TLS paths, `show dot1x interface TwoGigabitEthernet0/0/0 detail` only confirms
the control plane (**EAP Method = TLS**, **Auth SM State = AUTHENTICATED**), and that's the same with or without
ML-KEM. 

| Scenario | Playbook | CKN | `pqc_type` / config | `tls_version` / config |
|---|---|---|---|---|
| **PSK (Path A)** | `macsec-psk.yml` | `01` | absent (`non-pqc` default) | absent (`all` default) |
| **EAP-TLS (Path B base)** | `macsec-eaptls.yml` | 32 hex | absent | absent |
| **EAP-TLS + ML-KEM (Path B)** | `macsec-eaptls.yml` then `macsec-pq.yml` | 32 hex (same) | `hybrid` | `1.3` |

Teardown of the EAP-TLS chain, in reverse:

```bash
ansible-playbook macsec-pq.yml -e state=absent       # back to classical defaults, the link stays up
ansible-playbook macsec-eaptls.yml -e state=absent
# add -e macsec_eaptls_remove_trustpoint=true if you want the certificate gone as well
```


### SSH

Independent of everything else. No baseline needed, nothing stacks on it, all three routers
in parallel.

```bash
ansible-playbook ssh-pq.yml       # ML-KEM hybrid KEX, ordered offer list, plus the hostkey pin
```

`ssh-pq.yml` pins the router's SSH KEX offer list (`show running-config | include ^ip ssh server
algorithm`). That is a **server** knob. Ansible reaches the box over `network_cli`, not your
laptop's OpenSSH, so the playbook finishing does not mean your Mac negotiated ML-KEM.

A fresh `ssh` from the laptop often still lands on `ecdh-sha2-nistp256`. SSH picks the first
algorithm **both sides support that the client is willing to use**, and OpenSSH still prefers
classical KEX in its default list even when the router advertises ML-KEM first. 

How to actually check it:

```bash
ssh -Q kex | grep mlkem          # client must know the hybrid
ssh -vv admin@<router-mgmt-ip>   # server list starts with mlkem768x25519-sha256; NEGOTIATED is often still ecdh-sha2-nistp256
ssh -o KexAlgorithms=mlkem768x25519-sha256 admin@<router-mgmt-ip>   # force the hybrid
```

Disable it:

```bash
ansible-playbook ssh-pq.yml -e state=absent
```

### TLS

`tls-pq.yml` is the least dramatic unit here and the cleanest illustration of the whole idea:
one modelled leaf, one NETCONF edit, no CLI escape hatch anywhere. 26.2 already negotiates
X25519MLKEM768 out of the box, so on a default config this doesn't change what happens on the
wire. What it changes is that the posture is now stated in the running config rather than
being an accident of the default.

```bash
ansible-playbook tls-pq.yml       # ip http secure-pqc-type
```

`tls-pq.yml` sets `ip http secure-pqc-type` from `pq_https_pqc_type` in `group_vars/all/pqc.yml`
(default `all`). Full-fabric `verify.yml` reports the leaf as `https.pqc_type` in the posture
JSON. That is the **configured** steering knob, not proof of what your laptop negotiated.

How to actually check what was negotiated with your laptop:

```bash
openssl s_client -connect <router-mgmt-ip>:443 -tls1_3 -brief </dev/null
```

**Pass** looks like **`Negotiated TLS1.3 group: X25519MLKEM768`**. KEX is post-quantum; `Signature type:
rsa_pss_rsae_sha256` is still classical (same split as SSH).

Prove the knob does something:

```bash
ansible-playbook tls-pq.yml -e pq_https_pqc_type=non-pqc
openssl s_client -connect <router-mgmt-ip>:443 -tls1_3 -brief </dev/null
ansible-playbook tls-pq.yml
```

With `non-pqc`, OpenSSL does **not** print a `Negotiated TLS1.3 group` line. **Pass** here is **`Peer Temp Key: ECDH, secp384r1`** and **no** `X25519MLKEM768` anywhere in the output. After you re-apply with `ansible-playbook tls-pq.yml`, the hybrid line comes back.


Disable it:

```bash
ansible-playbook tls-pq.yml -e state=absent
```


### Checking where you are

**Whole fabric** (all three routers, full JSON for IPsec + SSH + HTTPS + MACsec):

```bash
ansible-playbook verify.yml
```

**IPsec**:

```bash
ansible-playbook verify.yml -e verify_ipsec_only=true
```

**MACsec**:

```bash
ansible-playbook verify.yml -e verify_macsec_only=true
```

Reports by default and asserts nothing on scoped `verify_ipsec_only` (pass
`verify_require_*` to add `require_checks=` on the line). Full-fabric `verify.yml` hard-fails when you pass
`verify_require_*` (for your CI pipelines):

```bash
ansible-playbook verify.yml -e verify_require_mlkem=true
ansible-playbook verify.yml -e verify_require_mldsa=true
ansible-playbook verify.yml -e verify_ipsec_only=true -e verify_require_mlkem=true
ansible-playbook verify.yml -e verify_macsec_only=true
```

You can pass more than one `verify_require_*` flag at once, for example after ML-KEM and
ML-DSA are both applied.


## Troubleshooting

Nothing is saved to startup-config unless you ask, which is deliberate: these are lab boxes
and an unsaved change being one reload away from gone is a feature while you experiment. When
you do want it to survive:

```bash
ansible-playbook <playbook>.yml -e save_config=true
```

### NETCONF isn't answering after bootstrap

Wait. Measured on a C8235-G2 running 26.2, from the moment `netconf-yang` was
accepted takes roughly **135 seconds**, and a fail-fast check is guaranteed to report a broken box that
is merely starting up. `bootstrap.yml` waits up to 300 seconds and polls every 15, which is
about double the measured time to cover a loaded box. A closed port at t+75s means nothing.

If it's still not up after that, `show platform software yang-management process` on the box.

### `macsec-eaptls.yml` fails: CA not `Status: enabled`

The first play stands up `crypto pki server CA_Server` on **r2**. Starting it is interactive
(`no shutdown` asks for a passphrase twice), so this fails in a few predictable ways:

**1. Clock not authoritative on r2**

```
r2# show clock
```

No leading `*`. If there is one, set the clock or point r2 at NTP, then re-run.

**2. `lab_ca_passphrase` missing or wrong in `lab.yml`**

Must be non-empty and **more than 7 characters**. IOS XE rejects shorter values at the
`no shutdown` prompt with `Password must be more than 7 characters` and loops on
`Password:` without ever reaching `Re-enter password:`, which makes Ansible time out.
Check `group_vars/all/lab.yml` (not the `.example` file).

**3. Check the CA by hand on r2**

```
r2# show crypto pki server
```

You want `Status: enabled` and a `CA cert fingerprint:` line. If it says
`Status: disabled, Time has not been set`, fix the clock. If `State: initial`, start it:

```
r2# configure terminal
r2(config)# crypto pki server CA_Server
r2(cs-server)# no shutdown
```

Enter the same passphrase twice that you put in `lab.yml`. Then `end` and re-run the playbook
(idempotent: it skips `no shutdown` when the CA is already enabled).

**4. Odd failure with output `end` only**

A previous interrupted run can leave the Ansible SSH session inside the passphrase prompt;
the next command on that session gets swallowed. Close the stuck session (exit SSH to r2) or
re-run after pulling the latest automation (the role now resets the connection after
`no shutdown`).

### `macsec-eaptls.yml` seems to hang

Give it two minutes before you believe it's broken. A cold EAP-TLS start on this link
**thrashes for about 120 seconds**: both ends come up as supplicant and authenticator at
once, both start a handshake, one loses, EAPOL retries, and it sometimes hits a session
timeout on the way.

Checking too early shows `Total MKA Sessions 0` and a session sitting at `Unauthorized` with
*both* methods already reporting `Authc Success`, which looks like a policy bug and isn't
one. The role waits it out (18 retries, 10 seconds apart) rather than failing. Later flaps
re-secure in about five seconds; it's only the cold start that churns.

### The port locked itself and took Vlan12 with it

This is the `access-session closed` ordering deadlock, and it's the most dangerous thing in
the MACsec unit.

`access-session port-control auto` together with `access-session closed` locks the port the
instant it's applied. If EAP-TLS can't complete, the port stays unauthorized, Vlan12 goes
down, and **Vlan12 is how you reach the CA over SCEP**. You can't fix enrolment because you
can't reach the CA, and you can't reach the CA because enrolment failed.

The layer is built to make this unreachable: certificates are enrolled and verified
`Available` first, the interface is configured **last**, and
`roles/macsec_eaptls/tasks/interface.yml` refuses outright to close a port on a router that
has no certificate. Teardown goes the other way, interface first, so the port stops being
closed before anything it depends on disappears.

If you get there anyway, take the interface configuration off and start again. Do it over the
management interface, never over the link you're unsecuring.

### "It applied but nothing changed"

Three specific versions of this, all documented rather than mysterious:

- **ML-KEM shows `pqc-gt-none`.** Either the SA never renegotiated (the roles clear it for
  you, so this is unusual) or `pq_key_exchange_optional: true` let a peer fall back silently.
- **The SSH server has your list but your client landed elsewhere.** SSH negotiation follows
  the *client's* preference, not the server's order. Check with `ssh -vv` from the client.
- **MACsec reports `Secured` and encrypts nothing.** That's the plain `macsec` versus
  `macsec network-link` trap from [macsec.md](../macsec.md). Never accept `Secured` as proof:
  `verify.yml` reports `out_pkts_encrypted` for exactly this reason.

### `ipsec-pq-mldsa.yml` shows `FAILED - RETRYING` then finishes with `failed=0`

Expected. See [ML-DSA runtime](#ml-dsa-runtime-1015-minutes-on-a-first-apply) above. The tunnel
re-trigger step retries overlay pings while peers are mid-migration; it is deliberately
non-fatal. Watch PLAY RECAP, not the retry lines.

### A playbook refused to run

Good. Every refusal in this layer names the command that clears the way, and none of them
change anything before stopping. The common ones: a PPK exists and you asked for ML-KEM (or
the reverse), the clock isn't authoritative, `lab.yml` is missing a value, the MACsec link is
keyed by a PSK and you asked for the PQ overlay, or your SSH kex list has no classical
fallback.

## Putting it all back

Tear the units down in reverse dependency order, or just use the snapshot you took before you
started:

```
R1# configure replace bootflash:pre-pqc.cfg
```

[Putting the routers back](../README.md#putting-the-routers-back) has the full manual
teardown, the debug-only globals that are easy to leave enabled, and the key material to
delete.

On the control node, don't forget `.lab-ca/`. It holds the **ML-DSA CA private key** in the
clear, gitignored so it never reaches a commit, and sitting there until you remove it
yourself:

```bash
rm -rf deploy/ios-xe/automation/.lab-ca/
```

Once the routers' trustpoints are gone that key protects nothing and can only cost you, so
there's no reason to keep it.
