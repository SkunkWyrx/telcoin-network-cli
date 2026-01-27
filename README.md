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
# Set GIT_BRANCH in your .env file, or:
GIT_BRANCH=develop docker compose -f src/docker/docker-compose.yml build
```

**Examples:**

- `GIT_BRANCH=main` - Build from main branch (default)
- `GIT_BRANCH=develop` - Build from develop branch
- `GIT_BRANCH=v1.0.0` - Build from a specific tag
- `GIT_BRANCH=feature-branch` - Build from a feature branch

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
docker build --build-arg GIT_BRANCH=develop -t telcoin-observer:latest -f src/docker/Dockerfile .

# Build from a specific tag
docker build --build-arg GIT_BRANCH=v1.0.0 -t telcoin-observer:latest -f src/docker/Dockerfile .

# Build from a feature branch
docker build --build-arg GIT_BRANCH=feature-branch -t telcoin-observer:latest -f src/docker/Dockerfile .
```

#### Step 2: Start the Node

**With environment variable passphrase** (non-interactive):

```bash
docker run -d \
  --name telcoin-observer \
  --restart unless-stopped \
  -v telcoin-data:/app/data \
  -e TN_BLS_PASSPHRASE="your-secure-passphrase" \
  -p 8545:8545 \
  telcoin-observer:latest
```

**With interactive passphrase** (prompts for passphrase):

```bash
docker run -it \
  --name telcoin-observer \
  --restart unless-stopped \
  -v telcoin-data:/app/data \
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
| `GIT_BRANCH`                 | No       | `main`                      | Git branch or tag to build from (e.g., `main`, `develop`, `v1.0.0`)                                                                                      |
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
docker volume inspect telcoin-data
```

**Remove volume** (⚠️ This deletes all data):

```bash
docker volume rm telcoin-data
```

**Backup volume**:

```bash
docker run --rm -v telcoin-data:/data -v $(pwd):/backup ubuntu tar czf /backup/telcoin-backup.tar.gz -C /data .
```

**Restore volume**:

```bash
docker run --rm -v telcoin-data:/data -v $(pwd):/backup ubuntu tar xzf /backup/telcoin-backup.tar.gz -C /data
```

## Passphrase Methods

### Method 1: Environment Variable (Non-Interactive)

Set the `TN_BLS_PASSPHRASE` environment variable:

```bash
docker run -d \
  -v telcoin-data:/app/data \
  -e TN_BLS_PASSPHRASE="your-secure-passphrase" \
  telcoin-observer
```

**Security Note**: Environment variables may be visible to other processes. Only use this for testing or with secure secret managers (e.g., systemd credentials, Docker secrets).

### Method 2: Interactive (Secure)

Run with `-it` flags to enter passphrase interactively:

```bash
docker run -it \
  -v telcoin-data:/app/data \
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
   docker run -it -v telcoin-data:/app/data telcoin-observer:latest
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
GIT_BRANCH=develop docker compose -f src/docker/docker-compose.yml build

# Build from a specific tag
GIT_BRANCH=v1.0.0 docker compose -f src/docker/docker-compose.yml build

# Or set in .env file
echo "GIT_BRANCH=develop" > .env
docker compose -f src/docker/docker-compose.yml build
```

**Using Docker CLI:**

```bash
# Build from develop branch
docker build --build-arg GIT_BRANCH=develop -t telcoin-observer:latest -f src/docker/Dockerfile .

# Build from a specific tag
docker build --build-arg GIT_BRANCH=v1.0.0 -t telcoin-observer:latest -f src/docker/Dockerfile .
```

**Note:** The build will automatically try the specified value as a branch first, then as a tag, and finally fall back to the default branch if neither is found.

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
  -v telcoin-data:/app/data \
  -e CHAIN=mainnet \
  telcoin-observer
```

### Custom Ports

```bash
docker run -d \
  -v telcoin-data:/app/data \
  -p 8545:8545 \
  -p 49590:49590/udp \
  -p 49594:49594/udp \
  telcoin-observer
```

## System Requirements

- Docker 20.10+
- At least 4GB RAM available to Docker
- At least 20GB disk space (for build and chain data)
- Stable internet connection

## What's Included

The image includes:

- Ubuntu 24.04 LTS base
- Rust toolchain (stable + nightly)
- All required build dependencies
- Pre-built `telcoin-network` binary with `redb` feature
- Entrypoint script for automatic key generation and node startup

## Security Considerations

1. **Passphrase Storage**: Never commit passphrases to version control
2. **Volume Security**: Protect your Docker volumes - they contain sensitive keys
3. **Network Exposure**: Only expose ports you need (RPC on 8545)
4. **Image Source**: Build from trusted source or verify the Dockerfile

## Publishing and Distribution

For information on publishing this image to Docker Hub, GitHub Container Registry, or other registries, see [DISTRIBUTION.md](DISTRIBUTION.md).

Quick publish commands:

```bash
# Publish to Docker Hub
DOCKER_USERNAME=yourusername ./scripts/publish-dockerhub.sh v1.0.0

# Publish to GHCR
GITHUB_USERNAME=yourusername GITHUB_TOKEN=your_token ./scripts/publish-ghcr.sh v1.0.0
```

## Support

For issues related to:

- **Docker image**: Check this repository
- **Telcoin Network**: See [Telcoin Network documentation](https://github.com/telcoin-association/telcoin-network)
