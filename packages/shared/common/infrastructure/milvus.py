"""Milvus client: async REST client using Zilliz Cloud REST API v2 via httpx.

Replaces the previous pymilvus-based implementation (both AsyncMilvusClient and
the asyncio.to_thread-wrapped sync MilvusClient).  Using the REST API directly:

  - Fully async — no thread pool, no gRPC channel issues
  - Batch search: data=[v1,v2,...] returns N result sets split via `topks`
  - Hits are plain dicts — no pymilvus object parsing needed
  - Works reliably with Zilliz Cloud serverless (https:// URIs)

Initialized in core.lifecycle.
"""

import asyncio
import logging
from typing import Any, List, Optional, Protocol

import httpx

logger = logging.getLogger(__name__)


class MilvusConnectionSettings(Protocol):
    """Subset of app settings required to connect to Milvus."""

    MILVUS_URI: str | None
    MILVUS_TOKEN: str | None
    MILVUS_TIMEOUT: int


_MilvusClient_instance: Optional["MilvusClient"] = None

_SEARCH_PATH = "/v2/vectordb/entities/search"
_OUTPUT_FIELDS = ["data", "entityId", "associationId", "createdAt", "updatedAt", "metadata"]


class MilvusClient:
    """Async Milvus client using Zilliz Cloud REST API v2 directly via httpx.

    All methods are fully async — no thread pool involved.
    """

    def __init__(self, settings: MilvusConnectionSettings) -> None:
        self._settings = settings
        self._http: Optional[httpx.AsyncClient] = None
        self._base_url: str = ""

    async def connect(self) -> None:
        """Create the httpx async client."""
        try:
            uri = (self._settings.MILVUS_URI or "http://localhost:19530").rstrip("/")
            self._base_url = uri
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._settings.MILVUS_TOKEN:
                headers["Authorization"] = f"Bearer {self._settings.MILVUS_TOKEN}"
            self._http = httpx.AsyncClient(
                base_url=uri,
                headers=headers,
                timeout=float(self._settings.MILVUS_TIMEOUT),
            )
            logger.info("Milvus REST client ready at %s", uri)
        except Exception as e:
            logger.error("Failed to create Milvus REST client: %s", e)
            raise

    async def disconnect(self) -> None:
        """Close the httpx client."""
        try:
            if self._http:
                await self._http.aclose()
                self._http = None
            logger.info("Milvus REST client closed")
        except Exception as e:
            logger.error("Error closing Milvus REST client: %s", e)

    def is_connected(self) -> bool:
        return self._http is not None

    async def _ensure_connected(self) -> None:
        if not self.is_connected():
            await self.connect()

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _search(
        self,
        collection_name: str,
        data: List[Any],
        anns_field: str,
        search_params: dict,
        limit: int,
        filter_expr: Optional[str],
        output_fields: List[str],
    ) -> dict:
        """POST /v2/vectordb/entities/search and return the parsed body."""
        await self._ensure_connected()
        payload: dict[str, Any] = {
            "collectionName": collection_name,
            "data": data,
            "annsField": anns_field,
            "searchParams": search_params,
            "limit": limit,
            "outputFields": output_fields,
        }
        if filter_expr:
            payload["filter"] = filter_expr

        resp = await self._http.post(_SEARCH_PATH, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"Milvus search error {body.get('code')}: {body.get('message')}")
        return body

    @staticmethod
    def _build_required_scope_filter(entity_ids: List[str], folder_ids: List[str]) -> str:
        filters = []
        clean_entity_ids = [
            str(eid) for eid in (entity_ids or []) if str(eid).strip()
        ]
        clean_folder_ids = [
            str(fid) for fid in (folder_ids or []) if str(fid).strip()
        ]

        if clean_entity_ids:
            filters.append(
                "(" + " or ".join(f'entityId == "{eid}"' for eid in clean_entity_ids) + ")"
            )
        if clean_folder_ids:
            filters.append(
                "(" + " or ".join(f'associationId == "{fid}"' for fid in clean_folder_ids) + ")"
            )

        if not filters:
            raise ValueError("Milvus scoped search requires entity_ids or folder_ids")
        return " or ".join(filters)

    @staticmethod
    def _split_by_topks(hits: List[Any], topks: List[int]) -> List[List[Any]]:
        """Split a flat results list into per-query result sets using topks."""
        result_sets: List[List[Any]] = []
        idx = 0
        for k in topks:
            result_sets.append(hits[idx: idx + k])
            idx += k
        return result_sets

    # ------------------------------------------------------------------ #
    # Public search methods                                                #
    # ------------------------------------------------------------------ #

    async def search_by_embedding_and_entity_and_folder_ids(
        self,
        collection_name: str,
        embedding: List[float],
        entity_ids: List[str],
        folder_ids: List[str],
        top_k: int = 5,
    ) -> List[List[Any]]:
        """Single dense search. Returns [[hit, ...]] to match original pymilvus shape."""
        expr = self._build_required_scope_filter(entity_ids, folder_ids)
        body = await self._search(
            collection_name=collection_name,
            data=[embedding],
            anns_field="embedding",
            search_params={"metricType": "COSINE"},
            limit=top_k,
            filter_expr=expr,
            output_fields=_OUTPUT_FIELDS,
        )
        return [body.get("data", [])]

    async def search_by_embeddings_batch(
        self,
        collection_name: str,
        embeddings: List[List[float]],
        entity_ids: List[str],
        folder_ids: List[str],
        top_k: int = 5,
    ) -> List[List[Any]]:
        """Batch dense search: one REST call for N embeddings, returns N result sets."""
        expr = self._build_required_scope_filter(entity_ids, folder_ids)
        body = await self._search(
            collection_name=collection_name,
            data=embeddings,
            anns_field="embedding",
            search_params={"metricType": "COSINE"},
            limit=top_k,
            filter_expr=expr,
            output_fields=_OUTPUT_FIELDS,
        )
        topks = body.get("topks", [top_k] * len(embeddings))
        return self._split_by_topks(body.get("data", []), topks)

    async def search_by_sparse_and_entity_and_folder_ids(
        self,
        collection_name: str,
        text: str,
        entity_ids: List[str],
        folder_ids: List[str],
        top_k: int = 10,
    ) -> List[List[Any]]:
        """BM25 sparse search. Returns [[hit, ...]]."""
        expr = self._build_required_scope_filter(entity_ids, folder_ids)
        body = await self._search(
            collection_name=collection_name,
            data=[text],
            anns_field="sparse",
            search_params={"metricType": "BM25"},
            limit=top_k,
            filter_expr=expr,
            output_fields=_OUTPUT_FIELDS,
        )
        return [body.get("data", [])]

    async def search_by_embedding_and_kids(
        self,
        collection_name: str,
        embedding: List[float],
        kids: List[str],
        eids: List[str],
        top_k: int = 5,
    ) -> List[List[Any]]:
        """Dense search scoped to kids (knowledge IDs) and eids (entity IDs)."""
        all_ids = list(kids or []) + list(eids or [])
        if not all_ids:
            raise ValueError("Milvus scoped search requires kids or eids")
        assoc_expr = " or ".join(f'associationId == "{i}"' for i in all_ids)
        entity_expr = " or ".join(f'entityId == "{i}"' for i in all_ids)
        expr = f"({assoc_expr}) or ({entity_expr})"
        body = await self._search(
            collection_name=collection_name,
            data=[embedding],
            anns_field="embedding",
            search_params={"metricType": "COSINE"},
            limit=top_k,
            filter_expr=expr,
            output_fields=_OUTPUT_FIELDS,
        )
        return [body.get("data", [])]

    async def search_by_sparse_and_kids(
        self,
        collection_name: str,
        text: str,
        kids: List[str],
        eids: List[str],
        top_k: int = 10,
    ) -> List[List[Any]]:
        """BM25 sparse search scoped to kids and eids."""
        all_ids = list(kids or []) + list(eids or [])
        if not all_ids:
            raise ValueError("Milvus scoped search requires kids or eids")
        assoc_expr = " or ".join(f'associationId == "{i}"' for i in all_ids)
        entity_expr = " or ".join(f'entityId == "{i}"' for i in all_ids)
        expr = f"({assoc_expr}) or ({entity_expr})"
        body = await self._search(
            collection_name=collection_name,
            data=[text],
            anns_field="sparse",
            search_params={"metricType": "BM25"},
            limit=top_k,
            filter_expr=expr,
            output_fields=_OUTPUT_FIELDS,
        )
        return [body.get("data", [])]


def get_milvus() -> MilvusClient:
    """Return the app Milvus client."""
    if _MilvusClient_instance is None:
        raise RuntimeError("Milvus not initialized; app lifespan may not have run.")
    return _MilvusClient_instance


def set_milvus(client: MilvusClient) -> None:
    """Set by lifecycle on startup. Do not call from application code."""
    global _MilvusClient_instance
    _MilvusClient_instance = client


def clear_milvus() -> None:
    """Cleared by lifecycle on shutdown."""
    global _MilvusClient_instance
    _MilvusClient_instance = None
