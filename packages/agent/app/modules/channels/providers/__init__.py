"""Channel provider registry."""

from typing import Optional

from app.modules.channels.providers.base import ChannelProvider

# --- Import concrete adapters -------------------------------------------------
from app.modules.channels.providers.gmail import GmailAdapter
from app.modules.channels.providers.meta.instagram.adapter import InstagramAdapter
from app.modules.channels.providers.meta.messenger.adapter import MessengerAdapter
from app.modules.channels.providers.meta.whatsapp.adapter import WhatsAppAdapter

# --- Registry -----------------------------------------------------------------

_PROVIDERS: dict[str, ChannelProvider] = {
    "gmail": GmailAdapter(),
    "whatsapp": WhatsAppAdapter(),
    "instagram": InstagramAdapter(),
    "messenger": MessengerAdapter(),
}


def get_provider(channel: str) -> Optional[ChannelProvider]:
    """Look up the provider for a given channel name."""
    return _PROVIDERS.get(channel)


def register_provider(provider: ChannelProvider) -> None:
    """Register a channel provider at runtime."""
    _PROVIDERS[provider.channel] = provider


def registered_channels() -> list[str]:
    """Return the list of currently registered channel names."""
    return list(_PROVIDERS.keys())


def get_adapter(channel: str) -> Optional[ChannelProvider]:
    """Backward-compatible alias."""
    return get_provider(channel)


def register_adapter(adapter: ChannelProvider) -> None:
    """Backward-compatible alias."""
    register_provider(adapter)


__all__ = [
    "ChannelProvider",
    "get_adapter",
    "get_provider",
    "register_adapter",
    "register_provider",
    "registered_channels",
]
