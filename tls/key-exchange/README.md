# A Hands-On Post-Quantum TLS Key Exchange Lab

### Can Your HTTPS Survive a Quantum Computer!?

The [IKEv2 key-exchange lab](../../ipsec/key-exchange/README.md) made a VPN tunnel quantum-safe. But the secure channel most of the world actually uses every day is **TLS**: the "S" in HTTPS, the thing behind your browser's padlock, your APIs, your email, almost everything. So the obvious question is: **can a TLS connection go post-quantum, and what does it cost?**

In this lab we answer it the same way as the other labs. We spin up two small containers **right on your own workstation**, point one `openssl s_client` at one `openssl s_server`, run a real **TLS 1.3** handshake, and watch them agree on a key with a **hybrid ML-KEM** group called `X25519MLKEM768`. Then we capture the bytes and prove it. The only thing you need installed is **Docker**.

Ready? Let's find out.

---

## Contents

1. [What are we trying to figure out?](#what-are-we-trying-to-figure-out)
2. [Why should you care? The quantum threat](#why-should-you-care-the-quantum-threat)
3. [Meet the two key exchanges](#meet-the-two-key-exchanges)
4. [Head-to-head: classical vs hybrid](#head-to-head-classical-vs-hybrid)
5. [Our tool of choice: OpenSSL 3.5](#our-tool-of-choice-openssl-35)
6. [Let's get our hands dirty: the lab](#lets-get-our-hands-dirty-the-lab)
7. [How this compares to the IKEv2 and MACsec labs](#how-this-compares-to-the-ikev2-and-macsec-labs)

---

## What are we trying to figure out?

One question drives this lab: **when a TLS 1.3 handshake switches its key exchange from classical to post-quantum, what changes on the wire?**

In TLS 1.3 the key exchange happens in the very first two messages: the client sends a `ClientHello`, the server replies with a `ServerHello`, and inside both there is a `key_share` that carries the public values. Swap the classical group for a hybrid ML-KEM one and that `key_share` is the part that changes.

By the end of this lab you'll have seen, with your own packets:

- **The negotiated group:** a TLS 1.3 handshake that picks `X25519MLKEM768`, confirmed in the OpenSSL output and again in the capture.
- **Size on the wire:** how much bigger the `key_share` gets when ML-KEM joins X25519.
- **No extra round trip:** that TLS 1.3 carries the hybrid key share in the same two messages a classical handshake uses, so the cost is bytes, not round trips.

---

## Why should you care? The quantum threat

The story is the same one from the [IKEv2 key-exchange lab](../../ipsec/key-exchange/README.md), so we'll keep it short here.

A classical TLS key exchange uses **(EC)DHE**: the client and server each send a public value, and they each compute the same shared secret from it. That secret then keys all the symmetric encryption for the session. The problem is that a large enough quantum computer running **Shor's algorithm** can recover that secret from the public values alone.

And here is the real problem, the same **"harvest now, decrypt later"** trap: an attacker can record your TLS traffic today and decrypt it years from now, once the hardware exists. Anything you send over HTTPS that still matters in ten years is already at risk.

The fix is **hybrid ML-KEM**: run the classical X25519 exchange and the post-quantum ML-KEM-768 exchange together, and mix both shared secrets into the TLS keys. An attacker has to break both to win.

---

## Meet the two key exchanges

### Classical: x25519

**X25519** is the most common key exchange in modern TLS 1.3. It is fast, has small 32-byte public values, and has been trusted for years. Its weakness is the one above: Shor's algorithm breaks it.

### Hybrid: X25519MLKEM768

**`X25519MLKEM768`** is a single TLS group that runs both halves at once:

- the classical **X25519** exchange, and
- the post-quantum **ML-KEM-768** ([FIPS 203](https://csrc.nist.gov/pubs/fips/203/final)) key encapsulation.

The TLS key schedule then derives the session keys from **both** shared secrets. If ML-KEM ever turns out to have a flaw, X25519 still protects you today. If a quantum computer breaks X25519, ML-KEM still protects you tomorrow. You only lose if both fall, which is the whole point of going hybrid.

> **Curious why ML-KEM resists a quantum computer?** The "ML" stands for "Module Lattice". The companion [module-lattices lab](../../module-lattices/README.md) builds that math from scratch and runs a real lattice attack to show why it holds. Worth a look if you want to understand the reason instead of just trusting it.

---

## Head-to-head: classical vs hybrid

This is the heart of the lab, and Exercise 2 lets you reproduce every number below.

### Size on the wire

The hybrid group joins the classical and post-quantum public values together, so the `key_share` carries both:

| | x25519 | X25519MLKEM768 (hybrid) |
|-|--------|-------------------------|
| Client key share | 32 B | **1216 B** (32 + 1184) |
| Server key share | 32 B | **1120 B** (32 + 1088) |
| TLS 1.3 group ID | `0x001d` | `0x11ec` |
| Quantum-safe | no | yes |

So the hybrid `ClientHello` and `ServerHello` get about 1.1 KB heavier each. That is the whole cost, and you'll measure it yourself.

### Round trips: nothing extra

Here is the good news. In the [IKEv2 lab](../../ipsec/key-exchange/README.md), ML-KEM's big payloads needed a brand new round trip (`IKE_INTERMEDIATE`, RFC 9370) to carry them. **TLS 1.3 needs none.** The hybrid key share travels in the same `ClientHello` and `ServerHello` that a classical handshake already uses, so the handshake is still **one round trip**. The messages are just bigger.

| Mode | Round trips | What carries the key exchange |
|------|-------------|-------------------------------|
| x25519 only | 1 | `ClientHello` -> `ServerHello` |
| X25519MLKEM768 (hybrid) | 1 | `ClientHello` -> `ServerHello` (same messages, larger) |

### What stays the same

The record encryption is unchanged: TLS 1.3 uses **AES-GCM**, which is symmetric and already quantum-safe. Just like MACsec and IPsec, the quantum work is all in the key exchange, not in the encryption that protects your data.

---

## Our tool of choice: OpenSSL 3.5

The other labs reached for strongSwan (IKEv2) or wpa_supplicant and hostapd (MACsec). For plain TLS we use the tool everyone already knows: **[OpenSSL](https://openssl-library.org/)**, specifically **3.5 or newer**.

Why 3.5? Because it is the first mainstream OpenSSL that ships the post-quantum pieces in the default provider, with no extra libraries and no patches. That includes the `X25519MLKEM768` group we need here. Older OpenSSL (like the 3.0 on Ubuntu 24.04) does not know the group at all and rejects it with an error.

Both containers in this lab run OpenSSL 3.5. We use two OpenSSL commands:

- **`openssl s_server`**: a small TLS server that accepts connections.
- **`openssl s_client`**: a small TLS client that connects and prints what it negotiated.

That is all we need to run a real TLS 1.3 handshake and look inside it.

---

## Let's get our hands dirty: the lab

Here's the plan:

- **[Exercise 1](#exercise-1-run-a-hybrid-tls-13-handshake)**: run a TLS 1.3 handshake that negotiates hybrid `X25519MLKEM768`, and confirm it in the OpenSSL output.
- **[Exercise 2](#exercise-2-prove-it-on-the-wire-and-compare)**: capture the handshake, prove in the bytes that the key share is hybrid, then run a classical handshake next to it and compare. (This is the payoff.)

### How the topology works

Two containers on a private Docker network:

- **`tls-server`** (172.22.0.2): runs `openssl s_server`.
- **`tls-client`** (172.22.0.3): runs `openssl s_client`.

### Build and start

Everything runs **locally on your workstation**. Clone the repo and run all commands from the `tls/key-exchange/` directory:

```bash
cd tls/key-exchange
```

```bash
# Build the image (first run pulls Debian trixie + OpenSSL 3.5)
docker compose build

# Start both roles
docker compose up -d

# Verify both are up
docker compose ps
```

Expected:
```
NAME         STATUS
tls-server   Up
tls-client   Up
```

---

### Exercise 1: Run a hybrid TLS 1.3 handshake

**Step 1: Make a server certificate**

`openssl s_server` needs a certificate to start, but the key exchange does not care which kind it is (that is the [authentication lab's](../authentication/README.md) job). So we make a quick throwaway ECDSA one. Open a shell on the server container:

```bash
docker exec -it tls-server bash
```

```bash
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out /tmp/server.key
openssl req -x509 -new -key /tmp/server.key -out /tmp/server.crt -days 365 \
    -subj "/CN=tls-server.pqc.lab"
```

**Step 2: Start the server, offering the hybrid group**

From that same terminal:

```bash
openssl s_server -accept 4433 -cert /tmp/server.crt -key /tmp/server.key \
    -tls1_3 -groups X25519MLKEM768 -www
```

`-tls1_3` pins TLS 1.3 (ML-KEM only exists there), `-groups X25519MLKEM768` says "this is the only key exchange I'll accept", and `-www` keeps the server up serving a small status page. Leave it running and open a **second terminal** for the client.

**Step 3: Connect with the client**

```bash
docker exec -it tls-client bash
```

```bash
openssl s_client -connect 172.22.0.2:4433 -groups X25519MLKEM768 </dev/null 2>&1 \
    | grep -iE "Negotiated TLS1.3 group|Protocol|Cipher is"
```

You should see:

```
Negotiated TLS1.3 group: X25519MLKEM768
New, TLSv1.3, Cipher is TLS_AES_256_GCM_SHA384
Protocol: TLSv1.3
```

That `Negotiated TLS1.3 group: X25519MLKEM768` line is the proof. OpenSSL only prints it when a hybrid or post-quantum group is chosen. The session keys for this TLS connection are now rooted in ML-KEM.

> **Heads up:** the client also prints a `verify error` line because the server's certificate is self-signed and we did not give the client a CA to trust. That is fine here. This lab is about the key exchange, not who the server is. Proving identity is the [authentication lab's](../authentication/README.md) story.

Stop the server with Ctrl-C when you've seen the line. Leave the containers up for Exercise 2.

---

### Exercise 2: Prove it on the wire, and compare

The negotiated-group line is convincing, but let's look at the actual bytes, and then put a classical handshake right next to the hybrid one.

**Step 1: Capture a hybrid handshake**

On the **server**, start a capture and the server in the background:

```bash
# capture TLS traffic on port 4433
tcpdump -i any -w /tmp/hybrid.pcap -U 'tcp port 4433' &
TCPDUMP_PID=$!

# start the server in the background (reusing the cert from Exercise 1)
openssl s_server -accept 4433 -cert /tmp/server.crt -key /tmp/server.key \
    -tls1_3 -groups X25519MLKEM768 -www >/dev/null 2>&1 &
SSERVER_PID=$!
```

From the **client** (second terminal), run one handshake:

```bash
openssl s_client -connect 172.22.0.2:4433 -groups X25519MLKEM768 </dev/null >/dev/null 2>&1
```

Back on the server, stop the capture and the server:

```bash
kill $TCPDUMP_PID $SSERVER_PID
```

**Step 2: Read the key share out of the capture**

The image ships `tshark`, so we can decode the TLS handshake. From the **server** terminal pull out the key-share groups and their sizes from the `ClientHello` and `ServerHello`:

```bash
tshark -r /tmp/hybrid.pcap -V 2>/dev/null \
    | grep -iE "Key Share Entry: Group:"
```

```
Key Share Entry: Group: X25519MLKEM768, Key Exchange length: 1216
Key Share Entry: Group: X25519MLKEM768, Key Exchange length: 1120
```

There it is, measured. The first line is the client's key share (**1216 bytes**: 32 for X25519 + 1184 for ML-KEM-768) and the second is the server's (**1120 bytes**: 32 + 1088). Exactly the table from earlier, now in your own capture.

**Step 3: Run a classical handshake and compare**

Now do the same with plain X25519. Restart the capture and server on the **server** terminal, this time with `-groups x25519` and a new filename:

```bash
tcpdump -i any -w /tmp/classical.pcap -U 'tcp port 4433' &
TCPDUMP_PID=$!

openssl s_server -accept 4433 -cert /tmp/server.crt -key /tmp/server.key \
    -tls1_3 -groups x25519 -www >/dev/null 2>&1 &
SSERVER_PID=$!
```

From the **client** terminal:

```bash
openssl s_client -connect 172.22.0.2:4433 -groups x25519 </dev/null >/dev/null 2>&1
```

Back to the **server** terminal stop the capture and server again (`kill $TCPDUMP_PID $SSERVER_PID`), then read the classical key shares:

```bash
tshark -r /tmp/classical.pcap -V 2>/dev/null \
    | grep -iE "Key Share Entry: Group:"
```

```
Key Share Entry: Group: x25519, Key Exchange length: 32
Key Share Entry: Group: x25519, Key Exchange length: 32
```

**32 bytes each.** Side by side:

| | x25519 (classical) | X25519MLKEM768 (hybrid) |
|-|--------------------|-------------------------|
| Client key share | 32 B | 1216 B |
| Server key share | 32 B | 1120 B |
| Round trips | 1 | 1 |
| Quantum-safe | no | yes |

That is the core finding of the lab: **post-quantum key exchange in TLS 1.3 costs about a kilobyte extra in each of the first two handshake messages, and nothing else.** No extra round trip, no change to the record cipher.

#### What we've seen

- A real TLS 1.3 handshake can negotiate hybrid `X25519MLKEM768`, proven both in OpenSSL's output and in the captured `key_share`.
- The hybrid key share is about 1.1 KB bigger than the classical one, on each side.
- It travels in the same `ClientHello` and `ServerHello`, so there is no extra round trip (unlike IKEv2's `IKE_INTERMEDIATE`).

---

### Cleanup

```bash
docker compose down
```

`docker compose down` stops and removes the containers and the `tls_net` network. The built image is kept, so the next `docker compose up -d` starts right away. The certificate and captures lived in the container's `/tmp`, so they vanish with the containers, nothing is left on your host.

That's a wrap! You ran a real post-quantum TLS handshake, confirmed the hybrid group, and measured its exact cost on the wire. Next, the [authentication lab](../authentication/README.md) takes care of the other half of the handshake: proving who the server (and client) really is, with post-quantum certificates.

---

## How this compares to the IKEv2 and MACsec labs

All three labs make the *key exchange* quantum-safe with hybrid ML-KEM. What differs is the protocol carrying it:

| | IKEv2 ([key-exchange lab](../../ipsec/key-exchange/README.md)) | MACsec ([lab](../../macsec/README.md)) | TLS (this lab) |
|-|-------------------------------------------------------|--------------------------------------------------|----------------|
| Layer | 3 (IP) | 2 (Ethernet) | 4+ (over TCP) |
| Underlying handshake | IKEv2 + RFC 9370 | EAP-TLS (TLS 1.3) | TLS 1.3 |
| Hybrid group | `x25519-ke1_mlkem768` | `X25519MLKEM768` | `X25519MLKEM768` |
| Extra round trip for ML-KEM? | Yes (`IKE_INTERMEDIATE`) | No | No |
| Data-plane cipher | ESP AES-GCM | MACsec AES-GCM | TLS record AES-GCM |
| Tool | strongSwan | wpa_supplicant + hostapd | OpenSSL |

Same idea, three different protocols: keep the classical exchange, add ML-KEM next to it, and derive the keys from both.
