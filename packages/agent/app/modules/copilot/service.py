import json
import logging
from typing import Any, Dict, List, Optional, AsyncGenerator
from app.modules.common import get_usage
from app.modules.copilot.graph import get_agent
from app.modules.copilot.utils import create_chat_title
from app.modules.copilot.repository import CopilotRepository

logger = logging.getLogger(__name__)


_repo = CopilotRepository()


async def process_chat_request(
    message: str,
    chatId: str,
    apps: List[dict],
    fileIds: List[str],
    isNewChat: bool,
    kids: List[str],
    webSearch: bool,
    user_id: str,
) -> AsyncGenerator[str, None]:
    sources: List[Any] = []
    assistant_content = ""
    token_data = 0

    try:
        agent = get_agent()

        chat_title = None
        extra_info = None

        additional_params = {
            "apps": apps,
            "fileIds": fileIds,
            "kids": kids,
            "webSearch": webSearch,
            "userId": user_id,
        }

        if isNewChat:
            chat_title = await create_chat_title(message)
            yield f"event: chat_title\ndata: {chat_title}\n\n"

        max_retries = 3
        retry_count = 0
        last_error = None

        while retry_count <= max_retries:
            error_occurred = False
            sources = []

            async for chunk in agent.run(
                chatId,
                message,
                additional_params,
            ):
                # Handle different event types and stream them
                if chunk.event_type == "chat_message":
                    yield f"event: chat_message\ndata: {chunk.text}\n\n"
                elif chunk.event_type == "tool_call":
                    payload = json.dumps(
                        {
                            "text": chunk.text,
                            "data": chunk.data,
                            "tool": chunk.tool_name,
                        }
                    )
                    yield f"event: tool_call\ndata: {payload}\n\n"
                elif chunk.event_type == "interrupt":
                    interrupt_payload = json.dumps(
                        {
                            "text": chunk.text,
                            "interrupt_data": (
                                chunk.interrupt_data
                                if hasattr(chunk, "interrupt_data")
                                else None
                            ),
                        }
                    )
                    yield f"event: interrupt\ndata: {interrupt_payload}\n\n"
                    assistant_content = chunk.text
                elif chunk.event_type == "chat_error":
                    error_occurred = True
                    last_error = chunk.text
                    logger.error(
                        f"Chat error (attempt {retry_count + 1}/{max_retries + 1}): {chunk.text}"
                    )
                    # Don't yield error yet, will retry or yield after all retries
                    break
                elif chunk.event_type == "end_of_chat":
                    assistant_content = chunk.final_message.content
                    sources = chunk.data
                    extra_info = chunk.extra_info
                    if chunk.token_usage:
                        token_data = chunk.token_usage
                        logger.info(
                            f"Saving message for chat {chatId} with tokens: {token_data}"
                        )
                    yield f"event: end_of_chat\ndata: {json.dumps(chunk.data)}\n\n"

            # If no error occurred, break out of retry loop
            if not error_occurred:
                break

            # If error occurred and we have retries left, retry
            if error_occurred and retry_count < max_retries:
                retry_count += 1
                logger.info(
                    f"Retrying chat request (attempt {retry_count + 1}/{max_retries + 1})"
                )
                continue

            # If error occurred and no retries left, yield the error
            if error_occurred and retry_count >= max_retries:
                logger.error(
                    f"Chat error after {max_retries + 1} attempts: {last_error}"
                )
                yield f"event: chat_error\ndata: {last_error}\n\n"
                break

        # Save conversation after streaming is complete
        if message and assistant_content:
            logger.info(f"Saving conversation")
            try:
                db_success = await _repo.save_conversation(
                    chatId,
                    user_id,
                    isNewChat,
                    chat_title,
                    message,
                    assistant_content,
                    sources,
                    token_data,
                    extra_info,
                )
                if db_success:
                    logger.info(f"Successfully saved messages for chat {chatId}")
                    usage_svc = get_usage()
                    await usage_svc.update_usage_consumed(
                        user_id=user_id,
                        token_info=extra_info,
                    )
                else:
                    logger.warning(
                        f"Partial failure saving messages for chat {chatId} - DB: {db_success}"
                    )

            except Exception as e:
                logger.error(f"Failed to save messages: {e}")

    except Exception as e:
        logger.error(f"Error in process_chat_request: {e}")
        raise
