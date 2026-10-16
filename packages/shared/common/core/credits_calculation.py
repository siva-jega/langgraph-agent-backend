import math

from dataclasses import dataclass
from typing import Optional


@dataclass
class ModelPricing:
    input_price: float  # USD per 1M input tokens
    output_price: float  # USD per 1M output tokens


MODEL_PRICING = {
    "azure/gpt-4o-mini": ModelPricing(input_price=0.17, output_price=0.66),
    "gpt-4o-mini": ModelPricing(input_price=0.17, output_price=0.66),
    "gemini/gemini-2.5-flash": ModelPricing(input_price=0.30, output_price=2.50),
    "gemini/gemini-2.5-flash-lite": ModelPricing(input_price=0.10, output_price=0.40),
}

# Credit baseline: $0.003 = 1 credit
USD_PER_CREDIT = 0.003  # (10k tokens at gpt-4o-mini pricing -> 7k input + 3k output)


def _fallback_model_id() -> str:
    """Configured default agent model from app settings, or first pricing-table id if unbound."""
    try:
        from common.project_settings import project_settings

        return project_settings().DEFAULT_AGENT_MODEL
    except RuntimeError:
        return next(iter(MODEL_PRICING))


def _match_pricing(model_name: str) -> Optional[ModelPricing]:
    normalized = model_name.lower().split("-202")[0]

    if normalized in MODEL_PRICING:
        return MODEL_PRICING[normalized]

    for key in MODEL_PRICING:
        if key in normalized or normalized in key:
            return MODEL_PRICING[key]

    return None


def get_model_pricing(model_name: str) -> ModelPricing:
    p = _match_pricing(model_name)
    if p is not None:
        return p
    p = _match_pricing(_fallback_model_id())
    if p is not None:
        return p
    return next(iter(MODEL_PRICING.values()))


def calculate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    model_name: Optional[str] = None,
) -> float:
    resolved = model_name or _fallback_model_id()
    pricing = get_model_pricing(resolved)

    input_cost = (input_tokens / 1_000_000) * pricing.input_price
    output_cost = (output_tokens / 1_000_000) * pricing.output_price

    return input_cost + output_cost


def calculate_credits(
    input_tokens: int,
    output_tokens: int,
    model_name: Optional[str] = None,
) -> float:
    cost_usd = calculate_cost_usd(input_tokens, output_tokens, model_name)
    credits = cost_usd / USD_PER_CREDIT
    return max(1, math.ceil(credits))


def calculate_credits_from_usage(usage_dict: Optional[dict]) -> float:
    if not usage_dict:
        return 0.0

    input_tokens = usage_dict.get("input_tokens", 0)
    output_tokens = usage_dict.get("output_tokens", 0)
    model_name = usage_dict.get("model")

    return calculate_credits(input_tokens, output_tokens, model_name)
