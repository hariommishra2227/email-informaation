"""Microsoft delegated authentication helpers with persisted MSAL token cache."""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from typing import Any

try:
    import msal
except ImportError:  # pragma: no cover
    msal = None

import streamlit as st

import config
from storage import database


LOGGER = logging.getLogger(__name__)
TOKEN_STATE_KEY = "outlook_token_result"
ACCOUNT_STATE_KEY = "outlook_account"
USER_STATE_KEY = "outlook_connected_user"
AUTH_STATE_KEY = "outlook_auth_state"
AUTH_ERROR_STATE_KEY = "outlook_auth_error"
REQUIRED_MAIL_SCOPE = "Mail.Read"
AUTH_FLOW_TTL_SECONDS = 600


def _build_msal_app(token_cache: Any | None = None):
    """Create an MSAL confidential client for live delegated auth."""
    if msal is None:
        raise RuntimeError("msal is required for live Outlook mode. Install requirements.txt first.")
    kwargs: dict[str, Any] = {}
    if token_cache is not None:
        kwargs["token_cache"] = token_cache
    return msal.ConfidentialClientApplication(
        client_id=config.CLIENT_ID,
        client_credential=config.CLIENT_SECRET,
        authority=config.AUTHORITY,
        **kwargs,
    )


def create_login_url() -> str:
    """Return the Microsoft authorization URL for work/school and personal accounts."""
    if config.is_mock_mode():
        return "mock://outlook/login"
    missing = config.missing_live_settings()
    if missing:
        raise RuntimeError(f"Live Outlook configuration is missing: {', '.join(missing)}")

    _clear_callback_query_params()
    now = int(time.time())
    database.delete_expired_oauth_auth_flows(now)
    st.session_state[AUTH_ERROR_STATE_KEY] = ""

    flow_id = secrets.token_urlsafe(32)
    token_cache, _account = _load_msal_token_cache()
    app = _build_msal_app(token_cache)
    flow = app.initiate_auth_code_flow(
        scopes=config.GRAPH_SCOPES,
        redirect_uri=config.REDIRECT_URI,
        state=flow_id,
        prompt="select_account",
    )
    auth_uri = str(flow.get("auth_uri") or "")
    if not auth_uri:
        raise RuntimeError("Microsoft login URL could not be created.")

    flow_id = str(flow.get("state") or flow_id)
    database.store_oauth_auth_flow(
        flow_id=flow_id,
        flow=flow,
        created_at=now,
        expires_at=now + AUTH_FLOW_TTL_SECONDS,
    )
    st.session_state[AUTH_STATE_KEY] = flow_id
    return auth_uri


def acquire_token_by_authorization_code(code: str) -> dict[str, Any]:
    """Exchange an authorization code for delegated Microsoft Graph tokens."""
    if config.is_mock_mode():
        return {"access_token": "mock-access-token", "account": {"username": config.APP_USER_EMAIL}}
    token_cache, stored_account = _load_msal_token_cache()
    app = _build_msal_app(token_cache)
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=config.GRAPH_SCOPES,
        redirect_uri=config.REDIRECT_URI,
    )
    if "access_token" not in result:
        raise RuntimeError(_format_token_error(result))
    _store_token_result(result)
    _persist_msal_token_cache(token_cache, _account_from_result_or_cache(result, app, stored_account))
    if not has_granted_scope(REQUIRED_MAIL_SCOPE, result):
        granted = ", ".join(granted_scopes(result)) or "none"
        raise RuntimeError(f"Microsoft token did not include {REQUIRED_MAIL_SCOPE}. Granted scopes: {granted}.")
    return result


def acquire_token_by_auth_code_flow(flow: dict[str, Any], callback_params: dict[str, Any]) -> dict[str, Any]:
    """Exchange a Microsoft callback through MSAL's saved auth-code flow."""
    if config.is_mock_mode():
        return {"access_token": "mock-access-token", "account": {"username": config.APP_USER_EMAIL}}
    token_cache, stored_account = _load_msal_token_cache()
    app = _build_msal_app(token_cache)
    result = app.acquire_token_by_auth_code_flow(flow, callback_params)
    if "access_token" not in result:
        raise RuntimeError(_format_token_error(result))
    _store_token_result(result)
    _persist_msal_token_cache(token_cache, _account_from_result_or_cache(result, app, stored_account))
    if not has_granted_scope(REQUIRED_MAIL_SCOPE, result):
        granted = ", ".join(granted_scopes(result)) or "none"
        raise RuntimeError(f"Microsoft token did not include {REQUIRED_MAIL_SCOPE}. Granted scopes: {granted}.")
    return result


def handle_auth_callback() -> bool:
    """Handle the OAuth callback query parameters from Microsoft."""
    if config.is_mock_mode():
        return True

    params = dict(st.query_params)
    if "error" in params:
        flow_id = str(params.get("state") or "")
        if flow_id:
            database.delete_oauth_auth_flow(flow_id)
        st.session_state[AUTH_ERROR_STATE_KEY] = _format_query_error(params)
        st.session_state[AUTH_STATE_KEY] = ""
        _clear_callback_query_params()
        return False
    code = params.get("code")
    if not code:
        return has_valid_access_token()

    flow_id = str(params.get("state") or "")
    if not flow_id:
        st.session_state[AUTH_ERROR_STATE_KEY] = "Microsoft sign-in did not include a state value. Please connect Outlook again."
        st.session_state[AUTH_STATE_KEY] = ""
        _clear_callback_query_params()
        return False

    status, flow = database.consume_oauth_auth_flow(flow_id, now=int(time.time()))
    if status == "missing":
        st.session_state[AUTH_ERROR_STATE_KEY] = "Microsoft sign-in flow was not found or was already used. Please connect Outlook again."
        st.session_state[AUTH_STATE_KEY] = ""
        _clear_callback_query_params()
        return False
    if status == "expired":
        st.session_state[AUTH_ERROR_STATE_KEY] = "Microsoft sign-in flow expired. Please connect Outlook again."
        st.session_state[AUTH_STATE_KEY] = ""
        _clear_callback_query_params()
        return False

    try:
        acquire_token_by_auth_code_flow(flow or {}, params)
        st.session_state[AUTH_STATE_KEY] = ""
        _clear_callback_query_params()
        st.session_state[AUTH_ERROR_STATE_KEY] = ""
        _rerun_after_callback()
        return True
    except Exception as exc:
        st.session_state[AUTH_ERROR_STATE_KEY] = str(exc)
        st.session_state[AUTH_STATE_KEY] = ""
        _clear_callback_query_params()
        return False


def get_valid_access_token() -> str:
    """Return a non-expired access token, renewing through MSAL when needed."""
    if config.is_mock_mode():
        return "mock-access-token"
    token_result = st.session_state.get(TOKEN_STATE_KEY) or {}
    access_token = token_result.get("access_token")
    expires_at = int(token_result.get("expires_at") or 0)
    if access_token and expires_at > int(time.time()) + 60:
        return str(access_token)

    silent_token = acquire_token_silent_once(force_refresh=False, clear_on_failure=True)
    if silent_token:
        return silent_token

    logout_user()
    raise RuntimeError("Your Microsoft session expired. Sign in again.")


def acquire_token_silent_once(force_refresh: bool = False, clear_on_failure: bool = False) -> str | None:
    """Try to restore or renew an access token from the persisted MSAL cache."""
    if config.is_mock_mode():
        return "mock-access-token"
    token_cache, stored_account = _load_msal_token_cache()
    app = _build_msal_app(token_cache)
    account = _select_account(app, stored_account)
    if not account:
        if clear_on_failure:
            _clear_persisted_auth()
        return None

    result = _acquire_token_silent(app, account, force_refresh=force_refresh)
    if result and "access_token" in result:
        _store_token_result(result)
        _persist_msal_token_cache(token_cache, _account_from_result_or_cache(result, app, account))
        return str(result["access_token"])

    if result and ("error" in result or "error_description" in result):
        st.session_state[AUTH_ERROR_STATE_KEY] = _format_token_error(result)
    if clear_on_failure:
        _clear_persisted_auth()
    return None


def logout_user() -> None:
    """Disconnect Outlook by removing account and token data from session state."""
    st.session_state[TOKEN_STATE_KEY] = {}
    st.session_state[ACCOUNT_STATE_KEY] = {}
    st.session_state[USER_STATE_KEY] = {}
    st.session_state[AUTH_STATE_KEY] = ""
    st.session_state[AUTH_ERROR_STATE_KEY] = ""
    st.session_state["outlook_messages_cache"] = []
    st.session_state["selected_outlook_messages"] = []
    st.session_state["outlook_selected_messages"] = []
    st.session_state["outlook_import_summary"] = None
    _clear_persisted_auth()


def is_connected() -> bool:
    """Return whether the current Streamlit session has a usable Outlook token."""
    try:
        get_valid_access_token()
        return has_granted_scope(REQUIRED_MAIL_SCOPE)
    except Exception:
        return False


def token_exists() -> bool:
    """Return whether a current token or persisted MSAL cache exists."""
    token_result = st.session_state.get(TOKEN_STATE_KEY, {}) or {}
    access_token = token_result.get("access_token")
    expires_at = int(token_result.get("expires_at") or 0)
    if access_token and expires_at > int(time.time()) + 60:
        return True
    cache_json, _account = database.load_oauth_token_cache(_token_cache_owner())
    return bool(cache_json)


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
    cached = dict(st.session_state.get(USER_STATE_KEY) or {})
    if cached:
        return cached
    _cache_json, account = database.load_oauth_token_cache(_token_cache_owner())
    safe_account = _safe_account_metadata(account or {})
    if safe_account:
        st.session_state[USER_STATE_KEY] = safe_account
    return safe_account


def set_connected_user(user: dict[str, Any]) -> None:
    """Cache safe user profile fields in Streamlit session state."""
    st.session_state[USER_STATE_KEY] = {
        "displayName": user.get("displayName", ""),
        "mail": user.get("mail", ""),
        "userPrincipalName": user.get("userPrincipalName", ""),
        "id": user.get("id", ""),
    }
    cache_json, account = database.load_oauth_token_cache(_token_cache_owner())
    if cache_json:
        merged_account = dict(account or {})
        merged_account.update(st.session_state[USER_STATE_KEY])
        database.store_oauth_token_cache(
            _token_cache_owner(),
            cache_json,
            _safe_account_metadata(merged_account),
            int(time.time()),
        )


def auth_error() -> str:
    """Return the latest user-facing auth error."""
    return str(st.session_state.get(AUTH_ERROR_STATE_KEY, ""))


def _store_token_result(result: dict[str, Any]) -> None:
    """Store the current token result in session state only."""
    token_result = dict(result)
    token_result["expires_at"] = int(time.time()) + int(token_result.get("expires_in", 0))
    st.session_state[TOKEN_STATE_KEY] = token_result
    if result.get("account"):
        account = _safe_account_metadata(result["account"])
        st.session_state[ACCOUNT_STATE_KEY] = account
        st.session_state[USER_STATE_KEY] = account


def _token_cache_owner() -> str:
    """Return the server-side token cache owner for this single-user app."""
    return str(config.DEFAULT_USER_ID or "default_user")


def _new_msal_token_cache() -> Any | None:
    """Create an MSAL SerializableTokenCache when MSAL is available."""
    if msal is None:
        return None
    return msal.SerializableTokenCache()


def _load_msal_token_cache() -> tuple[Any | None, dict[str, Any] | None]:
    """Load the serialized MSAL cache and account metadata from SQLite."""
    token_cache = _new_msal_token_cache()
    cache_json, account = database.load_oauth_token_cache(_token_cache_owner())
    if token_cache is not None and cache_json:
        token_cache.deserialize(cache_json)
    return token_cache, account


def _persist_msal_token_cache(token_cache: Any | None, account: dict[str, Any] | None) -> None:
    """Persist the MSAL token cache when it has changed."""
    if token_cache is None:
        return
    if not getattr(token_cache, "has_state_changed", False):
        return
    database.store_oauth_token_cache(
        cache_owner=_token_cache_owner(),
        cache_json=str(token_cache.serialize()),
        account=_safe_account_metadata(account or {}),
        updated_at=int(time.time()),
    )


def _clear_persisted_auth() -> None:
    """Remove persisted token cache and account metadata."""
    database.delete_oauth_token_cache(_token_cache_owner())


def _select_account(app: Any, stored_account: dict[str, Any] | None) -> dict[str, Any] | None:
    """Find the best MSAL account for silent token acquisition."""
    accounts = _get_accounts(app)
    if not accounts:
        return None
    stored = stored_account or {}
    for key in ("home_account_id", "local_account_id", "username"):
        wanted = stored.get(key)
        if not wanted:
            continue
        for account in accounts:
            if account.get(key) == wanted:
                return account
    return accounts[0]


def _get_accounts(app: Any) -> list[dict[str, Any]]:
    """Return MSAL accounts from an app object."""
    get_accounts = getattr(app, "get_accounts", None)
    if not callable(get_accounts):
        return []
    accounts = get_accounts()
    return [dict(account) for account in accounts or [] if isinstance(account, dict)]


def _account_from_result_or_cache(
    result: dict[str, Any],
    app: Any,
    fallback_account: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return safe account metadata from token result, cache, or fallback."""
    if isinstance(result.get("account"), dict):
        return _safe_account_metadata(result["account"])
    account = _select_account(app, fallback_account)
    return _safe_account_metadata(account or fallback_account or {})


def _safe_account_metadata(account: dict[str, Any]) -> dict[str, Any]:
    """Keep only non-token account fields needed to find the MSAL account again."""
    safe_keys = (
        "home_account_id",
        "local_account_id",
        "username",
        "environment",
        "realm",
        "displayName",
        "mail",
        "userPrincipalName",
        "id",
    )
    return {key: str(account.get(key, "")) for key in safe_keys if account.get(key)}


def _acquire_token_silent(app: Any, account: dict[str, Any], force_refresh: bool = False) -> dict[str, Any] | None:
    """Call MSAL acquire_token_silent while tolerating older/test signatures."""
    try:
        return app.acquire_token_silent(config.GRAPH_SCOPES, account=account, force_refresh=force_refresh)
    except TypeError:
        return app.acquire_token_silent(config.GRAPH_SCOPES, account=account)


def _rerun_after_callback() -> None:
    """Trigger a Streamlit rerun after a successful callback when available."""
    rerun = getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


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


def _clear_callback_query_params() -> None:
    """Clear OAuth callback parameters from the visible Streamlit URL."""
    params = st.query_params
    if any(key in params for key in ("code", "state", "error", "error_description")):
        params.clear()


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
