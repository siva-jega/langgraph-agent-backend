# Agent Flow

This document covers the internals of the LangGraph-based ReAct agent used for both the customer-facing chat agent and the internal copilot.

## Graph structure

```
START
  │
  ▼
trim_messages_node
  │  keep last 5 messages; always keep SystemMessage
  ▼
llm_node
  │  call LLM with tools dynamically bound from agent config
  │
  ├─ finish_reason == "tool_calls"
  │       │
  │       ▼
  │   tools_node
  │       │  execute each tool call; emit SSE updates
  │       │
  │       └──────────► trim_messages_node (loop)
  │
  └─ finish_reason == "stop"
          │
          ▼
         END
```

Source: `app/modules/agents/graph/`

## Node details

### `trim_messages_node`

Trims the conversation history to the last 5 messages before each LLM call. The `SystemMessage` is always preserved as an anchor. This bounds context size regardless of conversation length.

### `llm_node`

Dynamically binds the tool set to the LLM based on the agent's runtime config:

- **Apps** (e.g. `shopify`, `gmail`) → all tools from that provider are bound
- **Skills** (explicit `toolName` references) → specific individual tools
- **Knowledge base** → if `entity_ids` or `folder_ids` are present, the hybrid search tool is added

The LLM is selected per-agent via the `model` field in the config (e.g. `gpt-4o-mini`, `gemini-2.5-flash`).

### `tools_node`

Executes tool calls returned by the LLM. Each tool:
1. Receives structured arguments from the LLM
2. Calls the external API (Shopify, Gmail, etc.)
3. Emits intermediate status via `emit_update()` → forwarded to the client as `tool_call` SSE events
4. Returns `(text_result, artifact_dict)` — text goes back to the LLM; artifact is accumulated for `end_of_chat`

## Agent configuration binding

The LangGraph `configurable` dict carries per-request context through the graph:

| Key | Type | Description |
|---|---|---|
| `thread_id` | string | Maps to `chatId` — used for Postgres checkpointing |
| `entity_ids` | list[str] | Knowledge base entity IDs for RAG scoping |
| `folder_ids` | list[str] | Knowledge base folder IDs for RAG scoping |
| `apps` | list[str] | Enabled integration slugs |
| `skills` | list[SkillConfig] | Specific tool names to enable |
| `model` | string | LLM model identifier |
| `owner_email` | string | Agent creator email (used in prompts/tools) |
| `human_handoff_enabled` | bool | Whether HITL is available |

## HITL (Human-in-the-Loop)

The agent supports pausing mid-execution to get human approval before performing a sensitive action.

### How it works

1. A tool calls LangGraph's `interrupt()` with a description of the pending action.
2. The graph checkpoint is saved to Postgres; execution pauses.
3. The service emits an `interrupt` SSE event with `interrupt_data` to the client.
4. The client displays the confirmation UI to the end user.
5. The user approves or rejects.
6. The client sends a new `POST /api/agents/stream` with:
   ```json
   {
     "resumeConfig": { "interrupt": true, "choice": true }
   }
   ```
7. The service calls `graph.run()` with the resume input; LangGraph restores the checkpoint and continues (or skips) the action.

### Enabling HITL

Set `humanHandoffEnabled = true` on the agent in the database. The `human_handoff` tool provider handles the escalation to a human agent (separate from the interrupt/resume flow above).

## System prompt

The system prompt is built dynamically in `app/modules/agents/graph/prompt.py`. It includes:
- Agent persona (name, description, instructions)
- Available tools and their purpose
- Constraints (e.g. stay on topic, language, tone)
- Context injected at runtime (customer email, website info)

## Conversation persistence

The agent uses `langgraph-checkpoint-postgres` with `CHECKPOINTER_URL`. Each `(sessionId, agentId)` pair is a unique thread. The copilot uses `MemorySaver` (in-memory, no persistence).

After the stream completes, `service.py` explicitly saves the full conversation to the `AgentChatMessages` table (including token counts, response times, geographic location on first message).

## Retry behavior

The service wraps the graph invocation in a retry loop (3 attempts, exponential backoff). On each failure, a `chat_error` SSE event is emitted. After all retries are exhausted, the error is surfaced to the client and logged to Sentry.

## WhatsApp channel

When `channel == "whatsapp"`:
- Markdown is stripped from `chat_message` text
- Links are normalized to plain URLs
- The same graph runs; only the output formatting changes
