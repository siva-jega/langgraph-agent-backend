#!/usr/bin/env python3
"""
Exercise the same Shopify Admin GraphQL used by get_products (and optional by-id query).

Usage (from repo root):
  uv run python scripts/shopify_graphql_products_test.py \\
    --shop your-store.myshopify.com \\
    --token shpat_xxx \\
    --search "title:hoodie" \\
    --first 5

Or set env (e.g. in .env loaded via dotenv):
  SHOPIFY_SHOP=your-store.myshopify.com
  SHOPIFY_ACCESS_TOKEN=shpat_xxx

By product GIDs (same shape as _fetch_products_by_ids):
  uv run python scripts/shopify_graphql_products_test.py --shop ... --token ... \\
    --by-ids gid://shopify/Product/123 gid://shopify/Product/456
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Kept in sync with app/tools/providers/shopify/tools.py (_PRODUCT_FIELDS + queries).
_PRODUCT_FIELDS = """
    id
    title
    description
    onlineStoreUrl
    onlineStorePreviewUrl
    totalInventory
    priceRangeV2 {
        minVariantPrice {
            amount
            currencyCode
        }
        maxVariantPrice {
            amount
            currencyCode
        }
    }
    variants(first: 250) {
        edges {
            node {
                id
                title
                displayName
                price
                compareAtPrice
                availableForSale
                inventoryQuantity
                sellableOnlineQuantity
            }
        }
    }
    media(first: 6, sortKey: POSITION) {
        nodes {
            ... on MediaImage {
                id
                alt
                image {
                    url
                }
            }
        }
    }
"""

QUERY_GET_PRODUCTS = f"""
query GetProducts($first: Int!, $query: String) {{
    products(first: $first, query: $query) {{
        edges {{
            node {{
                {_PRODUCT_FIELDS}
            }}
        }}
    }}
}}
"""

QUERY_GET_PRODUCTS_BY_IDS = f"""
query GetProductsByIds($ids: [ID!]!) {{
    nodes(ids: $ids) {{
        ... on Product {{
            {_PRODUCT_FIELDS}
        }}
    }}
}}
"""


def _normalize_shop(shop: str) -> str:
    s = shop.strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    return s.split("/")[0].strip()


def _post_graphql(
    shop_domain: str,
    access_token: str,
    api_version: str,
    query: str,
    variables: dict[str, Any],
) -> dict[str, Any]:
    api_url = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }
    payload = {"query": query, "variables": variables}
    with httpx.Client(timeout=30.0) as client:
        r = client.post(api_url, json=payload, headers=headers)
        if r.status_code == 401:
            print("HTTP 401: invalid access token or shop.", file=sys.stderr)
            sys.exit(1)
        r.raise_for_status()
        return r.json()


def _print_summary(data: dict[str, Any], mode: str) -> None:
    if "errors" in data:
        print("GraphQL errors:", file=sys.stderr)
        print(json.dumps(data["errors"], indent=2), file=sys.stderr)
        sys.exit(1)

    d = data.get("data") or {}
    if mode == "search":
        edges = d.get("products", {}).get("edges", [])
        print(f"products returned: {len(edges)}")
        for i, edge in enumerate(edges, 1):
            node = edge.get("node") or {}
            pid = node.get("id", "")
            title = node.get("title", "")
            print(f"  {i}. {title}\n      {pid}")
    else:
        nodes = d.get("nodes") or []
        print(f"nodes returned: {len([n for n in nodes if n])}")
        for i, node in enumerate(nodes, 1):
            if not node:
                print(f"  {i}. <null node>")
                continue
            print(f"  {i}. {node.get('title', '')}\n      {node.get('id', '')}")


def main() -> None:
    p = argparse.ArgumentParser(description="Test Shopify product GraphQL (same as ai-backend tools).")
    p.add_argument(
        "--shop",
        default=os.getenv("SHOPIFY_SHOP") or os.getenv("SHOPIFY_SHOP_DOMAIN"),
        help="Shop domain, e.g. my-store.myshopify.com (env: SHOPIFY_SHOP)",
    )
    p.add_argument(
        "--token",
        default=os.getenv("SHOPIFY_ACCESS_TOKEN"),
        help="Admin API access token (env: SHOPIFY_ACCESS_TOKEN)",
    )
    p.add_argument(
        "--api-version",
        default=os.getenv("SHOPIFY_API_VERSION", "2025-04"),
        help="Admin API version (default 2025-04, matches tools.py)",
    )
    p.add_argument("--search", "-q", default="", help='Shopify products() search string, e.g. "snowboard"')
    p.add_argument("--first", type=int, default=5, help="Max products (default 5)")
    p.add_argument(
        "--by-ids",
        nargs="+",
        metavar="GID",
        help="If set, run GetProductsByIds (nodes) instead of product search",
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help="Print full JSON response instead of a short summary",
    )
    args = p.parse_args()

    if not args.shop or not args.token:
        p.error("Provide --shop and --token (or SHOPIFY_SHOP + SHOPIFY_ACCESS_TOKEN in environment).")

    shop = _normalize_shop(args.shop)

    if args.by_ids:
        variables: dict[str, Any] = {"ids": list(args.by_ids)}
        query = QUERY_GET_PRODUCTS_BY_IDS
        mode = "ids"
    else:
        # Match tools.py: omit `query` when unset so Shopify treats it as null (all products, capped by first).
        variables = {"first": args.first}
        if args.search:
            variables["query"] = args.search
        query = QUERY_GET_PRODUCTS
        mode = "search"

    data = _post_graphql(shop, args.token.strip(), args.api_version.strip(), query, variables)

    if args.raw:
        print(json.dumps(data, indent=2))
    else:
        _print_summary(data, mode)

    print("\nOK — GraphQL request completed.", file=sys.stderr)


if __name__ == "__main__":
    main()
