# ============================================================================
# Builder Stage - Contains all build dependencies and builds the binary
# ============================================================================
FROM rust:1.94-slim-bookworm AS builder

ENV RUSTUP_TOOLCHAIN=1.94

# Build arguments for git ref and repository URL
ARG NETWORK_FEATURES=adiri
ARG GIT_BRANCH=main
ARG GIT_TAG=v0.17.0-adiri
ARG GIT_REPO_URL=https://github.com/telcoin-association/telcoin-network.git
ENV GIT_BRANCH=${GIT_BRANCH}
ENV GIT_TAG=${GIT_TAG}
ENV GIT_REPO_URL=${GIT_REPO_URL}

# Install build dependencies
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential \
    git \
    cmake \
    pkg-config \
    libssl-dev \
    libclang-16-dev \
    libapr1-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Clone telcoin-network repository (prefer tag when provided, otherwise branch)
WORKDIR /app
RUN set -eu; \
    REF_TO_BUILD="${GIT_BRANCH}"; \
    if [ -n "${GIT_TAG}" ]; then REF_TO_BUILD="${GIT_TAG}"; fi; \
    echo "Cloning ${GIT_REPO_URL} at ${REF_TO_BUILD}"; \
    git clone "${GIT_REPO_URL}" /app/telcoin-network; \
    cd /app/telcoin-network; \
    git checkout --detach "${REF_TO_BUILD}"; \
    if [ -n "${GIT_TAG}" ]; then \
      test "$(git rev-parse HEAD)" = "$(git rev-parse "refs/tags/${GIT_TAG}^{commit}")"; \
    fi; \
    git log -1 --oneline

# Fix submodule URL for tn-contracts (replace ssh with https)
# Need to fix both .gitmodules and .git/config, and handle case variations
WORKDIR /app/telcoin-network
RUN echo "Fixing submodule URLs..." && \
    # Fix .gitmodules file (case-insensitive, handle both Telcoin-Association and telcoin-association)
    sed -i 's|git@github.com:[Tt]elcoin-[Aa]ssociation/tn-contracts.git|https://github.com/telcoin-association/tn-contracts.git|g' .gitmodules 2>/dev/null || true && \
    sed -i 's|ssh://git@github.com/[Tt]elcoin-[Aa]ssociation/tn-contracts.git|https://github.com/telcoin-association/tn-contracts.git|g' .gitmodules 2>/dev/null || true && \
    # Also fix .git/config if it exists
    sed -i 's|url = git@github.com:[Tt]elcoin-[Aa]ssociation/tn-contracts.git|url = https://github.com/telcoin-association/tn-contracts.git|g' .git/config 2>/dev/null || true && \
    sed -i 's|url = ssh://git@github.com/[Tt]elcoin-[Aa]ssociation/tn-contracts.git|url = https://github.com/telcoin-association/tn-contracts.git|g' .git/config 2>/dev/null || true && \
    echo "Submodule URLs fixed. Current .gitmodules:" && \
    cat .gitmodules || echo "No .gitmodules file found"

# Initialize submodules (try multiple methods)
RUN echo "Initializing submodules..." && \
    git submodule update --init --recursive || \
    (echo "Direct git submodule failed, trying make..." && \
     make init-submodules 2>&1 || true) && \
    echo "Submodule initialization complete"

# Verify submodules are initialized
RUN echo "Checking submodule status..." && \
    git submodule status && \
    echo "Submodules initialized successfully"

# Build the telcoin-network node
# --features adiri enables adiri support for testnet (adiri)
# Set CARGO_NET_GIT_FETCH_WITH_CLI=true to avoid issues with git operations
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
WORKDIR /app/telcoin-network
RUN if [ -n "$NETWORK_FEATURES" ]; then \
      cargo build --bin telcoin-network --release --features "$NETWORK_FEATURES"; \
    else \
      cargo build --bin telcoin-network --release; \
    fi

# ============================================================================
# Runtime Stage - Minimal image with only the binary and runtime dependencies
# ============================================================================
FROM debian:bookworm-slim

# Install only runtime dependencies (no build tools)
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    libapr1 \
    libssl3 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create app directories
WORKDIR /app
RUN mkdir -p /app/bin /app/data

# Copy the binary from builder stage
COPY --from=builder /app/telcoin-network/target/release/telcoin-network /app/bin/telcoin-network

# Copy entrypoint script
COPY src/docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Set default environment variables
ENV DATADIR=/app/data
ENV PRIMARY_LISTENER_MULTIADDR=/ip6/::/udp/49590/quic-v1
ENV WORKER_LISTENER_MULTIADDR=/ip6/::/udp/49594/quic-v1
ENV CHAIN=adiri

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]