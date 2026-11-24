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

GoDaddy delegates `_acme-challenge.adiri-orbs.oprail.com` and
`_acme-challenge.rpc-adiri.oprail.com` to their corresponding Azure public
DNS zones. The RPC A records are managed at GoDaddy. Observer 1's Azure
managed identity has DNS Zone Contributor scoped to the two challenge zones.
The `adiri-rpc-dual` certificate covers both RPC hostnames. The Certbot hook
creates a single apex TXT record in the selected zone, waits
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

### Public readiness and rate limits

The public proxy probes `/public-readyz` on the local health service. A responsive
node on the expected chain remains eligible when it falls behind or the reference
RPC is unavailable. A maintenance drain, wrong chain, or local RPC failure returns
HTTP 503. The private `/readyz` and rollout `/status` retain their strict sync
checks. This public policy can keep multiple lagging observers eligible.

Each proxy limits HTTP requests to 30 requests/second per client IP with a burst
of 60, and 200 requests/second per backend with a burst of 400. Excess requests
receive HTTP 429. These limits apply independently on each observer and count HTTP
requests, including a JSON-RPC batch as one request.

### Public endpoint alerts

`scripts/pool/rpc_monitor.py` runs on the operations runner using
`infra/pool/adiri-rpc-monitor.service` and its one-minute timer. Install the script
and `health.py` under `/usr/local/lib/oprail-rpc-monitor/`. The runner identity
requires permission to publish Azure Monitor custom metrics. The alert template
`infra/pool/adiri-rpc-alerts.bicep` references the existing `adiri-rpc-alerts` action
group and runner VM.

Three consecutive samples of a public RPC failure or lag of at least five blocks
publish an incident metric. Stateful Azure Monitor alerts notify on activation
and resolution. A reference failure omits the lag metric. Alert evaluation runs
every minute over a five-minute window, so delivery and recovery include Azure
Monitor processing time. The timer does not send an email on every sample.

### Named RPC API keys

The optional `public_rpc.api_keys` configuration adds tiers to the existing HTTPS
proxy. Anonymous clients continue to use the public RPC without a key. Supply a
key only in the `X-API-Key` header; URL/query keys are not supported. Keys are
intended for server-side applications, where they can be kept private.

| Tier | Sustained requests/s | Burst | Counter |
|---|---:|---:|---|
| Anonymous | 30 | 60 | Client IP |
| Partner | 100 | 200 | Key ID |
| Internal | 300 | 600 | Key ID |
| All traffic | 500 | 400 | Observer backend |

All limits apply **per observer**, including the combined cap. They count HTTP
requests, so one JSON-RPC batch counts as one request. Multiple keys have separate
budgets; sharing one key also shares its budget. The backend cap can limit any
tier when combined traffic is high. Legacy deployments without API-key settings
retain the original anonymous 30/s and backend 200/s configuration.

The management panel's **RPC API keys** section creates a named partner/internal
key, with optional expiration, and displays its random 256-bit value once. Copy
it before hiding it or leaving the page. There is no endpoint to retrieve an
existing key; create a replacement and revoke the old key when rotating. Key
listing exposes metadata only. Mutations use the existing owner-only Microsoft
sign-in, Azure authorization checks, and CSRF protection. Key responses are
`Cache-Control: no-store`.

Each key is an independent secret in a dedicated vault, separate from TLS keys.
The app's system-assigned identity has Secrets Officer on that vault. Observers
have Secrets User; the runner can delegate only that reader role at this vault.
API key values never enter GitHub inputs, operation history, logs, or tracked
configuration. The NGINX map/cache on a VM necessarily contains credentials and
is root-readable only. NGINX strips the header before forwarding to the observer.

Observers refresh the vault about every minute and apply a tested graceful proxy
reload. No observer restart or database change is needed. Expiration and revocation
normally propagate within one refresh cycle plus request time; in-flight requests
are not canceled. Invalid/expired/revoked keys return HTTP 401 rather than falling
back to the anonymous tier. Rate violations return HTTP 429. During a vault outage,
the refresh task uses only a successful cache no older than five minutes and still
filters expired keys. Once the cache is stale, it removes keyed access while
retaining anonymous access. This depends on the refresh timer continuing to run;
check its service journal when troubleshooting. Updates validate NGINX configuration
and restore the prior file on a validation/reload failure.

Deployment order for Adiri:

1. Prepare the dedicated vault, management identity, and current observer readers:
   `python3 scripts/configure-rpc-api-keys.py --network adiri --apply`.
   Without `--apply`, it prints a plan. It preserves existing image-pull identities,
   sets `RpcKeys__Vault`, and records `OBSERVER_RPC_KEY_VAULT` in the website's
   GitHub environment. It does not restart observers.
2. Merge the tested change and approve the management-site release. This adds the
   key-management UI; it does not modify nodes.
3. Dispatch **Observer management operation** with network `adiri`, operation
   `rpc-keys-sync`, and argument `cluster`, and approve the environment gate. It
   updates proxy policy on existing members, including the TLS issuer, with a
   graceful reload. New observer provisioning inherits the same policy.
4. Only after proxy rollout and verification succeed, set the management app's
   `RpcKeys__ProxyEnabled=true` setting. Until then, the page explicitly reports
   **Not activated**, and the API rejects key creation. A website-only deployment
   therefore makes no claims about changing the live observer or its limits.
5. Create a key in the management panel and allow a refresh cycle before using it.

Change rates/bursts in `infra/pool/adiri.json`, rerun the preparation script to
update the panel's displayed policy, and run `rpc-keys-sync` to apply the updated
proxy policy. An explicit runner profile's `public_rpc.api_keys` overrides the
repository defaults. Never add actual key values to the profile.

Verification:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
dotnet run --project tests/Observer.Management.PolicyTests
python3 scripts/prove-rpc-api-keys.py
```

The final command uses temporary local containers and a mock RPC backend, never
the public RPC. It checks unknown/expired/revoked keys, relative tier budgets,
independent keys, stripping the upstream header, reload revocation, and the shared
backend cap. It removes its containers and network afterward.

VM diagnostics (do not print `api-keys.conf` or `api-keys-cache.json`):

```bash
sudo systemctl status oprail-rpc-key-sync.timer
sudo journalctl -u oprail-rpc-key-sync.service
```
