from typing import Literal

import pytest

from app.modules.channels.providers.meta.messenger.adapter import MessengerAdapter
from app.modules.channels.providers.meta.whatsapp import adapter as whatsapp_adapter_module
from app.modules.channels.providers.meta.whatsapp.adapter import WhatsAppAdapter
from app.modules.channels.schemas import (
    InboundMessagePayload,
    InboundMeta,
    InboundSender,
    NormalizedInboundMessage,
)


def _inbound(channel: Literal["messenger", "whatsapp"]) -> NormalizedInboundMessage:
    return NormalizedInboundMessage(
        id="inbound-1",
        timestamp="2026-05-11T00:00:00Z",
        channel=channel,
        agentId="agent-1",
        integrationId="integration-1",
        workspaceId="workspace-1",
        chatId="chat-1",
        sender=InboundSender(id="sender-1", phone="15555550123"),
        message=InboundMessagePayload(text="hello", messageId="message-1"),
        meta=InboundMeta(pageId="page-1", whatsappPhoneNumberId="phone-1"),
    )


@pytest.mark.asyncio
async def test_messenger_send_splits_messages_at_2000_chars(monkeypatch):
    provider = MessengerAdapter()
    posts: list[dict] = []

    monkeypatch.setattr(
        provider, "_resolve_page_id", lambda integration, inbound: "page-1"
    )
    monkeypatch.setattr(
        provider, "_resolve_page_access_token", lambda integration: "token-1"
    )

    async def fake_post_to_meta_graph(url, body, access_token):
        posts.append(body)
        return {"message_id": f"mid-{len(posts)}"}

    monkeypatch.setattr(provider, "_post_to_meta_graph", fake_post_to_meta_graph)

    result = await provider.send(
        formatted="x" * 2001,
        ai_text="",
        inbound=_inbound("messenger"),
        integration={},
    )

    assert result.success is True
    assert result.messageId == "mid-2"
    assert [len(post["message"]["text"]) for post in posts] == [2000, 1]


@pytest.mark.asyncio
async def test_whatsapp_send_splits_messages_at_4096_chars(monkeypatch):
    provider = WhatsAppAdapter()
    posts: list[dict] = []

    monkeypatch.setattr(
        provider, "_resolve_phone_number_id", lambda integration, inbound: "phone-1"
    )
    monkeypatch.setattr(provider, "_resolve_access_token", lambda integration: "token-1")

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = ""

        def __init__(self, message_id: str) -> None:
            self._message_id = message_id

        def json(self) -> dict:
            return {"messages": [{"id": self._message_id}]}

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            posts.append(json)
            return FakeResponse(f"wamid.{len(posts)}")

    monkeypatch.setattr(whatsapp_adapter_module.httpx, "AsyncClient", FakeAsyncClient)

    result = await provider.send(
        formatted="x" * 4097,
        ai_text="",
        inbound=_inbound("whatsapp"),
        integration={},
    )

    assert result.success is True
    assert result.messageId == "wamid.2"
    assert [len(post["text"]["body"]) for post in posts] == [4096, 1]
