"""Strip CRM keys marked aiPopulated from interrupt form fields (model supplies them via tools)."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig


def crm_ai_keys_for_entity(config: RunnableConfig, entity: str) -> set[str]:
    """Storage keys the model fills for this CrmEntity (from agent config / SQL)."""
    if not entity:
        return set()
    c = config.get("configurable", {}) or {}
    hints = c.get("crmAiPopulatedFields") or []
    if not isinstance(hints, list):
        return set()
    want = str(entity).upper()
    out: set[str] = set()
    for h in hints:
        if not isinstance(h, dict):
            continue
        if str(h.get("entity") or "").upper() != want:
            continue
        key = h.get("key")
        if key:
            out.add(str(key))
    return out


def strip_ai_populated_form_fields(fields: list, ai_keys: set[str]) -> list:
    if not ai_keys:
        return fields
    out: list = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or f.get("name") or "").strip()
        maps_to = str(f.get("mapsTo") or "").strip()
        if fid in ai_keys or maps_to in ai_keys:
            continue
        out.append(f)
    return out
