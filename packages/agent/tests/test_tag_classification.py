"""Tests for the tag-classification background hook.

Covers the invariants that keep auto-tagging safe:
  * flag OFF            -> no LLM call, no DB write
  * indices map to ids  -> dedup + out-of-range numbers dropped
  * empty tags/messages -> early return, no attach
  * classifier output   -> resolved ids attached additively
"""

import pytest

from app.core.config import app_settings
from app.modules.tag_classification import service as svc
from app.modules.tag_classification.models import AgentTag, TagClassificationResult


def _tags() -> list[AgentTag]:
    return [
        AgentTag(id="id-a", title="Abandoned Cart"),
        AgentTag(id="id-b", title="High Intent"),
    ]


def test_resolve_selected_dedups_and_drops_out_of_range():
    result = TagClassificationResult(selected=[1, 1, 2, 0, 3, -1])
    assert svc._resolve_selected(result, _tags()) == ["id-a", "id-b"]


def test_resolve_selected_empty():
    assert svc._resolve_selected(TagClassificationResult(selected=[]), _tags()) == []


def test_format_conversation_skips_empty_and_labels_roles():
    conv = svc._format_conversation(
        [("USER", "where is my order"), ("ASSISTANT", "let me check"), ("USER", "")]
    )
    assert conv == "Customer: where is my order\nAI Agent: let me check"


async def test_classify_and_tag_chat_flag_off(monkeypatch):
    monkeypatch.setattr(app_settings, "TAG_CLASSIFICATION_ENABLED", False)
    called = {"tags": False}

    async def _fetch_tags(_agent_id):
        called["tags"] = True
        return _tags()

    monkeypatch.setattr(svc, "fetch_agent_tags", _fetch_tags)
    await svc.classify_and_tag_chat("chat-1", "agent-1")
    assert called["tags"] is False


async def test_classify_and_tag_chat_no_tags(monkeypatch):
    monkeypatch.setattr(app_settings, "TAG_CLASSIFICATION_ENABLED", True)

    async def _fetch_tags(_agent_id):
        return []

    attached = []

    async def _attach(_chat_id, _ids):
        attached.append(_ids)
        return 0

    monkeypatch.setattr(svc, "fetch_agent_tags", _fetch_tags)
    monkeypatch.setattr(svc, "attach_tags_to_chat", _attach)
    await svc.classify_and_tag_chat("chat-1", "agent-1")
    assert attached == []


async def test_classify_and_tag_chat_attaches_resolved_ids(monkeypatch):
    monkeypatch.setattr(app_settings, "TAG_CLASSIFICATION_ENABLED", True)
    attached = []

    async def _fetch_tags(_agent_id):
        return _tags()

    async def _fetch_messages(_chat_id, _limit):
        return [("USER", "i left items in my cart")]

    async def _classify(_tags, _conv):
        return TagClassificationResult(selected=[1])

    async def _attach(chat_id, ids):
        attached.append((chat_id, ids))
        return len(ids)

    monkeypatch.setattr(svc, "fetch_agent_tags", _fetch_tags)
    monkeypatch.setattr(svc, "fetch_recent_messages", _fetch_messages)
    monkeypatch.setattr(svc, "classify", _classify)
    monkeypatch.setattr(svc, "attach_tags_to_chat", _attach)

    await svc.classify_and_tag_chat("chat-1", "agent-1")
    assert attached == [("chat-1", ["id-a"])]


async def test_classify_and_tag_chat_no_selection_skips_attach(monkeypatch):
    monkeypatch.setattr(app_settings, "TAG_CLASSIFICATION_ENABLED", True)
    attached = []

    async def _fetch_tags(_agent_id):
        return _tags()

    async def _fetch_messages(_chat_id, _limit):
        return [("USER", "hello")]

    async def _classify(_tags, _conv):
        return TagClassificationResult(selected=[])

    async def _attach(chat_id, ids):
        attached.append((chat_id, ids))
        return len(ids)

    monkeypatch.setattr(svc, "fetch_agent_tags", _fetch_tags)
    monkeypatch.setattr(svc, "fetch_recent_messages", _fetch_messages)
    monkeypatch.setattr(svc, "classify", _classify)
    monkeypatch.setattr(svc, "attach_tags_to_chat", _attach)

    await svc.classify_and_tag_chat("chat-1", "agent-1")
    assert attached == []


def test_schedule_flag_off_is_noop(monkeypatch):
    monkeypatch.setattr(app_settings, "TAG_CLASSIFICATION_ENABLED", False)
    # No running loop and flag off: must not raise.
    svc.schedule_tag_classification("chat-1", "agent-1")
