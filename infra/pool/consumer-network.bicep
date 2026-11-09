param location string
param vnetName string
param addressPrefix string
param subnetPrefix string
param observerVnetId string

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: { addressPrefixes: [addressPrefix] }
    subnets: [{
      name: 'apps'
      properties: {
        addressPrefix: subnetPrefix
        delegations: [{name: 'container-apps', properties: {serviceName: 'Microsoft.App/environments'}}]
      }
    }]
  }
}
resource peer 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: network
  name: 'observers'
  properties: {
    remoteVirtualNetwork: {id: observerVnetId}
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: false
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}
output vnetId string = network.id
output subnetId string = '${network.id}/subnets/apps'
