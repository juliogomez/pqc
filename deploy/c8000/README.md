# Post-Quantum Cryptography on Cisco 8000 Series Secure Routers

If you've done the [container labs](../../learn/README.md), you already know the concepts:
hybrid key exchange, PPK, ML-KEM, IKE fragmentation, large KEM ciphertexts. This is where
you run all of it on real Cisco hardware and find out which parts the platform can actually
do today. The concepts are the same across both environments. The RFCs don't change just because
you're on a different platform. What changes is the CLI and how the implementation handles
things like fragmentation and licensing.

The target platform is the **Cisco 8000 Series Secure Routers** running **IOS XE
26.2**. Three of them, wired back-to-back, with the "advantage" license
that unlocks all crypto features without needing a separate HSECK9 key. These docs
write "C8000" as shorthand for the platform in tables, paths, and command output.

IOS XE 26.1 gave you post-quantum *key exchange*
and left *authentication* classical. 26.2 adds ML-DSA signatures for IKEv2, so a
site-to-site tunnel can now be quantum-safe end to end.


## Set up the underlay connectivity

To run this lab we will use 3 routers: R1, R2, R3. Before you touch any protocol doc, wire up VLANs, interfaces, and static routes (or routing protocols) so these
routers can reach each other. IPsec needs end-to-end reachability between R1 and R3; MACsec
needs the R1–R2 link up. SSH and TLS only need router management reachability from your laptop.


## The protocols

Once the underlay connectivity is in place, pick any doc. They don't depend on each other: IPsec, SSH,
MACsec, and TLS each stand alone. The order here isn't the same as
[Stage 1's](../../learn/README.md#recommended-order), and that's fine. In containers each
lab builds its own world; on hardware you build the underlay once and then run whatever
interests you.

| Doc | What you do |
|-----|-------------|
| [**IPsec**](ipsec.md) | Classical baseline, PPK, native ML-KEM hybrid, a phased hub-and-spoke migration, then ML-DSA certificate authentication |
| [**SSH**](ssh.md) | Enable a PQ hybrid KEX on the SSH server and prove it from your laptop |
| [**MACsec**](macsec.md) | PSK-based MKA end to end, then EAP-TLS with ML-KEM on a local CA |
| [**TLS**](tls.md) | Prove the management HTTPS server is already negotiating hybrid PQ key exchange |


## Automation

The same exercises for the four protocols exist as Ansible playbooks over NETCONF. Start with
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


## The configs

If you don't have the hardware, sanitized running configs for the verified end state live in
[`device-configs/`](device-configs/), so you can read the outcome or diff against it:

| File | State it captures |
|---|---|
| [`R1-mldsa.txt`](device-configs/R1-mldsa.txt) | 26.2, spoke 1, two ML-DSA-65 tunnels |
| [`R2-mldsa-hub.txt`](device-configs/R2-mldsa-hub.txt) | 26.2, hub, one IKEv2 profile per spoke |
| [`R3-mldsa.txt`](device-configs/R3-mldsa.txt) | 26.2, spoke 2, the migrated peer  |
| [`R1-macsec.txt`](device-configs/R1-macsec.txt) | 26.2, PQ MACsec via EAP-TLS |
| [`R2-macsec.txt`](device-configs/R2-macsec.txt) | 26.2, same plus the local CA |


### Clean after yourself

None of these break a tunnel, but they might be real security regressions to
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

Not everything *needs* to be reverted.

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
cd deploy/c8000/mldsa-certs
rm -rf mldsa-pki/
```

`.gitignore` already keeps that directory out of commits, but it's still sitting on your
disk. Delete the copies you pushed to the routers too:

```
R1# delete /force bootflash:/mldsa65-r1.p12
```

## Platform trust (secure boot)

The protocol docs above are all transport-layer PQC. There's a second layer underneath:
the hardware-anchored secure boot chain. You don't configure it, but you can verify it,
and the output reveals that the C8235-G2's microloader and ROMMON are already signed
with LDWM (a quantum-resistant hash-based signature scheme).

See [**secure-boot.md**](secure-boot.md) for the full walkthrough.