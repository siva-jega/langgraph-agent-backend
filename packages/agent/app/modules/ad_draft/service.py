import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import app_settings
from app.modules.ad_draft.models import AdDraft, AdDraftRequest
from app.modules.agents.repository import AgentsRepository

logger = logging.getLogger(__name__)

_repo = AgentsRepository()

_CATALOG_LIMIT = 20

_SYSTEM_MSG = SystemMessage(
    content=(
        "You draft Click-to-WhatsApp (CTWA) ads on behalf of a specific business "
        "agent. The tap on the ad opens a WhatsApp chat with that same agent, so you "
        "are designing the ad AND the conversation it opens — one continuous journey.\n\n"
        "You are given the agent's persona (its real identity, brand voice, tone and "
        "guardrails). Everything you write must sound like that agent — same voice, "
        "same guardrails. Never break the persona's rules; never invent facts, prices, "
        "or products not present in the inputs.\n\n"
        "## Produce\n"
        "- `copyVariants`: exactly 3 distinct ad primary-text options. Short (ideally "
        "under 125 chars), scroll-stopping, on-brand. Vary the angle (e.g. offer-led, "
        "curiosity-led, expertise-led) — not three rewordings of one line.\n"
        "- `openingMessage`: the FIRST WhatsApp message the agent sends the moment "
        "someone taps the ad. In the agent's voice, acknowledge they came from the ad, "
        "restate the hook, and ask one question that moves toward a qualified chat. "
        "This is the piece a plain ad tool cannot write — make it count.\n"
        "- `creativePrompt`: one line describing the ideal image, used only when no "
        "catalog image fits.\n"
        "- `suggestedImageUrl`: reuse a fitting image URL FROM THE PROVIDED CATALOG "
        "only. If none fits, leave it null — never fabricate a URL.\n"
        "- `targeting`: sensible starter countries (ISO alpha-2) and an age range, "
        "inferred from the brand and brief. Default broadly if unsure."
    )
)


def _build_user_message(
    persona_name: str, persona_description: str, req: AdDraftRequest
) -> HumanMessage:
    lines: list[str] = []
    if persona_name:
        lines.append(f"Agent name: {persona_name}")
    lines.append(
        "Agent persona (write in this voice, obey its guardrails):\n"
        + (persona_description.strip() or "(no persona configured — use a warm, helpful, on-brand default)")
    )
    if req.brief:
        lines.append(f"Campaign brief / steer:\n{req.brief.strip()}")
    if req.catalog:
        catalog_lines = []
        for p in req.catalog[:_CATALOG_LIMIT]:
            parts = [p.title]
            if p.price:
                parts.append(f"price {p.price}")
            if p.imageUrl:
                parts.append(f"image {p.imageUrl}")
            catalog_lines.append("- " + " | ".join(parts))
        lines.append(
            "Catalog (pick a creative image URL from here only):\n" + "\n".join(catalog_lines)
        )
    return HumanMessage(content="\n\n".join(lines))


def _get_model(model_name: str) -> Any:
    llm = ChatOpenAI(
        model=model_name,
        api_key=app_settings.OPENAI_API_KEY,
        base_url=app_settings.OPENAI_API_BASE,
        temperature=0.7,
    )
    return llm.with_structured_output(AdDraft)


async def draft_ad(req: AdDraftRequest) -> AdDraft:
    config = await _repo.get_config(req.agentId)
    if not config:
        raise ValueError(f"Agent {req.agentId} not found")

    persona = (config.get("config") or {}).get("persona") or {}
    persona_name = persona.get("personaName") or ""
    persona_description = persona.get("personaDescription") or ""
    # Draft with the agent's OWN model, so the voice matches what actually runs.
    model_name = persona.get("model") or app_settings.DEFAULT_AGENT_MODEL

    model = _get_model(model_name)
    result = await model.ainvoke(
        [_SYSTEM_MSG, _build_user_message(persona_name, persona_description, req)],
        config={"callbacks": []},
    )
    if isinstance(result, AdDraft):
        return result
    return AdDraft.model_validate(result)
