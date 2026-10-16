"""
Protocols for common services. Used for typing cache, API, and search backends
without coupling to concrete implementations.
"""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class RedisLike(Protocol):
    """Cache backend: async get_json / set_json. Implemented by shared RedisClient."""

    async def get_json(self, key: str) -> Optional[Dict[str, Any]]: ...
    async def set_json(
        self, key: str, value: Dict[str, Any], ex: Optional[int] = None
    ) -> bool: ...
    async def delete(self, key: str) -> bool: ...


@runtime_checkable
class ApiClientLike(Protocol):
    """HTTP API client: async get_json / post_json. Used for NestJS user-backend."""

    async def get_json(self, url: str) -> Optional[Dict[str, Any]]: ...
    async def post_json(
        self, url: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]: ...


@runtime_checkable
class MilvusSearchLike(Protocol):
    """Vector/search backend: embedding and sparse search with entity_ids. Implemented by MilvusClient."""

    async def search_by_embedding_and_entity_and_folder_ids(
        self,
        collection_name: str,
        embedding: List[float],
        entity_ids: List[str],
        folder_ids: List[str],
        top_k: int = 5,
    ) -> Any: ...
    async def search_by_embeddings_batch(
        self,
        collection_name: str,
        embeddings: List[List[float]],
        entity_ids: List[str],
        folder_ids: List[str],
        top_k: int = 5,
    ) -> Any: ...
    async def search_by_sparse_and_entity_and_folder_ids(
        self,
        collection_name: str,
        text: str,
        entity_ids: List[str],
        folder_ids: List[str],
        top_k: int = 10,
    ) -> Any: ...
    async def search_by_embedding_and_kids(
        self,
        collection_name: str,
        embedding: List[float],
        kids: List[str],
        eids: List[str],
        top_k: int = 5,
    ) -> Any: ...
    async def search_by_sparse_and_kids(
        self,
        collection_name: str,
        text: str,
        kids: List[str],
        eids: List[str],
        top_k: int = 10,
    ) -> Any: ...
