"""Calendly provider: event management, scheduling, and user tools."""

from typing import List, Optional

from langchain_core.tools import BaseTool

from .tools import get_calendly_tools


class CalendlyTools:
    """Calendly tools provider."""

    def __init__(self) -> None:
        self._tools: List[BaseTool] = []
        tools = get_calendly_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[BaseTool]:
        """Return all Calendly tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[BaseTool]:
        """Return a tool by name."""
        for t in self._tools:
            if t.name == name:
                return t
        return None


CalendlyTools = CalendlyTools()

__all__ = ["CalendlyTools", "get_calendly_tools"]
