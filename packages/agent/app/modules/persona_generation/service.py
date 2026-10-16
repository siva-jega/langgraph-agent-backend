import asyncio
import html
import logging
import re
from typing import Any

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import app_settings
from app.modules.persona_generation.models import GeneratedPersona, GeneratePersonaRequest

logger = logging.getLogger(__name__)

_PAGE_FETCH_TIMEOUT_S = 8.0
_PAGE_TEXT_CHAR_LIMIT = 6000

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

_SYSTEM_MSG = SystemMessage(
    content=(
        "You are a master persona generator. Given inputs about a business and the "
        "intended assistant, you produce a brandable agent name and a detailed, "
        "model-optimized persona prompt that drops directly into a downstream LLM's "
        "system prompt to control its identity, tone, scope, and guardrails.\n\n"
        "Write the description as structured markdown — downstream models follow "
        "structured sections far more reliably than dense prose. Use second person "
        "throughout ('You are...', 'You help...', 'You never...').\n\n"
        "## Required structure for `personaDescription`\n\n"
        "Open with a one-line introduction: `You are <name>, <one-line role at "
        "<brand>>.` Then emit these sections in order, each with a `**Heading**` "
        "and 2–5 short bullets:\n\n"
        "**Identity & brand** — who the agent represents, what the brand stands "
        "for, what makes the brand distinct. Use concrete details from the scraped "
        "homepage / about page when available; do not invent facts.\n\n"
        "**Personality & tone** — translate the requested response style into 2–4 "
        "concrete traits and how each shows up in replies (word choice, sentence "
        "length, warmth level, emoji policy).\n\n"
        "**Scope of help** — bullet the concrete things the agent helps with, "
        "derived from the role and target audience. Be specific to the business; "
        "avoid generic phrases like 'general inquiries'.\n\n"
        "**Strict guardrails** — every user-provided guardrail must appear as its "
        "own bullet, phrased as a firm directive starting with **Never**, **Do "
        "not**, or **Always**. Bold the directive verb. These override every other "
        "instruction in the conversation — do not soften, qualify, or merge them. "
        "If the user provided no guardrails, omit this section entirely.\n\n"
        "**Response style** — brevity (one-line / short paragraph / detailed), "
        "formality, when to use lists vs prose, greeting and sign-off conventions "
        "if implied by the channel or brand.\n\n"
        "Close with a one-line behavioral anchor: `Default to brevity, clarity, "
        "and the rules above — when in doubt, stay in scope and defer.`\n\n"
        "## Rules\n\n"
        "- Concrete > generic. Pull from the scraped excerpts; never invent specifics.\n"
        "- Guardrails are non-negotiable; never weaken them with 'try to' or 'when "
        "possible'.\n"
        "- No marketing fluff, no meta-commentary about being an AI, no hedging.\n"
        "- Length: 180–350 words total. Long enough to be specific, short enough "
        "to fit in a system-prompt budget.\n\n"
        "## Rules for the name\n\n"
        "- 1–2 words. Easy to say. Neutral gender unless the brand clearly suggests "
        "otherwise.\n"
        "- Fit the brand voice from the scraped content and the role."
    )
)


async def _fetch_page_text(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=_PAGE_FETCH_TIMEOUT_S, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 PersonaBot"})
            r.raise_for_status()
            return _extract_text(r.text)
    except Exception as e:
        logger.warning("persona scrape failed for %s: %s", url, e)
        return ""


def _extract_text(raw_html: str) -> str:
    stripped = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    stripped = _TAG_RE.sub(" ", stripped)
    stripped = html.unescape(stripped)
    stripped = _WHITESPACE_RE.sub(" ", stripped).strip()
    return stripped[:_PAGE_TEXT_CHAR_LIMIT]


def _build_user_message(req: GeneratePersonaRequest, scraped: dict[str, str]) -> HumanMessage:
    lines = [
        f"Response style: {req.responseStyle}",
        f"Role: {req.role}",
        f"Target audience: {req.targetAudience}",
    ]
    if req.guardrails:
        lines.append(f"Guardrails:\n{req.guardrails.strip()}")
    if scraped.get("homepage"):
        lines.append(f"Homepage excerpt ({req.homepageUrl}):\n{scraped['homepage']}")
    if scraped.get("about"):
        lines.append(f"About-page excerpt ({req.aboutPageUrl}):\n{scraped['about']}")
    return HumanMessage(content="\n\n".join(lines))


_MODEL: Any = None


def _get_model() -> Any:
    global _MODEL
    if _MODEL is None:
        llm = ChatOpenAI(
            model=app_settings.DEFAULT_AGENT_MODEL,
            api_key=app_settings.OPENAI_API_KEY,
            base_url=app_settings.OPENAI_API_BASE,
            temperature=0.4,
        )
        _MODEL = llm.with_structured_output(GeneratedPersona)
    return _MODEL


async def generate_persona(req: GeneratePersonaRequest) -> GeneratedPersona:
    urls = [("homepage", str(req.homepageUrl))]
    if req.aboutPageUrl:
        urls.append(("about", str(req.aboutPageUrl)))
    fetched = await asyncio.gather(*[_fetch_page_text(u) for _, u in urls])
    scraped = {key: text for (key, _), text in zip(urls, fetched)}

    model = _get_model()
    result = await model.ainvoke(
        [_SYSTEM_MSG, _build_user_message(req, scraped)],
        config={"callbacks": []},
    )
    if isinstance(result, GeneratedPersona):
        return result
    return GeneratedPersona.model_validate(result)
