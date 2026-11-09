param location string = resourceGroup().location
param vmName string = 'telcoin-observer-vm-1'
param vmSize string = 'Standard_D4s_v6'
@allowed(['1', '2', '3'])
param availabilityZone string = '1'
@minValue(3000)
@maxValue(80000)
param dataDiskIops int = 5000
@minValue(125)
@maxValue(1200)
param dataDiskMbps int = 125
param adminUsername string = 'azureuser'
param sshPublicKey string
param sshSourceCidr string
param dnsLabel string
param existingSubnetId string = ''
param backendPoolId string = ''
param privateRpcSourceCidrs array = []
param acrResourceGroup string = resourceGroup().name
param acrName string
param customData string
// Keep this seed when retrying the same deployment; new VMs need a new assignment.
param roleAssignmentSeed string
param observerChain string = 'adiri'
param publicRpcUrl string = ''
param publicRpcEnabled bool = false
param publicRpcSourceCidr string = '*'
param executionSnapshotId string = ''
param consensusSnapshotId string = ''
var snapshotIds = [executionSnapshotId, consensusSnapshotId]


resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = if (empty(existingSubnetId)) {
  name: '${vmName}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.42.0.0/16'] }
    subnets: [{ name: 'observer', properties: { addressPrefix: '10.42.0.0/24' } }]
  }
}
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${vmName}-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'SSH'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: sshSourceCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      ...(empty(privateRpcSourceCidrs) ? [] : [{
        name: 'Private-RPC'
        properties: {priority: 115, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefixes: privateRpcSourceCidrs, sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRanges: ['8545', '8546']}
      }])
      {
        name: 'Readiness-Probe'
        properties: {priority: 116, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: 'AzureLoadBalancer', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '9090'}
      }
      {
        name: 'Observer-RPC'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: publicRpcEnabled ? 'Allow' : 'Deny'
          protocol: 'Tcp'
          sourceAddressPrefix: publicRpcEnabled ? publicRpcSourceCidr : '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRanges: ['8545', '8546']
        }
      }
      {
        name: 'Adiri-QUIC'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Udp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRanges: ['49590', '49594']
        }
      }
    ]
  }
}
resource ip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${vmName}-ip'
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: { domainNameLabel: dnsLabel }
  }
}
resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${vmName}-nic'
  location: location
  properties: {
    enableAcceleratedNetworking: true
    networkSecurityGroup: { id: nsg.id }
    ipConfigurations: [{
      name: 'primary'
      properties: {
        privateIPAllocationMethod: 'Dynamic'
        subnet: { id: empty(existingSubnetId) ? '${network.id}/subnets/observer' : existingSubnetId }
        loadBalancerBackendAddressPools: empty(backendPoolId) ? [] : [{id: backendPoolId}]
        publicIPAddress: { id: ip.id }
      }
    }]
  }
}
resource disks 'Microsoft.Compute/disks@2024-03-02' = [for (suffix, index) in ['execution', 'consensus']: {
  name: '${vmName}-${suffix}'
  location: location
  zones: [availabilityZone]
  sku: { name: 'PremiumV2_LRS' }
  properties: {
    creationData: empty(snapshotIds[index]) ? { createOption: 'Empty' } : {
      createOption: 'Copy'
      sourceResourceId: snapshotIds[index]
    }
    diskSizeGB: 512
    diskIOPSReadWrite: dataDiskIops
    diskMBpsReadWrite: dataDiskMbps
  }
}]
resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: vmName
  tags: {
    'telcoin-role': 'observer'
    'telcoin-chain': observerChain
    'telcoin-rpc-url': publicRpcEnabled ? (empty(publicRpcUrl) ? 'http://${dnsLabel}.${location}.cloudapp.azure.com:8545' : publicRpcUrl) : ''
    'telcoin-rpc-enabled': publicRpcEnabled ? 'true' : 'false'
  }
  location: location
  zones: [availabilityZone]
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      customData: customData
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: { publicKeys: [{ path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: sshPublicKey }] }
      }
    }
    storageProfile: {
      diskControllerType: 'NVMe'
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: { createOption: 'FromImage', diskSizeGB: 64, managedDisk: { storageAccountType: 'Premium_LRS' }, deleteOption: 'Detach' }
      dataDisks: [for (suffix, index) in ['execution', 'consensus']: {
        name: '${vmName}-${suffix}'
        lun: index
        createOption: 'Attach'
        caching: 'None'
        deleteOption: 'Detach'
        managedDisk: { id: disks[index].id }
      }]
    }
    networkProfile: { networkInterfaces: [{ id: nic.id }] }
  }
}
module pull 'acr-pull.bicep' = {
  name: '${vmName}-registry-access'
  scope: resourceGroup(acrResourceGroup)
  params: {
    acrName: acrName
    principalId: vm.identity.principalId
    assignmentSeed: roleAssignmentSeed
    vmId: vm.id
  }
}
output fqdn string = ip.properties.dnsSettings.fqdn
output vmId string = vm.id
