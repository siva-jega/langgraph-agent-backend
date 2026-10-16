"""Hermetic tests for ad-draft generation — repo and LLM are stubbed, no DB/network.

Covers the two bits of real logic: pulling persona out of the agent config dict,
and building a user message that carries the persona voice + catalog into the prompt.
"""

from __future__ import annotations

import pytest

from app.modules.ad_draft import service
from app.modules.ad_draft.models import AdDraft, AdDraftRequest, AdProductHint, AdTargeting


def _sample_draft() -> AdDraft:
    return AdDraft(
        copyVariants=["a", "b", "c"],
        openingMessage="Hi! Saw our ad?",
        creativePrompt="a bright planter on a balcony",
        suggestedImageUrl="https://cdn/x.jpg",
        targeting=AdTargeting(countries=["CA"], ageMin=18, ageMax=65),
    )


def test_build_user_message_includes_persona_and_catalog() -> None:
    req = AdDraftRequest(
        agentId="agent-1",
        catalog=[AdProductHint(title="Self-watering pot", price="₹1840", imageUrl="https://cdn/x.jpg")],
        brief="monsoon sale",
    )
    msg = service._build_user_message("Bloom", "You are Bloom, a plant expert.", req)
    assert "Bloom" in msg.content
    assert "You are Bloom" in msg.content
    assert "Self-watering pot" in msg.content
    assert "https://cdn/x.jpg" in msg.content
    assert "monsoon sale" in msg.content


async def test_draft_ad_reads_persona_and_returns_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def fake_get_config(agent_id: str):
        captured["agent_id"] = agent_id
        return {"config": {"persona": {"personaName": "Bloom", "personaDescription": "You are Bloom.", "model": "gemini/x"}}}

    class FakeModel:
        async def ainvoke(self, messages, config=None):
            captured["model_name"] = self._name
            captured["messages"] = messages
            return _sample_draft()

    def fake_get_model(model_name: str):
        m = FakeModel()
        m._name = model_name
        return m

    monkeypatch.setattr(service._repo, "get_config", fake_get_config)
    monkeypatch.setattr(service, "_get_model", fake_get_model)

    out = await service.draft_ad(AdDraftRequest(agentId="agent-1"))

    assert isinstance(out, AdDraft)
    assert len(out.copyVariants) == 3
    assert captured["agent_id"] == "agent-1"
    # Drafts with the agent's OWN model, not a hardcoded default.
    assert captured["model_name"] == "gemini/x"


async def test_draft_ad_missing_agent_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_config(agent_id: str):
        return None

    monkeypatch.setattr(service._repo, "get_config", fake_get_config)
    with pytest.raises(ValueError):
        await service.draft_ad(AdDraftRequest(agentId="missing"))
