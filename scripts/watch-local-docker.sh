#!/usr/bin/env bash
# =============================================================================
# watch-local-docker.sh
#
# Tails and analyzes a local Telcoin observer Docker container.
# Mirrors key diagnostics from watch-azure-aci.sh for local usage.
#
# Usage:
#   ./scripts/watch-local-docker.sh               # status + live tail
#   ./scripts/watch-local-docker.sh --tail        # live tail only
#   ./scripts/watch-local-docker.sh --status      # container + sync snapshot
#   ./scripts/watch-local-docker.sh --processing  # consensus vs execution progress
#   ./scripts/watch-local-docker.sh --eta         # estimate time to full consensus sync
#   ./scripts/watch-local-docker.sh --diagnose    # compact health verdict
#
# Environment overrides:
#   CONTAINER_NAME       (default: telcoin-observer)
#   RPC_URL              (default: http://localhost:8545)
#   ETA_SAMPLE_LINES     (default: 50)
#   EXEC_STATE_FILE      (default: /tmp/telcoin-local-execution-eta.state)
#   LOG_SCAN_LINES       (default: 20000)
# =============================================================================

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-telcoin-observer}"
RPC_URL="${RPC_URL:-http://localhost:8545}"
MODE="${1:---tail}"
ETA_SAMPLE_LINES="${ETA_SAMPLE_LINES:-50}"
EXEC_STATE_FILE="${EXEC_STATE_FILE:-/tmp/telcoin-local-execution-eta.state}"
LOG_SCAN_LINES="${LOG_SCAN_LINES:-20000}"

_PY_PARSE_LATEST="$(mktemp /tmp/telcoin_local_parse_latest.XXXXXX.py)"
_PY_ETA="$(mktemp /tmp/telcoin_local_eta.XXXXXX.py)"
trap 'rm -f "$_PY_PARSE_LATEST" "$_PY_ETA"' EXIT

cat > "$_PY_PARSE_LATEST" << 'PYEOF'
import re
import sys

blob = sys.stdin.read()
blob = re.sub(r'\x1b\[[0-9;]*[mKGHF]', '', blob)
pat_follow = re.compile(
    r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+INFO\s+(?:state-sync:\s+)?observer follow progress'
    r' processed_count=(\d+)\s+header_number=(\d+)'
    r'\s+latest_processed_consensus=(\d+)\s+latest_network_consensus=(\d+)'
    r'\s+consensus_sync_distance=(\d+)',
    re.MULTILINE,
)

pat_fetch = re.compile(
  r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+INFO\s+(?:state-sync:\s+)?fetching consensus from peer'
  r'\s+number=(\d+)\s+hash=\S+\s+end_number=(\d+)',
  re.MULTILINE,
)

matches_follow = list(pat_follow.finditer(blob))
if matches_follow:
  m = matches_follow[-1]
  ts, pc, hn, lpc, lnc, dist = m.groups()
  print('Consensus')
  print('  source: observer follow progress')
  print(f'  timestamp: {ts}')
  print(f'  processed_count: {pc}')
  print(f'  header_number: {hn}')
  print(f'  latest_processed_consensus: {lpc}')
  print(f'  latest_network_consensus: {lnc}')
  print(f'  consensus_sync_distance: {dist}')
  raise SystemExit(0)

matches_fetch = list(pat_fetch.finditer(blob))
if matches_fetch:
  ts, number, end_number = matches_fetch[-1].groups()
  number_i = int(number)
  end_i = int(end_number)
  distance = max(end_i - number_i, 0)
  print('Consensus')
  print('  source: fetching consensus from peer')
  print(f'  timestamp: {ts}')
  print(f'  latest_processed_consensus: {number_i}')
  print(f'  latest_network_consensus: {end_i}')
  print(f'  consensus_sync_distance: {distance}')
  raise SystemExit(0)

print('Consensus: unable to parse progress lines')
PYEOF

cat > "$_PY_ETA" << 'PYEOF'
import os
import re
import sys
from datetime import datetime

blob = sys.stdin.read()
blob = re.sub(r'\x1b\[[0-9;]*[mKGHF]', '', blob)
pat_follow = re.compile(
    r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+INFO\s+(?:state-sync:\s+)?observer follow progress'
    r' processed_count=(\d+)\s+header_number=(\d+)'
    r'\s+latest_processed_consensus=(\d+)\s+latest_network_consensus=(\d+)'
    r'\s+consensus_sync_distance=(\d+)',
    re.MULTILINE,
)

pat_fetch = re.compile(
  r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+INFO\s+(?:state-sync:\s+)?fetching consensus from peer'
  r'\s+number=(\d+)\s+hash=\S+\s+end_number=(\d+)',
  re.MULTILINE,
)

rows = []
source = 'observer follow progress'
for m in pat_follow.finditer(blob):
    ts = datetime.fromisoformat(m.group(1).replace('Z', '+00:00'))
    rows.append(
        {
            'ts': ts,
            'processed': int(m.group(4)),
            'network': int(m.group(5)),
            'distance': int(m.group(6)),
        }
    )

if not rows:
  source = 'fetching consensus from peer'
  for m in pat_fetch.finditer(blob):
    ts = datetime.fromisoformat(m.group(1).replace('Z', '+00:00'))
    processed = int(m.group(2))
    network = int(m.group(3))
    rows.append(
      {
        'ts': ts,
        'processed': processed,
        'network': network,
        'distance': max(network - processed, 0),
      }
    )

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
net_rate = (last['network'] - first['network']) / seconds * 60
close_rate = (first['distance'] - last['distance']) / seconds * 60
remaining = last['distance']
print(f"Source pattern: {source}")
print(f"Sample window: {int(seconds)}s  ({first['ts'].isoformat()} -> {last['ts'].isoformat()})")
print(f"Processed consensus rate : {proc_rate:.2f} blocks/min")
print(f"Network growth rate      : {net_rate:.2f} blocks/min")
print(f"Net distance close rate  : {close_rate:.2f} blocks/min")
print(f"Remaining consensus dist : {remaining}")
if remaining == 0:
  print('ETA to full consensus sync: synced (distance is zero)')
elif close_rate <= 0:
    print('ETA: unbounded (distance is not currently shrinking)')
else:
    eta_h = remaining / close_rate / 60
    print(f"ETA to full consensus sync: {eta_h:.2f}h  ({eta_h/24:.2f}d)")
PYEOF

container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"
}

ensure_container_exists() {
  if ! container_exists; then
    echo "Container '$CONTAINER_NAME' not found." >&2
    echo "Available containers:" >&2
    docker ps -a --format '  - {{.Names}} ({{.Status}})' >&2 || true
    exit 1
  fi
}

print_processing_breakdown() {
  echo ""
  echo "=== Processing Breakdown ==="

  docker logs --tail "$LOG_SCAN_LINES" "$CONTAINER_NAME" 2>&1 | python3 "$_PY_PARSE_LATEST" || echo "Consensus: unable to parse progress lines"

  local block_resp latest_resp syncing_resp
  block_resp="$(curl -fsS "$RPC_URL" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' 2>/dev/null || true)"
  latest_resp="$(curl -fsS "$RPC_URL" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["latest",false],"id":1}' 2>/dev/null || true)"
  syncing_resp="$(curl -fsS "$RPC_URL" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_syncing","params":[],"id":1}' 2>/dev/null || true)"

  if [[ -z "$block_resp" ]]; then
    echo "Execution: unable to query eth_blockNumber from $RPC_URL"
    return
  fi

  local block_hex latest_hex block_dec syncing_value
  block_hex="$(printf '%s' "$block_resp" | python3 -c 'import json, sys; print(json.loads(sys.stdin.read()).get("result", ""))' 2>/dev/null || true)"
  latest_hex="$(printf '%s' "$latest_resp" | python3 -c 'import json, sys; print((json.loads(sys.stdin.read()).get("result") or {}).get("number", ""))' 2>/dev/null || true)"
  syncing_value="$(printf '%s' "$syncing_resp" | python3 -c 'import json, sys; print(json.dumps(json.loads(sys.stdin.read()).get("result")))' 2>/dev/null || true)"

  if [[ -z "$block_hex" ]]; then
    echo "Execution: unable to parse eth_blockNumber response"
    return
  fi

  block_dec="$(python3 -c 'import sys; print(int(sys.argv[1], 16))' "$block_hex" 2>/dev/null || true)"

  echo "Execution"
  echo "  rpc_url: $RPC_URL"
  if [[ -n "$block_dec" ]]; then
    echo "  eth_blockNumber: $block_dec ($block_hex)"
  else
    echo "  eth_blockNumber: $block_hex"
  fi
  if [[ -n "$latest_hex" ]]; then
    echo "  eth_getBlockByNumber(latest).number: $latest_hex"
    if [[ "$latest_hex" != "$block_hex" ]]; then
      echo "  execution_head_consistency: WARNING (RPC methods disagree)"
    else
      echo "  execution_head_consistency: OK"
    fi
  fi
  if [[ -n "$syncing_value" ]]; then
    echo "  eth_syncing: $syncing_value"
  else
    echo "  eth_syncing: unavailable"
  fi
}

print_eta_estimate() {
  echo ""
  echo "=== Full Sync ETA (Consensus) ==="

  ETA_SAMPLE_LINES="$ETA_SAMPLE_LINES" docker logs --tail "$LOG_SCAN_LINES" "$CONTAINER_NAME" 2>&1 | python3 "$_PY_ETA"
}

print_execution_eta() {
  echo ""
  echo "=== Full Sync ETA (Execution) ==="

  local block_resp block_hex block_dec
  local block_obj block_ts_hex block_ts_dec now_ts lag_now
  local prev_epoch prev_block prev_lag close_rate eta_seconds

  block_resp="$(curl -fsS "$RPC_URL" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["latest",false],"id":1}' 2>/dev/null || true)"
  block_hex="$(printf '%s' "$block_resp" | python3 -c 'import json, sys; print(json.loads(sys.stdin.read()).get("result", {}).get("number", ""))' 2>/dev/null || true)"
  block_ts_hex="$(printf '%s' "$block_resp" | python3 -c 'import json, sys; print(json.loads(sys.stdin.read()).get("result", {}).get("timestamp", ""))' 2>/dev/null || true)"

  if [[ -z "$block_hex" || -z "$block_ts_hex" ]]; then
    echo "Execution ETA: unable to query latest execution block/timestamp from $RPC_URL"
    return
  fi

  block_dec="$(python3 -c 'import sys; print(int(sys.argv[1], 16))' "$block_hex" 2>/dev/null || true)"
  block_ts_dec="$(python3 -c 'import sys; print(int(sys.argv[1], 16))' "$block_ts_hex" 2>/dev/null || true)"
  now_ts="$(date -u +%s)"

  if [[ -z "$block_dec" || -z "$block_ts_dec" || -z "$now_ts" ]]; then
    echo "Execution ETA: unable to parse execution timing values"
    return
  fi

  lag_now="$((now_ts - block_ts_dec))"
  if (( lag_now < 0 )); then lag_now=0; fi

  printf 'Execution head block        : %s (%s)\n' "$block_dec" "$block_hex"
  printf 'Execution head timestamp lag: %ss\n' "$lag_now"

  if (( lag_now == 0 )); then
    echo "ETA to near-realtime execution: synced (timestamp lag is zero)"
    printf 'epoch=%s block=%s lag=%s\n' "$now_ts" "$block_dec" "$lag_now" > "$EXEC_STATE_FILE"
    return
  fi

  if [[ -f "$EXEC_STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$EXEC_STATE_FILE" || true
    prev_epoch="${epoch:-}"
    prev_block="${block:-}"
    prev_lag="${lag:-}"

    if [[ -n "$prev_epoch" && -n "$prev_block" && -n "$prev_lag" ]]; then
      local dt
      dt="$((now_ts - prev_epoch))"
      if (( dt > 0 )); then
        local dblock dlag
        dblock="$((block_dec - prev_block))"
        dlag="$((prev_lag - lag_now))"
        printf 'Execution sample window     : %ss\n' "$dt"
        printf 'Execution block delta       : %s\n' "$dblock"
        printf 'Observed execution rate     : %.2f blocks/min\n' "$(python3 -c 'import sys; print((int(sys.argv[1]) / int(sys.argv[2])) * 60.0)' "$dblock" "$dt")"

        if (( dlag > 0 )); then
          close_rate="$(python3 -c 'import sys; print((int(sys.argv[1]) / int(sys.argv[2])) * 60.0)' "$dlag" "$dt")"
          eta_seconds="$(python3 -c 'import sys; print(int(int(sys.argv[1]) / ((int(sys.argv[2]) / int(sys.argv[3])))))' "$lag_now" "$dlag" "$dt")"
          printf 'Execution lag close rate    : %.2f sec/min\n' "$close_rate"
          printf 'ETA to near-realtime execution: %ss (%.2fh / %.2fd)\n' "$eta_seconds" "$(python3 -c 'import sys; print(int(sys.argv[1]) / 3600.0)' "$eta_seconds")" "$(python3 -c 'import sys; print(int(sys.argv[1]) / 86400.0)' "$eta_seconds")"
        elif (( dblock == 0 )); then
          echo "ETA to near-realtime execution: unbounded (execution head block did not move in sample window)"
        else
          echo "ETA to near-realtime execution: unbounded (timestamp lag is not shrinking)"
        fi
      else
        echo "ETA to near-realtime execution: waiting for next sample window"
      fi
    else
      echo "ETA to near-realtime execution: waiting for next sample window"
    fi
  else
    echo "ETA to near-realtime execution: waiting for next sample window"
    echo "Execution rate note          : first run initializes sample state"
  fi

  printf 'epoch=%s block=%s lag=%s\n' "$now_ts" "$block_dec" "$lag_now" > "$EXEC_STATE_FILE"
}

print_health_verdict() {
  echo ""
  echo "=== Health Verdict ==="

  local consensus_out cons_dist cons_head cons_net cons_ts
  local block_resp block_hex block_ts_hex block_dec block_ts_dec now_ts lag_now
  local syncing_resp syncing_value
  local verdict detail

  consensus_out="$(docker logs --tail "$LOG_SCAN_LINES" "$CONTAINER_NAME" 2>&1 | python3 "$_PY_PARSE_LATEST" 2>/dev/null || true)"
  cons_dist="$(printf '%s' "$consensus_out" | sed -n 's/.*consensus_sync_distance: \([0-9][0-9]*\).*/\1/p' | head -n 1)"
  cons_head="$(printf '%s' "$consensus_out" | sed -n 's/.*latest_processed_consensus: \([0-9][0-9]*\).*/\1/p' | head -n 1)"
  cons_net="$(printf '%s' "$consensus_out" | sed -n 's/.*latest_network_consensus: \([0-9][0-9]*\).*/\1/p' | head -n 1)"
  cons_ts="$(printf '%s' "$consensus_out" | sed -n 's/.*timestamp: \([^ ]*\).*/\1/p' | head -n 1)"

  block_resp="$(curl -fsS "$RPC_URL" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["latest",false],"id":1}' 2>/dev/null || true)"
  block_hex="$(printf '%s' "$block_resp" | python3 -c 'import json, sys; print(json.loads(sys.stdin.read()).get("result", {}).get("number", ""))' 2>/dev/null || true)"
  block_ts_hex="$(printf '%s' "$block_resp" | python3 -c 'import json, sys; print(json.loads(sys.stdin.read()).get("result", {}).get("timestamp", ""))' 2>/dev/null || true)"
  syncing_resp="$(curl -fsS "$RPC_URL" -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_syncing","params":[],"id":1}' 2>/dev/null || true)"
  syncing_value="$(printf '%s' "$syncing_resp" | python3 -c 'import json, sys; print(json.dumps(json.loads(sys.stdin.read()).get("result")))' 2>/dev/null || true)"

  if [[ -z "$block_hex" || -z "$block_ts_hex" ]]; then
    echo "verdict: unknown"
    echo "detail: unable to query execution head from $RPC_URL"
    return
  fi

  block_dec="$(python3 -c 'import sys; print(int(sys.argv[1], 16))' "$block_hex" 2>/dev/null || true)"
  block_ts_dec="$(python3 -c 'import sys; print(int(sys.argv[1], 16))' "$block_ts_hex" 2>/dev/null || true)"
  now_ts="$(date -u +%s)"
  lag_now="$((now_ts - block_ts_dec))"
  if (( lag_now < 0 )); then lag_now=0; fi

  verdict="unknown"
  detail="insufficient data"

  if [[ -n "$cons_dist" && "$cons_dist" -eq 0 ]]; then
    if [[ "$syncing_value" == "false" && "$lag_now" -gt 3600 ]]; then
      verdict="consensus_live_execution_stalled"
      detail="consensus is at head while execution lags by ${lag_now}s and eth_syncing=false"
    elif [[ "$syncing_value" != "false" ]]; then
      verdict="consensus_live_execution_syncing"
      detail="consensus is at head and execution reports syncing"
    else
      verdict="healthy"
      detail="consensus at head and execution lag is within tolerance"
    fi
  elif [[ -n "$cons_dist" && "$cons_dist" -gt 0 ]]; then
    verdict="consensus_catching_up"
    detail="consensus distance is ${cons_dist} blocks"
  elif [[ -z "$cons_dist" ]]; then
    verdict="consensus_unknown"
    detail="unable to parse consensus progress lines"
  fi

  echo "verdict: $verdict"
  echo "detail: $detail"
  if [[ -n "$cons_head" ]]; then echo "consensus_head: $cons_head"; fi
  if [[ -n "$cons_net" ]]; then echo "consensus_network_head: $cons_net"; fi
  if [[ -n "$cons_ts" ]]; then echo "consensus_timestamp: $cons_ts"; fi
  echo "execution_head: $block_dec"
  echo "execution_lag_seconds: $lag_now"
  echo "eth_syncing: ${syncing_value:-unavailable}"

  if [[ -f "$EXEC_STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$EXEC_STATE_FILE" || true
    if [[ -n "${epoch:-}" && -n "${block:-}" && -n "${lag:-}" ]]; then
      local dt dblock dlag
      dt="$((now_ts - epoch))"
      dblock="$((block_dec - block))"
      dlag="$((lag - lag_now))"
      if (( dt > 0 )); then
        printf 'execution_block_delta: %s (over %ss)\n' "$dblock" "$dt"
        printf 'execution_lag_delta_seconds: %s (positive means improving)\n' "$dlag"
      fi
    fi
  fi

  printf 'epoch=%s block=%s lag=%s\n' "$now_ts" "$block_dec" "$lag_now" > "$EXEC_STATE_FILE"
}

print_status() {
  echo ""
  echo "=== Container Status ==="
  docker inspect "$CONTAINER_NAME" --format 'name={{.Name}} status={{.State.Status}} running={{.State.Running}} restarts={{.RestartCount}} startedAt={{.State.StartedAt}} finishedAt={{.State.FinishedAt}}'

  echo ""
  echo "=== Port Bindings ==="
  docker port "$CONTAINER_NAME" 2>/dev/null || echo "(No exposed ports found)"

  echo ""
  echo "=== Last 5 sync progress lines ==="
  docker logs --tail "$LOG_SCAN_LINES" "$CONTAINER_NAME" 2>&1 | grep -E "observer follow progress|fetching consensus from peer" | tail -5 || echo "(No progress lines found yet - node may still be starting)"

  print_processing_breakdown
}

tail_logs() {
  echo "Tailing logs for $CONTAINER_NAME (Ctrl+C to stop)..."
  echo ""
  docker logs -f "$CONTAINER_NAME"
}

ensure_container_exists

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
    print_execution_eta
    ;;
  --diagnose|--health)
    print_status
    print_eta_estimate
    print_execution_eta
    print_health_verdict
    ;;
  --tail|tail|*)
    print_status
    echo ""
    tail_logs
    ;;
esac
