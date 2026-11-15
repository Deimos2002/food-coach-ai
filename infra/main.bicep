// Food Coach — main template (spec 2026-10-14 §2, §5, §6, §8, §9).
// Deployed by CI (GitHub OIDC, Contributor on the resource group only): it contains NO role
// assignments. Identities, Key Vault, storage, Log Analytics and the action group come
// from bootstrap.bicep, whose outputs are passed in as parameters (prod.bicepparam).

@description('Environment name, e.g. prod.')
param env string = 'prod'

@allowed([
  'italynorth'
])
param location string = 'italynorth'

// ---------------------------------------------------------------- bootstrap outputs

param idApiId string
param idApiClientId string
param idOpsId string
param idOpsClientId string
param keyVaultName string
param keyVaultUri string
param backupAccountName string
param ledgerAccountName string
param ledgerShareName string
param logWorkspaceId string
param actionGroupId string

// ---------------------------------------------------------------- images (full digest references)

@description('e.g. ghcr.io/deimos2002/foodcoach@sha256:...')
param apiImageDigest string

@description('e.g. ghcr.io/deimos2002/foodcoach@sha256:...')
param jobImageDigest string

// ---------------------------------------------------------------- auth (A21)

@description('Entra tenant id. Required: an empty value would switch off the tid/scp checks.')
@minLength(36)
param allowedTid string

@description('Object id of the single allowed user.')
@minLength(36)
param allowedOid string

param entraAudience string = 'api://foodcoach-prod'
param entraIssuer string = '${environment().authentication.loginEndpoint}${allowedTid}/v2.0'
param jwksUrl string = '${environment().authentication.loginEndpoint}${allowedTid}/discovery/v2.0/keys'

// ---------------------------------------------------------------- coach (§8)

param aoaiEndpoint string
param coachPriceInPer1k string
param coachPriceOutPer1k string
param coachLlmEnabled bool = true

// ---------------------------------------------------------------- jobs, budget

@description('job-import command; arguments (e.g. the Ciqual xlsx path) are supplied at start time.')
param importCommand array = [
  'python'
  '-m'
  'app.importers.ciqual'
]

@description('Budget start, first of a month. Fixed: Azure rejects changes to a budget start date.')
param budgetStartDate string

// ---------------------------------------------------------------- modules

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    env: env
    location: location
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    env: env
    location: location
    vnetId: network.outputs.vnetId
    pgSubnetId: network.outputs.pgSubnetId
    adminPassword: kv.getSecret('db-admin-password')
  }
}

module cae 'modules/environment.bicep' = {
  name: 'environment'
  params: {
    env: env
    location: location
    acaSubnetId: network.outputs.acaSubnetId
    logWorkspaceId: logWorkspaceId
    ledgerAccountName: ledgerAccountName
    ledgerShareName: ledgerShareName
  }
}

module api 'modules/api.bicep' = {
  name: 'api'
  params: {
    location: location
    environmentId: cae.outputs.environmentId
    ledgerStorageName: cae.outputs.ledgerStorageName
    apiImageDigest: apiImageDigest
    idApiId: idApiId
    keyVaultUri: keyVaultUri
    allowedTid: allowedTid
    allowedOid: allowedOid
    entraIssuer: entraIssuer
    entraAudience: entraAudience
    jwksUrl: jwksUrl
    aoaiEndpoint: aoaiEndpoint
    coachPriceInPer1k: coachPriceInPer1k
    coachPriceOutPer1k: coachPriceOutPer1k
    coachLlmEnabled: coachLlmEnabled
  }
  dependsOn: [
    postgres
  ]
}

module jobs 'modules/jobs.bicep' = {
  name: 'jobs'
  params: {
    location: location
    environmentId: cae.outputs.environmentId
    ledgerStorageName: cae.outputs.ledgerStorageName
    jobImageDigest: jobImageDigest
    idOpsId: idOpsId
    idOpsClientId: idOpsClientId
    keyVaultUri: keyVaultUri
    backupAccountName: backupAccountName
    importCommand: importCommand
  }
  dependsOn: [
    postgres
  ]
}

module alerts 'modules/alerts.bicep' = {
  name: 'alerts'
  params: {
    location: location
    logWorkspaceId: logWorkspaceId
    actionGroupId: actionGroupId
    apiId: api.outputs.id
    postgresId: postgres.outputs.serverId
    budgetStartDate: budgetStartDate
  }
  dependsOn: [
    jobs
  ]
}

// ---------------------------------------------------------------- outputs (no secrets)

output apiFqdn string = api.outputs.fqdn
output postgresFqdn string = postgres.outputs.fqdn
output idApiClientId string = idApiClientId
