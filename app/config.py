"""Central configuration loaded from environment variables / .env file."""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Export .env into os.environ so provider SDKs (e.g. ANTHROPIC_API_KEY) can see it.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM (CrewAI model string, provider/model) ---
    llm_model: str = "anthropic/claude-sonnet-5"
    llm_temperature: float = 0.2

    # --- Tools ---
    firecrawl_api_key: str = ""
    firecrawl_base_url: str = "https://api.firecrawl.dev/v1"
    risk_free_rate: float = 0.04
    benchmark_ticker: str = "^GSPC"

    # --- Azure Blob Storage ---
    # Either a connection string, or an account URL (auth via Managed Identity / DefaultAzureCredential).
    azure_storage_connection_string: str = ""
    azure_storage_account_url: str = ""
    azure_storage_container: str = "reports"

    # --- Azure PostgreSQL Flexible Server ---
    # e.g. postgresql://user:pass@myserver.postgres.database.azure.com:5432/quantdb?sslmode=require
    database_url: str = "postgresql://postgres:postgres@localhost:5432/quantdb"

    # --- Monitoring ---
    applicationinsights_connection_string: str = ""
    log_level: str = "INFO"

    # --- API ---
    max_concurrent_analyses: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
