# Validator deployment profiles

The Adiri and mainnet profiles define independent one-node deployments in
`oprail-validator-adiri` and `oprail-validator-mainnet`. Each has its own network,
release channel and operations runner/environment names. These profiles describe
the desired configuration. Provisioning and installer integration are the next
implementation step.

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
| Execution disk | 256 GiB, 12,000 IOPS, 240 MB/s | 256 GiB, 20,000 IOPS, 300 MB/s |
| Consensus disk | 128 GiB, 3,000 IOPS, 125 MB/s | 128 GiB, 3,000 IOPS, 125 MB/s |

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
management uses Azure Run Command. SSH sources start empty. There is no public
RPC load balancer in these profiles. The advertised transaction RPC URL is a
separate future gateway setting required for validator onboarding.

Use Trusted Launch, vTPM and Secure Boot. Store the BLS passphrase using upstream
systemd encrypted credentials. Execution addresses are operator-supplied, and
private wallet keys and BLS passphrases do not belong in a profile or preview.

Upgrades preserve registered keys and consensus signing state, check quorum and
observe the epoch restart rules. Each network has its own backend operations
identity. Staking and activation remain explicit wallet operations. Observer
fresh-key replacement operations do not apply to registered validator identities.

## Implementation sequence

1. Provision the VM, VNet, P2P IP/NSG and the two configurable disks from a profile.
2. Install the verified upstream scripts and automate keygen and finalize, with
   an encrypted identity backup between phases.
3. Add private health/status reporting and validator-specific upgrade operations.
4. Complete the transaction gateway and on-chain onboarding inputs.

Requirements and installer references:

- https://run.telcoin.network/testnet-node-requirements.pdf
- https://run.telcoin.network/mainnet-node-requirements.pdf
- https://run.telcoin.network/mno-node-guide.pdf
