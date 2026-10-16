"""Unit tests for workflow expression resolution (no DB / I/O)."""

from __future__ import annotations

from workflow_service.engine.expression import resolve_dict, resolve_expression


# ── Single-expression (returns typed value) ───────────────────────────────────

def test_resolve_node_output_field() -> None:
    ctx = {"http-1": {"output": {"body": {"email": "a@example.com"}}}}
    assert resolve_expression("{{ $http-1.output.body.email }}", ctx) == "a@example.com"


def test_resolve_trigger_node_output() -> None:
    ctx = {"webhook-1": {"output": {"body": {"orderId": "123"}, "headers": {}}}}
    assert resolve_expression("{{ $webhook-1.output.body.orderId }}", ctx) == "123"


def test_resolve_returns_typed_value_not_string() -> None:
    ctx = {"step-a": {"output": {"count": 42}}}
    result = resolve_expression("{{ $step-a.output.count }}", ctx)
    assert result == 42
    assert isinstance(result, int)


def test_resolve_returns_none_for_missing_node() -> None:
    ctx: dict = {}
    assert resolve_expression("{{ $missing-node.output.x }}", ctx) is None


def test_resolve_returns_none_for_missing_path() -> None:
    ctx = {"http-1": {"output": {}}}
    assert resolve_expression("{{ $http-1.output.nonexistent }}", ctx) is None


# ── Embedded substitution (string interpolation) ─────────────────────────────

def test_embedded_expression_in_string() -> None:
    ctx = {"s1": {"output": {"x": "world"}}}
    assert resolve_expression("Hello {{ $s1.output.x }}!", ctx) == "Hello world!"


def test_multiple_embedded_expressions() -> None:
    ctx = {
        "trigger-1": {"output": {"name": "Alice"}},
        "http-1": {"output": {"status": 200}},
    }
    result = resolve_expression("User {{ $trigger-1.output.name }} got {{ $http-1.output.status }}", ctx)
    assert result == "User Alice got 200"


def test_embedded_missing_resolves_to_empty_string() -> None:
    ctx: dict = {}
    assert resolve_expression("prefix-{{ $gone.output.x }}-suffix", ctx) == "prefix--suffix"


# ── Non-string values pass through unchanged ─────────────────────────────────

def test_non_string_value_passthrough() -> None:
    ctx: dict = {}
    assert resolve_expression(123, ctx) == 123  # type: ignore[arg-type]
    assert resolve_expression(None, ctx) is None  # type: ignore[arg-type]
    assert resolve_expression({"a": 1}, ctx) == {"a": 1}  # type: ignore[arg-type]


# ── resolve_dict ──────────────────────────────────────────────────────────────

def test_resolve_dict_nested() -> None:
    ctx = {
        "step-a": {"output": {"n": 42}},
        "webhook-1": {"output": {"q": "find"}},
    }
    data = {
        "query": "{{ $webhook-1.output.q }}",
        "nested": {"n": "{{ $step-a.output.n }}"},
    }
    assert resolve_dict(data, ctx) == {"query": "find", "nested": {"n": 42}}


def test_resolve_dict_list_values() -> None:
    ctx = {"trigger-1": {"output": {"tag": "vip"}}}
    data = {"tags": ["{{ $trigger-1.output.tag }}", "other"]}
    assert resolve_dict(data, ctx) == {"tags": ["vip", "other"]}


def test_resolve_dict_unresolved_keys_preserved() -> None:
    ctx: dict = {}
    data = {"static": "hello", "dynamic": "{{ $gone.output.x }}"}
    result = resolve_dict(data, ctx)
    assert result["static"] == "hello"
    assert result["dynamic"] is None


# ── Path traversal edge cases ─────────────────────────────────────────────────

def test_resolve_nested_dict_path() -> None:
    ctx = {"n1": {"output": {"a": {"b": {"c": "deep"}}}}}
    assert resolve_expression("{{ $n1.output.a.b.c }}", ctx) == "deep"


def test_resolve_list_index() -> None:
    ctx = {"n1": {"output": {"items": ["first", "second"]}}}
    assert resolve_expression("{{ $n1.output.items.0 }}", ctx) == "first"


def test_resolve_node_root_no_path() -> None:
    ctx = {"n1": {"output": {"x": 1}}}
    result = resolve_expression("{{ $n1 }}", ctx)
    assert result == {"output": {"x": 1}}


# ── Old syntax no longer resolves ────────────────────────────────────────────

def test_old_steps_prefix_does_not_resolve() -> None:
    """$steps.http-1 is treated as nodeId='steps', not a prefix — resolves to None."""
    ctx = {"http-1": {"output": {"body": "ok"}}}
    assert resolve_expression("{{ $steps.http-1.output.body }}", ctx) is None


def test_old_trigger_prefix_does_not_resolve() -> None:
    """$trigger is treated as nodeId='trigger'. Context has no 'trigger' key."""
    ctx = {"webhook-1": {"output": {"name": "Ada"}}}
    assert resolve_expression("{{ $trigger.inputData.name }}", ctx) is None
