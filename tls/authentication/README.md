# A Hands-On Post-Quantum TLS Authentication Lab

### Who Is That Server, Really? Proving Identity After Quantum

The companion [TLS key-exchange lab](../key-exchange/README.md) made sure a future quantum computer can't decrypt your HTTPS traffic. Good. But a secure channel to the *wrong* server is no help at all. So the other half of every TLS handshake is **authentication**: the certificate that proves the server (and sometimes the client) is who it claims to be. And those certificates rely on **digital signatures**, which a quantum computer can also break.

In this lab we generate real certificates with the post-quantum signature algorithm **ML-DSA**, line them up next to today's classical **ECDSA**, and then use both to authenticate a real **mutual TLS** handshake between two containers on your own workstation. We'll see how much bigger the post-quantum certificates are, prove the handshake really signed with ML-DSA, and measure what that does to the bytes on the wire. The only thing you need installed is **Docker**.

Ready? Let's find out who is on the other end.

---

## Contents

1. [What are we trying to figure out?](#what-are-we-trying-to-figure-out)
2. [Why should you care? A different deadline](#why-should-you-care-a-different-deadline)
3. [Meet the two contenders](#meet-the-two-contenders)
4. [Head-to-head: the size story](#head-to-head-the-size-story)
5. [Our tool of choice: OpenSSL 3.5](#our-tool-of-choice-openssl-35)
6. [Let's get our hands dirty: the lab](#lets-get-our-hands-dirty-the-lab)
7. [Where TLS authentication stands today](#where-tls-authentication-stands-today)
8. [How this compares to the IKEv2 authentication lab](#how-this-compares-to-the-ikev2-authentication-lab)

---

## What are we trying to figure out?

Here's the question driving this lab: **when a TLS certificate swaps its classical signature (ECDSA) for a post-quantum one (ML-DSA), what changes, and how much should we worry about it?**

Authentication is how one side proves "I am who I say I am." On the web that proof is a **digital signature**, wrapped in an **X.509 certificate** signed by a Certificate Authority (CA). Your browser trusts a website because a CA signed its certificate. Follow that idea far enough and the whole trust system of the internet comes down to signatures.

By the end of this lab you'll have seen, with your own certificates:

- **Size:** how much bigger ML-DSA certificates are than ECDSA ones (measured, not guessed).
- **A real handshake:** two peers proving their identities to each other (mutual TLS), first with ECDSA, then with ML-DSA.
- **The cost on the wire:** how much heavier the handshake gets when the certificates go post-quantum.

---

## Why should you care? A different deadline

The key-exchange labs had one clear villain: **"harvest now, decrypt later"**. An attacker records your traffic today and decrypts it once a quantum computer arrives. That makes post-quantum key exchange urgent right now.

Authentication is different, and this trips up a lot of people. A signature on a live handshake only has to resist forgery **up to the moment it is checked**. If a quantum computer that can forge signatures shows up in 2035, it cannot go back in time and fake your 2026 handshake. That session is already over. So for a single TLS handshake, there is no "harvest now" version of the threat.

But that does not mean you can relax. Authentication still has to go post-quantum, just for slower reasons:

- **Trust anchors live for a long time.** Root and intermediate CA certificates often last 10 to 25 years. A root signing key a quantum computer can forge lets an attacker mint perfectly "trusted" certificates the day such a machine exists. Those roots need to be safe well before that day.
- **PKI moves slowly.** Changing a signature algorithm means updating CAs, issuance pipelines, validation libraries, and every client that checks a certificate. That is years of work across the whole ecosystem.

So the mental model is: **key exchange is the fire alarm, authentication is the slow renovation.** Less urgent, but you cannot do it overnight. The companion [IKEv2 authentication lab](../../ipsec/authentication/README.md) digs into this deadline in more depth.

---

## Meet the two contenders

### Classical: ECDSA (P-256)

**ECDSA** on the P-256 curve is the signature behind a huge share of today's TLS certificates. Small keys, small signatures, fast, and trusted for years. Its problem is the usual one: Shor's algorithm on a powerful enough quantum computer recovers the private key from the public key, and then an attacker can forge any signature.

### Post-quantum: ML-DSA

**ML-DSA** (Module-Lattice-Based Digital Signature Algorithm, [FIPS 204](https://csrc.nist.gov/pubs/fips/204/final)) is NIST's recommended general-purpose post-quantum signature. It comes in three sizes: **ML-DSA-44** (NIST level 2), **ML-DSA-65** (level 3, the sensible default), and **ML-DSA-87** (level 5). Signing and verifying are fast, about as fast as ECDSA. The catch is size: the keys and signatures are much bigger.

> **Why is ML-DSA quantum-safe?** Like ML-KEM in the key-exchange labs, ML-DSA is built on module lattices. The companion [module-lattices lab](../../module-lattices/README.md) builds that math from the ground up and shows, with a real lattice attack, why no quantum computer can break it.

---

## Head-to-head: the size story

This is where post-quantum authentication earns its reputation. The numbers below are the DER-encoded (the compact binary form that travels on the wire) sizes of self-signed certificates, and you'll reproduce them in Exercise 1:

| Algorithm | Security | Public key | Signature | Self-signed cert (DER) |
|-----------|----------|-----------|-----------|------------------------|
| ECDSA P-256 | ~128-bit | 65 B | ~70 B | ~378 B |
| ML-DSA-44 | NIST L2 | 1312 B | 2420 B | ~3977 B |
| ML-DSA-65 | NIST L3 | 1952 B | 3309 B | ~5506 B |
| ML-DSA-87 | NIST L5 | 2592 B | 4627 B | ~7464 B |

An ECDSA certificate is under 400 bytes. The ML-DSA-65 one is about **15 times** larger. And that matters for TLS because a handshake carries a whole **certificate chain plus a signature**. Make the certificates post-quantum and the handshake jumps from a few hundred bytes of identity material to several kilobytes. You'll feel that in Exercise 3.

One nice contrast with the [IKEv2 authentication lab](../../ipsec/authentication/README.md): IKEv2 runs over UDP, which has no built-in way to carry a message that is bigger than one packet, so those big certificates force the `IKE_AUTH` message to split into many fragments (IKEv2's own fragmentation, RFC 7383), and that is where the rough edges live. TLS runs over TCP instead, which streams the bytes for you, so the certificates just make the handshake longer rather than forcing fragmentation. The size is still real, it just shows up as more bytes to send, not as a fragmentation headache.

---

## Our tool of choice: OpenSSL 3.5

One tool carries this whole lab: **[OpenSSL 3.5+](https://openssl-library.org/)**. It has native support for the NIST post-quantum signatures (ML-DSA and SLH-DSA) in the default provider, with no extra libraries. You can build post-quantum keys and certificates and you can run a full TLS handshake.

Both containers here run OpenSSL 3.5. A small script (`gen-certs.sh`) mints the CA and the server and client certificates for you, in either ECDSA or ML-DSA, so you can switch between them by changing one word on the command line.

And here is a difference from the IKEv2 authentication lab: post-quantum certificate authentication in TLS works on **stable** OpenSSL 3.5, while ML-DSA authentication in IKEv2 still needs an experimental strongSwan branch. The TLS "plumbing" is more mature. (See [Where TLS authentication stands today](#where-tls-authentication-stands-today) for the honest caveats.)

---

## Let's get our hands dirty: the lab

Here's the plan:

- **[Exercise 1](#exercise-1-weigh-the-certificates)**: generate ECDSA and ML-DSA certificates and weigh them, so you measure the difference in size.
- **[Exercise 2](#exercise-2-mutual-tls-with-ecdsa-certificates)**: bring up mutual TLS with classical ECDSA certificates (today's baseline).
- **[Exercise 3](#exercise-3-mutual-tls-with-ml-dsa-certificates)**: swap in ML-DSA certificates, prove the handshake signed with ML-DSA, and measure how much heavier the handshake got.

### How the topology works

Two containers on a private Docker network, like the other labs' two peers:

- **`tls-auth-server`** (172.23.0.2): runs `openssl s_server` and proves its identity.
- **`tls-auth-client`** (172.23.0.3): runs `openssl s_client` and proves its identity too.

Both trust one small **CA** we spin up just for the lab. The CA signs a server certificate and a client certificate, and both sides get the CA certificate so they can check each other. That is **mutual TLS**: each end proves who it is.

### Build and start

Everything runs **locally on your workstation**. Run all commands from the `tls/authentication/` directory:

```bash
cd tls/authentication
```

```bash
# Build the image and start both roles
docker compose up -d --build

# Verify both are up
docker compose ps
```

Expected:
```
NAME              STATUS
tls-auth-server   Up
tls-auth-client   Up
```

---

### Exercise 1: Weigh the certificates

Let's measure the size jump before we use the certificates for anything. Open a shell on the server (any container with OpenSSL 3.5 works):

```bash
docker exec -it tls-auth-server bash
cd /tmp
```

Generate one self-signed certificate per algorithm, in DER (the form that travels on the wire):

```bash
for entry in "EC:ecdsa" "ML-DSA-44:mldsa44" "ML-DSA-65:mldsa65" "ML-DSA-87:mldsa87"; do
    alg=${entry%%:*}; name=${entry##*:}
    case "$alg" in
        EC) openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$name.key" 2>/dev/null ;;
        *)  openssl genpkey -algorithm "$alg" -out "$name.key" 2>/dev/null ;;
    esac
    openssl req -x509 -new -key "$name.key" -out "$name.crt" -days 365 \
        -subj "/CN=$name" -outform DER 2>/dev/null
done

ls -l *.crt | awk '{print $5"  "$9}' | sort -n
```

Expected (your bytes will be within a few of these):

```
378  ecdsa.crt
3977  mldsa44.crt
5506  mldsa65.crt
7464  mldsa87.crt
```

There it is, measured yourself. The ML-DSA-65 certificate is about 15 times the size of the ECDSA one. Look inside one to confirm it is a perfectly ordinary X.509 certificate, just with a quantum-safe signature:

```bash
openssl x509 -in mldsa65.crt -inform DER -text -noout | grep -iE "Signature Algorithm|Public Key Algorithm" | head -2
```

```
Signature Algorithm: ML-DSA-65
Public Key Algorithm: ML-DSA-65
```

Exit the shell when you're done (`exit`). For the full eight-algorithm lineup, including SLH-DSA, see the [IKEv2 authentication lab](../../ipsec/authentication/README.md).

---

### Exercise 2: Mutual TLS with ECDSA certificates

Now let's actually use certificates to authenticate a real handshake, starting with today's classical ECDSA.

**Step 1: Mint the ECDSA certificates**

```bash
docker compose run --rm --build certgen ecdsa
```

This builds the lab CA and issues an ECDSA server certificate and an ECDSA client certificate, dropping each (plus the CA certificate) into the right config directory, which is mounted into each container at `/cfg`. The `--build` makes sure the helper uses the same OpenSSL 3.5 image as the two roles (it prints its OpenSSL version as it runs). You need to run this before starting the server in the next step, otherwise `/cfg/server.key` won't exist yet.

**Step 2: Start the server, requiring a client certificate**

Open a shell on the server:

```bash
docker exec -it tls-auth-server bash
openssl s_server -accept 4433 -cert /cfg/server.crt -key /cfg/server.key \
    -CAfile /cfg/ca.crt -Verify 1 -tls1_3 -www
```

`-Verify 1` tells the server to ask for and check the client's certificate (this is what makes it *mutual*), and `-CAfile` is the CA it checks against. Leave it running and open a **second terminal** for the client.

**Step 3: Connect with the client, presenting its own certificate**

```bash
docker exec -it tls-auth-client bash
openssl s_client -connect 172.23.0.2:4433 \
    -cert /cfg/client.crt -key /cfg/client.key -CAfile /cfg/ca.crt </dev/null 2>&1 \
    | grep -iE "Verify return code|Peer signature type|SSL handshake has read"
```

You should see something like:

```
Peer signature type: ecdsa_secp256r1_sha256
SSL handshake has read 2544 bytes and written 2383 bytes
Verify return code: 0 (ok)
```

Three things to read here:

- **`Verify return code: 0 (ok)`**: the client checked the server's certificate against the shared CA and it passed.
- **`Peer signature type: ecdsa_secp256r1_sha256`**: the server proved its identity with an ECDSA signature (on the P-256 curve).
- **`SSL handshake has read ... bytes`**: note this number. It is roughly how many bytes the client received during the handshake, including the server's certificate. We'll compare it in the next exercise. Your byte counts may be off by a few from the ones above, because each certificate has a random serial number. (The server, in its terminal, also printed the client's certificate details, that is the mutual half doing its job.)

Stop the server with Ctrl-C, but keep the containers up and the terminals connected to them.

---

### Exercise 3: Mutual TLS with ML-DSA certificates

Same handshake, post-quantum certificates this time. The lesson is in how little changes to *run* it, and how much changes on the *wire*.

**Step 1: Reissue the certificates as ML-DSA**

From a third terminal window, go to the TLS authentication folder (`tls/authentication`) and run:

```bash
docker compose run --rm --build certgen ml-dsa-65
```

Same helper, different algorithm: the CA, the server certificate, and the client certificate are now all ML-DSA-65. Notice we did not change anything else.

**Step 2: Start the server again**

The command is identical to before (the certificate files just hold ML-DSA keys now). Go to the **server** terminal:

```bash
openssl s_server -accept 4433 -cert /cfg/server.crt -key /cfg/server.key \
    -CAfile /cfg/ca.crt -Verify 1 -tls1_3 -www
```

**Step 3: Connect, and prove it went post-quantum**

From the **client** terminal:

```bash
openssl s_client -connect 172.23.0.2:4433 \
    -cert /cfg/client.crt -key /cfg/client.key -CAfile /cfg/ca.crt </dev/null 2>&1 \
    | grep -iE "Verify return code|Peer signature type|SSL handshake has read"
```

Now you should see something like:

```
Peer signature type: mldsa65
SSL handshake has read 16031 bytes and written 15901 bytes
Verify return code: 0 (ok)
```

(Your exact byte counts will differ. The jump is the point.)

That `Peer signature type: mldsa65` is the proof: the server authenticated itself with an **ML-DSA signature**, verified against an ML-DSA CA, over a real TLS 1.3 handshake. Both ends are now quantum-safe for authentication, and you changed one word (`ecdsa` to `ml-dsa-65`) to get there.

**Step 4: Compare the two handshakes**

Put the `SSL handshake has read ... bytes` lines from Exercise 2 and Exercise 3 side by side:

| | ECDSA certs | ML-DSA-65 certs |
|-|-------------|-----------------|
| Server signature | ECDSA | ML-DSA-65 |
| Handshake bytes read by client | ~2.5 KB | ~16 KB |
| Verify result | ok | ok |

The handshake got several kilobytes heavier, and that extra weight is almost entirely the bigger certificates and the bigger signature. That is the core finding: **post-quantum TLS authentication works the same way and proves the same thing, it just ships a lot more bytes to do it.**

> **Why measure bytes instead of decoding the certificate with tshark?** In the key-exchange lab we read the key share straight out of the capture, because the `ClientHello` and `ServerHello` are sent in the clear. In TLS 1.3 the certificates come *after* those messages and are **encrypted**, so a passive capture can't show them without the session keys. OpenSSL's own handshake byte counter is the simplest honest way to see the size impact.

---

### Cleanup

From the third terminal window:

```bash
docker compose down

# Remove the generated CA + server/client keys and certs. They were written to
# these host dirs via bind mounts, so `down` doesn't clear them, and they
# include private keys. (.gitignore keeps them out of commits.)
rm -f config/server/ca.crt config/server/server.crt config/server/server.key \
      config/client/ca.crt config/client/client.crt config/client/client.key
```

That's a wrap! You generated post-quantum certificates, measured the size jump, and used them to mutually authenticate a real TLS handshake, first with classical ECDSA, then with post-quantum ML-DSA. You have now touched both halves of a quantum-safe TLS connection: key exchange in the companion lab, and authentication here.

---

## Where TLS authentication stands today

"This is great," you might be thinking, "but can I get an ML-DSA certificate for my real website today?" Short answer: not yet, and here is the honest picture.

- **The crypto is ready and stable.** OpenSSL 3.5 supports ML-DSA keys, certificates, and TLS authentication in its default provider. That is why this lab runs on a stable release, unlike the IKEv2 authentication lab, which needs an experimental strongSwan branch.
- **No public CA issues post-quantum certificates yet.** Browsers and operating systems do not trust ML-DSA roots, so for now post-quantum certificates only work with a **local CA** or self-signed certificates, exactly like the lab CA we built here.
- **The TLS code points are still settling.** The signature scheme identifiers that let TLS negotiate ML-DSA are recent and still moving through the IETF TLS working group. OpenSSL implements them, but expect details to shift before everything is final. One practical quirk: if a server offers both a classical and an ML-DSA certificate, OpenSSL often prefers the classical one, so you may need `-sigalgs mldsa65` to force the post-quantum choice. (In this lab the server holds *only* an ML-DSA certificate, so it is always chosen.)

So just like the IKEv2 story, the building blocks are here and worth getting hands-on with now, even though the wider ecosystem (public CAs, browsers, every server) still has a long way to go.

---

## How this compares to the IKEv2 authentication lab

Both labs make *authentication* quantum-safe with ML-DSA certificates. The differences are in the protocol and how mature the tooling is:

| | IKEv2 ([authentication lab](../../ipsec/authentication/README.md)) | TLS (this lab) |
|-|-----------------------------------------------------------|----------------|
| Protocol | IKEv2 | TLS 1.3 |
| Tool | strongSwan | OpenSSL |
| PQC auth available on | experimental `ml-dsa` branch | stable OpenSSL 3.5 |
| Big certs cause | `IKE_AUTH` fragmentation (many fragments) | a longer handshake over TCP (no fragmentation) |
| Signature proven by | `authentication ... with ML_DSA_44 successful` | `Peer signature type: mldsa65` |
| Identity carried in | certificate SAN matched as IKE ID | certificate, verified against the CA |

Same idea (ML-DSA certificates proving identity), two protocols, with TLS a step ahead on tooling maturity.
