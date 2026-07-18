"""Microsoft Graph client for Outlook read-only access."""

from __future__ import annotations

import logging
import base64
import json
import re
import time
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
LAST_GRAPH_REQUEST_DIAGNOSTIC: dict[str, str] = {}


class GraphApiError(RuntimeError):
    """Microsoft Graph failure with safe diagnostic fields."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        authenticate_header: str = "",
        diagnostics: dict[str, str] | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.code = _sanitize_graph_error(code or "")
        self.graph_message = _sanitize_graph_error(message or "")
        self.authenticate_header = _sanitize_graph_error(authenticate_header or "")
        self.diagnostics = dict(diagnostics or {})
        detail = self.graph_message or self.code or "Microsoft Graph request failed."
        super().__init__(f"Microsoft Graph HTTP {self.status_code} {self.code}: {detail}")


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
    LOGGER.info("Calling Microsoft Graph current-user endpoint.")
    return _graph_get(f"{GRAPH_BASE_URL}/me", token)


def list_inbox_messages(user_id: str, limit: int = 50) -> list[OutlookMessage]:
    """Return Outlook inbox messages without marking them read."""
    limit = max(1, int(limit or 50))
    if config.is_mock_mode():
        return list_mock_messages(user_id, limit=limit)

    token = graph_auth.get_valid_access_token()
    LOGGER.info("Calling Microsoft Graph inbox endpoint with limit=%s.", limit)
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
    headers = _headers(str(token or ""))
    diagnostics = _graph_request_diagnostics("GET", url, str(token or ""), headers)
    _remember_graph_request_diagnostic(diagnostics)
    _log_graph_request_diagnostics(diagnostics)
    if not token:
        raise RuntimeError("Microsoft Graph request was not sent because the access token is empty.")
    if diagnostics["Token Expired"] == "Yes":
        raise RuntimeError("Microsoft Graph request was not sent because the access token is expired.")
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        LOGGER.exception("Microsoft Graph network request failed for %s.", _safe_graph_url(url))
        raise RuntimeError("Network failure while contacting Microsoft Graph.") from exc
    LOGGER.info("Microsoft Graph response status=%s url=%s", response.status_code, _safe_graph_url(url))
    LOGGER.info("Microsoft Graph response body=%s", _safe_response_text(response))
    graph_error = _graph_error_details(response)
    authenticate_header = _safe_authenticate_header(response)
    diagnostics.update(_graph_response_diagnostics(response, graph_error, authenticate_header))
    _remember_graph_request_diagnostic(diagnostics)
    if response.status_code == 401 and retry_on_unauthorized:
        LOGGER.warning(
            "Microsoft Graph returned 401 code=%s message=%s authenticate_header=%s response_headers=%s response_body=%s; attempting silent token renewal once.",
            graph_error["code"],
            graph_error["message"],
            authenticate_header,
            diagnostics["Response Headers"],
            diagnostics["Response Body"],
        )
        renewed_token = graph_auth.acquire_token_silent_once(force_refresh=True)
        if renewed_token:
            return _graph_get(url, renewed_token, retry_on_unauthorized=False)
        LOGGER.warning("Microsoft Graph 401 silent renewal did not return an access token.")
    if response.status_code == 401:
        raise GraphApiError(
            response.status_code,
            graph_error["code"],
            graph_error["message"],
            authenticate_header=authenticate_header,
            diagnostics=diagnostics,
        )
    if response.status_code == 403:
        raise GraphApiError(response.status_code, graph_error["code"] or "Forbidden", graph_error["message"])
    if response.status_code == 404:
        raise GraphApiError(response.status_code, graph_error["code"] or "NotFound", graph_error["message"])
    if response.status_code >= 400:
        LOGGER.warning(
            "Microsoft Graph returned HTTP %s code=%s message=%s",
            response.status_code,
            graph_error["code"],
            graph_error["message"],
            stack_info=True,
        )
        raise GraphApiError(response.status_code, graph_error["code"], graph_error["message"])
    return response.json()


def last_graph_request_diagnostic() -> dict[str, str]:
    """Return the latest safe Microsoft Graph request diagnostic."""
    return dict(LAST_GRAPH_REQUEST_DIAGNOSTIC)


def _graph_error_details(response: requests.Response) -> dict[str, str]:
    """Return sanitized Microsoft Graph error code and message."""
    code = ""
    message = ""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        error = payload.get("error") or {}
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = str(error.get("message") or "")
    return {
        "code": _sanitize_graph_error(code),
        "message": _sanitize_graph_error(message or response.reason or ""),
    }


def _friendly_unauthorized_error(graph_error: dict[str, str]) -> str:
    """Return a safe 401 diagnostic without clearing persisted auth."""
    code = graph_error.get("code") or "Unauthorized"
    message = graph_error.get("message") or "Microsoft Graph rejected the access token."
    return f"Microsoft Graph 401: {code}. {message}"


def _safe_graph_url(url: str) -> str:
    """Remove query secrets from a Graph URL before logging."""
    return _sanitize_graph_error(url)


def _safe_response_text(response: requests.Response) -> str:
    """Return a sanitized response body for diagnostics."""
    text = getattr(response, "text", "")
    if text:
        return _sanitize_graph_error(text)
    try:
        return _sanitize_graph_error(str(response.json()))
    except ValueError:
        return ""


def _safe_response_headers(response: requests.Response) -> str:
    """Return sanitized response headers for diagnostics."""
    headers = getattr(response, "headers", {}) or {}
    try:
        header_items = dict(headers)
    except (TypeError, ValueError):
        header_items = {"headers": str(headers)}
    return _sanitize_graph_error(str(header_items))


def _safe_authenticate_header(response: requests.Response) -> str:
    """Return a sanitized WWW-Authenticate response header."""
    headers = getattr(response, "headers", {}) or {}
    get_header = getattr(headers, "get", None)
    if not callable(get_header):
        return ""
    return _sanitize_graph_error(str(get_header("WWW-Authenticate", "") or ""))


def _graph_request_diagnostics(method: str, url: str, token: str, headers: dict[str, str]) -> dict[str, str]:
    """Build safe request diagnostics before sending a Graph request."""
    authorization = str(headers.get("Authorization") or "")
    token_status = _session_token_status(token)
    return {
        "Request URL": _safe_graph_url(url),
        "HTTP Method": method,
        "Authorization Header Present": "Yes" if authorization else "No",
        "Bearer Prefix": "Yes" if authorization.startswith("Bearer ") else "No",
        "Token Length": str(len(token or "")),
        "Token Expired": _token_expired_label(token),
        "Silent Token Used": token_status,
        "HTTP Status": "",
        "WWW-Authenticate": "",
        "Graph Error Code": "",
        "Graph Error Message": "",
        "Response Headers": "",
        "Response Body": "",
    }


def _graph_response_diagnostics(
    response: requests.Response,
    graph_error: dict[str, str],
    authenticate_header: str,
) -> dict[str, str]:
    """Build safe response diagnostics after a Graph request."""
    return {
        "HTTP Status": str(getattr(response, "status_code", "")),
        "WWW-Authenticate": authenticate_header,
        "Graph Error Code": graph_error.get("code", ""),
        "Graph Error Message": graph_error.get("message", ""),
        "Response Headers": _safe_response_headers(response),
        "Response Body": _safe_response_text(response),
    }


def _remember_graph_request_diagnostic(diagnostics: dict[str, str]) -> None:
    """Remember the latest safe Graph request diagnostic for Streamlit rendering."""
    LAST_GRAPH_REQUEST_DIAGNOSTIC.clear()
    LAST_GRAPH_REQUEST_DIAGNOSTIC.update({key: _sanitize_graph_error(value) for key, value in diagnostics.items()})


def _log_graph_request_diagnostics(diagnostics: dict[str, str]) -> None:
    """Log safe request details before a Graph request is sent."""
    LOGGER.info(
        "Microsoft Graph request method=%s url=%s authorization_present=%s bearer_prefix=%s token_length=%s token_expired=%s silent_token_used=%s",
        diagnostics["HTTP Method"],
        diagnostics["Request URL"],
        diagnostics["Authorization Header Present"],
        diagnostics["Bearer Prefix"],
        diagnostics["Token Length"],
        diagnostics["Token Expired"],
        diagnostics["Silent Token Used"],
    )


def _token_expired_label(token: str) -> str:
    """Return whether a JWT access token is expired without exposing the token."""
    claims = _decode_jwt_payload(token)
    exp = claims.get("exp")
    if exp is None:
        return "Unknown"
    try:
        return "Yes" if int(exp) <= int(time.time()) + 60 else "No"
    except (TypeError, ValueError):
        return "Unknown"


def _session_token_status(token: str) -> str:
    """Describe whether the request token came from the current session or silent acquisition."""
    try:
        token_result = graph_auth.st.session_state.get(graph_auth.TOKEN_STATE_KEY, {}) or {}
        session_token = str(token_result.get("access_token") or "")
        auth_diagnostics = graph_auth.auth_diagnostics()
    except Exception:
        return "Unknown - session diagnostics unavailable"
    if token and session_token and token != session_token:
        return "Unknown - request token differs from current session token"
    silent_result = str(auth_diagnostics.get("silent_token_result") or "not_run")
    if silent_result == "access_token":
        return "Yes - token matches latest session token after silent acquisition"
    if session_token:
        return "No - current session token used; silent acquisition skipped because session token was usable"
    if silent_result in {"not_run", ""}:
        return "No - silent acquisition has not run"
    return f"No - silent acquisition result: {silent_result}"


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode unsigned JWT claims for diagnostics only."""
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}


def _sanitize_graph_error(value: str) -> str:
    """Remove OAuth secrets and token-shaped strings from Graph diagnostics."""
    sanitized = str(value)
    keyed_patterns = (
        r"(?i)(client_secret=)[^&\s]+",
        r"(?i)(access_token['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+",
        r"(?i)(refresh_token['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+",
        r"(?i)(authorization_code['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+",
        r"(?i)(code=)[^&\s]+",
    )
    for pattern in keyed_patterns:
        sanitized = re.sub(pattern, r"\1[redacted]", sanitized)
    sanitized = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*", "[redacted-token]", sanitized)
    return sanitized[:1000]


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
        return "Your Microsoft session expired. Please sign in again."
    return f"Microsoft Graph API failure ({status_code}). Please try again."
