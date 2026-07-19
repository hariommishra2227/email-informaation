"""Tests for Microsoft Graph auth error handling."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from services import graph_auth, graph_client


def _unsigned_jwt(claims: dict[str, Any]) -> str:
    """Build an unsigned JWT-shaped token for diagnostics tests."""
    header = {"alg": "none", "typ": "JWT"}

    def encode(data: dict[str, Any]) -> str:
        payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(claims)}."


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
    monkeypatch.setitem(
        graph_auth.st.session_state,
        graph_auth.TOKEN_STATE_KEY,
        {"access_token": "latest-token"},
    )
    monkeypatch.setitem(
        graph_auth.st.session_state,
        graph_auth.ACCOUNT_STATE_KEY,
        {"username": "user@example.com", "home_account_id": "home-1"},
    )

    graph_client._graph_get("https://graph.microsoft.com/v1.0/me", "latest-token")

    assert captured["headers"]["Authorization"] == "Bearer latest-token"
    diagnostics = graph_client.last_graph_request_diagnostic()
    assert diagnostics["Authorization Header Present"] == "Yes"
    assert diagnostics["Bearer Prefix"] == "Yes"
    assert diagnostics["Token Length"] == str(len("latest-token"))
    assert diagnostics["Account Username"] == "user@example.com"
    assert diagnostics["Account Home Account ID"].startswith("hash:")
    assert "home-1" not in diagnostics["Account Home Account ID"]
    assert diagnostics["Current Token Hash"] == diagnostics["Latest MSAL Token Hash"]
    assert diagnostics["Silent Token Used"] == (
        "No - current session token used; silent acquisition skipped because session token was usable"
    )
    assert "latest-token" not in str(diagnostics)


def test_paginated_inbox_uses_renewed_token_after_first_401(monkeypatch) -> None:
    """A renewed token from the first page should be reused for later inbox pages."""
    calls = []

    class FakeResponse:
        reason = "OK"
        headers = {}
        text = "{}"

        def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
            self.status_code = status_code
            self.payload = payload

        def json(self) -> dict[str, Any]:
            return self.payload

    responses = [
        FakeResponse(401, {"error": {"code": "InvalidAuthenticationToken", "message": "stale token"}}),
        FakeResponse(
            200,
            {
                "value": [],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?page=2",
            },
        ),
        FakeResponse(200, {"value": []}),
    ]

    def fake_get(url, headers, timeout):
        calls.append(headers["Authorization"])
        return responses.pop(0)

    monkeypatch.setattr(graph_client.config, "OUTLOOK_MODE", graph_client.config.OUTLOOK_MODE_LIVE)
    monkeypatch.setattr(graph_client.config, "CLIENT_ID", "client-id")
    monkeypatch.setattr(graph_client.config, "CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(graph_client.config, "AUTHORITY", "https://login.microsoftonline.com/common")
    monkeypatch.setattr(graph_client.config, "REDIRECT_URI", "https://example.com/callback")
    monkeypatch.setattr(graph_auth, "get_valid_access_token", lambda: "old-token")
    monkeypatch.setattr(graph_auth, "acquire_token_silent_once", lambda force_refresh=False: "renewed-token")
    monkeypatch.setattr(graph_auth, "auth_diagnostics", lambda: {"silent_token_result": "access_token"})
    monkeypatch.setitem(
        graph_auth.st.session_state,
        graph_auth.TOKEN_STATE_KEY,
        {"access_token": "renewed-token"},
    )
    monkeypatch.setattr(graph_client.requests, "get", fake_get)

    graph_client.list_inbox_messages("user-1", limit=100)

    assert calls == [
        "Bearer old-token",
        "Bearer renewed-token",
        "Bearer renewed-token",
    ]


def test_graph_request_diagnostics_decode_safe_access_token_claims(monkeypatch) -> None:
    """The token sent to Graph should expose safe claims without exposing the JWT."""
    token = _unsigned_jwt(
        {
            "aud": "https://graph.microsoft.com",
            "iss": "https://sts.windows.net/tenant-id/",
            "tid": "tenant-id",
            "oid": "object-id",
            "azp": "client-id",
            "scp": "User.Read Mail.Read",
            "ver": "2.0",
            "exp": 4102444800,
            "iat": 1700000000,
        }
    )
    monkeypatch.setattr(graph_auth, "auth_diagnostics", lambda: {"silent_token_result": "access_token"})
    monkeypatch.setitem(graph_auth.st.session_state, graph_auth.TOKEN_STATE_KEY, {"access_token": token})

    diagnostics = graph_client._graph_request_diagnostics(
        "GET",
        "https://graph.microsoft.com/v1.0/me",
        token,
        graph_client._headers(token),
    )

    assert diagnostics["Token Claim aud"] == "https://graph.microsoft.com"
    assert diagnostics["Token Claim iss"] == "https://sts.windows.net/tenant-id/"
    assert diagnostics["Token Claim tid"] == "tenant-id"
    assert diagnostics["Token Claim oid"] == "object-id"
    assert diagnostics["Token Claim appid"] == ""
    assert diagnostics["Token Claim azp"] == "client-id"
    assert diagnostics["Token Claim scp"] == "User.Read Mail.Read"
    assert diagnostics["Token Claim roles"] == ""
    assert diagnostics["Token Claim ver"] == "2.0"
    assert diagnostics["Token Claim exp"] == "4102444800"
    assert diagnostics["Token Claim iat"] == "1700000000"
    assert diagnostics["Is Access Token"] == "Yes"
    assert diagnostics["Is ID Token"] == "No"
    assert diagnostics["Audience Equals Graph URL"] == "Yes"
    assert diagnostics["Contains Mail.Read Scope"] == "Yes"
    assert diagnostics["Token Delegation Type"] == "Delegated"
    assert token not in str(diagnostics)


def test_graph_request_diagnostics_identify_id_token(monkeypatch) -> None:
    """A token with the app client id as audience should be marked as an ID token."""
    monkeypatch.setattr(graph_client.config, "CLIENT_ID", "client-id")
    monkeypatch.setattr(graph_auth, "auth_diagnostics", lambda: {"silent_token_result": "not_run"})
    token = _unsigned_jwt(
        {
            "aud": "client-id",
            "iss": "https://login.microsoftonline.com/tenant-id/v2.0",
            "tid": "tenant-id",
            "oid": "object-id",
            "ver": "2.0",
            "exp": 4102444800,
            "iat": 1700000000,
        }
    )

    diagnostics = graph_client._graph_request_diagnostics(
        "GET",
        "https://graph.microsoft.com/v1.0/me",
        token,
        graph_client._headers(token),
    )

    assert diagnostics["Is Access Token"] == "No"
    assert diagnostics["Is ID Token"] == "Yes"
    assert diagnostics["Audience Equals Graph URL"] == "No"
    assert diagnostics["Contains Mail.Read Scope"] == "No"
    assert diagnostics["Token Delegation Type"] == "Unknown"
