#!/usr/bin/env bash
# Run the existing Docker observer monitor over SSH; no remote installation needed.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---status}"
case "$MODE" in
  --help)
    echo 'Usage: bash scripts/watch-azure-vm.sh [--status|--tail|--processing|--eta|--diagnose|--sync]'
    echo 'Reads .env (or ENV_FILE). Exported settings override the file.'
    echo 'SSH_HOST optionally overrides VM_DNS_LABEL.AZURE_LOCATION.cloudapp.azure.com.'
    exit 0 ;;
  --status|--tail|--processing|--eta|--diagnose|--health|--sync) ;;
  *) echo 'Unknown mode; use --help' >&2; exit 1 ;;
esac
saved_names=() saved_values=()
for name in NETWORK_RPC_URL SSH_HOST VM_DNS_LABEL AZURE_LOCATION VM_ADMIN_USERNAME SSH_PRIVATE_KEY_FILE; do
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
: "${SSH_PRIVATE_KEY_FILE:?Set SSH_PRIVATE_KEY_FILE in .env}"
[[ -r "$SSH_PRIVATE_KEY_FILE" ]] || { echo 'Cannot read SSH private key' >&2; exit 1; }
if [[ -z "${SSH_HOST:-}" ]]; then
  : "${VM_DNS_LABEL:?Set VM_DNS_LABEL in .env}"
  : "${AZURE_LOCATION:?Set AZURE_LOCATION in .env}"
  SSH_HOST="${VM_DNS_LABEL}.${AZURE_LOCATION}.cloudapp.azure.com"
fi
ADMIN="${VM_ADMIN_USERNAME:-azureuser}"
[[ "$SSH_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ && "$ADMIN" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'Invalid SSH host or username' >&2; exit 1; }
echo "Checking observer on $SSH_HOST..."
if [[ "$MODE" == --sync ]]; then
  printf -v REMOTE_COMMAND 'sudo -n python3 - --network-rpc %q' "${NETWORK_RPC_URL:-https://rpc.adiri.tel}"
  ssh -i "$SSH_PRIVATE_KEY_FILE" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=10 "$ADMIN@$SSH_HOST" "$REMOTE_COMMAND" < "$ROOT_DIR/scripts/check-observer-sync.py"
  exit $?
fi
# Only the monitor source is sent. Secrets from .env stay on this machine.
{
  cat <<'REMOTE'
echo '=== Service ==='
systemctl show telcoin-observer -p ActiveState -p SubState -p NRestarts
 echo '=== VM resources ==='
free -h
df -h /var/lib/telcoin/db /var/lib/telcoin/consensus-db /var/lib/telcoin/static_files
findmnt -R /var/lib/telcoin
REMOTE
  cat "$ROOT_DIR/scripts/watch-local-docker.sh"
} | ssh -i "$SSH_PRIVATE_KEY_FILE" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=10 "$ADMIN@$SSH_HOST" "sudo -n bash -s -- $MODE"
