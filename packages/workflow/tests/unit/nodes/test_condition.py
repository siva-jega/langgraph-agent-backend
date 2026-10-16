"""Unit tests for ConditionStepExecutor and evaluate_rule."""

from __future__ import annotations

import pytest

from workflow_service.engine.nodes.condition import (
    ConditionStepExecutor,
    evaluate_rule,
)


# ── evaluate_rule operator tests ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "left, op, right, expected",
    [
        # equals / not_equals
        ("hello", "equals", "hello", True),
        ("hello", "equals", "world", False),
        ("10", "equals", "10", True),
        ("hello", "not_equals", "world", True),
        # numeric comparisons
        ("5", "gt", "3", True),
        ("3", "gt", "5", False),
        ("5", "gte", "5", True),
        ("4", "gte", "5", False),
        ("3", "lt", "5", True),
        ("5", "lte", "5", True),
        # string operations (case-insensitive)
        ("Hello World", "contains", "hello", True),
        ("Hello World", "contains", "xyz", False),
        ("Hello World", "not_contains", "xyz", True),
        ("Hello World", "starts_with", "hello", True),
        ("Hello World", "ends_with", "World", True),
        ("Hello World", "ends_with", "xyz", False),
        # null / empty checks
        (None, "is_null", None, True),
        ("", "is_null", None, True),
        ("x", "is_null", None, False),
        ("x", "is_not_null", None, True),
        (None, "is_not_null", None, False),
        ("", "is_empty", None, True),
        ("x", "is_empty", None, False),
        ("x", "is_not_empty", None, True),
        # boolean checks
        (True, "is_true", None, True),
        ("true", "is_true", None, True),
        (False, "is_true", None, False),
        (False, "is_false", None, True),
        ("false", "is_false", None, True),
        # regex
        ("hello123", "matches_regex", r"\d+", True),
        ("hello", "matches_regex", r"\d+", False),
        # length ops
        ("hello", "length_eq", "5", True),
        ("hello", "length_eq", "3", False),
        ("hello", "length_gt", "3", True),
        ("hi", "length_lt", "5", True),
    ],
)
def test_evaluate_rule_operator(left, op, right, expected) -> None:
    rule = {"left": left, "operator": op, "right": right}
    assert evaluate_rule(rule, {}) is expected


def test_evaluate_rule_invalid_regex_returns_false() -> None:
    rule = {"left": "hello", "operator": "matches_regex", "right": "[invalid(regex"}
    assert evaluate_rule(rule, {}) is False


def test_evaluate_rule_exception_returns_false() -> None:
    rule = {"left": "{{ $missing.output }}", "operator": "equals", "right": "x"}
    assert evaluate_rule(rule, {}) is False


# ── ConditionStepExecutor tests ───────────────────────────────────────────────


def _make_condition(rules: list, combinator: str = "AND") -> dict:
    return {"conditions": {"rules": rules, "combinator": combinator}}


def _rule(left, op="equals", right=""):
    return {"left": left, "operator": op, "right": right}


async def test_and_combinator_all_true() -> None:
    config = _make_condition([_rule("a", "equals", "a"), _rule("b", "equals", "b")])
    result = await ConditionStepExecutor().execute(config, {})
    assert result == {"result": True}


async def test_and_combinator_one_false() -> None:
    config = _make_condition([_rule("a", "equals", "a"), _rule("x", "equals", "y")])
    result = await ConditionStepExecutor().execute(config, {})
    assert result == {"result": False}


async def test_or_combinator_any_true() -> None:
    config = _make_condition(
        [_rule("x", "equals", "y"), _rule("a", "equals", "a")],
        combinator="OR",
    )
    result = await ConditionStepExecutor().execute(config, {})
    assert result == {"result": True}


async def test_or_combinator_all_false() -> None:
    config = _make_condition(
        [_rule("x", "equals", "y"), _rule("1", "equals", "2")],
        combinator="OR",
    )
    result = await ConditionStepExecutor().execute(config, {})
    assert result == {"result": False}


async def test_no_conditions_config_returns_false() -> None:
    result = await ConditionStepExecutor().execute({}, {})
    assert result == {"result": False}


async def test_empty_rules_list_returns_false() -> None:
    config = {"conditions": {"rules": [], "combinator": "AND"}}
    result = await ConditionStepExecutor().execute(config, {})
    assert result == {"result": False}


async def test_type_coercion_numeric_string() -> None:
    config = _make_condition([_rule("10", "gt", "9")])
    result = await ConditionStepExecutor().execute(config, {})
    assert result["result"] is True


async def test_string_comparison_case_insensitive() -> None:
    config = _make_condition([_rule("HELLO", "contains", "hello")])
    result = await ConditionStepExecutor().execute(config, {})
    assert result["result"] is True


async def test_rule_exception_counts_as_false() -> None:
    # Rule that raises during evaluate_rule; combinator AND → overall False
    bad_rule = {"left": None, "operator": "gt", "right": None}
    config = _make_condition([bad_rule, _rule("a", "equals", "a")])
    result = await ConditionStepExecutor().execute(config, {})
    assert result["result"] is False


async def test_expression_resolved_in_left() -> None:
    ctx = {"trigger": {"output": {"status": "active"}}}
    config = _make_condition([{"left": "{{ $trigger.output.status }}", "operator": "equals", "right": "active"}])
    result = await ConditionStepExecutor().execute(config, ctx)
    assert result["result"] is True
