"""Map skill form submissions to a flat data dict for the dynamic fields schema.

All values go into a single `data` JSONB column on Ticket/Customer/HandoffRequest.
The `mapsTo` on each field is now just the key name (e.g. "requesterName", "name", "email").
Legacy prefixed paths (ticket.*, customer.*, lead.*, custom.*) are normalized to bare keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Optional leading +, then 7-15 digits with only spaces/dashes/dots/parens as separators.
_PHONE_RE = re.compile(r"^\+?(?:[\s().-]*\d){7,15}[\s().-]*$")

_VALID_TICKET_PRIORITIES = frozenset({"LOW", "MEDIUM", "HIGH", "URGENT"})
_VALID_TICKET_STATUSES = frozenset({"OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"})
_VALID_CUSTOMER_STATUSES = frozenset(
    {"NEW", "CONTACTED", "QUALIFIED", "CONVERTED", "LOST"}
)


def normalize_ticket_priority(value: Optional[str], fallback: str = "MEDIUM") -> str:
    u = (value or fallback or "MEDIUM").strip().upper()
    return u if u in _VALID_TICKET_PRIORITIES else "MEDIUM"


def normalize_ticket_status(value: Optional[str], fallback: str = "OPEN") -> str:
    u = (value or fallback or "OPEN").strip().upper()
    return u if u in _VALID_TICKET_STATUSES else fallback


def normalize_customer_status(value: Optional[str], fallback: str = "NEW") -> str:
    u = (value or fallback or "NEW").strip().upper()
    return u if u in _VALID_CUSTOMER_STATUSES else fallback


def _field_id(f: Mapping[str, Any]) -> str:
    return str(f.get("id") or f.get("name") or "").strip()


def _is_media_field(f: Mapping[str, Any]) -> bool:
    return str(f.get("type") or "").strip().lower() == "media_upload"


def _is_empty(value: Any) -> bool:
    # Check lists/dicts by length, not str() — `str([])` is the truthy "[]".
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return not str(value).strip()


def validate_required_form_fields(
    form_fields: List[Mapping[str, Any]],
    submitted: Mapping[str, Any],
) -> Tuple[bool, str]:
    for f in form_fields:
        if not isinstance(f, dict):
            continue
        if not f.get("required"):
            continue
        fid = _field_id(f)
        if not fid:
            continue
        if _is_empty(submitted.get(fid)):
            label = str(f.get("label") or fid)
            return False, f"Missing required field: {label}"
    return True, ""


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def _is_valid_phone(value: str) -> bool:
    return bool(_PHONE_RE.match(value.strip()))


def validate_form_field_values(
    form_fields: List[Mapping[str, Any]],
    submitted: Mapping[str, Any],
) -> Tuple[bool, str]:
    """Validate format of typed fields (email/phone) that have a non-empty value."""
    for f in form_fields:
        if not isinstance(f, dict):
            continue
        fid = _field_id(f)
        if not fid:
            continue
        raw = submitted.get(fid)
        if _is_empty(raw):
            continue
        ftype = str(f.get("type") or "").strip().lower()
        label = str(f.get("label") or fid)
        if ftype == "email" and not _is_valid_email(str(raw)):
            return False, f"{label} is not a valid email address."
        if ftype == "phone" and not _is_valid_phone(str(raw)):
            return False, f"{label} is not a valid phone number."
    return True, ""


def _strip_prefix(maps_to: str) -> str:
    """Strip legacy entity prefixes like ticket.*, customer.*, lead.*, custom.* to get bare key."""
    for prefix in ("ticket.", "customer.", "lead.", "custom.", "handoff."):
        if maps_to.startswith(prefix):
            return maps_to[len(prefix):]
    return maps_to


@dataclass
class TicketFieldMapping:
    """All ticket fields as a flat dict ready for data JSON."""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CustomerFieldMapping:
    """All customer fields as a flat dict ready for data JSON."""
    data: Dict[str, Any] = field(default_factory=dict)


# Backward-compat alias
LeadFieldMapping = CustomerFieldMapping


def map_submitted_to_ticket(
    form_fields: List[Mapping[str, Any]],
    submitted: Mapping[str, Any],
) -> TicketFieldMapping:
    out = TicketFieldMapping()
    for f in form_fields:
        if not isinstance(f, dict):
            continue
        fid = _field_id(f)
        if not fid:
            continue
        raw = submitted.get(fid)
        if _is_empty(raw):
            continue
        maps_to = str(f.get("mapsTo") or "").strip()
        key = _strip_prefix(maps_to) if maps_to else fid
        if _is_media_field(f) and isinstance(raw, (list, dict)):
            out.data[key] = raw
            continue
        val = str(raw).strip()
        if key == "priority":
            val = normalize_ticket_priority(val)
        out.data[key] = val
    return out


def map_submitted_to_customer(
    form_fields: List[Mapping[str, Any]],
    submitted: Mapping[str, Any],
) -> CustomerFieldMapping:
    out = CustomerFieldMapping()
    for f in form_fields:
        if not isinstance(f, dict):
            continue
        fid = _field_id(f)
        if not fid:
            continue
        raw = submitted.get(fid)
        if _is_empty(raw):
            continue
        maps_to = str(f.get("mapsTo") or "").strip()
        key = _strip_prefix(maps_to) if maps_to else fid
        # Normalize legacy "leadEmail"/"customerEmail" to "email"
        if key in ("leadEmail", "customerEmail"):
            key = "email"
        if _is_media_field(f) and isinstance(raw, (list, dict)):
            out.data[key] = raw
        else:
            out.data[key] = str(raw).strip()
    return out


# Backward-compat alias
map_submitted_to_lead = map_submitted_to_customer
