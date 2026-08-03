"""Production-readiness regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from services import graph_client
from services.health import health_status
from storage import attachments, database


@pytest.fixture()
def isolated_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a fresh in-memory database."""
    database.DATABASE_PATH = Path(":memory:")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    database.initialize_database()


def test_attachment_filename_sanitization_blocks_traversal() -> None:
    assert attachments.sanitize_filename("../secret/report?.pdf") == "report_.pdf"


def test_health_check_reports_database_ok(isolated_db: None) -> None:
    assert health_status()["database"] == "ok"


def test_graph_retry_respects_transient_429(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.headers = {"Retry-After": "0"}
            self.text = "{}"
            self.reason = ""

        def json(self) -> dict:
            return {"value": []}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        return FakeResponse(429 if calls["count"] == 1 else 200)

    monkeypatch.setattr(graph_client.requests, "get", fake_get)
    response = graph_client._send_graph_get_with_retries("https://graph.microsoft.com/v1.0/me", {})

    assert response.status_code == 200
    assert calls["count"] == 2


def test_no_permanent_email_records_in_session_state_source() -> None:
    app_source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert "outlook_messages_cache" not in app_source
