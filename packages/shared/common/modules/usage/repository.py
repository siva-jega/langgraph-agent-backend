import logging
from typing import Optional

from common.db_queries import get_update_usage_query
from common.infrastructure import get_pg

logger = logging.getLogger(__name__)


class UsageRepository:
    async def update_message_credits(
        self,
        user_id: str,
        credits_delta: int,
    ) -> bool:
        """
        Update message_credits in the consumed field for a user.

        Args:
            user_id: The user ID
            credits_delta: Number of credits to add

        Returns:
            True if updated, False otherwise
        """
        if not user_id or credits_delta <= 0:
            return False
        try:
            pg = get_pg()
            query = get_update_usage_query()
            async with pg.acquire() as conn:
                result = await conn.fetchrow(query, user_id, credits_delta)
                if result:
                    logger.info(
                        "Updated usage for user %s: added %s credits",
                        user_id,
                        credits_delta,
                    )
                    return True
                else:
                    logger.warning(
                        "No active usage record found for user %s",
                        user_id,
                    )
                    return False
        except RuntimeError:
            logger.warning("Database not available for usage update")
            return False
        except Exception as e:
            logger.error("Usage update failed: %s", e, exc_info=True)
            return False

    async def get_usage(self, user_id: str) -> Optional[dict]:
        """
        Get current usage for a user.
        Returns None if not found.
        """
        # TODO: implement when needed
        return None
