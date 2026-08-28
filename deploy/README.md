# Stage 2: deploy on Cisco hardware

The [container labs](../learn/README.md) prove the protocols. This half proves the
Cisco *platforms*: the same ML-KEM and ML-DSA work, on Cisco gear you'd actually put in a
network.

Expect a different kind of writing here. The container labs are reproducible by design;
these documents are field notes. They record what shipped, what is roadmap, what the
platform can do, and which `show` command tells you the truth when two of them disagree.

## Platforms covered

### IOS XE on Cisco 8000 Secure Routers

Start with the [topology, feature status, and reading order](ios-xe/README.md). It has
the lab topology, the per-protocol lab links, and a summary table showing exactly which
PQ features work today and which are roadmap. Read that first to understand the scope,
then dive into whichever protocol you care about:
[IPsec](ios-xe/ipsec.md) · [SSH](ios-xe/ssh.md) · [MACsec](ios-xe/macsec.md) · [TLS](ios-xe/tls.md).

## Reading these docs without the hardware

You don't need the Cisco gear to get value out of this half. Every document includes the
real command output, and the sanitized running configs are checked in, so you can read the
outcome of each exercise and use the status tables to plan a migration.
