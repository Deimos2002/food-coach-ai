// Production parameters for main.bicep. No secrets here: every secret is read from Key Vault.
using 'main.bicep'

param env = 'prod'
param location = 'italynorth'

// ---------------------------------------------------------------- bootstrap outputs
// Deterministic names from bootstrap.bicep (env = prod, rg-foodcoach-prod).
var rgId = '/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-foodcoach-prod'

param idApiId = '${rgId}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-api'
param idOpsId = '${rgId}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-ops'
param keyVaultName = 'kv-foodcoach-prod'
param keyVaultUri = 'https://kv-foodcoach-prod.vault.azure.net/'
param ledgerShareName = 'ledger'
param logWorkspaceId = '${rgId}/providers/Microsoft.OperationalInsights/workspaces/log-foodcoach-prod'
param actionGroupId = '${rgId}/providers/Microsoft.Insights/actionGroups/ag-foodcoach-prod'

// Only known once bootstrap has run (client ids, uniqueString-derived storage names).
// deploy.yml exports them from the bootstrap deployment outputs; main.bicep rejects empty values.
param idApiClientId = readEnvironmentVariable('BOOTSTRAP_ID_API_CLIENT_ID', '')
param idOpsClientId = readEnvironmentVariable('BOOTSTRAP_ID_OPS_CLIENT_ID', '')
param backupAccountName = readEnvironmentVariable('BOOTSTRAP_BACKUP_ACCOUNT_NAME', '')
param ledgerAccountName = readEnvironmentVariable('BOOTSTRAP_LEDGER_ACCOUNT_NAME', '')

// ---------------------------------------------------------------- images (set by deploy.yml)

param apiImageDigest = readEnvironmentVariable('API_IMAGE_DIGEST', '')
param jobImageDigest = readEnvironmentVariable('JOB_IMAGE_DIGEST', '')

// ---------------------------------------------------------------- auth (identifiers, not secrets)

param allowedTid = '00000000-0000-0000-0000-000000000002'
param allowedOid = '00000000-0000-0000-0000-000000000003'
// v2 tokens carry the API app's client ID as aud; tenant policy forbids a custom URI.
param entraAudience = '00000000-0000-0000-0000-000000000004'

// ---------------------------------------------------------------- coach (§8)

param aoaiEndpoint = 'https://foodcoach-aoai-0000.openai.azure.com/'
// gpt-4.1-mini DataZoneStandard, EUR per 1k tokens (list price + data-zone uplift, rounded up).
param coachPriceInPer1k = '0.00044'
param coachPriceOutPer1k = '0.00176'
param coachLlmEnabled = true

// Budget start date is fixed: Azure rejects updates that change it.
param budgetStartDate = '2026-10-01'
