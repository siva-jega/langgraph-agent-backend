from __future__ import annotations

from common.app_events.shopify import SHOPIFY_EVENT_SCHEMAS

APP_EVENT_REGISTRY: dict[str, dict[str, list[dict]]] = {
    "shopify": SHOPIFY_EVENT_SCHEMAS,
}


def get_event_schema(app_slug: str, event_type: str) -> list[dict] | None:
    return APP_EVENT_REGISTRY.get(app_slug, {}).get(event_type)
