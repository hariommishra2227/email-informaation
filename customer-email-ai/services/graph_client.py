"""Microsoft Graph client for Outlook read-only access."""

from __future__ import annotations

import logging
import base64
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import quote

import requests

import config
from models import OutlookMessage
from services import graph_auth
from services.email_processor import clean_html_to_text
from services.outlook_email_service import get_mock_message, list_mock_messages


LOGGER = logging.getLogger(__name__)
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
REQUEST_TIMEOUT_SECONDS = 20
GRAPH_PAGE_SIZE = 50
RETRYABLE_GRAPH_STATUS_CODES = {429, 500, 502, 503, 504}
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


def list_inbox_messages(
    user_id: str,
    limit: int = 50,
    received_after: str | None = None,
    received_before: str | None = None,
) -> list[OutlookMessage]:
    """Return Outlook inbox messages without marking them read."""
    limit = max(1, int(limit or 50))
    if config.is_mock_mode():
        messages = list_mock_messages(user_id, limit=limit)
        if received_after:
            messages = [message for message in messages if message.received_datetime >= received_after]
        if received_before:
            messages = [message for message in messages if message.received_datetime < received_before]
        return messages

    token = graph_auth.get_valid_access_token()
    LOGGER.info("Calling Microsoft Graph inbox endpoint with limit=%s.", limit)
    next_url = (
        f"{GRAPH_BASE_URL}/me/messages"
        "?$select=id,internetMessageId,subject,from,receivedDateTime,bodyPreview,body,isRead,hasAttachments,webLink"
        "&$orderby=receivedDateTime desc&$top=50"
    )
    if received_after:
        filter_expression = f"receivedDateTime ge {received_after}"
        if received_before:
            filter_expression += f" and receivedDateTime lt {received_before}"
        next_url += f"&$filter={quote(filter_expression, safe='-:TZ.') }"
    messages: list[OutlookMessage] = []
    while next_url and len(messages) < limit:
        payload, token = _graph_get_with_token(next_url, token)
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
                    internet_message_id=str(item.get("internetMessageId") or ""),
                )
            )
        next_url = payload.get("@odata.nextLink")
    return messages


def iter_mailbox_message_pages(
    user_id: str,
    received_after: str | None = None,
    received_before: str | None = None,
    page_size: int = GRAPH_PAGE_SIZE,
    start_next_link: str | None = None,
    checkpoint: Any | None = None,
):
    """Yield every mailbox page, optionally restricted to a received-time high-water mark."""
    # Keep Graph requests bounded. The caller may use smaller pages in tests,
    # but never request more than 50 messages from Graph.
    page_size = min(GRAPH_PAGE_SIZE, max(1, int(page_size)))
    if config.is_mock_mode():
        messages = list_mock_messages(user_id, limit=10_000_000)
        if received_after:
            messages = [message for message in messages if message.received_datetime >= received_after]
        if received_before:
            messages = [message for message in messages if message.received_datetime < received_before]
        for offset in range(0, len(messages), page_size):
            yield messages[offset : offset + page_size]
        return

    token = graph_auth.get_valid_access_token()
    # Bodies are fetched one message at a time by the large-mailbox worker.
    select = "id,internetMessageId,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments"
    next_url = start_next_link or f"{GRAPH_BASE_URL}/me/messages?$select={select}&$orderby=receivedDateTime%20asc&$top={page_size}"
    if received_after:
        graph_filter = f"receivedDateTime ge {received_after}"
        if received_before:
            graph_filter += f" and receivedDateTime lt {received_before}"
        graph_filter = quote(graph_filter, safe="-:TZ.")
        next_url += f"&$filter={graph_filter}"

    LOGGER.info("Streaming complete Microsoft Graph mailbox after=%s page_size=%s.", received_after, page_size)
    while next_url:
        payload, token = _graph_get_with_token(next_url, token)
        page: list[OutlookMessage] = []
        for item in payload.get("value", []):
            message = _outlook_message_from_graph_item(user_id, item)
            if message is not None:
                page.append(message)
        following_link = str(payload.get("@odata.nextLink") or "")
        if checkpoint:
            checkpoint(following_link)
        if page:
            yield page
        next_url = following_link


def _outlook_message_from_graph_item(user_id: str, item: dict[str, Any]) -> OutlookMessage | None:
    """Convert one Graph response item without changing mailbox state."""
    message_id = str(item.get("id") or "").strip()
    if not message_id:
        LOGGER.warning("Graph message without id was skipped.")
        return None
    sender = (item.get("from") or {}).get("emailAddress", {}) or {}
    body_info = item.get("body") or {}
    return OutlookMessage(
        message_id=message_id,
        user_id=user_id,
        sender_name=str(sender.get("name") or ""),
        sender_email=str(sender.get("address") or ""),
        subject=str(item.get("subject") or ""),
        body=_body_to_text(str(body_info.get("content") or ""), str(body_info.get("contentType") or "")),
        body_preview=str(item.get("bodyPreview") or ""),
        received_datetime=str(item.get("receivedDateTime") or ""),
        is_read=bool(item.get("isRead")),
        has_attachments=bool(item.get("hasAttachments")),
        attachment_names=[],
    )
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
    payload, _token = _graph_get_with_token(url, token, retry_on_unauthorized=retry_on_unauthorized)
    return payload


def _graph_get_with_token(url: str, token: str, retry_on_unauthorized: bool = True, retry_count: int = 0) -> tuple[dict[str, Any], str]:
    """GET Microsoft Graph JSON and return the access token that was actually used."""
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
    if response.status_code in RETRYABLE_GRAPH_STATUS_CODES and retry_count < 5:
        retry_after = _retry_after_seconds(response)
        delay = retry_after if retry_after is not None else min(60.0, 0.5 * (2 ** retry_count))
        LOGGER.warning("Retryable Microsoft Graph response %s; retrying in %.1fs.", response.status_code, delay)
        time.sleep(delay)
        return _graph_get_with_token(url, token, retry_on_unauthorized, retry_count + 1)
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
            return _graph_get_with_token(url, renewed_token, retry_on_unauthorized=False)
        diagnostics["Latest MSAL Token Hash"] = _session_access_token_hash()
        _remember_graph_request_diagnostic(diagnostics)
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
    return response.json(), str(token or "")


def _retry_after_seconds(response: requests.Response) -> float | None:
    """Parse Retry-After safely, with bounded exponential fallback handled by caller."""
    value = str(getattr(response, "headers", {}).get("Retry-After", "") or "").strip()
    try:
        return min(60.0, max(0.0, float(value))) if value else None
    except ValueError:
        return None


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
    diagnostics = {
        "Request URL": _safe_graph_url(url),
        "HTTP Method": method,
        "Token Source": token_status,
        "Account Username": _session_account_value("username"),
        "Account Home Account ID": _session_account_value("home_account_id"),
        "Authorization Header Present": "Yes" if authorization else "No",
        "Bearer Prefix": "Yes" if authorization.startswith("Bearer ") else "No",
        "Token Length": str(len(token or "")),
        "Token Expired": _token_expired_label(token),
        "Token Expiry": _token_claim_value(token, "exp"),
        "Current Token Hash": _token_hash(token),
        "Latest MSAL Token Hash": _session_access_token_hash(),
        "Silent Token Used": token_status,
        "HTTP Status": "",
        "WWW-Authenticate": "",
        "Graph Error Code": "",
        "Graph Error Message": "",
        "Response Headers": "",
        "Response Body": "",
    }
    diagnostics.update(_access_token_claim_diagnostics(token))
    return diagnostics


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
        "Microsoft Graph request method=%s url=%s token_source=%s account_username=%s account_home_account_id=%s authorization_present=%s bearer_prefix=%s token_length=%s token_expiry=%s token_expired=%s aud=%s scp=%s",
        diagnostics["HTTP Method"],
        diagnostics["Request URL"],
        diagnostics["Token Source"],
        diagnostics["Account Username"],
        diagnostics["Account Home Account ID"],
        diagnostics["Authorization Header Present"],
        diagnostics["Bearer Prefix"],
        diagnostics["Token Length"],
        diagnostics["Token Expiry"],
        diagnostics["Token Expired"],
        diagnostics.get("Token Claim aud", ""),
        diagnostics.get("Token Claim scp", ""),
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


def _session_account_value(key: str) -> str:
    """Return safe current account metadata for request diagnostics."""
    try:
        account = graph_auth.st.session_state.get(graph_auth.ACCOUNT_STATE_KEY, {}) or {}
        if not account:
            account = graph_auth.connected_user() or {}
    except Exception:
        return ""
    value = str(account.get(key) or "")
    if key == "home_account_id" and value:
        return f"hash:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"
    return _sanitize_graph_error(value)


def _session_access_token_hash() -> str:
    """Return a short hash of the latest session access token without exposing it."""
    try:
        token_result = graph_auth.st.session_state.get(graph_auth.TOKEN_STATE_KEY, {}) or {}
        return _token_hash(str(token_result.get("access_token") or ""))
    except Exception:
        return ""


def _token_hash(token: str) -> str:
    """Return a short stable token fingerprint without exposing token material."""
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _token_claim_value(token: str, claim_name: str) -> str:
    """Return one safe decoded JWT claim value."""
    return _format_claim_value(_decode_jwt_payload(token).get(claim_name))


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


def _access_token_claim_diagnostics(token: str) -> dict[str, str]:
    """Return safe decoded access-token claims and token-shape diagnostics."""
    claims = _decode_jwt_payload(token)
    safe_claims = {
        claim_name: _format_claim_value(claims.get(claim_name))
        for claim_name in ("aud", "iss", "tid", "oid", "appid", "azp", "scp", "roles", "ver", "exp", "iat")
    }
    scopes = set(str(claims.get("scp") or "").split())
    roles = claims.get("roles")
    has_roles = bool(roles)
    audience = str(claims.get("aud") or "")
    has_delegated_scopes = bool(scopes)
    is_graph_audience = audience == "https://graph.microsoft.com"
    is_graph_app_id_audience = audience == "00000003-0000-0000-c000-000000000000"
    is_id_token = bool(audience and audience == str(config.CLIENT_ID or ""))
    is_access_token = bool(audience and (has_delegated_scopes or has_roles) and not is_id_token)
    if has_delegated_scopes:
        token_type = "Delegated"
    elif has_roles:
        token_type = "Application"
    else:
        token_type = "Unknown"
    diagnostics = {
        f"Token Claim {claim_name}": claim_value
        for claim_name, claim_value in safe_claims.items()
    }
    diagnostics.update(
        {
            "Is Access Token": "Yes" if is_access_token else "No",
            "Is ID Token": "Yes" if is_id_token else "No",
            "Audience Equals Graph URL": "Yes" if is_graph_audience else "No",
            "Audience Is Accepted Graph Resource": "Yes" if is_graph_audience or is_graph_app_id_audience else "No",
            "Contains Mail.Read Scope": "Yes" if any(scope.lower() == "mail.read" for scope in scopes) else "No",
            "Token Delegation Type": token_type,
        }
    )
    return diagnostics


def _format_claim_value(value: Any) -> str:
    """Format one safe JWT claim value for diagnostics."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_sanitize_graph_error(str(item)) for item in value)
    if isinstance(value, dict):
        return _sanitize_graph_error(json.dumps(value, sort_keys=True))
    return _sanitize_graph_error(str(value))


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
