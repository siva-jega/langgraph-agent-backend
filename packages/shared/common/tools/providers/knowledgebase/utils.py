"""Helpers for the knowledge-base tool. Currently houses query-translation so a
non-English user message can be searched against an English-indexed knowledge base
without forcing the calling LLM to translate inline."""

import logging
from typing import Any, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from common.llm_utils import apply_openai_service_tier
from common.project_settings import project_settings
from common.tools.providers.shopify.utils import is_english_language  # noqa: F401 — re-exported

from .models import KBQueryTranslation

logger = logging.getLogger(__name__)

# Module-level cached model — built once on first call, reused forever.
_KB_TRANSLATION_STRUCT_MODEL: Any = None

_KB_TRANSLATION_SYSTEM_MSG = SystemMessage(
    content=(
        "You translate knowledge-base search queries into English so they match an "
        "English-indexed corpus. Translate `query`, `query_text_for_sparse_search`, "
        "and every string in `text_list` into natural, search-friendly English. "
        "Preserve proper nouns, brand names, product codes, identifiers, dates, and "
        "numeric values verbatim. Return `text_list_en` with the same length and "
        "ordering as the input `text_list`. If a field is already English, return it "
        "unchanged. Never invent details. Set `detected_language` to the ISO 639-1 "
        "code of the source language."
    )
)


def _get_kb_translation_model() -> Any:
    global _KB_TRANSLATION_STRUCT_MODEL
    if _KB_TRANSLATION_STRUCT_MODEL is None:
        settings = project_settings()
        model_name = (
            getattr(settings, "KNOWLEDGEBASE_QUERY_TRANSLATION_MODEL", None)
            or getattr(settings, "SHOPIFY_QUERY_TRANSLATION_MODEL", None)
            or getattr(settings, "DEFAULT_AGENT_MODEL", None)
        )
        llm = ChatOpenAI(
            **apply_openai_service_tier(
                {
                    "model": model_name,
                    "api_key": settings.OPENAI_API_KEY,  # type: ignore[arg-type]
                    "base_url": settings.OPENAI_API_BASE,
                    "temperature": 0,
                },
                settings,
            )
        )
        _KB_TRANSLATION_STRUCT_MODEL = llm.with_structured_output(KBQueryTranslation)
    return _KB_TRANSLATION_STRUCT_MODEL


async def translate_knowledgebase_queries_to_english(
    *,
    query: str,
    query_text_for_sparse_search: str,
    text_list: List[str],
) -> KBQueryTranslation:
    """Translate the knowledge-base search-query fields to English via a single LLM
    call. Caller is responsible for gating on `original_language`; this function
    always invokes the translation model. On LLM failure it logs and returns the
    inputs unchanged so the downstream search still runs."""
    text_list_payload = "\n".join(f"- {t}" for t in text_list) if text_list else "(empty)"
    user_msg = HumanMessage(
        content=(
            "Translate the following knowledge-base search fields into English. "
            "Return `text_list_en` with the same length and order as the input.\n\n"
            f"query: {query}\n"
            f"query_text_for_sparse_search: {query_text_for_sparse_search}\n"
            f"text_list ({len(text_list)} items):\n{text_list_payload}"
        )
    )
    try:
        model = _get_kb_translation_model()
        result = await model.ainvoke(
            [_KB_TRANSLATION_SYSTEM_MSG, user_msg],
            config={"callbacks": []},
        )
        translation = (
            result if isinstance(result, KBQueryTranslation)
            else KBQueryTranslation.model_validate(result)
        )
        # Defensive: if the model returned the wrong number of dense queries, fall
        # back to the original list so retrieval still has the same fan-out.
        if len(translation.text_list_en) != len(text_list):
            logger.warning(
                "KB translation returned %d items, expected %d; keeping original text_list",
                len(translation.text_list_en),
                len(text_list),
            )
            translation = KBQueryTranslation(
                detected_language=translation.detected_language,
                query_en=translation.query_en,
                query_text_for_sparse_search_en=translation.query_text_for_sparse_search_en,
                text_list_en=text_list,
            )
        return translation
    except Exception as e:
        logger.warning(
            "Knowledgebase query translation failed (%s); falling back to original inputs",
            e,
        )
        return KBQueryTranslation(
            detected_language="unknown",
            query_en=query,
            query_text_for_sparse_search_en=query_text_for_sparse_search,
            text_list_en=text_list,
        )
