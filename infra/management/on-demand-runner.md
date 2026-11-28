# Adiri runner power and backup retention

The runner is a management execution host, not an RPC backend. Management requests
still require the `observer-operations-adiri` GitHub approval gate. Approval now
happens on a hosted `wake` job before the VM starts; the self-hosted job depends
on that successful approval. A hosted `always()` cleanup job deallocates the runner
after success/failure. Both lifecycle workflows keep the same network concurrency
key, preventing a cleanup job from shutting down another managed operation.

A dedicated `adiri-runner-power` identity can only read/start/deallocate the exact
runner VM. It has no Run Command, VM write/delete, secret, observer or group-wide
permissions. Its OIDC trusts are this repository's main ref and the existing
approved-operations environment. Its client ID is the repository variable
`ADIRI_RUNNER_POWER_CLIENT_ID`. Enable execution with
`ADIRI_RUNNER_ON_DEMAND_ENABLED=true` only after the monitoring cutover.
Mainnet/pregenesis observers are outside these power controls.

The management app's opt-in `Monitoring__Enabled=true` worker samples public RPC
and the independent reference once a minute. The existing five-block threshold,
three-consecutive-sample debounce, metric names/resource anchor and stateful Azure
Monitor rules/email recipient stay unchanged. The app's managed identity receives
Monitoring Metrics Publisher at the existing runner resource only. Metrics can be
published to that ARM resource while the runner is deallocated. The management
app remains min/max one replica and needs no additional VM. A reference failure
does not raise a lag incident. Secret/header values are never logged.

Set protected management environment variable `OBSERVER_RPC_MONITOR_ENABLED=true`
and deploy the website through its existing approval gate. Confirm repeated fresh
sampler publications and Azure metric timestamps before disabling the runner's old
`adiri-rpc-monitor.timer`. Only then set runner tags
`runner-power-mode=on-demand` and `rpc-monitor-host=management-app-verified`.
Power-down refuses missing tags. The website release workflow refuses to disable
monitoring while on-demand power is enabled.

An optional root-owned idle service checks every five minutes. It waits 15 minutes
from boot/last Runner.Worker activity, then tries the fleet operation lock before
deallocating itself. It covers canceled/abandoned hosted cleanup jobs; it never
stops an active worker or locked fleet operation. Keep its timer disabled until
monitoring is verified. For out-of-band interactive runner maintenance, set
`runner-keepalive=true` first, and remove it afterward. Normal management work must
use the serialized GitHub workflows. Power controls do not change RPC VM state.

## Backups without interrupting the public observer

Adiri uses `backup_mode=temporary-observer`. Each due capture creates/resumes one
disposable observer on the current exact image, waits for canonical hash/sync
readiness, then takes a graceful cold snapshot of that helper. The helper stays deallocated during Azure background copy and restarts for
validation only when the copies finish. The public source
is never stopped, deallocated or drained. The helper is not added to public HTTPS.
Its private membership/readiness is used only by the controller during the job.

The latest compatible cold pair is the normal warm-start source. For the initial
capture when only obsolete migration backups exist, live execution-first/consensus-
second disk copies may seed a **disposable, untrusted** candidate. They are tagged
`oprail-observer-seed`, never treated as backups, and never cause old backups to be
pruned. The candidate must pass actual startup/canonical sync, clean shutdown,
completed cold snapshot copies and restart/sync before a new backup is accepted.
A source with a restore-floor marker uses fresh sync instead of these seed copies.
Failure keeps old recovery points and cleans up the exact tagged disposable helper
when another healthy observer remains; automatic retries pause for operator review.
Uncertain partial provisioning refuses blind recreation and requires inspection.

After a new cold pair succeeds, retention includes both normal fleet pairs and
explicitly opted-in recovery pairs (`prune_recovery_snapshots=true` for Adiri).
It keeps today's captures, the earliest pair per prior day in the seven-day
window, and always the latest complete pair. Pruning refuses to run without a
normal completed backup less than `backup_max_age_hours` (24) old. It never
operates on seed-only, incomplete, foreign-network or unowned captures. A persisted
`prune.json` deletion list finishes both disks on retry if deletion was interrupted
halfway through a pair. The helper, its disks/NIC/IP/NSG/roles, private env file and
seed copies are removed after the valid backup is retained.

The helper's short-lived compute/disks incur costs during backup; measure its
catch-up duration before assuming net savings. No second observer is kept running
between captures. Snapshot metadata/API completion plus clean-stop/restart evidence
is checked; this is not a full disaster-recovery restore test of every snapshot.

## Activation checklist

1. Validate code/tests and provision the narrow power identity. Run
   `python3 scripts/configure-rpc-monitor.py` as the operator to idempotently
   grant the management identity its one-resource metric permission.
2. Complete a no-interruption temporary-observer cold backup; verify the retained
   pair, removed helper resources and pruned old recovery snapshots.
3. Merge the code PR, approve the management website release, and verify the new
   app sampler/metrics before disabling the old runner sampler.
4. Update the runner's trusted Adiri profile with helper/retention settings and
   enable its existing automation flag. Set repository
   `OBSERVER_AUTOMATION_ENABLED=true` to enable the scheduled lifecycle tick.
5. Tag/enable on-demand power, deallocate the runner, then dispatch lifecycle
   `plan` to prove wake/work/deallocate while public RPC and new metrics continue.
6. Recheck the management page and backup workflow history. Do not disable or
   delete load balancers, certificate DNS/vaults, API-key synchronization, the
   observer's own health service or disk-growth timers.

## Mainnet: two existing observers

Production uses two observers and `backup_mode=existing-fleet` in
`infra/pool/mainnet.json`. Scheduled backups select the highest-ordinal (last)
observer; no temporary third observer is provisioned. The selected source must be
running and synced. Another observer must be synced, undrained, and have its TLS
proxy service active when public RPC is configured. This is checked before drain
and again after the drain delay, immediately before stopping the source.
Mainnet refuses a single-node capture even if the downtime override is set.

The source drains, closes gracefully, and is deallocated for paired execution /
consensus snapshot creation. It restarts after capture, catches up, and clears its
drain marker only after sync validation. Azure's background snapshot copies must
also complete before the new pair becomes latest. Capture/recovery failures leave
old backups unpruned. The other observer remains in service during maintenance.
The existing four-hour interval and seven-day retention still apply.

At mainnet launch:

1. Configure the independent mainnet runner (`observer-operations-mainnet`), its
   network-scoped managed identity permissions, and the private mainnet profile at
   `/etc/oprail-observers/mainnet/pool.json`, including the verified reference RPC.
2. Bring both mainnet observers online and verify both are synced and healthy
   members of the RPC load balancer before enabling scheduled maintenance.
   If no mainnet backup exists yet, provision the second node with fresh data
   disks and let it sync while the first serves; keep the two-node snapshot guard.
3. Set `automation_enabled=true` in that private mainnet profile. Enable repository
   variables `OBSERVER_MAINNET_ENABLED=true` and
   `OBSERVER_MAINNET_AUTOMATION_ENABLED=true` only at launch readiness.
4. Run `observer-mainnet-backups.yml` manually for the first production proof:
   verify RPC remains available, the last observer rejoins synced, the pair is
   complete, and retention preserves the latest usable backup.

The mainnet workflow checks hourly and captures only when the configured interval
is due. It uses `observer-lifecycle-mainnet` concurrency, shared with mainnet
management operations, plus the fleet operation lock. Mainnet backup opt-in is
independent of Adiri's automation flag and runner power controls. All mainnet
launch/automation flags remain disabled until explicitly enabled at launch.
The installed one-off command uses the same strategy:

```bash
OBSERVER_STATE_DIR=/var/lib/oprail-observers python3 scripts/pool/backup_now.py \
  --config /etc/oprail-observers/mainnet/pool.json --apply
```
