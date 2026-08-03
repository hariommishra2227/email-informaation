"""Local/SQS-ready job dispatch boundaries."""

from __future__ import annotations

import json
from typing import Any

import config
from sync import sync_outlook_mailbox


def enqueue_email_sync(user_id: str) -> dict[str, Any]:
    """Enqueue or run an email sync job depending on JOB_BACKEND."""
    if config.JOB_BACKEND == "local":
        result = sync_outlook_mailbox(user_id)
        return {"backend": "local", "status": "completed", "processed_emails": result.processed_emails}
    if config.JOB_BACKEND == "sqs":
        if not config.AWS_SQS_QUEUE_URL:
            raise RuntimeError("AWS_SQS_QUEUE_URL is required when JOB_BACKEND=sqs.")
        import boto3

        boto3.client("sqs", region_name=config.AWS_REGION).send_message(
            QueueUrl=config.AWS_SQS_QUEUE_URL,
            MessageBody=json.dumps({"job_type": "email_sync", "user_id": user_id}),
        )
        return {"backend": "sqs", "status": "queued"}
    raise RuntimeError(f"Unsupported JOB_BACKEND: {config.JOB_BACKEND}")
