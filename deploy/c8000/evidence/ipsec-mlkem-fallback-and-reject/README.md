# ML-KEM on IOS XE: matching the IKE SA to its child SAs, plus fallback and rejection

A reader asked two good questions:

1. How do you tie "this tunnel negotiated hybrid ML-KEM" to the child SAs that were actually installed, and to their ESP counters?
2. What does it look like when a classical-only peer shows up? Does it fall back, or is it rejected?

The repo had the first half of question 1 (the IKE SA view) and no capture at all of the second. So I ran it on real hardware. This folder is that run, sanitised.

## The setup

| | |
|---|---|
| Routers | R1 C8355-G2, R2 C8235-G2 (hub), R3 C8455-G2 |
| Software | IOS XE 26.02.01eftr2 on all three |
| Date | 26 Nov 2026 |
| Topology | hub-and-spoke, like [Exercise 4 in ipsec.md](../../ipsec.md#exercise-4-phased-migration). Tunnel101 R1 to R2, Tunnel102 R2 to R3 |
| IKE | AES-CBC-256, SHA-512, group 20, PSK auth, `crypto ikev2 fragmentation mtu 1400` |
| ESP | `esp-gcm 256`, tunnel mode, no PFS (except case 4) |

Heads up: this was configured by hand over the CLI, NOT with the playbooks in "../../automation/". So the object names are different (`HUB-PROPOSAL`, `PROFILE-R1`...). The full base config per router is in "config/". The only thing that changes between cases is the `pqc` line in each IKEv2 proposal.

| Case | R1 | R2 (hub) | R3 | What happened |
|---|---|---|---|---|
| "01-hybrid/" | `pqc mlkem768 optional` | `pqc mlkem768 optional` | `pqc mlkem768 optional` | both legs ML-KEM-768 |
| "02-fallback/" | `optional` | `optional` | no `pqc` line | R1 leg ML-KEM-768, R3 leg classical, tunnel up |
| "03-reject/" | `optional` | `pqc mlkem768` (no `optional`) | no `pqc` line | R1 leg ML-KEM-768, R3 leg never comes up |
| "04-child-pfs/" | as case 1, plus `set pfs group20 pqc mlkem768` on the R1-R2 IPsec profiles | | | child SA runs its own ML-KEM at rekey |

By the way, there's no `required` keyword. `pqc mlkem768 ?` offers only `optional` (and more ML-KEM sizes). "Required" just means leaving `optional` out.

## Question 1: how do you correlate them?

### One command does most of the work

```
show crypto ikev2 session detailed
```

It prints each IKE SA, its PQC line, AND its child SAs with their ESP SPIs, in one block per session. From "01-hybrid/r2-ike-and-child-sa.txt":

```
Session-id:57, Status:UP-ACTIVE, IKE count:1, CHILD count:1
2         10.0.12.2/500         10.0.12.1/500         none/none            READY
      Encr: AES-CBC, keysize: 256, PRF: SHA512, Hash: SHA512, DH Grp:20, Auth sign: PSK, Auth verify: PSK
      PQC Key Exchange: ML-KEM-768
      Local spi: 8468A9DBA927B034       Remote spi: C71FD85A5EA4D48F
      ...
Child sa:
          ESP spi in/out: 0x5BEB586/0x33319F7
          Encr: AES-GCM, keysize: 256, esp_hmac: None
```

`show crypto ikev2 sa detailed` (the command most guides use) doesn't show the child SAs. So use the `session` one.

### Then follow the ESP SPIs to the counters

```
show crypto ipsec sa peer 10.0.12.2
```

You get the same two SPIs, plus `#pkts encaps` and `#pkts decaps`. They're mirrored between the two ends: R2's inbound `0x5BEB586` is R1's `current outbound spi`. Read the counters, send a known number of packets, read again. From "01-hybrid/r1-esp-counters.txt", 100 pings across Tunnel101:

| | before | after |
|---|---|---|
| `#pkts encaps` | 6 | 106 |
| `#pkts decaps` | 6 | 106 |
| SPIs | `0x5BEB586` / `0x33319F7` | unchanged |

Always look at the delta. The totals include whatever ran before you looked, and in "02-fallback/r2-esp-counters.txt" you can see a few extra packets on top of the 100 (209 vs 206). A known packet count is what makes the number mean something.

`show crypto session detail` is the short version: interface, peer, `Capabilities:FUQ` (the `Q` is quantum-safe), and `#pkts enc'ed` / `#pkts dec'ed` for the flow. Good for a quick look. It has no SPIs, so it can't prove which child SA you're looking at.

The SPIs are the join key. ML-KEM is a property of the IKE SA, the child SA is listed under that IKE SA, and the counters are listed under the child's SPIs.

### The YANG view, and one trap

If you automate this, read `Cisco-IOS-XE-crypto-oper`:

- `crypto-ikev2-sa/sa-data/pqc-group` is `pqc-gt-mlkem768` on a hybrid SA and `pqc-gt-none` on a classical one. That's the leaf to trust ("01-hybrid/r1-yang-ikev2-sa.xml").
- `init-spi` and `resp-spi` come back base64-encoded. `ODQ2OEE5REJBOTI3QjAzNA==` decodes to `8468A9DBA927B034`, the same SPI the CLI prints.
- `crypto-ipsec-ident` has the child SA: interface, peer, both ESP SPIs (in decimal) and transform. No packet counters in this subtree on 26.2. Use the CLI for those.

**The trap:** `crypto-ipsec-ident` has a `pqc-group` leaf too, and it says `pqc-gt-none` on a perfectly hybrid tunnel ("01-hybrid/r1-yang-ipsec-ident.xml"). That does NOT mean the child SA is unprotected.

That leaf belongs to the child SA's OWN key exchange (PFS), and it sits right next to `<dh-group>crypto-dh-grouptype-none</dh-group>`. The first child SA is created in `IKE_AUTH` and takes its keys from the IKE SA, which already has ML-KEM mixed in. Case 4 proves it. Turn on `set pfs group20 pqc mlkem768` and wait for a rekey. The child's leaf then flips to `pqc-gt-mlkem768`, `dh-group` to `group20`, and the CLI says:

```
PFS (Y/N): Y, DH group: group20, PQC Key Exchange: ML-KEM-768
```

You can also watch `IKE_FOLLOWUP_KE_EXCH` go up by one in `show crypto ikev2 stats exchange`. That's the RFC 9370 follow-up exchange that carries the child's ML-KEM ("04-child-pfs/r1-after-rekey.txt").

The router's own debug agrees about the case 1 child SA: `IPSec SA PQ classification pqc=YES sa_pq_encr=2(PQ-T-Hybrid)` ("01-hybrid/r2-debug-child-sa-pq-classification.txt").

One more YANG oddity: the `outbound-esp-sa` record says `<dir>crypto-dir-inbound</dir>`. Rely on the container name, not on `dir`.

## Question 2: what happens with a classical-only peer?

### With `optional` it falls back, silently

"02-fallback/r2-ike-and-child-sa.txt" shows the hub with two legs side by side:

| | R1 leg | R3 leg |
|---|---|---|
| `PQC Key Exchange` line | `ML-KEM-768` | (absent) |
| `Capabilities` | `FUQ` | `FU` |
| YANG `pqc-group` | `pqc-gt-mlkem768` | `pqc-gt-none` |
| `DH Grp` | 20 | 20 |
| tunnel | up, 100/100 pings | up, 100/100 pings |

Both tunnels pass traffic. Nothing in syslog tells you one of them is classical ("02-fallback/r2-syslog.txt" has only line-protocol flaps from my `clear crypto session`). The missing `Q`, the missing PQC line, or `pqc-gt-none` are your only signals. If you don't check them, you won't know.

### Without `optional` it fails closed, also silently

"03-reject/" is the hub on `pqc mlkem768` with no `optional`, and R3 with no `pqc` line. The R3 leg never comes up. Pings fail both ways, and `show crypto session` shows `Session status: DOWN` on both ends. The R1 leg stays up on ML-KEM-768 the whole time.

`debug crypto ikev2` tells you exactly why. When R2 starts, R3 refuses ("03-reject/r3-debug-ikev2.txt"):

```
IKEv2-ERROR:Received Policies: ... DH GROUP: DH_GROUP_384_ECP/Group 20 AKE1: ML-KEM-768
IKEv2-ERROR:Expected Policies: ... DH GROUP: DH_GROUP_384_ECP/Group 20
IKEv2-ERROR:: Failed to find a matching policy
IKEv2:Sending no proposal chosen notify
```

When R3 starts, R2 refuses with the same two lines swapped ("03-reject/r2-debug-ikev2-r3-leg.txt"). `AKE1` is the RFC 9370 additional key exchange #1. It fails in `IKE_SA_INIT`, so there's no `IKE_INTERMEDIATE` and nothing reaches `IKE_AUTH`.

Without debugs, the router tells you very little:

- **No syslog.** 65 seconds of the failure with debugs off produced zero log lines ("03-reject/r2-no-debug-65s-delta.txt").
- **`Rejected IKEv2 Requests: 0`** in `show crypto ikev2 stats`. That counter doesn't count proposal mismatches ("03-reject/r2-ikev2-stats.txt").
- **The one counter that moves** is `NO_PROPOSAL_CHOSEN` in `show crypto ikev2 stats exchange`: +2 sent, +2 received per minute on each router. Both ends retry every 30 seconds, forever.
- `clear crypto ikev2 stats` doesn't reset the exchange table. So read it twice and compare.

Also, `Sessions with Quantum Resistance: 0` on R2, while its R1 leg runs ML-KEM. That counter is PPK only.

## Things that bit me

- **IPsec profile changes don't reach a live session.** I set a 120 s lifetime and ran `clear crypto session`. The new SA still had the old 3600 s. Only `shutdown` / `no shutdown` on the tunnel interface picked it up.
- **Teardown order matters.** Remove the tunnel interface while an SA is up and you can get an orphan IKE SA stuck in `DELETE` that `clear crypto session` won't remove. `clear crypto ikev2 sa fast` did.
- **`clear crypto sa peer` on a VTI** drops the whole session, not just the child SA. It doesn't trigger a `CREATE_CHILD_SA` rekey. Use a short lifetime for that.

## What isn't here

- **No packet capture.** Everything here is router output. For the wire view of the same RFC 9370 exchange, see the strongSwan [key-exchange lab](../../../../learn/ipsec/key-exchange/README.md).
- **The "classical peer" is IOS XE 26.2 without a `pqc` line**, not an older release or another vendor. On the wire it offers the same plain group 20 proposal. But an old box that doesn't know RFC 9370 at all could behave differently around `IKE_INTERMEDIATE`. Not tested.
- **YANG ESP counters.** I didn't find packet counters in `crypto-ipsec-ident` on this build, so the counters here are CLI only.

All three routers were put back to their starting state afterwards ("99-teardown/"). The PSK is replaced with `<REDACTED-IKEV2-PSK>`. Login and session noise from my management host is removed from the syslog files.
