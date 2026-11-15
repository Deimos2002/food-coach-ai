"""Static checks on the Bicep templates under infra/ (compiled to ARM JSON, never deployed)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.infra

AZ = shutil.which("az")
if AZ is None:
    pytest.skip("az CLI not found on PATH", allow_module_level=True)

INFRA_DIR = Path(__file__).resolve().parents[3] / "infra"
BOOTSTRAP = INFRA_DIR / "bootstrap.bicep"

SECRETS_USER_ROLE = "4633458b-17de-408a-b874-0445c86b69e6"
BLOB_CONTRIBUTOR_ROLE = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
CONTRIBUTOR_ROLE = "b24988ac-6180-42a0-ab88-20f7382dd24c"
READER_ROLE = "acdd72a7-3385-48ef-bd42-f606fba81ae7"


# ---------------------------------------------------------------- helpers (shared)


def compile_bicep(path: Path) -> dict:
    """Compile a Bicep file to its ARM JSON template with `az bicep build --stdout`."""
    proc = subprocess.run(
        [AZ, "bicep", "build", "--file", str(path), "--stdout"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _resource_list(template: dict) -> list[dict]:
    res = template.get("resources", [])
    # languageVersion 2.0 templates key resources by symbolic name
    return list(res.values()) if isinstance(res, dict) else list(res)


def flatten_resources(template: dict) -> list[dict]:
    """All resources, including those inside nested (module) deployments.

    Each returned resource carries `_vars`: the variables of the template that declares it,
    so expressions like `variables('x')` can be resolved with `resolve_vars`.
    """
    out: list[dict] = []
    variables = template.get("variables", {})
    for r in _resource_list(template):
        r = {**r, "_vars": variables}
        out.append(r)
        if r.get("type") == "Microsoft.Resources/deployments":
            inner = r.get("properties", {}).get("template")
            if inner:
                out.extend(flatten_resources(inner))
    return out


def of_type(resources: list[dict], rtype: str) -> list[dict]:
    return [r for r in resources if r.get("type", "").lower() == rtype.lower()]


def resolve_vars(expr: str, variables: dict) -> str:
    """Inline string-valued `variables('x')` references (one level) into an ARM expression."""

    def sub(m: re.Match) -> str:
        value = variables.get(m.group(1))
        return value if isinstance(value, str) else m.group(0)

    return re.sub(r"variables\('([A-Za-z0-9_]+)'\)", sub, expr)


def role_assignments(resources: list[dict], role_guid: str) -> list[dict]:
    return [
        r
        for r in of_type(resources, "Microsoft.Authorization/roleAssignments")
        if role_guid in resolve_vars(r["properties"]["roleDefinitionId"], r.get("_vars", {}))
    ]


# ---------------------------------------------------------------- bootstrap.bicep


@pytest.fixture(scope="module")
def bootstrap() -> dict:
    return compile_bicep(BOOTSTRAP)


@pytest.fixture(scope="module")
def bootstrap_resources(bootstrap: dict) -> list[dict]:
    return flatten_resources(bootstrap)


def test_bootstrap_location_italynorth(bootstrap, bootstrap_resources):
    assert bootstrap["parameters"]["location"]["defaultValue"] == "italynorth"
    located = [r for r in bootstrap_resources if "location" in r]
    assert located
    for r in located:
        if r["location"] == "global":
            # action groups only exist in the global location
            assert r["type"] == "Microsoft.Insights/actionGroups"
        else:
            assert r["location"] == "[parameters('location')]", r["type"]


def test_backup_storage_hardened(bootstrap_resources):
    accounts = of_type(bootstrap_resources, "Microsoft.Storage/storageAccounts")
    backup = [a for a in accounts if "backupAccountName" in a["name"]]
    assert len(backup) == 1
    props = backup[0]["properties"]
    assert props["allowSharedKeyAccess"] is False
    assert props["minimumTlsVersion"] == "TLS1_2"
    assert props["allowBlobPublicAccess"] is False

    blob_svcs = of_type(bootstrap_resources, "Microsoft.Storage/storageAccounts/blobServices")
    assert len(blob_svcs) == 1
    assert "backupAccountName" in blob_svcs[0]["name"]
    bp = blob_svcs[0]["properties"]
    assert bp["deleteRetentionPolicy"] == {"enabled": True, "days": 7}
    assert bp["isVersioningEnabled"] is False

    policies = of_type(bootstrap_resources, "Microsoft.Storage/storageAccounts/managementPolicies")
    assert len(policies) == 1
    assert "backupAccountName" in policies[0]["name"]
    rules = policies[0]["properties"]["policy"]["rules"]
    deletes = [
        r["definition"]["actions"]["baseBlob"]["delete"]
        for r in rules
        if r["enabled"] and "baseBlob" in r["definition"]["actions"]
    ]
    assert {"daysAfterCreationGreaterThan": 14} in deletes

    containers = of_type(
        bootstrap_resources, "Microsoft.Storage/storageAccounts/blobServices/containers"
    )
    assert len(containers) == 1
    assert "backupAccountName" in containers[0]["name"]
    assert "'backups')]" in containers[0]["name"]
    assert containers[0]["properties"]["publicAccess"] == "None"


def test_ledger_storage_separate(bootstrap, bootstrap_resources):
    accounts = of_type(bootstrap_resources, "Microsoft.Storage/storageAccounts")
    assert len(accounts) == 2
    assert len([a for a in accounts if "ledgerAccountName" in a["name"]]) == 1
    shares = of_type(bootstrap_resources, "Microsoft.Storage/storageAccounts/fileServices/shares")
    assert len(shares) == 1
    assert "ledgerAccountName" in shares[0]["name"]
    assert "'ledger')]" in shares[0]["name"]
    assert shares[0]["properties"]["shareQuota"] == 1
    # no blob container (or blob service) on the ledger account
    for r in of_type(
        bootstrap_resources, "Microsoft.Storage/storageAccounts/blobServices/containers"
    ) + of_type(bootstrap_resources, "Microsoft.Storage/storageAccounts/blobServices"):
        assert "ledgerAccountName" not in r["name"]
    assert bootstrap["outputs"]["ledgerShareName"]["value"] == "ledger"


def _secrets(resources) -> dict[str, dict]:
    out = {}
    for s in of_type(resources, "Microsoft.KeyVault/vaults/secrets"):
        m = re.search(r"'([a-z-]+)'\)\]$", s["name"])
        assert m, s["name"]
        out[m.group(1)] = s
    return out


def test_key_vault_rbac(bootstrap_resources):
    vaults = of_type(bootstrap_resources, "Microsoft.KeyVault/vaults")
    assert len(vaults) == 1
    vp = vaults[0]["properties"]
    assert vp["enableRbacAuthorization"] is True
    assert vp["enablePurgeProtection"] is True

    secrets = _secrets(bootstrap_resources)
    assert set(secrets) == {
        "db-admin-password",
        "db-app-password",
        "aoai-key",
        "ghcr-pull-token",
        "database-url-app",
        "database-url-admin",
    }
    assert secrets["db-admin-password"]["properties"]["value"] == "[parameters('dbAdminPassword')]"
    assert secrets["db-app-password"]["properties"]["value"] == "[parameters('dbAppPassword')]"
    assert secrets["aoai-key"]["properties"]["value"] == "[parameters('aoaiKey')]"
    assert secrets["ghcr-pull-token"]["properties"]["value"] == "[parameters('ghcrPullToken')]"

    tail = ".postgres.database.azure.com:5432/foodcoach?sslmode=require"
    app_url = secrets["database-url-app"]["properties"]["value"]
    assert "postgresql+psycopg://foodcoach_app:" in app_url
    assert "uriComponent(parameters('dbAppPassword'))" in app_url
    assert "dbAdminPassword" not in app_url
    assert "@psql-foodcoach-" in app_url and "parameters('env')" in app_url
    assert tail in app_url

    admin_url = secrets["database-url-admin"]["properties"]["value"]
    assert "postgresql+psycopg://foodcoach_admin:" in admin_url
    assert "uriComponent(parameters('dbAdminPassword'))" in admin_url
    assert "dbAppPassword" not in admin_url
    assert "@psql-foodcoach-" in admin_url and "parameters('env')" in admin_url
    assert tail in admin_url


def test_secure_params_not_output(bootstrap):
    secure = [
        name
        for name, p in bootstrap["parameters"].items()
        if p["type"].lower() in ("securestring", "secureobject")
    ]
    assert set(secure) == {"dbAdminPassword", "dbAppPassword", "aoaiKey", "ghcrPullToken"}
    outputs = json.dumps(bootstrap.get("outputs", {}))
    for name in secure:
        assert name not in outputs
    assert "listKeys" not in outputs
    assert "/secrets" not in outputs


def _scoped_secret(ra: dict) -> str | None:
    m = re.search(r"Microsoft\.KeyVault/vaults/secrets', [^,]+, '([a-z-]+)'\)", ra.get("scope", ""))
    return m.group(1) if m else None


def _principal_ref(identity: str) -> str:
    return f"Microsoft.ManagedIdentity/userAssignedIdentities', '{identity}')"


def test_role_assignments_per_secret(bootstrap_resources):
    secrets_user = role_assignments(bootstrap_resources, SECRETS_USER_ROLE)
    by_identity: dict[str, set[str]] = {"id-api": set(), "id-ops": set()}
    for ra in secrets_user:
        principal = ra["properties"]["principalId"]
        ident = next(i for i in by_identity if _principal_ref(i) in principal)
        secret = _scoped_secret(ra)
        assert secret is not None, f"not scoped to a single secret: {ra.get('scope')}"
        by_identity[ident].add(secret)
        assert ra["properties"]["principalType"] == "ServicePrincipal"
    assert by_identity["id-api"] == {"database-url-app", "aoai-key", "ghcr-pull-token"}
    assert by_identity["id-ops"] == {"database-url-admin", "db-admin-password", "ghcr-pull-token"}
    assert len(secrets_user) == 6


def test_deploy_principal_roles(bootstrap_resources):
    all_ras = of_type(bootstrap_resources, "Microsoft.Authorization/roleAssignments")
    deploy = [r for r in all_ras if "deployPrincipalId" in r["properties"]["principalId"]]
    assert len(deploy) == 2
    contributor = role_assignments(deploy, CONTRIBUTOR_ROLE)
    reader = role_assignments(deploy, READER_ROLE)
    assert len(contributor) == 1 and len(reader) == 1
    assert "scope" not in contributor[0]  # resource-group scope
    assert "Microsoft.CognitiveServices/accounts" in reader[0]["scope"]
    # Reader lives in a module deployed into the AOAI account's resource group
    modules = of_type(bootstrap_resources, "Microsoft.Resources/deployments")
    aoai_mods = [m for m in modules if "aoaiAccountId" in m.get("resourceGroup", "")]
    assert len(aoai_mods) == 1
    assert len(flatten_resources(aoai_mods[0]["properties"]["template"])) == 1


def test_ops_blob_contributor_scoped_to_container(bootstrap_resources):
    ras = role_assignments(bootstrap_resources, BLOB_CONTRIBUTOR_ROLE)
    assert len(ras) == 1
    ra = ras[0]
    assert _principal_ref("id-ops") in ra["properties"]["principalId"]
    assert "Microsoft.Storage/storageAccounts/blobServices/containers" in ra["scope"]
    assert "'backups')" in ra["scope"]


# ---------------------------------------------------------------- main.bicep

MAIN = INFRA_DIR / "main.bicep"
PROD_PARAMS = INFRA_DIR / "prod.bicepparam"
JOB_NAMES = {"job-migrate", "job-import", "job-backup", "job-restore", "job-retention-check"}


@pytest.fixture(scope="module")
def main_template() -> dict:
    return compile_bicep(MAIN)


@pytest.fixture(scope="module")
def main_resources(main_template: dict) -> list[dict]:
    return flatten_resources(main_template)


def deep_resolve(value, variables: dict):
    """Recursively replace whole-value `[variables('x')]` strings with the variable's value."""
    if isinstance(value, dict):
        return {k: deep_resolve(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [deep_resolve(v, variables) for v in value]
    if isinstance(value, str):
        m = re.fullmatch(r"\[variables\('([A-Za-z0-9_]+)'\)\]", value)
        if m and m.group(1) in variables:
            return deep_resolve(variables[m.group(1)], variables)
    return value


def _named(resources: list[dict], rtype: str, name: str) -> dict:
    found = [r for r in of_type(resources, rtype) if name in r["name"]]
    assert len(found) == 1, (rtype, name, [r["name"] for r in of_type(resources, rtype)])
    return deep_resolve(found[0], found[0].get("_vars", {}))


def _env(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env", [])}


def _assert_kv_secrets(secrets: list[dict], identity_param: str) -> dict[str, str]:
    """Every secret is a Key Vault reference through the identity; returns name -> url."""
    out = {}
    for s in secrets:
        assert "value" not in s, s["name"]
        assert "keyVaultUrl" in s and "secrets/" in s["keyVaultUrl"], s
        assert f"parameters('{identity_param}')" in s["identity"], s
        out[s["name"]] = s["keyVaultUrl"]
    return out


def _secret_url(env: dict, name: str, urls: dict[str, str]) -> str:
    return urls[env[name]["secretRef"]]


def test_main_has_no_role_assignments(main_resources):
    assert of_type(main_resources, "Microsoft.Authorization/roleAssignments") == []
    assert of_type(main_resources, "Microsoft.Authorization/roleDefinitions") == []


def test_subnets(main_resources):
    vnet = _named(main_resources, "Microsoft.Network/virtualNetworks", "vnet-foodcoach-")
    assert vnet["properties"]["addressSpace"]["addressPrefixes"] == ["10.40.0.0/24"]
    subnets = {s["name"]: s["properties"] for s in vnet["properties"]["subnets"]}
    assert subnets["snet-aca"]["addressPrefix"] == "10.40.0.0/26"
    assert [d["properties"]["serviceName"] for d in subnets["snet-aca"]["delegations"]] == [
        "Microsoft.App/environments"
    ]
    assert subnets["snet-pg"]["addressPrefix"] == "10.40.0.64/28"
    assert [d["properties"]["serviceName"] for d in subnets["snet-pg"]["delegations"]] == [
        "Microsoft.DBforPostgreSQL/flexibleServers"
    ]


def test_postgres_private(main_template, main_resources):
    pg = _named(main_resources, "Microsoft.DBforPostgreSQL/flexibleServers", "psql-foodcoach-")
    props = pg["properties"]
    assert props["version"] == "16"
    assert pg["sku"]["name"] == "Standard_B1ms"
    assert props["storage"]["storageSizeGB"] == 32
    assert props["network"]["publicNetworkAccess"] == "Disabled"
    assert "pgSubnetId" in props["network"]["delegatedSubnetResourceId"]
    assert "privateDnsZones" in props["network"]["privateDnsZoneArmResourceId"]
    assert props["backup"]["backupRetentionDays"] == 7
    assert props["administratorLogin"] == "foodcoach_admin"
    assert props["administratorLoginPassword"] == "[parameters('adminPassword')]"

    zone = _named(main_resources, "Microsoft.Network/privateDnsZones", "privatelink.postgres")
    assert zone["name"] == "privatelink.postgres.database.azure.com"
    links = of_type(main_resources, "Microsoft.Network/privateDnsZones/virtualNetworkLinks")
    assert len(links) == 1
    assert "vnetId" in links[0]["properties"]["virtualNetwork"]["id"]
    assert links[0]["properties"]["registrationEnabled"] is False

    ext = _named(
        main_resources,
        "Microsoft.DBforPostgreSQL/flexibleServers/configurations",
        "azure.extensions",
    )
    assert ext["properties"]["value"] == "PG_TRGM,UNACCENT"

    # admin password: getSecret on the existing vault -> Key Vault reference module param
    mods = [
        m
        for m in of_type(main_resources, "Microsoft.Resources/deployments")
        if "adminPassword" in m["properties"].get("parameters", {})
    ]
    assert len(mods) == 1
    ref = mods[0]["properties"]["parameters"]["adminPassword"]["reference"]
    assert ref["secretName"] == "db-admin-password"
    assert "Microsoft.KeyVault/vaults" in ref["keyVault"]["id"]
    assert "postgresFqdn" in main_template["outputs"]


def test_environment_workload_profiles(main_resources):
    cae = _named(main_resources, "Microsoft.App/managedEnvironments", "cae-foodcoach-")
    props = cae["properties"]
    assert props["workloadProfiles"] == [
        {"name": "Consumption", "workloadProfileType": "Consumption"}
    ]
    assert props["vnetConfiguration"]["internal"] is False
    assert "acaSubnetId" in props["vnetConfiguration"]["infrastructureSubnetId"]
    logs = props["appLogsConfiguration"]
    assert logs["destination"] == "log-analytics"
    la = logs["logAnalyticsConfiguration"]
    assert "logWorkspaceId" in la["customerId"]
    assert "listKeys" in la["sharedKey"]

    storage = _named(main_resources, "Microsoft.App/managedEnvironments/storages", "ledger")
    af = storage["properties"]["azureFile"]
    assert af["accountName"] == "[parameters('ledgerAccountName')]"
    assert af["shareName"] == "[parameters('ledgerShareName')]"
    assert af["accessMode"] == "ReadWrite"
    assert "listKeys" in af["accountKey"]


def test_api_app(main_template, main_resources):
    app = _named(main_resources, "Microsoft.App/containerApps", "ca-foodcoach-api")
    assert "parameters('idApiId')" in json.dumps(app["identity"]["userAssignedIdentities"])
    cfg = app["properties"]["configuration"]
    assert cfg["activeRevisionsMode"] == "Single"
    assert cfg["ingress"]["external"] is True
    assert cfg["ingress"]["targetPort"] == 8000
    (reg,) = cfg["registries"]
    assert reg["server"] == "ghcr.io" and reg["username"] == "deimos2002"

    urls = _assert_kv_secrets(cfg["secrets"], "idApiId")
    assert "secrets/ghcr-pull-token'" in urls[reg["passwordSecretRef"]]

    tmpl = app["properties"]["template"]
    assert tmpl["scale"]["minReplicas"] == 0
    assert tmpl["scale"]["maxReplicas"] == 1
    (c,) = tmpl["containers"]
    assert c["image"] == "[parameters('apiImageDigest')]"
    ready = [p for p in c["probes"] if p["type"] == "Readiness"]
    assert ready and ready[0]["httpGet"]["path"] == "/health"

    env = _env(c)
    expected = {
        "API_PREFIX": "/api",
        "STATIC_DIR": "/app/static",
        "REQUIRE_LEDGER_VOLUME": "true",
        "DELETION_LEDGER_PATH": "/mnt/ledger/deletion_ledger.jsonl",
        "AZURE_OPENAI_DEPLOYMENT": "coach-mini-eu",
        "AZURE_OPENAI_DEPLOYMENT_TYPE": "datazone",
        "COACH_ALLOW_GLOBAL": "false",
    }
    for k, v in expected.items():
        assert env[k]["value"] == v, k
    for k in (
        "ALLOWED_TID",
        "ALLOWED_OID",
        "ENTRA_AUDIENCE",
        "ENTRA_ISSUER",
        "JWKS_URL",
        "AZURE_OPENAI_ENDPOINT",
        "COACH_PRICE_IN_PER_1K",
        "COACH_PRICE_OUT_PER_1K",
        "COACH_LLM_ENABLED",
    ):
        assert env[k].get("value"), k
    assert "secrets/database-url-app'" in _secret_url(env, "DATABASE_URL", urls)
    assert "secrets/aoai-key'" in _secret_url(env, "AZURE_OPENAI_API_KEY", urls)
    assert "database-url-admin" not in json.dumps(app)

    # Ruling R2: tid/oid must be supplied, never defaulted (empty disables the checks)
    params = main_template["parameters"]
    for p in ("allowedTid", "allowedOid"):
        assert "defaultValue" not in params[p], p
        assert params[p].get("minLength", 0) >= 1, p

    (mount,) = c["volumeMounts"]
    assert mount["mountPath"] == "/mnt/ledger"
    (vol,) = [v for v in tmpl["volumes"] if v["name"] == mount["volumeName"]]
    assert vol["storageType"] == "AzureFile"
    assert "apiFqdn" in main_template["outputs"]


def _jobs(main_resources) -> dict[str, dict]:
    jobs = {}
    for j in of_type(main_resources, "Microsoft.App/jobs"):
        m = re.search(r"(job-[a-z-]+)", j["name"])
        assert m, j["name"]
        jobs[m.group(1)] = deep_resolve(j, j.get("_vars", {}))
    return jobs


def test_jobs(main_resources):
    jobs = _jobs(main_resources)
    assert set(jobs) == JOB_NAMES
    for name, j in jobs.items():
        assert "parameters('idOpsId')" in json.dumps(j["identity"]["userAssignedIdentities"]), name
        cfg = j["properties"]["configuration"]
        urls = _assert_kv_secrets(cfg["secrets"], "idOpsId")
        (reg,) = cfg["registries"]
        assert reg["server"] == "ghcr.io" and reg["username"] == "deimos2002"
        assert "secrets/ghcr-pull-token'" in urls[reg["passwordSecretRef"]]
        (c,) = j["properties"]["template"]["containers"]
        assert c["image"] == "[parameters('jobImageDigest')]", name
        env = _env(c)
        assert "secrets/database-url-admin'" in _secret_url(env, "DATABASE_URL", urls)
        assert "database-url-app" not in json.dumps(j)
        url = env["BACKUP_ACCOUNT_URL"]["value"]
        assert "https://{0}.blob." in url and "backupAccountName" in url, name

    def cfg(n):
        return jobs[n]["properties"]["configuration"]

    assert cfg("job-backup")["triggerType"] == "Schedule"
    assert cfg("job-backup")["scheduleTriggerConfig"]["cronExpression"] == "30 0 * * *"
    assert cfg("job-retention-check")["triggerType"] == "Schedule"
    assert cfg("job-retention-check")["scheduleTriggerConfig"]["cronExpression"] == "0 6 * * 1"
    for n in ("job-migrate", "job-import", "job-restore"):
        assert cfg(n)["triggerType"] == "Manual", n
    for n in ("job-migrate", "job-restore"):
        assert cfg(n)["replicaRetryLimit"] == 0, n

    restore = jobs["job-restore"]["properties"]["template"]
    assert restore["containers"][0]["volumeMounts"][0]["mountPath"] == "/mnt/ledger"
    assert restore["volumes"][0]["storageType"] == "AzureFile"
    for n in JOB_NAMES - {"job-restore"}:
        assert "volumeMounts" not in jobs[n]["properties"]["template"]["containers"][0], n

    def command(n):
        return jobs[n]["properties"]["template"]["containers"][0]["command"]

    assert command("job-migrate") == ["alembic", "upgrade", "head"]
    assert command("job-backup")[:2] == ["sh", "-c"]
    assert "scripts/backup.py --blob" in command("job-backup")[2]
    assert "$BACKUP_ACCOUNT_URL" in command("job-backup")[2]
    assert command("job-retention-check")[:2] == ["sh", "-c"]
    assert "scripts/retention_check.py" in command("job-retention-check")[2]
    assert command("job-restore") == ["python", "scripts/restore.py"]


def test_alerts(main_resources):
    ag = "parameters('actionGroupId')"
    queries = of_type(main_resources, "Microsoft.Insights/scheduledQueryRules")
    metrics = of_type(main_resources, "Microsoft.Insights/metricAlerts")
    for r in queries:
        assert any(ag in a for a in r["properties"]["actions"]["actionGroups"]), r["name"]
    for r in metrics:
        assert any(ag in a["actionGroupId"] for a in r["properties"]["actions"]), r["name"]
    rules = {
        r["name"]: deep_resolve(r["properties"], r.get("_vars", {})) for r in queries + metrics
    }
    assert set(rules) == {
        "alert-job-failed",
        "alert-backup-missing",
        "alert-retention-breach",
        "alert-api-5xx",
        "alert-db-storage",
    }

    def query(name):
        (crit,) = rules[name]["criteria"]["allOf"]
        return crit

    assert "JobName_s" in query("alert-job-failed")["query"]
    backup = query("alert-backup-missing")
    assert rules["alert-backup-missing"]["windowSize"] == "PT26H"
    assert "job-backup" in backup["query"]
    assert backup["operator"] == "LessThan" and backup["threshold"] == 1
    assert "job-retention-check" in query("alert-retention-breach")["query"]

    api = query("alert-api-5xx")
    assert rules["alert-api-5xx"]["windowSize"] == "PT15M"
    assert api["metricName"] == "Requests"
    assert api["operator"] == "GreaterThanOrEqual" and api["threshold"] == 5
    assert api["dimensions"][0]["values"] == ["5xx"]

    db = query("alert-db-storage")
    assert db["metricName"] == "storage_percent"
    assert db["operator"] == "GreaterThan" and db["threshold"] == 80

    (budget,) = of_type(main_resources, "Microsoft.Consumption/budgets")
    bp = budget["properties"]
    assert bp["amount"] == 10 and bp["timeGrain"] == "Monthly"
    notes = bp["notifications"].values()
    assert sorted(n["threshold"] for n in notes) == [50, 90]
    for n in notes:
        assert n["enabled"] is True
        assert any(ag in g for g in n["contactGroups"])


def test_all_italynorth(main_template, main_resources):
    assert main_template["parameters"]["location"]["defaultValue"] == "italynorth"
    global_ok = {
        "microsoft.network/privatednszones",
        "microsoft.network/privatednszones/virtualnetworklinks",
        "microsoft.insights/metricalerts",
    }
    located = [
        r
        for r in main_resources
        if "location" in r and r["type"] != "Microsoft.Resources/deployments"
    ]
    assert located
    for r in located:
        if r["location"] == "global":
            assert r["type"].lower() in global_ok, r["type"]
        else:
            assert r["location"] == "[parameters('location')]", r["type"]


def test_prod_params_build():
    proc = subprocess.run(
        [AZ, "bicep", "build-params", "--file", str(PROD_PARAMS), "--stdout"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    params = json.loads(json.loads(proc.stdout)["parametersJson"])["parameters"]
    for p in ("allowedTid", "allowedOid"):
        assert re.fullmatch(r"[0-9a-f-]{36}", params[p]["value"]), p
    assert params["entraAudience"]["value"] == "00000000-0000-0000-0000-000000000004"  # R15: v2 aud is the API client id


def test_budget_start_date_pinned(main_template):
    p = main_template["parameters"]["budgetStartDate"]
    assert "defaultValue" not in p
    assert "utcNow" not in json.dumps(main_template)
    proc = subprocess.run(
        [AZ, "bicep", "build-params", "--file", str(PROD_PARAMS), "--stdout"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    params = json.loads(json.loads(proc.stdout)["parametersJson"])["parameters"]
    assert re.fullmatch(r"\d{4}-\d{2}-01", params["budgetStartDate"]["value"])


def test_key_vault_allows_template_get_secret(bootstrap_resources):
    # main.bicep reads secrets with getSecret, which needs this flag.
    (kv,) = of_type(bootstrap_resources, "Microsoft.KeyVault/vaults")
    assert kv["properties"]["enabledForTemplateDeployment"] is True
