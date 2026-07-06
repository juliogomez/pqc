#!/bin/sh
# Entrypoint for the TLS lab containers (tls-key-exchange and tls-authentication).
#
# There is no daemon to start here. The TLS handshake is run by the user via
# `docker exec` (so you can watch it live, like `swanctl --initiate` in the
# IKEv2 labs or `wpa_supplicant` in the MACsec labs). This entrypoint just keeps
# the container alive for interactive shells, or runs an explicit command if one
# is given (used by the certgen helper).
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec sleep infinity
