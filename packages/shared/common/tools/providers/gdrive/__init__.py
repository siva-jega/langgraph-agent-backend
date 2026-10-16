from typing import List, Optional
from langchain_core.tools import Tool
from .tools import get_gdrive_tools


class GDriveTools:
    """Google Drive tools provider."""

    def __init__(self):
        self._tools: List[Tool] = []
        # Load Google Drive tools
        tools = get_gdrive_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[Tool]:
        """Get all Google Drive tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Get a specific Google Drive tool by name."""
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


# Create global instance
GDriveTools = GDriveTools()

__all__ = ["GDriveTools"]
