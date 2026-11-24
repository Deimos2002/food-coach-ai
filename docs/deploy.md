# Deployment and operations

This runbook follows the checked-in Azure deployment code and design §7. Commands
are for a human operator in **Bash on Linux/WSL**, from the repository root, with
Azure CLI, GitHub CLI, Python 3.12+, `jq`, OpenSSL and Git installed. Activate the
backend environment locally for the two Python verification scripts. The jobs run
inside the private VNet; a laptop cannot connect directly to production Postgres.

Commands below create or change production resources. Documentation verification
used source inspection and `--help` only; no Azure deployment, restore, rotation,
paid model request or production data operation has been executed.

## Before bootstrap: unresolved deployment prerequisites

Resolve these with the infrastructure/workflow controller before first deployment:

- `infra/prod.bicepparam` contains fixed subscription, tenant and allowed-user IDs,
  endpoint and budget start date. Confirm they match this operator's subscription
  and tenant. Bootstrap outputs for fixed names must match it. Do not invent an
  alternate vault name in these commands: coordinating a name change needs an IaC
  update. Keep the existing budget start date on subsequent deployments.
- The API's issuer is v2.0 but its configured audience is `api://foodcoach-prod`.
  Microsoft documents v2.0 access-token `aud` as the API's **client ID**, not its
  App ID URI. **Task 12 go-live check:** confirm the issued token's audience and
  resolve API audience configuration before accepting live sign-in. Keep the scope URI separate from
  the audience; do not weaken audience validation. See the
  [access-token claims reference](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference#payload-claims).

The first workflow's deep-health smoke test also precedes enabling the newly
created `foodcoach_app` login. Expect it to fail at that point on a fresh server;
complete bootstrap step 9, then re-run the failed workflow job as described there
before calling deployment done.

## Operator session

Use an authenticated subscription Owner for bootstrap and role assignments, an
Entra administrator for registrations/consent, and the repository owner for GitHub.
Keep this shell open through bootstrap. Never enable shell tracing or Azure
`--debug` while secrets are present. Secrets are captured into variables, never
printed or committed. Run one step at a time and stop on any nonzero exit status;
do not paste an entire procedure and continue past a failed command. Temporary
files remain available after failures and shell exit. Keep `RUNBOOK_TMP`'s path
privately so a replacement shell can reuse it; do not recreate the directory or
overwrite snapshots during recovery. Cleanup is explicit after each completed
procedure, never automatic on EXIT.

```bash
set +e
set -uo pipefail
set +x
umask 077
RUNBOOK_TMP=$(mktemp -d "${TMPDIR:-/tmp}/foodcoach-runbook.XXXXXXXX")
RG=rg-foodcoach-prod
LOCATION=italynorth
AOAI_RG=rg-foodcoach
AOAI_ACCOUNT=foodcoach-aoai-0000
REPO='Deimos2002/<repo>'                 # replace <repo>
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
OPERATOR_OID=$(az ad signed-in-user show --query id -o tsv)
ALLOWED_OID=$(python3 - <<'PY'
import pathlib, re
text = pathlib.Path("infra/prod.bicepparam").read_text()
match = re.search(r"(?m)^param allowedOid = '([^']+)'$", text)
if not match:
    raise SystemExit("Cannot read allowedOid from infra/prod.bicepparam")
print(match.group(1))
PY
)
AOAI_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$AOAI_RG/providers/Microsoft.CognitiveServices/accounts/$AOAI_ACCOUNT"
read -r -p 'Alert email: ' ALERT_EMAIL
```

`OPERATOR_OID` is the signed-in operator for the Key Vault RBAC grant.
`ALLOWED_OID` is the application user read from `infra/prod.bicepparam`, used by
restore's `--oid`; these users can differ.

Define explicit cleanup once. Call it only at the procedure's completion point,
after success or fully verified recovery, when none of its files are needed:

```bash
cleanup_runbook() {
  case "${RUNBOOK_TMP:-}" in
    "${TMPDIR:-/tmp}"/foodcoach-runbook.*) rm -r -- "$RUNBOOK_TMP" || return ;;
    *) printf 'Refusing cleanup: unexpected RUNBOOK_TMP path\n' >&2; return 1 ;;
  esac
  unset RUNBOOK_TMP DB_ADMIN_PASSWORD DB_APP_PASSWORD AOAI_KEY GHCR_PULL_TOKEN \
    NEW_GHCR_TOKEN NEW_DB_APP_PASSWORD NEW_DB_ADMIN_PASSWORD LEDGER_KEY
}
```

On any failure, retain the directory (especially `active-revisions.txt`,
`old-app-url.txt`, `old-admin-url.txt` and `backup-configuration.json`). Do not run
cleanup while a job is still running or before rollback/recovery is accepted.
Start a new operator session for the next independent procedure after cleanup.

For a later operations session, run the session setup above, then reload existing
outputs with these **read-only** commands instead of re-running bootstrap:

```bash
az deployment group show -g "$RG" -n bootstrap --query properties.outputs -o json \
  > "$RUNBOOK_TMP/bootstrap-outputs.json"
KV_NAME=$(jq -r '.keyVaultName.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
LEDGER_ACCOUNT=$(jq -r '.ledgerAccountName.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
LEDGER_SHARE=$(jq -r '.ledgerShareName.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
BACKUP_ACCOUNT=$(jq -r '.backupAccountName.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
BACKUP_ACCOUNT_URL="https://$BACKUP_ACCOUNT.blob.core.windows.net"
API_FQDN=$(az containerapp show -g "$RG" -n ca-foodcoach-api \
  --query properties.configuration.ingress.fqdn -o tsv)
```

### Job helpers

Define these after the first deployment has created the jobs. A complete execution
template preserves the image, secret references, identity environment, resources
and (for restore) ledger mount. JSON is valid YAML for `--yaml`. Overrides affect
one execution, not the stored default command. See Microsoft's
[execution-template procedure](https://learn.microsoft.com/en-us/azure/container-apps/jobs#start-a-job-execution-on-demand).

```bash
job_template() {
  local job=$1 file=$2; shift 2
  local command
  command=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@") || return
  az containerapp job show -g "$RG" -n "$job" --query properties.template -o json |
    jq --argjson command "$command" \
      '.containers[0].command=$command | .containers[0].args=[]' > "$file"
}
run_job() {
  local job=$1 file=$2 execution status deadline
  execution=$(az containerapp job start -g "$RG" -n "$job" --yaml "$file" --query name -o tsv) || return
  test -n "$execution" || return 1
  deadline=$(( $(date +%s) + 7500 ))
  while (( $(date +%s) < deadline )); do
    status=$(az containerapp job execution show -g "$RG" -n "$job" \
      --job-execution-name "$execution" --query properties.status -o tsv) || return
    case "$status" in
      Succeeded) printf '%s %s Succeeded\n' "$job" "$execution"; return 0 ;;
      Running|Pending|Processing) sleep 10 ;;
      *) printf '%s %s failed: %s\n' "$job" "$execution" "$status" >&2; return 1 ;;
    esac
  done
  printf '%s %s timed out; inspect before retrying\n' "$job" "$execution" >&2
  return 1
}
pause_backups() {
  local job_id
  job_id=$(az containerapp job show -g "$RG" -n job-backup --query id -o tsv) || return
  az containerapp job show -g "$RG" -n job-backup --query properties.configuration -o json \
    > "$RUNBOOK_TMP/backup-configuration.pending" &&
    mv -- "$RUNBOOK_TMP/backup-configuration.pending" "$RUNBOOK_TMP/backup-configuration.json" || return
  jq '{properties:{configuration:(. | .triggerType="Manual" |
      .manualTriggerConfig={parallelism:1,replicaCompletionCount:1} |
      del(.scheduleTriggerConfig))}}' "$RUNBOOK_TMP/backup-configuration.json" \
    > "$RUNBOOK_TMP/backup-paused.json" || return
  az rest --method patch --url "$job_id?api-version=2024-03-01" \
    --body "@$RUNBOOK_TMP/backup-paused.json" --output none
}
resume_backups() {
  local job_id
  job_id=$(az containerapp job show -g "$RG" -n job-backup --query id -o tsv) || return
  jq '{properties:{configuration:.}}' "$RUNBOOK_TMP/backup-configuration.json" \
    > "$RUNBOOK_TMP/backup-resumed.json" || return
  az rest --method patch --url "$job_id?api-version=2024-03-01" \
    --body "@$RUNBOOK_TMP/backup-resumed.json" --output none
}
```

Do not nest `pause_backups` calls: preserve the original configuration until
`resume_backups` completes. Verify trigger type is `Manual` after pausing and
`Schedule` after resuming with `az containerapp job show -g "$RG" -n job-backup
--query properties.configuration.triggerType -o tsv`. Pausing does not stop a run
already in progress. Inspect `az containerapp job execution list -g "$RG" -n
job-backup -o table` and wait for running/pending/processing executions to finish.
Do the same for `job-import`, `job-migrate` and `job-restore` before maintenance.
Freeze GitHub deployments during maintenance; another deploy restores the schedule.
The installed CLI has no `job update --trigger-type` option, hence the REST patch.

## Bootstrap, in order

### 1. Resource group

```bash
az group create --name "$RG" --location "$LOCATION" --output none
```

### 2. Entra applications, delegated scope and OIDC

Create single-tenant registrations. The checked-in API expects audience
`api://foodcoach-prod` and exact delegated scope `Food.ReadWrite`; resolve the v2
audience mismatch above before enabling production sign-in. The browser
uses the SPA authorization-code/PKCE flow; it has no client secret.

```bash
API_APP_ID=$(az ad app create --display-name 'Food Coach API' \
  --sign-in-audience AzureADMyOrg --identifier-uris api://foodcoach-prod --query appId -o tsv)
API_OBJECT_ID=$(az ad app show --id "$API_APP_ID" --query id -o tsv)
az ad sp create --id "$API_APP_ID" --output none
SCOPE_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
jq -n --arg id "$SCOPE_ID" '{api:{requestedAccessTokenVersion:2,oauth2PermissionScopes:[{
  id:$id,value:"Food.ReadWrite",type:"Admin",isEnabled:true,
  adminConsentDisplayName:"Read and write Food Coach data",
  adminConsentDescription:"Read and write Food Coach data for the signed-in user"
}]}}' > "$RUNBOOK_TMP/api-registration.json"
az rest --method patch --url "https://graph.microsoft.com/v1.0/applications/$API_OBJECT_ID" \
  --body "@$RUNBOOK_TMP/api-registration.json" --output none
WEB_APP_ID=$(az ad app create --display-name 'Food Coach Web' \
  --sign-in-audience AzureADMyOrg --query appId -o tsv)
az ad sp create --id "$WEB_APP_ID" --output none
az ad app permission add --id "$WEB_APP_ID" --api "$API_APP_ID" \
  --api-permissions "$SCOPE_ID=Scope" --output none
az ad app permission admin-consent --id "$WEB_APP_ID"
DEPLOY_APP_ID=$(az ad app create --display-name 'Food Coach Deploy' \
  --sign-in-audience AzureADMyOrg --query appId -o tsv)
az ad sp create --id "$DEPLOY_APP_ID" --output none
DEPLOY_PRINCIPAL_ID=$(az ad sp show --id "$DEPLOY_APP_ID" --query id -o tsv)
jq -n --arg subject "repo:$REPO:environment:prod" '{name:"github-prod",
  issuer:"https://token.actions.githubusercontent.com",subject:$subject,
  audiences:["api://AzureADTokenExchange"]}' > "$RUNBOOK_TMP/federated.json"
az ad app federated-credential create --id "$DEPLOY_APP_ID" \
  --parameters "$RUNBOOK_TMP/federated.json" --output none
```

Use the **service principal object ID**, not the application/client ID, for
`deployPrincipalId`. Register the final SPA redirect URI in step 8, when the FQDN
exists. Conditional Access and tenant policy may require additional administrator
consent. If the tenant rejects the App ID URI, stop and coordinate the audience
and scope configuration rather than substituting an inconsistent URI.

### 3. Generate database passwords locally

```bash
DB_ADMIN_PASSWORD=$(openssl rand -base64 32)
DB_APP_PASSWORD=$(openssl rand -base64 32)
read -r -s -p 'Existing Azure OpenAI key: ' AOAI_KEY; printf '\n'
read -r -s -p 'GHCR classic PAT (read:packages, 90-day expiry): ' GHCR_PULL_TOKEN; printf '\n'
export DB_ADMIN_PASSWORD DB_APP_PASSWORD AOAI_KEY GHCR_PULL_TOKEN
```

The PAT belongs to `Deimos2002`, matching the registry username in both app and job
templates, and must read this private package. Store the credentials in your
password manager; these commands do not print them.

### 4. Bootstrap Bicep and its four secure parameters

**Key Vault names are globally unique.** The current name is `kv-foodcoach-prod`.
It has 90-day soft deletion and **purge protection enabled**. Deleting it does not
free its name; purge protection cannot be turned off. Resolve an unavailable name
with the controller before deploying, not by deleting/recreating the vault.

```bash
python3 - <<'PY' > "$RUNBOOK_TMP/bootstrap-secrets.json"
import json, os
names = {"dbAdminPassword":"DB_ADMIN_PASSWORD", "dbAppPassword":"DB_APP_PASSWORD",
         "aoaiKey":"AOAI_KEY", "ghcrPullToken":"GHCR_PULL_TOKEN"}
print(json.dumps({"$schema":"https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                  "contentVersion":"1.0.0.0",
                  "parameters":{k:{"value":os.environ[v]} for k,v in names.items()}}))
PY
az deployment group create --name bootstrap --resource-group "$RG" \
  --template-file infra/bootstrap.bicep --parameters "@$RUNBOOK_TMP/bootstrap-secrets.json" \
  --parameters env=prod location="$LOCATION" deployPrincipalId="$DEPLOY_PRINCIPAL_ID" \
    aoaiAccountId="$AOAI_ID" alertEmail="$ALERT_EMAIL" --output none
az deployment group show -g "$RG" -n bootstrap --query properties.outputs -o json \
  > "$RUNBOOK_TMP/bootstrap-outputs.json"
KV_NAME=$(jq -r '.keyVaultName.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
KV_URI=$(jq -r '.keyVaultUri.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
LEDGER_ACCOUNT=$(jq -r '.ledgerAccountName.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
LEDGER_SHARE=$(jq -r '.ledgerShareName.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
BACKUP_ACCOUNT=$(jq -r '.backupAccountName.value' "$RUNBOOK_TMP/bootstrap-outputs.json")
BACKUP_ACCOUNT_URL="https://$BACKUP_ACCOUNT.blob.core.windows.net"
test "$LEDGER_SHARE" = ledger
```

Bootstrap writes `db-admin-password`, `db-app-password`, `aoai-key`,
`ghcr-pull-token` and the derived, URL-encoded `database-url-admin` and
`database-url-app`. It outputs names/IDs only, never keys. It grants `id-api`
per-secret access to the app URL, OpenAI key and GHCR token; `id-ops` reads the admin
URL, admin password and GHCR token, plus Blob Data Contributor on `backups`.
It does not grant the operator vault data access. If needed, an Owner grants the
operator Key Vault Secrets Officer on this vault for the later rotation steps:

```bash
KV_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG/providers/Microsoft.KeyVault/vaults/$KV_NAME"
az role assignment create --assignee-object-id "$OPERATOR_OID" --assignee-principal-type User \
  --role 'Key Vault Secrets Officer' --scope "$KV_ID" --output none
```

### 5. Upload the ledger marker

Take the account/share names from bootstrap outputs. The separate ledger account
supports key authentication; the backup account explicitly disables shared keys.
Bootstrap intentionally outputs no authentication secret, so retrieve the ledger
account key as Owner and capture it without displaying it.

```bash
: > "$RUNBOOK_TMP/.ledger-volume"
LEDGER_KEY=$(az storage account keys list -g "$RG" -n "$LEDGER_ACCOUNT" --query '[0].value' -o tsv)
AZURE_STORAGE_KEY="$LEDGER_KEY" az storage file upload --account-name "$LEDGER_ACCOUNT" \
  --share-name ledger --path .ledger-volume --source "$RUNBOOK_TMP/.ledger-volume" --output none
unset LEDGER_KEY
```

The API and restore job mount share `ledger` at `/mnt/ledger`; deletion uses
`/mnt/ledger/deletion_ledger.jsonl`. A marker is not a ledger entry. A missing
ledger file makes restore fail; never bypass that with `--no-ledger` in production.
Before the first deletion, a deliberately empty ledger may be created using the
same upload command with `deletion_ledger.jsonl` as source/path, **only after
confirming no ledger already exists**. Never overwrite the current ledger.

### 6. Create and pin the EU model

Confirm Italy North has quota for the chosen capacity before execution. This is
deployment metadata configuration, not a paid inference call. The CLI flags are
`--sku-name DataZoneStandard --model-version 2025-04-14`; this installed CLI has no
version-upgrade flag, so immediately apply the complete pinned declaration by PUT.

```bash
az cognitiveservices account deployment create -g "$AOAI_RG" -n "$AOAI_ACCOUNT" \
  --deployment-name coach-mini-eu --model-format OpenAI --model-name gpt-4.1-mini \
  --model-version 2025-04-14 --sku-name DataZoneStandard --sku-capacity 1 --output none
az rest --method put --url "$AOAI_ID/deployments/coach-mini-eu?api-version=2024-10-01" \
  --body '{"sku":{"name":"DataZoneStandard","capacity":1},"properties":{"model":{"format":"OpenAI","name":"gpt-4.1-mini","version":"2025-04-14"},"versionUpgradeOption":"NoAutoUpgrade"}}' \
  --output none
az cognitiveservices account deployment show -g "$AOAI_RG" -n "$AOAI_ACCOUNT" \
  --deployment-name coach-mini-eu -o json | python backend/scripts/check_model_deployment.py
```

The gate must exit 0; it checks all four fields including `NoAutoUpgrade`. Production
uses only `coach-mini-eu`. The existing `coach-mini` GlobalStandard deployment stays
for synthetic eval/dev data. The workflow's model gate targets `rg-foodcoach`,
matching the existing account. The [deployment PUT schema](https://learn.microsoft.com/en-us/rest/api/aiservices/accountmanagement/deployments/create-or-update?view=rest-aiservices-accountmanagement-2024-10-01)
defines the version-upgrade property.

### 7. Private GitHub repository and variables

Run repository creation only if it does not already exist. Set a required reviewer
on GitHub environment **prod** before pushing: Settings → Environments → prod →
Required reviewers. Confirm the account's GitHub plan supports reviewers for a
private repo; do not treat the workflow's environment name alone as an approval.

```bash
gh repo create "$REPO" --private --source . --remote origin
gh variable set AZURE_TENANT_ID -R "$REPO" --body "$TENANT_ID"
gh variable set AZURE_SUBSCRIPTION_ID -R "$REPO" --body "$SUBSCRIPTION_ID"
gh variable set AZURE_CLIENT_ID -R "$REPO" --body "$DEPLOY_APP_ID"
gh variable set VITE_ENTRA_CLIENT_ID -R "$REPO" --body "$WEB_APP_ID"
gh variable set VITE_ENTRA_TENANT_ID -R "$REPO" --body "$TENANT_ID"
gh variable set VITE_API_SCOPE -R "$REPO" --body 'api://foodcoach-prod/Food.ReadWrite'
gh variable set BOOTSTRAP_ID_API_CLIENT_ID -R "$REPO" \
  --body "$(jq -r '.idApiClientId.value' "$RUNBOOK_TMP/bootstrap-outputs.json")"
gh variable set BOOTSTRAP_ID_OPS_CLIENT_ID -R "$REPO" \
  --body "$(jq -r '.idOpsClientId.value' "$RUNBOOK_TMP/bootstrap-outputs.json")"
gh variable set BOOTSTRAP_BACKUP_ACCOUNT_NAME -R "$REPO" --body "$BACKUP_ACCOUNT"
gh variable set BOOTSTRAP_LEDGER_ACCOUNT_NAME -R "$REPO" --body "$LEDGER_ACCOUNT"
git push origin main
```

All ten variables are identifiers/configuration, not secrets. **Set all three
`VITE_*` variables**: a missing client ID silently builds a web app without sign-in;
an empty tenant with a client ID now throws at browser startup; an empty scope uses
the default above. `VITE_API_URL=/api` is the image default. The workflow reads the
four `BOOTSTRAP_*` repository variables; it does not fetch bootstrap outputs itself.

### 8. First deployment and SPA redirect URI

The push runs CI. Successful **push-to-main** CI triggers Deploy via `workflow_run`;
approve the prod environment and inspect the run:

```bash
gh run list -R "$REPO" --workflow deploy.yml --limit 5
# Replace with the run ID shown above; on a fresh server first smoke will fail until step 9.
gh run watch '<run-id>' -R "$REPO" --exit-status
API_FQDN=$(az containerapp show -g "$RG" -n ca-foodcoach-api \
  --query properties.configuration.ingress.fqdn -o tsv)
WEB_OBJECT_ID=$(az ad app show --id "$WEB_APP_ID" --query id -o tsv)
jq -n --arg uri "https://$API_FQDN/" '{spa:{redirectUris:[$uri]}}' \
  > "$RUNBOOK_TMP/spa-registration.json"
az rest --method patch --url "https://graph.microsoft.com/v1.0/applications/$WEB_OBJECT_ID" \
  --body "@$RUNBOOK_TMP/spa-registration.json" --output none
```

Use the SPA platform and the trailing `/`, matching MSAL's origin-based redirect.
`main.bicep` also outputs `apiFqdn`, `postgresFqdn`, `idApiClientId`; the workflow
reads the app FQDN directly. Its manual dispatch requires a digest and is rollback
mode: it skips build **and migrations**. It is not a fresh normal deployment button.

### 9. Enable the app database role

Migrations **always** run through `database-url-admin`, as `foodcoach_admin`. Run
`ALTER ROLE foodcoach_app LOGIN PASSWORD ...` as that same admin login, which owns
the role and its default privileges. A downgrade/re-upgrade of migration
`0006_app_role` can drop/re-create the role as NOLOGIN; redo this step afterwards.
If another database still references it, downgrade keeps the existing cluster role.

The one-off `job-migrate` override below receives the app password through a
temporary job secret rather than a plaintext execution template. Its existing
`DATABASE_URL` remains the `database-url-admin` reference. If opening a new shell,
reload `DB_APP_PASSWORD` from Key Vault with `--query value -o tsv` into a variable.

The password is briefly visible on the process command line for `--secrets`; this
handoff is needed because `id-ops` has no read access to `db-app-password`
(`infra/bootstrap.bicep:143`).

```bash
az containerapp job secret set -g "$RG" -n job-migrate \
  --secrets "app-role-password=$DB_APP_PASSWORD" --output none
ROLE_SQL=$(cat <<'PY'
import os, sys
import sqlalchemy as sa
from psycopg import sql
try:
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        conn.exec_driver_sql(sql.SQL("ALTER ROLE foodcoach_app LOGIN PASSWORD {}").format(
            sql.Literal(os.environ["APP_ROLE_PASSWORD"])).as_string())
    engine.dispose()
except Exception:
    print("App role password update failed", file=sys.stderr)
    raise SystemExit(1)
print("App role login enabled")
PY
)
job_template job-migrate "$RUNBOOK_TMP/role-base.json" python -c "$ROLE_SQL"
jq '.containers[0].env += [{name:"APP_ROLE_PASSWORD",secretRef:"app-role-password"}]' \
  "$RUNBOOK_TMP/role-base.json" > "$RUNBOOK_TMP/role.json"
run_job job-migrate "$RUNBOOK_TMP/role.json"
role_status=$?
# Always attempt removal, including after a failed/timed-out job; no errexit/EXIT trap.
az containerapp job secret remove -g "$RG" -n job-migrate \
  --secret-names app-role-password --yes --output none
secret_cleanup_status=$?
if (( role_status != 0 || secret_cleanup_status != 0 )); then
  printf 'Stop: role update or temporary-secret removal failed; preserve recovery files and inspect the job.\n' >&2
else
  python backend/scripts/smoke.py "https://$API_FQDN"
fi
```

Expected: job status `Succeeded`, `App role login enabled` in its console output,
then `Smoke passed`. If removal fails, retry removal after resolving the error;
retain recovery files and do not proceed to import until both operations succeed.
The API readiness probe `/health` does not check the database; `/health?deep=1` must
return 200 before accepting real traffic. Smoke checks HTML and missing/malformed/
synthetic-expired credentials; complete the full A21 matrix during the release gate.

On a fresh server the first Deploy run's smoke **will fail** before this login is
enabled. After step 9 succeeds, re-run that failed workflow job, then approve prod
again if requested and wait for success:

```bash
gh run rerun '<first-deploy-run-id>' -R "$REPO" --failed
gh run watch '<first-deploy-run-id>' -R "$REPO" --exit-status
```

`deploy.yml` has one job, so `--failed` repeats all deploy-job steps, not just smoke.
For this initial smoke-only failure that is safe: checkout, image tag and `GIT_SHA`
use the original CI head SHA; rebuilding may produce a new digest, which the job
then deploys consistently. Bicep reapplies the same declared resources, and
`alembic upgrade head` is already at head. Migration 0006 creates NOLOGIN only when
the role does not exist; re-running upgrade does not undo step 9's LOGIN/password.
The workflow updates the API and repeats smoke without a downgrade or restore.
Use this recovery only after confirming smoke failed for the missing app login,
with no intervening infrastructure/secret changes or import/restore/rotation in
progress. Other failure causes need diagnosis before a rerun.

### 10. Import Ciqual, with backups paused

Stage 1 imports **Ciqual only**, with **no bulk OFF import**. Copy the current
official XLSX download URL from [ANSES Ciqual](https://ciqual.anses.fr/); the download
URL was not verified in this docs task. Validate the source/release before running.
The image has Python and the importer, but removes `curl` during its build, so the
override downloads with the Python standard library into `/tmp/ciqual_2025.xlsx`.

```bash
read -r -p 'Official Ciqual XLSX URL (https://ciqual.anses.fr/...xlsx): ' CIQUAL_URL
export CIQUAL_URL
python3 - <<'PY'
import os, urllib.parse
url = urllib.parse.urlparse(os.environ["CIQUAL_URL"])
assert url.scheme == "https" and url.hostname == "ciqual.anses.fr"
assert url.path.lower().endswith(".xlsx")
PY
pause_backups
# Inspect executions and wait for all conflicting jobs to finish before continuing.
az containerapp job execution list -g "$RG" -n job-backup -o table
IMPORT_CODE=$(cat <<'PY'
import os, subprocess, urllib.request
path = "/tmp/ciqual_2025.xlsx"
urllib.request.urlretrieve(os.environ["CIQUAL_URL"], path)
subprocess.run(["python", "-m", "app.importers.ciqual", path], check=True)
subprocess.run(["python", "-m", "app.importers.curated", "catalogue_data"], check=True)
PY
)
job_template job-import "$RUNBOOK_TMP/import-base.json" python -c "$IMPORT_CODE"
jq --arg url "$CIQUAL_URL" '.containers[0].env += [{name:"CIQUAL_URL",value:$url}]' \
  "$RUNBOOK_TMP/import-base.json" > "$RUNBOOK_TMP/import.json"
run_job job-import "$RUNBOOK_TMP/import.json"
resume_backups
```

Expected console output: `Ciqual import complete: <count> catalogue products.`
Confirm the release/product counts, then run `job-backup` once and the retention
check. If import fails, inspect the failure and explicitly resume the backup
schedule once no import is running; do not leave backups silently disabled.

After bootstrap, import verification and backup/retention checks all succeed and
the backup schedule is confirmed restored, explicitly clean up:

```bash
cleanup_runbook
```

## Deploy and rollback

Normal merges to `main` run CI, build/push `ghcr.io/<owner>/<repo>:sha-<SHA>`, gate
the model, deploy new **job** digest while keeping the current API digest, run
`job-migrate`, update the API, then smoke test. Images deploy by full digest.
Only expand/contract migrations compatible with the previous API revision belong
in this path. A failed migration leaves the old API image serving.

Checkout, image tag and the `GIT_SHA` build argument all use
`workflow_run.head_sha || github.sha`.

For rollback, choose a retained, known-good digest (keep the last ten in GHCR; the
workflow does not implement that retention policy). Do not revert the database:

```bash
ROLLBACK_IMAGE='ghcr.io/deimos2002/<repo>@sha256:<64-lowercase-hex-digest>'
gh workflow run deploy.yml -R "$REPO" --ref main --raw-field digest="$ROLLBACK_IMAGE"
gh run list -R "$REPO" --workflow deploy.yml --limit 5
gh run watch '<rollback-run-id>' -R "$REPO" --exit-status
```

Approve prod. Dispatch skips build and migrations, but still gates the model,
redeploys `main.bicep`, updates jobs/API and runs smoke. It does not revert IaC or
database settings. Verify sign-in and a real meal read/write before closing an
incident. Do not dispatch during restore or rotation.

After rollback verification succeeds (or recovery is complete), explicitly clean up:

```bash
cleanup_runbook
```

## Backup and retention operations

The actual backup cron is `30 0 * * *` (00:30 UTC: 01:30 Paris in winter, 02:30 in
summer), not 02:30 Paris year-round. `job-retention-check` runs `0 6 * * 1` (Monday
06:00 UTC). Blob lifecycle deletes after 14 days, soft deletion adds 7 days, no
versioning. Retention monitoring fails on any live or soft-deleted blob older than
25 days, no live backup, or newest live backup older than 48 hours.

```bash
job_template job-backup "$RUNBOOK_TMP/backup.json" sh -c \
  'python scripts/backup.py --blob "$BACKUP_ACCOUNT_URL" --container backups'
run_job job-backup "$RUNBOOK_TMP/backup.json"
job_template job-retention-check "$RUNBOOK_TMP/retention.json" sh -c \
  'python scripts/retention_check.py "$BACKUP_ACCOUNT_URL" --max-age-days 25 --max-fresh-hours 48'
run_job job-retention-check "$RUNBOOK_TMP/retention.json"
```

`backup.py` also accepts `--out`, `--db-url`, `--sha`; SHA precedence is explicit
`--sha`, `GIT_SHA`, `GITHUB_SHA`, `unknown`. Blob names are UTC
`YYYYMMDDTHHMMSSZ-<sha>.dump` and uploads refuse overwrite. A same-second/same-SHA
collision fails rather than replacing a backup. Capture the printed dump name in
the operations record. Retention success exits 0 without output; failure exits 1
and prints offending names and/or a missing/stale-backup reason. Managed identity
`id-ops` uses `AZURE_CLIENT_ID`; never authenticate the backup account with keys.

After recording the dump name and successful retention result, explicitly clean up.
Retain the files if either job failed until its recovery is complete:

```bash
cleanup_runbook
```

## Restore: maintenance window, no writes

Freeze deployments and imports. Keep the existing ledger intact, and choose a dump
whose schema is compatible with the image used for restoration. Record the image,
dump name, current database and ledger generation before starting. If the dump is
unusable, stop and arrange PITR to a new server; that alternative has not been
exercised here and requires checking private DNS/networking and app login setup.

### 1. Stop writes and scheduled backups

Setting only `minReplicas=0` does not stop HTTP-triggered scaling. Deactivate all
active API revisions, confirm no running replicas/connections remain, and pause
backups. [Revision deactivation stops replicas](https://learn.microsoft.com/en-us/azure/container-apps/revisions-manage#revision-deactivate).

```bash
az containerapp revision list -g "$RG" -n ca-foodcoach-api \
  --query '[?properties.active].name' -o tsv > "$RUNBOOK_TMP/active-revisions.pending" &&
  mv -- "$RUNBOOK_TMP/active-revisions.pending" "$RUNBOOK_TMP/active-revisions.txt"
while IFS= read -r revision; do
  az containerapp revision deactivate -g "$RG" -n ca-foodcoach-api --revision "$revision" --output none
done < "$RUNBOOK_TMP/active-revisions.txt"
pause_backups
az containerapp job execution list -g "$RG" -n job-backup -o table
az containerapp job execution list -g "$RG" -n job-import -o table
az containerapp job execution list -g "$RG" -n job-migrate -o table
az containerapp job execution list -g "$RG" -n job-restore -o table
```

Wait for all conflicting executions to finish. Preserve both existing database URL
secrets in private files for failure recovery; retrieve them only into private files:

```bash
az keyvault secret show --vault-name "$KV_NAME" -n database-url-app --query value -o tsv \
  > "$RUNBOOK_TMP/old-app-url.pending" &&
  mv -- "$RUNBOOK_TMP/old-app-url.pending" "$RUNBOOK_TMP/old-app-url.txt"
az keyvault secret show --vault-name "$KV_NAME" -n database-url-admin --query value -o tsv \
  > "$RUNBOOK_TMP/old-admin-url.pending" &&
  mv -- "$RUNBOOK_TMP/old-admin-url.pending" "$RUNBOOK_TMP/old-admin-url.txt"
RESTORE_DB="foodcoach_restore_$(date -u +%Y%m%d_%H%M%S)"
export RESTORE_DB
az postgres flexible-server db create -g "$RG" --server-name psql-foodcoach-prod \
  --name "$RESTORE_DB" --charset UTF8 --collation en_US.utf8 --output none
```

### 2. Restore the blob into the fresh database

The following override constructs the target URL from the existing admin secret
inside the private job. It never puts a database password into a command or local
execution template. Supply `--oid` explicitly: `job-restore` does not configure
`ALLOWED_OID`, and it is required when the restored database has no account row.
Use the `ALLOWED_OID` read from `infra/prod.bicepparam` in the operator setup,
not the operator's signed-in `OPERATOR_OID`.

```bash
read -r -p 'Exact dump blob name: ' DUMP_NAME
RESTORE_CODE=$(cat <<'PY'
import os, subprocess
from sqlalchemy.engine import make_url
url = make_url(os.environ["DATABASE_URL"]).set(database=os.environ["RESTORE_DB"])
result = subprocess.run(["python", "scripts/restore.py", "--blob", os.environ["DUMP_NAME"],
    "--account-url", os.environ["BACKUP_ACCOUNT_URL"], url.render_as_string(hide_password=False),
    "--ledger", "/mnt/ledger/deletion_ledger.jsonl", "--oid", os.environ["RESTORE_OID"]])
raise SystemExit(result.returncode)
PY
)
job_template job-restore "$RUNBOOK_TMP/restore-base.json" python -c "$RESTORE_CODE"
jq --arg db "$RESTORE_DB" --arg dump "$DUMP_NAME" --arg oid "$ALLOWED_OID" \
  '.containers[0].env += [{name:"RESTORE_DB",value:$db},{name:"DUMP_NAME",value:$dump},
    {name:"RESTORE_OID",value:$oid}]' "$RUNBOOK_TMP/restore-base.json" > "$RUNBOOK_TMP/restore.json"
run_job job-restore "$RUNBOOK_TMP/restore.json"
```

The actual CLI is `restore.py --blob NAME --account-url URL <target_db_url>
--ledger PATH [--oid OID]`. `--account-url` defaults to `BACKUP_ACCOUNT_URL` but
must be non-empty; local positional `dump` and `--blob` are mutually exclusive.
`--ledger` and `--no-ledger` are mutually exclusive; production always uses the
current ledger. pg_restore runs with `--clean --if-exists --no-owner --no-acl
--exit-on-error`; the script re-applies `foodcoach_app` and its grants itself.
**No manual grant step is needed.** Passwords are not in the dump: if restoring to
a fresh server, redo bootstrap step 9 there as the same migrating admin login.

### 3. Verify A22/A32 before switching

Inspect the completed job's console logs in the Azure portal (Container Apps Jobs →
job-restore → execution → Logs). Local `az containerapp job logs show --help` could
not load because the extension is absent; no unverified log CLI is assumed here.

Expected result is one JSON object with `ledger_generation`, `deleted`, and
`verification`. With a non-empty ledger, generation is its maximum G; every value
in `verification` must be 0. For example, these are **selected fields**, not the
complete object:

```json
{"ledger_generation":2,"deleted":{"meal":1},"verification":{"meal_below":0,"meal_item_below":0,"user_product_below":0,"meal_item_orphan_meal":0}}
```

The actual object also includes below-generation counts for day, weight, profile,
saved meals, aliases, nutrient overrides and coach tables, plus orphan counts for
meal/saved-meal children, catalogue children, overrides and coach references.
`deleted` counts depend on the dump; they need not be zero. Nonzero verification,
unreadable/torn ledger or a failed job means **stop**. An empty ledger yields
`ledger_generation:null`, `deleted:{}`, `verification:{}` and is not evidence of
the deletion-aware release gate. The first drill must include a real deletion.

For A22, compare the restored meal/day/nutrient data with the recorded pre-backup
baseline; unknown nutrients must remain unknown. For A32, verify data deleted
before the drill is absent and the restored generation is at least G. Also inspect
catalogue counts and exercise an app-role connection against the fresh database
inside the VNet. Unit/integration tests cover these behaviors on disposable local
databases; **do not point pytest at production**. Record actual checks and results
in the controller-owned `docs/evals/restore-drill.md` during the live drill.

If any check fails, leave the old database/URL secrets unchanged, reactivate the
saved revisions, and resume backups once the failed restore job has stopped:

```bash
while IFS= read -r revision; do
  az containerapp revision activate -g "$RG" -n ca-foodcoach-api --revision "$revision" --output none
done < "$RUNBOOK_TMP/active-revisions.txt"
resume_backups
```

After verifying recovery on the original database and the restored backup schedule,
explicitly clean up and end this restore attempt; do not continue to step 4:

```bash
cleanup_runbook
```

### 4. Switch both database URLs and smoke test

Update **both** the API URL and admin job URL, preserving percent encoding and
TLS options, so subsequent backups and migrations target the restored database.
The current Bicep bootstrap derives both URLs with database name `foodcoach`;
re-running it later would undo this switch. Coordinate persistent desired-state
changes with the controller before the next bootstrap deployment.

```bash
python3 - "$RUNBOOK_TMP" <<'PY'
import os, pathlib, sys
from sqlalchemy.engine import make_url
root = pathlib.Path(sys.argv[1])
for kind in ("app", "admin"):
    url = make_url((root / f"old-{kind}-url.txt").read_text().strip())
    (root / f"new-{kind}-url.txt").write_text(
        url.set(database=os.environ["RESTORE_DB"]).render_as_string(hide_password=False))
PY
az keyvault secret set --vault-name "$KV_NAME" -n database-url-app \
  --file "$RUNBOOK_TMP/new-app-url.txt" --output none
az keyvault secret set --vault-name "$KV_NAME" -n database-url-admin \
  --file "$RUNBOOK_TMP/new-admin-url.txt" --output none
az containerapp update -g "$RG" -n ca-foodcoach-api \
  --revision-suffix "restore-$(date -u +%Y%m%d%H%M%S)" \
  --set-env-vars DATABASE_URL=secretref:database-url --output none
python backend/scripts/smoke.py "https://$API_FQDN"
```

Allow Key Vault reference refresh before testing; verify the new revision and a
job resolve the new URLs, rather than assuming instantaneous propagation. Require
`Smoke passed` and a real signed-in read/write against the restored data. If switch
verification fails, stop writes again, restore both old URL secrets with `--file`,
restart/reactivate the saved old revision, verify the original database, then resume
backups. Do not revoke the old database's access until the switch is accepted.

### 5. Isolate the old database, record its drop date, resume backups

Use a one-off admin `job-migrate` template after confirming its secret now resolves
to the new database. The old database is not dropped during the restore window.
Revoking only from `foodcoach_app` is insufficient when PUBLIC retains CONNECT.

```bash
OLD_DB=$(python3 -c 'from sqlalchemy.engine import make_url; import pathlib,sys; print(make_url(pathlib.Path(sys.argv[1]).read_text().strip()).database)' "$RUNBOOK_TMP/old-admin-url.txt")
ISOLATE_CODE=$(cat <<'PY'
import os
import sqlalchemy as sa
from psycopg import sql
engine = sa.create_engine(os.environ["DATABASE_URL"])
with engine.begin() as conn:
    conn.exec_driver_sql(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC, foodcoach_app").format(
        sql.Identifier(os.environ["OLD_DB"])).as_string())
engine.dispose()
print("Old database isolated")
PY
)
job_template job-migrate "$RUNBOOK_TMP/isolate-base.json" python -c "$ISOLATE_CODE"
jq --arg db "$OLD_DB" '.containers[0].env += [{name:"OLD_DB",value:$db}]' \
  "$RUNBOOK_TMP/isolate-base.json" > "$RUNBOOK_TMP/isolate.json"
run_job job-migrate "$RUNBOOK_TMP/isolate.json"
resume_backups
```

Record the row below with a drop date **seven days after restore**. Confirm no
existing old-database app sessions remain (CONNECT revocation does not disconnect
them). After that date and explicit operational confirmation that it is still the
old inactive database, use another admin job override to execute
`DROP DATABASE <old_database>` from a different database. Never drop the active DB;
the destructive drop has not been executed or validated in this docs task.

| Database retained | Restored database | Restored at (UTC) | Drop date (UTC, +7 days) | Dump / image / ledger G | Verification / operator |
|---|---|---|---|---|---|
| _No restore performed_ | — | — | — | — | — |

After successful switching, old-database isolation, log recording and confirmation
that backups are scheduled again, explicitly clean up. On failure, retain all
recovery files until the old/new database choice is verified and recovery is complete:

```bash
cleanup_runbook
```

### Retire the old database after seven days

After confirming the recorded drop date has passed, use this guarded override;
`OLD_DB` must be the isolated database recorded above, not the current admin URL's
database. The identifier is quoted, and active sessions make DROP fail safely:

```bash
read -r -p 'Retired database from the restore log (drop date must have passed): ' OLD_DB
DROP_CODE=$(cat <<'PY'
import os
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from psycopg import sql
url = make_url(os.environ["DATABASE_URL"])
old = os.environ["OLD_DB"]
if old == url.database or old in ("postgres", "template0", "template1"):
    raise SystemExit("Refusing to drop active/system database")
engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
with engine.connect() as conn:
    conn.exec_driver_sql(sql.SQL("DROP DATABASE {}").format(sql.Identifier(old)).as_string())
engine.dispose()
print("Retired database dropped")
PY
)
job_template job-migrate "$RUNBOOK_TMP/drop-base.json" python -c "$DROP_CODE"
jq --arg db "$OLD_DB" '.containers[0].env += [{name:"OLD_DB",value:$db}]' \
  "$RUNBOOK_TMP/drop-base.json" > "$RUNBOOK_TMP/drop.json"
run_job job-migrate "$RUNBOOK_TMP/drop.json"
```

After confirming the job succeeded and recording the retirement, explicitly clean
up this separate operator session; retain files on failure:

```bash
cleanup_runbook
```

## Credential rotation

Use a maintenance window and pause backups/deployments for database or ledger
rotation. Keep old credentials privately until all consumers are verified. The
derived URL secrets must be updated along with passwords; changing only
`db-app-password` or `db-admin-password` leaves existing consumers unchanged.

### GHCR pull token: every 90 days

Create a replacement classic PAT as `Deimos2002` with `read:packages` and a new
90-day expiry. Update the existing Key Vault secret without printing it:

```bash
read -r -s -p 'New GHCR read:packages PAT: ' NEW_GHCR_TOKEN; printf '\n'
printf '%s' "$NEW_GHCR_TOKEN" > "$RUNBOOK_TMP/ghcr-token.txt"
az keyvault secret set --vault-name "$KV_NAME" -n ghcr-pull-token \
  --file "$RUNBOOK_TMP/ghcr-token.txt" --output none
unset NEW_GHCR_TOKEN
```

Wait for Key Vault-reference refresh, restart the API revision if necessary, and
start a known-safe job/new revision to prove a fresh private image pull succeeds.
Only then revoke the previous PAT. Failed pulls should stop the rollout, not cause
a switch to public package access.

After a verified fresh pull and revocation of the old token, explicitly clean up.
Retain the private files if verification or recovery is still pending:

```bash
cleanup_runbook
```

### Ledger account key: two-key rotation

The environment storage currently selects `keys[0]` (key1); there is no ledger-key
Key Vault secret. In a no-write window deactivate the API revisions as in restore
and wait for restore jobs to stop. Renew the unused key2, switch the environment
storage registration to it, restart/reactivate the API, and verify marker access
and ledger append before renewing key1. Then switch back to fresh key1 so Bicep's
`keys[0]` stays consistent. Use the same deactivate/activate sequence around each
storage switch; confirm both API and restore can read the existing ledger.

```bash
az storage account keys renew -g "$RG" -n "$LEDGER_ACCOUNT" --key key2 --output none
LEDGER_KEY=$(az storage account keys list -g "$RG" -n "$LEDGER_ACCOUNT" \
  --query "[?keyName=='key2'].value | [0]" -o tsv)
az containerapp env storage set -g "$RG" -n cae-foodcoach-prod --storage-name ledger \
  --account-name "$LEDGER_ACCOUNT" --azure-file-share-name "$LEDGER_SHARE" \
  --azure-file-account-key "$LEDGER_KEY" --access-mode ReadWrite --output none
unset LEDGER_KEY
# Reactivate/restart and verify, then quiesce again before the second switch.
az storage account keys renew -g "$RG" -n "$LEDGER_ACCOUNT" --key key1 --output none
LEDGER_KEY=$(az storage account keys list -g "$RG" -n "$LEDGER_ACCOUNT" \
  --query "[?keyName=='key1'].value | [0]" -o tsv)
az containerapp env storage set -g "$RG" -n cae-foodcoach-prod --storage-name ledger \
  --account-name "$LEDGER_ACCOUNT" --azure-file-share-name "$LEDGER_SHARE" \
  --azure-file-account-key "$LEDGER_KEY" --access-mode ReadWrite --output none
unset LEDGER_KEY
```

If a switch fails, leave the key still in use valid and restore that registration.
Never renew both keys before proving the first switch. Re-enable API and backups
after marker/ledger checks pass. Account-key changes can require a fresh mount;
live remount behavior was not verified here.

After both mount checks pass and API/backups are restored, explicitly clean up;
keep the recovery files if either switch failed:

```bash
cleanup_runbook
```

### Database passwords

For the app password, generate a new value, update the login through bootstrap
step 9 (using `DB_APP_PASSWORD` set to the new value), update `db-app-password` and
the password component of `database-url-app`, then restart the API and test. For
the admin password, change the server password, update `db-admin-password` and
`database-url-admin`, then verify a new admin job connection. Preserve each URL's
current database after a restore; do not reconstruct a URL hard-coded to foodcoach.

```bash
NEW_DB_APP_PASSWORD=$(openssl rand -base64 32)
NEW_DB_ADMIN_PASSWORD=$(openssl rand -base64 32)
export NEW_DB_APP_PASSWORD NEW_DB_ADMIN_PASSWORD
az keyvault secret show --vault-name "$KV_NAME" -n database-url-app --query value -o tsv \
  > "$RUNBOOK_TMP/current-app-url.txt"
az keyvault secret show --vault-name "$KV_NAME" -n database-url-admin --query value -o tsv \
  > "$RUNBOOK_TMP/current-admin-url.txt"
python3 - "$RUNBOOK_TMP" <<'PY'
import os, pathlib, sys
from sqlalchemy.engine import make_url
root = pathlib.Path(sys.argv[1])
for kind in ("app", "admin"):
    password = os.environ[f"NEW_DB_{kind.upper()}_PASSWORD"]
    (root / f"new-{kind}-password.txt").write_text(password)
    url = make_url((root / f"current-{kind}-url.txt").read_text().strip())
    (root / f"rotated-{kind}-url.txt").write_text(
        url.set(password=password).render_as_string(hide_password=False))
PY
DB_APP_PASSWORD=$NEW_DB_APP_PASSWORD
# Execute bootstrap step 9's job-secret/template/run/remove commands now.
# Smoke waits until the URL secret update and API restart below.
az keyvault secret set --vault-name "$KV_NAME" -n db-app-password \
  --file "$RUNBOOK_TMP/new-app-password.txt" --output none
az keyvault secret set --vault-name "$KV_NAME" -n database-url-app \
  --file "$RUNBOOK_TMP/rotated-app-url.txt" --output none
# Password briefly visible in process arguments: az CLI has no stdin option for --admin-password.
az postgres flexible-server update -g "$RG" -n psql-foodcoach-prod \
  --admin-password "$NEW_DB_ADMIN_PASSWORD" --output none
az keyvault secret set --vault-name "$KV_NAME" -n db-admin-password \
  --file "$RUNBOOK_TMP/new-admin-password.txt" --output none
az keyvault secret set --vault-name "$KV_NAME" -n database-url-admin \
  --file "$RUNBOOK_TMP/rotated-admin-url.txt" --output none
```

Wait for reference refresh, restart/reactivate the API with
`az containerapp revision restart -g "$RG" -n ca-foodcoach-api --revision <revision>`.
Run deep health and an admin `job-migrate` one-off `python -c` override executing
`SELECT 1` through its `DATABASE_URL` (do not downgrade migrations), then a backup
and retention check. Restore the old password and matching secrets if verification
fails. Resume the schedule only once connections work. Remove private temporary
files and unset password variables after completion.

```bash
job_template job-migrate "$RUNBOOK_TMP/admin-check.json" python -c \
  'import os,sqlalchemy as sa; engine=sa.create_engine(os.environ["DATABASE_URL"]); conn=engine.connect(); assert conn.execute(sa.text("SELECT 1")).scalar_one()==1; conn.close(); engine.dispose(); print("Admin connection verified")'
run_job job-migrate "$RUNBOOK_TMP/admin-check.json"
python backend/scripts/smoke.py "https://$API_FQDN"
```

After the API/admin/backup checks pass and the schedule is restored (or full
recovery is verified), explicitly clean up. Do not clean up during a partial rotation:

```bash
cleanup_runbook
```

## Coach playground

The public playground is a separate Container App (`foodcoach-demo` in `rg-copilot`) running
`uvicorn app.demo:create_demo_app --factory` from the image the `Demo image` workflow publishes for each
commit on `main` (`ghcr.io/deimos2002/food-coach-ai:sha-<commit>`). It has no database and no login.

- Deploy: `az containerapp update -n foodcoach-demo -g rg-copilot --image ghcr.io/deimos2002/food-coach-ai:sha-<commit>`.
- Scale: one replica kept running (`--min-replicas 1 --max-replicas 1`, 0.25 vCPU / 0.5 GiB). From zero
  replicas the first page load took 25 s; the app's peak use over a week was 84 MB and 0.012 cores.
- Settings: `AZURE_OPENAI_*` (key as the secret `azure-openai-key`), `COACH_PRICE_IN_PER_1K`,
  `COACH_PRICE_OUT_PER_1K`, `COACH_DEMO_DAILY_REVIEWS` (live calls per day, default 30).
- Reviewer flags: `COACH_DEMO_FLAGS_PATH=/data/flags/flags.jsonl`, on the Azure Files share `flags` of
  the storage account `foodcoachflags41344`, registered in the environment as `foodcoach-flags` and
  mounted at `/data/flags` with `uid=10001,gid=10001,dir_mode=0700,file_mode=0600` (the image runs as
  uid 10001). The flags survive restarts and deploys; to clear them, delete `flags.jsonl` from the
  share and restart the revision.

## Verification limits

Resource names, outputs, secret references, script flags and workflow triggers were
checked against the source files listed in the implementation report. Backup,
restore and retention `--help` succeeded. `smoke.py --help` exits 1 with an invalid
URL/HTTP-request failure; it takes a single BASE_URL and has no help parser.
`check_model_deployment.py` reads JSON from stdin and has no help parser; passing
`--help` with closed/empty stdin exits 1. The Ciqual CLI treats its only argument
as an XLSX path, so `--help` would try a database connection; it was read, not run.

Azure/GitHub CLI flags were checked using local `--help`, except job log streaming,
which requires the missing `containerapp` extension. Template overrides, Graph
registration payloads, REST patches, identity/RBAC propagation, quota, private DNS,
Key Vault refresh, initial deployment, actual download contents, full live A21,
A22/A32, rollback, restore and credential rotation remain **unverified in Azure**.
No what-if was run: it needs a correctly bootstrapped subscription and identities.

## Verified interface inventory

References are relative to the repository root, checked after merging main commit
`20d3008`. Rows group related names but enumerate each referenced interface.

| Name / flag / setting | Source file:line |
|---|---|
| `rg-foodcoach-prod`, prod approval environment, `deploy-prod` concurrency | `.github/workflows/deploy.yml:19`, `:29`, `:32` |
| `italynorth`, fixed subscription/tenant/user IDs, `budgetStartDate` | `infra/prod.bicepparam:5`, `:9`, `:33`, `:34`, `:46` |
| `kv-foodcoach-prod`, `log-foodcoach-prod`, unique `stbackup…`, `stledger…` names | `infra/bootstrap.bicep:44`, `:45`, `:46`, `:47` |
| `id-api`, `id-ops`, `ag-foodcoach-prod` | `infra/bootstrap.bicep:65`, `:70`, `:330` |
| `cae-foodcoach-prod`, environment storage `ledger`, `ReadWrite`, key1 (`keys[0]`) | `infra/modules/environment.bicep:20`, `:46`, `:50`, `:52` |
| `ca-foodcoach-api`, container `api`, Single revision, min 0/max 1 | `infra/modules/api.bicep:21`, `:33`, `:68`, `:123`, `:124` |
| `psql-foodcoach-prod`, `foodcoach_admin`, database `foodcoach`, Postgres 16 | `infra/modules/postgres.bicep:29`, `:37`, `:38`, `:71` |
| `vnet-foodcoach-prod`, `snet-aca`, `snet-pg`, private DNS `privatelink.postgres.database.azure.com` | `infra/modules/network.bicep:7`, `:17`, `:31`; `infra/modules/postgres.bicep:12` |
| Bootstrap inputs `env`, `location`, `deployPrincipalId`, `aoaiAccountId`, `alertEmail` | `infra/bootstrap.bicep:9`, `:18`, `:21`, `:24`, `:27` |
| Secure inputs `dbAdminPassword`, `dbAppPassword`, `aoaiKey`, `ghcrPullToken` | `infra/bootstrap.bicep:30`, `:33`, `:36`, `:39` |
| Outputs `idApiId`, `idApiClientId`, `idOpsId`, `idOpsClientId` | `infra/bootstrap.bicep:367`, `:368`, `:369`, `:370` |
| Outputs `keyVaultName`, `keyVaultUri`, `backupAccountName`, `ledgerAccountName`, `ledgerShareName`, `logWorkspaceId`, `actionGroupId` | `infra/bootstrap.bicep:371`, `:372`, `:373`, `:374`, `:375`, `:376`, `:377` |
| Main outputs `apiFqdn`, `postgresFqdn`, `idApiClientId` | `infra/main.bicep:165`, `:166`, `:167` |
| Secrets `db-admin-password`, `db-app-password`, `aoai-key`, `ghcr-pull-token`, `database-url-app`, `database-url-admin` | `infra/bootstrap.bicep:96`, `:104`, `:112`, `:120`, `:128`, `:136` |
| API `database-url` → app URL; jobs `database-url` → admin URL | `infra/modules/api.bicep:49`; `infra/modules/jobs.bicep:26` |
| `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID` repository variables | `.github/workflows/deploy.yml:86`, `:87`, `:88` |
| `VITE_ENTRA_CLIENT_ID`, `VITE_ENTRA_TENANT_ID`, `VITE_API_SCOPE` build variables | `.github/workflows/deploy.yml:67`, `:68`, `:69`; `web/src/lib/msal.ts:44`, `:45`, `:67` |
| `BOOTSTRAP_ID_API_CLIENT_ID`, `BOOTSTRAP_ID_OPS_CLIENT_ID`, `BOOTSTRAP_BACKUP_ACCOUNT_NAME`, `BOOTSTRAP_LEDGER_ACCOUNT_NAME` | `.github/workflows/deploy.yml:33`, `:34`, `:35`, `:36`; `infra/prod.bicepparam:21` |
| `API_IMAGE_DIGEST`, `JOB_IMAGE_DIGEST`, Bicep `apiImageDigest`, `jobImageDigest` | `.github/workflows/deploy.yml:122`, `:123`, `:128`; `infra/main.bicep:31`, `:34` |
| Dispatch `digest`; GHCR digest format, `deimos2002` pull username; `GIT_SHA` build argument | `.github/workflows/deploy.yml:9`, `:78`, `:70`; `infra/modules/jobs.bicep:40`; `Dockerfile:49` |
| `Food Coach API`, `Food Coach Web`, `Food Coach Deploy`, `repo:<owner>/<repo>:environment:prod` | Design §2 (Entra registration names and credential subject); workflow environment `.github/workflows/deploy.yml:29` |
| `api://foodcoach-prod`, `Food.ReadWrite`, v2 issuer, exact audience validation | `infra/main.bicep:46`, `:47`; `backend/app/config.py:23`; `backend/app/api/auth.py:45` |
| `foodcoach-aoai-0000`, `coach-mini-eu`, `DataZoneStandard`, `gpt-4.1-mini`, `2025-04-14`, `NoAutoUpgrade` | `.github/workflows/deploy.yml:92`, `:93`; `backend/scripts/check_model_deployment.py:8`, `:9`, `:10`, `:11` |
| `job-migrate`, `job-import`, `job-backup`, `job-restore`, `job-retention-check`; job container `main` | `infra/modules/jobs.bicep:70`, `:103`, `:133`, `:171`, `:217`, `:87` |
| Backup cron `30 0 * * *`, retention cron `0 6 * * 1` | `infra/modules/jobs.bicep:143`, `:226` |
| `backups`, share `ledger`, lifecycle 14 days, soft deletion 7 days, no versioning, no backup shared-key access | `infra/bootstrap.bicep:237`, `:310`, `:265`, `:229`, `:231`, `:216` |
| `BACKUP_ACCOUNT_URL`, managed identity `AZURE_CLIENT_ID`, `DATABASE_URL` | `infra/modules/jobs.bicep:47`, `:49`, `:46` |
| `/mnt/ledger/deletion_ledger.jsonl`, `DELETION_LEDGER_PATH`, mount `/mnt/ledger`, `.ledger-volume` | `infra/modules/jobs.bicep:56`, `:200`; `backend/app/services/privacy.py:30` |
| Local `data/deletion_ledger.jsonl`, marker guard | `backend/app/config.py:26`; `backend/app/services/privacy.py:37` |
| Backup `--out`, `--db-url`, `--blob ACCOUNT_URL`, `--container`, `--sha`; `GIT_SHA` → `GITHUB_SHA` → `unknown` | `backend/scripts/backup.py:58`, `:59`, `:60`, `:61`, `:63` |
| UTC dump-name pattern, overwrite refusal, `include=["deleted"]` | `backend/scripts/blobstore.py:45`, `:33`, `:40` |
| Restore positional `dump` / `target_db_url`, `--blob NAME`, `--account-url`, `BACKUP_ACCOUNT_URL`, `--ledger`, `--no-ledger`, `--oid` | `backend/scripts/restore.py:130`, `:131`, `:132`, `:133`, `:135`, `:136`, `:137` |
| pg_dump `--format=custom`; pg_restore `--clean`, `--if-exists`, `--no-owner`, `--no-acl`, `--exit-on-error`; automatic app grants | `backend/scripts/backup.py:35`; `backend/scripts/restore.py:76`, `:77`, `:78`, `:79`, `:80`, `:87` |
| `foodcoach_app`, migration `0006_app_role`, `NOLOGIN`, default privileges use migrating admin | `backend/app/db/app_role.py:12`, `:23`, `:36`; `backend/migrations/versions/0006_app_role.py:17`, `:31` |
| Retention positional `ACCOUNT_URL`, `--max-age-days` 25, `--max-fresh-hours` 48, live/soft-deleted/freshness checks | `backend/scripts/retention_check.py:33`, `:34`, `:35`, `:16` |
| `python -m app.importers.ciqual <path-to-xlsx>`, completion message | `backend/app/importers/ciqual.py:145`, `:147`, `:164` |
| Restore JSON `ledger_generation`, `deleted`, `verification`, all counts zero | `backend/scripts/restore.py:88`, `:109`; `backend/app/services/privacy.py:154`, `:168`, `:197` |
| `/health`, `/health?deep=1`, `/api`, `smoke.py BASE_URL`, `Smoke passed` | `backend/app/main.py:37`; `infra/modules/api.bicep:75`; `backend/scripts/smoke.py:14`, `:34`, `:42` |

### External CLI flags checked with local help

These flags are Azure/GitHub CLI interfaces, not project-defined flags. The source
above supplies their names/values; each command below was invoked with `--help`
only. This table lists the flags used by the runbook, not every flag offered by
the CLIs. Role/password SQL and Graph payload fields were inspected, not executed.

| CLI command family | Verified flags used |
|---|---|
| Common Azure flags | `--resource-group`/`-g`, `--name`/`-n`, `--query`, `--output`/`-o` (`none`, `json`, `tsv`, `table`) |
| `az group create`, `account show`, `ad signed-in-user show` | `--location`; `--query`, `-o` |
| `az ad app create`, `ad app show`, `ad sp create/show` | `--display-name`, `--sign-in-audience`, `--identifier-uris`, `--id` |
| `az ad app permission add/admin-consent`, `ad app federated-credential create` | `--id`, `--api`, `--api-permissions`, `--parameters` |
| `az deployment group create/show` | `--template-file`, `--parameters`, `--name` |
| `az role assignment create` | `--assignee-object-id`, `--assignee-principal-type`, `--role`, `--scope` |
| `az rest` | `--method` (`patch`, `put`), `--url`, `--body` (including `@file`) |
| `az storage account keys list/renew`, `storage file upload` | `--key` (`key1`, `key2`), `--account-name`, `--share-name`, `--path`, `--source`; `AZURE_STORAGE_KEY` credential environment |
| `az cognitiveservices account deployment create/show` | `--deployment-name`, `--model-format`, `--model-name`, `--model-version`, `--sku-name`, `--sku-capacity`; no version-upgrade flag |
| `az containerapp job show/start`, `job execution show/list` | `--yaml`, `--job-execution-name`; template query `properties.template`, status query `properties.status` |
| `az containerapp job secret set/remove` | `--secrets`, `--secret-names`, `--yes` |
| `az containerapp show/update`, `revision list/activate/deactivate/restart` | `--revision`, `--revision-suffix`, `--set-env-vars` |
| `az containerapp env storage set` | `--storage-name`, `--account-name`, `--azure-file-share-name`, `--azure-file-account-key`, `--access-mode` |
| `az postgres flexible-server db create`, `flexible-server update` | `--server-name`, `--name`, `--charset`, `--collation`, `--admin-password` |
| `az keyvault secret show/set` | `--vault-name`, `--name`, `--file`, `--query value` |
| `gh repo create`, `variable set` | `--private`, `--source`, `--remote`, `-R`, `--body` |
| `gh workflow run`, `run list/watch/rerun` | `-R`, `--ref`, `--raw-field`, `--workflow`, `--limit`, `--exit-status`, `--failed` |
