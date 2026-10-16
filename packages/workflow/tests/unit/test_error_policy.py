"""Unit tests for error policy evaluation — no I/O required."""

from __future__ import annotations

import pytest

from workflow_service.engine.error_policy import evaluate_error_policy


@pytest.mark.parametrize("retry_count", [0, 1, 5])
def test_fail_policy_always_aborts(retry_count: int) -> None:
    result = evaluate_error_policy("FAIL", retry_count)
    assert result.action == "abort"


def test_skip_policy_continues_with_empty_output() -> None:
    result = evaluate_error_policy("SKIP", 0)
    assert result.action == "continue"
    assert result.output == {}


def test_unknown_policy_aborts() -> None:
    result = evaluate_error_policy("BADPOLICY", 0)
    assert result.action == "abort"


class TestRetryPolicy:
    def test_retry_within_limit_returns_retry(self) -> None:
        result = evaluate_error_policy("RETRY", 0)
        assert result.action == "retry"

    @pytest.mark.parametrize(
        "retry_count, expected_delay",
        [
            (0, 1),   # 2^0 = 1
            (1, 2),   # 2^1 = 2
            (2, 4),   # 2^2 = 4
            (3, 8),   # 2^3 = 8
            (6, 60),  # 2^6 = 64 → capped at 60
            (10, 60), # way over cap
        ],
    )
    def test_backoff_formula(self, retry_count: int, expected_delay: float) -> None:
        # Use high maxRetries so we're always within the retry window
        config = {"maxRetries": 100}
        result = evaluate_error_policy("RETRY", retry_count, config)
        assert result.action == "retry"
        assert result.delay_seconds == expected_delay

    def test_custom_max_retries_respected(self) -> None:
        config = {"maxRetries": 1}
        assert evaluate_error_policy("RETRY", 0, config).action == "retry"
        assert evaluate_error_policy("RETRY", 1, config).action == "abort"

    def test_exhausted_default_fallback_is_fail(self) -> None:
        config = {"maxRetries": 2}
        result = evaluate_error_policy("RETRY", 2, config)
        assert result.action == "abort"

    def test_exhausted_fallback_skip(self) -> None:
        config = {"maxRetries": 2, "fallbackPolicy": "SKIP"}
        result = evaluate_error_policy("RETRY", 2, config)
        assert result.action == "continue"
        assert result.output == {}

    def test_exhausted_fallback_fail(self) -> None:
        config = {"maxRetries": 2, "fallbackPolicy": "FAIL"}
        result = evaluate_error_policy("RETRY", 2, config)
        assert result.action == "abort"

    def test_no_config_uses_default_max_retries_of_3(self) -> None:
        # retry_count 0,1,2 → retry; 3 → exhausted
        for count in range(3):
            assert evaluate_error_policy("RETRY", count).action == "retry"
        assert evaluate_error_policy("RETRY", 3).action == "abort"
