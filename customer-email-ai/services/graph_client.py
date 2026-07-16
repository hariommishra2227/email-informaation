"""Microsoft Graph client for Outlook read-only access."""

from __future__ import annotations

import logging
from typing import Any

import requests

import config
from models import OutlookMessage
from services import graph_auth
from services.email_processor import clean_html_to_text
from services.outlook_email_service import get_mock_message, list_mock_messages


LOGGER = logging.getLogger(__name__)
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
REQUEST_TIMEOUT_SECONDS = 20


def _headers(access_token: str) -> dict[str, str]:
    """Build Microsoft Graph request headers."""
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def get_current_user(user_id: str | None = None) -> dict[str, Any]:
    """Return the current Microsoft user, or the configured app user in mock mode."""
    if config.is_mock_mode():
        return {
            "id": user_id or config.DEFAULT_USER_ID,
            "mail": config.APP_USER_EMAIL,
            "userPrincipalName": config.APP_USER_EMAIL,
            "displayName": config.APP_USER_EMAIL,
        }

    token = graph_auth.get_valid_access_token()
    return _graph_get(f"{GRAPH_BASE_URL}/me", token)


def list_inbox_messages(user_id: str, limit: int = 50) -> list[OutlookMessage]:
    """Return Outlook inbox messages without marking them read."""
    limit = max(1, int(limit or 50))
    if config.is_mock_mode():
        return list_mock_messages(user_id, limit=limit)

    token = graph_auth.get_valid_access_token()
    next_url = (
        f"{GRAPH_BASE_URL}/me/mailFolders/inbox/messages"
        "?$select=id,subject,from,receivedDateTime,isRead,bodyPreview,body,hasAttachments"
        "&$orderby=receivedDateTime desc&$top=50"
    )
    messages: list[OutlookMessage] = []
    while next_url and len(messages) < limit:
        payload = _graph_get(next_url, token)
        for item in payload.get("value", []):
            if len(messages) >= limit:
                break
            sender = item.get("from", {}).get("emailAddress", {})
            body_info = item.get("body") or {}
            body = _body_to_text(str(body_info.get("content", "")), str(body_info.get("contentType", "")))
            messages.append(
                OutlookMessage(
                    message_id=item["id"],
                    user_id=user_id,
                    sender_name=sender.get("name", ""),
                    sender_email=sender.get("address", ""),
                    subject=item.get("subject", ""),
                    body=body,
                    body_preview=item.get("bodyPreview", ""),
                    received_datetime=item.get("receivedDateTime", ""),
                    is_read=bool(item.get("isRead")),
                    has_attachments=bool(item.get("hasAttachments")),
                    attachment_names=[],
                )
            )
        next_url = payload.get("@odata.nextLink")
    return messages


def get_message_body(user_id: str, message_id: str) -> str:
    """Return one Outlook message body."""
    if config.is_mock_mode():
        message = get_mock_message(user_id, message_id)
        return message.body if message else ""

    token = graph_auth.get_valid_access_token()
    payload = _graph_get(
        f"{GRAPH_BASE_URL}/me/messages/{message_id}?$select=body",
        token,
    )
    body = payload.get("body", {})
    return _body_to_text(str(body.get("content", "")), str(body.get("contentType", "")))


def list_message_attachments(user_id: str, message_id: str) -> list[str]:
    """Return attachment names only; do not download or modify attachments."""
    if config.is_mock_mode():
        message = get_mock_message(user_id, message_id)
        return list(message.attachment_names) if message else []

    token = graph_auth.get_valid_access_token()
    payload = _graph_get(
        f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments?$select=name",
        token,
    )
    return [str(item.get("name", "")) for item in payload.get("value", [])]


def _body_to_text(content: str, content_type: str) -> str:
    """Return readable text for plain-text or HTML Graph message bodies."""
    if content_type.lower() == "html" or "<html" in content.lower() or "<body" in content.lower():
        return clean_html_to_text(content)
    return content.strip()


def _graph_get(url: str, token: str) -> dict[str, Any]:
    """GET Microsoft Graph JSON and raise clear user-facing failures."""
    try:
        response = requests.get(url, headers=_headers(token), timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise RuntimeError(f"Graph API request failed: {exc}") from exc
    if response.status_code == 401:
        graph_auth.logout_user()
        raise RuntimeError("Your Microsoft session expired. Sign in again.")
    if response.status_code in {403, 65001}:
        raise RuntimeError("Microsoft Graph permission denied. Admin consent may be required.")
    if response.status_code == 404:
        raise RuntimeError("Mailbox unavailable for this Microsoft account.")
    if response.status_code >= 400:
        try:
            details = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            details = response.text
        raise RuntimeError(f"Graph API failure ({response.status_code}): {details}")
    return response.json()
