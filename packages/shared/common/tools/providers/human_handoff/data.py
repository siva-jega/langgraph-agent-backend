from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "human_handoff_trigger": {
        "id": "human_handoff_trigger",
        "name": "Request Human Handoff",
        "description": "Escalate to a human agent. Pass priority (LOW|MEDIUM|HIGH|URGENT) and a short summary of why the user needs a human.",
    },
    "human_handoff_trigger_channel": {
        "id": "human_handoff_trigger_channel",
        "name": "Request Human Handoff",
        "description": "Escalate to a human agent on messaging channels (WhatsApp, Instagram, Messenger). Triggers immediately — no form or approval prompt.",
    },
}

__all__ = ["TOOLS_DATA"]

