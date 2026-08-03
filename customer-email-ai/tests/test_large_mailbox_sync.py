from __future__ import annotations

from pathlib import Path

from models import OutlookMessage
from services import graph_client
from services.email_processor import process_outlook_message
from storage import database


def _message(number: int) -> OutlookMessage:
    return OutlookMessage(
        message_id=f"large-{number}", user_id="u", sender_name="Ada Lovelace",
        sender_email=f"ada{number}@example.com", subject="Hello", body="Ada Lovelace ada@example.com",
        received_datetime=f"2026-01-01T00:{number:02d}:00Z", is_read=False,
    )


def test_same_message_id_is_not_inserted_twice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "mail.db")
    database.initialize_database()
    message = _message(1)
    process_outlook_message("u", message)
    process_outlook_message("u", message)
    assert len(database.list_customers("u")) == 1
    assert database.message_processing_status("u", message.message_id) == "Already Processed"


def test_graph_iterator_caps_requests_at_fifty_and_follows_next_link(monkeypatch) -> None:
    calls: list[str] = []
    payloads = [
        {"value": [{"id": "1"}], "@odata.nextLink": "next"},
        {"value": [{"id": "2"}]},
    ]
    monkeypatch.setattr(graph_client.config, "is_mock_mode", lambda: False)
    monkeypatch.setattr(graph_client.graph_auth, "get_valid_access_token", lambda: "token")
    monkeypatch.setattr(graph_client, "_graph_get_with_token", lambda url, token, *args: (calls.append(url) or (payloads.pop(0), token)))
    pages = list(graph_client.iter_mailbox_message_pages("u", page_size=500))
    assert [m.message_id for page in pages for m in page] == ["1", "2"]
    assert "$top=50" in calls[0]
    assert calls[1] == "next"


def test_retryable_graph_status_uses_retry_after(monkeypatch) -> None:
    class Response:
        reason = "Busy"
        text = "{}"
        headers = {"Retry-After": "0"}
        def __init__(self, status_code: int): self.status_code = status_code
        def json(self): return {}

    responses = [Response(503), Response(200)]
    sleeps: list[float] = []
    monkeypatch.setattr(graph_client.requests, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(graph_client.time, "sleep", lambda value: sleeps.append(value))
    payload, _ = graph_client._graph_get_with_token("https://graph.example", "token")
    assert payload == {}
    assert sleeps == [0.0]
