# A Hands-On Post-Quantum SSH Lab

### Is your remote access ready for a quantum computer?

Almost everything we do to a server, we do over **SSH**: admin shells, `git push`, CI/CD deploys, `scp`/`sftp`, port-forwarding tunnels. It is the padlock on remote access. So here is a fair question for anyone who runs infrastructure: is SSH ready for a cryptographically relevant quantum computer, and if not, where does the post-quantum work go?

SSH has a surprisingly good answer, and a surprisingly honest one. Its **key exchange** is already post-quantum, and not as an opt-in curiosity: it is the **negotiated default** in current OpenSSH, on before you touch a single setting. Its **authentication** (the host keys and user keys that prove identity) is the piece still catching up, but as of **OpenSSH 10.4** you can now turn on a post-quantum signature and watch it work too.

We find all of this the same way as the other labs in this repo: start two containers **on your own workstation**, run a real SSH handshake between them, and capture the packets to see the post-quantum crypto for ourselves. The only tool you need installed is **Docker**.

Ready? Let's start.

---

## Sections

1. [What are we trying to figure out?](#what-are-we-trying-to-figure-out)
2. [Why should you care?](#why-should-you-care)
3. [SSH did PQ first](#ssh-did-pq-first)
4. [Where's the quantum risk?](#wheres-the-quantum-risk)
5. [Post-quantum pieces](#post-quantum-pieces)
6. [Tooling: OpenSSH](#tooling-openssh)
7. [The lab](#the-lab)

---

## What are we trying to figure out?

One question drives this lab: **when SSH goes post-quantum, what part of it actually changes, and how much of that has already happened without you noticing?**

Like every secure handshake, an SSH connection rests on two pillars, and a quantum computer threatens both:

- **Key exchange** decides the session secret. It is vulnerable to *harvest now, decrypt later*: an attacker records your SSH session today and decrypts it once a quantum computer arrives. The fix is **ML-KEM**.
- **Authentication** proves who is on each end (the server's **host key**, and your **user key**). Its quantum deadline is different: there is no forging a past session, but your long-lived host keys and SSH CAs must outlive the threat. The fix is **ML-DSA**.

By the end you will have seen, in your own packets and logs:

- **Post-quantum key exchange, by default:** a real handshake negotiating hybrid `mlkem768x25519-sha256` with zero configuration, proven in the cleartext `KEXINIT`.
- **The cost, on the wire:** how ML-KEM's chunky key share grows the key-exchange packets, byte for byte, with *no extra round trip*.
- **A loud downgrade:** connect to a server that only speaks classical crypto and watch SSH both fall back **and warn you about it** (a useful contrast with MACsec's *silent* downgrade).
- **Post-quantum authentication:** swap the classical Ed25519 host and user keys for a composite **Ed25519+ML-DSA-44** signature, prove both the server and you authenticate with it, and measure the size cost.

---

## Why should you care? 

**SSH** ([RFC 4251](https://www.rfc-editor.org/rfc/rfc4251)) is the secure channel for operating infrastructure. Where TLS protects the browser-to-web-server path and IPsec/MACsec protect network links, SSH protects the *administrative* path: the shell you get on a box, the transport under `git`, the pipe your CI uses to deploy, the tunnels that forward internal services.

That makes it a juicy **harvest-now-decrypt-later** target. An SSH session often carries exactly the things an attacker most wants recorded for later: commands, credentials typed at a prompt, private files pulled over `scp`, secrets forwarded through an agent or a tunnel. Record that ciphertext today, break the key exchange with a future quantum computer, and the whole session replays in the clear. Protecting the *key exchange* is the urgent half, and it is the half SSH has already largely solved.

The other half, **authentication**, is about the host keys that stop you connecting to an impostor and the user keys that let the server trust you. A quantum computer cannot retroactively forge a signature from a session that already happened, but it *can* forge future ones, so long-lived host keys and SSH certificate authorities are the things you want migrated before the threat lands, not during a fire drill.

---

## SSH did PQ first

Here is the part that makes SSH genuinely different from every other protocol in this repo: it was **first**, and it turned PQC **on by default** years ago.

First, SSH adopted post-quantum key exchange **before NIST finished standardising one**. The OpenSSH developers picked a conservative lattice/code-based KEM they trusted and shipped the hybrid in 2022, rather than wait. When ML-KEM was standardised (FIPS 203, 2024) they added it and made it the default. So SSH is the one place a real protocol has carried **two generations** of post-quantum key exchange at once: the pre-standard `sntrup761x25519-sha512` and the NIST `mlkem768x25519-sha256` that has since replaced it as the default (both still show up in the `ssh -Q kex` list in Exercise 1).

Second, because it is the *default*, the interesting failure mode for SSH is the opposite of MACsec's. In the MACsec lab you had to *fight* to keep post-quantum key exchange (a simple config flag silently dropped it). In SSH you get it for free, and modern OpenSSH will even **warn you** when a connection *ain't* post-quantum. The risk is not silently losing PQC; it is talking to old software that never had it.

---

## Where's the quantum risk?

An SSH connection is built in two phases. It opens in cleartext: a `KEXINIT` negotiation, the key exchange, and the server's host-key signature all ride in the clear. Only after keys are derived (`NEWKEYS`) does the encrypted channel open, where you authenticate with your user key before the session (shell, sftp, tunnel) begins. Walk those steps and ask, at each, "does a quantum computer break this?"

- **The transport cipher** (like AES-GCM) that protects the session once keys are set is **symmetric** crypto. Grover's algorithm only halves its effective strength, so it stays quantum-safe. Leave it alone.
- **The key exchange** is classical (EC)DHE by default in old SSH: exactly what Shor's algorithm breaks, and exactly the **harvest-now-decrypt-later** problem. This is the urgent one, and it is what hybrid ML-KEM fixes.
- **Authentication** is two signatures: the **host key** (the server signs the handshake so you know you reached the right box, verified against your `known_hosts` or an SSH CA) and your **user key** (you sign to prove who you are). Both are classical digital signatures (Ed25519/ECDSA/RSA) today, both are forgeable by a quantum computer, but only *going forward*: breaking the signature in 2035 does not rewrite a login that happened in 2025.

| | Key exchange | Authentication (host + user keys) |
|-|--------------|-----------------------------------|
| Classical primitive in SSH | X25519 / ECDH | Ed25519 / ECDSA / RSA signatures |
| Broken by | Shor's algorithm | Shor's algorithm |
| Threat timing | **Harvest now, decrypt later** | **Must break *during* the session** |
| Urgency | High (recorded today, broken later) | Lower (no forging the past) |
| Post-quantum fix | hybrid ML-KEM (`mlkem768x25519-sha256`) | composite ML-DSA (`mldsa44-ed25519`) |
| Status in OpenSSH | **default** since 10.0 | **experimental, opt-in** since 10.4 |

> **One thing to remember:** SSH's session encryption is already quantum-safe. Its *key exchange* is quantum-safe too, by default, in any recent OpenSSH. The one piece still on the frontier is *authentication*, and that is the piece this lab has to turn on by hand.

---

## Post-quantum pieces

Making SSH post-quantum means two changes, and OpenSSH 10.4 provides both.

### 1. Key exchange

SSH negotiates its key exchange as a **named algorithm** in the cleartext `KEXINIT` message that opens every connection. A classical handshake picks something like `curve25519-sha256`. A post-quantum one picks **`mlkem768x25519-sha256`**: a hybrid that runs classical X25519 **and** post-quantum ML-KEM-768 together and mixes *both* shared secrets into the session keys, so an attacker has to break **both** to win.

- **X25519** is fast and battle-tested but falls to Shor's algorithm.
- **ML-KEM-768** ([FIPS 203](https://csrc.nist.gov/pubs/fips/203/final)) has no known quantum attack but is newer.
- **`mlkem768x25519-sha256`** combines them. If either holds, the session is safe.

There is a useful contrast with IKEv2. IKEv2 needed a whole extra round trip (`IKE_INTERMEDIATE`, RFC 9370) to carry ML-KEM's big payloads. SSH does not: the client's X25519 public key **and** its ML-KEM-768 encapsulation key ride together in the *same* init msg, and the server's reply carries its X25519 key **and** the ML-KEM ciphertext in one reply. Same two messages a classical handshake uses, just bigger, exactly like TLS. You will measure that in [Exercise 2](#exercise-2-prove-it-on-the-wire-then-watch-a-downgrade).

| | X25519 | ML-KEM-768 | contribution to the SSH message |
|-|--------|-----------|---------------------------------|
| Client sends (`KEX_ECDH_INIT`) | 32 B | **1184 B** (encapsulation key) | +1184 B over classical |
| Server sends (`KEX_ECDH_REPLY`) | 32 B | **1088 B** (ciphertext) | +1088 B over classical |
| Quantum-safe | no | yes | |

### 2. Authentication

SSH authentication is a signature on both ends: the server signs with its **host key**, you sign with your **user key**. Making that post-quantum means using a PQ signature key type for both.

OpenSSH 10.4 introduced exactly one, and it is a **composite** (hybrid) type, `mldsa44-ed25519`: every key and signature is classical **Ed25519** *and* post-quantum **ML-DSA-44** concatenated, so, just like the hybrid key exchange, an attacker must break both. 

ML-DSA ([FIPS 204](https://csrc.nist.gov/pubs/fips/204/final)) is a lattice-based signature scheme with no known quantum attack. Its defining practical feature is **size**: its keys and signatures dwarf Ed25519's, and that shows up in the host key on the wire and everything that carries it.

| | Ed25519 | composite `mldsa44-ed25519` | vs Ed25519 |
|-|---------|-----------------------------|------------|
| Public-key blob (SSH wire) | 51 B | 1383 B | ~27x |
| Host-key signature (SSH wire) | 83 B | 2523 B | ~30x |

That is the whole trade you will see in [Exercise 3](#exercise-3-post-quantum-authentication-composite-ml-dsa): a few dozen bytes become a few kilobytes.

> **This one is genuinely on the frontier.** The composite type is **experimental** in OpenSSH 10.4, **off by default**, and tracks an *individual* Internet-Draft (`draft-miller-sshm-mldsa44-ed25519-composite-sigs`), not a ratified standard. Competing drafts propose *pure* ML-DSA (`ssh-mldsa44/65/87`, `draft-sfluhrer`/`draft-rpe`) and other composites (`draft-sun`, `draft-josefsson`); which wins is not settled. The older [OQS-OpenSSH](https://openquantumsafe.org/applications/ssh.html) fork (Dilithium/Falcon/SPHINCS+) is archived and research-only. So this is a *hands-on preview*, not a production recommendation, exactly the "the crypto is ready, the protocol plumbing is still settling" caveat you saw in the IKEv2 and MACsec labs.

> **Why ML-KEM and ML-DSA are quantum-safe.** Both are built on module lattices. The companion [module-lattices lab](../module-lattices/README.md) builds that math from the ground up and runs a real attack against the lattice problem that protects them.

---

## Tooling: OpenSSH

**OpenSSH** is the reference implementation and what almost every server and client actually runs. It does the key exchange *and* the authentication.

We build **OpenSSH 10.4 from source** for one reason: the post-quantum bits are new. The composite `mldsa44-ed25519` signature only landed in **10.4** (just released on July '26), and distro packages lag behind. Building from source pins the exact version so both ends definitely have both features.

> **OpenSSH ships its own post-quantum crypto.** This is a neat contrast with the other labs. The IKEv2 lab gets ML-KEM from strongSwan's `ml` plugin; the TLS and MACsec labs get it from OpenSSL 3.5. OpenSSH links OpenSSL only for *classical* primitives (Ed25519, X25519, the ciphers); its **ML-KEM is its own formally-verified implementation** and the composite ML-DSA-44 code ships with it too. No oqs-provider, no patches.

> **Why no OpenSSL's `s_client` here?** Because SSH is its own protocol, not TLS. `openssl s_client` speaks TLS; it has no idea how to do an SSH `KEXINIT` or an SSH host-key check. OpenSSH is the tool that speaks the wire protocol, and it carries the PQC algorithms natively, so we watch them run in a real SSH handshake.

---

## The lab

Here is the plan: we run one SSH handshake and look at its two independent halves, key exchange and authentication, one at a time:

- **The key exchange is post-quantum from the very first connection.** No configuration; OpenSSH negotiates `mlkem768x25519-sha256` by default. Exercises 1 and 2 prove it, measure it, and show how it can be lost to old software.
- **The authentication starts classical.** We begin with **Ed25519** host and user keys, so the *identity* half is still quantum-vulnerable at the baseline. Exercise 3 is where we upgrade to composite ML-DSA-44.

So:

- **[Exercise 1](#exercise-1-post-quantum-key-exchange-on-by-default)**: connect and see hybrid `mlkem768x25519-sha256` negotiated with zero config.
- **[Exercise 2](#exercise-2-prove-it-on-the-wire-then-watch-a-downgrade)**: capture the handshake, prove the key exchange in the cleartext bytes and measure ML-KEM's size cost, then connect to a classical-only server and watch SSH downgrade *and warn you*.
- **[Exercise 3](#exercise-3-post-quantum-authentication-composite-ml-dsa)**: reissue the host and user keys as composite **`mldsa44-ed25519`**, prove both ends authenticate with a post-quantum signature, and measure the size cost on the wire.

### Topology

Two containers on a plain Docker bridge network, like the TLS lab's server/client (SSH is ordinary TCP, so no veth tricks are needed):

- **`ssh-server`**: runs OpenSSH `sshd`.
- **`ssh-client`**: runs OpenSSH `ssh`.

Both idle on startup; you drive `sshd` and `ssh` by hand via `docker exec` (like `hostapd`/`wpa_supplicant` in the MACsec lab), so the exercises can capture, edit config, and reconnect freely. The client has network reachability to the server by its docker compose service name, `ssh-server`.

### Prerequisites

**Docker** with the Compose v2 plugin (`docker compose ...`), and three terminals: one shell on the server (to run `sshd` and capture packets), one on the client (to run `ssh`), and one on your host (for `docker compose` commands like reissuing keys). Everything the handshake needs, OpenSSH 10.4, `tcpdump`, `tshark`, `openssl`, is compiled/installed into the image.

### Build and start

Everything runs **locally on your workstation**. Clone the repo and run all commands from the `ssh/` directory:

```bash
cd ssh
```

```bash
# Build the image (first run compiles OpenSSH 10.4 from source, ~1-2 min)
docker compose build

# Mint the lab SSH host key + user key (classical Ed25519 to start)
docker compose run --rm keygen ed25519

# Start both roles
docker compose up -d

# Verify both are up
docker compose ps
```

Expected:
```
NAME         STATUS
ssh-server   Up
ssh-client   Up
```

The `keygen` step mints two key pairs and installs them: an **Ed25519 host key** for the server plus a **user key** whose public half becomes the server's sole `authorized_keys` entry, and a `known_hosts` on the client that **pins** the server's host key (so host verification actually checks the key rather than trusting on first use). We start with classical **Ed25519** so the *authentication* half has a clean classical baseline; [Exercise 3](#exercise-3-post-quantum-authentication-composite-ml-dsa) reissues these as composite `mldsa44-ed25519` with a single command.

---

### Exercise 1: Post-quantum key exchange

We start the server's `sshd`, then connect and read what the handshake negotiated.

**Step 1: Start the SSH server**

Open a shell on the server and start `sshd` in the foreground (`-D` foreground, `-e` log to stderr). Leave it running:

```bash
docker exec -it ssh-server bash
/usr/local/sbin/sshd -D -e -f /cfg/sshd_config
```

It loads the host key and waits for a client. Open a **second terminal** for the client.

**Step 2: See the key exchange**

```bash
docker exec -it ssh-client bash
ssh -F /cfg/ssh_config -v ssh-server 'echo AUTH_OK; uname -n'
```

`-F /cfg/ssh_config` uses the lab's client config (which sets the user, key, and pinned `known_hosts`), and `-v` prints the negotiated algorithms. The lines that matter:

```
debug1: kex: algorithm: mlkem768x25519-sha256
debug1: kex: host key algorithm: ssh-ed25519
debug1: kex: server->client cipher: chacha20-poly1305@openssh.com MAC: <implicit> compression: none
debug1: Server host key: ssh-ed25519 SHA256:...
debug1: Host 'ssh-server' is known and matches the ED25519 host key.
Authenticated to ssh-server ([172.23.0.2]:22) using "publickey".
AUTH_OK
```

Three things just happened, and they map exactly onto the two pillars:

- **`kex: algorithm: mlkem768x25519-sha256`**: the key exchange is **hybrid ML-KEM, chosen by default**. Nothing in the config asked for it; OpenSSH 10.4 prefers it automatically. This is the whole "SSH did it first, and for free" point, in one line.
- **`host key algorithm: ssh-ed25519`** and the `publickey` authentication: the *authentication* half is still **classical Ed25519** on both ends (the server's host key and your user key). That is the baseline we upgrade in Exercise 3.
- **`chacha20-poly1305`**: the session cipher, symmetric and already quantum-safe.

Confirm which key exchanges this OpenSSH knows about:

```bash
ssh -Q kex | grep -iE 'mlkem|sntrup|curve25519'
```

```
curve25519-sha256
curve25519-sha256@libssh.org
sntrup761x25519-sha512
sntrup761x25519-sha512@openssh.com
mlkem768x25519-sha256
```

Both post-quantum families are present: the pre-standard `sntrup761x25519-sha512` (the one OpenSSH shipped first, back in 2022) and the NIST-standard `mlkem768x25519-sha256` (today's default). Leave `sshd` running in terminal 1 for the next exercise.

---

### Exercise 2: On the wire

`-v` saying `mlkem768x25519-sha256` is convincing, but let's prove it in the bytes, because the algorithm negotiation happens in **cleartext** before any encryption. Then we'll do the more interesting thing: watch what happens against a server that can't do post-quantum.

**Step 1: Capture a handshake**

The capture and the handshake happen in two shells, and the one rule is that the handshake must run *while* the capture is live. In the **server** shell, stop the foreground `sshd` from Exercise 1 (Ctrl-C), then start a capture and `sshd` in the background:

```bash
# server container
tcpdump -i any -w /tmp/hybrid.pcap -U 'tcp port 22' &
TCPDUMP_PID=$!
/usr/local/sbin/sshd -D -e -f /cfg/sshd_config > /tmp/sshd.log 2>&1 &
```

In the **client** shell, run one handshake:

```bash
# client container
ssh -F /cfg/ssh_config ssh-server 'true'
```

Back in the **server** shell, stop the capture:

```bash
kill $TCPDUMP_PID
```

**Step 2: Decode the key exchanges**

The image has `tshark`, which dissects the SSH transport. The `KEXINIT` message (`ssh.message_code == 20`) carries the algorithm name-lists in the clear. Look at what was offered:

```bash
# server container
tshark -r /tmp/hybrid.pcap -Y 'ssh.message_code==20' -V 2>/dev/null | grep -m1 'kex_algorithms string'
```

```
kex_algorithms string: mlkem768x25519-sha256,sntrup761x25519-sha512,sntrup761x25519-sha512@openssh.com,curve25519-sha256,...
```

**`mlkem768x25519-sha256` is offered first**, so it is what both sides select (SSH picks the first algorithm the client lists that the server also supports). The hybrid ML-KEM key exchange is right there in the cleartext handshake.

**Step 3: Measure ML-KEM's size cost**

Now the fun part. Capture a **classical** handshake alongside the hybrid one and compare the key-exchange packets. Force the client to classical X25519 and capture into a second file (same three-shell flow):

```bash
# server container: fresh capture
tcpdump -i any -w /tmp/classical.pcap -U 'tcp port 22' &
TCPDUMP_PID=$!
```

```bash
# client container: force classical key exchange
ssh -F /cfg/ssh_config -o KexAlgorithms=curve25519-sha256 ssh-server 'true'
```

```bash
# server container
kill $TCPDUMP_PID
```

The two key-exchange messages are `SSH_MSG_KEX_ECDH_INIT` (`ssh.message_code == 30`, the client's key share) and `SSH_MSG_KEX_ECDH_REPLY` (`ssh.message_code == 31`, the server's). Compare their sizes:

```bash
# server container
for f in classical hybrid; do
  echo "-- $f --"
  tshark -r /tmp/$f.pcap -Y 'ssh.message_code==30 || ssh.message_code==31' \
    -T fields -e ssh.message_code -e frame.len 2>/dev/null
done
```

```
-- classical --
30    120
31,21 628
-- hybrid --
30    1304
31,21 1716
```

(The reply reads `31,21` because the server packs `SSH_MSG_KEX_ECDH_REPLY` (31) and `SSH_MSG_NEWKEYS` (21) into one frame; the `frame.len` is what we care about.)

There is the entire cost of post-quantum key exchange, laid out in four numbers:

- The **client's** message (30) grows from **120 B to 1304 B**: a **+1184 B** jump, which is exactly the ML-KEM-768 encapsulation key from the table earlier.
- The **server's** reply (31) grows from **628 B to 1716 B**: a **+1088 B** jump, which is exactly the ML-KEM-768 ciphertext.

And crucially, it is still **one init and one reply**. Post-quantum SSH adds **no extra round trip** (unlike IKEv2's `IKE_INTERMEDIATE`); ML-KEM just rides in the messages that were already there. The whole price is those extra ~2.3 KB.

**Step 4: Watch a downgrade**

What happens when your modern client meets an old server that can't do post-quantum? Simulate one by restarting `sshd` offering **only classical** key exchange. In the **server** shell:

```bash
# server container
pkill sshd; sleep 1
/usr/local/sbin/sshd -D -e -f /cfg/sshd_config -o KexAlgorithms=curve25519-sha256 > /tmp/sshd.log 2>&1 &
```

Now connect normally from the **client** (its config still offers the hybrid; we change nothing on the client):

```bash
# client container
ssh -F /cfg/ssh_config ssh-server 'true'
```

```
** WARNING: connection is not using a post-quantum key exchange algorithm.
** This session may be vulnerable to "store now, decrypt later" attacks.
** The server may need to be upgraded. See https://openssh.com/pq.html
```

The connection **still succeeds** (both ends share `curve25519-sha256`), but OpenSSH 10.4 **tells you** the session dropped to classical crypto. This is the sharp contrast with the MACsec lab, where the equivalent downgrade was **silent** and still reported success. Here the fallback is loud: you cannot lose post-quantum key exchange to an old peer without being warned.

Restore the post-quantum-capable server before moving on (stop the classical one, then start the normal server again in the background so the next capture can share this shell):

```bash
# server container
pkill sshd; sleep 1
/usr/local/sbin/sshd -D -e -f /cfg/sshd_config > /tmp/sshd.log 2>&1 &
```

#### What we've seen

- The key exchange negotiates **hybrid `mlkem768x25519-sha256` by default**, proven in the cleartext `KEXINIT`.
- ML-KEM rides in the **same two messages** as a classical exchange (client `+1184 B`, server `+1088 B`), with **no extra round trip**.
- A downgrade to classical crypto is **loud**: modern OpenSSH warns when a connection isn't post-quantum, the opposite of MACsec's silent fallback.

---

### Exercise 3: Post-quantum authentication

The key exchange is done, and it was the easy half. Now the *identity* proof. So far both the server's **host key** and your **user key** have been classical **Ed25519**, exactly what Shor's algorithm forges. We switch both to the composite **`mldsa44-ed25519`** type new in OpenSSH 10.4.

**Step 1: Reissue the keys as composite ML-DSA-44**

`docker compose` is a **host** command, so run this in a **third terminal** on the host, from the `ssh/` directory. It reruns the same `keygen` helper, this time asking for the composite type:

```bash
# third terminal, on the host, from the ssh/ directory
docker compose run --rm keygen mldsa44-ed25519
```

This regenerates the server host key, the user key, `authorized_keys`, and the client's pinned `known_hosts`, all as `mldsa44-ed25519`. Confirm the type:

```bash
# client container
ssh-keygen -l -f /cfg/id_key.pub
```

```
256 SHA256:... labuser (mldsa44-ed25519) (MLDSA44-ED25519)
```

**Step 2: Turn the composite type on**

The composite signature is experimental, so OpenSSH will not offer or accept it unless you name it explicitly in `HostKeyAlgorithms` (server's host key) and `PubkeyAcceptedAlgorithms` (user key). The exact wire name is `ssh-mldsa44-ed25519@openssh.com`:

```bash
# client container: list the exact names
ssh -Q HostKeyAlgorithms | grep mldsa
```

```
ssh-mldsa44-ed25519@openssh.com
ssh-mldsa44-ed25519-cert-v01@openssh.com
```

Notice what is *not* there: no `ssh-mldsa44`, `ssh-mldsa65`, or `ssh-mldsa87`. Mainline OpenSSH ships **only the composite** type, no *pure* ML-DSA yet; that is still working its way through the IETF.

Restart the server offering the composite host key and accepting the composite user key. In the **server** shell (backgrounded again so the capture in Step 4 can share this shell):

```bash
# server container
pkill sshd; sleep 1
ALG=ssh-mldsa44-ed25519@openssh.com
/usr/local/sbin/sshd -D -e -f /cfg/sshd_config -o HostKeyAlgorithms=$ALG -o PubkeyAcceptedAlgorithms=$ALG > /tmp/sshd.log 2>&1 &
```

**Step 3: Prove everything is post-quantum**

From the **client**, ask for the composite type on both the host key and your user key:

```bash
# client container
ALG=ssh-mldsa44-ed25519@openssh.com
ssh -F /cfg/ssh_config -o HostKeyAlgorithms=$ALG -o PubkeyAcceptedAlgorithms=$ALG -v ssh-server 'echo PQ_AUTH_OK' \
  | grep -iE 'kex: algorithm|host key algorithm|Server accepts key|Authenticated to|PQ_AUTH_OK'
```

```
debug1: kex: algorithm: mlkem768x25519-sha256
debug1: kex: host key algorithm: ssh-mldsa44-ed25519@openssh.com
debug1: Server accepts key: /cfg/id_key MLDSA44-ED25519 SHA256:... explicit
Authenticated to ssh-server ([172.23.0.2]:22) using "publickey".
PQ_AUTH_OK
```

That is the full picture, both pillars post-quantum at once:

- **`kex: algorithm: mlkem768x25519-sha256`**: the key exchange is still hybrid ML-KEM (it never depended on the key type).
- **`host key algorithm: ssh-mldsa44-ed25519@openssh.com`**: the **server** authenticated with a composite Ed25519+ML-DSA-44 host key, verified against the pinned `known_hosts`.
- **`Server accepts key: MLDSA44-ED25519`**: **you** authenticated with a composite Ed25519+ML-DSA-44 user key.

SSH is now hybrid/composite in *both* halves: X25519+ML-KEM for secrecy, Ed25519+ML-DSA-44 for identity.

**Step 4: See the size on the wire**

Unlike your *user* auth (which happens after encryption), the server's **host key and its signature are sent in the clear**, inside the key-exchange reply, so we can measure them directly. Capture a composite handshake:

```bash
# server container
tcpdump -i any -w /tmp/comp.pcap -U 'tcp port 22' &
TCPDUMP_PID=$!
```

```bash
# client container
ALG=ssh-mldsa44-ed25519@openssh.com
ssh -F /cfg/ssh_config -o HostKeyAlgorithms=$ALG -o PubkeyAcceptedAlgorithms=$ALG ssh-server 'true'
```

```bash
# server container
kill $TCPDUMP_PID
tshark -r /tmp/comp.pcap -V 2>/dev/null | grep -iE 'Host key length|Host signature length|Host key type:'
```

```
Host key length: 1383
Host key type: ssh-mldsa44-ed25519@openssh.com
Host signature length: 2523
```

There is the composite host key (**1383 B**) and its signature (**2523 B**) sitting in the cleartext handshake, next to the **51 B** key and **83 B** signature the Ed25519 baseline used. That size jump is the whole story of post-quantum authentication, and it pushes the key-exchange reply packet from **1716 B** (hybrid KEX + Ed25519 host key) to **5308 B** (hybrid KEX + composite host key). In SSH, over a fast TCP connection, a few extra kilobytes per connection is negligible, but on constrained links, or anywhere host keys and signatures are stored and shipped in bulk (CAs, `known_hosts` fleets, certificate bundles), that ~27-30x growth is the thing to plan for.

#### What we've seen

- SSH authentication with a **composite Ed25519+ML-DSA-44** key works end to end, for **both** the server host key and the user key, over a real OpenSSH connection.
- It is **opt-in and experimental**: off by default and only the *composite* type exists in mainline (no pure ML-DSA yet).
- The cost is **size**: ~27-30x larger keys and signatures, visible in the cleartext host key on the wire. 

---

### Cleanup

`docker compose` is a **host** command, so run this from the **third terminal** on the host, from the `ssh/` directory:

```bash
docker compose down
# Remove generated keys (written to host via bind mounts; include private keys)
rm -f config/server/ssh_host_key config/server/ssh_host_key.pub config/server/authorized_keys \
      config/client/id_key config/client/id_key.pub config/client/known_hosts
```

---

That is it. You watched a real SSH handshake negotiate hybrid ML-KEM key exchange with **zero configuration**, proved it in the cleartext bytes and measured its exact size cost, saw a downgrade get caught **out loud**, and then turned on a brand-new composite ML-DSA-44 signature to make **both** the server and yourself authenticate post-quantum. Key exchange on by default, authentication on the frontier: that split is the whole state of post-quantum SSH today. Well done!
