"""Tests for Outlook email extraction and storage processing."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from models import OutlookMessage
from services.email_processor import build_customer_record, calculate_confidence, process_outlook_message
from services.outlook_email_service import get_mock_message
from storage import database


@pytest.fixture()
def isolated_db(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a temporary SQLite database for each test."""
    db_path = Path(":memory:")
    if database._MEMORY_CONNECTION is not None:
        database._MEMORY_CONNECTION.close()
    monkeypatch.setattr(database, "_MEMORY_CONNECTION", None)
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.initialize_database()
    return db_path


def test_full_customer_details_are_extracted_from_outlook_mock(isolated_db: Path) -> None:
    """A full Outlook mock email should become a complete Outlook customer."""
    message = get_mock_message(config.DEFAULT_USER_ID, "mock-emp1-001")
    assert message is not None

    customer = process_outlook_message(message.user_id, message)

    assert customer.contact_name == "Rajesh Kumar"
    assert customer.organisation == "ABC Technologies Pvt. Ltd."
    assert customer.email == "rajesh.kumar@abctech.com"
    assert customer.mobile == "+91 9876543210"
    assert customer.normalized_mobile == "9876543210"
    assert customer.source == "Outlook"
    assert customer.confidence == 100
    assert customer.status == "Unique"


def test_email_only_record_is_accepted(isolated_db: Path) -> None:
    """Only an email address is enough to create a customer record."""
    customer = build_customer_record(
        user_id=config.DEFAULT_USER_ID,
        text="Please contact priya@deltasystems.com for details.",
        source="Outlook",
    )

    assert customer.email == "priya@deltasystems.com"
    assert customer.status in {"Unique", "Incomplete"}
    assert customer.confidence >= 30


def test_duplicate_message_id_is_not_imported_twice(isolated_db: Path) -> None:
    """The same Outlook message id should not create multiple customers."""
    message = get_mock_message(config.DEFAULT_USER_ID, "mock-emp1-001")
    assert message is not None

    first = process_outlook_message(message.user_id, message)
    second = process_outlook_message(message.user_id, message)
    customers = database.list_customers(message.user_id)

    assert first.status == "Unique"
    assert second.status == "Already Processed"
    assert len(customers) == 1


def test_duplicate_customer_email_is_identified(isolated_db: Path) -> None:
    """A second customer with the same normalized email is retained as Duplicate."""
    first = get_mock_message(config.DEFAULT_USER_ID, "mock-emp1-001")
    assert first is not None
    duplicate = OutlookMessage(
        message_id="mock-emp1-duplicate-email",
        user_id=config.DEFAULT_USER_ID,
        sender_name="Rajesh Kumar",
        sender_email="rajesh.kumar@abctech.com",
        subject="Duplicate customer inquiry",
        body="Rajesh Kumar\nABC Technologies Pvt. Ltd.\nEmail: rajesh.kumar@abctech.com",
        received_datetime="2026-07-16T12:00:00+05:30",
        is_read=False,
    )

    process_outlook_message(first.user_id, first)
    second = process_outlook_message(duplicate.user_id, duplicate)

    assert second.status == "Duplicate"
    assert len(database.list_customers(config.DEFAULT_USER_ID)) == 2


def test_phone_format_is_preserved_and_normalized_for_duplicates(isolated_db: Path) -> None:
    """Display phone format should be preserved while duplicate key is normalized."""
    message = get_mock_message(config.DEFAULT_USER_ID, "mock-emp1-003")
    assert message is not None

    customer = process_outlook_message(message.user_id, message)

    assert customer.mobile == "+91-99887-66554"
    assert customer.normalized_mobile == "9988766554"


def test_customer_import_uses_default_user_id(isolated_db: Path) -> None:
    """Customer import automatically stores records under the fixed internal user."""
    message = get_mock_message(config.DEFAULT_USER_ID, "mock-emp1-002")
    assert message is not None

    process_outlook_message(config.DEFAULT_USER_ID, message)

    rows = database.list_customers(config.DEFAULT_USER_ID)
    assert len(rows) == 1
    assert rows[0]["user_id"] == config.DEFAULT_USER_ID


def test_confidence_is_calculated_correctly() -> None:
    """Confidence should use the dynamic weighted score."""
    assert calculate_confidence({"email": "a@example.com"}) == 30
    assert calculate_confidence(
        {
            "email": "a@example.com",
            "contact_name": "A User",
            "organisation": "Example Ltd",
            "mobile": "+91 9876543210",
            "designation": "Manager",
            "subject": "Hello",
            "address": "Noida",
        }
    ) == 100
