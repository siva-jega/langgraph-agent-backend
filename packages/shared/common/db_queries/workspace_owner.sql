SELECT u.id, u.email, u.name
FROM "Workspace" w
JOIN "User" u ON u.id = w."ownerId"
WHERE w.id = $1;
