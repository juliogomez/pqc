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

Every doc below shares this topology. The IPsec doc builds the underlay configs from scratch.

## The docs

The order here isn't the same as [Stage 1's](../../learn/README.md#recommended-order), and
that's deliberate. In containers you can start anywhere because each lab builds its own
world. On hardware the config accumulates, so the order follows the dependencies: IPsec
first because it builds the underlay everything else sits on, SSH next because it's a single
line on a box you're already logged into, MACsec third because it needs the VLANs and SVIs
IPsec created, and TLS last because it's the one where the interesting finding is that the
router was already post-quantum before you typed anything.

| # | Doc | What you do |
|---|-----|-------------|
| 1 | [**IPsec / IKEv2**](ipsec.md) | Classical baseline, RFC 8784 PPK, native ML-KEM-768 hybrid, a phased hub-and-spoke migration, then ML-DSA certificate authentication and what it costs on the wire |
| 2 | [**SSH**](ssh.md) | Enable a PQ hybrid KEX on the SSH server and prove it from your laptop |
| 3 | [**MACsec**](macsec.md) | PSK-based MKA end to end, then EAP-TLS with ML-KEM on a local CA (no RADIUS needed) |
| 4 | [**TLS**](tls.md) | Prove the management HTTPS server is already negotiating hybrid PQ key exchange, and steer it |

Everything in those docs was run and verified on real hardware. 

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
| MACsec | Authentication | ML-DSA certificates | Not supported |
| TLS | Key exchange | ML-KEM-768 hybrid for mgmt HTTPS | Working |
| TLS | Authentication | ML-DSA certificate auth | Not available |

### Where ML-DSA keys come from

The router generates them itself. That's a genuine security improvement over minting the key on a workstation and importing it. IOS XE 26.2 supports `crypto key generate mldsa` with `crypto key generate` as an *exec-mode* command (not _config-mode_):

```
R1# crypto key generate ?
  ec     Generate EC keys for ECDSA
  mldsa  Generate ML-DSA keys
  rsa    Generate RSA keys
```

For example:

```
R1# crypto key generate mldsa param 65 label PROBE-MLDSA65
The name for the keys will be: PROBE-MLDSA65
% Generating MLDSA-65 keys, keys will be non-exportable...[OK] (elapsed time was 0 seconds)

R1# show crypto key mypubkey all
Key name: PROBE-MLDSA65
Key type: ML-DSA-65 KEYS
```

Full syntax is `crypto key generate mldsa param {44|65|87} [label WORD] [exportable]`, and
`crypto key zeroize mldsa <label>` takes it back out. Keys are non-exportable unless you ask,
which is the right default for an identity key.


### Getting the certificate onto the router

Having the key is not the same as having a certificate. The key lives on the router; the
certificate wrapping it comes from an external CA you build on your workstation with
OpenSSL 3.5+.

[gen-mldsa-certs.sh](mldsa-certs/gen-mldsa-certs.sh) is the script the
[ML-DSA exercise](ipsec.md#exercise-5-ml-dsa-certificate-authentication) walks
through: a root CA per parameter set, an identity certificate for each router, and a PKCS#12
bundle to import. It's the quickest way to get three routers holding ML-DSA identities,
and the path verified end to end on this hardware. The trade-off is clear: that
bundle carries a private key that was born on your workstation, not on the router.

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

None of these break a tunnel, so nothing reminds you they're still there. Three of them
(`service internal`, `ip http server`, and the `revocation-check none` below) are real
security regressions to leave behind on a box that isn't a lab.

```
! debug-only global that unlocked the ML-DSA CLI in ipsec.md
no service internal

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
you remove it while an ECDSA trustpoint still exists you can lock yourself out again, so
drop the trustpoints first, or just leave the pin in place. It costs nothing.

`ip ssh server algorithm kex mlkem768x25519-sha256 ...` is the whole point of the SSH doc,
and hybrid ML-KEM KEX is strictly better than what the box shipped with. Keep it. If you
want the default back anyway, `no ip ssh server algorithm kex`.

### Confirm everything's clean

```
show crypto ikev2 sa                    ! expect no output
show crypto ipsec sa | include peer     ! expect no output
show mka sessions                       ! expect Total MKA Sessions 0
show access-session                     ! expect no sessions
show crypto pki trustpoints | include Trustpoint
show run | include service internal|pqc-type|monitor capture
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
