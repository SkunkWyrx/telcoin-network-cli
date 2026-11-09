// Dedicated observer/API networks only. No explorer, indexer or public DNS changes.
param location string = resourceGroup().location
param observerVnetName string = 'oprail-observer-adiri-vnet'
param observerSubnetName string = 'observer'
param loadBalancerName string = 'adiri-observers'
param privateIp string = '10.52.0.10'
param apiResourceGroup string = 'oprail-api-adiri'
param apiVnetName string = 'oprail-api-adiri-vnet'
param apiAddressPrefix string = '10.51.0.0/16'
param apiSubnetPrefix string = '10.51.0.0/23'
// Empty until private DNS is explicitly requested; never touches public oprail.com.
param privateDnsName string = ''

resource observerVnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: observerVnetName
  location: location
  properties: {
    addressSpace: {addressPrefixes: ['10.52.0.0/16']}
    subnets: [{name: observerSubnetName, properties: {addressPrefix: '10.52.0.0/24'}}]
  }
}
resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: observerVnet
  name: observerSubnetName
}
module apiNetwork 'consumer-network.bicep' = {
  name: 'api-private-network'
  scope: resourceGroup(apiResourceGroup)
  params: {
    location: location
    vnetName: apiVnetName
    addressPrefix: apiAddressPrefix
    subnetPrefix: apiSubnetPrefix
    observerVnetId: observerVnet.id
  }
}
resource observerToApi 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: observerVnet
  name: 'oprail-api'
  properties: {
    remoteVirtualNetwork: {id: apiNetwork.outputs.vnetId}
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: false
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}
resource lb 'Microsoft.Network/loadBalancers@2024-05-01' = {
  name: loadBalancerName
  location: location
  sku: {name: 'Standard'}
  properties: {
    frontendIPConfigurations: [{
      name: 'rpc'
      properties: {
        privateIPAddress: privateIp
        privateIPAllocationMethod: 'Static'
        subnet: {id: subnet.id}
      }
    }]
    backendAddressPools: [{name: 'observers'}]
    probes: [{
      name: 'synced'
      properties: {protocol: 'Http', port: 9090, requestPath: '/readyz', intervalInSeconds: 5, numberOfProbes: 2}
    }]
    loadBalancingRules: [for port in [8545, 8546]: {
      name: 'rpc-${port}'
      properties: {
        protocol: 'Tcp'
        frontendPort: port
        backendPort: port
        idleTimeoutInMinutes: 30
        enableTcpReset: true
        disableOutboundSnat: true
        frontendIPConfiguration: {id: resourceId('Microsoft.Network/loadBalancers/frontendIPConfigurations', loadBalancerName, 'rpc')}
        backendAddressPool: {id: resourceId('Microsoft.Network/loadBalancers/backendAddressPools', loadBalancerName, 'observers')}
        probe: {id: resourceId('Microsoft.Network/loadBalancers/probes', loadBalancerName, 'synced')}
      }
    }]
  }
}
resource zone 'Microsoft.Network/privateDnsZones@2020-06-01' = if (!empty(privateDnsName)) {
  name: privateDnsName
  location: 'global'
}
resource record 'Microsoft.Network/privateDnsZones/A@2020-06-01' = if (!empty(privateDnsName)) {
  parent: zone
  name: '@'
  properties: {ttl: 60, aRecords: [{ipv4Address: privateIp}]}
}
resource observerDns 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (!empty(privateDnsName)) {
  parent: zone
  name: 'observers'
  location: 'global'
  properties: {registrationEnabled: false, virtualNetwork: {id: observerVnet.id}}
}
resource apiDns 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (!empty(privateDnsName)) {
  parent: zone
  name: 'api'
  location: 'global'
  properties: {registrationEnabled: false, virtualNetwork: {id: apiNetwork.outputs.vnetId}}
}
output backendPoolId string = '${lb.id}/backendAddressPools/observers'
output privateAddress string = privateIp
output subnetId string = subnet.id
output apiVnetId string = apiNetwork.outputs.vnetId
