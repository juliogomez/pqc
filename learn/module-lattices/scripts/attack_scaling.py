#!/usr/bin/env python3
"""Exercise 4 - Why a quantum computer does NOT rescue the attacker.

EDUCATIONAL TOY CODE - NOT FOR PRODUCTION USE.

The best known way to break LWE / Module-LWE - classically OR quantumly - is
lattice reduction: embed the LWE instance as a "unique shortest vector" (uSVP)
problem and run BKZ to dig that short vector out. This script runs that REAL
attack with fpylll on toy parameters and times it as the dimension grows.

The point is the SHAPE of the curve. The cost of lattice reduction is
exponential in the dimension. Shor's algorithm - which obliterates RSA and
elliptic curves - simply does not apply here (see the notes printed at the
end), and the only quantum help, faster sieving inside BKZ, shaves the
exponent's constant a little. A little off an exponential is still exponential.
Watch the seconds climb here, then imagine n = 256 * k.
"""

import argparse
import time
import numpy as np
from fpylll import IntegerMatrix, LLL, BKZ

Q = 97                        # small modulus -> a tight gap, so the attack
                              # actually has to work (LLL alone is not enough)
rng = np.random.default_rng(11)


def center(v, q=Q):
    v = np.array(v) % q
    return np.where(v > q // 2, v - q, v)


def make_lwe(n, q=Q):
    """A ternary-secret LWE instance with m = n samples: b = A*s + e (mod q)."""
    A = rng.integers(0, q, size=(n, n))
    s = rng.integers(-1, 2, size=n)            # secret in {-1, 0, 1}
    e = rng.integers(-1, 2, size=n)            # error  in {-1, 0, 1}
    b = (A @ s + e) % q
    return A, s, e, b


def kannan_embedding(A, b, q=Q, c=1):
    """Build the primal uSVP basis whose shortest vector is (e, -s, c).

    Rows (vectors live in Z^(m+n+1)):
        [ q*I_m | 0     | 0 ]   (m rows: the mod-q lattice)
        [ A^T   | I_n   | 0 ]   (n rows: ties the s-part to A)
        [ b     | 0     | c ]   (1 row : the LWE target b)
    A short integer combo of these equals (b - A*s mod q, -s, c) = (e, -s, c)."""
    m, n = A.shape
    d = m + n + 1
    B = [[0] * d for _ in range(d)]
    for i in range(m):
        B[i][i] = q
    for j in range(n):
        for i in range(m):
            B[m + j][i] = int(A[i, j])
        B[m + j][m + j] = 1
    for i in range(m):
        B[m + n][i] = int(b[i])
    B[m + n][d - 1] = c
    return B, m, n


def try_recover(M, m, n, s_true, c=1):
    """Scan the reduced basis for a row that decodes to the secret s."""
    for i in range(M.nrows):
        row = [M[i, j] for j in range(M.ncols)]
        if abs(row[-1]) != c:
            continue
        sign = 1 if row[-1] == c else -1
        s_cand = center([sign * (-row[m + j]) for j in range(n)])
        if np.array_equal(s_cand, center(s_true)):
            return True
    return False


def attack(n, max_block=40):
    A, s, e, b = make_lwe(n)
    B, m, nn = kannan_embedding(A, b)
    M = IntegerMatrix.from_matrix(B)
    t0 = time.time()
    LLL.reduction(M)
    if try_recover(M, m, nn, s):
        return True, "LLL", time.time() - t0
    for bs in range(20, max_block + 1, 5):       # progressive BKZ
        BKZ.reduction(M, BKZ.Param(block_size=bs))
        if try_recover(M, m, nn, s):
            return True, f"BKZ-{bs}", time.time() - t0
    return False, f"BKZ-{max_block} (cap)", time.time() - t0


def parse_args():
    p = argparse.ArgumentParser(
        description="Run the real lattice attack on toy LWE and time it as the "
                    "dimension grows. Rerun with larger dimensions to watch the "
                    "cost explode exponentially.")
    p.add_argument(
        "dims", nargs="*", type=int, default=[20, 30, 40, 50, 60],
        metavar="N",
        help="LWE dimensions n to attack (default: 20 30 40 50 60). "
             "Try e.g. '60 70 80' on a second run and compare the times.")
    p.add_argument(
        "--max-block", type=int, default=40, metavar="B",
        help="Highest BKZ block size to escalate to (default: 40). Bigger "
             "dimensions may need a higher cap to still solve, at steep cost.")
    return p.parse_args()


args = parse_args()

print("=" * 72)
print("RUNNING THE REAL LATTICE ATTACK ON TOY LWE (ternary secret, q=97)")
print("=" * 72)
print(f"{'LWE dim n':>9} | {'lattice dim':>11} | {'solved?':>8} | "
      f"{'effort':>12} | {'time (s)':>9}")
print("-" * 72)

for n in args.dims:
    solved, effort, secs = attack(n, max_block=args.max_block)
    print(f"{n:>9} | {2 * n + 1:>11} | {str(solved):>8} | "
          f"{effort:>12} | {secs:>9.2f}")

print("-" * 72)
print("(Exact times vary by machine, but the SHAPE is the lesson.) The small")
print("instances fall to cheap, polynomial-time LLL instantly. Past a threshold")
print("LLL stalls and BKZ has to take over - and from there the time climbs")
print("steeply, because BKZ cost is exponential in its block size. The toy stops")
print("at n=60; the curve does not.\n")

print("=" * 72)
print("EXTRAPOLATING TO REAL PARAMETERS")
print("=" * 72)
print("ML-KEM-768 :  n=256, k=3  -> secret dimension 256*3 = 768")
print("ML-DSA-65  :  n=256, k/l around 5-6 -> comparable dimensions")
print("The attack lattice would have dimension ~1500+. Estimated BKZ block size")
print("to crack it is in the HUNDREDS, and the cost of one such reduction exceeds")
print("2^150 operations - more than the ~2^128 to ~2^256 security NIST targets.\n")

print("=" * 72)
print("WHY A CRYPTOGRAPHICALLY-RELEVANT QUANTUM COMPUTER (CRQC) DOES NOT HELP")
print("=" * 72)
print("""
1. SHOR DOES NOT APPLY.
   Shor's algorithm breaks RSA and elliptic curves because factoring and
   discrete log are instances of the *abelian* hidden subgroup problem
   (HSP) - secretly periodic functions over a commutative group, which a
   quantum Fourier transform cracks efficiently. Lattice problems (SVP/CVP,
   LWE, SIS) have no such periodic structure. The closest reduction is to
   the *dihedral* HSP, for which - after 20+ years - no efficient quantum
   algorithm is known. So the polynomial-time quantum break that kills
   today's crypto has no foothold here.

2. GROVER ONLY HALVES THE EXPONENT.
   Grover's search gives at most a quadratic speedup on brute force
   (2^k -> 2^(k/2)). NIST already accounts for it: it is why the symmetric
   security target for, say, ML-KEM-768 is set so that even a Grover-armed
   adversary stays above the line. Quadratic is not exponential.

3. QUANTUM SIEVING SHAVES A CONSTANT, NOT THE CURVE.
   Inside BKZ, the priciest step is solving SVP in a block via sieving.
   The best classical sieve costs ~2^(0.292*b); the best known quantum
   sieve ~2^(0.265*b) (some estimates ~2^(0.257*b)). That is a smaller
   exponent - but STILL exponential in the block size b, and b grows with
   the lattice dimension. A little discount on an exploding cost leaves it
   exploding. NIST's parameters carry margin for exactly this.

CONCLUSION: every known quantum tool either does not apply (Shor) or only
dents the exponent (Grover, quantum sieving). The exponential wall you just
watched rise in the table above is what protects ML-KEM and ML-DSA from a CRQC.
""")
