# Why this automation layer looks like this

This is the design rationale, not the runbook. If you want to run the thing, start at
[README.md](README.md). Read this if you're deciding whether to copy the approach, because
the interesting part isn't that it works, it's where it stops working and why.

The short version: all the post-quantum *configuration* on IOS XE 26.2 is properly modelled
in YANG, so you can push it as structured data over NETCONF. None of the post-quantum
*actions* are. That one asymmetry shapes every decision below.

## The problem

Go and look at [ipsec.md](../ipsec.md), [ssh.md](../ssh.md), [macsec.md](../macsec.md) and
[tls.md](../tls.md). Four docs, seven IPsec exercises, a local CA, an 802.1X control policy,
three routers. That's the scale of the thing.

Then notice how much of it is the same lines typed three times with two addresses changed.
The hub needs one IKEv2 profile per spoke; each spoke needs one toward the hub. The MACsec
config is symmetric on both ends apart from an identity string. The EAP-TLS stack is
identical on both peers apart from R1 versus R2.

That's why automation makes sense. The PQC posture is a decision you want to be able to state, prove and change. Ideally you configure `pq_key_exchange: mlkem768` in one file, re-run, and boom, every proposal on the fabric
moves parameter set. Try that by hand and you'll often get two routers agreeing and one that
doesn't, discovered after troubleshooting a week later.

## What do I mean by "standards-based"

The **transport** is standard. NETCONF ([RFC 6241](https://www.rfc-editor.org/rfc/rfc6241))
over SSH on port 830, and RESTCONF ([RFC 8040](https://www.rfc-editor.org/rfc/rfc8040)) over
HTTPS, both carrying YANG-modelled data. `bootstrap.yml` turns both on. Every configuration
change in the layer is an `<edit-config>` against the running datastore, with
`:rollback-on-error` so a payload that fails half way leaves the box as it was.

The **content** is native `Cisco-IOS-XE-*` YANG. Not OpenConfig, not any other
vendor-neutral intent model.

Why? Because no vendor-neutral model expresses what this lab is about. There is no
OpenConfig leaf for hybrid ML-KEM key exchange in an IKEv2 proposal, none for an RFC 8784
post-quantum pre-shared key, none for ML-DSA authentication on an IKEv2 profile. Those
concepts only exist in the vendor model today.


## Six PQC leaves are genuinely modelled

This is the load-bearing fact. These are not CLI strings shipped over a fancier wire; they
are real structured edits against named nodes:

| Intent | XML path | Module |
|---|---|---|
| ML-KEM group | `/native/crypto/ikev2/proposal[name]/pqc/mlkem768` (the element name *is* the value) | `Cisco-IOS-XE-crypto` |
| PPK (RFC 8784) | `/native/crypto/ikev2/keyring[name]/peer[name]/ppk/manual/{id,key/hex,key/required}` | `Cisco-IOS-XE-crypto` |
| ML-DSA auth | `/native/crypto/pki/trustpoint[id]/mldsakeypair/*` and `.../authentication/{local,remote}/mldsa-sig` | `Cisco-IOS-XE-crypto` |
| SSH kex | `/native/ip/ssh/server/algorithm/kex/kex-options` | `Cisco-IOS-XE-ip` (a **submodule**) |
| MACsec PQ | `/native/access-session/{pqc-type,tls-version}` | `Cisco-IOS-XE-sanet` |
| HTTPS PQ | `/native/ip/http/secure-pqc-type` | `Cisco-IOS-XE-http` |

A couple of these are worth staring at.

The ML-KEM parameter set is the **element name**, not a value. `mlkem512`, `mlkem768` and
`mlkem1024` are sibling leaves of type `empty` inside a `pqc` container, so choosing a
parameter set means emitting a different tag. That's why `pq_key_exchange` in
`group_vars/all/pqc.yml` has to be spelled the way the schema spells it, and why the whole
ML-KEM change is eleven lines of XML.

`parameter-set` under `mldsakeypair` has a YANG range of literally `44 | 65 | 87`, so a typo
is rejected at the edit rather than accepted and puzzled over later. That's the schema
validation you're paying for, and this is what it looks like when it pays.

## Current model limitation

`Cisco-IOS-XE-crypto.yang` contains **zero** `rpc` statements. Not "few", zero. So none of
these have a structured equivalent:

- `crypto key generate mldsa`
- `crypto pki authenticate`
- `crypto pki enroll`
- `clear crypto ikev2 sa`
- `clear access-session`
- `write memory`

Every one of them goes over CLI, through `ansible.netcommon.network_cli`.

You can
declare desired *state* over the modelled interface. You cannot ask the box to *do*
something. "This proposal should offer ML-KEM-768" is expressible. "Renegotiate now" is not.

And you need both. Changing a proposal does not touch a SA that is already up. Push ML-KEM,
read the operational data, see `pqc-gt-none`, and you'll conclude the feature is broken when
it simply hasn't renegotiated. That's why `roles/common/tasks/clear-ikev2-sa.yml` exists and
why it's called out as an escape hatch in a comment rather than buried.

The escape hatches are marked, deliberately, with a literal `ESCAPE HATCH` comment at the
top of every task file that uses CLI as its primary tool. Grep for it and you have the
standards-gap inventory:

- exec-mode actions (the list above)
- interface configuration, because the `Cisco-IOS-XE-native` copy pulled off the box is a
  partial with no interface containers, so there's no schema to validate a payload against
- the EAP profile and the subscriber control policy, which weren't among the modules the box
  advertised at all
- `show` parsing where there's no operational leaf

Everything else is NETCONF.

## Hardware

Each of these was measured on a C8235-G2 running 26.2. None of it is inferred from
reading the schema, and two of the findings directly contradict what reading the schema
suggested.

### An `<ok/>` is not proof

Just an example: starting a `crypto pki server` by setting `shutdown-config=false` over NETCONF returns
`<ok/>`, and the CA never starts. It sits at `disabled` / `initial` with nothing logged
anywhere. No error, no warning, no hint.

Only the CLI `no shutdown` works, and it prompts for a passphrase to protect the CA's private
key. That passphrase has **no leaf anywhere in the schema**. There is nowhere to put the
secret in a NETCONF payload, so this step genuinely cannot be done over the modelled
interface.

In general terms: **a successful edit is not a working
feature.** That's why every unit in this layer asserts against the box after configuring it,
rather than trusting the reply. A NETCONF remove of a node the server chose not to remove still answers `<ok/>`.

### `service internal` gates a modelled node (early 26.2.x; expect removal at GA)

The `mldsakeypair` container is in the schema, published, with proper types. On some
pre-GA 26.2.x builds, a CLI edit against it needs `service internal` on the box, and
**NETCONF does not bypass the gate**.

Which tells you something about the implementation: the YANG interface sits *below* the CLI
parser's feature gating, not beside it. The node exists in the model and is still refused for
a reason that has nothing to do with the model.

`service internal` is a debug-only global you don't want left behind, so
`roles/ipsec_pq_mldsa` records whether it was already on before touching anything, and the
teardown restores exactly the prior value. These same routers are used for the hand-driven
exercises in `../ipsec.md`, which turn it on for their own reasons, and unconditionally
switching it off at teardown would take away something this layer never added.

### NETCONF edits race the CLI's datastore lock

Run a `show` over the CLI and a NETCONF `<edit-config>` in quick succession and the edit
fails outright:

```
the configuration database is locked by session 293 admin tcp
```

Nothing is wrong. The CLI session's lock just hasn't been released by the time NETCONF asks
for it, and this layer mixes the two transports constantly, because some steps have no
modelled path and have to go over the CLI.

`roles/common/tasks/netconf-edit.yml` retries, and the condition is deliberately narrow: it
matches that lock text and nothing else. The tempting version is "retry until it succeeds",
and that version is worse than no retry at all, because it sits there re-sending a payload
the box has already told you is malformed, six times, before failing with a stale error.
Retry the transient thing, fail immediately on everything else.

### `Cisco-IOS-XE-ip` is a submodule, not an augmenting module

So the SSH kex payload carries **one** `xmlns`, the native one, not two. Get that wrong and
the edit is silently rejected.

Compare with `Cisco-IOS-XE-http`, which *is* a real module augmenting `/native/ip`, so the
HTTPS payload does carry its own namespace. Two nodes that look like siblings in the CLI,
two different namespace rules. There's no way to guess this correctly; you read the module
header or you pull the schema.

### SSH negotiation follows the client's preference, not the server's order

Both SSH leaf-lists are `ordered-by user`, and that ordering is honoured in the
configuration: push the list and the running config shows your order. It does not decide the
outcome. The client picks.

This is easy to misread as the config not applying. It applied. Your client just wanted
something else on the list. Check with `ssh -vv` from the client side, not by re-reading the
router.

The layer still pushes the whole container with `nc:operation="replace"` rather than merging
it, because a merge would insert entries in whatever position the box felt like, and then
"which algorithm is first" becomes a coin flip. Deterministic order is worth having even
though it isn't decisive.

### Use the plural `trust-points`

Under an IKEv2 profile, the list is `trust-points`. The singular `trustpoint` leaf exists in
the schema, is marked obsolete, and is rejected as an unknown element.

Note the asymmetry, because it will make you doubt yourself: you write **plural** over
NETCONF, and the CLI renders it **singular**.

```
! what you sent:      <trust-points><name>PQAUTO-MLDSA65</name></trust-points>
! what show run says:  pki trustpoint PQAUTO-MLDSA65
```

Both are correct. They're just different interfaces to the same node.

### MACsec key lifetimes ARE expressible

First analysis said they weren't, and first analysis was wrong. This one is a good
illustration of why you test against hardware instead of reading the schema alone, because
two of the three obvious paths really are dead ends and it's easy to stop there.

| Path | Result |
|---|---|
| `accept-lifetime` / `send-lifetime` | gated `when 'not(../../macsec)'`, rejected with unknown-element on a MACsec key chain |
| the older `lifetime` choice | status obsolete, also rejected |
| `macsec-lifetime/lifetime` | gated `when '../../../macsec'`, **accepted** |

The live container is the third one, gated the opposite way round from the first pair. Feed
it this and 26.2.1 renders exactly `lifetime local 00:00:00 Jan 1 2020 infinite`:

```xml
<macsec-lifetime><lifetime><lifetime-group-v1>
  <local/><start-hh-mm-ss>00:00:00</start-hh-mm-ss>
  <start-month>Jan</start-month><start-day>1</start-day>
  <start-year>2020</start-year><infinite/>
</lifetime-group-v1></lifetime></macsec-lifetime>
```

`roles/macsec_psk` leaves it out anyway, because a key with no lifetime is always valid and
the shorter payload is one less thing to get wrong. The template carries the working XML in a
comment for anyone who wants byte-for-byte parity with `macsec.md`.

### One legitimate screen-scrape remains for IPsec

`typedef crypto-auth-method` in `Cisco-IOS-XE-crypto-oper` stops at `crypto-ikev2-auth-eap`
and has no ML-DSA value. So `my-auth-method` and `peer-auth-method` cannot report an ML-DSA
signature, and the only place the box will tell you which signature algorithm authenticated
an SA is the `Auth sign:` line of `show crypto ikev2 sa detailed`.

That's it. That's the whole remaining gap on the IPsec read path. Everything else
post-quantum comes back structurally:

| Question | How it's answered |
|---|---|
| Did this SA use ML-KEM, and which parameter set? | `pqc-group` per IKEv2 SA, `typedef crypto-pqc-group-type`, four legal values |
| Is PPK working, manual or dynamic, any mismatches? | five counters under `crypto-ikev2-stats` |
| What is the SSH server offering, in what order? | `kex-options` leaf-list from the running datastore |
| What is the HTTPS server's PQ posture? | `secure-pqc-type` from the running datastore |
| What did we tell EAP-TLS to negotiate? | `access-session/{pqc-type,tls-version}` from the running datastore |

The single most important claim in this project, "this SA actually used ML-KEM-768", is a
leaf value. That's the payoff.

MACsec is worse off: there's no MKA or MACsec operational YANG on this platform at all, so
session state and the data-plane counters both come off `show` output. Two more scrapes,
both annotated. And read `macsec.md` on why the counters matter more than the status: MKA
will report `Secured` on a link that is encrypting exactly nothing.

### The initiator's `Auth verify` field is not trustworthy

This one is worth more than the scrape it lives in, because it changes what you are allowed
to conclude from a passing check.

Mid-migration, with the hub still on pre-shared keys, both spokes reported this:

```
R1:  Auth sign: MLDSA, Auth verify: MLDSA
R3:  Auth sign: MLDSA, Auth verify: MLDSA
R2:  Auth sign: PSK,   Auth verify: MLDSA     <- the hub, and it is signing PSK
```

Read the spokes on their own and the fabric looks finished. It isn't. The hub was
demonstrably still signing with a pre-shared key, and it is the one telling the truth:
the responder reports both directions correctly, the initiator does not.

The tempting reaction is to call this a display bug and ignore it. Don't, because the state
it is failing to display is real. **IKEv2 lets each peer choose its AUTH method
independently**, so "R1 signs with ML-DSA while R2 signs with a pre-shared key" is a
perfectly legal SA, not a broken one. A half-migrated fabric is a state you will actually
pass through, on purpose, every time you migrate one router at a time.

So an assertion that trusts `Auth verify` will pass on a fabric that is half classical. That
is why `roles/ipsec_pq_mldsa/tasks/verify-tunnel.yml` checks the **local `Auth sign:` on
every SA row**, and requires the row count to match the peer count. Each box asserts only
the direction it is authoritative for, and every box has to do it before the fabric counts
as migrated.

### The MACsec CAK can only be written once

Same shape of problem as the certificate one below, different cause, and this one has an
operator-visible consequence you need to know about.

Push a MACsec CAK, and IOS XE stores it encrypted:

```
key-string 7 09424B1A0D17393C2B3A37...
```

Re-run the unit and the role sends the plaintext hex again. The box renders it against the
existing leaf's type-7 prefix, gets `key-string 7 <plaintext hex>`, and its own parser
throws it out:

```
Rejecting: Key must consist of hex digits
```

So the second run didn't report a spurious change, it **failed**, `rc=2`, on the first
NETCONF edit. The interface half was idempotent the whole time; this was isolated to the
one leaf.

There is no clean fix available. The stored value is encrypted, so the role cannot compare
it against the desired value to decide whether a write is needed. What it can do is notice
the key chain already exists and leave it alone, and that's the approach `roles/macsec_psk`
takes: probe for the chain, and if it's there, omit the `<key>` element from the payload
entirely.

**The honest consequence: a rotated CAK in `group_vars` will not be applied.** Change
`macsec_cak` and re-run, and the box keeps the old key. That is a real limitation and
silently swallowing it would be worse than the bug, so the role says so out loud on every
such run, naming the chain it skipped and printing the rotation path. The task label also
changes, from `(with key-string)` to `(key-string skipped, chain exists)`, so it's visible
in the log as well as in the debug output.

To actually rotate, tear the chain down and rebuild:

```bash
ansible-playbook macsec-psk.yml -e state=absent -e macsec_psk_remove_globals=true
ansible-playbook macsec-psk.yml
```

`macsec_psk_remove_globals` defaults to false, so an ordinary teardown leaves the chain in
place. Rotation is the one time you want it true.

### An idempotency limit that cannot be fixed

IOS XE renders certificates **inline** in `running-config` and as an **`nvram:` file
reference** in `startup-config`:

```
running:  certificate self-signed 01
          30820430 30820298 ... 	quit
startup:  certificate self-signed 01 nvram:IOS-Self-Sig#3.cer
```

Measured on R1: 1,263 differing lines, every single one that artefact rather than a real
config delta.

So `cisco.ios`'s `save_when: modified`, which decides "modified" by diffing running against
startup, reports a change on every run forever on any box that holds a certificate. Which is
every box here. A textual save-config comparison can never converge on this platform.

The answer in `roles/common/tasks/save-config.yml` is to take saving out of the changed
contract entirely: saving is an action, not a piece of desired state, so it runs and reports
`changed=false`. Not a workaround so much as a correction of the category error.

## Q1: the ML-DSA certificate path

The question: can you get an ML-DSA identity certificate onto a router without a private key
ever touching a laptop?

### The IOS CA's signer is fine with ML-DSA

Established by experiment, not by reading anything.

Feed a raw PKCS#10 to an RSA-keyed IOS certificate server through
`crypto pki server <name> request pkcs10 terminal`, which bypasses SCEP entirely, and you get
a real certificate back. What came out, read off the issued certificate:

- `Public Key Algorithm: ML-DSA-65`
- `Issuer: CN=PROBEQ1CA`
- serial 2
- signed `sha512WithRSAEncryption`

And the CA logged `reqID=1 granted`. So the signing engine will happily certify an ML-DSA
public key. That eliminates the most obvious suspect straight away.

### SCEP is where it dies

The same CSR, the same CA, over **SCEP**, fails with `%PKI-2-CERT_ENROLL_FAIL`. An identical
RSA enrolment to that same CA succeeds.

One variable changed, so the failure is in the SCEP transport rather than in the signer.

[RFC 8894](https://www.rfc-editor.org/rfc/rfc8894) requires the requester to sign the CMS
`SignedData` wrapping a `PKCSReq` with its own key, so a missing ML-DSA CMS signer is the
obvious suspect.

**That specific mechanism was not isolated.** Nobody ran the paired client-and-CA debug
capture that would show whether the request dies before transmission or is rejected at the
CA's SCEP message layer. Those are different bugs with different fixes, and this experiment
doesn't distinguish them. What was measured is what's above: ML-DSA over SCEP fails, RSA over
SCEP to the same CA succeeds, and the same CA signs the same ML-DSA request fine when SCEP is
taken out of the path.

### An ML-DSA-keyed IOS CA is not configurable

Separate finding, same area. `crypto pki server` exposes no key-type or algorithm option at
all:

- `hash` offers sha1, sha256, sha384 and sha512, and nothing else
- all three `grant` values are documented purely in terms of SCEP
- its own key is auto-generated RSA-2048

So there is no knob, anywhere, that makes an IOS certificate server sign with ML-DSA.

### Two workable paths, and why the external CA won

Both of these work. Pick according to what you're optimising for, and understand that the
difference is not cosmetic.

**Path A: local IOS CA, terminal PKCS#10.** The router generates its own key, `crypto pki
enroll` on a trustpoint with `enrollment terminal pem` produces the request, and you hand it
to the IOS CA with `request pkcs10 terminal`. Everything stays on-box. No private key ever
exists outside a router, and no CA exists outside the fabric.

The cost: the CA can only be RSA-keyed. So you end up with an ML-DSA public key wrapped in a
**classical RSA signature**. The leaf is post-quantum, the chain above it is not. For a
production estate that might be an acceptable staging point. For a PQC lab it's an odd place
to land, because the thing you're demonstrating is precisely the property you gave up.

**Path B: external OpenSSL 3.6 ML-DSA CA.** Run the CA on the machine driving the playbooks,
where OpenSSL has native ML-DSA (it landed in 3.5). The router still generates its own key
and its own CSR; only the CSR goes out and only the certificate comes back, both public
material. You get a fully post-quantum chain, with `Signature Algorithm: ML-DSA-65` on the
leaf as well as an ML-DSA-65 subject public key.

The cost: a CA that lives outside the routers, with a private key on a workstation and all
the handling that implies.

| | Path A: local IOS CA | Path B: external OpenSSL CA |
|---|---|---|
| Router private key leaves the box | no | no |
| CA private key location | on the hub router | on the control node |
| Leaf subject public key | ML-DSA | ML-DSA |
| Leaf issuer signature | **RSA (classical)** | **ML-DSA** |
| Chain post-quantum end to end | no | yes |
| Extra machine in the trust path | no | yes |

**Decided: Path B is the design.** `roles/ipsec_pq_mldsa` implements the external OpenSSL
3.6 CA, which is why there's a `.lab-ca` directory on the control node and no
`crypto pki server` in that role any more.

The reason is the bold row in that table. `crypto pki server` exposes no key-type option
anywhere in its sub-commands and its own key is auto-generated RSA-2048, so the on-box route
can only ever hand you an ML-DSA public key under a `sha512WithRSAEncryption` signature.
Post-quantum key, classical signature. That isn't post-quantum authentication end to end, and
end to end is the whole point. Path B gives you `Signature Algorithm: ML-DSA-65` on the leaf
and a chain that's post-quantum the whole way up.

Path A stays documented, because "an RSA-keyed IOS certificate server will happily sign an
ML-DSA CSR" is a genuinely useful finding and shouldn't be buried. Run it if keeping the CA
inside the fabric matters more to you than the signature over the leaf. Just be clear about
what you're accepting: the leaf is post-quantum, the chain above it isn't.

## Unit layering

Nine units, one playbook each, all the logic in a role, and every one of them tears its own
work down with `-e state=absent`.

| Unit | What it is |
|---|---|
| `ipsec-baseline` | classical hub-and-spoke IPsec, the "before" picture |
| `ipsec-pq-ppk` | RFC 8784 post-quantum pre-shared key |
| `ipsec-pq-mlkem` | native hybrid ML-KEM key exchange (RFC 9370) |
| `ipsec-pq-mldsa` | ML-DSA certificate authentication |
| `macsec-psk` | MACsec keyed by a pre-shared secret |
| `macsec-eaptls` | MACsec keyed by an EAP-TLS handshake |
| `macsec-pq` | ML-KEM inside that handshake |
| `ssh-pq` | ML-KEM hybrid SSH key exchange |
| `tls-pq` | PQ posture on the management HTTPS server |

Plus `bootstrap.yml`, which switches the transport on, and `verify.yml`, which every unit
imports at the end and which is also fine to run on its own.

### Baseline and overlay are deliberately separate

`ipsec-baseline` builds a classical tunnel that works. `ipsec-pq-mlkem` adds two YANG leaves
to one proposal and nothing else.

That split is the whole pedagogical point. A reader can stand up a classical IPsec tunnel,
watch it come up, and then add ML-KEM as a distinct step and see exactly what changed. Fold
them together and you lose the ability to answer "what did post-quantum actually cost me
here", which is the question the repo exists to answer.

### PPK and ML-KEM are alternatives, never additive

Two answers to the same problem. PPK needs no new primitives but gives you a secret to
distribute, rotate and store on every pair of peers. ML-KEM has nothing to distribute but
needs both ends to speak it.

So each role probes for the other and **refuses to run**, naming the exact command that
clears the way. Neither auto-removes the other, on purpose: quietly undoing configuration you
didn't ask it to touch is how a lab stops matching what you think it's running.

The round trip PPK, teardown, ML-KEM, teardown, PPK is meant to work, so you can see both
answers on the same hardware in one sitting.

### One non-obvious decision: IKE fragmentation lives in the baseline

`crypto ikev2 fragmentation mtu 1400` is provisioned by `ipsec_baseline`, not by either PQ
role. That looks wrong at first glance, since a classical tunnel doesn't need it.

Here's why. **Both** ML-KEM and ML-DSA need fragmentation: ML-KEM-768 puts a 1,184-byte
encapsulation key into `IKE_INTERMEDIATE`, and ML-DSA-65 pushes `IKE_AUTH` to roughly 20 KB
across 16 frames. Without it the handshake never completes.

If either PQ role owned the setting, tearing that one down would pull fragmentation out from
under the other, and a tunnel would break for a reason with nothing to do with the unit you
just removed. Debugging that is miserable, and it's entirely avoidable. The baseline pays for
it (it costs nothing on a classical tunnel) and the PQ units stay independent of each other,
which is the property that matters.

### Why every object name starts with `PQAUTO-`

Automation that shares a namespace with hand-driven config will eventually delete the
reader's work, and `-e state=absent` turns "eventually" into "the first time you tear
anything down". These are the same three routers the CLI walkthroughs run on, and the names
those walkthroughs tell you to type are sitting right there in
[`device-configs/`](../device-configs/): `SPOKE-R1`, `IPSEC-R3`, `TP-MLDSA65`, `CA_TP`,
`EAP-PROFILE`, `PQ-MACSEC-POLICY`, `LINKSEC_POLICY`. Every one of those was, character for
character, a name this layer used to generate.

`Tunnel1` is the sharpest example, because a teardown doesn't merely overwrite it: it runs
`no interface Tunnel1` and the reader's hand-built tunnel is gone with everything on it.
That's why `tunnel_id` in `group_vars/all/topology.yml` is 101 and 102, and why the object
names in `crypto.yml` and the MACsec and PKI names all carry the prefix. **The prefix is a
safety mechanism, not a style choice.** It's also the second line of defence behind the
protected-object guards, since a role that only ever removes `PQAUTO-` objects can't remove
one it didn't create.

#### Two names are deliberately not prefixed

`pki_names.ca_server` is `CA_Server`, with no prefix, and that is the one exception. It's
worth being explicit about, because "everything carries the prefix" is otherwise a claim you
can check in thirty seconds and find false.

**The EAP-TLS identity user** is the other one. `roles/macsec_eaptls` creates a local
`username R1` on R1, `username R2` on R2, and calling it `PQAUTO-R1` is not an option:
that name is the certificate CN, EAP sends the CN as its identity, and `dot1x credentials`
claims the same string. Prefix it and the three stop matching and authentication fails. The
name is determined by the protocol, not by us.

It is also the reason the teardown gate exists. A prefixed object can be removed safely by
name; this one can't be told apart from a user you created, so removing it is opt-in.

**`pki_names.ca_server`** is the first, and the more interesting case.

Why no prefix? Because a distinct name isn't available. IOS XE runs a single certificate
server per box, and `crypto pki server <name>` is bound to a trustpoint of the same name, so
you cannot stand a second, differently-named CA up alongside the reader's. There is one slot
and `macsec.md` already tells the reader to fill it.

So what makes it safe isn't the name, it's that **nothing in this layer ever removes it.**
`roles/common/tasks/ios-ca.yml` is only ever included under `when: state == 'present'`. There
is no teardown path that touches a certificate server, prefixed or not. A build merges
`grant auto` and the two EKUs into whatever `CA_Server` is already there, which is additive,
and it's what the doc configures by hand anyway.

That's the general shape of the rule, then: the prefix protects objects a teardown can
delete. For the one object a teardown can never reach, the protection is that there is no
code path to reach it with.

The flip side is that **`CA_Server` survives every teardown in this layer**, so after a full
run of everything followed by a full teardown, R2 still has a running certificate server. It
is doing no harm, and `macsec.md` wants it there, so leaving it is the right default. If you
want it gone, that is a deliberate hand-driven act:

```
conf t
 crypto pki server CA_Server
  shutdown
 exit
 no crypto pki server CA_Server
 no crypto pki trustpoint CA_Server
end
```

Two things to expect. `no crypto pki server` asks for confirmation, and removing the server
does not remove the trustpoint of the same name, which is why the second `no` is there.
Deleting the server destroys the CA's private key, so anything it issued can no longer be
renewed or revoked. Nothing here does that for you on purpose.

### Two things teardown deliberately does not remove

Teardown isn't perfectly complete, and both gaps are on purpose. Better to say so than to
imply it's surgical.

**`dot1x system-auth-control` stays enabled.** It's a box-wide global with no name, so
there's no way to tell "the one this role added" from "the one that was already there", and
all three routers carry it already. `macsec.md` Exercise 2 needs it. Removing it switched
802.1X off box-wide and silently killed the reader's own MACsec session, so
`roles/macsec_eaptls` leaves it exactly as it leaves `aaa new-model`: on.

**`no username` is not run unless you ask for it.** The EAP-TLS unit creates a local user
whose name has to equal the certificate CN, and removing a username on IOS XE prompts for
confirmation, which is the class of thing this layer refuses to answer blind. So the removal
sits behind `macsec_eaptls_remove_identity_user`, default false, and an ordinary teardown
leaves a bare `username R1` behind. Set it true if you want it cleaned up, and read the note
below on why that user cannot carry the `PQAUTO-` prefix.

**The interface-scoped `no ...` lines in the MACsec teardown do overlap your own interface
config.** `roles/macsec_eaptls` and `roles/macsec_psk` configure the same
`TwoGigabitEthernet0/0/0` that `macsec.md` walks you through configuring by hand, and their
teardowns take `macsec network-link`, the `access-session` lines and the `dot1x` lines back
off it. No prefix can fix that, because the interface has no namespace to prefix. If you
built Exercise 2 by hand on that link, expect a MACsec teardown here to dismantle part of it.

## What is verified and what is not

Blunt, because this is exactly where a design document usually starts lying.

**All nine units have now been run end to end on real hardware.** Every one was applied,
asserted, re-run for `changed=0` and torn down, with the teardown independently checked
from a separate connection. The output is in [`captured/`](captured/), indexed, so you can
read what the box actually said rather than take this table's word for it.

| Unit | Status |
|---|---|
| `bootstrap.yml` | proven, `changed=0` on a re-run across three routers that each started in a different state, and on the **first** run too, so the role recognises pre-existing state without a priming run in front of it |
| `ipsec-baseline` | proven |
| `ipsec-pq-ppk` | proven, plus the mutual-exclusion guard verified by watching `ipsec-pq-mlkem` refuse while a PPK was present |
| `ipsec-pq-mlkem` | proven, `pqc-group = pqc-gt-mlkem768` on a live SA |
| `ipsec-pq-mldsa` | proven, including a **clean build from zero state, `changed=10` on all three routers** |
| `macsec-psk` | proven, and the re-run failure it exposed is fixed (see above) |
| `macsec-eaptls` | proven |
| `macsec-pq` | proven as far as the platform allows, which is not all the way. See the limit below |
| `ssh-pq` | proven, with the negotiated kex confirmed from an OpenSSH client rather than from the router |
| `tls-pq` | proven, plus the negative case: setting `non-pqc` drops the handshake to classical P-384 |

The ML-DSA row used to read "written and statically validated, unrun", and it carried four
specific worries. All four are now settled, and it's worth recording how, because three of
them were wrong in an interesting direction:

- the **multi-line certificate paste** works. `crypto pki authenticate` and
  `crypto pki import ... certificate` both accept a pasted body through the strict-prompt
  wrapper
- the **prompt wording** matched what the wrapper expected
- **IOS does accept an ML-DSA-signed CA certificate**, which was the assumption Path B rests
  on entirely, and the one that would have forced a rewrite. `show crypto pki certificates
  verbose` reports `Signature Algorithm: ML-DSA-65` on the CA and `Public Key Algorithm:
  ML-DSA` on the identity
- the **`show crypto pki certificates verbose` wording** the regexes anchor on is confirmed

One thing did have to change, and it wasn't on the list: the ML-DSA profile swap **replaces**
the authentication method rather than adding one, so between the first router switching and
the second following, the tunnel is legitimately down. That's why the role runs `serial: 1`.
A parallel run leaves the fabric in a state where each end has moved on without the other.

### The screen-scrape checklist

Every `show` parse in this layer anchors on literal wording, and literal wording is exactly
the thing that changes under you. Run the command on the left, compare, then fix the regex
or tick it off.

**The checklist is complete.** All seven items are confirmed against a live box, and the
three that were still open are at the bottom. One of them found a broken check.

#### Confirmed against hardware

C8235-G2, IOS XE 26.02.01eftr2, on a live MACsec link and a live local CA.

| Command | What was measured |
|---|---|
| `show mka sessions` | Confirmed. A populated session is a **two-line record**: `Status` is the 4th field of the second line and the CKN is that line's last field, exactly as the layer assumed from an empty table. Totals wording confirmed as `Total MKA Sessions.......`, `      Secured Sessions... ` and `      Pending Sessions... `, with the dot runs part of the literal |
| `show macsec statistics interface <if>` | Confirmed as `Out Pkts Encrypted:`, which is what `roles/verify` greps. It appears **twice**, once under `Transmit SC Counters` and again under `Transmit SA Counters`; the role takes `\| first`, so it reads the SC one. It is cumulative, so `roles/verify` now takes a **delta across two reads** with 20 pings in between and asserts the delta is positive. Measured 40 to 42 for 20 pings, because both ends ping at once so each box encrypts its own requests plus the peer's replies |
| `show crypto pki server` | Confirmed. `Status: enabled` once running, `Status: disabled` with `State: initial` before. The fingerprint line is `CA cert fingerprint: ` followed by four space-separated 8-hex-digit groups, e.g. `1D4742CB 3C2C4F8E A7116351 C28B1A29`, which is why the regex strips spaces |
| `show crypto pki certificates <tp>` | Confirmed. The identity block is headed by a bare `Certificate` and the CA's own by `CA Certificate`, so the newline-anchored regex is doing real work |
| `crypto pki server <n>` / `no shutdown` | Confirmed non-obvious. The passphrase prompts are exactly `Password:` then `Re-enter password:`, preceded by `%Some server settings cannot be changed after CA certificate generation.` and `% Please enter a passphrase to protect the private key`. Captured by sending the command with a prompt regex that cannot match, so nothing was answered |
| `policy-map type control subscriber <n>` | **Not previously listed, and it cost two failed runs.** On a box still in legacy authentication display mode this asks `Do you wish to continue? [yes]:` before converting to CPL. `ios_config` cannot answer a prompt, so it hangs for the full command timeout. Nothing in running-config distinguishes the two modes, so it cannot be detected in advance. `roles/macsec_eaptls/tasks/aaa-stack.yml` now enters the policy-map once through the strict-prompt wrapper |

There is one thing hardware could **not** confirm: nothing in IOS XE 26.2 reports the
key exchange group an EAP-TLS handshake actually negotiated. Not `show access-session
... details`, not `show mka sessions`, not the `Cisco-IOS-XE-sanet` container. So the
post-quantum evidence for `macsec-pq.yml` is the two configured leaves, plus a forced
re-authentication producing a fresh CKN under them, plus `hybrid` failing closed
instead of downgrading. That is strong, but it is not a direct observation of
ML-KEM-768 on the wire, and this document should not pretend otherwise.

#### The last three, now closed

| Command | What was measured |
|---|---|
| `show crypto ikev2 sa detailed` | Confirmed. The wording is `Auth sign:` and the value is spelled `MLDSA`, no hyphen and no parameter set, so `Auth sign: MLDSA`. Still the one legitimate screen-scrape on the IPsec read path. And read the `Auth verify` finding above before trusting the line next to it |
| `crypto key generate mldsa param <n> label <l>` | Confirmed non-interactive. It prints `[OK] (elapsed time was 0 seconds)` and asks nothing. This is now proof rather than observation: it was run through a module with no prompt handler at all, so a prompt would have hung the task instead of passing it |
| `show version` | **The check was broken.** `show version \| include Technology\|Current` returns only the two header lines on 26.02.01eftr2, because the data row reads `Smart License  Subscription advantage          advantage` and contains neither `Technology` nor `Current`. It matches headers and never the value, so it looks like it works and tells you nothing. Two things that do work: `show version \| begin Technology`, and `show license summary` |

That last one is the useful reminder in this whole section. A screen-scrape doesn't fail
loudly when the wording moves. It returns something plausible, and you carry on believing
you checked.
