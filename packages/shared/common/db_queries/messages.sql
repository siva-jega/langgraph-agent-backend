-- SQL queries for creating chats and messages (used by MessagesRepository)
-- Uses JSONB batch insert for efficient multi-message inserts

-- ============================================
-- Create Chat and Messages (Batch Insert with JSONB)
-- ============================================
-- Parameters:
--   $1: chat_id (TEXT)
--   $2: type (TEXT, e.g., 'AGENT' or 'REGULAR') - ignored (no longer stored)
--   $3: session_id (TEXT, nullable) — client key; not written to column; used by _resolve_and_link_session
--   $4: title (TEXT, nullable; empty = keep existing on update / default Untitled on insert)
--   $5: user_id (TEXT, nullable)
--   $6: agent_id (TEXT, nullable)
--   $7: messages (JSONB array) - array of message objects
--   $8: latitude (FLOAT, nullable)
--   $9: longitude (FLOAT, nullable)
--   $10: country (TEXT, nullable)
--   $11: city (TEXT, nullable)
--   $12: channel — 'WEB' | 'WHATSAPP' | 'INSTAGRAM' (AgentChatChannel)
--   $13: integration_id (TEXT, nullable; META_WHATSAPP integration id for WhatsApp)

WITH chat_check AS (
  -- Check and create chat if needed, update userId if chat exists and userId is provided
  INSERT INTO "AgentChats" (
    id,
    "sessionId",
    title,
    "userId",
    "agentId",
    latitude,
    longitude,
    country,
    city,
    channel,
    "integrationId",
    "createdAt",
    "updatedAt"
  )
  VALUES (
    $1,  -- chat_id
    NULL,  -- sessionId FK — set by _resolve_and_link_session after INSERT
    COALESCE(NULLIF($4, ''), 'Untitled Chat'),  -- title
    NULLIF($5, ''),  -- user_id
    NULLIF($6, ''),  -- agent_id
    $8::double precision,  -- latitude (explicit cast for NULL safety)
    $9::double precision,  -- longitude (explicit cast for NULL safety)
    $10::text,  -- country (explicit cast for NULL safety)
    $11::text,  -- city (explicit cast for NULL safety)
    $12::"AgentChatChannel",
    NULLIF($13, ''),
    NOW(),
    NOW()
  )
  ON CONFLICT (id) DO UPDATE SET
    "userId" = CASE 
      WHEN NULLIF($5, '') IS NOT NULL THEN NULLIF($5, '')  -- Update userId if provided
      ELSE "AgentChats"."userId"  -- Keep existing userId if not provided
    END,
    title = CASE
      WHEN NULLIF($4, '') IS NOT NULL THEN NULLIF($4, '')
      ELSE "AgentChats".title
    END,
    latitude = COALESCE("AgentChats".latitude, EXCLUDED.latitude),
    longitude = COALESCE("AgentChats".longitude, EXCLUDED.longitude),
    country = COALESCE("AgentChats".country, EXCLUDED.country),
    city = COALESCE("AgentChats".city, EXCLUDED.city),
    channel = EXCLUDED.channel,
    "integrationId" = COALESCE(NULLIF(EXCLUDED."integrationId", ''), "AgentChats"."integrationId"),
    "updatedAt" = NOW()
  RETURNING id
),
base_time AS (
  -- Include $2 and $3 (ignored) with explicit types so asyncpg/Postgres can infer them
  SELECT NOW() as base_timestamp, $2::TEXT as _ignored_type, $3::TEXT as _ignored_session_key
)
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
LATERAL jsonb_array_elements($7::jsonb) AS msg;  -- $7 is the messages JSON array
