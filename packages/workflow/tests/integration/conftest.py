"""
Integration test fixtures.

Requires:
  DATABASE_URL pointing to a _test Postgres database.
  Prisma migrations must already be applied (run `npx prisma migrate deploy`
  in user-backend before the first run, or use `test:db:prepare`).

Each test gets its own asyncpg pool (function-scoped) so there are no
event-loop sharing issues with pytest-asyncio >= 1.x.
"""
import os
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from common.infrastructure.postgres import PostgresClient, set_pg, clear_pg
from workflow_service.modules.runs.repository import RunRepository
from workflow_service.modules.workflows.repository import WorkflowRepository
from tests.integration.factories import (  # noqa: F401 — re-exported for test use
    make_workflow,
    make_node,
    make_edge,
    make_run,
    seed_graph,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    assert url, "DATABASE_URL must be set for integration tests"
    assert "_test" in url, (
        f"Integration tests require a _test database, got: {url}"
    )
    return url


# ── per-test pool & pg setup ─────────────────────────────────────────────────

@pytest_asyncio.fixture
async def pg_pool():
    """Asyncpg pool per test. Sets the global get_pg() singleton for repos."""
    pool = await asyncpg.create_pool(_db_url(), min_size=1, max_size=3)
    set_pg(PostgresClient(pool))
    yield pool
    clear_pg()
    await pool.close()


# ── workspace/user seed (per-test, not truncated between tests) ──────────────

@pytest_asyncio.fixture
async def workspace_seed(pg_pool):
    """Creates a User + Workspace for the test."""
    user_id = str(uuid4())
    ws_id = str(uuid4())
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public."User" (id, email, name, "emailVerified", "createdAt", "updatedAt")
            VALUES ($1, $2, $3, true, now(), now())
            """,
            user_id,
            f"integration-test-{user_id[:8]}@example.com",
            "Integration Test User",
        )
        await conn.execute(
            """
            INSERT INTO public."Workspace" (id, name, "ownerId", "createdAt", "updatedAt")
            VALUES ($1, $2, $3, now(), now())
            """,
            ws_id,
            "Integration Test Workspace",
            user_id,
        )
    return {"user_id": user_id, "workspace_id": ws_id}


# ── per-test truncate ────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def truncate(pg_pool):
    """Wipe workflow tables after each integration test."""
    yield
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE
              public."WorkflowNodeRuns",
              public."WorkflowRuns",
              public."WorkflowEdges",
              public."WorkflowNodes",
              public."Workflows"
            CASCADE
            """
        )


# ── expose repos for tests ────────────────────────────────────────────────────

@pytest.fixture
def run_repo():
    return RunRepository()


@pytest.fixture
def workflow_repo():
    return WorkflowRepository()
