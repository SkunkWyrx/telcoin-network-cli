resource runner 'Microsoft.Compute/virtualMachines@2024-07-01' existing = {
  name: 'adiri-observer-runner'
}

resource recipients 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: 'adiri-rpc-alerts'
}

var signals = [
  {
    name: 'adiri-rpc-lag'
    metric: 'LagIncident'
    description: 'Public Adiri RPC is at least five blocks behind for three consecutive samples.'
  }
  {
    name: 'adiri-rpc-outage'
    metric: 'OutageIncident'
    description: 'Public Adiri RPC failed three consecutive samples.'
  }
]

resource alerts 'Microsoft.Insights/metricAlerts@2018-03-01' = [for signal in signals: {
  name: signal.name
  location: 'global'
  properties: {
    description: signal.description
    enabled: true
    severity: 2
    scopes: [runner.id]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    autoMitigate: true
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [{
        name: signal.metric
        criterionType: 'StaticThresholdCriterion'
        metricNamespace: 'Oprail.AdiriRPC'
        metricName: signal.metric
        timeAggregation: 'Maximum'
        operator: 'GreaterThan'
        threshold: 0
        skipMetricValidation: true
      }]
    }
    actions: [{ actionGroupId: recipients.id }]
  }
}]
