SELECT
  id,
  "workspaceId",
  "agentId",
  provider,
  status,
  creds,
  metadata
FROM public."AgentIntegration"
WHERE id = $1
  AND provider = 'META_WHATSAPP'
  AND status = 'ACTIVE'
LIMIT 1;
