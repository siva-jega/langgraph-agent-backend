"""Tests for the live `get_collection` Shopify tool (internal HTTP call mocked)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import common.tools.providers.shopify.tools as shopify_tools
from common.tools.providers.shopify.tools import get_collection


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _CapturingClient:
    """Async-context httpx.AsyncClient stand-in that records the GET it receives."""

    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        _CapturingClient.captured = {"url": url, "params": params, "headers": headers}
        return _FakeResp(_CapturingClient.response)


_COLLECTION_PAYLOAD = {
    "id": "c1",
    "name": "Summer Picks",
    "coverImageUrl": "https://img/cover.jpg",
    "products": [
        {
            "id": "gid://shopify/Product/1",
            "name": "Linen Shirt",
            "price": 49.0,
            "compareAtPrice": 69.0,
            "currency": "USD",
            "image": "https://img/1.jpg",
            "images": ["https://img/1.jpg", "https://img/1b.jpg"],
            "description": "Breathable linen.",
            "inStock": True,
            "productUrl": "https://shop/linen-shirt",
            "variants": [
                {"id": "v1", "name": "S", "price": 49.0, "availableForSale": True}
            ],
        },
        {
            "id": "gid://shopify/Product/2",
            "name": "Straw Hat",
            "price": 25.0,
            "currency": "USD",
            "image": "https://img/2.jpg",
            "inStock": False,
            "productUrl": "https://shop/straw-hat",
            "variants": [],
        },
    ],
}


def _patch(monkeypatch, *, response: dict) -> None:
    monkeypatch.setattr(
        shopify_tools,
        "project_settings",
        lambda: SimpleNamespace(NESTJS_URL="https://api.test/", INTERNAL_SECRET="sek"),
        raising=False,
    )
    # The tool imports project_settings from common.project_settings inside the function.
    import common.project_settings as ps

    monkeypatch.setattr(
        ps,
        "project_settings",
        lambda: SimpleNamespace(NESTJS_URL="https://api.test/", INTERNAL_SECRET="sek"),
    )
    _CapturingClient.response = response
    _CapturingClient.captured = {}
    monkeypatch.setattr(shopify_tools.httpx, "AsyncClient", _CapturingClient)


def _tool_call() -> dict:
    """A ToolCall payload so ainvoke returns a ToolMessage carrying content + artifact."""
    return {"type": "tool_call", "name": "get_collection", "args": {}, "id": "call-1"}


@pytest.mark.asyncio
async def test_get_collection_fetches_and_returns_products(monkeypatch) -> None:
    _patch(monkeypatch, response=_COLLECTION_PAYLOAD)

    msg = await get_collection.ainvoke(
        _tool_call(),
        config={"configurable": {"agent_id": "a", "active_collection_id": "c"}},
    )
    content, artifact = msg.content, msg.artifact

    cap = _CapturingClient.captured
    assert cap["url"] == "https://api.test/internal/storefront/a/collection/c"
    assert cap["headers"] == {"x-internal-secret": "sek"}

    assert "Summer Picks" in content
    assert "Linen Shirt" in content
    assert "out of stock" in content  # Straw Hat is inStock=False

    assert artifact["name"] == "Shopify"
    assert artifact["tool"] == "Get Products"
    cards = artifact["data"]
    assert len(cards) == 2
    first = cards[0]
    assert first["id"] == "gid://shopify/Product/1"
    assert first["name"] == "Linen Shirt"
    assert first["product_url"] == "https://shop/linen-shirt"
    assert first["image_url"] == "https://img/1.jpg"
    assert first["images"] == ["https://img/1.jpg", "https://img/1b.jpg"]
    assert first["currencyCode"] == "USD"
    assert first["price_range"] == "USD 49.0"
    assert first["availableForSale"] is True
    assert first["variants"][0]["name"] == "S"
    assert first["variants"][0]["price"] == 49.0


@pytest.mark.asyncio
async def test_get_collection_wraps_result_interceptor(monkeypatch) -> None:
    _patch(monkeypatch, response={"result": _COLLECTION_PAYLOAD})

    msg = await get_collection.ainvoke(
        _tool_call(),
        config={"configurable": {"agent_id": "a", "active_collection_id": "c"}},
    )

    assert "Summer Picks" in msg.content
    assert len(msg.artifact["data"]) == 2


@pytest.mark.asyncio
async def test_get_collection_no_collection_open(monkeypatch) -> None:
    _patch(monkeypatch, response=_COLLECTION_PAYLOAD)

    msg = await get_collection.ainvoke(
        _tool_call(),
        config={"configurable": {"agent_id": "a"}},
    )

    assert "no collection" in msg.content.lower()
    assert msg.artifact == []
    assert _CapturingClient.captured == {}
