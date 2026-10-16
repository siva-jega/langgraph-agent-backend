-- SQL query for creating a new chat
-- Parameters:
--   $1: chat_id (TEXT)
--   $2: title (TEXT)
--   $3: type (TEXT, e.g., 'AGENT', 'REGULAR') - ignored (no longer stored)
--   $4: user_id (TEXT, nullable)

WITH _ignored AS (
  -- Ensure $3 has a known type even though we don't store it anymore
  SELECT $3::TEXT as _ignored_type
)
INSERT INTO "AgentChats" (
  id, title, "userId", "createdAt", "updatedAt"
)
SELECT
  $1,
  COALESCE($2, 'Untitled Chat'),
  NULLIF($4, ''),
  NOW(),
  NOW()
FROM _ignored
ON CONFLICT (id) DO NOTHING
RETURNING id;
