#!/usr/bin/env bash
#
# Build the ML-DSA PKI for the IOS XE 26.2 IKEv2 authentication exercises.
#
# IOS XE 26.2 CAN generate ML-DSA keys on the box ("crypto key generate mldsa
# param {44|65|87}", exec mode), but it cannot get one certified over SCEP, and
# "crypto pki server" has no key-type option so a local IOS CA is always
# RSA-keyed. This script takes the off-box road instead: every key, certificate
# and PKCS#12 bundle is made here with OpenSSL and imported to the router, which
# is the path the ML-DSA exercises in ../ipsec.md walk through.
#
# The trade-off, stated plainly: these bundles carry private keys that were born
# on your workstation. On-box key generation keeps the private key inside the
# router. See ../automation/DESIGN.md for that route and what it costs.
#
# Produces, for each of ML-DSA-44 / 65 / 87:
#   - a self-signed root CA
#   - an identity certificate for R1, R2 and R3
#   - a PKCS#12 bundle per router (identity key + cert + root)
# plus the same set on classical RSA-2048 and ECDSA P-256. RSA-2048 is the
# "not yet upgraded" peer in the migration exercise and the baseline in the size
# table, because `ecdsa-sig` does not complete IKE_AUTH on 26.2 (see the
# "Things that will bite you" section of ../ipsec.md). ECDSA is generated anyway
# so you can retest it on a later build.
#
# Usage:  ./gen-mldsa-certs.sh
#         OPENSSL=/opt/homebrew/bin/openssl ./gen-mldsa-certs.sh
#         OUT=/tmp/pki PASS=<bundle-password> ./gen-mldsa-certs.sh

set -euo pipefail

OPENSSL="${OPENSSL:-openssl}"
OUT="${OUT:-$(cd "$(dirname "$0")" && pwd)/mldsa-pki}"

# Throwaway password protecting the PKCS#12 bundles in transit to the router.
# It is not a secret: the bundle lives on bootflash for a few seconds and is
# deleted straight after import (see ../ipsec.md). Override it with PASS= if
# you would rather not have it in your shell history.
PASS="${PASS:-cisco123}"

DAYS_CA=3650
DAYS_LEAF=825

# Certificate subject and SAN addresses must match the IKEv2 `identity local
# address` on each router. Without the matching IP SAN, IOS XE still brings the
# tunnel up but logs IKMP_NO_ID_CERT_ADDR_MATCH on every negotiation.
router_san() {
  case "$1" in
    r1) echo "IP:10.0.12.1" ;;
    r2) echo "IP:10.0.12.2,IP:10.0.23.1" ;;   # hub, terminates both tunnels
    r3) echo "IP:10.0.23.2" ;;
    *)  echo "unknown router: $1" >&2; exit 1 ;;
  esac
}

say() { printf '\n=== %s\n' "$*"; }

# ML-DSA is a self-contained signature scheme, so the digest argument is inert
# and SHA-512 matches the `hash sha512` the router puts in the trustpoint.
# P-256 is a different story: IOS XE fails the IKEv2 auth exchange outright on
# an ecdsa-with-SHA512 certificate, so the baseline has to be SHA-256.
digest_for() {
  case "$1" in
    ec-p256)  echo "-sha256" ;;
    rsa-2048) echo "-sha256" ;;
    *)        echo "-sha512" ;;
  esac
}

check_openssl() {
  local ver
  ver="$("$OPENSSL" version)"
  say "$ver"
  if ! "$OPENSSL" list -signature-algorithms 2>/dev/null | grep -qi mldsa87; then
    cat >&2 <<EOF
ERROR: this OpenSSL does not expose ML-DSA.

ML-DSA landed natively in OpenSSL 3.5. Point the script at a newer build:
  OPENSSL=/opt/homebrew/bin/openssl $0
EOF
    exit 1
  fi
}

# gen_key <algorithm> <output.key>
#   algorithm is an OpenSSL name: mldsa44 / mldsa65 / mldsa87 / ec-p256 / rsa-2048
gen_key() {
  local alg="$1" out="$2"
  case "$alg" in
    ec-p256)
      "$OPENSSL" genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$out" ;;
    rsa-2048)
      "$OPENSSL" genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$out" ;;
    *)
      "$OPENSSL" genpkey -algorithm "$alg" -out "$out" ;;
  esac
}

# gen_root <algorithm> <label>
gen_root() {
  local alg="$1" label="$2"
  local key="$OUT/${label}-root.key" crt="$OUT/${label}-root.crt"

  gen_key "$alg" "$key"
  "$OPENSSL" req -new -x509 -key "$key" -out "$crt" \
    -days "$DAYS_CA" "$(digest_for "$alg")" \
    -subj "/C=ES/O=PQC-Lab/CN=PQC-LAB-ROOT-${label}" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash"
}

# gen_leaf <algorithm> <label> <router>
gen_leaf() {
  local alg="$1" label="$2" rtr="$3"
  local base="$OUT/${label}-${rtr}"
  local san; san="$(router_san "$rtr")"

  gen_key "$alg" "$base.key"
  "$OPENSSL" req -new -key "$base.key" -out "$base.csr" \
    -subj "/C=ES/O=PQC-Lab/CN=$(echo "$rtr" | tr '[:lower:]' '[:upper:]')-${label}"

  # IKEv2 peers act as both initiator and responder, so the identity cert needs
  # clientAuth and serverAuth. A clientAuth-only leaf is rejected by the
  # responder half of the exchange.
  "$OPENSSL" x509 -req -in "$base.csr" -out "$base.crt" \
    -CA "$OUT/${label}-root.crt" -CAkey "$OUT/${label}-root.key" \
    -CAcreateserial -days "$DAYS_LEAF" "$(digest_for "$alg")" \
    -extfile <(printf '%s\n' \
      "basicConstraints=critical,CA:FALSE" \
      "keyUsage=critical,digitalSignature" \
      "extendedKeyUsage=clientAuth,serverAuth" \
      "subjectKeyIdentifier=hash" \
      "authorityKeyIdentifier=keyid,issuer" \
      "subjectAltName=${san}")

  rm -f "$base.csr"

  "$OPENSSL" pkcs12 -export \
    -inkey "$base.key" -in "$base.crt" -certfile "$OUT/${label}-root.crt" \
    -name "${label}-${rtr}" -out "$base.p12" -passout "pass:$PASS"
}

# gen_family <algorithm> <label>
gen_family() {
  local alg="$1" label="$2"
  say "Generating $label"
  gen_root "$alg" "$label"
  for rtr in r1 r2 r3; do
    gen_leaf "$alg" "$label" "$rtr"
    printf '  %-14s %s\n' "$label-$rtr" "$(router_san "$rtr")"
  done
}

size_table() {
  say "Artifact sizes in bytes (R1 identity, DER-encoded cert)"
  printf '%-12s %12s %12s %12s %12s\n' \
    ALGORITHM "PUBKEY" "PRIVKEY" "CERT" "P12"
  for label in rsa-2048 ecdsa-p256 mldsa44 mldsa65 mldsa87; do
    local base="$OUT/${label}-r1"
    local pub priv crt p12
    pub=$("$OPENSSL" pkey -in "$base.key" -pubout -outform DER 2>/dev/null | wc -c)
    priv=$("$OPENSSL" pkey -in "$base.key" -outform DER 2>/dev/null | wc -c)
    crt=$("$OPENSSL" x509 -in "$base.crt" -outform DER 2>/dev/null | wc -c)
    p12=$(wc -c < "$base.p12")
    printf '%-12s %12s %12s %12s %12s\n' \
      "$label" "$pub" "$priv" "$crt" "$p12"
  done
}

main() {
  check_openssl
  rm -rf "$OUT"
  mkdir -p "$OUT"
  chmod 700 "$OUT"

  gen_family rsa-2048 rsa-2048
  gen_family ec-p256 ecdsa-p256
  gen_family mldsa44 mldsa44
  gen_family mldsa65 mldsa65
  gen_family mldsa87 mldsa87

  chmod 600 "$OUT"/*.key "$OUT"/*.p12
  size_table

  say "Done. Bundles are in $OUT"
  cat <<EOF

Copy a bundle to a router and import it, for example:

  scp -O $OUT/mldsa65-r1.p12 admin@<R1-mgmt-ip>:bootflash:/mldsa65-r1.p12
  R1# crypto pki import TP-MLDSA65 pkcs12 bootflash:/mldsa65-r1.p12 password $PASS
  R1# delete /force bootflash:/mldsa65-r1.p12

The .key and .p12 files hold unencrypted private keys. They are lab material,
they are gitignored, and they should never leave this directory.
EOF
}

main "$@"
