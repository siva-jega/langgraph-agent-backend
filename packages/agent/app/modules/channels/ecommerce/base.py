"""Abstract base for e-commerce providers.

To add a new provider (e.g. WooCommerce):
  1. Subclass EcommerceProvider
  2. Set the `source` class attribute (matches InboundPostback.source)
  3. Implement all abstract methods
  4. Register in registry.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EcommerceProvider(ABC):
    """Channel-agnostic interface for cart, product, and order operations."""

    source: str  # e.g. "SHOPIFY" — must match InboundPostback.source

    # ------------------------------------------------------------------ #
    # Credentials                                                         #
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def get_credentials(self, agent_config: dict) -> Any | None:
        """Resolve credentials from agent app config. Returns None if not configured."""

    # ------------------------------------------------------------------ #
    # Products                                                            #
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def fetch_product(self, product_id: str) -> dict | None:
        """Fetch a single product by ID. Returns None if not found."""

    @abstractmethod
    async def fetch_product_handles(self, product_ids: list[str]) -> dict[str, str]:
        """Batch fetch product handles. Returns {product_id: handle}."""

    # ------------------------------------------------------------------ #
    # Cart                                                                #
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def get_cart(self, session_id: str) -> dict | None:
        """Get the existing cart for a session. Returns None if no cart exists."""

    @abstractmethod
    async def upsert_cart(
        self,
        *,
        session_id: str,
        workspace_id: str,
        creds: Any,
        existing_cart: dict | None,
        variant_id: str,
    ) -> dict:
        """Create a new cart or add an item to an existing one.

        Returns a dict with at least:
          - ``cart_id``     (str) — provider cart / order ID
          - ``checkout_url``(str) — URL the customer uses to complete purchase
        """

    @abstractmethod
    async def save_cart(
        self,
        *,
        session_id: str,
        workspace_id: str,
        cart_id: str,
        checkout_url: str,
    ) -> None:
        """Persist cart state so it can be reused on the next postback."""

    # ------------------------------------------------------------------ #
    # Source extraction (parse AI tool-call results)                      #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def extract_products_from_sources(self, sources: list[dict]) -> list[dict]:
        """Extract a de-duplicated product list from AI runtime sources."""

    @abstractmethod
    def extract_order_from_sources(self, sources: list[dict]) -> dict | None:
        """Extract order data from AI runtime sources. Returns None if not found."""
