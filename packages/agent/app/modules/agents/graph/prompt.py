from typing import Any

import logging

logger = logging.getLogger(__name__)

_SKILL_PROMPT_MAX_LEN = 420


def _looks_like_internal_skill_config_blob(text: str) -> bool:
    """True if triggerCondition.prompt looks like pasted JSON/YAML form schema, not a short usage hint."""
    s = text.strip()
    if len(s) < 80:
        return False
    lower = s.lower()
    if '"fields"' in s or "'fields'" in s or "form.fields" in lower:
        return True
    if '"type"' in s and '"label"' in s and ("required" in lower or "mapsTo" in lower):
        return True
    if s.startswith(("{", "[")) and ("customskill" in lower or "custom_skill" in lower):
        return True
    return False


def _trim_public_skill_prompt(text: str, max_len: int = _SKILL_PROMPT_MAX_LEN) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if _looks_like_internal_skill_config_blob(t):
        return ""
    if len(t) > max_len:
        return t[: max_len - 1].rstrip() + "…"
    return t


_MAX_SKILL_EXAMPLES = 6


def _render_skill_examples(trigger_condition: Any) -> list[str]:
    """Indented guidance lines from `triggerCondition.examples`.

    Examples are workspace-curated boundary cases: `shouldInvoke: true` entries
    show messages that must trigger the skill, `false` entries are near-misses
    that must NOT (they guard against over-triggering). Rendered as two compact
    sub-bullets under the skill line.
    """
    if not isinstance(trigger_condition, dict):
        return []
    raw = trigger_condition.get("examples")
    if not isinstance(raw, list):
        return []
    positives: list[str] = []
    negatives: list[str] = []
    for ex in raw[:_MAX_SKILL_EXAMPLES]:
        if not isinstance(ex, dict):
            continue
        said = " ".join(str(ex.get("userSays") or "").split())[:160]
        if not said:
            continue
        note = " ".join(str(ex.get("note") or "").split())[:120]
        entry = f'"{said}"' + (f" ({note})" if note else "")
        (positives if ex.get("shouldInvoke") else negatives).append(entry)
    lines: list[str] = []
    if positives:
        lines.append("  - Invoke for messages like: " + "; ".join(positives) + ".")
    if negatives:
        lines.append("  - Do NOT invoke for: " + "; ".join(negatives) + ".")
    return lines


def _flow_app_action_slugs(skills: Any) -> set[str]:
    """Slugs from `use_skill` flow skills whose afterSubmit action is `app_action`."""
    out: set[str] = set()
    if not isinstance(skills, list):
        return out
    for s in skills:
        if not isinstance(s, dict) or (s.get("toolName") or "").strip() != "use_skill":
            continue
        cfg_raw = s.get("config")
        cfg = cfg_raw if isinstance(cfg_raw, dict) else {}
        cs_raw = cfg.get("customSkill")
        cs = cs_raw if isinstance(cs_raw, dict) else {}
        sub_raw = cs.get("afterSubmit")
        sub = sub_raw if isinstance(sub_raw, dict) else {}
        if str(sub.get("action") or "").strip().lower() != "app_action":
            continue
        slug = str(sub.get("appSlug") or "").strip().lower()
        if slug:
            out.add(slug)
    return out


def _connected_app_slugs(additional_params: dict[str, Any]) -> set[str]:
    apps = additional_params.get("apps") or []
    out: set[str] = set()
    if isinstance(apps, list):
        for a in apps:
            if isinstance(a, dict):
                sl = str(a.get("slug") or a.get("name") or "").strip().lower()
                if sl:
                    out.add(sl)
    return out


def _flow_vs_direct_integration_section(
    skills: Any, additional_params: dict[str, Any]
) -> str:
    """When flows and connected apps overlap, tell the model to prefer the configured flow."""
    if not isinstance(skills, list):
        return ""
    has_use_skill = any(
        isinstance(s, dict) and (s.get("toolName") or "").strip() == "use_skill"
        for s in skills
    )
    flow_slugs = _flow_app_action_slugs(skills)
    connected = _connected_app_slugs(additional_params)
    overlap = flow_slugs & connected
    if not has_use_skill or not overlap:
        return ""
    names = ", ".join(sorted(overlap))
    return f"""
# Workspace flows vs direct integration tools
- Custom **flows** (`use_skill` with the right `agent_skill_id`) can target the same integrations as **direct app tools** (here: **{names}**).
- **Prefer `use_skill`** when the user's request matches a flow described under **Available Skills**, so the configured form, validation, and post-submit behavior run as designed.
- Use **direct** integration tools only when no flow covers that intent, or the user clearly wants a quick unstructured call without the flow.

"""


def _crm_ai_populated_prompt_section(fields: Any) -> str:
    """Tell the model which CRM keys it must supply via tools and allowed option values."""
    if not isinstance(fields, list) or len(fields) == 0:
        return ""
    lines: list[str] = [
        "\n# CRM fields supplied by you (not collected in user skill forms)\n",
        "These workspace fields are **not** shown in interrupt forms. You must set them via the "
        "appropriate tool arguments when you call `human_handoff_trigger`, `create_ticket_aide`, "
        "or `create_lead_aide`. Use the exact **storage key** (shown below) and, for dropdowns, "
        "one of the listed **value** codes (not the human label).\n",
    ]
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        ent = str(raw.get("entity") or "UNKNOWN").strip()
        by_entity.setdefault(ent, []).append(raw)
    for entity in sorted(by_entity.keys()):
        lines.append(f"\n**{entity}**\n")
        for f in sorted(by_entity[entity], key=lambda x: str(x.get("key") or "")):
            key = str(f.get("key") or "").strip()
            if not key:
                continue
            label = str(f.get("label") or key).strip()
            typ = str(f.get("type") or "").strip()
            req = bool(f.get("required"))
            req_s = "required" if req else "optional"
            line = f"- `{key}` ({label}, {typ}, {req_s})"
            opts = f.get("options")
            if isinstance(opts, list) and len(opts) > 0:
                parts: list[str] = []
                for o in sorted(opts, key=lambda x: str(x.get("value") or "")):
                    if isinstance(o, dict):
                        v = str(o.get("value") or "").strip()
                        lab = str(o.get("label") or v).strip()
                        if v:
                            parts.append(f"`{v}`={lab}")
                if parts:
                    line += f" — allowed values: {', '.join(parts)}"
            lines.append(line + "\n")
    lines.append(
        "\nWhen a tool exposes parameters that match these keys (e.g. ticket **status**, **priority**, "
        "**reason**; customer **status**, **intent**; handoff **priority** and escalation summary/reason), "
        "choose values consistent with the conversation and the allowed codes above.\n"
    )
    return "".join(lines)


def _product_faq_context_section(additional_params: dict[str, Any]) -> str:
    pfc = additional_params.get("product_faq_context")
    if not isinstance(pfc, dict):
        return ""
    q = str(pfc.get("question") or "").strip()
    a = str(pfc.get("answer") or "").strip()
    pt = str(pfc.get("product_title") or "").strip()
    if not q or not a:
        return ""
    title_line = f"Product: {pt}\n" if pt else ""
    return f"""
# Approved product FAQ (ground truth) — **highest priority for this message**
The shopper tapped this suggested question on a product page. Treat the following as **canonical** store-approved facts. You may paraphrase warmly, but **do not** contradict this content or invent different specs, materials, or policies.

{title_line}**Question:** {q}
**Approved answer:** {a}

**You must** answer in line with the approved answer above (this turn is in scope). Do **not** reply with only a generic greeting like "How can I help?" without addressing the question. Respond in a natural, helpful tone—do not say you are quoting an FAQ.
"""


# ---------------------------------------------------------------------------
# Shared section builders
# ---------------------------------------------------------------------------

_SKILL_DEFAULTS: dict[str, str] = {
    "web_search": (
        "Search the web for current information, news, or anything not in your "
        "knowledge base. Call whenever the user asks to search online or you need "
        "up-to-date information."
    ),
    "human_handoff_trigger": (
        "Transfer the conversation to a human agent. Call when the user explicitly "
        "asks to speak with a human or real person, expresses frustration, or the "
        "issue is too complex to resolve automatically."
    ),
    "create_ticket_aide": (
        "Create a support ticket on behalf of the user. Call when the user reports "
        "a problem, complaint, or issue that needs tracking — e.g. order issues, "
        "refund requests, product defects, or service failures. "
        "The `reason` argument is REQUIRED and must be a concrete phrase derived "
        "from the conversation describing the customer's actual problem (e.g. "
        "'damaged item on arrival', 'refund requested for order #1242', "
        "'wrong size delivered'). Never pass placeholders like 'support', "
        "'inquiry', or 'help' — they are rejected."
    ),
    "create_lead_aide": (
        "Register the user as a potential customer or lead. Call when the user "
        "expresses purchase intent, requests a callback, asks for a quote, or wants "
        "to be contacted by the sales team."
    ),
    "get_products": (
        "Fetch live product listings from the connected store. Call for any shopping "
        "intent — browse, recommendations, pricing, or availability queries."
    ),
    "get_order_status": (
        "Look up the status of a customer order. Call when the user asks about their "
        "order, shipping, or delivery."
    ),
    "send_email": (
        "Send an email on behalf of the user. Call when the user explicitly asks to "
        "send an email."
    ),
    "search_emails": (
        "Search the user's inbox. Call when the user asks to find or look up emails."
    ),
    "create_event": (
        "Create a calendar event. Call when the user wants to schedule a meeting or "
        "appointment."
    ),
    "list_events": (
        "List upcoming calendar events. Call when the user asks about their schedule."
    ),
    "send_slack_message": (
        "Send a Slack message. Call when the user wants to notify someone on Slack."
    ),
}


def _get_visible_skills(additional_params: dict[str, Any]) -> list[dict]:
    """Return enabled skills, filtering out disabled ones and handoff if not configured."""
    human_handoff_enabled = additional_params.get("human_handoff_enabled", False)
    skills = additional_params.get("skills", [])
    visible = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        skill_cfg = skill.get("config") or {}
        if isinstance(skill_cfg, dict) and skill_cfg.get("_enabled") is False:
            continue
        tool_name = skill.get("toolName")
        if tool_name == "human_handoff_trigger" and not human_handoff_enabled:
            continue
        visible.append(skill)
    visible.sort(key=lambda s: (str(s.get("toolName") or ""), str(s.get("id") or "")))
    return visible


def _build_skills_section(additional_params: dict[str, Any]) -> str:
    """Skills listing + handoff section (shared across all channels)."""
    skills_section = ""
    visible_skills = _get_visible_skills(additional_params)

    skill_tool_names = [
        s.get("toolName")
        for s in visible_skills
        if isinstance(s, dict) and s.get("toolName")
    ]
    has_crm_approval_tools = any(
        t in skill_tool_names
        for t in ("create_lead_aide", "create_ticket_aide")
    )

    if skill_tool_names:
        skills_section += "\n# Available Skills\n"
        skills_section += (
            "You have access to the following skill tools. Use them proactively when relevant.\n"
            "When intent matches, **call the tool immediately** with arguments you can infer "
            "from the conversation. Do not ask the user for values the tool handles internally.\n\n"
        )
        for skill in visible_skills:
            tool_name = skill.get("toolName", "")
            skill_name = skill.get("name", tool_name)
            if not tool_name:
                continue
            skill_cfg = skill.get("config") or {}
            trigger_condition = (
                skill_cfg.get("triggerCondition") or {}
                if isinstance(skill_cfg, dict)
                else {}
            )
            if not isinstance(trigger_condition, dict):
                trigger_condition = {}
            custom_prompt = _trim_public_skill_prompt(
                (trigger_condition.get("prompt") or "").strip(),
            )

            if tool_name == "use_skill":
                sid = str(skill.get("id") or "").strip()
                base = (
                    custom_prompt
                    or "Use when the user needs this workspace-defined custom skill."
                )
                if sid:
                    description = (
                        f"{base} "
                        f"**Call `use_skill` immediately** with `agent_skill_id`=\"{sid}\" "
                        f"(and optional short `context`). Do **not** ask the user to supply "
                        f"form-field values in chat — call the tool first."
                    )
                else:
                    description = base
            else:
                description = custom_prompt or _SKILL_DEFAULTS.get(
                    tool_name,
                    "Use this tool when it is relevant to the user's request.",
                )
            skills_section += f"- **{skill_name}** (`{tool_name}`): {description}\n"
            for example_line in _render_skill_examples(trigger_condition):
                skills_section += f"{example_line}\n"
        skills_section += "\n"

    if "human_handoff_trigger" in skill_tool_names:
        skills_section += (
            "# Human Handoff\n"
            "- Call `human_handoff_trigger` when the user explicitly asks to speak with "
            "a human, expresses frustration, or the issue cannot be resolved automatically.\n\n"
        )

    if has_crm_approval_tools:
        active_crm_tools = [t for t in ("create_ticket_aide", "create_lead_aide") if t in skill_tool_names]
        tool_list = " or ".join(f"`{t}`" for t in active_crm_tools)
        skills_section += (
            "# Skill Interrupt Behavior (CRITICAL)\n"
            f"- When you call {tool_list}, the system **pauses and shows a confirmation form** "
            "to the user. Call the tool immediately when intent is clear — do NOT ask the user "
            "for form fields in chat, the form handles collection.\n"
            "- You supply tool arguments you can infer from the conversation (e.g. summary, "
            "priority, reason). The interrupt form collects contact fields from the user.\n\n"
        )

    return skills_section


def _build_kb_section(additional_params: dict[str, Any]) -> str:
    has_knowledgebase = (
        len(additional_params.get("folderIds", [])) > 0
        or len(additional_params.get("entityIds", [])) > 0
    )
    if not has_knowledgebase:
        return ""
    has_product_tool = bool(_connected_app_slugs(additional_params) & {"shopify", "woocommerce"})
    product_tool_ref = "`get_products`" if has_product_tool else "the product/catalog tool"
    return f"""
# Knowledge Base & Search Guidelines (CRITICAL)
- **Scope — what `search_knowledgebase` is for**: company policy, terms & conditions, shipping/returns/refunds, warranty, privacy, about-the-company, store hours, contact/support info, and other general domain questions that aren't about a specific product.
- **Out of scope — do NOT use it for products**: Any query where the user is asking about a certain kind of product, a specific product, availability, pricing, or wants a product recommendation is NOT this tool's job. Let {product_tool_ref} handle those — do not call `search_knowledgebase` for them, even as a fallback.
- **When in scope, search fresh**: Even if items weren't found previously, perform a new search rather than relying on history or assuming the answer.
- **Combine related terms**: Include all synonyms, variations, and related concepts in ONE query.
- **Data Faithfulness**: Always be faithful to the data provided from the knowledge base; do not make up or hallucinate data.

"""


# Tool-level descriptions per connected app slug.
# Each entry: (tool_name, when/what it does)
_APP_TOOLS: dict[str, list[tuple[str, str]]] = {
    "hubspot": [
        ("list_contacts", "List all CRM contacts"),
        ("search_contacts", "Search contacts by name, email, or keyword"),
        ("get_contact", "Get full details of a specific contact"),
        ("search_contacts_by_company", "Find all contacts belonging to a company"),
        ("list_companies", "List all companies in CRM"),
        ("search_companies", "Search companies by name or domain"),
        ("list_deals", "List deals in the pipeline"),
        ("search_deals", "Search deals by name, stage, or value"),
        ("search_deals_by_company", "Find deals linked to a specific company"),
        ("list_tickets", "List all support tickets"),
        ("search_tickets", "Search tickets by subject or status"),
        ("create_ticket", "Create a new support ticket"),
        ("get_analytics_summary", "Get CRM analytics and performance summary"),
    ],
    "zendesk": [
        ("create_ticket", "Create a new support ticket in Zendesk"),
    ],
    "shopify": [
        ("get_products", "Browse the live product catalog — pricing, availability, recommendations"),
        ("get_order_status", "Look up order status, tracking, and shipping updates"),
    ],
    "woocommerce": [
        ("get_products", "Browse the live product catalog — pricing, availability, recommendations"),
        ("get_order_status", "Look up order status, tracking, and shipping updates"),
    ],
    "gmail": [
        ("list_messages", "List recent emails in the inbox"),
        ("search_messages", "Search inbox by keyword, sender, or date"),
        ("get_message", "Read the full content of a specific email"),
        ("list_threads", "List email conversation threads"),
    ],
    "outlook": [
        ("list_outlook_messages", "List recent emails in the inbox"),
        ("search_outlook_messages", "Search inbox by keyword or sender"),
        ("get_outlook_message", "Read the full content of a specific email"),
    ],
    "gcalendar": [
        ("list_events", "List upcoming calendar events"),
        ("get_event_details", "Get full details of a specific event"),
        ("search_events", "Search events by keyword or attendee"),
    ],
    "calendly": [
        ("list_event_types_calendly", "List available meeting types and slots"),
        ("book_meeting_calendly", "Book a meeting slot for the user"),
        ("cancel_event_calendly", "Cancel a scheduled event"),
        ("find_event_calendly", "Find a specific scheduled event"),
    ],
    "calcom": [
        ("list_event_types_calcom", "List available meeting types"),
        ("book_meeting_calcom", "Book a meeting slot for the user"),
        ("list_bookings_calcom", "View upcoming bookings"),
        ("cancel_booking_calcom", "Cancel an existing booking"),
        ("reschedule_booking_calcom", "Reschedule an existing booking"),
    ],
    "notion": [
        ("search_notion", "Search across the entire Notion workspace"),
        ("list_notion_pages", "List pages in the workspace"),
        ("query_notion_database", "Query a Notion database with filters"),
        ("create_notion_database_item", "Create a new item in a Notion database"),
    ],
    "slack": [
        ("slack_send_message", "Send a message to a Slack channel or user"),
        ("slack_search_messages", "Search Slack message history"),
        ("slack_conversations_history", "Get recent messages in a channel"),
        ("slack_channels_list", "List available Slack channels"),
    ],
    "airtable": [
        ("list_airtable_records", "List records in an Airtable base/table"),
        ("get_airtable_record", "Get a specific record by ID"),
        ("create_airtable_record", "Create a new record in a table"),
        ("update_airtable_record", "Update an existing record"),
    ],
    "gdrive": [
        ("list_drive_items", "List files and folders in Google Drive"),
        ("search_drive_files", "Search files by name or content"),
        ("get_drive_file_content", "Read the content of a specific file"),
    ],
}


def _build_apps_section(additional_params: dict[str, Any]) -> str:
    slugs = _connected_app_slugs(additional_params)
    if not slugs:
        return ""
    lines = ["\n# Connected App Tools\n"]
    for slug in sorted(slugs):
        tools = _APP_TOOLS.get(slug)
        if not tools:
            continue
        lines.append(f"\n**{slug.capitalize()}**\n")
        for tool_name, description in tools:
            lines.append(f"- `{tool_name}`: {description}\n")
    return "".join(lines) + "\n" if len(lines) > 1 else ""

# ---------------------------------------------------------------------------
# build_base_prompt — shared by all channels
# ---------------------------------------------------------------------------

def build_base_prompt(additional_params: dict[str, Any]) -> str:
    """Base system prompt shared by all channels (web, Gmail, Meta, etc.).

    Contains: persona, KB instructions, skills, apps, CRM fields, domain restriction,
    core guidelines, and tool-calling behavior. Does NOT include channel-specific
    response format, product cards, or channel-specific tone instructions.
    """
    additional_params = additional_params or {}
    persona = additional_params.get("persona", {}) or {}
    persona_description = persona.get("personaDescription", "")
    persona_name = persona.get("personaName", "")

    crm_ai_section = _crm_ai_populated_prompt_section(
        additional_params.get("crmAiPopulatedFields") or []
    )
    skills_section = _build_skills_section(additional_params)
    kb_section = _build_kb_section(additional_params)
    apps_section = _build_apps_section(additional_params)
    flow_vs_integration_section = _flow_vs_direct_integration_section(
        additional_params.get("skills", []), additional_params
    )

    # Gate instructions on connected apps
    _app_slugs = _connected_app_slugs(additional_params)
    _has_gmail = bool(_app_slugs & {"gmail", "outlook"})
    _has_calendar = bool(_app_slugs & {"calendly", "calcom", "gcalendar"})
    _has_shopify = bool(_app_slugs & {"shopify"})
    _visible_skill_names = {s.get("toolName") for s in _get_visible_skills(additional_params) if s.get("toolName")}
    _human_handoff_enabled = "human_handoff_trigger" in _visible_skill_names

    # Shopify: tell the LLM to call get_products so the widget renders interactive cards
    shopify_catalog_section = ""
    if _has_shopify:
        shopify_catalog_section = """
# Shopify product cards — CRITICAL
- The widget renders **interactive product cards** only when you call **`get_products`**. KB text or a prose list does not render cards.
- For any shopping intent (browse, recommend, price, availability, "suggest …", "show me …", product types): **call `get_products` first** with a query from the user message. Do **not** call HubSpot CRM tools (e.g. `search_tickets`, `list_tickets`, `search_contacts`) for product searches — those tools are for customer data only, not product catalog lookups.
- **Describe and recommend in the text — don't just point at the cards.** For the most relevant products you show (up to ~5; never dump the whole catalog), give each a short line: a brief description **in your own words** plus *why you recommend it* for what the user asked. Refer to each product by name so it maps to its card. Open with a one-line intro and close with a short follow-up question. Add a salesperson's take — don't copy the card's raw specs verbatim.
- **Language handling (CRITICAL)**: Pass the search-query arguments in the **user's original language** — the server translates them to English internally. Always set `original_language` to the ISO 639-1 code of the user's message (e.g. "en", "es", "fr", "hi", "ta", "ar", "zh"); use "en" only when the message is already English.
- **Non-English reply contract (CRITICAL)**: When `original_language` is anything other than `"en"`, your **entire text reply MUST be 100% in that language** — including the per-product descriptions and recommendations. Concretely:
  - You may describe and recommend each product, but write **every word in the user's language**. Translate any detail you mention (description, category, why it fits). A product's brand or proper-noun name may stay as-is, but all surrounding commentary must be translated.
  - Do **not** paste English descriptions, English prices, English labels ("Price", "Description", "View product"), or any other English fragment from the `get_products` result. If you're about to write an English word that came from the tool, translate it first.
  - The only English strings that may appear are raw URL targets (e.g. `https://...`), and only if genuinely needed; normally URLs are on the cards and need not be repeated.

"""
        if additional_params.get("active_collection_id"):
            shopify_catalog_section += """
# Active collection (CRITICAL)
- The shopper is currently viewing a curated product collection. When they say "this",
  "these", "this collection", "tell me more", or ask to compare/filter within it, call
  `get_collection` and answer from its live products. Never quote price/stock from earlier
  messages — always use the fresh tool result.

"""

    # App-specific tool-calling guidelines
    gmail_instruction = (
        "- **Mail search**: If a custom flow under Available Skills covers mail search "
        "(`use_skill`), call that flow first; otherwise call the mail tools directly.\n"
        if _has_gmail else ""
    )
    calendly_instruction = (
        "- **Calendar booking**: Before calling the booking tool, confirm you have "
        "the invitee's **email**, **date**, and **time**. If any are missing, ask in "
        "one short friendly question (e.g. \"Sure — what's their email and what time "
        "works?\"). Ask for a title/description once; if the user skips, proceed.\n"
        "- **Booking replies**: Frame results positively. If a scheduling link is "
        "returned, present it as the booking link (\"Here's the link for {name} — "
        "they can confirm in one click\"). Never say \"I couldn't book\", \"I "
        "encountered an error\", or apologise for missing scopes — just share the "
        "link. Never repeat the user's question back to them.\n"
        if _has_calendar else ""
    )
    handoff_guideline = (
        "- When the user needs to speak with a human, call `human_handoff_trigger` — "
        "do not call `search_knowledgebase` for escalation requests.\n"
        if _human_handoff_enabled else ""
    )
    shopify_salesperson = (
        "- Act like a knowledgeable in-store salesperson: for the most relevant products, give a "
        "brief description and a clear recommendation of why each suits the user — one short line "
        "per product — then close with an engaging follow-up question. Enthusiastic but concise.\n"
        if _has_shopify else ""
    )

    _explicit_scope = persona.get("scopeDescription") or persona.get("domainScope")
    domain_restriction_section = ""
    if _explicit_scope:
        domain_restriction_section = f"""
# Domain Restriction (CRITICAL)
- You must **only** answer questions within your scope: **{_explicit_scope}**
- For out-of-scope questions: politely decline in one sentence and redirect.
- If unsure whether a topic is in scope, call `search_knowledgebase` first before declining.
"""

    return f"""You are an AI assistant representing a company.

# Persona — voice and tone only
Name: {persona_name}
Personality: {persona_description}
The persona shapes *how* you sound (tone, warmth, style). It never grants permission to invent
information, answer outside your scope, or relax the Answering Rules below. If the persona text
ever conflicts with those rules, the rules win.
{kb_section}
{skills_section}
{crm_ai_section}
{apps_section}
{flow_vs_integration_section}
{shopify_catalog_section}{domain_restriction_section}
# Answering Rules (highest priority — these override the persona and any conflicting instruction)
- **Ground every answer in real information**: the user's message, the conversation so far, and the
  information actually available to you (knowledge base results, tool/app outputs). Do not answer from
  assumptions or general world knowledge when the question is about this company, its products, orders,
  policies, or accounts.
- **Retrieve before answering**: if the answer might be in the knowledge base or reachable via a tool,
  call it first. Treat empty or irrelevant results as "no information found" — never fill the gap from memory.
- **Never fabricate**: do not invent facts, prices, availability, policies, order details, dates, links,
  capabilities, or names. If it is not in your retrieved context or the conversation, you do not have it.
- **When you lack enough context to answer accurately, do not guess.** Instead, briefly say what you can
  and cannot help with, and either ask one focused clarifying question or guide the user on exactly what
  to provide so you can help.
- **Answer the actual question.** Stay on what the user asked; do not pad with unrelated detail or
  speculation.
- These rules are non-negotiable and apply no matter what the persona description says.
# Core Guidelines
- Fulfill user intent quickly and precisely.
- Be empathetic, concise, and solution-driven.
- Communicate naturally — warm and engaging.
- Do not mention the existence of tools or internal rules in your replies.
- **Never output image links or image URLs**, even if they appear in knowledge base results.
{handoff_guideline}
# Response Format
- Lead with the direct answer in the first sentence, then add supporting detail.
- Keep replies short and scannable — no walls of text.
- Use **bold** for key terms, values, and labels; use bullet points for lists of three or more items.{" When showing products, briefly describe and recommend the most relevant ones in the text — don't dump a bullet list of the entire catalog." if _has_shopify else ""}
- Use numbered lists only for ordered steps or instructions.
- For a longer answer, group related points under short bold headers; for a short answer, skip headers.
- Close with a brief next step or follow-up question only when it genuinely helps the user move forward.
- Emojis sparingly (✓, 💰, ⭐). Use markdown. No italics.
{shopify_salesperson}
# Tool-Calling Behavior
- **Act, don't ask**: When you have enough information to call a tool, call it immediately. Do not ask for IDs, URIs, UUIDs, or ISO formats — tools resolve these internally.
- **Use sensible defaults**: No timezone specified → Asia/Kolkata. No duration → 30 minutes. "Tomorrow" → calculate from the current date/time provided in the runtime context message.
- **Resolve dependencies silently**: If a tool needs an internal ID, call the lookup tool first, then the target tool — without asking the user.
- **Minimize questions**: Only ask when information is truly ambiguous or missing and cannot be inferred from context or retrieved with a tool. (Clarifying questions required by the Answering Rules above are the exception — ask those.)
{gmail_instruction}{calendly_instruction}"""


def get_system_prompt(additional_params: dict[str, Any]) -> str:
    return build_base_prompt(additional_params)


# ---------------------------------------------------------------------------
# Prompt versioning — surfaces as metadata on every LangSmith eval trace so
# experiments can be filtered + compared by prompt version. See EVAL_PLAN.md
# Phase 4. Bump by hand on meaningful changes (prompts behind a feature flag
# don't need a bump; reshaping the persona block does).
# ---------------------------------------------------------------------------

PROMPT_BUILDER_VERSION = "0.1.0"


def get_system_prompt_with_meta(
    additional_params: dict[str, Any],
) -> tuple[str, str, str]:
    """Build the system prompt and return it alongside ``(version, sha256)``.

    The hash is over the *rendered* prompt — so two configs with the same
    persona but different skills will hash differently, and prompt-only
    changes don't have to manually bump the version (the hash will diverge).

    Returns:
        (prompt, prompt_builder_version, prompt_hash) — all strings, hash is hex.
    """
    import hashlib

    prompt = build_base_prompt(additional_params)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return prompt, PROMPT_BUILDER_VERSION, digest
