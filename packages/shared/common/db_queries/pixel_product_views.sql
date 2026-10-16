SELECT data
FROM "IntegrationEvent"
WHERE "workspaceId" = $1
  AND provider = 'shopify'
  AND "eventType" = 'product_viewed'
  AND "shopifyClientId" = $2
  AND "occurredAt" >= $3
  AND "occurredAt" <= $4
ORDER BY "occurredAt" DESC
LIMIT 20
