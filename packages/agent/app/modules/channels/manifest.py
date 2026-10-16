"""Render real-value preview manifests for stored assistant messages.

The UI preview must mirror what Meta actually renders on WhatsApp. For
library templates (e.g. ``shipment_confirmation_4``), Meta hosts the
header/body text; we keep a local definition so the same params used to
send the template are reused to build the preview.
"""

from __future__ import annotations

import json as _json
import re
from typing import Any, Callable, Optional, TypedDict


class _LibraryTemplateDef(TypedDict, total=False):
    header: str
    body: str
    footer: str
    buttons: list[dict[str, Any]]
    params: list[Callable[[dict[str, Any], Any], str]]


def _customer_first_name(inbound: Any) -> str:
    if inbound is None:
        return ""
    sender = getattr(inbound, "sender", None)
    display = ""
    if sender is not None:
        display = str(getattr(sender, "displayName", "") or "").strip()
    if not display:
        return ""
    return display.split()[0]


def _order_display_name(order: dict[str, Any]) -> str:
    name = str(order.get("name") or "").strip()
    if name:
        return name if name.startswith("#") else f"#{name.lstrip('#')}"
    oid = str(order.get("id") or "").strip()
    return f"#{oid}" if oid else ""


_LIBRARY_TEMPLATES: dict[str, _LibraryTemplateDef] = {
    "shipment_confirmation_4": {
        "header": "Your order is en route!",
        "body": (
            "Hi {1},\n\n"
            "We're happy to inform you that your order {2} has shipped! "
            "Click view order details to view the status of your shipment."
        ),
        "buttons": [{"type": "URL", "label": "View order details", "icon": "link"}],
        "params": [
            lambda order, inbound: _customer_first_name(inbound) or "there",
            lambda order, inbound: _order_display_name(order) or "your order",
        ],
    },
}


def _parse_manifest(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    if isinstance(raw, dict):
        return dict(raw)
    return None


def _parse_meta_payload(asset: dict[str, Any]) -> dict[str, Any]:
    raw = asset.get("metaPayload")
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _resolve_library_template_name(asset: dict[str, Any]) -> str:
    meta = _parse_meta_payload(asset)
    name = str(
        meta.get("library_template_name") or meta.get("name") or ""
    ).strip()
    return name


def build_library_template_body_params(
    *,
    template_name: str,
    order: dict[str, Any],
    inbound: Any,
) -> list[dict[str, Any]]:
    """Return the WhatsApp body-component params for a known library template.

    Falls back to an empty list if the template is not registered.
    """
    tpl = _LIBRARY_TEMPLATES.get(template_name)
    if not tpl:
        return []
    params = tpl.get("params") or []
    return [{"type": "text", "text": fn(order, inbound)} for fn in params]


def _fill_body(body: str, values: list[str]) -> str:
    rendered = body
    for idx, value in enumerate(values, start=1):
        rendered = rendered.replace(f"{{{idx}}}", value)
    return rendered


# Display symbols for common ISO currency codes; unmapped codes fall back to
# "<CODE> " so the value stays unambiguous (e.g. "AED 100").
_CURRENCY_SYMBOLS = {
    "INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "SGD": "S$", "HKD": "HK$",
    "BRL": "R$", "ZAR": "R",
}


def _product_price_display(product: dict[str, Any]) -> str:
    raw = ""
    for key in ("price_range", "price", "priceRange"):
        v = product.get(key)
        if v:
            raw = str(v).strip()
            break
    if not raw:
        return ""

    code = str(product.get("currencyCode") or "").strip().upper()

    # price_range arrives as "<CODE> <amount>" (e.g. "INR 2499"); derive the
    # code from it when not provided explicitly, then strip it off the amount.
    match = re.match(r"^([A-Za-z]{3})\s+(.*)$", raw)
    if match:
        code = code or match.group(1).upper()
        amount = match.group(2).strip()
    else:
        amount = raw.replace("₹", "").replace("Rs.", "").strip()

    if not code:
        # No currency info at all — return the amount as-is rather than guess.
        return amount
    symbol = _CURRENCY_SYMBOLS.get(code, f"{code} ")
    return f"{symbol}{amount}"


def _product_description(product: dict[str, Any]) -> str:
    for key in ("description", "short_description", "shortDescription"):
        v = product.get(key)
        if v:
            return str(v).strip()
    return ""


def _product_image_label(product: dict[str, Any]) -> str:
    name = str(product.get("name") or "").strip()
    return name or "Product Image"


def _product_image_url(product: dict[str, Any]) -> str:
    for key in ("image_url", "imageUrl", "image"):
        v = product.get(key)
        if v:
            url = str(v).strip()
            if url:
                return url
    images = product.get("images")
    if isinstance(images, list):
        for entry in images:
            if not entry:
                continue
            if isinstance(entry, str):
                url = entry.strip()
                if url:
                    return url
            elif isinstance(entry, dict):
                for key in ("url", "src", "image_url"):
                    v = entry.get(key)
                    if v:
                        url = str(v).strip()
                        if url:
                            return url
    return ""


def render_order_tracking_manifest(
    *,
    asset: dict[str, Any],
    order: dict[str, Any],
    inbound: Any = None,
) -> Optional[dict[str, Any]]:
    base = _parse_manifest(asset.get("previewManifest")) or {}
    template_name = _resolve_library_template_name(asset)
    tpl = _LIBRARY_TEMPLATES.get(template_name)

    if tpl:
        values = [fn(order, inbound) for fn in tpl.get("params", [])]
        header = tpl.get("header")
        body = _fill_body(tpl.get("body", ""), values)
        buttons = tpl.get("buttons") or base.get("buttons")
        footer = tpl.get("footer") or base.get("footer")
        return {
            "layout": "template",
            "headerType": "text" if header else (base.get("headerType") or "none"),
            "headerText": header,
            "body": body,
            "footer": footer,
            "buttons": buttons,
        }

    return {
        "layout": "template",
        "headerType": base.get("headerType") or "none",
        "body": str(base.get("body") or ""),
        "footer": base.get("footer"),
        "buttons": base.get("buttons"),
    }


def render_catalog_manifest(
    *,
    asset: dict[str, Any],
    products: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    base = _parse_manifest(asset.get("previewManifest")) or {}
    layout = str(base.get("layout") or "carousel")

    if layout == "carousel":
        base_cards = base.get("cards") if isinstance(base.get("cards"), list) else []
        cards = []
        for idx, product in enumerate(products[:10]):
            default = base_cards[idx] if idx < len(base_cards) and isinstance(base_cards[idx], dict) else {}
            card: dict[str, Any] = {
                "title": str(product.get("name") or default.get("title") or "").strip(),
                "price": _product_price_display(product) or str(default.get("price") or ""),
                "description": _product_description(product) or str(default.get("description") or ""),
                "imageLabel": _product_image_label(product),
                "buttons": default.get("buttons") or ["View Product", "Add to Cart"],
            }
            image_url = _product_image_url(product)
            if image_url:
                card["imageUrl"] = image_url
            cards.append(card)
        if not cards:
            return None
        return {
            "layout": "carousel",
            "introMessage": base.get("introMessage"),
            "cards": cards,
        }

    if layout == "template":
        first = products[0] if products else {}
        name = str(first.get("name") or "").strip()
        price = _product_price_display(first)
        description = _product_description(first)
        if name:
            body_parts = ["Check out this product!"]
            if name:
                body_parts.append(f"\n*{name}*")
            if description:
                body_parts.append(description)
            if price:
                body_parts.append(f"Price: {price}")
            body_parts.append("\nTap below to view details or add it to your cart.")
            body = "\n".join(body_parts)
        else:
            body = str(base.get("body") or "")
        header_type = base.get("headerType") or "image"
        manifest: dict[str, Any] = {
            "layout": "template",
            "headerType": header_type,
            "body": body,
            "footer": base.get("footer"),
            "buttons": base.get("buttons"),
        }
        if header_type == "image":
            image_url = _product_image_url(first)
            if image_url:
                manifest["headerImageUrl"] = image_url
            manifest["headerImageLabel"] = _product_image_label(first)
        return manifest

    return base or None
