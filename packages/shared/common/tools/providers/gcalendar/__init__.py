from typing import List, Optional
from langchain_core.tools import Tool
from .tools import get_gcalendar_tools


class GCalendarTools:
    """Google Calendar tools provider."""

    def __init__(self):
        self._tools: List[Tool] = []
        # Load Google Calendar tools
        tools = get_gcalendar_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[Tool]:
        """Get all Google Calendar tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Get a specific Google Calendar tool by name."""
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


# Create global instance
GCalendarTools = GCalendarTools()

__all__ = ["GCalendarTools"]
