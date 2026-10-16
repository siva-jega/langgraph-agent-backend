from typing import List, Optional
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from .models import AddMemoryResult, GetMemoriesResult, MemoryItem
from common.tools.utils.writer import emit_update

logger = logging.getLogger(__name__)

_mem0_client = None

def get_mem0_client(api_key: str):
    global _mem0_client
    if _mem0_client is None:
        try:
            from mem0 import MemoryClient
            _mem0_client = MemoryClient(api_key=api_key)
            logger.info("Mem0 client initialized")
        except ImportError:
            logger.error("mem0ai not installed. Run: pip install mem0ai")
            raise
    return _mem0_client


@tool(response_format="content_and_artifact")
async def add_memory(
    memory: str,
    config: RunnableConfig,
    metadata: Optional[dict] = None,
) -> AddMemoryResult:
    """Add a new memory for the current user.

    Args:
        memory: The memory text to store
        config: Configuration containing user_id
        metadata: Optional metadata

    Returns:
        AddMemoryResult with success status and memory ID
    """
    try:
        emit_update(
            {"tool": "Memory", "update": f"Adding memory: {memory[:50]}..."},
            "memory",
        )
        
        user_id = config.get("configurable", {}).get("user_id")
        if not user_id:
            return AddMemoryResult(
                success=False,
                message="No user_id provided in configuration.",
            ), {
                "name": "Memory",
                "tool": "Add Memory",
                "data": {"success": False, "error": "No user_id"},
            }

        client = get_mem0_client(config.get("configurable", {}).get("mem0_api_key"))
        result = client.add(memory, user_id=user_id, metadata=metadata)
        
        emit_update(
            {"tool": "Memory", "update": "Memory added successfully"},
            "memory",
        )
        
        add_result = AddMemoryResult(
            success=True,
            message="Memory added successfully",
        )
        
        return add_result, {
            "name": "Memory",
            "tool": "Add Memory",
            "data": {
                "success": True,
                "memory": memory[:100],
            },
        }

    except Exception as e:
        logger.error(f"Error adding memory: {str(e)}")
        return AddMemoryResult(
            success=False,
            message=f"Error: {str(e)}",
        ), {
            "name": "Memory",
            "tool": "Add Memory",
            "data": {"success": False, "error": str(e)},
        }


@tool(response_format="content_and_artifact")
async def get_memories(
    query: str,
    config: RunnableConfig,
    limit: int = 5,
) -> GetMemoriesResult:
    """Search memories for the current user.

    Args:
        query: Search query
        config: Configuration containing user_id
        limit: Max results (default: 10)

    Returns:
        GetMemoriesResult with list of memories
    """
    try:
        emit_update(
            {"tool": "Memory", "update": f"Searching: {query}"},
            "memory",
        )
    
        user_id = config.get("configurable", {}).get("user_id")
        if not user_id:
            return GetMemoriesResult(
                success=False,
                message="No user_id provided.",
                memories=[],
            ), {
                "name": "Memory",
                "tool": "Get Memories",
                "data": {"success": False, "error": "No user_id"},
            }
        client = get_mem0_client(config.get("configurable", {}).get("mem0_api_key"))
        results = client.search(query, filters={"user_id": user_id}, top_k=limit)
        
        memories = []
        for item in (results['results'] or []):
            memories.append(
                MemoryItem(
                    id=str(item.get("id", "")),
                    memory=item.get("memory", ""),
                    metadata=item.get("metadata"),
                )
            )
        
        emit_update(
            {"tool": "Memory", "update": f"Found {len(memories)} memories"},
            "memory",
        )
        
        result = GetMemoriesResult(
            success=True,
            message=f"Found {len(memories)} memories",
            memories=memories,
        )
        
        return result, {
            "name": "Memory",
            "tool": "Get Memories",
            "data": {
                "count": len(memories),
                "memories": [{"id": m.id, "memory": m.memory} for m in memories],
            },
        }

    except Exception as e:
        logger.error(f"Error searching memories: {str(e)}")
        return GetMemoriesResult(
            success=False,
            message=f"Error: {str(e)}",
            memories=[],
        ), {
            "name": "Memory",
            "tool": "Get Memories",
            "data": {"success": False, "error": str(e)},
        }

def get_memory_tools() -> List[Tool]:
    return [add_memory, get_memories]