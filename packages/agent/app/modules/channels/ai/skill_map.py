"""Shared mapping of interrupt-based skill tools to their channel-safe variants.

Channels can't pause for a confirmation form, so the interrupt tools are swapped
for their ``_channel`` siblings which execute directly. Imported by both the
runtime (``nodes._get_tools``) and the prompt builder so what's advertised in
the system prompt matches what's actually bound on the model.
"""

CHANNEL_SKILL_MAP: dict[str, str] = {
    "human_handoff_trigger": "human_handoff_trigger_channel",
    "create_ticket_aide": "create_ticket_aide_channel",
    "create_lead_aide": "create_lead_aide_channel",
    # `use_skill` raises a LangGraph interrupt to render a form; on channels the
    # LLM collects field values from the user in chat and passes them via the
    # channel variant's `fields` argument.
    "use_skill": "use_skill_channel",
}
