"""Tests for the channel-first system prompt builder.

Covers the concrete defects we found in the prior import-from-agents approach:
empty persona, wrong (interrupt) tool names, false widget/interrupt copy, leaked
HubSpot warnings, unsanitized user-supplied skill prompts, missing customer
context, and per-channel few-shots.
"""

from __future__ import annotations

from app.modules.channels.ai.prompt import (
    build_channel_base_prompt,
    build_customer_context,
    _sanitize_user_skill_prompt,
)
from app.modules.channels.ai.skill_map import CHANNEL_SKILL_MAP
from app.modules.channels.providers.gmail.adapter import GmailAdapter
from app.modules.channels.providers.meta.instagram.adapter import InstagramAdapter
from app.modules.channels.providers.meta.messenger.adapter import MessengerAdapter
from app.modules.channels.providers.meta.whatsapp.adapter import WhatsAppAdapter
from app.modules.channels.schemas import (
    InboundMessagePayload,
    InboundMeta,
    InboundSender,
    NormalizedInboundMessage,
)


def _make_inbound(
    channel: str = "instagram",
    display_name: str | None = "Aditya",
    email: str | None = None,
    phone: str | None = None,
    subject: str | None = None,
) -> NormalizedInboundMessage:
    return NormalizedInboundMessage(
        id="msg-1",
        timestamp="2026-06-03T10:00:00Z",
        channel=channel,  # type: ignore[arg-type]
        agentId="agent-1",
        integrationId="int-1",
        workspaceId="ws-1",
        chatId="chat-1",
        sender=InboundSender(displayName=display_name, email=email, phone=phone),
        message=InboundMessagePayload(text="hey", messageId="mid-1", subject=subject),
        meta=InboundMeta(),
    )


# ---------------------------------------------------------------------------
# Persona fallback
# ---------------------------------------------------------------------------


def test_persona_fallback_when_persona_empty() -> None:
    prompt = build_channel_base_prompt(
        {"persona": {}}, customer_ctx=build_customer_context(_make_inbound())
    )
    assert "Support assistant" in prompt
    # Old bug: literal "Name: " (blank) on its own line
    assert "Name: \n" not in prompt
    assert "Personality: \n" not in prompt


def test_persona_fallback_uses_brand_name_when_available() -> None:
    prompt = build_channel_base_prompt(
        {"persona": {}, "brandName": "Acme"},
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "Acme Support" in prompt


def test_persona_uses_provided_fields_when_set() -> None:
    prompt = build_channel_base_prompt(
        {
            "persona": {
                "personaName": "Riley",
                "personaDescription": "Witty and helpful retail concierge.",
            }
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "**Riley**" in prompt
    assert "Witty and helpful retail concierge." in prompt


# ---------------------------------------------------------------------------
# Channel tool names — the prompt must advertise the _channel variants.
# ---------------------------------------------------------------------------


def test_skills_section_uses_channel_tool_names() -> None:
    prompt_params = {
        "persona": {"personaName": "Riley"},
        "skills": [
            {"id": "s1", "toolName": "create_lead_aide", "name": "Lead Capture"},
            {"id": "s2", "toolName": "create_ticket_aide", "name": "Ticket"},
            {"id": "s3", "toolName": "human_handoff_trigger", "name": "Escalate"},
        ],
    }
    prompt = build_channel_base_prompt(
        prompt_params, customer_ctx=build_customer_context(_make_inbound())
    )
    for variant in CHANNEL_SKILL_MAP.values():
        assert f"`{variant}`" in prompt, f"expected `{variant}` in prompt"
    # Old (interrupt-style) names must not be advertised as bound tools.
    assert "`create_lead_aide`)" not in prompt
    assert "`create_ticket_aide`)" not in prompt
    assert "`human_handoff_trigger`)" not in prompt


def test_skills_section_lists_required_fields_for_channel_variants() -> None:
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "skills": [{"id": "s1", "toolName": "create_ticket_aide", "name": "Ticket"}],
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "You must supply" in prompt
    assert "summary" in prompt
    assert "requester_name" in prompt
    assert "requester_email" in prompt
    # priority and status were removed from the LLM surface — they default in
    # the backend (MEDIUM / OPEN). The LLM should no longer be prompted for them.
    assert "priority" not in prompt or "human_handoff" in prompt


def test_skills_section_shows_confirmation_rule_for_write_skills() -> None:
    """When a write-skill is visible, the prompt must instruct the LLM to read
    key fields back to the user before calling — replacing the missing form."""
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "skills": [
                {"id": "s1", "toolName": "create_lead_aide", "name": "Lead"},
                {"id": "s2", "toolName": "create_ticket_aide", "name": "Ticket"},
            ],
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "Confirmation rule for write-skills" in prompt
    assert "even if the value already appears earlier in the conversation" in prompt
    # Per-skill hint also instructs explicit confirmation.
    assert "Confirm each of these explicitly with the user" in prompt


def test_skills_section_omits_confirmation_rule_without_write_skills() -> None:
    """Read-only skills (handoff, web_search) don't need the confirmation banner."""
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "skills": [{"id": "s1", "toolName": "human_handoff_trigger", "name": "Escalate"}],
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "Confirmation rule for write-skills" not in prompt


def test_skill_interrupt_form_copy_is_absent() -> None:
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "skills": [{"id": "s1", "toolName": "create_ticket_aide", "name": "Ticket"}],
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    # The FALSE web-style claim ("the system pauses and shows a confirmation
    # form") must not appear. The prompt should still tell the model there is
    # no form, but the exact wording depends on whether it's a write-skill or
    # a non-write skill.
    assert "pauses and shows" not in prompt
    assert "the form handles collection" not in prompt
    assert "Skill Interrupt Behavior" not in prompt
    assert "no form on this channel" in prompt


# ---------------------------------------------------------------------------
# User-prompt sanitization
# ---------------------------------------------------------------------------


def test_sanitizer_strips_zero_width_chars() -> None:
    raw = "Trigger⁠ when the customer​ mentions a refund or return."
    cleaned = _sanitize_user_skill_prompt(raw)
    assert "⁠" not in cleaned
    assert "​" not in cleaned
    assert "Trigger when the customer mentions a refund or return." == cleaned


def test_sanitizer_rejects_fragmentary_trigger_prompts() -> None:
    # Real-world bad inputs we saw — fragmentary "trigger" hints with no
    # actual rule. These would otherwise replace canonical guidance.
    assert _sanitize_user_skill_prompt("when greet - trigger") == ""
    assert _sanitize_user_skill_prompt("when a customer greets - trigger") == ""
    assert _sanitize_user_skill_prompt("trigger on hi") == ""


def test_sanitizer_keeps_real_instructions() -> None:
    # A proper instruction (sentence-shaped) is kept.
    assert (
        _sanitize_user_skill_prompt(
            "Create a ticket when the customer reports a damaged or missing item."
        )
        != ""
    )


def test_short_user_prompt_falls_back_to_canonical_skill_default() -> None:
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "skills": [
                {
                    "id": "s1",
                    "toolName": "create_lead_aide",
                    "name": "Customer Creation",
                    "config": {"triggerCondition": {"prompt": "when greet - trigger"}},
                }
            ],
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    # Bad user prompt is dropped — canonical "purchase intent" default leads.
    assert "when greet - trigger" not in prompt
    assert "purchase intent" in prompt.lower()


def test_long_user_prompt_is_appended_not_replacing_default() -> None:
    user_text = (
        "Also create a ticket when the customer mentions a damaged product or a "
        "missing item from their last order, especially if it has been more than "
        "three business days since delivery."
    )
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "skills": [
                {
                    "id": "s1",
                    "toolName": "create_ticket_aide",
                    "name": "Ticket",
                    "config": {"triggerCondition": {"prompt": user_text}},
                }
            ],
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "Workspace note:" in prompt
    assert "damaged product" in prompt
    assert "support ticket" in prompt.lower()  # canonical default still present


def test_skill_examples_render_as_invoke_and_dont_invoke_lines() -> None:
    """triggerCondition.examples become ✓/✗ guidance under the skill line —
    positives as "Invoke for messages like", negatives (near-misses) as
    "Do NOT invoke for", with notes in parentheses."""
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "skills": [
                {
                    "id": "s1",
                    "toolName": "create_ticket_aide",
                    "name": "Ticket",
                    "config": {
                        "triggerCondition": {
                            "type": "llm_decides",
                            "prompt": (
                                "Create a support ticket when the customer reports "
                                "an order issue, defect, or refund request."
                            ),
                            "examples": [
                                {
                                    "userSays": "My order arrived broken, replace it.",
                                    "shouldInvoke": True,
                                    "note": "Product defect.",
                                },
                                {
                                    "userSays": "How long does delivery take?",
                                    "shouldInvoke": False,
                                    "note": "General question.",
                                },
                            ],
                        }
                    },
                }
            ],
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert 'Invoke for messages like: "My order arrived broken, replace it." (Product defect.)' in prompt
    assert 'Do NOT invoke for: "How long does delivery take?" (General question.)' in prompt


def test_skill_without_examples_renders_no_example_lines() -> None:
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "skills": [{"id": "s1", "toolName": "create_ticket_aide", "name": "Ticket"}],
        },
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "Invoke for messages like:" not in prompt
    assert "Do NOT invoke for:" not in prompt


# ---------------------------------------------------------------------------
# Web-only sections must not leak into channel prompts.
# ---------------------------------------------------------------------------


def test_widget_renders_cards_copy_is_absent() -> None:
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "apps": [{"slug": "shopify"}],
        },
        customer_ctx=build_customer_context(_make_inbound()),
        format_rules=InstagramAdapter().get_format_rules(_make_inbound()),
    )
    assert "widget renders" not in prompt
    assert "interactive product cards" not in prompt


def test_hubspot_warning_absent_when_hubspot_not_connected() -> None:
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}, "apps": [{"slug": "shopify"}]},
        customer_ctx=build_customer_context(_make_inbound()),
    )
    # Old prompt warned against `search_tickets` even without HubSpot — must be gone.
    assert "search_tickets" not in prompt
    assert "HubSpot" not in prompt and "hubspot" not in prompt


# ---------------------------------------------------------------------------
# Customer context block
# ---------------------------------------------------------------------------


def test_customer_context_block_includes_display_name() -> None:
    inbound = _make_inbound(display_name="Aditya", phone="+919999999999")
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}},
        customer_ctx=build_customer_context(inbound),
    )
    assert "# Customer" in prompt
    assert "Aditya" in prompt
    assert "+919999999999" in prompt
    assert "instagram" in prompt


def test_customer_context_includes_email_subject_for_gmail() -> None:
    inbound = _make_inbound(
        channel="gmail",
        email="adi@example.com",
        subject="Refund for order 1242",
    )
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}},
        customer_ctx=build_customer_context(inbound),
    )
    assert "Refund for order 1242" in prompt


# ---------------------------------------------------------------------------
# Per-channel format rules + few-shots
# ---------------------------------------------------------------------------


def test_instagram_format_rules_and_few_shots() -> None:
    inbound = _make_inbound(channel="instagram", display_name="Aditya")
    adapter = InstagramAdapter()
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}, "apps": [{"slug": "shopify"}]},
        customer_ctx=build_customer_context(inbound),
        format_rules=adapter.get_format_rules(inbound),
        few_shots=adapter.get_few_shots(inbound),
    )
    assert "under 900 characters" in prompt
    assert "Plain text only" in prompt
    assert "# Examples" in prompt
    # All six skill-routing scenarios show up so the LLM has unambiguous guidance.
    assert "get_products" in prompt
    assert "get_order_status" in prompt
    assert "search_knowledgebase" in prompt
    assert "create_lead_aide_channel" in prompt
    assert "create_ticket_aide_channel" in prompt
    assert "human_handoff_trigger_channel" in prompt


def test_whatsapp_format_rules_allow_whatsapp_markdown() -> None:
    inbound = _make_inbound(channel="whatsapp", display_name="Aditya", phone="+91999")
    adapter = WhatsAppAdapter()
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}, "apps": [{"slug": "shopify"}]},
        customer_ctx=build_customer_context(inbound),
        format_rules=adapter.get_format_rules(inbound),
        few_shots=adapter.get_few_shots(inbound),
    )
    assert "under 3900 characters" in prompt
    assert "*bold*" in prompt
    # The full-markdown web style ("**bold**") must NOT be promoted here.
    assert "**bold** for emphasis" not in prompt


def test_messenger_format_rules_are_plain_text() -> None:
    inbound = _make_inbound(channel="messenger", display_name="Aditya")
    adapter = MessengerAdapter()
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}},
        customer_ctx=build_customer_context(inbound),
        format_rules=adapter.get_format_rules(inbound),
        few_shots=adapter.get_few_shots(inbound),
    )
    assert "under 1900 characters" in prompt
    assert "Plain text only" in prompt


def test_gmail_format_rules_include_greeting_and_sign_off() -> None:
    inbound = _make_inbound(
        channel="gmail",
        display_name="Aditya",
        email="adi@example.com",
        subject="Question",
    )
    adapter = GmailAdapter()
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}},
        customer_ctx=build_customer_context(inbound),
        format_rules=adapter.get_format_rules(inbound),
        few_shots=adapter.get_few_shots(inbound),
    )
    assert "Hi Aditya," in prompt
    assert "Best regards," in prompt


# ---------------------------------------------------------------------------
# Sanity: channel labels in the role block
# ---------------------------------------------------------------------------


def test_role_block_names_channel_label() -> None:
    for channel, label in (
        ("whatsapp", "WhatsApp"),
        ("messenger", "Facebook Messenger"),
        ("instagram", "Instagram DM"),
        ("gmail", "email"),
    ):
        prompt = build_channel_base_prompt(
            {"persona": {"personaName": "Riley"}},
            customer_ctx=build_customer_context(_make_inbound(channel=channel)),
        )
        assert label in prompt, f"missing channel label {label!r} for {channel}"


# ---------------------------------------------------------------------------
# Apps section is gated on connected app slugs.
# ---------------------------------------------------------------------------


def test_apps_section_only_shows_connected_apps() -> None:
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}, "apps": [{"slug": "shopify"}]},
        customer_ctx=build_customer_context(_make_inbound()),
        format_rules=InstagramAdapter().get_format_rules(_make_inbound()),
    )
    assert "Shopify" in prompt
    # Calendly/notion/airtable not connected — must not appear.
    assert "Calendly" not in prompt
    assert "Notion" not in prompt


def test_kb_section_only_when_kb_attached() -> None:
    prompt_without = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}},
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "search_knowledgebase" not in prompt_without

    prompt_with = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}, "entityIds": ["e1"]},
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "search_knowledgebase" in prompt_with
    # Old prompt's "EVERY user response" pressure removed.
    assert "EVERY user response" not in prompt_with


def test_kb_section_disambiguates_when_shopify_also_connected() -> None:
    """Product queries must route to get_products, not search_knowledgebase."""
    prompt = build_channel_base_prompt(
        {
            "persona": {"personaName": "Riley"},
            "entityIds": ["e1"],
            "apps": [{"slug": "shopify"}],
        },
        customer_ctx=build_customer_context(_make_inbound()),
        format_rules=InstagramAdapter().get_format_rules(_make_inbound()),
    )
    # KB section restricts itself to non-product topics.
    assert "non-product" in prompt
    assert "never `search_knowledgebase`" in prompt
    # Apps section asserts get_products is the source of truth.
    assert (
        "only" in prompt
        and "sources of truth for products" in prompt
    )
    # "product specs" must not be claimed by KB anymore (was ambiguous before).
    kb_idx = prompt.index("# Knowledge base")
    kb_section = prompt[kb_idx : kb_idx + 800]
    assert "specs" not in kb_section.lower() or "product questions" in kb_section


def test_kb_section_keeps_general_guidance_without_ecommerce() -> None:
    """When no ecommerce app is connected, KB section keeps broader phrasing."""
    prompt = build_channel_base_prompt(
        {"persona": {"personaName": "Riley"}, "entityIds": ["e1"]},
        customer_ctx=build_customer_context(_make_inbound()),
    )
    assert "non-product" not in prompt
    assert "any user question" in prompt
