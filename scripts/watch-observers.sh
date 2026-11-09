#!/usr/bin/env bash
# Discover Azure observer resources, query their public RPC endpoints locally.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
saved_names=() saved_values=()
for name in AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP CHAIN NETWORK_RPC_URL; do
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
exec python3 "$ROOT_DIR/scripts/watch-observers.py" "$@"
