"""Customer registry helpers that bridge storage and legacy exporters."""

from __future__ import annotations

from typing import Any

from models import CustomerRecord
from storage import database


def save_customer(customer: CustomerRecord) -> int:
    """Persist a manually extracted or uploaded customer."""
    database.ensure_user(customer.user_id)
    return database.insert_customer(customer)


def get_customers(user_id: str | None = None) -> list[dict[str, Any]]:
    """Return raw customer rows from SQLite."""
    return database.list_customers(user_id)


def to_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert database rows into the existing Excel export schema."""
    export_rows: list[dict[str, Any]] = []
    for row in rows:
        export_rows.append(
            {
                "contact_person_name": row.get("contact_name", ""),
                "sender_name": row.get("sender_name", ""),
                "receiver_name": row.get("receiver_name", ""),
                "organisation_name": row.get("organisation", ""),
                "email_id": row.get("email", ""),
                "mobile_number": row.get("mobile", ""),
                "normalized_phone": row.get("normalized_mobile", ""),
                "designation": row.get("designation", ""),
                "address": row.get("address", ""),
                "subject": row.get("subject", ""),
                "input_source": row.get("source", ""),
                "extraction_confidence": row.get("confidence", ""),
                "duplicate_status": row.get("status", ""),
                "confidence_score": 100 if row.get("status") == "Duplicate" else 0,
            }
        )
    return export_rows
