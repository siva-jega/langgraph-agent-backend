"""Tools module: return tool metadata by provider from the global registry."""

from typing import Any, Dict, List

from common.tools.registry import registry


def get_tools_by_provider_slug(slug: str) -> List[Dict[str, Any]]:
    """Return tools metadata for the given provider slug.

    Each tool includes: `id`, `name`, `description`.
    """
    return registry.get_tools_metadata_by_provider(slug)


def get_all_tools_metadata() -> List[Dict[str, Any]]:
    """Return tools metadata across all providers (aggregated)."""
    return registry.get_all_tools_metadata()


def get_available_provider_slugs() -> List[str]:
    """Return all registered provider slugs."""
    return registry.get_provider_names()
