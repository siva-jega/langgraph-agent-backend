import logging
from typing import Any

import httpx

from workflow_service.engine.expression import resolve_expression, resolve_dict

logger = logging.getLogger(__name__)


class HttpStepError(Exception):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}")


class HttpRequestStepExecutor:
    async def execute(
        self, config: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        method = config.get("method", "GET").upper()
        url = resolve_expression(config.get("url", ""), context)
        headers = resolve_dict(config.get("headers") or {}, context)
        timeout_ms = config.get("timeoutMs", 10000)
        timeout_s = timeout_ms / 1000

        body_template = config.get("bodyTemplate")
        body = None
        if body_template:
            if isinstance(body_template, dict):
                body = resolve_dict(body_template, context)
            else:
                body = resolve_expression(body_template, context)

        logger.info("HTTP_REQUEST: %s %s", method, url)

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            if isinstance(body, dict):
                response = await client.request(method, url, headers=headers, json=body)
            elif body:
                response = await client.request(method, url, headers=headers, content=str(body))
            else:
                response = await client.request(method, url, headers=headers)

        # Parse response body
        try:
            response_body = response.json()
        except Exception:
            response_body = response.text

        if response.status_code >= 400:
            raise HttpStepError(response.status_code, response_body)

        return response_body if isinstance(response_body, dict) else {"body": response_body}
