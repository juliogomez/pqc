# Can Your VPN Survive a Quantum Computer!?

### A Hands-On ML-KEM vs Diffie-Hellman Lab

So… you've heard that quantum computers are going to break all our encryption someday, and you're wondering what on earth we can actually do about it *today*. Great news: you're in the right place, and we're going to get our hands dirty.

In this lab we'll spin up two tiny Docker containers, have them act as VPN peers, and watch them negotiate a **hybrid** key exchange that mixes good old **classical Diffie-Hellman** with the shiny new **post-quantum ML-KEM** — all inside a real IKEv2 handshake powered by strongSwan. Then we'll capture the traffic and see the difference with our own eyes. No magic, no zillion-line examples from some expert… just you, me, and a couple of containers.

Ready? Let's get this party started!

---

## Contents

1. [What are we trying to figure out?](#what-are-we-trying-to-figure-out)
2. [Why should you care? The quantum threat](#why-should-you-care-the-quantum-threat)
3. [Meet our two contenders](#meet-our-two-contenders)
4. [Head-to-head: let's get ready to rumble](#head-to-head-lets-get-ready-to-rumble)
5. [Why not both? The hybrid solution](#why-not-both-the-hybrid-solution)
6. [Our tool of choice: strongSwan](#our-tool-of-choice-strongswan)
7. [Let's get our hands dirty: the lab](#lets-get-our-hands-dirty-the-lab)
8. [Configuration reference](#configuration-reference)
9. [Appendix](#appendix)

---

## What are we trying to figure out?

Here's the one question this whole lab revolves around: **when we bolt post-quantum key exchange (ML-KEM) onto IKEv2, how does it really compare to the classical Diffie-Hellman we've trusted for decades?**

Is it slower? Bigger? Does it break things? Let's stop guessing and actually measure it. By the time we're done, you'll have seen the trade-offs with your own eyes, from several angles:

- **Security** — what each algorithm protects against, and why neither is good enough on its own today.
- **Size on the wire** — how ML-KEM's chunky keys force IKE fragmentation that DH never needed.
- **Latency** — the extra round trip a hybrid handshake adds, measured directly.
- **Compute** — the CPU cost of lattice-based crypto vs elliptic curves (spoiler: it's not what you'd expect).
- **Protocol mechanics** — how RFC 9370 cleverly bolts ML-KEM onto IKEv2 without breaking anything.

And the best part? We'll run the same handshake twice — once classical-only, once hybrid — capture both, and compare them packet by packet. Seeing is believing.

---

## Why should you care? The quantum threat

Let's set the scene. **Diffie-Hellman (DH)** is the quiet hero behind nearly every secure channel on the internet. Two parties each generate a key pair, swap public keys over an untrusted network, and — through some beautiful math — independently arrive at the *same* shared secret without ever sending it across the wire. IKEv2 has always leaned on some form of DH as its primary key exchange.

So what's the problem? Well… DH, in all its flavors, falls apart against a sufficiently powerful quantum computer running **Shor's algorithm**. Such an adversary could derive the shared secret from the public keys alone. And here's the truly nasty part: an attacker can record your encrypted traffic *today* and simply wait — decrypting it years later once quantum hardware grows up. We call this the **"harvest now, decrypt later"** threat, and yes, it's as creepy as it sounds.


Post-quantum cryptography (PQC) exists precisely to slam this door shut. In this lab we'll use **ML-KEM**, the NIST-standardised PQC key encapsulation mechanism — *alongside* DH, not instead of it. Why alongside? Hold that thought, we'll get there.

---

## Meet our two contenders

Every good showdown needs proper introductions. So let's meet our fighters.

### In the classical corner: X25519 (Elliptic Curve Diffie-Hellman)

**X25519** is a modern, high-performance flavor of DH built on Curve25519, an elliptic curve designed by Daniel Bernstein. You may see it called ECDH (Elliptic Curve Diffie-Hellman) or referred to by its group number `#31` in IKE. It's the recommended classical DH algorithm today — faster and safer than the traditional finite-field DH groups (modp2048 and friends) or the older NIST curves (P-256/ecp256).

It's a true **key exchange**: both parties contribute. Each side whips up an ephemeral key pair, they swap public keys, and each computes the same shared secret from their own private key and the other party's public key. Neither side controls the outcome. It's been battle-tested since 2016 ([RFC 7748](https://www.rfc-editor.org/rfc/rfc7748))… but it's quantum-vulnerable. Sigh.

### In the post-quantum corner: ML-KEM

ML-KEM (Module-Lattice-Based Key Encapsulation Mechanism, [**FIPS 203**](https://csrc.nist.gov/pubs/fips/203/final)) is a post-quantum key encapsulation algorithm standardised by NIST in 2024. The new kid on the block comes in three flavors:

| Name | Security level | Public key | Ciphertext | |
|------|---------------|-----------|-----------|---|
| ML-KEM-512 | ~128-bit classical | 800 B | 768 B | |
| **ML-KEM-768** | **~192-bit classical** | **1184 B** | **1088 B** | **← recommended default** |
| ML-KEM-1024 | ~256-bit classical | 1568 B | 1568 B | |

ML-KEM-768 is the sweet spot for most deployments: a comfortable security margin against quantum attacks without the extra bandwidth of ML-KEM-1024. ML-KEM-512 is generally avoided, as its security level is considered a bit marginal for long-term protection. **So this lab uses ML-KEM-768.**

Now here's the twist that trips a lot of people up: ML-KEM is a **Key Encapsulation Mechanism**, not a symmetric key exchange. One party generates, the other encapsulates. The initiator sends a public key, the responder runs the encapsulation algorithm on it — which spits out *both* a ciphertext and a shared secret — and sends back the ciphertext. Only the initiator, holding the private key, can run decapsulation on that ciphertext to recover the same shared secret. Neat, huh?

---

## Head-to-head: let's get ready to rumble

This is the heart of the lab. The differences below explain every design decision that follows — and Exercise 2 lets you reproduce them yourself. So let's put our two contenders side by side.

### Size on the wire

| | X25519 | ML-KEM-512 | ML-KEM-768 | ML-KEM-1024 |
|-|--------|-----------|-----------|------------|
| Public key | 32 B | 800 B | 1184 B | 1568 B |
| Response (ciphertext / public key) | 32 B | 768 B | 1088 B | 1568 B |
| Shared secret | 32 B | 32 B | 32 B | 32 B |
| Fits in one IKE message (≤1280 B)? | ✅ yes | ✅ yes | ❌ no — needs fragmentation | ❌ no |

X25519's 32-byte keys are adorably tiny. ML-KEM's are 25–50× larger — big enough that ML-KEM-768 forces IKE fragmentation. This single fact drives the `fragmentation = yes` requirement and those chunky packets you'll spot in the capture later.

### Latency: round trips in IKEv2

| Mode | Round trips | Messages |
|------|-------------|---------|
| X25519 only | 2 | `IKE_SA_INIT` → `IKE_AUTH` |
| Hybrid X25519 + ML-KEM (RFC 9370) | 3 | `IKE_SA_INIT` → `IKE_INTERMEDIATE` → `IKE_AUTH` |

The hybrid mode adds one full round trip — measurable, but small in practice (typically a few milliseconds on a LAN). Nothing to lose sleep over.

### Compute cost

A common misconception is that "post-quantum" means "painfully slow". For ML-KEM, the opposite is closer to the truth: its lattice operations are seriously fast — in the same ballpark as, and often faster than, an elliptic-curve scalar multiplication.

Approximate per-operation cost on modern x86 (AVX2), as published reference figures (ML-KEM / X25519 benchmarks on comparable hardware — not measured here):

| Operation | X25519 | ML-KEM-768 |
|-----------|--------|-----------|
| Key generation | ~50–65k cycles (one scalar mult) | ~30k cycles |
| Encapsulate / derive shared secret | ~50–65k cycles (one scalar mult) | ~45k cycles |
| Decapsulate | — (DH has no separate decap) | ~35k cycles |

The two algorithms don't map one-to-one: a DH exchange is two scalar multiplications (make a keypair, then derive), while a KEM splits the work across the peers (the initiator does keygen + decapsulate, the responder does encapsulate). Either way the totals are comparable, and ML-KEM-768 is **not** the bottleneck.

You can measure X25519 yourself inside the container with `openssl speed ecdhx25519`. The containers in this lab are based on Ubuntu 24.04 (the Dockerfile base image), which ships OpenSSL 3.0 — and OpenSSL 3.0 cannot benchmark ML-KEM from the CLI, hence the cited reference figures above.

The decisive evidence for this lab is the end-to-end handshake time you'll measure directly in Exercise 2: hybrid adds only ~1 ms over classical (≈21 ms vs ≈20 ms on the Docker bridge), and essentially all of that is the extra network round trip, not computation. Since a handshake happens once per tunnel (with rekeying every few hours), the CPU cost of either algorithm is negligible in practice.

### Security

| | X25519 | ML-KEM-768 |
|-|--------|-----------|
| Classical security¹ | ~128-bit | ~192-bit |
| Quantum security (Shor / Grover) | ❌ broken by Shor's algorithm | ✅ no known quantum attack |
| Standardisation | RFC 7748 (2016) | FIPS 203 (2024) |
| Deployment maturity | Very high | Emerging |

¹ *Classical security* is the estimated work required to break the algorithm on a conventional (non-quantum) computer, expressed as equivalent bits of symmetric key strength. ~128-bit means an attacker would need roughly 2¹²⁸ operations — currently infeasible. This says nothing about quantum resistance.

### The verdict: why not both?

Here's the thing — neither contender wins outright today. X25519 is battle-tested but quantum-vulnerable. ML-KEM is quantum-safe but new and less field-tested. So instead of picking a champion, we make them tag-team. The hybrid approach gives you both — at the cost of one extra round trip and ~2 KB of additional data per handshake. That's the trade-off this lab makes concrete.


---

## Why not both? The hybrid solution

So how do we actually get our two contenders to work together? Enter [**RFC 9370**](https://www.rfc-editor.org/rfc/rfc9370) (Multiple Key Exchanges in IKEv2, 2023), which defines a mechanism to run additional key exchanges *on top of* the standard IKEv2 DH exchange — with each contributing keying material to the final IKE SA (Security Association) keys. This is the magic that makes hybrid PQC possible in IKEv2 without redesigning the whole protocol.

The scheme is elegant: X25519 stays as the primary key exchange (carried in the standard `IKE_SA_INIT` message), and ML-KEM joins as an *additional* key exchange in a brand-new `IKE_INTERMEDIATE` round trip. The final session key is derived from **both** shared secrets combined. Which means:

- If ML-KEM is broken by a future quantum attack, X25519 still provides classical security.
- If X25519 is broken by a quantum computer, ML-KEM provides quantum resistance.
- An attacker must break **both** simultaneously — believed to be infeasible.

RFC 9370 is the reason this lab works at all. Without it, you'd be forced to either replace X25519 with ML-KEM entirely (losing classical security) or sit around waiting for a full protocol redesign. The hybrid approach is the practical migration path recommended by NIST and most VPN vendors. And it's backward compatible too — peers that don't speak additional key exchanges simply fall back to the base DH exchange. Everybody's happy.

The proposal string `x25519-ke1_mlkem768` says all of this out loud: `x25519` is the main DH group in `IKE_SA_INIT`, and `ke1_mlkem768` is the first RFC 9370 additional key exchange, riding in `IKE_INTERMEDIATE`.

### The handshake, step by step

```
Initiator                                        Responder
    |                                                |
    |--- IKE_SA_INIT (KE[x25519], Ni) -------------> |
    |<-- IKE_SA_INIT (KE[x25519], Nr) -------------- |
    |                                                |
    |    X25519 shared secret derived               |
    |                                                |
    |--- IKE_INTERMEDIATE (mlkem768 pub key) ------> |   (~1250 B, fragmented)
    |<-- IKE_INTERMEDIATE (mlkem768 ciphertext) ---- |   (~1155 B)
    |                                                |
    |    ML-KEM shared secret combined with         |
    |    X25519 secret → final IKE SA keys          |
    |                                                |
    |    IKE SA ESTABLISHED                         |
```

Those big packets in the intermediate exchange are the ML-KEM public key (~1184 B) and ciphertext (~1088 B) — exactly the size difference we saw in the head-to-head — which is precisely why `fragmentation = yes` is required.

---

## Our tool of choice: strongSwan

Time to talk tooling. **strongSwan** is an open-source IKEv2/IPsec implementation widely used in Linux-based VPN gateways, routers, and security appliances. It implements the full IKEv2 protocol ([RFC 7296](https://www.rfc-editor.org/rfc/rfc7296)) and manages the keying lifecycle for IPsec tunnels: negotiating IKE SAs, installing ESP/AH child SAs into the kernel, handling rekeying, and responding to dead peer detection. Quite the workhorse.

Here are the pieces we'll be using:

| Component | Role |
|-----------|------|
| `charon` | The IKE daemon — runs in the background, handles all IKEv2 message exchange |
| `swanctl` | The CLI control tool — connects to `charon` to load config, initiate/terminate SAs, and query state |
| `ml` plugin | strongSwan's native ML-KEM implementation (FIPS 203), enabled with `--enable-ml` at build time |
| `openssl` plugin | Provides AES-GCM, SHA-2, X25519, and SHAKE/SHA-3 (required by the `ml` plugin for its internal hashing) |
| `kernel-netlink` | Linux kernel interface — installs IPsec policies and SAs via Netlink |

strongSwan uses a plugin architecture: each algorithm or kernel interface is a separate shared library loaded at startup. We build strongSwan from source (pinned to the **6.0.6** release tag) to enable the `ml` plugin, which is opt-in at build time via `--enable-ml`.

> **Why strongSwan and not OpenSSL here?** Simple: OpenSSL isn't an IKEv2 implementation. It's a crypto library, and while OpenSSL 3.5 does ship ML-KEM, that support is wired into **TLS** — there's no OpenSSL "IKEv2 mode" you could point at a VPN peer. So for a post-quantum *IKEv2* key exchange there's genuinely no OpenSSL alternative; strongSwan is the tool that speaks the protocol, and ML-KEM is production-ready inside it today (6.0.x), so we get to watch it run in a real handshake. Its companion lab, [Who goes there? Post-quantum authentication](../authentication/README.md), reaches for OpenSSL instead — not by preference, but because post-quantum *authentication* (ML-DSA certificates and signatures) hasn't landed in strongSwan/IKEv2 yet, and OpenSSL is where you can generate and inspect those certs today. Two labs, two tools: that split isn't us being fussy, it's an honest snapshot of where each piece of the post-quantum puzzle is mature right now.

---

## Let's get our hands dirty: the lab

Alright, enough theory — let's actually run this thing! We'll build and start the containers, work through two exercises, and finally tidy everything up. Here's the game plan:

- **Exercise 1** — observe a single hybrid handshake from initiation to teardown, inspecting the SA and the packet capture along the way.
- **Exercise 2** — toggle the config between classical-only and hybrid proposals, and compare round trips, packet sizes, and timing side by side. (This is the payoff — don't skip it!)

### Build and start

All commands in this lab run from the `key-exchange/` directory, so hop in first:

```bash
cd key-exchange
```

```bash
# Build images (takes ~5 min on first run — compiles strongSwan from source)
docker compose build

# Start both containers
docker compose up -d

# Verify both are running
docker compose ps
```

Expected output (once the healthcheck passes, `STATUS` shows `Up (healthy)`):
```
NAME            IMAGE                    STATUS
ike-initiator   key-exchange-initiator   Up (healthy)
ike-responder   key-exchange-responder   Up (healthy)
```

---

### Exercise 1: Observe a hybrid handshake

**Step 1 — Shell into the initiator**

```bash
docker exec -it ike-initiator bash
```

All `swanctl` commands below are run from inside this shell.

---

**Step 2 — Verify strongSwan is running and ML-KEM is available**

```bash
# Check daemon stats and loaded plugins
swanctl --stats
```

Confirm `ml` appears in the `loaded plugins` line. Also check available algorithms:

```bash
swanctl --list-algs
```

Look for the ML-KEM key exchange methods provided by the `ml` plugin:
```
ML_KEM_512[ml]
ML_KEM_768[ml]
ML_KEM_1024[ml]
```
(In proposal strings these are written `mlkem512` / `mlkem768` / `mlkem1024`; `--list-algs` shows strongSwan's internal `ML_KEM_*` names with the providing plugin in brackets.)

---

**Step 3 — Start capturing packets before initiating**

We want to catch the whole conversation, so let's start the recorder *before* anyone says a word. Run tcpdump in the background. `-i any` captures on all interfaces (needed to reliably see traffic on Docker bridge networks), `--immediate-mode` delivers packets to tcpdump as they arrive instead of waiting for the kernel ring buffer to fill, and `-U` flushes each packet to disk as it's written. Together these ensure a short, fast handshake is captured completely.

```bash
tcpdump -i any --immediate-mode -U -n 'port 500 or port 4500' -w /tmp/capture.pcap &
TCPDUMP_PID=$!
```

---

**Step 4 — Initiate the connection**

```bash
swanctl --initiate --child pqc-child
```

You should see:
```
[IKE] initiating IKE_SA pqc-tunnel[1] to 172.20.0.3
[IKE] IKE_SA pqc-tunnel[1] established between 172.20.0.2...172.20.0.3
[IKE] CHILD_SA pqc-child{1} established ...
```

---

**Step 5 — Verify the established SA**

```bash
swanctl --list-sas
```

Expected output:
```
pqc-tunnel: #1, ESTABLISHED, IKEv2, <cookies>
  local  '172.20.0.2' @ 172.20.0.2[4500]
  remote '172.20.0.3' @ 172.20.0.3[4500]
  AES_GCM_16-256/PRF_HMAC_SHA2_256/CURVE_25519/KE1_ML_KEM_768
  established Xs ago, rekeying in ...
  pqc-child: #1, reqid 1, INSTALLED, TUNNEL, ESP:AES_GCM_16-128
    local  172.20.0.2/32
    remote 172.20.0.3/32
```

The key line `AES_GCM_16-256/PRF_HMAC_SHA2_256/CURVE_25519/KE1_ML_KEM_768` decodes as:

| Field | Value | Meaning |
|-------|-------|---------|
| `AES_GCM_16-256` | Encryption | AES-256 in GCM mode with a 16-byte (128-bit) authentication tag. AEAD — provides both confidentiality and integrity in one pass, no separate HMAC needed. |
| `PRF_HMAC_SHA2_256` | Pseudo-random function | HMAC-SHA-256 used to derive IKE keying material (SKEYSEED and child key derivation). |
| `CURVE_25519` | Main key exchange | X25519 ECDH — the classical DH exchange performed in `IKE_SA_INIT`. `CURVE_25519` is strongSwan's internal name for X25519 / group #31. |
| `KE1_ML_KEM_768` | Additional key exchange #1 | ML-KEM-768 performed in `IKE_INTERMEDIATE` (RFC 9370). The `1` means it is the first additional exchange on top of the base DH. The final IKE SA keys are derived from both this and the X25519 secret. |

This suite is shown for the **IKE SA** (the control channel). The child SA line separately shows `ESP:AES_GCM_16-128` — the data-plane tunnel uses AES-128-GCM (a common default for ESP, where the shorter key is still secure and reduces overhead).

And just like that, you have a quantum-resistant tunnel up and running. **It works!**


---

**Step 6 — Inspect the packet capture**

Now for the fun part — let's peek at what actually flew across the wire. Stop the capture and wait for the file to be fully written:

```bash
kill $TCPDUMP_PID
wait $TCPDUMP_PID 2>/dev/null
```

The raw `tcpdump -r /tmp/capture.pcap -n -vv` output is *very* verbose (full payload dump per packet). Let's filter it down to one meaningful line per IKE message — the exchange type and direction:

```bash
tcpdump -r /tmp/capture.pcap -n -vv 2>/dev/null | grep -E "parent_sa|child_sa"
```

What to look for:

| Packet | Direction | Filtered line shows | What it means |
|--------|-----------|---------------------|---------------|
| 1 | `.2 → .3` | `parent_sa ikev2_init[I]` | `IKE_SA_INIT` — SA proposal with X25519 (`dh=#31`) and ML_KEM_768 (`type=#6 id=36`) |
| 2 | `.3 → .2` | `parent_sa ikev2_init[R]` | `IKE_SA_INIT` — responder accepts same proposal |
| 3-4 | `.2 → .3` | `child_sa #43[I]` | `IKE_INTERMEDIATE` (RFC 9370 exchange type 43) — ML-KEM encapsulation key (~1250 B, fragmented across two UDP packets) |
| 5 | `.3 → .2` | `child_sa #43[R]` | `IKE_INTERMEDIATE` response — ML-KEM ciphertext (~1155 B) |
| 6 | `.2 → .3` | `child_sa ikev2_auth[I]` | `IKE_AUTH` — encrypted PSK auth + child SA request |
| 7 | `.3 → .2` | `child_sa ikev2_auth[R]` | `IKE_AUTH` — encrypted confirmation |

> **Why does tcpdump say `child_sa #43` instead of `IKE_INTERMEDIATE`?**
> tcpdump's ISAKMP dissector doesn't have a name for exchange type 43, so it falls back to displaying the raw number. Exchange type 43 (0x2B) is `IKE_INTERMEDIATE`, assigned by RFC 9370. The `[|v2ke]` annotation confirms there is a key exchange payload inside — that is the ML-KEM public key.

> **Why does a 1184 B key need fragmentation when the limit is 1280 B?**
>
> [RFC 7296](https://www.rfc-editor.org/rfc/rfc7296) requires every IKEv2 implementation to handle messages up to **1280 bytes** without relying on IP fragmentation. The ML-KEM-768 encapsulation key is only 1184 B — under the limit — yet the `IKE_INTERMEDIATE` message still gets fragmented. The reason is the layers of headers wrapped around the key, and the UDP/IP encapsulation added on the wire. Accounting for every byte:
>
> | Layer | Adds | Running total |
> |-------|------|---------------|
> | ML-KEM-768 encapsulation key (raw) | — | 1184 B |
> | Key Exchange payload header (RFC 7296 section 3.4: 4 B generic + 2 B method + 2 B reserved) | +8 | 1192 B |
> | Encrypted (SK) payload wrapper (section 3.14 / AES-GCM per RFC 5282: 4 B header + 8 B IV + 1 B pad-length + 16 B GCM tag) | +29 | 1221 B |
> | IKE header (section 3.1) | +28 | **1249 B** |
> | UDP-encap on port 4500 (4 B non-ESP marker + 8 B UDP + 20 B IPv4 header) | +32 | **1281 B** |
>
> That final **1281 B just crosses the 1280 ceiling**, so strongSwan fragments at the IKE layer ([RFC 7383](https://www.rfc-editor.org/rfc/rfc7383)) rather than letting the IP layer fragment it (IP fragments are widely dropped by firewalls). You can see it in the capture: the first fragment's UDP length is **1252 B**, so its datagram is `1252 + 8 (UDP) + 20 (IP) = 1280` — sized to sit exactly on the limit — and the remainder spills into the tiny second fragment (`70 + 8 + 20 = 98 B`). The responder's reply carries the 1088 B ML-KEM ciphertext through the same wrapping, landing at ~1155 B. This is exactly why `fragmentation = yes` is mandatory: without it the oversized `IKE_INTERMEDIATE` message would be IP-fragmented or silently dropped.
>
> (Exact byte counts vary slightly depending on whether UDP/IP headers are counted in a given view.)

For a compact view of packet sizes and counts, use `-q` instead:

```bash
tcpdump -r /tmp/capture.pcap -n -q
```

To inspect the full payload detail of a single packet (SA proposals, KE payloads, the `[v2ke]` key-exchange marker, etc.), drop the filter and read the raw `-vv` output:

```bash
tcpdump -r /tmp/capture.pcap -n -vv
```

To copy the capture to your host for Wireshark:
```bash
docker cp ike-initiator:/tmp/capture.pcap ~/Desktop/ike_capture.pcap
```

---

**Step 7 — Tear down the connection**

```bash
swanctl --terminate --ike pqc-tunnel
```

Verify it's gone:
```bash
swanctl --list-sas   # should return empty
```

---

### Exercise 2: Compare classical-only vs hybrid handshake

This is the payoff exercise — the moment the whole head-to-head comparison stops being a table and becomes something you can see. We'll run the same handshake twice — once with pure X25519, once with our default hybrid proposal — and watch the differences in round trips, packet sizes, and timing. Let's go!

**Step 1 — Switch to classical-only proposals**

Both config files ship with two proposal lines pre-provisioned, so switching is just a matter of swapping which one is commented out. Shell into the initiator:

```bash
docker exec -it ike-initiator bash
vi /usr/local/etc/swanctl/swanctl.conf
```

Swap the active line so the proposals block looks like this:
```
# proposals = aes256gcm16-prfsha256-x25519-ke1_mlkem768
proposals = aes256gcm16-prfsha256-x25519
```

Do the same in the responder (open a second terminal):
```bash
docker exec -it ike-responder bash
vi /usr/local/etc/swanctl/swanctl.conf
```

**Step 2 — Disable fragmentation**

Since classical X25519 keys are tiny, we won't need fragmentation here. Still inside each container, comment out the `fragmentation` line:
```
# fragmentation = yes
```

Then reload the config in both containers:
```bash
swanctl --load-all
```

**Step 3 — Capture and time the classical handshake**

> **Note:** both captures must be taken in the same container session. `/tmp` is ephemeral — files there are lost if the container is restarted between steps.

Back in the initiator shell, start a capture:
```bash
tcpdump -i any --immediate-mode -U -n 'port 500 or port 4500' -w /tmp/classical.pcap &
TCPDUMP_PID=$!
sleep 1
time swanctl --initiate --child pqc-child
sleep 1
kill $TCPDUMP_PID
wait $TCPDUMP_PID 2>/dev/null
```

Note the wall-clock time reported by `time`.

Terminate the tunnel before the next test:
```bash
swanctl --terminate --ike pqc-tunnel
```

**Step 4 — Restore hybrid config and capture again**

In each container shell, revert the config: uncomment the hybrid proposal, comment out the classical one, and uncomment `fragmentation = yes`:

```bash
vi /usr/local/etc/swanctl/swanctl.conf
```

```
proposals = aes256gcm16-prfsha256-x25519-ke1_mlkem768
# proposals = aes256gcm16-prfsha256-x25519
fragmentation = yes
```

Reload in both containers:
```bash
swanctl --load-all
```

Then in the initiator shell, repeat the capture:
```bash
tcpdump -i any --immediate-mode -U -n 'port 500 or port 4500' -w /tmp/hybrid.pcap &
TCPDUMP_PID=$!
sleep 1
time swanctl --initiate --child pqc-child
sleep 1
kill $TCPDUMP_PID
wait $TCPDUMP_PID 2>/dev/null
```

**Step 5 — Compare the two captures**

First, let's identify the IKE exchange types. Use `-vv` with a `grep` to cut through the verbose output and show just the exchange type per packet:
```bash
tcpdump -r /tmp/classical.pcap -n -vv 2>/dev/null | grep -E "parent_sa|child_sa"
tcpdump -r /tmp/hybrid.pcap -n -vv 2>/dev/null | grep -E "parent_sa|child_sa"
```

Each line shows the exchange type and direction (`[I]` initiator, `[R]` responder):
- `parent_sa ikev2_init` — IKE_SA_INIT
- `child_sa #43` — IKE_INTERMEDIATE (tcpdump has no name for exchange type 43)
- `child_sa ikev2_auth` — IKE_AUTH

Then use `-q` for a compact size-and-count view:
```bash
tcpdump -r /tmp/classical.pcap -n -q
tcpdump -r /tmp/hybrid.pcap -n -q
```

What to look for:

| | Classical (X25519 only) | Hybrid (X25519 + ML-KEM-768) |
|-|------------------------|------------------------------|
| Lines in `-q` output | 5 (see note below) | 7 |
| Unique IKE messages | 4 | 6 (IKE_INTERMEDIATE[I] fragmented into 2 packets) |
| Exchanges | `IKE_SA_INIT` → `IKE_AUTH` | `IKE_SA_INIT` → `IKE_INTERMEDIATE` → `IKE_AUTH` |
| Extra round trip | No | Yes — `IKE_INTERMEDIATE` |
| Largest single packet | 357 B | 1252 B (ML-KEM-768 public key, fragmented) |
| Fragmented packets | None | 2 — ML-KEM public key (1184 B) split across fragments |
| IKE_AUTH size | identical — 357 B / 213 B | identical — 357 B / 213 B |
| Handshake time | ~20 ms | ~21 ms |
| Quantum-safe | ❌ | ✅ |

> **Why does the classical capture show 5 lines?** `-i any` on Linux captures the outgoing `IKE_SA_INIT[I]` twice: once as `Out` (leaving the socket) and once as `P` (promiscuous pass-through on the Docker bridge). Both lines show the same length (232 B) and nearly identical timestamps — this is a capture artifact, not an extra protocol message. The hybrid capture does not show this duplicate because the subsequent messages switch to port 4500 before the same condition is triggered.

The handshake time difference will be small — typically under 10 ms on a local Docker network. The main observable cost is the additional round trip and the ~2 KB of extra data. And *that*, right there, is the core finding of this whole lab: **post-quantum security in IKEv2 is cheap in compute and latency — its main footprint is bandwidth and one extra round trip.** Quantum resistance for the price of a couple of kilobytes? I'll take it.


**Step 6 — Measure the classical key-exchange compute cost**

Want to put the "compute is negligible" claim on even firmer footing? Let's benchmark X25519 directly:

```bash
openssl speed ecdhx25519
```

This reports X25519 operations per second on this machine — typically tens of thousands per core. Compare that to the single handshake per tunnel: even at the low end, the key exchange is a vanishingly small fraction of the work. (OpenSSL 3.0 on Ubuntu 24.04 has no CLI benchmark for ML-KEM; published figures put ML-KEM-768 in the same range or faster — see the Compute cost discussion earlier.)

---

### Cleanup

All done? Let's tidy up. Terminate any active IKE SA from inside the initiator container shell, then exit:

```bash
swanctl --terminate --ike pqc-tunnel
exit
```

Then stop and remove the containers:

```bash
docker compose down
```

`docker compose down` stops and removes the containers and the `pqc_net` bridge network. The built images are kept, so the next `docker compose up -d` starts immediately without rebuilding.

And that's all, folks! You just stood up a hybrid post-quantum VPN tunnel, captured it, and proved — with your own packets — that quantum-safe IKEv2 is both practical and cheap. Not bad for a couple of containers, huh? Now go and explore your own integrations and use cases. Nice job, really well done!


---

## Configuration reference

### IKE proposal breakdown

```
aes256gcm16 - prfsha256 - x25519 - ke1_mlkem768
     │              │         │           │
     │              │         │           └─ RFC 9370 additional KE #1: ML-KEM-768 (recommended)
     │              │         │              carried in IKE_INTERMEDIATE exchange
     │              │         └─ Main DH group: X25519 (ECDH on Curve25519, group #31)
     │              │              carried in IKE_SA_INIT exchange
     │              └─ PRF: HMAC-SHA-256
     └─ Encryption: AES-256-GCM (AEAD, no separate integrity algorithm needed)
```

The `ke1_` prefix means "first RFC 9370 additional key exchange". You could chain more with `ke2_mlkem1024`, `ke3_...`, etc., each adding another round trip and another layer of keying material. RFC 9370 supports up to 7 additional key exchanges (`ke1_` through `ke7_`).

### swanctl.conf options used

| Option | Where | Meaning |
|--------|-------|---------|
| `proposals` | connection | IKE SA algorithm suite |
| `fragmentation = yes` | connection | Enable [RFC 7383](https://www.rfc-editor.org/rfc/rfc7383) IKE fragmentation (required for large ML-KEM payloads) |
| `local_addrs` / `remote_addrs` | connection | Bind/connect address |
| `auth = psk` | local/remote | Pre-shared key authentication (the lab PSK is a throwaway — see note below) |
| `local_ts` / `remote_ts` | child | Traffic selectors for the ESP tunnel |
| `start_action = none` | child | Do not auto-initiate on config load |
| `start_action = start` | child | Auto-initiate immediately on config load |

> **Note on the pre-shared key:** the PSK in `config/*/swanctl.conf` (`pqc-lab-preshared-key`) is a hardcoded throwaway for lab convenience only. Authentication is deliberately kept trivial so the focus stays on key exchange. Never commit secrets or reuse this pattern in production — use a high-entropy key from a secrets manager.

---

## Appendix

- The `ml` plugin (`--enable-ml`) is a standalone implementation of ML-KEM (FIPS 203) with no external dependencies. It ships in stable 6.x releases (this lab pins to **6.0.6**) but is opt-in — it is not part of the compiled-in defaults.
- The `liboqs` library is no longer needed and is not supported in stable releases (as of 6.0.4+). The `ml` plugin replaces it.
- ML-KEM via OpenSSL 3.5+ is also supported in strongSwan 6.0.2+ (`--enable-openssl`), but requires building OpenSSL 3.5 from source on Ubuntu 24.04. This lab uses the `ml` plugin instead: it is self-contained (no extra dependencies), keeps the Dockerfile simple and the build fast, and lets strongSwan own the ML-KEM implementation end-to-end — a cleaner setup when the goal is understanding how ML-KEM integrates into IKEv2 rather than evaluating a production crypto library stack. The OpenSSL 3.5 path makes more sense in production environments where a single FIPS 140-3 validated library is required across all algorithms.
- The `ke1_` prefix in proposal strings implements RFC 9370 (Multiple Key Exchanges in IKEv2, 2023), which is backward compatible — peers that don't support additional key exchanges fall back to the base DH exchange.

### Build dependencies

Because strongSwan is compiled from a git checkout (not a release tarball), the `Dockerfile` installs the full autotools chain. Each is needed for a specific build stage — these are already handled, listed here only for understanding:

| Package(s) | Why it's needed |
|------------|-----------------|
| `autoconf automake libtool gettext` | `./autogen.sh` runs `autoreconf` to generate `configure` (tarballs ship this pre-generated) |
| `gperf` | generates perfect hash tables during `configure` |
| `bison flex` | provide `yacc`/`lex` for strongSwan's config-file grammar parsers |
| `build-essential libssl-dev pkg-config` | C toolchain and OpenSSL headers for the `openssl` plugin |
| `iproute2 iputils-ping kmod` | runtime networking utilities inside the container |
| `tcpdump` | packet capture for the lab exercises |
| `vim` | editing `swanctl.conf` in-container for Exercise 2 |

The Dockerfile also sets `load_modular = no` in `strongswan.conf`: strongSwan generates one `.conf` file per plugin, but they are empty here, so the default `load_modular = yes` would load **no** plugins and charon would fail on missing primitives like `NONCE_GEN`.

### Reference RFCs

| RFC | Title | Relevance |
|-----|-------|-----------|
| [RFC 7296](https://www.rfc-editor.org/rfc/rfc7296) | IKEv2 | Base protocol; defines the 1280-byte unfragmented message ceiling |
| [RFC 7383](https://www.rfc-editor.org/rfc/rfc7383) | IKEv2 Fragmentation | How oversized IKE messages (like ML-KEM payloads) are split |
| [RFC 7748](https://www.rfc-editor.org/rfc/rfc7748) | Elliptic Curves for Security | Defines X25519 |
| [RFC 9370](https://www.rfc-editor.org/rfc/rfc9370) | Multiple Key Exchanges in IKEv2 | Enables hybrid PQC; defines `IKE_INTERMEDIATE` exchange type 43 |
| [FIPS 203](https://csrc.nist.gov/pubs/fips/203/final) | Module-Lattice-Based KEM | The ML-KEM standard |
