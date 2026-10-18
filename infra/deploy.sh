#!/usr/bin/env bash
# Deploy Azure Recall to an existing resource group, keyless (managed identity everywhere).
# Creates: App Service (Linux, Python), Cosmos DB serverless, Azure OpenAI (gpt-5-mini), Application Insights.
# usage: infra/deploy.sh <resource-group> [suffix] [location]
set -euo pipefail
RG=$1; S=${2:-$RANDOM}; L=${3:-swedencentral}
APP=recall-$S; COSMOS=recall-cosmos-$S; AOAI=recall-aoai-$S; AI=recall-ai-$S; PLAN=recall-plan-$S
cd "$(dirname "$0")/.."
exists() { az resource show -g "$RG" -n "$1" --resource-type "$2" -o none 2>/dev/null; }

exists "$AOAI" Microsoft.CognitiveServices/accounts || az cognitiveservices account create -n "$AOAI" -g "$RG" -l "$L" --kind OpenAI --sku S0 --custom-domain "$AOAI" --yes -o none
az cognitiveservices account deployment show -n "$AOAI" -g "$RG" --deployment-name gpt-5-mini -o none 2>/dev/null || \
  az cognitiveservices account deployment create -n "$AOAI" -g "$RG" --deployment-name gpt-5-mini --model-name gpt-5-mini \
    --model-version 2025-08-07 --model-format OpenAI --sku-name GlobalStandard --sku-capacity 50 -o none
exists "$COSMOS" Microsoft.DocumentDB/databaseAccounts || az cosmosdb create -n "$COSMOS" -g "$RG" --locations regionName="$L" --capabilities EnableServerless -o none
az cosmosdb sql database create -a "$COSMOS" -g "$RG" -n recall -o none 2>/dev/null || true
az cosmosdb sql container create -a "$COSMOS" -g "$RG" -d recall -n items -p /kind -o none 2>/dev/null || true
exists "$AI" Microsoft.Insights/components || az monitor app-insights component create -a "$AI" -g "$RG" -l "$L" -o none
exists "$PLAN" Microsoft.Web/serverfarms || az appservice plan create -n "$PLAN" -g "$RG" -l "$L" --is-linux --sku B1 -o none
exists "$APP" Microsoft.Web/sites || az webapp create -n "$APP" -g "$RG" -p "$PLAN" --runtime "PYTHON:3.12" -o none

# identity + data-plane roles (no keys or connection strings in app settings)
PID=$(az webapp identity assign -n "$APP" -g "$RG" --query principalId -o tsv)
ME=$(az ad signed-in-user show --query id -o tsv)
for P in "$PID" "$ME"; do  # Cosmos DB Built-in Data Contributor
  az cosmosdb sql role assignment create -a "$COSMOS" -g "$RG" --scope / --principal-id "$P" \
    --role-definition-id 00000000-0000-0000-0000-000000000002 -o none 2>/dev/null || true
done
az role assignment create --assignee-object-id "$PID" --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" --scope "$(az cognitiveservices account show -n "$AOAI" -g "$RG" --query id -o tsv)" -o none || true

COSMOS_EP=$(az cosmosdb show -n "$COSMOS" -g "$RG" --query documentEndpoint -o tsv)
AOAI_EP=$(az cognitiveservices account show -n "$AOAI" -g "$RG" --query properties.endpoint -o tsv)
AI_CS=$(az monitor app-insights component show -a "$AI" -g "$RG" --query connectionString -o tsv)
az webapp config appsettings set -n "$APP" -g "$RG" -o none --settings \
  COSMOS_ENDPOINT="$COSMOS_EP" AZURE_OPENAI_ENDPOINT="$AOAI_EP" AZURE_OPENAI_DEPLOYMENT=gpt-5-mini \
  APPLICATIONINSIGHTS_CONNECTION_STRING="$AI_CS" AUTH_MODE="${AUTH_MODE:-demo}" SCM_DO_BUILD_DURING_DEPLOYMENT=true
az webapp config set -n "$APP" -g "$RG" -o none \
  --startup-file "python -m uvicorn app:app --host 0.0.0.0 --port 8000"

# seed Cosmos DB from this machine (uses your az login via DefaultAzureCredential)
COSMOS_ENDPOINT="$COSMOS_EP" python seed.py || echo "seed skipped (role assignment may take a few minutes to apply; rerun seed.py)"

rm -f /tmp/recall.zip && zip -qr /tmp/recall.zip app.py seed.py requirements.txt static kb -x '*/__pycache__/*'
az webapp deploy -n "$APP" -g "$RG" --src-path /tmp/recall.zip --type zip -o none
echo "https://$(az webapp show -n "$APP" -g "$RG" --query defaultHostName -o tsv)"
