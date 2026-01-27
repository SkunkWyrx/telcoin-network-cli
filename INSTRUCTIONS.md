Telcoin Network – Observer Node Guide 
An observer node on the Telcoin Network:
Syncs the chain


Participates in gossip / consensus observation


Exposes RPC & HTTP endpoints


Does not produce blocks


Does not earn rewards (currently)


Does not require staking


Ideal for:
Learning the network


Monitoring health


Running explorers, analytics, or local RPC







1. System Requirements
Ubuntu 24.04 LTS


Stable internet connection (IPv4 or IPv6)


No static IP required


No router port forwarding required



2. System Prep (One-Time)
sudo apt update && sudo apt upgrade -y
sudo apt install -y build-essential git curl pkg-config libssl-dev clang cmake


3. Install Rust (Required: Rust 1.91+)
curl https://sh.rustup.rs -sSf | sh
source ~/.cargo/env

Verify version:
rustc --version

If below 1.91:
rustup update stable
rustup default stable




4. Clone Repo & Init Submodules
git clone https://github.com/telcoin-association/telcoin-network.git
cd telcoin-network

Initialize all submodules (required): 
Important before running below: edit .git/config file and replace the tn-contracts url at the bottom with the http version (https://github.com/telcoin-association/tn-contracts.git) save and then run below:
make init-submodules
# or
git submodule update --init --recursive


5. Build the Node (Important Temporary Flag)
⚠️ Temporary requirement
 Until an upcoming database refactor lands, the node must be built with the redb feature.
cargo build --bin telcoin-network --release --features redb

Binary location:
target/release/telcoin-network

ℹ️ This workaround will be removed once the DB issue is resolved upstream.




6. Choose a Data Directory
Pick one permanent directory and reuse it everywhere.
mkdir -p ~/telcoin-observer
We’ll refer to this as:
DATADIR=~/telcoin-observer


7. Generate Observer Keys & Config
This step:
Creates config files
Generates a BLS key
Sets an execution address (required but unused for observers)


target/release/telcoin-network keytool generate observer \
  --datadir ~/telcoin-observer \
  --address 0x4444444444444444444444444444444444444444 \
  --bls-passphrase-source ask

Notes
The execution address:


❌ Does not receive rewards
✅ Must still be set
Can be a dummy or real address


You will be prompted to set a BLS passphrase


Required on every startup
Or supplied via environment variable (see below)



8. (Optional) Use Env Var for BLS Passphrase
Instead of interactive input:
export TN_BLS_PASSPHRASE="your-secure-passphrase"
Then use:
--bls-passphrase-source env
⚠️ Security note
 Environment variables may be visible to other processes.
 Only use this for testing or secure secret managers (e.g. systemd credentials).

9. Network Binding (Critical Step)
By default, the node binds to localhost only, which prevents it from reaching the external network.
You must explicitly set listener addresses.
Recommended (IPv6 – NAT-free, dynamic-safe)
export PRIMARY_LISTENER_MULTIADDR=/ip6/::/udp/49590/quic-v1
export WORKER_LISTENER_MULTIADDR=/ip6/::/udp/49594/quic-v1
Alternative (IPv4)
export PRIMARY_LISTENER_MULTIADDR=/ip4/0.0.0.0/udp/49590/quic-v1
export WORKER_LISTENER_MULTIADDR=/ip4/0.0.0.0/udp/49594/quic-v1
Notes:
udp + quic-v1 are mandatory


Ports can be changed if needed


:: / 0.0.0.0 means “listen on all interfaces”


No router port forwarding required for observer nodes



10. Start the Observer Node (Adiri Testnet)
Interactive passphrase
target/release/telcoin-network node -vvv \
  --http \
  --observer \
  --chain adiri \
  --bls-passphrase-source ask \
  --datadir ~/telcoin-observer

Env-based passphrase
target/release/telcoin-network node -vvv \
  --http \
  --observer \
  --chain adiri \
  --bls-passphrase-source env \
  --datadir ~/telcoin-observer


11. Verify It’s Working
You should see:
Peer discovery
Gossip traffic
Consensus sync logs
RPC startup messages


Test local RPC:
curl 127.0.0.1:8545 \
  -X POST \
  -H "Content-Type: application/json" \
  --data '{"method":"eth_chainId","params":[],"id":1,"jsonrpc":"2.0"}'
A valid response confirms the observer node is live.

12. Firewall & Router Notes (Observer Nodes)
❌ No router port forwarding required


❌ No static IP required


❌ No IPv4 required


✅ Outbound UDP is sufficient


✅ IPv6-only works


Inbound UDP ports are optional and only needed for:
Validators


Public RPC


Infrastructure-grade peers



13. Recommended Next Steps (Separate Docs)
Once confirmed working:
Run as a systemd service


Secure BLS passphrase via credentials


Enable auto-restart


Monitor peer count & sync health




Summary (Quick Checklist)
✅ Ubuntu 24.04


✅ Rust ≥ 1.91


✅ --features redb (temporary)


✅ Explicit libp2p listener addresses


✅ No router changes required


✅ IPv6-first friendly

