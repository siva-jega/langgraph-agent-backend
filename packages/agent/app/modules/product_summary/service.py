import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_openai import ChatOpenAI
from app.core.config import app_settings
from app.modules.product_summary.repository import ProductSummaryRepository

logger = logging.getLogger(__name__)

_repo = ProductSummaryRepository()


def _build_product_text(product: Dict[str, Any], platform: str) -> str:
    parts = []

    if platform == "shopify":
        parts.append(f"Product: {product.get('title', 'Unknown')}")
        if product.get("description"):
            parts.append(f"Description: {product['description']}")
        if product.get("productType"):
            parts.append(f"Type: {product['productType']}")
        if product.get("minVariantPrice"):
            currency = product.get("currencyCode", "USD")
            min_p = product.get("minVariantPrice")
            max_p = product.get("maxVariantPrice")
            if min_p == max_p or not max_p:
                parts.append(f"Price: {min_p} {currency}")
            else:
                parts.append(f"Price: {min_p} - {max_p} {currency}")
        variants = product.get("variants", [])
        if variants:
            variant_names = [v.get("displayName") or v.get("title", "") for v in variants]
            parts.append(f"Variants: {', '.join(variant_names)}")
    else:
        # WooCommerce
        parts.append(f"Product: {product.get('name', 'Unknown')}")
        if product.get("description"):
            parts.append(f"Description: {product['description']}")
        if product.get("price"):
            parts.append(f"Price: {product['price']}")
        if product.get("stockStatus"):
            parts.append(f"Stock Status: {product['stockStatus']}")
        if product.get("averageRating"):
            parts.append(
                f"Rating: {product['averageRating']}/5 ({product.get('ratingCount', 0)} reviews)"
            )
        if product.get("totalSales"):
            parts.append(f"Total Sales: {product['totalSales']}")
        if product.get("categories"):
            cats = product["categories"]
            if isinstance(cats, str):
                try:
                    cats = json.loads(cats)
                except (json.JSONDecodeError, TypeError):
                    cats = []
            if isinstance(cats, list):
                cat_names = [c.get("name", "") for c in cats if isinstance(c, dict)]
                if cat_names:
                    parts.append(f"Categories: {', '.join(cat_names)}")
        variants = product.get("variants", [])
        if variants:
            variant_info = []
            for v in variants:
                name = v.get("name", "")
                price = v.get("price", "")
                variant_info.append(f"{name} ({price})" if price else name)
            if variant_info:
                parts.append(f"Variants: {', '.join(variant_info)}")

    return "\n".join(parts)


async def _generate_summary_and_questions(
    product_text: str,
) -> Tuple[str, List[str]]:
    model = ChatOpenAI(
        model=app_settings.DEFAULT_AGENT_MODEL,
        api_key=app_settings.OPENAI_API_KEY,
        base_url=app_settings.OPENAI_API_BASE,
    )

    prompt = f"""You are a helpful product expert. Given the following product details, provide:

1. A concise, engaging summary (2-3 sentences) that highlights the key features and benefits of the product. Make it helpful for a potential buyer.

2. Exactly 3 suggested questions that a buyer might want to ask about this product. Keep them short and relevant.

Return your response in this exact JSON format:
{{
  "summary": "your summary here",
  "suggestedQuestions": ["question 1", "question 2", "question 3"]
}}

Product Details:
{product_text}
"""

    response = await model.ainvoke(prompt)
    content = response.content.strip()

    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])

    try:
        parsed = json.loads(content)
        summary = parsed.get("summary", "")
        questions = parsed.get("suggestedQuestions", [])
        if not isinstance(questions, list):
            questions = []
        return summary, questions[:3]
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Failed to parse LLM response as JSON: %s", e)
        return content, []


async def get_product_summary(
    product_id: str,
    platform: str,
    workspace_id: str,
) -> Dict[str, Any]:
    if platform == "shopify":
        product = await _repo.get_shopify_product(product_id, workspace_id)
    elif platform == "woocommerce":
        product = await _repo.get_woocommerce_product(product_id, workspace_id)
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    if not product:
        return None

    existing_summary = product.get("aiSummary")
    if existing_summary:
        logger.info("returning AI summary for product %s", product_id)
        try:
            cached = json.loads(existing_summary)
            return {
                "productId": product_id,
                "platform": platform,
                "summary": cached.get("summary", existing_summary),
                "suggestedQuestions": cached.get("suggestedQuestions", []),
                "product": _sanitize_product(product, platform),
            }
        except (json.JSONDecodeError, TypeError):
            return {
                "productId": product_id,
                "platform": platform,
                "summary": existing_summary,
                "suggestedQuestions": [],
                "product": _sanitize_product(product, platform),
            }

    logger.info("generating AI summary for product %s (%s)", product_id, platform)
    product_text = _build_product_text(product, platform)
    summary, questions = await _generate_summary_and_questions(product_text)

    summary_data = json.dumps({"summary": summary, "suggestedQuestions": questions})
    if platform == "shopify":
        await _repo.update_shopify_summary(product_id, workspace_id, summary_data)
    else:
        await _repo.update_woocommerce_summary(product_id, workspace_id, summary_data)

    return {
        "productId": product_id,
        "platform": platform,
        "summary": summary,
        "suggestedQuestions": questions,
        "product": _sanitize_product(product, platform),
    }


def _sanitize_product(product: Dict[str, Any], platform: str) -> Dict[str, Any]:
    """Return a clean product dict for the API response (remove internal DB fields)."""
    clean = dict(product)
    clean.pop("aiSummary", None)
    return clean
