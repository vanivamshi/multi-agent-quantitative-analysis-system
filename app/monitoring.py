"""Logging + Azure Monitor (Application Insights) via OpenTelemetry."""
import logging

from app.config import Settings

logger = logging.getLogger("quant.monitoring")


def setup_monitoring(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # Azure SDK logs every HTTP request/response at INFO; too noisy.
    logging.getLogger("azure").setLevel(logging.WARNING)

    if not settings.applicationinsights_connection_string:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set; Azure Monitor disabled")
        return

    from azure.monitor.opentelemetry import configure_azure_monitor

    # Exports requests (FastAPI auto-instrumentation), dependencies (HTTP, psycopg),
    # custom spans and logs from the 'quant.*' loggers to Application Insights.
    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string,
        logger_name="quant",
    )
    logger.info("Azure Monitor OpenTelemetry configured")
