# WhatsApp and Instagram Chat Storage in `ai-backend` (`staging`)

## Scope

This document traces how `ai-backend` on local `staging` handles Meta-channel conversations, focusing on:

- how WhatsApp and Instagram chats are stored
- how `sessionId` is used and persisted
- how `chatId` is expected, interpreted, and linked to DB records

## Short answer

`ai-backend` does not generate the incoming `chatId` or `sessionId` for WhatsApp/Instagram requests.

- `chatId` is required from caller and is passed through to the DB as `AgentChats.id`.
- `sessionId` is required from caller and must already be a valid UUID.
- `ai-backend` creates or reuses an `AgentChatSessions` row from `(agentId, sessionId)` and then links that row back to `AgentChats.sessionId`.

For Meta channels, both WhatsApp and Instagram use the same persistence path. Main differences are channel enum, display title, and WhatsApp-specific response shaping.

## Entry points

### Request schema

`ChatRequest` requires:

- `chatId`
- `agentId`
- `sessionId`
- `channel`

Important details:

- `sessionId` is validated as UUID at request-model level.
- `chatId` is mandatory for non-preview requests.

Code:

- `app/modules/agents/models.py`

Snippet:

```python
class ChatRequest(BaseModel):
    message: str
    chatId: str
    agentId: str
    sessionId: str
    channel: Literal["web", "whatsapp", "instagram", "messenger"] = "web"

    @field_validator("sessionId")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        try:
            UUID(v)
        except ValueError:
            raise ValueError("sessionId must be a valid UUID")
        return v

    def model_post_init(self, __context) -> None:
        if self.resumeConfig and self.resumeConfig.interrupt:
            pass
        elif not self.message.strip():
            raise ValueError("Message cannot be empty")

        if not self.chatId:
            raise ValueError("Chat ID is required for non-preview requests")
```

### HTTP routes

Two routes accept these values and forward them unchanged:

- `POST /agents/stream`
- `POST /agents/chat`

Both routes pass request `chatId` and `sessionId` directly into service layer. No generation happens in route layer.

Code:

- `app/modules/agents/route.py`

Snippet:

```python
async for chunk in process_chat_request(
    message=payload.message,
    chat_id=payload.chatId,
    session_id=payload.sessionId,
    agent_id=payload.agentId,
    is_preview=payload.isPreview,
    channel=payload.channel or "web",
    sender_phone=payload.senderPhone,
    sender_display_name=payload.senderDisplayName,
):
    yield chunk
```

## Chat ID lifecycle

### 1. `chatId` comes from caller

`ChatRequest` only validates presence of `chatId`; it does not build one.

For Meta flows, service code contains one key assumption:

- comment says Meta handler uses `integrationId:waId` as `chatId`
- helper `_integration_id_from_whatsapp_chat_id()` splits on first `:`
- left side becomes `integrationId`

This means expected Meta-style chat key is:

```text
<AgentIntegration.id>:<waId-or-customer-identifier>
```

Examples:

```text
5d2b...:919876543210
6a11...:17841400000000000
```

In practice:

- full incoming `chatId` becomes `AgentChats.id`
- first segment before `:` becomes `AgentChats.integrationId` for WhatsApp/Instagram saves

Code:

- `app/modules/agents/service.py` (`_integration_id_from_whatsapp_chat_id`)

Snippet:

```python
def _integration_id_from_whatsapp_chat_id(chat_id: str) -> Optional[str]:
    """Meta handler uses `integrationId:waId` as chatId; first segment is AgentIntegration id."""
    if ":" not in chat_id:
        return None
    left = chat_id.split(":", 1)[0].strip()
    return left or None
```

Important:

- this does not create `chatId`
- this only interprets existing Meta-style `chatId`
- full `chatId` still remains primary chat key

### 2. `chatId` becomes graph/thread identity

Inside agent graph config:

- `thread_id = chat_id`
- `chat_id = chat_id`
- `langfuse_session_id = chat_id`

So same caller-supplied `chatId` is reused as:

- conversation identity in graph/checkpoint flow
- chat key for handoff/live-conversation APIs
- Langfuse session identifier

Code:

- `app/modules/agents/graph/graph.py`

Snippet:

```python
config = {
    "configurable": {
        "thread_id": chat_id,
        "chat_id": chat_id,
        "session_id": session_id,
        "langfuse_session_id": chat_id,
    },
}
```

### 3. `chatId` becomes DB primary key for chat

`save_conversation()` executes SQL from `app/db/queries/messages.sql`.

That SQL:

- inserts into `AgentChats`
- sets `id = $1` (`chat_id`)
- upserts on conflict by chat ID

So chat storage is keyed by caller-supplied `chatId`, not by server-generated UUID.

Storage SQL snippet:

```sql
INSERT INTO "AgentChats" (
  id,
  "sessionId",
  title,
  "userId",
  "agentId",
  channel,
  "integrationId",
  "createdAt",
  "updatedAt"
)
VALUES (
  $1,   -- chat_id
  NULL, -- sessionId FK set later
  COALESCE(NULLIF($4, ''), 'Untitled Chat'),
  NULLIF($5, ''),
  NULLIF($6, ''),
  $12::"AgentChatChannel",
  NULLIF($13, ''),
  NOW(),
  NOW()
)
ON CONFLICT (id) DO UPDATE SET
  channel = EXCLUDED.channel,
  "integrationId" = COALESCE(NULLIF(EXCLUDED."integrationId", ''), "AgentChats"."integrationId"),
  "updatedAt" = NOW();
```

## Session ID lifecycle

### 1. `sessionId` comes from caller

`sessionId` is required in request model and must already be a valid UUID.

No code in request route or service creates it.

Proof snippet:

```python
@field_validator("sessionId")
@classmethod
def validate_session_id(cls, v: str) -> str:
    try:
        UUID(v)
    except ValueError:
        raise ValueError("sessionId must be a valid UUID")
    return v
```

### 2. `sessionId` is passed through as logical client session key

`process_chat_request()` and non-streaming flow pass request `session_id` through into repository save path unchanged.

SQL comment in `messages.sql` is explicit:

- incoming `$3` is client session key
- it is not written directly to `AgentChats.sessionId`
- it is used later by `_resolve_and_link_session()`

### 3. Backend creates internal `AgentChatSessions.id`

Repository method `_resolve_and_link_session()` does actual session-row creation:

1. looks up `AgentChatSessions` by:
   - `agentId`
   - `clientSessionKey = session_id`
2. if found:
   - reuses existing row `id`
3. if missing:
   - generates new UUID with `uuid.uuid4()`
   - inserts new `AgentChatSessions` row
   - stores incoming request `sessionId` in `clientSessionKey`
4. updates `AgentChats.sessionId` with internal `AgentChatSessions.id`

So there are two session identifiers in play:

- external session key: request `sessionId`
- internal DB FK: `AgentChatSessions.id`

Creation and lookup snippet:

```python
row = await conn.fetchrow(
    'SELECT id FROM "AgentChatSessions" '
    'WHERE "agentId" = $1 AND "clientSessionKey" = $2 LIMIT 1',
    agent_id,
    session_id,
)
if row:
    session_pk = row["id"]
else:
    import uuid as _uuid

    session_pk = str(_uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO "AgentChatSessions"
          (id, "workspaceId", "agentId", channel, "clientSessionKey", "createdAt", "updatedAt")
        VALUES ($1, $2, $3, $4::"AgentChatChannel", $5, NOW(), NOW())
        ON CONFLICT ("agentId", "clientSessionKey") DO NOTHING
        """,
        session_pk,
        workspace_id,
        agent_id,
        channel,
        session_id,
    )

await conn.execute(
    'UPDATE "AgentChats" SET "sessionId" = $1 WHERE id = $2',
    session_pk,
    chat_id,
)
```

## How storage works for WhatsApp and Instagram

### Shared persistence path

For both channels, service builds metadata then calls repository save:

- channel mapped to enum:
  - `whatsapp` -> `WHATSAPP`
  - `instagram` -> `INSTAGRAM`
- display title derived from sender name/phone when available
- `integrationId` derived from `chatId` prefix

Both streaming and non-streaming flows ultimately persist with same repository method:

- `AgentsRepository.save_conversation()`

Repository save snippet:

```python
await conn.execute(
    query,
    chat_id,
    "AGENT",
    session_id or "",
    title_arg,
    user_id,
    agent_id or "",
    messages_jsonb,
    latitude,
    longitude,
    country,
    city,
    db_channel,
    (integration_id or "").strip(),
)

if session_id and agent_id:
    await self._resolve_and_link_session(
        conn, chat_id, agent_id, session_id, db_channel
    )
```

### Non-streaming Meta path

Code comment states:

- Meta WhatsApp / Instagram webhooks use non-streaming path

When channel is `whatsapp` or `instagram`, non-streaming code:

- normalizes assistant text for plain-text channels
- persists chat/messages when request is not preview and conversation completed normally

### DB writes

Persistence creates or updates:

#### `AgentChats`

Stored fields include:

- `id = chatId`
- `sessionId = NULL` initially, later replaced with `AgentChatSessions.id`
- `title`
- `userId`
- `agentId`
- geo fields if available
- `channel`
- `integrationId`

Upsert behavior:

- existing chat row reused if same `chatId`
- `channel` refreshed from latest write
- `integrationId` filled when available
- title updated only if non-empty title provided

#### `AgentChatMessages`

Each save writes JSON-expanded rows for:

- user message
- assistant message

Stored message fields include:

- `agentChatId = chatId`
- `message`
- `role`
- `sources`
- `tokenConsume`
- `extraInfo`
- `responseTime`

Messages are timestamped from common base time with millisecond offsets.

Message storage SQL snippet:

```sql
INSERT INTO "AgentChatMessages" (
  id, "agentChatId", message, role, sources, "tokenConsume", "extraInfo", "responseTime", "createdAt", "updatedAt"
)
SELECT
  gen_random_uuid(),
  $1,  -- chat_id
  msg->>'message' as message,
  (msg->>'role')::"MessageRole" as role,
  CASE
    WHEN msg->>'sources' IS NULL OR msg->>'sources' = 'null' THEN NULL
    ELSE (msg->>'sources')::jsonb
  END as sources,
  COALESCE((msg->>'token_consumed')::integer, 0) as "tokenConsume",
  CASE
    WHEN msg->>'extra_info' IS NULL OR msg->>'extra_info' = 'null' THEN NULL
    ELSE (msg->>'extra_info')::jsonb
  END as "extraInfo",
  COALESCE((msg->>'response_time')::double precision, 0) as "responseTime",
  base_timestamp + ((row_number() OVER () - 1) * INTERVAL '1 millisecond'),
  NOW()
FROM base_time,
LATERAL jsonb_array_elements($7::jsonb) AS msg;
```

## WhatsApp vs Instagram differences

### Same storage model

Both channels share:

- same request model
- same repository save logic
- same `AgentChats` and `AgentChatMessages` tables
- same session-linking logic
- same `integrationId` extraction helper

### WhatsApp-only output shaping

For WhatsApp responses, service also builds:

- `whatsapp.text`
- product cards extracted from sources
- CTA actions like `Buy now`

Instagram currently shares plain-text normalization path but does not add WhatsApp-specific payload block.

## Important observations

### `ai-backend` stores chats; caller owns ID creation

From traced code in this repo:

- `chatId` creation is outside `ai-backend`
- `sessionId` creation is also outside `ai-backend`
- only internal `AgentChatSessions.id` is generated inside `ai-backend`

If you need true origin of Meta `chatId` or request `sessionId`, next repo to inspect is likely channel-ingestion or `user-backend`, not `ai-backend`.

Most direct proof:

```python
async for chunk in process_chat_request(
    message=payload.message,
    chat_id=payload.chatId,
    session_id=payload.sessionId,
    agent_id=payload.agentId,
    ...
):
    yield chunk
```

No `uuid.uuid4()` exists for incoming request `chatId` or request `sessionId` in traced path.
Only internal `AgentChatSessions.id` is created server-side.

### Chat title behavior

For WhatsApp/Instagram, title is derived in this order:

1. `senderDisplayName (senderPhone)`
2. `senderPhone`
3. `senderDisplayName`
4. fallback label like `WhatsApp · <short-chat-id>` or `Instagram · <short-chat-id>`

### `pageId` not part of traced save path

`pageId` exists in request model, but in traced `ai-backend` persistence path for this branch, chat/session storage is driven by:

- `chatId`
- `sessionId`
- `channel`
- `senderPhone`
- `senderDisplayName`

## How backend checks existing chat/session

### Chat existence check

Backend has helper to check whether chat row already exists:

```python
async def chat_exists(self, chat_id: str) -> bool:
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT 1 FROM "AgentChats" WHERE id = $1 LIMIT 1', chat_id
            )
        return row is not None
    except RuntimeError:
        return False
```

### Session existence check

Before creating internal session row, backend checks:

```python
row = await conn.fetchrow(
    'SELECT id FROM "AgentChatSessions" '
    'WHERE "agentId" = $1 AND "clientSessionKey" = $2 LIMIT 1',
    agent_id,
    session_id,
)
if row:
    session_pk = row["id"]
```

### Upsert-based chat storage check

Chat storage itself also checks by DB conflict on `AgentChats.id`:

```sql
INSERT INTO "AgentChats" (...)
VALUES (...)
ON CONFLICT (id) DO UPDATE SET
  channel = EXCLUDED.channel,
  "updatedAt" = NOW();
```

## End-to-end summary

```text
caller/webhook
  -> sends chatId + sessionId + channel
  -> /agents/chat or /agents/stream
  -> service passes values through
  -> repository upserts AgentChats using chatId
  -> repository inserts AgentChatMessages using chatId
  -> repository finds/creates AgentChatSessions using (agentId, sessionId)
  -> AgentChats.sessionId updated to internal AgentChatSessions.id
```

## Source files reviewed

- `app/modules/agents/models.py`
- `app/modules/agents/route.py`
- `app/modules/agents/service.py`
- `app/modules/agents/repository.py`
- `app/modules/agents/graph/graph.py`
- `app/db/queries/messages.sql`
