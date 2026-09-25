"""FastAPI server: POST /analyze triggers the CrewAI analysis as a background job."""
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from opentelemetry import trace
from pydantic import BaseModel, Field

from app.config import get_settings
from app.monitoring import setup_monitoring

settings = get_settings()
setup_monitoring(settings)  # must run before FastAPI app is created for auto-instrumentation

from app.crew import run_analysis  # noqa: E402
from app.services.blob_storage import ReportStorage  # noqa: E402
from app.services.database import ReportLogRepository  # noqa: E402

logger = logging.getLogger("quant.api")
tracer = trace.get_tracer("quant.api")

_slots = threading.BoundedSemaphore(settings.max_concurrent_analyses)
state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    repo = ReportLogRepository(settings.database_url)
    repo.init_schema()
    storage = ReportStorage(settings)
    storage.ensure_container()
    state["repo"], state["storage"] = repo, storage
    logger.info("API started (model=%s)", settings.llm_model)
    yield
    repo.close()


app = FastAPI(
    title="Multi-Agent Quantitative Analysis API",
    description="CrewAI Quant + Strategist agents, reports in Azure Blob, logs in Azure PostgreSQL.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------- Schemas ----------
class AnalyzeRequest(BaseModel):
    ticker: str = Field(
        ..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-\^=]+$", examples=["AAPL"]
    )
    horizon: str = Field("medium-term (6-12 months)", max_length=64)


class AnalyzeAccepted(BaseModel):
    request_id: str
    ticker: str
    status: str
    status_url: str


class ReportLog(BaseModel):
    request_id: uuid.UUID
    ticker: str
    horizon: str | None = None
    status: str
    llm_model: str | None = None
    blob_name: str | None = None
    blob_url: str | None = None
    report_size_bytes: int | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ReportStatus(ReportLog):
    report_markdown: str | None = None


# ---------- Background job ----------
def execute_analysis(request_id: str, ticker: str, horizon: str) -> None:
    repo: ReportLogRepository = state["repo"]
    storage: ReportStorage = state["storage"]

    with _slots, tracer.start_as_current_span("crew.analysis") as span:
        span.set_attribute("analysis.request_id", request_id)
        span.set_attribute("analysis.ticker", ticker)
        span.set_attribute("analysis.llm_model", settings.llm_model)
        repo.mark_running(request_id)
        started = time.perf_counter()
        try:
            result = run_analysis(ticker, horizon, request_id)
            blob_name = storage.build_blob_name(ticker, request_id)
            with tracer.start_as_current_span("blob.upload_report"):
                blob_url = storage.upload_report(blob_name, result.report_markdown)
            duration = time.perf_counter() - started
            size = len(result.report_markdown.encode("utf-8"))
            repo.mark_completed(request_id, blob_name, blob_url, size, duration)
            span.set_attribute("analysis.duration_seconds", duration)
            logger.info(
                "Analysis completed ticker=%s request_id=%s duration=%.1fs",
                ticker, request_id, duration,
                extra={"ticker": ticker, "request_id": request_id, "duration_seconds": duration},
            )
        except Exception as exc:
            duration = time.perf_counter() - started
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            logger.exception("Analysis failed ticker=%s request_id=%s", ticker, request_id)
            repo.mark_failed(request_id, f"{type(exc).__name__}: {exc}", duration)


# ---------- Routes ----------
@app.get("/health")
def health() -> dict:
    try:
        db_ok = state["repo"].ping()
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "model": settings.llm_model}


@app.post("/analyze", response_model=AnalyzeAccepted, status_code=status.HTTP_202_ACCEPTED)
def analyze(req: AnalyzeRequest, background: BackgroundTasks) -> AnalyzeAccepted:
    ticker = req.ticker.upper().strip()
    request_id = str(uuid.uuid4())
    state["repo"].create(request_id, ticker, req.horizon, settings.llm_model)
    background.add_task(execute_analysis, request_id, ticker, req.horizon)
    logger.info("Analysis queued ticker=%s request_id=%s", ticker, request_id)
    return AnalyzeAccepted(
        request_id=request_id, ticker=ticker, status="QUEUED", status_url=f"/analyze/{request_id}"
    )


@app.get("/analyze/{request_id}", response_model=ReportStatus)
def analysis_status(request_id: uuid.UUID, include_report: bool = True) -> ReportStatus:
    row = state["repo"].get(str(request_id))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown request_id")
    result = ReportStatus(**row)
    if include_report and row["status"] == "COMPLETED" and row["blob_name"]:
        result.report_markdown = state["storage"].download_report(row["blob_name"])
    return result


@app.get("/reports", response_model=list[ReportLog])
def list_reports(
    limit: int = Query(50, ge=1, le=500), ticker: str | None = None
) -> list[ReportLog]:
    return [ReportLog(**row) for row in state["repo"].list(limit=limit, ticker=ticker)]


@app.get("/reports/{request_id}/content", response_class=PlainTextResponse)
def report_content(request_id: uuid.UUID) -> str:
    row = state["repo"].get(str(request_id))
    if not row or row["status"] != "COMPLETED":
        raise HTTPException(status_code=404, detail="Report not found or not completed")
    return state["storage"].download_report(row["blob_name"])
