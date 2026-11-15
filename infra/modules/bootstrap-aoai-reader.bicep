// Reader on the existing Azure OpenAI account (another resource group) for the deploy principal.
// Deployed by bootstrap.bicep with scope = the account's resource group.

@description('Name of the existing Azure OpenAI account in this resource group.')
param aoaiAccountName string

@description('Object ID of the GitHub OIDC deploy service principal.')
param deployPrincipalId string

var readerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'acdd72a7-3385-48ef-bd42-f606fba81ae7'
)

resource aoai 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: aoaiAccountName
}

resource deployReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aoai.id, deployPrincipalId, readerRoleId)
  scope: aoai
  properties: {
    roleDefinitionId: readerRoleId
    principalId: deployPrincipalId
    principalType: 'ServicePrincipal'
  }
}
