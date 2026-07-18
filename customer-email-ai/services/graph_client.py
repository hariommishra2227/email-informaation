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
        f"{GRAPH_BASE_URL}/me/messages"
        "?$select=id,subject,from,receivedDateTime,bodyPreview,body,isRead,hasAttachments,webLink"
        "&$orderby=receivedDateTime desc&$top=50"
    )
    messages: list[OutlookMessage] = []
    while next_url and len(messages) < limit:
        payload = _graph_get(next_url, token)
        for item in payload.get("value", []):
            if len(messages) >= limit:
                break
            message_id = str(item.get("id") or "").strip()
            if not message_id:
                LOGGER.warning("Graph message without id was skipped.")
                continue
            sender = (item.get("from") or {}).get("emailAddress", {}) or {}
            body_info = item.get("body") or {}
            body = _body_to_text(str(body_info.get("content", "")), str(body_info.get("contentType", "")))
            messages.append(
                OutlookMessage(
                    message_id=message_id,
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


def _graph_get(url: str, token: str, retry_on_unauthorized: bool = True) -> dict[str, Any]:
    """GET Microsoft Graph JSON and raise clear user-facing failures."""
    try:
        response = requests.get(url, headers=_headers(token), timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        LOGGER.warning("Microsoft Graph network request failed: %s", exc.__class__.__name__)
        raise RuntimeError("Network failure while contacting Microsoft Graph.") from exc
    if response.status_code == 401 and retry_on_unauthorized:
        renewed_token = graph_auth.acquire_token_silent_once(force_refresh=True, clear_on_failure=True)
        if renewed_token:
            return _graph_get(url, renewed_token, retry_on_unauthorized=False)
    if response.status_code == 401:
        graph_auth.logout_user()
        raise RuntimeError("Your Microsoft session expired. Sign in again.")
    if response.status_code == 403:
        raise RuntimeError("Microsoft Graph permission denied. Mail.Read or admin consent may be required.")
    if response.status_code == 404:
        raise RuntimeError("Mailbox unavailable for this Microsoft account.")
    if response.status_code >= 400:
        try:
            error = response.json().get("error", {})
            details = str(error.get("message") or error.get("code") or response.reason)
        except ValueError:
            details = response.reason
        LOGGER.warning("Microsoft Graph returned HTTP %s: %s", response.status_code, details)
        raise RuntimeError(_friendly_graph_error(response.status_code, details))
    return response.json()


def _friendly_graph_error(status_code: int, details: str) -> str:
    """Map Graph failures to safe messages for the Streamlit UI."""
    lower = details.lower()
    if "mail.read" in lower or "permission" in lower or "forbidden" in lower:
        return "Microsoft Graph Mail.Read permission is missing or not approved."
    if "invalidtenant" in lower or "tenant" in lower:
        return "The configured Microsoft tenant is invalid for this account."
    if "consent" in lower:
        return "Microsoft Graph permissions need administrator approval."
    if "token" in lower or "expired" in lower:
        graph_auth.logout_user()
        return "Your Microsoft session expired. Please sign in again."
    return f"Microsoft Graph API failure ({status_code}). Please try again."
