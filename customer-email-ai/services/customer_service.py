"""Customer registry helpers that bridge storage and legacy exporters."""

from __future__ import annotations

from typing import Any

from models import CustomerRecord
from storage import database

BUSINESS_COLUMNS = ("Client Name", "Contact Person Name", "Contact Email", "Phone Number", "Full Address", "Location", "Subject", "Email Date")


def save_customer(customer: CustomerRecord) -> int:
    """Persist a manually extracted or uploaded customer."""
    database.ensure_user(customer.user_id)
    return database.insert_customer(customer)


def get_customers(user_id: str | None = None) -> list[dict[str, Any]]:
    """Return raw customer rows from the database."""
    return database.list_customers(user_id)


def get_customer_page(
    user_id: str | None = None,
    *,
    page: int = 1,
    page_size: int = 50,
    sender: str = "",
    organisation: str = "",
    source: str = "",
    status: str = "",
    search: str = "",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    """Return a server-side paginated customer page."""
    return database.list_customers_page(
        user_id,
        page=page,
        page_size=page_size,
        sender=sender,
        organisation=organisation,
        source=source,
        status=status,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


def iter_customer_export_rows(user_id: str | None = None, *, chunk_size: int = 1000, limit: int | None = None):
    """Yield export rows in database chunks."""
    for rows in database.iter_customers(user_id, chunk_size=chunk_size, limit=limit):
        yield to_export_rows(rows)


def to_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert database rows into the existing Excel export schema."""
    return [
        {
            "contact_person_name": row.get("contact_name", ""),
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
        for row in rows
    ]


def to_business_output(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one database row into the business-facing registry/export shape."""
    return {
        "Client Name": row.get("organisation", ""),
        "Contact Person Name": row.get("contact_name", ""),
        "Contact Email": row.get("email", ""),
        "Phone Number": row.get("mobile", ""),
        "Full Address": row.get("address", ""),
        "Location": row.get("location", ""),
        "Subject": row.get("subject", ""),
        "Email Date": row.get("email_date", ""),
    }
