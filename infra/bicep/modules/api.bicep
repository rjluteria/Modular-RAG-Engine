param name string
param location string
param environmentId string
param identityId string
param image string
param registryServer string
param env array
@secure()
param adminToken string
@secure()
param queryToken string

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    workloadProfileName: 'Consumption'
    managedEnvironmentId: environmentId
    configuration: {
      registries: [{ server: registryServer, identity: identityId }]
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: 8000, allowInsecure: false, transport: 'http' }
      secrets: [
        { name: 'admin-token', value: adminToken }
        { name: 'query-token', value: queryToken }
      ]
    }
    template: {
      containers: [{
        name: 'api'
        image: image
        command: ['uvicorn', 'api.main:app', '--host', '0.0.0.0', '--port', '8000', '--no-access-log']
        env: concat(env, [
          { name: 'API_ADMIN_TOKEN', secretRef: 'admin-token' }
          { name: 'API_USER_TOKEN', secretRef: 'query-token' }
        ])
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [{
          type: 'Readiness'
          httpGet: { path: '/api/v1/health', port: 8000 }
          periodSeconds: 10
        }]
      }]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
