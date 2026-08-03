"""Repository layer for Outlook sync persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from duplicate_detector import normalize_email
from models import CustomerRecord
from storage import database


@dataclass(frozen=True)
class ContactUpsertResult:
    """Result of inserting or merging a contact."""

    created: bool = False
    updated: bool = False
    duplicate_removed: bool = False
    contact_id: int | None = None


class EmailSyncRepository:
    """PostgreSQL-ready repository for processed messages, contacts, and sync state."""

    def __init__(self) -> None:
        database.initialize_database()

    def has_processed_email(self, message_id: str, user_id: str = "") -> bool:
        """Return whether a Microsoft message id was already processed."""
        status = database.message_processing_status(user_id or "default_user", message_id)
        return status in {"Unique", "Duplicate", "Incomplete", "Already Processed"}

    def mark_email_processed(
        self,
        *,
        message_id: str,
        user_id: str = "default_user",
        internet_message_id: str = "",
        received_datetime: str = "",
        subject: str = "",
        sender_email: str = "",
        processed_datetime: str | None = None,
    ) -> None:
        """Mark one message as processed if it exists in the email table."""
        database.set_message_status(user_id, message_id, "Already Processed")

    def get_last_sync_datetime(self, user_id: str = "default_user") -> str | None:
        """Return last successful sync timestamp."""
        return database.get_sync_state(user_id).get("last_successful_sync_at") or None

    def set_last_sync_datetime(self, value: str, user_id: str = "default_user") -> None:
        """Store a high-water mark for legacy callers."""
        database.set_sync_state(user_id, delta_link=None, status="Succeeded", processed_records=0, successful=True)

    def get_delta_link(self, user_id: str) -> str:
        """Return stored Microsoft Graph delta link for service use."""
        return database.get_delta_link(user_id)

    def set_sync_state(
        self,
        user_id: str,
        *,
        delta_link: str | None = None,
        status: str,
        error_message: str = "",
        processed_records: int = 0,
        successful: bool = False,
    ) -> None:
        """Persist sync state."""
        database.set_sync_state(
            user_id,
            delta_link=delta_link,
            status=status,
            error_message=error_message,
            processed_records=processed_records,
            successful=successful,
        )

    def upsert_contact(self, contact: dict[str, str], user_id: str = "default_user") -> ContactUpsertResult:
        """Insert or merge contact information by normalized email."""
        normalized_email = normalize_email(str(contact.get("email", "")))
        existed = database.customer_duplicate_exists(user_id, normalized_email, "")
        record = CustomerRecord(
            user_id=user_id,
            contact_name=str(contact.get("name", "")),
            organisation=str(contact.get("company", "")),
            email=str(contact.get("email", "")),
            normalized_email=normalized_email,
            mobile=str(contact.get("phone", "")),
            normalized_mobile=str(contact.get("phone", "")),
            designation=str(contact.get("designation", "")),
            address=str(contact.get("address", "")),
            source="Outlook",
            source_message_id=str(contact.get("source_message_id", "")),
            confidence=100 if normalized_email else 0,
            status="Duplicate" if existed else "Unique",
        )
        contact_id = database.insert_customer(record)
        return ContactUpsertResult(
            created=not existed,
            updated=existed,
            duplicate_removed=existed,
            contact_id=contact_id,
        )

    def stats(self, user_id: str = "default_user") -> dict[str, Any]:
        """Return database metrics for the Streamlit dashboard."""
        counts = database.dashboard_counts(user_id)
        state = database.get_sync_state(user_id)
        return {
            "total_contacts": counts["unique_customers"] + counts["duplicate_customers"] + counts["incomplete_records"],
            "processed_emails": counts["imported_emails"],
            "last_sync_datetime": state.get("last_successful_sync_at", ""),
        }
