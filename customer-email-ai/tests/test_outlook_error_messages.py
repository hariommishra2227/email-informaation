"""Tests for Outlook Connector authentication error messages."""

from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTLOOK_PAGE = PROJECT_ROOT / "pages" / "Outlook Connector.py"


def _load_outlook_page():
    spec = importlib.util.spec_from_file_location("outlook_connector_page", OUTLOOK_PAGE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_login_error_with_mail_read_keeps_real_exception_details() -> None:
    """Login URL failures should not be hidden as Mail.Read permission failures."""
    page = _load_outlook_page()
    exc = RuntimeError("MSAL login URL creation failed while requesting Mail.Read")

    message = page._safe_auth_exception_message(exc)

    assert "Mail.Read permission is missing" not in message
    assert "RuntimeError" in message
    assert "MSAL login URL creation failed" in message


def test_mail_read_message_is_only_for_graph_permission_errors() -> None:
    """The Mail.Read friendly text is reserved for genuine Graph permission failures."""
    page = _load_outlook_page()
    exc = RuntimeError(
        "Microsoft Graph HTTP 403 Authorization_RequestDenied: "
        "insufficient privileges or missing Mail.Read scope"
    )

    assert page._friendly_exception_message(exc) == "The Mail.Read permission is missing or has not been approved."


def test_invalid_client_has_specific_auth_message() -> None:
    """Invalid client-secret failures should get a specific Azure credential message."""
    page = _load_outlook_page()
    exc = RuntimeError("Microsoft token error: invalid_client. AADSTS7000215")

    assert page._safe_auth_exception_message(exc) == "The Azure client secret is invalid or expired."


def test_connection_panel_does_not_load_inbox_without_token(monkeypatch) -> None:
    """The connection panel should stop inbox loading when Outlook is not connected."""
    page = _load_outlook_page()
    calls = {"list_inbox": 0}

    monkeypatch.setattr(page.config, "is_mock_mode", lambda: False)
    monkeypatch.setattr(page.config, "missing_live_settings", lambda: [])
    monkeypatch.setattr(page.config, "REDIRECT_URI", "https://example.com/Outlook_Connector")
    monkeypatch.setattr(page.config, "CLIENT_ID", "client-id")
    monkeypatch.setattr(page.config, "CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(page.config, "AUTHORITY", "https://login.microsoftonline.com/common")
    monkeypatch.setattr(page.config, "TENANT_ID", "")
    monkeypatch.setattr(page.config, "GRAPH_SCOPES", ["User.Read", "Mail.Read"])
    monkeypatch.setattr(page.config, "is_microsoft_configured", lambda: True)
    monkeypatch.setattr(page.graph_auth, "handle_auth_callback", lambda: False)
    monkeypatch.setattr(page.graph_auth, "is_connected", lambda: False)
    monkeypatch.setattr(page.graph_auth, "auth_error", lambda: "")
    monkeypatch.setattr(page.graph_auth, "connected_user", lambda: {})
    monkeypatch.setattr(page.graph_auth, "token_exists", lambda: False)
    monkeypatch.setattr(page.graph_auth, "granted_scopes", lambda: [])
    monkeypatch.setattr(page.graph_auth, "create_login_url", lambda: "https://login.example.com")

    def fake_list_inbox(*args, **kwargs):
        calls["list_inbox"] += 1
        return []

    monkeypatch.setattr(page.graph_client, "list_inbox_messages", fake_list_inbox)

    class FakeColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeStreamlit:
        session_state = {}

        @staticmethod
        def subheader(*args, **kwargs):
            return None

        @staticmethod
        def columns(spec):
            return [FakeColumn() for _ in spec]

        @staticmethod
        def metric(*args, **kwargs):
            return None

        @staticmethod
        def button(*args, **kwargs):
            return False

        @staticmethod
        def link_button(*args, **kwargs):
            return None

        @staticmethod
        def error(*args, **kwargs):
            return None

        @staticmethod
        def warning(*args, **kwargs):
            return None

        @staticmethod
        def caption(*args, **kwargs):
            return None

        @staticmethod
        def write(*args, **kwargs):
            return None

        @staticmethod
        def expander(*args, **kwargs):
            return FakeColumn()

    monkeypatch.setattr(page, "st", FakeStreamlit)

    assert not page._render_connection_panel()
    assert calls["list_inbox"] == 0
