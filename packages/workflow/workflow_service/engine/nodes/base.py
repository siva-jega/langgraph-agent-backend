from typing import Any, Protocol


class StepExecutor(Protocol):
    async def execute(
        self, config: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a workflow step and return output data."""
        ...
