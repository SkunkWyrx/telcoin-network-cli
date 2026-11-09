#!/usr/bin/env bash
# =============================================================================
# watch-azure-aci.sh
#
# Tails and monitors stdout logs from the Telcoin observer running in ACI.
# Works immediately via az container logs (no setup needed).
# When --analytics is passed, queries Log Analytics for richer filtering.
#
# Usage:
#   ./scripts/watch-azure-aci.sh               # live tail (like docker logs -f)
#   ./scripts/watch-azure-aci.sh --analytics   # query Log Analytics workspace
#   ./scripts/watch-azure-aci.sh --status      # show container state + sync progress
#   ./scripts/watch-azure-aci.sh --processing  # consensus vs execution progress
#   ./scripts/watch-azure-aci.sh --eta         # estimate time to full consensus sync
#
# Environment overrides:
#   AZURE_RESOURCE_GROUP    (default: oprail-telcoin-testnet)
#   ACI_NAME                (default: telcoin-observer-1)
#   AZURE_SUBSCRIPTION_ID   (default: resolved from az account)
#   RPC_URL                 (default: derived from container FQDN)
#   ETA_SAMPLE_LINES        (default: 50)
# =============================================================================

set -euo pipefail

RG="${AZURE_RESOURCE_GROUP:-oprail-telcoin-testnet}"
ACI_NAME="${ACI_NAME:-telcoin-observer-1}"
CONTAINER_NAME="${CONTAINER_NAME:-telcoin-observer}"
WORKSPACE_NAME="${LOG_ANALYTICS_WORKSPACE_NAME:-telcoin-observer-logs}"
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
MODE="${1:-tail}"
ETA_SAMPLE_LINES="${ETA_SAMPLE_LINES:-50}"

AZ_SUB_ARGS=(--subscription "$AZURE_SUBSCRIPTION_ID")
azc() { az "$@" "${AZ_SUB_ARGS[@]}"; }

# Write Python helpers to temp files once at startup to avoid bash quoting issues
_PY_PARSE_LATEST=$(mktemp /tmp/telcoin_parse_latest.XXXXXX.py)
_PY_ETA=$(mktemp /tmp/telcoin_eta.XXXXXX.py)
trap 'rm -f "$_PY_PARSE_LATEST" "$_PY_ETA"' EXIT

cat > "$_PY_PARSE_LATEST" << 'PYEOF'
import re, sys
blob = sys.stdin.read()
blob = re.sub(r'\x1b\[[0-9;]*[mKGHF]', '', blob)
pat = re.compile(
    r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+INFO observer follow progress'
    r' processed_count=(\d+)\s+header_number=(\d+)'
    r'\s+latest_processed_consensus=(\d+)\s+latest_network_consensus=(\d+)'
    r'\s+consensus_sync_distance=(\d+)',
    re.MULTILINE)
matches = list(pat.finditer(blob))
if not matches:
    print('Consensus: unable to parse progress lines')
    raise SystemExit(0)
m = matches[-1]
ts, pc, hn, lpc, lnc, dist = m.groups()
print('Consensus')
print(f'  timestamp: {ts}')
print(f'  processed_count: {pc}')
print(f'  header_number: {hn}')
print(f'  latest_processed_consensus: {lpc}')
print(f'  latest_network_consensus: {lnc}')
print(f'  consensus_sync_distance: {dist}')
PYEOF

cat > "$_PY_ETA" << 'PYEOF'
import re, sys, os
from datetime import datetime
blob = sys.stdin.read()
blob = re.sub(r'\x1b\[[0-9;]*[mKGHF]', '', blob)
pat = re.compile(
    r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+INFO observer follow progress'
    r' processed_count=(\d+)\s+header_number=(\d+)'
    r'\s+latest_processed_consensus=(\d+)\s+latest_network_consensus=(\d+)'
    r'\s+consensus_sync_distance=(\d+)',
    re.MULTILINE)
rows = []
for m in pat.finditer(blob):
    ts = datetime.fromisoformat(m.group(1).replace('Z', '+00:00'))
    rows.append({'ts': ts, 'processed': int(m.group(4)),
                 'network': int(m.group(5)), 'distance': int(m.group(6))})
sample_lines = int(os.environ.get('ETA_SAMPLE_LINES', '50'))
rows = rows[-sample_lines:]
if len(rows) < 2:
    print('Not enough parseable progress rows to estimate ETA.')
    raise SystemExit(0)
first, last = rows[0], rows[-1]
seconds = (last['ts'] - first['ts']).total_seconds()
if seconds <= 0:
    print('Sample window is too small to estimate ETA.')
    raise SystemExit(0)
proc_rate = (last['processed'] - first['processed']) / seconds * 60
net_rate  = (last['network']   - first['network'])   / seconds * 60
close_rate = (first['distance'] - last['distance'])  / seconds * 60
remaining = last['distance']
print(f"Sample window: {int(seconds)}s  ({first['ts'].isoformat()} -> {last['ts'].isoformat()})")
print(f"Processed consensus rate : {proc_rate:.2f} blocks/min")
print(f"Network growth rate      : {net_rate:.2f} blocks/min")
print(f"Net distance close rate  : {close_rate:.2f} blocks/min")
print(f"Remaining consensus dist : {remaining}")
if close_rate <= 0:
    print('ETA: unbounded (distance is not currently shrinking)')
else:
    eta_h = remaining / close_rate / 60
    print(f"ETA to full consensus sync: {eta_h:.2f}h  ({eta_h/24:.2f}d)")
PYEOF

get_rpc_url() {
  if [[ -n "${RPC_URL:-}" ]]; then
    printf '%s\n' "$RPC_URL"
    return
  fi

  local fqdn
  fqdn="$(azc container show \
    --resource-group "$RG" \
    --name "$ACI_NAME" \
    --query 'ipAddress.fqdn' -o tsv 2>/dev/null || true)"

  if [[ -n "$fqdn" ]]; then
    printf 'http://%s:8545\n' "$fqdn"
  fi
}

print_processing_breakdown() {
  echo ""
  echo "=== Processing Breakdown ==="

  azc container logs \
    --resource-group "$RG" \
    --name "$ACI_NAME" \
    --container-name "$CONTAINER_NAME" 2>/dev/null \
    | python3 "$_PY_PARSE_LATEST" || echo "Consensus: unable to parse progress lines"

  local rpc
  rpc="$(get_rpc_url || true)"

  if [[ -z "$rpc" ]]; then
    echo "Execution: RPC URL not available"
    return
  fi

  local block_hex syncing_json
  block_hex="$(curl -fsS "$rpc" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' 2>/dev/null | jq -r '.result // empty' || true)"
  syncing_json="$(curl -fsS "$rpc" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_syncing","params":[],"id":1}' 2>/dev/null | jq -c '.result' || true)"

  if [[ -z "$block_hex" ]]; then
    echo "Execution: unable to query eth_blockNumber from $rpc"
    return
  fi

  local block_dec
  block_dec="$(python3 -c 'import sys; print(int(sys.argv[1], 16))' "$block_hex" 2>/dev/null || true)"

  echo "Execution"
  echo "  rpc_url: $rpc"
  if [[ -n "$block_dec" ]]; then
    echo "  eth_blockNumber: $block_dec ($block_hex)"
  else
    echo "  eth_blockNumber: $block_hex"
  fi
  if [[ -n "$syncing_json" ]]; then
    echo "  eth_syncing: $syncing_json"
  else
    echo "  eth_syncing: unavailable"
  fi
}

print_eta_estimate() {
  echo ""
  echo "=== Full Sync ETA (Consensus) ==="

  azc container logs \
    --resource-group "$RG" \
    --name "$ACI_NAME" \
    --container-name "$CONTAINER_NAME" 2>/dev/null \
    | python3 "$_PY_ETA"
}

print_status() {
  echo ""
  echo "=== Container Status ==="
  azc container show \
    --resource-group "$RG" \
    --name "$ACI_NAME" \
    --query '{
      name: name,
      state: containers[0].instanceView.currentState.state,
      restarts: containers[0].instanceView.restartCount,
      fqdn: ipAddress.fqdn,
      ip: ipAddress.ip
    }' \
    -o table

  echo ""
  echo "=== Last 5 sync progress lines ==="
  azc container logs \
    --resource-group "$RG" \
    --name "$ACI_NAME" \
    --container-name "$CONTAINER_NAME" 2>/dev/null \
    | grep "observer follow progress" \
    | tail -5 \
    || echo "(No progress lines found yet — node may still be starting)"

  print_processing_breakdown
}

query_log_analytics() {
  local WORKSPACE_ID
  WORKSPACE_ID="$(azc monitor log-analytics workspace show \
    -g "$RG" -n "$WORKSPACE_NAME" \
    --query customerId -o tsv 2>/dev/null || echo "")"

  if [[ -z "$WORKSPACE_ID" ]]; then
    echo "Log Analytics workspace '${WORKSPACE_NAME}' not found in ${RG}." >&2
    echo "Falling back to direct container log tail..." >&2
    tail_logs
    return
  fi

  echo "Querying Log Analytics workspace ${WORKSPACE_NAME}..."
  echo "(Shows last 100 lines — Ctrl+C to stop, re-run to refresh)"
  echo ""

  # ContainerInsights log query — filters to relevant sync telemetry
  azc monitor log-analytics query \
    --workspace "$WORKSPACE_ID" \
    --analytics-query "
      ContainerInstanceLog_CL
      | where ContainerGroup_s == '${ACI_NAME}'
      | where Message has_any ('observer follow progress', 'fetching consensus', 'ERROR', 'WARN', 'epoch pack', 'retrieved cert')
      | project TimeGenerated, Message
      | order by TimeGenerated desc
      | take 100
    " \
    --output table 2>/dev/null \
    || {
      echo "Log Analytics query failed. Falling back to direct container log tail..."
      tail_logs
    }
}

tail_logs() {
  echo "Tailing logs for ${ACI_NAME} > ${CONTAINER_NAME} (Ctrl+C to stop)..."
  echo ""
  # ACI does not support --follow natively, so we poll every 5 seconds
  local LAST_LINES=0
  while true; do
    ALL_LINES=$(azc container logs \
      --resource-group "$RG" \
      --name "$ACI_NAME" \
      --container-name "$CONTAINER_NAME" 2>/dev/null | wc -l | tr -d ' ')

    if (( ALL_LINES > LAST_LINES )); then
      NEW=$(( ALL_LINES - LAST_LINES ))
      azc container logs \
        --resource-group "$RG" \
        --name "$ACI_NAME" \
        --container-name "$CONTAINER_NAME" 2>/dev/null \
        | tail -"$NEW"
      LAST_LINES=$ALL_LINES
    fi
    sleep 5
  done
}

case "$MODE" in
  --status)
    print_status
    ;;
  --processing)
    print_status
    ;;
  --eta)
    print_status
    print_eta_estimate
    ;;
  --analytics)
    query_log_analytics
    ;;
  --tail|tail|*)
    print_status
    echo ""
    tail_logs
    ;;
esac
