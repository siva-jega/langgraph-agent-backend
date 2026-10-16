"""Utility functions for Gmail integration."""

import asyncio
import base64
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from common.integration import get_token
from common.modules.token import TokenResponse
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError
from .models import GmailMessage, GmailThread
import html2text

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)


async def get_gmail_credentials(
    user_id: str, connectorId: str
) -> Optional[TokenResponse]:
    """Get Gmail credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="gmail",
            connectorId=connectorId,
            needs_refresh=True,
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting Gmail credentials: {str(e)}")
        return None


def build_gmail_service(access_token: str):
    """Build Gmail service instance from access token."""
    credentials = Credentials(token=access_token)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    reraise=True,
)
async def execute_gmail_request(request: Any) -> Any:
    """Execute Gmail API request with retry logic."""
    async with _semaphore:
        try:
            response = request.execute()
            logger.debug(f"Gmail API response received. Type: {type(response)}")
            return response
        except HttpError as e:
            status = e.resp.status if e.resp else None
            logger.error(f"Gmail API HttpError: status={status}, error={e}")
            if status in [429, 500, 502, 503, 504]:
                raise Exception(f"Gmail API server/quota error: {status} - {e}")
            if status in [401, 403]:
                raise ValueError("Gmail credentials expired")
            raise Exception(f"Gmail API error: {status} - {e}")
        except RetryError as e:
            # Extract underlying exception from RetryError
            underlying_error = e.last_attempt.exception() if e.last_attempt else e
            error_msg = f"RetryError after {e.last_attempt.attempt_number if e.last_attempt else 'unknown'} attempts: {underlying_error}"
            logger.error(f"Gmail API RetryError: {error_msg}")
            raise underlying_error if underlying_error else e
        except Exception as e:
            error_msg = (
                f"An unexpected error occurred with Gmail API: {type(e).__name__}: {e}"
            )
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg) from e


# Helper functions for parsing
def parse_message(message: Dict[str, Any], full_message: bool = False) -> GmailMessage:
    """Parse raw Gmail message into GmailMessage object."""
    headers = {
        header["name"]: header["value"]
        for header in message.get("payload", {}).get("headers", [])
    }

    payload = message.get("payload", {})
    body = ""

    if full_message:

        def extract_body(part):
            """Recursively extract the best body (prefer html, fallback to plain)."""
            if part.get("mimeType") == "text/html" and "data" in part.get("body", {}):
                try:
                    data = base64.urlsafe_b64decode(part["body"]["data"]).decode(
                        "utf-8"
                    )
                    markdown = html2text.html2text(data)
                    return markdown, "markdown", data
                except Exception:
                    return "", "markdown", None
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                try:
                    data = base64.urlsafe_b64decode(part["body"]["data"]).decode(
                        "utf-8"
                    )
                    return data, "plain", None
                except Exception:
                    return "", "plain", None
            if "parts" in part:
                htmlData = None
                plain = None
                for subpart in part["parts"]:
                    body, kind, raw = extract_body(subpart)
                    if kind == "markdown" and body:
                        htmlData = body
                    if kind == "plain" and body:
                        plain = body
                if htmlData:
                    return htmlData, "markdown", None
                if plain:
                    return plain, "plain", None
            return "", "plain", None

        # Extract message body (prefer html, fallback to plain)
        body, _, html_body = extract_body(payload)
    else:
        body = ""

    # Extract attachments
    attachments = []
    if "parts" in payload:
        for part in payload["parts"]:
            if "filename" in part and part["filename"]:
                attachments.append(
                    {
                        "id": part["body"].get("attachmentId"),
                        "filename": part["filename"],
                        "mimeType": part["mimeType"],
                        "size": part["body"].get("size", 0),
                    }
                )

    return GmailMessage(
        id=message["id"],
        thread_id=message["threadId"],
        subject=headers.get("Subject", ""),
        sender=headers.get("From", ""),
        recipients=headers.get("To", "").split(",") if headers.get("To") else [],
        body=body,
        date=headers.get("Date", ""),
        labels=message.get("labelIds", []),
        attachments=attachments if attachments else None,
    )


def parse_thread(thread: Dict[str, Any], include_messages: bool = True) -> GmailThread:
    """Parse raw Gmail thread into GmailThread object."""
    messages = []
    participants = set()
    subject = ""
    last_date = ""
    all_labels = set()
    has_attachments = False

    if include_messages and "messages" in thread:
        for msg_data in thread["messages"]:
            parsed_msg = parse_message(msg_data, full_message=True)
            messages.append(parsed_msg)

            # Extract participants
            if parsed_msg.sender:
                participants.add(parsed_msg.sender)
            participants.update(parsed_msg.recipients)

            # Get subject from first message
            if not subject and parsed_msg.subject:
                subject = parsed_msg.subject

            # Track latest date
            if parsed_msg.date:
                last_date = parsed_msg.date

            # Collect all labels
            all_labels.update(parsed_msg.labels)

            # Check for attachments
            if parsed_msg.attachments:
                has_attachments = True

    return GmailThread(
        id=thread["id"],
        snippet=thread.get("snippet", ""),
        history_id=thread.get("historyId", ""),
        messages=messages,
        subject=subject,
        participants=list(participants),
        message_count=len(messages),
        last_message_date=last_date,
        labels=list(all_labels),
        has_attachments=has_attachments,
    )


async def get_full_message(
    gmail_service: Any, message_id: str
) -> Optional[GmailMessage]:
    """Get full message details with error handling."""
    try:
        request = (
            gmail_service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
        )
        full_msg = await execute_gmail_request(request)
        return parse_message(full_msg, full_message=True)
    except Exception as e:
        logger.error(f"Error getting full message {message_id}: {str(e)}")
        return None


async def get_partial_message(
    gmail_service: Any, message_id: str
) -> Optional[GmailMessage]:
    """Get partial message details with error handling (metadata only)."""
    try:
        request = (
            gmail_service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata")
        )
        partial_msg = await execute_gmail_request(request)
        return parse_message(partial_msg, full_message=False)
    except Exception as e:
        logger.error(f"Failed to get message {message_id}: {str(e)}")
        return None


async def get_full_thread(gmail_service: Any, thread_id: str) -> Optional[GmailThread]:
    """Get full thread details with error handling."""
    try:
        request = (
            gmail_service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
        )
        full_thread = await execute_gmail_request(request)
        return parse_thread(full_thread, include_messages=True)
    except Exception as e:
        logger.error(f"Error getting full thread {thread_id}: {str(e)}")
        return None


async def get_partial_thread(
    gmail_service: Any, thread_id: str
) -> Optional[GmailThread]:
    """Get partial thread details with error handling (metadata only)."""
    try:
        request = (
            gmail_service.users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata")
        )
        partial_thread = await execute_gmail_request(request)
        return parse_thread(partial_thread, include_messages=False)
    except Exception as e:
        logger.error(f"Failed to get thread {thread_id}: {str(e)}")
        return None
