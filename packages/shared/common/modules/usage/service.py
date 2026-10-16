import logging
from typing import Optional

from common.core.credits_calculation import calculate_credits_from_usage
from common.modules.usage.repository import UsageRepository

logger = logging.getLogger(__name__)

_repo = UsageRepository()


class UsageService:
    async def update_usage_consumed(
        self,
        user_id: str,
        token_info: dict,
    ) -> bool:
        """
        Calculate and record message credits consumed.

        Args:
            user_id: The user ID
            token_info: Dict with input_tokens, output_tokens, model

        Returns:
            True if updated, False otherwise
        """
        if not user_id or not token_info:
            return False

        messages_credits = calculate_credits_from_usage(token_info)
        if not messages_credits:
            return False

        return await _repo.update_message_credits(user_id, messages_credits)

    async def get_usage(self, user_id: str) -> Optional[dict]:
        """Get current usage for a user."""
        return await _repo.get_usage(user_id)
