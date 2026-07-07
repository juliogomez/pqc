#!/bin/sh
# Generate a lab CA plus a server certificate (serverAuth) and a client
# certificate (clientAuth) for mutual TLS, and install them into each role's
# config directory.
#
# Run via the certgen helper service (which mounts both roles' config dirs),
# from the tls/authentication/ directory:
#   docker compose run --rm certgen ecdsa          # classical certificates
#   docker compose run --rm certgen ml-dsa-65      # post-quantum certificates
#
# The certificate algorithm is the whole point of this lab: ecdsa is today's
# classical baseline, the ml-dsa-* types make the certificates post-quantum.
# (The key exchange is independent of this and is covered by the companion
# tls/key-exchange lab.)
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

# Show which OpenSSL this image has. ML-DSA needs OpenSSL 3.5+; an older build
# (for example 3.0) does not know the algorithm and the commands below fail. We
# leave the OpenSSL errors visible on purpose, so a failure is never silent.
echo ">> Using $(openssl version)"

echo ">> Generating CA key + self-signed CA cert ($TYPE) ..."
$KEYGEN -out ca.key
openssl req -x509 -new -key ca.key -out ca.crt -days 3650 \
    -subj "/C=US/O=PQC Lab/CN=PQC Lab CA"

# issue <name> <CN> <eku>: mint a leaf cert signed by the lab CA.
issue() {
  name="$1"; cn="$2"; eku="$3"
  echo ">> Issuing $name certificate (CN=$cn, $eku, $TYPE) ..."
  $KEYGEN -out "$name.key"
  openssl req -new -key "$name.key" -out "$name.csr" -subj "/C=US/O=PQC Lab/CN=$cn"
  printf 'extendedKeyUsage=%s\nsubjectAltName=DNS:%s\n' "$eku" "$cn" > "$name.ext"
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
      -out "$name.crt" -days 365 -extfile "$name.ext"
}

issue server server.pqc.lab serverAuth
issue client client.pqc.lab clientAuth

mkdir -p /work/server /work/client
cp ca.crt server.crt server.key /work/server/
cp ca.crt client.crt client.key /work/client/

echo ">> Done. Certs installed into config/{server,client}/."
echo "   Server: ca.crt + server.crt/server.key (proves the server's identity)"
echo "   Client: ca.crt + client.crt/client.key (proves the client's identity)"
