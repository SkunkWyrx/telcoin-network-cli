// Separate controller VM: no observer resources are declared here.
param location string = 'centralus'
param name string = 'adiri-observer-runner'
param subnetId string
param sshPublicKey string
param customData string
var tags = { purpose: 'oprail-observer-runner', network: 'adiri' }
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${name}-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [{
      name: 'Deny-Inbound'
      properties: { priority: 100, direction: 'Inbound', access: 'Deny', protocol: '*', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '*' }
    }]
  }
}
// Explicit outbound Internet access for GitHub/Azure package feeds. No inbound
// ports are allowed; administration uses Azure Run Command.
resource ip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${name}-ip'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}
resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${name}-nic'
  location: location
  tags: tags
  properties: {
    networkSecurityGroup: { id: nsg.id }
    ipConfigurations: [{ name: 'primary', properties: { privateIPAllocationMethod: 'Dynamic', subnet: { id: subnetId }, publicIPAddress: { id: ip.id } } }]
  }
}
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: name
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: 'Standard_B2s' }
    networkProfile: { networkInterfaces: [{ id: nic.id }] }
    storageProfile: {
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: { createOption: 'FromImage', diskSizeGB: 32, managedDisk: { storageAccountType: 'StandardSSD_LRS' }, deleteOption: 'Delete' }
    }
    osProfile: {
      computerName: name
      adminUsername: 'azureuser'
      customData: customData
      linuxConfiguration: { disablePasswordAuthentication: true, ssh: { publicKeys: [{ path: '/home/azureuser/.ssh/authorized_keys', keyData: sshPublicKey }] } }
    }
  }
}
output principalId string = vm.identity.principalId
output privateIp string = nic.properties.ipConfigurations[0].properties.privateIPAddress
output outboundIp string = ip.properties.ipAddress
