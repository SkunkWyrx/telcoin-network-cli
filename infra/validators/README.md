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

After completing the encrypted backup and signed recovery drill below, and obtaining
the correct network configuration/reference RPC, finalize explicitly:

```bash
python3 scripts/validators/deploy.py --network adiri --phase finalize --apply \
  --release-tag vX.Y.Z-adiri --execution-address 0xCONFIRMED_ADDRESS \
  --acknowledge-key-backup
```

The reference chain ID must match before starting. In particular, do not use the
planned mainnet RPC while it still serves Adiri; set the internal mainnet RPC
provided by the team. Finalization applies only approved P2P sources to the
validator NSG, requires a signed recovery receipt matching the current identity and pinned passphrase version, requires all data mounts on every service start, and runs upstream
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

## Encrypted identity backup and recovery verification

Before the first `finalize`, complete the recovery drill. `--acknowledge-key-backup`
alone is no longer sufficient. This feature is for a newly **prepared** validator,
before its service is installed or it signs consensus. It never backs up a running
signer or automatically replaces/starts a validator. Existing prepared nodes from
before this feature have no pinned passphrase version: do not regenerate their
keys; review and migrate their state explicitly.

### What is protected

* The identity archive contains only `node-keys/bls.kw`, `primary.seed`,
  `worker.seed`, `node-info.yaml`, installer metadata and deployment configuration.
  Cleartext BLS keys, unexpected key files and symlinks are rejected. Network seed
  files are private too, so the entire archive receives additional encryption.
* The passphrase is put in a **different** encrypted envelope, including its
  versioned Key Vault reference and matching identity fingerprint. It never goes
  into the identity archive, an ARM parameter, a GitHub artifact or a command line.
* Envelopes use OpenSSL CMS AES-256-GCM, with independent RSA-3072-or-larger
  recipients and OAEP/SHA-256 key wrapping. Two certificates sharing a private key
  are refused. Keep both private recovery keys encrypted and off the validator,
  runner and repository, preferably with separate custodians/offline media.
* Only the public certificates go to the validator. The passphrase remains in its
  own network's Key Vault; generation pins the exact secret version. Later secret
  versions cannot silently change the validator's password. Key Vault has 90-day
  soft deletion and purge protection.
* Each network gets a dedicated ZRS storage account with separate `identity` and
  `passphrase` private containers. Shared-key authentication and public blobs are
  disabled. HTTPS/OAuth, infrastructure encryption, blob versioning, and 90-day
  blob/container soft deletion and a storage-account deletion lock are enabled. VM and custodian roles are scoped to
  those containers with read/write, **no delete permissions**. Writes use unique
  backup IDs and refuse overwrites. This is not immutable WORM storage: privileged
  Azure administrators can change policy or remove resources. Keep offline copies
  of both ciphertext envelopes, their index, and the two recovery private keys
  under an independent custody/recovery policy.
* This protects backups at rest. Root, Azure Run Command administrators and a
  compromised live validator can still access the live signing key/passphrase.
  Restrict those privileges, require MFA/PIM and audit their use. Python cannot
  guarantee erasure of every in-memory secret copy. RAM scratch is required for
  decrypted files; use a dedicated patched recovery host with swap disabled,
  hibernation/core dumps disabled, and no untrusted workloads.

### Create recovery keys once, outside Azure/GitHub

Use OpenSSL 3 on a trusted workstation. These commands prompt for private-key
passwords; do not put passwords in shell arguments. Use different passwords and
separate secure custody for the two private keys. A self-signed certificate here
is a pinned encryption recipient, not a public website/TLS certificate.

```bash
umask 077
openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 \
  -keyout adiri-identity-recovery.key -out adiri-identity-recovery.pem \
  -subj '/CN=OpRail Adiri identity recovery'
openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 \
  -keyout adiri-passphrase-recovery.key -out adiri-passphrase-recovery.pem \
  -subj '/CN=OpRail Adiri passphrase recovery'
```

Back up the encrypted `.key` files and their passwords securely and independently.
Do not lose them: the validator has only their public `.pem` certificates and
cannot recover these private keys for you. Keep the original certificates for
historical backups when rotating recipients. Never reuse these recipients across
Adiri and mainnet. Do not upload private recovery keys to Key Vault, the VM, CI,
GitHub secrets, the repository or this management site.

### Back up a prepared validator

After `prepare`, run:

```bash
python3 scripts/validators/deploy.py --network adiri --phase backup --apply \
  --identity-certificate /secure/public/adiri-identity-recovery.pem \
  --passphrase-certificate /secure/public/adiri-passphrase-recovery.pem
```

It obtains the **pinned** passphrase version through the VM identity, encrypts both
bundles on the VM with scratch files in `/dev/shm`, uploads them, reads them back
and checks their hashes. Its output contains only a public backup ID and storage
account, not secret data. Record the backup ID. Download and retain offline copies
of `<ID>/index.json` and `<ID>/identity.cms` from container `identity`, and
`<ID>/passphrase.cms` from container `passphrase`, using Azure Storage tools with
`--auth-mode login`. The Azure account name is `backup.storage_account` in the
network profile. The BLS passphrase's password-manager copy remains a recommended
independent recovery route; no plaintext export is produced by these scripts.

For the GitHub `backup` phase, configure public environment variables
`VALIDATOR_IDENTITY_RECOVERY_CERTIFICATE` and
`VALIDATOR_PASSPHRASE_RECOVERY_CERTIFICATE` with the corresponding full PEM text.
Only the public PEM certificates belong there. The recovery drill below runs
outside GitHub.

### Prove recovery before the first start

Use a dedicated, trusted Linux recovery host as a non-root operator. It needs
OpenSSL 3, Python 3, Azure CLI, Docker, and a tmpfs directory such as `/dev/shm`.
Give that operator the narrowly scoped backup permissions configured at
provisioning. Preload the exact trusted container digest from the backup index;
verify its official release provenance before use. The script refuses missing
images rather than fetching code while keys are present. Access to Docker is
privileged: this must not be a shared build runner or developer host.

```bash
python3 scripts/validators/backup.py --network adiri --backup-id BACKUP_ID \
  --identity-certificate /secure/adiri-identity-recovery.pem \
  --passphrase-certificate /secure/adiri-passphrase-recovery.pem \
  --identity-private-key /secure/adiri-identity-recovery.key \
  --passphrase-private-key /secure/adiri-passphrase-recovery.key
```

The tool downloads ciphertext with the operator's Azure login, asks for the two
private-key passwords using hidden prompts, decrypts into RAM, checks the archive
allowlist/digests/network/identity and tests the recovered BLS passphrase using
`keytool generate pop` **on a disposable copy with the original staking address**.
It compares the public key/proof before and after and verifies key files did not
change. The test container has no network, public ports, capabilities or writable
root filesystem and never runs `node`. The recovery host still needs Azure access
for download/upload; "offline" describes the node/keytool execution, not an
air-gapped Azure client. Secrets are not included in process arguments or logs.

After success it signs a public receipt with the identity recovery private key
and uploads `<ID>/verified.cms`. First `finalize` verifies that signature against
the pinned public certificate, reads/checks both remote ciphertext objects and
compares the current identity/pinned passphrase version. A missing, stale,
tampered, wrong-network or wrong-signer receipt blocks startup. The checkbox is
retained as an additional human acknowledgment, not the evidence of recovery.
No live node or registered identity is changed by this drill. A failed drill does
not start anything; check input files, RBAC propagation, installed tools, the
preloaded pinned image and key passwords. No failed subprocess output is returned
to CI. Retrying an already uploaded receipt requires a new backup ID; existing
backup objects are not overwritten.

### Recovering after VM loss

The same tool supports `--stage-only --output-dir /dev/shm/validator-restored`.
It performs the same cryptographic drill, then stages the verified identity files
in that new owner-only RAM directory. It does **not** save a plaintext passphrase,
copy files to Azure, install a service, retrieve old TPM state or start a signer.
If the vault secret has been lost entirely, add `--recover-passphrase-to-vault`
while staging. This writes the **original recovered passphrase** directly over
stdin to the configured network vault, only when no secret with that name exists.
It refuses to rotate an existing secret. Soft-deleted secrets should be recovered
through Key Vault first; Azure will refuse creating a secret over a deleted one.
The new secret version reference (not the passphrase) is recorded in
`recovered-secret-id.txt`; restoring deployment metadata to refer to that version
requires explicit review before any startup. The passphrase is never printed.
This option needs the operator's existing network-specific Key Vault permission.

The output directory is intentionally retained until the operator securely
removes it or shuts down the isolated recovery host. The normal verification
command cleans its temporary directory automatically.

Before a real replacement, an operator must:

1. Fence the old signer through Azure: stop/deallocate it and block its ability to
   restart, using controlled deployment/RBAC access. Confirm no other VM/service
   has this identity. A local checkbox or ping is not fencing.
2. Keep the **registered** BLS/network identity. Do not run `prepare`/key generation
   on a replacement or create a fresh validator identity for an existing stake.
3. Recover current consensus signing state and consistent chain data according to
   Telcoin's release-specific recovery instructions. The pre-activation identity
   backup does **not** contain `consensus-db/`, `db/`, `static_files/` or a current
   signing checkpoint. It cannot by itself authorize safe recovery of an active
   validator. Do not restore stale consensus state or reuse observer consensus.
4. Restore identity/configuration with owner-only permissions, retain or carefully
   update the advertised P2P IP, and check the public key against registration.
5. Recover the original passphrase via the pinned Key Vault secret or the separate
   encrypted envelope on the trusted recovery host. Transport it to the new host
   only through a protected operator channel, never command arguments/ARM/logs.
   Reseal it with `systemd-creds encrypt --with-key=host+tpm2` to the upstream
   credential name `bls-passphrase`, using stdin and the replacement's TPM. The
   credential file and service use must follow the pinned installer's exact paths;
   copying the old `.cred` file will not supply the new TPM binding. Never back up
   or distribute the host's TPM secrets as a substitute for this recovery step.
6. Verify quorum/epoch readiness and restore consistency before enabling the new
   service. Retire the old signer permanently before releasing the replacement.

Live consensus snapshots, source-VM fencing and automated replacement activation
are deliberately outside this identity-backup tool. They require a separate
coordinated maintenance procedure; the tool refuses backups after installation
and contains no live-restore/start switch. Complete a release-specific recovery
rehearsal before relying on this for an active validator.
