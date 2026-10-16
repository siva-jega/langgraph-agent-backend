from typing import List, Optional
from langchain_core.tools import Tool
from .tools import get_memory_tools


class MemoryTools:
    """Memory tools provider."""

    def __init__(self):
        self._tools: List[Tool] = []
        tools = get_memory_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[Tool]:
        """Get all memory tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Get a specific memory tool by name."""
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


# Create global instance
MemoryTools = MemoryTools()

__all__ = ["MemoryTools"]