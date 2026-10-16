"""Channel AI engine (nodes, graph, prompt helpers).

Initialized from app lifespan via ``init_channel_graph`` with the same
checkpointer as the main widget agent when Postgres checkpointing is enabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.channels.ai.engine import ChannelGraph

_channel_graph: ChannelGraph | None = None


def init_channel_graph(checkpointer=None) -> "ChannelGraph":
    """Create the global channel runtime (call once at startup)."""
    global _channel_graph
    from app.modules.channels.ai.engine import ChannelGraph as _ChannelGraph

    _channel_graph = _ChannelGraph(checkpointer=checkpointer)
    return _channel_graph


def get_channel_graph() -> "ChannelGraph":
    """Return the channel runtime instance."""
    if _channel_graph is None:
        raise ValueError("Channel graph not initialized; init_channel_graph was not called")
    return _channel_graph


def init_channel_runtime(checkpointer=None) -> "ChannelGraph":
    """Preferred runtime initializer."""
    return init_channel_graph(checkpointer)


def get_channel_runtime() -> "ChannelGraph":
    """Preferred runtime accessor."""
    return get_channel_graph()


__all__ = [
    "get_channel_graph",
    "get_channel_runtime",
    "init_channel_graph",
    "init_channel_runtime",
]
