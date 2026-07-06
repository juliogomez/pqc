#!/usr/bin/env python3
"""Exercise 2 - Learning With Errors (LWE): why a little noise changes everything.

EDUCATIONAL TOY CODE - NOT FOR PRODUCTION USE.

LWE is the hard problem under ML-KEM (and, with a sibling problem, ML-DSA).
The setup is deceptively simple. Pick a secret vector s and publish a random
matrix A together with:

        b = A * s + e   (mod q)

where e is a small "error" / "noise" vector. Recovering s from (A, b) is:

  * TRIVIAL with no noise   -> it is just solving a linear system mod q.
  * HARD with noise         -> the best known attacks are lattice reduction,
                               which blows up with the dimension n.

That single addition of noise is the whole game. This script makes it concrete:
first we break noise-free LWE with high-school linear algebra, then we watch the
exact same approach fail the moment noise is present, and finally we build a
tiny Regev-style bit-encryption to see the "noise budget" that ML-KEM lives in.
"""

import numpy as np

Q = 3329          # ML-KEM's prime modulus (so modular inverses exist)
N = 8             # toy secret dimension (ML-KEM uses 256 per ring element)


def center(x, q=Q):
    """Map a residue mod q to the symmetric range (-q/2, q/2]."""
    x = x % q
    return int(x - q if x > q // 2 else x)


def solve_mod_q(A, b, q=Q):
    """Solve A x = b (mod q) by Gaussian elimination over the field Z_q.
    Requires q prime (3329 is). Returns the unique solution x for a full-rank A."""
    A = (np.array(A, dtype=np.int64) % q).copy()
    b = (np.array(b, dtype=np.int64) % q).copy()
    n = len(b)
    for col in range(n):
        piv = next(r for r in range(col, n) if A[r, col] % q != 0)
        A[[col, piv]] = A[[piv, col]]
        b[col], b[piv] = b[piv], b[col]
        inv = pow(int(A[col, col]), q - 2, q)          # Fermat inverse
        A[col] = (A[col] * inv) % q
        b[col] = (b[col] * inv) % q
        for r in range(n):
            if r != col and A[r, col] % q != 0:
                f = A[r, col]
                A[r] = (A[r] - f * A[col]) % q
                b[r] = (b[r] - f * b[col]) % q
    return b % q


rng = np.random.default_rng(7)
A = rng.integers(0, Q, size=(N, N))
s = rng.integers(0, Q, size=N)

print("=" * 70)
print("PART A - No noise: LWE collapses into plain linear algebra")
print("=" * 70)
b_clean = (A @ s) % Q
recovered = solve_mod_q(A, b_clean)
print("secret s          :", list(s))
print("recovered (no e)  :", list(recovered))
print("match             :", bool(np.array_equal(recovered, s % Q)))
print("-> With e = 0, anyone who sees (A, b) just solves for s. No security.")

print()
print("=" * 70)
print("PART B - Add small noise: the same linear solve falls apart")
print("=" * 70)
e = rng.integers(-2, 3, size=N)        # tiny error in {-2,..,2}
b_noisy = (A @ s + e) % Q
guess = solve_mod_q(A, b_noisy)
err_vec = [center(int(v)) for v in (guess - s)]
print("error e added     :", list(e))
print("recovered (with e):", list(guess))
print("secret s          :", list(s % Q))
print("difference        :", err_vec)
print("-> A handful of +/-2 nudges scrambles the solution completely. The")
print("   noise hides s, and no efficient classical OR quantum algorithm is")
print("   known to peel it back off at large n. THAT is the LWE hardness.")

print()
print("=" * 70)
print("PART C - A tiny Regev encryption and its noise budget")
print("=" * 70)
print("Public key is an LWE sample (A, b = A*s + e). To encrypt one bit we add")
print("a random subset of the rows and, for a 1-bit, an extra q/2 shift. The")
print("receiver subtracts s and rounds: as long as the accumulated noise stays")
print("below q/4, the bit decodes correctly.\n")


def keygen():
    A = rng.integers(0, Q, size=(N, N))
    s = rng.integers(0, Q, size=N)
    e = rng.integers(-1, 2, size=N)
    b = (A @ s + e) % Q
    return (A, b), s


def encrypt(pub, bit, noise_scale=1):
    A, b = pub
    r = rng.integers(0, 2, size=N)                 # random subset of samples
    extra = rng.integers(-noise_scale, noise_scale + 1)
    u = (r @ A) % Q
    v = (int(r @ b) + extra + bit * (Q // 2)) % Q
    return u, v


def decrypt(sk, ct):
    u, v = ct
    m = center(int(v - u @ sk))                    # remove the s-mask
    return 1 if abs(m) > Q // 4 else 0             # round to nearest of {0, q/2}


pub, sk = keygen()
print("Encrypting the bits [1, 0, 1, 1, 0] with a healthy noise budget:")
bits = [1, 0, 1, 1, 0]
out = [decrypt(sk, encrypt(pub, b)) for b in bits]
print(f"   sent     = {bits}")
print(f"   recovered= {out}   correct = {out == bits}")

print("\nNow crank the per-ciphertext noise way past the q/4 budget:")
overshoot = Q // 3                                 # bigger than q/4 -> unsafe
out_bad = [decrypt(sk, encrypt(pub, b, noise_scale=overshoot)) for b in bits]
print(f"   sent     = {bits}")
print(f"   recovered= {out_bad}   correct = {out_bad == bits}")
print("-> Too much noise and decryption flips bits. ML-KEM picks n, q, and the")
print("   noise distribution so this 'decryption failure' is astronomically")
print("   rare while keeping the secret buried. It is a careful balancing act.")
