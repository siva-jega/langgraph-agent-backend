-- Insert a customer (dynamic fields schema — all values in data JSONB).
-- Parameters:
--   $1: agentId (TEXT, UUID of Agent)
--   $2: agentChatId (TEXT, nullable, UUID of AgentChats)
--   $3: userId (TEXT, nullable, UUID of User)
--   $4: data (JSONB — all field values including name, email, status, intent, etc.)
WITH resolved_agent AS (
  SELECT id, "workspaceId"
  FROM "Agent"
  WHERE id = $1
)
INSERT INTO "Customer" (
  id,
  "agentId",
  "agentChatId",
  "userId",
  data,
  "workspaceId",
  "createdAt",
  "updatedAt"
)
SELECT
  gen_random_uuid(),
  ra.id,
  NULLIF(TRIM($2), ''),
  NULLIF(TRIM($3), ''),
  COALESCE($4::jsonb, '{}'::jsonb),
  ra."workspaceId",
  NOW(),
  NOW()
FROM resolved_agent ra
RETURNING id;
