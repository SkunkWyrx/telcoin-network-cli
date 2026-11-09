#!/usr/bin/env bash
# Provision a new observer VM. No existing ACI/node data is migrated.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
if [[ "${1:-}" == --help ]]; then
  cat <<'HELP'
Settings are loaded automatically from the project .env (or ENV_FILE).
Exported settings override values in that file.
Required settings: ACR_NAME, VM_DNS_LABEL, SSH_SOURCE_CIDR,
SSH_PUBLIC_KEY_FILE, SSH_PRIVATE_KEY_FILE, TN_BLS_PASSPHRASE.
Optional: AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_LOCATION,
VM_NAME, VM_SIZE, VM_ZONE, DATA_DISK_IOPS, DATA_DISK_MBPS, VM_ADMIN_USERNAME, GIT_TAG, GIT_BRANCH, GIT_REPO_URL,
IMAGE_REPO, IMAGE_TAG, SKIP_IMAGE_BUILD=true, CHAIN.
Restore: EXECUTION_SNAPSHOT_ID and CONSENSUS_SNAPSHOT_ID (provide both).
Use --preview to render the ARM parameters and cloud-init locally without
contacting Azure or writing the passphrase. Output goes to PREVIEW_DIR.
HELP
  exit 0
fi
MODE="${1:-deploy}"
[[ "$MODE" == deploy || "$MODE" == --preview ]] || { echo 'Use --help or --preview' >&2; exit 1; }
# Load project settings, retaining explicit environment overrides (including empty tags).
load_env_file() {
  local file="$1" name i
  local saved_names=() saved_values=()
  for name in AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP AZURE_LOCATION ACR_NAME \
      VM_NAME VM_SIZE VM_ZONE DATA_DISK_IOPS DATA_DISK_MBPS VM_ADMIN_USERNAME VM_DNS_LABEL SSH_SOURCE_CIDR \
      SSH_PUBLIC_KEY_FILE SSH_PRIVATE_KEY_FILE TN_BLS_PASSPHRASE GIT_TAG GIT_BRANCH \
      GIT_REPO_URL IMAGE_REPO IMAGE_TAG IMAGE_DIGEST ACR_RESOURCE_GROUP EXISTING_SUBNET_ID BACKEND_POOL_ID PRIVATE_RPC_SOURCE_CIDRS EXPECTED_CHAIN_ID NETWORK_RPC_URL SKIP_IMAGE_BUILD CHAIN HTTP_API WS_ENABLED WS_API PUBLIC_RPC_URL PUBLIC_RPC_ENABLED PUBLIC_RPC_SOURCE_CIDR PREVIEW_DIR EXECUTION_SNAPSHOT_ID CONSENSUS_SNAPSHOT_ID; do
    if declare -p "$name" >/dev/null 2>&1; then
      saved_names+=("$name")
      saved_values+=("${!name}")
    fi
  done
  set -a
  # .env is a trusted local shell settings file, matching the publishing scripts.
  # shellcheck disable=SC1090
  source "$file"
  set +a
  for ((i=0; i<${#saved_names[@]}; i++)); do
    export "${saved_names[$i]}=${saved_values[$i]}"
  done
}
if [[ -f "${ENV_FILE:-$ROOT_DIR/.env}" ]]; then
  load_env_file "${ENV_FILE:-$ROOT_DIR/.env}"
elif [[ -n "${ENV_FILE:-}" ]]; then
  echo "Cannot read ENV_FILE: $ENV_FILE" >&2; exit 1
fi
RG="${AZURE_RESOURCE_GROUP:-oprail-telcoin-testnet}"
LOCATION="${AZURE_LOCATION:-eastus}"
VM_NAME="${VM_NAME:-telcoin-observer-vm-1}"
VM_SIZE="${VM_SIZE:-Standard_D4s_v6}"
VM_ZONE="${VM_ZONE:-1}"
DATA_DISK_IOPS="${DATA_DISK_IOPS:-5000}"
DATA_DISK_MBPS="${DATA_DISK_MBPS:-125}"
ADMIN="${VM_ADMIN_USERNAME:-azureuser}"
: "${ACR_NAME:?Set ACR_NAME to your registry name}"
: "${VM_DNS_LABEL:?Set a unique VM_DNS_LABEL}"
: "${SSH_SOURCE_CIDR:?Set SSH_SOURCE_CIDR to your administrative IP/CIDR}"
: "${SSH_PUBLIC_KEY_FILE:?Set SSH_PUBLIC_KEY_FILE}"
[[ -r "$SSH_PUBLIC_KEY_FILE" ]] || { echo 'Cannot read SSH public key' >&2; exit 1; }
GIT_TAG="${GIT_TAG-v0.16.0-adiri}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_REPO_URL="${GIT_REPO_URL:-https://github.com/telcoin-association/telcoin-network.git}"
IMAGE_REPO="${IMAGE_REPO:-telcoin-observer}"
IMAGE_TAG="${IMAGE_TAG:-${GIT_TAG:-branch-build}}"
REGISTRY="${ACR_NAME}.azurecr.io"
IMAGE="${REGISTRY}/${IMAGE_REPO}:${IMAGE_TAG}"
if [[ -n "${IMAGE_DIGEST:-}" ]]; then
  [[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Invalid IMAGE_DIGEST' >&2; exit 1; }
  IMAGE="${REGISTRY}/${IMAGE_REPO}@${IMAGE_DIGEST}"
fi
export EXISTING_SUBNET_ID="${EXISTING_SUBNET_ID:-}" BACKEND_POOL_ID="${BACKEND_POOL_ID:-}"
export PRIVATE_RPC_SOURCE_CIDRS="${PRIVATE_RPC_SOURCE_CIDRS:-}"
export ACR_RESOURCE_GROUP="${ACR_RESOURCE_GROUP:-$RG}"
export EXPECTED_CHAIN_ID="${EXPECTED_CHAIN_ID:-}" NETWORK_RPC_URL="${NETWORK_RPC_URL:-https://rpc.adiri.tel}"
[[ -z "$BACKEND_POOL_ID" || -n "$EXPECTED_CHAIN_ID" ]] || { echo 'Pool nodes require EXPECTED_CHAIN_ID' >&2; exit 1; }

FQDN="${VM_DNS_LABEL}.${LOCATION}.cloudapp.azure.com"
CHAIN="${CHAIN:-adiri}"
EXECUTION_SNAPSHOT_ID="${EXECUTION_SNAPSHOT_ID:-}"
CONSENSUS_SNAPSHOT_ID="${CONSENSUS_SNAPSHOT_ID:-}"
if [[ -n "$EXECUTION_SNAPSHOT_ID" && -z "$CONSENSUS_SNAPSHOT_ID" || -z "$EXECUTION_SNAPSHOT_ID" && -n "$CONSENSUS_SNAPSHOT_ID" ]]; then
  echo 'Provide both snapshot IDs from the same capture, or neither.' >&2; exit 1
fi
export PUBLIC_RPC_ENABLED="${PUBLIC_RPC_ENABLED:-false}" PUBLIC_RPC_SOURCE_CIDR="${PUBLIC_RPC_SOURCE_CIDR:-*}"
export HTTP_API="${HTTP_API:-eth,net,web3,rpc,debug,trace}" WS_ENABLED="${WS_ENABLED:-false}"
export WS_API="${WS_API:-$HTTP_API}"
[[ "$WS_ENABLED" == true || "$WS_ENABLED" == false ]] || { echo "WS_ENABLED must be true or false" >&2; exit 1; }
export PUBLIC_RPC_URL="${PUBLIC_RPC_URL:-}"
export EXECUTION_SNAPSHOT_ID CONSENSUS_SNAPSHOT_ID
export RG LOCATION VM_NAME VM_SIZE VM_ZONE DATA_DISK_IOPS DATA_DISK_MBPS ADMIN ACR_NAME VM_DNS_LABEL SSH_SOURCE_CIDR SSH_PUBLIC_KEY_FILE CHAIN FQDN
umask 077
if [[ "$MODE" == --preview ]]; then
  : "${PREVIEW_DIR:?Set PREVIEW_DIR for rendered files}"
  mkdir -p "$PREVIEW_DIR"
  WORK_DIR="$PREVIEW_DIR"
else
  : "${SSH_PRIVATE_KEY_FILE:?Set SSH_PRIVATE_KEY_FILE}"
  : "${TN_BLS_PASSPHRASE:?Set TN_BLS_PASSPHRASE in .env}"
  [[ -r "$SSH_PRIVATE_KEY_FILE" ]] || { echo 'Cannot read SSH private key' >&2; exit 1; }
  WORK_DIR=$(mktemp -d)
  trap 'rm -rf "$WORK_DIR"' EXIT
fi
python3 infra/vm/render-cloud-init.py "$REGISTRY" "$IMAGE" "$FQDN" > "$WORK_DIR/cloud-init.yaml"
export WORK_DIR
python3 - <<'PY'
import base64, ipaddress, json, os, re, uuid
from pathlib import Path
get = os.environ.__getitem__
ipaddress.ip_network(get('SSH_SOURCE_CIDR'), strict=False)
for key, pattern in [('VM_NAME',r'[a-zA-Z][a-zA-Z0-9-]{0,63}'), ('ADMIN',r'[a-z_][a-z0-9_-]*'), ('ACR_NAME',r'[a-zA-Z0-9]{5,50}'), ('VM_DNS_LABEL',r'[a-z][a-z0-9-]{1,61}[a-z0-9]')]:
    if not re.fullmatch(pattern,get(key)): raise SystemExit('Invalid '+key)
if get('VM_ZONE') not in ('1','2','3'): raise SystemExit('Invalid VM_ZONE')
iops, mbps = int(get('DATA_DISK_IOPS')), int(get('DATA_DISK_MBPS'))
if not (3000 <= iops <= 80000 and 125 <= mbps <= min(1200, iops // 4)):
    raise SystemExit('Invalid Premium SSD v2 IOPS/throughput settings')
if get('PUBLIC_RPC_ENABLED') not in ('true', 'false'): raise SystemExit('PUBLIC_RPC_ENABLED must be true or false')
if get('PUBLIC_RPC_SOURCE_CIDR') != '*': ipaddress.ip_network(get('PUBLIC_RPC_SOURCE_CIDR'), strict=False)
private_cidrs=[v.strip() for v in get('PRIVATE_RPC_SOURCE_CIDRS').split(',') if v.strip()]
for cidr in private_cidrs: ipaddress.ip_network(cidr, strict=False)
values = {'existingSubnetId':get('EXISTING_SUBNET_ID'), 'backendPoolId':get('BACKEND_POOL_ID'), 'privateRpcSourceCidrs':private_cidrs, 'acrResourceGroup':get('ACR_RESOURCE_GROUP'), 'roleAssignmentSeed':str(uuid.uuid4()), 'publicRpcEnabled':get('PUBLIC_RPC_ENABLED') == 'true', 'publicRpcSourceCidr':get('PUBLIC_RPC_SOURCE_CIDR'), 'observerChain':get('CHAIN'), 'publicRpcUrl':get('PUBLIC_RPC_URL'), 'executionSnapshotId':get('EXECUTION_SNAPSHOT_ID'), 'consensusSnapshotId':get('CONSENSUS_SNAPSHOT_ID'), 'availabilityZone':get('VM_ZONE'), 'dataDiskIops':iops, 'dataDiskMbps':mbps, 'location':get('LOCATION'),'vmName':get('VM_NAME'),'vmSize':get('VM_SIZE'),
 'adminUsername':get('ADMIN'),'sshSourceCidr':get('SSH_SOURCE_CIDR'),
 'dnsLabel':get('VM_DNS_LABEL'),'acrName':get('ACR_NAME'),
 'sshPublicKey':Path(get('SSH_PUBLIC_KEY_FILE')).read_text().strip(),
 'customData':base64.b64encode(Path(get('WORK_DIR'),'cloud-init.yaml').read_bytes()).decode()}
Path(get('WORK_DIR'),'parameters.json').write_text(json.dumps({'parameters':{k:{'value':v} for k,v in values.items()}}))
PY
if [[ "$MODE" == --preview ]]; then
  echo "Rendered $WORK_DIR/cloud-init.yaml and parameters.json (no secrets)."
  exit 0
fi
AZ_ARGS=()
[[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]] || AZ_ARGS=(--subscription "$AZURE_SUBSCRIPTION_ID")
azc() { az "$@" ${AZ_ARGS[@]+"${AZ_ARGS[@]}"}; }
# Avoid changing customData or unexpectedly reconfiguring an existing node.
EXISTING=$(azc vm list -g "$RG" --query "[?name=='${VM_NAME}'].name" -o tsv)
[[ -z "$EXISTING" ]] || { echo "VM $VM_NAME already exists. Manage its service/image over SSH; this command provisions new nodes only." >&2; exit 1; }
azc group show -n "$RG" --output none
EXISTING_DISKS=$(azc disk list -g "$RG" --query "[?name=='${VM_NAME}-execution' || name=='${VM_NAME}-consensus'].name" -o tsv)
[[ -z "$EXISTING_DISKS" ]] || { echo "Data disks with this VM name already exist. Use a new VM name; retained disks require an explicit recovery plan." >&2; exit 1; }
# Fail before building/provisioning if a snapshot is incompatible or still copying.
if [[ -n "$EXECUTION_SNAPSHOT_ID" ]]; then
  for snapshot in "$EXECUTION_SNAPSHOT_ID" "$CONSENSUS_SNAPSHOT_ID"; do
    azc snapshot show --ids "$snapshot" -o json > "$WORK_DIR/snapshot.json"
    python3 - "$WORK_DIR/snapshot.json" "$LOCATION" <<'CHECK'
import json, sys
s = json.load(open(sys.argv[1]))
if s['location'].lower() != sys.argv[2].lower():
    raise SystemExit('Snapshot must be copied to the deployment region first')
if s.get('provisioningState') != 'Succeeded' or s.get('completionPercent', 0) != 100:
    raise SystemExit('Snapshot is not ready; wait for completionPercent=100')
if s.get('diskSizeGB', s.get('diskSizeGb', 0)) > 512:
    raise SystemExit('Snapshot exceeds the configured 512 GiB disk size')
CHECK
  done
fi
# Build through ACR (Linux/amd64), not the developer's local architecture.
if [[ "${SKIP_IMAGE_BUILD:-false}" != true ]]; then
  mkdir -p "$WORK_DIR/build-context/src/docker"
  cp src/docker/Dockerfile src/docker/entrypoint.sh "$WORK_DIR/build-context/src/docker/"
  azc acr build --registry "$ACR_NAME" --resource-group "$RG" --platform linux/amd64 \
    --image "${IMAGE_REPO}:${IMAGE_TAG}" --build-arg "GIT_TAG=${GIT_TAG}" \
    --build-arg "GIT_BRANCH=${GIT_BRANCH}" --build-arg "GIT_REPO_URL=${GIT_REPO_URL}" \
    --file src/docker/Dockerfile "$WORK_DIR/build-context" --output none
fi
azc deployment group create -g "$RG" --name "${VM_NAME}-deploy" \
  --template-file infra/vm/main.bicep --parameters "@$WORK_DIR/parameters.json" --output none
FQDN=$(azc network public-ip show -g "$RG" -n "${VM_NAME}-ip" --query dnsSettings.fqdn -o tsv)
export FQDN
SSH=(ssh -i "$SSH_PRIVATE_KEY_FILE" -o IgnoreUnknown=UseKeychain -o UseKeychain=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$ADMIN@$FQDN")
echo "Waiting for SSH and cloud-init on $FQDN..."
READY=false
for attempt in $(seq 1 60); do
  if "${SSH[@]}" true 2>/dev/null; then READY=true; break; fi
  sleep 10
done
[[ "$READY" == true ]] || { echo 'SSH unavailable; check the SSH CIDR, key, and VM boot diagnostics.' >&2; exit 1; }
"${SSH[@]}" 'sudo cloud-init status --wait' || { echo 'cloud-init failed; inspect /var/log/cloud-init-output.log on the VM.' >&2; exit 1; }
export TN_BLS_PASSPHRASE
python3 - <<'PY' > "$WORK_DIR/observer.env"
import os
values={'HTTP_API':os.environ['HTTP_API'],'WS_ENABLED':os.environ['WS_ENABLED'],'WS_API':os.environ['WS_API'],'TN_BLS_PASSPHRASE':os.environ['TN_BLS_PASSPHRASE'],'DATADIR':'/var/lib/telcoin',
 'CHAIN':os.environ['CHAIN'],'PRIMARY_LISTENER_MULTIADDR':'/ip4/0.0.0.0/udp/49590/quic-v1',
 'WORKER_LISTENER_MULTIADDR':'/ip4/0.0.0.0/udp/49594/quic-v1',
 'EXTERNAL_PRIMARY_MULTIADDR':f"/dns4/{os.environ['FQDN']}/udp/49590/quic-v1",
 'EXTERNAL_WORKER_MULTIADDR':f"/dns4/{os.environ['FQDN']}/udp/49594/quic-v1"}
for k,v in values.items():
 if '\n' in v or '\r' in v: raise SystemExit('Environment values must be single-line')
 print(f'{k}={v}')
PY
# Transport the secret over SSH, never through ARM/cloud-init or command arguments.
"${SSH[@]}" 'sudo install -m 600 /dev/stdin /etc/telcoin/observer.env' < "$WORK_DIR/observer.env"
"${SSH[@]}" 'sudo systemctl start telcoin-observer && sudo systemctl status telcoin-observer --no-pager'
echo "Observer provisioned on $FQDN. Check sync with: ssh -i $SSH_PRIVATE_KEY_FILE $ADMIN@$FQDN sudo journalctl -u telcoin-observer -f"
