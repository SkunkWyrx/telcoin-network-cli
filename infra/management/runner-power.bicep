// This GitHub identity can power only the existing runner, never execute guest commands.
param location string = 'centralus'
param repository string = 'SkunkWyrx/telcoin-network-cli'
resource runner 'Microsoft.Compute/virtualMachines@2024-07-01' existing = {name: 'adiri-observer-runner'}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'adiri-runner-power'
  location: location
  tags: {purpose: 'oprail-runner-power', network: 'adiri'}
}
resource role 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id,'runner-power-only')
  properties: {
    roleName: 'Oprail Adiri runner power only'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{actions: ['Microsoft.Compute/virtualMachines/read','Microsoft.Compute/virtualMachines/instanceView/read','Microsoft.Compute/virtualMachines/start/action','Microsoft.Compute/virtualMachines/deallocate/action'], notActions: []}]
  }
}
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(runner.id,identity.id,'power-only')
  scope: runner
  properties: {principalId: identity.properties.principalId, principalType: 'ServicePrincipal',roleDefinitionId: role.id}
}
@batchSize(1)
resource trust 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = [for subject in ['ref:refs/heads/main','environment:observer-operations-adiri']: {
  parent: identity
  name: startsWith(subject,'ref:') ? 'main-only' : 'approved-operations'
  properties: {issuer: 'https://token.actions.githubusercontent.com',subject: 'repo:${repository}:${subject}',audiences: ['api://AzureADTokenExchange']}
}]
output clientId string = identity.properties.clientId
