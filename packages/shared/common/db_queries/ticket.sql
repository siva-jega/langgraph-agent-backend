-- Insert a ticket (dynamic fields schema — all values in data JSONB).
-- Parameters:
--   $1: agentId (TEXT, UUID of Agent)
--   $2: agentChatId (TEXT, nullable, UUID of AgentChats)
--   $3: data (JSONB — all field values including subject, status, priority, etc.)
WITH resolved_agent AS (
  SELECT id, "workspaceId"
  FROM "Agent"
  WHERE id = $1
)
INSERT INTO "Ticket" (
  id,
  "agentId",
  "agentChatId",
  data,
  "workspaceId",
  "createdAt",
  "updatedAt"
)
SELECT
  gen_random_uuid(),
  ra.id,
  NULLIF(TRIM($2), ''),
  COALESCE($3::jsonb, '{}'::jsonb),
  ra."workspaceId",
  NOW(),
  NOW()
FROM resolved_agent ra
RETURNING id;
