"""Enterprise SQLite schema for idempotent Outlook mailbox synchronization."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config


LOGGER = logging.getLogger(__name__)
DATABASE_FILE = Path(config.DATABASE_PATH)


def utc_now() -> str:
    """Return the current UTC timestamp for storage."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_connection(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection configured for durable local processing."""
    path = Path(db_path or DATABASE_FILE)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        LOGGER.exception("Enterprise database operation failed.")
        raise
    finally:
        connection.close()


def initialize_database(db_path: Path | str | None = None) -> None:
    """Create enterprise sync tables if they do not already exist."""
    with get_connection(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_emails (
                message_id TEXT PRIMARY KEY,
                internet_message_id TEXT,
                received_datetime TEXT,
                subject TEXT,
                sender_email TEXT,
                processed_datetime TEXT
            );

            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                email TEXT UNIQUE,
                phone TEXT,
                company TEXT,
                designation TEXT,
                address TEXT,
                city TEXT,
                country TEXT,
                last_updated TEXT,
                source_message_id TEXT
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                id INTEGER PRIMARY KEY,
                last_sync_datetime TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_processed_emails_received
                ON processed_emails(received_datetime);
            CREATE INDEX IF NOT EXISTS idx_contacts_phone
                ON contacts(phone);
            CREATE INDEX IF NOT EXISTS idx_contacts_name_company
                ON contacts(name, company);
            """
        )

