// Container Apps environment: workload profiles (Consumption only), VNet-integrated,
// external VIP, Log Analytics attached, ledger Azure Files share registered (spec §2, §5.2).

param env string
param location string
param acaSubnetId string
param logWorkspaceId string
param ledgerAccountName string
param ledgerShareName string

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logWorkspaceId, '/'))
}

resource ledgerAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: ledgerAccountName
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-foodcoach-${env}'
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    vnetConfiguration: {
      infrastructureSubnetId: acaSubnetId
      internal: false
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logWorkspaceId, '2023-09-01').customerId
        sharedKey: logWorkspace.listKeys().primarySharedKey
      }
    }
  }
}

// The account key is read here and stays inside Azure.
resource ledgerStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: cae
  name: 'ledger'
  properties: {
    azureFile: {
      accountName: ledgerAccountName
      accountKey: ledgerAccount.listKeys().keys[0].value
      shareName: ledgerShareName
      accessMode: 'ReadWrite'
    }
  }
}

output environmentId string = cae.id
output ledgerStorageName string = ledgerStorage.name
