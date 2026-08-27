#!/usr/bin/env python3
"""Exercise 1 - From vectors to lattices: good vs bad bases, and LLL.

EDUCATIONAL TOY CODE - NOT FOR PRODUCTION USE.

A lattice is the set of all integer combinations of some basis vectors. The
*same* lattice has infinitely many bases: some "good" (short, nearly
orthogonal vectors) and some "bad" (long, nearly parallel vectors). Hard
lattice problems like finding the shortest vector (SVP) are easy given a good
basis and hard given a bad one - and a public key hands you only a bad basis.

This script shows that contrast concretely and then uses LLL lattice reduction
to turn a bad basis back into a good one in low dimension.
"""

import numpy as np
from fpylll import IntegerMatrix, LLL


def hadamard_ratio(basis):
    """Orthogonality measure in [0, 1]: 1.0 = perfectly orthogonal basis,
    near 0 = very skewed ("bad"). It is the lattice volume divided by the
    product of the row norms."""
    B = np.array(basis, dtype=float)
    vol = abs(np.linalg.det(B))
    norms = np.prod([np.linalg.norm(row) for row in B])
    return (vol / norms) ** (1.0 / B.shape[0])


def shortest_row_norm(basis):
    return min(float(np.linalg.norm(np.array(row, dtype=float))) for row in basis)


print("=" * 70)
print("PART A - One lattice, two bases")
print("=" * 70)

# A "good" basis: two short, near-orthogonal vectors.
good = [[1, 0], [0, 1]]

# A "bad" basis for the SAME lattice. These rows are a unimodular (det = +/-1)
# combination of the good basis, so they generate EXACTLY the same set of
# lattice points - but they are long and nearly parallel.
bad = [[15, 8], [13, 7]]

print("good basis :", good)
print("bad  basis :", bad)
print(f"det(good)  = {round(np.linalg.det(np.array(good))):d}")
print(f"det(bad)   = {round(np.linalg.det(np.array(bad))):d}   "
      "(equal magnitude -> same lattice)")
print(f"Hadamard ratio  good = {hadamard_ratio(good):.4f}   "
      f"bad = {hadamard_ratio(bad):.4f}   (closer to 1 is better)")
print(f"Shortest basis vector  good = {shortest_row_norm(good):.2f}   "
      f"bad = {shortest_row_norm(bad):.2f}")

print()
print("=" * 70)
print("PART B - LLL turns the bad basis back into a good one")
print("=" * 70)

M = IntegerMatrix.from_matrix(bad)
LLL.reduction(M)
reduced = [[M[i, j] for j in range(M.ncols)] for i in range(M.nrows)]
print("LLL-reduced basis :", reduced)
print(f"Hadamard ratio after LLL = {hadamard_ratio(reduced):.4f}")
print(f"Shortest vector after LLL = {shortest_row_norm(reduced):.2f}")
print("-> LLL recovered short, near-orthogonal vectors from the bad basis.")

print()
print("=" * 70)
print("PART C - SVP gets hard fast as the dimension grows")
print("=" * 70)
print("LLL is efficient (polynomial time), but it only finds an APPROXIMATE")
print("shortest vector. Its WORST-CASE approximation factor is 2^((d-1)/2),")
print("which it can guarantee only up to a vector that long - and that bound")
print("grows EXPONENTIALLY with the dimension d:\n")

for dim in (2, 50, 256, 768):
    log2_factor = (dim - 1) / 2.0
    print(f"   dimension d = {dim:>4}: LLL may be off by up to 2^{log2_factor:.1f}")

print("\nIn 2-D that is nothing; at d = 768 (the secret dimension of ML-KEM-768)")
print("LLL's guarantee is astronomically loose. Closing that gap to actually")
print("find the short vector means running BKZ with a large block size - and")
print("BKZ's cost is itself exponential in the block size. That is the wall")
print("that protects ML-KEM and ML-DSA.")
print("Exercise 4 (attack_scaling.py) turns that cost into wall-clock seconds.")
