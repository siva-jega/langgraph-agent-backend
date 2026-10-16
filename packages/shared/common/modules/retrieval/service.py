"""
Retrieval service: dense (embedding) + sparse search, optional Cohere rerank.

Depends on MilvusSearchLike (e.g. MilvusClient) and optionally OpenAI + Cohere.
Lazy-connects on first retrieve/retrieve_sparse/retrieve_multiple.
"""

import asyncio
import json
import logging
from typing import Any, List, Optional

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None
try:
    import cohere
except ImportError:
    cohere = None
try:
    import yaml
except ImportError:
    yaml = None

from common.modules._protocols import MilvusSearchLike
from common.modules.retrieval.models import RetrievalResponse
from common.modules.settings_protocols import SupportsRetrievalSettings

logger = logging.getLogger(__name__)


def _get_field(obj: Any, key: str, default: Any = "") -> Any:
    """Extract a field from a hit dict or attribute-style object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _hit_to_response(hit: Any, source: str, entity_ids: List[str]) -> RetrievalResponse:
    """Map a Milvus search hit to RetrievalResponse.

    REST API v2 returns hits as flat dicts — entity fields (data, entityId, etc.)
    are directly on the hit, not nested under an 'entity' key.

    For COSINE metric, distance = cosine similarity (higher = more relevant).
    Score is used directly so higher score = more relevant.
    """
    if isinstance(hit, dict):
        # REST API: flat dict — fields are directly on the hit
        distance = hit.get("distance", 0.0)
        entity = hit  # fields are at the top level
    elif hasattr(hit, "entity"):
        # pymilvus Hit object (legacy path)
        distance = hit.distance
        inner = hit.entity
        # Unwrap if entity is itself a Hit wrapping another level
        entity = inner.entity if (hasattr(inner, "entity") and not isinstance(inner, dict)) else inner
    else:
        distance = getattr(hit, "distance", 0.0)
        entity = hit

    data = _get_field(entity, "data", "")
    meta = _get_field(entity, "metadata", {})
    entity_id = _get_field(entity, "entityId", "")
    association_id = _get_field(entity, "associationId", "")

    # Milvus stores `metadata` as a VARCHAR containing JSON (already serialized).
    meta_obj: dict[str, Any] = {}
    if isinstance(meta, dict):
        meta_obj = dict(meta)
    elif isinstance(meta, str) and meta:
        try:
            parsed_meta = json.loads(meta)
            if isinstance(parsed_meta, dict):
                meta_obj = parsed_meta
            else:
                meta_obj = {"raw_metadata": meta}
        except Exception:
            meta_obj = {"raw_metadata": meta}

    # Preserve Milvus routing keys for downstream scoped expansion/debugging.
    if entity_id:
        meta_obj.setdefault("entityId", entity_id)
    if association_id:
        meta_obj.setdefault("associationId", association_id)

    metadata_out = json.dumps(meta_obj) if meta_obj else "{}"
    return RetrievalResponse(
        text=data if isinstance(data, str) else str(data),
        metadata=metadata_out,
        score=float(distance),
        source=source,
    )


class RetrievalService:
    """Dense + sparse search and optional Cohere rerank. Uses MilvusSearchLike + OpenAI/Cohere."""

    def __init__(
        self,
        settings: SupportsRetrievalSettings,
        milvus_service: Optional[MilvusSearchLike] = None,
    ):
        self._s = settings
        self._milvus = milvus_service
        self._openai: Optional[Any] = None
        self._cohere: Optional[Any] = None
        self._init = False

    async def _ensure_connected(self) -> None:
        if self._init:
            return
        if self._s.OPENAI_API_KEY and AsyncOpenAI:
            self._openai = AsyncOpenAI(
                api_key=self._s.OPENAI_API_KEY,
                base_url=self._s.OPENAI_API_BASE,
            )
        if self._s.COHERE_AZURE_API_KEY and cohere:
            self._cohere = cohere.ClientV2(
                api_key=self._s.COHERE_AZURE_API_KEY,
                base_url=self._s.COHERE_AZURE_BASE_URL,
            )
        self._init = True

    def _ensure_milvus(self) -> None:
        if not self._milvus:
            raise RuntimeError("Milvus service not provided to RetrievalService")

    async def embed_query(self, text: str) -> List[float]:
        """Single OpenAI embedding for `text` (reuse across multiple Milvus dense searches)."""
        await self._ensure_connected()
        if not self._openai:
            raise RuntimeError("OpenAI client not available for embeddings")
        emb = await self._openai.embeddings.create(
            input=text, model=self._s.OPENAI_EMBEDDING_MODEL
        )
        return emb.data[0].embedding

    async def retrieve(
        self,
        text: str,
        entity_ids: List[str],
        folder_ids: List[str],
        collection_name: str,
        top_k: Optional[int] = None,
        embedding: Optional[List[float]] = None,
    ) -> List[RetrievalResponse]:
        """Dense search: embed text (unless `embedding` is provided), then search Milvus."""
        await self._ensure_connected()
        self._ensure_milvus()
        if not self._openai:
            raise RuntimeError("OpenAI client not available for embeddings")
        try:
            if embedding is not None:
                vec = embedding
            else:
                emb = await self._openai.embeddings.create(
                    input=text, model=self._s.OPENAI_EMBEDDING_MODEL
                )
                vec = emb.data[0].embedding
            top_k = top_k or self._s.RETRIEVAL_DEFAULT_TOP_K
            results = await self._milvus.search_by_embedding_and_entity_and_folder_ids(
                collection_name=collection_name,
                embedding=vec,
                entity_ids=entity_ids,
                folder_ids=folder_ids,
                top_k=top_k,
            )
            if not results:
                return []
            return [_hit_to_response(h, "dense_search", entity_ids) for h in results[0]]
        except Exception as e:
            logger.error("retrieve: %s", e)
            return []

    async def retrieve_sparse(
        self,
        text: str,
        entity_ids: List[str],
        folder_ids: List[str],
        collection_name: str,
        top_k: Optional[int] = None,
    ) -> List[RetrievalResponse]:
        """Sparse search in Milvus."""
        await self._ensure_connected()
        self._ensure_milvus()
        try:
            top_k = top_k or self._s.RETRIEVAL_DEFAULT_TOP_K
            results = await self._milvus.search_by_sparse_and_entity_and_folder_ids(
                collection_name=collection_name,
                text=text,
                entity_ids=entity_ids,
                folder_ids=folder_ids,
                top_k=top_k,
            )
            if not results:
                return []
            return [
                _hit_to_response(h, "sparse_search", entity_ids) for h in results[0]
            ]
        except Exception as e:
            logger.error("retrieve_sparse: %s", e)
            return []

    async def _retrieve_dense_batch(
        self,
        texts: List[str],
        entity_ids: List[str],
        folder_ids: List[str],
        collection_name: str,
        top_k: int,
    ) -> List[RetrievalResponse]:
        """Batch-embed all texts in one OpenAI call, then issue a single Milvus search
        with data=[v1, v2, ...] returning N result sets — one per input text."""
        if not texts:
            return []
        if not self._openai:
            raise RuntimeError("OpenAI client not available for embeddings")
        resp = await self._openai.embeddings.create(
            input=texts, model=self._s.OPENAI_EMBEDDING_MODEL
        )
        embeddings = [e.embedding for e in sorted(resp.data, key=lambda x: x.index)]
        batch_results = await self._milvus.search_by_embeddings_batch(
            collection_name=collection_name,
            embeddings=embeddings,
            entity_ids=entity_ids,
            folder_ids=folder_ids,
            top_k=top_k,
        )
        return [
            _hit_to_response(h, "dense_search", entity_ids)
            for result_set in batch_results
            for h in result_set
        ]

    async def retrieve_multiple(
        self,
        text_list: List[str],
        entity_ids: List[str],
        folder_ids: List[str],
        query: str,
        query_text_for_sparse_search: str,
        collection_name: str,
    ) -> List[RetrievalResponse]:
        """Run dense + optional sparse, dedupe, sort by score, optionally rerank with Cohere.

        Batch-embeds all texts in one OpenAI call and issues a single Milvus search
        for N result sets in one round-trip. Sparse search runs concurrently.
        """
        await self._ensure_connected()
        self._ensure_milvus()
        try:
            top_k = self._s.RETRIEVAL_DEFAULT_TOP_K
            texts = [t for t in (text_list or []) if t]

            dense_task = self._retrieve_dense_batch(
                texts, entity_ids, folder_ids, collection_name, top_k
            )
            sparse_task = self.retrieve_sparse(
                query_text_for_sparse_search, entity_ids, folder_ids, collection_name, top_k=top_k
            ) if query_text_for_sparse_search else asyncio.sleep(0, result=[])

            dense, sparse = await asyncio.gather(dense_task, sparse_task)

            # Dedupe by text, preserving dense then sparse order
            seen: set = set()
            unique: List[RetrievalResponse] = []
            unique_dense: List[RetrievalResponse] = []
            unique_sparse: List[RetrievalResponse] = []
            for n in list(dense) + list(sparse):
                if n.text not in seen:
                    seen.add(n.text)
                    unique.append(n)
                    (unique_dense if n.source == "dense_search" else unique_sparse).append(n)

            # Sort best-first by score (cosine similarity — higher = more relevant)
            unique.sort(key=lambda n: n.score, reverse=True)

            if len(unique) <= 1:
                return unique[:5]

            # Cohere is sync; run in thread to avoid blocking.
            if self._cohere and unique_dense and yaml:
                try:
                    # Sort dense by score before sending to Cohere
                    unique_dense.sort(key=lambda n: n.score, reverse=True)
                    docs = [
                        {"text": d.text, "metadata": d.metadata, "score": d.score}
                        for d in unique_dense
                    ]
                    yaml_docs = [yaml.dump(d, sort_keys=False) for d in docs]

                    def _rerank():
                        return self._cohere.rerank(
                            model=self._s.COHERE_RERANK_MODEL,
                            documents=yaml_docs,
                            query=query,
                            top_n=min(10, len(docs)),
                        )

                    resp = await asyncio.to_thread(_rerank)
                    idx = [r.index for r in resp.results]
                    reranked = [unique_dense[i] for i in idx]
                    unique_sparse.sort(key=lambda n: n.score, reverse=True)
                    return reranked[:3] + unique_sparse[:2]
                except Exception as e:
                    logger.error("rerank: %s", e)
            return unique[:5]
        except Exception as e:
            logger.error("retrieve_multiple: %s", e)
            return []
