import asyncio
import json
import logging
from typing import List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import app_settings
from app.modules.agents.graph.nodes import _get_model
from app.modules.tag_classification.models import AgentTag, TagClassificationResult
from app.modules.tag_classification.prompt import TAG_CLASSIFICATION_SYSTEM_PROMPT
from app.modules.tag_classification.repository import (
    attach_tags_to_chat,
    fetch_agent_tags,
    fetch_recent_messages,
)

logger = logging.getLogger(__name__)


def _format_tag(index: int, tag: AgentTag) -> str:
    parts = [f"{index}. {tag.title}"]
    if tag.description:
        parts.append(f"description: {tag.description}")
    if tag.rules:
        parts.append(f"rules: {tag.rules}")
    return " | ".join(parts)


def _format_conversation(messages: List[Tuple[str, str]]) -> str:
    lines: List[str] = []
    for role, content in messages:
        if not content:
            continue
        speaker = "Customer" if role == "USER" else "AI Agent"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def _resolve_selected(result: TagClassificationResult, tags: List[AgentTag]) -> List[str]:
    ids: List[str] = []
    seen = set()
    for n in result.selected:
        idx = n - 1
        if 0 <= idx < len(tags):
            tid = tags[idx].id
            if tid not in seen:
                seen.add(tid)
                ids.append(tid)
    return ids


async def classify(tags: List[AgentTag], conversation_text: str) -> TagClassificationResult:
    model_name = (app_settings.TAG_CLASSIFICATION_MODEL or "").strip() or app_settings.DEFAULT_AGENT_MODEL
    model = _get_model(
        model_name=model_name,
        api_key=app_settings.OPENAI_API_KEY,
        base_url=app_settings.OPENAI_API_BASE,
        model_kwargs={"response_format": {"type": "json_object"}},
    )
    catalog = "\n".join(_format_tag(i, t) for i, t in enumerate(tags, start=1))
    messages = [
        SystemMessage(content=TAG_CLASSIFICATION_SYSTEM_PROMPT),
        HumanMessage(content=f"Candidate tags:\n{catalog}\n\nConversation:\n{conversation_text}"),
    ]
    response = await model.ainvoke(messages)
    data = json.loads(response.content)
    return TagClassificationResult(**data)


async def classify_and_tag_chat(chat_id: str, agent_id: str) -> None:
    if not app_settings.TAG_CLASSIFICATION_ENABLED:
        return
    cid = (chat_id or "").strip()
    aid = (agent_id or "").strip()
    if not cid or not aid:
        return
    try:
        tags = await fetch_agent_tags(aid)
        if not tags:
            return
        messages = await fetch_recent_messages(cid, app_settings.TAG_CLASSIFICATION_MAX_MESSAGES)
        if not messages:
            return
        result = await classify(tags, _format_conversation(messages))
        selected_ids = _resolve_selected(result, tags)
        if not selected_ids:
            return
        await attach_tags_to_chat(cid, selected_ids)
        logger.info(
            "tag_classification: chat=%s agent=%s attached=%d",
            cid,
            aid,
            len(selected_ids),
        )
    except Exception:
        logger.warning(
            "tag_classification: failed chat=%s agent=%s", cid, aid, exc_info=True
        )


def schedule_tag_classification(
    chat_id: Optional[str], agent_id: Optional[str]
) -> None:
    """Fire-and-forget: classify the chat and attach agent tags (requires a running loop)."""
    if not app_settings.TAG_CLASSIFICATION_ENABLED:
        return
    cid = str(chat_id).strip() if chat_id else ""
    aid = str(agent_id).strip() if agent_id else ""
    if not cid or not aid:
        return

    async def _run() -> None:
        await classify_and_tag_chat(cid, aid)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        logger.warning(
            "tag_classification: no running event loop; skipped chat=%s", cid
        )
