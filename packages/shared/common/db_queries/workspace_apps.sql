SELECT id, name, slug
FROM "Apps"
WHERE "workspaceId" = $1;
