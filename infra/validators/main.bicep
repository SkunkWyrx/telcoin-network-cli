param location string
@allowed(['adiri', 'mainnet'])
param network string
param vmName string
param vmSize string
param zone string
@allowed(['azureuser'])
param adminUsername string = 'azureuser'
param sshPublicKey string
param dnsLabel string
param vnetName string
param vnetCidr string
param nodeSubnetCidr string
param managementSubnetCidr string
param sshSources array = []
param p2pSources array = []
param executionSize int
param consensusSize int
param executionIops int
param consensusIops int
param executionMbps int
param consensusMbps int
param customData string
param backupAccountName string
@minValue(90)
param backupRetentionDays int = 90
param vaultName string
param operatorPrincipalId string
param autoGrow bool = true
var tags = { purpose: 'oprail-validator', network: network }
resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [vnetCidr] }
    subnets: [
      { name: 'validators', properties: { addressPrefix: nodeSubnetCidr } }
      { name: 'management', properties: { addressPrefix: managementSubnetCidr } }
    ]
  }
}
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${vmName}-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      ...(empty(sshSources) ? [] : [{
        name: 'Admin-SSH'
        properties: { priority: 100, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefixes: sshSources, sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '22' }
      }])
      ...(empty(p2pSources) ? [] : [{
        name: 'Approved-P2P'
        properties: { priority: 110, direction: 'Inbound', access: 'Allow', protocol: 'Udp', sourceAddressPrefixes: p2pSources, sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRanges: ['49590', '49594'] }
      }])
      // Override Azure's implicit VNet allow: no direct RPC or management ingress.
      { name: 'Deny-Other-Inbound', properties: { priority: 4000, direction: 'Inbound', access: 'Deny', protocol: '*', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '*' } }
    ]
  }
}
resource ip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${vmName}-ip'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static', dnsSettings: { domainNameLabel: dnsLabel } }
}
resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${vmName}-nic'
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: true
    networkSecurityGroup: { id: nsg.id }
    ipConfigurations: [{ name: 'primary', properties: { privateIPAllocationMethod: 'Dynamic', subnet: { id: '${vnet.id}/subnets/validators' }, publicIPAddress: { id: ip.id } } }]
  }
}
resource disks 'Microsoft.Compute/disks@2024-03-02' = [for (kind, i) in ['execution', 'consensus']: {
  name: '${vmName}-${kind}'
  location: location
  zones: [zone]
  tags: tags
  sku: { name: 'PremiumV2_LRS' }
  properties: {
    creationData: { createOption: 'Empty' }
    diskSizeGB: i == 0 ? executionSize : consensusSize
    diskIOPSReadWrite: i == 0 ? executionIops : consensusIops
    diskMBpsReadWrite: i == 0 ? executionMbps : consensusMbps
  }
}]
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  zones: [zone]
  tags: union(tags, { 'telcoin-role': 'validator', 'telcoin-chain': network })
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    securityProfile: { securityType: 'TrustedLaunch', uefiSettings: { secureBootEnabled: true, vTpmEnabled: true } }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      customData: customData
      linuxConfiguration: { disablePasswordAuthentication: true, ssh: { publicKeys: [{ path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: sshPublicKey }] } }
    }
    storageProfile: {
      diskControllerType: 'NVMe'
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: { createOption: 'FromImage', diskSizeGB: 64, managedDisk: { storageAccountType: 'Premium_LRS' }, deleteOption: 'Detach' }
      dataDisks: [for (kind, i) in ['execution', 'consensus']: { name: '${vmName}-${kind}', lun: i, createOption: 'Attach', caching: 'None', deleteOption: 'Detach', managedDisk: { id: disks[i].id } }]
    }
    networkProfile: { networkInterfaces: [{ id: nic.id }] }
  }
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  tags: tags
  properties: { tenantId: tenant().tenantId, sku: { name: 'standard', family: 'A' }, enableRbacAuthorization: true, enableSoftDelete: true, enablePurgeProtection: true, softDeleteRetentionInDays: 90 }
}
resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, vm.id, 'validator-passphrase-reader')
  scope: vault
  properties: { principalId: vm.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') }
}
resource operator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, operatorPrincipalId, 'validator-key-custodian')
  scope: vault
  properties: { principalId: operatorPrincipalId, roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7') }
}
resource growthRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = if (autoGrow) {
  name: guid(resourceGroup().id, 'validator-disk-growth')
  properties: {
    roleName: 'Oprail validator disk growth ${resourceGroup().name}'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{ actions: ['Microsoft.Compute/disks/read', 'Microsoft.Compute/disks/write'], notActions: [] }]
  }
}
resource growAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (kind, i) in ['execution', 'consensus']: if (autoGrow) {
  name: guid(disks[i].id, vm.id, 'validator-disk-growth')
  scope: disks[i]
  properties: { principalId: vm.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: growthRole.id }
}]
output publicIp string = ip.properties.ipAddress
output fqdn string = ip.properties.dnsSettings.fqdn
output vmId string = vm.id

// Separate from the VM lifecycle. No public blobs, shared keys, or destructive
// data-plane permissions. Two independently encrypted recovery envelopes.
resource backupAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: backupAccountName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    encryption: { keySource: 'Microsoft.Storage', requireInfrastructureEncryption: true, services: { blob: { enabled: true, keyType: 'Account' } } }
  }
}
resource backupService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: backupAccount
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: { enabled: true, days: backupRetentionDays }
    containerDeleteRetentionPolicy: { enabled: true, days: backupRetentionDays }
  }
}
resource backupContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for kind in ['identity', 'passphrase']: {
  parent: backupService
  name: kind
  properties: { publicAccess: 'None' }
}]
resource backupRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'validator-backup-no-delete')
  properties: {
    roleName: 'Oprail validator recovery objects ${resourceGroup().name}'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: ['Microsoft.Storage/storageAccounts/blobServices/containers/read']
      notActions: []
      dataActions: ['Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read', 'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write']
      notDataActions: []
    }]
  }
}
resource backupVmAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (kind, i) in ['identity', 'passphrase']: {
  name: guid(backupContainers[i].id, vm.id, 'backup')
  scope: backupContainers[i]
  properties: { principalId: vm.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: backupRole.id }
}]
resource backupCustodianAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (kind, i) in ['identity', 'passphrase']: {
  name: guid(backupContainers[i].id, operatorPrincipalId, 'backup')
  scope: backupContainers[i]
  properties: { principalId: operatorPrincipalId, roleDefinitionId: backupRole.id }
}]
output backupStorageAccount string = backupAccount.name

resource backupDeleteLock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'protect-validator-recovery'
  scope: backupAccount
  properties: { level: 'CanNotDelete', notes: 'Validator recovery material: remove only through an approved retirement process.' }
}
