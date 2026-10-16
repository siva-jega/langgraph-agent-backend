import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class ErrorPolicyResult:
    action: Literal["continue", "abort", "retry"]
    output: dict | None = None
    delay_seconds: float = 0


def evaluate_error_policy(
    policy: str,
    retry_count: int,
    config_json: dict | None = None,
) -> ErrorPolicyResult:
    """Determine what to do after a step failure based on its error policy."""
    if policy == "FAIL":
        return ErrorPolicyResult(action="abort")

    if policy == "SKIP":
        return ErrorPolicyResult(action="continue", output={})

    if policy == "RETRY":
        max_retries = (config_json or {}).get("maxRetries", 3)
        if retry_count < max_retries:
            delay = min(2 ** retry_count, 60)
            return ErrorPolicyResult(action="retry", delay_seconds=delay)
        # Exhausted retries — check fallback
        fallback = (config_json or {}).get("fallbackPolicy", "FAIL")
        if fallback == "SKIP":
            return ErrorPolicyResult(action="continue", output={})
        return ErrorPolicyResult(action="abort")

    # Unknown policy defaults to FAIL
    return ErrorPolicyResult(action="abort")
