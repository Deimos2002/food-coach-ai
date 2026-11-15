// Food Coach — bootstrap template (spec 2026-10-14 §2, §5, §6.1, §6.2).
// Run ONCE, by hand, by a subscription Owner:
//   az deployment group create -g rg-foodcoach-prod -f infra/bootstrap.bicep -p ...
// Creates everything that needs elevated rights: identities, Key Vault + secrets, storage,
// Log Analytics, the alert action group and every role assignment. main.bicep (CI,
// Contributor only) consumes the outputs. No secret value is ever output.

@description('Environment name, e.g. prod.')
param env string

@allowed([
  'italynorth'
  'polandcentral'
  'austriaeast'
  'denmarkeast'
  'belgiumcentral'
])
param location string = 'italynorth'

@description('Object ID of the "Food Coach Deploy" service principal (GitHub OIDC).')
param deployPrincipalId string

@description('Full resource ID of the existing Azure OpenAI account.')
param aoaiAccountId string

@description('Email address that receives alerts.')
param alertEmail string

@secure()
param dbAdminPassword string

@secure()
param dbAppPassword string

@secure()
param aoaiKey string

@secure()
param ghcrPullToken string

// ---------------------------------------------------------------- names

var suffix = uniqueString(resourceGroup().id)
var keyVaultName = 'kv-foodcoach-${env}'
var logWorkspaceName = 'log-foodcoach-${env}'
var backupAccountName = take('stbackup${suffix}', 24)
var ledgerAccountName = take('stledger${suffix}', 24)

var secretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
var blobContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)
var contributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b24988ac-6180-42a0-ab88-20f7382dd24c'
)

// ---------------------------------------------------------------- identities

resource idApi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-api'
  location: location
}

resource idOps 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-ops'
  location: location
}

// ---------------------------------------------------------------- Key Vault + secrets

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enabledForTemplateDeployment: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource secDbAdminPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'db-admin-password'
  properties: {
    value: dbAdminPassword
  }
}

resource secDbAppPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'db-app-password'
  properties: {
    value: dbAppPassword
  }
}

resource secAoaiKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'aoai-key'
  properties: {
    value: aoaiKey
  }
}

resource secGhcrPullToken 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'ghcr-pull-token'
  properties: {
    value: ghcrPullToken
  }
}

resource secDatabaseUrlApp 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'database-url-app'
  properties: {
    value: 'postgresql+psycopg://foodcoach_app:${uriComponent(dbAppPassword)}@psql-foodcoach-${env}.postgres.database.azure.com:5432/foodcoach?sslmode=require'
  }
}

resource secDatabaseUrlAdmin 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'database-url-admin'
  properties: {
    value: 'postgresql+psycopg://foodcoach_admin:${uriComponent(dbAdminPassword)}@psql-foodcoach-${env}.postgres.database.azure.com:5432/foodcoach?sslmode=require'
  }
}

// Key Vault Secrets User, one assignment per (identity, secret). No vault-scope grants.
// db-app-password has no reader: only the user reads it, in the runbook.

resource apiDatabaseUrl 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secDatabaseUrlApp.id, idApi.id, secretsUserRoleId)
  scope: secDatabaseUrlApp
  properties: {
    roleDefinitionId: secretsUserRoleId
    principalId: idApi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apiAoaiKey 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secAoaiKey.id, idApi.id, secretsUserRoleId)
  scope: secAoaiKey
  properties: {
    roleDefinitionId: secretsUserRoleId
    principalId: idApi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apiGhcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secGhcrPullToken.id, idApi.id, secretsUserRoleId)
  scope: secGhcrPullToken
  properties: {
    roleDefinitionId: secretsUserRoleId
    principalId: idApi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource opsDatabaseUrl 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secDatabaseUrlAdmin.id, idOps.id, secretsUserRoleId)
  scope: secDatabaseUrlAdmin
  properties: {
    roleDefinitionId: secretsUserRoleId
    principalId: idOps.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource opsDbAdminPassword 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secDbAdminPassword.id, idOps.id, secretsUserRoleId)
  scope: secDbAdminPassword
  properties: {
    roleDefinitionId: secretsUserRoleId
    principalId: idOps.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource opsGhcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secGhcrPullToken.id, idOps.id, secretsUserRoleId)
  scope: secGhcrPullToken
  properties: {
    roleDefinitionId: secretsUserRoleId
    principalId: idOps.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- backup storage

resource backupAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: backupAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Cool'
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource backupBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: backupAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    isVersioningEnabled: false
  }
}

resource backupContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: backupBlobService
  name: 'backups'
  properties: {
    publicAccess: 'None'
  }
}

resource backupLifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: backupAccount
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-backups-after-14-days'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                'backups/'
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterCreationGreaterThan: 14
                }
              }
            }
          }
        }
      ]
    }
  }
}

resource opsBackupWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(backupContainer.id, idOps.id, blobContributorRoleId)
  scope: backupContainer
  properties: {
    roleDefinitionId: blobContributorRoleId
    principalId: idOps.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- ledger storage
// Separate account: its key (held by the Container Apps environment) grants nothing else.

resource ledgerAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: ledgerAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource ledgerFileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: ledgerAccount
  name: 'default'
}

resource ledgerShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: ledgerFileService
  name: 'ledger'
  properties: {
    shareQuota: 1
  }
}

// ---------------------------------------------------------------- monitoring

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logWorkspaceName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-foodcoach-${env}'
  location: 'global'
  properties: {
    groupShortName: 'foodcoach'
    enabled: true
    emailReceivers: [
      {
        name: 'owner'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// ---------------------------------------------------------------- deploy principal

resource deployContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployPrincipalId, contributorRoleId)
  properties: {
    roleDefinitionId: contributorRoleId
    principalId: deployPrincipalId
    principalType: 'ServicePrincipal'
  }
}

module aoaiReader 'modules/bootstrap-aoai-reader.bicep' = {
  name: 'bootstrap-aoai-reader'
  scope: resourceGroup(split(aoaiAccountId, '/')[2], split(aoaiAccountId, '/')[4])
  params: {
    aoaiAccountName: last(split(aoaiAccountId, '/'))
    deployPrincipalId: deployPrincipalId
  }
}

// ---------------------------------------------------------------- outputs (no secrets)

output idApiId string = idApi.id
output idApiClientId string = idApi.properties.clientId
output idOpsId string = idOps.id
output idOpsClientId string = idOps.properties.clientId
output keyVaultName string = kv.name
output keyVaultUri string = kv.properties.vaultUri
output backupAccountName string = backupAccount.name
output ledgerAccountName string = ledgerAccount.name
output ledgerShareName string = ledgerShare.name
output logWorkspaceId string = logWorkspace.id
output actionGroupId string = actionGroup.id
