# Mainnet bootstrap and fresh database upgrades

## Mainnet inputs

Telcoin documents chain ID 731 (`0x2db`) for mainnet and 2017 (`0x7e1`) for Adiri.
Mainnet builds omit the `adiri` feature and run with `--chain mainnet`. Observer
role comes from committee membership. Each new VM generates its own observer
keys. The execution and consensus disks contain chain data only.

Sources checked on November 21, 2026:

- https://docs.telcoin.network/evm-compatibility.html
- https://docs.telcoin.network/getting-started/installing-a-release.html
- https://docs.telcoin.network/getting-started/hardware-requirements.html
- https://docs.telcoin.network/getting-started/public-rpc-nodes.html

The public repository currently exposes Adiri release tags, and
`https://rpc.telcoin.network` returned chain ID 2017 during verification. Obtain
an approved mainnet release tag and an independent HTTPS reference RPC that
returns 731 from Telcoin before applying the bootstrap. The script verifies the
reference and resolves the tag before creating Azure resources.

`infra/pool/mainnet.json` defines a separate observer resource group
`oprail-observer-mainnet`, registry, image repository, VNet (`10.54.0.0/16`), private
load balancer, and API network (`10.53.0.0/16`). Check those address ranges against
any other networks you intend to peer. The reference URL is deliberately empty
until supplied to bootstrap.

## Deploy the initial observer

Copy `infra/pool/mainnet-node.env.example` to a private local environment file
such as `.env.observer-mainnet-1`. Set the administrator CIDR, SSH key paths and a
unique BLS passphrase. The sample uses the existing experimental D4s_v6 and two
512 GiB Premium SSD v2 disks. Telcoin's public RPC sizing guidance is larger,
including at least 2 TB storage. Choose capacity and performance for the expected
mainnet load before deploying publicly.

Preview after obtaining the two mainnet inputs:

```bash
python3 scripts/pool/bootstrap.py \
  --config infra/pool/mainnet.json \
  --env-file .env.observer-mainnet-1 \
  --release-tag "$MAINNET_RELEASE_TAG" \
  --reference-rpc "$MAINNET_REFERENCE_RPC"
```

Add `--apply` to build the network-specific digest-pinned image, create isolated
networking and provision exactly one observer with blank disks. The script waits
for sync and matching block hashes before promoting the image. Resource groups
must be empty at bootstrap. Existing observer inventories are rejected.

The verified runtime profile is saved under
`.observer-state/mainnet/mainnet/bootstrap-profile.json`. Use that profile for
subsequent CLI operations or install it at
`/etc/oprail-observers/mainnet/pool.json` on the trusted operations runner. Preserve
the runtime state directory when moving control to a runner. The generated image
and inventory files are operational state, excluded from Git.

If bootstrap fails after provisioning, inspect the recorded resources and node
inventory before proceeding. It refuses to redeploy over an existing VM. A
provisioned node that times out during sync remains recorded for inspection.

Bootstrap exposes RPC through the private load balancer. Public HTTPS requires a
mainnet-specific public load balancer, certificate issuer and hostname setup;
Adiri's DNS zones, vault and certificate configuration are network-specific.

## Fresh database upgrade

The management panel operation **Replace nodes with fresh databases** accepts a
release tag. It dispatches `fresh-tag` through the existing approval workflow.
The workflow builds a candidate for the selected network, then:

1. Records the original node count and replacement plan in durable runner state.
2. Creates one new VM with fresh observer keys and two blank data disks. Snapshot
   IDs are explicitly cleared even if the node template contains restore values.
3. Waits for repeated strict sync checks against the reference RPC.
4. Configures and verifies its public TLS proxy when the pool uses public HTTPS.
5. Transfers certificate renewal responsibilities if the retiring VM is the issuer.
   Renewal state and narrowly scoped DNS/vault roles move to the replacement.
   A Certbot staging renewal and vault publication must succeed before retirement.
6. Rechecks sync, drains the old observer, stops it cleanly and deletes its VM,
   attached disks and dedicated networking.
7. Repeats for the other original nodes and promotes the new release only after
   the original count has been restored with replacements.

A one-node pool finishes with one new node. A two-node pool finishes with two new
nodes. Peak capacity is the original count plus one VM and its disks. Every new
node syncs from the network, so allow a long enough `upgrade_timeout_seconds`.
No database snapshots are used for this operation.

Direct CLI use with an already built candidate:

```bash
python3 scripts/pool/fleet.py --config /etc/oprail-observers/mainnet/pool.json \
  --apply replace-fresh "$PINNED_MAINNET_IMAGE"
```

The CLI uses `node_template` from the profile. Each replacement receives a unique
name, DNS label and passphrase. The command shares the fleet operation lock with
other lifecycle operations. The workflow also serializes requests per network.

A sync or TLS failure stops the rollout before retiring that node. Successfully
provisioned replacements remain in inventory for inspection and retry. Request
the same tag again to resume a workflow rollout. Completed steps are skipped and
already provisioned replacements are reused. A partially deleted old VM is
cleaned up using its exact persisted resource manifest. An interruption during
provisioning before the new node is recorded requires inspection of the recorded
replacement name before resuming, avoiding duplicate VMs or accidental overwrite.

## Mainnet management workflow setup

Provision a mainnet operations runner with network access to the mainnet VNet,
using `infra/management/runner.bicep` with `network=mainnet`. Register it with the
`observer-operations-mainnet` runner label. Install its mainnet
profile and private `template.env`, and grant its identity the relevant mainnet
VM/network/registry permissions. Public TLS replacement additionally requires
role-assignment permissions for the certificate issuer handoff.

Configure required reviewers on GitHub environment `observer-operations-mainnet`
before setting repository variable `OBSERVER_MAINNET_ENABLED=true`. The workflow
selects `/etc/oprail-observers/mainnet/pool.json` and uses a separate network
concurrency group. The management website needs its own mainnet settings and
release environment before it can dispatch mainnet requests. Existing Adiri
website settings continue selecting Adiri.

## Small observer disks and automatic growth

New pool observers start with 50 GiB execution and 50 GiB consensus disks. The
`storage` section in each network profile sets the initial capacities and enables
growth at 75% usage by 50 GiB per event. A systemd timer checks both physical data
filesystems independently every five minutes. Bind mounts on the consensus disk
are counted once. Capacity changes retain the configured IOPS and throughput.

The VM's managed identity receives disk read/write permissions scoped to its two
data disks. A disk must be attached to that VM, use Premium SSD v2, be mounted as
the expected whole-disk ext4 device and have no incomplete background copy before
the service attempts an expansion. Growth uses Azure ARM, rescans the device and
runs `resize2fs` online. The service records completed expansions and retries an
interrupted filesystem expansion on its next run. Azure's 64 TiB disk limit is the
default ceiling. Failures and completed actions appear in the system journal:

```bash
sudo systemctl status telcoin-disk-growth.timer
sudo journalctl -u telcoin-disk-growth.service
```

The timer is enabled at creation on new pool VMs. Existing VMs retain their disks
until explicitly replaced. Snapshot restores must use disks at least as large as
the snapshot, so the fleet automatically raises the initial capacity to match a
larger source snapshot. A move from existing 512 GiB disks to 50 GiB therefore
uses the fresh-disk replacement path and syncs from the network.

The `replace` workflow operation accepts the network's promoted image digest. It
uses the current validated release, provisions a fresh replacement, waits for
sync and public HTTPS setup, transfers certificate renewal if needed, then
retires the old observer. It preserves the original node count and shares the
same serialized operation lock as upgrades. This is the storage migration path
when the node software version remains the same.

Existing runner profiles without a `storage` section inherit the repository's
network-specific storage defaults. An explicit runtime `storage` section takes
precedence. Before the first growth-enabled deployment, an operator applies
`infra/pool/disk-growth-access.bicep` with the runner's principal ID. That creates
the custom disk role and delegates only that role to the runner. The VM template
then assigns it at each new data disk scope. Optional `challengeZones` and
`certificateVault` inputs delegate only the DNS contributor and certificate
publisher roles at the dedicated certificate resources for issuer replacement.
The operations runner cannot redefine the custom disk role.

Certificate handoff validates the active lineage named by
`public_rpc.certificate_name` (Adiri: `adiri-rpc-dual`) before moving renewal
responsibility. The DNS hook merges its challenge into existing TXT values using
conditional writes and removes only its own value afterward. A leftover challenge
therefore cannot block renewal or cause another challenge to be deleted. Allow at
least the current 60-second challenge TTL for cached answers to expire. Failed
validation leaves the original issuer's renewal timer enabled and prevents its
retirement.
