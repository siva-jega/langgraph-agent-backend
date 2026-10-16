"""OpenAI helpers for shared packages (tier from bound ``project_settings``)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, List

from openai import AsyncOpenAI

from common.project_settings import project_settings

logger = logging.getLogger(__name__)


def openai_service_tier(settings: Any | None = None) -> str | None:
    """Resolved OpenAI ``service_tier`` (e.g. ``priority``) from app settings."""
    s = settings if settings is not None else project_settings()
    tier = (getattr(s, "OPENAI_SERVICE_TIER", None) or "").strip()
    return tier or None


def cache_read_tokens(input_token_details: Any | None) -> int:
    """Cached input tokens from a usage-metadata ``input_token_details`` dict.

    When a service tier is set, langchain-openai stores the count under a
    tier-prefixed key (``priority_cache_read`` / ``flex_cache_read``) rather than
    ``cache_read``, so read whichever ``*cache_read`` key is present.
    """
    details = input_token_details or {}
    return sum(v or 0 for k, v in details.items() if k.endswith("cache_read"))


def apply_openai_service_tier(
    kwargs: dict[str, Any], settings: Any | None = None
) -> dict[str, Any]:
    """Add ``service_tier`` to ChatOpenAI / completion kwargs when configured."""
    tier = openai_service_tier(settings)
    if tier:
        kwargs["service_tier"] = tier
    return kwargs


async def create_chat_title(message: str, settings: Any | None = None) -> str:
    s = settings if settings is not None else project_settings()
    openai = AsyncOpenAI(
        api_key=s.OPENAI_API_KEY, base_url=s.OPENAI_API_BASE
    )
    completion_kwargs: dict[str, Any] = {
        "model": s.DEFAULT_AGENT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": f"""
                    You are an AI title maker bot.
                    From the input summarise a chat title with more than 2 words and max 5 words.
                    Make a title that describes the conversation.
                    If the input is missing or is gibberish then just generate any cool chat name
                    with in 5 words, never generate a title more than 5 words, and never generate same title twice.
                    This is the input entered by the user: {message}
                """,
            },
            {
                "role": "user",
                "content": f"Generate a title for the following conversation: {message}",
            },
        ],
        "temperature": 0.3,
        "max_tokens": 200,
    }
    apply_openai_service_tier(completion_kwargs, s)
    response = await openai.chat.completions.create(**completion_kwargs)
    return response.choices[0].message.content


async def ttft_fallback_stream(
    models: List[Any],
    messages: List[Any],
    request_start_time: float,
    node_label: str = "Assistant",
    global_timeout: float = 5.0,
    force_model_index: int | None = None,
    **kwargs,
) -> AsyncGenerator[tuple[Any, int], None]:
    """
    Custom wrapper to track Time to First Token (TTFT) with cumulative timeout.
    Yields chunks as they arrive for true streaming.

    Returns:
        AsyncGenerator yielding (message_chunk, successful_model_index)
    """
    idx = force_model_index if force_model_index is not None else 0

    try:
        async for chunk in _stream_with_timeout(
            models,
            idx,
            messages,
            request_start_time,
            global_timeout,
            node_label,
            **kwargs,
        ):
            yield chunk, idx
        return
    except (asyncio.TimeoutError, Exception) as e:
        if idx == 0 and len(models) > 1:
            idx = 1
            async for chunk in _stream_with_timeout(
                models,
                idx,
                messages,
                request_start_time,
                global_timeout,
                node_label,
                **kwargs,
            ):
                yield chunk, idx
        else:
            raise e


async def _stream_with_timeout(
    models, i, messages, request_start_time, global_timeout, node_label, **kwargs
):
    """Helper to handle the actual streaming for a single model with timeout."""
    elapsed = time.time() - request_start_time
    current_timeout = max(0.1, global_timeout - elapsed)
    stream = models[i].astream(messages, **kwargs)
    iterator = stream.__aiter__()

    first_chunk = None
    while True:
        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=current_timeout)
        if chunk.content or (hasattr(chunk, "tool_calls") and chunk.tool_calls):
            first_chunk = chunk
            break
        elapsed = time.time() - request_start_time
        current_timeout = max(0.01, global_timeout - elapsed)

    yield first_chunk
    async for chunk in iterator:
        yield chunk
