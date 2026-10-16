"""Post-add-to-cart "recommend similar" routine (Phase 5).

Covers the button detection / seed parsing, the cart+carousel history helper
(with a fake pg client), and the `_handle_postcart_similar` orchestration —
re-offering the carousel's other products while excluding what's in the cart,
and the graceful path when nothing fresh remains.
"""

import app.modules.channels.providers.meta.whatsapp.adapter as wa_adapter
import app.modules.channels.repository as repo
import app.modules.channels.service as service
from app.modules.channels.providers.meta.whatsapp.adapter import WhatsAppAdapter
from app.modules.channels.repository import get_cart_and_recent_carousel_products
from app.modules.channels.schemas import (
    InboundMessagePayload,
    InboundSender,
    NormalizedInboundMessage,
    SendResult,
)


def _inbound(payload: str | None) -> NormalizedInboundMessage:
    button = (
        {"type": "button", "text": "Recommend similar", "payload": payload}
        if payload is not None
        else None
    )
    return NormalizedInboundMessage(
        id="env-1",
        timestamp="2026-06-16T00:00:00Z",
        channel="whatsapp",
        agentId="agent-1",
        integrationId="int-1",
        workspaceId="ws-1",
        chatId="chat-1",
        sender=InboundSender(phone="919999999999"),
        message=InboundMessagePayload(
            text="Recommend similar",
            messageId="wamid.CLICK",
            messageType="BUTTON" if button else "TEXT",
            buttonPayload=button,
        ),
    )


# --------------------------------------------------------------------------- #
# Pure detection / parsing                                                     #
# --------------------------------------------------------------------------- #
def test_is_postcart_similar_matches_only_the_similar_button() -> None:
    assert service._is_postcart_similar(
        _inbound("POSTCART_SIMILAR::SHOPIFY::gid://shopify/Product/1")
    )
    assert not service._is_postcart_similar(_inbound("POSTCART_ELSE"))
    assert not service._is_postcart_similar(_inbound("POSTCART_DONE"))
    # plain text message is never a postcart-similar tap
    plain = _inbound(None)
    assert not service._is_postcart_similar(plain)


def test_seed_product_id_parsed_from_payload() -> None:
    assert (
        service._postcart_seed_product_id(
            _inbound("POSTCART_SIMILAR::SHOPIFY::gid://shopify/Product/9")
        )
        == "gid://shopify/Product/9"
    )
    # missing product segment → empty
    assert service._postcart_seed_product_id(_inbound("POSTCART_SIMILAR")) == ""


# --------------------------------------------------------------------------- #
# get_cart_and_recent_carousel_products (fake pg)                              #
# --------------------------------------------------------------------------- #
class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *_args):
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePg:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


async def test_cart_and_carousel_split_from_history(monkeypatch) -> None:
    # DESC by createdAt: newest first. Two add-to-cart taps + one carousel.
    rows = [
        {"messageType": "BUTTON", "extraInfo": {"button": {"productId": "p2"}}},
        {
            "messageType": "TEMPLATE",
            "extraInfo": {
                "template": {
                    "products": [
                        {"id": "p1", "name": "A"},
                        {"id": "p2", "name": "B"},
                        {"id": "p3", "name": "C"},
                    ]
                }
            },
        },
        {"messageType": "BUTTON", "extraInfo": {"button": {"productId": "p1"}}},
    ]
    monkeypatch.setattr(repo, "get_pg", lambda: _FakePg(_FakeConn(rows)))

    cart_ids, products = await get_cart_and_recent_carousel_products("chat-1")
    assert cart_ids == {"p1", "p2"}
    assert [p["id"] for p in products] == ["p1", "p2", "p3"]


async def test_cart_helper_skips_actions_template_without_products(monkeypatch) -> None:
    rows = [
        # most recent TEMPLATE is the actions prompt (no products) — skipped
        {"messageType": "TEMPLATE", "extraInfo": {"template": {"type": "actions"}}},
        {
            "messageType": "TEMPLATE",
            "extraInfo": {"template": {"products": [{"id": "p1"}]}},
        },
    ]
    monkeypatch.setattr(repo, "get_pg", lambda: _FakePg(_FakeConn(rows)))

    cart_ids, products = await get_cart_and_recent_carousel_products("chat-1")
    assert cart_ids == set()
    assert [p["id"] for p in products] == ["p1"]


# --------------------------------------------------------------------------- #
# _handle_postcart_similar orchestration                                       #
# --------------------------------------------------------------------------- #
class _FakeProvider:
    def __init__(self):
        self.text_sends: list[str] = []
        self.quick_reply_calls: list[list[str]] = []

    async def send(self, formatted, ai_text, inbound, integration):
        self.text_sends.append(formatted)
        return SendResult(success=True, messageId="wamid.text")

    async def send_quick_reply_template(
        self, *, inbound, integration, asset, button_payloads
    ):
        self.quick_reply_calls.append(button_payloads)
        return SendResult(success=True, messageId="wamid.actions")


def _patch_common(monkeypatch, *, cart_ids, products, sent_capture):
    monkeypatch.setattr(
        service,
        "get_cart_and_recent_carousel_products",
        lambda _chat: _async(( cart_ids, products )),
    )
    monkeypatch.setattr(
        service, "get_ecommerce_provider_for_agent", lambda _cfg: object()
    )
    monkeypatch.setattr(
        service, "fetch_best_carousel_asset", lambda _i, _c: _async({"slug": "wa_tmpl_product_cards_carousel"})
    )
    monkeypatch.setattr(
        service,
        "fetch_asset_by_logical_key",
        lambda _i, key: _async({"slug": key, "metaPayload": {"name": "postcart_actions_v1", "language": "en_US"}}),
    )

    async def _fake_send_and_persist(**kwargs):
        sent_capture.append(kwargs["products"])
        return SendResult(success=True, messageId="wamid.cards")

    monkeypatch.setattr(service, "_send_and_persist_catalog_cards", _fake_send_and_persist)
    monkeypatch.setattr(service, "create_chat_message", lambda **_k: _async(True))


def _async(value):
    async def _coro():
        return value

    return _coro()


async def test_handle_similar_sends_carousel_excluding_cart_then_buttons(
    monkeypatch,
) -> None:
    sent: list[list[dict]] = []
    _patch_common(
        monkeypatch,
        cart_ids={"p1"},
        products=[{"id": "p1", "name": "A"}, {"id": "p2", "name": "B"}],
        sent_capture=sent,
    )
    provider = _FakeProvider()

    await service._handle_postcart_similar(
        inbound=_inbound("POSTCART_SIMILAR::SHOPIFY::p1"),
        integration={},
        provider=provider,
        agent_config={},
        integration_row_id="aia-1",
    )

    # carousel sent with only the un-carted product
    assert len(sent) == 1
    assert [p["id"] for p in sent[0]] == ["p2"]
    # actions buttons re-sent with the three payloads
    assert provider.quick_reply_calls == [
        ["POSTCART_ELSE", "POSTCART_SIMILAR::SHOPIFY::p1", "POSTCART_DONE"]
    ]
    assert provider.text_sends == []


async def test_handle_similar_graceful_when_nothing_fresh(monkeypatch) -> None:
    sent: list[list[dict]] = []
    _patch_common(
        monkeypatch,
        cart_ids={"p1", "p2"},
        products=[{"id": "p1"}, {"id": "p2"}],  # all already in cart
        sent_capture=sent,
    )
    provider = _FakeProvider()

    await service._handle_postcart_similar(
        inbound=_inbound("POSTCART_SIMILAR::SHOPIFY::p1"),
        integration={},
        provider=provider,
        agent_config={},
        integration_row_id="aia-1",
    )

    assert sent == []  # no carousel
    assert provider.quick_reply_calls == []  # no buttons
    assert len(provider.text_sends) == 1
    assert "everything similar" in provider.text_sends[0]


# --------------------------------------------------------------------------- #
# adapter.send_quick_reply_template payload shape                              #
# --------------------------------------------------------------------------- #
class _FakeResp:
    status_code = 200
    headers = {"content-type": "application/json"}

    def json(self):
        return {"messages": [{"id": "wamid.OUT"}]}


class _CapturingClient:
    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _CapturingClient.captured = {"url": url, "json": json}
        return _FakeResp()


async def test_quick_reply_template_payload_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        WhatsAppAdapter, "_resolve_phone_number_id", lambda self, i, inb: "pnid-1"
    )
    monkeypatch.setattr(
        WhatsAppAdapter, "_resolve_access_token", lambda self, i: "tok"
    )
    monkeypatch.setattr(wa_adapter.httpx, "AsyncClient", _CapturingClient)

    result = await WhatsAppAdapter().send_quick_reply_template(
        inbound=_inbound("POSTCART_SIMILAR::SHOPIFY::p1"),
        integration={},
        # metaPayload arrives as a JSON string from asyncpg — must be parsed
        asset={"metaPayload": '{"name": "postcart_actions_v1", "language": "en_US"}'},
        button_payloads=["POSTCART_ELSE", "POSTCART_SIMILAR::SHOPIFY::p1", "POSTCART_DONE"],
    )

    assert result.success is True
    assert result.messageId == "wamid.OUT"
    body = _CapturingClient.captured["json"]
    assert _CapturingClient.captured["url"].endswith("pnid-1/messages")
    assert body["type"] == "template"
    assert body["template"]["name"] == "postcart_actions_v1"
    assert body["template"]["language"] == {"code": "en_US"}
    assert body["template"]["components"] == [
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "0",
            "parameters": [{"type": "payload", "payload": "POSTCART_ELSE"}],
        },
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "1",
            "parameters": [
                {"type": "payload", "payload": "POSTCART_SIMILAR::SHOPIFY::p1"}
            ],
        },
        {
            "type": "button",
            "sub_type": "quick_reply",
            "index": "2",
            "parameters": [{"type": "payload", "payload": "POSTCART_DONE"}],
        },
    ]
