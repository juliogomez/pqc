#!/bin/sh
# Entrypoint for the post-quantum SSH lab containers.
#
# Two roles on a plain Docker bridge (no veth tricks needed, this is ordinary
# TCP), exactly like the TLS lab's server/client:
#   ssh-server  - runs OpenSSH sshd(8)  (started by the reader via `docker exec`)
#   ssh-client  - runs OpenSSH ssh(1)   (the connecting endpoint)
#
# Like the MACsec and IKEv2 labs, the handshake is left for the reader to drive
# by hand (start sshd in one shell, `ssh` from another, capture in between), so
# this entrypoint only prepares the privsep directory and then idles. That also
# lets the exercises stop/edit/restart sshd freely (needed for the downgrade and
# the composite-key config toggles).
set -e

# sshd(8) requires its privilege-separation directory to exist and be owned by
# root with no group/world write bits.
mkdir -p /var/empty
chmod 755 /var/empty

# Run an explicit command if given; otherwise idle so the container stays up for
# interactive `docker exec` sessions.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec sleep infinity
