"""Bound application settings for shared code (set once from agent `main` / lifespan)."""

from __future__ import annotations

from typing import Any, Callable

_get_settings: Callable[[], Any] | None = None


def bind_project_settings(getter: Callable[[], Any]) -> None:
    """Register the callable that returns the live AppSettings (or compatible) object."""
    global _get_settings
    _get_settings = getter


def project_settings() -> Any:
    if _get_settings is None:
        raise RuntimeError(
            "bind_project_settings() was not called; shared services need app settings."
        )
    return _get_settings()
