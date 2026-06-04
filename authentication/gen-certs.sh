#!/bin/sh
# Generate a lab CA plus two leaf certificates (initiator, responder) for IKEv2
# mutual public-key authentication, and install them into each peer's swanctl
# credential directories.
#
# Run via the certgen helper service (which mounts both peers' config dirs),
# from the authentication/ directory:
#   docker compose run --rm certgen ecdsa                                   # Stage 1
#   docker compose -f docker-compose.yml -f docker-compose.mldsa.yml \
#       run --rm certgen ml-dsa-44                                          # Stage 2
#
# LAB ONLY: keys are generated unencrypted for convenience. Never reuse them or
# commit them; .gitignore excludes the generated material.
set -eu

TYPE="${1:-ecdsa}"

# Map the friendly type name to the token pki --gen expects (ML-DSA variants are
# written without hyphens, e.g. mldsa65). For --self/--pub we use the generic
# "priv" token, which auto-detects the key type from the file (pki --self does
# not accept an explicit mldsa token).
case "$TYPE" in
  ecdsa)
    GEN_OPTS="--type ecdsa --size 256"
    ;;
  ml-dsa-44)
    GEN_OPTS="--type mldsa44"
    ;;
  ml-dsa-65)
    GEN_OPTS="--type mldsa65"
    ;;
  ml-dsa-87)
    GEN_OPTS="--type mldsa87"
    ;;
  *)
    echo "Unsupported type '$TYPE' (use: ecdsa | ml-dsa-44 | ml-dsa-65 | ml-dsa-87)" >&2
    exit 1
    ;;
esac

# pki auto-detects the key type from the file when given "priv".
KEY_TYPE="priv"

WORK="$(mktemp -d)"
cd "$WORK"

echo ">> Generating CA key + self-signed CA cert ($TYPE) ..."
pki --gen $GEN_OPTS --outform pem > ca.key
pki --self --ca --lifetime 3650 --in ca.key --type "$KEY_TYPE" \
    --dn "C=US, O=PQC Lab, CN=PQC Lab CA" --outform pem > ca.crt

for peer in initiator responder; do
  echo ">> Issuing leaf certificate for $peer ($TYPE) ..."
  pki --gen $GEN_OPTS --outform pem > "$peer.key"
  pki --pub --in "$peer.key" --type "$KEY_TYPE" --outform pem > "$peer.pub"
  # Identity is carried in the SAN (FQDN), which swanctl matches against the IKE ID.
  pki --issue --lifetime 365 --cacert ca.crt --cakey ca.key \
      --in "$peer.pub" --type pub \
      --dn "C=US, O=PQC Lab, CN=$peer.pqc.lab" --san "$peer.pqc.lab" \
      --flag serverAuth --flag clientAuth --outform pem > "$peer.crt"

  dest="/work/$peer"
  mkdir -p "$dest/private" "$dest/x509" "$dest/x509ca"
  cp "$peer.key" "$dest/private/$peer.key"
  cp "$peer.crt" "$dest/x509/$peer.crt"
  cp ca.crt      "$dest/x509ca/ca.crt"
done

echo ">> Done. Credentials installed into config/{initiator,responder}/{private,x509,x509ca}."
echo "   Reload each peer with: swanctl --load-all"
