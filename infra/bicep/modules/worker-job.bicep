param name string
param location string
param environmentId string
param identityId string
param image string
param registryServer string
param env array

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: name
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    workloadProfileName: 'Consumption'
    environmentId: environmentId
    configuration: {
      registries: [{ server: registryServer, identity: identityId }]
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [{
        name: 'worker'
        image: image
        command: ['python', '-m', 'worker.servicebus_worker']
        env: env
        resources: { cpu: json('0.5'), memory: '1Gi' }
      }]
    }
  }
}
