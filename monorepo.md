# Monorepo Migration Plan

## Current State

```
ai-backend/
├── app/
│   ├── main.py              # uvicorn app.main:app on port 9005
│   ├── core/                # Config, lifecycle, utils
│   ├── db/
│   │   ├── geo/             # GeoLite2 mmdb
│   │   └── queries/         # .sql files (agent, chat, lead, messages, etc.)
│   ├── infrastructure/      # DB connection clients (postgres, redis, milvus)
│   ├── modules/
│   │   ├── common/          # Shared logic (retrieval, email, token, usage, handoff)
│   │   ├── agents/          # Agent-specific (ReAct graph)
│   │   ├── copilot/
│   │   ├── tools/           # Module-level tool bindings
│   │   ├── voice/
│   │   ├── product_summary/
│   │   └── product_faq_generation/
│   ├── tools/               # 35+ tool providers, registry, utils
│   │   ├── providers/       # aide, hubspot, shopify, gmail, slack, etc.
│   │   ├── registry.py
│   │   └── utils/
│   ├── whatsapp/
│   └── otel_utils.py
├── tests/
├── pyproject.toml           # Single uv project ("widget-server")
├── uv.lock
├── Dockerfile
├── docker-compose.yml
├── run.sh
└── .github/workflows/
    ├── build-and-push.yml
    └── unit-test.yml
```

---

## Target State

```
ai-backend/
├── packages/
│   ├── shared/                        # Shared library (pip name = "shared", importable as `common.*`)
│   │   ├── common/                    # Python package root
│   │   │   ├── __init__.py
│   │   │   ├── infrastructure/        # ← from app/infrastructure/
│   │   │   │   ├── base.py
│   │   │   │   ├── postgres.py
│   │   │   │   ├── redis.py
│   │   │   │   └── milvus.py
│   │   │   ├── modules/               # ← from app/modules/common/
│   │   │   │   ├── retrieval/
│   │   │   │   ├── email/
│   │   │   │   ├── token/
│   │   │   │   ├── usage/
│   │   │   │   ├── _protocols.py
│   │   │   │   ├── settings_protocols.py  # Structural typing for settings
│   │   │   │   └── handoff_client.py      # NestJS handoff (used by tool providers)
│   │   │   ├── integration/           # Service getters (wires modules to infra)
│   │   │   │   └── registry.py        #   get_email, get_retrieval, get_token, get_usage
│   │   │   ├── core/                  # ← portable parts from app/core/
│   │   │   │   ├── credits_calculation.py
│   │   │   │   ├── serialization.py   #   to_dict() from app/core/utils.py
│   │   │   │   └── nestjs_staging_defaults.py
│   │   │   ├── db_queries/            # ← from app/db/queries/ (SQL + lazy loader)
│   │   │   │   ├── __init__.py
│   │   │   │   └── *.sql
│   │   │   ├── tools/                 # ← from app/tools/
│   │   │   │   ├── providers/         #   All 35+ tool providers
│   │   │   │   ├── registry.py
│   │   │   │   ├── slug_map.py
│   │   │   │   ├── interrupt_payload.py
│   │   │   │   ├── crm_ai_form_fields.py
│   │   │   │   └── utils/
│   │   │   └── project_settings.py    # Settings binding for shared services
│   │   └── pyproject.toml             # name = "shared", packages = ["common"]
│   │
│   ├── agent/                         # Existing AI agent service (thin)
│   │   ├── app/
│   │   │   ├── main.py               # Port 9005; calls bind_project_settings()
│   │   │   ├── otel_utils.py         # OpenTelemetry instrumentation
│   │   │   ├── core/
│   │   │   │   ├── config.py          # AppSettings (satisfies shared Protocols)
│   │   │   │   ├── lifecycle.py       # FastAPI lifespan
│   │   │   │   ├── types.py           # Agent/copilot streaming types
│   │   │   │   ├── llm_utils.py       # create_chat_title, ttft_fallback_stream
│   │   │   │   └── utils.py           # Re-exports from shared (backwards compat)
│   │   │   ├── db/
│   │   │   │   └── geo/               # GeoLite2-City.mmdb
│   │   │   └── modules/
│   │   │       ├── common/            # Re-export facade + agent-only clients
│   │   │       │   ├── __init__.py    #   Re-exports get_email, get_retrieval, etc.
│   │   │       │   ├── handoff_client.py      # Re-export from shared
│   │   │       │   ├── conversation_live_client.py
│   │   │       │   └── registry.py            # Re-export from shared
│   │   │       ├── agents/            # ReAct graph, agent logic
│   │   │       ├── copilot/
│   │   │       ├── tools/             # Module-level tool bindings
│   │   │       ├── voice/
│   │   │       ├── whatsapp/
│   │   │       ├── product_summary/
│   │   │       ├── product_faq_generation/
│   │   │       └── routes.py
│   │   ├── tests/
│   │   ├── pyproject.toml            # name = "agent-service", depends on shared
│   │   ├── Dockerfile
│   │   └── run.sh
│   │
│   └── workflow/                      # New workflow backend service
│       ├── workflow_service/          # Python package root (avoids `app` name collision)
│       │   ├── __init__.py
│       │   ├── main.py               # Port 9006; calls bind_project_settings()
│       │   ├── core/                  # Workflow-specific config & lifecycle
│       │   └── modules/
│       │       ├── engine/            # Workflow execution engine
│       │       └── routes.py
│       ├── tests/
│       ├── pyproject.toml            # name = "workflow-service", packages = ["workflow_service"]
│       ├── Dockerfile
│       └── run.sh
│
├── pyproject.toml                    # Root uv workspace
├── uv.lock                          # Single lockfile
├── docker-compose.yml               # Both services
├── .dockerignore
├── .env
├── .github/workflows/
│   ├── agent-build.yml
│   ├── workflow-build.yml
│   └── unit-test.yml
└── docs/
```

---

## What Goes Where

### `packages/shared/common/` — reusable across services

> The pip package is called `shared` (for workspace dependency), but the importable Python package is `common` (the directory name). Hatchling config: `packages = ["common"]`.

| Current Location | New Location | What |
|---|---|---|
| `app/infrastructure/` | `common/infrastructure/` | DB singletons (postgres, redis, milvus) |
| `app/modules/common/retrieval/` | `common/modules/retrieval/` | Dense + sparse retrieval with reranking |
| `app/modules/common/email/` | `common/modules/email/` | Resend email service |
| `app/modules/common/token/` | `common/modules/token/` | JWT credentials + AES-GCM encryption |
| `app/modules/common/usage/` | `common/modules/usage/` | Credit tracking |
| `app/modules/common/_protocols.py` | `common/modules/_protocols.py` | Protocol interfaces |
| `app/modules/common/handoff_client.py` | `common/modules/handoff_client.py` | NestJS handoff client (tool providers depend on it) |
| `app/modules/common/registry.py` | `common/integration/registry.py` | Service getters (tool providers depend on it) |
| `app/tools/` | `common/tools/` | All 35+ tool providers, registry, utils |
| `app/db/queries/*.sql` | `common/db_queries/` | SQL files + lazy-loading cache (shared repos need them) |
| `app/core/credits_calculation.py` | `common/core/credits_calculation.py` | Pure pricing/credits math |
| `app/core/nestjs_staging_defaults.py` | `common/core/nestjs_staging_defaults.py` | NestJS URL/secret fallback defaults |
| `app/core/utils.py` (`to_dict`) | `common/core/serialization.py` | Pure recursive serializer |
| `app/core/utils.py` (`map_slug_*`) | `common/tools/slug_map.py` | Slug → provider name mapping |
| `app/modules/agents/interrupt_payload.py` | `common/tools/interrupt_payload.py` | Tool interrupt payload (tool providers depend on it) |
| *(new)* | `common/project_settings.py` | Settings binding: `bind_project_settings()` |
| *(new)* | `common/modules/settings_protocols.py` | Protocol classes for typed settings |

### Settings decoupling pattern

Shared modules need settings like `OPENAI_API_KEY`, `RESEND_API_KEY`, etc., but `config.py` stays in the agent. Solution: **Protocol-based structural typing + runtime binding**.

```python
# common/modules/settings_protocols.py
class SupportsRetrievalSettings(Protocol):
    OPENAI_API_KEY: str | None
    OPENAI_EMBEDDING_MODEL: str
    ...

# common/project_settings.py
def bind_project_settings(getter: Callable[[], Any]) -> None: ...
def project_settings() -> Any: ...

# agent main.py — binds at startup
from app.core.config import app_settings
from common.project_settings import bind_project_settings
bind_project_settings(lambda: app_settings)
```

### `packages/agent/app/` — agent-specific only

| What stays | Why |
|---|---|
| `core/config.py` | Full AppSettings; satisfies shared Protocol interfaces |
| `core/lifecycle.py` | FastAPI lifespan + graph init (not shared) |
| `core/types.py` | Agent/copilot streaming types |
| `core/llm_utils.py` | `create_chat_title`, `ttft_fallback_stream` (depend on `app_settings`) |
| `core/utils.py` | Thin re-export shim from shared |
| `otel_utils.py` | OpenTelemetry instrumentation + JSON logging |
| `db/geo/` | GeoLite2-City.mmdb binary asset |
| `modules/agents/` | ReAct graph, agent logic |
| `modules/copilot/` | Copilot module |
| `modules/tools/` | Module-level tool bindings |
| `modules/voice/` | OpenAI Whisper transcription |
| `modules/whatsapp/` | WhatsApp text formatting |
| `modules/product_summary/` | Agent-specific feature |
| `modules/product_faq_generation/` | Agent-specific feature |
| `modules/common/__init__.py` | Re-export facade from `common.integration` |
| `modules/common/handoff_client.py` | Re-export from `common.modules.handoff_client` |
| `modules/common/conversation_live_client.py` | NestJS live-conversation notifier |
| `modules/common/registry.py` | Re-export from `common.integration.registry` |
| `main.py` | Agent FastAPI app; calls `bind_project_settings()` |

### `packages/workflow/workflow_service/` — workflow-specific only

> Uses `workflow_service/` as Python package name (not `app/`) to avoid collision with agent.

| What | Why |
|---|---|
| `main.py` | Workflow FastAPI app; calls `bind_project_settings()` |
| `core/` | Workflow-specific config & lifecycle |
| `modules/engine/` | Workflow execution engine — new code |
| `modules/routes.py` | Workflow API routes |

---

## Migration Steps

### Phase 1: Create workspace structure (no logic changes)

1. **Create directory skeleton**
   ```bash
   mkdir -p packages/agent packages/workflow/workflow_service packages/shared/common
   ```

2. **Move existing app → `packages/agent/`**
   ```bash
   mv app/ packages/agent/app/
   mv tests/ packages/agent/tests/
   mv Dockerfile packages/agent/Dockerfile
   mv run.sh packages/agent/run.sh
   ```

3. **Create root `pyproject.toml` as uv workspace**

4. **Create `packages/shared/pyproject.toml`**
   ```toml
   [project]
   name = "shared"
   version = "0.1.0"
   requires-python = ">=3.11"
   dependencies = [...]

   [build-system]
   requires = ["hatchling>=1.26.0"]
   build-backend = "hatchling.build"

   [tool.hatch.build.targets.wheel]
   packages = ["common"]
   ```

5. **Create `packages/agent/pyproject.toml`** with existing deps + shared dependency

6. **Verify:** `uv sync` from root, run tests, nothing breaks

### Phase 2: Extract shared code + settings decoupling

Do it incrementally, test after each step.

**Step 2a — Settings decoupling (do this FIRST)**
- Create `common/project_settings.py` — `bind_project_settings()` / `project_settings()`
- Create `common/modules/settings_protocols.py` — Protocol classes

**Step 2b — Infrastructure (DB clients)**
```bash
mv packages/agent/app/infrastructure/ packages/shared/common/infrastructure/
```
- Update imports: `from app.infrastructure.X` → `from common.infrastructure.X`

**Step 2c — DB queries**
```bash
mv packages/agent/app/db/queries/*.sql packages/shared/common/db_queries/
```
- Create `__init__.py` with lazy-loading query cache
- Keep `packages/agent/app/db/geo/` in agent

**Step 2d — Common modules**
- Move to shared: `retrieval/`, `email/`, `token/`, `usage/`, `_protocols.py`, `handoff_client.py`
- Move `registry.py` → `common/integration/registry.py` (decoupled via `project_settings()`)
- Refactor services to use Protocol-based settings instead of importing `app_settings`
- Keep in agent: `conversation_live_client.py`
- Agent's `modules/common/__init__.py` becomes a re-export facade

**Step 2e — Tools**
```bash
mv packages/agent/app/tools/ packages/shared/common/tools/
```
- Move `interrupt_payload.py` to `common/tools/`
- Update imports: `from app.tools.X` → `from common.tools.X`

**Step 2f — Core (portable only)**
```bash
mv packages/agent/app/core/credits_calculation.py packages/shared/common/core/
mv packages/agent/app/core/nestjs_staging_defaults.py packages/shared/common/core/
```

**Step 2g — Split `app/core/utils.py`**
- Create `common/core/serialization.py` — move `to_dict`
- Create `common/tools/slug_map.py` — move `map_slug_*`
- Create `packages/agent/app/core/llm_utils.py` — move `create_chat_title` + `ttft_fallback_stream`
- Shrink `utils.py` to thin re-export shim

**Step 2h — Wire up agent main.py**
- Add `bind_project_settings(lambda: app_settings)` early in `main.py`

**After each step:** `uv sync && pytest`

### Phase 3: Scaffold workflow service

1. **Create `packages/workflow/pyproject.toml`**
   ```toml
   [project]
   name = "workflow-service"
   ...
   [tool.hatch.build.targets.wheel]
   packages = ["workflow_service"]
   ```

2. **Create `packages/workflow/workflow_service/main.py`** — minimal FastAPI on port 9006

3. **Create `packages/workflow/run.sh`**

4. Workflow can now import shared:
   ```python
   from common.infrastructure.postgres import PostgresClient
   from common.tools.registry import registry
   ```

### Phase 4: Update CI/CD

1. **Split into per-service workflows:** `agent-build.yml`, `workflow-build.yml`
2. **Path filters** include `packages/shared/**`, `pyproject.toml`, `uv.lock`
3. **Update `unit-test.yml`** — both packages

### Phase 5: Update Docker

1. **`packages/agent/Dockerfile`** — build context is repo root
   ```dockerfile
   # Builder: uv sync --package agent-service installs shared into venv
   # Prod: only copy agent source; shared is in venv site-packages
   COPY packages/agent/app /app/app
   CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9005"]
   ```

2. **`packages/workflow/Dockerfile`** — same pattern, port 9006

3. **Root `docker-compose.yml`** — both services, `.env` file

---

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Workspace tool | **uv workspaces** | Already using uv; native support, single lockfile |
| Build backend | **hatchling** | `packages = ["common"]` controls Python package discovery |
| Import prefix | **`from common.*`** | pip name is `shared`, importable package is `common` — no double nesting |
| Settings decoupling | **Protocol + bind_project_settings()** | Shared modules use structural typing; each service binds settings at startup |
| Shared scope | **Infrastructure + modules + tools + db_queries + integration** | Everything tool providers and shared repos need lives in shared |
| Workflow package name | **`workflow_service/`** | Avoids Python `app` module collision with agent |
| Docker shared install | **Via venv** | `uv sync --package X` installs shared; prod stage only copies service source |
| Port allocation | agent=9005, workflow=9006 | Keeps existing agent port unchanged |

---

## Import Convention

```python
# Shared imports (available to all services)
from common.infrastructure.postgres import PostgresClient
from common.infrastructure.redis import RedisClient
from common.modules.retrieval import RetrievalService
from common.modules.token import TokenService
from common.integration import get_email, get_retrieval, get_token, get_usage
from common.tools.registry import registry
from common.tools.providers.hubspot import HubspotTools
from common.core.serialization import to_dict
from common.db_queries import get_agent_query
from common.project_settings import bind_project_settings

# Agent-specific imports (within agent service)
from app.core.config import app_settings
from app.modules.agents.graph import build_graph
from app.modules.common import get_email, get_retrieval  # re-export facade

# Workflow-specific imports (within workflow service)
from workflow_service.main import app
```

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Massive import rewrite in Phase 2 | Do it incrementally (2a→2h), test after each step |
| Circular deps between shared & services | shared never imports from `app.*` or `workflow_service.*`; enforced by Protocol pattern |
| Settings not bound at runtime | `project_settings()` raises `RuntimeError` if not bound — fails fast |
| Breaking agent in production | Keep old CI workflow until new one is verified on staging |
| Docker build context too large | `.dockerignore` excludes tests, docs, IDE files |
| Shared becomes a dumping ground | Rule: shared = reusable across 2+ services |
| `common` name collision | Workspace-only install; no clash with external packages |

---

## Execution Order

1. **Phase 1** — restructure into packages (agent moves, shared is empty shell)
2. **Phase 2** — settings decoupling (2a), then extract shared code (2b→2h)
3. **Phase 3** — scaffold workflow service
4. **Phase 4 + 5** — CI/CD and Docker updates

> Phase 2 comes before Phase 3 because the shared package needs to be populated before the workflow service can meaningfully use it. Settings Protocol pattern (2a) must be done first since all subsequent extractions depend on it.
