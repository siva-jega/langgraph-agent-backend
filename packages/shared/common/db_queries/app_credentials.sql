SELECT
  a.id,
  a.name,
  a.slug,
  a.credentials,
  a.config,
  a."workspaceId"
FROM "Apps" a
WHERE a.id = $2
  AND a."createdBy" = $1
LIMIT 1;


