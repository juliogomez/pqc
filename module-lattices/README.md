# A Hands-On Module-Lattice Lab

### The One Idea Behind Both ML-KEM and ML-DSA

The companion [key-exchange lab](../ipsec/key-exchange/README.md) makes a VPN's *key exchange* quantum-safe with **ML-KEM**. The [authentication lab](../ipsec/authentication/README.md) makes its *authentication* quantum-safe with **ML-DSA**. Both labs lean on the same "ML-" prefix, which stands for "Module Lattice", and both quietly promise the same thing: "no known quantum attack." This lab is where we cash that check.

This is the **optional deep-dive** behind the other labs: the place to come if you want to understand *why* they're safe rather than just take it on faith. Here's the secret those two labs share: **ML-KEM and ML-DSA stand on the exact same mathematical foundation.** Learn it once, and you understand the security of *both*. No prior crypto-math required: we'll build it from vectors up, in readable Python you run yourself, and finish by launching a *real* lattice attack and watching it hit a wall. The only thing you need installed is **Docker**.

Ready to meet the math a quantum computer can't crack? Let's dig in.

---

## Contents

1. [What are we trying to figure out?](#what-are-we-trying-to-figure-out)
2. [Why should you care?](#why-should-you-care)
3. [From vectors to lattices](#from-vectors-to-lattices)
4. [The hard problem that matters: LWE](#the-hard-problem-that-matters-lwe)
5. [Climbing the ladder: Ring-LWE and Module-LWE](#climbing-the-ladder-ring-lwe-and-module-lwe)
6. [The other half: Module-SIS](#the-other-half-module-sis)
7. [How ML-KEM uses it](#how-ml-kem-uses-it)
8. [How ML-DSA uses it](#how-ml-dsa-uses-it)
9. [Why a quantum computer can't break it](#why-a-quantum-computer-cant-break-it)
10. [Let's get our hands dirty: the lab](#lets-get-our-hands-dirty-the-lab)
11. [Configuration reference](#configuration-reference)
12. [Appendix](#appendix)

---

## What are we trying to figure out?

The question driving this lab: **what *is* a module lattice, why is "the shortest vector problem" so hard that even a quantum computer can't solve it, and how do ML-KEM and ML-DSA turn that hardness into real security?**

By the end of this lab you'll have, with your own hands:

- **Built a lattice** and watched a "bad" basis hide what a "good" basis makes obvious.
- **Seen the magic of noise:** how adding a tiny error term turns trivial linear algebra into a problem nobody knows how to solve efficiently.
- **Implemented a baby ML-KEM** over the same polynomial ring the real thing uses, and watched two peers agree on a shared secret.
- **Run a real lattice attack** and measured the cost exploding as the dimension grows: the concrete reason a cryptographically-relevant quantum computer (CRQC) doesn't rescue the attacker.

---

## Why should you care?

Because this is the *single* foundation under the entire NIST post-quantum lineup that matters most in practice. ML-KEM (FIPS 203, key exchange) and ML-DSA (FIPS 204, signatures) are both "module-lattice-based," and they are the default recommendations for almost everything: TLS, IKEv2, SSH, code signing. Understand module lattices and you've understood *why* the post-quantum internet is built the way it is.

```mermaid
graph TD
  ML["Module lattices over R_q = Z_q[x]/(x^n+1)"]
  MLWE["Module-LWE<br/>(secret hidden by noise)"]
  MSIS["Module-SIS<br/>(no short solution)"]
  KEM["ML-KEM / FIPS 203<br/>key exchange (Lab 1)"]
  DSA["ML-DSA / FIPS 204<br/>signatures (Lab 2)"]
  ML --> MLWE
  ML --> MSIS
  MLWE --> KEM
  MLWE --> DSA
  MSIS --> DSA
```

ML-KEM's security rests on **Module-LWE**. ML-DSA's rests on Module-LWE *and* a sibling problem, **Module-SIS**. Both problems live on the same kind of object (a module lattice), and that object is what the rest of this lab unpacks.

---

## From vectors to lattices

Start simple. A **lattice** is the set of *all integer combinations* of a few starting vectors (a **basis**). Take the two vectors `(1,0)` and `(0,1)`: every integer combination lands on a grid point with whole-number coordinates. That grid, `Z²`, is a lattice.

Here's the twist that makes lattices cryptographically interesting: **the same lattice has infinitely many bases.** The vectors `(15,8)` and `(13,7)` generate the *exact same grid* as `(1,0)` and `(0,1)`. Why exactly the same? Write the two new vectors as the rows of a matrix and take its determinant: `15·7 − 8·13 = 105 − 104 = 1`. A determinant of `±1` means the change of basis is *invertible in whole numbers* (you can get back to `(1,0)` and `(0,1)` using only integer steps), so the two bases reach precisely the same set of points, with none gained and none lost. But `(15,8)` and `(13,7)` are a **bad** basis, while `(1,0)` and `(0,1)` are a **good** one. What makes them bad? They are *long* (length about 17, versus length 1 for the unit vectors) and *nearly parallel* (both point up and to the right at almost the same angle), instead of short and perpendicular.

Why does this matter? Two famously hard problems:

- **SVP (Shortest Vector Problem):** find the shortest non-zero vector in the lattice.
- **CVP (Closest Vector Problem):** given an arbitrary point, find the nearest lattice point.

With a **good** basis, both are easy. To solve CVP, you express the target point in terms of the basis vectors and round each coordinate to the nearest whole number; because the vectors are short and perpendicular, that rounded point really is the closest one. And the shortest vectors are essentially the basis vectors themselves, so SVP is sitting right in front of you.

With a **bad** basis, that rounding trick collapses. Since `(15,8)` and `(13,7)` are long and nearly parallel, reaching a point close to your target means *adding large positive and negative multiples of the two that almost cancel out*, and the right combination is no longer the obvious "round each coordinate" answer. You have to search for it. In two dimensions you could still brute-force that search, but the number of plausible integer combinations to try grows *exponentially* with the dimension, so past a few hundred dimensions no known method, classical or quantum, finds the answer in reasonable time. Same lattice, same points; trivial with the good basis, hopeless with the bad one. That gap is the whole game.

And a public key, it turns out, is essentially a **bad basis**: it pins an attacker to the hard version of these problems, while the matching secret lets the legitimate owner sidestep them. The whole field rests on this asymmetry. You'll see it directly in [Exercise 1](#exercise-1-build-a-lattice-good-vs-bad-bases).

> **The dimension is everything.** "Dimension" simply means how many coordinates each vector has (equivalently, the number of basis vectors). Our toy example is 2-dimensional, and in 2-D you can solve SVP by eye. But the difficulty grows *exponentially* with the dimension, and real schemes use hundreds: at that size, a bad basis leaves no efficient solution, for classical or quantum computers alike.

---

## The hard problem that matters: LWE

Lattices are the stage; **Learning With Errors (LWE)** is the play. It's the problem ML-KEM and ML-DSA actually reduce to, and it's beautifully simple.

Pick a secret vector `s` and keep it hidden. Choose a random matrix `A`, then compute:

```
b = A·s + e   (mod q)
```

where `e` is a small **error** (or **noise**) vector. Here `q` is a fixed public **modulus**: a number chosen up front as part of the scheme's parameters, so that every value is reduced into the range `0` to `q−1` (it is usually a prime; the real ML-KEM uses `q = 3329`, the value our toy code in the lab will borrow). Now publish the pair `(A, b)`, but **never** `s` itself. The challenge for an attacker: recover the hidden `s` from the public `(A, b)`.

Before going further, it's worth being clear about what `A` is, and what it is *not*: it isn't a lattice. On its own it's just a rectangular grid of random numbers mod `q`, the coefficients of the linear system `A·s ≈ b`. The lattice only shows up later, once an attacker reframes "find `s`" as a search for a short or close vector. At that point `A` is what *defines* the lattice, but the matrix itself is still not one.

With that settled, what does `b` actually look like? Take `A` to be an `m × n` matrix and `s` a vector of length `n`. The product `A·s` is the ordinary matrix-times-vector operation: dot each of `A`'s `m` rows with `s` to get one number per row, which gives a vector of length `m`. Adding the length-`m` error `e` and reducing every entry mod `q` leaves `b` as a plain vector of `m` integers, each between `0` and `q−1`. (Our toy code keeps `A` square, so `m = n`.)

**So where's the lattice from the previous section?** Right here. Take every point you can build as `A·s` (mod `q`) as `s` ranges over all integer vectors; together with the wrap-around multiples of `q`, those points form a lattice, and `A` is a basis for it, just as `(15,8)` and `(13,7)` were a basis for their grid. Watch the roles carefully, because this is where it's easy to get tangled up: `A` is the *basis*, and `s` is the list of *integer coefficients* that selects one particular lattice point. The product `A·s` is "basis times coefficients", i.e. one specific point of the lattice. The equation `b = A·s + e` then says `b` sits just a tiny step `e` off that lattice point. So "recover `s`" becomes "find the lattice point nearest to `b`, then read off its coefficients", which is precisely the **closest-vector problem (CVP)** from before. And the public key hands the attacker only `A`, a *bad* basis for that lattice, which is exactly what makes the CVP hard.

> **Then where's the *good* basis, and is it `s`?** No, and this is the subtle bit worth pausing on. `s` is not a basis at all; it's the secret *coordinates*. And the legitimate key holder does **not** secretly own a good basis that it uses to solve CVP. It never solves a lattice problem in the first place: it *generated* `s` itself, so it simply knows the answer. The good-versus-bad-basis story is really about the **attacker**, who is handed the bad basis `A` and no shortcut, and is therefore stuck with a hard CVP. (ML-KEM and ML-DSA keep the small secret `s`, and never publish anything that would need a good basis to undo.) So nothing here is "good basis times bad basis": there's one basis, `A` (bad, public), one coefficient vector, `s` (secret), and one small error, `e`.

Now here's the entire point, in one sentence: **without the noise it's trivial, with the noise it's intractable.**

- **No noise (`e = 0`):** `b = A·s` is just a system of linear equations. Any schoolchild with [Gaussian elimination](https://en.wikipedia.org/wiki/Gaussian_elimination) (the standard, fast method for solving such systems exactly) recovers `s`. Zero security.
- **With noise:** every equation is *slightly* wrong, by an unknown small amount, so Gaussian elimination just amplifies those errors into garbage. The only known way forward is the CVP attack above: run **[lattice reduction](https://en.wikipedia.org/wiki/Lattice_reduction)**, the family of algorithms that slowly grind a bad basis toward a good one until the nearest lattice point to `b` becomes findable. The catch is cost. Producing a basis good *enough* takes time that grows **exponentially with the dimension** (the number of coordinates in `s`): doubling the dimension does not double the work, it squares it and worse. At a handful of dimensions it is instant; at the hundreds real schemes use, it is hopeless, for classical and quantum machines alike. You'll watch this curve bend out of reach in [Exercise 4](#exercise-4-run-a-real-attack-and-watch-it-stall).

That single addition of `e` is the difference between "homework" and "post-quantum cryptography." You'll break the noise-free version and watch the noisy version defeat the same code in [Exercise 2](#exercise-2-the-magic-of-noise-lwe).

> **Why noise is safe to add.** It seems reckless to base encryption on deliberately *wrong* equations. Here's the trick. To hide a message, a scheme buries it under a term like `A·s` that looks like uniform random junk to anyone without the secret: a one-time **mask**. The legitimate user holds a trapdoor (the secret `s`) that lets them recompute that exact masking term and *subtract it back off* ("cancel the mask"), leaving just the message plus the small noise, which then rounds away cleanly, provided the noise stays within a **budget** (below `q/4` in the toy encryption you'll build in [Exercise 2](#exercise-2-the-magic-of-noise-lwe)). Too much noise and even the legitimate user gets the wrong answer (a "decryption failure"). Real ML-KEM tunes its parameters so this budget is essentially never exceeded.

---

## Climbing the ladder: Ring-LWE and Module-LWE

Plain LWE works, but it's heavy: `A` is a big `n×n` matrix of independent random numbers, so keys are large and arithmetic is slow. The fix is to add *structure*, and that comes in three levels:

| Level | What `A`'s entries are | Trade-off |
|------|------------------------|-----------|
| **LWE** | plain integers mod `q` | most conservative, but big keys / slow |
| **Ring-LWE** | elements of one polynomial ring `R_q = Z_q[x]/(x^n+1)` | small, fast (one big ring), but maximum algebraic structure |
| **Module-LWE** | small `k×k` matrices *of ring elements* | the sweet spot: fast ring math, tunable security |

**Ring-LWE** replaces integers with *polynomials* in the ring `R_q = Z_q[x]/(x^n+1)`: degree-`n` polynomials with coefficients mod `q`, where `x^n` wraps around to `-1` (so multiplication is "negacyclic"). One ring element packs `n` numbers, multiplication is fast (it can use the [Number-Theoretic Transform](https://en.wikipedia.org/wiki/Discrete_Fourier_transform_over_a_ring#Number-theoretic_transform)), and keys shrink dramatically. The slight worry: all that extra algebraic structure *might* one day give an attacker a foothold (none is known, but in cryptography conservatism is wise).

> **Background: what is a polynomial ring?** A **ring** is just a number system where you can add, subtract, and multiply and always land back inside the system (division is not required). The plain integers are a ring; so are the integers mod `q`. A **polynomial ring** is what you get when the "numbers" are polynomials. Let's unpack `Z_q[x]/(x^n+1)` one symbol at a time:
>
> - **`Z_q`**: the coefficients live in the integers mod `q` (e.g. `0..q-1`). Every number wraps around at `q`, exactly like a clock face.
> - **`Z_q[x]`**: all polynomials in a variable `x` whose coefficients come from `Z_q`, things like `3 + 5x + 2x^2`. You add and multiply them the ordinary way, reducing coefficients mod `q`.
> - **`/(x^n+1)`**: the "quotient" part. It says *work modulo the polynomial `x^n+1`*. This is the exact same idea as ordinary "mod" arithmetic on integers, just with a polynomial instead of a number. Recall that "mod `q`" means you treat `q` as `0`: whenever a `q` shows up, you replace it with `0` and only keep what's left over. Here, "mod `x^n+1`" means you treat the whole polynomial `x^n+1` as `0`. And if `x^n + 1 = 0`, then rearranging gives `x^n = -1`. So the rule "mod `x^n+1`" is just a compact way of saying "every time you see `x^n`, replace it with `-1`." Any time a product pushes the degree up to `x^n` or beyond, you fold it back down: `x^n` becomes `-1`, `x^{n+1}` becomes `-x`, and so on. This keeps every element pinned to degree below `n`.
>
> The payoff: an element of `R_q` is nothing more exotic than a list of `n` coefficients `(a_0, a_1, ..., a_{n-1})`, i.e. **a vector of `n` numbers mod `q`**. Addition is coordinate-wise; multiplication is polynomial multiplication followed by the `x^n = -1` wraparound (a "negacyclic convolution"). So when ML-KEM talks about a "vector of ring elements," picture a short stack of these `n`-number blocks. You'll manipulate them directly in [Exercise 3](#exercise-3-build-a-baby-ml-kem).
>
> **A worked example.** Take the tiny ring `Z_17[x]/(x^4+1)`: coefficients mod `17`, polynomials of degree below `4` (so every element is just 4 numbers), and the rule `x^4 = -1`. Let `a = 1 + 2x` (the vector `(1, 2, 0, 0)`) and `b = 3 + 4x^3` (the vector `(3, 0, 0, 4)`).
>
> - **Add** (coordinate-wise, no wraparound of `x`): `a + b = 4 + 2x + 4x^3 = (4, 2, 0, 4)`.
> - **Subtract** (coordinate-wise, then reduce mod `17`): `a - b = -2 + 2x - 4x^3 = (15, 2, 0, 13)`.
> - **Multiply** (this is where the ring "wraps"): expand normally first,
>   ```
>   (1 + 2x)(3 + 4x^3) = 3 + 6x + 4x^3 + 8x^4
>   ```
>   The `8x^4` term has degree 4, too high to keep. Apply `x^4 = -1`, so `8x^4` becomes `-8`:
>   ```
>   = (3 - 8) + 6x + 4x^3 = -5 + 6x + 4x^3  =  (12, 6, 0, 4)  (mod 17)
>   ```
>   That fold-back is what keeps the result a 4-coefficient element instead of growing without bound. (Ordinary "cyclic" rings use `x^n = +1`; the `-1` here is why this flavor is called *negacyclic*. Add and subtract never trigger it; only multiplication can push the degree to `n` or higher.)
>
> **Why this is fast.** Done naively, that multiply costs `n²` coefficient products (every term times every term). The Number-Theoretic Transform is an exact, integer-only cousin of the FFT: transform both operands, multiply them **coordinate-wise** (`n` cheap products), then transform back, for a total cost of about `n·log(n)` instead of `n²`. At ML-KEM's `n = 256` that is the difference between ~65,000 and ~2,000 multiplications per ring product, which is why real schemes pick a `q` that makes the NTT available.

**Module-LWE** is what ML-KEM and ML-DSA actually use. Instead of one giant ring element (Ring-LWE) or `n²` lonely integers (LWE), you build small **vectors and matrices whose entries are ring elements**. The "module rank" `k` is how many ring elements you stack. This gives you:

- **Ring-LWE's speed**, because the underlying arithmetic is still the fast ring `R_q`.
- **LWE-like tunability**, because you dial security up or down just by changing `k` *without touching the ring*.

That last point is the punchline you'll prove in [Exercise 3](#exercise-3-build-a-baby-ml-kem): **ML-KEM-512, -768, and -1024 are the same ring (`n=256, q=3329`) with `k = 2, 3, 4`.** The only thing that changes between security levels is how many ring elements you stack. A "module lattice" is exactly the lattice you get when you write out one of these module equations over the integers.

---

## The other half: Module-SIS

LWE has a twin: the **Short Integer Solution (SIS)** problem. Where LWE asks "find the *hidden secret*," SIS asks "find a *short solution* to a random linear system":

```
Given a random A, find a short, non-zero z with   A·z = 0   (mod q)
```

Finding *some* `z` is easy (linear algebra); finding a **short** one is hard: it's an SVP-flavored lattice problem again. In **module** form (entries are ring elements) it becomes **Module-SIS (MSIS)**.

Why introduce it? Because signatures need it. Roughly: **MLWE hides the signing key, and MSIS makes forgery hard**: a forger would have to produce a short solution to a random system, i.e. solve MSIS. ML-DSA leans on both. That's the whole reason this lab covers both problems: ML-KEM needs one, ML-DSA needs both, and they're two faces of the same lattice hardness.

---

## How ML-KEM uses it

ML-KEM (FIPS 203, the [key-exchange lab](../ipsec/key-exchange/README.md)'s star) is Module-LWE wearing a key-encapsulation costume. Stripped to its skeleton (which you'll build in [Exercise 3](#exercise-3-build-a-baby-ml-kem)):

1. **Key generation.** Sample a public matrix `A` (`k×k` ring elements) and *short* secret/error vectors `s`, `e`. Publish `t = A·s + e`. That `t` is a Module-LWE sample: `s` is buried under noise, so the public key leaks nothing usable.
2. **Encapsulation.** The sender picks fresh short randomness `r`, computes `u = Aᵀ·r + e₁` and `v = tᵀ·r + e₂ + encode(message)`, and sends `(u, v)`.
3. **Decapsulation.** The holder of `s` computes `v − sᵀ·u`. The `A·r` masking terms cancel through `s`, leaving `encode(message) + (small noise)`, which **rounds back** to the message, provided the noise stayed under budget.

Wrap a hash around the recovered message and both sides have a shared secret. (Real ML-KEM adds the Fujisaki–Okamoto transform to upgrade this from "secure if nobody cheats" to "secure against active attackers," but the lattice core is exactly the above.) The chunky ML-KEM public keys and ciphertexts that fragment across `IKE_INTERMEDIATE` in the key-exchange lab? Those are these `t`, `u`, and `v` vectors going over the wire.

---

## How ML-DSA uses it

ML-DSA (FIPS 204, the [authentication lab](../ipsec/authentication/README.md)'s star) is a **Fiat–Shamir signature** built on the same module lattice, and this is where **both** MLWE and MSIS show up:

1. **Keys.** The public key is (essentially) another MLWE sample `t = A·s₁ + s₂` with short secrets `s₁`, `s₂`. MLWE hardness keeps the secret hidden.
2. **Signing.** Commit to short randomness `y` via `w = A·y`. Hash the message and `w` into a small challenge `c`. Respond with `z = y + c·s₁`.
3. **The catch: rejection sampling.** A naive `z` would leak the secret `s₁`. So ML-DSA *rejects and retries* whenever `z` strays outside a safe range ("Fiat–Shamir with aborts"), ensuring signatures reveal nothing about the key.
4. **Verification.** Check that `A·z − c·t` reproduces the committed `w` *and* that `z` is short.

Forging a signature without the key means producing a short `z` satisfying that relation for a fresh challenge, which is an **MSIS** problem. So ML-DSA's unforgeability rests on MSIS, while its key secrecy rests on MLWE. The enormous ML-DSA certificates and signatures that blow `IKE_AUTH` up to six fragments in the authentication lab are these `t` and `z` values.

> **What we actually build below:** the hands-on exercises take the *KEM* side (ML-KEM / Module-LWE) all the way to a working shared secret, because that's the cleanest way to watch a module lattice do real work. Module-SIS and the full ML-DSA signature stay on paper here. If you want to see ML-DSA actually sign and verify, that's the job of the [IKEv2](../ipsec/authentication/README.md) and [TLS](../tls/authentication/README.md) authentication labs.

> **One foundation, two jobs.** ML-KEM = MLWE → encryption/KEM. ML-DSA = MLWE (hide key) + MSIS (block forgery) → signatures. Different costumes, same module-lattice hardness underneath. That's why a single lab can explain both.

---

## Why a quantum computer can't break it

This is the question every PQC discussion circles back to. RSA and elliptic curves *die* to a quantum computer, so why do these survive? Three reasons, and you'll watch the third one happen in [Exercise 4](#exercise-4-run-a-real-attack-and-watch-it-stall).

### 1. Shor's algorithm simply doesn't apply

Shor's algorithm, the quantum bomb that breaks RSA and ECC, isn't a generic "solve hard math" machine. It solves one specific thing astonishingly well: the **abelian hidden subgroup problem (HSP)**, which is the abstract pattern behind *factoring* and *discrete logarithms*. Both boil down to finding a hidden **period** in a function over a commutative group, and the quantum Fourier transform finds periods almost for free.

Lattice problems have **no such periodic structure**. The closest anyone has tied SVP/LWE to is the *dihedral* (non-abelian) HSP, and despite 20+ years of effort, there is **no known efficient quantum algorithm** for the dihedral HSP. Shor's machinery has no foothold. This is the deep reason lattices were chosen.

### 2. Grover's algorithm only halves the exponent

The other famous quantum tool, Grover's search, speeds up *brute force*, but only quadratically: a `2^k` search becomes `2^(k/2)`. That's real, but it's not catastrophic, and NIST already prices it in. Security levels are *defined* so that even a Grover-equipped adversary stays above the bar (this is why ML-KEM-768 targets a level comfortably above the symmetric-key line). Quadratic is not exponential.

### 3. The best attack stays exponential, even quantumly

The genuinely best known attack on LWE/MLWE, classical or quantum, is **lattice reduction** (BKZ), whose engine is solving SVP in a "block" via **sieving**. Its cost is exponential in the block size `b`, and `b` must grow with the lattice dimension. Quantum sieving helps only at the margin:

| Sieve | Cost (in the block size `b`) |
|-------|------------------------------|
| Best classical | ~`2^(0.292·b)` |
| Best known quantum | ~`2^(0.265·b)` (some estimates ~`2^(0.257·b)`) |

A smaller constant in the exponent, but **still exponential**. Shaving a sliver off an exploding curve leaves it exploding. NIST's parameters carry margin precisely for this.

> **The bottom line:** every quantum tool we know either *doesn't apply* (Shor), *only square-roots* the work (Grover), or *barely dents the exponent* (quantum sieving). The exponential wall you'll watch rise in [Exercise 4](#exercise-4-run-a-real-attack-and-watch-it-stall) is what a CRQC runs into, and can't climb.

---

## Let's get our hands dirty: the lab

Enough theory: let's run it. Everything here runs **locally on your own workstation** inside one tiny throwaway Docker container with Python, `numpy`, and `fpylll` (a real lattice-reduction library). Four exercises, each a script you run and can edit:

- **[Exercise 1](#exercise-1-build-a-lattice-good-vs-bad-bases)**: build a lattice, feel the good-vs-bad-basis gap, and watch LLL reduce one.
- **[Exercise 2](#exercise-2-the-magic-of-noise-lwe)**: break noise-free LWE with linear algebra, then watch noise defeat it.
- **[Exercise 3](#exercise-3-build-a-baby-ml-kem)**: implement a mini Module-LWE KEM over the real ring and agree on a shared secret.
- **[Exercise 4](#exercise-4-run-a-real-attack-and-watch-it-stall)**: launch a real lattice attack and measure the cost exploding.

### Prerequisites

Just **Docker**, with the Compose v2 plugin (the `docker compose` subcommand, not the old standalone `docker-compose`). Everything else, Python, `numpy`, and `fpylll`, lives inside the container, so there is nothing to install on your host and nothing to uninstall later. The first `docker compose build` takes a minute or two while it pulls Debian and the lattice library; after that it is instant. No prior crypto-math needed, though if you are comfortable with vectors and "mod q" arithmetic you will move through it faster.

### Build and start

Run all commands in this lab from the `module-lattices/` directory:

```bash
cd module-lattices
```

```bash
# Build the lab image (quick, just installs Python + numpy + fpylll)
docker compose build

# Start the container in the background
docker compose up -d

# Confirm it's up
docker compose ps
```

The `scripts/` directory is mounted into the container at `/lab`, so you can edit the scripts on your workstation and rerun instantly. Run each exercise with `docker exec`:

```bash
docker exec lattice-lab python3 lattice_basics.py
```

(Prefer an interactive shell? `docker exec -it lattice-lab bash` drops you into `/lab`, where `vim` is available for tinkering.)

> **Why your numbers will match the ones below:** each script pins its random seed, so the vectors, hashes, and noise readings you see are the same ones printed here, run after run. Change a seed (or any parameter) and the numbers move, that's expected, and honestly a good way to convince yourself nothing is faked. The one thing that *will* drift is the wall-clock times in Exercise 4, since those depend on your machine.

---

### Exercise 1: Build a lattice, good vs bad bases

```bash
docker exec lattice-lab python3 lattice_basics.py
```

**Part A** shows two bases for the *same* lattice: one good, one bad:

```
good basis : [[1, 0], [0, 1]]
bad  basis : [[15, 8], [13, 7]]
det(good)  = 1
det(bad)   = 1   (equal magnitude -> same lattice)
Hadamard ratio  good = 1.0000   bad = 0.0631   (closer to 1 is better)
Shortest basis vector  good = 1.00   bad = 14.76
```

Both have determinant 1, so they generate the *identical* grid of points, but the "Hadamard ratio" (an orthogonality measure: 1.0 is perfectly perpendicular, near 0 is badly skewed) screams the difference. The bad basis's shortest vector is ~15× longer than the good one's. A public key hands an attacker exactly this kind of skewed basis.

**Part B** runs **LLL lattice reduction** on the bad basis and recovers a good one:

```
LLL-reduced basis : [[-1, 0], [0, -1]]
Hadamard ratio after LLL = 1.0000
Shortest vector after LLL = 1.00
```

LLL turned the long, skewed basis straight back into the unit vectors (up to sign). In 2-D, reduction is trivial, which is the setup for the bad news in **Part C**:

```
   dimension d =    2: LLL may be off by up to 2^0.5
   dimension d =   50: LLL may be off by up to 2^24.5
   dimension d =  256: LLL may be off by up to 2^127.5
   dimension d =  768: LLL may be off by up to 2^383.5
```

LLL is fast (polynomial time), but its *quality guarantee* (how close to the true shortest vector it promises to get) degrades exponentially with dimension. At `d = 768` (ML-KEM-768's secret dimension) that guarantee is useless, and closing the gap means BKZ with large blocks. Hold that thought for [Exercise 4](#exercise-4-run-a-real-attack-and-watch-it-stall).

---

### Exercise 2: The magic of noise (LWE)

```bash
docker exec lattice-lab python3 toy_lwe.py
```

**Part A**: noise-free LWE is just linear algebra, and the script solves it exactly:

```
secret s          : [3306, 39, 323, 640, 3225, 2303, 2935, 667]
recovered (no e)  : [3306, 39, 323, 640, 3225, 2303, 2935, 667]
match             : True
-> With e = 0, anyone who sees (A, b) just solves for s. No security.
```

**Part B**: add a tiny error vector (entries in `{-2,…,2}`) and run the *exact same* Gaussian elimination:

```
error e added     : [1, -1, 0, -2, 1, 2, 1, -2]
recovered (with e): [3262, 747, 1459, 1817, 650, 1873, 3199, 111]
secret s          : [3306, 39, 323, 640, 3225, 2303, 2935, 667]
difference        : [-44, 708, 1136, 1177, 754, -430, 264, -556]
```

A handful of `±2` nudges and the recovered "secret" is total garbage. The noise propagates through elimination and explodes. *This* is the LWE hardness, and no efficient classical or quantum algorithm is known to undo it at real dimensions.

**Part C** builds a tiny Regev-style bit encryption and shows the **noise budget**. With a healthy budget the bits round-trip perfectly; crank the noise past `q/4` and decryption starts flipping bits:

```
Encrypting the bits [1, 0, 1, 1, 0] with a healthy noise budget:
   sent     = [1, 0, 1, 1, 0]
   recovered= [1, 0, 1, 1, 0]   correct = True

Now crank the per-ciphertext noise way past the q/4 budget:
   sent     = [1, 0, 1, 1, 0]
   recovered= [1, 0, 1, 0, 0]   correct = False
```

That tension, *enough* noise to hide the secret but *little enough* to decrypt reliably, is the tightrope every lattice scheme walks. ML-KEM's parameters are chosen so the "decryption failure" probability is astronomically small.

---

### Exercise 3: Build a baby ML-KEM

```bash
docker exec lattice-lab python3 baby_kyber.py
```

This is the payoff: a working **Module-LWE key encapsulation mechanism** in miniature, over the *same ring* ML-KEM uses (`R_q = Z_q[x]/(x^n+1)` with `q = 3329`), just with toy sizes (`n=16, k=2`).

**Part A** encrypts and decrypts a message, reporting the actual noise:

```
PART A - encrypt / decrypt a message
   message  : [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1]
   recovered: [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1]
   match    : True
   peak decryption noise = 15  (must stay < q/4 = 832)
```

The secret `s` is the trapdoor: only it cancels the `A·r` mask, leaving message + small noise (peak 15, comfortably under the 832 budget) that rounds back cleanly: the same mechanism from [Exercise 2](#exercise-2-the-magic-of-noise-lwe), now over polynomials.

**Part B** runs it as an actual KEM: both peers derive the same shared key:

```
PART B - key encapsulation (the actual ML-KEM job)
   encapsulated shared secret : 256e41b497074a6ff5e6921c189c443c...
   decapsulated shared secret : 256e41b497074a6ff5e6921c189c443c...
   secrets agree              : True
```

And the closing table nails the whole point of "module":

```
   toy here        real ML-KEM-768
   n  = 16          n  = 256        (ring degree, SAME for all sizes)
   q  = 3329        q  = 3329       (SAME prime modulus)
   k  = 2           k  = 3          (module rank: bumps security level)
   The ONLY structural difference between ML-KEM-512/768/1024 is k=2/3/4.
```

The same ring, the same arithmetic: security level is just the module rank `k`. That's the engineering elegance that made module lattices win.

> **Tinker with it.** Open `scripts/baby_kyber.py`, bump `K` to 3 or 4 (the real ML-KEM-768/1024 ranks), or shrink `N`, and rerun. Watch the public key grow with `k` while decryption keeps working.

---

### Exercise 4: Run a real attack and watch it stall

```bash
docker exec lattice-lab python3 attack_scaling.py
```

Now we *attack* LWE for real. The script builds genuine LWE instances, embeds each as a "unique shortest vector" problem (the standard **primal** attack), and unleashes `fpylll`'s LLL and BKZ on it, timing each as the dimension climbs:

(You'll notice the header says `q=97`, not the `3329` from the baby-Kyber lab. That's deliberate: a small modulus and tiny ternary secrets keep the embedded lattice loose enough that the reductions actually finish on a laptop. The point here is the *scaling* of the effort as `n` grows, not the specific modulus.)

```
LWE dim n | lattice dim |  solved? |       effort |  time (s)
------------------------------------------------------------------------
       20 |          41 |     True |          LLL |      0.01
       30 |          61 |     True |          LLL |      0.01
       40 |          81 |     True |          LLL |      0.03
       50 |         101 |     True |       BKZ-20 |      1.62
       60 |         121 |     True |       BKZ-20 |      3.54
```

(Your exact times will differ, but the *shape* won't.) The small instances fall instantly to cheap, polynomial-time LLL. Then, right around `n = 50` here, LLL **stalls**, BKZ has to take over, and the clock starts climbing fast, because BKZ's cost is exponential in its block size. That inflection is the exponential wall coming into view.

The script then extrapolates to real parameters (where the attack lattice has dimension ~1500+, the required BKZ block size runs into the *hundreds*, and the cost blows past `2^150`) and closes with the three reasons a CRQC doesn't help:

```
WHY A CRYPTOGRAPHICALLY-RELEVANT QUANTUM COMPUTER (CRQC) DOES NOT HELP

1. SHOR DOES NOT APPLY.        (no periodic / abelian-HSP structure to exploit)
2. GROVER ONLY HALVES THE EXPONENT.   (quadratic speedup, already priced in)
3. QUANTUM SIEVING SHAVES A CONSTANT, NOT THE CURVE.  (2^0.292b -> 2^0.265b)
```

You just watched, in wall-clock seconds, the thing the theory promised: the cost of breaking a lattice explodes with dimension, and no known quantum trick changes that verdict. *That's* why ML-KEM and ML-DSA are safe.

> **Push it further.** Edit `scripts/attack_scaling.py` and add `70` or `80` to the dimension loop, but be ready to wait. Each step up the dimension is a step up the exponential. That impatience you feel is the security.

---

### Troubleshooting

- **`python3: can't open file '/lab/scripts/...'`** — drop the `scripts/` prefix. The scripts are mounted straight into the container's working directory, so it's just `python3 baby_kyber.py`.
- **`ModuleNotFoundError: No module named 'numpy'` (or `fpylll`)** — you ran the script on your host by accident. Those libraries only exist inside `lattice-lab`; always go through `docker exec lattice-lab ...`.
- **`docker exec` says the container isn't running** — start it with `docker compose up -d`, then `docker compose ps` to confirm.
- **Exercise 4 seems to hang around `n=60`** — it isn't hung, BKZ is just grinding. That pause *is* the lesson. If you added bigger dimensions to the loop, settle in.

### Cleanup

```bash
docker compose down
```

That stops and removes the container. The built image is kept, so the next `docker compose up -d` is instant. The scripts live in your clone, untouched.

And that's the foundation! You built a lattice, watched noise turn easy math into hard math, implemented a baby ML-KEM over the real ring, and ran an honest lattice attack into the exponential wall that protects every module-lattice scheme. The next time the [key-exchange](../ipsec/key-exchange/README.md) or [authentication](../ipsec/authentication/README.md) lab says "module-lattice-based" and "no known quantum attack," you'll know *exactly* what's holding the line. Nicely done!

---

## Configuration reference

### The shared ring

Both ML-KEM and ML-DSA work over `R_q = Z_q[x]/(x^n+1)` with the **same ring degree `n = 256`**. They differ in the modulus `q` and in how many ring elements they stack (the module dimensions):

| Scheme | `n` | `q` | Underlying problems |
|--------|-----|-----|---------------------|
| ML-KEM (FIPS 203) | 256 | 3329 | Module-LWE |
| ML-DSA (FIPS 204) | 256 | 8380417 (`2²³ − 2¹³ + 1`) | Module-LWE + Module-SIS |

### ML-KEM parameter sets (FIPS 203)

| Parameter set | NIST level | Module rank `k` | `η₁` / `η₂` | Public key | Ciphertext |
|---------------|-----------|-----------------|-------------|-----------|-----------|
| ML-KEM-512 | 1 | 2 | 3 / 2 | 800 B | 768 B |
| **ML-KEM-768** | **3** | **3** | **2 / 2** | **1184 B** | **1088 B** |
| ML-KEM-1024 | 5 | 4 | 2 / 2 | 1568 B | 1568 B |

The only structural knob across the three is the module rank `k = 2/3/4`. Bigger `k` → a taller Module-LWE instance → a higher-dimensional lattice → more security (and bigger keys). ML-KEM-768 is the recommended default, the same one the [key-exchange lab](../ipsec/key-exchange/README.md) negotiates.

### ML-DSA parameter sets (FIPS 204)

| Parameter set | NIST level | `(k, ℓ)` | `η` | Public key | Signature |
|---------------|-----------|----------|-----|-----------|-----------|
| ML-DSA-44 | 2 | (4, 4) | 2 | 1312 B | 2420 B |
| **ML-DSA-65** | **3** | **(6, 5)** | **4** | **1952 B** | **3309 B** |
| ML-DSA-87 | 5 | (8, 7) | 2 | 2592 B | 4627 B |

Here the module dimensions `(k, ℓ)` set both the MLWE key-hiding hardness and the MSIS forgery hardness. ML-DSA-65 is the general-purpose default, the family the [authentication lab](../ipsec/authentication/README.md) generates certificates from.

---

## Appendix

- **Module lattices are the practical compromise.** Plain LWE is the most conservative but heavy; Ring-LWE is lean but maximally structured; **Module-LWE** sits between them and lets a single ring (`n=256`) serve every security level by varying the module rank. That engineering win is why both ML-KEM and ML-DSA are module-lattice schemes.
- **`fpylll`** (used in [Exercise 1](#exercise-1-build-a-lattice-good-vs-bad-bases) and [Exercise 4](#exercise-4-run-a-real-attack-and-watch-it-stall)) is the Python interface to **`fplll`**, the standard open-source lattice-reduction library. The LLL and BKZ you run here are the same algorithms cryptanalysts use to *estimate* the security of real parameter sets, just pointed at toy dimensions so they finish in seconds.
- **The toy crypto is for intuition only.** `toy_lwe.py` and `baby_kyber.py` use tiny parameters, no constant-time arithmetic, no Number-Theoretic Transform, and no Fujisaki–Okamoto / Fiat–Shamir-with-aborts hardening. Never use them for anything real: they exist to make the structure visible, exactly as this repo's other labs keep their non-subject parts deliberately trivial.
- **Why no Shor here, in one line:** factoring and discrete log are the *abelian* hidden subgroup problem (period-finding), which the quantum Fourier transform devours; lattice problems aren't, and the nearest fit (the *dihedral* HSP) has no known efficient quantum algorithm.

### Reference standards and papers

| Reference | Title | Relevance |
|-----------|-------|-----------|
| [FIPS&nbsp;203](https://csrc.nist.gov/pubs/fips/203/final) | Module-Lattice-Based Key-Encapsulation Mechanism | Defines ML-KEM (Module-LWE) |
| [FIPS&nbsp;204](https://csrc.nist.gov/pubs/fips/204/final) | Module-Lattice-Based Digital Signature Standard | Defines ML-DSA (Module-LWE + Module-SIS) |
| [Regev&nbsp;2005](https://cims.nyu.edu/~regev/papers/qcrypto.pdf) | On Lattices, Learning with Errors, ... | Introduces LWE and its hardness |
| [Lyubashevsky–Peikert–Regev](https://eprint.iacr.org/2012/230) | On Ideal Lattices and Learning with Errors over Rings | Ring-LWE |
| [CRYSTALS-Kyber](https://eprint.iacr.org/2017/634) | CRYSTALS-Kyber | The Module-LWE KEM that became ML-KEM |
| [CRYSTALS-Dilithium](https://eprint.iacr.org/2017/633) | CRYSTALS-Dilithium | The Module-lattice signature that became ML-DSA |
| [Shor&nbsp;1997](https://arxiv.org/abs/quant-ph/9508027) | Polynomial-Time Algorithms for Factoring ... | Why RSA/ECC fall, and why the abelian HSP is the key |
| [Becker–Ducas–Gama–Laarhoven](https://eprint.iacr.org/2015/1128) | New directions in nearest neighbor searching ... | The `2^0.292·b` classical sieving exponent |
