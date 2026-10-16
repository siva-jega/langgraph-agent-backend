import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from common.project_settings import project_settings
from common.modules.email.service import EmailService
from workflow_service.engine.expression import resolve_expression
from workflow_service.core.config import app_settings

logger = logging.getLogger(__name__)


class NodeDeliveryError(Exception):
    """A send the provider ACCEPTED but did NOT deliver (e.g. WhatsApp 131049).

    Raised so the engine marks the node FAILED instead of COMPLETED. Carries the
    structured node output so the FAILED step still records the message id and
    the delivery failure reason.
    """

    def __init__(self, message: str, output: dict):
        super().__init__(message)
        self.output = output


def _base_url() -> str:
    s = project_settings()
    return (s.NESTJS_URL or "").rstrip("/")


def _secret() -> str:
    s = project_settings()
    return s.INTERNAL_SECRET or ""


def _extract_session_id(context: dict) -> str:
    """Find sessionId from workflow execution context.

    App triggers store the payload as inputData = {appSlug, eventType, data: {sessionId, ...}}.
    Manual/simple triggers may store it flat as inputData = {sessionId, ...}.
    Falls back to scanning node outputs as a last resort.
    """
    run_input = context.get("__run__", {}).get("inputData") or {}

    # App trigger: payload nested under 'data'
    sid = (run_input.get("data") or {}).get("sessionId")
    if sid:
        return str(sid)

    # Simple/manual trigger: payload at top level
    sid = run_input.get("sessionId")
    if sid:
        return str(sid)

    # Fallback: scan node outputs
    for node_data in context.values():
        if not isinstance(node_data, dict):
            continue
        output = node_data.get("output") or {}
        if not isinstance(output, dict):
            continue
        sid = output.get("sessionId") or (output.get("data") or {}).get("sessionId")
        if sid:
            return str(sid)

    return ""


class SendMessageStepExecutor:
    def __init__(self):
        self._email_service: EmailService | None = None

    def _get_email_service(self) -> EmailService:
        if self._email_service is None:
            self._email_service = EmailService(app_settings)
        return self._email_service

    async def execute(
        self, config: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        channel = config.get("channel", "META").upper()
        # Support both old (recipientId/contactIdExpr) and new (to) field names
        recipient_id = resolve_expression(
            config.get("to") or config.get("contactIdExpr") or config.get("recipientId", ""),
            context,
        )
        # Support both old (content) and new (message) field names
        content = resolve_expression(
            config.get("message") or config.get("content", ""),
            context,
        )
        message_type = config.get("messageType", "text")

        # WhatsApp template binding: dispatch through user-backend's workflow
        # dispatcher, which resolves the recipient from sessionId, validates the
        # approved binding + mapping, builds the Meta payload, sends, and
        # classifies the outcome. The node carries only the binding + sessionId +
        # named data; the DAG injects the run/node/workspace identity.
        if channel == "WHATSAPP":
            base = _base_url()
            secret = _secret()
            endpoint = f"{base}/internal/meta/workflow/dispatch"
            session_id = config.get("sessionId") or _extract_session_id(context)
            binding_id = config.get("agentIntegrationAssetId", "")
            payload = {
                "workspaceId": config.get("workspaceId", ""),
                "workflowRunId": config.get("workflowRunId", ""),
                "nodeId": config.get("nodeId", ""),
                "agentIntegrationAssetId": binding_id,
                "agentIntegrationId": config.get("agentIntegrationId", ""),
                "sessionId": session_id,
                # Already expression-resolved by the DAG (resolve_dict recurses).
                "data": config.get("data") or {},
            }
            if not binding_id:
                payload["message"] = resolve_expression(
                    config.get("message", ""), context
                )

            logger.info(
                "SEND_MESSAGE via WHATSAPP binding=%s session=%s freeform=%s",
                binding_id,
                session_id,
                not binding_id,
            )

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers={"x-internal-secret": secret},
                )
                response.raise_for_status()
                resp_data = response.json()

            # user-backend wraps responses as { status, statusCode, result, version }.
            outcome = (
                resp_data.get("result", resp_data)
                if isinstance(resp_data, dict)
                else resp_data
            )
            outcome_dict = outcome if isinstance(outcome, dict) else {}
            message_id = outcome_dict.get("messageId", "")
            # `delivered` reflects Meta's async delivery webhook, captured by a
            # bounded wait in user-backend: True = delivered, False = accepted but
            # NOT delivered (see failureReason), None = still pending at timeout.
            delivered = outcome_dict.get("delivered")
            failure_code = outcome_dict.get("failureCode")
            failure_reason = outcome_dict.get("failureReason")

            if delivered is False:
                node_status = "failed"
            elif delivered is True:
                node_status = "delivered"
            elif outcome_dict.get("status") == "sent":
                node_status = "accepted"
            else:
                node_status = outcome_dict.get("status") or "unknown"

            result = {
                "outcome": outcome,
                "status": node_status,
                "delivered": delivered,
                "messageId": message_id or "",
                "failureCode": failure_code,
                "failureReason": failure_reason,
                "sentAt": datetime.now(timezone.utc).isoformat(),
            }

            # Accepted by Meta but NOT delivered -> fail the node (don't let the
            # run continue as if the message went out). The structured output
            # rides along so the FAILED step keeps the reason.
            if delivered is False:
                logger.warning(
                    "SEND_MESSAGE WHATSAPP not delivered messageId=%s code=%s reason=%s",
                    message_id,
                    failure_code,
                    failure_reason,
                )
                raise NodeDeliveryError(
                    f"WhatsApp message not delivered"
                    f" (code {failure_code}): {failure_reason}",
                    output=result,
                )

            return result

        # Gmail: connected account (customer's OAuth) → dispatch through user-backend;
        # platform sender (no agentIntegrationId) → fall back to Resend.
        if channel == "GMAIL":
            subject = config.get("subject", "Workflow Notification")
            if subject:
                subject = resolve_expression(subject, context)

            agent_integration_id = config.get("agentIntegrationId", "")

            if agent_integration_id:
                base = _base_url()
                secret = _secret()
                endpoint = f"{base}/internal/gmail/workflow/dispatch"
                html_content = content.replace("\n", "<br>")

                logger.info(
                    "SEND_MESSAGE via connected GMAIL agentIntegrationId=%s to=%s",
                    agent_integration_id,
                    recipient_id,
                )

                payload = {
                    "workspaceId": config.get("workspaceId", ""),
                    "workflowRunId": config.get("workflowRunId", ""),
                    "nodeId": config.get("nodeId", ""),
                    "agentIntegrationId": agent_integration_id,
                    "to": recipient_id,
                    "subject": subject,
                    "body": html_content,
                }

                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        endpoint,
                        json=payload,
                        headers={"x-internal-secret": secret},
                    )
                    response.raise_for_status()
                    resp_data = response.json()

                result_data = (
                    resp_data.get("result", resp_data)
                    if isinstance(resp_data, dict)
                    else resp_data
                )
                result_dict = result_data if isinstance(result_data, dict) else {}
                return {
                    "messageId": result_dict.get("messageId", ""),
                    "sentAt": result_dict.get("sentAt", datetime.now(timezone.utc).isoformat()),
                    "fromAddress": result_dict.get("fromAddress", ""),
                }

            # Platform sender — no connected account, use Resend
            email_service = self._get_email_service()
            html_content = content.replace("\n", "<br>")

            logger.info("SEND_MESSAGE via platform GMAIL (Resend) to %s", recipient_id)

            result = await email_service.send_email(
                to=recipient_id,
                subject=subject,
                body=html_content,
            )

            return {
                "messageId": result.get("id", "") if result else "",
                "sentAt": datetime.now(timezone.utc).isoformat(),
            }

        # WIDGET: push a proactive message to the web widget session
        if channel == "WIDGET":
            # Auto-infer sessionId from trigger payload; allow explicit override via `to`
            session_id = recipient_id or _extract_session_id(context)
            cart_url = resolve_expression(config.get("cartUrl", ""), context) or None
            base = _base_url()
            secret = _secret()
            endpoint = f"{base}/internal/widget/send-message"

            logger.info("SEND_MESSAGE via WIDGET to session %s", session_id)

            body: dict = {"sessionId": session_id, "message": content}
            if cart_url:
                body["cartUrl"] = cart_url

            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    endpoint,
                    json=body,
                    headers={"x-internal-secret": secret},
                )
                response.raise_for_status()
                resp_data = response.json()

            return {
                "messageId": resp_data.get("chatId", ""),
                "sentAt": resp_data.get("sentAt", datetime.now(timezone.utc).isoformat()),
            }

        # Other channels (META): call NestJS backend
        # TODO(workflow): legacy free-text WhatsApp path. No longer offered in the
        # editor (superseded by the WHATSAPP template channel). Remove once no
        # ACTIVE workflow nodes reference channel == "META".
        base = _base_url()
        secret = _secret()
        endpoint = f"{base}/internal/meta/send"

        payload = {
            "recipientId": recipient_id,
            "messageType": message_type,
            "content": content,
        }

        # Add any extra config fields
        template_key = config.get("templateLogicalKey")
        if template_key:
            payload["templateLogicalKey"] = template_key

        logger.info("SEND_MESSAGE via %s to %s", channel, recipient_id)

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"x-internal-secret": secret},
            )
            response.raise_for_status()
            resp_data = response.json()

        return {
            "messageId": resp_data.get("messageId", ""),
            "sentAt": datetime.now(timezone.utc).isoformat(),
        }
