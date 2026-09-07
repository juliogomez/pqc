# Post-Quantum Cryptography on Cisco IOS XE

If you've done the [container labs](../../learn/README.md), you already know the concepts:
hybrid key exchange, PPK, ML-KEM, IKE fragmentation, large KEM ciphertexts. This is where
you run all of it on real Cisco hardware and find out which parts the platform can actually
do today. The concepts are the same across both environments. The RFCs don't change just because
you're on a different platform. What changes is the CLI and how the implementation handles
things like fragmentation and licensing.

The target platform is the **Cisco 8000 Series Secure Router** (C8235-G2 in our case) running **IOS XE
26.2**. Three of them, wired back-to-back, with the "advantage" license
that unlocks all crypto features without needing a separate HSECK9 key.

26.2 is the release where the picture changes. 26.1 gave you post-quantum *key exchange*
and left *authentication* classical. 26.2 adds ML-DSA signatures for IKEv2, so a
site-to-site tunnel can now be quantum-safe end to end. 

## Lab topology

```
        ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
        │      R1      │          │      R2      │          │      R3      │
        │   (Spoke-1)  │          │ (Hub/Transit)│          │   (Spoke-2)  │
        │              │          │              │          │              │
        │  Vlan12      │          │  Vlan12      │          │              │
        │  10.0.12.1   │          │  10.0.12.2   │          │              │
        │              │  VLAN 12 │              │          │              │
        │      Tw0/0/0 ├──────────┤ Tw0/0/0      │          │              │
        │              │  2.5 Gb  │              │          │              │
        │              │          │  Vlan23      │          │  Vlan23      │
        │              │          │  10.0.23.1   │          │  10.0.23.2   │
        │              │          │              │  VLAN 23 │              │
        │              │          │      Tw0/0/1 ├──────────┤ Tw0/0/0      │
        │              │          │              │  2.5 Gb  │              │
        └──────────────┘          └──────────────┘          └──────────────┘

```

Every doc below shares this topology.

## Set up the underlay

Before you touch any protocol doc, wire up VLANs, SVIs, and static routes so the three
routers can reach each other. IPsec needs end-to-end reachability between R1 and R3; MACsec
needs the R1–R2 link up. SSH and TLS only need management reachability from your laptop.

**On R1:**

```
vlan 12
 name R1-to-R2

interface TwoGigabitEthernet0/0/0
 switchport mode access
 switchport access vlan 12

interface Vlan12
 ip address 10.0.12.1 255.255.255.252
 no shutdown

ip route 10.0.23.0 255.255.255.252 10.0.12.2
```

**On R2 (transit):**

```
vlan 12
 name R1-to-R2
vlan 23
 name R2-to-R3

interface TwoGigabitEthernet0/0/0
 switchport mode access
 switchport access vlan 12

interface TwoGigabitEthernet0/0/1
 switchport mode access
 switchport access vlan 23

interface Vlan12
 ip address 10.0.12.2 255.255.255.252
 no shutdown

interface Vlan23
 ip address 10.0.23.1 255.255.255.252
 no shutdown

ip routing
```

**On R3:**

```
vlan 23
 name R2-to-R3

interface TwoGigabitEthernet0/0/0
 switchport mode access
 switchport access vlan 23

interface Vlan23
 ip address 10.0.23.2 255.255.255.252
 no shutdown

ip route 10.0.12.0 255.255.255.252 10.0.23.1
```

Verify end-to-end reachability:

```
R1# traceroute 10.0.23.2
  1 10.0.12.2 0 msec 0 msec 0 msec
  2 10.0.23.2 4 msec 0 msec *
```

Two hops. R2 is forwarding. You're ready for whichever protocol doc you want.

## The docs

Once the underlay is in place, pick any doc. They don't depend on each other: IPsec, SSH,
MACsec, and TLS each stand alone. The order here isn't the same as
[Stage 1's](../../learn/README.md#recommended-order), and that's fine. In containers each
lab builds its own world; on hardware you build the underlay once and then run whatever
interests you.

| Doc | What you do |
|-----|-------------|
| [**IPsec**](ipsec.md) | Classical baseline, PPK, native ML-KEM hybrid, a phased hub-and-spoke migration, then ML-DSA certificate authentication and what it costs on the wire |
| [**SSH**](ssh.md) | Enable a PQ hybrid KEX on the SSH server and prove it from your laptop |
| [**MACsec**](macsec.md) | PSK-based MKA end to end, then EAP-TLS with ML-KEM on a local CA (no RADIUS needed) |
| [**TLS**](tls.md) | Prove the management HTTPS server is already negotiating hybrid PQ key exchange, and steer it |

Everything in these docs has been run and verified on real hardware.

## Automation

The same four protocols exist as Ansible playbooks over NETCONF. Start with
[`automation/README.md`](automation/README.md) for what to install and how to run it;
[`automation/DESIGN.md`](automation/DESIGN.md) if you want the YANG-vs-CLI reasons.

## Support summary

| Protocol | Category | PQ feature | Status on IOS XE 26.2 |
|----------|----------|-----------|----------------------|
| IPsec | Key exchange | ML-KEM-768 hybrid IKEv2 | Working |
| IPsec | Key exchange | RFC 8784 PPK | Working |
| IPsec | Authentication | ML-DSA-44 / 65 / 87 signatures | Working |
| SSH | Key exchange | ML-KEM-768 hybrid | Working |
| SSH | Authentication | ML-DSA host key | Not available |
| SSH | Authentication | ML-DSA user key | Not available |
| MACsec | Key exchange | PSK-based MKA + GCM-AES-256 | Working |
| MACsec | Key exchange | ML-KEM EAP-TLS MKA | Working |
| MACsec | Authentication | ML-DSA certificates | Not available |
| TLS | Key exchange | ML-KEM-768 hybrid for mgmt HTTPS | Working |
| TLS | Authentication | ML-DSA certificate auth | Not available |

### ML-DSA keys and certificates

ML-DSA authentication is live for IPsec/IKEv2 only on 26.2. The
[IPsec doc](ipsec.md#two-paths-from-key-exists-to-ml-dsa-tunnel-is-up) covers platform
keygen, 2 different enrollment paths (PKCS#12 import for the IPsec lab,
on-box CSR for the IPsec automation lab), and what the signatures cost on the wire.

## The configs

Sanitized running configs for the verified end state live in
[`device-configs/`](device-configs/), so you can read the outcome or diff against it
without owning the hardware:

| File | State it captures |
|---|---|
| [`R1-mldsa.txt`](device-configs/R1-mldsa.txt) | 26.2, spoke 1, two ML-DSA-65 tunnels |
| [`R2-mldsa-hub.txt`](device-configs/R2-mldsa-hub.txt) | 26.2, hub, one IKEv2 profile per spoke |
| [`R3-mldsa.txt`](device-configs/R3-mldsa.txt) | 26.2, spoke 2, the migrated peer  |
| [`R1-macsec.txt`](device-configs/R1-macsec.txt) | 26.2, PQ MACsec via EAP-TLS |
| [`R2-macsec.txt`](device-configs/R2-macsec.txt) | 26.2, same plus the local CA |


### Clean after yourself

None of these break a tunnel, so nothing reminds you they're still there. Two of them
(`ip http server` and the `revocation-check none` below) are real security regressions to
leave behind on a box that isn't a lab.

```
! the packet capture from the IKE_AUTH size measurement, on R2
R2# no monitor capture CAP

! the EAP trace levels raised in macsec.md, on both MACsec peers
R1# set platform software trace smd R0 eap notice
R1# set platform software trace smd R0 eap-all notice

! the HTTP server, turned on in macsec.md so SCEP had a listener (R2)
no ip http server

! PQC steering on the HTTPS server, back to the platform default
no ip http secure-pqc-type
```

If you kept any trustpoint you set `revocation-check none` on, put it back to
`revocation-check crl`. Turning revocation checking off is fine for a lab with a CA that
publishes no CRL, and not fine anywhere else.

### Two SSH settings worth keeping

Not everything should be reverted.

`ip ssh server algorithm hostkey rsa-sha2-512 rsa-sha2-256` is the pin that stops the ECDSA
lockout described in [ssh.md](ssh.md#watch-out-importing-an-ec-keypair-can-lock-you-out). If
you remove it while an ECDSA trustpoint still exists you can lock yourself out, so
drop the trustpoints first, or just leave the pin in place. It costs nothing.

`ip ssh server algorithm kex mlkem768x25519-sha256 ...` is the whole point of the SSH doc,
and hybrid ML-KEM KEX is a perfectly fine config. Keep it. If you
want the default back anyway, `no ip ssh server algorithm kex`.

### Confirm everything's clean

```
show crypto ikev2 sa                    ! expect no output
show crypto ipsec sa | include peer     ! expect no output
show mka sessions                       ! expect Total MKA Sessions 0
show access-session                     ! expect no sessions
show crypto pki trustpoints | include Trustpoint
show run | include pqc-type|monitor capture
```

### On your workstation

`gen-mldsa-certs.sh` writes unencrypted private keys, and the PKCS#12 bundles you copied to
the routers are the same key material:

```bash
cd deploy/ios-xe/mldsa-certs
rm -rf mldsa-pki/
```

`.gitignore` already keeps that directory out of commits, but it's still sitting on your
disk. Delete the copies you pushed to the routers too, which
[ipsec.md](ipsec.md) covers inline:

```
R1# delete /force bootflash:/mldsa65-r1.p12
```
