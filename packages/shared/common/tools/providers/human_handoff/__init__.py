from typing import List, Optional
from langchain_core.tools import Tool
from .tools import build_human_handoff_trigger_channel_tool, get_human_handoff_tools

# Base-only fallback variant — no workspace form-fields layered in. The per-turn
# tool that DOES include workspace `form.fields` is built fresh by
# ``channels/ai/nodes._get_tools`` via the factory.
_HANDOFF_CHANNEL_FALLBACK = build_human_handoff_trigger_channel_tool(None)


class HumanHandoffTools:
    """Human handoff tools provider."""

    def __init__(self):
        self._tools: List[Tool] = []
        # Load human handoff tools
        tools = get_human_handoff_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[Tool]:
        """Get all human handoff tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Get a specific human handoff tool by name."""
        if name == _HANDOFF_CHANNEL_FALLBACK.name:
            return _HANDOFF_CHANNEL_FALLBACK
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


# Create global instance
HumanHandoffTools = HumanHandoffTools()

__all__ = ["HumanHandoffTools"]
