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
