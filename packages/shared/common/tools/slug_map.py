"""Map dashboard app slugs to tool registry provider names (no app_settings dependency)."""

from __future__ import annotations

from typing import List


def map_slug_to_provider_name(slug: str) -> str:
    slug_lower = slug.lower()
    slug_to_provider = {
        "shopify": "shopify",
        "googledrive": "gdrive",
        "gmail": "gmail",
        "github": "github",
        "calendar": "calendar",
        "notion": "notion",
        "slack": "slack",
        "jira": "jira",
        "hubspot": "hubspot",
        "confluence": "confluence",
        "outlook": "outlook",
        "web_search": "web_search",
    }
    return slug_to_provider.get(slug_lower, slug_lower)


def map_slugs_to_provider_names(slugs: List[str]) -> List[str]:
    return [map_slug_to_provider_name(slug) for slug in slugs]
