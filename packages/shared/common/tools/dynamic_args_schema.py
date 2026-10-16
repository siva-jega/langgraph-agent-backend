"""Build a Pydantic args_schema from a workspace skill's configured `form.fields`
merged with a tool's fixed base fields.

Used by the channel-runtime variants of `create_ticket_aide`, `create_lead_aide`,
and `human_handoff_trigger` so workspace-configured fields appear as **typed tool
args** instead of being silently dropped. With the fields in the args_schema,
LangChain's tool-calling layer enforces required-ness — the LLM cannot invoke
the tool without supplying every required value, so it must ask the user in chat
first. This is the channel-runtime equivalent of the web interrupt form.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional, Type

from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)

# Form-field `type` tokens we recognize. Anything unrecognized falls through to `str`.
_TEXT_TYPES = {
    "text", "string", "email", "phone", "phonenumber", "tel", "url", "textarea",
}
_INT_TYPES = {"integer", "int", "number"}
_FLOAT_TYPES = {"decimal", "float", "double"}
_BOOL_TYPES = {"boolean", "bool", "checkbox", "toggle"}
_SELECT_TYPES = {"select", "radio", "dropdown"}


def _option_value(option: Any) -> str:
    """Extract a string value from a form-field option entry.

    Workspaces store options as either raw strings or `{value, label}` dicts.
    """
    if isinstance(option, dict):
        return str(option.get("value") if option.get("value") is not None else option.get("label") or "")
    return str(option)


def _python_type_for_form_field(field: dict) -> Any:
    """Map a configured form-field `type` to a Python type for the args_schema."""
    raw_type = str(field.get("type") or "text").strip().lower()
    options = field.get("options")

    if raw_type in _SELECT_TYPES and isinstance(options, list) and options:
        values = tuple(_option_value(o) for o in options if _option_value(o))
        if values:
            # Literal[("a", "b", "c")] is valid runtime syntax.
            return Literal[values]  # type: ignore[valid-type]
    if raw_type in _INT_TYPES:
        return int
    if raw_type in _FLOAT_TYPES:
        return float
    if raw_type in _BOOL_TYPES:
        return bool
    # Default: any unrecognised type (text / email / phone / unknown) is str.
    return str


def _normalize_field_id(fid: str) -> str:
    """Lowercase + strip non-alphanumeric chars for collision detection across
    snake_case / camelCase / kebab-case variants of the same logical field."""
    return "".join(c for c in fid.lower() if c.isalnum())


def build_args_schema(
    *,
    name: str,
    base_fields: dict[str, tuple[Any, Any]],
    configured_form_fields: list[dict] | None,
) -> Type[BaseModel]:
    """Build a Pydantic model that merges static `base_fields` with workspace-
    configured `form.fields`.

    Collision handling: a configured field is dropped (with a log) when its
    normalized id matches either a base field or an already-added configured
    field. Normalization is case- and separator-insensitive, so `requesterName`
    correctly collides with `requester_name`.

    Args:
        name: Pydantic model class name (also used by LangChain when serialising
            the args_schema for the LLM).
        base_fields: ``{field_name: (python_type, Field(...))}`` — the small set
            of args every invocation needs (e.g. ``summary``). Workspace fields
            cannot shadow these.
        configured_form_fields: The raw `form.fields` list from the skill row.

    Returns:
        A ``BaseModel`` subclass suitable to pass as ``args_schema`` to LangChain's
        ``@tool`` decorator or ``StructuredTool``.
    """
    merged: dict[str, tuple[Any, Any]] = dict(base_fields)
    # Normalized keys already taken — seeded with the base fields. Configured
    # fields whose normalized id is already here are dropped.
    seen_normalized: set[str] = {_normalize_field_id(k) for k in merged.keys()}

    for f in configured_form_fields or []:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or f.get("name") or "").strip()
        if not fid:
            continue
        fid_norm = _normalize_field_id(fid)
        if not fid_norm:
            logger.warning(
                "Dropping configured form field with empty normalized id "
                "(raw id=%r, label=%r)", fid, f.get("label"),
            )
            continue
        if fid_norm in seen_normalized:
            logger.info(
                "Dropping configured form field %r on tool %r — collides "
                "(case-/separator-insensitive) with an existing field",
                fid, name,
            )
            continue
        py_type = _python_type_for_form_field(f)
        description = str(f.get("label") or fid).strip() or fid
        if f.get("required"):
            merged[fid] = (py_type, Field(..., description=description))
        else:
            merged[fid] = (Optional[py_type], Field(default=None, description=description))
        seen_normalized.add(fid_norm)

    return create_model(name, **merged)  # type: ignore[arg-type, call-overload]


def split_kwargs_by_base(
    kwargs: dict[str, Any], base_keys: set[str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Split tool-invocation kwargs into ``(base_args, configured_submitted)``.

    Configured-field values are coerced to stripped strings and empty entries
    are dropped, matching the shape `validate_required_form_fields` /
    `map_submitted_to_ticket` expect.
    """
    base_args: dict[str, Any] = {k: kwargs.get(k) for k in base_keys}
    submitted: dict[str, str] = {}
    for k, v in kwargs.items():
        if k in base_keys or v is None:
            continue
        s = str(v).strip()
        if s:
            submitted[k] = s
    return base_args, submitted
