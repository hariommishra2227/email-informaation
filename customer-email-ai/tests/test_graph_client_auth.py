"""Tests for Microsoft Graph auth error handling."""

from __future__ import annotations

from typing import Any

import pytest

from services import graph_auth, graph_client


class FakeResponse:
    """Minimal requests response for Graph client tests."""

    status_code = 401
    reason = "Unauthorized"
    headers = {
        "WWW-Authenticate": (
            'Bearer realm="", authorization_uri="https://login.microsoftonline.com/common/oauth2/authorize", '
            'error="invalid_token", error_description="Access token eyJabc.def.ghi is invalid", '
            'access_token="secret-token-value"'
        )
    }
    text = ""

    def json(self) -> dict[str, Any]:
        return {
            "error": {
                "code": "InvalidAuthenticationToken",
                "message": "Access token has expired.",
            }
        }


def test_first_graph_401_does_not_immediately_destroy_authentication(monkeypatch) -> None:
    """A 401 should try silent renewal and preserve auth/cache for diagnostics."""
    calls = {"silent": 0, "logout": 0}

    def fake_silent_renewal(force_refresh: bool = False):
        calls["silent"] += 1
        assert force_refresh
        return None

    def fail_if_logout_called(*args, **kwargs):
        calls["logout"] += 1
        raise AssertionError("logout_user should not be called on first Graph 401")

    monkeypatch.setattr(graph_client.requests, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(graph_auth, "acquire_token_silent_once", fake_silent_renewal)
    monkeypatch.setattr(graph_auth, "logout_user", fail_if_logout_called)

    with pytest.raises(RuntimeError) as exc_info:
        graph_client._graph_get("https://graph.microsoft.com/v1.0/me", "expired-token")

    assert calls == {"silent": 1, "logout": 0}
    message = str(exc_info.value)
    assert "Microsoft Graph HTTP 401 InvalidAuthenticationToken" in message
    assert "Access token has expired" in message
    assert "expired-token" not in message
    assert exc_info.value.authenticate_header
    assert "invalid_token" in exc_info.value.authenticate_header
    assert "[redacted-token]" in exc_info.value.authenticate_header
    assert "secret-token-value" not in exc_info.value.authenticate_header
    assert exc_info.value.diagnostics["Authorization Header Present"] == "Yes"
    assert exc_info.value.diagnostics["Bearer Prefix"] == "Yes"
    assert exc_info.value.diagnostics["Token Length"] == str(len("expired-token"))
    assert exc_info.value.diagnostics["HTTP Status"] == "401"
    assert exc_info.value.diagnostics["WWW-Authenticate"] == exc_info.value.authenticate_header


def test_graph_get_sends_exact_bearer_authorization_header(monkeypatch) -> None:
    """Graph requests should send exactly Authorization: Bearer <access_token>."""
    captured = {}

    class OkResponse:
        status_code = 200
        reason = "OK"
        headers = {}
        text = "{}"

        def json(self) -> dict[str, Any]:
            return {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return OkResponse()

    monkeypatch.setattr(graph_client.requests, "get", fake_get)
    monkeypatch.setattr(graph_auth, "auth_diagnostics", lambda: {"silent_token_result": "not_run"})
    monkeypatch.setitem(graph_auth.st.session_state, graph_auth.TOKEN_STATE_KEY, {"access_token": "latest-token"})

    graph_client._graph_get("https://graph.microsoft.com/v1.0/me", "latest-token")

    assert captured["headers"]["Authorization"] == "Bearer latest-token"
    diagnostics = graph_client.last_graph_request_diagnostic()
    assert diagnostics["Authorization Header Present"] == "Yes"
    assert diagnostics["Bearer Prefix"] == "Yes"
    assert diagnostics["Token Length"] == str(len("latest-token"))
    assert diagnostics["Silent Token Used"] == (
        "No - current session token used; silent acquisition skipped because session token was usable"
    )
    assert "latest-token" not in str(diagnostics)
