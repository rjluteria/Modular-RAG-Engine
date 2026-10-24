targetScope = 'resourceGroup'
param prefix string
param searchLocation string = 'northcentralus'
var ownership = { project: 'modular-rag-engine', deployment: prefix }
resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: '${prefix}-search'
  location: searchLocation
  tags: ownership
  sku: { name: 'free' }
  properties: { replicaCount: 1, partitionCount: 1, hostingMode: 'default', disableLocalAuth: true, semanticSearch: 'disabled' }
}
output searchId string = search.id
output searchEndpoint string = 'https://${search.name}.search.windows.net'
