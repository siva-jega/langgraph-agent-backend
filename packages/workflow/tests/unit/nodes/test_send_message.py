"""Unit tests for SendMessageStepExecutor."""

from __future__ import annotations

import pytest
import respx
import httpx

from workflow_service.engine.nodes.send_message import (
    NodeDeliveryError,
    SendMessageStepExecutor,
    _extract_session_id,
)


NESTJS_BASE = "http://nestjs-test"


@pytest.fixture
def http_mock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


# ── GMAIL channel ─────────────────────────────────────────────────────────────


async def test_gmail_calls_email_service(fake_email) -> None:
    await SendMessageStepExecutor().execute(
        {
            "channel": "GMAIL",
            "to": "user@example.com",
            "message": "Hello\nWorld",
            "subject": "Test Subject",
        },
        {},
    )
    assert len(fake_email) == 1
    assert fake_email[0]["to"] == "user@example.com"
    assert fake_email[0]["subject"] == "Test Subject"


async def test_gmail_newlines_converted_to_br(fake_email) -> None:
    await SendMessageStepExecutor().execute(
        {"channel": "GMAIL", "to": "a@b.com", "message": "line1\nline2\nline3"},
        {},
    )
    assert fake_email[0]["body"] == "line1<br>line2<br>line3"


async def test_gmail_returns_message_id_and_sent_at(fake_email) -> None:
    result = await SendMessageStepExecutor().execute(
        {"channel": "GMAIL", "to": "a@b.com", "message": "hi"},
        {},
    )
    assert result["messageId"] == "test-msg-id"
    assert "sentAt" in result


# ── META channel ──────────────────────────────────────────────────────────────


async def test_meta_posts_to_internal_endpoint(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/meta/send").mock(
        return_value=httpx.Response(200, json={"messageId": "meta-123"})
    )
    await SendMessageStepExecutor().execute(
        {"channel": "META", "to": "wa-id-123", "message": "Hello"},
        {},
    )
    assert route.called


async def test_meta_sends_internal_secret_header(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/meta/send").mock(
        return_value=httpx.Response(200, json={"messageId": "m1"})
    )
    await SendMessageStepExecutor().execute(
        {"channel": "META", "to": "r1", "message": "msg"},
        {},
    )
    assert route.calls.last.request.headers.get("x-internal-secret") == "test-secret"


async def test_meta_4xx_raises(http_mock) -> None:
    http_mock.post(f"{NESTJS_BASE}/internal/meta/send").mock(
        return_value=httpx.Response(400, json={"error": "bad"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await SendMessageStepExecutor().execute(
            {"channel": "META", "to": "r1", "message": "msg"},
            {},
        )


# ── WIDGET channel ────────────────────────────────────────────────────────────


async def test_widget_posts_to_internal_endpoint(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/widget/send-message").mock(
        return_value=httpx.Response(200, json={"chatId": "chat-1", "sentAt": "2024-01-01T00:00:00Z"})
    )
    await SendMessageStepExecutor().execute(
        {"channel": "WIDGET", "to": "session-abc", "message": "Hello"},
        {},
    )
    assert route.called


async def test_widget_uses_explicit_to_over_context_session(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/widget/send-message").mock(
        return_value=httpx.Response(200, json={"chatId": "c1", "sentAt": "now"})
    )
    ctx = {"__run__": {"inputData": {"sessionId": "ctx-session"}}}
    await SendMessageStepExecutor().execute(
        {"channel": "WIDGET", "to": "explicit-session", "message": "hi"},
        ctx,
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["sessionId"] == "explicit-session"


async def test_widget_falls_back_to_context_session_id(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/widget/send-message").mock(
        return_value=httpx.Response(200, json={"chatId": "c1", "sentAt": "now"})
    )
    ctx = {"__run__": {"inputData": {"sessionId": "ctx-session-id"}}}
    await SendMessageStepExecutor().execute(
        {"channel": "WIDGET", "message": "hi"},  # no 'to'
        ctx,
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["sessionId"] == "ctx-session-id"


async def test_widget_includes_cart_url_when_provided(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/widget/send-message").mock(
        return_value=httpx.Response(200, json={"chatId": "c1", "sentAt": "now"})
    )
    await SendMessageStepExecutor().execute(
        {"channel": "WIDGET", "to": "s1", "message": "hi", "cartUrl": "https://shop.com/cart"},
        {},
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["cartUrl"] == "https://shop.com/cart"


async def test_widget_4xx_raises(http_mock) -> None:
    http_mock.post(f"{NESTJS_BASE}/internal/widget/send-message").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await SendMessageStepExecutor().execute(
            {"channel": "WIDGET", "to": "s1", "message": "hi"},
            {},
        )


# ── WHATSAPP channel (template dispatch) ──────────────────────────────────────


def _dispatch_envelope(result: dict) -> dict:
    """user-backend wraps responses as { status, statusCode, result, version }."""
    return {"status": True, "statusCode": 200, "result": result, "version": "1.0"}


async def test_whatsapp_posts_to_dispatch_endpoint(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/meta/workflow/dispatch").mock(
        return_value=httpx.Response(
            200, json=_dispatch_envelope({"status": "sent", "messageId": "wamid.1"})
        )
    )
    result = await SendMessageStepExecutor().execute(
        {
            "channel": "WHATSAPP",
            "agentIntegrationAssetId": "binding-1",
            "sessionId": "sess-1",
            "data": {"item_count": 2, "cart_total": 1498},
            "workspaceId": "ws-1",
            "workflowRunId": "run-1",
            "nodeId": "send-1",
        },
        {},
    )
    assert route.called
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "workspaceId": "ws-1",
        "workflowRunId": "run-1",
        "nodeId": "send-1",
        "agentIntegrationAssetId": "binding-1",
        "agentIntegrationId": "",
        "sessionId": "sess-1",
        "data": {"item_count": 2, "cart_total": 1498},
    }
    # The wrapped envelope is unwrapped to the dispatch outcome.
    assert result["outcome"] == {"status": "sent", "messageId": "wamid.1"}
    assert result["messageId"] == "wamid.1"


async def test_whatsapp_sends_internal_secret_header(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/meta/workflow/dispatch").mock(
        return_value=httpx.Response(200, json=_dispatch_envelope({"status": "skipped", "reason": "X"}))
    )
    await SendMessageStepExecutor().execute(
        {"channel": "WHATSAPP", "agentIntegrationAssetId": "b1", "sessionId": "s1", "data": {}},
        {},
    )
    assert route.calls.last.request.headers.get("x-internal-secret") == "test-secret"


async def test_whatsapp_falls_back_to_context_session_id(http_mock) -> None:
    route = http_mock.post(f"{NESTJS_BASE}/internal/meta/workflow/dispatch").mock(
        return_value=httpx.Response(200, json=_dispatch_envelope({"status": "skipped", "reason": "X"}))
    )
    ctx = {"__run__": {"inputData": {"data": {"sessionId": "ctx-session"}}}}
    await SendMessageStepExecutor().execute(
        {"channel": "WHATSAPP", "agentIntegrationAssetId": "b1", "data": {}},  # no sessionId in config
        ctx,
    )
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["sessionId"] == "ctx-session"


async def test_whatsapp_returns_skip_outcome_without_raising(http_mock) -> None:
    http_mock.post(f"{NESTJS_BASE}/internal/meta/workflow/dispatch").mock(
        return_value=httpx.Response(
            200, json=_dispatch_envelope({"status": "skipped", "reason": "NO_RECIPIENT_PHONE"})
        )
    )
    result = await SendMessageStepExecutor().execute(
        {"channel": "WHATSAPP", "agentIntegrationAssetId": "b1", "sessionId": "s1", "data": {}},
        {},
    )
    assert result["outcome"]["status"] == "skipped"
    assert result["messageId"] == ""


async def test_whatsapp_accepted_returns_without_raising(http_mock) -> None:
    """delivered is None (pending at timeout) -> node completes as accepted."""
    http_mock.post(f"{NESTJS_BASE}/internal/meta/workflow/dispatch").mock(
        return_value=httpx.Response(
            200, json=_dispatch_envelope({"status": "sent", "messageId": "wamid.1"})
        )
    )
    result = await SendMessageStepExecutor().execute(
        {"channel": "WHATSAPP", "agentIntegrationAssetId": "b1", "sessionId": "s1", "data": {}},
        {},
    )
    assert result["status"] == "accepted"
    assert result["delivered"] is None


async def test_whatsapp_not_delivered_raises_delivery_error(http_mock) -> None:
    """delivered is False -> raise so the engine marks the node FAILED."""
    http_mock.post(f"{NESTJS_BASE}/internal/meta/workflow/dispatch").mock(
        return_value=httpx.Response(
            200,
            json=_dispatch_envelope(
                {
                    "status": "sent",
                    "messageId": "wamid.1",
                    "delivered": False,
                    "failureCode": 131049,
                    "failureReason": "not delivered to maintain healthy ecosystem engagement",
                }
            ),
        )
    )
    with pytest.raises(NodeDeliveryError) as exc_info:
        await SendMessageStepExecutor().execute(
            {"channel": "WHATSAPP", "agentIntegrationAssetId": "b1", "sessionId": "s1", "data": {}},
            {},
        )
    # The structured output rides along for the FAILED step.
    out = exc_info.value.output
    assert out["status"] == "failed"
    assert out["delivered"] is False
    assert out["failureCode"] == 131049
    assert out["messageId"] == "wamid.1"


async def test_whatsapp_4xx_raises(http_mock) -> None:
    http_mock.post(f"{NESTJS_BASE}/internal/meta/workflow/dispatch").mock(
        return_value=httpx.Response(401, json={"message": "Invalid or missing x-internal-secret header"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await SendMessageStepExecutor().execute(
            {"channel": "WHATSAPP", "agentIntegrationAssetId": "b1", "sessionId": "s1", "data": {}},
            {},
        )


# ── _extract_session_id helper ────────────────────────────────────────────────


def test_extract_session_id_from_app_trigger_data() -> None:
    ctx = {"__run__": {"inputData": {"data": {"sessionId": "app-session"}}}}
    assert _extract_session_id(ctx) == "app-session"


def test_extract_session_id_from_flat_input_data() -> None:
    ctx = {"__run__": {"inputData": {"sessionId": "flat-session"}}}
    assert _extract_session_id(ctx) == "flat-session"


def test_extract_session_id_app_trigger_takes_precedence() -> None:
    ctx = {
        "__run__": {
            "inputData": {
                "sessionId": "flat",
                "data": {"sessionId": "nested"},
            }
        }
    }
    assert _extract_session_id(ctx) == "nested"


def test_extract_session_id_falls_back_to_node_outputs() -> None:
    ctx = {
        "__run__": {"inputData": {}},
        "some-node": {"output": {"sessionId": "from-output"}},
    }
    assert _extract_session_id(ctx) == "from-output"


def test_extract_session_id_returns_empty_string_when_not_found() -> None:
    assert _extract_session_id({}) == ""
