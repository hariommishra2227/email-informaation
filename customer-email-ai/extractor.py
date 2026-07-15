"""Reusable extraction engine for customer contact details from business emails.

The module uses spaCy, regular expressions, phonenumbers, and BeautifulSoup
for a Phase 1 rule-based contact extraction pipeline.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any

try:
    import phonenumbers
except ImportError:  # pragma: no cover
    phonenumbers = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

try:
    import spacy
except ImportError:  # pragma: no cover
    spacy = None

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

EMAIL_PATTERN = re.compile(r"(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+|00)?\d{1,3}[\s().-]?\d{3}[\s().-]?\d{4,5}(?!\w)")
ADDRESS_KEYWORDS = (
    "street",
    "road",
    "avenue",
    "lane",
    "drive",
    "way",
    "court",
    "place",
    "boulevard",
    "highway",
    "st",
    "rd",
    "ave",
    "ln",
    "dr",
)

DESIGNATION_KEYWORDS: dict[str, str] = {
    "manager": "Manager",
    "it manager": "IT Manager",
    "purchase manager": "Purchase Manager",
    "project manager": "Project Manager",
    "director": "Director",
    "ceo": "CEO",
    "cto": "CTO",
    "cio": "CIO",
    "system administrator": "System Administrator",
    "network engineer": "Network Engineer",
    "sales manager": "Sales Manager",
    "business development manager": "Business Development Manager",
}


class EmailExtractionEngine:
    """Extract contact details from a general business email.

    The engine combines spaCy named entities, regex-based email and phone
    extraction, phone normalization, HTML cleanup, and rule-based
    designation detection.
    """

    def __init__(self) -> None:
        self.nlp: Any = self._initialize_spacy_model()

    def _initialize_spacy_model(self) -> Any:
        """Initialize the spaCy NLP model with a safe fallback."""
        if spacy is None:
            LOGGER.warning("spaCy is not installed; falling back to a minimal mode.")
            return None

        try:
            return spacy.load("en_core_web_sm")
        except OSError:
            LOGGER.warning("en_core_web_sm is not available; using a blank English model.")
            return spacy.blank("en")
        except Exception as exc:
            LOGGER.exception("Unexpected spaCy initialization failure: %s", exc)
            return spacy.blank("en")

    def clean_html(self, text: str) -> str:
        """Strip HTML tags and decode HTML entity strings."""
        if not text:
            return ""

        try:
            if BeautifulSoup is None:
                LOGGER.warning("BeautifulSoup is not available; HTML cleanup skipped.")
                return unescape(text)
            soup = BeautifulSoup(text, "html.parser")
            cleaned = soup.get_text("\n", strip=True)
            return unescape(cleaned)
        except Exception as exc:
            LOGGER.exception("HTML cleanup failed: %s", exc)
            return unescape(text)

    def _clean_text(self, text: str) -> str:
        """Normalize repeated whitespace into a single space."""
        return re.sub(r"\s+", " ", text).strip()

    def _iter_lines(self, text: str) -> list[str]:
        """Return cleaned, non-empty lines from a text blob."""
        return [self._clean_text(line) for line in text.splitlines() if self._clean_text(line)]

    def extract_email_addresses(self, text: str) -> list[str]:
        """Extract unique email addresses from the supplied text."""
        if not text:
            return []

        try:
            return list(dict.fromkeys(EMAIL_PATTERN.findall(text)))
        except Exception as exc:
            LOGGER.exception("Email extraction failed: %s", exc)
            return []

    def _normalize_phone_number(self, raw_number: str) -> str:
        """Validate and normalize the candidate phone number."""
        if not raw_number or phonenumbers is None:
            return ""

        cleaned_number = re.sub(r"\s+", "", raw_number)
        try:
            parsed_number = phonenumbers.parse(cleaned_number, None)
            if not phonenumbers.is_valid_number(parsed_number):
                return ""
            return phonenumbers.format_number(parsed_number, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            return ""

    def extract_mobile_numbers(self, text: str) -> list[str]:
        """Extract phone numbers from phone-related lines and normalize them."""
        if not text:
            return []

        try:
            phones: list[str] = []
            for line in self._iter_lines(text):
                lowered_line = line.lower()
                if any(keyword in lowered_line for keyword in ("phone", "mobile", "tel", "telephone", "contact")):
                    phones.extend(PHONE_PATTERN.findall(line))

            normalized_numbers: list[str] = []
            for candidate in phones:
                candidate = candidate.strip()
                if len(re.sub(r"\D", "", candidate)) < 8:
                    continue
                normalized = self._normalize_phone_number(candidate)
                if normalized:
                    normalized_numbers.append(normalized)

            return list(dict.fromkeys(normalized_numbers))
        except Exception as exc:
            LOGGER.exception("Mobile number extraction failed: %s", exc)
            return []

    def extract_contact_person_name(self, text: str) -> str:
        """Extract a likely contact person's name with conservative heuristics."""
        if not text:
            return ""

        try:
            lines = self._iter_lines(text)

            for line in lines:
                for prefix in ("my name is ", "this is ", "i am "):
                    if line.lower().startswith(prefix):
                        candidate = line[len(prefix):].strip()
                        if re.fullmatch(r"[A-Z][A-Za-z'\-.]+(?:\s+[A-Z][A-Za-z'\-.]+)+", candidate):
                            return candidate

            for index, line in enumerate(lines):
                lowered_line = line.lower()
                if any(keyword in lowered_line for keyword in ("best regards", "regards", "thanks", "thank you", "sincerely", "kind regards", "warm regards")) and index + 1 < len(lines):
                    candidate = lines[index + 1]
                    if re.fullmatch(r"[A-Z][A-Za-z'\-.]+(?:\s+[A-Z][A-Za-z'\-.]+)+", candidate):
                        return candidate

            if self.nlp is None:
                return ""

            doc = self.nlp(text)
            for ent in doc.ents:
                if ent.label_ == "PERSON":
                    candidate = ent.text.strip()
                    if len(candidate.split()) >= 2 and re.fullmatch(r"[A-Z][A-Za-z'\-.]+(?:\s+[A-Z][A-Za-z'\-.]+)+", candidate):
                        return candidate
            return ""
        except Exception as exc:
            LOGGER.exception("Contact person extraction failed: %s", exc)
            return ""

    def extract_organisation_name(self, text: str) -> str:
        """Extract an organization name using compact line-based signals."""
        if not text:
            return ""

        try:
            lines = self._iter_lines(text)
            for line in lines:
                lowered_line = line.lower()
                if " at " in lowered_line and any(keyword in lowered_line for keyword in ("manager", "director", "ceo", "cto", "cio", "administrator", "engineer", "sales", "business development")):
                    match = re.search(r"\bat\s+([A-Z][A-Za-z0-9&.'\-\s]+)", line)
                    if match:
                        return match.group(1).strip()
                if lowered_line.startswith("from "):
                    return line[5:].strip()
                if lowered_line.startswith("work at "):
                    return line[8:].strip()

            if self.nlp is None:
                return ""
            doc = self.nlp(text)
            org_candidates = [ent.text.strip() for ent in doc.ents if ent.label_ == "ORG"]
            for candidate in org_candidates:
                if candidate and len(candidate.split()) >= 2 and candidate.lower() not in {"business development", "network engineer"}:
                    return candidate
            return ""
        except Exception as exc:
            LOGGER.exception("Organisation extraction failed: %s", exc)
            return ""

    def extract_address(self, text: str) -> str:
        """Extract a probable address from address-like lines only."""
        if not text:
            return ""

        try:
            address_candidates: list[str] = []
            for line in self._iter_lines(text):
                lowered_line = line.lower()
                if any(keyword in lowered_line for keyword in ADDRESS_KEYWORDS) and re.search(r"\d", line):
                    if not any(keyword in lowered_line for keyword in ("phone", "mobile", "tel", "telephone", "contact", "email")):
                        address_candidates.append(line)

            if address_candidates:
                return self._clean_text(address_candidates[0])
            return ""
        except Exception as exc:
            LOGGER.exception("Address extraction failed: %s", exc)
            return ""

    def extract_designation(self, text: str) -> str:
        """Use keyword rules to detect a common business designation."""
        if not text:
            return ""

        try:
            lower_text = text.lower()
            for keyword, designation in DESIGNATION_KEYWORDS.items():
                if keyword in lower_text:
                    return designation
            return ""
        except Exception as exc:
            LOGGER.exception("Designation extraction failed: %s", exc)
            return ""

    def extract(self, email_text: str) -> dict[str, str]:
        """Run the full extraction pipeline and return the requested JSON schema."""
        try:
            cleaned_text = self.clean_html(email_text)
            email_list = self.extract_email_addresses(cleaned_text)
            mobile_numbers = self.extract_mobile_numbers(cleaned_text)
            contact_name = self.extract_contact_person_name(cleaned_text)
            organisation_name = self.extract_organisation_name(cleaned_text)
            address = self.extract_address(cleaned_text)
            designation = self.extract_designation(cleaned_text)

            result: dict[str, str] = {
                "contact_person_name": contact_name,
                "email_id": email_list[0] if email_list else "",
                "organisation_name": organisation_name,
                "mobile_number": mobile_numbers[0] if mobile_numbers else "",
                "address": address,
                "designation": designation,
            }
            LOGGER.info("Extraction complete for %d characters of email content.", len(cleaned_text))
            return result
        except Exception as exc:
            LOGGER.exception("Unexpected extraction failure: %s", exc)
            return {
                "contact_person_name": "",
                "email_id": "",
                "organisation_name": "",
                "mobile_number": "",
                "address": "",
                "designation": "",
            }


if __name__ == "__main__":
    engine = EmailExtractionEngine()
    sample_text = "Hello,\nMy name is Sarah Johnson, I am the IT Manager at Acme Solutions.\nContact: sarah@acmesolutions.com | +1 555 123 4567\n123 Test Street, Dallas, TX"
    print(engine.extract(sample_text))
