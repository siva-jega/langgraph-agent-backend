# Tool Providers

The tool system is a registry of async functions that the LLM can call during agent execution. Each integration lives in its own directory under `app/tools/providers/`.

## Registry

`app/tools/registry.py` exports a global `Registry` singleton. It is populated at import time — every provider registers itself when the module is first imported.

```python
from app.tools.registry import registry

registry.get_tools_by_provider("shopify")       # → [Tool, ...]
registry.get_tool_by_name("shopify_get_order")  # → Tool
registry.get_provider_names()                   # → ["shopify", "gmail", ...]
registry.get_tools_metadata_by_provider("shopify")  # → [{"id": ..., "name": ..., "description": ...}]
```

## Registered providers

| Slug | Integration |
|---|---|
| `web` | Tavily web search |
| `knowledgebase` | Hybrid RAG (Milvus dense + sparse + Cohere rerank) |
| `shopify` | Product search, order lookup |
| `woocommerce` | WooCommerce product/order APIs |
| `zendesk` | Support ticket creation |
| `hubspot` | CRM: contacts, deals, companies |
| `gmail` | Email send/read (OAuth) |
| `gdrive` | Google Drive file access (OAuth) |
| `gcalendar` | Google Calendar events (OAuth) |
| `notion` | Notion pages and databases |
| `airtable` | Airtable bases and records |
| `outlook` | Outlook email (OAuth) |
| `slack` | Slack messaging |
| `memory` | Mem0 long-term memory |
| `aide` | Internal platform integration |
| `human_handoff` | Escalate to human agent |
| `calendly` | Calendly scheduling links |
| `calcom` | Cal.com booking (OAuth) |
| `ithinklogistics` | Logistics shipment tracking |

## Provider directory structure

```
app/tools/providers/<slug>/
├── __init__.py      # Provider class: get_all(), get_tool_by_name()
├── tools.py         # @tool decorated async functions
├── models.py        # Pydantic schemas for tool inputs/outputs
└── data.py          # TOOLS_DATA dict for introspection API
```

### `tools.py` — tool implementation

```python
from langchain_core.tools import tool
from app.tools.utils.writer import emit_update

@tool(response_format="content_and_artifact")
async def shopify_get_order(order_id: str, config: RunnableConfig) -> tuple[str, dict]:
    """Look up a Shopify order by ID."""
    await emit_update(config, "Fetching order from Shopify...")
    # ... call Shopify API ...
    return f"Order {order_id} status: shipped", {"order_id": order_id, "status": "shipped"}
```

Key points:
- `response_format="content_and_artifact"` — returns a `(text, artifact)` tuple
- `text` is returned to the LLM for reasoning
- `artifact` is accumulated and sent to the client as `end_of_chat`
- `await emit_update(config, message)` sends a `tool_call` SSE event to the client in real time
- `config: RunnableConfig` is injected by LangGraph; access agent config via `config["configurable"]`

### `data.py` — metadata for introspection

```python
TOOLS_DATA = {
    "shopify_get_order": {
        "id": "shopify_get_order",
        "name": "shopify_get_order",
        "description": "Look up a Shopify order by ID.",
    }
}
```

### `__init__.py` — provider class

```python
from app.tools.providers.shopify.tools import shopify_get_order

class ShopifyTools:
    @staticmethod
    def get_all() -> list:
        return [shopify_get_order]

    @staticmethod
    def get_tool_by_name(name: str):
        tools = {t.name: t for t in ShopifyTools.get_all()}
        return tools.get(name)
```

## OAuth token access

For OAuth integrations (Gmail, Google Drive, Outlook, etc.), tools retrieve tokens via the shared token manager:

```python
from app.modules.common.token.service import get_access_token

token = await get_access_token(agent_id, "gmail", config)
```

The token manager:
1. Checks Redis cache for a valid token
2. Falls back to Postgres (encrypted, AES)
3. Refreshes via the NestJS auth service if near expiry
4. Returns a plaintext access token

## Adding a new provider

1. **Create the directory**

   ```bash
   mkdir app/tools/providers/myprovider
   touch app/tools/providers/myprovider/__init__.py
   touch app/tools/providers/myprovider/tools.py
   touch app/tools/providers/myprovider/models.py
   touch app/tools/providers/myprovider/data.py
   ```

2. **Implement tools in `tools.py`**

   Follow the pattern above. Use `emit_update()` for progress events. Return `(str, dict)`.

3. **Add metadata to `data.py`**

   Fill in `TOOLS_DATA` with `id`, `name`, `description` for each tool.

4. **Expose via `__init__.py`**

   Implement `get_all()` and `get_tool_by_name()`.

5. **Register with the global registry**

   In `app/tools/registry.py`, import and register the new provider:

   ```python
   from app.tools.providers.myprovider import MyProviderTools
   registry.register("myprovider", MyProviderTools)
   ```

6. **Test**

   ```bash
   curl http://localhost:9005/api/tools/myprovider
   ```

   The new tools should appear in the response.

## Knowledgebase tool (RAG)

The `knowledgebase` provider is special — it performs hybrid search over the agent's knowledge base in Milvus. It is automatically enabled when the agent config has `entity_ids` or `folder_ids`. No explicit app or skill configuration is needed.

Search pipeline: dense embedding (OpenAI `text-embedding-3-large`, 3072 dims) + sparse BM25 → merge + deduplicate → optional Cohere rerank → return top-k chunks.

Source: `app/modules/common/retrieval/service.py`
