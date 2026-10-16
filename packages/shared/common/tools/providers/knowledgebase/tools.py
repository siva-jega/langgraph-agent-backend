import json
from typing import Any, List
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
from common.modules.retrieval import RetrievalResponse
from common.integration import get_retrieval
import logging
from common.tools.providers.shopify.utils import process_retrieved_products
from common.tools.utils.writer import emit_status
from .models import KnowledgeBaseResponse
from .utils import (
    is_english_language,
    translate_knowledgebase_queries_to_english,
)

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace as otel_trace
except Exception:  # pragma: no cover - OpenTelemetry is optional for shared package users.
    otel_trace = None

SHOPIFY_PRODUCT_CONNECTOR_TYPES = {
    "shopify",
    # Temporal ShopifyCrawlWebsites stores connector_type="SHOPIFY_WEBSITES".
    "shopify_websites",
}
KNOWLEDGEBASE_LLM_TEXT_LIMIT_CHARS = 2000
KNOWLEDGEBASE_LLM_TOTAL_TEXT_LIMIT_CHARS = 12000
KNOWLEDGEBASE_MIN_SCORE = 0.30


def _sanitize_metadata_for_llm(raw_metadata: str | dict | None) -> str:
    """
    Keep only small provenance-style metadata in the tool result so the model
    gets source context without the full Shopify payload.
    """
    parsed: dict = {}
    if isinstance(raw_metadata, dict):
        parsed = raw_metadata
    elif isinstance(raw_metadata, str) and raw_metadata.strip():
        try:
            loaded = json.loads(raw_metadata)
            if isinstance(loaded, dict):
                parsed = loaded
        except Exception:
            parsed = {}

    link = (
        parsed.get("link")
        or parsed.get("item_link")
        or parsed.get("url")
        or parsed.get("s3_key")
        or parsed.get("onlineStoreUrl")
    )
    sanitized = {
        "title": parsed.get("title"),
        "link": link,
        "url": link,
    }
    sanitized = {k: v for k, v in sanitized.items() if v not in (None, "")}
    return json.dumps(sanitized, ensure_ascii=False) if sanitized else ""


def _compact_text_for_llm(text: str | None, limit: int = KNOWLEDGEBASE_LLM_TEXT_LIMIT_CHARS) -> str:
    if not text:
        return ""
    if limit <= 0:
        return ""
    compacted = " ".join(str(text).split())
    if len(compacted) <= limit:
        return compacted
    if limit <= 3:
        return "." * limit
    trimmed = compacted[: limit - 3].rsplit(" ", 1)[0].strip()
    if not trimmed:
        trimmed = compacted[: limit - 3].strip()
    return f"{trimmed}..."


def _build_knowledgebase_artifact_data(
    retrieved_nodes: List[RetrievalResponse],
) -> list[dict[str, Any]]:
    retrieved_nodes = [n for n in retrieved_nodes if (n.score or 0) >= KNOWLEDGEBASE_MIN_SCORE]
    retrieved_nodes = sorted(retrieved_nodes, key=lambda n: n.score or 0, reverse=True)
    artifact_data: list[dict[str, Any]] = []
    total_chars = 0
    for node in retrieved_nodes:
        remaining = KNOWLEDGEBASE_LLM_TOTAL_TEXT_LIMIT_CHARS - total_chars
        if remaining <= 0:
            break
        text = _compact_text_for_llm(
            node.text,
            min(KNOWLEDGEBASE_LLM_TEXT_LIMIT_CHARS, remaining),
        )
        total_chars += len(text)
        artifact_data.append(
            {
                "text": text,
                "metadata": _sanitize_metadata_for_llm(node.metadata),
                "score": node.score,
                "source": node.source,
            }
        )
    return artifact_data


def _build_knowledgebase_responses(
    knowledgebase_artifact_data: list[dict[str, Any]],
) -> list[KnowledgeBaseResponse]:
    return [
        KnowledgeBaseResponse(
            text=item.get("text") or "",
            metadata=item.get("metadata") or "",
            score=item.get("score") or 0,
            source=item.get("source") or "",
        )
        for item in knowledgebase_artifact_data
    ]


def _set_current_span_attributes(attributes: dict[str, Any]) -> None:
    if otel_trace is None:
        return
    try:
        span = otel_trace.get_current_span()
        if span and span.is_recording():
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
    except Exception:
        logger.debug("Failed to set knowledgebase trace attributes", exc_info=True)


def _parse_metadata(raw_metadata: str | dict | None) -> dict[str, Any]:
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if isinstance(raw_metadata, str) and raw_metadata.strip():
        try:
            loaded = json.loads(raw_metadata)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            return {}
    return {}


def _is_shopify_product_node(node: RetrievalResponse) -> bool:
    metadata = _parse_metadata(node.metadata)
    connector_type = str(metadata.get("connector_type") or "").strip().lower()
    if connector_type not in SHOPIFY_PRODUCT_CONNECTOR_TYPES:
        return False

    record_type = str(metadata.get("record_type") or "").strip().lower()
    if record_type != "product":
        return False

    # Only ACTIVE products are recommendable. DRAFT/ARCHIVED products are never
    # surfaced. Missing/empty status is treated as ACTIVE for backward-compat
    # with older embeddings that predate this field.
    status = str(metadata.get("status") or "").strip().upper()
    if status and status != "ACTIVE":
        return False

    has_title = bool(metadata.get("title"))
    has_url = bool(
        metadata.get("onlineStoreUrl")
        or metadata.get("onlineStorePreviewUrl")
        or metadata.get("product_url")
        or metadata.get("url")
        or metadata.get("link")
    )
    has_image = bool(
        metadata.get("image_url")
        or (
            isinstance(metadata.get("image_urls"), list)
            and len(metadata.get("image_urls") or []) > 0
        )
        or (
            isinstance(metadata.get("images"), list)
            and len(metadata.get("images") or []) > 0
        )
    )
    return has_title and has_url and has_image


def _build_shopify_product_artifact(
    retrieved_nodes: List[RetrievalResponse],
) -> dict[str, Any] | None:
    shopify_nodes = [node for node in retrieved_nodes if _is_shopify_product_node(node)]
    if not shopify_nodes:
        logger.info(
            "Knowledgebase Shopify artifact skipped: product_nodes_returned=0 retrieved_nodes=%d",
            len(retrieved_nodes),
        )
        return None

    product_nodes = [
        RetrievalResponse(
            text=json.dumps(_parse_metadata(node.metadata), ensure_ascii=False),
            metadata=node.metadata,
            score=node.score,
            source=node.source,
        )
        for node in shopify_nodes
    ]

    try:
        _, artifacts = process_retrieved_products(product_nodes)
    except Exception as exc:
        logger.warning(
            "Knowledge base Shopify product artifact normalization failed: %s",
            exc,
        )
        return None

    valid_artifacts = [
        artifact
        for artifact in artifacts
        if artifact.get("name")
        and artifact.get("product_url")
        and (
            artifact.get("image_url")
            or (
                isinstance(artifact.get("images"), list)
                and len(artifact.get("images") or []) > 0
            )
        )
    ]
    if not valid_artifacts:
        logger.info(
            "Knowledgebase Shopify artifact skipped: product_nodes_returned=%d valid_artifacts=0",
            len(shopify_nodes),
        )
        return None

    logger.info(
        "Knowledgebase Shopify artifact built: product_nodes_returned=%d artifact_count=%d",
        len(shopify_nodes),
        len(valid_artifacts),
    )

    return {
        "name": "Shopify",
        "tool": "Get Products",
        "data": valid_artifacts,
    }


# TODO: Simplify to a single `keywords: str` (comma-separated) and fan out server-side.
# The 3-field signature leaks retrieval internals to the LLM and costs ~40-80 extra output
# tokens per call. Fan-out logic: terms = keywords.split(","); text_list = terms;
# query = query_text_for_sparse_search = keywords.
@tool(response_format="content_and_artifact")
async def search_knowledgebase(
    query: str,
    query_text_for_sparse_search: str,
    text_list: List[str],
    config: RunnableConfig,
    original_language: str = "en",
) -> List[KnowledgeBaseResponse]:
    """
    Retrieve information from the knowledge base using a query
    and specific knowledge Base IDs.

    Args:
        query (str): The main query to search for, in the user's language.
            The server will translate to English internally when needed.
        query_text_for_sparse_search (str): The query to search for in the sparse
            index, in the user's language. The server will translate to English
            internally when needed.
        text_list (List[str]): The list of queries to search for in the dense
            index, in the user's language. The server will translate to English
            internally when needed.
        config (RunnableConfig): Configuration containing collection_name and kids.
        original_language (str): ISO 639-1 code of the user's message language
            ("en", "es", "fr", "hi", "ta", "ar", "zh", …). Pass "en" only when the
            message is already in English. The assistant's reply MUST be produced
            entirely in this language.

    Returns:
        List[KnowledgeBaseResponse]: A list of retrieved documents.
    """
    try:
        emit_status("Looking that up...")

        if not is_english_language(original_language):
            translation = await translate_knowledgebase_queries_to_english(
                query=query,
                query_text_for_sparse_search=query_text_for_sparse_search,
                text_list=text_list,
            )
            logger.info(
                "Knowledgebase search_knowledgebase: translated search args from %s -> en "
                "(query=%r)",
                original_language,
                translation.query_en,
            )
            query = translation.query_en
            query_text_for_sparse_search = translation.query_text_for_sparse_search_en
            text_list = translation.text_list_en

        # Extract configuration
        configurable = config.get("configurable", {})
        collection_name = configurable.get("collection_name")
        entity_ids = configurable.get("entity_ids")
        folder_ids = configurable.get("folder_ids")

        if not collection_name:
            logger.error("No collection name provided in configuration")
            return [
                KnowledgeBaseResponse(
                    text="No collection name provided in configuration.",
                    metadata="",
                    score=0,
                    source="",
                )
            ]

        if not entity_ids and not folder_ids:
            logger.warning(
                "No Entity IDs (entity_ids) or Folder IDs (folder_ids) selected"
            )
            return [
                KnowledgeBaseResponse(
                    text="No Entity IDs (entity_ids) or Folder IDs (folder_ids) selected.",
                    metadata="",
                    score=0,
                    source="",
                )
            ]

        # Get retrieval service from infra registry (set at lifespan)
        try:
            retrieval_service = get_retrieval()
        except RuntimeError as e:
            logger.error(f"Failed to get retrieval service from package: {e}")
            return [
                KnowledgeBaseResponse(
                    text="Retrieval service not available. Please ensure the package is properly initialized.",
                    metadata="",
                    score=0,
                    source="",
                )
            ]

        logger.info(
            f"Retrieving knowledge base with entity_ids: {entity_ids} and folder_ids: {folder_ids}"
        )
        # Perform retrieval
        retrieved_nodes: List[RetrievalResponse] = (
            await retrieval_service.retrieve_multiple(
                text_list=text_list,
                entity_ids=entity_ids,
                folder_ids=folder_ids,
                query=query,
                query_text_for_sparse_search=query_text_for_sparse_search,
                collection_name=collection_name,
            )
        )

        emit_status("Found what I need, putting it together...")

        knowledgebase_artifact_data = _build_knowledgebase_artifact_data(retrieved_nodes)
        responses = _build_knowledgebase_responses(knowledgebase_artifact_data)
        raw_text_chars = sum(len(node.text or "") for node in retrieved_nodes)
        compacted_text_chars = sum(
            len(item.get("text") or "") for item in knowledgebase_artifact_data
        )
        _set_current_span_attributes(
            {
                "knowledgebase.retrieved_nodes": len(retrieved_nodes),
                "knowledgebase.llm_text.raw_chars": raw_text_chars,
                "knowledgebase.llm_text.compacted_chars": compacted_text_chars,
                "knowledgebase.llm_text.compacted": raw_text_chars != compacted_text_chars,
            }
        )
        if raw_text_chars != compacted_text_chars:
            logger.info(
                "Knowledgebase text compacted for LLM: retrieved_nodes=%d raw_chars=%d compacted_chars=%d",
                len(retrieved_nodes),
                raw_text_chars,
                compacted_text_chars,
            )
        artifacts: list[dict[str, Any]] = [{
            "name": "Knowledge Base",
            "tool": "Search",
            "data": knowledgebase_artifact_data,
        }]

        shopify_artifact = _build_shopify_product_artifact(retrieved_nodes)
        if shopify_artifact:
            artifacts.append(shopify_artifact)

        return responses, artifacts

    except Exception as e:
        logger.error(f"Error retrieving knowledge base: {str(e)}")
        return [
            KnowledgeBaseResponse(
                text=f"Error retrieving knowledge base: {str(e)}",
                metadata="",
                score=0,
                source="",
            )
        ]


def get_knowledgebase_tools() -> List[Tool]:
    """Get all knowledge base tools."""
    return [search_knowledgebase]
