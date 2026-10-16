"""Unit-test-only fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Patch asyncio.sleep so unit tests never actually wait; returns recorded delays."""
    delays: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)
    return delays
