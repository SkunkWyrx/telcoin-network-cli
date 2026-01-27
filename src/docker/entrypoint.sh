#!/bin/bash
set -e

# Default values
DATADIR=${DATADIR:-/app/data}
CHAIN=${CHAIN:-adiri}
PRIMARY_LISTENER_MULTIADDR=${PRIMARY_LISTENER_MULTIADDR:-/ip6/::/udp/49590/quic-v1}
WORKER_LISTENER_MULTIADDR=${WORKER_LISTENER_MULTIADDR:-/ip6/::/udp/49594/quic-v1}

# Binary path (moved to /app/bin after build cleanup)
BINARY="/app/bin/telcoin-network"

# Ensure data directory exists
mkdir -p "$DATADIR"

# Determine passphrase source (needed for both key generation and node startup)
if [ -n "$TN_BLS_PASSPHRASE" ]; then
    PASSPHRASE_SOURCE="env"
else
    PASSPHRASE_SOURCE="ask"
fi

# Check if observer keys/config already exist
# Check for key files in the node-keys directory
# All three files must exist: bls.kw, primary.seed, and worker.seed
if [ ! -f "$DATADIR/node-keys/bls.kw" ] || [ ! -f "$DATADIR/node-keys/primary.seed" ] || [ ! -f "$DATADIR/node-keys/worker.seed" ]; then
    echo "Observer keys/config not found. Generating new keys..."
    
    if [ "$PASSPHRASE_SOURCE" = "env" ]; then
        echo "Using BLS passphrase from environment variable"
    else
        echo "Using interactive BLS passphrase (requires -it flag)"
    fi
    
    # Generate observer keys
    # For interactive mode, ensure we can read from terminal
    if [ "$PASSPHRASE_SOURCE" = "ask" ]; then
        # Check if we have a TTY available
        if [ -t 0 ]; then
            echo "Ready for passphrase input..." >&2
            "$BINARY" keytool generate observer \
                --datadir "$DATADIR" \
                --address 0x4444444444444444444444444444444444444444 \
                --bls-passphrase-source "$PASSPHRASE_SOURCE"
        else
            echo "ERROR: No TTY available for interactive passphrase input." >&2
            echo "Please use one of the following:" >&2
            echo "  1. Set TN_BLS_PASSPHRASE environment variable" >&2
            echo "  2. Use 'docker compose run' instead of 'docker compose up'" >&2
            echo "  3. Use 'docker run -it' directly" >&2
            exit 1
        fi
    else
        "$BINARY" keytool generate observer \
            --datadir "$DATADIR" \
            --address 0x4444444444444444444444444444444444444444 \
            --bls-passphrase-source "$PASSPHRASE_SOURCE"
    fi
    
    echo "Observer keys generated successfully"
else
    echo "Observer keys/config already exist. Skipping key generation."
fi

# Export network listener addresses for the node
export PRIMARY_LISTENER_MULTIADDR
export WORKER_LISTENER_MULTIADDR

# Start the observer node
echo "Starting Telcoin Network observer node..."
echo "Chain: $CHAIN"
echo "Data directory: $DATADIR"
echo "Primary listener: $PRIMARY_LISTENER_MULTIADDR"
echo "Worker listener: $WORKER_LISTENER_MULTIADDR"

exec "$BINARY" node -vvv \
    --http \
    --observer \
    --chain "$CHAIN" \
    --bls-passphrase-source "$PASSPHRASE_SOURCE" \
    --datadir "$DATADIR"
