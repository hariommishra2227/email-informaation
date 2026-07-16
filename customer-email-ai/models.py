"""Shared data models for Outlook messages and extracted customers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OutlookMessage:
    """A mailbox message in the shape needed by the importer."""

    message_id: str
    user_id: str
    sender_name: str
    sender_email: str
    subject: str
    body: str
    received_datetime: str
    is_read: bool
    body_preview: str = ""
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)

    @property
    def attachment_count(self) -> int:
        """Return the number of advertised attachments."""
        return len(self.attachment_names)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dictionary representation."""
        return asdict(self)


@dataclass
class CustomerRecord:
    """A normalized customer record ready for storage and export."""

    user_id: str
    contact_name: str = ""
    organisation: str = ""
    email: str = ""
    normalized_email: str = ""
    mobile: str = ""
    normalized_mobile: str = ""
    designation: str = ""
    address: str = ""
    subject: str = ""
    source: str = "Manual"
    source_message_id: str = ""
    confidence: int = 0
    status: str = "Unique"

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the existing app/export field names for compatibility."""
        return {
            "contact_person_name": self.contact_name,
            "organisation_name": self.organisation,
            "email_id": self.email,
            "mobile_number": self.mobile,
            "normalized_phone": self.normalized_mobile,
            "designation": self.designation,
            "address": self.address,
            "subject": self.subject,
            "input_source": self.source,
            "extraction_confidence": self.confidence,
            "duplicate_status": self.status,
        }
