# Observer HTTPS proof

The proof runs Nginx beside the observer Docker container. It binds only
`127.0.0.1:18443`, forwards POST requests to local RPC on `127.0.0.1:8545`,
and removes its temporary container and certificate on exit. It does not
change the observer image, service, disks, NSG, DNS or load balancer.
The pulled Nginx image remains in Docker's image cache.

On Observer 1, run the proof script with the checked-in Nginx config on
standard input (both files must be copied to the VM):

```bash
sudo bash prove-observer-tls.sh < proof-nginx.conf
```

The curl test validates the test certificate and hostname using an explicit
trust anchor and checks for Adiri chain ID `0x7e1`. The existing readiness
endpoint is reported separately; HTTPS can work while the node is syncing.

## Before any public rollout

- Use a publicly trusted certificate for the RPC hostname and arrange
  renewal. The one-day self-signed proof certificate is only for this test.
- Put a restricted RPC listener or method-filtering proxy behind HTTPS.
  This proof forwards all POST bodies to the existing listener, which can
  include debug and trace methods. It is not a public proxy configuration.
- Keep the existing private load balancer. Test a separate public frontend
  mapping TCP 443 to the proxy and probing HTTP 9090 at `/readyz`. A failed
  probe stops new connections; also check existing connections during drain.
- Keep direct public access to observer RPC blocked. Standard Load Balancer
  preserves client IP, so an NSG client-source rule alone cannot distinguish
  the public load-balancer path from a node's own public IP. For the initial
  public test, use a private-only backend after verifying private admin access,
  outbound internet access and peer connectivity. Test direct access failure
  before publishing DNS.
- Before DNS cutover, use curl with `--resolve` and the load balancer IP to
  verify HTTPS, check that a synced observer answers, and verify that draining
  the test observer stops new connections.

## Readiness and RPC isolation proof

Run on the VM as root:

```bash
sudo bash prove-observer-tls-gate.sh < gated-proof-nginx.conf
```

This uses the real local Adiri RPC and a loopback-only readiness fixture.
It simulates unready, ready, and draining states without changing the node's
real readiness marker or telling an Azure load balancer that the node is
synced. The TLS proxy must return 503, then 200, then 503. The client sends a
debug method on purpose: in this proof the proxy discards the caller's body
and issues only a fixed `eth_chainId` request. That is a test harness, not a
general public JSON-RPC API or a production allowlist.

## Adding nodes once the public endpoint is enabled

Provision one proxy container per public backend as a separate service from
the observer, with the public hostname's certificate and key mounted read
only. Keep the key out of the image and give the renewal process a way to
reload each proxy. Leave the node out of public traffic until the proxy is
running and the observer has passed sync checks. A snapshot restore must
configure the proxy separately; the chain data snapshots are not a
distribution mechanism for certificates or proxy configuration.

For Azure acceptance, test from a client outside the VNet with the temporary
public rule limited to that client's address. Verify a healthy response
through the frontend, then drain and test a fresh connection. Test the node
addresses directly as well. The local fixture cannot prove Azure's probe
behavior, routing, or network isolation.

## Transparent debug and transaction RPC proof

Run on Observer 1 with the transparent (not gated fixture) config:

```bash
sudo bash prove-observer-rpc-passthrough.sh < proof-nginx.conf
```

The script pins a block or transaction from the local chain, compares the
debug trace from plain RPC and verified TLS, and checks that request IDs
survive the proxy. It also compares rejection of the empty raw transaction
`0x` on both paths. This cannot broadcast a valid transaction.

For a forwarding proof, use a funded disposable Adiri account and a synced
observer, send one valid signed transaction through the proxy, and verify
the receipt on an independent reference RPC. Do not submit that transaction
through another endpoint; otherwise the receipt cannot distinguish the
paths. A receipt verifies inclusion through the tested submission path
but does not identify which validator received the forwarded transaction.

Run the client-side acceptance checks with
`python3 scripts/check-public-observer-rpc.py ready --frontend <public-ip> --direct <observer-public-ip>`.
Repeat with `drained` after the one-node test backend is marked unhealthy,
and with `recovered` after readiness and the Azure probe recover. Omit
`--direct` only for VMs with no public address. For a temporary certificate,
pass its trust anchor via `--ca-file`; repeat without that option to prove
public certificate trust. Run the separate DNS resolution check after the
record is published.

On 2026-11-06, Observer 1 did not pass the preflight: the reference returned
Adiri chain ID 2017, the observer reported an 8,210-block gap during catch-up,
and the service restarted repeatedly with a consensus output gap (expected
7929819, received 7941524). The public frontend, DNS and disks were left
unchanged. Investigate the deployed build and the related upstream observer
catch-up fix [#1561](https://github.com/Telcoin-Association/telcoin-network/pull/1561)
before using this node for ingress acceptance.

### Restricted Azure frontend result

On 2026-11-06, a temporary Standard Load Balancer used the existing observer
NIC as a second backend membership, probed the real `/readyz` endpoint, and
forwarded TCP 443 with Floating IP enabled. Nginx listened only on the
temporary frontend IPv4 address configured on loopback. The inbound rule
allowed only the test client's /32 to that frontend address on 443. This
kept the observer's public peer IP and existing private endpoint in place.

An external client verified the certificate against the test trust anchor
and got Adiri chain ID 2017 through the frontend. From the same client, direct
connections to the observer's own public IP on ports 443, 8545, 8546, and
18443 failed. The temporary IP, load balancer, backend membership, rule,
listener, and test certificate were removed. This verified restricted
routing and direct-access isolation; public certificate trust, DNS,
drain/recovery, and signed transaction forwarding remain separate checks.

A follow-up used the same restricted Azure setup to exercise the actual
maintenance flag. Before draining, HTTPS returned chain ID 2017. During
maintenance `/readyz` returned 503, and three fresh attempts to connect to
the public frontend failed. After clearing maintenance, `/readyz` returned
200 at zero lag and HTTPS returned chain ID 2017 again. Direct VM ports
443, 8545, 8546, and 18443 remained unreachable in all three phases. All
temporary resources were removed and Observer 1 was left undrained.

The signed forwarding proof also passed on 2026-11-06. A zero-value
self-transfer from a disposable Adiri account was submitted once, only
through the restricted public HTTPS frontend. The observer returned the
expected hash
`0x84dddde79476ea1524727f993f8e20c095952af78ac0614e378ef13b43a01e1c`.
The independent reference RPC returned a successful receipt at block
525077 (21,000 gas, 147,000 wei fee). The temporary frontend and guest
listener were removed. This proves inclusion through the observer path;
it does not identify the specific validator that received the transaction.

## Permanent Adiri frontend

The permanent Standard Load Balancer `adiri-observers-public` has static
public IPv4 `20.12.235.224`. Its Floating IP rule sends TCP 443 to the
Nginx sidecar bound to that IP only. The public rule probes the sidecar
on private port 19091, which forwards to the observer's real `/readyz`.
Both the proxy and the observer must be responsive for this probe to pass.
The private load balancer remains separate.

Observer 1 runs `telcoin-rpc-tls.service` with a digest-pinned Nginx image,
a rendered config, and a root-only certificate directory at
`/etc/telcoin/rpc-tls`. The Let’s Encrypt certificate for
`adiri-orbs.oprail.com` was verified using public trust before DNS cutover.
The first issuance was manual. Automatic renewal for Observer 1 is now
configured as described below. Never put the key in an image or in git.

The `Observer-Public-HTTPS` rule now allows Internet clients to the frontend's
IPv4 /32 on TCP 443. The observer's own public address does not listen for
HTTPS and its raw RPC ports remain blocked. The HTTPS listener passes all
enabled HTTP JSON-RPC methods through unchanged. No WebSocket route is configured by this
HTTP JSON-RPC listener.

### Automatic certificate renewal

GoDaddy delegates only `_acme-challenge.adiri-orbs.oprail.com` to the Azure
public DNS zone of the same name. The RPC A record stays at GoDaddy. Observer
1's Azure managed identity has DNS Zone Contributor scoped only to that
challenge zone. The Certbot hook creates a single apex TXT record, waits
for all four Azure nameservers, and removes only its own record.

The `oprail-rpc-renew.timer` checks twice daily on Observer 1. Certbot uses
its private state in `/var/lib/oprail-acme`. After issuance, the deploy hook
checks the certificate/key pair, reloads Nginx, verifies the live fingerprint,
and restores the prior files if verification fails. Check the timer and
`journalctl -u oprail-rpc-renew.service` when diagnosing renewal. The timer
cannot run while the VM is deallocated. Observer 1 is the sole issuer. The renewal service publishes the active
bundle to the dedicated `oprail-adiri-rpc-tls` Key Vault after each
successful renewal check, without creating a new secret version when the
bundle has not changed. Follower VMs have read access to that secret only
and check for updates every 30 minutes. Their private keys never pass
through the fleet controller or a build log.

`fleet.py --apply add <env-file>` restores a completed pair into a new node
and waits for private sync first. If the pool has public RPC configured,
it installs the digest-pinned proxy, validates the certificate over TLS,
and admits the NIC to the public backend pool last. If public setup fails
after the private node is recorded, retry with
`fleet.py --apply install-public-rpc <node-name>`.
An operator can try a pinned candidate with `add --candidate-image <image>`;
this never changes the promoted release or replaces an existing node.
Record the exact trial image and commit in `infra/pool/adiri-recovery.json`
until controlled restart and second-backend tests pass.

The two-node live failover test passed on 2026-11-06. After Observer 1
was drained and its observer container was confirmed stopped, five
fresh public HTTPS requests succeeded through Observer 2. Observer 1
then restarted on recovery build `6955e3b8`, passed three fresh sync
checks, and was undrained. Both nodes matched Adiri at block 525290
and the public endpoint passed again. No VM or disk was created or
restored for this test. The verified candidate is recorded in
`infra/pool/adiri-recovery.json`; promotion is a separate operation.
