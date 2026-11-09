using System.Net.Sockets;

var builder = DistributedApplication.CreateBuilder(args);

// Parameters are overridable at deploy time to create multiple instances with different settings.
var image = builder.Configuration["TELCOIN_IMAGE"] ?? "oprailtelcoin.azurecr.io/telcoin-observer:latest";
var chain = builder.AddParameter("chain", "adiri");
var dataDir = builder.AddParameter("data-dir", "/app/data");
var primaryListener = builder.AddParameter("primary-listener", "/ip4/0.0.0.0/udp/49590/quic-v1");
var workerListener = builder.AddParameter("worker-listener", "/ip4/0.0.0.0/udp/49594/quic-v1");
var blsPassphrase = builder.AddParameter("tn-bls-passphrase", secret: true);

builder.AddContainer("telcoin-observer", image)
    .WithEnvironment("CHAIN", chain)
    .WithEnvironment("DATADIR", dataDir)
    .WithEnvironment("PRIMARY_LISTENER_MULTIADDR", primaryListener)
    .WithEnvironment("WORKER_LISTENER_MULTIADDR", workerListener)
    .WithEnvironment("TN_BLS_PASSPHRASE", blsPassphrase)
    // Runtime volume for local orchestration. Azure deployment is handled via scripts/deploy-azure-aci.sh.
    .WithVolume(target: "/app/data", name: "telcoin-data")
    .WithEndpoint(name: "rpc", port: 8545, targetPort: 8545)
    .WithEndpoint(name: "primary-quic", port: 49590, targetPort: 49590, protocol: ProtocolType.Udp)
    .WithEndpoint(name: "worker-quic", port: 49594, targetPort: 49594, protocol: ProtocolType.Udp);

builder.Build().Run();
