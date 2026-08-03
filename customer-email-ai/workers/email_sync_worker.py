"""Minimal worker entrypoint for future AWS SQS processing."""

from __future__ import annotations

import json
import logging
from typing import Any

from sync import sync_outlook_mailbox


LOGGER = logging.getLogger(__name__)


def handle_message(message: dict[str, Any]) -> dict[str, Any]:
    """Handle one decoded job message."""
    if message.get("job_type") != "email_sync":
        raise ValueError("Unsupported job type.")
    user_id = str(message.get("user_id") or "")
    if not user_id:
        raise ValueError("Job message is missing user_id.")
    result = sync_outlook_mailbox(user_id)
    return {"processed_emails": result.processed_emails, "failed_emails": result.failed_emails}


def handle_sqs_record(record: dict[str, Any]) -> dict[str, Any]:
    """Handle one AWS SQS event record."""
    body = json.loads(str(record.get("body") or "{}"))
    return handle_message(body)
