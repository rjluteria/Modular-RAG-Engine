targetScope = 'resourceGroup'

@description('Unique deployment prefix, 3-16 lowercase letters/digits')
@minLength(3)
@maxLength(16)
param prefix string
param location string = resourceGroup().location
param foundryResourceGroupName string
param foundryAccountName string
param foundryOpenAIEndpoint string
param generationDeploymentName string
param judgeDeploymentName string
param embeddingDeploymentName string
param existingSearchResourceGroupName string
param existingSearchName string
param containerImage string
param registryName string
param deployerPrincipalId string
@allowed(['User', 'ServicePrincipal'])
param deployerPrincipalType string = 'User'
param generationModel string
param generationVersion string
param judgeModel string
param judgeVersion string
param embeddingModel string
param embeddingVersion string
@secure()
param adminToken string
@secure()
param queryToken string
param corsOrigins array = []
param approvedCollections array = []
param embeddingDimensions int = 1536
@minValue(1)
@maxValue(50)
param retrievalTopK int = 8
@minValue(0)
param providerMinIntervalSeconds int = 65

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-app'
  location: location
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${take(prefix, 11)}${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {}
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for name in ['state', 'originals']: {
  parent: blobs
  name: name
  properties: { publicAccess: 'None' }
}]
resource search 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: existingSearchName
  scope: resourceGroup(existingSearchResourceGroupName)
}
resource bus 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: '${prefix}-${uniqueString(resourceGroup().id)}-bus'
  location: location
  sku: { name: 'Basic', tier: 'Basic' }
  properties: { disableLocalAuth: true, minimumTlsVersion: '1.2' }
}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: bus
  name: 'rag-jobs'
  properties: {
    lockDuration: 'PT5M'
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'P1D'
  }
}
resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, 'blob')
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
module searchAccess 'modules/runtime-search-access.bicep' = {
  name: 'runtime-search-access'
  scope: resourceGroup(existingSearchResourceGroupName)
  params: { searchName: existingSearchName, principalId: identity.properties.principalId }
}
resource evaluationBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, deployerPrincipalId, 'evaluation-state')
  scope: storage
  properties: {
    principalId: deployerPrincipalId
    principalType: deployerPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource senderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(bus.id, identity.id, 'sender')
  scope: bus
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
  }
}
resource receiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(bus.id, identity.id, 'receiver')
  scope: bus
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
  }
}
module modelAccess 'modules/runtime-model-access.bicep' = {
  name: 'runtime-model-access'
  scope: resourceGroup(foundryResourceGroupName)
  params: { accountName: foundryAccountName, principalId: identity.properties.principalId }
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource registryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'pull')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-environment'
  location: location
  properties: {
    appLogsConfiguration: {}
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}
var env = [
  { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
  { name: 'PARSER_PROVIDER', value: 'structured' }
  { name: 'VECTOR_STORE_PROVIDER', value: 'azure_search' }
  { name: 'LLM_PROVIDER', value: 'azure_openai' }
  { name: 'EMBEDDER_PROVIDER', value: 'azure_openai' }
  { name: 'QUEUE_PROVIDER', value: 'service_bus' }
  { name: 'SHARED_STATE_PROVIDER', value: 'azure_blob' }
  { name: 'ORIGINALS_PROVIDER', value: 'azure_blob' }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
  { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
  { name: 'AZURE_SERVICE_BUS_NAMESPACE', value: '${bus.name}.servicebus.windows.net' }
  { name: 'AZURE_SERVICE_BUS_QUEUE', value: queue.name }
  { name: 'AZURE_OPENAI_ENDPOINT', value: foundryOpenAIEndpoint }
  { name: 'AZURE_OPENAI_CHAT_DEPLOYMENT', value: generationDeploymentName }
  { name: 'AZURE_OPENAI_JUDGE_DEPLOYMENT', value: judgeDeploymentName }
  { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: embeddingDeploymentName }
  { name: 'AZURE_GENERATION_MODEL_IDENTITY', value: '${generationModel}:${generationVersion}' }
  { name: 'AZURE_JUDGE_MODEL_IDENTITY', value: '${judgeModel}:${judgeVersion}' }
  { name: 'AZURE_EMBEDDING_MODEL_IDENTITY', value: '${embeddingModel}:${embeddingVersion}' }
  { name: 'AZURE_EMBEDDING_DIMENSIONS', value: string(embeddingDimensions) }
  { name: 'API_USER_USE_CASES', value: string(approvedCollections) }
  { name: 'CORS_ORIGINS', value: string(corsOrigins) }
  { name: 'UPLOAD_DIR', value: '/tmp/uploads' }
  { name: 'RETRIEVAL_TOP_K', value: string(retrievalTopK) }
  { name: 'PROVIDER_MIN_INTERVAL_SECONDS', value: string(providerMinIntervalSeconds) }
]
module api 'modules/api.bicep' = {
  name: 'api'
  dependsOn: [registryRole, blobRole, searchAccess, modelAccess, senderRole, receiverRole]
  params: {
    name: '${prefix}-api'
    location: location
    environmentId: environment.id
    identityId: identity.id
    image: containerImage
    registryServer: registry.properties.loginServer
    env: env
    adminToken: adminToken
    queryToken: queryToken
  }
}
module worker 'modules/worker-job.bicep' = {
  name: 'worker'
  dependsOn: [registryRole, blobRole, searchAccess, modelAccess, senderRole, receiverRole]
  params: {
    name: '${prefix}-worker'
    location: location
    environmentId: environment.id
    identityId: identity.id
    image: containerImage
    registryServer: registry.properties.loginServer
    env: env
  }
}
output apiUrl string = api.outputs.url
output storageName string = storage.name
output searchName string = search.name
output identityClientId string = identity.properties.clientId

output modelsEndpoint string = foundryOpenAIEndpoint
output serviceBusNamespace string = '${bus.name}.servicebus.windows.net'
output registryServer string = registry.properties.loginServer
output workerName string = '${prefix}-worker'
