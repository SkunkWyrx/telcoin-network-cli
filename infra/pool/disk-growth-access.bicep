// One-time operator deployment. The runner can delegate only this disk role.
param runnerPrincipalId string
resource growthRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'observer-disk-growth')
  properties: {
    roleName: 'Oprail observer disk growth ${resourceGroup().name}'
    type: 'CustomRole'
    description: 'Read and resize observer data disks at an individual disk scope.'
    assignableScopes: [resourceGroup().id]
    permissions: [{actions: ['Microsoft.Compute/disks/read', 'Microsoft.Compute/disks/write'], notActions: []}]
  }
}
resource delegation 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, runnerPrincipalId, 'delegate-disk-growth')
  properties: {
    principalId: runnerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f58310d9-a9f6-439a-9e8d-f62e7b41a168')
    conditionVersion: '2.0'
    condition: '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${growthRole.name}})) AND ((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${growthRole.name}}))'
  }
}
output growthRoleId string = growthRole.id

param challengeZones array = []
param certificateVault string = ''
resource zones 'Microsoft.Network/dnsZones@2018-05-01' existing = [for zone in challengeZones: {
  name: zone
}]
resource dnsDelegation 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (zone, index) in challengeZones: {
  name: guid(zones[index].id, runnerPrincipalId, 'delegate-certificate-dns')
  scope: zones[index]
  properties: {
    principalId: runnerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f58310d9-a9f6-439a-9e8d-f62e7b41a168')
    conditionVersion: '2.0'
    condition: '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {befefa01-2a29-4197-83a8-272ff33ce314})) AND ((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {befefa01-2a29-4197-83a8-272ff33ce314}))'
  }
}]
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = if (!empty(certificateVault)) {
  name: certificateVault
}
resource certificateDelegation 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(certificateVault)) {
  name: guid(vault.id, runnerPrincipalId, 'delegate-certificate-publisher')
  scope: vault
  properties: {
    principalId: runnerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f58310d9-a9f6-439a-9e8d-f62e7b41a168')
    conditionVersion: '2.0'
    condition: '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {b86a8fe4-44ce-4948-aee5-eccb2c155cd7})) AND ((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {b86a8fe4-44ce-4948-aee5-eccb2c155cd7}))'
  }
}
