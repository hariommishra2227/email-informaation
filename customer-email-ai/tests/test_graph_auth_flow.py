"""Tests for MSAL auth-code-flow callback handling."""

from __future__ import annotations

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


class FakeMsalApp:
    """MSAL test double with deterministic flow and token behavior."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {
            "access_token": "header.payload.signature",
            "scope": "User.Read Mail.Read",
            "expires_in": 3600,
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
        return dict(self.result)


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
    monkeypatch.setattr(graph_auth, "_build_msal_app", lambda: app or FakeMsalApp())
    database.initialize_database(db_path)


def test_auth_code_flow_successful_state_match(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)

    graph_auth.create_login_url()
    state = fake_st.session_state[graph_auth.AUTH_STATE_KEY]
    fake_st.query_params.update({"code": "auth-code", "state": state})

    assert graph_auth.handle_auth_callback()
    assert graph_auth.token_exists()
    assert fake_st.query_params == {}


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

    assert not graph_auth.handle_auth_callback()
    assert "already used" in graph_auth.auth_error().lower()


def test_auth_code_flow_missing_stored_flow(monkeypatch) -> None:
    fake_st = FakeStreamlit()
    _configure_live_auth(monkeypatch, fake_st)
    fake_st.query_params.update({"code": "auth-code", "state": "missing-state"})

    assert not graph_auth.handle_auth_callback()
    assert "not found" in graph_auth.auth_error().lower()
