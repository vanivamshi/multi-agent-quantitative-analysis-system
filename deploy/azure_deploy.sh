#!/usr/bin/env bash
# Provision Azure resources and deploy API + Streamlit to Azure Container Apps.
#
# Required env vars:  ANTHROPIC_API_KEY  FIRECRAWL_API_KEY  PG_ADMIN_PASSWORD
# Optional overrides: PREFIX LOCATION RG LLM_MODEL
#
# Usage: az login && ./deploy/azure_deploy.sh
set -euo pipefail

: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"
: "${FIRECRAWL_API_KEY:?set FIRECRAWL_API_KEY}"
: "${PG_ADMIN_PASSWORD:?set PG_ADMIN_PASSWORD (8+ chars, upper/lower/digit)}"

PREFIX="${PREFIX:-quantcrew}"
SUFFIX="${SUFFIX:-$(openssl rand -hex 3)}"   # keeps globally-unique names unique
LOCATION="${LOCATION:-eastus}"
RG="${RG:-rg-${PREFIX}}"
ACR="${PREFIX}acr${SUFFIX}"
STORAGE="${PREFIX}st${SUFFIX}"
PG_SERVER="${PREFIX}-pg-${SUFFIX}"
PG_ADMIN="${PG_ADMIN:-quantadmin}"
PG_DB="quantdb"
LAW="${PREFIX}-logs"
APPINSIGHTS="${PREFIX}-appi"
ACA_ENV="${PREFIX}-env"
API_APP="${PREFIX}-api"
UI_APP="${PREFIX}-ui"
LLM_MODEL="${LLM_MODEL:-anthropic/claude-sonnet-5}"
TAG="$(date +%Y%m%d%H%M%S)"

cd "$(dirname "$0")/.."

echo ">> Extensions & providers"
az extension add --name containerapp --upgrade -y >/dev/null
az extension add --name application-insights --upgrade -y >/dev/null
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.DBforPostgreSQL Microsoft.ContainerRegistry; do
  az provider register --namespace "$ns" >/dev/null
done

echo ">> Resource group: $RG"
az group create -n "$RG" -l "$LOCATION" -o none

echo ">> Container registry + images"
az acr create -n "$ACR" -g "$RG" --sku Basic --admin-enabled true -o none
az acr build -r "$ACR" -t "quant-api:$TAG" -f Dockerfile.api . -o none
az acr build -r "$ACR" -t "quant-ui:$TAG" -f Dockerfile.ui . -o none
ACR_SERVER=$(az acr show -n "$ACR" --query loginServer -o tsv)
ACR_USER=$(az acr credential show -n "$ACR" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR" --query "passwords[0].value" -o tsv)

echo ">> Blob storage (container: reports)"
az storage account create -n "$STORAGE" -g "$RG" -l "$LOCATION" --sku Standard_LRS \
  --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false -o none
STORAGE_CONN=$(az storage account show-connection-string -n "$STORAGE" -g "$RG" -o tsv)
az storage container create -n reports --connection-string "$STORAGE_CONN" -o none

echo ">> PostgreSQL Flexible Server (this takes a few minutes)"
az postgres flexible-server create -g "$RG" -n "$PG_SERVER" -l "$LOCATION" \
  --admin-user "$PG_ADMIN" --admin-password "$PG_ADMIN_PASSWORD" \
  --tier Burstable --sku-name Standard_B1ms --storage-size 32 --version 16 \
  --public-access 0.0.0.0 --yes -o none   # 0.0.0.0 = allow Azure services only
az postgres flexible-server db create -g "$RG" -s "$PG_SERVER" -d "$PG_DB" -o none
PG_PASS_ENC=$(python3 -c "import urllib.parse,os;print(urllib.parse.quote(os.environ['PG_ADMIN_PASSWORD'],safe=''))")
DATABASE_URL="postgresql://${PG_ADMIN}:${PG_PASS_ENC}@${PG_SERVER}.postgres.database.azure.com:5432/${PG_DB}?sslmode=require"

echo ">> Monitoring: Log Analytics + Application Insights"
az monitor log-analytics workspace create -g "$RG" -n "$LAW" -l "$LOCATION" -o none
LAW_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query customerId -o tsv)
LAW_KEY=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW" --query primarySharedKey -o tsv)
LAW_RES_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LAW" --query id -o tsv)
az monitor app-insights component create -a "$APPINSIGHTS" -g "$RG" -l "$LOCATION" \
  --workspace "$LAW_RES_ID" --application-type web -o none
APPI_CONN=$(az monitor app-insights component show -a "$APPINSIGHTS" -g "$RG" --query connectionString -o tsv)

echo ">> Container Apps environment"
az containerapp env create -n "$ACA_ENV" -g "$RG" -l "$LOCATION" \
  --logs-workspace-id "$LAW_ID" --logs-workspace-key "$LAW_KEY" -o none

echo ">> API container app"
# min-replicas 1: background jobs run in-process, so never scale to zero.
az containerapp create -n "$API_APP" -g "$RG" --environment "$ACA_ENV" \
  --image "$ACR_SERVER/quant-api:$TAG" \
  --registry-server "$ACR_SERVER" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
  --target-port 8000 --ingress external \
  --cpu 1.0 --memory 2.0Gi --min-replicas 1 --max-replicas 1 \
  --secrets \
    anthropic-key="$ANTHROPIC_API_KEY" \
    firecrawl-key="$FIRECRAWL_API_KEY" \
    storage-conn="$STORAGE_CONN" \
    database-url="$DATABASE_URL" \
    appi-conn="$APPI_CONN" \
  --env-vars \
    LLM_MODEL="$LLM_MODEL" \
    ANTHROPIC_API_KEY=secretref:anthropic-key \
    FIRECRAWL_API_KEY=secretref:firecrawl-key \
    AZURE_STORAGE_CONNECTION_STRING=secretref:storage-conn \
    AZURE_STORAGE_CONTAINER=reports \
    DATABASE_URL=secretref:database-url \
    APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appi-conn \
    OTEL_SERVICE_NAME=quant-api \
  -o none
API_FQDN=$(az containerapp show -n "$API_APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)

echo ">> Streamlit container app"
az containerapp create -n "$UI_APP" -g "$RG" --environment "$ACA_ENV" \
  --image "$ACR_SERVER/quant-ui:$TAG" \
  --registry-server "$ACR_SERVER" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
  --target-port 8501 --ingress external \
  --cpu 0.5 --memory 1.0Gi --min-replicas 0 --max-replicas 2 \
  --env-vars API_BASE_URL="https://${API_FQDN}" \
  -o none
UI_FQDN=$(az containerapp show -n "$UI_APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)

cat <<EOF

✅ Deployment complete
   Dashboard : https://${UI_FQDN}
   API docs  : https://${API_FQDN}/docs
   Storage   : ${STORAGE} (container: reports)
   Postgres  : ${PG_SERVER}.postgres.database.azure.com / ${PG_DB}
   App Insights: ${APPINSIGHTS}  (queries: deploy/monitoring_queries.kql)

Redeploy after code changes:
   az acr build -r $ACR -t quant-api:<tag> -f Dockerfile.api .
   az containerapp update -n $API_APP -g $RG --image $ACR_SERVER/quant-api:<tag>
EOF
