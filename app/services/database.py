"""Azure PostgreSQL Flexible Server: run logs + metadata in the reports_log table."""
import logging
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger("quant.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reports_log (
    id                BIGSERIAL PRIMARY KEY,
    request_id        UUID UNIQUE NOT NULL,
    ticker            VARCHAR(20) NOT NULL,
    horizon           VARCHAR(64),
    status            VARCHAR(16) NOT NULL,          -- QUEUED | RUNNING | COMPLETED | FAILED
    llm_model         VARCHAR(128),
    blob_name         TEXT,
    blob_url          TEXT,
    report_size_bytes INTEGER,
    duration_seconds  DOUBLE PRECISION,
    error_message     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_reports_log_ticker_created ON reports_log (ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_log_created ON reports_log (created_at DESC);
"""


class ReportLogRepository:
    def __init__(self, database_url: str):
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    def close(self) -> None:
        self.pool.close()

    def init_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(SCHEMA_SQL)
        logger.info("reports_log schema ensured")

    def ping(self) -> bool:
        with self.pool.connection() as conn:
            conn.execute("SELECT 1")
        return True

    def create(self, request_id: str, ticker: str, horizon: str, llm_model: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO reports_log (request_id, ticker, horizon, status, llm_model)
                   VALUES (%s, %s, %s, 'QUEUED', %s)""",
                (request_id, ticker, horizon, llm_model),
            )

    def mark_running(self, request_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE reports_log SET status='RUNNING', started_at=now() WHERE request_id=%s",
                (request_id,),
            )

    def mark_completed(
        self, request_id: str, blob_name: str, blob_url: str, size_bytes: int, duration: float
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE reports_log
                   SET status='COMPLETED', blob_name=%s, blob_url=%s, report_size_bytes=%s,
                       duration_seconds=%s, completed_at=now()
                   WHERE request_id=%s""",
                (blob_name, blob_url, size_bytes, duration, request_id),
            )

    def mark_failed(self, request_id: str, error: str, duration: float) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """UPDATE reports_log
                   SET status='FAILED', error_message=%s, duration_seconds=%s, completed_at=now()
                   WHERE request_id=%s""",
                (error[:4000], duration, request_id),
            )

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT * FROM reports_log WHERE request_id=%s", (request_id,)
            ).fetchone()

    def list(self, limit: int = 50, ticker: str | None = None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            if ticker:
                cur = conn.execute(
                    "SELECT * FROM reports_log WHERE ticker=%s ORDER BY created_at DESC LIMIT %s",
                    (ticker.upper(), limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM reports_log ORDER BY created_at DESC LIMIT %s", (limit,)
                )
            return cur.fetchall()
