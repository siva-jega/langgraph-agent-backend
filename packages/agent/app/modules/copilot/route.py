import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from app.modules.copilot.models import CopilotChatRequest
from app.modules.copilot.service import process_chat_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/copilot", tags=["Copilot"])


@router.post("/stream")
async def copilot_chat_stream(
    request: Request, body: CopilotChatRequest
) -> StreamingResponse:
    try:

        async def stream():
            async for chunk in process_chat_request(
                message=body.message,
                chatId=body.chatId,
                apps=body.apps if body.apps else [],
                fileIds=body.fileIds if body.fileIds else [],
                isNewChat=body.isNewChat,
                kids=body.kids if body.kids else [],
                webSearch=body.webSearch,
                user_id=body.userId,
            ):
                yield chunk

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream",
            },
        )
    except Exception as e:
        logger.error("Unexpected error in chat stream: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
