// Pre-genesis observer infrastructure. No node/image/secret value is started or deployed.
param location string = 'centralus'
param sshPublicKey string
param operatorPrincipalId string
param customData string
param vmSize string = 'Standard_D4s_v6'
param identityVaultName string = 'oprail-mainnet-keys'
param backupVaultName string = 'oprail-mainnet-backup'
var nodeNames = ['mainnet-observer-1', 'mainnet-observer-2']
param provisionNodeNames array = ['mainnet-observer-1', 'mainnet-observer-2']
var tags = { network: 'mainnet', workload: 'observers', lifecycle: 'pre-genesis' }
resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'oprail-observer-mainnet-vnet'
  location: location
  tags: tags
  properties: { addressSpace: { addressPrefixes: ['10.54.0.0/16'] }, subnets: [{name: 'observer', properties: {addressPrefix: '10.54.0.0/24'}}] }
}
resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'observer'
}
resource privateLb 'Microsoft.Network/loadBalancers@2024-05-01' = {
  name: 'mainnet-observers'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    frontendIPConfigurations: [{name: 'rpc', properties: {privateIPAddress: '10.54.0.10', privateIPAllocationMethod: 'Static', subnet: {id: subnet.id}}}]
    backendAddressPools: [{name: 'observers'}]
    probes: [{name: 'synced', properties: {protocol: 'Http', port: 9090, requestPath: '/readyz', intervalInSeconds: 5, numberOfProbes: 2}}]
    loadBalancingRules: [for port in [8545, 8546]: {
      name: 'rpc-${port}'
      properties: {protocol: 'Tcp', frontendPort: port, backendPort: port, idleTimeoutInMinutes: 30, enableTcpReset: true, disableOutboundSnat: true, frontendIPConfiguration: {id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', 'mainnet-observers', 'rpc')}, backendAddressPool: {id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', 'mainnet-observers', 'observers')}, probe: {id: resourceId('Microsoft.Network/loadBalancers/probes', 'mainnet-observers', 'synced')}}
    }]
  }
}
resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'mainnet-observers-public-ip'
  location: location
  tags: tags
  sku: {name: 'Standard'}
  properties: {publicIPAllocationMethod: 'Static'}
}
resource publicLb 'Microsoft.Network/loadBalancers@2024-05-01' = {
  name: 'mainnet-observers-public'
  location: location
  tags: tags
  sku: {name: 'Standard'}
  properties: {
    frontendIPConfigurations: [{name: 'https', properties: {publicIPAddress: {id: publicIp.id}}}]
    backendAddressPools: [{name: 'observers'}]
    probes: [{name: 'proxy-synced', properties: {protocol: 'Http', port: 19091, requestPath: '/readyz', intervalInSeconds: 5, numberOfProbes: 2}}]
    loadBalancingRules: [{name: 'https', properties: {protocol: 'Tcp', frontendPort: 443, backendPort: 443, enableFloatingIP: true, disableOutboundSnat: true, frontendIPConfiguration: {id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', 'mainnet-observers-public', 'https')}, backendAddressPool: {id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', 'mainnet-observers-public', 'observers')}, probe: {id: resourceId('Microsoft.Network/loadBalancers/probes', 'mainnet-observers-public', 'proxy-synced')}}}]
  }
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: identityVaultName
  location: location
  tags: tags
  properties: {tenantId: tenant().tenantId, sku: {name: 'standard', family: 'A'}, enableRbacAuthorization: true, enablePurgeProtection: true, softDeleteRetentionInDays: 90}
}
resource backupVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: backupVaultName
  location: location
  tags: tags
  properties: {tenantId: tenant().tenantId, sku: {name: 'standard', family: 'A'}, enableRbacAuthorization: true, enablePurgeProtection: true, softDeleteRetentionInDays: 90}
}
resource operator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, operatorPrincipalId, 'custodian')
  properties: {principalId: operatorPrincipalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')}
}
resource backupOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: backupVault
  name: guid(backupVault.id, operatorPrincipalId, 'custodian')
  properties: {principalId: operatorPrincipalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')}
}
resource growthRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'observer-disk-growth')
  properties: {roleName: 'Oprail observer disk growth ${resourceGroup().name}', type: 'CustomRole', assignableScopes: [resourceGroup().id], permissions: [{actions: ['Microsoft.Compute/disks/read', 'Microsoft.Compute/disks/write'], notActions: []}]}
}
module nodes '../vm/main.bicep' = [for name in nodeNames: if (contains(provisionNodeNames, name)) {
  name: '${name}-prepare'
  dependsOn: [growthRole]
  params: {
    location: location
    vmName: name
    vmSize: vmSize
    availabilityZone: '1'
    executionDiskSizeGB: 50
    consensusDiskSizeGB: 50
    dataDiskIops: 5000
    dataDiskMbps: 125
    diskAutoGrowEnabled: true
    sshPublicKey: sshPublicKey
    sshSourceCidr: ''
    dnsLabel: 'oprail-${name}'
    existingSubnetId: subnet.id
    backendPoolId: '${privateLb.id}/backendAddressPools/observers'
    publicBackendPoolId: '${publicLb.id}/backendAddressPools/observers'
    publicHttpsFrontendIp: publicIp.properties.ipAddress
    privateRpcSourceCidrs: ['10.53.0.0/16']
    acrName: 'unused-pre-genesis'
    configureRegistryPull: false
    roleAssignmentSeed: name
    observerChain: 'mainnet'
    publicRpcEnabled: false
    customData: customData
  }
}]
output nodeNames array = nodeNames
output publicFrontend string = publicIp.properties.ipAddress
output passphraseVault string = vault.name
output backupVault string = backupVault.name
