"""Tests for root Streamlit OAuth callback handling."""

from __future__ import annotations

from typing import Any

import app
import config
import page_context
from services import graph_auth


class SwitchPage(BaseException):
    """Signal that Streamlit would stop on st.switch_page."""


class FakeStreamlit:
    """Small Streamlit facade for upgraded_main callback-order tests."""

    def __init__(self, query_params: dict[str, str] | None = None) -> None:
        self.query_params = query_params or {}
        self.session_state: dict[str, Any] = {}
        self.switched_to = ""

    def set_page_config(self, *args, **kwargs) -> None:
        return None

    def title(self, *args, **kwargs) -> None:
        return None

    def caption(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None

    def success(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def exception(self, *args, **kwargs) -> None:
        return None

    def page_link(self, *args, **kwargs) -> None:
        return None

    def columns(self, count, *args, **kwargs):
        return [_FakeColumn() for _ in range(int(count))]

    def metric(self, *args, **kwargs) -> None:
        return None

    def switch_page(self, page: str) -> None:
        self.switched_to = page
        raise SwitchPage(page)


class _FakeColumn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _configure_root_app(monkeypatch, fake_st: FakeStreamlit) -> None:
    monkeypatch.setattr(app, "st", fake_st)
    monkeypatch.setattr(app, "initialize_session_state", lambda: None)
    monkeypatch.setattr(app, "render_styles", lambda: None)
    monkeypatch.setattr(config, "is_mock_mode", lambda: False)
    monkeypatch.setattr(config, "APP_PAGE_ICON", "icon", raising=False)
    monkeypatch.setattr(config, "APP_USER_EMAIL", "user@example.com")
    monkeypatch.setattr(page_context, "initialize_outlook_session_state", lambda: None)
    monkeypatch.setattr(page_context, "initialize_database_safely", lambda: False)
    monkeypatch.setattr(page_context, "selected_user", lambda: "default_user")


def test_root_app_processes_callback_before_is_connected(monkeypatch) -> None:
    fake_st = FakeStreamlit({"code": "auth-code", "state": "state-1"})
    calls: list[str] = []
    _configure_root_app(monkeypatch, fake_st)

    def handle_callback() -> bool:
        calls.append("handle")
        return False

    def is_connected() -> bool:
        calls.append("is_connected")
        assert calls == ["handle", "is_connected"]
        return False

    monkeypatch.setattr(graph_auth, "handle_auth_callback", handle_callback)
    monkeypatch.setattr(graph_auth, "auth_error", lambda: "")
    monkeypatch.setattr(graph_auth, "is_connected", is_connected)

    app.upgraded_main()

    assert calls == ["handle", "is_connected"]


def test_successful_root_callback_switches_to_outlook_connector(monkeypatch) -> None:
    fake_st = FakeStreamlit({"code": "auth-code", "state": "state-1"})
    _configure_root_app(monkeypatch, fake_st)
    monkeypatch.setattr(graph_auth, "handle_auth_callback", lambda: True)
    monkeypatch.setattr(
        graph_auth,
        "is_connected",
        lambda: (_ for _ in ()).throw(AssertionError("is_connected should not run before switch_page")),
    )

    try:
        app.upgraded_main()
    except SwitchPage:
        pass

    assert fake_st.switched_to == "pages/Outlook Connector.py"
