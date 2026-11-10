# Telcoin Network Observer Node Docker Image

This Docker image provides an easy way to run a Telcoin Network observer node. The image automatically builds the node binary and handles key generation and startup.

## Features

- **Automatic Build**: The node binary is built during image creation for faster startup
- **Key Management**: Automatically generates observer keys on first run if they don't exist
- **Flexible Passphrase**: Supports both environment variable and interactive passphrase methods
- **Data Persistence**: Uses Docker volumes to persist keys, config, and chain data
- **Network Configuration**: Defaults to IPv6 (NAT-free) but supports IPv4 and custom configurations

## Quick Start

Get your Telcoin Network observer node running in minutes. Choose your preferred method below.

---

### Option 1: Docker Compose (Recommended)

**Easiest method** - Handles volumes and configuration automatically.

#### Step 1: Configure Environment Variables

**Create your `.env` file** (if you haven't already):

```bash
# Copy the example file
cp .env.example .env

# Edit .env and set your values (especially TN_BLS_PASSPHRASE)
# The .env file is git-ignored and won't be committed
```

**Important:** The `.env` file contains sensitive information (like your BLS passphrase) and is automatically git-ignored. Never commit it to version control!

#### Step 2: Build the Image

**Build from default branch (main):**

```bash
docker compose -f src/docker/docker-compose.yml build
```

**Build from a specific branch or tag:**

```bash
# Set GIT_BRANCH or GIT_TAG in your .env file, or:
GIT_TAG= GIT_BRANCH=develop docker compose -f src/docker/docker-compose.yml build
```

**Examples:**

- `GIT_TAG= GIT_BRANCH=main` - Build from main branch
- `GIT_TAG= GIT_BRANCH=develop` - Build from develop branch
- `GIT_TAG=v0.17.0-adiri` - Build from the default release tag
- `GIT_TAG= GIT_BRANCH=feature-branch` - Build from a feature branch

#### Step 3: Start the Node

**With environment variable passphrase** (non-interactive):

```bash
# Make sure TN_BLS_PASSPHRASE is set in your .env file
docker compose -f src/docker/docker-compose.yml up -d
```

**With interactive passphrase** (prompts for passphrase):

```bash
# Use 'docker compose run' for better interactive support
docker compose -f src/docker/docker-compose.yml run --rm telcoin-observer

# OR use 'docker compose up' (may have limited interactive support)
docker compose -f src/docker/docker-compose.yml up
```

**Note:** If `TN_BLS_PASSPHRASE` is not set in `.env`, the container will prompt for it interactively.

#### Step 4: Check Status

```bash
# View logs
docker compose -f src/docker/docker-compose.yml logs -f

# Stop the node
docker compose -f src/docker/docker-compose.yml down
```

---

### Option 2: Docker CLI

**More control** - Direct docker commands.

#### Step 1: Build the Image

**Build from default branch (main):**

```bash
docker build -t telcoin-observer:latest -f src/docker/Dockerfile .
```

**Build from a specific branch or tag:**

```bash
# Build from a specific branch
docker build --build-arg GIT_TAG= --build-arg GIT_BRANCH=develop -t telcoin-observer:latest -f src/docker/Dockerfile .

# Build from a specific tag (preferred)
docker build --build-arg GIT_TAG=v0.17.0-adiri -t telcoin-observer:latest -f src/docker/Dockerfile .

# Build from a feature branch
docker build --build-arg GIT_TAG= --build-arg GIT_BRANCH=feature-branch -t telcoin-observer:latest -f src/docker/Dockerfile .
```

#### Step 2: Start the Node

**With environment variable passphrase** (non-interactive):

```bash
docker run -d \
  --name telcoin-observer \
  --restart unless-stopped \
  -v telcoin-data-v2:/app/data \
  -e TN_BLS_PASSPHRASE="your-secure-passphrase" \
  -p 8545:8545 \
  telcoin-observer:latest
```

**With interactive passphrase** (prompts for passphrase):

```bash
docker run -it \
  --name telcoin-observer \
  --restart unless-stopped \
  -v telcoin-data-v2:/app/data \
  -p 8545:8545 \
  telcoin-observer:latest
```

#### Step 3: Check Status

```bash
# View logs
docker logs -f telcoin-observer

# Stop the node
docker stop telcoin-observer

# Remove the container (keeps volume data)
docker rm telcoin-observer
```

---

### Option 3: Pre-built Image (If Published)

If the image is published to Docker Hub or GitHub Container Registry:

```bash
# Pull from Docker Hub
docker pull yourusername/telcoin-observer:latest

# Or from GHCR
docker pull ghcr.io/username/telcoin-observer:latest

# Then run using Option 2 commands above
```

**Note**: Update `docker-compose.yml` to use pre-built image:

```yaml
services:
  telcoin-observer:
    image: yourusername/telcoin-observer:latest
    # Remove or comment out the 'build' section
```

---

### Verify It's Working

Once running, test the RPC endpoint:

```bash
curl http://localhost:8545 \
  -X POST \
  -H "Content-Type: application/json" \
  --data '{"method":"eth_chainId","params":[],"id":1,"jsonrpc":"2.0"}'
```

You should receive a JSON response with the chain ID.

## Environment Variables

Environment variables are configured in the `.env` file in the project root. Copy `.env.example` to `.env` and customize as needed.

| Variable                     | Required | Default                     | Description                                                                                                                                              |
| ---------------------------- | -------- | --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TN_BLS_PASSPHRASE`          | No\*     | -                           | BLS passphrase. If set, uses `--bls-passphrase-source env`. If not set, uses interactive mode (requires `-it` flag). \*Required for non-interactive mode |
| `GIT_BRANCH`                 | No       | `main`                      | Git branch to build from when `GIT_TAG` is empty (e.g., `main`, `develop`, `feature-branch-name`)                                                     |
| `GIT_TAG`                    | No       | `v0.17.0-adiri`             | Git tag to build from. When set, it is preferred over `GIT_BRANCH` (e.g., `v0.17.0-adiri`, `v1.0.0`)                                                  |
| `GIT_REPO_URL`               | No       | upstream repo URL           | Repository URL to clone during image builds. Override this to build from a fork or alternate clone URL                                                  |
| `DATADIR`                    | No       | `/app/data`                 | Data directory path for keys, config, and chain data                                                                                                     |
| `PRIMARY_LISTENER_MULTIADDR` | No       | `/ip6/::/udp/49590/quic-v1` | Primary listener multiaddress (IPv6 default)                                                                                                             |
| `WORKER_LISTENER_MULTIADDR`  | No       | `/ip6/::/udp/49594/quic-v1` | Worker listener multiaddress (IPv6 default)                                                                                                              |
| `CHAIN`                      | No       | `adiri`                     | Chain to connect to (e.g., `adiri` for testnet)                                                                                                          |

**Security Note:** The `.env` file is git-ignored. Never commit it to version control. Use `.env.example` as a template.

## Network Configuration

### IPv6 (Default - Recommended)

IPv6 is recommended because it's NAT-free and works with dynamic IPs. The defaults are:

- `PRIMARY_LISTENER_MULTIADDR=/ip6/::/udp/49590/quic-v1`
- `WORKER_LISTENER_MULTIADDR=/ip6/::/udp/49594/quic-v1`

### IPv4

To use IPv4 instead, set:

```bash
-e PRIMARY_LISTENER_MULTIADDR=/ip4/0.0.0.0/udp/49590/quic-v1
-e WORKER_LISTENER_MULTIADDR=/ip4/0.0.0.0/udp/49594/quic-v1
```

## Data Persistence

The image uses Docker volumes to persist data. The data directory contains:

- Observer keys and BLS keys
- Configuration files
- Chain sync data

**Important**: Keep your volume safe! It contains your keys and sync data.

### Managing Volumes

**List volumes**:

```bash
docker volume ls
```

**Inspect volume**:

```bash
docker volume inspect telcoin-data-v2
```

**Remove volume** (⚠️ This deletes all data):

```bash
docker volume rm telcoin-data-v2
```

**Backup volume**:

```bash
docker run --rm -v telcoin-data-v2:/data -v $(pwd):/backup ubuntu tar czf /backup/telcoin-backup.tar.gz -C /data .
```

**Restore volume**:

```bash
docker run --rm -v telcoin-data-v2:/data -v $(pwd):/backup ubuntu tar xzf /backup/telcoin-backup.tar.gz -C /data
```

## Passphrase Methods

### Method 1: Environment Variable (Non-Interactive)

Set the `TN_BLS_PASSPHRASE` environment variable:

```bash
docker run -d \
  -v telcoin-data-v2:/app/data \
  -e TN_BLS_PASSPHRASE="your-secure-passphrase" \
  telcoin-observer
```

**Security Note**: Environment variables may be visible to other processes. Only use this for testing or with secure secret managers (e.g., systemd credentials, Docker secrets).

### Method 2: Interactive (Secure)

Run with `-it` flags to enter passphrase interactively:

```bash
docker run -it \
  -v telcoin-data-v2:/app/data \
  telcoin-observer
```

You'll be prompted to enter the passphrase when keys are generated.

## Verifying the Node

Once the container is running, verify it's working:

### Check Logs

```bash
docker logs telcoin-observer
```

You should see:

- Peer discovery messages
- Gossip traffic
- Consensus sync logs
- RPC startup messages

### Test RPC Endpoint

```bash
curl http://localhost:8545 \
  -X POST \
  -H "Content-Type: application/json" \
  --data '{"method":"eth_chainId","params":[],"id":1,"jsonrpc":"2.0"}'
```

A valid JSON response confirms the observer node is live.

## Troubleshooting

### Container exits immediately

- Check logs: `docker logs telcoin-observer`
- Ensure you're using `-it` flags if not providing `TN_BLS_PASSPHRASE`
- Verify the volume is mounted correctly

### Keys not generating

- Ensure the data directory is writable
- Check if keys already exist (they won't regenerate)
- Verify passphrase is being provided correctly

### Interactive passphrase prompt not showing

If you're using `docker compose up` and don't see the passphrase prompt:

1. **Use `docker compose run` instead** (recommended for interactive mode):

   ```bash
   docker compose -f src/docker/docker-compose.yml run --rm telcoin-observer
   ```

2. **Or use environment variable** (non-interactive):

   ```bash
   # Edit docker-compose.yml and uncomment the environment section
   docker compose -f src/docker/docker-compose.yml up
   ```

3. **Or use Docker CLI directly**:
   ```bash
  docker run -it -v telcoin-data-v2:/app/data telcoin-observer:latest
   ```

The issue is that `docker compose up` doesn't always forward stdin properly for interactive prompts, while `docker compose run` handles it better.

### Network connectivity issues

- Check firewall settings
- Verify listener addresses are correct
- For IPv4, ensure ports are accessible
- Observer nodes don't require port forwarding, but outbound UDP is needed

### Build failures

- Ensure you have sufficient disk space (build requires several GB)
- Check internet connectivity (needs to clone repo and submodules)
- Verify Rust installation in the image

## Advanced Usage

### Building from Different Branches/Tags

You can build the image from any branch, tag, or commit in the telcoin-network repository:

**Using Docker Compose:**

```bash
# Build from develop branch
GIT_TAG= GIT_BRANCH=develop docker compose -f src/docker/docker-compose.yml build

# Build from a specific tag (preferred)
GIT_TAG=v1.0.0 docker compose -f src/docker/docker-compose.yml build

# Or set in .env file (example)
echo "GIT_TAG=v0.17.0-adiri" >> .env
docker compose -f src/docker/docker-compose.yml build
```

**Using Docker CLI:**

```bash
# Build from develop branch
docker build --build-arg GIT_TAG= --build-arg GIT_BRANCH=develop -t telcoin-observer:latest -f src/docker/Dockerfile .

# Build from a specific tag (preferred)
docker build --build-arg GIT_TAG=v1.0.0 -t telcoin-observer:latest -f src/docker/Dockerfile .
```

**Note:** If `GIT_TAG` is set, the build uses that tag. If `GIT_TAG` is empty (for example `GIT_TAG=`), it uses `GIT_BRANCH`.

### Building From A Different Repository

If you want to build from a fork or alternate clone URL, set `GIT_REPO_URL`:

```bash
GIT_REPO_URL=https://github.com/your-org/telcoin-network.git docker compose -f src/docker/docker-compose.yml build

docker build \
  --build-arg GIT_REPO_URL=https://github.com/your-org/telcoin-network.git \
  -t telcoin-observer:latest \
  -f src/docker/Dockerfile .
```

For ACI deploys, set `GIT_REPO_URL` in `.env` before running `scripts/deploy-azure-aci.sh`.

### Custom Data Directory

```bash
docker run -d \
  -v /host/path/to/data:/app/data \
  -e DATADIR=/app/data \
  telcoin-observer
```

### Different Chain

```bash
docker run -d \
  -v telcoin-data-v2:/app/data \
  -e CHAIN=mainnet \
  telcoin-observer
```

### Custom Ports

```bash
docker run -d \
  -v telcoin-data-v2:/app/data \
  -p 8545:8545 \
  -p 49590:49590/udp \
  -p 49594:49594/udp \
  telcoin-observer
```

## System Requirements

### Minimum Requirements
- **Docker**: 20.10+ (or Docker Compose 2.0+)
- **RAM**: 4GB available to Docker (8GB+ recommended for better performance)
- **CPU**: 2 cores (4+ cores recommended for faster sync)
- **Disk Space**: 
  - 20GB minimum for initial build and chain data
  - 50GB+ recommended for long-term operation (chain data grows over time)
- **Network**: Stable internet connection (broadband recommended)

### Recommended Requirements
- **RAM**: 8GB+ available to Docker
- **CPU**: 4+ cores
- **Disk Space**: 100GB+ SSD (faster sync and better performance)
- **Network**: High-speed connection for initial sync

**Note**: Resource usage will be higher during:
- Initial chain sync
- Build process (temporary, only during image creation)
- High network activity periods

Monitor resource usage with:
```bash
docker stats telcoin-observer
```

## What's Included

The image includes:

- Debian Bookworm Slim base
- Rust 1.94 toolchain (via `rust:1.94-slim-bookworm` builder image)
- All required build dependencies
- Pre-built `telcoin-network` binary with `faucet` feature (testnet/adiri default)
- Entrypoint script for automatic key generation and node startup

## Security Considerations

1. **Passphrase Storage**: Never commit passphrases to version control
2. **Volume Security**: Protect your Docker volumes - they contain sensitive keys
3. **Network Exposure**: Only expose ports you need (RPC on 8545)
4. **Image Source**: Build from trusted source or verify the Dockerfile

## Support

For issues related to:

- **Docker image**: Check this repository
- **Telcoin Network**: See [Telcoin Network documentation](https://github.com/telcoin-association/telcoin-network)

## Azure VM observer (two Premium SSD data disks)

Deploy a new Ubuntu 24.04 VM with `scripts/deploy-azure-vm.sh`. The default is
`Standard_D4s_v6` (4 vCPU, 16 GiB RAM), a persistent 64 GiB Premium SSD OS disk,
and **two separate 512 GiB Premium SSD v2 LRS managed disks**. Data disk host
caching is disabled. Each data disk starts at 5,000 IOPS and 125 MB/s.
`VM_ZONE=1` places the VM and both disks in the same availability zone.
The VM uses NVMe; cloud-init installs `azure-vm-utils` and identifies data disks
using stable Azure LUN links before mounting them. These are separate Azure-managed disks, not partitions or
local physical SSDs. Both share the VM's aggregate storage limits; benchmark sync
and disk latency before assuming this layout improves throughput.

| Host and container path | Backing storage |
| --- | --- |
| `/var/lib/telcoin/db` | Execution disk, LUN 0, ext4 |
| `/var/lib/telcoin/consensus-db` | Consensus disk, LUN 1, directory bind mount |
| `/var/lib/telcoin/static_files` | Consensus disk, LUN 1, directory bind mount |
| `/var/lib/telcoin` (keys, configuration, PID lock) | Persistent OS disk |

The consensus disk is mounted at `/mnt/telcoin-consensus`; its `consensus-db` and
`static_files` directories are bind-mounted into the data directory. The entire
consensus tree stays on one filesystem so atomic renames within it remain valid.
The container starts with `DATADIR=/var/lib/telcoin`, which the entrypoint passes
as `--datadir`. systemd uses `RequiresMountsFor` for all three mounts and checks
that each is a mount point before starting Docker. Docker has no independent
restart policy; systemd controls startup after a reboot. First boot formats only
blank, unpartitioned data disks and refuses unexpected filesystem signatures or
nonempty underlying mount directories. fstab uses filesystem UUIDs.

Prerequisites: Azure CLI with Bicep, Python 3, SSH, an existing resource group and
ACR in that group, and permission to deploy resources and assign `AcrPull` to the
VM's system-assigned managed identity. ACR must use registry RBAC permissions
(`AcrPull`), rather than repository ABAC permissions. Set the region to match your
resource group and registry. VM quota/capacity must be available in that region.

```bash
az login
# Add the Azure VM settings from .env.example to your existing .env,
# then fill in the subscription, registry, DNS label, SSH CIDR, keys,
# and TN_BLS_PASSPHRASE. Keep GIT_TAG=v0.17.0-adiri.
bash scripts/deploy-azure-vm.sh
```

The script automatically reads the project `.env` before applying defaults.
Exported environment values override `.env`, including an explicitly empty
`GIT_TAG`. Use `ENV_FILE=/path/to/settings.env` for an alternate settings file.
SSH paths can use `$HOME` as shown in `.env.example`. Quote passphrases containing
spaces or shell special characters with single quotes.

The script builds a Linux/amd64 image in ACR, provisions the VM/network/disks,
waits for cloud-init, sends the passphrase over SSH to a root-only
`/etc/telcoin/observer.env`, and starts the service. No passphrase is included in
ARM parameters, cloud-init, or command arguments. SSH host keys use OpenSSH's
`accept-new` policy: new hosts are trusted on first connection, and changed keys
are rejected. The VM authenticates to ACR using managed identity rather than
registry admin credentials. Set `SKIP_IMAGE_BUILD=true` and `IMAGE_TAG` to reuse
an existing image. `VM_SIZE` can override compute size; data disks remain 512 GiB. `VM_ZONE` selects the zone;
`DATA_DISK_IOPS` and `DATA_DISK_MBPS` set performance per disk. Compute overrides
must support NVMe and Premium SSD v2. Confirm SKU and disk availability in the
selected zone before deployment.

Preview files locally without contacting Azure or providing a passphrase:

```bash
PREVIEW_DIR=/tmp/telcoin-vm-preview bash scripts/deploy-azure-vm.sh --preview
az bicep build --file infra/vm/main.bicep --outfile /tmp/telcoin-vm-template.json
# Optional read-only Azure review of the rendered deployment:
az deployment group what-if --resource-group "<resource-group>" \
  --template-file infra/vm/main.bicep \
  --parameters @/tmp/telcoin-vm-preview/parameters.json
```

Monitor the VM from your workstation using the same `.env` settings:

```bash
bash scripts/watch-azure-vm.sh --status     # service, RAM, disks, sync and RPC
bash scripts/watch-azure-vm.sh --tail       # live logs; Ctrl+C to stop
bash scripts/watch-azure-vm.sh --eta        # consensus and execution catch-up estimates
bash scripts/watch-azure-vm.sh --diagnose   # health verdict
```

The wrapper runs `watch-local-docker.sh` over SSH without installing it remotely.
RPC queries run on the VM against localhost; no public RPC port is needed.
Run ETA twice with some time between samples for an execution estimate. ETA and
health verdicts are heuristics based on recent logs and block timestamps.

The public NSG permits QUIC UDP 49590/49594 and SSH only from `SSH_SOURCE_CIDR`.
RPC port 8545 is not publicly opened; use an SSH tunnel:

```bash
ssh -i "$SSH_PRIVATE_KEY_FILE" -L 8545:127.0.0.1:8545 azureuser@<vm-fqdn>
ssh -i "$SSH_PRIVATE_KEY_FILE" azureuser@<vm-fqdn> 'sudo journalctl -u telcoin-observer -f'
ssh -i "$SSH_PRIVATE_KEY_FILE" azureuser@<vm-fqdn> 'findmnt -R /var/lib/telcoin; df -h /var/lib/telcoin/{db,consensus-db,static_files}'
```

This path provisions **new nodes only** and refuses an existing VM name. It does
not migrate or stop an ACI observer. Use a unique VM name/DNS label per instance.
The OS and data disks are retained when the VM is deleted; removing the resource
group still deletes them. Retention is not a backup: snapshot both data disks and
protect the OS-disk keys/configuration separately. Stop the node for a consistent
cross-disk backup. Logs are available through journald and `docker logs`;
this path does not provision Log Analytics.

Budget reference: the checked Central US retail rates put compute at about
$166/month, both data disks at about $116/month, and the 64 GiB OS disk plus public
IP at about $14/month: **approximately $296/month** at 730 hours, before
registry/builds, bandwidth, logs, and backups. Prices vary by region and agreement;
confirm current pricing before deploying. Disk IOPS above 3,000 and throughput
above 125 MB/s incur additional charges per disk. Both disks share the VM's
aggregate I/O limit, so their configured performance does not add unlimited VM
throughput. See [Azure disk specifications](https://learn.microsoft.com/en-us/azure/virtual-machines/disks-types).

### Create another observer from chain-data snapshots

First establish a healthy, syncing observer on `v0.17.0-adiri`. Capture its two data
disks with:

```bash
bash scripts/snapshot-azure-vm.sh
```

The script reads the source VM settings from `.env`. It cleanly stops the observer,
flushes writes, deallocates the VM, creates incremental snapshots of LUN 0 (execution)
and LUN 1 (consensus plus static files), and restarts the source VM. Expect downtime;
if a capture fails, it still attempts to restart the VM. A failed restart is reported
with a recovery command. Snapshot background copies can continue after restart.
Do not use snapshots from different captures together.

The script saves the two resource IDs in `snapshots/<capture-name>.env`.
`SNAPSHOT_PREFIX` optionally supplies a unique capture name and `SNAPSHOT_OUTPUT_DIR`
changes the output directory. Snapshots contain chain data, not the keys or OS disk.
They incur storage charges until deleted. Check readiness for each ID:

```bash
az snapshot show --ids '<snapshot-resource-id>' --query completionPercent
```

Wait for **both** to report `100`. Copy `.env` to a separate, private settings file
such as `.env.observer-2` and change these values:

```dotenv
VM_NAME=telcoin-observer-vm-2
VM_DNS_LABEL=your-unique-observer-2-dns-label
EXECUTION_SNAPSHOT_ID=/subscriptions/.../snapshots/<capture>-execution
CONSENSUS_SNAPSHOT_ID=/subscriptions/.../snapshots/<capture>-consensus
SKIP_IMAGE_BUILD=true
```

Keep the source's chain and image version initially. Set a passphrase for the new
observer and ensure its SSH CIDR is current. The snapshot region must match
`AZURE_LOCATION`; both restored disks remain 512 GiB Premium SSD v2 with the configured
IOPS/throughput. Each observer gets its own disks, fresh OS disk and identity.

```bash
ENV_FILE=.env.observer-2 PREVIEW_DIR=/tmp/observer-2-preview bash scripts/deploy-azure-vm.sh --preview
ENV_FILE=.env.observer-2 bash scripts/deploy-azure-vm.sh
ENV_FILE=.env.observer-2 bash scripts/watch-azure-vm.sh --status
```

The entrypoint generates fresh observer keys and `node-info.yaml` after the restored
disks are mounted, then starts against the copied databases. Check that execution
height advances and peer identities differ from the source. This workflow was
validated on observer 2 on October 22, 2026; both observers reached the same
reference height and block hash. See `docs/operations/adiri-observer-pool.md`. Leave both snapshot IDs blank to provision
empty disks instead. The deploy command refuses an existing VM or destination disks.

Azure snapshot behavior and readiness requirements:
[Incremental managed-disk snapshots](https://learn.microsoft.com/en-us/azure/virtual-machines/disks-incremental-snapshots).

Compare observer execution height with Adiri's reference RPC:

```bash
bash scripts/watch-azure-vm.sh --sync
```

Repeat after 30–60 seconds to see block advancement and whether the gap is closing.
The command runs over SSH; public access to the observer RPC is unnecessary.
`NETWORK_RPC_URL` in `.env` or the environment overrides the default
`https://rpc.adiri.tel`. RPC failures report status unknown, not synced. Within three
blocks is reported as near the reference head; this is an operational tolerance,
not proof of finality or matching chain hashes. An observer ahead of the reference
is explicitly reported because the reference may itself lag.
For local Docker or an SSH tunnel, use `python3 scripts/check-observer-sync.py`
(with optional `--rpc`, `--network-rpc`, and `--threshold` arguments).

### Monitor the observer fleet through public RPC

```bash
bash scripts/watch-observers.sh
```

This reads `.env`, discovers Azure resources tagged `telcoin-role=observer` and
`telcoin-chain=adiri` in `AZURE_RESOURCE_GROUP` and the configured subscription,
and queries each resource's `telcoin-rpc-url` from your machine. No SSH is used.
It queries the reference RPC once and checks observers concurrently, displaying
height, blocks behind, and status. Missing endpoints, unreachable RPCs and reference
failures are reported explicitly. Exit status is nonzero unless all discovered
observers are near the reference head (default tolerance: three blocks).
`--subscription`, `--resource-group`, `--chain`, `--network-rpc`, and `--threshold`
can override settings. Tags discover configured observers, including stopped VMs;
an unreachable endpoint remains visible rather than silently disappearing.

New VM deployments add these tags automatically. Set `PUBLIC_RPC_URL` in each
observer's deployment settings to its externally reachable endpoint. With `PUBLIC_RPC_ENABLED=true`, deployment opens TCP 8545/8546 and automatically
advertises the VM DNS endpoint if `PUBLIC_RPC_URL` is blank. This is direct HTTP;
it does not provision an HTTPS proxy.
Use an RPC ingress exposing the intended public methods; the node's existing local
RPC also enables `debug` and should not be exposed wholesale for this monitor.
URLs in tags must not contain credentials or API keys.

Existing resources can be tagged without redeploying; `Merge` preserves other tags:

```bash
az tag update --resource-id '<observer-resource-id>' --operation Merge --tags \
  telcoin-role=observer telcoin-chain=adiri \
  telcoin-rpc-url=https://your-observer-rpc.example.com
```


### Experimental public RPC ingress

For fresh or snapshot-based observers, set these values in the observer settings:

```dotenv
PUBLIC_RPC_ENABLED=true
PUBLIC_RPC_SOURCE_CIDR='*'
PUBLIC_RPC_URL=
HTTP_API=eth,net,web3,rpc,debug,trace
WS_ENABLED=true
WS_API=eth,net,web3,rpc,debug,trace
```

Deployment opens TCP 8545/8546 and advertises `http://<vm-fqdn>:8545` in Azure tags.
Then `bash scripts/watch-observers.sh` checks every tagged observer without SSH.
Use a CIDR instead of `*` to restrict callers. This experimental endpoint is plain
HTTP with no authentication and exposes the node's enabled APIs, including debug and trace. WebSocket listens at `ws://<vm-fqdn>:8546`
when `WS_ENABLED=true`; set it to false to disable the listener. The same public
RPC toggle and source CIDR apply to both ports.
Keep credentials out of RPC URLs/tags. Public RPC defaults to disabled.

Toggle an existing deployment without restarting the observer or affecting sync:

```bash
ENV_FILE=.env.observer-1 bash scripts/set-observer-rpc.sh enable
ENV_FILE=.env.observer-1 bash scripts/set-observer-rpc.sh disable
```

The command updates the dedicated VM NSG rule and discovery tags. Disabling installs
an explicit inbound deny for TCP 8545 and 8546; Azure NSGs may allow existing TCP sessions to
continue until closed. If a separate proxy or ingress also exposes RPC, disable
that route separately. Update `PUBLIC_RPC_ENABLED` in the settings file as well to
keep future deployments consistent. To disable on a new deployment, set it to false.
Snapshot restores use the destination observer's ingress settings; snapshots do not
copy the source VM's networking or identity. This lets a synced seed observer supply
snapshot pairs while each clone gets a separate identity and public monitoring URL.


### Update VM RPC settings without rebuilding the node

The VM mounts `/etc/telcoin/entrypoint.sh` into the container, so RPC startup options
can change without compiling Rust or rebuilding the image. Deployment copies
`HTTP_API`, `WS_ENABLED`, and `WS_API` from the observer's `.env` settings into
`/etc/telcoin/observer.env`. For an existing VM, update those three settings in
that VM file and restart `telcoin-observer` to apply them. Changing only the local
`.env` does not change a running VM. Keep the other VM environment values intact.

Opening or closing public ingress with `scripts/set-observer-rpc.sh` needs no
restart. Enabling the WebSocket listener or changing API namespaces does require
a service restart. The observer currently runs the original release; during
catch-up a restart can still encounter the checkpoint bug documented in
`infra/pool/adiri-recovery.json`.


### Private Adiri observer pool

The current observer lives in `oprail-observer-adiri` and is deliberately
deallocated. Its disks are retained; the internal load balancer uses `10.52.0.10`
on ports 8545/8546. `oprail-api-adiri` has a peered VNet ready for future APIs.
Public observer RPC is disabled. `adiri-orbs.oprail.com` is reserved in the plan
but no DNS record has been created. TelExplorer/indexer and `telscan.io` were
not modified. The original observer resources were retired after a validated
snapshot restore.

See [the pool operations runbook](docs/operations/adiri-observer-pool.md) for
startup/shutdown, readiness, digest-pinned releases, rolling upgrades, snapshot
retention, node add/remove and the opt-in automation scaffold. All lifecycle
mutations require `--apply`; scheduled automation remains disabled.

### Observer management website

The separate [.NET/Aspire management site](docs/operations/observer-management.md) provides Microsoft-authenticated fleet status and approval-gated lifecycle requests. Website releases deploy only the management application; observer provisioning remains an explicit operation. See the runbook for Entra, private networking, runner and environment approval setup. Operations default to disabled.
