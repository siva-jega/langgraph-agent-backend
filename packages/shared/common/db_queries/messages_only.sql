-- SQL query for inserting messages only (no chat creation)
-- Parameters:
--   $1: chat_id (TEXT)
--   $2: messages (JSONB array)

WITH base_time AS (
  SELECT NOW() as base_timestamp
)
INSERT INTO "AgentChatMessages" (
  id, "agentChatId", message, role, sources, "tokenConsume", "extraInfo", "responseTime", "createdAt", "updatedAt"
)
SELECT
  gen_random_uuid(),
  $1,
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
LATERAL jsonb_array_elements($2::jsonb) AS msg;
