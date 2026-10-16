"""Shared fixtures for workflow_service tests."""

from __future__ import annotations

import os
from typing import Any

import pytest
import respx

# Blank out real-service credentials so unit tests never talk to production.
os.environ.setdefault("RESEND_API_KEY", "test-key")
os.environ.setdefault("WORKFLOW_QUEUE_URL", "")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("NESTJS_URL", "http://nestjs-test")
os.environ.setdefault("INTERNAL_SECRET", "test-secret")

from workflow_service.core.config import app_settings  # noqa: E402
from common.project_settings import bind_project_settings  # noqa: E402

bind_project_settings(lambda: app_settings)

# Re-export for convenience (tests should import from workflow_service.testing directly)
from workflow_service.testing import FakeRunRepo, FakeWorkflowRepo  # noqa: E402, F401


# ---------------------------------------------------------------------------
# HTTP / email mocking fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def respx_mock():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def fake_email(monkeypatch):
    """Patches EmailService.send_email; returns list of recorded call dicts."""
    calls: list[dict] = []

    async def _fake_send(self, to: str, subject: str, body: str) -> dict:
        calls.append({"to": to, "subject": subject, "body": body})
        return {"id": "test-msg-id"}

    monkeypatch.setattr(
        "common.modules.email.service.EmailService.send_email",
        _fake_send,
    )
    return calls
