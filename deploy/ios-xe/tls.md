# TLS on IOS XE

> **Learn it first:** this doc assumes you went through the container labs
> [TLS key exchange](../../learn/tls/key-exchange/README.md) and
> [TLS authentication](../../learn/tls/authentication/README.md).
> Start at the [platform overview](README.md) for the topology and hardware.

IOS XE uses TLS in more places than you'd expect: management HTTPS, EAP-TLS for MACsec,
RADIUS, TACACS+, syslog, and gNMI all speak it. This doc focuses on the management HTTPS
server because it's the only one that got a PQC knob on 26.2. The others are covered at the
[end of this page](#what-about-the-other-tls-consumers).

On 26.2 the HTTPS server negotiates post-quantum hybrid key exchange (X25519MLKEM768) out of the box, with no
configuration at all. The authentication half is still classical.

## Exercise 1: Prove the HTTPS server is already post-quantum

Point OpenSSL 3.5+ at the router's HTTPS interface. No router config needed:

```
$ openssl s_client -connect <R1-mgmt-ip>:443 -tls1_3 -brief </dev/null

CONNECTION ESTABLISHED
Protocol version: TLSv1.3
Ciphersuite: TLS_AES_256_GCM_SHA384
Peer certificate: CN=IOS-Self-Signed-Certificate-3526832631
Hash used: SHA256
Signature type: rsa_pss_rsae_sha256
Negotiated TLS1.3 group: X25519MLKEM768
```

`Negotiated TLS1.3 group: X25519MLKEM768` is the whole result. ML-KEM-768 combined with
X25519, exactly what the [container TLS lab](../../learn/tls/key-exchange/README.md)
negotiated between two OpenSSL peers, now spoken by the Cisco router.

Read this other line though: `Signature type: rsa_pss_rsae_sha256`. Key exchange is
post-quantum, the certificate is classical RSA. Same split as
[SSH](ssh.md#ssh-authentication).

The router of course agrees with your client:

```
R1# show ip http server secure status
HTTP secure server status: Enabled
HTTP secure server port: 443
HTTP secure server TLS version:  TLSv1.3 TLSv1.2
HTTP secure server trustpoint: TP-self-signed-3526832631
HTTP secure server ECDHE curve: secp384r1
HTTP secure server PQC type: all
```

`HTTP secure server PQC type: all` is the new line in 26.2 and the reason this works
without you doing anything.

## Exercise 2: Steer the key exchange

`all` isn't the only setting:

```
R1(config)# ip http secure-pqc-type ?
  all      All pqc, non-pqc and hybrid algorithms will be supported
  hybrid   Hybrid cryptographic algorithms (Key derived with PQC and non-PQC)
  non-pqc  Classic cryptographic algorithms
  pqc      Post-Quantum Cryptographic algorithms
```

Prove the knob does something. Force the server classical:

```
R1(config)# ip http secure-pqc-type non-pqc
```

Then connect again with the same client offering the same groups:

```
$ openssl s_client -connect <R1-mgmt-ip>:443 -tls1_3 -trace </dev/null \
    | grep -A2 "extension_type=key_share"

        extension_type=key_share(51), length=1258
            NamedGroup: X25519MLKEM768 (4588)
            NamedGroup: ecdh_x25519 (29)
        extension_type=key_share(51), length=2
            NamedGroup: secp384r1 (P-384) (24)
```

Read that exchange carefully, because it's a textbook HelloRetryRequest. The client offers
X25519MLKEM768 and x25519. The server wants neither, and sends back a bare `key_share`
naming `secp384r1`, which is the `HTTP secure server ECDHE curve` from the status output.
The client retries with a P-384 share and the handshake completes classically.

Notice the client's first `key_share` is **1,258 bytes** against 2 bytes for the server's
retry. That's the ML-KEM-768 encapsulation key, and it's the same size story you measured
for [ML-DSA in IKEv2](ipsec.md#exercise-6-what-ml-dsa-actually-costs), just smaller.

Put it back:

```
R1(config)# ip http secure-pqc-type all
```

and `Negotiated TLS1.3 group: X25519MLKEM768` returns.

**When would you set this?** `non-pqc` for a client that chokes on a large ClientHello,
`pqc` if you want to *require* post-quantum and fail closed rather than fall back. `all`
is the sane default and is what ships.

## TLS authentication: still classical

The container [TLS authentication lab](../../learn/tls/authentication/README.md)
demonstrated mutual TLS with ML-DSA certificates. The router's management HTTPS server can't do
that on 26.2.

As with SSH, the CLI will *accept* an ML-DSA trustpoint:

```
R1(config)# ip http secure-trustpoint TP-MLDSA65
R1(config)#
```

No error, because `ip http secure-trustpoint` takes any trustpoint name. That's not proof
of support. In testing, the HTTPS server did not serve a working ML-DSA certificate, and
the signature type on every successful handshake stayed classical
(`rsa_pss_rsae_sha256`).

Worth flagging separately: on 26.2, repeatedly repointing `ip http
secure-trustpoint` at imported certificates left the HTTPS server wedged, returning
internal errors even after pointing back at a classical trustpoint. Recovery was to set it
explicitly back to the router's own self-signed trustpoint:

```
R1(config)# ip http secure-trustpoint TP-self-signed-3526832631
```

That failure was **not** ML-DSA specific. An imported RSA-2048 trustpoint produced it too.


## Summary

| | 26.1 | 26.2 |
|---|---|---|
| TLS 1.3 key exchange | classical only | **post-quantum hybrid by default** |
| Steering the key exchange | no knob | ip http secure-pqc-type |
| Certificates | classical | classical |

Half the problem is solved, and it's the half that matters for
harvest-now-decrypt-later: a recorded management session can no longer be decrypted later
by a quantum attacker. Forging the router's HTTPS identity still only needs to defeat RSA,
but that's an active real-time attack, not a recording.

### What about the other TLS consumers?

The HTTPS server is not the only thing on the box that speaks TLS. EAP-TLS runs a full
TLS 1.3 handshake for 802.1X/MACsec authentication (covered in the
[MACsec doc](macsec.md#exercise-2-post-quantum-macsec-eap-tls-with-ml-kem), where
hybrid post-quantum is verified) and has its own steering knob: `access-session pqc-type`.

Beyond those two, IOS XE speaks TLS in several other places: RADIUS, TACACS+, and syslog
(all TLS clients connecting out to external servers) and gNMI (a gRPC/TLS server that
external collectors connect to). I walked the CLI for all four on 26.2. None of them
expose a PQC knob of any kind.

`ip http secure-pqc-type` is scoped to the HTTPS server, and `access-session pqc-type` is scoped to EAP-TLS. The other TLS consumers
have no equivalent on 26.2.

This doc focuses on management HTTPS because it's the easiest to prove from your laptop
with `openssl s_client` and the only one with a PQC steering CLI.

## The automated version

`ip http secure-pqc-type` is one modelled YANG leaf, which makes
[`tls-pq.yml`](automation/README.md#ssh-and-tls) the smallest playbook in the
[automation layer](automation/README.md) and the only one that needs no CLI escape hatch at
all. Nothing here you couldn't type in five seconds, and that's the point: it's the cleanest
example of what a structured PQC edit looks like when the platform has modelled the feature
properly.

---

That's the last of the four. You've now run post-quantum key exchange on real hardware at
Layer 2 (MACsec), Layer 3 (IPsec) and the application layer (SSH, TLS), plus post-quantum
authentication where 26.2 supports it, and you've measured exactly where it doesn't.

**Cleanup:** four docs' worth of config is sitting on those routers, along with unencrypted
ML-DSA private keys on your workstation and PKCS#12 bundles on `bootflash:`.
[Putting the routers back](README.md#putting-the-routers-back) clears all three.
