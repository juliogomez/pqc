#!/bin/bash
set -e

# Ensure the vici socket directory exists
mkdir -p /run/strongswan

# Pre-create the credential subdirectories swanctl scans on --load-all.
# The config dir is bind-mounted, so these can't be baked into the image;
# creating them here silences the noisy "opening directory ... failed" warnings.
for d in x509 x509ca x509ocsp x509aa x509ac x509crl pubkey private rsa ecdsa pkcs8 pkcs12; do
    mkdir -p "/usr/local/etc/swanctl/$d"
done

# Start the charon IKE daemon in the background
/usr/local/libexec/ipsec/charon &
CHARON_PID=$!

# Forward termination signals to charon for a clean shutdown on `docker compose down`
shutdown() {
    kill -TERM "$CHARON_PID" 2>/dev/null || true
    wait "$CHARON_PID" 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

# Wait for the VICI socket to appear (up to 30 s)
for i in $(seq 1 30); do
    if swanctl --stats >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Load connections and secrets (non-fatal if config is missing)
swanctl --load-all 2>&1 || true

# Keep the container alive by waiting for charon
wait $CHARON_PID
