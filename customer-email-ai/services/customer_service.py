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


BUSINESS_COLUMNS = ("Client Name", "Contact Person Name", "Contact Email", "Phone Number", "Full Address", "Location", "Subject", "Email Date")

def to_business_output(record: dict[str, Any] | CustomerRecord) -> dict[str, Any]:
    row = record if isinstance(record, dict) else record.__dict__
    return {"Client Name": row.get("organisation", ""), "Contact Person Name": row.get("sender_name") or row.get("contact_name", ""),
            "Contact Email": row.get("sender_email") or row.get("email", ""), "Phone Number": row.get("mobile", ""),
            "Full Address": row.get("address", ""), "Location": row.get("location", ""), "Subject": row.get("subject", ""),
            "Email Date": row.get("email_date", row.get("received_datetime", ""))}

def to_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert database rows into the existing Excel export schema."""
    return [to_business_output(row) for row in rows]
