"""Tests for Outlook loaded-email selection behavior."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from models import OutlookMessage


PAGE_PATH = Path(__file__).resolve().parents[1] / "pages" / "Outlook Connector.py"
SPEC = importlib.util.spec_from_file_location("outlook_connector_selection", PAGE_PATH)
PAGE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(PAGE)


class SessionStateMock(dict):
    """Dictionary-compatible session-state test double."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


def _message(message_id: str) -> OutlookMessage:
    return OutlookMessage(
        message_id=message_id,
        user_id="user",
        sender_name="Sender",
        sender_email="sender@example.com",
        subject="Subject",
        body="Body",
        received_datetime="2026-01-01T00:00:00Z",
        is_read=False,
    )


def test_select_all_selects_only_loaded_emails() -> None:
    PAGE.st.session_state = SessionStateMock(selected_outlook_messages=["existing"])
    messages = [_message("one"), _message("two")]

    selected = PAGE._update_selected_outlook_messages(messages, True)

    assert selected == ["existing", "one", "two"]


def test_deselect_all_clears_current_selections_only() -> None:
    PAGE.st.session_state = SessionStateMock(
        selected_outlook_messages=["one", "two"],
        previous_select_all_outlook_messages=True,
    )

    selected = PAGE._update_selected_outlook_messages([_message("one"), _message("two")], False)

    assert selected == []
    assert PAGE.st.session_state["outlook_selected_messages"] == []


def test_individual_selection_remains_when_select_all_is_disabled() -> None:
    PAGE.st.session_state = SessionStateMock(selected_outlook_messages=["one"])

    selected = PAGE._update_selected_outlook_messages([_message("one"), _message("two")], False)

    assert selected == ["one"]


def test_select_all_supports_1000_loaded_emails() -> None:
    PAGE.st.session_state = SessionStateMock()
    messages = [_message(f"message-{index}") for index in range(1000)]

    selected = PAGE._update_selected_outlook_messages(messages, True)

    assert len(selected) == 1000
    assert selected[-1] == "message-999"


def test_import_skips_duplicate_message_ids(monkeypatch) -> None:
    class Progress:
        def progress(self, _value):
            return None

    class FakeStreamlit:
        session_state = SessionStateMock(imported_outlook_message_ids=["already-processed"])

        @staticmethod
        def subheader(*_args, **_kwargs):
            return None

        @staticmethod
        def progress(_value):
            return Progress()

    monkeypatch.setattr(PAGE, "st", FakeStreamlit)
    process_calls = []
    monkeypatch.setattr(PAGE, "process_outlook_message", lambda *_args: process_calls.append(True))

    PAGE._import_messages("user", [_message("already-processed")], ["already-processed"])

    assert process_calls == []
    assert FakeStreamlit.session_state["outlook_import_summary"]["duplicates_skipped"] == 1


def test_selected_email_extraction_processes_and_reports_saved_customer(monkeypatch) -> None:
    class Progress:
        def progress(self, _value):
            return None

    class FakeStreamlit:
        session_state = SessionStateMock()

        @staticmethod
        def subheader(*_args, **_kwargs):
            return None

        @staticmethod
        def progress(_value):
            return Progress()

    class Result:
        status = "Unique"

    monkeypatch.setattr(PAGE, "st", FakeStreamlit)
    processed = []
    monkeypatch.setattr(
        PAGE,
        "process_outlook_message",
        lambda user_id, message: processed.append((user_id, message.message_id)) or Result(),
    )

    PAGE._import_messages("user", [_message("selected")], ["selected"])

    assert processed == [("user", "selected")]
    assert FakeStreamlit.session_state["outlook_import_summary"] == {
        "selected_emails": 1,
        "emails_processed": 1,
        "customers_extracted": 1,
        "duplicates_skipped": 0,
        "incomplete_records": 0,
        "failed_records": 0,
    }


def test_internal_email_is_not_reported_as_extracted_customer(monkeypatch) -> None:
    class Progress:
        def progress(self, _value):
            return None

    class FakeStreamlit:
        session_state = SessionStateMock()

        @staticmethod
        def subheader(*_args, **_kwargs):
            return None

        @staticmethod
        def progress(_value):
            return Progress()

    class Result:
        status = "Skipped Internal"

    monkeypatch.setattr(PAGE, "st", FakeStreamlit)
    monkeypatch.setattr(PAGE, "process_outlook_message", lambda *_args: Result())

    PAGE._import_messages("user", [_message("internal")], ["internal"])

    summary = FakeStreamlit.session_state["outlook_import_summary"]
    assert summary["customers_extracted"] == 0
    assert summary["duplicates_skipped"] == 1


def test_customer_excel_is_disabled_when_no_processed_outlook_records(monkeypatch) -> None:
    calls = []

    class FakeStreamlit:
        @staticmethod
        def button(label, **kwargs):
            calls.append(("button", label, kwargs))

        @staticmethod
        def caption(message):
            calls.append(("caption", message, {}))

    monkeypatch.setattr(PAGE, "st", FakeStreamlit)
    monkeypatch.setattr(
        PAGE,
        "get_customer_page",
        lambda user_id, **kwargs: calls.append(("query", user_id, kwargs)) or {"rows": []},
    )
    monkeypatch.setattr(PAGE, "create_large_excel_export", lambda *_args, **_kwargs: pytest.fail("empty state must not export"))

    PAGE._render_excel_export("user")

    assert ("query", "user", {"page": 1, "page_size": 1, "source": "Outlook"}) in calls
    assert ("button", "Download Customer Excel", {"disabled": True, "use_container_width": True}) in calls
    assert ("caption", "Extract customer emails first to enable Excel download.", {}) in calls
