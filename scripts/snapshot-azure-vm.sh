#!/usr/bin/env bash
# Capture only chain-data disks; never copy the OS disk or observer identity.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help ]]; then
  cat <<'HELP'
Usage: bash scripts/snapshot-azure-vm.sh
Reads .env (or ENV_FILE); exported settings override the file.
Requires AZURE_RESOURCE_GROUP, VM_NAME, SSH_PRIVATE_KEY_FILE.
Optional: AZURE_SUBSCRIPTION_ID, VM_ADMIN_USERNAME, SNAPSHOT_PREFIX.
Stops the observer, deallocates the running VM, captures both data disks,
and restarts the VM. Expect downtime. Writes <prefix>.env containing snapshot IDs
into SNAPSHOT_OUTPUT_DIR (default ./snapshots). No keys or secrets are copied.
Snapshot background copies may continue after the script returns.
HELP
  exit 0
fi
[[ $# == 0 ]] || { echo 'Use --help' >&2; exit 1; }
saved_names=() saved_values=()
for name in AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP VM_NAME SSH_PRIVATE_KEY_FILE VM_ADMIN_USERNAME SNAPSHOT_PREFIX SNAPSHOT_OUTPUT_DIR; do
  if declare -p "$name" >/dev/null 2>&1; then
    saved_names+=("$name"); saved_values+=("${!name}")
  fi
done
if [[ -f "${ENV_FILE:-$ROOT_DIR/.env}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE:-$ROOT_DIR/.env}"
  set +a
elif [[ -n "${ENV_FILE:-}" ]]; then
  echo "Cannot read ENV_FILE: $ENV_FILE" >&2; exit 1
fi
for ((i=0; i<${#saved_names[@]}; i++)); do
  export "${saved_names[$i]}=${saved_values[$i]}"
done
: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP}"
: "${VM_NAME:?Set VM_NAME}"
: "${SSH_PRIVATE_KEY_FILE:?Set SSH_PRIVATE_KEY_FILE}"
ADMIN="${VM_ADMIN_USERNAME:-azureuser}"
PREFIX="${SNAPSHOT_PREFIX:-${VM_NAME}-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$PREFIX" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,65}$ ]] || { echo 'Invalid SNAPSHOT_PREFIX (maximum 66 characters)' >&2; exit 1; }
[[ "$ADMIN" =~ ^[a-z_][a-z0-9_-]*$ && -r "$SSH_PRIVATE_KEY_FILE" ]] || { echo 'Invalid SSH username or unreadable key' >&2; exit 1; }
AZ_ARGS=()
[[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]] || AZ_ARGS=(--subscription "$AZURE_SUBSCRIPTION_ID")
azc() { az "$@" ${AZ_ARGS[@]+"${AZ_ARGS[@]}"}; }
RG="$AZURE_RESOURCE_GROUP"
OUT="${SNAPSHOT_OUTPUT_DIR:-$ROOT_DIR/snapshots}"
umask 077
mkdir -p "$OUT"
MANIFEST="$OUT/$PREFIX.env"
[[ ! -e "$MANIFEST" ]] || { echo 'Snapshot manifest already exists; use another prefix' >&2; exit 1; }
WORK_DIR=$(mktemp -d)
RESUME=false
cleanup() {
  local result=$?
  trap - EXIT
  if [[ "$RESUME" == true ]]; then
    echo "Restarting source VM $VM_NAME..."
    if ! azc vm start -g "$RG" -n "$VM_NAME" --output none; then
      echo "Restart failed. Run: az vm start -g $RG -n $VM_NAME${AZURE_SUBSCRIPTION_ID:+ --subscription $AZURE_SUBSCRIPTION_ID}" >&2
      result=1
    else
      local ready=false attempt
      for attempt in $(seq 1 30); do
        if ssh -i "$SSH_PRIVATE_KEY_FILE" -o IgnoreUnknown=UseKeychain -o UseKeychain=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 \
          "$ADMIN@$HOST" 'sudo -n systemctl start telcoin-observer' 2>/dev/null; then ready=true; break; fi
        sleep 2
      done
      if [[ "$ready" != true ]]; then
        echo "VM started, but observer restart could not be verified. Check $ADMIN@$HOST." >&2
        result=1
      fi
    fi
  fi
  rm -rf "$WORK_DIR"
  exit "$result"
}
trap cleanup EXIT
STATE=$(azc vm get-instance-view -g "$RG" -n "$VM_NAME" --query "instanceView.statuses[?starts_with(code, 'PowerState/')].code | [0]" -o tsv)
[[ "$STATE" == PowerState/running ]] || { echo 'Source VM must be running for a clean observer shutdown' >&2; exit 1; }
azc vm show -g "$RG" -n "$VM_NAME" -o json > "$WORK_DIR/vm.json"
python3 - "$WORK_DIR/vm.json" > "$WORK_DIR/disks" <<'PY'
import json, sys
vm = json.load(open(sys.argv[1]))
for lun in (0, 1):
    matches = [d for d in vm['storageProfile']['dataDisks'] if d['lun'] == lun]
    if len(matches) != 1:
        raise SystemExit(f'Expected exactly one data disk at LUN {lun}')
    print(matches[0]['managedDisk']['id'])
PY
EXECUTION_DISK=$(sed -n '1p' "$WORK_DIR/disks")
CONSENSUS_DISK=$(sed -n '2p' "$WORK_DIR/disks")
LOCATION=$(azc vm show -g "$RG" -n "$VM_NAME" --query location -o tsv)
HOST=$(azc network public-ip show -g "$RG" -n "${VM_NAME}-ip" --query dnsSettings.fqdn -o tsv)
[[ "$HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo 'Invalid VM DNS name' >&2; exit 1; }
# Refuse name collisions before interrupting the source.
for suffix in execution consensus; do
  MATCH=$(azc snapshot list -g "$RG" --query "[?name=='${PREFIX}-${suffix}'].name" -o tsv)
  [[ -z "$MATCH" ]] || { echo 'Snapshot name already exists; use another prefix' >&2; exit 1; }
done
echo "Stopping observer and flushing writes on $HOST..."
RESUME=true
ssh -i "$SSH_PRIVATE_KEY_FILE" -o IgnoreUnknown=UseKeychain -o UseKeychain=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
  "$ADMIN@$HOST" 'sudo -n systemctl stop telcoin-observer && sudo -n sync'
RESUME=true
echo 'Deallocating source VM to keep both disks unchanged during capture...'
azc vm deallocate -g "$RG" -n "$VM_NAME" --output none
for suffix in execution consensus; do
  DISK="$EXECUTION_DISK"
  [[ "$suffix" != consensus ]] || DISK="$CONSENSUS_DISK"
  ID=$(azc snapshot create -g "$RG" -n "${PREFIX}-${suffix}" --location "$LOCATION" \
    --source "$DISK" --incremental true --tags "observerCapture=$PREFIX" "observerDisk=$suffix" \
    --query id -o tsv)
  KEY=EXECUTION_SNAPSHOT_ID
  [[ "$suffix" != consensus ]] || KEY=CONSENSUS_SNAPSHOT_ID
  printf '%s=%s\n' "$KEY" "$ID" >> "$MANIFEST"
done
echo "Snapshot IDs saved to $MANIFEST. Use both IDs together."
echo 'Before deploying, check each snapshot: az snapshot show --ids <ID> --query completionPercent'
echo 'Wait for both to reach 100. The deployment also checks readiness.'
