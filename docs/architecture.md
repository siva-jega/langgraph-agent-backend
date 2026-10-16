# Architecture

## Overview

The backend is a single FastAPI service that exposes three public subsystems: a customer-facing **Agent**, an internal **Copilot**, and a **Tools introspection** API. All three share a common infrastructure layer (Postgres, Redis, Milvus) and a pluggable tool registry.

```
                        ┌─────────────────────────────┐
                        │          FastAPI             │
                        │  /api/agents   /api/copilot  │
                        │  /api/tools    /health        │
                        └────────────┬────────────────┘
                                     │
              ┌──────────────────────┼───────────────────────┐
              │                      │                       │
     ┌────────▼──────┐     ┌─────────▼──────┐    ┌─────────▼──────┐
     │  Agents module │     │ Copilot module  │    │  Tools module  │
     │  (LangGraph +  │     │  (LangGraph,    │    │  (metadata     │
     │  checkpointer) │     │  no persist)    │    │   introspection│
     └────────┬──────┘     └─────────┬──────┘    └────────────────┘
              │                      │
              └──────────┬───────────┘
                         │
              ┌──────────▼───────────┐
              │    Tool Registry     │
              │  (20+ providers)     │
              └──────────┬───────────┘
                         │
     ┌───────────────────┼──────────────────┐
     │                   │                  │
┌────▼─────┐   ┌─────────▼──┐   ┌──────────▼──┐
│ Postgres  │   │   Redis    │   │   Milvus    │
│ (app data │   │  (cache,   │   │  (vectors,  │
│  + state) │   │   tokens)  │   │    RAG)     │
└───────────┘   └────────────┘   └─────────────┘
```

## Key design decisions

### Single-process, async-first
All I/O (DB, Redis, Milvus, LLM APIs) is async. Infrastructure clients are module-level singletons initialized once at startup via `app/core/lifecycle.py`.

### LangGraph for agent control flow
Agent logic is a compiled `StateGraph` (ReAct loop). Using LangGraph means:
- The graph is explicit and inspectable
- PostgreSQL checkpointing gives per-conversation persistence with no custom serialization
- HITL (Human-in-the-Loop) is a first-class primitive (`interrupt()` / `resume`)

### Separation of agent and copilot
The customer-facing agent (`modules/agents/`) persists state across sessions and enforces domain allowlists. The internal copilot (`modules/copilot/`) is ephemeral (MemorySaver), always-traced via Langfuse, and used by dashboard users.

### Tool registry as a singleton
All tool providers register at import time. Routes and LangGraph nodes look up tools by slug at runtime, so enabling/disabling integrations per agent is purely a data concern — no code change needed.

### Raw SQL over ORM
All database queries are `.sql` files under `app/db/queries/`. This keeps SQL readable and avoids ORM abstraction leaking into business logic. The asyncpg pool is exposed directly.

## Module map

| Path | Responsibility |
|---|---|
| `app/main.py` | FastAPI app factory, CORS, Sentry, OTEL, route mounting |
| `app/core/config.py` | `AppSettings` — single source of truth for all env vars |
| `app/core/lifecycle.py` | Infrastructure startup/shutdown (Postgres, Redis, Milvus, GeoIP, graphs) |
| `app/core/types.py` | Shared dataclasses (`AgentStreamResponse`, `MessageRole`, etc.) |
| `app/core/credits_calculation.py` | Token-count → billable credit conversion |
| `app/infrastructure/` | Singleton clients: Postgres, Redis, Milvus |
| `app/db/queries/` | Raw SQL files |
| `app/modules/agents/` | Customer-facing agent: route, service, repository, LangGraph graph |
| `app/modules/copilot/` | Internal copilot: route, service, repository, LangGraph graph |
| `app/modules/tools/` | Tool metadata API (no execution) |
| `app/modules/common/` | Shared services: retrieval (RAG), OAuth tokens, usage, email |
| `app/tools/registry.py` | Global `Registry` singleton |
| `app/tools/providers/<slug>/` | One directory per integration |

## Data flow — agent chat request

```
POST /api/agents/stream
  │
  ├─ route.py: validate domain allowlist, GeoIP lookup
  │
  ├─ service.py: fetch agent config (Redis → Postgres)
  │             build tool list from apps + skills config
  │
  ├─ graph.py: LangGraph StateGraph.stream()
  │   ├─ trim node   → keep last 5 messages + SystemMessage
  │   ├─ llm node    → call LLM with bound tools
  │   └─ tools node  → execute tool calls, emit SSE updates
  │       └─ (loop until finish_reason == "stop")
  │
  ├─ SSE events → client (chat_message, tool_call, interrupt, end_of_chat)
  │
  └─ post-stream: save messages to Postgres, deduct credits
```

## Observability

| Concern | Tool |
|---|---|
| Errors / crash reports | Sentry (`SENTRY_DSN`) |
| LLM trace / cost | Langfuse |
| Service instrumentation | OpenTelemetry (FastAPI + asyncpg + requests) |
| Structured logs | Python `logging` to stdout |
