# API Reference

Base URL: `http://<host>:9005`

All protected endpoints expect a Bearer JWT in the `Authorization` header.

---

## Health

### `GET /health`

No auth required.

**Response `200`**
```json
{"status": "healthy", "service": "assistant-api"}
```

### `GET /ready`

No auth required. Returns `200` when startup is complete and configured
dependencies are ready, or `503` when a required check fails. Unconfigured
Postgres, Redis, and Milvus are marked `skipped`. The Milvus check confirms
client initialization; it does not query the vector database.

```json
{
  "status": "ready",
  "checked_at": "2026-10-16T12:00:00+00:00",
  "checks": {
    "checkpointer": {"status": "ok", "duration_ms": 0.0},
    "postgres": {"status": "skipped", "duration_ms": 0.0},
    "redis": {"status": "skipped", "duration_ms": 0.0},
    "milvus_client": {"status": "skipped", "duration_ms": 0.0}
  }
}
```

The response contains status and timings only; it does not include URLs,
credentials, or exception details. Results are cached briefly to limit probe
load. Run `python scripts/doctor.py --base-url http://127.0.0.1:9005` for a
terminal summary or add `--json` for machine-readable output.

---

## Agents

### `POST /api/agents/stream`

Stream a chat response as Server-Sent Events (SSE).

**Request body**

```typescript
{
  message:       string          // user message
  chatId:        string          // client-generated chat ID
  agentId:       string          // agent configuration ID
  sessionId:     string          // UUID; used as LangGraph thread_id
  channel?:      "web" | "whatsapp"   // default "web"
  isPreview?:    boolean         // default true
  resumeConfig?: {               // only for HITL resumption
    interrupt: true
    choice:    boolean           // true = approve, false = reject
  }
  websiteInfo?:  string
  customerEmail?: string
}
```

**Response** `Content-Type: text/event-stream`

Each line is `data: <JSON>\n\n`. Event types:

| `type` | Payload | Notes |
|---|---|---|
| `chat_message` | `{ type, text }` | Incremental LLM token; newlines escaped as `\\n` |
| `tool_call` | `{ type, text, tool }` | Tool is executing; `text` is a user-visible status |
| `interrupt` | `{ type, text, interrupt_data }` | HITL pause; send `resumeConfig` to continue |
| `end_of_chat` | JSON array of artifact objects | Final artifacts from all tool calls |
| `human_handoff` | `{ type, text, handoff_triggered: true }` | Agent escalated to human |
| `chat_error` | `{ type, text }` | Error string; service retries 3× before emitting |

**Example stream**

```
data: {"type":"chat_message","text":"Looking up your order"}

data: {"type":"tool_call","text":"Searching Shopify...","tool":"shopify_get_order"}

data: {"type":"chat_message","text":" — order #1042 is on its way."}

data: {"type":"end_of_chat","artifacts":[{"order_id":"1042","status":"shipped"}]}
```

**HITL flow**

1. Stream returns an `interrupt` event with `interrupt_data`.
2. Display the prompt to the user; collect approval.
3. Resend the same `chatId` + `sessionId` with `resumeConfig: { interrupt: true, choice: <bool> }`.
4. The graph resumes from the paused node.

---

### `POST /api/agents/chat`

Non-streaming version. Returns the full response after the agent finishes.

Same request body as `/api/agents/stream`. Response is a JSON object with the assembled message and artifacts.

---

### `GET /api/agents/{agent_id}/config`

Fetch the full agent configuration (persona, connected apps, enabled skills, knowledge base entities).

**Response `200`** — agent config object (cached in Redis).

---

### `DELETE /api/agents/{agent_id}/config/cache`

Invalidate the Redis cache for an agent's config. Call this after modifying agent settings.

**Response `200`**

---

## Copilot

### `POST /api/copilot/stream`

Internal dashboard copilot. SSE streaming, same event protocol as the agent.

**Request body**

```typescript
{
  message:      string
  chatId:       string
  apps?:        string[]     // provider slugs to enable (e.g. ["gmail","notion"])
  fileIds?:     string[]     // knowledge file IDs to scope search
  isNewChat?:   boolean
  kids?:        string[]     // knowledge entity IDs
  webSearch?:   boolean      // enable Tavily web search
  userId?:      string
}
```

Key differences from the agent endpoint:
- No conversation persistence (in-memory per request)
- Langfuse tracing always enabled
- No domain allowlist check

---

## Tools introspection

### `GET /api/tools`

List all registered provider slugs and their tools.

**Response `200`**

```json
{
  "providers": ["web", "shopify", "zendesk", "hubspot", "..."],
  "tools": [
    {
      "id": "web_search",
      "name": "web_search",
      "description": "Search the web for current information."
    }
  ]
}
```

---

### `GET /api/tools/{provider}`

List tools for a specific provider slug (e.g. `shopify`, `gmail`).

**Response `200`** — same shape as above, scoped to the provider.

**Response `404`** — provider slug not registered.

---

## Error responses

All errors return:

```json
{
  "detail": "human-readable message"
}
```

| Status | Meaning |
|---|---|
| `400` | Bad request / validation error |
| `401` | Missing or invalid JWT |
| `403` | Domain not allowed for this agent |
| `404` | Resource not found |
| `500` | Internal server error (check Sentry) |
