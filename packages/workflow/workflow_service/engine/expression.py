import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Matches {{ $<nodeId>.<path> }} — e.g. {{ $webhook-1.output.body.email }}
# Group 1: nodeId (word chars and hyphens)
# Group 2: dotted path (e.g. .output.body.email), may be empty
_EXPR_PATTERN = re.compile(r"\{\{\s*\$([\w-]+)((?:\.[\w\-]+)*)\s*\}\}")


def _resolve_path(root: dict, path_parts: list[str]) -> Any:
    """Traverse a nested dict/list by path parts."""
    current = root
    for part in path_parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def resolve_expression(template: Any, context: dict[str, Any]) -> Any:
    """Resolve {{ $nodeId.output.field }} expressions.

    If the entire value is a single expression, returns the raw typed value.
    If expressions are embedded in a string, substitutes as strings.
    Non-string values are returned as-is.
    """
    if not isinstance(template, str):
        return template

    stripped = template.strip()
    single_match = re.fullmatch(r"\{\{\s*\$([\w-]+)((?:\.[\w\-]+)*)\s*\}\}", stripped)
    if single_match:
        return _resolve_single(single_match.group(1), single_match.group(2), context)

    def replacer(match: re.Match) -> str:
        val = _resolve_single(match.group(1), match.group(2), context)
        return str(val) if val is not None else ""

    return _EXPR_PATTERN.sub(replacer, template)


def _resolve_single(node_id: str, path_str: str, context: dict[str, Any]) -> Any:
    """Look up context[nodeId] then traverse the dotted path."""
    node_data = context.get(node_id)
    if node_data is None:
        return None
    if not path_str:
        return node_data
    path_parts = path_str.lstrip(".").split(".")
    return _resolve_path(node_data, path_parts)


def resolve_dict(data: dict, context: dict[str, Any]) -> dict:
    """Recursively resolve all expressions in a dict."""
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = resolve_expression(value, context)
        elif isinstance(value, dict):
            result[key] = resolve_dict(value, context)
        elif isinstance(value, list):
            result[key] = [
                resolve_expression(v, context) if isinstance(v, str)
                else resolve_dict(v, context) if isinstance(v, dict)
                else v
                for v in value
            ]
        else:
            result[key] = value
    return result
