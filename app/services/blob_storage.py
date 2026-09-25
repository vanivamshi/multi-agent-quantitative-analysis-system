"""Azure Blob Storage: stores final markdown reports in the 'reports' container."""
import logging
from datetime import datetime, timezone

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings

from app.config import Settings

logger = logging.getLogger("quant.blob")


class ReportStorage:
    def __init__(self, settings: Settings):
        if settings.azure_storage_connection_string:
            client = BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)
        elif settings.azure_storage_account_url:
            from azure.identity import DefaultAzureCredential

            client = BlobServiceClient(settings.azure_storage_account_url, credential=DefaultAzureCredential())
        else:
            raise RuntimeError(
                "Set AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_URL for report storage."
            )
        self.container = client.get_container_client(settings.azure_storage_container)

    def ensure_container(self) -> None:
        try:
            self.container.create_container()
            logger.info("Created blob container '%s'", self.container.container_name)
        except ResourceExistsError:
            pass

    @staticmethod
    def build_blob_name(ticker: str, request_id: str) -> str:
        now = datetime.now(timezone.utc)
        return f"{ticker}/{now:%Y/%m/%d}/{ticker}_{now:%Y%m%dT%H%M%SZ}_{request_id[:8]}.md"

    def upload_report(self, blob_name: str, content: str) -> str:
        blob = self.container.upload_blob(
            name=blob_name,
            data=content.encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="text/markdown; charset=utf-8"),
        )
        logger.info("Uploaded report to blob '%s'", blob_name)
        return blob.url

    def download_report(self, blob_name: str) -> str:
        return self.container.download_blob(blob_name).readall().decode("utf-8")
