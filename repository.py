"""Repository layer for processed emails, contacts, and synchronization state."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any

from models import OutlookMessage


CONTACT_FIELDS = ("name", "email", "phone", "company", "designation", "address", "city", "country")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MailboxRepository:
    """Perform synchronization persistence using a caller-owned transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def is_email_processed(self, message_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def mark_email_processed(self, message: OutlookMessage, processed_datetime: str | None = None) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO processed_emails (
                message_id, internet_message_id, received_datetime, subject,
                sender_email, processed_datetime
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id,
                message.internet_message_id,
                message.received_datetime,
                message.subject,
                message.sender_email,
                processed_datetime or utc_now(),
            ),
        )

    def find_contact_by_email(self, email: str) -> dict[str, Any] | None:
        if not email:
            return None
        return self._one("SELECT * FROM contacts WHERE email = ?", (email,))

    def find_contact_by_phone(self, phone: str) -> dict[str, Any] | None:
        if not phone:
            return None
        return self._one("SELECT * FROM contacts WHERE phone = ? ORDER BY id LIMIT 1", (phone,))

    def find_contact_by_name_company(self, name: str, company: str) -> dict[str, Any] | None:
        if not name or not company:
            return None
        return self._one(
            """
            SELECT * FROM contacts
            WHERE name = ? COLLATE NOCASE AND company = ? COLLATE NOCASE
            ORDER BY id LIMIT 1
            """,
            (name, company),
        )

    def insert_contact(self, contact: dict[str, str], source_message_id: str) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO contacts (
                name, email, phone, company, designation, address, city, country,
                last_updated, source_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contact.get("name") or None,
                contact.get("email") or None,
                contact.get("phone") or None,
                contact.get("company") or None,
                contact.get("designation") or None,
                contact.get("address") or None,
                contact.get("city") or None,
                contact.get("country") or None,
                utc_now(),
                source_message_id,
            ),
        )
        return int(cursor.lastrowid)

    def update_missing_fields(self, contact_id: int, contact: dict[str, str]) -> bool:
        existing = self.get_contact(contact_id)
        if existing is None:
            return False
        updates = {
            field: contact.get(field, "").strip()
            for field in CONTACT_FIELDS
            if not str(existing.get(field) or "").strip() and contact.get(field, "").strip()
        }
        if not updates:
            return False
        assignments = ", ".join(f"{field} = ?" for field in updates)
        self.connection.execute(
            f"UPDATE contacts SET {assignments}, last_updated = ? WHERE id = ?",
            (*updates.values(), utc_now(), contact_id),
        )
        return True

    def merge_and_delete(self, primary_id: int, duplicate_id: int) -> bool:
        if primary_id == duplicate_id:
            return False
        duplicate = self.get_contact(duplicate_id)
        if duplicate is None:
            return False
        self.update_missing_fields(primary_id, {field: str(duplicate.get(field) or "") for field in CONTACT_FIELDS})
        self.connection.execute("DELETE FROM contacts WHERE id = ?", (duplicate_id,))
        return True

    def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM contacts WHERE id = ?", (contact_id,))

    def get_last_sync_datetime(self) -> str | None:
        row = self.connection.execute(
            "SELECT last_sync_datetime FROM sync_state WHERE id = 1"
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def set_last_sync_datetime(self, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO sync_state (id, last_sync_datetime) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_sync_datetime = excluded.last_sync_datetime
            """,
            (value,),
        )

    def statistics(self) -> dict[str, Any]:
        contacts = self.connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        processed = self.connection.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
        return {
            "total_contacts": int(contacts),
            "processed_emails": int(processed),
            "last_sync_datetime": self.get_last_sync_datetime(),
        }

    def _one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        row = self.connection.execute(query, params).fetchone()
        return dict(row) if row else None
