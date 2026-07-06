#!/bin/sh
# Generate a lab CA plus an EAP-TLS server certificate (for the authenticator's
# integrated EAP server) and a supplicant client certificate, and install them
# into each role's config directory.
#
# Run via the certgen helper service (which mounts both roles' config dirs),
# from the macsec/ directory:
#   docker compose run --rm certgen ecdsa          # classical baseline
#   docker compose run --rm certgen ml-dsa-44      # post-quantum signatures
#   docker compose run --rm certgen ml-dsa-65      # larger PQC parameter set
#
# The key exchange itself (hybrid ML-KEM) is independent of the certificate
# algorithm and is negotiated by TLS 1.3 either way; only the authentication
# half changes when you switch cert types. ML-DSA support comes from OpenSSL 3.5
# directly (the same image handles every type), so there is no image to rebuild.
#
# LAB ONLY: keys are generated unencrypted for convenience. Never reuse them or
# commit them; .gitignore excludes the generated material.
set -eu

TYPE="${1:-ecdsa}"

case "$TYPE" in
  ecdsa)     KEYGEN="openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256" ;;
  ml-dsa-44) KEYGEN="openssl genpkey -algorithm ML-DSA-44" ;;
  ml-dsa-65) KEYGEN="openssl genpkey -algorithm ML-DSA-65" ;;
  ml-dsa-87) KEYGEN="openssl genpkey -algorithm ML-DSA-87" ;;
  *)
    echo "Unsupported type '$TYPE' (use: ecdsa | ml-dsa-44 | ml-dsa-65 | ml-dsa-87)" >&2
    exit 1
    ;;
esac

WORK="$(mktemp -d)"
cd "$WORK"

echo ">> Generating CA key + self-signed CA cert ($TYPE) ..."
$KEYGEN -out ca.key 2>/dev/null
openssl req -x509 -new -key ca.key -out ca.crt -days 3650 \
    -subj "/C=US/O=PQC Lab/CN=PQC Lab CA" 2>/dev/null

# issue <name> <CN> <eku>: mint a leaf cert signed by the lab CA.
issue() {
  name="$1"; cn="$2"; eku="$3"
  echo ">> Issuing $name certificate (CN=$cn, $eku, $TYPE) ..."
  $KEYGEN -out "$name.key" 2>/dev/null
  openssl req -new -key "$name.key" -out "$name.csr" -subj "/C=US/O=PQC Lab/CN=$cn" 2>/dev/null
  printf 'extendedKeyUsage=%s\nsubjectAltName=DNS:%s\n' "$eku" "$cn" > "$name.ext"
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
      -out "$name.crt" -days 365 -extfile "$name.ext" 2>/dev/null
}

issue server server.pqc.lab serverAuth
issue client client.pqc.lab clientAuth

mkdir -p /work/authenticator /work/supplicant
cp ca.crt server.crt server.key /work/authenticator/
cp ca.crt client.crt client.key /work/supplicant/

echo ">> Done. Certs installed into config/{authenticator,supplicant}/ ($TYPE)."
echo "   Authenticator: ca.crt + server.crt/server.key (EAP server)"
echo "   Supplicant:    ca.crt + client.crt/client.key (EAP peer)"
