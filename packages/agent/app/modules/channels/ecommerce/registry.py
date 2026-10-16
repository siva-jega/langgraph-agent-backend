"""E-commerce provider registry.

To add a new provider:
  1. Implement EcommerceProvider in a new module
  2. Import it here and add it to _PROVIDERS
"""

from app.modules.channels.ecommerce.base import EcommerceProvider
from app.modules.channels.ecommerce.shopify import ShopifyProvider

_PROVIDERS: dict[str, EcommerceProvider] = {
    "SHOPIFY": ShopifyProvider(),
}


def get_ecommerce_provider(source: str) -> EcommerceProvider | None:
    """Look up an e-commerce provider by postback source name (case-insensitive)."""
    return _PROVIDERS.get(source.upper())


def get_ecommerce_provider_for_agent(agent_config: dict) -> EcommerceProvider | None:
    """Return the first registered provider whose source name appears in the agent's app slugs."""
    apps = agent_config.get("config", {}).get("apps", [])
    for app in apps:
        slug = (app.get("slug") or "").upper()
        for source, provider in _PROVIDERS.items():
            if source in slug:
                return provider
    return None


def register_ecommerce_provider(provider: EcommerceProvider) -> None:
    """Register a provider at runtime (useful for plugins / tests)."""
    _PROVIDERS[provider.source.upper()] = provider
