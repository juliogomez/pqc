# What the automation actually does, on real hardware

You don't need three C8235-G2s to see what these playbooks do. This directory is the
output of running them, on IOS XE 26.02.01eftr2, across two sittings. Every file here
came off a router or out of `ansible-playbook`; nothing is reconstructed and nothing is
idealised.

Read this file first. On its own the directory is 83 files of `show` output, which is a
pile rather than evidence.

## How it's organised

One directory per unit, numbered in the order [README.md](../README.md) runs them. Inside
each, the same three files where they exist:

| File | What it is |
|---|---|
| `01-apply.log` | the configure-and-assert run. This is the one that proves the unit works |
| `02-rerun-changed-0.log` | the same command again. Scroll to `PLAY RECAP` and look for `changed=0` |
| `03-teardown.log` | `-e state=absent` |

Then `evidence-*.txt` for the operational proof, `teardown-verified-*.txt` for the
independent `show` check that the removal really happened, and `trap-*` or `bug-*` for
the things that went wrong.

`config-diffs/` has the before-and-after for the units where two captures share a
command, so you can see the exact lines a unit owns without reading a whole config.

## Start here if you only read three files

- **[`config-diffs/ipsec-pq-mlkem-proposal-r2-hub.diff`](config-diffs/ipsec-pq-mlkem-proposal-r2-hub.diff)**
  is the whole idea in one line: `+ pqc mlkem768 optional`. That's the unit.
- **[`03-ipsec-pq-mlkem/evidence-negotiated-group-r1.txt`](03-ipsec-pq-mlkem/evidence-negotiated-group-r1.txt)**
  is the payoff: `PQC Key Exchange: ML-KEM-768` on a live SA.
- **[`05-macsec-psk/bug-rerun-rpc-error-verbatim.txt`](05-macsec-psk/bug-rerun-rpc-error-verbatim.txt)**
  is what a real defect looks like when you find it, with the box's own parser telling
  you why.

## Screen-scrape or YANG?

This matters, so it's marked per file rather than assumed. Anything labelled
**screen-scrape** is text parsing against literal wording, and literal wording changes
under you between releases. Anything labelled **YANG** came back as a leaf value from
the operational datastore and is as stable as the model.

The short version: everything post-quantum on the IPsec read path is YANG except
`Auth sign:`, and **MACsec is entirely screen-scrape** because this platform has no MKA
or MACsec operational YANG at all. [DESIGN.md](../DESIGN.md) has the reasoning.

---

## 00-verify

`verify.yml` on its own, which is what you run when you want to know where you left the
lab.

| File | What to look for |
|---|---|
| `verify-standalone-pass.log` | the posture report for all three routers. The `msg` block per host is the whole point |
| `verify-standalone-transient-failure.log` | `Could not open socket to <ip>:830` on all three at once, then a pass on immediate retry. Port 830 was open the whole time. This is why `roles/common/tasks/netconf-fetch.yml` retries |

## 01-ipsec-baseline

**Honest gap: there is no build log for this unit.** The classical baseline was stood up
in a sitting whose artefacts were lost with `/tmp`, and rebuilding the whole fabric just
to re-capture it was out of scope for the close-out. What's here is the teardown and the
independent check that it left nothing behind. The baseline objects are visible under
their `PQAUTO-` names in every PPK, ML-KEM and ML-DSA log in this directory, so you can
see what it built even without its own log.

| File | What to look for |
|---|---|
| `03-teardown.log` | `changed=4` per router |
| `teardown-verified-r*.txt` | zero `PQAUTO-` IKEv2 and IPsec objects, zero `Tunnel` interfaces, and the underlay deliberately untouched: `Gi0`, `Vlan12` and `Vlan23` all still up/up. Screen-scrape |

## 02-ipsec-pq-ppk

RFC 8784. A secret you distribute out of band, mixed into the IKEv2 key schedule.

| File | What to look for |
|---|---|
| `01-apply.log` | `changed=2` per router |
| `02-rerun-changed-0.log` | `changed=0` |
| `03-teardown.log` | `changed=2` |
| `evidence-quantum-resistance-counters-r1.txt` | `Sessions with Quantum Resistance: 1  Manual: 1  Dynamic: 0`, and all three PPK failure counters at zero. **YANG** (five counters under `crypto-ikev2-stats`); the file shows the CLI rendering of the same data |
| `evidence-quantum-resistance-counters-r2-hub.txt` | the hub, so `2` and `2`, one per spoke |
| `teardown-verified-r2-hub.txt` | counters back to `0`, no `ppk` lines, and the classical `pre-shared-key` lines still present. That last one is the point: the PPK sits *inside* the keyring peers, so an over-broad remove would take the IKE pre-shared key with it |
| `negative-test-mlkem-refuses-while-ppk-present.log` | the mutual-exclusion guard doing its job. `rc=2`, `changed=0`, and the error names the exact command that clears the way |

The PPK value itself is redacted in every file here. So is the classical pre-shared key.

## 03-ipsec-pq-mlkem

RFC 9370 additional key exchange. Two YANG leaves on one proposal.

| File | What to look for |
|---|---|
| `01-apply.log` / `02-rerun-changed-0.log` / `03-teardown.log` | `changed=2`, then `changed=0`, then `changed=2` |
| `evidence-negotiated-group-r1.txt` | two lines that did not exist before the unit: `PQC Key Exchange: ML-KEM-768` and `Quantum-safe Encryption using PQC: ML-KEM-768`. **YANG** for the real assertion (`negotiated_pqc_group = pqc-gt-mlkem768`, see the apply log); this file is the CLI view |
| `evidence-negotiated-group-r2-hub.txt` | the hub reporting it for both legs |
| `teardown-verified-r1.txt` | both PQC lines gone, SA back to classical group 20 |

**`DH Grp:20` does not change, and should not.** This is hybrid: classical ECDH P-384 is
still negotiated and ML-KEM-768 is mixed in alongside it. Anyone looking for a different
DH group as proof will conclude the unit did nothing.

## 04-ipsec-pq-mldsa

ML-DSA certificate authentication. The biggest unit and the one with the most moving
parts: key generation on the box, a CSR out, offline ML-DSA signing, a certificate back.

| File | What to look for |
|---|---|
| `01-apply-clean-build-changed-10.log` | the full path from zero state, `changed=10` on all three. Certificate and CSR bodies are elided (see "About the elisions" below) |
| `02-rerun-changed-0.log` | `changed=0` |
| `03-teardown.log` | `changed=4` per router |
| `convergence-from-half-applied.log` | the interesting one. Two spokes already migrated, the hub not. `r1 changed=0, r2 changed=8, r3 changed=0`, so the role recognised the finished work and did only the hub |
| `evidence-auth-sign-mldsa-r*.txt` | `Auth sign: MLDSA` per SA. **Screen-scrape, and the only one on the IPsec read path**, because `typedef crypto-auth-method` has no ML-DSA value |
| `evidence-responder-view-r2-hub.txt` | the hub's view of both legs. Compare it with the spokes' view and see [DESIGN.md](../DESIGN.md) on why the initiator's `Auth verify` field cannot be trusted |
| `keygen-non-interactive-verbatim.txt` | `crypto key generate mldsa` prints `[OK] (elapsed time was 0 seconds)` and asks nothing. Proven rather than observed: it ran through a module with no prompt handler, so a prompt would have hung |
| `pki-certificates-verbose-r2.txt` | `Public Key Algorithm: ML-DSA` on the subject and `Signature Algorithm: ML-DSA-65` on the signature. Post-quantum leaf under a post-quantum signature, which is the whole reason the CA is off-box |
| `crypto-key-mypubkey-r2.txt` | `Key type: ML-DSA-65 KEYS`. Public key data elided |
| `teardown-verified-r*.txt` | trustpoint gone, key gone, `service internal` restored to its recorded prior value, protected objects (`TP-self-signed-*`, `SLA-*`, `CISCO_IDEVID_SUDI*`, `NETCONF_SSH_RSA_KEY`) all intact |

## 05-macsec-psk

MACsec keyed by a static CAK. Quantum-safe by having no key exchange at all, and a dead
end for exactly the same reason.

These logs are from the close-out run, against the role **after** the idempotency fix.

| File | What to look for |
|---|---|
| `pre-state-r1.txt` | the interface is 3 lines, no key chain, no MKA policy, zero sessions |
| `01-apply.log` | `changed=2`. The NETCONF task line says `(with key-string)`. In the verify block: `mka_secured_sessions: 1`, `cipher: GCM-AES-256`, `ckn: 01`, `out_pkts_encrypted_DELTA: 46` |
| `evidence-mka-secured-r1-r2.txt` | independent confirmation from a separate connection. `Secured` in the Status column, CKN `01`, cross-matching SCIs, `Transmitting: TRUE`. All **screen-scrape** |
| `02-rerun-changed-0.log` | `changed=0`. The task line now says `(key-string skipped, chain exists)` and a debug task spells out, on both routers, that the stored CAK was left untouched and how to rotate it |
| `03-teardown.log` | `changed=2`, run with `-e macsec_psk_remove_globals=true` |
| `teardown-verified-r1-r2.txt` | key chain gone, MKA policy gone, interface back to 3 lines, `MACsec is not enabled`, Vlan12 still up |
| `bug-rerun-failed-before-the-fix.log` | what the second run did **before** the fix: `rc=2`, and it died on the first NETCONF edit |
| `bug-rerun-rpc-error-verbatim.txt` | the box's own diagnosis. Three isolating edits, and `Rejecting: Key must consist of hex digits` on the third |
| `bug-how-the-cak-is-stored.txt` | why. The stored form is `key-string 7 <blob>`, so the box renders our plaintext hex against that `7` |

**The CKN is worth knowing.** `01` here comes straight from `macsec_key_id`. The EAP-TLS
path gives you 32 hex characters instead, which is how you tell the two apart at a glance.

**The delta, not the total.** `out_pkts_encrypted` is cumulative, so a non-zero value
proves nothing about now. `verify.yml` reads it, pushes 20 pings, reads it again and
asserts the difference. The measured delta is about double the ping count because both
ends ping at once, so each box encrypts its own requests plus the peer's replies.

## 06-macsec-eaptls

MACsec keyed by an EAP-TLS handshake instead of a typed-in secret. This is the
scaffolding `macsec-pq` needs, because a handshake is a thing ML-KEM can go into.

| File | What to look for |
|---|---|
| `pre-state-r1-r2.txt` | bare interface, no sessions, no certificate |
| `01-apply.log` | the successful build |
| `02-rerun-changed-0.log` | `changed=0` on all three hosts |
| `03-teardown.log` | interface first, then the AAA stack |
| `evidence-secured-and-user-name-cn.txt` | the good stuff. `Secured`, `GCM-AES-256`, a 32-hex-character CKN matching on both ends, and `User-Name: R2` in `show access-session ... details`, which is the certificate CN, so EAP really did carry the certificate identity. Also `dot1x` and `dot1xSup` both at `Authc Success`, the symmetric supplicant/authenticator pair the role depends on. All **screen-scrape** |
| `trap-cpl-conversion-prompt-hang.log` | the failure that cost two runs. R2 sat for the full 180-second timeout on `policy-map type control subscriber` |
| `trap-cpl-conversion-verbatim.txt` | the question it was sitting on: `Do you wish to continue? [yes]:`, before an **irreversible** conversion. `ios_config` cannot answer a prompt, and nothing in running-config distinguishes the two display modes, so this cannot be detected in advance, only handled |
| `trap-ca-passphrase-prompt-verbatim.txt` | the CA passphrase prompts, captured by sending the command with a prompt regex that cannot match, so nothing was answered. Exactly `Password:` then `Re-enter password:`. Read the hazard note at the bottom: aborting leaves the CLI *at* the question and the next command on that connection is eaten as the answer |
| `teardown-verified-r1-r2.txt` | interface back to its pre-state |

## 07-macsec-pq

Two leaves that put ML-KEM inside that TLS 1.3 handshake.

| File | What to look for |
|---|---|
| `01-apply.log` / `02-rerun-changed-0.log` / `03-teardown.log` | `changed=2` per end, then `changed=0`, then `changed=2` |
| `evidence-fresh-ckn-under-pq-settings.txt` | `access-session pqc-type hybrid` and `access-session tls-version 1.3` in the config, and a **new CKN** after a forced re-authentication, so the live session was keyed by a handshake made under the PQ settings rather than a stale classical one |
| `teardown-verified-r1-r2.txt` | both leaves gone, and the link stayed up and re-secured on a new CKN rather than dropping |

**Read the limit before you quote this unit.** Nothing in IOS XE 26.2 reports the key
exchange group an EAP-TLS handshake negotiated: not `show access-session ... details`,
not `show mka sessions`, not the `Cisco-IOS-XE-sanet` container. The evidence here is the
configured leaves, plus a fresh handshake under them, plus `hybrid` failing closed
instead of downgrading. That is strong. It is not a wire observation, and this directory
does not pretend it is.

## 08-ssh-pq

ML-KEM hybrid SSH key exchange, plus the host key pin that stops you locking yourself out.

| File | What to look for |
|---|---|
| `01-apply.log` | three routers in three different starting states, so it doubles as a convergence test: `r1 changed=0` (already correct), `r2 changed=1`, `r3 changed=1` |
| `02-rerun-changed-0.log` / `03-teardown.log` | `changed=0`, then `changed=1` |
| `evidence-ssh-vv-negotiated-kex.txt` | the one that earns its keep. Real `ssh -vv` from an OpenSSH 10.3 client. The server's list starts with ML-KEM, and the session still lands on `ecdh-sha2-nistp256` because **the client picks**. Force it with `-o KexAlgorithms=mlkem768x25519-sha256` and the hybrid completes. Both results are in the file |
| `teardown-verified-r1.txt` | no `ip ssh server algorithm` lines left |

If you take one thing from this unit: re-reading the router will never tell you what your
session negotiated. Check from the client.

## 09-tls-pq

`ip http secure-pqc-type`. One modelled leaf, one NETCONF edit, no CLI escape hatch.

| File | What to look for |
|---|---|
| `01-apply.log` / `02-rerun-changed-0.log` / `03-teardown.log` | `changed=1`, then `changed=0`, then `changed=1` |
| `evidence-openssl-group-with-all.txt` | `Negotiated TLS1.3 group: X25519MLKEM768` on all three |
| `knob-proof-set-non-pqc.log` | the run that sets `-e pq_https_pqc_type=non-pqc` |
| `evidence-openssl-full-handshake-non-pqc-r1.txt` | and the result: `Peer Temp Key: ECDH, secp384r1, 384 bits`, purely classical |
| `evidence-openssl-group-line-absent-non-pqc.txt` | the same experiment across all three, where the evidence is an **absence**: the `Negotiated TLS1.3 group` line is simply not there. The file looks empty. That is the finding |
| `evidence-openssl-group-restored.txt` | back to `X25519MLKEM768` |
| `teardown-verified.txt` | still `X25519MLKEM768`, because that is the platform default |

**26.2 already negotiates X25519MLKEM768 out of the box.** So on a default config this
unit changes the *stated posture*, not the wire. Setting `non-pqc` is what proves the
leaf is load-bearing, which is why that experiment is in here.

There's a trap in verifying this one: after a successful apply,
`show running-config | include secure-pqc-type` returns **nothing**, because `all` is the
platform default and IOS suppresses defaults. Use `show running-config all` or read it
over NETCONF. Anyone grepping a plain running-config will wrongly conclude it did not
apply.

## config-diffs

Real `diff -u` output, built by pulling the same `show` section out of two captures. The
header of each file names the command and the two sources.

| File | What it shows |
|---|---|
| `ipsec-pq-mlkem-proposal-r2-hub.diff` | one added line, `pqc mlkem768 optional` |
| `ipsec-pq-ppk-keyring-r2-hub.diff` | one added line per peer, PPK redacted |
| `ipsec-pq-mldsa-profile-r1.diff` | `pre-share` to `mldsa-sig` on both directions, plus a trustpoint. Note it **replaces** rather than adds, which is why the tunnel is legitimately down between one end switching and the other following, and why that unit runs `serial: 1` |
| `ssh-pq-algorithms-r1.diff` | the two lines the unit owns |
| `macsec-psk-interface-r1.diff` | **no differences**, and that is the pass: build, re-run, teardown, and the interface is byte-identical to how it started |
| `macsec-eaptls-interface-r1.diff` | also no differences, after the two missing `no` lines were added to the teardown. It did not look like this before that fix |

## platform-notes

Wording confirmations. Every one of these is a regex in the layer that would break
silently if the wording moved.

| File | What it settles |
|---|---|
| `licence-check-broken-and-alternatives.txt` | `show version \| include Technology\|Current` returns **only headers** on 26.02.01eftr2, because the data row reads `Smart License  Subscription advantage          advantage` and contains neither word. `show version \| begin Technology` and `show license summary` both work |
| `mka-sessions-empty-table.txt` | the totals block renders with zero sessions and uses **dot leaders**, not colons: `Total MKA Sessions....... 0` |
| `macsec-statistics-when-not-enabled.txt` | with MACsec off the command returns one line and no counters, so there is nothing to measure |
| `pki-certificates-block-headings.txt` | the identity block is a bare `Certificate` and the CA's is `CA Certificate`, which is what the newline-anchored regex depends on |

## final-state

`r1-audit.txt`, `r2-audit.txt`, `r3-audit.txt`: the independent audit at the end of the
run. No `PQAUTO-` objects, no `service internal`, no `ip ssh server algorithm` lines, no
`secure-pqc-type`, no tunnels, no MKA, zero IKEv2 SAs, protected trustpoints intact,
config-register `0x2102`.

Two things a later audit of the same lab will show that these files don't, both expected
and both left on purpose:

- **`crypto pki server CA_Server` on R2.** No teardown in this layer removes a certificate
  server. [DESIGN.md](../DESIGN.md) explains why and shows the manual removal.
- **`ip ssh server algorithm kex mlkem768x25519-sha256 ...` on R1**, and
  `ip radius source-interface GigabitEthernet0` on R3. Both are in the committed reference
  configs under [`device-configs/`](../../device-configs/), so they are the lab's baseline
  rather than residue. The audits above were taken at the bottom of a full teardown, which
  is below that baseline.

---

## About the redactions and the elisions

Two different things, both deliberate.

**Redactions are secrets.** Every credential in this directory is replaced with an
obvious placeholder: `<REDACTED-IKEV2-PSK>`, `<REDACTED-PPK-HEX>`, `<REDACTED-CAK>`,
`<REDACTED-PASSWORD>`, `<REDACTED-SECRET-HASH>`. This is done by pattern *and* by
context, so a value nobody thought of still gets caught: anything following
`pre-shared-key`, `key hex`, `key-string`, `secret <n>` or `password <n>` is replaced
whether or not it matches a known literal.

**Elisions are bulk.** Certificate and CSR bodies, the NETCONF capability list the box
echoes on every edit, and public key hex dumps are all public material, but an ML-DSA-65
certificate is 40 KB of hex and it drowns everything around it. Those are collapsed to
`[... elided by the capture scrubber]`. Nothing that carries meaning was elided; if a
file looks like it is missing something, it is missing a blob, not a finding.

## What isn't here

Stated plainly, because a gap you know about is worth more than one you find later.

- **No `bootstrap.yml` log.** Running it means pushing `netconf-yang`, and
  the transport was off limits during the close-out run. The unit is proven elsewhere:
  every NETCONF task in every log here only works because bootstrap already ran.
- **No `ipsec-baseline` build log**, as covered above.
- **No wire observation of ML-KEM inside EAP-TLS**, as covered under `07-macsec-pq`. The
  platform does not expose it.
