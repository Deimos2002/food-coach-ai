# Setting up Azure OpenAI for the coach

The weekly coach feature (stage 4) calls Azure OpenAI through `backend/app/coach/llm.py`.
No other code path, and no test, is allowed to call Azure directly. Follow these steps to
provision the resource and wire it into your local `.env`.

## 0. Check your allowed regions (Azure for Students)

Azure for Students subscriptions are restricted to a fixed list of regions by policy.
Before creating anything, find out which regions you are actually allowed to deploy to:

```
az policy assignment list --query "[].{name:displayName, regions:parameters.listOfAllowedLocations.value}"
```

A typical student subscription allows a set like `italynorth`, `polandcentral`,
`austriaeast`, `denmarkeast` and `belgiumcentral`. Your list may differ — always check
with the command above rather than assuming.

## 1. Create an Azure OpenAI resource

1. Sign in with `az login` (or the [Azure portal](https://portal.azure.com)) using your
   Azure student account.
2. From the regions returned in step 0, pick one that both (a) is in the EU and (b) has
   Azure OpenAI available. **Italy North** or **Poland Central** are good choices when
   they appear in your allowed list.
3. Create a resource group and the Azure OpenAI resource (CLI shown; the portal works
   too):

   ```
   az group create --name rg-foodcoach --location italynorth

   az cognitiveservices account create \
     --name foodcoach-openai \
     --resource-group rg-foodcoach \
     --location italynorth \
     --kind OpenAI \
     --sku S0 \
     --custom-domain foodcoach-openai
   ```

   The `--custom-domain` value becomes part of your endpoint hostname and must be
   globally unique — pick something specific to your project.

## 2. Deploy a model

Recommended model: **`gpt-4.1-mini`** (version `2025-04-14`), deployed on the
**`DataZoneStandard`** SKU, which keeps processing within the EU data zone without
requiring every allowed region to individually host the model.

```
az cognitiveservices account deployment create \
  --name foodcoach-openai \
  --resource-group rg-foodcoach \
  --deployment-name coach-mini \
  --model-name gpt-4.1-mini \
  --model-version 2025-04-14 \
  --model-format OpenAI \
  --sku-name DataZoneStandard \
  --sku-capacity 10
```

Give the deployment a short, memorable name (e.g. `coach-mini`, as above) — you will
need this exact name in `.env`.

## 3. Put secrets in `backend/.env`

`backend/.env` is listed in `.gitignore` and is never committed. Secrets belong only
there — never in code, never in `docs/`, never in a committed `.env.example`.

Copy `backend/.env.example` to `backend/.env` if you have not already, then add:

```
AZURE_OPENAI_ENDPOINT=https://<your-resource-name>.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_API_KEY=<your-key-from-the-azure-portal>
```

Fetch the endpoint with the CLI (safe to print — it contains no secret):

```
az cognitiveservices account show \
  --name foodcoach-openai \
  --resource-group rg-foodcoach \
  --query "properties.endpoint" -o tsv
```

Fetch the key and append it straight to `.env` without ever printing it to the
terminal:

```
az cognitiveservices account keys list \
  --name foodcoach-openai \
  --resource-group rg-foodcoach \
  --query "key1" -o tsv >> backend/.env.key.tmp
```

then open `backend/.env.key.tmp`, copy the value into `AZURE_OPENAI_API_KEY=` in
`backend/.env`, and delete the temp file. The deployment name is whatever you chose in
step 2. The API version above is the one this codebase is tested against; only change
it if you have verified the new version against the test suite.

## 4. Set the € price per 1k tokens

Azure OpenAI pricing is published per 1,000 tokens, separately for input and output,
and varies by region and model. Look up the current price for your model and region
(Azure pricing page, or the price shown in AI Foundry when you deployed), convert to
EUR if quoted in another currency, and set:

```
COACH_PRICE_IN_PER_1K=<price per 1k input tokens, in EUR>
COACH_PRICE_OUT_PER_1K=<price per 1k output tokens, in EUR>
```

These feed `cost_eur()`, which is used to enforce `COACH_MONTHLY_CAP_EUR` (default
`2`). Leaving them at `0` means spend is never tracked — set them correctly before
enabling live calls.

## Notes

- `COACH_LOG_TEXT` defaults to `false`. Leave it that way unless you are debugging and
  have considered the privacy implications of storing prompt/response text.
- Tests never call Azure. They use `FakeLlmClient` or mock the HTTP layer with `respx`.
  Only the `eval --source llm` tool without `--replay` is allowed to reach Azure.
