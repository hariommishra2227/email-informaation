"""SQLite persistence for users, Outlook messages, customers, and logs."""

from __future__ import annotations

import logging
import json
import sqlite3
import shutil
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DATABASE_PATH
from models import CustomerRecord, OutlookMessage
from duplicate_detector import normalize_email, normalize_mobile


LOGGER = logging.getLogger(__name__)
_MEMORY_CONNECTION: sqlite3.Connection | None = None


def utc_now() -> str:
    """Return a compact UTC timestamp for storage."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_connection(db_path: Path | str | None = None) -> Iterable[sqlite3.Connection]:
    """Yield a SQLite connection with dictionary rows enabled."""
    global _MEMORY_CONNECTION
    db_path = db_path or DATABASE_PATH
    if str(db_path) == ":memory:":
        if _MEMORY_CONNECTION is None:
            _MEMORY_CONNECTION = sqlite3.connect(":memory:", check_same_thread=False)
            _MEMORY_CONNECTION.row_factory = sqlite3.Row
            _MEMORY_CONNECTION.execute("PRAGMA temp_store = MEMORY")
        try:
            yield _MEMORY_CONNECTION
            _MEMORY_CONNECTION.commit()
        except Exception:
            _MEMORY_CONNECTION.rollback()
            LOGGER.exception("Database operation failed.")
            raise
        return

    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA temp_store = MEMORY")
    try:
        connection.execute("PRAGMA journal_mode = MEMORY")
    except sqlite3.OperationalError:
        LOGGER.warning("SQLite journal mode could not be changed; continuing with default mode.")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        LOGGER.exception("Database operation failed.")
        raise
    finally:
        connection.close()


def initialize_database(db_path: Path | str | None = None) -> None:
    """Create local development tables on first run."""
    global DATABASE_PATH
    try:
        _initialize_database(db_path)
    except sqlite3.DatabaseError as exc:
        if not _can_recover_database(db_path, exc):
            raise
        LOGGER.warning("Recovering local SQLite database after initialization failure: %s", exc)
        try:
            _move_broken_database_files(db_path)
            _initialize_database(db_path)
        except OSError as move_exc:
            fallback_path = _fallback_database_path(db_path)
            LOGGER.warning(
                "Could not move broken SQLite database (%s). Using fallback database at %s.",
                move_exc,
                fallback_path,
            )
            if db_path is None:
                DATABASE_PATH = fallback_path
            _initialize_database(fallback_path)


def _initialize_database(db_path: Path | str | None = None) -> None:
    """Create local development tables on first run without recovery handling."""
    with get_connection(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outlook_messages (
                message_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                sender_name TEXT,
                sender_email TEXT,
                subject TEXT,
                received_datetime TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                processing_status TEXT NOT NULL DEFAULT 'Pending',
                imported_at TEXT,
                PRIMARY KEY (message_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                contact_name TEXT,
                organisation TEXT,
                email TEXT,
                normalized_email TEXT,
                mobile TEXT,
                normalized_mobile TEXT,
                designation TEXT,
                address TEXT,
                subject TEXT,
                source TEXT,
                source_message_id TEXT,
                confidence INTEGER,
                status TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processing_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                source_message_id TEXT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS extraction_jobs (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                target_count INTEGER NOT NULL,
                processed_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                next_link TEXT,
                status TEXT NOT NULL DEFAULT 'Pending',
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_outlook_messages_user_status
                ON outlook_messages(user_id, processing_status);
            CREATE INDEX IF NOT EXISTS idx_customers_user_email
                ON customers(user_id, normalized_email);
            CREATE INDEX IF NOT EXISTS idx_customers_user_mobile
                ON customers(user_id, normalized_mobile);
            CREATE INDEX IF NOT EXISTS idx_customers_user_source_message
                ON customers(user_id, source_message_id);

            CREATE TABLE IF NOT EXISTS oauth_auth_flows (
                flow_id TEXT PRIMARY KEY,
                flow_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_token_caches (
                cache_owner TEXT PRIMARY KEY,
                cache_json TEXT NOT NULL,
                account_json TEXT,
                updated_at INTEGER NOT NULL
            );
            """
        )
        _migrate_customer_provenance(connection, db_path)


PROVENANCE_COLUMNS = {
    "name_source": "TEXT DEFAULT ''", "name_confidence": "REAL DEFAULT 0", "name_evidence": "TEXT DEFAULT ''",
    "email_source": "TEXT DEFAULT ''", "email_confidence": "REAL DEFAULT 0", "email_evidence": "TEXT DEFAULT ''",
    "organisation_source": "TEXT DEFAULT ''", "organisation_confidence": "REAL DEFAULT 0", "organisation_evidence": "TEXT DEFAULT ''",
    "mobile_source": "TEXT DEFAULT ''", "mobile_confidence": "REAL DEFAULT 0", "mobile_evidence": "TEXT DEFAULT ''",
    "designation_source": "TEXT DEFAULT ''", "designation_confidence": "REAL DEFAULT 0", "designation_evidence": "TEXT DEFAULT ''",
    "address_source": "TEXT DEFAULT ''", "address_confidence": "REAL DEFAULT 0", "address_evidence": "TEXT DEFAULT ''",
    "extraction_method": "TEXT DEFAULT 'regex_spacy'", "llm_used": "INTEGER DEFAULT 0", "llm_model": "TEXT DEFAULT ''", "llm_error": "TEXT DEFAULT ''",
    "review_status": "TEXT DEFAULT 'Needs Review'", "reviewed_at": "TEXT DEFAULT ''", "reviewed_by": "TEXT DEFAULT ''", "correction_notes": "TEXT DEFAULT ''",
    "internet_message_id": "TEXT DEFAULT ''", "sender_email": "TEXT DEFAULT ''", "sender_domain": "TEXT DEFAULT ''", "processed_at": "TEXT DEFAULT ''",
}


def _migrate_customer_provenance(connection: sqlite3.Connection, db_path: Path | str | None = None) -> None:
    """Idempotently add provenance columns and review audit storage."""
    existing = {str(row[1]) for row in connection.execute("PRAGMA table_info(customers)").fetchall()}
    missing = {name: definition for name, definition in PROVENANCE_COLUMNS.items() if name not in existing}
    if missing and db_path not in (None, ":memory:"):
        path = Path(db_path)
        if path.exists():
            backup = path.with_name(f"{path.name}.pre-provenance.bak")
            if not backup.exists():
                shutil.copy2(path, backup)
    for name, definition in missing.items():
        connection.execute(f"ALTER TABLE customers ADD COLUMN {name} {definition}")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS review_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER NOT NULL,
            field_name TEXT NOT NULL, old_value TEXT, new_value TEXT,
            old_source TEXT, new_source TEXT, reviewed_at TEXT NOT NULL,
            reviewed_by TEXT, notes TEXT
        )"""
    )


def _ensure_oauth_auth_flows_table(connection: sqlite3.Connection) -> None:
    """Create the pending OAuth flow table if database initialization has not run yet."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_auth_flows (
            flow_id TEXT PRIMARY KEY,
            flow_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )


def _ensure_oauth_token_caches_table(connection: sqlite3.Connection) -> None:
    """Create the OAuth token cache table if database initialization has not run yet."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_token_caches (
            cache_owner TEXT PRIMARY KEY,
            cache_json TEXT NOT NULL,
            account_json TEXT,
            updated_at INTEGER NOT NULL
        )
        """
    )


def _can_recover_database(db_path: Path | str | None, exc: sqlite3.DatabaseError) -> bool:
    """Return whether an initialization failure is safe to recover locally."""
    path = Path(db_path or DATABASE_PATH)
    if str(path) == ":memory:":
        return False
    message = str(exc).lower()
    recoverable_messages = (
        "disk i/o error",
        "database disk image is malformed",
        "file is not a database",
    )
    return path.exists() and any(text in message for text in recoverable_messages)


def _move_broken_database_files(db_path: Path | str | None) -> None:
    """Move a broken local database and its journal aside before recreating schema."""
    path = Path(db_path or DATABASE_PATH)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    for candidate in (path, Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm")):
        if not candidate.exists():
            continue
        backup = candidate.with_name(f"{candidate.name}.broken-{timestamp}")
        candidate.replace(backup)


def _fallback_database_path(db_path: Path | str | None) -> Path:
    """Return a usable local database path when the configured file is locked."""
    path = Path(db_path or DATABASE_PATH)
    return path.with_name(f"{path.stem}_recovered{path.suffix}")


def ensure_user(user_id: str, email: str | None = None, display_name: str | None = None) -> None:
    """Create a user row if it does not already exist."""
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO users (user_id, email, display_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, email or user_id, display_name or user_id, utc_now()),
        )


def store_oauth_auth_flow(flow_id: str, flow: dict[str, Any], created_at: int, expires_at: int) -> None:
    """Persist one pending MSAL auth-code flow server-side."""
    with get_connection() as connection:
        _ensure_oauth_auth_flows_table(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO oauth_auth_flows (flow_id, flow_json, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (flow_id, json.dumps(flow), int(created_at), int(expires_at)),
        )


def consume_oauth_auth_flow(flow_id: str, now: int) -> tuple[str, dict[str, Any] | None]:
    """Return and delete a pending MSAL flow, reporting missing or expired state."""
    status, flow = load_oauth_auth_flow(flow_id, now)
    if status == "ok":
        delete_oauth_auth_flow(flow_id)
    return status, flow


def load_oauth_auth_flow(flow_id: str, now: int) -> tuple[str, dict[str, Any] | None]:
    """Return a pending MSAL flow without deleting it."""
    with get_connection() as connection:
        _ensure_oauth_auth_flows_table(connection)
        row = connection.execute(
            """
            SELECT flow_json, expires_at FROM oauth_auth_flows
            WHERE flow_id = ?
            """,
            (flow_id,),
        ).fetchone()
        if row is None:
            return "missing", None

        if int(row["expires_at"]) < int(now):
            return "expired", None

        try:
            flow = json.loads(str(row["flow_json"]))
        except json.JSONDecodeError:
            return "missing", None
        return ("ok", flow) if isinstance(flow, dict) else ("missing", None)


def delete_oauth_auth_flow(flow_id: str) -> None:
    """Delete one pending OAuth flow if it exists."""
    with get_connection() as connection:
        _ensure_oauth_auth_flows_table(connection)
        connection.execute("DELETE FROM oauth_auth_flows WHERE flow_id = ?", (flow_id,))


def delete_expired_oauth_auth_flows(now: int) -> None:
    """Remove expired OAuth auth-code flows."""
    with get_connection() as connection:
        _ensure_oauth_auth_flows_table(connection)
        connection.execute("DELETE FROM oauth_auth_flows WHERE expires_at < ?", (int(now),))


def store_oauth_token_cache(
    cache_owner: str,
    cache_json: str,
    account: dict[str, Any] | None,
    updated_at: int,
) -> None:
    """Persist one serialized MSAL token cache and safe account metadata."""
    with get_connection() as connection:
        _ensure_oauth_token_caches_table(connection)
        account_json = json.dumps(account or {})
        connection.execute(
            """
            INSERT INTO oauth_token_caches (cache_owner, cache_json, account_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_owner) DO UPDATE SET
                cache_json=excluded.cache_json,
                account_json=excluded.account_json,
                updated_at=excluded.updated_at
            """,
            (cache_owner, cache_json, account_json, int(updated_at)),
        )


def load_oauth_token_cache(cache_owner: str) -> tuple[str, dict[str, Any] | None]:
    """Return the serialized MSAL token cache and account metadata for an owner."""
    with get_connection() as connection:
        _ensure_oauth_token_caches_table(connection)
        row = connection.execute(
            """
            SELECT cache_json, account_json FROM oauth_token_caches
            WHERE cache_owner = ?
            """,
            (cache_owner,),
        ).fetchone()
    if row is None:
        return "", None
    try:
        account = json.loads(str(row["account_json"] or "{}"))
    except json.JSONDecodeError:
        account = {}
    return str(row["cache_json"] or ""), account if isinstance(account, dict) else {}


def delete_oauth_token_cache(cache_owner: str) -> None:
    """Delete one persisted MSAL token cache."""
    with get_connection() as connection:
        _ensure_oauth_token_caches_table(connection)
        connection.execute("DELETE FROM oauth_token_caches WHERE cache_owner = ?", (cache_owner,))


def upsert_outlook_message(message: OutlookMessage, status: str = "Pending") -> None:
    """Insert or update message metadata without modifying Outlook itself."""
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO outlook_messages (
                message_id, user_id, sender_name, sender_email, subject,
                received_datetime, is_read, processing_status, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(message_id, user_id) DO UPDATE SET
                sender_name=excluded.sender_name,
                sender_email=excluded.sender_email,
                subject=excluded.subject,
                received_datetime=excluded.received_datetime,
                is_read=excluded.is_read
            """,
            (
                message.message_id,
                message.user_id,
                message.sender_name,
                message.sender_email,
                message.subject,
                message.received_datetime,
                int(message.is_read),
                status,
            ),
        )


def message_processing_status(user_id: str, message_id: str) -> str | None:
    """Return the stored processing status for one Outlook message."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT processing_status FROM outlook_messages
            WHERE user_id = ? AND message_id = ?
            """,
            (user_id, message_id),
        ).fetchone()
    return str(row["processing_status"]) if row else None


def set_message_status(user_id: str, message_id: str, status: str) -> None:
    """Update the local processing status for one Outlook message."""
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE outlook_messages
            SET processing_status = ?, imported_at = COALESCE(imported_at, ?)
            WHERE user_id = ? AND message_id = ?
            """,
            (status, utc_now(), user_id, message_id),
        )


def message_was_imported(user_id: str, message_id: str) -> bool:
    """Return whether a message has already completed import for this user."""
    status = message_processing_status(user_id, message_id)
    return status in {"Unique", "Duplicate", "Incomplete", "Already Processed"}


def insert_customer(customer: CustomerRecord) -> int:
    """Insert a customer record and return its local id."""
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM customers WHERE user_id = ? AND source_message_id = ? LIMIT 1",
            (customer.user_id, customer.source_message_id),
        ).fetchone() if customer.source_message_id else None
        if existing:
            return int(existing["id"])
        values = {"user_id": customer.user_id, "contact_name": customer.contact_name, "organisation": customer.organisation,
            "email": customer.email, "normalized_email": customer.normalized_email, "mobile": customer.mobile,
            "normalized_mobile": customer.normalized_mobile, "designation": customer.designation, "address": customer.address,
            "subject": customer.subject, "source": customer.source, "source_message_id": customer.source_message_id,
            "confidence": customer.confidence, "status": customer.status, "created_at": utc_now()}
        for name in PROVENANCE_COLUMNS:
            values[name] = getattr(customer, name)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = connection.execute(f"INSERT INTO customers ({columns}) VALUES ({placeholders})", tuple(values.values()))
    return int(cursor.lastrowid)


def create_extraction_job(job_id: str, user_id: str, target_count: int, next_link: str = "") -> None:
    """Create a resumable mailbox extraction job."""
    now = utc_now()
    with get_connection() as connection:
        connection.execute(
            """INSERT INTO extraction_jobs
            (job_id, user_id, target_count, next_link, status, started_at, updated_at)
            VALUES (?, ?, ?, ?, 'Pending', ?, ?)""",
            (job_id, user_id, int(target_count), next_link or None, now, now),
        )
        try:
            connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_user_source_message_unique
                ON customers(user_id, source_message_id)
                WHERE source_message_id IS NOT NULL AND source_message_id != ''"""
            )
        except sqlite3.IntegrityError:
            LOGGER.warning("Existing duplicate source-message customer rows prevent unique-index migration; application upsert protection remains active.")


def get_extraction_job(job_id: str) -> dict[str, Any] | None:
    """Return one extraction job checkpoint."""
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM extraction_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def update_extraction_job(job_id: str, **values: Any) -> None:
    """Persist only approved extraction-job checkpoint fields."""
    allowed = {"processed_count", "skipped_count", "failed_count", "next_link", "status"}
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with get_connection() as connection:
        connection.execute(
            f"UPDATE extraction_jobs SET {assignments} WHERE job_id = ?",
            (*updates.values(), job_id),
        )


def list_customers(user_id: str | None = None) -> list[dict[str, Any]]:
    """Return customer rows, optionally restricted to one employee."""
    query = "SELECT * FROM customers"
    params: tuple[Any, ...] = ()
    if user_id:
        query += " WHERE user_id = ?"
        params = (user_id,)
    query += " ORDER BY created_at DESC, id DESC"
    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_outlook_message_rows(user_id: str) -> list[dict[str, Any]]:
    """Return locally cached Outlook message rows for a user."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM outlook_messages
            WHERE user_id = ?
            ORDER BY received_datetime DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


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
    """Return whether a customer key already exists for the selected employee."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id FROM customers
            WHERE user_id = ?
              AND (
                (? != '' AND normalized_email = ?)
                OR (? != '' AND normalized_mobile = ?)
              )
            LIMIT 1
            """,
            (user_id, normalized_email, normalized_email, normalized_mobile, normalized_mobile),
        ).fetchone()
    return row is not None


def dashboard_counts(user_id: str) -> dict[str, int]:
    """Return dashboard metrics for one employee."""
    with get_connection() as connection:
        total_messages = connection.execute(
            "SELECT COUNT(*) AS total FROM outlook_messages WHERE user_id = ?",
            (user_id,),
        ).fetchone()["total"]
        imported_messages = connection.execute(
            """
            SELECT COUNT(*) AS total FROM outlook_messages
            WHERE user_id = ? AND processing_status IN ('Unique', 'Duplicate', 'Incomplete', 'Already Processed')
            """,
            (user_id,),
        ).fetchone()["total"]
        customer_rows = connection.execute(
            """
            SELECT status, COUNT(*) AS total FROM customers
            WHERE user_id = ?
            GROUP BY status
            """,
            (user_id,),
        ).fetchall()

    counts = {
        "total_outlook_emails": int(total_messages),
        "imported_emails": int(imported_messages),
        "unique_customers": 0,
        "duplicate_customers": 0,
        "incomplete_records": 0,
        "failed_records": 0,
    }
    for row in customer_rows:
        status = row["status"]
        if status == "Unique":
            counts["unique_customers"] = int(row["total"])
        elif status == "Duplicate":
            counts["duplicate_customers"] = int(row["total"])
        elif status == "Incomplete":
            counts["incomplete_records"] = int(row["total"])
        elif status == "Failed":
            counts["failed_records"] = int(row["total"])
    return counts


def write_processing_log(
    user_id: str,
    source_message_id: str,
    level: str,
    message: str,
    details: str = "",
) -> None:
    """Store technical processing details away from the user interface."""
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO processing_logs (user_id, source_message_id, level, message, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, source_message_id, level, message, details, utc_now()),
        )
