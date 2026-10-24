param searchName string
param principalId string
resource search 'Microsoft.Search/searchServices@2023-11-01' existing = { name: searchName }
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, principalId, 'runtime-search-data')
  scope: search
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
  }
}
