#!/usr/bin/env bash
# =============================================================================
# azure-setup-storage.sh
#
# Creates an Azure Storage Account and File Share for Telcoin observer data
# persistence. Run this ONCE before deploying the container.
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - ACR already created (run azure-setup-acr.sh or Step 1 in the docs first)
#
# Usage:
#   chmod +x scripts/azure-setup-storage.sh
#   ./scripts/azure-setup-storage.sh
# =============================================================================

set -euo pipefail

RESOURCE_GROUP="oprail-telcoin-testnet"
STORAGE_ACCOUNT="oprailtelcoinprem"   # Must be globally unique, lowercase, no hyphens, max 24 chars
FILE_SHARE_NAME="telcoin-data-v3"
LOCATION="eastus"                     # Change to match your resource group region if needed

echo "==> Creating storage account: ${STORAGE_ACCOUNT}"
az storage account create \
  --resource-group "${RESOURCE_GROUP}" \
  --name "${STORAGE_ACCOUNT}" \
  --location "${LOCATION}" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --output table

echo ""
echo "==> Fetching storage account key..."
STORAGE_KEY=$(az storage account keys list \
  --resource-group "${RESOURCE_GROUP}" \
  --account-name "${STORAGE_ACCOUNT}" \
  --query "[0].value" \
  --output tsv)

echo "==> Creating file share: ${FILE_SHARE_NAME}"
az storage share create \
  --name "${FILE_SHARE_NAME}" \
  --account-name "${STORAGE_ACCOUNT}" \
  --account-key "${STORAGE_KEY}" \
  --quota 32 \
  --output table

echo ""
echo "==> Done. Add these values to your .env or deployment environment:"
echo ""
echo "    STORAGE_ACCOUNT=${STORAGE_ACCOUNT}"
echo "    STORAGE_KEY=${STORAGE_KEY}"
echo "    FILE_SHARE_NAME=${FILE_SHARE_NAME}"
echo ""
echo "    (Keep STORAGE_KEY secret — do not commit it to source control)"
