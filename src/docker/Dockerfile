# ============================================================================
# Builder Stage - Contains all build dependencies and builds the binary
# ============================================================================
FROM ubuntu:24.04 AS builder

USER root
ENV USER=root

# Build argument for git branch/tag (defaults to main)
ARG GIT_BRANCH=main
ENV GIT_BRANCH=${GIT_BRANCH}

# Install build dependencies
RUN apt-get update \
    && apt-get install -y \
    apt-utils \
    build-essential \
    git \
    curl \
    pkg-config \
    libssl-dev \
    libssl3 \
    clang \
    cmake \
    gcc \
    protobuf-compiler \
    libprotobuf-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl https://sh.rustup.rs -sSf > /tmp/rustup-init.sh \
    && chmod +x /tmp/rustup-init.sh \
    && sh /tmp/rustup-init.sh -y \
    && rm -rf /tmp/rustup-init.sh
ENV PATH="/root/.cargo/bin:${PATH}"

# Update the local crate index
RUN /root/.cargo/bin/cargo search

# Ensure stable Rust is installed and set as default (required: Rust 1.91+)
RUN /root/.cargo/bin/rustup update stable \
    && /root/.cargo/bin/rustup default stable \
    && /root/.cargo/bin/rustc --version

# Install nightly rust (optional, for future use)
RUN /root/.cargo/bin/rustup install nightly

# Clone telcoin-network repository (with specified branch)
WORKDIR /app
RUN echo "Cloning telcoin-network repository from branch/tag: ${GIT_BRANCH}" && \
    git clone --branch ${GIT_BRANCH} https://github.com/telcoin-association/telcoin-network.git /app/telcoin-network || \
    (echo "Branch/tag ${GIT_BRANCH} not found, cloning default branch and checking out..." && \
     git clone https://github.com/telcoin-association/telcoin-network.git /app/telcoin-network && \
     cd /app/telcoin-network && \
     git checkout ${GIT_BRANCH} 2>/dev/null || echo "Using default branch (${GIT_BRANCH} not found)") && \
    echo "Repository cloned. Current branch/commit:" && \
    cd /app/telcoin-network && git log -1 --oneline

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

# Build the telcoin-network node with redb feature
# Set CARGO_NET_GIT_FETCH_WITH_CLI=true to avoid issues with git operations
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
WORKDIR /app/telcoin-network
RUN echo "Starting cargo build with redb feature..." && \
    /root/.cargo/bin/cargo build --bin telcoin-network --release --features redb

# ============================================================================
# Runtime Stage - Minimal image with only the binary and runtime dependencies
# ============================================================================
FROM ubuntu:24.04

USER root
ENV USER=root

# Install only runtime dependencies (no build tools)
RUN apt-get update \
    && apt-get install -y \
    ca-certificates \
    libssl3 \
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