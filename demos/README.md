# PQC demos: containers first, then Cisco routers

What does post-quantum crypto look like when you actually turn it on? Each notebook here
answers that for one protocol, in three acts:

- **Act 1:** learn it in containers, on your laptop. Nothing to break.
- **Act 2:** the same thing by hand on IOS XE 26.2 routers. The notebook gives you each
  command to paste at the CLI, then checks the result.
- **Act 3:** the same thing on every router at once, with Ansible.

Every step tells you what to run and what you should see, then *checks* it: expected vs
measured, right in the notebook.

| Demo | What you'll see | What it needs |
|---|---|---|
| [IPsec](ipsec_demo/) | ML-KEM in IKEv2, ML-DSA certificates, bytes on the wire | An R1, R2, R3 underlay |
| [MACsec](macsec_demo/) | MACsec with a key you type, then EAP-TLS with hybrid ML-KEM | A direct R1 to R2 cable |
| [TLS](tls_demo/) | Hybrid PQC on the router's HTTPS server | Routers you can SSH to |
| [SSH](ssh_demo/) | Hybrid PQC between your laptop and a router | Routers you can SSH to |
| [Containers](containers_demo/) | all four protocols, but just in containers | Docker only |

Do the steps below **once**. They're shared, so once one demo works, the others only add
what their own README lists. Everything you do on each run lives in the notebooks.

## 1. Set up your laptop

Every act runs in the notebook venv. Create it from the repo root:

```bash
cd demos
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd -
```

On top of that, **Act 1** adds Docker running, with docker compose v2. **Acts 2 and 3**
add the Ansible venv. Create it once, also from the repo root:

```bash
cd deploy/c8000/automation
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/ansible-galaxy collection install -r requirements.yml
cd -
```

No `source` this time. The notebook calls that venv on its own for every router check and
playbook, so your shell stays in the notebook venv.

The SSH and TLS demos ask your laptop's own client what it negotiates, so check those too:

```bash
ssh -Q kex | grep mlkem     # OpenSSH 9.9+: prints mlkem768x25519-sha256
openssl version             # 3.5 or newer (TLS, and IPsec's certificates)
```

## 2. Prepare the routers

Three routers on IOS XE 26.2 with the _Advantage_ license, reachable over SSH from your
laptop. [On the routers](../deploy/c8000/automation/README.md#on-the-routers) has the
details.

That's it for now. NETCONF, which Act 3 uses, gets turned on by each notebook's
`bootstrap.yml` cell.

<a id="your-lab-file"></a>
<a id="tell-the-notebook-and-the-playbooks"></a>

## 3. Describe your lab in one file

```bash
cd demos/common
cp lab.example.yml lab.yml
```

Edit `lab.yml`:

- **Every demo:** the routers' management addresses, the login, and the throwaway keys.
- **IPsec and MACsec:** your interfaces and addresses too. The comments in the file explain each key.

`lab.yml` is **gitignored**, so never commit it. `lab.example.yml` is tracked, so never put
anything real in *that*. The notebooks pass `lab.yml` to every router cell and playbook,
and they never print the credentials.

<a id="build-your-lab"></a>

## 4. Build the underlay (IPsec and MACsec only)

Why do these two care about your addresses? Because they end up in config: the IPsec
keyring, tunnel destination and certificate SANs, and the MACsec demo's certificate
authority on R2.

- **The underlay:** R1 and R3 reach each other through R2, so topology is R1-R2-R3. Any interfaces, any
  addresses, static routes or a routing protocol. If you check [`deploy/c8000/README.md`](../deploy/c8000/README.md),
  "Set up the underlay connectivity", has a worked example on SVIs. Build it once and
  leave it in place between runs.
- **MACsec:** a direct cable between R1 and R2, which can be the same wire the underlay
  runs on. Routed port or switchport changes the config:
  [the MACsec link](macsec_demo/README.md#the-macsec-link).
- **Clocks:** use NTP or `clock set`, but make sure `show clock` has no leading `*`. The certificate
  steps reject an unset clock. MACsec also needs R1 and R2 to agree with each other.

<details>
<summary><b>Routed-only ports?</b> IOS refuses the SVI layout with <code>% ... is not a switchable port</code>. Here's one that works.</summary>

R1 on a routed port, R2 keeping its SVIs, R3 on a tagged subinterface. The addresses are
the ones from the repo, replace those with yours. The topology section of `common/lab.example.yml` describes exactly
this layout, MACsec link included (R1 `Fi0/0/0` to R2 `Tw0/0/0`).

```
! R1
interface FiveGigabitEthernet0/0/0
 ip address 10.0.12.1 255.255.255.0
 no shutdown
ip route 10.0.23.0 255.255.255.0 10.0.12.2

! R2
vlan 12
vlan 23
interface TwoGigabitEthernet0/0/0
 switchport access vlan 12
 switchport mode access
interface Vlan12
 ip address 10.0.12.2 255.255.255.0
 no shutdown
interface Vlan23
 ip address 10.0.23.1 255.255.255.0
 no shutdown
interface TwoGigabitEthernet0/0/3
 switchport mode trunk
 switchport trunk allowed vlan 23

! R3
interface GigabitEthernet0/0/3.23
 encapsulation dot1Q 23
 ip address 10.0.23.2 255.255.255.0
ip route 10.0.12.0 255.255.255.0 10.0.23.1
```

Why a trunk and a subinterface on R2 to R3? So that wire keeps carrying whatever it
already carries untagged (make that VLAN the trunk's native one), with VLAN 23 tagged
beside it. On a dedicated wire, an access port on R2 and a routed port on R3 work just
as well.

</details>

## 5. Open a demo

From the repo root, with the IPsec demo as the example:

```bash
source demos/.venv/bin/activate
jupyter lab demos/ipsec_demo/ipsec_demo.ipynb
```

Swap the notebook path for the demo you want, then run its **"Before each run"** section
first. Its readiness
board and router checks tell you whether anything above still needs work, and where the
fix is.

No Docker? Skip Act 1 and jump to the **"Starting at Act 2?"** cell. It loads its own
helpers and puts the routers in that demo's starting state.

Just want to read? GitHub renders the notebooks too.

## Reference

<a id="run-a-playbook-from-a-terminal"></a>

**Running a playbook from a terminal.** The notebooks pass your `lab.yml` for you. In a
terminal, from `deploy/c8000/automation`, pass it yourself, or the playbooks use the
repo's values instead of yours:

```bash
ansible-playbook ipsec-baseline.yml -e @../../../demos/common/lab.yml
```

Tired of typing that flag? Link your file into the automation's `group_vars`, once:

```bash
ln -s ../../../../../demos/common/lab.yml deploy/c8000/automation/group_vars/all/zz-lab.yml
```

Why the `zz-`? Ansible loads that folder in alphabetical order and the last file wins, so
your file has to sort after `topology.yml`. With the link in place, a plain
`ansible-playbook ipsec-baseline.yml` uses your values. Then move everything from the old
`group_vars/all/lab.yml` into your file and delete it, so you keep one file only.

**How `lab.yml` combines with the repo's files.** It goes in as `-e`, so every key in it
beats `deploy/c8000/automation/group_vars/all/`. Keys you leave out come from there
(`lab.yml`, `topology.yml`), so an existing `group_vars/all/lab.yml` keeps working. One
catch: a key you *do* set replaces the repo's value whole. Set `lab_mgmt` with only `r1`
in it, and `r2` and `r3` are gone. Each notebook's readiness board shows which file
every setting came from.

**Changing the notebooks.** Each one is generated by the `build_notebook.py` next to it
(`python3 demos/tls_demo/build_notebook.py`), and the Act 1 cells are copied from
`containers_demo/PQC_DEMOS.ipynb` at build time, so regenerate after you change that
one. Edited a notebook by hand? Then that notebook is the file to change from now on.
The builder notices and refuses to overwrite it (`--force` throws your edits away). The
IPsec notebook works this way.

| File | What it holds |
|---|---|
| `containers_demo/pqc_demo.py` | containers, panels, the Act 1 ladders |
| `common/lab.py` | router handles, checks, playbooks, container labs, laptop probes |
| `common/nbbuild.py` | the notebook builder and the blocks every demo shares |
| `<demo>/<proto>_lab.py` | one demo's `labs`, `check()` and `setup()` |
