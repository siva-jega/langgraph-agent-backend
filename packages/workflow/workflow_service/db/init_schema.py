import logging

from common.infrastructure.postgres import get_pg

logger = logging.getLogger(__name__)


async def ensure_schema() -> None:
    """Verify that the workflow tables exist (owned by Prisma in the public schema)."""
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchval(
                """SELECT count(*) FROM pg_tables
                   WHERE schemaname = 'public' AND tablename = 'WorkflowDefinition'"""
            )
            if row and row > 0:
                logger.info("Workflow tables found in public schema (Prisma-managed)")
            else:
                logger.warning(
                    "WorkflowDefinition table not found in public schema. "
                    "Run 'npx prisma db push' in the user-backend first."
                )
    except RuntimeError:
        logger.warning("Postgres not available, skipping schema check")
    except Exception as e:
        logger.error("Schema check failed: %s", e, exc_info=True)
