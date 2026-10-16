SELECT
  id,
  "workspaceId",
  "agentId",
  provider,
  status,
  creds,
  metadata
FROM public."AgentIntegration"
WHERE provider = 'META_WHATSAPP'
  AND status = 'ACTIVE'
  AND (
    creds->>'phoneNumberId' = $1
    OR metadata->>'accountId' = $1
  )
ORDER BY
  CASE WHEN creds->>'phoneNumberId' = $1 THEN 0 ELSE 1 END
LIMIT 1;
