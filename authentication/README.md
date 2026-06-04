# A Hands-On Post-Quantum Authentication Lab

### Who Goes There!? Proving Identity After Quantum

So in the [companion lab](../key-exchange/README.md) we tackled *key exchange* — we made sure a future quantum computer can't decrypt the traffic we send today. Great. But that leaves a juicy question hanging in the air: when two machines set up a secure channel, how do they know they're *actually talking to who they think they're talking to*? That's **authentication**, and it's the other half of the post-quantum story… the half almost everyone forgets.

In this lab we'll generate real keys and certificates using the brand-new NIST post-quantum signature algorithms — **ML-DSA** and **SLH-DSA** — line them up next to the classics (RSA, ECDSA, Ed25519), and measure exactly what changes when your signatures go quantum-safe. Spoiler: the certificates get *a lot bigger*. We'll see why that matters, where the real urgency is (it's not where you'd guess), and where the tooling stands today.

No deep math, no hand-waving — just you, OpenSSL, and a pile of certificates. Ready? Let's find out who goes there!

---

## Contents

1. [What are we trying to figure out?](#what-are-we-trying-to-figure-out)
2. [Why should you care? (It's not "harvest now, decrypt later")](#why-should-you-care-its-not-harvest-now-decrypt-later)
3. [Meet our contenders](#meet-our-contenders)
4. [Head-to-head: the signature showdown](#head-to-head-the-signature-showdown)
5. [The migration story: hybrid and composite signatures](#the-migration-story-hybrid-and-composite-signatures)
6. [Our tools of choice: OpenSSL 3.5 and strongSwan](#our-tools-of-choice-openssl-35-and-strongswan)
7. [Let's get our hands dirty: the lab](#lets-get-our-hands-dirty-the-lab)
8. [Live fire: mutual authentication over real IKEv2](#live-fire-mutual-authentication-over-real-ikev2)
9. [Where IKEv2 authentication is heading](#where-ikev2-authentication-is-heading)
10. [Configuration reference](#configuration-reference)
11. [Appendix](#appendix)

---

## What are we trying to figure out?

Here's the question driving this whole lab: **when we swap our classical digital signatures (RSA, ECDSA, Ed25519) for the new post-quantum ones (ML-DSA, SLH-DSA), what actually changes — and how much should we worry about it today?**

Authentication is how a peer proves "I am who I say I am." On the internet that proof is almost always a **digital signature**, usually wrapped in an **X.509 certificate** issued by a Certificate Authority (CA). Your browser trusts a website because a CA signed its certificate. Your VPN gateway trusts its peer because a signature checks out. Pull on that thread and the entire trust fabric of the internet is signatures, all the way down.

So let's actually measure what post-quantum does to that fabric. By the end you'll have seen, with your own keys and certs:

- **Size** — how much bigger PQC certificates and signatures are (measured, not guessed).
- **Speed** — whether signing and verifying still feels instant (mostly yes, with one big exception).
- **Security & maturity** — what each algorithm buys you, and how battle-tested it is.
- **Urgency** — why authentication has a genuinely *different* quantum deadline than key exchange.
- **The migration path** — hybrid/composite signatures, and where IKEv2 and the tooling stand right now.

---

## Why should you care? (It's not "harvest now, decrypt later")

Now here's the bit I really want you to sit with, because it trips up almost everyone.

In the key-exchange lab, the villain was **"harvest now, decrypt later"**: an attacker records your encrypted traffic *today* and patiently waits for a quantum computer to crack the key years later. That threat is *retroactive* — today's secrets still have value tomorrow — which is exactly why post-quantum key exchange is urgent *right now*.

Authentication doesn't work like that. A signature on a live handshake only has to resist forgery **up to the moment it's verified**. If a quantum computer capable of forging signatures arrives in, say, 2035, it cannot reach back in time and forge your 2026 VPN handshake to break into a session that ended years ago. The session's already over. There's nothing to steal. So for *ephemeral* authentication — a single TLS or IKEv2 handshake — there's no "harvest now" equivalent. Phew, right?

Well… don't relax *too* much. Authentication still has to go post-quantum, just for different (and sneakier) reasons:

- **Long-lived trust anchors.** Root and intermediate CA certificates routinely have **10–25 year** lifetimes. A root signing key that a quantum computer can forge means an attacker could mint perfectly "trusted" certificates the moment a cryptographically-relevant quantum computer (CRQC) exists. Those roots need to be quantum-safe *long before* that day.
- **Credentials that outlive the threat horizon.** Code-signing keys, firmware-signing keys, device identity certificates baked into hardware, long-term document signatures — anything whose signature must still hold up a decade from now.
- **PKI migrates very slowly.** Changing a signature algorithm means updating CAs, issuance pipelines, validation libraries, HSMs, and *every single endpoint* that verifies. That's years of coordinated work across the whole ecosystem. Start late and you get caught out.

So the mental model is: **key exchange is the fire alarm; authentication is the slow structural retrofit.** Less acute, but you can't do it overnight — and your trust anchors outlive your traffic. That's why we should understand it now.

---

## Meet our contenders

Every showdown needs introductions. In one corner, the signatures that have guarded the internet for decades. In the other, the post-quantum newcomers. Let's shake hands with all of them.

### The classics (quantum-vulnerable)

- **RSA** — the granddaddy. Security rests on the difficulty of factoring large numbers. Rock-solid for decades, widely supported… and completely broken by Shor's algorithm on a quantum computer. Big-ish keys (3072-bit for ~128-bit security), modest signatures.
- **ECDSA (P-256)** — elliptic-curve signatures. Much smaller keys than RSA for the same strength. Also toast against Shor's algorithm.
- **Ed25519** — the modern darling: a fast, misuse-resistant EdDSA signature on Curve25519 ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)). Tiny 32-byte keys, 64-byte signatures. Beautiful… and just as quantum-vulnerable as the others.

All three share the same fatal flaw: their hard problem (factoring or discrete log) melts away in the face of a sufficiently large quantum computer running Shor's algorithm. An attacker could recover the private key from the public key and forge signatures at will.

### The post-quantum newcomers

- **ML-DSA** (Module-Lattice-Based Digital Signature Algorithm, [**FIPS 204**](https://csrc.nist.gov/pubs/fips/204/final), formerly CRYSTALS-Dilithium). The lattice-based all-rounder and NIST's recommended general-purpose default. Comes in three sizes: **ML-DSA-44** (NIST level 2), **ML-DSA-65** (level 3, the sensible default), and **ML-DSA-87** (level 5). Fast signing and verification; moderately large keys and signatures.
- **SLH-DSA** (Stateless Hash-Based Digital Signature Algorithm, [**FIPS 205**](https://csrc.nist.gov/pubs/fips/205/final), formerly SPHINCS+). Its security relies *only* on hash functions — no lattices, no number theory, the most conservative assumption you can make. The price? *Enormous* signatures and slow signing. Each variant is tagged `s` (small/slow) or `f` (fast/huge), e.g. `SLH-DSA-SHA2-128s`.
- **FN-DSA** (FFT over NTRU lattices, [**FIPS 206**](https://csrc.nist.gov/projects/post-quantum-cryptography), formerly FALCON) — small signatures, but notoriously tricky to implement safely (it leans on floating-point arithmetic). Still in **draft** as of this writing, so we won't lab it — but keep it on your radar for bandwidth-constrained uses.

The headline trade-off: **ML-DSA is the balanced workhorse you'll reach for most of the time. SLH-DSA is the ultra-conservative choice for rarely-signed, long-lived things (think root CAs and firmware) where you'll accept a giant signature in exchange for security that rests on nothing but hash functions.**

---

## Head-to-head: the signature showdown

Enough talk — let's line them up. (Every number in the size and signature columns below is *measured* with OpenSSL 3.5, generating real keys, real self-signed certificates, and real signatures. You'll reproduce these yourself in the lab.)

### Size on the wire

This is where post-quantum authentication earns its reputation. A quick note on what we're measuring: a **self-signed certificate** is one where the subject and the issuer are the same entity — the certificate is signed with its own private key instead of by a separate CA. We use it here precisely because it's the cleanest yardstick: it bundles exactly one public key and exactly one signature with no external CA involved, so the size reflects *only* the algorithm's own footprint — a fair, apples-to-apples comparison across all eight algorithms. (Real-world certs are signed by a CA, but they carry the same public-key-plus-signature payload, so the size story is identical.)

The sizes below are [DER-encoded](https://en.wikipedia.org/wiki/X.690#DER_encoding) — DER (Distinguished Encoding Rules) is the compact, canonical *binary* serialization of an X.509 certificate (as opposed to PEM, the base64 text wrapper you usually see in `.pem` files). DER is what actually travels on the wire during a handshake, so it's the size that genuinely matters. Here are the real self-signed certificate sizes and detached signature sizes:

| Algorithm | Security | Public key | Signature | Self-signed cert (DER) |
|-----------|----------|-----------|-----------|------------------------|
| Ed25519 | ~128-bit | 32 B | 64 B | 326 B |
| ECDSA P-256 | ~128-bit | 65 B | ~70 B | 389 B |
| RSA-3072 | ~128-bit | 384 B | 384 B | 1043 B |
| **ML-DSA-44** | NIST L2 | 1312 B | 2420 B | 3987 B |
| **ML-DSA-65** | NIST L3 | 1952 B | 3309 B | 5516 B |
| **ML-DSA-87** | NIST L5 | 2592 B | 4627 B | 7474 B |
| SLH-DSA-128s | NIST L1 | 32 B | 7856 B | 8139 B |
| SLH-DSA-128f | NIST L1 | 32 B | 17088 B | 17371 B |

Look at that jump! An Ed25519 certificate is just 326 bytes. The equivalent ML-DSA-65 cert is **5516 bytes** — roughly **17× larger**. And SLH-DSA-128f? Its signature *alone* is **17 KB** — bigger than many entire web pages. Notice SLH-DSA's quirk: its public key is a tiny 32 bytes (great for storage), but the signature is gigantic.

> **Wait — why isn't the cert just public key + signature?** Good catch. For ML-DSA-65, the key (1952 B) plus the signature (3309 B) add up to 5261 B, yet the certificate is 5516 B — about 255 B more. That's because a certificate isn't a simple concatenation; it's a structured [X.509](https://en.wikipedia.org/wiki/X.509) document that *embeds* the key and signature alongside metadata: a version and serial number, the validity dates (`notBefore`/`notAfter`), the issuer and subject names, algorithm identifier OIDs (which appear more than once), a few default extensions (`basicConstraints`, `subjectKeyIdentifier`, `authorityKeyIdentifier`), and the ASN.1/DER tag-and-length bytes framing every field. That overhead is roughly *fixed* (~230–260 B here) no matter the algorithm — which is why it dominates a tiny Ed25519 cert (230 of its 326 bytes) but barely registers for a chunky ML-DSA one (255 of 5516).

Why does this matter for authentication? Because handshakes carry **certificate chains *plus* a handshake signature**. A typical chain is leaf + intermediate (+ sometimes the root), and each cert carries its issuer's signature. Swap a 3-cert ECDSA chain (~1.2 KB total) for an ML-DSA-65 chain and you're suddenly shipping **15–20 KB** in the handshake. In IKEv2 that means the `IKE_AUTH` exchange balloons and leans hard on fragmentation ([RFC 7383](https://www.rfc-editor.org/rfc/rfc7383)) — exactly the same pressure ML-KEM put on `IKE_INTERMEDIATE` in the key-exchange lab, but now on the authentication leg.

> **Why is "leaning hard on fragmentation" a big deal?** Fragmentation was designed as an occasional fallback for the rare oversized message. Post-quantum flips that: now *almost every* handshake is large and fragmented, so the exception becomes the steady state — and that brings real costs:
>
> - **Packet loss hurts much more.** A message split into N fragments only reassembles if *all N* arrive. Lose a single one and the whole message is retransmitted, not just the missing piece. The more fragments, the higher the odds at least one drops — so on lossy or congested links, big PQC handshakes retransmit more often, and latency spikes.
> - **Middleboxes are hostile to fragments.** Firewalls, NATs, and load balancers routinely drop, rate-limit, or mishandle fragmented UDP. (IKE-layer fragmentation exists precisely because *IP*-layer fragmentation is so unreliable on the open internet.) More fragments means more chances to hit a box that quietly black-holes them, producing handshakes that fail in maddeningly hard-to-debug ways.
> - **It enlarges the pre-authentication attack surface.** These bytes fly *before* the peer has proven who it is, so a responder must buffer and reassemble fragments from a not-yet-authenticated initiator. That's extra memory and state an attacker can try to exhaust (fragment floods), plus a juicier target for traffic-amplification abuse.
> - **MTU guesswork bites.** Fragment sizing depends on the path MTU; guess wrong and fragments get silently dropped, again surfacing as intermittent, confusing failures.
>
> None of this *breaks* PQC authentication — strongSwan and friends handle it — but it's why "the certs got bigger" isn't a cosmetic detail. Size turns into round trips, retransmits, and middlebox trouble, which is exactly the kind of thing that bites at scale.

### Speed

A common worry: "are these slow?" Let's measure (per-signature wall-clock, OpenSSL CLI):

| Algorithm | Signing | Notes |
|-----------|---------|-------|
| Ed25519 | ~instant | dominated by process startup |
| ECDSA P-256 | ~instant | dominated by process startup |
| ML-DSA-44 / 65 / 87 | ~instant | sits right alongside the classics |
| SLH-DSA-128f | a few ms | noticeably heavier |
| SLH-DSA-128s | ~100+ ms | dramatically slower to sign |

> **Honest caveat:** invoking the `openssl` CLI costs a few milliseconds of process startup, which swamps the actual crypto for the fast algorithms — so treat the "instant" rows as an upper bound. The signal that *is* real and reproducible: **ML-DSA signs and verifies right alongside Ed25519 and ECDSA**, while **SLH-DSA's `s` ("small") variants are orders of magnitude slower to sign.** That slowness is precisely why SLH-DSA is reserved for things you sign rarely (a root CA signs a handful of certs a year — who cares if each takes 100 ms?) rather than things you sign constantly (a busy TLS terminator doing thousands of handshakes a second).

### Security and maturity

| | Classical (RSA/ECDSA/Ed25519) | ML-DSA | SLH-DSA |
|-|-------------------------------|--------|---------|
| Quantum-safe | ❌ broken by Shor's algorithm | ✅ no known quantum attack | ✅ no known quantum attack |
| Security basis | Factoring / discrete log | Module lattices (MLWE) | Hash functions only |
| Standardised | Decades of deployment | FIPS 204 (2024) | FIPS 205 (2024) |
| Maturity | Very high | Emerging | Emerging |
| Best for | Today's everything | General-purpose default | Long-lived, rarely-signed anchors |

### The verdict

Neither extreme wins outright. The classics are tiny, fast, and battle-tested — but quantum-doomed. SLH-DSA is the most conservative quantum-safe option but pays in size and speed. **ML-DSA-65 is the pragmatic default**: quantum-safe, fast, with certificate sizes that are bigger but entirely manageable. And just like with key exchange, the smart near-term move isn't to rip out the classics — it's to *combine* them. Which brings us to…

---

## The migration story: hybrid and composite signatures

Remember how the key-exchange lab combined X25519 *and* ML-KEM, so an attacker had to break both? Authentication has the very same idea, and it goes by the name **composite signatures** (or "hybrid" authentication).

The concept: bind a classical signature (say ECDSA or Ed25519) **and** a post-quantum signature (say ML-DSA) together into a single credential. A verifier checks *both*. The credential stays safe as long as **either** algorithm holds:

- If ML-DSA turns out to have a flaw (it's new!), the classical signature still protects you today.
- If a quantum computer breaks the classical part, ML-DSA still protects you tomorrow.

Why bother instead of just going pure ML-DSA? Two reasons. First, **hedging**: lattice cryptography is young, and a belt-and-suspenders approach guards against an unforeseen break in the new stuff. Second, **compliance and interop during the transition**: many environments still mandate a FIPS-validated classical algorithm, so a composite lets you satisfy "must include ECDSA" and "must be quantum-safe" at the same time.

The IETF's LAMPS working group is standardising composite signatures for X.509, and the trade-off is exactly what you'd expect: you carry *both* signatures, so the credential is even bigger. It's the authentication mirror of the key-exchange bargain — pay a bit of size and complexity now to buy migration safety.

---

## Our tools of choice: OpenSSL 3.5 and strongSwan

Two tools carry this lab:

- **[OpenSSL 3.5+](https://openssl-library.org/)** is the star here. Released in April 2025, it's the first mainstream OpenSSL with **native** support for all three NIST PQC algorithms — ML-KEM (FIPS 203), ML-DSA (FIPS 204), and SLH-DSA (FIPS 205) — straight from the default provider. No external libraries, no patches, no `oqs-provider`. If you've got OpenSSL 3.5 or newer, you can generate post-quantum keys and certificates with the same `genpkey` and `req` commands you already know. (Check yours with `openssl version`.)
- **strongSwan** is our IKEv2 engine from the key-exchange lab, and we put it to work twice in the live exercises: the **stable** release for classical (ECDSA) certificate auth, and the **experimental `ml-dsa` branch** for post-quantum (ML-DSA) certificate auth. Where that branch stands is covered in [Where IKEv2 authentication is heading](#where-ikev2-authentication-is-heading).

The plan: first use OpenSSL to generate and dissect the certificates (the most convenient, reproducible way to do that today), then hand those certificates to strongSwan to authenticate a real IKEv2 tunnel. One honest caveat carries over from the companion lab: OpenSSL isn't an IKEv2 implementation — its PQC support targets TLS — so anything happening *on the VPN wire*, key exchange there and authentication here, is strongSwan's job. And while ML-KEM key exchange ships in stable strongSwan, post-quantum *authentication* over IKEv2 only exists on an experimental branch today, which is exactly what makes the post-quantum authentication run a peek over the frontier.

---

## Let's get our hands dirty: the lab

Alright, enough theory — let's make some quantum-safe certificates and weigh them! These labs are self-contained and run inside a tiny throwaway container, so the only thing you need installed is **Docker**.

### Setup: get an OpenSSL 3.5+ shell

A tiny throwaway container gives you a clean OpenSSL 3.5+ environment in seconds:

```bash
docker run --rm -it alpine:3.22 sh
```

Then inside the container:

```bash
apk add --no-cache openssl
openssl version          # expect OpenSSL 3.5.x or newer
mkdir -p /pqauth && cd /pqauth
```

All commands below run in that shell. (We use a container because OpenSSL 3.5+ is required for the post-quantum algorithms, and `alpine:3.22` ships it — no need to touch whatever OpenSSL your host happens to have.)

---

### Exercise 1: Meet the candidates

Let's first confirm the post-quantum algorithms are really there, then generate one key of each family.

**Step 1 — List the available signature algorithms**

```bash
openssl list -signature-algorithms | grep -iE "ml-dsa|slh-dsa|ed25519"
```

You should see the ML-DSA and SLH-DSA families listed, for example:

```
{ 1.3.101.112, ED25519 } @ default
{ 2.16.840.1.101.3.4.3.17, id-ml-dsa-44, ML-DSA-44, MLDSA44 } @ default
{ 2.16.840.1.101.3.4.3.18, id-ml-dsa-65, ML-DSA-65, MLDSA65 } @ default
{ 2.16.840.1.101.3.4.3.19, id-ml-dsa-87, ML-DSA-87, MLDSA87 } @ default
{ 2.16.840.1.101.3.4.3.20, id-slh-dsa-sha2-128s, SLH-DSA-SHA2-128s } @ default
...
```

There they are — straight from OpenSSL's default provider. No plugins required.

**Step 2 — Generate one key from each family**

```bash
openssl genpkey -algorithm ED25519 -out ed25519.key
openssl genpkey -algorithm ML-DSA-65 -out mldsa65.key
openssl genpkey -algorithm SLH-DSA-SHA2-128s -out slh128s.key
```

Easy as that — generating a post-quantum private key is the same one-liner you've always used.

Despite the name, a `.key` file here holds the **whole key pair**, not just the private key. `genpkey` writes the private key material, and because the public key is mathematically derived from it, the public key is either stored alongside it or recomputed on demand — which is why later we can pull the public half out of this same file with `openssl pkey -pubout`. The file is plain-text PEM (base64 between `-----BEGIN PRIVATE KEY-----` / `-----END PRIVATE KEY-----` markers) and is unencrypted here for lab convenience, so guard it like the secret it is. Peek inside:

```bash
openssl pkey -in mldsa65.key -text -noout | head -5
```

---

### Exercise 2: Make certificates and weigh them (the payoff)

Now the fun part — let's mint self-signed certificates and watch the size explosion for ourselves.

**Step 1 — Generate keys and self-signed certs for the whole line-up**

```bash
# Each entry is "openssl-algorithm-name:output-filename"
algs="ED25519:ed25519 EC:ecp256 RSA:rsa3072 ML-DSA-44:mldsa44 ML-DSA-65:mldsa65 ML-DSA-87:mldsa87 SLH-DSA-SHA2-128s:slh128s SLH-DSA-SHA2-128f:slh128f"

for entry in $algs; do
    alg=${entry%%:*}     # part before the colon (algorithm)
    name=${entry##*:}    # part after the colon (filename)

    # 1. Generate the private key (RSA and EC need an extra size/curve option)
    case "$alg" in
        RSA) openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$name.key" ;;
        EC)  openssl genpkey -algorithm EC  -pkeyopt ec_paramgen_curve:P-256 -out "$name.key" ;;
        *)   openssl genpkey -algorithm "$alg" -out "$name.key" ;;
    esac

    # 2. Create a self-signed certificate from that key (DER-encoded)
    openssl req -x509 -new -key "$name.key" -out "$name.crt" -days 365 -subj "/CN=$name-peer" -outform DER
done
```

**Step 2 — Compare certificate sizes**

```bash
ls -la *.crt | awk '{print $5"  "$9}' | sort -n
```

Expected (your bytes will be within a few of these):

```
326  ed25519.crt
389  ecp256.crt
1043  rsa3072.crt
3987  mldsa44.crt
5516  mldsa65.crt
7474  mldsa87.crt
8139  slh128s.crt
17371  slh128f.crt
```

And there it is — the post-quantum certificate size jump, measured with your own hands. The ML-DSA-65 certificate is ~17× the size of the Ed25519 one, and SLH-DSA-128f is over 50×. **Whoa.**

**Step 3 — Look inside a post-quantum certificate**

```bash
openssl x509 -in mldsa65.crt -inform DER -text -noout | head -20
```

Look for the `Signature Algorithm: ML-DSA-65` line and the (large!) public key block. It's a perfectly ordinary X.509 certificate — same structure your browser validates every day — just with a quantum-safe signature inside.

---

### Exercise 3: Sign, verify, and tamper

Certificates are about *trust*, and trust comes from signatures that can't be forged. Let's prove that to ourselves.

**Step 1 — Sign a message with ML-DSA**

```bash
echo "transfer 1000 to tom" > msg.txt
openssl pkeyutl -sign -inkey mldsa65.key -rawin -in msg.txt -out msg.sig
wc -c < msg.sig          # ~3309 bytes
```

**Step 2 — Verify it**

```bash
openssl pkey -in mldsa65.key -pubout -out mldsa65.pub
openssl pkeyutl -verify -pubin -inkey mldsa65.pub -rawin -in msg.txt -sigfile msg.sig
```

Two distinct things happen here, and the key distinction is which key each command touches:

- The **first** command takes our **private** key (`mldsa65.key`, the file we signed with — it holds both halves of the key pair) and extracts *just the public half* into a new file (`mldsa65.pub`) via `-pubout`. The public key is the part you can hand out freely; anyone with it can check our signatures but nobody can forge new ones.
- The **second** command does the actual verification using *only* that **public** key (`-pubin` says "the input key file is a public key"). It confirms the signature in `msg.sig` was produced by the matching private key over exactly the bytes in `msg.txt`. Note it never needs the private key — that's the whole point of public-key signatures: signing is private, verifying is public.

You should see `Signature Verified Successfully`. Nice!

**Step 3 — Tamper with the message and watch it fail**

```bash
echo "transfer 1000 to mallory" > msg.txt
openssl pkeyutl -verify -pubin -inkey mldsa65.pub -rawin -in msg.txt -sigfile msg.sig
```

This time it reports `Signature Verification Failure`. Change a single byte of the signed data and the post-quantum signature rejects it — exactly the unforgeable integrity guarantee we depend on, now resting on lattice math instead of elliptic curves.

**Step 4 — Feel the SLH-DSA difference**

```bash
time openssl pkeyutl -sign -inkey slh128s.key -rawin -in msg.txt -out slh.sig
wc -c < slh.sig          # ~7856 bytes
```

Notice the signing takes *noticeably* longer than ML-DSA, and the signature is more than twice the size. That's the hash-based trade-off in action: ultra-conservative security, but you'd never want this on a high-volume handshake. Perfect for a root CA that signs a few certs a year, though.

---

### Cleanup (OpenSSL lab)

Done with the cert-weighing? Just `exit` — the container was started with `--rm`, so it vanishes along with all the keys and certs you generated. Nothing to clean up on your host.

You just generated post-quantum certificates, measured the size hit, and watched ML-DSA and SLH-DSA sign and verify (and reject a forgery) with your own two hands. Not bad at all, huh? But certificates sitting in a folder are only half the fun — let's actually *use* them to authenticate a real VPN.

---

## Live fire: mutual authentication over real IKEv2

Remember the key-exchange lab, where we stood up two containers and watched them negotiate ML-KEM over a real IKEv2 handshake? Let's do the exact same thing — but this time the star of the show is **authentication**. Two peers, each holding a certificate, are going to prove their identities to each other before the tunnel comes up. That's *mutual* authentication, and it's the real job those certs we just minted were born to do.

We'll do it twice, and the progression is the whole point:

- **First, classical auth with ECDSA (today's posture).** Each peer authenticates with an **ECDSA** certificate. Rock-solid, runs on stable strongSwan. And here's the neat part: we keep the **ML-KEM hybrid key exchange** switched on, so the tunnel is already *post-quantum for key exchange* — just classical for the signature. That's exactly the posture real deployments ship right now (5G fronthaul setups do precisely this).
- **Then, post-quantum auth with ML-DSA (the bleeding edge).** We swap the ECDSA certs for **ML-DSA** ones and rebuild strongSwan from its experimental `ml-dsa` branch. Now *both* halves of the handshake are quantum-safe: ML-KEM for the key exchange, ML-DSA for the signature. Fair warning — this is the frontier, and it behaves like the frontier. We'll be honest about the rough edges.

> **Heads up:** this part uses its own little stack (`authentication/docker-compose.yml`) on its own network, completely separate from the key-exchange lab. Nothing here touches that lab's PSK setup. You'll need Docker, and the first build compiles strongSwan from source (~5 min), same as before.

### How the trust works

Both peers trust one tiny **certificate authority (CA)** we spin up just for the lab. The CA signs two leaf certificates — one for the initiator, one for the responder — and each peer gets the CA certificate pre-installed so it can verify the other side. During the handshake each peer sends *only its own leaf cert*; the CA is already known to both. (That keeps the on-the-wire bytes down, which matters a lot once the certs go post-quantum.)

A helper script, `gen-certs.sh`, does all the minting and drops the files into each peer's credential directory. You just tell it which algorithm to use.

### Exercise 4: Mutual auth with classical certificates (ECDSA)

**Step 1 — Bring up the two peers**

All commands in this live-fire section run from the `authentication/` directory, so hop in first:

```bash
cd authentication
```

```bash
docker compose up -d --build
```

That builds the (stable) strongSwan image and starts `ike-auth-initiator` (172.21.0.2) and `ike-auth-responder` (172.21.0.3) on a private network.

**Step 2 — Mint the CA and both leaf certs**

```bash
docker compose run --rm --build certgen ecdsa
```

The `certgen` helper builds a CA and issues an ECDSA leaf cert for each peer, installing the leaf cert, its private key, and the CA cert into the right folders for both sides. (The `--build` flag makes sure the helper is built from the same image as the peers — this matters once we move to ML-DSA, where the helper must come from the `ml-dsa` branch. Pass `ecdsa` now; we'll pass an ML-DSA variant when we go post-quantum.)

**Step 3 — Load the fresh credentials**

The peers started before the certs existed, so reload them. Load the responder with a one-shot `exec`, then drop into a shell on the initiator for the rest of the exercise:

```bash
docker exec ike-auth-responder swanctl --load-all
docker exec -it ike-auth-initiator bash
```

From here on, every `swanctl` command runs **inside the initiator's shell**. Load its credentials:

```bash
swanctl --load-all
```

To confirm it picked up *its own* cert, ask the daemon what it's holding:

```bash
swanctl --list-certs --utc | head -n 15
```

In that output, two fields tell you the side picked the right cert:

- **`subject`** / **`altNames`** match this peer's identity — `CN=initiator.pqc.lab` and `altNames: initiator.pqc.lab` on the initiator (the responder shows `responder.pqc.lab`). That altName is exactly the IKE ID `swanctl.conf` matches against.
- The **`pubkey`** line ends with **`, has private key`** — e.g. `pubkey: ECDSA 256 bits, has private key`. That suffix is the tell that this is the peer's *own* leaf (it holds the matching private key), not just a cert it learned about. A cleanly loaded peer lists exactly one end-entity cert, and it carries that suffix.

**Step 4 — Bring up the tunnel and prove who's who**

Still inside the initiator's shell, kick off the handshake:

```bash
swanctl --initiate --child auth-child
```

This streams the live handshake. The line to look for is the authentication result — proof that each side verified the other's certificate against the shared CA:

```
[IKE] authentication of 'responder.pqc.lab' with ECDSA_WITH_SHA256_DER successful
```

The handshake success is confirmed by the `initiate completed successfully` line in the initiator output above. Now inspect the security association that came up:

```bash
swanctl --list-sas
```

You should see the IKE SA **ESTABLISHED**, with something like:

```
auth-tunnel: #1, ESTABLISHED, IKEv2, ...
  local  'initiator.pqc.lab' @ 172.21.0.2[4500]
  remote 'responder.pqc.lab' @ 172.21.0.3[4500]
  AES_GCM_16-256/PRF_HMAC_SHA2_256/CURVE_25519/KE1_ML_KEM_768
  auth-child: #1, reqid 1, INSTALLED, TUNNEL, ESP:AES_GCM_16-128
    local  172.21.0.2/32
    remote 172.21.0.3/32
```

Read that proposal line: **`CURVE_25519/KE1_ML_KEM_768`** — the key exchange is post-quantum hybrid (X25519 plus an RFC 9370 additional ML-KEM-768 exchange) — while the two identities (`initiator.pqc.lab` / `responder.pqc.lab`) were proven with ECDSA certificates, each side validating the other's leaf against the shared CA. That's mutual certificate authentication over IKEv2, with a quantum-safe key exchange, running for real on stable strongSwan.

So: KE is already future-proof, auth is still classical. Now let's fix that second half. Exit the container shell before moving on to Exercise 5:

```bash
exit
```

### Exercise 5 (bleeding edge): post-quantum auth with ML-DSA

Time to make the *signature* quantum-safe too. This means swapping in strongSwan's experimental `ml-dsa` branch and reissuing the certificates as ML-DSA.

> **Expectation setting:** this is genuinely experimental. The branch changes often, the IKEv2-for-PQC-auth wire format is still an [IETF draft](https://datatracker.ietf.org/doc/draft-ietf-ipsecme-ikev2-pqc-auth/), and there are known sharp edges (ML-DSA private keys only parse in the 32-byte *seed* format, and large `IKE_AUTH` messages that split into *many* fragments have hit reassembly bugs — strongSwan [issue #2889](https://github.com/strongswan/strongswan/issues/2889)). To stay on the happy path we use **ML-DSA-44** (the smallest variant) and keep the CA off the wire, which holds the handshake to 6 fragments — and in our testing the tunnel comes up cleanly. Push to larger variants or longer cert chains and you can absolutely tip it over; that's the frontier doing frontier things.

**Step 1 — Rebuild the peers from the `ml-dsa` branch**

```bash
docker compose -f docker-compose.yml -f docker-compose.mldsa.yml up -d --build
```

The override (`docker-compose.mldsa.yml`) points the build at `Dockerfile.mldsa`, which compiles strongSwan from the `ml-dsa` branch. The *first* build compiles from source (give it a few minutes on a cold cache); after that Docker caches the compiled layer, so re-running this command is near-instant unless you change the Dockerfile or bust the cache.

**Step 2 — Reissue the certificates as ML-DSA**

```bash
docker compose -f docker-compose.yml -f docker-compose.mldsa.yml run --rm --build certgen ml-dsa-44
```

Same helper, different algorithm — the CA and both leaf certs are now ML-DSA-44. (The `--build` flag is doing real work here: it rebuilds the helper from the `ml-dsa` branch, whose `pki` tool knows the `mldsa44/65/87` key types. Without it you'd reuse the helper image from the ECDSA run — built from stable strongSwan, whose `pki` doesn't know those types — and get `error: invalid key type`.) Notice we didn't touch `swanctl.conf` at all: `auth = pubkey` is algorithm-agnostic, so strongSwan figures out it's ML-DSA straight from the certificate's key type. (That's a genuinely nice property of how this slots into the existing framework.)

**Step 3 — Reload and initiate**

The rebuild in Step 1 recreated the containers, so open a fresh shell. Load the responder, then hop into the initiator (the container names are unchanged from the ECDSA run — `docker exec` doesn't care which image is underneath):

```bash
docker exec ike-auth-responder swanctl --load-all
docker exec -it ike-auth-initiator bash
```

Inside the initiator's shell, first load the new credentials:

```bash
swanctl --load-all
```

Confirm the private key loaded as ML-DSA — you want `ML_DSA_44` here, not `ECDSA`:

```
loaded ML_DSA_44 key from '/usr/local/etc/swanctl/private/initiator.key'
```

Now kick off the handshake:

```bash
swanctl --initiate --child auth-child
```

With ML-DSA-44 this establishes cleanly. The line that proves it's gone fully post-quantum is the authentication result, now signed with ML-DSA:

```
[IKE] authentication of 'responder.pqc.lab' with ML_DSA_44 successful
```

Finally, inspect the security association:

```bash
swanctl --list-sas
```

It looks just like the ECDSA run — same proposal line, same tunnel — except the identities were proven with **ML-DSA** signatures:

```
auth-tunnel: #1, ESTABLISHED, IKEv2, ...
  local  'initiator.pqc.lab' @ 172.21.0.2[4500]
  remote 'responder.pqc.lab' @ 172.21.0.3[4500]
  AES_GCM_16-256/PRF_HMAC_SHA2_256/CURVE_25519/KE1_ML_KEM_768
  auth-child: #1, reqid 1, INSTALLED, TUNNEL, ESP:AES_GCM_16-128
```

Same handshake, same config, fully post-quantum now: ML-KEM for the key exchange *and* ML-DSA for authentication. Take a second to appreciate that — you just brought up a VPN tunnel with *nothing* in the handshake a quantum computer could break. Exit the container shell before Step 4:

```bash
exit
```

**Step 4 — Watch the certificates blow up the handshake**

This is the lesson even if the tunnel is fussy to bring up. Look at the initiator's log **from the host** to see which messages had to be fragmented:

```bash
docker logs ike-auth-initiator | grep -iE "splitting IKE message"
```

You'll see *two* outbound messages getting chopped up:

```
[ENC] splitting IKE message (1249 bytes) into 2 fragments
[ENC] splitting IKE message (6904 bytes) into 6 fragments
```

Each one is a different post-quantum payload straining the MTU:

- The **~1.2 KB message → 2 fragments** is the **ML-KEM key exchange** (carried in `IKE_INTERMEDIATE`, RFC 9370). This one shows up in the ECDSA run too — the key exchange is post-quantum either way.
- The **~6.9 KB message → 6 fragments** is the **`IKE_AUTH`** carrying the ML-DSA leaf cert plus the ML-DSA signature. *This* split is unique to the post-quantum-auth run: an ML-DSA-44 leaf cert (~4 KB DER) plus its signature dwarfs the ~400-byte ECDSA equivalent. For comparison, in the ECDSA run `IKE_AUTH` fit in a single ~900-byte packet and never split — only the key exchange fragmented there.

(The matching *inbound* reassembly — `reassembled fragmented IKE message (6682 bytes)` — scrolls past in the `swanctl --initiate` output rather than `docker logs`: the daemon's stdout records the outbound split but not the inbound reassembly at its default verbosity.)

This is *why* `fragmentation = yes` is non-negotiable for PQC auth, and why the fragment-reassembly path is exactly where the current bugs live.

> **If it doesn't come up:** ML-DSA-44 establishes cleanly in our testing (6 fragments, comfortably inside the reassembly limits), but bump up to ML-DSA-65/87 — or add an intermediate CA so more big certs go on the wire — and you can push the fragment count into the territory of the known reassembly bug ([#2889](https://github.com/strongswan/strongswan/issues/2889)). If a run hangs, check the responder log from the host (`docker logs ike-auth-responder | tail -n 40`) for fragment errors, confirm `fragmentation = yes` on both ends (it is, in the provided config), and stick with `ml-dsa-44`. Remember the goal here isn't a production tunnel — it's standing on the post-quantum authentication frontier and seeing exactly where it bends.

### Cleanup (IKEv2 lab)

```bash
docker compose down

# Remove the generated CA + leaf keys/certs. They were written to these host
# dirs via bind mounts, so `down` doesn't clear them — and they include private
# keys. (.gitignore keeps them out of commits, but they still sit on disk.)
rm -rf config/initiator/private config/initiator/x509 config/initiator/x509ca \
       config/responder/private config/responder/x509 config/responder/x509ca
```

And *now* that's a wrap! You generated post-quantum certificates, measured them, signed and verified with them, and then used them to mutually authenticate a real IKEv2 VPN — first with classical ECDSA, then (at the bleeding edge) with post-quantum ML-DSA, all over a quantum-safe ML-KEM key exchange. You've touched every moving part of post-quantum authentication. Seriously, well done!

---

## Where IKEv2 authentication is heading

"This is great for certificates," you might be thinking, "but can I actually authenticate my strongSwan VPN with ML-DSA *today*?" You just did, in the ML-DSA run above — but with a big asterisk: **it works only on an experimental branch, not a stable release.** Here's the lay of the land.

- **strongSwan's ML-DSA support lives on a branch.** ML-DSA signature support (FIPS 204, all of ML-DSA-44/65/87) is implemented in strongSwan's `ml-dsa` branch ([PR #2626](https://github.com/strongswan/strongswan/pull/2626)) — the very branch the ML-DSA run built from — not in the 6.0.x stable line. You generate ML-DSA keys with the `pki` tool (via the `ml` plugin) just like any other key type, which is exactly what `gen-certs.sh` does for you.
- **Composite/hybrid authentication is separate again.** Combining ECDSA + ML-DSA into one credential — the authentication mirror of hybrid key exchange — is being developed on the `pq-composite-sigs` branch.
- **The IKEv2 wire format is still standardising.** The IPSECME working group has a draft, [`draft-ietf-ipsecme-ikev2-pqc-auth`](https://datatracker.ietf.org/doc/draft-ietf-ipsecme-ikev2-pqc-auth/) (at `-08` as of this writing), that carries ML-DSA and SLH-DSA in IKEv2 by identifying them with their DER-encoded `AlgorithmIdentifier` OIDs and the "Identity" hash (value 5, since these are pure signature schemes). An earlier individual draft, [`draft-sfluhrer-ipsecme-ikev2-mldsa`](https://datatracker.ietf.org/doc/html/draft-sfluhrer-ipsecme-ikev2-mldsa-00), also exists, and strongSwan's implementation differs from both in places (the context-string and prehash-vs-pure questions are still being worked out). Expect the details to shift before this stabilises — which is why the ML-DSA run is bleeding-edge even though ML-DSA-44 comes up cleanly today.

So unlike the key-exchange story — where ML-KEM ships in stable strongSwan 6.0.x and just works — **post-quantum *authentication* in IKEv2 is still emerging.** That's not a gap in this lab; it's the honest state of the world, and it's exactly *why* getting hands-on with the building blocks now (the keys, certs, and signatures you made, plus the experimental tunnel you just stood up) is the most useful thing you can do. When the IKE plumbing lands in a stable release, you'll already get it — and you'll have run it before most people knew it was possible.

---

## Configuration reference

### OpenSSL PQC commands used in this lab

| Command | What it does |
|---------|--------------|
| `openssl list -signature-algorithms` | Show available signature algorithms (confirms ML-DSA / SLH-DSA presence) |
| `openssl genpkey -algorithm ML-DSA-65 -out k.key` | Generate a post-quantum private key |
| `openssl pkey -in k.key -pubout -out k.pub` | Extract the public key |
| `openssl req -x509 -new -key k.key -out k.crt -days 365 -subj "/CN=peer"` | Create a self-signed certificate |
| `openssl x509 -in k.crt -text -noout` | Inspect a certificate (add `-inform DER` for DER files) |
| `openssl pkeyutl -sign -inkey k.key -rawin -in msg -out msg.sig` | Sign a message (one-shot, no external hash) |
| `openssl pkeyutl -verify -pubin -inkey k.pub -rawin -in msg -sigfile msg.sig` | Verify a signature |

### PQC signature parameter sets

| Algorithm | NIST level | Public key | Signature |
|-----------|-----------|-----------|-----------|
| ML-DSA-44 | 2 (~128-bit) | 1312 B | 2420 B |
| ML-DSA-65 | 3 (~192-bit) | 1952 B | 3309 B |
| ML-DSA-87 | 5 (~256-bit) | 2592 B | 4627 B |
| SLH-DSA-128s | 1 (~128-bit) | 32 B | 7856 B |
| SLH-DSA-128f | 1 (~128-bit) | 32 B | 17088 B |
| SLH-DSA-256s | 5 (~256-bit) | 64 B | 29792 B |

(SLH-DSA has many more variants — `s`/`f` for small/fast and SHA2/SHAKE hash families at 128/192/256-bit levels.)

---

## Appendix

- **ML-DSA (FIPS 204)** is the lattice-based, general-purpose default. Fast, moderate sizes. Reach for **ML-DSA-65** unless you have a specific reason not to.
- **SLH-DSA (FIPS 205)** is hash-based: the most conservative security assumption (no lattices, no number theory), at the cost of large, slow signatures. Ideal for high-value, rarely-signed, long-lived anchors (root CAs, firmware signing).
- **FN-DSA / FALCON (FIPS 206)** offers small signatures but is hard to implement in constant time (floating-point); still in draft. Watch this space for bandwidth-sensitive uses.
- **OpenSSL 3.5+** ships all three natively in the default provider — no `oqs-provider` needed. Ubuntu 24.04 (the base image in the companion key-exchange lab) ships OpenSSL 3.0, which predates this support; that's why this lab uses a 3.5+ environment (e.g. `alpine:3.22`, which ships OpenSSL 3.5.x).
- **strongSwan** ML-DSA authentication is on the `ml-dsa` branch (which this lab's ML-DSA run builds from); composite signatures on `pq-composite-sigs`. Not yet in a stable release.

### Reference standards

| Standard | Title | Relevance |
|----------|-------|-----------|
| [FIPS&nbsp;204](https://csrc.nist.gov/pubs/fips/204/final) | Module-Lattice-Based Digital Signature Standard | Defines ML-DSA |
| [FIPS&nbsp;205](https://csrc.nist.gov/pubs/fips/205/final) | Stateless Hash-Based Digital Signature Standard | Defines SLH-DSA |
| [FIPS&nbsp;206](https://csrc.nist.gov/projects/post-quantum-cryptography) | FN-DSA (FALCON) | Draft; small-signature lattice scheme |
| [RFC&nbsp;8032](https://www.rfc-editor.org/rfc/rfc8032) | EdDSA (Ed25519/Ed448) | The classical signature we compare against |
| [RFC&nbsp;7383](https://www.rfc-editor.org/rfc/rfc7383) | IKEv2 Fragmentation | Why big PQC certs/signatures still fit in IKE_AUTH |
| [draft-ietf-ipsecme-ikev2-pqc-auth](https://datatracker.ietf.org/doc/draft-ietf-ipsecme-ikev2-pqc-auth/) | PQC Signature Auth in IKEv2 | IPSECME WG draft for ML-DSA/SLH-DSA authentication in IKEv2 |
| [draft-sfluhrer-ipsecme-ikev2-mldsa](https://datatracker.ietf.org/doc/html/draft-sfluhrer-ipsecme-ikev2-mldsa-00) | ML-DSA in IKEv2 | Earlier individual draft for PQC authentication in IKEv2 |




