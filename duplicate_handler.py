"""Deterministic contact identity matching and missing-field merging."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from duplicate_detector import normalize_email, normalize_mobile
from repository import MailboxRepository


@dataclass(frozen=True)
class MergeResult:
    created: bool = False
    updated: bool = False
    duplicates_removed: int = 0


def normalize_contact(contact: dict[str, Any]) -> dict[str, str]:
    """Map extraction output to the enterprise contact schema."""
    return {
        "name": _clean(contact.get("name") or contact.get("contact_name") or contact.get("contact_person_name")),
        "email": normalize_email(_clean(contact.get("email") or contact.get("email_id"))),
        "phone": normalize_mobile(_clean(contact.get("phone") or contact.get("mobile") or contact.get("mobile_number"))),
        "company": _clean(contact.get("company") or contact.get("organisation") or contact.get("organisation_name")),
        "designation": _clean(contact.get("designation")),
        "address": _clean(contact.get("address")),
        "city": _clean(contact.get("city")),
        "country": _clean(contact.get("country")),
    }


def merge_contact(
    repository: MailboxRepository,
    contact: dict[str, Any],
    source_message_id: str,
) -> MergeResult:
    """Upsert a contact by email, then phone, then name and company."""
    normalized = normalize_contact(contact)
    matches: list[dict[str, Any]] = []
    for match in (
        repository.find_contact_by_email(normalized["email"]),
        repository.find_contact_by_phone(normalized["phone"]),
        repository.find_contact_by_name_company(normalized["name"], normalized["company"]),
    ):
        if match and all(existing["id"] != match["id"] for existing in matches):
            matches.append(match)

    if not matches:
        repository.insert_contact(normalized, source_message_id)
        return MergeResult(created=True)

    primary = matches[0]
    updated = repository.update_missing_fields(int(primary["id"]), normalized)
    removed = 0
    for duplicate in matches[1:]:
        if repository.merge_and_delete(int(primary["id"]), int(duplicate["id"])):
            removed += 1
    return MergeResult(updated=updated or removed > 0, duplicates_removed=removed)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
