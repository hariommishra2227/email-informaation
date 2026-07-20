"""Incremental, idempotent Outlook mailbox synchronization service."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from time import perf_counter
from typing import Any

import database
from duplicate_handler import merge_contact
from extractor import EmailExtractionEngine
from models import OutlookMessage
from repository import MailboxRepository
from services import graph_client
from services.email_processor import build_customer_record


LOGGER = logging.getLogger(__name__)


@dataclass
class SyncStatistics:
    processed_emails: int = 0
    skipped_emails: int = 0
    new_contacts: int = 0
    updated_contacts: int = 0
    duplicates_removed: int = 0
    total_processing_time: float = 0.0

    def to_dict(self) -> dict[str, int | float]:
        return self.__dict__.copy()


ProgressCallback = Callable[[int, int], None]
Extractor = Callable[[OutlookMessage], dict[str, Any]]


class MailboxSynchronizer:
    """Synchronize unseen messages without retaining the complete mailbox in memory."""

    def __init__(
        self,
        db_path: Path | str = database.DATABASE_PATH,
        batch_size: int = 100,
        extractor: Extractor | None = None,
    ) -> None:
        self.db_path = db_path
        self.batch_size = max(1, int(batch_size))
        self.extractor = extractor or _default_extractor()

    def synchronize(
        self,
        user_id: str,
        message_pages: Iterable[list[OutlookMessage]] | None = None,
        progress: ProgressCallback | None = None,
    ) -> SyncStatistics:
        started = perf_counter()
        database.initialize_database(self.db_path)
        last_sync = self._last_sync()
        pages = message_pages or graph_client.iter_mailbox_message_pages(
            user_id, received_after=last_sync, page_size=self.batch_size
        )
        stats = SyncStatistics()
        highest_received = last_sync or ""
        buffer: list[OutlookMessage] = []

        try:
            for page_number, page in enumerate(pages, start=1):
                for message in page:
                    buffer.append(message)
                    highest_received = max(highest_received, message.received_datetime or "")
                    if len(buffer) >= self.batch_size:
                        self._process_batch(buffer, stats)
                        buffer.clear()
                        if progress:
                            progress(stats.processed_emails + stats.skipped_emails, page_number)
            if buffer:
                self._process_batch(buffer, stats)
                if progress:
                    progress(stats.processed_emails + stats.skipped_emails, page_number if 'page_number' in locals() else 1)
            if highest_received:
                with database.transaction(self.db_path) as connection:
                    MailboxRepository(connection).set_last_sync_datetime(highest_received)
        except Exception:
            LOGGER.exception("Mailbox synchronization stopped; the high-water mark was not advanced.")
            raise
        finally:
            stats.total_processing_time = round(perf_counter() - started, 3)
        return stats

    def _process_batch(self, messages: list[OutlookMessage], stats: SyncStatistics) -> None:
        with database.transaction(self.db_path) as connection:
            repository = MailboxRepository(connection)
            for message in messages:
                if repository.is_email_processed(message.message_id):
                    stats.skipped_emails += 1
                    continue
                contact = self.extractor(message)
                result = merge_contact(repository, contact, message.message_id)
                repository.mark_email_processed(message)
                stats.processed_emails += 1
                stats.new_contacts += int(result.created)
                stats.updated_contacts += int(result.updated)
                stats.duplicates_removed += result.duplicates_removed

    def _last_sync(self) -> str | None:
        connection = database.connect(self.db_path)
        try:
            return MailboxRepository(connection).get_last_sync_datetime()
        finally:
            connection.close()


def database_statistics(db_path: Path | str = database.DATABASE_PATH) -> dict[str, Any]:
    database.initialize_database(db_path)
    connection = database.connect(db_path)
    try:
        return MailboxRepository(connection).statistics()
    finally:
        connection.close()


def _default_extractor() -> Extractor:
    engine = EmailExtractionEngine()

    def extract(message: OutlookMessage) -> dict[str, Any]:
        record = build_customer_record(
            user_id=message.user_id,
            text=message.body,
            source="Outlook",
            source_message_id=message.message_id,
            sender_email=message.sender_email,
            sender_name=message.sender_name,
            subject=message.subject,
            engine=engine,
        )
        return {
            "name": record.contact_name,
            "email": record.email,
            "phone": record.mobile,
            "company": record.organisation,
            "designation": record.designation,
            "address": record.address,
        }

    return extract
