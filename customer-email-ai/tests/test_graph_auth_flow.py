"""Tests for MSAL auth-code-flow callback handling."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import config
from services import graph_auth
from storage import database


class FakeQueryParams(dict):
    """Small stand-in for Streamlit query params."""

    def clear(self) -> None:
        super().clear()


class FakeSessionState(dict):
    """Small stand-in for Streamlit session state."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class FakeStreamlit:
    """Minimal Streamlit facade used by graph_auth."""

    def __init__(self) -> None:
        self.query_params = FakeQueryParams()
        self.session_state = FakeSessionState()


class FakeSerializableTokenCache:
    """Serializable cache stand-in with MSAL-like state tracking."""

    def __init__(self) -> None:
        self.payload = ""
        self.has_state_changed = False

    def serialize(self) -> str:
        return self.payload or '{"tokens": true}'

    def deserialize(self, payload: str) -> None:
        self.payload = payload
        self.has_state_changed = False


class FakeMsalModule:
    """Tiny MSAL module facade used by graph_auth."""

    SerializableTokenCache = FakeSerializableTokenCache


class FakeMsalApp:
    """MSAL test double with deterministic flow and token behavior."""

    def __init__(self, token_cache: FakeSerializableTokenCache | None = None, result: dict[str, Any] | None = None) -> None:
        self.token_cache = token_cache
        self.silent_calls = 0
        self.silent_result: dict[str, Any] | None = None
        self.accounts = [
            {
                "home_account_id": "home-1",
                "local_account_id": "local-1",
                "username": "user@example.com",
            }
        ]
        self.result = result or {
            "access_token": "header.payload.signature",
            "scope": "User.Read Mail.Read",
            "expires_in": 3600,
            "account": dict(self.accounts[0]),
        }

    def initiate_auth_code_flow(
        self,
        scopes: list[str],
        redirect_uri: str,
        state: str,
        prompt: str,
    ) -> dict[str, Any]:
        return {
            "auth_uri": f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?state={state}",
            "state": state,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "prompt": prompt,
        }

    def acquire_token_by_auth_code_flow(
        self,
        flow: dict[str, Any],
        callback_params: dict[str, Any],
    ) -> dict[str, Any]:
        if flow.get("state") != callback_params.get("state"):
            return {"error": "state_mismatch", "error_description": "State mismatch from MSAL."}
        if self.token_cache is not None:
            self.token_cache.payload = '{"cached": true}'
            self.token_cache.has_state_changed = True
        return dict(self.result)

    def acquire_token_silent(
        self,
        scopes: list[str],
        account: dict[str, Any],
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        self.silent_calls += 1
        if self.silent_result is not None:
            return dict(self.silent_result)
        token = "renewed-token" if force_refresh else "silent-token"
        return {
            "access_token": token,
            "scope": " ".join(scopes),
            "expires_in": 3600,
            "account": dict(account),
        }

    def get_accounts(self) -> list[dict[str, Any]]:
        return list(self.accounts)


def _configure_live_auth(monkeypatch, fake_st: FakeStreamlit, app: FakeMsalApp | None = None) -> None:
    db_path = ":memory:"
    monkeypatch.setattr(database, "_MEMORY_CONNECTION", None)
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    monkeypatch.setattr(config, "OUTLOOK_MODE", config.OUTLOOK_MODE_LIVE)
    monkeypatch.setattr(config, "CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "AUTHORITY", "https://login.microsoftonline.com/common")
    monkeypatch.setattr(
        config,
        "REDIRECT_URI",
        "https://email-informaation-frmrxrcergpwxbvh5lcqux.streamlit.app/Outlook_Connector",
    )
    monkeypatch.setattr(graph_auth, "st", fake_st)
    monkeypatch.setattr(graph_auth, "msal", FakeMsalModule)

    def build_app(token_cache=None):
        if app is not None:
            app.token_cache = token_cache
            return app
        return FakeMsalApp(token_cache)

    monkeypatch.setattr(graph_auth, "_build_msal_app", build_app)
    database.initialize_database(db_path)


def _unsigned_jwt_with_claims(claims: dict[str, Any]) -> str:
    """Return an unsigned JWT-shaped string for safe claim decoding tests."""
    header = {"alg": "none", "typ": "JWT"}

    def encode(data: dict[str, Any]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(claims)}."


def test_auth_code_flow_successful_state_match(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)

    graph_auth.create_login_url()
    state = fake_st.session_state[graph_auth.AUTH_STATE_KEY]
    fake_st.query_params.update({"code": "auth-code", "state": state})

    assert graph_auth.handle_auth_callback()
    assert graph_auth.token_exists()
    assert fake_st.query_params == {}
    assert fake_st.session_state[graph_auth.CALLBACK_CACHE_SAVED_STATE_KEY]
    cache_json, account = database.load_oauth_token_cache(config.DEFAULT_USER_ID)
    assert cache_json
    assert account["home_account_id"] == "home-1"


def test_auth_code_flow_mismatched_state(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)

    graph_auth.create_login_url()
    fake_st.query_params.update({"code": "auth-code", "state": "wrong-state"})

    assert not graph_auth.handle_auth_callback()
    assert "not found" in graph_auth.auth_error().lower()
    assert not graph_auth.token_exists()


def test_auth_code_flow_expired(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)
    flow = {"state": "expired-state", "auth_uri": "https://example.com"}
    database.store_oauth_auth_flow("expired-state", flow, created_at=1, expires_at=2)
    fake_st.query_params.update({"code": "auth-code", "state": "expired-state"})

    assert not graph_auth.handle_auth_callback()
    assert "expired" in graph_auth.auth_error().lower()


def test_auth_code_flow_callback_processed_twice(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)

    graph_auth.create_login_url()
    state = fake_st.session_state[graph_auth.AUTH_STATE_KEY]
    fake_st.query_params.update({"code": "auth-code", "state": state})

    assert graph_auth.handle_auth_callback()
    fake_st.query_params.update({"code": "auth-code", "state": state})

    assert graph_auth.handle_auth_callback()
    assert "already used" not in graph_auth.auth_error().lower()
    assert fake_st.query_params == {}


def test_failed_auth_code_exchange_keeps_flow_until_success(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    app = FakeMsalApp(result={"error": "temporarily_unavailable", "error_description": "Try again later."})
    _configure_live_auth(monkeypatch, fake_st, app=app)

    graph_auth.create_login_url()
    state = fake_st.session_state[graph_auth.AUTH_STATE_KEY]
    fake_st.query_params.update({"code": "auth-code", "state": state})

    assert not graph_auth.handle_auth_callback()
    assert "temporarily_unavailable" in graph_auth.auth_error()
    status, flow = database.load_oauth_auth_flow(state, now=int(time.time()))
    assert status == "ok"
    assert flow and flow["state"] == state
    assert fake_st.session_state[graph_auth.AUTH_FLOW_STATE_KEY]["state"] == state
    assert fake_st.query_params == {}


def test_auth_code_flow_missing_stored_flow(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)
    fake_st.query_params.update({"code": "auth-code", "state": "missing-state"})

    assert not graph_auth.handle_auth_callback()
    assert "not found" in graph_auth.auth_error().lower()


def test_cache_restored_after_new_streamlit_session(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)
    graph_auth.create_login_url()
    state = fake_st.session_state[graph_auth.AUTH_STATE_KEY]
    fake_st.query_params.update({"code": "auth-code", "state": state})
    assert graph_auth.handle_auth_callback()

    new_fake_st = FakeStreamlit()
    monkeypatch.setattr(graph_auth, "st", new_fake_st)

    assert graph_auth.get_valid_access_token() == "silent-token"
    assert graph_auth.token_exists()
    assert new_fake_st.session_state[graph_auth.SILENT_RESULT_STATE_KEY] == "access_token"


def test_fresh_callback_token_is_used_immediately(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    app = FakeMsalApp()
    _configure_live_auth(monkeypatch, fake_st, app=app)
    graph_auth.create_login_url()
    state = fake_st.session_state[graph_auth.AUTH_STATE_KEY]
    fake_st.query_params.update({"code": "auth-code", "state": state})

    assert graph_auth.handle_auth_callback()
    assert graph_auth.get_valid_access_token() == "header.payload.signature"
    assert app.silent_calls == 0


def test_valid_session_token_bypasses_silent_acquisition(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    app = FakeMsalApp()
    _configure_live_auth(monkeypatch, fake_st, app=app)
    fake_st.session_state[graph_auth.TOKEN_STATE_KEY] = {
        "access_token": "session-token",
        "scope": "User.Read Mail.Read",
        "expires_at": int(time.time()) + 3600,
    }

    assert graph_auth.get_valid_access_token() == "session-token"
    assert app.silent_calls == 0


def test_expired_session_token_uses_silent_acquisition(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    app = FakeMsalApp()
    _configure_live_auth(monkeypatch, fake_st, app=app)
    database.store_oauth_token_cache(
        config.DEFAULT_USER_ID,
        '{"cached": true}',
        {"home_account_id": "home-1", "username": "user@example.com"},
        123,
    )
    fake_st.session_state[graph_auth.TOKEN_STATE_KEY] = {
        "access_token": "expired-session-token",
        "scope": "User.Read Mail.Read",
        "expires_at": int(time.time()) - 1,
    }

    assert graph_auth.get_valid_access_token() == "silent-token"
    assert app.silent_calls == 1


def test_valid_mail_read_token_keeps_user_connected_after_rerun(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    app = FakeMsalApp()
    app.accounts = []
    _configure_live_auth(monkeypatch, fake_st, app=app)
    fake_st.session_state[graph_auth.TOKEN_STATE_KEY] = {
        "access_token": _unsigned_jwt_with_claims(
            {"aud": "https://graph.microsoft.com", "tid": "tenant-1", "scp": "User.Read Mail.Read"}
        ),
        "expires_at": int(time.time()) + 3600,
    }

    assert graph_auth.is_connected()
    assert app.silent_calls == 0


def test_silent_token_renewal_force_refresh(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)
    database.store_oauth_token_cache(
        config.DEFAULT_USER_ID,
        '{"cached": true}',
        {"home_account_id": "home-1", "username": "user@example.com"},
        123,
    )

    assert graph_auth.acquire_token_silent_once(force_refresh=True) == "renewed-token"


def test_missing_or_expired_token_keeps_persisted_cache(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    empty_app = FakeMsalApp()
    empty_app.accounts = []
    _configure_live_auth(monkeypatch, fake_st, app=empty_app)
    database.store_oauth_token_cache(
        config.DEFAULT_USER_ID,
        '{"cached": true}',
        {"home_account_id": "missing"},
        123,
    )

    assert graph_auth.acquire_token_silent_once(clear_on_failure=True) is None
    cache_json, _account = database.load_oauth_token_cache(config.DEFAULT_USER_ID)
    assert cache_json == '{"cached": true}'


def test_passive_is_connected_check_does_not_delete_cache(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    empty_app = FakeMsalApp()
    empty_app.accounts = []
    _configure_live_auth(monkeypatch, fake_st, app=empty_app)
    database.store_oauth_token_cache(
        config.DEFAULT_USER_ID,
        '{"cached": true}',
        {"home_account_id": "missing"},
        123,
    )

    assert not graph_auth.is_connected()
    cache_json, _account = database.load_oauth_token_cache(config.DEFAULT_USER_ID)
    assert cache_json == '{"cached": true}'
