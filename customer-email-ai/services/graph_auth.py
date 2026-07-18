"""Microsoft delegated authentication helpers with session-only token storage."""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from typing import Any

try:
    import msal
except ImportError:  # pragma: no cover
    msal = None

import streamlit as st

import config


LOGGER = logging.getLogger(__name__)
TOKEN_STATE_KEY = "outlook_token_result"
ACCOUNT_STATE_KEY = "outlook_account"
USER_STATE_KEY = "outlook_connected_user"
AUTH_STATE_KEY = "outlook_auth_state"
AUTH_ERROR_STATE_KEY = "outlook_auth_error"
REQUIRED_MAIL_SCOPE = "Mail.Read"


def _build_msal_app():
    """Create an MSAL confidential client for live delegated auth."""
    if msal is None:
        raise RuntimeError("msal is required for live Outlook mode. Install requirements.txt first.")
    return msal.ConfidentialClientApplication(
        client_id=config.CLIENT_ID,
        client_credential=config.CLIENT_SECRET,
        authority=config.AUTHORITY,
    )


def create_login_url() -> str:
    """Return the Microsoft authorization URL for work/school and personal accounts."""
    if config.is_mock_mode():
        return "mock://outlook/login"
    missing = config.missing_live_settings()
    if missing:
        raise RuntimeError(f"Live Outlook configuration is missing: {', '.join(missing)}")
    state = uuid.uuid4().hex
    st.session_state[AUTH_STATE_KEY] = state
    app = _build_msal_app()
    return app.get_authorization_request_url(
        scopes=config.GRAPH_SCOPES,
        redirect_uri=config.REDIRECT_URI,
        state=state,
        prompt="select_account",
    )


def acquire_token_by_authorization_code(code: str) -> dict[str, Any]:
    """Exchange an authorization code for delegated Microsoft Graph tokens."""
    if config.is_mock_mode():
        return {"access_token": "mock-access-token", "account": {"username": config.APP_USER_EMAIL}}
    app = _build_msal_app()
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=config.GRAPH_SCOPES,
        redirect_uri=config.REDIRECT_URI,
    )
    if "access_token" not in result:
        raise RuntimeError(_format_token_error(result))
    _store_token_result(result)
    if not has_granted_scope(REQUIRED_MAIL_SCOPE, result):
        granted = ", ".join(granted_scopes(result)) or "none"
        raise RuntimeError(f"Microsoft token did not include {REQUIRED_MAIL_SCOPE}. Granted scopes: {granted}.")
    return result


def handle_auth_callback() -> bool:
    """Handle the OAuth callback query parameters from Microsoft."""
    if config.is_mock_mode():
        return True

    params = st.query_params
    if "error" in params:
        st.session_state[AUTH_ERROR_STATE_KEY] = _format_query_error(params)
        return False
    code = params.get("code")
    if not code:
        return has_valid_access_token()

    expected_state = st.session_state.get(AUTH_STATE_KEY)
    received_state = params.get("state")
    if not expected_state or not received_state or expected_state != received_state:
        st.session_state[AUTH_ERROR_STATE_KEY] = "Microsoft sign-in state did not match. Please try again."
        st.query_params.clear()
        return False

    try:
        acquire_token_by_authorization_code(str(code))
        st.session_state.pop(AUTH_STATE_KEY, None)
        st.query_params.clear()
        st.session_state.pop(AUTH_ERROR_STATE_KEY, None)
        return True
    except Exception as exc:
        st.session_state[AUTH_ERROR_STATE_KEY] = str(exc)
        return False


def get_valid_access_token() -> str:
    """Return a non-expired access token from Streamlit session state."""
    if config.is_mock_mode():
        return "mock-access-token"
    token_result = st.session_state.get(TOKEN_STATE_KEY) or {}
    access_token = token_result.get("access_token")
    expires_at = int(token_result.get("expires_at") or 0)
    if access_token and expires_at > int(time.time()) + 60:
        return str(access_token)

    refresh_token = token_result.get("refresh_token")
    refresh_error = ""
    if refresh_token:
        result = _build_msal_app().acquire_token_by_refresh_token(
            refresh_token=refresh_token,
            scopes=config.GRAPH_SCOPES,
        )
        if "access_token" in result:
            _store_token_result(result)
            if not has_granted_scope(REQUIRED_MAIL_SCOPE, result):
                granted = ", ".join(granted_scopes(result)) or "none"
                raise RuntimeError(f"Microsoft token did not include {REQUIRED_MAIL_SCOPE}. Granted scopes: {granted}.")
            return str(result["access_token"])
        refresh_error = _format_token_error(result)

    logout_user()
    if refresh_error:
        st.session_state[AUTH_ERROR_STATE_KEY] = refresh_error
    raise RuntimeError("Your Microsoft session expired. Sign in again.")


def logout_user() -> None:
    """Disconnect Outlook by removing account and token data from session state."""
    for key in (TOKEN_STATE_KEY, ACCOUNT_STATE_KEY, USER_STATE_KEY, AUTH_STATE_KEY, AUTH_ERROR_STATE_KEY):
        st.session_state.pop(key, None)
    for key in ("outlook_messages_cache", "selected_outlook_messages", "outlook_import_summary"):
        st.session_state.pop(key, None)


def is_connected() -> bool:
    """Return whether the current Streamlit session has a usable Outlook token."""
    return has_valid_access_token() and has_granted_scope(REQUIRED_MAIL_SCOPE)


def token_exists() -> bool:
    """Return whether any Outlook token result is present in session state."""
    return bool(st.session_state.get(TOKEN_STATE_KEY, {}).get("access_token"))


def has_valid_access_token() -> bool:
    """Return whether a non-expired access token exists without refreshing it."""
    if config.is_mock_mode():
        return True
    token_result = st.session_state.get(TOKEN_STATE_KEY) or {}
    access_token = token_result.get("access_token")
    expires_at = int(token_result.get("expires_at") or 0)
    return bool(access_token and expires_at > int(time.time()) + 60)


def granted_scopes(token_result: dict[str, Any] | None = None) -> list[str]:
    """Return granted scope names from MSAL result or the JWT scp claim."""
    token_data = token_result if token_result is not None else st.session_state.get(TOKEN_STATE_KEY, {})
    scope_text = str((token_data or {}).get("scope") or "").strip()
    scopes = [scope for scope in scope_text.split() if scope]
    if scopes:
        return sorted(set(scopes), key=str.lower)

    access_token = str((token_data or {}).get("access_token") or "")
    claims = _decode_jwt_payload(access_token)
    claim_scopes = str(claims.get("scp") or "").strip()
    return sorted({scope for scope in claim_scopes.split() if scope}, key=str.lower)


def has_granted_scope(scope_name: str, token_result: dict[str, Any] | None = None) -> bool:
    """Return whether a granted delegated scope is present, case-insensitively."""
    wanted = scope_name.lower()
    return any(scope.lower() == wanted for scope in granted_scopes(token_result))


def connected_user() -> dict[str, Any]:
    """Return cached connected Microsoft user metadata."""
    return dict(st.session_state.get(USER_STATE_KEY) or {})


def set_connected_user(user: dict[str, Any]) -> None:
    """Cache safe user profile fields in Streamlit session state."""
    st.session_state[USER_STATE_KEY] = {
        "displayName": user.get("displayName", ""),
        "mail": user.get("mail", ""),
        "userPrincipalName": user.get("userPrincipalName", ""),
        "id": user.get("id", ""),
    }


def auth_error() -> str:
    """Return the latest user-facing auth error."""
    return str(st.session_state.get(AUTH_ERROR_STATE_KEY, ""))


def _store_token_result(result: dict[str, Any]) -> None:
    """Store Microsoft token data only in Streamlit session state."""
    token_result = dict(result)
    token_result["expires_at"] = int(time.time()) + int(token_result.get("expires_in", 0))
    st.session_state[TOKEN_STATE_KEY] = token_result
    if result.get("account"):
        st.session_state[ACCOUNT_STATE_KEY] = result["account"]


def _format_token_error(result: dict[str, Any]) -> str:
    """Return the actual Microsoft token error fields without token secrets."""
    error = str(result.get("error") or "token_error")
    description = str(result.get("error_description") or result.get("suberror") or "Microsoft token request failed.")
    return f"Microsoft token error: {error}. {description}"


def _format_query_error(params: Any) -> str:
    """Return the actual Microsoft redirect error fields without authorization codes."""
    error = str(params.get("error") or "authorization_error")
    description = str(params.get("error_description") or "Microsoft sign-in failed.")
    return f"Microsoft sign-in error: {error}. {description}"


def _decode_jwt_payload(access_token: str) -> dict[str, Any]:
    """Decode JWT payload claims for diagnostics only; this does not verify the signature."""
    parts = access_token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload}{padding}")
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _friendly_error_text(message: str) -> str:
    """Normalize common Microsoft identity errors for the UI."""
    lower = message.lower()
    if "aadsts65001" in lower or "consent" in lower:
        return "Admin consent or user consent is required for Microsoft Graph permissions."
    if "aadsts7000222" in lower or "expired" in lower:
        return "The Microsoft client secret has expired. Create a new Secret Value and update Streamlit Secrets."
    if "aadsts7000215" in lower or "invalid_client" in lower:
        return "The Microsoft client secret is invalid. Use the client secret Value, not the Secret ID."
    if "aadsts700016" in lower or "tenant" in lower:
        return "The Microsoft tenant or application id is invalid for this app registration."
    if "aadsts50020" in lower:
        return "This Microsoft account is not allowed by the app registration. Enable personal accounts and organizational accounts."
    if "aadsts50011" in lower or "redirect_uri" in lower or "reply address" in lower:
        return "The redirect URI is invalid or missing in Microsoft Entra app registration."
    if "access_denied" in lower or "permission" in lower:
        return "Permission was denied during Microsoft sign-in."
    return message


get_login_url = create_login_url
exchange_authorization_code = acquire_token_by_authorization_code
get_access_token = get_valid_access_token
