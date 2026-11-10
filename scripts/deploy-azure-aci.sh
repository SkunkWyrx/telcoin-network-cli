#!/usr/bin/env bash
# Deploy Telcoin observer to Azure Container Instances with Azure Files persistence.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RG="${AZURE_RESOURCE_GROUP:-oprail-telcoin-testnet}"
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
AZURE_SUBSCRIPTION_NAME="${AZURE_SUBSCRIPTION_NAME:-Pay-As-You-Go}"
ACR_NAME="${ACR_NAME:-oprailtelcoin}"
ACI_NAME="${ACI_NAME:-telcoin-observer-1}"
DNS_LABEL="${ACI_DNS_LABEL:-$ACI_NAME}"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-oprailtelcoinprem}"
FILE_SHARE_NAME="${FILE_SHARE_NAME:-telcoin-data-v3}"
FILE_SHARE_QUOTA_GB="${FILE_SHARE_QUOTA_GB:-500}"
IMAGE_REPO="${IMAGE_REPO:-telcoin-observer}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
SKIP_IMAGE_BUILD="${SKIP_IMAGE_BUILD:-false}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_TAG="${GIT_TAG-v0.17.0-adiri}"
GIT_REPO_URL="${GIT_REPO_URL:-https://github.com/telcoin-association/telcoin-network.git}"
TELCOIN_CPU="${TELCOIN_CPU:-2.0}"
TELCOIN_MEMORY_GB="${TELCOIN_MEMORY_GB:-8.0}"
DEPLOY_TEMPLATE="infra/aci/telcoin-observer-aci.yml"
DEPLOY_FILE="/tmp/${ACI_NAME}.aci.deploy.yml"
LOG_ANALYTICS_WORKSPACE_NAME="${LOG_ANALYTICS_WORKSPACE_NAME:-telcoin-observer-logs}"

if [[ -f ".env" ]]; then
  # Load only simple KEY=VALUE pairs from .env for deployment defaults.
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${TN_BLS_PASSPHRASE:-}" ]]; then
  echo "TN_BLS_PASSPHRASE is required. Set it in .env or export it before running this script." >&2
  exit 1
fi

if [[ -z "$AZURE_SUBSCRIPTION_ID" ]]; then
  AZURE_SUBSCRIPTION_ID="$(az account list --query "[?name=='${AZURE_SUBSCRIPTION_NAME}'].id | [0]" -o tsv)"
fi

if [[ -z "$AZURE_SUBSCRIPTION_ID" ]]; then
  AZURE_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
fi

if [[ -n "$AZURE_SUBSCRIPTION_ID" ]]; then
  if ! az account set --subscription "$AZURE_SUBSCRIPTION_ID" >/dev/null 2>&1; then
    echo "Subscription '$AZURE_SUBSCRIPTION_ID' was not found or you do not have access." >&2
    echo "Run: az account list -o table" >&2
    exit 1
  fi
fi

AZ_SUB_ARGS=(--subscription "$AZURE_SUBSCRIPTION_ID")
azc() {
  az "$@" "${AZ_SUB_ARGS[@]}"
}

echo "Using Azure subscription: ${AZURE_SUBSCRIPTION_ID}"

ensure_provider_registered() {
  local namespace="$1"
  local state
  state="$(azc provider show --namespace "$namespace" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")"
  if [[ "$state" != "Registered" ]]; then
    echo "Registering provider ${namespace} (current state: ${state})..."
    azc provider register --namespace "$namespace" --wait --output none
    state="$(azc provider show --namespace "$namespace" --query registrationState -o tsv)"
    echo "Provider ${namespace} registration state: ${state}"
  fi
}

ensure_provider_registered "Microsoft.Storage"
ensure_provider_registered "Microsoft.ContainerInstance"
ensure_provider_registered "Microsoft.ContainerRegistry"

echo "Target resource group: ${RG}"
if ! RG_CHECK_OUTPUT="$(azc group show -n "$RG" --query name -o tsv 2>&1)"; then
  echo "Failed to resolve resource group '${RG}' in subscription '${AZURE_SUBSCRIPTION_ID}'." >&2
  echo "Azure CLI error:" >&2
  echo "${RG_CHECK_OUTPUT}" >&2
  echo "Available groups in this subscription:" >&2
  azc group list --query "[].name" -o tsv >&2 || true
  exit 1
fi

LOCATION="$(azc group show -n "$RG" --query location -o tsv)"
IMAGE_FQDN="${ACR_NAME}.azurecr.io/${IMAGE_REPO}:${IMAGE_TAG}"
EXTERNAL_PRIMARY_MULTIADDR="${EXTERNAL_PRIMARY_MULTIADDR:-/dns4/${DNS_LABEL}.${LOCATION}.azurecontainer.io/udp/49590/quic-v1}"
EXTERNAL_WORKER_MULTIADDR="${EXTERNAL_WORKER_MULTIADDR:-/dns4/${DNS_LABEL}.${LOCATION}.azurecontainer.io/udp/49594/quic-v1}"

if ! azc acr show -n "$ACR_NAME" >/dev/null 2>&1; then
  echo "Creating ACR ${ACR_NAME} in ${RG}..."
  azc acr create \
    --resource-group "$RG" \
    --name "$ACR_NAME" \
    --sku Basic \
    --admin-enabled true \
    --location "$LOCATION" \
    --output none
fi

SKIP_IMAGE_BUILD_NORMALIZED="$(printf '%s' "$SKIP_IMAGE_BUILD" | tr '[:upper:]' '[:lower:]')"
if [[ "$SKIP_IMAGE_BUILD_NORMALIZED" == "true" ]]; then
  echo "Skipping image build and using existing image ${IMAGE_FQDN}."
else
  echo "Building image ${IMAGE_FQDN} in ACR ${ACR_NAME}..."
  azc acr build \
    --registry "$ACR_NAME" \
    --resource-group "$RG" \
    --image "${IMAGE_REPO}:${IMAGE_TAG}" \
    --build-arg "GIT_BRANCH=${GIT_BRANCH}" \
    --build-arg "GIT_TAG=${GIT_TAG}" \
    --build-arg "GIT_REPO_URL=${GIT_REPO_URL}" \
    --file src/docker/Dockerfile \
    . \
    --output none
fi

if ! azc storage account show -g "$RG" -n "$STORAGE_ACCOUNT" >/dev/null 2>&1; then
  echo "Creating storage account ${STORAGE_ACCOUNT} in ${LOCATION}..."
  azc storage account create \
    --resource-group "$RG" \
    --name "$STORAGE_ACCOUNT" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --output none
fi

echo "Ensuring Azure File share ${FILE_SHARE_NAME} (${FILE_SHARE_QUOTA_GB} GiB quota)..."
if ! azc storage share-rm show --resource-group "$RG" --storage-account "$STORAGE_ACCOUNT" --name "$FILE_SHARE_NAME" >/dev/null 2>&1; then
  azc storage share-rm create \
    --resource-group "$RG" \
    --storage-account "$STORAGE_ACCOUNT" \
    --name "$FILE_SHARE_NAME" \
    --quota "$FILE_SHARE_QUOTA_GB" \
    --output none
else
  azc storage share-rm update \
    --resource-group "$RG" \
    --storage-account "$STORAGE_ACCOUNT" \
    --name "$FILE_SHARE_NAME" \
    --quota "$FILE_SHARE_QUOTA_GB" \
    --output none
fi

  ACR_USER="$(azc acr credential show -n "$ACR_NAME" --query username -o tsv)"
ACR_PASS="$(azc acr credential show -n "$ACR_NAME" --query passwords[0].value -o tsv)"
STORAGE_KEY="$(azc storage account keys list -g "$RG" -n "$STORAGE_ACCOUNT" --query "[0].value" -o tsv)"

# Log Analytics workspace — create if it does not exist, then fetch credentials
if ! azc monitor log-analytics workspace show -g "$RG" -n "$LOG_ANALYTICS_WORKSPACE_NAME" >/dev/null 2>&1; then
  echo "Creating Log Analytics workspace ${LOG_ANALYTICS_WORKSPACE_NAME}..."
  azc monitor log-analytics workspace create \
    --resource-group "$RG" \
    --workspace-name "$LOG_ANALYTICS_WORKSPACE_NAME" \
    --location "$LOCATION" \
    --retention-time 30 \
    --output none
fi
LOG_WORKSPACE_ID="$(azc monitor log-analytics workspace show -g "$RG" -n "$LOG_ANALYTICS_WORKSPACE_NAME" --query customerId -o tsv)"
LOG_WORKSPACE_KEY="$(azc monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LOG_ANALYTICS_WORKSPACE_NAME" --query primarySharedKey -o tsv)"

cp "$DEPLOY_TEMPLATE" "$DEPLOY_FILE"

sed -i '' "s|name: telcoin-observer-1|name: ${ACI_NAME}|" "$DEPLOY_FILE"
sed -i '' "s|location: eastus|location: ${LOCATION}|" "$DEPLOY_FILE"
sed -i '' "s|dnsNameLabel: telcoin-observer-1|dnsNameLabel: ${DNS_LABEL}|" "$DEPLOY_FILE"
sed -i '' "s|shareName: telcoin-data-v3|shareName: ${FILE_SHARE_NAME}|" "$DEPLOY_FILE"
sed -i '' "s|storageAccountName: oprailtelcoindata|storageAccountName: ${STORAGE_ACCOUNT}|" "$DEPLOY_FILE"
sed -i '' "s|image: oprailtelcoin.azurecr.io/telcoin-observer:latest|image: ${IMAGE_FQDN}|" "$DEPLOY_FILE"
sed -i '' "s|cpu: 2.0|cpu: ${TELCOIN_CPU}|" "$DEPLOY_FILE"
sed -i '' "s|memoryInGB: 4.0|memoryInGB: ${TELCOIN_MEMORY_GB}|" "$DEPLOY_FILE"

sed -i '' "s|<REPLACE_ACR_USERNAME>|${ACR_USER}|" "$DEPLOY_FILE"
sed -i '' "s|<REPLACE_ACR_PASSWORD>|${ACR_PASS}|" "$DEPLOY_FILE"
sed -i '' "s|<REPLACE_STORAGE_ACCOUNT_KEY>|${STORAGE_KEY}|" "$DEPLOY_FILE"
sed -i '' "s|<REPLACE_EXTERNAL_PRIMARY_MULTIADDR>|${EXTERNAL_PRIMARY_MULTIADDR}|" "$DEPLOY_FILE"
sed -i '' "s|<REPLACE_EXTERNAL_WORKER_MULTIADDR>|${EXTERNAL_WORKER_MULTIADDR}|" "$DEPLOY_FILE"
sed -i '' "s|<REPLACE_BLS_PASSPHRASE>|${TN_BLS_PASSPHRASE}|" "$DEPLOY_FILE"
sed -i '' "s|<REPLACE_LOG_ANALYTICS_WORKSPACE_ID>|${LOG_WORKSPACE_ID}|" "$DEPLOY_FILE"
sed -i '' "s|<REPLACE_LOG_ANALYTICS_WORKSPACE_KEY>|${LOG_WORKSPACE_KEY}|" "$DEPLOY_FILE"

if azc container show -g "$RG" -n "$ACI_NAME" >/dev/null 2>&1; then
  echo "Replacing existing container group ${ACI_NAME}..."
  azc container delete -g "$RG" -n "$ACI_NAME" --yes --output none
fi

echo "Deploying ACI container group ${ACI_NAME}..."
azc container create \
  --resource-group "$RG" \
  --file "$DEPLOY_FILE" \
  --output none

echo "Deployment complete."
azc container show \
  --resource-group "$RG" \
  --name "$ACI_NAME" \
  --query '{name:name,state:containers[0].instanceView.currentState.state,fqdn:ipAddress.fqdn,ip:ipAddress.ip}' \
  -o table
