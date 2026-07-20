"""SQLite connection and schema management for enterprise mailbox synchronization."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path
import sqlite3


LOGGER = logging.getLogger(__name__)
DATABASE_PATH = Path(__file__).resolve().parent / "contacts.db"


def connect(db_path: Path | str = DATABASE_PATH) -> sqlite3.Connection:
    """Open a production-oriented SQLite connection."""
    connection = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if str(db_path) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


@contextmanager
def transaction(db_path: Path | str = DATABASE_PATH) -> Iterator[sqlite3.Connection]:
    """Commit one atomic unit of work, rolling it back on failure."""
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        LOGGER.exception("Enterprise contacts database transaction failed.")
        raise
    finally:
        connection.close()


def initialize_database(db_path: Path | str = DATABASE_PATH) -> None:
    """Create the enterprise synchronization schema and lookup indexes."""
    connection = connect(db_path)
    try:
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
            CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone);
            CREATE INDEX IF NOT EXISTS idx_contacts_name_company
                ON contacts(name COLLATE NOCASE, company COLLATE NOCASE);
            """
        )
        connection.commit()
    finally:
        connection.close()
