"""Base protocol for infrastructure clients. Single instance per app, created in core.lifecycle."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Connectable(Protocol):
    """Protocol for clients that have connect/disconnect lifecycle."""

    def is_connected(self) -> bool:
        ...


# Optional: use AsyncConnectable for async connect/disconnect
# class AsyncConnectable(Protocol):
#     async def connect(self) -> None: ...
#     async def disconnect(self) -> None: ...
#     def is_connected(self) -> bool: ...
