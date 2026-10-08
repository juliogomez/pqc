# Stage 2: deploy on Cisco hardware

The [container labs](../learn/README.md) prove the protocols. This half proves the
Cisco *platforms*: the same ML-KEM and ML-DSA work, on Cisco gear you'd actually put in a
network.

Expect a different kind of writing here. The container labs are reproducible by design;
these documents are field notes. They record what shipped, what it costs, what the
platform does not support yet, and what are the most relevant and useful router commands.

## Platforms covered

### Cisco 8000 Series Secure Routers (IOS XE)

The WAN side: IPsec tunnels, SSH and TLS management, and MACsec between routers. Start
with the [C8000 platform guide](c8000/README.md) for the topology, feature summary, and
reading order, then pick a protocol:
[IPsec](c8000/ipsec.md) · [MACsec](c8000/macsec.md) · [SSH](c8000/ssh.md) · [TLS](c8000/tls.md).

Ansible playbooks for all four protocols live under
[`c8000/automation/`](c8000/automation/README.md) ([operator guide](c8000/automation/README.md),
[design notes](c8000/automation/DESIGN.md), and
[captured hardware runs](c8000/automation/captured/)).

### Cisco 9300 Series Smart Switches (IOS XE)

The access layer: MACsec with EAP-TLS 1.3 using ML-KEM on host-to-switch,
switch-to-switch, and switch-to-router links, plus SSH and TLS management PQC. Start with the
[C9300 platform guide](c9300/README.md) for the feature summary, then pick a protocol:
[MACsec](c9300/macsec.md) · [SSH](c9300/ssh.md) · [TLS](c9300/tls.md) · [IPsec](c9300/ipsec.md).

Ansible playbooks for all four protocols live under
[`c9300/automation/`](c9300/automation/README.md) ([operator guide](c9300/automation/README.md),
[design notes](c9300/automation/DESIGN.md)).

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

## If you don't have the hardware

You don't need the Cisco gear to get value out of this half. Every document includes the
real command output, the sanitized running configs are checked in, and where a platform has
automation, its captured playbook runs document that path the same way. Read the outcome of
each exercise and use the status tables to plan a migration.

## If you do have the hardware

Two things before you start typing, because routers don't have a `docker compose down`:

- **Snapshot the running config first.** Every exercise builds on the previous one, so
  nothing you configure gets undone as you go. One `copy running-config` up front turns the
  whole teardown into a single command later.
- **Plan the teardown.** [Putting the routers back](c8000/README.md#putting-the-routers-back)
  covers the fast rollback, the surgical removal in dependency order, the debug-only globals
  that are easy to leave enabled, and the private key material to delete from `bootflash:`
  and from your workstation.
