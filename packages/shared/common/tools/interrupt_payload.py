"""Merge client-supplied interrupt JSON with tool metadata for HITL SSE payloads."""

from __future__ import annotations

from typing import Any, Dict, Optional

from langchain_core.runnables import RunnableConfig


def merge_interrupt_value(
    config: RunnableConfig,
    *,
    tool_name: str,
    tool_display_name: str,
    tool_args: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the value passed to ``interrupt()``.

    The frontend may send arbitrary JSON on each chat request as ``interrupt_payload``
    (request field ``interruptPayload``). It is merged into the interrupt payload with no
    required shape. Server fields ``tool_name``, ``tool_display_name``, and optional
    ``tool_args`` (model-supplied parameters) are set last so they are always present for
    routing and backwards compatibility.
    """
    c = config.get("configurable", {}) or {}
    raw = c.get("interrupt_payload")

    merged: Dict[str, Any] = {}
    if isinstance(raw, dict):
        merged.update(raw)
    elif raw is not None:
        merged["interruptPayload"] = raw

    if tool_args:
        merged["tool_args"] = tool_args

    merged["tool_name"] = tool_name
    merged["tool_display_name"] = tool_display_name
    return merged
