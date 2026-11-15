// The five Container Apps jobs (spec §2, §5). All run as id-ops and connect as the
// Postgres admin; secrets are Key Vault references resolved through id-ops.
// Container Apps does not expand $(VAR) in args, so commands that need an env var use sh -c.

param location string
param environmentId string
param ledgerStorageName string
param jobImageDigest string
param idOpsId string
param idOpsClientId string
param keyVaultUri string
param backupAccountName string
param importCommand array

var backupAccountUrl = 'https://${backupAccountName}.blob.${environment().suffixes.storage}'

var identity = {
  type: 'UserAssigned'
  userAssignedIdentities: {
    '${idOpsId}': {}
  }
}

var secrets = [
  {
    name: 'database-url'
    keyVaultUrl: '${keyVaultUri}secrets/database-url-admin'
    identity: idOpsId
  }
  {
    name: 'ghcr-pull-token'
    keyVaultUrl: '${keyVaultUri}secrets/ghcr-pull-token'
    identity: idOpsId
  }
]

var registries = [
  {
    server: 'ghcr.io'
    username: 'deimos2002'
    passwordSecretRef: 'ghcr-pull-token'
  }
]

var jobEnv = [
  { name: 'DATABASE_URL', secretRef: 'database-url' }
  { name: 'BACKUP_ACCOUNT_URL', value: backupAccountUrl }
  // DefaultAzureCredential picks the user-assigned identity by client id.
  { name: 'AZURE_CLIENT_ID', value: idOpsClientId }
]

var restoreEnv = [
  { name: 'DATABASE_URL', secretRef: 'database-url' }
  { name: 'BACKUP_ACCOUNT_URL', value: backupAccountUrl }
  { name: 'AZURE_CLIENT_ID', value: idOpsClientId }
  { name: 'DELETION_LEDGER_PATH', value: '/mnt/ledger/deletion_ledger.jsonl' }
]

var resources = {
  cpu: json('0.5')
  memory: '1Gi'
}

var manual = {
  parallelism: 1
  replicaCompletionCount: 1
}

resource migrate 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-migrate'
  location: location
  identity: identity
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      manualTriggerConfig: manual
      replicaTimeout: 1800
      replicaRetryLimit: 0
      secrets: secrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'main'
          image: jobImageDigest
          command: [
            'alembic'
            'upgrade'
            'head'
          ]
          env: jobEnv
          resources: resources
        }
      ]
    }
  }
}

resource importJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-import'
  location: location
  identity: identity
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      manualTriggerConfig: manual
      replicaTimeout: 7200
      replicaRetryLimit: 0
      secrets: secrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'main'
          image: jobImageDigest
          // Arguments (e.g. the Ciqual xlsx path) are supplied when the execution is started.
          command: importCommand
          env: jobEnv
          resources: resources
        }
      ]
    }
  }
}

resource backup 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-backup'
  location: location
  identity: identity
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        // 00:30 UTC = 01:30 Paris in winter, 02:30 in summer.
        cronExpression: '30 0 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 3600
      replicaRetryLimit: 1
      secrets: secrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'main'
          image: jobImageDigest
          command: [
            'sh'
            '-c'
            'python scripts/backup.py --blob "$BACKUP_ACCOUNT_URL"'
          ]
          env: jobEnv
          resources: resources
        }
      ]
    }
  }
}

resource restore 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-restore'
  location: location
  identity: identity
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      manualTriggerConfig: manual
      replicaTimeout: 7200
      replicaRetryLimit: 0
      secrets: secrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'main'
          image: jobImageDigest
          // Arguments are supplied when the execution is started (runbook §7.3).
          command: [
            'python'
            'scripts/restore.py'
          ]
          env: restoreEnv
          resources: resources
          volumeMounts: [
            {
              volumeName: 'ledger'
              mountPath: '/mnt/ledger'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'ledger'
          storageType: 'AzureFile'
          storageName: ledgerStorageName
        }
      ]
    }
  }
}

resource retentionCheck 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-retention-check'
  location: location
  identity: identity
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '0 6 * * 1'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 600
      replicaRetryLimit: 0
      secrets: secrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'main'
          image: jobImageDigest
          command: [
            'sh'
            '-c'
            'python scripts/retention_check.py "$BACKUP_ACCOUNT_URL"'
          ]
          env: jobEnv
          resources: resources
        }
      ]
    }
  }
}
