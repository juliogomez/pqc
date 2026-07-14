#!/bin/sh
# Generate an SSH host key (for the server) and a lab user key (for pubkey auth),
# then install them into each role's config directory.
#
# Run via the keygen helper service (which mounts both roles' config dirs),
# from the ssh/ directory:
#   docker compose run --rm keygen ed25519           # classical baseline
#   docker compose run --rm keygen mldsa44-ed25519   # post-quantum signatures
#
# The key exchange itself (hybrid ML-KEM) is independent of the key algorithm
# and is negotiated by the SSH transport either way; only the authentication
# half changes when you switch key types. The composite "mldsa44-ed25519" type
# comes straight from OpenSSH 10.4 (no separate library), so the same image
# handles both types and there is nothing to rebuild.
#
# LAB ONLY: keys are generated unencrypted for convenience. Never reuse them or
# commit them; .gitignore excludes the generated material.
set -eu

TYPE="${1:-ed25519}"

case "$TYPE" in
  ed25519|mldsa44-ed25519) : ;;
  *)
    echo "Unsupported type '$TYPE' (use: ed25519 | mldsa44-ed25519)" >&2
    exit 1
    ;;
esac

WORK="$(mktemp -d)"
cd "$WORK"

echo ">> Generating server host key ($TYPE) ..."
ssh-keygen -q -t "$TYPE" -f ssh_host_key -N '' -C "ssh-server host key ($TYPE)"

echo ">> Generating labuser authentication key ($TYPE) ..."
ssh-keygen -q -t "$TYPE" -f id_key -N '' -C "labuser ($TYPE)"

mkdir -p /work/server /work/client

# Server: its host key + the user's public key as the sole authorized_keys entry.
cp ssh_host_key ssh_host_key.pub /work/server/
cp id_key.pub /work/server/authorized_keys

# Client: the user's private key + a known_hosts entry pinning the server's
# host key, so host-key verification actually exercises the (composite) key
# rather than trust-on-first-use. The hostname "ssh-server" is the compose
# service name the client connects to.
cp id_key id_key.pub /work/client/
echo "ssh-server $(cut -d' ' -f1-2 ssh_host_key.pub)" > /work/client/known_hosts

echo ">> Done. Keys installed ($TYPE):"
echo "   Server:  ssh_host_key(.pub) + authorized_keys (labuser's pubkey)"
echo "   Client:  id_key(.pub) + known_hosts (pins ssh-server's host key)"
