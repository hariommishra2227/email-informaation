"""Attachment storage backends for local development and private S3."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

import config


MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class StoredAttachment:
    """Metadata needed for the database attachment row."""

    original_filename: str
    safe_filename: str
    content_type: str
    file_size: int
    storage_key: str
    checksum: str


def sanitize_filename(filename: str) -> str:
    """Return a traversal-safe filename."""
    name = Path(str(filename or "attachment")).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name[:180] or "attachment"


def checksum_bytes(content: bytes) -> str:
    """Return a SHA-256 checksum for duplicate detection."""
    return hashlib.sha256(content).hexdigest()


def store_attachment(filename: str, content: bytes, content_type: str = "") -> StoredAttachment:
    """Store attachment bytes in the configured backend."""
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError("Attachment is larger than the configured maximum size.")
    safe_name = sanitize_filename(filename)
    checksum = checksum_bytes(content)
    generated = f"{checksum[:16]}-{secrets.token_hex(8)}-{safe_name}"
    if config.STORAGE_BACKEND == "s3":
        storage_key = _store_s3(generated, content, content_type)
    else:
        storage_key = _store_local(generated, content)
    return StoredAttachment(
        original_filename=filename,
        safe_filename=generated,
        content_type=content_type,
        file_size=len(content),
        storage_key=storage_key,
        checksum=checksum,
    )


def _store_local(filename: str, content: bytes) -> str:
    base = Path(config.LOCAL_ATTACHMENT_DIR).resolve()
    base.mkdir(parents=True, exist_ok=True)
    target = (base / filename).resolve()
    if base not in target.parents and target != base:
        raise ValueError("Invalid attachment path.")
    target.write_bytes(content)
    return str(target.relative_to(base))


def _store_s3(filename: str, content: bytes, content_type: str) -> str:
    if not config.AWS_S3_BUCKET:
        raise RuntimeError("AWS_S3_BUCKET is required when STORAGE_BACKEND=s3.")
    import boto3

    key = f"attachments/{filename}"
    client = boto3.client("s3", region_name=config.AWS_REGION)
    extra_args = {"ServerSideEncryption": "AES256"}
    if content_type:
        extra_args["ContentType"] = content_type
    client.put_object(
        Bucket=config.AWS_S3_BUCKET,
        Key=key,
        Body=content,
        **extra_args,
    )
    return key
