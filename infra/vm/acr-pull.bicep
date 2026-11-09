param acrName string
param principalId string
param assignmentSeed string
param vmId string
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {name: acrName}
resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, vmId, assignmentSeed, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
