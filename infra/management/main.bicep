// Management-only resources. The subnet, registry and pull identity are pre-existing.
param location string = 'centralus'
@allowed(['adiri', 'mainnet'])
param network string
param appName string = 'oprail-observer-mgmt-${network}'
param tenantId string
param clientId string
param allowedObjectId string
@secure()
param clientSecret string
@secure()
param githubDispatchToken string = ''
param subscriptionId string = subscription().subscriptionId
param observerResourceGroup string = 'oprail-observer-${network}'
param loadBalancerName string
param publicLoadBalancerName string
param referenceRpc string
param chainId string
param managementSubnetId string
param registryServer string
param registryPullIdentityId string
@description('Fully qualified management image, preferably pinned by digest.')
param image string
param githubRepository string = 'SkunkWyrx/telcoin-network-cli'

var tags = { purpose: 'oprail-observer-management', network: network }

resource environment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: '${appName}-env'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: managementSubnetId
      internal: false
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

resource app 'Microsoft.App/containerApps@2025-07-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${registryPullIdentityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        allowInsecure: false
      }
      registries: [{ server: registryServer, identity: registryPullIdentityId }]
      secrets: concat(
        [{ name: 'entra-client-secret', value: clientSecret }],
        empty(githubDispatchToken) ? [] : [{ name: 'github-dispatch-token', value: githubDispatchToken }]
      )
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 1 }
      containers: [
        {
          name: 'management'
          image: image
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          probes: [
            { type: 'Readiness', httpGet: { path: '/healthz', port: 8080, scheme: 'HTTP' } }
          ]
          env: concat([
            { name: 'Management__PublicHost', value: '${appName}.${environment.properties.defaultDomain}' }
            { name: 'Entra__TenantId', value: tenantId }
            { name: 'Entra__ClientId', value: clientId }
            { name: 'Entra__AllowedObjectId', value: allowedObjectId }
            { name: 'Entra__ClientSecret', secretRef: 'entra-client-secret' }
            { name: 'Fleet__Network', value: network }
            { name: 'Fleet__Subscription', value: subscriptionId }
            { name: 'Fleet__ResourceGroup', value: observerResourceGroup }
            { name: 'Fleet__LoadBalancer', value: loadBalancerName }
            { name: 'Fleet__PublicLoadBalancer', value: publicLoadBalancerName }
            { name: 'Fleet__ReferenceRpc', value: referenceRpc }
            { name: 'Fleet__ChainId', value: chainId }
            { name: 'Operations__Repository', value: githubRepository }
            { name: 'Operations__Enabled', value: 'false' }
          ], empty(githubDispatchToken) ? [] : [
            { name: 'Operations__GitHubToken', secretRef: 'github-dispatch-token' }
          ])
        }
      ]
    }
  }
}
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
