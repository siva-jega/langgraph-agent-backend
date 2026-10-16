from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.tools.providers.shopify.tools import (
    _extract_client_id_from_context,
    resolve_identity,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

WORKSPACE_ID = "ws-1"
CLIENT_ID = "shopify-client-abc"


def _config(workflow_context: dict | None = None) -> dict:
    """Build a LangChain RunnableConfig."""
    return {
        "configurable": {
            "workspace_id": WORKSPACE_ID,
            "workflow_context": workflow_context or {},
        }
    }


def _pixel_context(client_id: str = CLIENT_ID) -> dict:
    """Simulate a pixel trigger node in the workflow context."""
    return {
        "trigger_node": {
            "output": {
                "data": {
                    "clientId": client_id,
                    "sessionId": "sess-abc",
                }
            }
        }
    }


def _mock_http_client(response_payload: dict, raise_exc: Exception | None = None):
    """Return a context-manager mock for httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.json.return_value = response_payload
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    if raise_exc:
        mock_client.get = AsyncMock(side_effect=raise_exc)
    else:
        mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


def _mock_settings():
    """Return a mock project_settings object with required fields."""
    s = MagicMock()
    s.NESTJS_URL = "http://localhost:3000"
    s.INTERNAL_SECRET = "test-secret"
    return s


# ── _extract_client_id_from_context ──────────────────────────────────────────

def test_extract_client_id_returns_empty_string_for_empty_context() -> None:
    assert _extract_client_id_from_context({}) == ""


def test_extract_client_id_skips_dunder_keys() -> None:
    ctx = {"__meta": {"output": {"data": {"clientId": "should-skip"}}}}
    assert _extract_client_id_from_context(ctx) == ""


def test_extract_client_id_reads_from_trigger_node_output() -> None:
    assert _extract_client_id_from_context(_pixel_context("my-client")) == "my-client"


# ── resolve_identity tool ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_identity_found_true_adds_branch_true() -> None:
    payload = {
        "found": True,
        "sessionId": "sess-abc",
        "customerId": "cust-1",
        "email": "user@example.com",
    }
    mock_client = _mock_http_client(payload)

    with patch("common.tools.providers.shopify.tools.httpx.AsyncClient", return_value=mock_client):
        with patch("common.project_settings.project_settings", return_value=_mock_settings()):
            result = await resolve_identity.ainvoke({}, config=_config(_pixel_context()))

    assert result["found"] is True
    assert result["branch"] == "true"
    assert result["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_resolve_identity_found_false_adds_branch_false() -> None:
    mock_client = _mock_http_client({"found": False})

    with patch("common.tools.providers.shopify.tools.httpx.AsyncClient", return_value=mock_client):
        with patch("common.project_settings.project_settings", return_value=_mock_settings()):
            result = await resolve_identity.ainvoke({}, config=_config())

    assert result["found"] is False
    assert result["branch"] == "false"


@pytest.mark.asyncio
async def test_resolve_identity_returns_error_dict_on_http_failure() -> None:
    mock_client = _mock_http_client({}, raise_exc=Exception("connection refused"))

    with patch("common.tools.providers.shopify.tools.httpx.AsyncClient", return_value=mock_client):
        with patch("common.project_settings.project_settings", return_value=_mock_settings()):
            result = await resolve_identity.ainvoke({}, config=_config())

    assert result["found"] is False
    assert result["branch"] == "false"
    assert "error" in result


@pytest.mark.asyncio
async def test_resolve_identity_passes_client_id_from_context() -> None:
    mock_client = _mock_http_client({"found": False})

    with patch("common.tools.providers.shopify.tools.httpx.AsyncClient", return_value=mock_client):
        with patch("common.project_settings.project_settings", return_value=_mock_settings()):
            await resolve_identity.ainvoke({}, config=_config(_pixel_context("ctx-client-123")))

    call_kwargs = mock_client.get.call_args.kwargs
    assert call_kwargs["params"]["shopifyClientId"] == "ctx-client-123"
    assert call_kwargs["params"]["workspaceId"] == WORKSPACE_ID
