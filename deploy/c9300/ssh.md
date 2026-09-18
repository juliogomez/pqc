# SSH on C9300 Smart Switches

> **Pre-req:** the container [SSH lab](../../learn/ssh/README.md) covers the concepts.
> The [C8000 SSH doc](../c8000/ssh.md) is the same feature on routers. This is the
> switching equivalent.

The C9300 supports the same PQ hybrid SSH KEX algorithms as the C8000 routers.
These are **not enabled by default**. You have to explicitly configure them with
`ip ssh server algorithm kex`.

## Default KEX list

Out of the box, the SSH server advertises classical-only KEX:

```
C9300_PQC1# show ip ssh
SSH Enabled - version 2.0
...
KEX Algorithms:curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256,
               ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group14-sha256,
               diffie-hellman-group16-sha512
```

No ML-KEM in sight. Let's fix that.

## Exercise 1: Enable PQ SSH KEX

Check what's available:

```
C9300(config)# ip ssh server algorithm kex ?
  curve25519-sha256              Curve 25519 key exchange algorithm
  curve25519-sha256@libssh.org   Curve 25519 key exchange algorithm old name
  diffie-hellman-group14-sha1    DH_GRP14_SHA1 diffie-hellman key exchange algorithm
  diffie-hellman-group14-sha256  DH_GRP14_SHA256 diffie-hellman key exchange algorithm
  diffie-hellman-group16-sha512  DH_GRP16_SHA512 diffie-hellman key exchange algorithm
  ecdh-sha2-nistp256             ECDH_SHA2_P256 ecdh key exchange algorithm
  ecdh-sha2-nistp384             ECDH_SHA2_P384 ecdh key exchange algorithm
  ecdh-sha2-nistp521             ECDH_SHA2_P521 ecdh key exchange algorithm
  mlkem1024nistp384-sha384       MLKEM1024_NISTP384_SHA384 PQ/T hybrid key exchange algorithm
  mlkem768nistp256-sha256        MLKEM768_NISTP256_SHA256 PQ/T hybrid key exchange algorithm
  mlkem768x25519-sha256          MLKEM768_X25519_SHA256 PQ/T hybrid key exchange algorithm
```

Three ML-KEM hybrids, same as the C8000:

| Algorithm | ML-KEM variant | Classical component |
|-----------|---------------|-------------------|
| `mlkem768x25519-sha256` | ML-KEM-768 | X25519 |
| `mlkem768nistp256-sha256` | ML-KEM-768 | NIST P-256 |
| `mlkem1024nistp384-sha384` | ML-KEM-1024 | NIST P-384 |

All three are hybrids: both the ML-KEM and the classical DH have to be broken to
compromise the key exchange. There's no pure ML-KEM option, and that's fine. Hybrid
is the recommended deployment for SSH.

Configure the server to prefer ML-KEM, with classical fallback:

```
ip ssh server algorithm kex mlkem768x25519-sha256 curve25519-sha256 ecdh-sha2-nistp256
```

The order matters. ML-KEM first means clients that support it will negotiate PQ. Clients
that don't (older OpenSSH, PuTTY, etc.) fall back to `curve25519-sha256` and still
connect fine.

Verify:

```
C9300# show ip ssh
SSH Enabled - version 2.0
...
KEX Algorithms:mlkem768x25519-sha256,curve25519-sha256,ecdh-sha2-nistp256
```

## Exercise 2: Connect with PQ KEX

From your laptop, check that your OpenSSH client supports ML-KEM:

```bash
ssh -Q kex | grep mlkem
```

If it lists `mlkem768x25519-sha256`, you're good. OpenSSH 9.x and later support it.

Connect to the switch's management IP, forcing ML-KEM:

```bash
ssh -v -o KexAlgorithms=mlkem768x25519-sha256 admin@<switch-mgmt-ip>
```

In the verbose output, look for:

```
debug1: kex: algorithm: mlkem768x25519-sha256
```

That confirms the SSH session used ML-KEM hybrid key exchange. The session key is
quantum-safe.

Without the `-o KexAlgorithms=` flag, the client picks the first algorithm both sides
support. If you put ML-KEM first in the server's list (which you did), and the client
also has it, it should negotiate PQ automatically:

```bash
ssh -v admin@<switch-mgmt-ip>
```

Check the `debug1: kex: algorithm:` line. If it still shows a classical algorithm, your
client might have a different preference order. Use `-o KexAlgorithms=` to override.

## What this gives you

SSH ML-KEM protects the management plane. Anyone capturing your SSH traffic to the switch
can't derive the session keys, even with a quantum computer. The session payload
(commands, config output) stays confidential.

What it doesn't give you: PQ authentication. The SSH host key is still RSA or ECDSA.
ML-DSA host keys aren't available on the C9300 yet. For now, the key exchange is
quantum-safe; the host key authentication is classical. That's the same state as the
C8000 routers (see [C8000 SSH doc](../c8000/ssh.md)).

## Cleanup

To revert to the default KEX list:

```
no ip ssh server algorithm kex
```

Verify with `show ip ssh` that the KEX line goes back to the classical defaults.

> **Worth keeping?** Unlike MACsec config (which changes forwarding behavior and can
> break connectivity), adding ML-KEM to the SSH KEX list is low-risk. Classical clients
> still connect. You might want to leave this in place.
