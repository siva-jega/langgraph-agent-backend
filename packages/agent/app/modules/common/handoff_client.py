"""Re-export from shared — tool providers also depend on these helpers."""

from common.modules.handoff_client import (
    check_active_handoff_api,
    trigger_escalation_handoff_api,
    trigger_handoff_api,
)

__all__ = [
    "check_active_handoff_api",
    "trigger_escalation_handoff_api",
    "trigger_handoff_api",
]
