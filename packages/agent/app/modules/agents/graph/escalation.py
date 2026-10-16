import json
import logging
from typing import List

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.messages import HumanMessage as LCHumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.core.config import app_settings
from app.modules.agents.graph.escalation_prompt import ESCALATION_SYSTEM_PROMPT
from app.modules.agents.graph.nodes import GraphState, _get_model
from app.modules.common.handoff_client import trigger_escalation_handoff_api
from common.modules.interrupt_client import create_interrupt
from common.tools.providers.human_handoff.models import (
    EscalationCategory,
    EscalationConfig,
    EscalationResult,
)

logger = logging.getLogger(__name__)


class EscalationClassifier:
    """Analyzes conversation context and determines if human handoff is needed.

    Uses a separate lightweight LLM call (not the primary agent model) to
    minimize latency and keep escalation logic decoupled from the main agent.
    """

    async def classify(
        self,
        messages: List[BaseMessage],
        config: EscalationConfig,
    ) -> EscalationResult:
        conversation_text = self._format_conversation(messages)

        model_name = (config.model or "gemini/gemini-2.5-flash-lite").strip() or app_settings.DEFAULT_AGENT_MODEL
        model = _get_model(
            model_name=model_name,
            api_key=app_settings.OPENAI_API_KEY,
            base_url=app_settings.OPENAI_API_BASE,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

        classifier_messages = [
            SystemMessage(content=ESCALATION_SYSTEM_PROMPT),
            LCHumanMessage(
                content=f"Conversation to analyze:\n\n{conversation_text}"
            ),
        ]

        try:
            response = await model.ainvoke(classifier_messages)
            result_data = json.loads(response.content)
            return EscalationResult(**result_data)
        except Exception as e:
            logger.error("Escalation classifier failed: %s", e)
            return EscalationResult(
                should_escalate=False,
                confidence=0.0,
                reason=f"Classifier error: {e}",
                category=EscalationCategory.NONE,
            )

    def should_trigger_handoff(
        self,
        result: EscalationResult,
        config: EscalationConfig,
    ) -> bool:
        return (
            result.should_escalate
            and result.confidence >= config.threshold
            and result.category not in config.categories_to_skip
        )

    def _format_conversation(self, messages: List[BaseMessage]) -> str:
        """Format LangChain messages into readable text for classifier."""
        lines: List[str] = []
        for msg in messages:
            role = getattr(msg, "type", "unknown")
            content = getattr(msg, "content", "")
            if role == "system" or role == "tool":
                continue
            if role == "human":
                lines.append(f"Customer: {content}")
            elif role == "ai" and content:
                lines.append(f"AI Agent: {content}")
        return "\n".join(lines[-20:])


def get_escalation_config(configurable: dict) -> EscalationConfig:
    """Read escalation config from the handoff skill's config dict.

    The agent's handoff skill (AgentSkills row with toolName="human_handoff_trigger")
    carries an optional JSON config with escalation tuning. That config is extracted
    in graph.py and placed into configurable["escalation"]. Falls back to env-var
    defaults when keys are absent.

    Accepted config keys on the human_handoff skill (camelCase or snake_case):
      { "autoEscalate" / "auto_escalate" (or legacy "enabled"), "threshold", "model",
        "categoriesToSkip" / "categories_to_skip" }
    """
    human_handoff_enabled = configurable.get("human_handoff_enabled", False)
    escalation_cfg = configurable.get("escalation", {})
    if not isinstance(escalation_cfg, dict):
        escalation_cfg = {}

    categories_raw = (
        escalation_cfg.get("categoriesToSkip")
        or escalation_cfg.get("categories_to_skip")
        or []
    )

    # Auto-escalation LLM classifier (separate from the human_handoff tool / widget button).
    # Prefer `autoEscalate`; fall back to legacy flat `enabled` on the skill config for old rows.
    auto_escalate = escalation_cfg.get(
        "autoEscalate",
        escalation_cfg.get("auto_escalate", escalation_cfg.get("enabled", False)),
    )

    return EscalationConfig(
        enabled=bool(human_handoff_enabled) and bool(auto_escalate),
        threshold=escalation_cfg.get(
            "threshold",
            app_settings.ESCALATION_DEFAULT_THRESHOLD,
        ),
        model=escalation_cfg.get("model", app_settings.DEFAULT_AGENT_MODEL),
        categories_to_skip=[
            EscalationCategory(c) for c in categories_raw
        ],
    )


_classifier = EscalationClassifier()


async def escalation_check_node(
    state: GraphState,
    config: RunnableConfig,
) -> GraphState:
    """LangGraph node: runs escalation classifier on the final AI response.

    If escalation is detected and above threshold, calls the handoff API
    and emits a custom stream event. The AI response is always delivered
    regardless of escalation outcome.
    """
    configurable = config.get("configurable", {})
    esc_config = get_escalation_config(configurable)

    if not esc_config.enabled:
        logger.info(
            "escalation_check skipped: EscalationConfig.enabled=False "
            "(need human_handoff_enabled in graph + autoEscalate/auto_escalate/enabled in handoff skill JSON); "
            "human_handoff_enabled=%s",
            configurable.get("human_handoff_enabled"),
        )
        return state

    if configurable.get("is_preview"):
        logger.debug("escalation_check skipped: preview run")
        return state

    result = await _classifier.classify(state["messages"], esc_config)
    logger.info(
        "Escalation check: should_escalate=%s confidence=%.2f category=%s",
        result.should_escalate,
        result.confidence,
        result.category.value,
    )

    if _classifier.should_trigger_handoff(result, esc_config):
        chat_id = configurable.get("chat_id", "")
        session_id = configurable.get("session_id", "")
        user_id = configurable.get("user_id", "")
        agent_id = configurable.get("agent_id", "")
        reason = (
            f"[{result.category.value}] {result.reason} "
            f"(confidence: {result.confidence:.2f})"
        )
        success = await trigger_escalation_handoff_api(
            agent_chat_id=chat_id,
            session_id=session_id or "",
            agent_id=agent_id or "",
            reason=reason,
            user_id=user_id if user_id else None,
        )
        logger.info(
            "escalation_check: handoff API finished chat_id=%s success=%s category=%s",
            chat_id,
            success,
            result.category.value,
        )

        writer = get_stream_writer()
        writer(
            {
                "event": "escalation_detected",
                "should_escalate": True,
                "confidence": result.confidence,
                "category": result.category.value,
                "reason": result.reason,
                "handoff_triggered": success,
            }
        )

        if chat_id:
            try:
                await create_interrupt(
                    agent_chat_id=chat_id,
                    interrupt_type="escalation",
                    tool_display_name="Escalated to human",
                    status="auto",
                    escalation={
                        "category": result.category.value,
                        "confidence": float(result.confidence),
                        "reason": result.reason or "",
                    },
                )
            except Exception as e:
                logger.warning(
                    "persist escalation interrupt failed chat_id=%s: %s",
                    chat_id,
                    e,
                )

    return state
