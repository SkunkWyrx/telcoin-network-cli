#!/usr/bin/env bash
# Adiri management-site bootstrap only; never deploys observers or updates the VNet.
set -euo pipefail
subscription=8a149d4d-c52a-4432-929e-de754584e269
group=oprail-observer-adiri
identity=adiri-management-image-pull
subnet=oprailobservermanagement
registry=acaenvacrxvyzqyxhfzsno
azc() { az "$@" --subscription "$subscription"; }

delegation=$(azc network vnet subnet show -g "$group" --vnet-name oprail-observer-adiri-vnet -n "$subnet" --query 'delegations[].serviceName' -o tsv)
[[ "$delegation" == Microsoft.App/environments ]] || { echo 'Unexpected subnet delegation' >&2; exit 1; }
mode=$(azc acr show -g telcoin-network-testnet -n "$registry" --query roleAssignmentMode -o tsv)
[[ "$mode" == LegacyRegistryPermissions ]] || { echo 'Registry uses another permission mode; review repository-scoped access first.' >&2; exit 1; }

exists=$(azc identity list -g "$group" --query "[?name=='$identity'].name" -o tsv)
if [[ -z "$exists" ]]; then
  azc identity create -g "$group" -n "$identity" -l centralus --tags purpose=oprail-observer-management network=adiri --output none
fi
principal=$(azc identity show -g "$group" -n "$identity" --query principalId -o tsv)
scope=$(azc acr show -g telcoin-network-testnet -n "$registry" --query id -o tsv)
existing=$(azc role assignment list --scope "$scope" --query "[?principalId=='$principal' && roleDefinitionName=='AcrPull'].id" -o tsv)
if [[ -z "$existing" ]]; then
  azc role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal --role AcrPull --scope "$scope" --output none
fi
azc identity show -g "$group" -n "$identity" --query '{name:name,id:id,principalId:principalId}' -o json
azc role assignment list --scope "$scope" --query "[?principalId=='$principal'].{role:roleDefinitionName,scope:scope}" -o json
echo 'Management subnet and image-pull identity are ready. The site has not been deployed yet.'
