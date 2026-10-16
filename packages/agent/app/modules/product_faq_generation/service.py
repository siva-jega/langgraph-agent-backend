"""Generate product FAQ proposals from catalog data (LLM, structured output)."""

import logging
from typing import Any, Dict, List, Type

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, create_model

from app.core.config import app_settings
from app.modules.product_summary.repository import ProductSummaryRepository
from app.modules.product_summary.service import _build_product_text

logger = logging.getLogger(__name__)

_repo = ProductSummaryRepository()


class _FaqPair(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=8000)


def _proposals_output_model(proposal_count: int) -> Type[BaseModel]:
    """Pydantic model with exactly ``proposal_count`` FAQ pairs (for LangChain structured output)."""
    n = max(1, min(int(proposal_count), 3))
    return create_model(
        "ProductFaqProposalsStructured",
        __base__=BaseModel,
        faqs=(
            list[_FaqPair],
            Field(
                ...,
                min_length=n,
                max_length=n,
                description=f"Exactly {n} FAQ question/answer pairs for this product.",
            ),
        ),
    )


async def generate_product_faq_proposals(
    product_id: str,
    workspace_id: str,
    platform: str,
    proposal_count: int = 3,
) -> Dict[str, Any]:
    platform = platform.strip().lower()
    if platform == "shopify":
        product = await _repo.get_shopify_product(product_id, workspace_id)
    elif platform == "woocommerce":
        product = await _repo.get_woocommerce_product(product_id, workspace_id)
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    if not product:
        return {"ok": False, "error": "product_not_found", "faqs": []}

    n = max(1, min(int(proposal_count), 3))
    product_text = _build_product_text(product, platform)
    title = (
        product.get("title")
        if platform == "shopify"
        else product.get("name", "Product")
    )

    model = ChatOpenAI(
        model=app_settings.DEFAULT_AGENT_MODEL,
        api_key=app_settings.OPENAI_API_KEY,
        base_url=app_settings.OPENAI_API_BASE,
    )
    OutModel = _proposals_output_model(n)
    structured = model.with_structured_output(OutModel)

    prompt = f"""You write short on-page FAQs for an ecommerce product detail page (PDP).

You will receive **only** the product title and a block of product details. Use **nothing** outside that block—no invented specs, reviews, shipping dates, or policies. If something is not in the text, the answer should say briefly that the listing does not specify it and the shopper can check the store or product page.

Write **exactly {n}** FAQ pair(s) (no more, no fewer).

**Question style:** Each question should feel like a light **nudge** for someone browsing this product—intriguing and conversational, but still natural for an FAQ. Aim for curiosity, fit ("is this for me?"), or a small "worth knowing" hook **only when** the details support it. Avoid clickbait, false urgency, or questions you cannot answer from the text.

**Answers:** 2–3 sentences. Warm, clear, and strictly grounded in the product details below.

Product title: {title}

Product details:
{product_text}
"""

    try:
        result = await structured.ainvoke([HumanMessage(content=prompt)])
        if result is None:
            return {"ok": False, "error": "empty_model_output", "faqs": []}
        faqs: List[Dict[str, str]] = []
        for item in result.faqs:
            q = str(item.question).strip()
            a = str(item.answer).strip()
            if q and a:
                faqs.append({"question": q, "answer": a})
        if len(faqs) != n:
            logger.warning(
                "FAQ structured output length mismatch: expected %s got %s", n, len(faqs)
            )
        return {
            "ok": True,
            "productId": product_id,
            "platform": platform,
            "productTitle": title,
            "faqs": faqs[:n],
        }
    except Exception as e:
        logger.warning("FAQ generation structured output failed: %s", e, exc_info=True)
        return {"ok": False, "error": "generation_failed", "faqs": []}
