from __future__ import annotations

from pathlib import Path

from extractor import EmailExtractionEngine, select_customer_email
from models import OutlookMessage
from services.email_processor import process_outlook_message
from storage import database


def test_graph_sender_email_wins_over_unrelated_body_email() -> None:
    result = EmailExtractionEngine().extract(
        "Rajesh Kumar\nPlease contact accounts@other.example for billing.",
        graph_sender_name="Rajesh Kumar",
        graph_sender_email="rajesh@example.com",
    )
    assert result["email"] == "rajesh@example.com"
    assert result["email_source"] == "graph_sender"
    assert result["email_confidence"] == 1.0
    assert result["contact_person_name"] == "Rajesh Kumar"


def test_email_selector_ranks_external_sources_and_rejects_internal() -> None:
    assert select_customer_email(
        graph_sender_email="employee@itsipl.com",
        forwarded_sender_email="customer@example.com",
        body_emails=["other@example.com"],
    ) == {"email": "customer@example.com", "email_source": "forwarded_sender", "email_confidence": 0.95}


def test_internal_sender_is_skipped_before_customer_insert(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "mail.db")
    database.initialize_database()
    message = OutlookMessage(
        message_id="internal-1", user_id="u", sender_name="Internal", sender_email="EMPLOYEE@ITSIPL.COM",
        subject="Internal", body="employee@itsipl.com", received_datetime="2026-01-01", is_read=False,
    )
    result = process_outlook_message("u", message)
    assert result.status == "Skipped Internal"
    assert database.list_customers("u") == []

