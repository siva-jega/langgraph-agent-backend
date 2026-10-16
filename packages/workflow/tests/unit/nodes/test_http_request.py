"""Unit tests for HttpRequestStepExecutor."""

from __future__ import annotations

import pytest
import respx
import httpx

from workflow_service.engine.nodes.http_request import HttpRequestStepExecutor, HttpStepError


@pytest.fixture
def http_mock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


async def test_get_returns_json_dict(http_mock) -> None:
    http_mock.get("https://api.example.com/data").mock(
        return_value=httpx.Response(200, json={"key": "value"})
    )
    result = await HttpRequestStepExecutor().execute(
        {"method": "GET", "url": "https://api.example.com/data"},
        {},
    )
    assert result == {"key": "value"}


async def test_post_with_dict_body_sends_json(http_mock) -> None:
    route = http_mock.post("https://api.example.com/items").mock(
        return_value=httpx.Response(201, json={"id": "new-1"})
    )
    await HttpRequestStepExecutor().execute(
        {
            "method": "POST",
            "url": "https://api.example.com/items",
            "bodyTemplate": {"name": "test"},
        },
        {},
    )
    assert route.called
    assert route.calls.last.request.headers["content-type"] == "application/json"


async def test_post_with_string_body(http_mock) -> None:
    route = http_mock.post("https://api.example.com/echo").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    await HttpRequestStepExecutor().execute(
        {
            "method": "POST",
            "url": "https://api.example.com/echo",
            "bodyTemplate": "raw string body",
        },
        {},
    )
    assert route.called


async def test_non_dict_json_response_wrapped_in_body(http_mock) -> None:
    http_mock.get("https://api.example.com/list").mock(
        return_value=httpx.Response(200, json=[1, 2, 3])
    )
    result = await HttpRequestStepExecutor().execute(
        {"method": "GET", "url": "https://api.example.com/list"},
        {},
    )
    assert result == {"body": [1, 2, 3]}


async def test_text_response_wrapped_in_body(http_mock) -> None:
    http_mock.get("https://api.example.com/text").mock(
        return_value=httpx.Response(200, text="plain text response")
    )
    result = await HttpRequestStepExecutor().execute(
        {"method": "GET", "url": "https://api.example.com/text"},
        {},
    )
    assert result == {"body": "plain text response"}


async def test_http_4xx_raises_http_step_error(http_mock) -> None:
    http_mock.get("https://api.example.com/gone").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    with pytest.raises(HttpStepError) as exc_info:
        await HttpRequestStepExecutor().execute(
            {"method": "GET", "url": "https://api.example.com/gone"},
            {},
        )
    assert exc_info.value.status_code == 404


async def test_http_5xx_raises_http_step_error(http_mock) -> None:
    http_mock.get("https://api.example.com/broken").mock(
        return_value=httpx.Response(500, text="server error")
    )
    with pytest.raises(HttpStepError) as exc_info:
        await HttpRequestStepExecutor().execute(
            {"method": "GET", "url": "https://api.example.com/broken"},
            {},
        )
    assert exc_info.value.status_code == 500


async def test_timeout_propagated(http_mock) -> None:
    http_mock.get("https://api.example.com/slow").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    with pytest.raises(httpx.TimeoutException):
        await HttpRequestStepExecutor().execute(
            {"method": "GET", "url": "https://api.example.com/slow", "timeoutMs": 100},
            {},
        )


async def test_expression_resolved_in_url() -> None:
    ctx = {"trigger": {"output": {"host": "api.example.com"}}}
    with respx.mock() as mock:
        route = mock.get("https://api.example.com/info").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await HttpRequestStepExecutor().execute(
            {"method": "GET", "url": "https://{{ $trigger.output.host }}/info"},
            ctx,
        )
    assert route.called
