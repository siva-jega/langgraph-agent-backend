import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MAX_WAIT_SECONDS = 1800  # 30 minutes cap
PAUSE_THRESHOLD_SECONDS = 10  # 10 seconds — longer waits pause the run instead of sleeping


class PauseRequired(Exception):
    """Raised when wait duration exceeds PAUSE_THRESHOLD_SECONDS.
    The DAG executor catches this, saves state, and schedules a re-enqueue."""

    def __init__(self, resume_at: datetime) -> None:
        self.resume_at = resume_at
        super().__init__(f"pause until {resume_at.isoformat()}")


class WaitStepExecutor:
    async def execute(
        self, config: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        duration_ms = config.get("durationMs")
        resume_at = config.get("resumeAt")

        now = datetime.now(timezone.utc)

        if duration_ms is not None:
            original_seconds = duration_ms / 1000
            target = datetime.fromtimestamp(now.timestamp() + original_seconds, tz=timezone.utc)
            wait_seconds = min(original_seconds, MAX_WAIT_SECONDS)
        elif resume_at is not None:
            target = datetime.fromisoformat(resume_at.replace("Z", "+00:00"))
            delta = (target - now).total_seconds()
            wait_seconds = max(0, min(delta, MAX_WAIT_SECONDS))
        else:
            wait_seconds = 0
            target = now

        if wait_seconds > PAUSE_THRESHOLD_SECONDS:
            logger.info(
                "WAIT step requires pause for %.1f seconds (resume_at=%s)",
                wait_seconds,
                target.isoformat(),
            )
            raise PauseRequired(resume_at=target)

        logger.info("WAIT step sleeping for %.1f seconds", wait_seconds)
        await asyncio.sleep(wait_seconds)

        return {"waited": True, "durationMs": int(wait_seconds * 1000)}
