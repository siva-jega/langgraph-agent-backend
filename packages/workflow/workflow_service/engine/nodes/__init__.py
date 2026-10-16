from workflow_service.engine.nodes.condition import ConditionStepExecutor
from workflow_service.engine.nodes.connector import ConnectorStepExecutor
from workflow_service.engine.nodes.http_request import HttpRequestStepExecutor
from workflow_service.engine.nodes.identity_trigger import IdentityTriggerExecutor
from workflow_service.engine.nodes.send_message import SendMessageStepExecutor
from workflow_service.engine.nodes.wait import WaitStepExecutor


class EndStepExecutor:
    async def execute(self, config: dict, context: dict) -> dict:
        return {}


STEP_EXECUTORS: dict[str, type] = {
    # Trigger nodes — all return run.inputData as their output
    "WEBHOOK": IdentityTriggerExecutor,
    "SCHEDULED": IdentityTriggerExecutor,
    "MANUAL": IdentityTriggerExecutor,
    "FORM_SUBMISSION": IdentityTriggerExecutor,
    "APP": IdentityTriggerExecutor,
    "CONVERSATION_EVENT": IdentityTriggerExecutor,
    # Action nodes
    "WAIT": WaitStepExecutor,
    "HTTP_REQUEST": HttpRequestStepExecutor,
    "SEND_MESSAGE": SendMessageStepExecutor,
    "CONDITION": ConditionStepExecutor,
    "CONNECTOR": ConnectorStepExecutor,
    "END": EndStepExecutor,
}
