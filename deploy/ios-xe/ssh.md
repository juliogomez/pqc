# SSH on IOS XE

> **Pre-req:** this doc assumes you reviewed the container [SSH lab](../../learn/ssh/README.md),
> where OpenSSH negotiated hybrid ML-KEM with zero config.

IOS XE 26.2 supports 3 PQ hybrid (ML-KEM + DH) KEX algorithms in its SSH server:

```
R1(config)# ip ssh server algorithm kex ?
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

3 hybrids, all pairing ML-KEM with different classical DH options:

- `mlkem768x25519-sha256` (ML-KEM-768 + X25519)
- `mlkem768nistp256-sha256` (ML-KEM-768 + P-256)
- `mlkem1024nistp384-sha384` (ML-KEM-1024 + P-384)

No pure ML-KEM option, which is the right call to offer even stronger security. Hybrid means a break in either half still leaves you with the other.

## Exercise 1: Enable PQ SSH KEX

Configure the router SSH server to prefer ML-KEM (the order defines preference):

```
ip ssh server algorithm kex mlkem768x25519-sha256 curve25519-sha256 ecdh-sha2-nistp256
```

This tells the SSH server which KEX algorithms to offer (and in what order). Without this
command, ML-KEM will not be in the server's list, and any client requesting it will get
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

In the [container lab](../../learn/ssh/README.md#exercise-1-post-quantum-key-exchange), OpenSSH negotiated `mlkem768x25519-sha256` by default. On IOS
XE you enable it explicitly with the `ip ssh server algorithm kex` command.

Note the host key line: `rsa-sha2-512`. The key exchange is post-quantum, but the server's
identity proof is still classical RSA. That split is the whole subject of the next
section.

## SSH Authentication

The containers [SSH lab](../../learn/ssh/README.md#exercise-3-post-quantum-authentication) also demonstrated post-quantum **authentication**
using composite `mldsa44-ed25519` keys, so the server proves its identity
with a quantum-safe signature instead of just classical ECDSA/Ed25519. That feature is experimental in OpenSSH 10.4 and tracks
[draft-miller-sshm-mldsa44-ed25519-composite-sigs](https://datatracker.ietf.org/doc/draft-miller-sshm-mldsa44-ed25519-composite-sigs/),
an individual Internet-Draft that is not yet an RFC.

IOS XE 26.2 brings ML-DSA to IKEv2 ([ipsec.md](ipsec.md#exercise-5-ml-dsa-certificate-authentication)),
so the obvious question is whether SSH got it too. The short answer is NO. Ask the router for its **host key** algorithms:

```
R1(config)# ip ssh server algorithm hostkey ?
  ecdsa-sha2-nistp256  ECDSA_SHA2_NISTP256 Publickey based authentication
  ecdsa-sha2-nistp384  ECDSA_SHA2_NISTP384 Publickey based authentication
  ecdsa-sha2-nistp521  ECDSA_SHA2_NISTP521 Publickey based authentication
  rsa-sha2-256         RSA-SHA2-256 Publickey based authentication
  rsa-sha2-512         RSA-SHA2-512 Publickey based authentication
  ssh-rsa              RSA-SHA1 Publickey based authentication
  x509v3-ssh-rsa       RSA-SHA1 Certificate based authentication
```

RSA and ECDSA, nothing else. Same story for **user** authentication: `ip ssh server algorithm
publickey ?` lists Ed25519, FIDO2 security keys and various x509v3 flavours, but no ML-DSA
and no composite.

There is one thing that looks like a loophole. The SSH certificate profile accepts an
ML-DSA trustpoint name without complaint:

```
R1(config)# ip ssh server certificate profile
R1(conf-ssh-server-cert-profile)# server
R1(conf-ssh-server-cert-profile-server)# trustpoint sign TP-MLDSA65
R1(conf-ssh-server-cert-profile-server)#
```

The parser takes _any_ trustpoint name, so this is not evidence of anything. There's no
ML-DSA host key algorithm to advertise in the KEX, so nothing can ever select that
certificate. Treat the accepted command as a parser artifact, not a feature.

In any case... prioritizing ML-KEM support in front of ML-DSA makes total sense. Key exchange protects against
harvest-now-decrypt-later, where an attacker records today and decrypts in fifteen years.
Host key authentication protects against an active man-in-the-middle, which needs a
cryptographically relevant quantum computer *in real time*. One threat starts accruing
today, the other doesn't.

## Watch out: importing an EC keypair can lock you out

If you work through the [ML-DSA exercises](ipsec.md#exercise-5-ml-dsa-certificate-authentication)
you'll import PKCS#12 bundles onto these routers, and that has a side effect on SSH.

Importing an ECDSA P-256 bundle makes the SSH server start advertising
`ecdsa-sha2-nistp256` as a host key algorithm. On 26.2 it then fails every
handshake that selects it:

```
$ ssh admin@<R1-mgmt-ip>
debug1: kex: host key algorithm: ecdsa-sha2-nistp256
Connection closed by 198.18.154.202 port 22
```

Your client picks the algorithm the server claims to prefer, and the router SSH server can't really use it. Get back in by specifying the one that works:

```
$ ssh -o HostKeyAlgorithms=rsa-sha2-512 admin@<R1-mgmt-ip>
```

Then pin the server so it can't happen again:

```
R1(config)# ip ssh server algorithm hostkey rsa-sha2-512 rsa-sha2-256
```

Do this **before** you import anything, on any box where you can't reach a console.

## The automated version

Both settings from this doc are a single NETCONF edit in
[`ssh-pq.yml`](automation/README.md#ssh-and-tls), which pushes the KEX list and the hostkey
pin together as one ordered replace. Worth reaching for once you've done it by hand three
times, and worth respecting: it's the one unit that changes the crypto on the connection
you're managing the box over, so the role refuses to leave the server with no classical
fallback unless you explicitly tell it to.

---

**Cleanup:** this doc leaves two lines behind and you should keep both. The hostkey pin above
is what stops the lockout, and the hybrid KEX list is strictly better than the shipped
default. [Putting the routers back](README.md#two-ssh-settings-worth-keeping) explains why,
and what to remove first if you do want them gone. Next: [MACsec](macsec.md).