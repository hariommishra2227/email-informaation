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
    """Return raw customer rows from the database."""
    return database.list_customers(user_id)


def get_customer_page(
    user_id: str | None = None,
    *,
    page: int = 1,
    page_size: int = 50,
    sender: str = "",
    organisation: str = "",
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
    return [to_business_output(row) for row in rows]
