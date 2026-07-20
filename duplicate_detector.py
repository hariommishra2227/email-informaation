"""Duplicate detection for customer records extracted from business emails.

The detector handles exact duplicate rules on email and mobile values, and
uses RapidFuzz for fuzzy name/organisation similarity scoring.
"""

from __future__ import annotations

import logging
import re
from typing import Any

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    fuzz = None

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

REQUIRED_KEYS = (
    "contact_person_name",
    "email_id",
    "organisation_name",
    "mobile_number",
    "address",
    "designation",
)


def normalize_email(value: str) -> str:
    """Normalize an email value by removing spaces and lowercasing."""
    return re.sub(r"\s+", "", value).lower() if isinstance(value, str) else ""


def normalize_mobile(value: str) -> str:
    """Normalize a mobile value by keeping only the last 10 digits."""
    if not isinstance(value, str):
        return ""

    digits = re.sub(r"\D", "", value)
    if digits.startswith("91") and len(digits) > 10:
        digits = digits[2:]
    if len(digits) >= 10:
        digits = digits[-10:]
    return digits


def similarity_score(left: str, right: str) -> int:
    """Return the RapidFuzz similarity score in the range 0-100."""
    if not left or not right or fuzz is None:
        return 0
    return int(fuzz.ratio(left.lower(), right.lower()))


def _prepare_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized customer record suitable for duplicate detection."""
    if not isinstance(record, dict):
        raise TypeError("each record must be a dictionary")

    missing_keys = [key for key in REQUIRED_KEYS if key not in record]
    if missing_keys:
        raise KeyError(f"missing required keys: {missing_keys}")

    normalized_record = dict(record)
    normalized_record["contact_person_name"] = str(record.get("contact_person_name", "")).strip()
    normalized_record["email_id"] = normalize_email(record.get("email_id", ""))
    normalized_record["organisation_name"] = str(record.get("organisation_name", "")).strip()
    normalized_record["mobile_number"] = str(record.get("mobile_number", "")).strip()
    normalized_record["normalized_phone"] = normalize_mobile(
        record.get("normalized_phone") or record.get("mobile_number", "")
    )
    normalized_record["address"] = str(record.get("address", "")).strip()
    normalized_record["designation"] = str(record.get("designation", "")).strip()
    return normalized_record


def detect_duplicates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify records as Duplicate, Possible Duplicate, or Unique.

    Priority order:
    1. Same email -> Duplicate (100)
    2. Same mobile -> Duplicate (100)
    3. Name similarity >= 85 and organisation similarity >= 85 -> Possible Duplicate
    4. Otherwise -> Unique (0)
    """
    if not isinstance(records, list):
        raise TypeError("records must be a list of customer dictionaries")

    normalized_records = [_prepare_record(record) for record in records]
    results: list[dict[str, Any]] = []

    for index, record in enumerate(normalized_records):
        status = "Unique"
        confidence = 0

        for other_index, other_record in enumerate(normalized_records):
            if index == other_index:
                continue

            same_email = record.get("email_id") and record.get("email_id") == other_record.get("email_id")
            same_mobile = record.get("normalized_phone") and record.get("normalized_phone") == other_record.get("normalized_phone")

            if same_email or same_mobile:
                status = "Duplicate"
                confidence = 100
                break

        if status == "Unique":
            best_confidence = 0
            for other_index, other_record in enumerate(normalized_records):
                if index == other_index:
                    continue

                name_similarity = similarity_score(record.get("contact_person_name", ""), other_record.get("contact_person_name", ""))
                organisation_similarity = similarity_score(record.get("organisation_name", ""), other_record.get("organisation_name", ""))

                if name_similarity >= 85 and organisation_similarity >= 85:
                    status = "Possible Duplicate"
                    best_confidence = max(best_confidence, round((name_similarity + organisation_similarity) / 2))

            confidence = best_confidence

        results.append(
            {
                **record,
                "duplicate_status": status,
                "confidence_score": confidence,
            }
        )

    return results
