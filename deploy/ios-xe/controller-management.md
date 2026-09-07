# Can a Cisco controller manage our PQC routers without destroying the config?

Research notes, web sources only, gathered 30 Aug 2026. Everything here is cited. Where
public information is thin or absent, it says so explicitly rather than guessing.

**Our situation:** three C8235-G2 running IOS XE 26.02.01, hand-configured with IKEv2
ML-KEM-768 hybrid, RFC 8784 PPK, ML-DSA-65 certificate auth, MACsec with EAP-TLS + ML-KEM,
SSH `mlkem768x25519` KEX, and TLS management.

**The short version:** Meraki dashboard in *Configuration Source: Device* mode is the only
option that doesn't wipe the box, and even that mode pushes its own config onto the device.
Catalyst Center supports the C8235-G2 but does *not* list IOS XE 26.2.x as compatible. No
Cisco management product exposes PQC crypto configuration or a crypto posture view today.
Cisco IQ is the single exception, and it assesses rather than configures.

---

## Question 1: Meraki "Configuration source: device" for routers

### 1a. Is there a device-config-source (non-destructive) mode for the C8235-G2?

**Yes.** Both modes exist for the IOS XE Secure Routers, not just for Catalyst switches.

The onboarding guide has a dedicated *"Configuration Source: Device"* section listing
C8455-G2, C8355-G2, and C8235-G2 as the supported Generation-2 Secure Routers, plus an
"Additional Configuration (Configuration Source: Device Only)" prerequisites block
([onboarding guide](https://documentation.meraki.com/SASE_and_SD-WAN/MX/Operate_and_Maintain/How-Tos/Onboarding_IOS_XE_Based_Secure_Routers_into_Dashboard)).
The feature overview page separately documents "Cloud CLI (write)" as "available for devices
operating in Configuration Source: Device mode"
([intro guide](https://documentation.meraki.com/SASE_and_SD-WAN/MX/Operate_and_Maintain/How-Tos/Introduction_to_IOS_XE_Based_Secure_Routers_Managed_by_Dashboard)).
The Cisco at-a-glance for this solution frames it as a customer choice: "Whether you require
total 'cloud mode' simplicity or 'device mode' with granular Command-Line Interface (CLI)
control"
([AAG, updated 11 Jun 2026](https://www.cisco.com/c/en/us/products/collateral/networking/sdwan-routers/cloud-management-ios-xe-secure-routers-aag.html)).

Two caveats worth flagging.

*Maturity.* At Cisco Live EMEA 2026, the support matrix slide titled "Support matrix for
Configuration source: Device - Routing" listed C8235-G2 / C8355-G2 / C8455-G2 at IOS XE
26.1.1 with the annotation **"Documentation pending GA"**. The same deck listed Cloud mode
for "Cisco Secure Router 8000" as a **"roadmap item and only selected models"**
([BRKENS-2357](https://www.ciscolive.com/c/dam/r/ciscolive/emea/docs/2026/pdf/BRKENS-2357.pdf)).
The Meraki docs have since filled in, so device mode looks shipped, but I found no explicit
GA announcement naming it.

*"Monitoring only" is not a separate router mode.* Meraki's older *Cloud Monitoring for
Catalyst* product is Catalyst 9000 switches only, is closed to new devices, and ends service
31 Mar 2026
([Cloud Monitoring for Catalyst](https://documentation.meraki.com/Switching/Cloud_Monitoring_for_Catalyst)).
It was renamed into "Configuration Source: Device"
([Cloud Management with IOS XE overview](https://documentation.meraki.com/Switching/Cloud_Management_with_IOS_XE/Product_Information/Overviews_and_Datasheets/Cloud_Management_with_IOS_XE_Overview)).
So device mode *is* the monitoring mode. Interestingly, `show cloud-mgmt` on a C8235-G2 in
the onboarding doc reports `Mode: C8K-C [Monitoring]`, versus `C8K-M` in the cloud-mode
example in the ZTP doc. Neither string is documented anywhere I could find. **Unconfirmed
what those mode codes mean.**

### 1b. What does onboarding require and destroy?

| Claim | Cloud mode | Device mode |
| --- | --- | --- |
| Factory reset required to onboard | No, but all config is erased anyway | **No** |
| ROMMON autonomous mode required | Yes | Yes |
| Existing config replaced | **Yes, erased** | **No**, but dashboard adds its own config |
| Console / SSH disabled after onboarding | **Yes** | **No** |
| Factory reset required to *leave* the mode | Yes (`factory-reset all`) | No |

Evidence, all from the [onboarding guide](https://documentation.meraki.com/SASE_and_SD-WAN/MX/Operate_and_Maintain/How-Tos/Onboarding_IOS_XE_Based_Secure_Routers_into_Dashboard)
unless noted:

- **Autonomous mode is required for both.** "Onboarding an IOS XE based Secure Router into
  Dashboard requires the device to be set to 'Autonomous Mode'", via `controller-mode
  disable`. Good news for us: PQC IKEv2 is autonomous-mode-only anyway (see Q2).
- **Cloud mode erases everything.** "If it is intended for the device to be onboarded into
  Configuration Source: Cloud, note that all configuration is erased during the onboarding
  process." To keep even a static WAN IP you have to set it via the Local Status Page.
- **Cloud mode kills local access.** "Once a device has been onboarded into Configuration
  Source: Cloud mode, the console and local CLI/SSH is disabled." Recovery needs a
  TAC-issued consent token, which grants only `show`, `copy`, `delete`, `write erase`,
  `reload` plus a tiny recovery parser
  ([ZTP / UAC guide](https://documentation.meraki.com/SASE_and_SD-WAN/MX/Operate_and_Maintain/How-Tos/Uplink_Auto_Configuration_%26_Configuration_Updater_on_IOS_XE_Based_Secure_Routers)).
- **Leaving cloud mode nukes the images too.** "During the factory reset process all images
  shall be deleted from the device. USB or alternative image boot options will be required
  for full service restoration."
- **Device mode is additive, not passive.** Prerequisites are `aaa new-model`, `aaa
  authentication login default local`, `aaa authorization exec default local`, and a
  privilege-15 user whose credentials you hand to the dashboard. Nothing says existing
  config is touched.
- **Device -> Cloud is a one-way door.** "After the device has been re-added to a dashboard
  network in Device Configuration: Cloud mode ... operating mode conversion will begin,
  initiating a factory reset procedure and restricting the console to read-only mode."
- **Removal costs you config either way.** "When removing a device from a network L3 & L2
  Configurations are disabled and will need to be reapplied manually", and "When removing a
  device from a network a factory reset will be required in order to re-onboard the device."
- **Firmware floor is 26.1.1+**, and INSTALL mode (`.conf`) is required; BUNDLE mode (`.bin`)
  is not supported. Our 26.02.01 clears the floor.

**The catch nobody advertises:** in device mode the dashboard writes config to the box. The
switch-side page (no router equivalent published) lists what gets added: `netconf-yang`,
`ip ssh version 2`, `ip ssh port 2222 rotary 55`, `ip ssh pubkey-chain`, `ip ssh server
algorithm authentication publickey password keyboard`, four dedicated VTY lines,
`MERAKI_MGMT_*` ACLs, `ip http secure-server`, SNMP traps, NetFlow, device-tracking, and
`meraki-user` / `meraki-tdluser` / `meraki-cli-ro` / `meraki-cli-rw` local accounts
([required modifications](https://documentation.meraki.com/Switching/Cloud_Management_with_IOS_XE/Troubleshooting_and_Support/Cloud_Management_with_Device_Configuration_Required_Modifications),
[Cloud CLI guide](https://documentation.meraki.com/Switching/Cloud_Management_with_IOS_XE/Operate_and_Maintain/Cloud_CLI_for_Cloud-Managed_IOS_XE_Switches)).
The Cisco Live deck confirms the same list for the router walkthrough: "NETCONF, LINE VTY,
SSH access on port 2222, Device Tracking, Local users meraki-cli-ro meraki-cli-rw
meraki-user ... Configuration changes are pushed by the Cloud to the device"
([BRKENS-2357](https://www.ciscolive.com/c/dam/r/ciscolive/emea/docs/2026/pdf/BRKENS-2357.pdf)).
And if you remove those lines, the dashboard raises a configuration sync alert and re-applies
them.

**Risk for us, and it is unconfirmed:** the dashboard's Cloud CLI reaches the device over
SSH on port 2222 using publickey auth. Our SSH is restricted to `mlkem768x25519` KEX. **No
Cisco document states which KEX algorithms the Meraki cloud SSH client negotiates.** If it
can't do ML-KEM hybrid KEX, device-mode Cloud CLI breaks, or onboarding forces `ip ssh`
settings that widen our KEX policy. This needs a lab test; it is not answerable from public
docs.

### 1c. Are NETCONF (830) and RESTCONF still reachable? Is direct SSH possible?

**Device mode: SSH yes, NETCONF partially, RESTCONF unknown.**

- Direct SSH and console stay enabled. Local CLI access is explicitly retained; only cloud
  mode disables it.
- The dashboard *enables* `netconf-yang` itself (see the required-modifications list). The
  published ACLs (`MERAKI_MGMT_IP_IN` / `_OUT`, and the IPv6 pair permitting only tcp/2222
  inside `FD0A:9B09:1F7:1::/64`) are applied to VTY lines 32-35 and to `ip http`, not to port
  830. So NETCONF should stay reachable for you, **but no Cisco doc states this for routers
  and no doc confirms port 830 remains open to non-Meraki clients.** Treat as probable, not
  confirmed.
- **RESTCONF is never mentioned** in any Meraki cloud-management document I read. Unknown.

**Cloud mode: no direct SSH, and NETCONF is almost certainly unusable to you.** "When
operating in configuration source: cloud local CLI/SSH is not permitted." The dashboard's own
config-fetch pipeline is NETCONF-shaped (the staged `get_config.conf` on bootflash is a
NETCONF `<data>` document wrapping `Cisco-IOS-XE-native`), so NETCONF machinery is running
internally, but nothing suggests you get to talk to it
([ZTP / UAC guide](https://documentation.meraki.com/SASE_and_SD-WAN/MX/Operate_and_Maintain/How-Tos/Uplink_Auto_Configuration_%26_Configuration_Updater_on_IOS_XE_Based_Secure_Routers)).
**Not explicitly documented either way for routers.**

### 1d. Is there a Cloud CLI for these routers, and is it read/write?

**Yes, and it depends on the mode.**

| Mode | Read/show CLI | Read/write CLI |
| --- | --- | --- |
| Configuration Source: Device | Yes | **Yes** |
| Configuration Source: Cloud | Yes | No |

That table is from the [Cloud CLI guide](https://documentation.meraki.com/Switching/Cloud_Management_with_IOS_XE/Operate_and_Maintain/Cloud_CLI_for_Cloud-Managed_IOS_XE_Switches),
and the router intro page states the same split. Reach it at Security & SD-WAN > Appliance
Status > Cloud CLI. Write mode needs a Full Access org or network admin who re-authenticates
their dashboard password, plus Early Access opt-in. Everything is session-logged and
archived to the Organization change log.

Restrictions that matter: `parser`, `archive`, `ntp`, `timezone`, `clock`, and `guestshell`
are blocked from Cloud CLI config mode. `show tech-support` and `show memory` are excluded.
SSH/telnet out to other devices is blocked. Config mode silently downgrades to read-only if
the device clock is out of sync with the dashboard or if the archive-log / telemetry
subscription 10002 config is missing.

**Documentation gap:** the Cloud CLI guide says it "is supported only on cloud-managed IOS XE
switches and wireless controllers" and lists no router models, while the router intro page
says Cloud CLI works on routers. The two pages contradict each other. A third-party lab
writeup reports Cloud CLI working on a C8235-G2 in device mode
([CandM-network blog, Japanese](https://candm-network.hatenadiary.jp/entry/Meraki-C8K-ConfigDevice)),
but that is not a Cisco source.

### 1e. Does the dashboard expose any crypto configuration?

**No.** Not for IPsec/IKEv2 proposals, not cipher choices, not certificates.

The router intro page states plainly: "Features not explicitly listed here should be
considered to be not supported." The entire configuration surface is WAN uplink settings,
L2 interface, L3 interface, DHCP, NAT, static routing, AutoVPN, eBGP on LAN, and firmware
upgrade. There is no crypto, certificate, PKI, MACsec, or IKEv2 policy object anywhere in
that list.

**AutoVPN crypto is fixed, classical, and PSK-authenticated.** From the same page:

| Parameter | Value |
| --- | --- |
| IKE encryption | AES-GCM-256 |
| IKE PRF | SHA256 |
| IKE authentication | **Dashboard-derived PSK**, unique per endpoint pair |
| IKE DH group | **21** (ECP-521) |
| IKE lifetime | 8 hr |
| ESP | ESP-GCM-256, tunnel mode, replay window 1024 |
| IPsec PFS | **DH group 21** |
| IPsec lifetime | 4 hr |
| Ports | 500/4500, NAT-T forced |
| MTU | 1400, fragmentation enabled |

**There is no ML-KEM in Meraki AutoVPN, and no ML-DSA.** Group 21 is classical ECDH; auth is
a pre-shared key handed out by the dashboard, so our ML-DSA-65 certificate identity is
irrelevant to AutoVPN. None of it is tunable.

This flatly contradicts the marketing. The Cisco AAG for this exact solution promises
"quantum-safe enterprise-ready branches" and "Built-in post-quantum cryptography"
([AAG](https://www.cisco.com/c/en/us/products/collateral/networking/sdwan-routers/cloud-management-ios-xe-secure-routers-aag.html)),
while the technical page documents a classical-only SA. The Meraki 8000-series FAQ is more
honest about the MX-OS variants, listing "**Future** support for Post Quantum Cryptography
(PQC) at transport and boot level"
([8000 Series FAQ on documentation.meraki.com](https://documentation.meraki.com/SASE_and_SD-WAN/MX/Product_Information/Overviews_and_Datasheets/Cisco_8000_Series_Secure_Routers_Frequently_Asked_Questions_(FAQ))).
**Flagging this as a documentation conflict, not a resolved fact.**

One more constraint: an organization can have AutoVPN enabled for only one device type, MX
or IOS XE Secure Routers. Mixing requires a separate dashboard organization.

---

## Question 2: Catalyst Center and SD-WAN Manager

### Catalyst Center: is the C8235-G2 supported, and on what?

**Yes, supported. But not on IOS XE 26.2.x.**

The compatibility matrix page renders client-side, so the HTML looks empty; the real data
lives in `resources/data-min.json` behind
[the matrix](https://www.cisco.com/c/dam/en/us/td/docs/Website/enterprise/catalyst_center_compatibility_matrix/index.html)
(data payload version-stamped `2026-04-29`). Under Router > **"Cisco 8200 Series Secure
Router"**:

| Catalyst Center release | Compatible IOS XE | Recommended IOS XE |
| --- | --- | --- |
| 3.2.3 | **26.1.x**, 17.18.x | 17.18.3 |
| 3.2.2 | **26.1.x**, 17.18.x | 17.18.3 |
| 3.1.6 | **26.1.x**, 17.18.x | 17.18.3 |
| 3.1.5 | 17.18.x | 17.18.3 |
| 2.3.7.11, 2.3.7.10 | 17.18.x | 17.18.3 |

Platforms in that entry: C8235-E-G2, C8231-E-G2, **C8235-G2**, C8231-G2. Per-application
flags are Inventory Y, Topology Y, SWIM Y, PnP Y, Assurance Y, Patching (SMU) Y, IWAN Y,
SD-Access Y, Application Policy NA. Both DNA Essentials and DNA Advantage columns read "N".

Siblings check out too: C8355-G2 ("Cisco 8300 Series Secure Router") and C8455-G2 / C8475-G2
("Cisco 8400 Series Secure Router") are both listed at 26.1.x + 17.18.x in Catalyst Center
3.2.3.

**The blocker: IOS XE 26.2 appears nowhere in the matrix.** A search of the whole 4.7 MB
payload returns zero occurrences of the string "26.2"; the only 26.x values present are
`IOS XE 26.1.x` and `IOS XE 26.1.2`. So **our 26.02.01 is not a listed compatible release
for any Catalyst Center version** as of the 29 Apr 2026 data snapshot. That is an absence of
support rather than a documented incompatibility, and the matrix may simply lag. Worth
re-checking, and worth asking Cisco directly.

Corroborating (but weaker) evidence that Catalyst Center manages this family: the Cisco IQ
Supported Product List includes, under "Controller | Catalyst Center | Routers", the
"8100/8200/8300/8400/8500 Secure Routers"
([Cisco IQ supported products](https://www.cisco.com/c/en/us/support/docs/cx/cisco-iq/supported-product-list.html)),
and the 8000 Series FAQ lists Catalyst Center as a management option
([8000 Series Secure Routers FAQ](https://www.cisco.com/c/en/us/products/collateral/networking/sdwan-routers/8000-series-secure-routers-faq.html)).

### Can Catalyst Center push arbitrary Day-N CLI to these?

**Probably yes, but not confirmed for this platform.** The compatibility matrix has no
column for templates or provisioning, so it doesn't answer the question. Catalyst Center's
CLI-template capability is generic and Cisco Live material shows CLI templates as the
standard brownfield tool, including for appending config that Catalyst Center would otherwise
overwrite
([BRKOPS-1461](https://www.ciscolive.com/c/dam/r/ciscolive/emea/docs/2026/pdf/BRKOPS-1461.pdf)).
Note that in that same deck's support tiers, only "Supported" devices are "tested for all
applications"; Template Provisioning is listed even for "Limited" devices. Our platform is in
the main matrix, so it is "Supported".

**No IOS XE 26.x-specific Catalyst Center limitations were found.** I did not find any
document describing Catalyst Center behaviour with 26.x PQC config, ML-DSA trustpoints, or
the 26.1+ insecure-command blocking. Genuinely thin.

One 26.x behaviour to keep in mind regardless of controller: from 26.1.1 all "insecure CLI
commands are blocked by default", and upgrading with such commands present auto-inserts
`system mode insecure`
([Catalyst 8200/8300 26.1.x release notes](https://www.cisco.com/c/en/us/td/docs/routers/cloud_edge/c8300/rel_notes/26-x/release-notes-catalyst-8200-and-catalyst-8300-series-edge-platforms-release-26-1-x.html)).
Note that the Meraki-staged config on a cloud-mode C8235-G2 shows `<mode><insecure>true`,
which is a curious thing to see in a quantum-safe branch story.

### Catalyst SD-WAN Manager / "Catalyst Manager": C8235-G2 and PQC?

**Device support: yes.** The licensing guide lists C8231-G2 / **C8235-G2** / C8231-E-G2 /
C8235-E-G2 with license tags for all three modes: autonomous, **SD-Routing**, and SD-WAN
controller mode
([8000 Series Secure Routers Licensing](https://www.cisco.com/c/en/us/td/docs/routers/cloud_edge/licensing/8000-series-secure-routers-licensing.html)).
The 8200 data sheet gives minimum SD-WAN Manager 20.18.1 with IOS XE 17.18.1
([8200 data sheet](https://www.cisco.com/site/us/en/products/collateral/networking/sdwan-routers/8000-secure-routers/8200-series-secure-routers-ds.html)).
Caveat: classic feature templates do not cover the G2 secure routers; a Cisco employee in a
support thread states "Secure G2 routers will not be supported by Templates. You need to
convert your configuration to Config Groups"
([Cisco Community](https://community.cisco.com/t5/routing-and-sd-wan/c8375-e-g2-not-listed-when-creating-a-feature-template/td-p/5378862)).
That is a forum reply, not documentation.

**Can it push PQC crypto config? Yes via SD-Routing, no via SD-WAN controller mode.**

This is the decisive fact: "Starting release Cisco IOS XE 26.1 and later, Post-Quantum
Cryptography can be configured **only on these platforms in the autonomous mode**: C8100 /
C8200 / C8300 / C8400 / C8500 Series Secure Routers"
([PQC for IKEv2 config guide](https://www.cisco.com/c/en/us/td/docs/routers/ios-xe/security-vpn/security-vpn/m-pqc-ikev2.html)).
C8235-G2 is an 8200 Secure Router, so we qualify, but only in autonomous mode.

SD-Routing is exactly "Cisco Catalyst SD-WAN Manager to manage traditional (non-SD-WAN)
routing devices operating in **autonomous mode**", and it offers a **CLI Configuration
Group** for features with no Feature Parcel, where you load the device's running config,
edit, and deploy
([SD-Routing Solution Guide](https://www.cisco.com/c/en/us/td/docs/routers/sd-routing/sd-routing-solution-document.html)).
So arbitrary PQC CLI is deployable in principle.

Three things to weigh before doing that:

1. **It locks the CLI.** "After the device is successfully onboarded and initial
   configuration is complete, the device terminal locks and is no longer available for
   configuration purposes. All feature configurations must be performed using Cisco Catalyst
   SD-WAN Manager." Same again after a CLI Config Group deploy.
2. **No rollback for classic CLI.** SD-WAN Manager splits YANG from non-YANG commands and
   "does not support rollback Classic CLI commands." Much of our PQC config is likely to land
   in the classic pane.
3. **SD-WAN controller mode cannot do PQC data plane at all, and Cisco says so.** The Design
   Zone guide's "Strategic Note: PQC Roadmap for SD-WAN" says SD-WAN must first move "from
   its current model of controller-based key distribution" and that "**Future releases** will
   introduce native IKEv2 support for data plane tunnels", which is "a functional
   prerequisite" for ML-KEM
   ([Quantum-Ready Migration Guide](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/Quantum-Ready-Migration-Guide.html)).

Also note PQC restrictions that apply regardless of controller: not supported on GETVPN in
26.1; ML-KEM-1024 (1568 bytes) needs `crypto ikev2 fragmentation mtu 1400`; site-to-site and
FlexVPN phased migration is unsupported (both ends must upgrade together), only DMVPN can be
staged
([PQC for IKEv2 config guide](https://www.cisco.com/c/en/us/td/docs/routers/ios-xe/security-vpn/security-vpn/m-pqc-ikev2.html)).

---

## Question 3: Cisco's existing PQC posture and assessment tooling

### The Design Zone "Configuration Catalog" of PQC templates

**Found the guide. The catalog claim is essentially unverifiable from public sources.**

The document is the [Quantum-Ready Migration Guide](https://www.cisco.com/c/en/us/td/docs/solutions/CVD/Campus/Quantum-Ready-Migration-Guide.html)
(Design Zone, Campus CVD path, updated 6 May 2026). Under "Centralized orchestration: Cisco
SD-Routing" it claims, verbatim: "Cisco provides a pre-validated Configuration Catalog that
includes recommended templates for IPsec and MACsec PQC profiles. These templates are
pre-engineered to align with NIST and CNSA 2.0 standards."

What I can confirm:

- **The host product is real.** Configuration Catalog is a documented SD-WAN Manager feature
  from Manager 20.15.1 / IOS XE 17.15.1a. It is a cloud service hosted in the Cisco Catalyst
  SD-WAN Portal, browsed by label from Configuration > Configuration Catalog, and entries
  install as **read-only** configuration groups (copy one to edit it). It supports only
  configuration groups, policy groups, and topology groups, not templates
  ([Configuration Catalog reference, updated 21 Jun 2026](https://www.cisco.com/c/en/us/td/docs/routers/sdwan/configuration/config-groups/configuration-group-guide/config-catalog.html)).
- Cisco disclaims the contents: provided "as is", built from "industry best practices".

What I **cannot** confirm:

- **What is actually in the catalog.** Entries are fetched live from the portal with a Smart
  Account, so contents are not published. The Configuration Catalog documentation never
  mentions PQC, ML-KEM, IPsec, or MACsec profiles.
- **Whether PQC entries are GA.** No release note, feature history row, or announcement ties
  PQC profiles to the catalog.
- **Whether the templates are publicly available.** They are not. There is no public URL,
  and access requires SD-WAN Manager plus Smart Account credentials.
- The guide also asserts Catalyst Manager "Security Dashboards" showing "which tunnels are
  currently operating in a quantum-safe state (ML-KEM)" and "Crypto Audit Logs". **No SD-WAN
  Manager documentation I found describes either feature.** Treat both as CVD prose.

Note the naming mismatch too: the CVD says "Catalyst Manager (formerly vManage)" while all
current product documentation says "Cisco Catalyst SD-WAN Manager". "Catalyst Manager" is not
a documented product name.

### Cisco IQ "Quantum Ready Assessment"

**GA since July 2026. It assesses inventory, software, platform features, and encryption
usage. CBOM exists but is beta.**

The primary source is the [Cisco IQ Release Notes July 2026](https://www.cisco.com/c/en/us/support/docs/cx/cisco-iq/release-notes/cx226191-cisco-iq-release-notes-july-2026.html)
(published 23 Jul 2026), under Assessments Module > **Quantum Safe Infrastructure**:

> "The Assessments module now evaluates your network environment against emerging
> post-quantum cryptographic standards, identifying vulnerabilities to quantum-enabled
> threats. By analyzing encryption usages for potential gaps, findings are prioritized by
> risk level ... Quantum Safe Infrastructure Assessment includes IOS-XE (excluding ASR 1000
> and ISR 4000), IOS-XR, NX-OS, ACI, Cisco Secure Firewall Threat Defense (FTD), and Cisco
> Secure Firewall Adaptive Security Appliance (ASA). **The device cryptographic bill of
> materials (CBOM) feature is in beta.**"

So: **CBOM yes, but beta.** Assessment itself is a released feature, listed as GA.

- **Our platform is in scope.** IOS-XE is included and only ASR 1000 / ISR 4000 are excluded.
  The [Cisco IQ Supported Product List](https://www.cisco.com/c/en/us/support/docs/cx/cisco-iq/supported-product-list.html)
  lists "Quantum Safe Infra Assessment (excluding ASR and ISR 4000)" for IOS-XE and covers
  the "8100/8200/8300/8400/8500 Secure Routers" both as Direct Device and via the Catalyst
  Center controller.
- **What it evaluates:** three pillars, per the
  [Cisco IQ Getting Started Guide](https://www.cisco.com/c/en/us/support/docs/cx/cisco-iq/getting-started-guide/cx225778-cisco-iq-getting-started-guide.html):
  quantum-safe products, quantum-safe communication, and crypto agility, producing
  "severity-weighted conformance gaps" and hardware/software remediation priorities. The
  Design Zone guide adds that it analyses "trust anchors, secure boot, and secure storage"
  plus "cryptographic agility and communication protocols of the management, control, and
  data planes", and buckets each device into needs-hardware-refresh, needs-software-update,
  needs-configuration-change, or needs-feature-activation
  (that four-way split is also reported by
  [Fierce Network](https://www.fierce-network.com/cloud/cisco-iq-takes-aim-aging-infrastructure-and-quantum-risk)).
- **Does it read actual crypto config?** Partly. "Analyzing encryption usages" implies config
  inspection, and the getting-started guide mentions validating "RSA, Transport Layer
  Security, and cryptography readiness". **Whether it parses IKEv2 proposals, `pqc mlkem768`
  keywords, MACsec MKA, or ML-DSA trustpoints is not documented.** Unknown.
- **Careful with the CVD's claims.** The Design Zone guide lists Compliance Measurement,
  Configuration Drift Detection, and Predictive Analytics under a heading reading "**Future**
  capabilities of Cisco IQ will include", then describes them in the present tense. Roadmap,
  not shipped.
- Multi-standard support (select the applicable national standard: CNSA 2.0, EU, UK, Canada,
  Australia, Japan) is described in the CVD and the Fierce writeup, not in Cisco IQ product
  docs.

### Does Cloud Control, AI Canvas, Catalyst Center, or Meraki show crypto posture?

**No, for all four. Cisco IQ is the only place, and it reaches Cloud Control by being
embedded in it.**

- **Cisco Cloud Control / AI Canvas:** no PQC or crypto-posture feature is described in any
  primary source. Cloud Control went to Controlled Availability (US commercial) at Cisco Live
  US on 2 Jun 2026, unifying Meraki, Catalyst, Nexus, Security Cloud Control, Intersight,
  Splunk, and Webex Control Hub; AI Canvas is its workspace
  ([Cisco newsroom, 2 Jun 2026](https://newsroom.cisco.com/c/r/newsroom/en/us/a/y2026/m06/cisco-unveils-agentic-platform-for-operating-and-defending-critical-it-infrastructure.html),
  [Cloud Control blog](https://blogs.cisco.com/ai/cisco-cloud-control-the-secure-harness-for-the-agentic-era),
  [AI Canvas blog](https://blogs.cisco.com/ai/ai-canvas-controlled-availability)).
  The AI Canvas Board Library ships curated boards for "security posture reviews" and
  "compliance checks", but **nothing describes a cryptographic or quantum board**. The only
  connection is structural: "Cisco IQ, fully integrated into Cisco Cloud Control", so the
  Quantum Ready Assessment surfaces there.
- **Catalyst Center:** no crypto inventory, CBOM, or quantum posture view found in any
  release notes (3.1.3, 3.2.3), user guide, or the compatibility matrix. The July 2026 Cisco
  IQ notes mention Catalyst Center only as a source of "Catalyst Center-specific generic
  recommendation guidance" inside Cisco IQ findings, which is the opposite direction of flow.
- **Meraki dashboard:** nothing. No crypto view for the IOS XE Secure Routers, and as
  established in Q1e the AutoVPN crypto is fixed and classical.

### Roadmap and commitment statements

These are real and quotable, all from the [Cisco Live US 2026 announcement](https://newsroom.cisco.com/c/r/newsroom/en/us/a/y2026/m06/cisco-unveils-agentic-platform-for-operating-and-defending-critical-it-infrastructure.html)
(2 Jun 2026) and the accompanying
[Cisco blog](https://blogs.cisco.com/news/the-network-is-the-foundation-powering-the-agentic-ai-era):

- Quantum-safe communications across "the majority of Cisco's core portfolio **by December
  2026**".
- "Any newly introduced campus, branch and data center routers, switches, and firewall series
  will launch with **quantum-safe secure boot**."
- Quantum Ready Assessments via Cisco IQ, "Global availability planned for July 2026" (and
  the July release notes confirm it landed).
- A new "Quantum Resilience Framework" with two pillars: quantum-safe communications and
  quantum-safe products.

**No Cisco commitment specifically about PQC visibility in Cloud Control or AI Canvas was
found.** The quantum commitments attach to the portfolio and to Cisco IQ, not to Cloud
Control or AI Canvas as PQC-posture surfaces.

IOS XE roadmap detail from the Design Zone guide, useful for our version story: 26.1.1
brought ML-KEM for IPsec plus quantum-safe MACsec via ML-KEM-based key exchange; **26.2.1 is
described as "Hardened PQC Authentication"**, introducing quantum-safe signatures "(such as
ML-DSA)" for tunnel authentication plus secure boot hardening. That matches what we have
running.

---

## Where the public record is thin

Ranked by how much it would hurt us to be wrong:

1. **Which SSH KEX algorithms the Meraki cloud SSH client supports.** Our
   `mlkem768x25519`-only policy could break device-mode Cloud CLI, or onboarding could widen
   it. Undocumented. Test in a lab.
2. **Whether Catalyst Center works with IOS XE 26.2.x.** 26.2 is absent from the entire
   compatibility matrix. Absence, not a stated incompatibility. Ask Cisco.
3. **NETCONF port 830 and RESTCONF reachability after Meraki onboarding, for routers.** No
   router-specific required-modifications page exists; I extrapolated from the switch page
   and said so.
4. **What is actually inside the SD-WAN Manager Configuration Catalog.** The PQC IPsec and
   MACsec profiles are asserted in a CVD and appear in no product documentation. Contents are
   not public.
5. **Whether Cisco IQ inspects real crypto config or only versions and platform features.**
   The wording ("analyzing encryption usages") suggests config, but no doc says which
   constructs are parsed.
6. **Meraki AutoVPN "quantum-safe" marketing vs the documented classical SA.** Direct
   conflict between the Cisco AAG and the Meraki technical page. Unresolved.
7. **Whether Configuration Source: Device for routers is formally GA.** Cisco Live EMEA 2026
   said "Documentation pending GA"; documentation now exists; no GA announcement found.
8. **The `C8K-C [Monitoring]` vs `C8K-M` mode strings** in `show cloud-mgmt`. Undocumented.
9. **Catalyst Manager "Security Dashboards" and "Crypto Audit Logs"** for quantum-safe tunnel
   state. CVD prose only, no product documentation.
10. **Catalyst Center behaviour with 26.x PQC constructs** (ML-DSA trustpoints, `pqc` keywords,
    insecure-command blocking). Nothing found at all.

## Sources with a trust caveat

Everything above is Cisco-published except where noted. Two categories to treat as weaker:

- `learningnetwork.cisco.com` articles on the two configuration-source modes are
  Cisco-hosted but community-authored. Useful corroboration, not authoritative.
- The Japanese blog confirming Cloud CLI on a real C8235-G2 in device mode, and the Cisco
  Community thread about G2 feature-template support, are third-party or forum replies.

The Cisco Live PDFs (BRKENS-2357, BRKOPS-1461) are official Cisco conference material but
represent a point in time (Feb 2026) and include explicit roadmap disclaimers: "Not all these
platforms have been committed, released and/or are publicly available yet."
