# Mainnet pre-genesis observer preparation

This mirrors Adiri's current VM/storage/network pattern in a separate resource
group, `oprail-observer-mainnet`, in Central US. It prepares two D4s_v6 VMs,
two 50 GiB Premium SSD v2 data disks per VM (5,000 IOPS/125 MB/s each), retained
64 GiB OS disks, independent static P2P IPs, private RPC and public floating-IP
load balancers. SSH is closed. HTTPS is allowed only to the load-balancer frontend;
raw public node RPC is denied. No staging resources are deployed or updated.

Preview, without Azure calls:

```bash
python3 scripts/mainnet/prepare.py
```

Provision and prepare the two authorized observers:

```bash
python3 scripts/mainnet/prepare.py --apply
```

Only public configuration reaches ARM/cloud-init. The script creates independent
random 256-bit passphrases in `oprail-mainnet-keys` over stdin. Each VM's identity
can read only its own passphrase secret. The BLS key is encrypted; all BLS/network
key files live on the retained consensus SSD under its owner-only `identity`
directory, with `node-keys` bind-mounted into `/var/lib/telcoin`. A second vault,
`oprail-mainnet-backup`, keeps a per-node identity bundle including the encrypted
BLS key, private network seeds and public node-info. Each VM can read/write only
its own backup secret. The operator has custodian permissions on both vaults.
Both vaults have purge protection and 90-day soft deletion. Backups are encrypted
at rest by Key Vault; unlike validator PR #8, this is not an independently
offline-recipient-encrypted archive. Access to the backup vault exposes network
seed material, so restrict/audit custodian and Run Command access. Passphrase and
identity contents are never returned to the controller or logged. Existing secrets
or key backups are not overwritten on retry.

`v0.18.0-adiri` is used **only as an offline, network-independent keygen tool**.
The helper checks official release-key fingerprints against their GitHub profiles,
verifies the detached signature on SHA256SUMS, checks its exact expected asset
list, checks IMAGE_DIGEST's hash, and pulls only that pinned tool image. The
signed Adiri genesis/committee files are not downloaded or installed. The CLI's
`keytool generate observer` creates a fresh BLS/primary/worker identity with the
node's own `/ip4/<static-IP>/udp/49590|49594/quic-v1` addresses and appends peer IDs.
The execution address is zero: these are non-staking observers, not validators.

A disposable RAM copy is unlocked with the passphrase and re-signs the existing
proof to verify recovery without changing the real identity. All tool containers
use `--network none`, run only `--version` or `keytool`, and are removed afterward.
The offline tool image is removed after preparation to avoid mistaking it for a runtime image. The node service is **masked**, there is no runtime image configuration or
long-lived passphrase environment file, and neither `node` nor the RPC proxy is
started. Disk-growth timers are enabled (75% threshold, 50 GiB increments).

Exports are in ignored `.mainnet-preparation/`:

- `oprail-prod-observer-01-node-info.yaml`
- `oprail-prod-observer-02-node-info.yaml`
- `preparation.json` (public IPs, fingerprints/provenance, backup checks, runtime-disabled status)

Recheck the prepared nodes without regenerating keys:

```bash
python3 scripts/mainnet/verify.py --remove-keygen-image
```

This checks masked/inactive services, no containers or runtime configuration,
mounted persistent identity storage, the expected tool commit, public export
consistency and distinct BLS/network keys and passphrases. It can remove the
now-unused offline tool image. No secret values are returned. It refreshes the
combined public preparation report after per-node retries.

Only the two public YAML files are for Grant. Do not share Key Vault secret values,
private keys, network seeds, disks or backups. Node public IPs are deletion-locked
after successful preparation; retain them and these identities after the handoff.

Quota failures can leave empty disks/networking and a subset of the VMs. Inspect
Azure's deployment error, resolve quota/capacity, then use
`--apply --resume-infrastructure` to retry only missing/failed VMs. Existing
successful VMs are not redeployed. `--node mainnet-observer-1` or `-2` can prepare
one identity without changing observer count. A failed generation leaving keys
but no durable state refuses regeneration; recover/inspect, never add `--force`.

Before mainnet launch, perform a separate approved cutover: select/verify the
mainnet runtime image and genesis/network configuration, retain the generated
identities, retrieve the matching passphrase from Key Vault on-host, install
readiness/monitoring and certificate/proxy automation, and verify private sync
before enabling public RPC traffic. No public oprail.com DNS record or certificate
is changed by preparation. Do not run the normal observer deployment script against
these existing VMs: it is not the pre-genesis launch/handoff procedure.
