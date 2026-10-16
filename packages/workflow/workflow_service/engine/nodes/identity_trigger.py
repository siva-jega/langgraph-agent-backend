from typing import Any


class IdentityTriggerExecutor:
    """Trigger nodes pass run.inputData directly as their output.

    The DAG pre-seeds context["__run__"]["inputData"] before execution.
    This executor reads it and returns it unchanged, making trigger output
    available downstream via {{ $<triggerNodeId>.output.<path> }}.
    """

    async def execute(
        self, config: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        input_data = context.get("__run__", {}).get("inputData") or {}
        return input_data if isinstance(input_data, dict) else {}
