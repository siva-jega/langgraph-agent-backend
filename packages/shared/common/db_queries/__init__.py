"""SQL queries for database operations. Load queries from .sql files."""

import os
from pathlib import Path

_QUERIES_DIR = Path(__file__).parent


def load_query(filename: str) -> str:
    """Load a SQL query from a .sql file in this directory."""
    query_path = _QUERIES_DIR / filename
    if not query_path.exists():
        raise FileNotFoundError(f"Query file not found: {query_path}")
    return query_path.read_text()


# Pre-loaded queries (lazy load on first access)
_create_messages_query: str | None = None
_get_agent_query: str | None = None
_update_usage_query: str | None = None
_create_chat_query: str | None = None
_messages_only_query: str | None = None


def get_create_messages_query() -> str:
    """Get the create messages query (cached)."""
    global _create_messages_query
    if _create_messages_query is None:
        _create_messages_query = load_query("messages.sql")
    return _create_messages_query


def get_agent_query() -> str:
    """Get the get agent config query (cached)."""
    global _get_agent_query
    if _get_agent_query is None:
        _get_agent_query = load_query("agent.sql")
    return _get_agent_query


def get_update_usage_query() -> str:
    """Get the update usage message credits query (cached)."""
    global _update_usage_query
    if _update_usage_query is None:
        _update_usage_query = load_query("usage.sql")
    return _update_usage_query


def get_create_chat_query() -> str:
    """Get the create chat query (cached)."""
    global _create_chat_query
    if _create_chat_query is None:
        _create_chat_query = load_query("chat.sql")
    return _create_chat_query


def get_messages_only_query() -> str:
    """Get the messages-only insert query (cached)."""
    global _messages_only_query
    if _messages_only_query is None:
        _messages_only_query = load_query("messages_only.sql")
    return _messages_only_query


_app_credentials_query: str | None = None


def get_app_credentials_query() -> str:
    """Get the app credentials query (cached)."""
    global _app_credentials_query
    if _app_credentials_query is None:
        _app_credentials_query = load_query("app_credentials.sql")
    return _app_credentials_query


_app_credentials_by_slug_query: str | None = None


def get_app_credentials_by_slug_query() -> str:
    """Get app credentials by slug query (cached)."""
    global _app_credentials_by_slug_query
    if _app_credentials_by_slug_query is None:
        _app_credentials_by_slug_query = load_query("app_credentials_by_slug.sql")
    return _app_credentials_by_slug_query
_create_ticket_query: str | None = None

def get_create_ticket_query() -> str:
    """Get the create ticket insert query (cached)."""
    global _create_ticket_query
    if _create_ticket_query is None:
        _create_ticket_query = load_query("ticket.sql")
    return _create_ticket_query
_create_customer_query: str | None = None


def get_create_customer_query() -> str:
    """Get the create customer insert query (cached)."""
    global _create_customer_query
    if _create_customer_query is None:
        _create_customer_query = load_query("lead.sql")
    return _create_customer_query


_workspace_apps_query: str | None = None


def get_workspace_apps_query() -> str:
    global _workspace_apps_query
    if _workspace_apps_query is None:
        _workspace_apps_query = load_query("workspace_apps.sql")
    return _workspace_apps_query


_workspace_owner_query: str | None = None


def get_workspace_owner_query() -> str:
    global _workspace_owner_query
    if _workspace_owner_query is None:
        _workspace_owner_query = load_query("workspace_owner.sql")
    return _workspace_owner_query


_pixel_product_views_query: str | None = None


def get_pixel_product_views_query() -> str:
    global _pixel_product_views_query
    if _pixel_product_views_query is None:
        _pixel_product_views_query = load_query("pixel_product_views.sql")
    return _pixel_product_views_query
