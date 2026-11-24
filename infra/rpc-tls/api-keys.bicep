// Dedicated vault: API key readers cannot read the separate TLS private key vault.
param vaultName string
param network string = 'adiri'
param location string = resourceGroup().location
param managementPrincipalId string
param runnerPrincipalId string
param observerPrincipalIds array = []
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  tags: { purpose: 'oprail-rpc-api-keys', network: network }
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}
resource manager 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, managementPrincipalId, 'api-key-manager')
  scope: vault
  properties: {
    principalId: managementPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  }
}
resource readers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in observerPrincipalIds: {
  name: guid(vault.id, principal, 'api-key-reader')
  scope: vault
  properties: {
    principalId: principal
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}]
resource delegation 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, runnerPrincipalId, 'delegate-api-key-reader')
  scope: vault
  properties: {
    principalId: runnerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f58310d9-a9f6-439a-9e8d-f62e7b41a168')
    conditionVersion: '2.0'
    condition: '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {4633458b-17de-408a-b874-0445c86b69e6})) AND ((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {4633458b-17de-408a-b874-0445c86b69e6}))'
  }
}
output vaultId string = vault.id
