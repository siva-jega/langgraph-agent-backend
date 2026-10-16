from typing import List, Optional
from langchain_core.tools import Tool
from .tools import (
    build_create_lead_aide_channel_tool,
    build_create_ticket_aide_channel_tool,
    get_aide_tools,
)

# Base-only fallback variants — no workspace form-fields layered in. Per-turn
# tools that DO include workspace `form.fields` are built fresh by
# ``channels/ai/nodes._get_tools`` via the factories above. The registry exposes
# these fallbacks so any code that resolves by name (debug, admin) still works.
_CREATE_TICKET_AIDE_CHANNEL_FALLBACK = build_create_ticket_aide_channel_tool(None)
_CREATE_LEAD_AIDE_CHANNEL_FALLBACK = build_create_lead_aide_channel_tool(None)


class AideTools:
    """Aide tools provider."""

    def __init__(self):
        self._tools: List[Tool] = []
        # Load Zendesk tools
        tools = get_aide_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[Tool]:
        """Get all Aide tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Get a specific Aide tool by name."""
        channel_tools = {
            _CREATE_TICKET_AIDE_CHANNEL_FALLBACK.name: _CREATE_TICKET_AIDE_CHANNEL_FALLBACK,
            _CREATE_LEAD_AIDE_CHANNEL_FALLBACK.name: _CREATE_LEAD_AIDE_CHANNEL_FALLBACK,
        }
        if name in channel_tools:
            return channel_tools[name]
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


# Create global instance
AideTools = AideTools()

__all__ = ["AideTools"]
