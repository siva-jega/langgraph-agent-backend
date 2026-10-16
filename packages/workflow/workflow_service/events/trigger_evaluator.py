import logging
from typing import Any

logger = logging.getLogger(__name__)


class TriggerFilterEvaluator:
    """Evaluates whether an event matches a trigger's filter criteria."""

    def evaluate(self, filter_json: dict[str, Any] | None, event: dict[str, Any]) -> bool:
        if not filter_json:
            return True

        # agentId filter
        if "agentId" in filter_json:
            if filter_json["agentId"] != event.get("agentId"):
                logger.debug(
                    "Filter miss: agentId %s != %s",
                    filter_json["agentId"],
                    event.get("agentId"),
                )
                return False

        # channel filter
        if "channel" in filter_json:
            if filter_json["channel"].upper() != (event.get("channel") or "").upper():
                logger.debug(
                    "Filter miss: channel %s != %s",
                    filter_json["channel"],
                    event.get("channel"),
                )
                return False

        return True
