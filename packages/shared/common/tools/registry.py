from typing import List, Optional, Dict, Any
import importlib
from langchain_core.tools import Tool
from common.tools.providers.web import WebTools
from common.tools.providers.knowledgebase import KnowledgeBaseTools
from common.tools.providers.shopify import ShopifyTools
from common.tools.providers.woocommerce import WooCommerceTools
from common.tools.providers.zendesk import ZendeskTools
from common.tools.providers.hubspot import HubspotTools
from common.tools.providers.gmail import GmailTools
from common.tools.providers.gdrive import GDriveTools
from common.tools.providers.gcalendar import GCalendarTools
from common.tools.providers.notion import NotionTools
from common.tools.providers.airtable import AirtableTools
from common.tools.providers.outlook import OutlookTools
from common.tools.providers.slack import SlackTools
from common.tools.providers.aide import AideTools
from common.tools.providers.memory import MemoryTools
from common.tools.providers.human_handoff import HumanHandoffTools
from common.tools.providers.custom_skills import CustomSkillsTools
from common.tools.providers.calendly import CalendlyTools
from common.tools.providers.calcom import CalComTools
from common.tools.providers.ithinklogistics import IThinkLogisticsTools
from common.tools.providers.shiprocket import ShiprocketTools
from common.tools.providers.delhivery import DelhiveryTools
from common.tools.providers.dtdc import DTDCTools
from common.tools.providers.wareiq import WareIQTools


def _tool_args_json_schema(tool: Any) -> Any:
    if tool is None:
        return None
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return None
    try:
        if hasattr(schema, "model_json_schema"):
            return schema.model_json_schema()
    except Exception:
        return None
    return None


class Registry:
    """Global registry for all tools from all providers."""

    def __init__(self):
        self._providers: Dict[str, Any] = {}
        self._register_providers()

    def _register_providers(self):
        """Register all available providers."""
        self._providers["web"] = WebTools
        self._providers["knowledgebase"] = KnowledgeBaseTools
        self._providers["shopify"] = ShopifyTools
        self._providers["woocommerce"] = WooCommerceTools
        self._providers["zendesk"] = ZendeskTools
        self._providers["hubspot"] = HubspotTools
        self._providers["gmail"] = GmailTools
        self._providers["gdrive"] = GDriveTools
        self._providers["gcalendar"] = GCalendarTools
        self._providers["notion"] = NotionTools
        self._providers["airtable"] = AirtableTools
        self._providers["outlook"] = OutlookTools
        self._providers["slack"] = SlackTools
        self._providers["memory"] = MemoryTools
        self._providers["aide"] = AideTools
        self._providers["human_handoff"] = HumanHandoffTools
        self._providers["custom_skills"] = CustomSkillsTools
        self._providers["calendly"] = CalendlyTools
        self._providers["calcom"] = CalComTools
        self._providers["ithinklogistics"] = IThinkLogisticsTools
        self._providers["shiprocket"] = ShiprocketTools
        self._providers["delhivery"] = DelhiveryTools
        self._providers["dtdc"] = DTDCTools
        self._providers["wareiq"] = WareIQTools

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Get a tool by name from any provider."""
        for provider in self._providers.values():
            tool = provider.get_tool_by_name(name)
            if tool:
                return tool
        return None

    def get_all_tools(self) -> List[Tool]:
        """Get all tools from all providers."""
        all_tools = []
        for provider in self._providers.values():
            all_tools.extend(provider.get_all())
        return all_tools

    def get_provider(self, name: str):
        """Get a specific provider by slug."""
        return self._providers.get(name)

    def get_provider_names(self) -> List[str]:
        """Get all provider names (slugs)."""
        return list(self._providers.keys())

    def get_tools_by_provider(self, slug: str) -> List[Tool]:
        """Get all tools for a provider by slug. Returns empty list if provider not found."""
        provider = self._providers.get(slug)
        if provider is None:
            return []
        return provider.get_all()

    def _get_tools_metadata_from_dict(self, tools_data: Any) -> List[Dict[str, str]]:
        """
        Convert provider `TOOLS_DATA` dict into a normalized list of tool metadata.

        Expected shape:
          {
            "<tool_id>": {"id": "...", "name": "...", "description": "..."},
            ...
          }
        """
        if not isinstance(tools_data, dict):
            return []

        results: List[Dict[str, str]] = []
        for tool_id, meta in tools_data.items():
            if not isinstance(tool_id, str) or not tool_id:
                continue
            if not isinstance(meta, dict):
                continue
            name = meta.get("name") or meta.get("id") or tool_id
            description = meta.get("description") or ""
            row: Dict[str, Any] = {
                "id": tool_id,
                "name": name,
                "description": description,
            }
            if "output_parameters" in meta:
                row["output_parameters"] = meta["output_parameters"]
            if "input_parameters" in meta:
                row["input_parameters"] = meta["input_parameters"]
            if "compatible_triggers" in meta:
                row["compatible_triggers"] = meta["compatible_triggers"]
            results.append(row)

        # Deterministic ordering for clients/testing.
        results.sort(key=lambda x: x["id"])
        return results

    def get_tools_metadata_by_provider(self, slug: str) -> List[Dict[str, Any]]:
        """
        Get tool metadata for a provider slug from `common.tools.providers.<slug>.data`.

        Returns empty list if provider not found.
        """
        if not slug:
            return []
        provider = self._providers.get(slug)
        if provider is None:
            return []

        try:
            module = importlib.import_module(
                f"common.tools.providers.{slug}.data"
            )
        except ModuleNotFoundError:
            # Fallback: use LangChain tool objects (keeps API working even if a provider
            # forgets to add data.py).
            tools = provider.get_all()
            results: List[Dict[str, Any]] = []
            for t in tools:
                tool_id = getattr(t, "name", "") or ""
                row: Dict[str, Any] = {
                    "id": tool_id,
                    "name": tool_id,
                    "description": getattr(t, "description", "") or "",
                }
                sch = _tool_args_json_schema(t)
                if sch is not None:
                    row["parameters"] = sch
                results.append(row)
            results.sort(key=lambda x: x["id"])
            return results

        tools_data = getattr(module, "TOOLS_DATA", None)
        base_list = self._get_tools_metadata_from_dict(tools_data)
        out: List[Dict[str, Any]] = []
        for m in base_list:
            row = dict(m)
            tid = str(row.get("id") or "")
            inst = provider.get_tool_by_name(tid) if tid else None
            sch = _tool_args_json_schema(inst)
            if sch is not None:
                row["parameters"] = sch
            out.append(row)
        return out

    def get_all_tools_metadata(self) -> List[Dict[str, Any]]:
        """Get tool metadata across all providers using their `data.py` dicts."""
        all_tools: List[Dict[str, Any]] = []
        for slug in self.get_provider_names():
            all_tools.extend(self.get_tools_metadata_by_provider(slug))
        # Deterministic ordering
        all_tools.sort(key=lambda x: x["id"])
        return all_tools


# Create global registry instance
registry = Registry()

__all__ = [
    "registry",
    "WebTools",
    "KnowledgeBaseTools",
    "HumanHandoffTools",
    "ShopifyTools",
    "WooCommerceTools",
    "ZendeskTools",
    "HubspotTools",
    "GmailTools",
    "GDriveTools",
    "GCalendarTools",
    "NotionTools",
    "AirtableTools",
    "OutlookTools",
    "SlackTools",
    "MemoryTools",
    "AideTools",
    "CalendlyTools",
    "CalComTools",
    "IThinkLogisticsTools",
    "ShiprocketTools",
    "DelhiveryTools",
    "DTDCTools",
    "WareIQTools",
    "CustomSkillsTools",
]
