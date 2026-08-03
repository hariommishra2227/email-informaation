"""Minimal worker entrypoint for future AWS SQS processing."""

from __future__ import annotations

import json
import logging
import signal
import time
from typing import Any

import config
from sync import sync_outlook_mailbox
from storage import database


LOGGER = logging.getLogger(__name__)
_STOP = False


def handle_message(message: dict[str, Any]) -> dict[str, Any]:
    """Handle one decoded job message."""
    if message.get("job_type") != "email_sync":
        raise ValueError("Unsupported job type.")
    user_id = str(message.get("user_id") or "")
    if not user_id:
        raise ValueError("Job message is missing user_id.")
    job_id = int(message.get("job_id") or 0)
    if job_id:
        database.update_job_status(job_id, "Running")
    try:
        result = sync_outlook_mailbox(user_id)
    except Exception as exc:
        if job_id:
            database.update_job_status(job_id, "Failed", error_message=str(exc))
        raise
    if job_id:
        database.update_job_status(job_id, "Completed")
    return {"processed_emails": result.processed_emails, "failed_emails": result.failed_emails, "job_id": job_id}


def handle_sqs_record(record: dict[str, Any]) -> dict[str, Any]:
    """Handle one AWS SQS event record."""
    body = json.loads(str(record.get("body") or "{}"))
    return handle_message(body)


def run_worker() -> None:
    """Long-poll SQS and process one email sync job at a time."""
    if not config.AWS_SQS_QUEUE_URL:
        raise RuntimeError("AWS_SQS_QUEUE_URL is required for the SQS worker.")
    import boto3

    client = boto3.client("sqs", region_name=config.AWS_REGION)
    while not _STOP:
        response = client.receive_message(
            QueueUrl=config.AWS_SQS_QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=300,
            AttributeNames=["ApproximateReceiveCount"],
        )
        for record in response.get("Messages", []):
            receipt = record["ReceiptHandle"]
            attempts = int((record.get("Attributes") or {}).get("ApproximateReceiveCount") or 1)
            body = json.loads(record.get("Body") or "{}")
            job_id = int(body.get("job_id") or 0)
            if job_id:
                database.update_job_status(job_id, "Running", attempts=attempts)
            try:
                client.change_message_visibility(
                    QueueUrl=config.AWS_SQS_QUEUE_URL,
                    ReceiptHandle=receipt,
                    VisibilityTimeout=900,
                )
                handle_message(body)
            except Exception as exc:
                LOGGER.exception("SQS email sync job failed job_id=%s attempts=%s", job_id or "unknown", attempts)
                if job_id:
                    database.update_job_status(job_id, "Failed", error_message=str(exc), attempts=attempts)
                continue
            client.delete_message(QueueUrl=config.AWS_SQS_QUEUE_URL, ReceiptHandle=receipt)
        time.sleep(0.1)


def _handle_stop(signum, frame) -> None:  # pragma: no cover
    global _STOP
    _STOP = True


if __name__ == "__main__":  # pragma: no cover
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    run_worker()
