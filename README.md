# Multi-Agent Quantitative Analysis System

CrewAI multi-agent equity research, served by FastAPI + Streamlit, deployed and monitored on Azure.

```
Streamlit ──HTTP──▶ FastAPI POST /analyze ──triggers──▶ CrewAI Orchestrator (app/crew.py)
                                                          │
                     ┌────────────────────────────────────┼──────────────────────────┐
                     ▼                                    ▼                          ▼
          Quant Analyst Agent ──financial context──▶ Strategist Agent      Azure Blob Storage
          (Yahoo Finance tool)                      (Firecrawl search/      container "reports" (.md)
                                                      scrape)                         +
                                                                            Azure PostgreSQL Flexible
                                                                            Server: table reports_log
                                          Azure Monitor / Application Insights (traces, logs, metrics)
```

## Project layout

| Path | Purpose |
|---|---|
| `app/crew.py` | CrewAI orchestrator: Quant Analyst → Strategist (sequential, `context=[quant_task]`) |
| `app/tools/yahoo_finance_tool.py` | Returns, CAGR, volatility, Sharpe/Sortino, drawdown, VaR/CVaR, beta, SMA/RSI/MACD, fundamentals |
| `app/tools/firecrawl_tool.py` | Firecrawl web search + page scrape tools |
| `app/main.py` | FastAPI: `POST /analyze`, `GET /analyze/{id}`, `GET /reports`, `GET /reports/{id}/content`, `GET /health` |
| `app/services/blob_storage.py` | Uploads reports to Azure Blob (`reports/<TICKER>/<yyyy>/<mm>/<dd>/…md`) |
| `app/services/database.py` | `reports_log` table on Azure PostgreSQL (status, blob URL, duration, errors) |
| `app/monitoring.py` | Azure Monitor OpenTelemetry (Application Insights) |
| `frontend/streamlit_app.py` | Dashboard: run analyses, live status, report viewer/download, history |
| `deploy/azure_deploy.sh` | Provisions everything and deploys to Azure Container Apps |
| `deploy/monitoring_queries.kql` | Application Insights queries / alert examples |

## How a request flows

1. Streamlit calls `POST /analyze {"ticker": "NVDA", "horizon": "..."}`. The API adds a `QUEUED` row to `reports_log` and returns `202` with a `request_id` straight away.
2. A background job marks the row `RUNNING` and runs the crew:
   - **Quant Analyst** calls the Yahoo Finance tool and writes a metrics brief.
   - **Strategist** gets that brief as context, researches news with Firecrawl and writes the final report.
3. The report is uploaded to Blob, and the row is marked `COMPLETED` with the blob URL, size and duration (or `FAILED` with the error).
4. Streamlit polls `GET /analyze/{request_id}` and renders the markdown.

Runs are asynchronous because a crew run often takes longer than the Azure ingress request timeout (about 4 minutes).

## Run locally

```bash
cp .env.example .env        # set ANTHROPIC_API_KEY and FIRECRAWL_API_KEY
docker compose up --build   # Postgres + Azurite (Blob emulator) + API + UI
```
- Dashboard: http://localhost:8501
- API docs: http://localhost:8000/docs

Without Docker (you'll need your own Postgres and a Blob connection string in `.env`):
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-ui.txt
uvicorn app.main:app --reload
streamlit run frontend/streamlit_app.py
```

## Deploy to Azure

```bash
az login
export ANTHROPIC_API_KEY=... FIRECRAWL_API_KEY=... PG_ADMIN_PASSWORD='Str0ng!Passw0rd'
./deploy/azure_deploy.sh
```
The script creates:
- a resource group
- Azure Container Registry (it builds both images there)
- a Storage Account with the `reports` container
- PostgreSQL Flexible Server (Burstable B1ms) and the `quantdb` database
- a Log Analytics workspace and Application Insights
- a Container Apps environment with two apps:
  - **API**: 1 replica; secrets are stored as Container Apps secrets
  - **UI**: scales to zero

The API creates the `reports_log` table on startup (see also `sql/schema.sql`).

## Monitoring

When `APPLICATIONINSIGHTS_CONNECTION_STRING` is set, the API sends the following to Application Insights:
- **Requests**: FastAPI auto-instrumentation
- **Dependencies**: outbound HTTP (LLM, Yahoo, Firecrawl, Blob) and Postgres calls
- **Custom spans**: `crew.analysis` (with ticker, model and duration attributes) and `blob.upload_report`
- **Logs and exceptions** from the `quant.*` loggers

Container stdout also goes to Log Analytics. `reports_log` is a business-level audit trail, and the dashboard's History tab shows success rate and average duration.

## Configuration

Everything is set through environment variables; see `.env.example`.
- To switch LLMs, change `LLM_MODEL`, e.g. to `azure/<deployment>` for Azure OpenAI, and set that provider's key.
- To use Managed Identity for Blob instead of a connection string, set `AZURE_STORAGE_ACCOUNT_URL`.

## Notes / production hardening

- Background jobs run in-process, so if the API restarts mid-run, that row stays `RUNNING`. For durable jobs at scale, move to Azure Service Bus / Storage Queues with a worker container.
- Keep the API at 1 replica and 1 worker, or add a queue. `MAX_CONCURRENT_ANALYSES` limits parallel crews per process.
- For production, put secrets in Key Vault, restrict Postgres to a VNet or private endpoint, and add auth to the API (e.g. Container Apps Easy Auth).
- Reports are for research only, not financial advice.
