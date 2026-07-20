"""Bulk email processing for customer extraction and duplicate detection."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from duplicate_detector import detect_duplicates
from extractor import EmailExtractionEngine


LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]

DEFAULT_CUSTOM_DELIMITER = "---EMAIL---"
SEPARATOR_PATTERNS = (
    r"(?=^From:\s*)",
    r"(?=^Subject:\s*)",
    r"(?=^-{5}Original Message-{5}\s*$)",
)


def decode_txt_file(uploaded_file: Any) -> str:
    """Decode an uploaded TXT file-like object into text."""
    try:
        return uploaded_file.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        return uploaded_file.getvalue().decode("latin-1", errors="ignore")
    except AttributeError as exc:
        raise TypeError("uploaded_file must provide a getvalue() method") from exc


def split_emails(
    email_text: str,
    custom_delimiter: str | None = DEFAULT_CUSTOM_DELIMITER,
) -> list[str]:
    """Split a TXT payload into individual email messages."""
    if not isinstance(email_text, str):
        raise TypeError("email_text must be a string")

    cleaned_text = email_text.strip()
    if not cleaned_text:
        return []

    if custom_delimiter and custom_delimiter in cleaned_text:
        return [
            email.strip()
            for email in cleaned_text.split(custom_delimiter)
            if email.strip()
        ]

    for pattern in SEPARATOR_PATTERNS:
        parts = [
            part.strip()
            for part in re.split(pattern, cleaned_text, flags=re.MULTILINE)
            if part.strip()
        ]
        if len(parts) > 1:
            return parts

    return [cleaned_text]


def process_bulk_emails(
    email_text: str,
    custom_delimiter: str | None = DEFAULT_CUSTOM_DELIMITER,
    progress_callback: ProgressCallback | None = None,
    extraction_engine: EmailExtractionEngine | None = None,
) -> list[dict[str, Any]]:
    """Extract customers from many emails and return duplicate-classified records."""
    emails = split_emails(email_text, custom_delimiter=custom_delimiter)
    total_emails = len(emails)

    if total_emails == 0:
        LOGGER.warning("No emails found in bulk input.")
        return []

    engine = extraction_engine or EmailExtractionEngine()
    extracted_records: list[dict[str, Any]] = []

    for index, email in enumerate(emails, start=1):
        try:
            extracted_records.append(engine.extract(email))
        except Exception as exc:
            LOGGER.exception("Bulk extraction failed for email %s: %s", index, exc)
            extracted_records.append(
                {
                    "contact_person_name": "",
                    "email_id": "",
                    "organisation_name": "",
                    "mobile_number": "",
                    "normalized_phone": "",
                    "address": "",
                    "designation": "",
                    "subject": "",
                }
            )

        if progress_callback is not None:
            progress_callback(index, total_emails)

    return detect_duplicates(extracted_records)


def process_uploaded_txt_file(
    uploaded_file: Any,
    custom_delimiter: str | None = DEFAULT_CUSTOM_DELIMITER,
    progress_callback: ProgressCallback | None = None,
    extraction_engine: EmailExtractionEngine | None = None,
) -> list[dict[str, Any]]:
    """Process a TXT upload containing multiple email messages."""
    email_text = decode_txt_file(uploaded_file)
    return process_bulk_emails(
        email_text=email_text,
        custom_delimiter=custom_delimiter,
        progress_callback=progress_callback,
        extraction_engine=extraction_engine,
    )
