import logging
from typing import Any

from common.db_queries import get_workspace_apps_query
from common.infrastructure import get_pg
from common.tools.registry import registry
from workflow_service.engine.expression import resolve_dict

logger = logging.getLogger(__name__)


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
        logger.error("Failed to fetch workspace apps: %s", e)
        return []


class ConnectorStepExecutor:
    """Executes a CONNECTOR step by invoking a tool from the shared tools registry.

    Expected configJson shape:
    {
        "provider": "shopify",          # Provider slug
        "action": "get_order_status",   # Tool name
        "params": {                     # Tool arguments (expressions resolved)
            "order_id": "{{ $trigger.inputData.orderId }}",
            ...
        },
        "workspaceId": "...",           # Set by the DAG executor from the workflow
        "userId": "...",                # Owner/creator of the workflow
        "apps": [...]                   # Optional: pre-populated apps list
    }
    """

    async def execute(
        self, config: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        provider_slug = config.get("provider", "")
        action_name = config.get("action", "")
        params = config.get("params") or {}
        workspace_id = config.get("workspaceId", "")
        user_id = config.get("userId", "")
        apps = config.get("apps")
        entity_ids = config.get("entityIds") or []
        folder_ids = config.get("folderIds") or []

        if not provider_slug or not action_name:
            raise ValueError(
                f"CONNECTOR step requires 'provider' and 'action' in configJson, "
                f"got provider={provider_slug!r}, action={action_name!r}"
            )

        # Look up the tool
        tool = registry.get_tool_by_name(action_name)
        if tool is None:
            # Try provider-scoped lookup
            provider_tools = registry.get_tools_by_provider(provider_slug)
            for t in provider_tools:
                if t.name == action_name:
                    tool = t
                    break

        if tool is None:
            raise ValueError(
                f"Tool '{action_name}' not found in provider '{provider_slug}'. "
                f"Available providers: {registry.get_provider_names()}"
            )

        # Resolve expression templates in params
        resolved_params = resolve_dict(params, context)

        # Fetch workspace apps if not provided
        if not apps and workspace_id:
            apps = await _fetch_workspace_apps(workspace_id)

        # Build LangChain RunnableConfig
        runnable_config = {
            "configurable": {
                "user_id": user_id,
                "apps": apps or [],
                "entity_ids": entity_ids,
                "folder_ids": folder_ids,
                "workspace_id": workspace_id,
                "workflow_context": context,
            }
        }

        logger.info(
            "CONNECTOR: invoking %s/%s with params=%s",
            provider_slug,
            action_name,
            list(resolved_params.keys()),
        )

        # Invoke the tool
        result = await tool.ainvoke(resolved_params, runnable_config)

        # Normalize result to a dict for outputData
        if isinstance(result, tuple):
            # content_and_artifact format: (content, artifact)
            content, artifact = result
            output = {
                "content": _serialize(content),
                "artifact": _serialize(artifact),
            }
        elif isinstance(result, dict):
            output = result
        elif isinstance(result, list):
            output = {"items": result}
        elif isinstance(result, str):
            output = {"result": result}
        else:
            output = {"result": str(result)}

        return output


def _serialize(obj: Any) -> Any:
    """Convert tool result objects to JSON-serializable form."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    # Pydantic model or dataclass
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: _serialize(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    return str(obj)
