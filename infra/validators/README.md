# Validator deployment profiles

The Adiri and mainnet profiles define independent one-node deployments in
`oprail-validator-adiri` and `oprail-validator-mainnet`. Each has its own network,
release channel and operations runner/environment names. These profiles are consumed by the validator-only deployment command and workflow.

## Preview locally

```bash
python3 scripts/validators/profile.py --network adiri
python3 scripts/validators/profile.py --network mainnet
```

The preview lists the release, execution address and advertised transaction RPC
URL still to supply. Release tags must match the network. Adiri uses the upstream
`tn-testnet` package and `adiri` build feature. Mainnet uses `tn-mainnet` without
that feature. The mainnet reference URL is the planned launch endpoint and must
be checked for chain ID 731 when deployment is enabled. The planned launch date
is December 2, 2026. Current upstream scripts enable mainnet through their launch
release.

## Starting sizes

| Setting | Adiri | Mainnet |
| --- | --- | --- |
| VM | Standard_D8s_v6 | Standard_D16s_v6 |
| Execution disk | 50 GiB, 12,000 IOPS, 240 MB/s | 100 GiB, 20,000 IOPS, 300 MB/s |
| Consensus disk | 50 GiB, 3,000 IOPS, 125 MB/s | 100 GiB, 3,000 IOPS, 125 MB/s |

Both use Premium SSD v2. Disk capacities and performance are independent profile
settings. Small initial disks are intentional. Preview another capacity with:

```bash
python3 scripts/validators/profile.py --network mainnet \
  --execution-disk-gb 1024 --consensus-disk-gb 256
```

Disk growth will increase the Azure managed disk capacity and then expand its
ext4 filesystem. The initial mainnet capacity is below the published 2 TB sizing
figure. Monitor usage and grow the volumes as the database grows. Review the
VM's aggregate I/O ceiling when changing disk performance.

## Storage layout

Reuse `infra/vm/prepare-disks.sh` for the same whole-disk ext4 layout as the
observers. LUN 0 mounts at `/var/lib/telcoin/db`. LUN 1 mounts at
`/mnt/telcoin-consensus`, with directory bind mounts for the complete
`consensus-db` tree and `static_files`. The service must require all three data
mounts. The data directory remains `/var/lib/telcoin`, with identity/configuration
at its root. Data disks are retained when the VM is removed.

## Access and identity

RPC and metrics bind to loopback. The public IP accepts the two P2P UDP ports;
management uses Azure Run Command. SSH and P2P source lists start empty (inbound closed). Configure approved P2P sources before finalization. There is no public
RPC load balancer in these profiles. The advertised transaction RPC URL is a
separate future gateway setting required for validator onboarding.

Use Trusted Launch, vTPM and Secure Boot. Store the BLS passphrase using upstream
systemd encrypted credentials. Execution addresses are operator-supplied, and
private wallet keys and BLS passphrases do not belong in a profile or preview.

Upgrades preserve registered keys and consensus signing state, check quorum and
observe the epoch restart rules. Each network has its own backend operations
identity. Staking and activation remain explicit wallet operations. Observer
fresh-key replacement operations do not apply to registered validator identities.

## Deployment and identity preparation

The workflow is `.github/workflows/validator-deployment.yml`. It accepts either
`adiri` or `mainnet`; there are no shared VMs, disks, vaults, keys or resource groups.
It uses hosted runners and Azure Run Command rather than allocating another runner
VM or opening SSH. Both deploy one validator initially. The mainnet start is gated
separately from preparing its infrastructure and public identity.

Local plans never contact Azure:

```bash
python3 scripts/validators/deploy.py --network adiri
python3 scripts/validators/deploy.py --network mainnet
```

Provision infrastructure (billable resources; explicitly requires `--apply`):

```bash
python3 scripts/validators/deploy.py --network adiri --phase provision --apply \
  --ssh-public-key-file ~/.ssh/id_ed25519.pub
python3 scripts/validators/deploy.py --network mainnet --phase provision --apply \
  --ssh-public-key-file ~/.ssh/id_ed25519.pub
```

The deployment creates Trusted Launch/vTPM VMs, static IPs, sealed-by-default
NSGs, separate Premium SSD v2 data disks, and a dedicated passphrase vault. It
mounts the execution and consensus/static-files layout, enables clock sync, turns
off swap, and configures the upstream kernel-mitigation preflight requirement.
The same disk-growth helper used by observers grows each disk by 50 GiB at 75%
usage. Its VM identity can resize only its own two disks. The API-key/RPC observer
resources are never part of this template. An existing validator VM is retained;
provisioning refuses to redeploy it, recreate its keys, or reuse a partial unknown
resource set. Upgrade/recovery of an existing validator is a separate operation.

After setting each profile's confirmed execution address, signed release tag,
approved P2P source CIDRs, and any transaction gateway URL, store a strong BLS
passphrase through a hidden local prompt (never through GitHub inputs or ARM):

```bash
python3 scripts/validators/deploy.py --network adiri --phase set-passphrase --apply
```

Use `--network mainnet` for its independent vault. This refuses to replace an
existing passphrase; password rotation must preserve the validator identity.
The operator/deployment principal is granted Secrets Officer only on that network's
new vault. `--operator-principal-id` chooses another key custodian at provisioning;
otherwise the current Azure identity is used. The VM has Secrets User on its own
network's vault, and receives no secrets through cloud-init or Run Command.

Prepare and export the public handoff, without starting a node or staking:

```bash
python3 scripts/validators/deploy.py --network adiri --phase prepare --apply \
  --release-tag vX.Y.Z-adiri --execution-address 0xCONFIRMED_ADDRESS
python3 scripts/validators/deploy.py --network adiri --phase export --apply
```

For mainnet select `--network mainnet` and a mainnet release `vX.Y.Z`. Preparation
installs the official verified script release and calls its JSON `keygen` phase.
The passphrase travels directly from the VM's vault identity to the installer's
stdin; upstream stores the running credential with systemd host+TPM encryption.
No unencrypted BLS keystore is accepted. Public export mounts **only**
`node-info.yaml` read-only into the network-specific pinned image; private key
files are not mounted. It writes `.validator-state/<network>/onboarding.json`
(ignored by Git) with the BLS public key, proof, execution address, IP, P2P
addresses and release digest. Only this public artifact is uploaded by CI.

Grant must supply the precise staking identity, including whether it is the Safe
itself. A proof bound to its owner's ECDSA address is not interchangeable. Keep the
encrypted key backup and recoverable vault passphrase secure. The upstream mainnet
release/pin gates stay in force: this command does not bypass them while the image
is pending. Infrastructure can be reserved independently; key preparation waits
for an approved, supported release.

After backing up the encrypted identity and obtaining the correct network
configuration/reference RPC, finalize explicitly:

```bash
python3 scripts/validators/deploy.py --network adiri --phase finalize --apply \
  --release-tag vX.Y.Z-adiri --execution-address 0xCONFIRMED_ADDRESS \
  --acknowledge-key-backup
```

The reference chain ID must match before starting. In particular, do not use the
planned mainnet RPC while it still serves Adiri; set the internal mainnet RPC
provided by the team. Finalization applies only approved P2P sources to the
validator NSG, requires all data mounts on every service start, and runs upstream
`finalize`. It does not stake or activate on-chain. Existing validator services are
refused rather than restarted. Repeated preparation preserves the same keys;
identity, IP or release mismatches require coordinated recovery. `--phase network`
updates the peer allowlist without a restart. `--phase status` reads the durable
preparation state.

For GitHub deployments configure **both** environments with required reviewers:
`validator-operations-adiri` and `validator-operations-mainnet`. Give each a separate
Azure OIDC identity scoped to its validator resource group, with permission to
provision its resources, custom disk role, and scoped role assignments. Precreate
the empty, tagged groups when the identity cannot create groups. Set environment
variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
`VALIDATOR_SSH_PUBLIC_KEY` and `VALIDATOR_KEY_CUSTODIAN_OBJECT_ID`. The custodian
object ID should be the operator who will set/recover the vault passphrase, not
necessarily the deployment identity. Then set repository variable
`VALIDATOR_DEPLOYMENT_ENABLED=true`. Mainnet finalization additionally requires
repository variable `VALIDATOR_MAINNET_ENABLED=true`; preparing resources does not.
The passphrase-setting step runs locally with the custodian's Azure login.

Changes here do not create Azure resources until an explicit deployment. Mainnet
and Adiri key material must never be copied between deployments. There is no
validator fresh-key/observer snapshot replacement path.

Requirements and installer references:

- https://run.telcoin.network/testnet-node-requirements.pdf
- https://run.telcoin.network/mainnet-node-requirements.pdf
- https://run.telcoin.network/mno-node-guide.pdf

## Adiri peer allowlist

The Adiri profile permits inbound UDP 49590 and 49594 only from these /32 sources:

| Peer | Public IPv4 |
| --- | --- |
| Upstream bootstrap 1 | 35.203.41.59 |
| Upstream bootstrap 2 | 34.105.191.161 |
| Upstream bootstrap 3 | 34.102.122.126 |
| Upstream bootstrap 4 | 34.141.203.172 |
| Upstream bootstrap 5 | 34.40.242.29 |
| OpRail Adiri observer | 104.43.128.43 |

Upstream source: [bootstrap_servers.yaml at commit 55e0c10](https://github.com/Telcoin-Association/telcoin-network/blob/55e0c10ad2147d3e7da40f58e092b47bebfc5485/chain-configs/testnet/bootstrap_servers.yaml), checked November 24, 2026.
The observer address was checked against its static Azure public IP on that date.
Per the team's current Adiri guidance these bootstrap nodes also represent the
validator set; bootstrap membership itself does not confer committee membership.
This is a reviewed snapshot, not a dynamic committee feed. Refresh the list when
peer membership/IPs change, and after replacing an OpRail observer. Do not reuse
it for mainnet. New observers must be explicitly added before they can dial this
validator. These restrictions apply to the validator, not to public observer P2P.

The NSG allows arbitrary source ports from these IPs to the two destination P2P
ports, then denies other inbound traffic (except explicitly configured management).
This preserves QUIC peers that use ephemeral source ports. RPC and metrics stay
loopback-only. Apply a revised list to an existing validator without a restart:

```bash
python3 scripts/validators/deploy.py --network adiri --phase network --apply
```

Provisioning uses the same list for a new validator. No validator resources or live
observer firewall rules are changed by editing this profile.

This is an **inbound** allowlist. Azure's default outbound rules remain enabled;
a node may still initiate connections to other discovered peers. Enforcing a
bidirectional peer-only policy requires additional outbound rules and a reviewed
list of operational exceptions (DNS, clock synchronization, Azure VM agent and
identity, Key Vault, package/container downloads, and the reference/sync RPC).
Do not deploy a blanket outbound deny using only the six peer IPs: it would break
those services. NSGs are stateful, so replies to an allowed connection do not need
an extra rule permitting unsolicited inbound traffic.
