"""Enterprise mailbox persistence, deduplication, and pagination tests."""

from __future__ import annotations

from pathlib import Path

import database
from models import OutlookMessage
from repository import MailboxRepository
from services import graph_auth, graph_client
from sync import MailboxSynchronizer, database_statistics


def _message(number: int, received: str | None = None) -> OutlookMessage:
    return OutlookMessage(
        message_id=f"message-{number}",
        internet_message_id=f"<message-{number}@example.com>",
        user_id="user@example.com",
        sender_name=f"Contact {number}",
        sender_email=f"contact{number}@example.com",
        subject=f"Subject {number}",
        body=f"Email: contact{number}@example.com",
        received_datetime=received or f"2026-01-01T00:{number % 60:02d}:00Z",
        is_read=False,
    )


def _rows(db_path: Path, table: str) -> list[dict]:
    connection = database.connect(db_path)
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
    finally:
        connection.close()


def test_sync_is_idempotent_and_skips_extraction_for_processed_messages(tmp_path: Path) -> None:
    db_path = tmp_path / "contacts.db"
    calls: list[str] = []

    def extract(message: OutlookMessage) -> dict[str, str]:
        calls.append(message.message_id)
        return {"email": message.sender_email, "name": message.sender_name}

    synchronizer = MailboxSynchronizer(db_path=db_path, extractor=extract)
    first = synchronizer.synchronize("user@example.com", message_pages=[[_message(1)]])
    second = synchronizer.synchronize("user@example.com", message_pages=[[_message(1)]])

    assert first.processed_emails == 1
    assert first.new_contacts == 1
    assert second.processed_emails == 0
    assert second.skipped_emails == 1
    assert calls == ["message-1"]
    assert len(_rows(db_path, "processed_emails")) == 1
    assert len(_rows(db_path, "contacts")) == 1


def test_contact_matching_updates_missing_fields_and_merges_fallback_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "contacts.db"
    messages = [
        _message(1, "2026-01-01T00:00:00Z"),
        _message(2, "2026-01-02T00:00:00Z"),
        _message(3, "2026-01-03T00:00:00Z"),
    ]
    contacts = {
        "message-1": {"name": "Ada Lovelace", "email": "ADA@example.com", "company": "Analytical"},
        "message-2": {"name": "Ada Lovelace", "email": "ada@example.com", "phone": "+1 555 1000"},
        "message-3": {"name": "Ada Lovelace", "phone": "+1 (555) 1000", "company": "Analytical"},
    }
    synchronizer = MailboxSynchronizer(
        db_path=db_path,
        extractor=lambda message: contacts[message.message_id],
    )

    result = synchronizer.synchronize("user@example.com", message_pages=[messages])
    rows = _rows(db_path, "contacts")

    assert result.new_contacts == 1
    assert result.updated_contacts == 1
    assert len(rows) == 1
    assert rows[0]["email"] == "ada@example.com"
    assert rows[0]["phone"] == "15551000"
    assert rows[0]["company"] == "Analytical"


def test_large_sync_processes_all_batches_and_persists_statistics(tmp_path: Path) -> None:
    db_path = tmp_path / "contacts.db"
    messages = [_message(index, f"2026-01-{index // 24 + 1:02d}T{index % 24:02d}:00:00Z") for index in range(205)]
    pages = [messages[:73], messages[73:151], messages[151:]]
    synchronizer = MailboxSynchronizer(
        db_path=db_path,
        batch_size=100,
        extractor=lambda message: {"email": message.sender_email},
    )

    result = synchronizer.synchronize("user@example.com", message_pages=pages)
    statistics = database_statistics(db_path)

    assert result.processed_emails == 205
    assert result.new_contacts == 205
    assert statistics["processed_emails"] == 205
    assert statistics["total_contacts"] == 205
    assert statistics["last_sync_datetime"] == messages[-1].received_datetime


def test_graph_mailbox_iterator_follows_every_next_link(monkeypatch) -> None:
    calls: list[str] = []
    payloads = [
        {"value": [{"id": "1", "receivedDateTime": "2026-01-01T00:00:00Z"}], "@odata.nextLink": "page-2"},
        {"value": [{"id": "2", "receivedDateTime": "2026-01-02T00:00:00Z"}], "@odata.nextLink": "page-3"},
        {"value": [{"id": "3", "receivedDateTime": "2026-01-03T00:00:00Z"}]},
    ]

    monkeypatch.setattr(graph_client.config, "is_mock_mode", lambda: False)
    monkeypatch.setattr(graph_auth, "get_valid_access_token", lambda: "token")

    def fake_get(url: str, token: str):
        calls.append(url)
        return payloads.pop(0), token

    monkeypatch.setattr(graph_client, "_graph_get_with_token", fake_get)
    pages = list(
        graph_client.iter_mailbox_message_pages(
            "user@example.com", received_after="2025-12-31T00:00:00Z", page_size=100
        )
    )

    assert [message.message_id for page in pages for message in page] == ["1", "2", "3"]
    assert calls[1:] == ["page-2", "page-3"]
    assert "$filter=receivedDateTime%20gt%202025-12-31T00:00:00Z" in calls[0]


def test_failed_batch_does_not_mark_email_or_advance_sync_state(tmp_path: Path) -> None:
    db_path = tmp_path / "contacts.db"

    def fail(_message: OutlookMessage) -> dict[str, str]:
        raise RuntimeError("extraction failure")

    synchronizer = MailboxSynchronizer(db_path=db_path, extractor=fail)
    try:
        synchronizer.synchronize("user@example.com", message_pages=[[_message(1)]])
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected extraction failure")

    assert _rows(db_path, "processed_emails") == []
    connection = database.connect(db_path)
    try:
        assert MailboxRepository(connection).get_last_sync_datetime() is None
    finally:
        connection.close()
