"""Resumable, bounded-memory Outlook extraction jobs for large mailboxes."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Callable
from uuid import uuid4

import config
from models import OutlookMessage
from services import graph_client
from services.email_processor import process_outlook_message
from storage import database

LOGGER = logging.getLogger(__name__)
TERMINAL = {"Unique", "Duplicate", "Incomplete", "Already Processed"}


@dataclass
class JobProgress:
    job_id: str
    fetched: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    remaining: int = 0
    status: str = "Pending"


class LargeMailboxSynchronizer:
    """Process one Graph page at a time and persist a restart checkpoint."""

    def __init__(self, user_id: str, target_count: int, job_id: str | None = None, batch_size: int = 100) -> None:
        self.user_id = user_id
        self.target_count = max(0, int(target_count))
        self.job_id = job_id or str(uuid4())
        self.batch_size = max(1, int(batch_size))

    def run(self, progress: Callable[[JobProgress], None] | None = None, pause: Callable[[], bool] | None = None) -> JobProgress:
        database.initialize_database()
        job = database.get_extraction_job(self.job_id)
        if job is None:
            database.create_extraction_job(self.job_id, self.user_id, self.target_count)
            job = database.get_extraction_job(self.job_id) or {}
        processed = int(job.get("processed_count", 0))
        skipped = int(job.get("skipped_count", 0))
        failed = int(job.get("failed_count", 0))
        result = JobProgress(self.job_id, processed + skipped + failed, processed, skipped, failed, self.target_count, str(job.get("status", "Pending")))
        if result.status == "Completed":
            return result
        database.update_extraction_job(self.job_id, status="Processing")
        result.status = "Processing"
        next_link = str(job.get("next_link") or "")

        def checkpoint(link: str) -> None:
            database.update_extraction_job(self.job_id, next_link=link or None)

        try:
            pages = graph_client.iter_mailbox_message_pages(
                self.user_id,
                page_size=50,
                start_next_link=next_link or None,
                checkpoint=checkpoint,
            )
            for page in pages:
                if pause and pause():
                    database.update_extraction_job(self.job_id, status="Paused")
                    result.status = "Paused"
                    return result
                for message in page:
                    if self.target_count and result.fetched >= self.target_count:
                        break
                    result.fetched += 1
                    database.upsert_outlook_message(message)
                    status = database.message_processing_status(self.user_id, message.message_id)
                    if status in TERMINAL:
                        result.skipped += 1
                        continue
                    database.set_message_status(self.user_id, message.message_id, "Processing")
                    try:
                        body = graph_client.get_message_body(self.user_id, message.message_id)
                        hydrated = OutlookMessage(**{**message.to_dict(), "body": body})
                        record = process_outlook_message(self.user_id, hydrated)
                        result.processed += 1
                        if record.status == "Failed":
                            result.failed += 1
                    except Exception as exc:
                        result.failed += 1
                        database.set_message_status(self.user_id, message.message_id, "Failed")
                        database.write_processing_log(self.user_id, message.message_id, "ERROR", "Large-mailbox extraction failed", exc.__class__.__name__)
                    if (result.processed + result.skipped + result.failed) % self.batch_size == 0:
                        self._save(result)
                        if progress:
                            progress(result)
                self._save(result)
                if progress:
                    progress(result)
                if self.target_count and result.fetched >= self.target_count:
                    break
            result.status = "Completed"
            self._save(result)
            return result
        except Exception:
            LOGGER.exception("Mailbox job %s stopped; checkpoint retained.", self.job_id)
            result.status = "Failed"
            self._save(result)
            raise

    def _save(self, result: JobProgress) -> None:
        database.update_extraction_job(
            self.job_id,
            processed_count=result.processed,
            skipped_count=result.skipped,
            failed_count=result.failed,
            status=result.status,
        )
