# SSH on IOS XE

> **Pre-req:** this doc assumes you reviewed the container [SSH lab](../../learn/ssh/README.md),
> where OpenSSH negotiated hybrid ML-KEM with zero config.

IOS XE 26.1 supports 3 PQ hybrid (ML-KEM + DH) KEX algorithms for SSH:

- `mlkem768x25519-sha256` (ML-KEM-768 + X25519)
- `mlkem768nistp256-sha256` (ML-KEM-768 + P-256)
- `mlkem1024nistp384-sha384` (ML-KEM-1024 + P-384)

## Exercise 1: Enable PQ SSH KEX

Configure the router SSH server to prefer ML-KEM:

```
ip ssh server algorithm kex mlkem768x25519-sha256 curve25519-sha256 ecdh-sha2-nistp256
```

This tells the SSH server which KEX algorithms to offer (and in what order). Without this
command, ML-KEM won't be in the server's list, and any client requesting it will get
rejected. The order matters: ML-KEM first means the server prefers PQ, but falls back to
classical if the client doesn't support it.

Verify:

```
R1# show ip ssh
SSH Enabled - version 2.0
...
KEX Algorithms:mlkem768x25519-sha256,curve25519-sha256,ecdh-sha2-nistp256
```

## Exercise 2: Connect with PQ KEX

From your laptop (e.g. macOS with OpenSSH that supports ML-KEM, check with `ssh -Q kex | grep mlkem`) ssh to one of the routers:

```
$ ssh -v -o KexAlgorithms=mlkem768x25519-sha256 admin@<R1-mgmt-ip>
...
debug1: kex: algorithm: mlkem768x25519-sha256
debug1: kex: host key algorithm: rsa-sha2-512
debug1: kex: server->client cipher: aes128-gcm@openssh.com MAC: <implicit> compression: none
debug1: kex: client->server cipher: aes128-gcm@openssh.com MAC: <implicit> compression: none
```

`kex: algorithm: mlkem768x25519-sha256` confirms the SSH session used ML-KEM-768 hybrid
key exchange.

For comparison, a classical connection:

```
$ ssh -v -o KexAlgorithms=curve25519-sha256 admin@<R1-mgmt-ip>
...
debug1: kex: algorithm: curve25519-sha256
```

In the [container lab](../../learn/ssh/README.md#exercise-1-post-quantum-key-exchange), OpenSSH 10.4 negotiated `mlkem768x25519-sha256` by default. On IOS
XE you enable it explicitly with the `ip ssh server algorithm kex` command.

## SSH Authentication

The containers [SSH lab](../../learn/ssh/README.md#exercise-3-post-quantum-authentication) also demonstrated post-quantum **authentication**
using composite `mldsa44-ed25519` keys, so the server proves its identity
with a quantum-safe signature instead of just classical ECDSA/Ed25519. That feature is experimental in OpenSSH 10.4 and tracks
[draft-miller-sshm-mldsa44-ed25519-composite-sigs](https://datatracker.ietf.org/doc/draft-miller-sshm-mldsa44-ed25519-composite-sigs/),
an individual Internet-Draft that is not yet an RFC.

IOS XE 26.1 does not support ML-DSA for SSH. The router uses classical key types (RSA,
ECDSA, Ed25519) to prove its identity when you connect. This is expected: even in the
open-source world this is bleeding-edge and off by default. The SSH key exchange (ML-KEM)
is the more urgent PQ fix anyway, since it protects against harvest-now-decrypt-later
attacks on session confidentiality. Authentication (the router proving it's really that
router) protects against active man-in-the-middle attacks, which require a real-time
quantum computer, a harder threat model.

