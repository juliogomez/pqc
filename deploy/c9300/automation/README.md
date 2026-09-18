# Automation for Post-quantum C9300

Everything in the four protocol docs ([MACsec](../macsec.md), [SSH](../ssh.md),
[TLS](../tls.md), [IPsec](../ipsec.md)), pushed as structured data over NETCONF instead of
typed at the consoles.

This is the operator guide: what to install, what to fill in, and what to run in what order.
If you want to know *why* it's built this way, read [DESIGN.md](DESIGN.md) instead.

**Do the CLI docs first.** These playbooks are for after you understand what the commands do,
not instead of understanding them.


## Prerequisites

### On the devices

Cisco 9300 switches running IOS XE 26.2 or later.

**Basic connectivity is a requirement and it is not automated.** The MACsec pair needs a
direct physical link on the interfaces named in `topology.yml`. The IPsec pair needs a
routed underlay (addresses, static routes) so that the two switches can reach each other's
Loopback110 addresses through the router chain. Build those from the protocol docs before
you run anything here.

You also want SSH reachability from wherever you run Ansible to all management addresses.

### On your computer

```bash
cd deploy/c9300/automation
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
ansible-galaxy collection install -r requirements.yml
```

Every command in this doc assumes the venv is active. If you open a new shell, re-run
`source venv/bin/activate`.


## Customize your files

```bash
cp group_vars/all/lab.yml.example group_vars/all/lab.yml
vi group_vars/all/lab.yml
```

`lab.yml` is **gitignored**. `lab.yml.example` is not, so never put anything real in the
example template.

What you need to put in there: management addresses for the devices you have, the device login and enable password, the IKEv2 pre-shared key, the RFC 8784 PPK as hex, and the MACsec CAK as 64 hex characters.

The other three files in `group_vars/all/` are tracked and you will need to review and customize to your preferences and topology:

| File | What it holds |
|---|---|
| `pqc.yml` | the post-quantum posture of the whole lab |
| `crypto.yml` | classical algorithms and PQAUTO- object names |
| `topology.yml` | addresses, peers and interfaces |


## Step 0: bootstrap

```bash
ansible-playbook bootstrap.yml
```

Turns on NETCONF, waits for port 830, probes with a real NETCONF session. Idempotent.

**Expect it to take over two minutes on first run.** The NETCONF subsystem takes about 135
seconds to come up.

```bash
ansible-playbook bootstrap.yml -e state=absent    # last, if ever
```


## How the lab is organized

Three independent tracks, no dependency among playbooks:

| Track | Where it runs | Jump to |
|---|---|---|
| **MACsec** | switch-to-switch, switch-to-router, host-to-switch | [MACsec](#macsec) |
| **IPsec** | switch-to-switch (L3 routed) | [IPsec](#ipsec) |
| **SSH / TLS** | all switches | [SSH and TLS](#ssh-and-tls) |


## MACsec

This lab has three links available in the topology, connecting different switches and routers. Every MACsec playbook makes you name the one you mean with `-e macsec_play_hosts=`.

| Group | Link | 
|---|---|
| `macsec_s2s` | switch-to-switch | 
| `macsec_s2r` | switch-to-router |
| `macsec_h2s` | host-to-switch |

For example:

```bash
ansible-playbook macsec-psk.yml -e macsec_play_hosts=macsec_s2s         # switch to switch
ansible-playbook macsec-eaptls.yml -e macsec_play_hosts=macsec_s2r  # sw1 to rtr1
ansible-playbook macsec-eaptls.yml -e macsec_play_hosts=macsec_h2s     # host to switch
ansible-playbook verify.yml -e verify_macsec_only=true -e macsec_play_hosts=macsec_s2s
```

And then there are two paths (A and B) for MACsec testing:

| Order | Command | What to expect |
|---|---|---|
| 1a | `macsec-psk.yml` | **Path A:** static CAK, CKN is `01` |
| 1b | `macsec-eaptls.yml` | **Setup for Path B:** EAP-TLS + self-signed certs. CKN becomes 32 hex |
| 2 | `macsec-pq.yml` | **Path B only:** ML-KEM inside the TLS handshake |

Each of those still needs `-e macsec_play_hosts=<group>`.

**1a and 1b are alternatives.** Don't stack PSK and EAP-TLS. Tear one down before building
the other.

Typical **Path A** (PSK, no negotiation), HX pair only:

```bash
ansible-playbook macsec-psk.yml -e macsec_play_hosts=macsec_s2s
ansible-playbook macsec-psk.yml -e macsec_play_hosts=macsec_s2s -e state=absent
```

Typical **Path B** (EAP-TLS, then ML-KEM):

```bash
ansible-playbook macsec-eaptls.yml -e macsec_play_hosts=macsec_s2s
ansible-playbook macsec-pq.yml -e macsec_play_hosts=macsec_s2s
```

Typical **Path B** (EAP-TLS, then ML-KEM), host-to-switch:

```bash
ansible-playbook macsec-eaptls.yml -e macsec_play_hosts=macsec_h2s
ansible-playbook macsec-pq.yml -e macsec_play_hosts=macsec_h2s
```

Expect `macsec-eaptls.yml` to take a couple of minutes. That's the EAP cold start: both ends
come up as supplicant and authenticator at once, both start handshakes, one loses, EAPOL
retries.

### MACsec verification

A link can show **Secured** and still encrypt nothing. Treat **Secured** + **Transmitting:
TRUE** + rising **Out Pkts Encrypted** as the health bar.

Name a link and the report scopes to it:

```bash
ansible-playbook verify.yml -e macsec_play_hosts=macsec_s2s
ansible-playbook verify.yml -e macsec_play_hosts=macsec_h2s
ansible-playbook verify.yml -e macsec_play_hosts=macsec_s2s -e verify_macsec_expect=absent
```

A bare `ansible-playbook verify.yml` reports on every device: it reads state rather than
building a link, so it has nothing to pick.

This is also why the MACsec playbooks end with a report about the link they just built
rather than the whole fabric. They always name a link, so the imported `verify.yml`
inherits it. 

With `-e state=absent` the report expects the link to be gone instead of
secured.

### MACsec teardown

```bash
ansible-playbook macsec-pq.yml -e macsec_play_hosts=macsec_s2s -e state=absent
ansible-playbook macsec-eaptls.yml -e macsec_play_hosts=macsec_s2s -e state=absent
```

On the host-to-switch pair, `macsec-pq.yml -e state=absent` does **not** clear
`access-session pqc-type` or `tls-version` on either box, and says so when you run it.
Those leaves are global, one setting per switch, and sw1 shares them with the router
uplink. Reverting sw1 would un-PQ that uplink; reverting only sw2 would leave
the two ends of the host link disagreeing on the EAP-TLS handshake, which shows up as MKA
starting and dropping every couple of seconds with both ends stuck on `Blocked On: User
Profile Application`. So the pair keeps ML-KEM, and `macsec-eaptls.yml -e state=absent` is
what actually takes MACsec off the link.

`macsec-eaptls.yml` checks this before it configures anything: if the two ends disagree on
`pqc-type` or `tls-version` it stops immediately and tells you to run `macsec-pq.yml`
first, instead of spending three minutes waiting for a session that can never secure.

### Key difference from C8000

The C8000 uses SCEP enrollment against an IOS CA. The C9300 reuses whatever self-signed
identity the box already has (`TP-self-signed-<serial>`, or `Self`), exports
the PEM, and imports it into a peer trustpoint. Default links use `PQAUTO-PEER`.
Host-to-switch uses `PQAUTO-PEER-H2S` so sw1 can keep the router peer's cert at the same
time. The role never creates or deletes the identity trustpoint.


## IPsec

IPsec between two C9300 switches across a routed network. That's the whole point: MACsec
secures a directly connected link, IPsec secures traffic that crosses L3 hops. In this
lab the underlay runs through the routers (sw1 → rtr1 → rtr2 → rtr3 → sw2), so the
tunnel actually traverses a real routed path.

| Order | Command | What to expect |
|---|---|---|
| 1 | `ansible-playbook ipsec-baseline.yml` | classical IPsec tunnel (AES-256, SHA-512, ECDH P-521) |
| 2a | `ansible-playbook ipsec-pq-ppk.yml` | PPK mixed into the key schedule |
| 2b | `ansible-playbook ipsec-pq-mlkem.yml` | native ML-KEM |

**2a and 2b can be stacked.** PPK mixes an out-of-band secret into the key schedule.
ML-KEM adds a second key exchange (RFC 9370) alongside the classical DH, so the session
key depends on both. You can run either alone or both together. The
[CLI walkthrough](../ipsec.md) teaches them one at a time for clarity, but the automation
lets you layer both.

Teardown in reverse:

```bash
ansible-playbook ipsec-pq-mlkem.yml -e state=absent     # or ipsec-pq-ppk.yml
ansible-playbook ipsec-baseline.yml -e state=absent
```

### IPsec verification

```bash
ansible-playbook verify.yml -e verify_ipsec_only=true
```

That reports negotiated KEX, PPK counters, and SA count. On the switch itself, the
commands that matter:

```
show crypto ikev2 sa
show crypto ikev2 sa detailed
show crypto ikev2 stats | include Quantum
show interface Tunnel0 | include packets
```

What to look for:

| After | Proof |
|---|---|
| Baseline | `Status: READY`, `DH Grp:21`, `Auth sign: PSK`. No `PQC Key Exchange` line. |
| PPK | `Quantum-safe Encryption using Manual PPK` in the detailed SA, and `Sessions with Quantum Resistance` counting up. |
| ML-KEM | `PQC Key Exchange: ML-KEM-768` and `Quantum-safe Encryption using PQC: ML-KEM-768`. DH group 21 is still there: hybrid, not a replacement. |

Ping the peer tunnel address and verify with `show interface Tunnel0 | include packets`
that the input/output packet counts increase.

> **Why not `show crypto ipsec sa | include pkts`?** On C9350 switches, the crypto is
> offloaded to the Silicon One ASIC, so the software counters (`#pkts encaps`) stay at
> zero even when the tunnel is forwarding traffic. The interface-level counters reflect
> the actual data plane.

## SSH and TLS

Independent of everything else. All switches in parallel.

```bash
ansible-playbook ssh-pq.yml       # ML-KEM hybrid KEX offer list
ansible-playbook tls-pq.yml       # ip http secure-pqc-type
```

Both are one-leaf NETCONF edits. SSH pins the server's KEX offer order; TLS sets the PQC
steering knob. 26.2 already negotiates X25519MLKEM768 for HTTPS out of the box.

How to check SSH:

```bash
ssh -Q kex | grep mlkem
ssh -o KexAlgorithms=mlkem768x25519-sha256 admin@<switch-mgmt-ip>
```

How to check TLS:

```bash
openssl s_client -connect <switch-mgmt-ip>:443 -tls1_3 -brief </dev/null
```

**Pass** looks like **`Negotiated TLS1.3 group: X25519MLKEM768`**.


## Checking where you are

```bash
ansible-playbook verify.yml                                  # full fabric
ansible-playbook verify.yml -e verify_ipsec_only=true        # IPsec only
ansible-playbook verify.yml -e verify_macsec_only=true       # MACsec only
```


## Putting it all back

Nothing is saved to startup-config unless you ask:

```bash
ansible-playbook <playbook>.yml -e save_config=true
```

Tear the units down in reverse dependency order. Everything with `-e state=absent` undoes
what it built, and the PQAUTO- prefix means teardown never touches your hand-built exercises.

