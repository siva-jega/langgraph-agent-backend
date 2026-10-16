from __future__ import annotations

from typing import Callable, Dict, List

from langchain_core.tools import Tool


def build_tools_data(get_tools_fn: Callable[[], List[Tool]]) -> Dict[str, Dict[str, str]]:
    """
    Build a JSON-serializable tool metadata dict for a provider.

    Keyed by `tool.id` (we use LangChain `tool.name` which, in this codebase, matches the
    function name used in each provider's `tools.py`).
    """
    tools_data: Dict[str, Dict[str, str]] = {}
    for tool in get_tools_fn():
        tool_id = getattr(tool, "name", "") or ""
        tools_data[tool_id] = {
            "id": tool_id,
            "name": tool_id,
            "description": getattr(tool, "description", "") or "",
        }
    return tools_data

