"""Microsoft delegated authentication helpers with persisted MSAL token cache."""

from __future__ import annotations

import base64
import hashlib
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
AUTH_FLOW_STATE_KEY = "outlook_auth_flow"
AUTH_CODE_ATTEMPT_STATE_KEY = "outlook_auth_code_attempt"
AUTH_CODE_SUCCESS_STATE_KEY = "outlook_auth_code_success"
AUTH_ERROR_STATE_KEY = "outlook_auth_error"
CALLBACK_CACHE_SAVED_STATE_KEY = "outlook_callback_cache_saved"
SILENT_RESULT_STATE_KEY = "outlook_silent_token_result"
AUTH_SESSION_ID_STATE_KEY = "outlook_auth_session_id"
TOKEN_CACHE_OWNER_STATE_KEY = "outlook_token_cache_owner"
ACCOUNT_HOME_ID_STATE_KEY = "outlook_account_home_account_id"
REQUIRED_MAIL_SCOPE = "Mail.Read"
LEGACY_SHARED_CACHE_OWNER = "default_user"
SILENT_TOKEN_SCOPES = [
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.Read",
]
MICROSOFT_GRAPH_AUDIENCES = {
    "https://graph.microsoft.com",
    "00000003-0000-0000-c000-000000000000",
}
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
    _delete_legacy_shared_cache()
    now = int(time.time())
    database.delete_expired_oauth_auth_flows(now)
    st.session_state[AUTH_ERROR_STATE_KEY] = ""
    LOGGER.debug("create_auth_flow: auth_flow exists=%s state=%s", False, "")

    flow_id = secrets.token_urlsafe(32)
    temporary_owner = _temporary_cache_owner()
    token_cache, _account = _load_msal_token_cache(temporary_owner)
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
    flow["_cache_owner"] = temporary_owner
    database.store_oauth_auth_flow(
        flow_id=flow_id,
        flow=flow,
        created_at=now,
        expires_at=now + AUTH_FLOW_TTL_SECONDS,
    )
    st.session_state[AUTH_STATE_KEY] = flow_id
    st.session_state[AUTH_FLOW_STATE_KEY] = flow
    st.session_state[AUTH_CODE_ATTEMPT_STATE_KEY] = ""
    st.session_state[AUTH_CODE_SUCCESS_STATE_KEY] = ""
    st.session_state[TOKEN_CACHE_OWNER_STATE_KEY] = temporary_owner
    return auth_uri


def get_authorization_url() -> str:
    """Create a Microsoft authorization URL for the top-level Outlook sign-in link."""
    return create_login_url()


def acquire_token_by_authorization_code(code: str) -> dict[str, Any]:
    """Exchange an authorization code for delegated Microsoft Graph tokens."""
    if config.is_mock_mode():
        return {"access_token": "mock-access-token", "account": {"username": config.APP_USER_EMAIL}}
    cache_owner = _temporary_cache_owner()
    token_cache, stored_account = _load_msal_token_cache(cache_owner)
    app = _build_msal_app(token_cache)
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=config.GRAPH_SCOPES,
        redirect_uri=config.REDIRECT_URI,
    )
    _validate_successful_token_result(result, "authorization_code")
    _log_access_token_jwt_diagnostics(result, "authorization_code")
    account = _account_from_result_or_cache(result, app, stored_account)
    permanent_owner = _permanent_cache_owner(account)
    if not permanent_owner:
        raise RuntimeError("Microsoft sign-in did not return a stable account id. Please connect Outlook again.")
    _store_token_result(result)
    _remember_account_owner(account, permanent_owner)
    _remember_callback_cache_saved(
        _persist_msal_token_cache(token_cache, account, permanent_owner)
    )
    _delete_temp_cache_if_needed(cache_owner, permanent_owner)
    _verify_persisted_msal_cache(permanent_owner)
    return result


def acquire_token_by_auth_code_flow(
    flow: dict[str, Any],
    callback_params: dict[str, Any],
    cache_owner: str | None = None,
) -> dict[str, Any]:
    """Exchange a Microsoft callback through MSAL's saved auth-code flow."""
    if config.is_mock_mode():
        return {"access_token": "mock-access-token", "account": {"username": config.APP_USER_EMAIL}}
    flow_owner = cache_owner or str(flow.get("_cache_owner") or "") or _temporary_cache_owner()
    token_cache, stored_account = _load_msal_token_cache(flow_owner)
    app = _build_msal_app(token_cache)
    LOGGER.debug(
        "acquire_token_by_auth_code_flow: auth_flow exists=%s state=%s request_code=%s",
        bool(flow),
        str(flow.get("state") or ""),
        _safe_code_debug(callback_params.get("code")),
    )
    result = app.acquire_token_by_auth_code_flow(flow, callback_params)
    LOGGER.debug(
        "acquire_token_by_auth_code_flow: token exchange result access_token=%s account=%s error=%s cache_changed=%s",
        "access_token" in result,
        bool(result.get("account")),
        str(result.get("error") or ""),
        bool(getattr(token_cache, "has_state_changed", False)),
    )
    _validate_successful_token_result(result, "auth_code_flow")
    _log_access_token_jwt_diagnostics(result, "auth_code_flow")
    account = _account_from_result_or_cache(result, app, stored_account)
    permanent_owner = _permanent_cache_owner(account)
    if not permanent_owner:
        raise RuntimeError("Microsoft sign-in did not return a stable account id. Please connect Outlook again.")
    _store_token_result(result)
    _remember_account_owner(account, permanent_owner)
    _remember_callback_cache_saved(
        _persist_msal_token_cache(token_cache, account, permanent_owner)
    )
    _delete_temp_cache_if_needed(flow_owner, permanent_owner)
    _verify_persisted_msal_cache(permanent_owner)
    return result


def handle_auth_callback() -> bool:
    """Handle the OAuth callback query parameters from Microsoft."""
    if config.is_mock_mode():
        return True

    params = dict(st.query_params)
    LOGGER.debug(
        "handle_auth_callback: auth_flow exists=%s state=%s request_code=%s",
        bool(st.session_state.get(AUTH_FLOW_STATE_KEY)),
        str(params.get("state") or st.session_state.get(AUTH_STATE_KEY) or ""),
        _safe_code_debug(params.get("code")),
    )
    if "error" in params:
        flow_id = str(params.get("state") or "")
        st.session_state[AUTH_ERROR_STATE_KEY] = _format_query_error(params)
        return False
    code = params.get("code")
    if not code:
        return is_connected()

    flow_id = str(params.get("state") or "")
    if not flow_id:
        st.session_state[AUTH_ERROR_STATE_KEY] = "Microsoft sign-in did not include a state value. Please connect Outlook again."
        return False

    code_attempt = f"{flow_id}:{_safe_fingerprint(str(code))}"
    if st.session_state.get(AUTH_CODE_SUCCESS_STATE_KEY) == code_attempt and is_connected():
        _clear_callback_query_params()
        return True

    flow = _session_auth_flow(flow_id)
    status = "ok" if flow else "missing"
    if not flow:
        status, flow = database.load_oauth_auth_flow(flow_id, now=int(time.time()))
        if status == "ok" and flow:
            st.session_state[AUTH_FLOW_STATE_KEY] = flow
            st.session_state[AUTH_STATE_KEY] = flow_id
    if status == "missing":
        if is_connected() or token_exists():
            st.session_state[AUTH_ERROR_STATE_KEY] = ""
            st.session_state[AUTH_STATE_KEY] = ""
            st.session_state.pop(AUTH_FLOW_STATE_KEY, None)
            st.session_state[AUTH_CODE_ATTEMPT_STATE_KEY] = ""
            _clear_callback_query_params()
            return True

        st.session_state[AUTH_ERROR_STATE_KEY] = "Microsoft sign-in flow was not found or was already used. Please connect Outlook again."
        return False
    if status == "expired":
        st.session_state[AUTH_ERROR_STATE_KEY] = "Microsoft sign-in flow expired. Please connect Outlook again."
        return False

    if st.session_state.get(AUTH_CODE_ATTEMPT_STATE_KEY) == code_attempt:
        return is_connected()

    try:
        st.session_state[AUTH_CODE_ATTEMPT_STATE_KEY] = code_attempt
        acquire_token_by_auth_code_flow(flow or {}, params, cache_owner=str((flow or {}).get("_cache_owner") or ""))
        database.delete_oauth_auth_flow(flow_id)
        st.session_state[AUTH_STATE_KEY] = ""
        st.session_state.pop(AUTH_FLOW_STATE_KEY, None)
        st.session_state[AUTH_CODE_ATTEMPT_STATE_KEY] = ""
        st.session_state[AUTH_CODE_SUCCESS_STATE_KEY] = code_attempt
        _clear_callback_query_params()
        st.session_state[AUTH_ERROR_STATE_KEY] = ""
        _rerun_after_callback()
        return True
    except Exception as exc:
        st.session_state[AUTH_ERROR_STATE_KEY] = str(exc)
        LOGGER.exception(
            "handle_auth_callback: token exchange failed state=%s request_code=%s",
            flow_id,
            _safe_code_debug(code),
        )
        return False


def get_valid_access_token() -> str:
    """Return a usable access token, preferring the fresh Streamlit session token."""
    if config.is_mock_mode():
        return "mock-access-token"

    token_result = st.session_state.get(TOKEN_STATE_KEY, {}) or {}
    access_token = str(token_result.get("access_token") or "")

    try:
        expires_at = int(token_result.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0

    if access_token and expires_at > int(time.time()) + 60:
        if not _has_required_scope_when_available(token_result):
            granted = ", ".join(granted_scopes(token_result)) or "none"
            raise RuntimeError(f"Microsoft token did not include {REQUIRED_MAIL_SCOPE}. Granted scopes: {granted}.")
        LOGGER.info("Using current Streamlit session access token.")
        return access_token

    silent_token = acquire_token_silent_once(
        force_refresh=False,
        clear_on_failure=False,
    )
    if silent_token:
        return silent_token

    raise RuntimeError("Outlook is not connected. Please sign in with Outlook.")


def acquire_token_silent_once(force_refresh: bool = False, clear_on_failure: bool = False) -> str | None:
    """Try to restore or renew an access token from the persisted MSAL cache."""
    if config.is_mock_mode():
        return "mock-access-token"
    LOGGER.info("Starting MSAL silent token acquisition force_refresh=%s.", force_refresh)
    _delete_legacy_shared_cache()
    cache_owner = _authenticated_cache_owner()
    if not cache_owner:
        LOGGER.warning("MSAL silent token acquisition skipped: no authenticated cache owner for this session.")
        _remember_silent_token_result("no_cache_owner")
        return None
    token_cache, stored_account = _load_msal_token_cache(cache_owner)
    app = _build_msal_app(token_cache)
    account = _select_account(app, stored_account)
    if not account:
        LOGGER.warning("MSAL silent token acquisition skipped: no account found in token cache.")
        _remember_silent_token_result("no_account")
        return None

    result = _acquire_token_silent(app, account, SILENT_TOKEN_SCOPES, force_refresh=force_refresh)
    _persist_msal_token_cache(token_cache, account, cache_owner)
    if result and "access_token" in result:
        _validate_successful_token_result(result, "silent")
        _log_access_token_jwt_diagnostics(result, "silent")
        _store_token_result(result)
        refreshed_account = _account_from_result_or_cache(result, app, account)
        _remember_account_owner(refreshed_account, cache_owner)
        _persist_msal_token_cache(token_cache, refreshed_account, cache_owner)
        _remember_silent_token_result("access_token")
        LOGGER.info(
            "MSAL silent token acquisition succeeded for scopes=%s.",
            " ".join(granted_scopes(result)),
        )
        return str(result["access_token"])

    if result and ("error" in result or "error_description" in result):
        st.session_state[AUTH_ERROR_STATE_KEY] = _format_token_error(result)
        _remember_silent_token_result(str(result.get("error") or "token_error"))
        LOGGER.warning(
            "MSAL silent token acquisition failed: %s",
            _format_token_error(result),
            stack_info=True,
        )
    else:
        _remember_silent_token_result("no_result")
        LOGGER.warning("MSAL silent token acquisition returned no result.", stack_info=True)
    return None


def logout_user(clear_persisted: bool = True) -> None:
    """Disconnect Outlook by removing account and token data from session state."""
    flow_id = str(st.session_state.get(AUTH_STATE_KEY) or "")
    cache_owner = _authenticated_cache_owner()
    temporary_owner = _temporary_cache_owner()
    st.session_state[TOKEN_STATE_KEY] = {}
    st.session_state[ACCOUNT_STATE_KEY] = {}
    st.session_state[USER_STATE_KEY] = {}
    st.session_state[AUTH_STATE_KEY] = ""
    st.session_state[AUTH_ERROR_STATE_KEY] = ""
    st.session_state["outlook_messages_cache"] = []
    st.session_state["selected_outlook_messages"] = []
    st.session_state["outlook_selected_messages"] = []
    st.session_state["outlook_import_summary"] = None
    st.session_state[SILENT_RESULT_STATE_KEY] = ""
    st.session_state[TOKEN_CACHE_OWNER_STATE_KEY] = ""
    st.session_state[ACCOUNT_HOME_ID_STATE_KEY] = ""
    if clear_persisted:
        _clear_persisted_auth(cache_owner)
        _clear_persisted_auth(temporary_owner)
        if flow_id:
            database.delete_oauth_auth_flow(flow_id)


def is_connected() -> bool:
    """Return whether the current session or persisted cache has a usable token."""
    if config.is_mock_mode():
        return True

    token_result = st.session_state.get(TOKEN_STATE_KEY, {}) or {}
    access_token = str(token_result.get("access_token") or "")

    try:
        expires_at = int(token_result.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0

    if (
        access_token
        and expires_at > int(time.time()) + 60
        and _has_required_scope_when_available(token_result)
    ):
        LOGGER.info("Outlook is connected using current Streamlit session token.")
        return True

    silent_token = acquire_token_silent_once(
        force_refresh=False,
        clear_on_failure=False,
    )
    connected = bool(
        silent_token
        and _has_required_scope_when_available()
    )
    LOGGER.info("Outlook connected via persisted MSAL cache: %s.", connected)
    return connected


def token_exists() -> bool:
    """Return whether a current token or persisted MSAL cache exists."""
    token_result = st.session_state.get(TOKEN_STATE_KEY, {}) or {}
    access_token = token_result.get("access_token")
    expires_at = int(token_result.get("expires_at") or 0)
    if access_token and expires_at > int(time.time()) + 60:
        return True
    cache_owner = _authenticated_cache_owner()
    if not cache_owner:
        return False
    cache_json, _account = database.load_oauth_token_cache(cache_owner)
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
    return any(_scope_name(scope).lower() == wanted for scope in granted_scopes(token_result))


def access_token_audience(token_result: dict[str, Any] | None = None) -> str:
    """Return the access token audience claim without exposing the token."""
    token_data = token_result or st.session_state.get(TOKEN_STATE_KEY, {}) or {}
    access_token = str(token_data.get("access_token") or "")
    claims = _decode_jwt_payload(access_token)
    return str(claims.get("aud") or "opaque/unavailable")


def is_graph_access_token(token_result: dict[str, Any] | None = None) -> bool:
    """Return whether the access token is issued for Microsoft Graph."""
    return access_token_audience(token_result) in MICROSOFT_GRAPH_AUDIENCES


def connected_user() -> dict[str, Any]:
    """Return cached connected Microsoft user metadata."""
    cached = dict(st.session_state.get(USER_STATE_KEY) or {})
    if cached:
        return cached
    cache_owner = _authenticated_cache_owner()
    if not cache_owner:
        return {}
    _cache_json, account = database.load_oauth_token_cache(cache_owner)
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
        "username": (
            user.get("username")
            or user.get("mail")
            or user.get("userPrincipalName")
            or ""
        ),
        "id": user.get("id", ""),
    }
    cache_owner = _authenticated_cache_owner()
    if not cache_owner:
        return
    cache_json, account = database.load_oauth_token_cache(cache_owner)
    if cache_json:
        merged_account = dict(account or {})
        merged_account.update(st.session_state[USER_STATE_KEY])
        database.store_oauth_token_cache(
            cache_owner,
            cache_json,
            _safe_account_metadata(merged_account),
            int(time.time()),
        )


def auth_error() -> str:
    """Return the latest user-facing auth error."""
    return str(st.session_state.get(AUTH_ERROR_STATE_KEY, ""))


def auth_diagnostics() -> dict[str, str]:
    """Return safe Outlook auth diagnostics without exposing token material."""
    token_diagnostics = _session_token_diagnostics()
    if config.is_mock_mode():
        diagnostics = {
            "persisted_cache_exists": "No",
            "accounts_found": "0",
            "silent_token_result": "mock",
            "cache_saved_after_callback": "No",
        }
        diagnostics.update(token_diagnostics)
        return diagnostics
    cache_owner = _authenticated_cache_owner()
    try:
        cache_json, stored_account = _load_cache_record(cache_owner)
    except Exception:
        diagnostics = {
            "persisted_cache_exists": "Unknown",
            "accounts_found": "0",
            "silent_token_result": "database_error",
            "cache_saved_after_callback": "Yes" if _session_state_get(CALLBACK_CACHE_SAVED_STATE_KEY) else "No",
            "cache_owner": _safe_owner_label(cache_owner),
            "stored_account": "Unknown",
            "account_metadata_present": "Unknown",
            "username_present": "Unknown",
            "home_account_id_present": "Unknown",
            "cache_ownership_mode": "per-account",
            "exact_account_match_used": "Unknown",
        }
        diagnostics.update(token_diagnostics)
        return diagnostics
    accounts_count = 0
    try:
        token_cache = _new_msal_token_cache()
        if token_cache is not None and cache_json:
            token_cache.deserialize(cache_json)
        app = _build_msal_app(token_cache)
        accounts_count = len(_get_accounts(app))
    except Exception as exc:
        st.session_state[AUTH_ERROR_STATE_KEY] = _format_token_error(
            {"error": exc.__class__.__name__, "error_description": str(exc)}
        )
    account_match = _matching_account_exists(stored_account) if cache_owner else False
    diagnostics = {
        "persisted_cache_exists": "Yes" if cache_json else "No",
        "accounts_found": str(accounts_count),
        "silent_token_result": str(_session_state_get(SILENT_RESULT_STATE_KEY) or "not_run"),
        "cache_saved_after_callback": "Yes" if _session_state_get(CALLBACK_CACHE_SAVED_STATE_KEY) else "No",
        "cache_owner": _safe_owner_label(cache_owner),
        "stored_account": "Yes" if stored_account else "No",
        "account_metadata_present": "Yes" if stored_account or st.session_state.get(ACCOUNT_STATE_KEY) else "No",
        "username_present": "Yes" if (stored_account or st.session_state.get(ACCOUNT_STATE_KEY) or {}).get("username") else "No",
        "home_account_id_present": "Yes" if (stored_account or st.session_state.get(ACCOUNT_STATE_KEY) or {}).get("home_account_id") else "No",
        "cache_ownership_mode": "per-account",
        "exact_account_match_used": "Yes" if account_match else "No",
    }
    diagnostics.update(token_diagnostics)
    return diagnostics


def _store_token_result(result: dict[str, Any]) -> None:
    """Store only the current MSAL access-token result in session state."""
    access_token = str(result.get("access_token") or "")
    token_result: dict[str, Any] = {
        "access_token": access_token,
        "expires_in": int(result.get("expires_in") or 0),
        "scope": str(result.get("scope") or ""),
        "token_type": str(result.get("token_type") or "Bearer"),
    }
    if result.get("ext_expires_in") is not None:
        token_result["ext_expires_in"] = int(result.get("ext_expires_in") or 0)
    if isinstance(result.get("account"), dict):
        token_result["account"] = _safe_account_metadata(result["account"])
    if result.get("expires_at") not in (None, ""):
        token_result["expires_at"] = int(result.get("expires_at") or 0)
    else:
        token_result["expires_at"] = int(time.time()) + int(token_result.get("expires_in", 0))
    st.session_state[TOKEN_STATE_KEY] = token_result
    if result.get("account"):
        account = _safe_account_metadata(result["account"])
        st.session_state[ACCOUNT_STATE_KEY] = account
        if account.get("home_account_id"):
            st.session_state[ACCOUNT_HOME_ID_STATE_KEY] = account["home_account_id"]
        st.session_state[USER_STATE_KEY] = account


def _remember_account_owner(account: dict[str, Any], cache_owner: str) -> None:
    """Remember the authenticated account and per-account cache owner for this session."""
    safe_account = _safe_account_metadata(account)
    st.session_state[ACCOUNT_STATE_KEY] = safe_account
    st.session_state[USER_STATE_KEY] = safe_account
    st.session_state[TOKEN_CACHE_OWNER_STATE_KEY] = cache_owner
    st.session_state[ACCOUNT_HOME_ID_STATE_KEY] = str(safe_account.get("home_account_id") or "")


def _delete_temp_cache_if_needed(temporary_owner: str, permanent_owner: str) -> None:
    """Remove temporary per-browser cache after it has migrated to the account owner."""
    if temporary_owner and temporary_owner != permanent_owner:
        _clear_persisted_auth(temporary_owner)


def _ensure_auth_session_id() -> str:
    """Return the per-browser auth session id, creating one if needed."""
    session_id = str(st.session_state.get(AUTH_SESSION_ID_STATE_KEY) or "")
    if not session_id:
        session_id = secrets.token_urlsafe(32)
        st.session_state[AUTH_SESSION_ID_STATE_KEY] = session_id
    return session_id


def _temporary_cache_owner() -> str:
    """Return the temporary cache owner derived from the per-browser auth session id."""
    return f"session:{_sha256_text(_ensure_auth_session_id())}"


def _permanent_cache_owner(account: dict[str, Any]) -> str:
    """Return the permanent per-account cache owner."""
    home_account_id = str(account.get("home_account_id") or "")
    if not home_account_id:
        return ""
    return f"account:{_sha256_text(f'{home_account_id}|{config.CLIENT_ID}')}"


def _authenticated_cache_owner() -> str:
    """Return the current session's authenticated cache owner, never the legacy shared owner."""
    owner = str(st.session_state.get(TOKEN_CACHE_OWNER_STATE_KEY) or "")
    if owner == LEGACY_SHARED_CACHE_OWNER:
        return ""
    return owner if owner.startswith("account:") else ""


def _sha256_text(value: str) -> str:
    """Return a SHA-256 hex digest."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_owner_label(cache_owner: str | None) -> str:
    """Return a non-secret label for cache ownership diagnostics."""
    owner = str(cache_owner or "")
    if owner.startswith("account:"):
        return "per-account"
    if owner.startswith("session:"):
        return "per-browser-session"
    if owner == LEGACY_SHARED_CACHE_OWNER:
        return "legacy-shared-disabled"
    return "none"


def _new_msal_token_cache() -> Any | None:
    """Create an MSAL SerializableTokenCache when MSAL is available."""
    if msal is None:
        return None
    return msal.SerializableTokenCache()


def _load_msal_token_cache(cache_owner: str | None) -> tuple[Any | None, dict[str, Any] | None]:
    """Load the serialized MSAL cache and account metadata from SQLite."""
    _delete_legacy_shared_cache()
    token_cache = _new_msal_token_cache()
    cache_json, account = _load_cache_record(cache_owner)
    LOGGER.debug(
        "MSAL token cache load: cache_exists=%s account_exists=%s owner=%s",
        bool(cache_json),
        bool(account),
        _safe_owner_label(cache_owner),
    )
    if token_cache is not None and cache_json:
        token_cache.deserialize(cache_json)
        LOGGER.debug(
            "MSAL token cache load: deserialized=%s cache_changed=%s",
            True,
            bool(getattr(token_cache, "has_state_changed", False)),
        )
    return token_cache, account


def _persist_msal_token_cache(token_cache: Any | None, account: dict[str, Any] | None, cache_owner: str | None) -> bool:
    """Persist the MSAL token cache when it has changed."""
    if token_cache is None:
        LOGGER.debug("MSAL token cache save skipped: cache_exists=%s", False)
        return False
    if not cache_owner or cache_owner == LEGACY_SHARED_CACHE_OWNER:
        LOGGER.debug("MSAL token cache save skipped: no valid per-session/per-account owner.")
        return False
    if not getattr(token_cache, "has_state_changed", False):
        LOGGER.debug("MSAL token cache save skipped: cache_changed=%s", False)
        return False
    database.store_oauth_token_cache(
        cache_owner=cache_owner,
        cache_json=str(token_cache.serialize()),
        account=_safe_account_metadata(account or {}),
        updated_at=int(time.time()),
    )
    LOGGER.debug(
        "MSAL token cache save: saved=%s owner=%s account_exists=%s",
        True,
        _safe_owner_label(cache_owner),
        bool(account),
    )
    return True


def _verify_persisted_msal_cache(cache_owner: str) -> None:
    """Verify the callback persisted a reloadable MSAL cache."""
    cache_json, _account = database.load_oauth_token_cache(cache_owner)
    LOGGER.debug("MSAL token cache verify: cache_exists=%s", bool(cache_json))
    if not cache_json:
        raise RuntimeError("Microsoft token cache was not saved. Please connect Outlook again.")
    token_cache = _new_msal_token_cache()
    if token_cache is not None:
        token_cache.deserialize(cache_json)


def _remember_callback_cache_saved(saved: bool) -> None:
    """Remember whether the callback persisted the MSAL cache."""
    st.session_state[CALLBACK_CACHE_SAVED_STATE_KEY] = bool(saved)


def _remember_silent_token_result(status: str) -> None:
    """Remember safe silent-acquisition status for diagnostics."""
    st.session_state[SILENT_RESULT_STATE_KEY] = status


def _session_state_get(key: str, default: Any = None) -> Any:
    """Read Streamlit session state from dict-like or object-like test doubles."""
    getter = getattr(st.session_state, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(st.session_state, key, default)


def _session_auth_flow(flow_id: str) -> dict[str, Any] | None:
    """Return the saved Streamlit auth flow for this callback state."""
    flow = st.session_state.get(AUTH_FLOW_STATE_KEY)
    if not isinstance(flow, dict):
        return None
    return flow if str(flow.get("state") or "") == flow_id else None


def _safe_fingerprint(value: str) -> str:
    """Return a short non-secret fingerprint for correlating callback logs."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_code_debug(value: Any) -> str:
    """Describe an authorization code without logging the secret code itself."""
    code = str(value or "")
    if not code:
        return "present=False"
    return f"present=True length={len(code)} sha256={_safe_fingerprint(code)}"


def _session_token_diagnostics() -> dict[str, str]:
    """Return safe diagnostics for the current session token without exposing it."""
    token_result = _session_state_get(TOKEN_STATE_KEY, {}) or {}
    access_token = str(token_result.get("access_token") or "")
    expires_at = int(token_result.get("expires_at") or 0)
    now = int(time.time())
    claims = _decode_jwt_payload(access_token)
    scopes = str(claims.get("scp") or token_result.get("scope") or "").strip()
    audience = access_token_audience(token_result)
    return {
        "session_token_exists": "Yes" if access_token else "No",
        "session_token_expires_at": str(expires_at or ""),
        "session_token_expired": "Yes" if expires_at and expires_at <= now + 60 else "No",
        "session_token_aud": audience,
        "session_token_graph_audience_valid": (
            "Unknown" if audience == "opaque/unavailable"
            else "Yes" if audience in MICROSOFT_GRAPH_AUDIENCES
            else "No"
        ),
        "session_token_tid": str(claims.get("tid") or ""),
        "session_token_scopes": ", ".join(scope for scope in scopes.split() if scope),
        "session_current_timestamp": str(now),
    }


def _load_cache_record(cache_owner: str | None) -> tuple[str, dict[str, Any] | None]:
    """Load a cache record only for an explicit non-legacy owner."""
    if not cache_owner or cache_owner == LEGACY_SHARED_CACHE_OWNER:
        return "", None
    return database.load_oauth_token_cache(cache_owner)


def _clear_persisted_auth(cache_owner: str | None) -> None:
    """Remove persisted token cache and account metadata."""
    if cache_owner and cache_owner != LEGACY_SHARED_CACHE_OWNER:
        database.delete_oauth_token_cache(cache_owner)


def _delete_legacy_shared_cache() -> None:
    """Delete the legacy shared token cache and never load it again."""
    try:
        database.delete_oauth_token_cache(LEGACY_SHARED_CACHE_OWNER)
    except Exception:
        LOGGER.exception("Could not delete legacy shared Outlook token cache.")


def _select_account(app: Any, stored_account: dict[str, Any] | None) -> dict[str, Any] | None:
    """Find the best MSAL account for silent token acquisition."""
    accounts = _get_accounts(app)
    if not accounts:
        return None
    wanted_home_account_id = _authenticated_home_account_id(stored_account)
    if not wanted_home_account_id:
        LOGGER.warning("MSAL account selection skipped: no authenticated home_account_id in this session.")
        return None
    for account in accounts:
        if str(account.get("home_account_id") or "") == wanted_home_account_id:
            return account
    LOGGER.warning("MSAL account selection skipped: no exact home_account_id match.")
    return None


def _authenticated_home_account_id(stored_account: dict[str, Any] | None = None) -> str:
    """Return the current session's authenticated home account id."""
    session_home_id = str(st.session_state.get(ACCOUNT_HOME_ID_STATE_KEY) or "")
    if session_home_id:
        return session_home_id
    account = st.session_state.get(ACCOUNT_STATE_KEY) or {}
    home_id = str(account.get("home_account_id") or "")
    if home_id:
        return home_id
    return str((stored_account or {}).get("home_account_id") or "")


def _matching_account_exists(stored_account: dict[str, Any] | None) -> bool:
    """Return whether the stored account matches this session's account."""
    wanted = _authenticated_home_account_id(stored_account)
    return bool(wanted and str((stored_account or {}).get("home_account_id") or "") == wanted)


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
    claims = _decode_jwt_payload(str(result.get("access_token") or ""))
    if isinstance(result.get("account"), dict):
        return _safe_account_metadata(result["account"], claims)
    fallback = _safe_account_metadata(fallback_account or {}, claims)
    if fallback.get("home_account_id"):
        return fallback
    accounts = _get_accounts(app)
    if len(accounts) == 1:
        return _safe_account_metadata(accounts[0], claims)
    return fallback


def _safe_account_metadata(account: dict[str, Any], claims: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep only non-token account fields needed to find the MSAL account again."""
    safe_keys = (
        "home_account_id",
        "local_account_id",
        "username",
        "environment",
        "realm",
        "tenant_id",
        "tid",
        "displayName",
        "mail",
        "userPrincipalName",
        "id",
    )
    safe = {key: str(account.get(key, "")) for key in safe_keys if account.get(key)}
    claims = claims or {}
    tenant_id = str(claims.get("tid") or account.get("tenant_id") or account.get("realm") or "")
    if tenant_id:
        safe["tenant_id"] = tenant_id
        safe["tid"] = tenant_id
    return safe


def _acquire_token_silent(
    app: Any,
    account: dict[str, Any],
    scopes: list[str] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    """Call MSAL acquire_token_silent while tolerating older/test signatures."""
    requested_scopes = scopes or SILENT_TOKEN_SCOPES
    try:
        return app.acquire_token_silent(requested_scopes, account=account, force_refresh=force_refresh)
    except TypeError:
        return app.acquire_token_silent(requested_scopes, account=account)


def _rerun_after_callback() -> None:
    """Trigger a Streamlit rerun after a successful callback when available."""
    rerun = getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _session_auth_flow(flow_id: str) -> dict[str, Any] | None:
    """Return the saved Streamlit auth flow for this callback state."""
    flow = st.session_state.get(AUTH_FLOW_STATE_KEY)
    if not isinstance(flow, dict):
        return None
    return flow if str(flow.get("state") or "") == flow_id else None


def _safe_fingerprint(value: str) -> str:
    """Return a short non-secret fingerprint for correlating callback logs."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_code_debug(value: Any) -> str:
    """Describe an authorization code without logging the secret code itself."""
    code = str(value or "")
    if not code:
        return "present=False"
    return f"present=True length={len(code)} sha256={_safe_fingerprint(code)}"


def _scope_name(scope: str) -> str:
    """Return a scope's short delegated permission name."""
    return str(scope or "").rstrip("/").split("/")[-1]


def _validate_successful_token_result(result: dict[str, Any], source: str) -> None:
    """Validate only client-observable MSAL token fields without decoding resource claims."""
    if "access_token" not in result:
        raise RuntimeError(_format_token_error(result))
    access_token = result.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise RuntimeError("Microsoft token response did not include a usable access token.")
    _validate_token_expiry_fields(result, source)
    if _scope_information_available(result) and not has_granted_scope(REQUIRED_MAIL_SCOPE, result):
        granted = ", ".join(granted_scopes(result)) or "none"
        raise RuntimeError(f"Microsoft token did not include {REQUIRED_MAIL_SCOPE}. Granted scopes: {granted}.")


def _validate_token_expiry_fields(result: dict[str, Any], source: str) -> None:
    """Validate MSAL expiry values when they are provided."""
    for field in ("expires_in", "expires_at"):
        if result.get(field) in (None, ""):
            continue
        try:
            value = int(result.get(field) or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Microsoft token response included an invalid {field} value.") from exc
        if field == "expires_in" and value <= 0:
            raise RuntimeError("Microsoft token response included an expired access token.")
        if field == "expires_at" and value <= int(time.time()) + 60:
            raise RuntimeError("Microsoft token response included an expired access token.")
    LOGGER.debug("MSAL token expiry validation succeeded source=%s.", source)


def _scope_information_available(token_result: dict[str, Any] | None = None) -> bool:
    """Return whether MSAL or a decodable token exposed delegated scope information."""
    token_data = token_result if token_result is not None else st.session_state.get(TOKEN_STATE_KEY, {})
    if str((token_data or {}).get("scope") or "").strip():
        return True
    access_token = str((token_data or {}).get("access_token") or "")
    claims = _decode_jwt_payload(access_token)
    return bool(str(claims.get("scp") or "").strip())


def _has_required_scope_when_available(token_result: dict[str, Any] | None = None) -> bool:
    """Require Mail.Read only when scope information is locally available."""
    if not _scope_information_available(token_result):
        return True
    return has_granted_scope(REQUIRED_MAIL_SCOPE, token_result)


def _log_access_token_jwt_diagnostics(result: dict[str, Any], source: str) -> None:
    """Log safe JWT header/payload fields for the MSAL access token only."""
    access_token = str(result.get("access_token") or "")
    header = _decode_jwt_header(access_token)
    payload = _decode_jwt_payload(access_token)
    LOGGER.info(
        "MSAL access token diagnostics source=%s token_present=%s token_length=%s "
        "header_typ=%s payload_aud=%s payload_scp=%s payload_iss=%s payload_typ=%s "
        "id_token_present=%s id_token_claims_present=%s",
        source,
        bool(access_token),
        len(access_token),
        _safe_jwt_claim(header.get("typ")),
        _safe_jwt_claim(payload.get("aud") or "opaque/unavailable"),
        _safe_jwt_claim(payload.get("scp")),
        _safe_jwt_claim(payload.get("iss")),
        _safe_jwt_claim(payload.get("typ")),
        bool(result.get("id_token")),
        bool(result.get("id_token_claims")),
    )


def _validate_graph_access_token(result: dict[str, Any]) -> None:
    """Deprecated no-op: Microsoft Graph access tokens are opaque to this client."""
    _log_access_token_jwt_diagnostics(result, "optional_audience_diagnostic")


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


def _decode_jwt_header(access_token: str) -> dict[str, Any]:
    """Decode JWT header fields for diagnostics only; this does not verify the signature."""
    parts = access_token.split(".")
    if not parts:
        return {}
    header = parts[0]
    padding = "=" * (-len(header) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{header}{padding}")
        values = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return {}
    return values if isinstance(values, dict) else {}


def _safe_jwt_claim(value: Any) -> str:
    """Return a compact non-token JWT diagnostic value."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value)
    return str(value)


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
authorization_url = get_authorization_url
exchange_authorization_code = acquire_token_by_authorization_code
get_access_token = get_valid_access_token
