"""Local/SQS-ready job dispatch boundaries."""

from __future__ import annotations

import json
from typing import Any

import config
from sync import sync_outlook_mailbox
from storage import database


def enqueue_email_sync(user_id: str) -> dict[str, Any]:
    """Enqueue or run an email sync job depending on JOB_BACKEND."""
    job = database.create_sync_job(user_id, backend=config.JOB_BACKEND)
    if job.get("existing"):
        return {"backend": config.JOB_BACKEND, "status": job["status"], "job_id": job["id"], "existing": True}
    if config.JOB_BACKEND == "local":
        database.update_job_status(job["id"], "Running")
        try:
            result = sync_outlook_mailbox(user_id)
        except Exception as exc:
            database.update_job_status(job["id"], "Failed", error_message=str(exc))
            raise
        database.update_job_status(job["id"], "Completed")
        return {"backend": "local", "status": "completed", "job_id": job["id"], "processed_emails": result.processed_emails}
    if config.JOB_BACKEND == "sqs":
        if not config.AWS_SQS_QUEUE_URL:
            raise RuntimeError("AWS_SQS_QUEUE_URL is required when JOB_BACKEND=sqs.")
        import boto3

        boto3.client("sqs", region_name=config.AWS_REGION).send_message(
            QueueUrl=config.AWS_SQS_QUEUE_URL,
            MessageBody=json.dumps({"job_type": "email_sync", "user_id": user_id, "job_id": job["id"]}),
        )
        return {"backend": "sqs", "status": "queued", "job_id": job["id"]}
    raise RuntimeError(f"Unsupported JOB_BACKEND: {config.JOB_BACKEND}")
