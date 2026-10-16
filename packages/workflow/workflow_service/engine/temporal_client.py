from temporalio.client import Client

from workflow_service.core.config import app_settings

_client: Client | None = None


async def get_temporal_client() -> Client:
    global _client
    if _client is None:
        _client = await Client.connect(app_settings.TEMPORAL_ADDRESS)
    return _client
