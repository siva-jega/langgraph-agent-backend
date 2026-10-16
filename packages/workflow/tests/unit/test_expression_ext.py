"""Extended expression resolution tests (complements test_expression.py)."""

from __future__ import annotations

from workflow_service.engine.expression import resolve_dict, resolve_expression


# ── Typed value preservation ──────────────────────────────────────────────────

def test_resolve_returns_dict_typed() -> None:
    ctx = {"n": {"output": {"data": {"a": 1}}}}
    result = resolve_expression("{{ $n.output.data }}", ctx)
    assert result == {"a": 1}
    assert isinstance(result, dict)


def test_resolve_returns_list_typed() -> None:
    ctx = {"n": {"output": {"items": [1, 2, 3]}}}
    result = resolve_expression("{{ $n.output.items }}", ctx)
    assert result == [1, 2, 3]
    assert isinstance(result, list)


def test_resolve_returns_bool_typed() -> None:
    ctx = {"n": {"output": {"flag": True}}}
    result = resolve_expression("{{ $n.output.flag }}", ctx)
    assert result is True
    assert isinstance(result, bool)


def test_resolve_returns_zero_not_coerced_to_falsy_string() -> None:
    ctx = {"n": {"output": {"count": 0}}}
    result = resolve_expression("{{ $n.output.count }}", ctx)
    assert result == 0
    assert isinstance(result, int)


# ── List-of-dicts recursion ───────────────────────────────────────────────────

def test_resolve_dict_list_of_dicts() -> None:
    ctx = {"n": {"output": {"tag": "vip"}}}
    data = {"items": [{"label": "{{ $n.output.tag }}", "value": 1}, {"label": "static", "value": 2}]}
    result = resolve_dict(data, ctx)
    assert result["items"][0]["label"] == "vip"
    assert result["items"][1]["label"] == "static"


# ── Node IDs with hyphens and underscores ─────────────────────────────────────

def test_resolve_node_id_with_hyphens() -> None:
    ctx = {"http-request-1": {"output": {"status": 200}}}
    assert resolve_expression("{{ $http-request-1.output.status }}", ctx) == 200


def test_resolve_node_id_with_underscores() -> None:
    ctx = {"send_msg_1": {"output": {"id": "abc"}}}
    assert resolve_expression("{{ $send_msg_1.output.id }}", ctx) == "abc"


# ── Out-of-bounds list index → None ──────────────────────────────────────────

def test_resolve_out_of_bounds_list_index_returns_none() -> None:
    ctx = {"n": {"output": {"items": ["only"]}}}
    assert resolve_expression("{{ $n.output.items.5 }}", ctx) is None


def test_resolve_non_integer_list_index_returns_none() -> None:
    ctx = {"n": {"output": {"items": ["a", "b"]}}}
    assert resolve_expression("{{ $n.output.items.bad }}", ctx) is None


# ── Whitespace tolerance ──────────────────────────────────────────────────────

def test_resolve_leading_trailing_whitespace_in_expression() -> None:
    ctx = {"n": {"output": {"x": 42}}}
    assert resolve_expression("{{  $n.output.x  }}", ctx) == 42


# ── Adjacent expressions in one string ───────────────────────────────────────

def test_resolve_adjacent_expressions_with_separator() -> None:
    ctx = {
        "a": {"output": {"first": "Hello"}},
        "b": {"output": {"second": "World"}},
    }
    result = resolve_expression("{{ $a.output.first }} {{ $b.output.second }}", ctx)
    assert result == "Hello World"


def test_resolve_adjacent_expressions_no_separator() -> None:
    ctx = {
        "a": {"output": {"prefix": "foo"}},
        "b": {"output": {"suffix": "bar"}},
    }
    result = resolve_expression("{{ $a.output.prefix }}{{ $b.output.suffix }}", ctx)
    assert result == "foobar"


# ── Old $trigger / $steps. syntax → None (embedded → empty string) ───────────

def test_old_trigger_syntax_resolves_to_none_standalone() -> None:
    ctx = {"webhook-1": {"output": {"name": "Ada"}}}
    assert resolve_expression("{{ $trigger.inputData.name }}", ctx) is None


def test_old_trigger_syntax_embedded_resolves_to_empty_string() -> None:
    ctx = {"webhook-1": {"output": {"name": "Ada"}}}
    result = resolve_expression("Hello {{ $trigger.inputData.name }}!", ctx)
    assert result == "Hello !"


def test_old_steps_syntax_resolves_to_none_standalone() -> None:
    ctx = {"http-1": {"output": {"body": "ok"}}}
    assert resolve_expression("{{ $steps.http-1.output.body }}", ctx) is None


def test_old_steps_syntax_embedded_resolves_to_empty_string() -> None:
    ctx = {"http-1": {"output": {"body": "ok"}}}
    result = resolve_expression("Result: {{ $steps.http-1.output.body }}", ctx)
    assert result == "Result: "
