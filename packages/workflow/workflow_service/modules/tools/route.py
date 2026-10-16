import logging
from typing import Any

from fastapi import APIRouter, Depends, Header

from common.db_queries import get_workspace_apps_query
from common.infrastructure import get_pg
from common.tools.registry import registry
from workflow_service.core.auth import verify_internal_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["Tools"])


# Icon mapping for known providers
PROVIDER_ICONS: dict[str, str] = {
    "shopify": "ShoppingBag",
    "woocommerce": "ShoppingCart",
    "hubspot": "Users",
    "gmail": "Mail",
    "outlook": "Mail",
    "slack": "MessageSquare",
    "notion": "FileText",
    "airtable": "Table",
    "gdrive": "HardDrive",
    "gcalendar": "Calendar",
    "calendly": "Calendar",
    "calcom": "Calendar",
    "zendesk": "LifeBuoy",
    "aide": "LifeBuoy",
    "shiprocket": "Truck",
    "delhivery": "Truck",
    "dtdc": "Truck",
    "ithinklogistics": "Truck",
    "whatsapp_flow": "MessageCircle",
    "instagram_product": "Instagram",
    "web": "Globe",
    "knowledgebase": "BookOpen",
    "memory": "Brain",
    "human_handoff": "UserCheck",
}

# Color mapping for providers
PROVIDER_COLORS: dict[str, str] = {
    "shopify": "#96bf48",
    "woocommerce": "#7f54b3",
    "hubspot": "#ff7a59",
    "gmail": "#ea4335",
    "outlook": "#0078d4",
    "slack": "#4a154b",
    "notion": "#000000",
    "airtable": "#18bfff",
    "gdrive": "#4285f4",
    "gcalendar": "#4285f4",
    "calendly": "#006bff",
    "calcom": "#292929",
    "zendesk": "#03363d",
    "aide": "#5046e5",
    "shiprocket": "#7c3aed",
    "delhivery": "#e53935",
    "dtdc": "#e53935",
    "ithinklogistics": "#ff6b00",
    "whatsapp_flow": "#25d366",
    "instagram_product": "#e1306c",
}


async def _fetch_workspace_apps(workspace_id: str) -> list[dict]:
    try:
        pg = get_pg()
        query = get_workspace_apps_query()
        async with pg.acquire() as conn:
            rows = await conn.fetch(query, workspace_id)
        return [
            {
                "id": row["id"],
                "connectorId": row["id"],
                "slug": row["slug"] or row["name"].lower(),
                "name": row["name"],
            }
            for row in rows
        ]
    except Exception as e:
        logger.warning("Failed to fetch workspace apps: %s", e)
    return []


@router.get("/available")
async def get_available_tools(
    _auth: bool = Depends(verify_internal_secret),
    x_workspace_id: str = Header(..., alias="x-workspace-id"),
) -> dict[str, Any]:
    """Return tools available for this workspace, grouped by provider.

    Only returns tools for providers whose apps are connected in the workspace.
    Each tool has the info needed to create a CONNECTOR step.
    """
    # Fetch connected apps
    apps = await _fetch_workspace_apps(x_workspace_id)
    connected_slugs = {
        (a.get("slug") or "").lower()
        for a in apps
        if a.get("slug")
    }

    # Build response grouped by provider
    providers: list[dict] = []

    for slug in sorted(connected_slugs):
        tools_meta = registry.get_tools_metadata_by_provider(slug)
        if not tools_meta:
            continue

        providers.append({
            "slug": slug,
            "name": slug.replace("_", " ").title(),
            "icon": PROVIDER_ICONS.get(slug, "Puzzle"),
            "color": PROVIDER_COLORS.get(slug, "#6b7280"),
            "tools": [
                {
                    "action": t["id"],
                    "name": t["name"],
                    "description": t["description"],
                    **({"outputParameters": t["output_parameters"]} if t.get("output_parameters") else {}),
                    **({"inputParameters": t["input_parameters"]} if t.get("input_parameters") else {}),
                    **({"compatibleTriggers": t["compatible_triggers"]} if t.get("compatible_triggers") else {}),
                }
                for t in tools_meta
            ],
        })

    return {
        "providers": providers,
        "totalTools": sum(len(p["tools"]) for p in providers),
    }


@router.get("/all")
async def get_all_tools(
    _auth: bool = Depends(verify_internal_secret),
) -> dict[str, Any]:
    """Return ALL tools from the registry (regardless of workspace connections).

    Useful for showing what's possible before connecting an app.
    """
    providers: list[dict] = []

    for slug in sorted(registry.get_provider_names()):
        tools_meta = registry.get_tools_metadata_by_provider(slug)
        if not tools_meta:
            continue

        providers.append({
            "slug": slug,
            "name": slug.replace("_", " ").title(),
            "icon": PROVIDER_ICONS.get(slug, "Puzzle"),
            "color": PROVIDER_COLORS.get(slug, "#6b7280"),
            "tools": [
                {
                    "action": t["id"],
                    "name": t["name"],
                    "description": t["description"],
                    **({"outputParameters": t["output_parameters"]} if t.get("output_parameters") else {}),
                    **({"inputParameters": t["input_parameters"]} if t.get("input_parameters") else {}),
                    **({"compatibleTriggers": t["compatible_triggers"]} if t.get("compatible_triggers") else {}),
                }
                for t in tools_meta
            ],
        })

    return {
        "providers": providers,
        "totalTools": sum(len(p["tools"]) for p in providers),
    }
