"""Database facade used by the Streamlit app and services.

The public functions intentionally preserve the old SQLite module API while the
implementation uses SQLAlchemy models that run on PostgreSQL in production.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Select, and_, asc, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
from database.connection import create_all_for_local_tests, db_session, reset_engine, utc_now as _utc_now
from database.models import (
    ApplicationUser,
    ConnectedMailbox,
    Email,
    ExtractedContact,
    FailedProcessingRecord,
    MailboxSyncState,
    OAuthAuthFlow,
    OAuthTokenCache,
)
from models import CustomerRecord, OutlookMessage
from duplicate_detector import normalize_email, normalize_mobile


LOGGER = logging.getLogger(__name__)
DATABASE_PATH = Path(config.DATABASE_PATH)
_MEMORY_CONNECTION: object | None = None


def utc_now() -> str:
    """Return an ISO UTC timestamp for legacy callers."""
    return _utc_now().isoformat(timespec="seconds")


@contextmanager
def get_connection(db_path: Path | str | None = None) -> Iterator[Session]:
    """Yield a SQLAlchemy session for compatibility with old tests/helpers."""
    if db_path is not None:
        _configure_sqlite_url(db_path)
    with db_session() as session:
        yield session


def _configure_sqlite_url(db_path: Path | str) -> None:
    """Point SQLAlchemy at a local SQLite database for tests only."""
    path = str(db_path)
    config.APP_ENV = "test"
    config.DATABASE_URL = "sqlite+pysqlite:///:memory:" if path == ":memory:" else f"sqlite+pysqlite:///{path}"
    reset_engine()


def initialize_database(db_path: Path | str | None = None) -> None:
    """Initialize ORM tables for local/test use.

    Production deployments should run Alembic migrations instead of relying on
    this helper at startup.
    """
    if db_path is not None:
        _configure_sqlite_url(db_path)
    elif str(DATABASE_PATH) == ":memory:":
        _configure_sqlite_url(DATABASE_PATH)
    elif not config.DATABASE_URL:
        _configure_sqlite_url(DATABASE_PATH)
    create_all_for_local_tests()


def ensure_user(user_id: str, email: str | None = None, display_name: str | None = None) -> None:
    """Create or update an application user."""
    with db_session() as session:
        _ensure_user(session, user_id, email=email, display_name=display_name)


def _ensure_user(
    session: Session,
    user_id: str,
    email: str | None = None,
    display_name: str | None = None,
) -> ApplicationUser:
    user = session.scalar(select(ApplicationUser).where(ApplicationUser.external_user_id == user_id))
    if user is None:
        user = ApplicationUser(
            external_user_id=user_id,
            email=email or user_id,
            display_name=display_name or email or user_id,
        )
        session.add(user)
        session.flush()
    else:
        if email:
            user.email = email
        if display_name:
            user.display_name = display_name
    return user


def ensure_mailbox(
    session: Session,
    user_id: str,
    email_address: str | None = None,
    display_name: str | None = None,
    graph_user_id: str = "",
) -> ConnectedMailbox:
    """Return the active mailbox for a user, creating it if needed."""
    user = _ensure_user(session, user_id, email=email_address, display_name=display_name)
    mailbox_email = email_address or user.email or user_id
    mailbox = session.scalar(
        select(ConnectedMailbox).where(
            ConnectedMailbox.user_id == user.id,
            ConnectedMailbox.email_address == mailbox_email,
        )
    )
    if mailbox is None:
        mailbox = ConnectedMailbox(
            user_id=user.id,
            graph_user_id=graph_user_id,
            email_address=mailbox_email,
            display_name=display_name or mailbox_email,
        )
        session.add(mailbox)
        session.flush()
    return mailbox


def mailbox_id_for_user(user_id: str) -> int:
    """Return the numeric mailbox id for a legacy user id."""
    with db_session() as session:
        return int(ensure_mailbox(session, user_id).id)


def store_oauth_auth_flow(flow_id: str, flow: dict[str, Any], created_at: int, expires_at: int) -> None:
    """Persist one pending MSAL auth-code flow server-side."""
    with db_session() as session:
        existing = session.get(OAuthAuthFlow, flow_id)
        payload = json.dumps(flow)
        if existing is None:
            session.add(OAuthAuthFlow(flow_id=flow_id, flow_json=payload, created_at=created_at, expires_at=expires_at))
        else:
            existing.flow_json = payload
            existing.created_at = int(created_at)
            existing.expires_at = int(expires_at)


def consume_oauth_auth_flow(flow_id: str, now: int) -> tuple[str, dict[str, Any] | None]:
    """Return and delete a pending OAuth auth flow."""
    status, flow = load_oauth_auth_flow(flow_id, now)
    if status == "ok":
        delete_oauth_auth_flow(flow_id)
    return status, flow


def load_oauth_auth_flow(flow_id: str, now: int) -> tuple[str, dict[str, Any] | None]:
    """Load a pending MSAL flow without exposing secrets in logs."""
    with db_session() as session:
        row = session.get(OAuthAuthFlow, flow_id)
        if row is None:
            return "missing", None
        if int(row.expires_at) < int(now):
            return "expired", None
        try:
            flow = json.loads(row.flow_json)
        except json.JSONDecodeError:
            return "missing", None
    return ("ok", flow) if isinstance(flow, dict) else ("missing", None)


def delete_oauth_auth_flow(flow_id: str) -> None:
    """Delete one OAuth auth flow."""
    with db_session() as session:
        row = session.get(OAuthAuthFlow, flow_id)
        if row is not None:
            session.delete(row)


def delete_expired_oauth_auth_flows(now: int) -> None:
    """Delete expired OAuth auth flows."""
    with db_session() as session:
        for row in session.scalars(select(OAuthAuthFlow).where(OAuthAuthFlow.expires_at < int(now))):
            session.delete(row)


def store_oauth_token_cache(cache_owner: str, cache_json: str, account: dict[str, Any] | None, updated_at: int) -> None:
    """Persist one serialized MSAL token cache.

    The cache can contain refresh tokens. In production, encrypting this column
    with an application key from AWS Secrets Manager is required before broad
    multi-user rollout.
    """
    with db_session() as session:
        row = session.get(OAuthTokenCache, cache_owner)
        account_json = json.dumps(account or {})
        if row is None:
            session.add(
                OAuthTokenCache(
                    cache_owner=cache_owner,
                    cache_json=cache_json,
                    account_json=account_json,
                    updated_at=int(updated_at),
                )
            )
        else:
            row.cache_json = cache_json
            row.account_json = account_json
            row.updated_at = int(updated_at)


def load_oauth_token_cache(cache_owner: str) -> tuple[str, dict[str, Any] | None]:
    """Return a serialized MSAL cache and safe account metadata."""
    with db_session() as session:
        row = session.get(OAuthTokenCache, cache_owner)
        if row is None:
            return "", None
        try:
            account = json.loads(row.account_json or "{}")
        except json.JSONDecodeError:
            account = {}
        return row.cache_json or "", account if isinstance(account, dict) else {}


def delete_oauth_token_cache(cache_owner: str) -> None:
    """Delete a persisted MSAL token cache."""
    with db_session() as session:
        row = session.get(OAuthTokenCache, cache_owner)
        if row is not None:
            session.delete(row)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def upsert_outlook_message(message: OutlookMessage, status: str = "Pending") -> int:
    """Insert or update Outlook message metadata idempotently."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, message.user_id, email_address=message.user_id)
        email = session.scalar(
            select(Email).where(
                Email.mailbox_id == mailbox.id,
                Email.graph_message_id == message.message_id,
            )
        )
        if email is None:
            email = Email(
                mailbox_id=mailbox.id,
                graph_message_id=message.message_id,
                extraction_status=status,
            )
            session.add(email)
        email.internet_message_id = message.internet_message_id or ""
        email.sender_name = message.sender_name or ""
        email.sender_email = (message.sender_email or "").lower()
        email.subject = message.subject or ""
        email.body_preview = message.body_preview or ""
        email.body_text = message.body or ""
        email.received_datetime = _parse_datetime(message.received_datetime)
        email.is_read = bool(message.is_read)
        email.has_attachments = bool(message.has_attachments)
        session.flush()
        return int(email.id)


def message_processing_status(user_id: str, message_id: str) -> str | None:
    """Return processing status for one message."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        row = session.scalar(select(Email).where(Email.mailbox_id == mailbox.id, Email.graph_message_id == message_id))
        return row.extraction_status if row else None


def set_message_status(user_id: str, message_id: str, status: str) -> None:
    """Update processing status for one message."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        row = session.scalar(select(Email).where(Email.mailbox_id == mailbox.id, Email.graph_message_id == message_id))
        if row is not None:
            row.extraction_status = status


def message_was_imported(user_id: str, message_id: str) -> bool:
    """Return whether a message has already completed import."""
    return message_processing_status(user_id, message_id) in {"Unique", "Duplicate", "Incomplete", "Already Processed"}


def insert_customer(customer: CustomerRecord) -> int:
    """Insert or merge a customer contact and return its id."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, customer.user_id)
        normalized_email = (customer.normalized_email or customer.email or "").lower().strip()
        existing = None
        if normalized_email:
            existing = session.scalar(
                select(ExtractedContact).where(
                    ExtractedContact.mailbox_id == mailbox.id,
                    ExtractedContact.normalized_email == normalized_email,
                )
            )
        if existing is not None:
            _merge_contact(existing, customer)
            contact = existing
        else:
            contact = ExtractedContact(
                mailbox_id=mailbox.id,
                person_name=customer.contact_name,
                email_address=customer.email,
                normalized_email=normalized_email,
                organisation_name=customer.organisation,
                mobile_phone=customer.mobile,
                normalized_phone=customer.normalized_mobile,
                designation=customer.designation,
                address=customer.address,
                extraction_confidence=int(customer.confidence or 0),
                status=customer.status,
            )
            session.add(contact)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                raise
        session.flush()
        return int(contact.id)


def _merge_contact(existing: ExtractedContact, incoming: CustomerRecord) -> None:
    """Fill missing contact fields without merging on similar names."""
    for attr, value in {
        "person_name": incoming.contact_name,
        "email_address": incoming.email,
        "organisation_name": incoming.organisation,
        "mobile_phone": incoming.mobile,
        "normalized_phone": incoming.normalized_mobile,
        "designation": incoming.designation,
        "address": incoming.address,
    }.items():
        if not str(getattr(existing, attr) or "").strip() and str(value or "").strip():
            setattr(existing, attr, value)
    existing.extraction_confidence = max(int(existing.extraction_confidence or 0), int(incoming.confidence or 0))


def _contact_row(contact: ExtractedContact, user_id: str) -> dict[str, Any]:
    return {
        "id": int(contact.id),
        "user_id": user_id,
        "contact_name": contact.person_name,
        "organisation": contact.organisation_name,
        "email": contact.email_address,
        "normalized_email": contact.normalized_email,
        "mobile": contact.mobile_phone,
        "normalized_mobile": contact.normalized_phone,
        "designation": contact.designation,
        "address": contact.address,
        "subject": "",
        "source": "Outlook",
        "source_message_id": "",
        "confidence": int(contact.extraction_confidence or 0),
        "status": contact.status,
        "created_at": contact.created_at.isoformat() if contact.created_at else "",
    }


def _contacts_query(
    session: Session,
    user_id: str | None = None,
    *,
    sender: str = "",
    organisation: str = "",
    status: str = "",
    search: str = "",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
) -> tuple[Select[tuple[ExtractedContact]], str]:
    user_external_id = user_id or config.DEFAULT_USER_ID
    mailbox = ensure_mailbox(session, user_external_id)
    query = select(ExtractedContact).where(ExtractedContact.mailbox_id == mailbox.id)
    if organisation:
        query = query.where(ExtractedContact.organisation_name.ilike(f"%{organisation}%"))
    if status:
        query = query.where(ExtractedContact.status == status)
    if sender:
        query = query.where(ExtractedContact.email_address.ilike(f"%{sender}%"))
    if search:
        like = f"%{search}%"
        query = query.where(
            or_(
                ExtractedContact.person_name.ilike(like),
                ExtractedContact.email_address.ilike(like),
                ExtractedContact.organisation_name.ilike(like),
                ExtractedContact.mobile_phone.ilike(like),
                ExtractedContact.designation.ilike(like),
            )
        )
    sort_column = {
        "created_at": ExtractedContact.created_at,
        "organisation": ExtractedContact.organisation_name,
        "email": ExtractedContact.email_address,
        "status": ExtractedContact.status,
    }.get(sort_by, ExtractedContact.created_at)
    query = query.order_by(asc(sort_column) if sort_dir == "asc" else desc(sort_column), desc(ExtractedContact.id))
    return query, user_external_id


def list_customers(user_id: str | None = None) -> list[dict[str, Any]]:
    """Return customer rows for legacy callers."""
    with db_session() as session:
        query, external_id = _contacts_query(session, user_id)
        return [_contact_row(row, external_id) for row in session.scalars(query).all()]


def list_customers_page(
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
    """Return a database-paginated customer page."""
    page_size = min(100, max(1, int(page_size or 50)))
    page = max(1, int(page or 1))
    with db_session() as session:
        query, external_id = _contacts_query(
            session,
            user_id,
            sender=sender,
            organisation=organisation,
            status=status,
            search=search,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return {"rows": [_contact_row(row, external_id) for row in rows], "total": int(total), "page": page, "page_size": page_size}


def iter_customers(
    user_id: str | None = None,
    *,
    chunk_size: int = 1000,
    limit: int | None = None,
) -> Iterable[list[dict[str, Any]]]:
    """Yield customer rows in chunks for large exports."""
    offset = 0
    emitted = 0
    while True:
        page_size = min(chunk_size, (limit - emitted) if limit else chunk_size)
        if page_size <= 0:
            return
        page = offset // chunk_size + 1
        data = list_customers_page(user_id, page=page, page_size=page_size)
        rows = data["rows"]
        if not rows:
            return
        yield rows
        emitted += len(rows)
        offset += len(rows)
        if len(rows) < page_size:
            return


def list_outlook_message_rows(user_id: str) -> list[dict[str, Any]]:
    """Return cached Outlook message rows for a user."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        rows = session.scalars(select(Email).where(Email.mailbox_id == mailbox.id).order_by(desc(Email.received_datetime))).all()
    return [
        {
            "message_id": row.graph_message_id,
            "user_id": user_id,
            "sender_name": row.sender_name,
            "sender_email": row.sender_email,
            "subject": row.subject,
            "received_datetime": row.received_datetime.isoformat() if row.received_datetime else "",
            "is_read": int(row.is_read),
            "processing_status": row.extraction_status,
            "imported_at": row.updated_at.isoformat() if row.updated_at else "",
        }
        for row in rows
    ]


def update_customer_review(record_id: int, fields: dict[str, Any], reviewed_by: str = "", notes: str = "") -> None:
    """Apply reviewed corrections while recording every changed field."""
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM customers WHERE id = ?", (int(record_id),)).fetchone()
        if row is None:
            raise ValueError("Customer record not found.")
        now = utc_now()
        updates: dict[str, Any] = {}
        for field, new_value in fields.items():
            if field not in {"contact_name", "email", "organisation", "mobile", "designation", "address", "subject"}:
                continue
            old_value = str(row[field] or "")
            new_value = str(new_value or "").strip()
            if old_value == new_value:
                continue
            source_field = {"contact_name": "name_source", "email": "email_source", "organisation": "organisation_source", "mobile": "mobile_source", "designation": "designation_source", "address": "address_source"}.get(field)
            old_source = str(row[source_field] or "") if source_field else ""
            if source_field:
                updates[source_field] = "manual_review"
                updates[source_field.replace("_source", "_confidence")] = 1.0
            updates[field] = new_value
            if field == "email":
                updates["normalized_email"] = normalize_email(new_value)
            elif field == "mobile":
                updates["normalized_mobile"] = normalize_mobile(new_value)
            connection.execute("INSERT INTO review_audit (record_id, field_name, old_value, new_value, old_source, new_source, reviewed_at, reviewed_by, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (record_id, field, old_value, new_value, old_source, "manual_review", now, reviewed_by, notes))
        updates.update({"review_status": fields.get("review_status", "Approved"), "reviewed_at": now, "reviewed_by": reviewed_by, "correction_notes": notes})
        assignments = ", ".join(f"{key} = ?" for key in updates)
        connection.execute(f"UPDATE customers SET {assignments} WHERE id = ?", (*updates.values(), int(record_id)))


def list_review_audit(record_id: int | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM review_audit"
    params: tuple[Any, ...] = ()
    if record_id is not None:
        query += " WHERE record_id = ?"
        params = (int(record_id),)
    query += " ORDER BY reviewed_at DESC, id DESC"
    with get_connection() as connection:
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def list_pending_outlook_messages(user_id: str) -> list[dict[str, Any]]:
    """Return cached messages that still need extraction after a restart."""
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM outlook_messages
            WHERE user_id = ? AND processing_status IN ('Pending', 'Processing', 'Failed')
            ORDER BY received_datetime ASC""",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def customer_duplicate_exists(user_id: str, normalized_email: str, normalized_mobile: str) -> bool:
    """Return whether a duplicate exists by email first, then phone."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        conditions = []
        if normalized_email:
            conditions.append(ExtractedContact.normalized_email == normalized_email)
        if normalized_mobile:
            conditions.append(ExtractedContact.normalized_phone == normalized_mobile)
        if not conditions:
            return False
        row = session.scalar(select(ExtractedContact.id).where(ExtractedContact.mailbox_id == mailbox.id, or_(*conditions)).limit(1))
        return row is not None


def dashboard_counts(user_id: str) -> dict[str, int]:
    """Return dashboard metrics without loading tables into memory."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        total_messages = session.scalar(select(func.count()).select_from(Email).where(Email.mailbox_id == mailbox.id)) or 0
        imported_messages = session.scalar(
            select(func.count()).select_from(Email).where(
                Email.mailbox_id == mailbox.id,
                Email.extraction_status.in_(["Unique", "Duplicate", "Incomplete", "Already Processed"]),
            )
        ) or 0
        status_rows = session.execute(
            select(ExtractedContact.status, func.count()).where(ExtractedContact.mailbox_id == mailbox.id).group_by(ExtractedContact.status)
        ).all()
    counts = {
        "total_outlook_emails": int(total_messages),
        "imported_emails": int(imported_messages),
        "unique_customers": 0,
        "duplicate_customers": 0,
        "incomplete_records": 0,
        "failed_records": 0,
    }
    for status, total in status_rows:
        if status == "Unique":
            counts["unique_customers"] = int(total)
        elif status == "Duplicate":
            counts["duplicate_customers"] = int(total)
        elif status == "Incomplete":
            counts["incomplete_records"] = int(total)
        elif status == "Failed":
            counts["failed_records"] = int(total)
    return counts


def write_processing_log(user_id: str, source_message_id: str, level: str, message: str, details: str = "") -> None:
    """Store a failed processing record without raw email bodies or tokens."""
    safe_details = str(details or "")[:1000]
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        email = session.scalar(select(Email).where(Email.mailbox_id == mailbox.id, Email.graph_message_id == source_message_id))
        session.add(
            FailedProcessingRecord(
                mailbox_id=mailbox.id,
                email_id=email.id if email else None,
                graph_message_id=source_message_id,
                error_code=level,
                error_message=f"{message}: {safe_details}"[:1200],
            )
        )


def get_sync_state(user_id: str) -> dict[str, Any]:
    """Return mailbox sync state without exposing the sensitive delta link."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        state = session.get(MailboxSyncState, mailbox.id)
        if state is None:
            return {"last_successful_sync_at": "", "current_status": "Never", "error_message": "", "processed_records": 0}
        return {
            "last_successful_sync_at": state.last_successful_sync_at.isoformat() if state.last_successful_sync_at else "",
            "current_status": state.current_status,
            "error_message": state.error_message,
            "processed_records": int(state.processed_records or 0),
        }


def get_delta_link(user_id: str) -> str:
    """Return the stored Graph delta link for service use only."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        state = session.get(MailboxSyncState, mailbox.id)
        return state.delta_link if state else ""


def set_sync_state(
    user_id: str,
    *,
    delta_link: str | None = None,
    status: str,
    error_message: str = "",
    processed_records: int = 0,
    successful: bool = False,
) -> None:
    """Persist mailbox sync state and sensitive delta cursor."""
    with db_session() as session:
        mailbox = ensure_mailbox(session, user_id)
        state = session.get(MailboxSyncState, mailbox.id)
        if state is None:
            state = MailboxSyncState(mailbox_id=mailbox.id)
            session.add(state)
        if delta_link is not None:
            state.delta_link = delta_link
            state.sync_cursor = delta_link
        state.current_status = status
        state.error_message = str(error_message or "")[:1000]
        state.processed_records = int(processed_records or 0)
        if successful:
            state.last_successful_sync_at = _utc_now()
