"""Headroom tool-output compression node (Phase 1).

Shrinks large ``ToolMessage`` payloads (JSON arrays, search results, logs returned by
provider tools) *after* the ``tools`` node and *before* they re-enter the LLM context via
``trim`` -> ``llm``. This is the highest-value, lowest-risk compression seam in the agent
graph: the model's bound tool schemas (``bind_tools`` in ``llm_node``) are untouched, so
tool-calling behaviour does not change — only the *size* of tool results the model reads.

Design notes / why a custom node instead of Headroom's stock node:

* Headroom's ``create_compress_tool_messages_node`` returns brand-new ``ToolMessage``
  objects that drop ``.id``. Under this graph's ``add_messages`` reducer, a message with a
  new id is *appended* rather than *replacing* the original — leaving BOTH the original
  large payload and the compressed copy in state (more tokens, and two messages for one
  ``tool_call_id``, which the OpenAI API rejects). We therefore reuse Headroom's strong
  compression but re-attach the original ``id`` / ``name`` / ``tool_call_id`` and return
  only the messages that actually changed, so the reducer replaces them in place.
* Compression is best-effort: any failure falls back to the original messages. A
  compression bug must never break a live conversation.

Gated by ``app_settings.HEADROOM_ENABLED``; when off, the graph never adds this node.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import ToolMessage
from opentelemetry import trace

from app.core.config import app_settings

logger = logging.getLogger(__name__)

# Lazily-built, process-wide stock compressor (cheap to call, expensive-ish to import).
_stock_node: Any | None = None


def _get_stock_node() -> Any:
    global _stock_node
    if _stock_node is None:
        from headroom.integrations.langchain import create_compress_tool_messages_node

        _stock_node = create_compress_tool_messages_node(
            min_tokens_to_compress=app_settings.HEADROOM_MIN_TOKENS_TO_COMPRESS,
            preserve_errors=app_settings.HEADROOM_PRESERVE_ERRORS,
        )
    return _stock_node


def _compress_messages(messages: list[Any]) -> tuple[list[Any], int, int]:
    """Return (replacement_messages, original_chars, compressed_chars).

    ``replacement_messages`` are only the ToolMessages whose content changed, carrying the
    original id/name/tool_call_id so the ``add_messages`` reducer replaces them in place.
    """
    originals: dict[str, ToolMessage] = {
        m.tool_call_id: m
        for m in messages
        if isinstance(m, ToolMessage) and m.tool_call_id
    }
    if not originals:
        return [], 0, 0

    compressed = _get_stock_node()({"messages": messages})["messages"]

    replacements: list[Any] = []
    orig_chars = compressed_chars = 0
    for cm in compressed:
        if not isinstance(cm, ToolMessage):
            continue
        original = originals.get(cm.tool_call_id)
        if original is None:
            continue
        old, new = str(original.content), str(cm.content)
        if new == old:
            continue
        orig_chars += len(old)
        compressed_chars += len(new)
        replacements.append(
            ToolMessage(
                content=cm.content,
                tool_call_id=original.tool_call_id,
                name=original.name,
                id=original.id,  # critical: same id => reducer replaces, not appends
            )
        )
    return replacements, orig_chars, compressed_chars


async def compress_tool_messages_node(state: dict) -> dict:
    """Graph node: compress large ToolMessages in place. No-op-safe on any error.

    Returns a partial state update containing only the changed ToolMessages (or ``{}``).
    Ground-truth token savings are observed via the existing ``UsageMetadataCallbackHandler``
    on the next LLM turn; the char deltas logged here are for per-step observability.
    """
    if not app_settings.HEADROOM_ENABLED:  # defence in depth; graph also gates at build time
        return {}

    messages = state.get("messages", [])
    span = trace.get_current_span()
    try:
        replacements, orig_chars, comp_chars = await asyncio.to_thread(
            _compress_messages, messages
        )
    except Exception:  # never let compression break the conversation
        logger.exception("headroom: tool-output compression failed; passing through")
        if span is not None:
            span.set_attribute("headroom.error", True)
        return {}

    if not replacements:
        return {}

    saved = orig_chars - comp_chars
    ratio = (comp_chars / orig_chars) if orig_chars else 1.0
    logger.info(
        "headroom: compressed %d tool message(s) %d->%d chars (saved %d, ratio %.3f)",
        len(replacements),
        orig_chars,
        comp_chars,
        saved,
        ratio,
        extra={
            "headroom_messages_compressed": len(replacements),
            "headroom_chars_before": orig_chars,
            "headroom_chars_after": comp_chars,
            "headroom_chars_saved": saved,
            "headroom_ratio": round(ratio, 4),
        },
    )
    if span is not None:
        span.set_attribute("headroom.messages_compressed", len(replacements))
        span.set_attribute("headroom.chars_before", orig_chars)
        span.set_attribute("headroom.chars_after", comp_chars)
        span.set_attribute("headroom.chars_saved", saved)
        span.set_attribute("headroom.ratio", round(ratio, 4))

    return {"messages": replacements}
