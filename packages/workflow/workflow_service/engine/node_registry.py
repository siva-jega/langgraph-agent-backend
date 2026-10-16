from __future__ import annotations

from typing import Any, TypedDict


class FieldDef(TypedDict, total=False):
    key: str
    type: str       # "string" | "number" | "boolean" | "object" | "array" | "unknown"
    required: bool
    label: str
    description: str
    default: Any
    options: list[str]
    injected: bool  # true for system-injected fields hidden in the UI


class NodeDefinition(TypedDict):
    id: str           # matches node type string, e.g. "WEBHOOK", "HTTP_REQUEST"
    category: str     # "TRIGGER" | "ACTION" | "CONDITION" | "CONNECTOR"
    label: str
    description: str

    # Merged input shape: user-configurable params + any system-injected fields.
    # Injected fields are marked with injected=true and hidden in the editor UI.
    input_parameters: list[FieldDef]

    # Output field definitions. value is null at design time; executor fills it at runtime.
    output_parameters: list[FieldDef]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

NODE_REGISTRY: dict[str, NodeDefinition] = {

    # ── Triggers ────────────────────────────────────────────────────────────

    "CONVERSATION_EVENT": {
        "id": "CONVERSATION_EVENT",
        "category": "TRIGGER",
        "label": "Message Received",
        "description": "Fires when a conversation event (inbound message) is received for an agent.",
        "input_parameters": [
            {"key": "agentId",  "type": "string", "required": False,
             "label": "Agent ID",
             "description": "Scope to a specific agent. Leave empty to match all agents."},
            {"key": "channel",  "type": "string", "required": False,
             "label": "Channel",
             "description": "Scope to a specific channel (e.g. whatsapp, instagram). Leave empty to match all."},
        ],
        "output_parameters": [
            {"key": "workspaceId",    "type": "string", "description": "Workspace the conversation belongs to."},
            {"key": "conversationId", "type": "string", "description": "ID of the conversation."},
            {"key": "agentId",        "type": "string", "description": "Agent handling the conversation."},
            {"key": "channel",        "type": "string", "description": "Channel the message arrived on."},
        ],
    },

    "MANUAL": {
        "id": "MANUAL",
        "category": "TRIGGER",
        "label": "Manual",
        "description": "Triggered manually by a user or via API. Accepts an arbitrary input payload.",
        "input_parameters": [],
        "output_parameters": [
            {"key": "inputData", "type": "object",
             "description": "Arbitrary payload supplied at trigger time."},
        ],
    },

    "SCHEDULED": {
        "id": "SCHEDULED",
        "category": "TRIGGER",
        "label": "Scheduled",
        "description": "Runs on a cron-based schedule. Evaluated every minute; distributed lock prevents double-firing.",
        "input_parameters": [
            {"key": "cronExpression", "type": "string", "required": True,
             "label": "Cron Expression",
             "description": "Standard cron expression, e.g. '0 9 * * MON' for every Monday at 09:00."},
            {"key": "timezone", "type": "string", "required": False, "default": "UTC",
             "label": "Timezone",
             "description": "IANA timezone string, e.g. 'America/New_York'. Defaults to UTC."},
        ],
        "output_parameters": [
            {"key": "firedAt",     "type": "string", "description": "ISO-8601 timestamp when this run was triggered."},
            {"key": "triggerType", "type": "string", "description": "Always 'SCHEDULED'."},
        ],
    },

    "WEBHOOK": {
        "id": "WEBHOOK",
        "category": "TRIGGER",
        "label": "Webhook",
        "description": "Triggered by any external HTTP POST to the workflow's unique webhook URL.",
        "input_parameters": [],
        "output_parameters": [],
    },

    "APP": {
        "id": "APP",
        "category": "TRIGGER",
        "label": "App Event",
        "description": "Fires when a connected app raises an event (e.g. Shopify order.created).",
        "input_parameters": [
            {"key": "appSlug",    "type": "string", "required": True,
             "label": "App", "description": "Provider slug, e.g. 'shopify'."},
            {"key": "eventType",  "type": "string", "required": True,
             "label": "Event Type", "description": "Event identifier, e.g. 'order.created'."},
            {"key": "accountRef", "type": "string", "required": False,
             "label": "Account Reference",
             "description": "Optional account reference to scope to a specific connected account."},
        ],
        "output_parameters": [
            {"key": "appSlug",   "type": "string", "description": "Provider slug of the app that fired the event."},
            {"key": "eventType", "type": "string", "description": "Event type that was fired."},
            {"key": "data",      "type": "object", "description": "Raw event payload from the app."},
        ],
    },

    "FORM_SUBMISSION": {
        "id": "FORM_SUBMISSION",
        "category": "TRIGGER",
        "label": "Form Submission",
        "description": "Fires when a user submits a linked form.",
        "input_parameters": [
            {"key": "formDefinitionId", "type": "string", "required": False,
             "label": "Form",
             "description": "ID of the form to listen on. Set automatically when you select a form."},
            {"key": "formName",         "type": "string", "required": False,
             "label": "Form Name", "description": "Display name of the form."},
            {"key": "formDescription",  "type": "string", "required": False,
             "label": "Description", "description": "Description shown on the public form page."},
            {"key": "formFields",       "type": "array",  "required": False,
             "label": "Fields",
             "description": "Field definitions for the form (key, label, type, required, options)."},
        ],
        "output_parameters": [
            {"key": "submittedAt", "type": "string", "description": "ISO-8601 timestamp of submission."},
            {"key": "_meta",       "type": "object", "description": "Submission metadata: { formDefinitionId, submissionId }."},
        ],
    },

    # ── Action nodes ────────────────────────────────────────────────────────

    "WAIT": {
        "id": "WAIT",
        "category": "ACTION",
        "label": "Wait",
        "description": "Pauses the workflow for a fixed duration or until a specific time. Capped at 300 seconds.",
        "input_parameters": [
            {"key": "durationMs", "type": "number", "required": False,
             "label": "Duration (ms)",
             "description": "How long to wait in milliseconds (max 300 000). Mutually exclusive with resumeAt."},
            {"key": "resumeAt",   "type": "string", "required": False,
             "label": "Resume At",
             "description": "ISO-8601 datetime to resume at. Mutually exclusive with durationMs."},
        ],
        "output_parameters": [
            {"key": "waited",     "type": "boolean", "description": "Always true — confirms the wait completed."},
            {"key": "durationMs", "type": "number",  "description": "Actual milliseconds slept."},
        ],
    },

    "HTTP_REQUEST": {
        "id": "HTTP_REQUEST",
        "category": "ACTION",
        "label": "HTTP Request",
        "description": "Makes an outbound HTTP request. Raises an error on 4xx/5xx responses.",
        "input_parameters": [
            {"key": "url",          "type": "string",  "required": True,
             "label": "URL", "description": "Request URL. Supports {{ }} expressions."},
            {"key": "method",       "type": "string",  "required": False, "default": "GET",
             "label": "Method", "description": "HTTP method.",
             "options": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            {"key": "headers",      "type": "object",  "required": False,
             "label": "Headers", "description": "Key-value map of request headers."},
            {"key": "bodyTemplate", "type": "unknown", "required": False,
             "label": "Body", "description": "Request body. Supports {{ }} expressions."},
            {"key": "timeoutMs",    "type": "number",  "required": False, "default": 10000,
             "label": "Timeout (ms)", "description": "Request timeout in milliseconds."},
        ],
        "output_parameters": [
            {"key": "*", "type": "unknown", "description": "Response body fields are available directly (e.g. output.fieldName)."},
        ],
    },

    "SEND_MESSAGE": {
        "id": "SEND_MESSAGE",
        "category": "ACTION",
        "label": "Send Message",
        "description": "Sends a message via email (Gmail/Resend), WhatsApp/Meta, an approved WhatsApp template, or the web widget.",
        "input_parameters": [
            {"key": "channel",            "type": "string", "required": True,
             "label": "Channel", "default": "",
             "options": ["WHATSAPP", "WIDGET", "GMAIL"],
             "description": "Delivery channel, derived from the selected sender: 'WHATSAPP' (approved template via a connected account), 'WIDGET' (web widget), or 'GMAIL' (email). 'META' free-text is legacy/no longer offered in the editor."},
            {"key": "to",                 "type": "string", "required": False,
             "label": "To", "description": "Recipient address or ID. Supports {{ }} expressions. Not required for WIDGET (sessionId is inferred from trigger output)."},
            {"key": "cartUrl",            "type": "string", "required": False,
             "label": "Cart URL",
             "description": "Optional cart URL for WIDGET channel. When provided, a small CTA button is shown in the popup alongside the message."},
            {"key": "message",            "type": "string", "required": False,
             "label": "Message",
             "description": "Message body. Required for GMAIL, META and WIDGET; not used by WHATSAPP (which sends an approved template via the selected binding). Supports {{ }} expressions."},
            {"key": "subject",            "type": "string", "required": False,
             "label": "Subject", "default": "Workflow Notification",
             "description": "Email subject line (GMAIL only)."},
            {"key": "messageType",        "type": "string", "required": False,
             "label": "Message Type", "default": "text",
             "description": "Meta message type (META only), e.g. 'text' or 'template'."},
            {"key": "templateLogicalKey", "type": "string", "required": False,
             "label": "Template Key",
             "description": "Template logical key for template messages (META only)."},
            {"key": "agentIntegrationId", "type": "string", "required": False,
             "label": "Sending Account",
             "description": "Connected sending account (AgentIntegration) the node sends as. Set for connected channels (e.g. the chosen WhatsApp number); empty for platform senders (Web Widget, system Email)."},
            {"key": "agentIntegrationAssetId", "type": "string", "required": False,
             "label": "WhatsApp Template Binding",
             "description": "WHATSAPP only: selected approved WhatsApp template binding (AgentIntegrationAsset). The recipient resolves from sessionId and the named `data` map is mapped onto the template by user-backend."},
            {"key": "sessionId",          "type": "string", "required": False,
             "label": "Session ID",
             "description": "WHATSAPP only: pixel session id used to resolve the recipient phone. Supports {{ }} expressions."},
            {"key": "data",               "type": "object", "required": False,
             "label": "Template Data",
             "description": "WHATSAPP only: flat map of named values mapped onto the approved template. Supports {{ }} expressions."},
        ],
        "output_parameters": [
            {"key": "messageId", "type": "string", "description": "Provider message ID after successful send."},
            {"key": "sentAt",    "type": "string", "description": "ISO-8601 timestamp of when the message was sent."},
        ],
    },

    "END": {
        "id": "END",
        "category": "ACTION",
        "label": "End",
        "description": "Terminates this branch. Use to close off a condition branch that should do nothing.",
        "input_parameters": [],
        "output_parameters": [],
    },

    "CONDITION": {
        "id": "CONDITION",
        "category": "CONDITION",
        "label": "Condition",
        "description": (
            "Evaluates one or more rules and routes execution down the TRUE or FALSE branch. "
            "Rules are combined with AND (all must pass) or OR (any must pass)."
        ),
        "input_parameters": [
            {"key": "conditions", "type": "object", "required": True,
             "label": "Conditions",
             "description": (
                 "Structured condition rules: { combinator: 'AND'|'OR', rules: [{left, operator, right}] }. "
                 "Operators: equals, not_equals, gt, gte, lt, lte, contains, not_contains, "
                 "starts_with, ends_with, matches_regex, is_null, is_not_null, "
                 "is_empty, is_not_empty, is_true, is_false, length_eq, length_gt, length_lt."
             )},
        ],
        "output_parameters": [
            {"key": "result", "type": "boolean",
             "description": "True if conditions pass; false otherwise. Controls branching."},
        ],
    },

    "CONNECTOR": {
        "id": "CONNECTOR",
        "category": "CONNECTOR",
        "label": "Connector",
        "description": (
            "Calls a tool from the shared integration tools registry (Shopify, Slack, HubSpot, etc.). "
            "Per-tool parameter schemas are available via GET /api/tools/all."
        ),
        "input_parameters": [
            {"key": "provider", "type": "string", "required": True,
             "label": "Provider", "description": "Provider slug, e.g. 'shopify'."},
            {"key": "action",   "type": "string", "required": True,
             "label": "Action", "description": "Tool action name, e.g. 'get_order_status'."},
            {"key": "params",   "type": "object", "required": False,
             "label": "Parameters",
             "description": "Tool arguments. String values support {{ }} expressions."},
            {"key": "workspaceId", "type": "string", "required": False, "injected": True,
             "description": "Injected by the DAG executor from the workflow definition."},
            {"key": "userId",      "type": "string", "required": False, "injected": True,
             "description": "Injected by the DAG executor. Owner of the workflow."},
            {"key": "apps",        "type": "array",  "required": False, "injected": True,
             "description": "Connected app credentials, fetched from NestJS if not pre-populated."},
        ],
        # Output shape depends on the selected tool. Snapshot stored on WorkflowNode.outputParameters.
        "output_parameters": [
            {"key": "result", "type": "unknown",
             "description": "Tool output — shape depends on the selected provider and action."},
        ],
    },
}


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_node(node_id: str) -> NodeDefinition | None:
    return NODE_REGISTRY.get(node_id)


def get_all_nodes() -> list[NodeDefinition]:
    return list(NODE_REGISTRY.values())


def get_nodes_by_category(category: str) -> list[NodeDefinition]:
    return [n for n in NODE_REGISTRY.values() if n["category"] == category]
