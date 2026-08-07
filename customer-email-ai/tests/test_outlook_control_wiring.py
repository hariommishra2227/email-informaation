"""Focused tests for Outlook filter and action wiring."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from models import OutlookMessage
from services import graph_client


PAGE_PATH = Path(__file__).resolve().parents[1] / "pages" / "Outlook Connector.py"
SPEC = importlib.util.spec_from_file_location("outlook_connector_controls", PAGE_PATH)
PAGE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PAGE)


def _message(message_id: str, *, is_read: bool = False, sender_email: str = "sender@example.com") -> OutlookMessage:
    return OutlookMessage(
        message_id=message_id, user_id="u", sender_name="Sender", sender_email=sender_email,
        subject="Subject", body="Body", received_datetime="2026-08-07T10:00:00Z", is_read=is_read,
    )


@pytest.mark.parametrize(
    ("label", "expected_start"),
    (("Last 7 Days", "2026-08-01T00:00:00Z"), ("Last 30 Days", "2026-07-09T00:00:00Z"), ("Last 90 Days", "2026-05-10T00:00:00Z")),
)
def test_preset_date_filters_use_graph_boundaries(label: str, expected_start: str) -> None:
    date_range, received_after, received_before = PAGE._received_date_bounds(label, current_date=date(2026, 8, 7))
    assert date_range == []
    assert received_after == expected_start
    assert received_before == "2026-08-08T00:00:00Z"


def test_custom_date_range_uses_inclusive_ui_and_exclusive_graph_end() -> None:
    selected = (date(2026, 7, 1), date(2026, 7, 31))
    date_range, received_after, received_before = PAGE._received_date_bounds("Custom Date Range", selected)
    assert date_range == selected
    assert received_after == "2026-07-01T00:00:00Z"
    assert received_before == "2026-08-01T00:00:00Z"


def test_folder_date_limit_and_internal_option_are_sent_to_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(graph_client.config, "is_mock_mode", lambda: False)
    monkeypatch.setattr(graph_client.graph_auth, "get_valid_access_token", lambda: "token")

    def fake_get(url: str, token: str):
        captured.append(url)
        return {"value": []}, token

    monkeypatch.setattr(graph_client, "_graph_get_with_token", fake_get)
    graph_client.list_inbox_messages(
        "u", limit=37, folder="Sent Items", received_after="2026-08-01T00:00:00Z",
        received_before="2026-08-08T00:00:00Z", skip_internal=False,
    )
    assert "/mailFolders/sentitems/messages" in captured[0]
    assert "$top=37" in captured[0]
    assert "receivedDateTime" in captured[0]


def test_mock_internal_option_and_maximum_are_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [_message("internal", sender_email="EMPLOYEE@ITSIPL.COM"), _message("external")]
    monkeypatch.setattr(graph_client.config, "is_mock_mode", lambda: True)
    monkeypatch.setattr(graph_client, "list_mock_messages", lambda user_id, limit=50: messages[:limit])
    assert [item.message_id for item in graph_client.list_inbox_messages("u", limit=2, skip_internal=True)] == ["external"]
    assert [item.message_id for item in graph_client.list_inbox_messages("u", limit=1, skip_internal=False)] == ["internal"]


def test_cache_signature_changes_for_each_server_side_control() -> None:
    base = PAGE._message_cache_signature("Inbox", None, None, 100, True)
    assert base != PAGE._message_cache_signature("Archive", None, None, 100, True)
    assert base != PAGE._message_cache_signature("Inbox", "2026-08-01", None, 100, True)
    assert base != PAGE._message_cache_signature("Inbox", None, None, 200, True)
    assert base != PAGE._message_cache_signature("Inbox", None, None, 100, False)


def test_unread_action_is_limited_to_current_loaded_messages() -> None:
    assert PAGE._unread_loaded_message_ids([_message("one"), _message("two", is_read=True)]) == ["one"]


def test_sync_status_uses_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class Repository:
        def stats(self, user_id: str):
            calls.append(("stats", user_id))
            return {"total_contacts": 0}

    monkeypatch.setattr(PAGE, "EmailSyncRepository", Repository)
    monkeypatch.setattr(PAGE.database, "get_latest_sync_job", lambda user_id: calls.append(("job", user_id)))
    PAGE._sync_status_data("current-user")
    assert calls == [("stats", "current-user"), ("job", "current-user")]
