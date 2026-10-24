param accountName string
param projectName string
param connectionName string
@minLength(1)
param existingStorageId string
@minLength(1)
param existingStorageEndpoint string
@minLength(3)
param existingStorageAccountName string
@minLength(1)
param existingContainerName string = 'originals'
param storageExistenceVerified bool = false
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = { name: accountName }
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = { parent: account, name: projectName }
resource connection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = if (storageExistenceVerified) {
  parent: project
  name: connectionName
  properties: {
    category: 'AzureBlob'
    target: existingStorageEndpoint
    authType: 'AAD'
    isSharedToAll: false
    useWorkspaceManagedIdentity: false
    metadata: {
      ApiType: 'Azure'
      ResourceId: existingStorageId
      AccountName: existingStorageAccountName
      ContainerName: existingContainerName
    }
  }
}
