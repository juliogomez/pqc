#!/usr/bin/env python3
"""Exercise 3 - From LWE to MODULE-LWE: a baby Kyber (ML-KEM) in miniature.

EDUCATIONAL TOY CODE - NOT FOR PRODUCTION USE. Real ML-KEM uses n=256, the
number-theoretic transform, constant-time arithmetic, and careful hashing /
domain separation. This strips all that away to expose the skeleton.

ML-KEM does not work over plain integers - it works over the polynomial ring

        R_q = Z_q[x] / (x^n + 1)

and stacks those ring elements into short vectors and matrices. That is the
"module" in Module-Lattice: a middle ground between plain LWE (slow, huge keys)
and Ring-LWE (fast, but one big ring). Module-LWE gets Ring-LWE's speed while
tuning security simply by changing the module rank k - which is exactly how
ML-KEM-512/768/1024 differ (k = 2/3/4) while reusing the SAME n=256, q=3329 ring.

This script implements the ring arithmetic, then a Kyber-style CPA encryption,
then wraps it into a KEM and checks both peers derive the same shared secret.
"""

import hashlib
import numpy as np

N = 16            # ring degree  (ML-KEM: 256)
Q = 3329          # modulus      (ML-KEM: 3329, same across all sizes)
K = 2             # module rank  (ML-KEM-512/768/1024 use k = 2/3/4)
ETA = 2           # noise width for the centered binomial distribution

rng = np.random.default_rng(2025)


# ---------------------------------------------------------------------------
# Ring arithmetic in R_q = Z_q[x] / (x^n + 1)
# ---------------------------------------------------------------------------
def ring_mul(a, b):
    """Multiply two degree-<N polynomials, then reduce mod (x^n + 1).

    Because x^n = -1 in this ring, any coefficient that lands at degree n+i
    wraps back to degree i with a MINUS sign ("negacyclic" convolution)."""
    raw = np.convolve(a, b)                      # length 2N-1
    out = np.zeros(N, dtype=np.int64)
    for i, c in enumerate(raw):
        if i < N:
            out[i] += c
        else:
            out[i - N] -= c                      # x^n -> -1
    return out % Q


def ring_add(a, b):
    return (a + b) % Q


def cbd():
    """Sample one small ring element from a centered binomial distribution:
    coefficients cluster near 0, exactly the 'short' noise lattice crypto needs."""
    bits = rng.integers(0, 2, size=(N, 2, ETA))
    return (bits[:, 0, :].sum(axis=1) - bits[:, 1, :].sum(axis=1)) % Q


def center(v):
    v = v % Q
    return np.where(v > Q // 2, v - Q, v)


# ---------------------------------------------------------------------------
# CPA-secure public-key encryption (the core of ML-KEM)
# ---------------------------------------------------------------------------
def keygen():
    # Public matrix A: a k x k grid of random ring elements (would be derived
    # from a seed by SHAKE in real ML-KEM; here we just sample it).
    A = [[rng.integers(0, Q, size=N) for _ in range(K)] for _ in range(K)]
    s = [cbd() for _ in range(K)]                # short secret vector
    e = [cbd() for _ in range(K)]                # short error vector
    # t = A*s + e  -- this is the Module-LWE sample that hides s.
    t = []
    for i in range(K):
        acc = np.zeros(N, dtype=np.int64)
        for j in range(K):
            acc = ring_add(acc, ring_mul(A[i][j], s[j]))
        t.append(ring_add(acc, e[i]))
    return {"A": A, "t": t}, s


def encrypt(pk, msg_bits):
    """Encrypt N bits (a 0/1 array of length N) into a ciphertext (u, v)."""
    A, t = pk["A"], pk["t"]
    r = [cbd() for _ in range(K)]                # fresh short randomness
    e1 = [cbd() for _ in range(K)]
    e2 = cbd()
    # u = A^T * r + e1
    u = []
    for j in range(K):
        acc = np.zeros(N, dtype=np.int64)
        for i in range(K):
            acc = ring_add(acc, ring_mul(A[i][j], r[i]))
        u.append(ring_add(acc, e1[j]))
    # v = t^T * r + e2 + encode(msg)   (a 1-bit becomes a q/2 bump)
    acc = np.zeros(N, dtype=np.int64)
    for i in range(K):
        acc = ring_add(acc, ring_mul(t[i], r[i]))
    encoded = (np.array(msg_bits, dtype=np.int64) * (Q // 2)) % Q
    v = ring_add(ring_add(acc, e2), encoded)
    return u, v


def decrypt(sk, ct):
    u, v = ct
    acc = np.zeros(N, dtype=np.int64)            # s^T * u
    for i in range(K):
        acc = ring_add(acc, ring_mul(sk[i], u[i]))
    inner = center(ring_add(v, (-acc) % Q))      # v - s^T*u ~ encode(msg) + small noise
    bits = (np.abs(inner) > Q // 4).astype(int)
    return bits, inner


# ---------------------------------------------------------------------------
# KEM wrapper: encapsulate a random secret, derive a shared key by hashing.
# ---------------------------------------------------------------------------
def encapsulate(pk):
    msg = rng.integers(0, 2, size=N)
    ct = encrypt(pk, msg)
    shared = hashlib.sha3_256(bytes(msg.tolist())).hexdigest()
    return ct, shared


def decapsulate(sk, ct):
    msg, _ = decrypt(sk, ct)
    return hashlib.sha3_256(bytes(msg.tolist())).hexdigest()


print("=" * 70)
print(f"BABY KYBER  (n={N}, q={Q}, k={K}, eta={ETA})")
print("=" * 70)

pk, sk = keygen()
print(f"Public key 't' is a vector of {K} ring elements, each {N} coeffs mod {Q}.")
print("It is a Module-LWE sample A*s+e: the short secret s is buried under noise.\n")

# 1) Show the CPA encryption round-trips a message correctly.
msg = rng.integers(0, 2, size=N)
ct = encrypt(pk, msg)
recovered, inner = decrypt(sk, ct)
# True noise = how far the decrypted value sits from the ideal codeword
# (0 for a 0-bit, q/2 for a 1-bit). This is what must stay under q/4.
residual = center((inner - msg * (Q // 2)) % Q)
peak_noise = int(np.abs(residual).max())
print("PART A - encrypt / decrypt a message")
print("   message  :", list(msg))
print("   recovered:", list(recovered))
print(f"   match    : {bool(np.array_equal(msg, recovered))}")
print(f"   peak decryption noise = {peak_noise}  (must stay < q/4 = {Q // 4})")
print("   The secret s acts as a trapdoor: only s cancels the A*r masking term,")
print("   leaving message + small noise that rounds back to the original bits.\n")

# 2) Run it as a KEM: both sides must agree on the same shared secret.
print("PART B - key encapsulation (the actual ML-KEM job)")
ct, shared_enc = encapsulate(pk)
shared_dec = decapsulate(sk, ct)
print(f"   encapsulated shared secret : {shared_enc[:32]}...")
print(f"   decapsulated shared secret : {shared_dec[:32]}...")
print(f"   secrets agree              : {shared_enc == shared_dec}")
print("   -> Same shared key on both ends, derived without ever sending s or")
print("      the message in the clear. That is a key encapsulation mechanism.\n")

print("=" * 70)
print("HOW THIS MAPS TO REAL ML-KEM (FIPS 203)")
print("=" * 70)
print("   toy here        real ML-KEM-768")
print(f"   n  = {N:<10}  n  = 256        (ring degree, SAME for all sizes)")
print(f"   q  = {Q:<10}  q  = 3329       (SAME prime modulus)")
print(f"   k  = {K:<10}  k  = 3          (module rank: bumps security level)")
print("   The ONLY structural difference between ML-KEM-512/768/1024 is k=2/3/4.")
print("   Bigger k -> a taller Module-LWE instance -> a harder lattice -> more")
print("   security, with the ring math completely unchanged.")
