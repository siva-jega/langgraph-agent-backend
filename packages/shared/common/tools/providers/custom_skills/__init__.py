from typing import List, Optional

from langchain_core.tools import Tool

from .tools import get_custom_skill_tools


class CustomSkillsTools:
    """Registers `use_skill` (web) and `use_skill_channel` (messaging channels)
    for workspace-defined CUSTOM AgentSkills rows."""

    def __init__(self):
        self._tools: List[Tool] = list(get_custom_skill_tools())

    def get_all(self) -> List[Tool]:
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


CustomSkillsTools = CustomSkillsTools()

__all__ = ["CustomSkillsTools"]
