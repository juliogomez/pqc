#!/bin/sh
# Entrypoint for the MACsec + EAP-TLS lab containers.
#
# The authenticator and supplicant share one network namespace (the supplicant
# container is started with network_mode: service:<authenticator>), and are
# joined by a veth pair, a virtual Ethernet "cable" between a switch port
# (the authenticator end, "aut") and an endpoint (the supplicant end, "sup").
# A direct veth link is used rather than a Docker bridge because 802.1X EAPOL
# frames go to the PAE group address (01:80:c2:00:00:03), which Linux bridges
# filter by default; a point-to-point veth delivers them unfiltered, so the lab
# runs on plain Docker with no host-side bridge tweaks.
#
# The handshake itself is left to the user to run via `docker exec` (so you can
# watch it live, like `swanctl --initiate` in the IKEv2 labs); this entrypoint
# only sets up the link and then idles.
set -e

ROLE="${MACSEC_ROLE:-authenticator}"
IF_AUT="${MACSEC_IF_AUT:-aut}"
IF_SUP="${MACSEC_IF_SUP:-sup}"

if [ "$ROLE" = "authenticator" ]; then
    if ! ip link show "$IF_AUT" >/dev/null 2>&1; then
        ip link add "$IF_AUT" type veth peer name "$IF_SUP"
    fi
    ip link set "$IF_AUT" up
    ip link set "$IF_SUP" up
fi

# Run an explicit command if given; otherwise idle so the container stays up for
# interactive `docker exec` sessions.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec sleep infinity
