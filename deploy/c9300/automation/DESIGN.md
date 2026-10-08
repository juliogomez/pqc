# Why this automation layer looks like this

This is the design rationale, not the runbook. If you want to run the thing, start at
[README.md](README.md). Read this if you're deciding whether to copy the approach.

The short version: all the post-quantum *configuration* on IOS XE 26.2 is properly modelled
in YANG, so you can push it as structured data over NETCONF. None of the post-quantum
*actions* are. That one asymmetry shapes every decision below.


## The same layer, different topology

This layer mirrors the [C8000 automation](../../c8000/automation/) in design principles but
differs in topology and PKI approach:

| | C8000 | C9300 |
|---|---|---|
| **IPsec** | hub-and-spoke (3 routers) | single-peer (2 switches, L3 routed) |
| **MACsec** | router-to-router | switch-to-router (sw1↔rtr1), switch-to-switch (hx1↔hx2), and host-to-switch (sw1↔sw2) |
| **MACsec PKI** | SCEP enrollment against an IOS CA | self-signed certs with peer import |
| **Devices** | 3 identical C8235-G2s | 2 C9300 + 2 C9300 + 1 C8355-G2 |
| **Protocol groups** | IPsec on all 3, MACsec on 2 | IPsec on sw1/sw2; MACsec on one of three link groups, named per run |


## Design principles

Carried from the C8000 layer:

- **NETCONF for config pushes, CLI for exec-mode actions.** Every config change goes through
  `roles/common/tasks/netconf-edit.yml`. Every action (`clear crypto ikev2 sa`, `crypto pki
  enroll`, `write memory`) goes through CLI wrappers.

- **`state=present` / `state=absent`** on every playbook. A bare run builds; `-e state=absent`
  tears it down.

- **`PQAUTO-` prefix** on all created objects. Teardown never removes hand-typed exercises.
  No collision, no surprise deletions.

- **Group vars split.** `lab.yml` (gitignored secrets), `pqc.yml` (PQ posture), `crypto.yml`
  (classical algos and names), `topology.yml` (addresses and pairings).


## The PQC leaves are the same

The six modelled PQC leaves are identical on C8000 and C9300, because they're the same
IOS XE YANG modules:

| Intent | XML path | Module |
|---|---|---|
| ML-KEM group | `/native/crypto/ikev2/proposal[name]/pqc/mlkem768` | `Cisco-IOS-XE-crypto` |
| PPK (RFC 8784) | `/native/crypto/ikev2/keyring[name]/peer[name]/ppk/manual/*` | `Cisco-IOS-XE-crypto` |
| SSH kex | `/native/ip/ssh/server/algorithm/kex/kex-options` | `Cisco-IOS-XE-ip` |
| MACsec PQ | `/native/access-session/{pqc-type,tls-version}` | `Cisco-IOS-XE-sanet` |
| HTTPS PQ | `/native/ip/http/secure-pqc-type` | `Cisco-IOS-XE-http` |

Same templates, same leaf names, same enum values. If a template works on the C8000 it works
here.


## The cert exchange is the hard part

On the C8000, both MACsec peers are routers on the same lab, and one of them runs an IOS CA.
SCEP enrollment is a single exec command per device, and the PKI tasks in `roles/common`
handle it cleanly.

The C9300 lab has no IOS CA. The switch and router generate self-signed certificates and
exchange them via terminal import. That means:

1. **Generate** a self-signed cert on each device (`crypto pki enroll <tp>`, which prompts)
2. **Export** the PEM from `show crypto pki certificate <tp>`
3. **Create** a peer trustpoint with `enrollment terminal` on each device
4. **Import** the peer's PEM via `crypto pki authenticate <tp>` (which prompts for the PEM
   and then asks for acceptance)

All four steps are CLI escape hatches. Steps 1 and 4 are interactive and go through the
strict prompt handler in `roles/common/tasks/exec-with-prompt.yml`.

The cert exchange is sequential by nature: each device needs the other's PEM. Ansible's
`set_fact` with `delegate_facts` publishes each host's PEM as a host var, and the import task
reads the peer's published var.

This is the most complex role in this layer (`macsec_eaptls`). Everything else is either
a NETCONF merge or a straightforward CLI push.


## The escape hatches

Same list as C8000, plus the cert exchange:

- `crypto pki enroll` (self-signed, interactive)
- `crypto pki authenticate` (import peer cert, interactive)
- `clear crypto ikev2 sa`
- `clear access-session` (one end of the pair only, see below)
- `write memory`
- Interface configuration (the pulled Cisco-IOS-XE-native is a partial)
- EAP profile and subscriber control policy (not in the pulled modules)
- `show` parsing where there's no operational leaf

Each is marked with a literal `ESCAPE HATCH` comment. Grep for it and you have the
standards-gap inventory.

### Why `clear access-session` runs on one end only

`macsec_pq` clears the session to force a fresh EAP-TLS handshake with the new
`pqc-type`. It does that on exactly one member of the pair, picked as whichever
hostname sorts first, so the choice is stable across runs.

Doing it on both is what you'd write first, and it hangs the link permanently. Both
boxes run `dot1x pae both`, so each one's supplicant authenticates against the other
one's authenticator. Clear both in the same task and they restart in lockstep on the
same `authentication-restart 7` tick, each retry reaching a peer that is itself
mid-restart. One side loops `%DOT1X-5-RESULT_OVERRIDE` every seven seconds while the
other holds `Unauthorized` with `dot1xSup: Authc Failed`. It does not time out into a
working state, it just stays there. Clearing one end renegotiates the whole pair onto a
fresh CKN in well under a minute.


## IPsec on C9300: version requirements

IPsec and ML-KEM require **IOS XE 26.2 or later**. On 26.1.x the CLI doesn't exist at
all. Tested on C9350 switches (Silicon One) with both switch-to-switch and
switch-to-router (C8000) tunnels. See [IPsec feature status](../ipsec.md#feature-status).

The automation playbooks (`ipsec-baseline.yml`, `ipsec-pq-ppk.yml`,
`ipsec-pq-mlkem.yml`) configure the tunnel and negotiate the SA. Tunnel pings work
end-to-end on 26.2.

> **Counter quirk on C9350:** `show crypto ipsec sa | include pkts` freezes at whatever
> value the previous SA left behind and never increments, because the crypto is offloaded
> to the Silicon One ASIC. Use `show interface Tunnel0 | include packets` to verify
> data-plane forwarding. C8000 routers on the other end of a tunnel *do* show proper
> `#pkts encaps` counters.


## Object naming

Every named object carries the `PQAUTO-` prefix:

| Object | Name |
|---|---|
| IKEv2 proposal | `PQAUTO-PROPOSAL` |
| IKEv2 policy | `PQAUTO-POLICY` |
| IKEv2 keyring | `PQAUTO-KEYRING` |
| IKEv2 profile | `PQAUTO-IKEV2` |
| IPsec transform set | `PQAUTO-TS` |
| IPsec profile | `PQAUTO-IPSEC` |
| MKA policy (PSK) | `PQAUTO-MKA-PSK` |
| MKA policy (EAP-TLS) | `PQAUTO-MKA-EAP` |
| Key chain | `PQAUTO-MACSEC-KC` |
| EAP profile | `PQAUTO-EAP` |
| dot1x credentials | `PQAUTO-DOT1X` (`PQAUTO-DOT1X-H2S` on the host-to-switch pair) |
| Control policy | `PQAUTO-MACSEC-POL` |
| Peer trustpoint | `PQAUTO-PEER` (`PQAUTO-PEER-H2S` on the host-to-switch pair) |
| AAA attribute list | `PQAUTO-LINKSEC` |

The prefix is the **safety mechanism**. The hand-driven exercises in the protocol docs use
names like `CLASSICAL-PROPOSAL`, `EAP-PROFILE`, `Self`, `Peer`. The prefix guarantees
`-e state=absent` never touches them.

The one object with no `PQAUTO-` name is the EAP-TLS *identity* trustpoint, and that is
the point: there isn't one. IOS XE allows exactly **one self-signed trustpoint per box**,
and every device ships with the slot filled as `TP-self-signed-<serial>`, which is also
what `ip http secure-trustpoint` uses. Running `crypto pki enroll` on a second self-signed
trustpoint doesn't fail, it offers to delete the first:

```
The router has already generated a Self Signed Certificate for
trustpoint TP-self-signed-4130127353.
If you continue the existing trustpoint and Self Signed Certificate
will be deleted.
```

So `macsec_eaptls` doesn't create an identity trustpoint at all. It discovers the shipped
one (`TP-self-signed-<serial>`), or falls back to `Self` when that is what the 24Us
enrolled, presents that certificate, and never modifies it. Both names are in
`protected_trustpoint_regex`. The only trustpoint the role owns is `PQAUTO-PEER` (or
`PQAUTO-PEER-H2S` when you target `macsec_h2s`, so sw1 can hold the router peer cert
and the host peer cert at the same time).

### Two objects on sw1, and why

Being on two links makes sw1 awkward. Most of the `PQAUTO-` objects are global, one per
box, so building one link rewrites what the other one asked for. Usually that's harmless
(the EAP profile and the control policy are identical either way, and the MKA key-server
priority only decides an election sw1 loses regardless). Two of them are not harmless, so
they're split per link:

| Object | Why it can't be shared |
|---|---|
| Peer trustpoint | Holds the *peer's* certificate. One slot, two different peers. |
| dot1x credentials | Holds the identity this box announces, and sw1 announces `SW1` to rtr1 but `MUST` to sw2. |

The credentials one is worth spelling out because the failure is so indirect. Build the
router link with a shared object and sw1's username becomes `SW1` on both ports. sw2 has
no `username SW1 aaa attribute list PQAUTO-LINKSEC` entry, so when sw1 supplicates, sw2
authenticates it fine and then can't find a profile to apply. The port sits `Unauthorized`
/ `Blocked On: User Profile Application`, MKA never keys, and nothing in the output points
at the other link that caused it. Credentials attach per interface, so two objects on sw1
is the natural fix, not a workaround.

### One link isn't a switch pair

`macsec_s2r` ends on a C8000, and the router disagrees with the C9300s in two places.
Both live in `group_vars/all/topology.yml` and resolve per host, because that link has one
of each:

| | C9300 | C8000 |
|---|---|---|
| Interface mode | `macsec network-link` | `macsec` |
| Operational state | `show macsec interface` | `show macsec status interface` |
| Counters | `show macsec interface` | `show macsec statistics interface` |
| Live TX counter | `Encrypted Pkts` under `SA Statistics` | `Out Pkts Encrypted` under `Transmit SA Counters` |

The mode keyword is the one that bites. `network-link` exists on switches to distinguish
an inter-device port from an access port facing a NIC; a routed port can't be an access
port, so the keyword doesn't exist at all on a router and plain `macsec` *is* that mode.
Push the switch spelling and IOS rejects it, after the play has already configured every
global object, leaving the box half-built.

The show commands are an exact mirror image: the switch has only `show macsec interface`,
the router has everything *except* that. Both platforms print a frozen per-channel
aggregate right next to the live per-SA counter, so both patterns anchor on the SA block.
Read the wrong one and the encryption delta is always zero on a link that's encrypting
fine.

Each of the three links is its own inventory group, and `macsec_play_hosts` has no
default: the MACsec playbooks fail on the `hosts:` keyword before they reach a device
if you don't name one. That's deliberate rather than unhelpful. sw1 belongs to two
links, one play can drive one `my_macsec_interface` per host, and a default would sooner
or later reconfigure the port you didn't mean. For the same reason there's no group that
bundles links together. `host_pattern_mismatch = error` in `ansible.cfg` catches the
other half of the problem: a mistyped group is a failure, not a green run against zero
hosts.

`skip-unaddressed.yml` needs a matching exception. Dropping a device with no `lab.yml`
entry is right for a fabric-wide report and wrong for a two-host link, where losing one
end leaves the survivor doing half a handshake. Untreated, that surfaces as a certificate
error on the wrong host twenty tasks later: `macsec_s2r` fails with "expected peer rtr1
to publish a certificate PEM in this play", which is true and useless, because rtr1 left
at task one. So the MACsec roles follow the skip guard with
`common/tasks/assert-link-manageable.yml`, which fails on the spot and names the device
and the file. That is what you hit if rtr1 is missing from `lab.yml`. rtr1's management
port is `GigabitEthernet0` in VRF `Mgmt-intf`; putting it in `lab.yml` is what makes
`macsec_s2r` a playbook target instead of a console exercise.

Targeting `-e macsec_play_hosts=macsec_h2s` overlays Gi1/0/1 / Gi2/0/1, VLAN 10, and the
SVI addresses used for the encrypted ping.

`macsec-pq` teardown on that group is a deliberate no-op, and the reason is worth stating
because getting it wrong is expensive. `access-session pqc-type` and `tls-version` are
global, one setting per switch, and sw1 carries two MACsec links. Reverting sw1 would
un-PQ the router uplink on Gi1/0/3, so sw1 has to keep them. But reverting only sw2 leaves
the two ends of the host link offering different EAP-TLS key exchanges, and that failure
is nasty to read: MKA starts and dies roughly two seconds later, forever, on a 15-second
retry, with both ends reporting `Blocked On: User Profile Application` and a fresh CKN
each cycle. It looks like a certificate or policy problem and is neither, and
`macsec-eaptls.yml` cannot repair it because it never writes those leaves. So both ends
are left alone and the pair stays on ML-KEM; `macsec-eaptls.yml -e state=absent` is the
way to take MACsec off the link.

`macsec_eaptls` also refuses to build when the two ends disagree on those leaves. It reads
them from both boxes first and fails in seconds with the mismatch spelled out, rather than
exhausting the settle retries on a session that cannot secure.

That pair has two platform-specific details. Both orange ports activate together;
staggering a cold start leaves the first `pae both` endpoint retrying before its peer
exists. It also uses `pqc-type pqc`: `hybrid` is accepted by the 24U CLI and NETCONF
model but fails to establish MKA. This lab-only compatibility exception selects
ML-KEM-512; production hardware should keep an ML-KEM-768 hybrid. The other links
keep the global `hybrid` default.

The trade is the certificate CN: it's `IOS-Self-Signed-Certificate-<serial>` rather than
the hostname. That costs nothing here. The certificate proves identity by itself, and the
readable name (`HX1`, `HX2`) is the separate EAP identity string in `dot1x credentials`.
