# TLS on C9300 Smart Switches

> **Learn it first:** the container labs
> [TLS key exchange](../../learn/tls/key-exchange/README.md) and
> [TLS authentication](../../learn/tls/authentication/README.md) cover the concepts.
> The [C8000 TLS doc](../c8000/tls.md) is the same feature on routers. This is the
> switching equivalent.

IOS XE uses TLS in more places than you'd think: management HTTPS, EAP-TLS for MACsec,
RADIUS, TACACS+, syslog, and gNMI. This doc focuses on the **management HTTPS server**
because it's the one with a PQC steering knob, and the easiest to prove from your laptop
with `openssl s_client`.

EAP-TLS for MACsec has its own PQC knob (`access-session pqc-type`) and is covered in
the [MACsec doc](macsec.md#exercise-3-post-quantum-macsec-eap-tls-with-ml-kem).

On IOS XE 26.2 the HTTPS server negotiates post-quantum hybrid key exchange
(X25519MLKEM768) out of the box, with no configuration at all. Authentication is still
classical.

## Exercise 1: Prove the HTTPS server is already post-quantum

### Prerequisite: a working self-signed trustpoint

The HTTPS server needs a trustpoint with a valid certificate. Check:

```
show ip http server secure status
```

Look at the `HTTP secure server trustpoint` line. If the trustpoint it names has no
certificate (for example because the self-signed cert was deleted or the trustpoint was
changed), HTTPS connections fail with `tlsv1 alert internal error` before any PQC
negotiation can happen.

If that's the case, point the server at a trustpoint with a valid cert:

```
ip http secure-trustpoint Self
```

Confirm with `show crypto pki certificates Self` that a certificate exists and
`Status: Available`. This is a lab-setup detail, not a PQC issue.

### The test

Point OpenSSL 3.5+ at the switch's HTTPS interface. No switch config needed:

```bash
$ openssl s_client -connect <switch-mgmt-ip>:443 -tls1_3 -brief </dev/null

CONNECTION ESTABLISHED
Protocol version: TLSv1.3
Ciphersuite: TLS_AES_256_GCM_SHA384
Peer certificate: unstructuredName=C9300_PQC1_IPsec
Hash used: SHA256
Signature type: rsa_pss_rsae_sha256
Verification error: self-signed certificate
Negotiated TLS1.3 group: X25519MLKEM768
```

`Negotiated TLS1.3 group: X25519MLKEM768` is the whole result. ML-KEM-768 combined
with X25519, exactly what the
[container TLS lab](../../learn/tls/key-exchange/README.md) negotiated between two
OpenSSL peers, now spoken by the Cisco switch.

Read the other key line: `Signature type: rsa_pss_rsae_sha256`. Key exchange is
post-quantum; the certificate is classical RSA. Same split as
[SSH](ssh.md).

The switch agrees with your client:

```
SW1# show ip http server secure status
HTTP secure server status: Enabled
HTTP secure server port: 443
HTTP secure server TLS version:  TLSv1.3 TLSv1.2
HTTP secure server trustpoint: Self
HTTP secure server ECDHE curve: secp384r1
HTTP secure server PQC type: all
```

`HTTP secure server PQC type: all` is the line that makes this work without touching
the config.

## Exercise 2: Steer the key exchange

`all` isn't the only setting:

```
SW1(config)# ip http secure-pqc-type ?
  all      All pqc, non-pqc and hybrid algorithms will be supported
  hybrid   Hybrid cryptographic algorithms (Key derived with PQC and non-PQC)
  non-pqc  Classic cryptographic algorithms
  pqc      Post-Quantum Cryptographic algorithms
```

Prove the knob does something. Force the server classical:

```
SW1(config)# ip http secure-pqc-type non-pqc
```

Connect again with the same client offering the same groups:

```bash
$ openssl s_client -connect <switch-mgmt-ip>:443 -tls1_3 -trace </dev/null \
    | grep -A2 "extension_type=key_share"

        extension_type=key_share(51), length=1258
            NamedGroup: X25519MLKEM768 (4588)
            NamedGroup: ecdh_x25519 (29)
        extension_type=key_share(51), length=2
            NamedGroup: secp384r1 (P-384) (24)
```

Read that exchange carefully, because it's a textbook HelloRetryRequest. The client
offers X25519MLKEM768 and x25519. The server wants neither and sends back a bare
`key_share` naming `secp384r1`, which is the `HTTP secure server ECDHE curve` from the
status output. The client retries with a P-384 share and the handshake completes
classically.

The brief output confirms it:

```bash
$ openssl s_client -connect <switch-mgmt-ip>:443 -tls1_3 -brief </dev/null

CONNECTION ESTABLISHED
Protocol version: TLSv1.3
Peer Temp Key: ECDH, secp384r1, 384 bits
```

No `X25519MLKEM768`. Classical only.

Put it back:

```
SW1(config)# ip http secure-pqc-type all
```

And `Negotiated TLS1.3 group: X25519MLKEM768` returns.

**When would you set this?** `non-pqc` for a client that chokes on a large ClientHello,
`pqc` if you want to *require* post-quantum and fail closed rather than fall back. `all`
is the sane default and is what ships.

## TLS authentication: still classical

The container [TLS authentication lab](../../learn/tls/authentication/README.md)
demonstrated mutual TLS with ML-DSA certificates. The switch's management HTTPS server
can't do that yet.

`Signature type: rsa_pss_rsae_sha256` on every successful handshake. The key exchange is
post-quantum; the certificate identity proof is not.

That's the same story as the C8000 routers. See the
[C8000 TLS doc](../c8000/tls.md#tls-authentication-still-classical) for the full
discussion.

## Summary

| | Default (26.2) |
|---|---|
| TLS 1.3 key exchange | **Post-quantum hybrid (X25519MLKEM768)** |
| Steering the key exchange | `ip http secure-pqc-type` (`all` / `hybrid` / `pqc` / `non-pqc`) |
| Certificates | Classical (RSA) |

Half the problem is solved, and it's the half that matters for harvest-now-decrypt-later:
a recorded management session can no longer be decrypted later by a quantum attacker.
Forging the switch's HTTPS identity still only needs to defeat RSA, but that's an active
real-time attack, not a recording.

### What about the other TLS consumers?

The HTTPS server is not the only thing on the box that speaks TLS. EAP-TLS runs a full
TLS 1.3 handshake for 802.1X/MACsec authentication (covered in the
[MACsec doc](macsec.md#exercise-3-post-quantum-macsec-eap-tls-with-ml-kem), where
hybrid post-quantum is verified) and has its own steering knob: `access-session pqc-type`.

Beyond those two, IOS XE speaks TLS in several other places: RADIUS, TACACS+, and syslog
(all TLS clients connecting out to external servers) and gNMI (a gRPC/TLS server that
external collectors connect to). None of them expose a PQC knob on 26.2.

`ip http secure-pqc-type` is scoped to the HTTPS server. `access-session pqc-type` is
scoped to EAP-TLS. The other TLS consumers have no equivalent today.

## Cleanup

The only thing Exercise 2 changed was `ip http secure-pqc-type`. If you set it back to
`all`, the switch is in its default state:

```
ip http secure-pqc-type all
```

If you changed the trustpoint as part of the prerequisite, restore it to the original:

```
ip http secure-trustpoint <original-trustpoint-name>
```
