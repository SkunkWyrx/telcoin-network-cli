# Deploy the Adiri management panel

This deploys only the management panel. Do not run the observer VM or pool
infrastructure templates to deploy this site. Mainnet remains disabled in
the workflows and requires its own resource group, profile, image repository,
sign-in configuration and approvals when it is introduced.

## 1. Sign in and find the management subnet

Run in the repository root on your Mac:

```bash
az login
az account set --subscription 8a149d4d-c52a-4432-929e-de754584e269
az network vnet subnet list -g oprail-observer-adiri \
  --vnet-name oprail-observer-adiri-vnet \
  --query '[].{name:name,id:id,prefix:addressPrefix,delegation:delegations[].serviceName}' -o json
```

Select the dedicated management subnet (currently the 10.52.2.0/27 range),
delegated to Microsoft.App/environments. Do not choose the observer subnet.
Set its actual name below. The Container Apps environment needs this subnet
at creation; it cannot be retrofitted after creation.

```bash
RG=oprail-observer-adiri
APP=oprail-observer-mgmt-adiri
ACR=acaenvacrxvyzqyxhfzsno
SUBNET_NAME='<actual-management-subnet-name>'
SUBNET_ID=$(az network vnet subnet show -g "$RG" \
  --vnet-name oprail-observer-adiri-vnet -n "$SUBNET_NAME" --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
OWNER_ID=$(az ad signed-in-user show --query id -o tsv)
ACR_ID=$(az acr show -g telcoin-network-testnet -n "$ACR" --query id -o tsv)
SERVER=$(az acr show -g telcoin-network-testnet -n "$ACR" --query loginServer -o tsv)
```

## 2. Create the site's image-pull identity

Use a new, dedicated managed identity. This needs permission to create a
role assignment on the registry. For the current registry's RBAC mode:

```bash
az identity create -g "$RG" -n adiri-management-image-pull -l centralus --output none
PULL_ID=$(az identity show -g "$RG" -n adiri-management-image-pull --query id -o tsv)
PULL_PRINCIPAL=$(az identity show -g "$RG" -n adiri-management-image-pull --query principalId -o tsv)
az role assignment create --assignee-object-id "$PULL_PRINCIPAL" \
  --assignee-principal-type ServicePrincipal --role AcrPull --scope "$ACR_ID" --output none
```

This identity has no observer VM write permissions. For a registry switched
to ABAC repository permissions, use its repository-reader equivalent instead.

## 3. Build the management image

This is a different image repository from the observer node binary. Only
the management project's directory is uploaded as build context.

```bash
TAG="bootstrap-$(date -u +%Y%m%d%H%M%S)"
az acr build -g telcoin-network-testnet -r "$ACR" --platform linux/amd64 \
  -t "observer-management/adiri:$TAG" src/Observer.Management
DIGEST=$(az acr repository show -n "$ACR" \
  --image "observer-management/adiri:$TAG" --query digest -o tsv)
```

## 4. Deploy the management-only template

The Entra registration already found is:
`oprail-observer-management-adiri`, client ID
`76ad8768-117d-401d-8ac7-1be8c02ba59d`.

Have its client secret **value** available locally. Do not paste it into
chat or put it in git. Azure CLI prompts for it because it is omitted here.

```bash
az deployment group create -g "$RG" --name adiri-management-panel \
  --mode Incremental --confirm-with-what-if \
  --template-file infra/management/main.bicep \
  --parameters network=adiri location=centralus appName="$APP" \
    tenantId="$TENANT_ID" clientId=76ad8768-117d-401d-8ac7-1be8c02ba59d \
    allowedObjectId="$OWNER_ID" observerResourceGroup="$RG" \
    loadBalancerName=adiri-observers publicLoadBalancerName=adiri-observers-public \
    referenceRpc=https://rpc.adiri.tel chainId=0x7e1 \
    managementSubnetId="$SUBNET_ID" registryServer="$SERVER" \
    registryPullIdentityId="$PULL_ID" image="$SERVER/observer-management/adiri@$DIGEST"
```

Confirm that the preview contains the management Container Apps environment
and management app only. It must not change the observer VMs, disks, load
balancers or the TelExplorer environment.

Copy the output URL. In Entra > App registrations >
oprail-observer-management-adiri > Authentication > Web, register that
exact URL followed by `/signin-oidc`. Do not guess the generated Azure hostname.
The registration also needs delegated Azure Service Management
`user_impersonation` consent. The site requires the matching user object ID
and Azure permissions on the observer resource group.

## 5. Verify the read-only deployment

Sign in and confirm both nodes, current block gaps, public membership and
traffic readiness appear. An account with a different Entra object ID must
be denied even if it is an Azure administrator. The health probe is the
only anonymous endpoint. Operations default to disabled. Configure no
public DNS for the admin panel unless desired; the generated HTTPS hostname
works with the sign-in registration.

## 6. Enable operations only after runner setup

See [the management runbook](observer-management.md) for the dedicated
private-network runner, persistent state, protected node template and scoped
permissions. Configure the Adiri GitHub environments with required reviewers.
The website token must have repository Actions read/write permission; enter
it directly into Azure as a Container App secret, not into chat.

Set `Operations__Enabled=true` only after testing login, health, GitHub
history and an approved read-only runner plan. The same image can later be
deployed by the Adiri-only site workflow. Production operations need the
current promoted image and completed snapshot manifests on the runner,
not the old versions from the original deployment.
