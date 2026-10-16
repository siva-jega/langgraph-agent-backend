"""Reply/quoted-context resolution and message persistence wiring.

Covers the Phase 4 helpers (`_template_products`, `_summarize_template_products`)
and the DB-facing functions (`get_quoted_message_text`, `create_chat_message`)
with a fake pg client.
"""

import json

import app.modules.channels.repository as repo
from app.modules.channels.repository import (
    _summarize_template_products,
    _template_products,
    create_chat_message,
    get_quoted_message_text,
)


# --------------------------------------------------------------------------- #
# Fake Postgres client                                                         #
# --------------------------------------------------------------------------- #
class _FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.calls: list = []

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self.row

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        return "INSERT 0 1"


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


def _patch_pg(monkeypatch, row=None):
    conn = _FakeConn(row)
    monkeypatch.setattr(repo, "get_pg", lambda: _FakePg(conn))
    monkeypatch.setattr(repo, "schedule_conversation_live_activity", lambda *_: None)
    return conn


_CAROUSEL_EXTRA = {
    "template": {
        "type": "catalog_cards",
        "products": [
            {"id": "1", "name": "Ronin Graphic Jacket", "description": "x" * 200},
            {"id": "2", "name": "Lexi Dress", "description": "Black geogette dress"},
        ],
    }
}


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #
def test_template_products_accepts_dict_and_json_string() -> None:
    from_dict = _template_products(_CAROUSEL_EXTRA)
    from_json = _template_products(json.dumps(_CAROUSEL_EXTRA))
    assert [p["name"] for p in from_dict] == ["Ronin Graphic Jacket", "Lexi Dress"]
    assert from_dict == from_json


def test_template_products_empty_for_non_template() -> None:
    assert _template_products(None) == []
    assert _template_products({"foo": "bar"}) == []
    assert _template_products("not json") == []
    assert _template_products({"template": {"products": "nope"}}) == []


def test_summarize_lists_products_and_truncates_description() -> None:
    summary = _summarize_template_products(_CAROUSEL_EXTRA)
    assert summary.startswith("product carousel — ")
    assert "1. Ronin Graphic Jacket" in summary
    assert "2. Lexi Dress" in summary
    # 200-char description is clipped to 80
    assert "x" * 80 in summary
    assert "x" * 81 not in summary


def test_summarize_none_when_no_named_products() -> None:
    assert _summarize_template_products(None) is None
    assert _summarize_template_products({"template": {"products": []}}) is None
    assert (
        _summarize_template_products({"template": {"products": [{"id": "1"}]}}) is None
    )


# --------------------------------------------------------------------------- #
# get_quoted_message_text                                                      #
# --------------------------------------------------------------------------- #
async def test_quoted_text_returns_product_summary_for_template(monkeypatch) -> None:
    _patch_pg(
        monkeypatch,
        row={
            "message": "[Template: wa_tmpl_product_cards_carousel] 2 product(s)",
            "messageType": "TEMPLATE",
            "extraInfo": _CAROUSEL_EXTRA,
        },
    )
    text = await get_quoted_message_text(
        agent_chat_id="chat-1", provider_message_id="wamid.TEMPLATE"
    )
    assert text is not None
    assert "Ronin Graphic Jacket" in text
    assert "Lexi Dress" in text
    # never the bare placeholder
    assert "product(s)" not in text


async def test_quoted_text_returns_message_for_plain_text(monkeypatch) -> None:
    _patch_pg(
        monkeypatch,
        row={
            "message": "Here are some stylish options",
            "messageType": "TEXT",
            "extraInfo": None,
        },
    )
    text = await get_quoted_message_text(
        agent_chat_id="chat-1", provider_message_id="wamid.TEXT"
    )
    assert text == "Here are some stylish options"


async def test_quoted_text_none_when_not_found(monkeypatch) -> None:
    _patch_pg(monkeypatch, row=None)
    assert (
        await get_quoted_message_text(
            agent_chat_id="chat-1", provider_message_id="wamid.MISSING"
        )
        is None
    )


async def test_quoted_text_empty_provider_id_short_circuits(monkeypatch) -> None:
    conn = _patch_pg(monkeypatch, row={"message": "x"})
    assert (
        await get_quoted_message_text(agent_chat_id="chat-1", provider_message_id="  ")
        is None
    )
    assert conn.calls == []  # never hit the DB


# --------------------------------------------------------------------------- #
# create_chat_message                                                          #
# --------------------------------------------------------------------------- #
async def test_create_message_sent_status_auto_stamps_sent_at(monkeypatch) -> None:
    conn = _patch_pg(monkeypatch, row={"id": "new-row"})
    ok = await create_chat_message(
        agent_chat_id="chat-1",
        role="ASSISTANT",
        message="hello",
        client_message_id="wamid.OUT",
        status="SENT",
    )
    assert ok is True
    _, _, args = conn.calls[0]
    # the statusTimestamps json arg carries a sentAt stamp
    ts_args = [a for a in args if isinstance(a, str) and "sentAt" in a]
    assert ts_args, "expected a statusTimestamps arg containing sentAt"
    assert "sentAt" in json.loads(ts_args[0])
    assert "SENT" in args  # status passed through


async def test_create_message_persists_reply_link_and_button_type(monkeypatch) -> None:
    conn = _patch_pg(monkeypatch, row={"id": "new-row"})
    ok = await create_chat_message(
        agent_chat_id="chat-1",
        role="USER",
        message="Add to Cart",
        client_message_id="wamid.CLICK",
        message_type="BUTTON",
        reply_to_provider_message_id="wamid.TEMPLATE",
        extra_info={"button": {"type": "button", "text": "Add to Cart"}},
    )
    assert ok is True
    _, _, args = conn.calls[0]
    assert "BUTTON" in args
    assert "wamid.TEMPLATE" in args


async def test_create_message_skips_empty_text(monkeypatch) -> None:
    conn = _patch_pg(monkeypatch, row={"id": "new-row"})
    ok = await create_chat_message(agent_chat_id="chat-1", role="USER", message="   ")
    assert ok is False
    assert conn.calls == []  # never touches the DB
