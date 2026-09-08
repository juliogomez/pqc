# Stage 2: deploy on Cisco hardware

The [container labs](../learn/README.md) prove the protocols. This half proves the
Cisco *platforms*: the same ML-KEM and ML-DSA work, on Cisco gear you'd actually put in a
network.

Expect a different kind of writing here. The container labs are reproducible by design;
these documents are field notes. They record what shipped, what it costs, what the
platform does not support yet, and what are the most relevant and useful router commands.

## Platforms covered

### IOS XE on Cisco 8000 Secure Routers

Start with the [topology, feature status, and reading order](ios-xe/README.md). It has
the lab topology, the per-protocol lab links, and a summary table showing exactly which
PQ features work on 26.2 and which don't. Read that first to understand the scope,
then dive into whichever protocol you care about:
[IPsec](ios-xe/ipsec.md) · [SSH](ios-xe/ssh.md) · [MACsec](ios-xe/macsec.md) · [TLS](ios-xe/tls.md).

Ansible playbooks for all four protocols live under
[`ios-xe/automation/`](ios-xe/automation/README.md) ([operator guide](ios-xe/automation/README.md),
[design notes](ios-xe/automation/DESIGN.md), and
[captured hardware runs](ios-xe/automation/captured/)).

## Ansible automation

Stage 2 is not just CLI walkthroughs. Each platform we cover gets an Ansible layer that
pushes the same post-quantum posture as structured data over the device's management API:
playbooks you apply, assert against, re-run for idempotency, and tear down in dependency
order.

Same layout for every platform:

- **Operator guide**: prerequisites, inventory, run order. Do the
  CLI labs first; the playbooks check the same router commands output you'd verify by hand.
- **Design notes**: what the model covers, what still needs
  exec-mode CLI, enrollment paths the API cannot express on its own.
- **Captured runs** from real hardware when you cannot run the playbooks yourself: apply,
  assert, re-run, teardown.

## Reading these docs without the hardware

You don't need the Cisco gear to get value out of this half. Every document includes the
real command output, the sanitized running configs are checked in, and where a platform has
automation, its captured playbook runs document that path the same way. Read the outcome of
each exercise and use the status tables to plan a migration.

## If you do have the hardware

Two things before you start typing, because routers don't have a `docker compose down`:

- **Snapshot the running config first.** Every exercise builds on the previous one, so
  nothing you configure gets undone as you go. One `copy running-config` up front turns the
  whole teardown into a single command later.
- **Plan the teardown.** [Putting the routers back](ios-xe/README.md#putting-the-routers-back)
  covers the fast rollback, the surgical removal in dependency order, the debug-only globals
  that are easy to leave enabled, and the private key material to delete from `bootflash:`
  and from your workstation.
