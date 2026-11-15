// The API container app (spec §2, §3, §6). Every secret is a Key Vault reference
// resolved through id-api; no secret value passes through this template.

param location string
param environmentId string
param ledgerStorageName string
param apiImageDigest string
param idApiId string
param keyVaultUri string
param allowedTid string
param allowedOid string
param entraIssuer string
param entraAudience string
param jwksUrl string
param aoaiEndpoint string
param coachPriceInPer1k string
param coachPriceOutPer1k string
param coachLlmEnabled bool

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-foodcoach-api'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${idApiId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: 'ghcr.io'
          username: 'deimos2002'
          passwordSecretRef: 'ghcr-pull-token'
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: '${keyVaultUri}secrets/database-url-app'
          identity: idApiId
        }
        {
          name: 'aoai-key'
          keyVaultUrl: '${keyVaultUri}secrets/aoai-key'
          identity: idApiId
        }
        {
          name: 'ghcr-pull-token'
          keyVaultUrl: '${keyVaultUri}secrets/ghcr-pull-token'
          identity: idApiId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImageDigest
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'API_PREFIX', value: '/api' }
            { name: 'STATIC_DIR', value: '/app/static' }
            { name: 'REQUIRE_LEDGER_VOLUME', value: 'true' }
            { name: 'DELETION_LEDGER_PATH', value: '/mnt/ledger/deletion_ledger.jsonl' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'ENTRA_ISSUER', value: entraIssuer }
            { name: 'JWKS_URL', value: jwksUrl }
            { name: 'ENTRA_AUDIENCE', value: entraAudience }
            { name: 'ALLOWED_TID', value: allowedTid }
            { name: 'ALLOWED_OID', value: allowedOid }
            { name: 'AZURE_OPENAI_ENDPOINT', value: aoaiEndpoint }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: 'coach-mini-eu' }
            { name: 'AZURE_OPENAI_DEPLOYMENT_TYPE', value: 'datazone' }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'aoai-key' }
            { name: 'COACH_ALLOW_GLOBAL', value: 'false' }
            { name: 'COACH_PRICE_IN_PER_1K', value: coachPriceInPer1k }
            { name: 'COACH_PRICE_OUT_PER_1K', value: coachPriceOutPer1k }
            { name: 'COACH_LLM_ENABLED', value: string(coachLlmEnabled) }
          ]
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 30
            }
          ]
          volumeMounts: [
            {
              volumeName: 'ledger'
              mountPath: '/mnt/ledger'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
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

output id string = api.id
output fqdn string = api.properties.configuration.ingress.fqdn
