# TLS on IOS XE (mgmt plane)

> **Learn it first:** this doc assumes you went through the container labs
> [TLS key exchange](../../learn/tls/key-exchange/README.md) and
> [TLS authentication](../../learn/tls/authentication/README.md).
> Start at the [platform overview](README.md) for the topology and hardware.

This is the shortest doc in the set, because the answer is "not yet". It is here anyway:
knowing which surfaces are *not* covered is as operationally useful as knowing which are.

## Exercise 1: Testing PQ TLS on HTTPS management

The router's HTTPS management interface supports TLS 1.3:

```
ip http secure-server
ip http tls-version TLSv1.3
```

Testing from a client with OpenSSL 3.6 (which supports ML-KEM), e.g. MacOS:

```
$ echo | openssl s_client -connect <R1-mgmt-ip>:443 -tls1_3 \
    -groups X25519MLKEM768:secp256r1 2>&1 | grep -E "Temp Key|Cipher|Protocol"

Peer Temp Key: ECDH, prime256v1, 256 bits
New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
Protocol: TLSv1.3
```

**Result: PQ TLS is not yet supported on the management HTTPS interface.** The server
negotiated TLS 1.3 with classical ECDH (prime256v1) even when the client offered
X25519MLKEM768. The `ip http secure-ecdhe-curve` command only allows secp256r1, secp384r1,
and secp521r1.

This makes sense: Cisco prioritized PQ for the data plane (IPsec) and remote access (SSH)
first. Management plane TLS will likely get ML-KEM support in a future release.

## TLS Authentication (not covered)

The container [TLS authentication lab](../../learn/tls/authentication/README.md) demonstrated mutual
TLS with ML-DSA certificates. Since IOS XE 26.1 doesn't support PQ key exchange on its
HTTPS server, PQ authentication (ML-DSA certificates) is also not available. Both halves
of PQ TLS (key exchange and authentication) will need to come together in a future release.

Note that TLS itself is not absent from the PQ picture on this platform; it is just not
*this* TLS. The [MACsec doc](macsec.md#exercise-2-post-quantum-macsec-eap-tls-with-ml-kem) runs a
TLS 1.3 handshake with `X25519MLKEM768` inside EAP-TLS. What lags is the router's own
embedded HTTPS server, not the platform's TLS capability as a whole.

