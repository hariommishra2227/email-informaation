"""Batch-oriented Outlook mailbox synchronization service."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import config
from duplicate_handler import customer_record_to_contact
from extractor import EmailExtractionEngine
from repository import EmailSyncRepository
from services import graph_client
from services.email_processor import build_customer_record
from storage import database


LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[int, int], None]


@dataclass
class SyncResult:
    """Summary metrics for one mailbox sync run."""

    processed_emails: int = 0
    skipped_emails: int = 0
    new_contacts: int = 0
    updated_contacts: int = 0
    duplicates_removed: int = 0
    failed_emails: int = 0
    total_processing_time: float = 0.0
    last_sync_datetime: str = ""


class OutlookMailboxSyncService:
    """Process mailbox synchronization in bounded Graph/database batches."""

    def __init__(self, repository: EmailSyncRepository | None = None) -> None:
        self.repository = repository or EmailSyncRepository()

    def sync_mailbox(
        self,
        user_id: str,
        progress_callback: ProgressCallback | None = None,
        batch_size: int | None = None,
        engine: EmailExtractionEngine | None = None,
    ) -> SyncResult:
        """Sync Outlook messages using Microsoft Graph delta pagination."""
        started = time.perf_counter()
        result = SyncResult()
        extractor = engine or EmailExtractionEngine()
        fetch_size = int(batch_size or config.EMAIL_FETCH_BATCH_SIZE)
        process_checkpoint = max(1, int(config.EMAIL_PROCESS_BATCH_SIZE))
        processed_seen = 0
        delta_link = self.repository.get_delta_link(user_id)
        self.repository.set_sync_state(user_id, status="Running", processed_records=0)

        try:
            for messages in graph_client.iter_mailbox_message_pages(
                user_id,
                page_size=fetch_size,
                delta_link=delta_link,
            ):
                latest_delta_link = getattr(messages, "delta_link", "")
                page_total = len(messages)
                for index, message in enumerate(messages, start=1):
                    processed_seen += 1
                    try:
                        database.upsert_outlook_message(message)
                        if database.message_was_imported(user_id, message.message_id):
                            result.skipped_emails += 1
                            continue
                        customer = build_customer_record(
                            user_id=user_id,
                            text=message.body,
                            source="Outlook",
                            source_message_id=message.message_id,
                            sender_email=message.sender_email,
                            sender_name=message.sender_name,
                            subject=message.subject,
                            engine=extractor,
                        )
                        upsert = self.repository.upsert_contact(customer_record_to_contact(customer), user_id=user_id)
                        database.set_message_status(user_id, message.message_id, customer.status)
                        result.processed_emails += 1
                        result.new_contacts += int(upsert.created)
                        result.updated_contacts += int(upsert.updated)
                        result.duplicates_removed += int(upsert.duplicate_removed)
                    except Exception as exc:
                        LOGGER.exception("Mailbox sync failed for message hash=%s", _safe_message_id(message.message_id))
                        result.failed_emails += 1
                        database.record_email_failure(user_id, message.message_id, str(exc))
                        database.write_processing_log(user_id, message.message_id, "ERROR", "Mailbox sync failed", str(exc))
                    finally:
                        if progress_callback is not None:
                            progress_callback(index, page_total)
                    if processed_seen % process_checkpoint == 0:
                        self.repository.set_sync_state(
                            user_id,
                            delta_link=None,
                            status="Running",
                            processed_records=processed_seen,
                        )
                if latest_delta_link:
                    delta_link = latest_delta_link
                    self.repository.set_sync_state(
                        user_id,
                        delta_link=latest_delta_link,
                        status="Running",
                        processed_records=processed_seen,
                    )
            self.repository.set_sync_state(
                user_id,
                delta_link=delta_link or None,
                status="Succeeded",
                processed_records=processed_seen,
                successful=True,
            )
            result.last_sync_datetime = database.get_sync_state(user_id).get("last_successful_sync_at", "")
        except Exception as exc:
            self.repository.set_sync_state(
                user_id,
                delta_link=None,
                status="Failed",
                error_message=str(exc),
                processed_records=processed_seen,
            )
            raise
        finally:
            result.total_processing_time = round(time.perf_counter() - started, 2)
        return result


def sync_outlook_mailbox(user_id: str, progress_callback: ProgressCallback | None = None) -> SyncResult:
    """Convenience entrypoint for Streamlit and local worker mode."""
    return OutlookMailboxSyncService().sync_mailbox(user_id, progress_callback=progress_callback)


def _safe_message_id(message_id: str) -> str:
    """Return a short non-sensitive message identifier for logs."""
    import hashlib

    return hashlib.sha256(str(message_id or "").encode("utf-8")).hexdigest()[:12]
