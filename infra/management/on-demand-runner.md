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
