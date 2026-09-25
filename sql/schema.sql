-- The API creates this automatically on startup; kept here for reference / manual setup.
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

-- Handy monitoring queries
-- Success rate & latency per day:
-- SELECT date_trunc('day', created_at) d, count(*) runs,
--        avg((status='COMPLETED')::int) success_rate, avg(duration_seconds) avg_s
-- FROM reports_log GROUP BY 1 ORDER BY 1 DESC;
