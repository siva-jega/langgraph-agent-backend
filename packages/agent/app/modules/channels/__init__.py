"""Channels module.

Owns external channel ingress/egress and channel-specific provider behavior.
Agent reasoning remains in the channel runtime for now, behind this boundary.
"""

from app.modules.channels.providers import (
    ChannelProvider,
    get_provider,
    register_provider,
    registered_channels,
)
from app.modules.channels.ai import get_channel_runtime, init_channel_runtime
from app.modules.channels.service import (
    handle_inbound_message,
    process_inbound_message,
)
from app.modules.channels.consumers import start_consumer, stop_consumer

__all__ = [
    "ChannelProvider",
    "get_provider",
    "register_provider",
    "registered_channels",
    "handle_inbound_message",
    "process_inbound_message",
    "start_consumer",
    "stop_consumer",
    "get_channel_runtime",
    "init_channel_runtime",
]
