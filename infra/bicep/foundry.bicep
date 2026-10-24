targetScope = 'resourceGroup'
param prefix string
param searchId string
param searchEndpoint string
param accountName string
param projectName string
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}
resource generation 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: '${prefix}-generation'
  sku: { name: 'Standard', capacity: 20 }
  properties: { model: { format: 'OpenAI', name: 'gpt-4o', version: '2024-11-20' }, versionUpgradeOption: 'NoAutoUpgrade' }
}
resource judge 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  dependsOn: [generation]
  parent: account
  name: '${prefix}-judge'
  sku: { name: 'Standard', capacity: 20 }
  properties: { model: { format: 'OpenAI', name: 'gpt-4o', version: '2024-11-20' }, versionUpgradeOption: 'NoAutoUpgrade' }
}
resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  dependsOn: [judge]
  parent: account
  name: '${prefix}-embedding'
  sku: { name: 'Standard', capacity: 30 }
  properties: { model: { format: 'OpenAI', name: 'text-embedding-3-small', version: '1' }, versionUpgradeOption: 'NoAutoUpgrade' }
}
resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: '${prefix}-search'
  properties: {
    category: 'CognitiveSearch'
    target: searchEndpoint
    authType: 'AAD'
    isSharedToAll: false
    useWorkspaceManagedIdentity: false
    metadata: { ApiType: 'Azure', ResourceId: searchId, OwnerPrefix: prefix }
  }
}
output generationName string = generation.name
output judgeName string = judge.name
output embeddingName string = embedding.name
