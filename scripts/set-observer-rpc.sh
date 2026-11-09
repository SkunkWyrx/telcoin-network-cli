#!/usr/bin/env bash
# Toggle experimental public HTTP/WebSocket RPC without SSH, rebuilding, or restarting the VM.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-}"
case "$MODE" in
  enable|disable) ;;
  *) echo 'Usage: ENV_FILE=.env.observer-1 bash scripts/set-observer-rpc.sh enable|disable'
     echo 'Uses VM_NAME, AZURE_RESOURCE_GROUP, AZURE_SUBSCRIPTION_ID; optional PUBLIC_RPC_SOURCE_CIDR (default *).'
     exit 1 ;;
esac
saved_names=() saved_values=()
for name in AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP VM_NAME PUBLIC_RPC_SOURCE_CIDR PUBLIC_RPC_URL CHAIN; do
  if declare -p "$name" >/dev/null 2>&1; then
    saved_names+=("$name"); saved_values+=("${!name}")
  fi
done
if [[ -f "${ENV_FILE:-$ROOT_DIR/.env}" ]]; then
  set -a
  source "${ENV_FILE:-$ROOT_DIR/.env}"
  set +a
elif [[ -n "${ENV_FILE:-}" ]]; then
  echo "Cannot read ENV_FILE: $ENV_FILE" >&2; exit 1
fi
for ((i=0; i<${#saved_names[@]}; i++)); do
  export "${saved_names[$i]}=${saved_values[$i]}"
done
: "${VM_NAME:?Set VM_NAME}"
: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP}"
AZ_ARGS=()
[[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]] || AZ_ARGS=(--subscription "$AZURE_SUBSCRIPTION_ID")
azc() { az "$@" ${AZ_ARGS[@]+"${AZ_ARGS[@]}"}; }
RG="$AZURE_RESOURCE_GROUP"
VM_ID=$(azc vm show -g "$RG" -n "$VM_NAME" --query id -o tsv)
# The VM deployment owns this dedicated NSG and this named rule.
ACCESS=Deny
SOURCE='*'
ENABLED=false
URL=''
if [[ "$MODE" == enable ]]; then
  ACCESS=Allow
  SOURCE="${PUBLIC_RPC_SOURCE_CIDR:-*}"
  export SOURCE
  python3 - <<'PY'
import ipaddress, os
if os.environ['SOURCE'] != '*':
    ipaddress.ip_network(os.environ['SOURCE'], strict=False)
PY
  ENABLED=true
  HOST=$(azc network public-ip show -g "$RG" -n "${VM_NAME}-ip" --query dnsSettings.fqdn -o tsv)
  [[ -n "$HOST" ]] || { echo 'VM public DNS name missing' >&2; exit 1; }
  URL="${PUBLIC_RPC_URL:-http://${HOST}:8545}"
fi
azc network nsg rule create -g "$RG" --nsg-name "${VM_NAME}-nsg" -n Observer-RPC \
  --priority 120 --direction Inbound --access "$ACCESS" --protocol Tcp \
  --source-address-prefixes "$SOURCE" --source-port-ranges '*' \
  --destination-address-prefixes '*' --destination-port-ranges 8545 8546 --output none
azc tag update --resource-id "$VM_ID" --operation Merge --tags \
  telcoin-role=observer "telcoin-chain=${CHAIN:-adiri}" "telcoin-rpc-enabled=$ENABLED" "telcoin-rpc-url=$URL" --output none
echo "Public VM RPC $MODE completed for $VM_NAME. ${URL}"
echo "Set PUBLIC_RPC_ENABLED=$ENABLED in this observer's settings to retain this choice for future deployments."
if [[ "$MODE" == disable ]]; then
  echo 'Azure NSG changes block new connections; existing TCP sessions may persist until closed.'
fi
