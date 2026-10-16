"""Eval-only: ask the agent's own model to explain its tool selection.

Runs AFTER the agent run, inside the eval ``predict()`` path. Surfaces two
observability columns in the LangSmith experiment view —
``tool_selection_reasoning`` and ``prompt_rule_followed`` — to answer the
"know-why" question: why did the model pick this tool, and which prompt rule
drove it? Useful for spotting prompt gaps when routing is wrong.

Design constraints:
- **Eval-only.** Nothing here is imported by the production graph / ``/chat``
  path. The runner lazy-imports this module only when the explainer flag is on.
- **Same model as the agent.** We re-invoke the exact model the agent used for
  tool selection (resolved from the case config via the same precedence as
  ``llm_node``), giving it the rendered system prompt, the user query, and the
  tools actually called. This is the model's own (re-derived) rationale, not a
  third-party judge.
- **Surfaced via tracing.** The explainer is a ``@traceable`` span, so the
  ``{tool_selection_reasoning, prompt_rules_influenced}`` it returns shows up in
  each query's trace tree (alongside the agent's own LLM call) — read it there,
  not as an experiment column. ``prompt_rules_influenced`` is a list: a single
  run may call several tools, each driven by one or more rules.
- **Best-effort.** Any failure returns empty strings + a short note so an eval
  case never breaks because the explainer hiccuped.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langsmith import traceable

from app.core.config import app_settings

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")

_EXPLAINER_SYSTEM = (
    "You are analyzing why an AI support agent selected the tools it did. "
    "You are given the agent's full system prompt, the user's message, and the "
    "exact tools the agent called (it may call several tools in one run). "
    "Explain, from the agent's perspective, the reasoning across the whole tool "
    "selection (covering each tool called), then list EVERY rule or instruction "
    "in the system prompt that influenced the decision — there may be several "
    "(one per tool, or multiple rules behind a single tool).\n\n"
    "Reply with ONLY a JSON object:\n"
    '{"reasoning": "<2-5 sentences on why these tools (or no tool) were chosen '
    'for this query, covering each tool called>", '
    '"prompt_rules_influenced": ["<verbatim quote of an influencing rule from '
    'the system prompt>", "<another verbatim rule>", ...]}\n\n'
    "Each item in prompt_rules_influenced MUST be copied verbatim from the "
    "system prompt text. Include one entry per distinct rule that influenced any "
    "tool choice; use an empty array if no rule applies. If no tool was called, "
    "explain why the query did not match any tool's trigger rule."
)


def _format_tool_calls(tool_calls: list[dict[str, Any]] | None) -> str:
    """Render the agent's tool calls as a compact, readable list for the prompt."""
    if not tool_calls:
        return "(no tools were called)"
    lines: list[str] = []
    for tc in tool_calls:
        name = tc.get("name") or "(unknown)"
        args = tc.get("args")
        if args:
            try:
                args_str = json.dumps(args, ensure_ascii=False)
            except (TypeError, ValueError):
                args_str = str(args)
            lines.append(f"- {name}({args_str})")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


def _parse(content: str) -> dict[str, Any]:
    """Pull ``{reasoning, prompt_rules_influenced}`` out of a JSON response.

    Lenient about leading/trailing prose — grabs the first JSON object.
    ``prompt_rules_influenced`` is normalized to a list of non-empty strings
    (a model that returns a single string is wrapped into a one-element list).
    """
    match = _JSON_BLOCK_RE.search(content or "")
    if not match:
        raise ValueError(f"no JSON object in explainer response: {(content or '')[:200]!r}")
    parsed = json.loads(match.group(0))

    rules = parsed.get("prompt_rules_influenced")
    if isinstance(rules, str):
        rules = [rules]
    elif not isinstance(rules, list):
        rules = []
    rules = [str(r).strip() for r in rules if str(r).strip()]

    return {
        "reasoning": str(parsed.get("reasoning") or "").strip(),
        "prompt_rules_influenced": rules,
    }


def _trim_trace_inputs(inputs: dict) -> dict:
    """Keep the explainer span's logged inputs readable: drop the non-serializable
    model handle and the bulky prompt (the underlying LLM child run already shows
    the full prompt); keep the query + tool calls that make the span scannable.
    """
    return {
        "model_name": inputs.get("model_name"),
        "query": inputs.get("query"),
        "tool_calls": inputs.get("tool_calls"),
        "system_prompt_chars": len(inputs.get("system_prompt") or ""),
    }


@traceable(
    run_type="chain",
    name="tool_selection_reasoning",
    process_inputs=_trim_trace_inputs,
)
async def explain_tool_selection(
    *,
    model_name: str,
    system_prompt: str,
    query: str,
    tool_calls: list[dict[str, Any]] | None,
    model: Any = None,
) -> dict[str, Any]:
    """Ask the agent's own model to explain its tool selection.

    Decorated with ``@traceable``: when run inside a traced eval, this becomes a
    ``tool_selection_reasoning`` span whose **output** is the
    ``{tool_selection_reasoning, prompt_rules_influenced}`` dict — read it in
    each query's trace tree.

    Args:
        model_name: the model id the agent used (e.g. ``gpt-5.4-mini``). Used to
            build the LLM via the same ``_get_model`` path the graph uses.
        system_prompt: the rendered system prompt the agent saw — so the cited
            rules are grounded in the exact text.
        query: the user message for this case.
        tool_calls: the tools the agent actually called (``{name, args, ...}``);
            a single run may include several.
        model: optional pre-built LLM (used by tests to inject a fake); when
            ``None`` the agent's model is built from ``model_name``.

    Returns ``{"tool_selection_reasoning": str, "prompt_rules_influenced":
    list[str]}`` — the reasoning across all tool calls plus every verbatim
    system-prompt rule that influenced them. Best-effort: never raises.
    """
    try:
        if model is None:
            # Lazy import — the agent graph internals stay out of the import path
            # except when the explainer actually runs.
            from app.modules.agents.graph.nodes import _get_model

            model = _get_model(
                model_name=model_name,
                api_key=app_settings.OPENAI_API_KEY,
                base_url=app_settings.OPENAI_API_BASE,
                model_kwargs={"response_format": {"type": "json_object"}},
            )

        from langchain_core.messages import HumanMessage, SystemMessage

        user = (
            f"SYSTEM PROMPT GIVEN TO THE AGENT:\n{system_prompt}\n\n"
            f"USER MESSAGE:\n{query}\n\n"
            f"TOOLS THE AGENT ACTUALLY CALLED:\n{_format_tool_calls(tool_calls)}"
        )

        response = await model.ainvoke([
            SystemMessage(content=_EXPLAINER_SYSTEM),
            HumanMessage(content=user),
        ])
        content = getattr(response, "content", "") or ""
        if isinstance(content, list):
            # Some providers return list-of-blocks; join the text parts.
            content = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        parsed = _parse(content)
        return {
            "tool_selection_reasoning": parsed["reasoning"],
            "prompt_rules_influenced": parsed["prompt_rules_influenced"],
        }
    except Exception as e:  # noqa: BLE001 — observability only, never break a case
        logger.warning("explain_tool_selection failed: %s", e)
        return {
            "tool_selection_reasoning": f"(explainer failed: {type(e).__name__})",
            "prompt_rules_influenced": [],
        }
