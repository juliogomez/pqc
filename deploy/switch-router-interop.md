# Switch-to-router interop (C9300 ↔ C8000)

Tested between C9300 switches and Generation-2 Cisco 8000 Series Secure Routers on IOS XE
26.2. The CLI is almost identical on both platforms, with a few differences that
will bite you if you don't know where to look.

---

## IPsec

The IKEv2 proposals, keyrings, profiles, and IPsec transform-sets are identical CLI
on both platforms. We tested classical baseline and ML-KEM between a C9350 and a
C8235-G2 router running 26.2. Both worked end-to-end.

### Feature compatibility

| Feature | C9350 (switch, 26.2) | C8235-G2 (router, 26.2) |
|---------|---------------|--------------------------|
| Classical IKEv2 | ✓ | ✓ |
| PPK | ✓ | ✓ |
| ML-KEM | ✓ | ✓ |
| ML-DSA | ✓ | ✓ |


### PPK `key hex` gotcha

The `key hex` form rejects hex strings whose **decoded bytes** fall outside printable
ASCII (0x20-0x7E). The error says "All characters in hex string must be ASCII" which
sounds like it's complaining about the hex digits, but it's actually checking the
decoded value. A key like `aabbccdd` (bytes 0xAA, 0xBB, 0xCC, 0xDD) is rejected;
`48656C6C6F` (decodes to `Hello`) is accepted.

This means you cannot use arbitrary binary PPK material through `key hex`. Stick to
hex-encoded ASCII strings, or use `key 0 <plaintext>` which accepts any printable
text directly. Both forms negotiate PPK correctly.

### ML-DSA: `service internal` required

**ML-DSA works on both platforms**, but you need `service internal` first. Without it,
the `mldsakeypair` trustpoint keyword doesn't parse and PKCS#12 import of ML-DSA
bundles fails silently (`status = 65535: Unknown reason`). Enable it in global config
and the import succeeds, producing a trustpoint with `mldsakeypair TP-MLDSA65 65`.

```
configure terminal
 service internal
end
```

The full
ML-DSA walkthrough (PKI setup, PKCS#12 import, trustpoints, measuring handshake
overhead, phased migration) is in
[C8000 Exercise 5](c8000/ipsec.md#exercise-5-ml-dsa-certificate-authentication).

### Counter quirk on C9350

Crypto is offloaded to the Silicon One ASIC, so `show crypto ipsec sa | include pkts`
freezes at whatever value the previous SA left behind and never increments, even when
traffic is actively flowing. Use `show interface TunnelX | include packets` to verify
the data plane instead. C8000 routers on the other end of the tunnel *do* show proper
`#pkts encaps` counters.

### Policy matching on routers

If the router already has IKEv2 policies from other tunnels (DMVPN, site-to-site to
a different peer), make sure your new policy wins the match. IKEv2 policies are
selected by VRF and local address. A catch-all policy (`match address local any`)
can shadow a more specific one if it's evaluated first. Bind your policy to the
specific tunnel-source address:

```
crypto ikev2 policy MY-POLICY
 match address local 110.0.2.1
 proposal MY-PROPOSAL
```

Without this, the router might negotiate with the wrong proposal and reject the
peer's offer with `NO_PROPOSAL_CHOSEN`.

---

## MACsec

MACsec on a switch-to-router link uses network-link mode: both sides run authenticator
*and* supplicant (`dot1x pae both`), the EAP-TLS handshake is local (no RADIUS), and
the same ML-KEM PQC knobs (`access-session pqc-type`, `access-session tls-version 1.3`)
apply on both ends.

The exercises in [C9300 MACsec](c9300/macsec.md) cover switch-to-router as
"Scenario B." The CLI is the same as switch-to-switch with the exceptions below.

### CLI differences

The interface keyword is the one that bites first. On a switch, MACsec goes on with
`macsec network-link` to distinguish a peer link from an access port facing a host.
A routed port on a router can't be an access port, so the keyword doesn't exist and
plain `macsec` *is* network-link mode. Push the switch spelling to a router and IOS
rejects it.

| | C9300 switch | C8000 router |
|---|---|---|
| **Enable MACsec** | `macsec network-link` | `macsec` |
| **Disable MACsec** | `no macsec network-link` | `no macsec` |

Everything else on the interface (MKA policy, pre-shared key chain, dot1x pae,
EAP profile, credentials) is identical.

### Show commands

The show commands are an exact mirror image. The switch has one combined command;
the router splits status and statistics into two:

| What you want | C9300 switch | C8000 router |
|---|---|---|
| MACsec enabled + cipher | `show macsec interface <if>` | `show macsec status interface <if>` |
| Encrypted/decrypted counters | `show macsec interface <if>` | `show macsec statistics interface <if>` |
| Live TX counter label | `Encrypted Pkts` (under SA Statistics) | `Out Pkts Encrypted` (under Transmit SA Counters) |

Both platforms print a frozen per-channel aggregate right next to the live per-SA
counter. Read the wrong one and your encryption delta is always zero on a link that's
encrypting fine. Anchor on the SA block, not the channel summary.

### PQC verification: smd trace syntax

No standard show command reveals which key-exchange group the EAP-TLS handshake
negotiated. The proof lives in the `smd` (session manager) platform trace. The
trace path differs between the two platforms:

**On a C9300 switch:**

```
set platform software trace smd switch active R0 dot1x all debug
dot1x re-authenticate interface <interface>
show logging process smd internal | include Negotiated Group
set platform software trace smd switch active R0 dot1x all notice
```

**On a C8000 router:**

```
set platform software trace smd R0 dot1x all debug
dot1x re-authenticate interface <interface>
show logging process smd internal | include Negotiated Group
set platform software trace smd R0 dot1x all notice
```

No `switch active` in the path on a router. You're looking for
`Negotiated Group (key exchange algorithm):mlkem512` (or `mlkem768` / `mlkem1024`).
If you see `secp256r1`, PQC did not negotiate. Check that `access-session pqc-type`
and `access-session tls-version 1.3` are present on both ends.

### PKI model

The [C8000 MACsec doc](c8000/macsec.md) uses SCEP enrollment against an IOS CA for
router-to-router links. The [C9300 MACsec doc](c9300/macsec.md) takes a simpler path:
each device uses its existing self-signed certificate and you manually import the
peer's cert so both sides trust each other.

For switch-to-router, either model works. The C9300 doc's
[appendix](c9300/macsec.md#appendix-production-pki-with-ios-ca) shows how to use a
C8000 as the CA for both the switch and the router if you want proper CA-signed certs.

---

## Automation

Both platforms have Ansible playbooks. The switch-to-router MACsec scenario is already
a first-class target in the C9300 automation; IPsec interop uses the same playbooks as
switch-to-switch (the CLI is identical).

### MACsec (switch-to-router)

The [C9300 automation](c9300/automation/README.md#macsec) owns the switch-to-router
link. The inventory defines a `macsec_s2r` group containing the switch and the router,
and every MACsec playbook accepts it:

```bash
cd deploy/c9300/automation
ansible-playbook macsec-psk.yml    -e macsec_play_hosts=macsec_s2r   # PSK baseline
ansible-playbook macsec-eaptls.yml -e macsec_play_hosts=macsec_s2r   # EAP-TLS
ansible-playbook macsec-pq.yml     -e macsec_play_hosts=macsec_s2r   # ML-KEM overlay
```

The roles handle the CLI differences (the `macsec` vs `macsec network-link` keyword,
the split show commands on the router) internally. You don't need to touch the C8000
automation at all; the router is just another host in the C9300 inventory.

Teardown:

```bash
ansible-playbook macsec-pq.yml     -e macsec_play_hosts=macsec_s2r -e state=absent
ansible-playbook macsec-eaptls.yml -e macsec_play_hosts=macsec_s2r -e state=absent
```

### IPsec (switch-to-router)

The [C9300 IPsec playbooks](c9300/automation/README.md#ipsec) target the `ipsec_pair`
group, which is two switches. If one end is a C8000 router instead, add it to the
inventory and point `ipsec_pair` at the switch+router pair. The IKEv2 proposals,
keyrings, profiles, and transform-sets are identical CLI, so the roles work without
changes.

The one thing to watch: bind the IKEv2 policy to a specific local address on the
router if it already has policies from other tunnels. The
[policy matching](#policy-matching-on-routers) section above explains why.

The [C8000 automation](c8000/automation/README.md#ipsec) is router-only (hub and spoke
across three routers). It doesn't target switches, but the roles and templates are the
same NETCONF payloads. If you need to automate a mixed fabric, the C9300 playbooks are
the better starting point since the inventory already supports mixed device types.
