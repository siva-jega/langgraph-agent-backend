-- SQL query for updating usage message credits
-- Uses JSONB path to update the message_credits field in the consumed JSON

-- ============================================
-- Update Usage Message Credits
-- ============================================
-- Parameters:
--   $1: user_id (TEXT)
--   $2: credits_delta (INTEGER) - number of credits to add

UPDATE "Usage"
SET 
  consumed = jsonb_set(
    consumed,
    '{message_credits}',
    to_jsonb(COALESCE((consumed->>'message_credits')::integer, 0) + $2)
  ),
  "updatedAt" = NOW()
WHERE "userId" = $1
  AND "isActive" = true
RETURNING id, consumed;
