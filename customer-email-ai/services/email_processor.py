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
import config
from datetime import datetime, timezone
from llm_extractor import extract_with_llm


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
    receiver_name: str = "",
    subject: str = "",
    engine: EmailExtractionEngine | None = None,
) -> CustomerRecord:
    """Extract and normalize one customer record from text."""
    extractor = engine or EmailExtractionEngine()
    cleaned_text = clean_html_to_text(text)
    extracted = extractor.extract(
        cleaned_text,
        graph_sender_email=sender_email,
        graph_sender_name=sender_name,
    )
    llm_result = {"fields": {}, "llm_used": False, "llm_model": "", "llm_error": ""}
    if config.LLM_ENABLED:
        llm_result = extract_with_llm(cleaned_text, extracted, sender_email=sender_email)
        for field, item in llm_result.get("fields", {}).items():
            if not item.get("value"):
                continue
            target = {"customer_name": "contact_person_name", "email": "email_id", "organisation": "organisation_name", "mobile": "mobile_number", "mobile_number": "mobile_number", "designation": "designation", "address": "address"}.get(field)
            confidence_key = {"customer_name": "name_confidence", "email": "email_confidence", "organisation": "organisation_confidence", "mobile": "mobile_confidence", "designation": "designation_confidence", "address": "address_confidence"}.get(field)
            if target and (not extracted.get(target) or float(item.get("confidence", 0)) > float(extracted.get(confidence_key, 0) or 0)):
                extracted[target] = item["value"]
                if field == "email":
                    extracted["email"] = item["value"]
                    extracted["email_id"] = item["value"]
                if confidence_key:
                    extracted[confidence_key] = float(item.get("confidence", 0))

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
    if config.is_internal_company(customer_values["organisation"]):
        customer_values["organisation"] = ""
    normalized_email = normalize_email(customer_values["email"])
    normalized_mobile = normalize_mobile(customer_values["mobile"])
    confidence = calculate_confidence(customer_values)
    status = _status_for_customer(user_id, normalized_email, normalized_mobile, confidence)
    review_status = "Approved" if normalized_email and all(
        float(extracted.get(key, 0) or 0) >= threshold
        for key, threshold in (("name_confidence", 0.80), ("email_confidence", 0.80), ("organisation_confidence", 0.60), ("address_confidence", 0.50))
        if customer_values.get({"name_confidence": "contact_name", "email_confidence": "email", "organisation_confidence": "organisation", "address_confidence": "address"}[key])
    ) else ("Needs Review" if normalized_email else "Rejected")

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
        name_source=str(extracted.get("name_source") or ("graph_sender" if sender_name else "")),
        name_confidence=float(extracted.get("name_confidence", 0) or 0),
        name_evidence=contact_name,
        email_source=str(extracted.get("email_source") or ("graph_sender" if sender_email else "")),
        email_confidence=float(extracted.get("email_confidence", 0) or 0),
        email_evidence=customer_values["email"],
        organisation_source="llm" if llm_result.get("fields", {}).get("organisation", {}).get("value") == customer_values["organisation"] and llm_result.get("llm_used") else ("body" if customer_values["organisation"] else ""),
        organisation_confidence=float(extracted.get("organisation_confidence", 0.45) or 0) if customer_values["organisation"] else 0.0,
        organisation_evidence=llm_result.get("fields", {}).get("organisation", {}).get("evidence", "") or customer_values["organisation"],
        mobile_source="body" if customer_values["mobile"] else "",
        mobile_confidence=0.70 if customer_values["mobile"] else 0.0,
        mobile_evidence=customer_values["mobile"],
        designation_source="body" if customer_values["designation"] else "",
        designation_confidence=0.50 if customer_values["designation"] else 0.0,
        designation_evidence=customer_values["designation"],
        address_source="llm" if llm_result.get("fields", {}).get("address", {}).get("value") == customer_values["address"] and llm_result.get("llm_used") else ("body" if customer_values["address"] else ""),
        address_confidence=float(extracted.get("address_confidence", 0.60) or 0) if customer_values["address"] else 0.0,
        address_evidence=llm_result.get("fields", {}).get("address", {}).get("evidence", "") or customer_values["address"],
        review_status=review_status,
        sender_email=sender_email,
        sender_name=sender_name.strip(),
        receiver_name=receiver_name.strip(),
        sender_domain=config.get_email_domain(sender_email),
        processed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        llm_used=bool(llm_result.get("llm_used")),
        llm_model=str(llm_result.get("llm_model") or ""),
        llm_error=str(llm_result.get("llm_error") or ""),
        extraction_method="regex_spacy_llm" if llm_result.get("llm_used") else "regex_spacy",
    )


def process_outlook_message(
    user_id: str,
    message: OutlookMessage,
    engine: EmailExtractionEngine | None = None,
) -> CustomerRecord:
    """Import one Outlook message once and save its extracted customer record."""
    database.ensure_user(user_id)
    if config.is_internal_sender(message.sender_email):
        LOGGER.info("Skipping internal sender before extraction message_id=%s.", message.message_id)
        database.write_processing_log(user_id, message.message_id, "INFO", "Internal sender skipped", "internal_sender")
        return CustomerRecord(
            user_id=user_id,
            source="Outlook",
            source_message_id=message.message_id,
            subject=message.subject,
            status="Skipped Internal",
        )

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
            receiver_name=message.receiver_name,
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
