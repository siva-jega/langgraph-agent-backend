from typing import Any, Optional


def get_runtime_writer() -> Optional[Any]:
    """Return the current runtime's stream writer if available, else None.

    Works safely both inside and outside a LangGraph runtime context.
    """
    try:
        from langgraph.config import get_stream_writer  # type: ignore
    except Exception:
        return None

    try:
        return get_stream_writer()
    except Exception:
        return None


def emit_status(label: str) -> None:
    """Emit a status_update event to the UI stream.

    Picked up by graph.py's custom event handler and forwarded as
    `event: status_update` SSE so the frontend can show live phase indicators.
    Silently no-ops outside a LangGraph runtime context.
    """
    writer = get_runtime_writer()
    if not writer:
        return
    try:
        writer({"event": "status_update", "status": label})
    except Exception:
        return


def emit_update(message: str, channel: str = "", writer: Optional[Any] = None) -> None:
    """Emit a runtime update message via writer if available.

    Supports both callable writers and objects exposing a write() method.
    Silently no-ops if no writer is available or if emitting fails.
    """
    active_writer = writer if writer is not None else get_runtime_writer()
    if not active_writer:
        return

    try:
        if callable(active_writer):
            try:
                active_writer(message, channel)
            except TypeError:
                active_writer(message)
        elif hasattr(active_writer, "write"):
            try:
                active_writer.write(message, channel)  # type: ignore[attr-defined]
            except TypeError:
                active_writer.write(message)  # type: ignore[attr-defined]
    except Exception:
        # Intentionally swallow errors from optional streaming
        return
