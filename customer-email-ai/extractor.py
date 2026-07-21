"""Reusable extraction engine for customer contact details from business emails.

The module uses spaCy, regular expressions, phonenumbers, and BeautifulSoup
for a Phase 1 rule-based contact extraction pipeline.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any

import config

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
INTERNAL_DOMAIN = "itsipl.com"
BOILERPLATE_PHRASES = (
    "privacy statement", "unsubscribe", "view online", "click here",
    "warning: this message", "caution! this message",
    "this message was sent from outside your organization", "external email",
    "manage preferences", "copyright",
)
PHONE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:\+?\d{1,3}[\s.-]?)?"
    r"(?:\(?\d{2,5}\)?[\s.-]?){1,4}"
    r"\d{2,5}"
    r"(?![A-Za-z0-9])"
)
PERSON_NAME_PATTERN = re.compile(r"^[A-Z][A-Za-z'\-.]*(?:\s+[A-Z][A-Za-z'\-.]+)+$")
COMPANY_HINT_PATTERN = re.compile(
    r"\b(?:company|companies|organization|organisation|solutions|technologies|systems|software|services|industries|ventures|logistics|telecom|retail|group|ltd|limited|pvt|private|inc|llc|corp|corporation)\b",
    re.IGNORECASE,
)
COMPANY_HINT_KEYWORDS = (
    "ltd",
    "pvt",
    "private",
    "limited",
    "technologies",
    "solutions",
    "systems",
    "software",
    "services",
    "industries",
    "ventures",
    "logistics",
    "telecom",
    "retail",
    "company",
    "organization",
    "organisation",
)
PHONE_LABEL_KEYWORDS = (
    "mobile",
    "phone",
    "contact",
    "tel",
    "telephone",
    "cell",
    "whatsapp",
)
PHONE_DISPLAY_CLEANUP_PATTERN = re.compile(r"\s+")
NORMALIZED_PHONE_PATTERN = re.compile(r"\D")
NON_PHONE_KEYWORDS = (
    "invoice",
    "order",
    "gst",
    "pin",
    "pincode",
    "postal",
    "zip",
    "date",
    "due",
    "amount",
    "bill",
    "quotation",
    "quote",
)
ADDRESS_KEYWORDS = (
    "sector", "phase", "industrial area", "nagar", "colony", "building",
    "floor", "block", "plot", "district", "state", "india",
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
    "it manager": "IT Manager",
    "purchase manager": "Purchase Manager",
    "project manager": "Project Manager",
    "system administrator": "System Administrator",
    "network engineer": "Network Engineer",
    "sales manager": "Sales Manager",
    "business development manager": "Business Development Manager",
    "administrator": "Administrator",
    "consultant": "Consultant",
    "executive": "Executive",
    "engineer": "Engineer",
    "analyst": "Analyst",
    "director": "Director",
    "manager": "Manager",
    "ceo": "CEO",
    "cto": "CTO",
    "cio": "CIO",
}
GENERIC_MAILBOXES = {
    "info", "sales", "support", "contact", "admin", "office", "help",
    "enquiry", "marketing", "hr", "accounts", "noreply", "no-reply",
}
QUOTE_MARKERS = ("---------- forwarded message", "begin forwarded message", "original message", "-----original message-----")


def _valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", str(value or "").strip()))


def _automated_email(value: str) -> bool:
    local = str(value or "").split("@", 1)[0].lower()
    return local in {"no-reply", "noreply", "donotreply", "mailer-daemon"} or local.startswith(("no-reply", "noreply", "donotreply"))


def select_customer_email(
    graph_sender_email: str = "",
    forwarded_sender_email: str = "",
    signature_email: str = "",
    body_emails: list[str] | None = None,
) -> dict[str, Any]:
    """Select one external customer email while preserving source priority."""
    candidates = (
        (graph_sender_email, "graph_sender", 1.0),
        (forwarded_sender_email, "forwarded_sender", 0.95),
        (signature_email, "signature", 0.90),
    )
    for value, source, confidence in candidates:
        value = str(value or "").strip()
        if _valid_email(value) and not config.is_internal_email(value) and not _automated_email(value):
            return {"email": value, "email_source": source, "email_confidence": confidence}
    for value in dict.fromkeys(body_emails or []):
        value = str(value).strip()
        if _valid_email(value) and not config.is_internal_email(value) and not _automated_email(value):
            return {"email": value, "email_source": "body", "email_confidence": 0.40}
    return {"email": "", "email_source": "", "email_confidence": 0.0}


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

    def _current_text(self, text: str) -> str:
        """Return readable current-message text, stopping at quoted history."""
        cleaned = self.clean_html(text).replace("\r\n", "\n")
        lines = cleaned.splitlines()
        current: list[str] = []
        for line in lines:
            lowered = line.strip().lower()
            if any(marker in lowered for marker in QUOTE_MARKERS) or re.match(r"^>+", line.strip()):
                break
            if re.match(r"^(from|sent|to|cc|subject):\s", lowered) and current and lowered.startswith("from:"):
                break
            current.append(line)
        return "\n".join(current)

    def _split_blocks(self, text: str) -> list[str]:
        """Split current content without allowing fields to cross blank lines."""
        blocks: list[list[str]] = []
        block: list[str] = []
        for raw in text.splitlines():
            line = self._clean_text(raw)
            if not line:
                if block:
                    blocks.append(block); block = []
                continue
            block.append(line)
            if re.search(r"(?:best|kind|warm) regards|sincerely|thank you", line, re.I):
                # The marker belongs to the following signature lines; retain it in this block.
                continue
        if block:
            blocks.append(block)
        merged: list[str] = []
        for block in blocks:
            if merged and not self.extract_email_addresses(merged[-1]) and (
                self.extract_designation(merged[-1]) or self._looks_like_company(merged[-1])
            ) and self.extract_email_addresses("\n".join(block)):
                merged[-1] += "\n" + "\n".join(block)
            else:
                merged.append("\n".join(block))
        return merged

    def _name_email_similarity(self, name: str, email: str) -> float:
        local = email.split("@", 1)[0].lower()
        tokens = [part for part in re.split(r"[^a-z]+", local) if part]
        name_tokens = [part.lower() for part in re.findall(r"[A-Za-z]+", name)]
        if not tokens or not name_tokens:
            return 0.0
        if local in {"".join(name_tokens), ".".join(name_tokens), "_".join(name_tokens)}:
            return 1.0
        matches = sum(token == candidate or token.startswith(candidate[:1]) for token in tokens for candidate in name_tokens)
        return min(1.0, matches / max(len(name_tokens), 2))

    def _pair_block(self, block: str) -> dict[str, Any] | None:
        lines = self._iter_lines(block)
        emails = self.extract_email_addresses(block)
        if not emails:
            return None
        email = emails[0]
        generic = email.split("@", 1)[0].lower() in GENERIC_MAILBOXES
        names = [(i, line) for i, line in enumerate(lines) if self._looks_like_person_name(line) and not self.extract_designation(line)]
        for i, line in enumerate(lines):
            match = re.search(r"(?:contact|name|regards|thanks)\s*:\s*([A-Z][A-Za-z'\-.]+(?:\s+[A-Z][A-Za-z'\-.]+)+)", line, re.I)
            if match and self._looks_like_person_name(match.group(1)):
                names.append((i, match.group(1)))
        email_line = next((i for i, line in enumerate(lines) if EMAIL_PATTERN.search(line)), 0)
        ranked = []
        for index, name in names:
            distance = abs(email_line - index)
            similarity = self._name_email_similarity(name, email)
            score = 0.25 + max(0.0, 0.25 - min(distance, 5) * 0.04) + similarity * 0.35
            if generic:
                score = 0.45 + max(0.0, 0.25 - min(distance, 5) * 0.04)
            ranked.append((score, -distance, name))
        name = ""
        score = 0.35 if generic else 0.30
        if ranked:
            best = max(ranked)
            if (generic and best[0] >= 0.55) or (not generic and best[0] >= 0.55):
                score, _, name = best
        return {"block": block, "email": email, "name": name, "confidence": round(min(score, 1.0), 2)}

    def extract_contacts(self, email_text: str) -> list[dict[str, Any]]:
        """Extract independently paired contact candidates from local blocks."""
        current = self._current_text(email_text)
        return [candidate for block in self._split_blocks(current) if (candidate := self._pair_block(block))]

    def _is_boilerplate(self, line: str) -> bool:
        lowered = line.lower()
        return any(phrase in lowered for phrase in BOILERPLATE_PHRASES)

    def _looks_like_company(self, candidate: str) -> bool:
        """Return whether a candidate line looks like a company or organisation."""
        if not candidate:
            return False
        lowered = candidate.lower()
        if any(keyword in lowered for keyword in COMPANY_HINT_KEYWORDS):
            return True
        return bool(COMPANY_HINT_PATTERN.search(candidate))

    def extract_email_addresses(self, text: str) -> list[str]:
        """Extract unique email addresses from the supplied text."""
        if not text:
            return []

        try:
            return list(dict.fromkeys(
                email for email in EMAIL_PATTERN.findall(text)
                if not email.lower().endswith("@" + INTERNAL_DOMAIN)
            ))
        except Exception as exc:
            LOGGER.exception("Email extraction failed: %s", exc)
            return []

    def _normalize_phone_number(self, raw_number: str) -> str:
        """Validate and normalize the candidate phone number for duplicate checks."""
        if not raw_number:
            return ""

        cleaned_number = re.sub(r"[^\d+]", "", raw_number)
        if cleaned_number.startswith("00"):
            cleaned_number = f"+{cleaned_number[2:]}"

        digit_count = len(re.sub(r"\D", "", cleaned_number))
        if digit_count < 10 or digit_count > 15:
            return ""

        if phonenumbers is None:
            digits = re.sub(r"\D", "", cleaned_number)
            if cleaned_number.startswith("+"):
                return f"+{digits}"
            if digits.startswith("0") and len(digits) == 11:
                digits = digits[1:]
            if len(digits) == 10:
                return f"+91{digits}"
            return f"+{digits}"

        parse_regions: tuple[str | None, ...] = (None,) if cleaned_number.startswith("+") else ("IN", None)
        for region in parse_regions:
            try:
                parsed_number = phonenumbers.parse(cleaned_number, region)
                if not phonenumbers.is_valid_number(parsed_number):
                    continue
                return phonenumbers.format_number(parsed_number, phonenumbers.PhoneNumberFormat.E164)
            except phonenumbers.NumberParseException:
                continue

        return ""

    def _format_phone_number_for_display(self, raw_number: str) -> str:
        """Return a readable phone number while preserving country-code notation."""
        cleaned = raw_number.strip(" .,:;|")
        cleaned = PHONE_DISPLAY_CLEANUP_PATTERN.sub(" ", cleaned)
        cleaned = re.sub(r"\s*-\s*", "-", cleaned)
        return cleaned

    def _normalize_phone_for_duplicate_check(self, phone_number: str) -> str:
        """Return the phone key used by duplicate detection."""
        digits = NORMALIZED_PHONE_PATTERN.sub("", phone_number)
        if digits.startswith("91") and len(digits) > 10:
            digits = digits[2:]
        if digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
        if len(digits) >= 10:
            return digits[-10:]
        return digits

    def _line_has_phone_label(self, line: str) -> bool:
        """Return whether a line contains a customer contact label."""
        lowered_line = line.lower()
        return any(keyword in lowered_line for keyword in PHONE_LABEL_KEYWORDS)

    def _line_has_non_phone_signal(self, line: str) -> bool:
        """Return whether a line likely contains an ID, date, PIN, or invoice value."""
        lowered_line = line.lower()
        return any(keyword in lowered_line for keyword in NON_PHONE_KEYWORDS)

    def _collect_phone_candidates(self, lines: list[str], prefer_labelled: bool) -> list[str]:
        """Collect regex phone candidates from labelled or general text lines."""
        candidates: list[str] = []

        for line in lines:
            has_label = self._line_has_phone_label(line)
            if prefer_labelled and not has_label:
                continue
            if not prefer_labelled and (has_label or self._line_has_non_phone_signal(line)):
                continue

            searchable_line = line
            if has_label and ":" in searchable_line:
                searchable_line = searchable_line.split(":", 1)[1]

            candidates.extend(match.group().strip(" .,:;|") for match in PHONE_PATTERN.finditer(searchable_line))

        return candidates

    def extract_mobile_numbers(self, text: str) -> list[str]:
        """Extract display-ready phone numbers from phone-related lines."""
        if not text:
            return []

        try:
            lines = [line for line in self._iter_lines(text) if not self._is_boilerplate(line)]
            phones = self._collect_phone_candidates(lines, prefer_labelled=True)
            if not phones:
                phones = self._collect_phone_candidates(lines, prefer_labelled=False)

            display_numbers: list[str] = []
            for candidate in phones:
                normalized = self._normalize_phone_number(candidate)
                if normalized:
                    display_numbers.append(self._format_phone_number_for_display(candidate))

            return list(dict.fromkeys(display_numbers))
        except Exception as exc:
            LOGGER.exception("Mobile number extraction failed: %s", exc)
            return []

    def _looks_like_person_name(self, candidate: str) -> bool:
        """Return whether a candidate line resembles a person name."""
        if not candidate:
            return False
        if len(candidate.split()) < 2:
            return False
        lowered = candidate.lower()
        if self._looks_like_company(candidate):
            return False
        if any(keyword in lowered for keyword in ("@", "http", "mobile", "phone", "tel", "contact", "address", "location", "subject", "customer", "sample", "email")):
            return False
        return bool(PERSON_NAME_PATTERN.fullmatch(candidate.strip()))

    def extract_contact_person_name(self, text: str) -> str:
        """Extract a likely contact person's name with tolerant heuristics."""
        if not text:
            return ""

        try:
            lines = self._iter_lines(text)
            email_indices = [index for index, line in enumerate(lines) if EMAIL_PATTERN.search(line)]
            designation_indices = [
                index
                for index, line in enumerate(lines)
                if any(keyword in line.lower() for keyword in DESIGNATION_KEYWORDS)
            ]

            name_candidates: list[str] = []
            for index, line in enumerate(lines):
                lowered_line = line.lower()
                if lowered_line.startswith("subject:"):
                    continue
                if any(keyword in lowered_line for keyword in ("best regards", "regards", "thanks", "thank you", "sincerely", "kind regards", "warm regards")):
                    continue
                if self._looks_like_person_name(line):
                    name_candidates.append((index, line))

            for index, line in name_candidates:
                if designation_indices and index > max(designation_indices):
                    continue
                if index in email_indices:
                    continue
                if any(index == other_index - 1 for other_index in email_indices):
                    continue
                if any(address_keyword in line.lower() for address_keyword in ("address", "location", "office", "city", "state", "country")):
                    continue
                if any(keyword in line.lower() for keyword in ("customer", "sample", "email", "subject")):
                    continue

                if designation_indices and any(index < designation_index for designation_index in designation_indices):
                    return line

            for line in lines:
                lowered_line = line.lower()
                if lowered_line.startswith("subject:"):
                    continue
                if any(keyword in lowered_line for keyword in ("sample", "customer", "email")):
                    continue
                if self._looks_like_person_name(line):
                    return line

            for line in lines:
                lowered_line = line.lower()
                if lowered_line.startswith("subject:"):
                    continue
                if any(keyword in lowered_line for keyword in ("customer", "sample", "email")):
                    continue
                if self._looks_like_company(line):
                    continue
                if "@" in lowered_line:
                    continue
                if any(keyword in lowered_line for keyword in ("mobile", "phone", "tel", "address", "location")):
                    continue
                if self._looks_like_person_name(line):
                    return line

            if self.nlp is None:
                return ""

            doc = self.nlp(text)
            for ent in doc.ents:
                if ent.label_ == "PERSON":
                    candidate = ent.text.strip()
                    if self._looks_like_person_name(candidate):
                        return candidate
            return ""
        except Exception as exc:
            LOGGER.exception("Contact person extraction failed: %s", exc)
            return ""

    def extract_subject(self, text: str) -> str:
        """Extract a subject line from a business email if present."""
        if not text:
            return ""

        try:
            match = re.search(r"(?im)^\s*subject\s*:\s*(.+)$", text)
            if match:
                return self._clean_text(match.group(1))
            return ""
        except Exception as exc:
            LOGGER.exception("Subject extraction failed: %s", exc)
            return ""

    def extract_organisation_name(self, text: str) -> str:
        """Extract an organization name using compact line-based signals."""
        if not text:
            return ""

        try:
            lines = self._iter_lines(text)
            email_indices = [index for index, line in enumerate(lines) if EMAIL_PATTERN.search(line)]

            for index, line in enumerate(lines):
                lowered_line = line.lower()
                if index in email_indices and index > 0:
                    candidate = lines[index - 1]
                    if self._looks_like_company(candidate):
                        return candidate
                if EMAIL_PATTERN.search(line):
                    continue
                if " at " in lowered_line and any(keyword in lowered_line for keyword in ("manager", "director", "ceo", "cto", "cio", "administrator", "engineer", "sales", "business development")):
                    match = re.search(r"\bat\s+([A-Z][A-Za-z0-9&.'\-\s]+)", line)
                    if match:
                        return match.group(1).strip()
                if lowered_line.startswith("from "):
                    return line[5:].strip()
                if lowered_line.startswith("work at "):
                    return line[8:].strip()
                if lowered_line.startswith("company:"):
                    return line.split(":", 1)[1].strip()
                if lowered_line.startswith("organization:"):
                    return line.split(":", 1)[1].strip()
                if lowered_line.startswith("organisation:"):
                    return line.split(":", 1)[1].strip()
                if lowered_line.startswith("company ") or lowered_line.startswith("organization "):
                    return line.split(maxsplit=1)[1].strip()
                if self._looks_like_company(line) and len(line.split()) >= 2 and not self._looks_like_person_name(line):
                    return line

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
        """Extract a probable address or location from business email text."""
        if not text:
            return ""

        try:
            lines = [line for line in self._iter_lines(text) if not self._is_boilerplate(line)]
            for line in lines:
                lowered_line = line.lower()
                if self._is_boilerplate(line) or lowered_line.startswith(("address:", "location:", "office:")):
                    continue
                if "itsipl" in lowered_line or "i. t. solutions india" in lowered_line:
                    continue
                candidate = line.split(":", 1)[1].strip() if ":" in line else line
                if lowered_line.startswith(("address:", "location:", "office:")):
                    if self._valid_postal_address(candidate):
                        return candidate

            address_candidates: list[str] = []
            for line in lines:
                lowered_line = line.lower()
                if any(keyword in lowered_line for keyword in ("address", "location", "office", "city", "state", "country")):
                    if not any(keyword in lowered_line for keyword in ("phone", "mobile", "tel", "telephone", "contact", "email")):
                        address_candidates.append(line)
                        continue

                if any(keyword in lowered_line for keyword in ADDRESS_KEYWORDS) and re.search(r"\d", line):
                    if not any(keyword in lowered_line for keyword in ("phone", "mobile", "tel", "telephone", "contact", "email")):
                        address_candidates.append(line)
                        continue

                if re.search(r"\b(?:sector|street|road|avenue|lane|drive|way|boulevard|court|place|park|colony|area)\b", lowered_line) and re.search(r"\d", line):
                    address_candidates.append(line)

            for candidate in address_candidates:
                address_value = candidate.split(":", 1)[1].strip() if ":" in candidate else candidate
                if self._valid_postal_address(address_value):
                    return self._clean_text(address_value)
            return ""
        except Exception as exc:
            LOGGER.exception("Address extraction failed: %s", exc)
            return ""

    def _valid_postal_address(self, value: str) -> bool:
        """Accept only address-like text, never labels or disclaimer sentences."""
        if not value or self._is_boilerplate(value):
            return False
        lowered = value.lower()
        if "itsipl" in lowered or "i. t. solutions india" in lowered:
            return False
        if lowered.strip() in {"location", "privacy statement"}:
            return False
        indicators = sum(bool(re.search(rf"\b{re.escape(keyword)}\b", lowered)) for keyword in ADDRESS_KEYWORDS)
        comma_parts = len([part for part in value.split(",") if part.strip()])
        has_pin_or_number = bool(re.search(r"\b\d{6}\b", value) or re.search(r"\d", value))
        # A street/sector/etc. token plus separate locality components (for
        # example ``Sector 62, Noida, Uttar Pradesh``) is the common compact
        # address form used in signatures.
        return indicators >= 1 and has_pin_or_number and comma_parts >= 2

    def extract_designation(self, text: str) -> str:
        """Use keyword rules to detect a common business designation."""
        if not text:
            return ""

        try:
            lower_text = text.lower()
            for keyword, designation in sorted(DESIGNATION_KEYWORDS.items(), key=lambda item: len(item[0]), reverse=True):
                if keyword in lower_text:
                    return designation
            return ""
        except Exception as exc:
            LOGGER.exception("Designation extraction failed: %s", exc)
            return ""

    def extract(self, email_text: str, *, graph_sender_email: str = "", graph_sender_name: str = "") -> dict[str, Any]:
        """Run the full extraction pipeline and return the requested JSON schema."""
        try:
            cleaned_text = "\n".join(line for line in self._current_text(email_text).splitlines() if not self._is_boilerplate(line))
            contacts = self.extract_contacts(cleaned_text)
            email_list = [contact["email"] for contact in reversed(contacts)]
            selected_email = select_customer_email(graph_sender_email=graph_sender_email, body_emails=email_list)
            selected_contact = next((contact for contact in contacts if contact["email"].lower() == selected_email["email"].lower()), {})
            block_text = selected_contact.get("block", "")
            analysis_text = block_text or cleaned_text
            mobile_numbers = self.extract_mobile_numbers(analysis_text)
            contact_name = selected_contact.get("name", "")
            if graph_sender_name.strip() and graph_sender_email and graph_sender_email.lower() == selected_email["email"].lower():
                if not contact_name or self._name_email_similarity(graph_sender_name, selected_email["email"]) >= 0.5:
                    contact_name = graph_sender_name.strip()
            organisation_name = self.extract_organisation_name(analysis_text)
            address = self.extract_address(analysis_text)
            designation = self.extract_designation(analysis_text)
            subject = self.extract_subject(cleaned_text)

            result: dict[str, str] = {
                "contact_person_name": contact_name,
                "customer_name": contact_name,
                "name": contact_name,
                "email_id": selected_email["email"],
                "email": selected_email["email"],
                "email_source": selected_email["email_source"],
                "email_confidence": selected_email["email_confidence"],
                "pairing_confidence": selected_contact.get("confidence", 0.0),
                "contacts": contacts,
                "organisation_name": organisation_name,
                "company": organisation_name,
                "mobile_number": mobile_numbers[0] if mobile_numbers else "",
                "phone": mobile_numbers[0] if mobile_numbers else "",
                "normalized_phone": self._normalize_phone_for_duplicate_check(mobile_numbers[0]) if mobile_numbers else "",
                "address": address,
                "designation": designation,
                "subject": subject,
                "name_source": "graph_sender" if graph_sender_name.strip() else ("body" if contact_name else ""),
                "name_confidence": 0.95 if graph_sender_name.strip() else (0.45 if contact_name else 0.0),
            }
            LOGGER.info("Extraction complete for %d characters of email content.", len(cleaned_text))
            return result
        except Exception as exc:
            LOGGER.exception("Unexpected extraction failure: %s", exc)
            return {
                "contact_person_name": "",
                "customer_name": "",
                "name": "",
                "email_id": "",
                "email": "",
                "email_source": "",
                "email_confidence": 0.0,
                "organisation_name": "",
                "company": "",
                "mobile_number": "",
                "phone": "",
                "normalized_phone": "",
                "address": "",
                "designation": "",
                "subject": "",
                "name_source": "",
                "name_confidence": 0.0,
            }


if __name__ == "__main__":
    engine = EmailExtractionEngine()
    sample_text = "Hello,\nMy name is Sarah Johnson, I am the IT Manager at Acme Solutions.\nContact: sarah@acmesolutions.com | +1 555 123 4567\n123 Test Street, Dallas, TX"
    print(engine.extract(sample_text))
