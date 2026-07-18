"""Tests for Microsoft Graph auth error handling."""

from __future__ import annotations

from typing import Any

import pytest

from services import graph_auth, graph_client


class FakeResponse:
    """Minimal requests response for Graph client tests."""

    status_code = 401
    reason = "Unauthorized"

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
