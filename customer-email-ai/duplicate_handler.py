"""Contact duplicate merge helpers for enterprise sync records."""

from __future__ import annotations

import re
from typing import Any

from duplicate_detector import normalize_mobile
from models import CustomerRecord


def customer_record_to_contact(customer: CustomerRecord) -> dict[str, str]:
    """Map the existing extraction model to the enterprise contacts schema."""
    city, country = _split_city_country(customer.address)
    return {
        "name": customer.contact_name,
        "email": customer.normalized_email or customer.email.lower().strip(),
        "phone": customer.normalized_mobile or normalize_mobile(customer.mobile),
        "company": customer.organisation,
        "designation": customer.designation,
        "address": customer.address,
        "city": city,
        "country": country,
        "source_message_id": customer.source_message_id,
    }


def _split_city_country(address: str) -> tuple[str, str]:
    """Infer city and country from a comma-separated address without overfitting."""
    parts = [part.strip() for part in re.split(r",|\n", str(address or "")) if part.strip()]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[-2], parts[-1]

