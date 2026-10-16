import json
import logging
from typing import Any, List, Optional
from app.core.types import MessageRole
from common.db_queries import get_create_chat_query, get_messages_only_query
from common.infrastructure import get_pg

logger = logging.getLogger(__name__)


class CopilotRepository:
    async def save_conversation(
        self,
        chat_id: str,
        user_id: str,
        isNewChat: bool,
        chat_title: str,
        user_message: str,
        assistant_content: str,
        sources: List[Any],
        token_data: int,
        extra_info: Optional[dict],
    ) -> bool:
        try:
            user_msg = {
                "role": MessageRole.USER.value,
                "message": user_message,
            }

            assistant_msg = {
                "role": MessageRole.ASSISTANT.value,
                "message": assistant_content,
                "token_consumed": token_data,
                "sources": sources if sources else None,
                "extra_info": extra_info if extra_info else None,
            }

            messages = [user_msg, assistant_msg]

            logger.info(
                "Saving message for user %s with tokens: %s", user_id, token_data
            )

            try:
                pg = get_pg()

                if not user_id:
                    logger.warning(
                        "user_id is required but not provided for chat %s", chat_id
                    )
                    return False

                async with pg.acquire() as conn:
                    if isNewChat:
                        chat_query = get_create_chat_query()
                        await conn.execute(
                            chat_query,
                            chat_id,
                            chat_title or "Untitled Chat",
                            "REGULAR",
                            user_id,
                        )
                        logger.info("Created new chat %s", chat_id)

                    messages_json = []
                    for msg in messages:
                        msg_dict = {
                            "message": msg.get("message", ""),
                            "role": msg.get("role", ""),
                        }
                        sources_val = msg.get("sources")
                        msg_dict["sources"] = sources_val if sources_val else None

                        extra_info_val = msg.get("extra_info")
                        msg_dict["extra_info"] = (
                            extra_info_val if extra_info_val else None
                        )

                        msg_dict["token_consumed"] = msg.get("token_consumed", 0)
                        messages_json.append(msg_dict)

                    messages_jsonb = json.dumps(messages_json)

                    messages_query = get_messages_only_query()
                    await conn.execute(
                        messages_query,
                        chat_id,
                        messages_jsonb,
                    )

                logger.info(
                    "Successfully saved %s messages for chat %s",
                    len(messages),
                    chat_id,
                )
                return True
            except RuntimeError:
                logger.warning("DATABASE_URL not set; skipping save_conversation")
                return False
            except Exception as e:
                logger.error("Error saving messages to DB: %s", e, exc_info=True)
                return False
        except Exception as e:
            logger.error("Error saving conversation and usage: %s", e, exc_info=True)
            return False
