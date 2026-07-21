"""Convert Outlook/PDF/TXT/manual text into stored customer records."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from duplicate_detector import normalize_email, normalize_mobile
from extractor import EmailExtractionEngine
from models import CustomerRecord, OutlookMessage
from storage import database


LOGGER = logging.getLogger(__name__)
INTERNAL_DOMAIN = "itsipl.com"


def clean_html_to_text(body: str) -> str:
    """Convert HTML-only or mixed email body content into plain text."""
    if not body:
        return ""
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link", "svg"]):
        tag.decompose()
    for tag in soup.find_all(["img", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def calculate_confidence(customer: dict[str, Any]) -> int:
    """Calculate extraction confidence from populated fields."""
    scoring_fields = (
        ("email", 30),
        ("contact_name", 15),
        ("organisation", 15),
        ("mobile", 15),
        ("designation", 10),
        ("subject", 10),
        ("address", 5),
    )
    return min(100, sum(points for field, points in scoring_fields if str(customer.get(field, "")).strip()))


def _status_for_customer(user_id: str, normalized_email: str, normalized_mobile: str, confidence: int) -> str:
    """Classify a customer while retaining duplicate rows in the registry."""
    database.initialize_database()
    if database.customer_duplicate_exists(user_id, normalized_email, normalized_mobile):
        return "Duplicate"
    if confidence < 45:
        return "Incomplete"
    return "Unique"


def _looks_like_valid_email(value: str) -> bool:
    """Return whether a value has a basic email shape."""
    return bool(re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value.strip()))


def build_customer_record(
    *,
    user_id: str,
    text: str,
    source: str,
    source_message_id: str = "",
    sender_email: str = "",
    sender_name: str = "",
    subject: str = "",
    engine: EmailExtractionEngine | None = None,
) -> CustomerRecord:
    """Extract and normalize one customer record from text."""
    extractor = engine or EmailExtractionEngine()
    cleaned_text = clean_html_to_text(text)
    extracted = extractor.extract(cleaned_text)

    email = str(extracted.get("email_id") or extracted.get("email") or "").strip()
    if not email and _looks_like_valid_email(sender_email) and not sender_email.lower().endswith("@" + INTERNAL_DOMAIN):
        email = sender_email.strip()

    contact_name = str(extracted.get("contact_person_name") or extracted.get("name") or "").strip()
    if not contact_name:
        contact_name = sender_name.strip()

    customer_values = {
        "contact_name": contact_name,
        "organisation": str(extracted.get("organisation_name") or extracted.get("company") or "").strip(),
        "email": email,
        "mobile": str(extracted.get("mobile_number") or extracted.get("phone") or "").strip(),
        "designation": str(extracted.get("designation") or "").strip(),
        "address": str(extracted.get("address") or "").strip(),
        "subject": subject or str(extracted.get("subject") or "").strip(),
    }
    normalized_email = normalize_email(customer_values["email"])
    normalized_mobile = normalize_mobile(customer_values["mobile"])
    confidence = calculate_confidence(customer_values)
    status = _status_for_customer(user_id, normalized_email, normalized_mobile, confidence)

    return CustomerRecord(
        user_id=user_id,
        contact_name=customer_values["contact_name"],
        organisation=customer_values["organisation"],
        email=customer_values["email"],
        normalized_email=normalized_email,
        mobile=customer_values["mobile"],
        normalized_mobile=normalized_mobile,
        designation=customer_values["designation"],
        address=customer_values["address"],
        subject=customer_values["subject"],
        source=source,
        source_message_id=source_message_id,
        confidence=confidence,
        status=status,
    )


def process_outlook_message(
    user_id: str,
    message: OutlookMessage,
    engine: EmailExtractionEngine | None = None,
) -> CustomerRecord:
    """Import one Outlook message once and save its extracted customer record."""
    database.ensure_user(user_id)
    database.upsert_outlook_message(message)

    if database.message_was_imported(user_id, message.message_id):
        database.set_message_status(user_id, message.message_id, "Already Processed")
        return CustomerRecord(
            user_id=user_id,
            email=message.sender_email,
            normalized_email=normalize_email(message.sender_email),
            source="Outlook",
            source_message_id=message.message_id,
            subject=message.subject,
            status="Already Processed",
        )

    try:
        if not (message.body or "").strip():
            raise ValueError("Outlook message body is empty.")

        customer = build_customer_record(
            user_id=user_id,
            text=message.body,
            source="Outlook",
            source_message_id=message.message_id,
            sender_email=message.sender_email,
            sender_name=message.sender_name,
            subject=message.subject,
            engine=engine,
        )
        database.insert_customer(customer)
        database.set_message_status(user_id, message.message_id, customer.status)
        return customer
    except Exception as exc:
        LOGGER.exception("Outlook message import failed for %s", message.message_id)
        database.set_message_status(user_id, message.message_id, "Failed")
        database.write_processing_log(user_id, message.message_id, "ERROR", "Outlook import failed", str(exc))
        return CustomerRecord(
            user_id=user_id,
            source="Outlook",
            source_message_id=message.message_id,
            subject=message.subject,
            status="Failed",
        )
