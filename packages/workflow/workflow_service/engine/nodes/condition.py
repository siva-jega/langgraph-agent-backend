import logging
import re
from typing import Any

from workflow_service.engine.expression import resolve_expression

logger = logging.getLogger(__name__)

UNARY_OPS = {
    "is_null", "is_not_null",
    "is_empty", "is_not_empty",
    "is_true", "is_false",
}


def _coerce(v: Any) -> Any:
    if v is None:
        return None
    s = str(v)
    for t in (int, float):
        try:
            return t(s)
        except (ValueError, TypeError):
            pass
    return s


def _is_null(v: Any) -> bool:
    return v is None or str(v).strip().lower() in ("null", "none", "")


def evaluate_rule(rule: dict, context: dict) -> bool:
    left = resolve_expression(rule.get("left", ""), context)
    op = rule.get("operator", "equals")
    right = rule.get("right", "")

    if op == "is_null":      return _is_null(left)
    if op == "is_not_null":  return not _is_null(left)
    if op == "is_true":      return left is True or str(left).lower() == "true"
    if op == "is_false":     return left is False or str(left).lower() == "false"
    if op == "is_empty":     return left is None or str(left).strip() == ""
    if op == "is_not_empty": return left is not None and str(left).strip() != ""

    right = resolve_expression(right, context)

    if op == "equals":        return _coerce(left) == _coerce(right)
    if op == "not_equals":    return _coerce(left) != _coerce(right)
    if op == "contains":      return str(right).lower() in str(left).lower()
    if op == "not_contains":  return str(right).lower() not in str(left).lower()
    if op == "starts_with":   return str(left).lower().startswith(str(right).lower())
    if op == "ends_with":     return str(left).lower().endswith(str(right).lower())
    if op == "matches_regex":
        try:
            return bool(re.search(str(right), str(left)))
        except re.error:
            return False

    try:
        l_num = float(_coerce(left))
        r_num = float(_coerce(right))
        if op == "gt":  return l_num > r_num
        if op == "gte": return l_num >= r_num
        if op == "lt":  return l_num < r_num
        if op == "lte": return l_num <= r_num
    except (TypeError, ValueError):
        pass

    try:
        l_len = len(str(left))
        r_len = int(str(right))
        if op == "length_eq": return l_len == r_len
        if op == "length_gt": return l_len > r_len
        if op == "length_lt": return l_len < r_len
    except (TypeError, ValueError):
        pass

    logger.warning("Unknown or unevaluable condition operator: %s", op)
    return False


class ConditionStepExecutor:
    async def execute(self, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        conditions = config.get("conditions")
        if not conditions or not isinstance(conditions, dict):
            logger.warning("Condition node has no valid conditions config")
            return {"result": False}

        rules = conditions.get("rules", [])
        if not rules:
            return {"result": False}

        combinator = conditions.get("combinator", "AND").upper()
        results = []
        for rule in rules:
            try:
                results.append(evaluate_rule(rule, context))
            except Exception as e:
                logger.error("Error evaluating condition rule %s: %s", rule, e)
                results.append(False)

        result = all(results) if combinator == "AND" else any(results)
        return {"result": result}
