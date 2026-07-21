"""Application configuration for demo mode and Microsoft Outlook live mode."""

from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:
        """Fallback when python-dotenv has not been installed yet."""
        return None


load_dotenv()

# Customer extraction must treat these as internal data, regardless of case or punctuation.
INTERNAL_DOMAINS = {"itsipl.com"}
INTERNAL_COMPANY_ALIASES = {
    "ITSIPL",
    "I.T. Solutions India Pvt. Ltd.",
    "I T Solutions India Pvt Ltd",
    "IT Solutions India Pvt Ltd",
    "I.T. Solutions India Private Limited",
    "IT Solutions India Private Limited",
    "I. T. Solutions India Pvt. Limited",
    "I.T Solutions India",
    "IT Solutions India",
}
INTERNAL_EMAILS: set[str] = set()
INTERNAL_PHONE_NUMBERS: set[str] = set()
INTERNAL_ADDRESS_MARKERS = {"itsipl", "i.t. solutions india", "it solutions india"}


def normalize_email(value: str) -> str:
    """Normalize an email address for validation and comparisons."""
    return str(value or "").strip().lower()


def get_email_domain(value: str) -> str:
    """Return the lower-case domain portion of an email address."""
    email = normalize_email(value)
    return email.rsplit("@", 1)[1] if "@" in email else ""


def get_graph_sender(message: dict) -> tuple[str, str]:
    """Read the authoritative sender name/address from a Graph message payload."""
    sender = ((message or {}).get("from") or {}).get("emailAddress") or {}
    return str(sender.get("name") or "").strip(), normalize_email(str(sender.get("address") or ""))


def is_internal_email(value: str) -> bool:
    """Return true only for an exact internal domain or configured address."""
    email = normalize_email(value)
    return bool(email and (email in {normalize_email(item) for item in INTERNAL_EMAILS} or get_email_domain(email) in INTERNAL_DOMAINS))


def normalize_company_text(value: str) -> str:
    """Normalize company punctuation and common Pvt/Ltd variants."""
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    normalized = re.sub(r"\b(private|pvt)\b", "private", normalized)
    normalized = re.sub(r"\b(limited|ltd)\b", "limited", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


_NORMALIZED_INTERNAL_COMPANIES = {normalize_company_text(value) for value in INTERNAL_COMPANY_ALIASES}


def is_internal_company(value: str) -> bool:
    """Return true when a company value matches a configured internal alias."""
    normalized = normalize_company_text(value)
    return bool(normalized and (normalized in _NORMALIZED_INTERNAL_COMPANIES or "itsipl" in normalized))


def is_internal_sender(sender_email: str) -> bool:
    """Alias for the message-level sender decision."""
    return is_internal_email(sender_email)

try:
    import streamlit as st
except ImportError:  # pragma: no cover
    st = None


def _secret_value(name: str, default: str = "") -> str:
    """Read a setting from Streamlit secrets first, then environment variables."""
    if st is not None:
        try:
            value = st.secrets.get(name, "")
            if value is not None and str(value).strip():
                return str(value).strip()
        except Exception:
            pass
    env_value = os.getenv(name)
    if env_value is not None and env_value.strip():
        return env_value.strip()
    return default


def _nested_microsoft_secret_value(key: str) -> str:
    """Read legacy nested Microsoft secrets for backward compatibility."""
    if st is not None:
        try:
            microsoft_secrets = st.secrets.get("microsoft", {})
            value = microsoft_secrets.get(key, "")
            if value is not None and str(value).strip():
                return str(value).strip()
        except Exception:
            pass
    return ""


def _first_value(*names: str, default: str = "") -> str:
    """Return the first configured top-level secret or environment value."""
    for name in names:
        value = _secret_value(name, "")
        if value:
            return value
    return default

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(_secret_value("DATABASE_PATH", str(BASE_DIR / "customer_data.db")))

OUTLOOK_MODE_MOCK = "mock"
OUTLOOK_MODE_LIVE = "live"

APP_USER_EMAIL = _secret_value("APP_USER_EMAIL", "Demo account").strip() or "Demo account"
DEFAULT_USER_ID = _secret_value("APP_USER_ID", "default_user").strip() or "default_user"

CONFIGURED_OUTLOOK_MODE = _secret_value("OUTLOOK_MODE", OUTLOOK_MODE_MOCK).strip().lower() or OUTLOOK_MODE_MOCK

MICROSOFT_CLIENT_ID = _first_value("MICROSOFT_CLIENT_ID", default=_nested_microsoft_secret_value("client_id"))
MICROSOFT_CLIENT_SECRET = _first_value(
    "MICROSOFT_CLIENT_SECRET",
    default=_nested_microsoft_secret_value("client_secret"),
)
MICROSOFT_TENANT_ID = _first_value("MICROSOFT_TENANT_ID", default=_nested_microsoft_secret_value("tenant_id"))
MICROSOFT_REDIRECT_URI = _first_value(
    "MICROSOFT_REDIRECT_URI",
    default=_nested_microsoft_secret_value("redirect_uri"),
)

AZURE_CLIENT_ID = _secret_value("AZURE_CLIENT_ID", "").strip()
AZURE_CLIENT_SECRET = _secret_value("AZURE_CLIENT_SECRET", "").strip()
AZURE_REDIRECT_URI = _secret_value(
    "AZURE_REDIRECT_URI",
    "https://email-informaation-frmrxrcergpwxbvh5lcqux.streamlit.app/Outlook_Connector",
).strip()
AZURE_AUTHORITY = _secret_value(
    "AZURE_AUTHORITY",
    "https://login.microsoftonline.com/common",
).strip()

RESOLVED_CLIENT_ID = AZURE_CLIENT_ID or MICROSOFT_CLIENT_ID
RESOLVED_CLIENT_SECRET = AZURE_CLIENT_SECRET or MICROSOFT_CLIENT_SECRET
RESOLVED_TENANT_ID = MICROSOFT_TENANT_ID
RESOLVED_REDIRECT_URI = AZURE_REDIRECT_URI or MICROSOFT_REDIRECT_URI
RESOLVED_AUTHORITY = (
    f"https://login.microsoftonline.com/{RESOLVED_TENANT_ID}"
    if RESOLVED_TENANT_ID
    else AZURE_AUTHORITY
)

# Canonical resolved Microsoft Outlook configuration used by the app.
CLIENT_ID = RESOLVED_CLIENT_ID
CLIENT_SECRET = RESOLVED_CLIENT_SECRET
TENANT_ID = RESOLVED_TENANT_ID
REDIRECT_URI = RESOLVED_REDIRECT_URI
AUTHORITY = RESOLVED_AUTHORITY

# Backward-compatible aliases for older code paths and tests.
AZURE_TENANT_ID = TENANT_ID

GRAPH_SCOPES = [
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.Read",
]
OUTLOOK_MODE = OUTLOOK_MODE_LIVE if CONFIGURED_OUTLOOK_MODE == OUTLOOK_MODE_LIVE else OUTLOOK_MODE_MOCK
APP_PAGE_ICON = "📧"
OUTLOOK_SIGN_IN_LABEL = "Sign in with Outlook"
OUTLOOK_SIGN_IN_ICON = "🔐"


def get_microsoft_client_id() -> str:
    """Return the resolved Microsoft application client id."""
    return CLIENT_ID


def get_microsoft_client_secret() -> str:
    """Return the resolved Microsoft application client secret."""
    return CLIENT_SECRET


def get_microsoft_tenant_id() -> str:
    """Return the configured Microsoft tenant id."""
    return TENANT_ID


def get_microsoft_redirect_uri() -> str:
    """Return the resolved Microsoft OAuth redirect URI."""
    return REDIRECT_URI


def get_microsoft_authority() -> str:
    """Return the resolved Microsoft OAuth authority."""
    return AUTHORITY


def has_authority_configuration() -> bool:
    """Return whether tenant id or legacy authority is configured."""
    authority = get_microsoft_authority().lower()
    return bool(
        get_microsoft_tenant_id()
        or authority.startswith("https://login.microsoftonline.com/")
    )


def is_microsoft_configured() -> bool:
    """Return True only when every required Microsoft OAuth setting exists."""
    return all(
        (
            OUTLOOK_MODE == OUTLOOK_MODE_LIVE,
            get_microsoft_client_id(),
            get_microsoft_client_secret(),
            has_authority_configuration(),
            get_microsoft_redirect_uri(),
        )
    )


def is_mock_mode() -> bool:
    """Return whether the app is running without Microsoft credentials."""
    return not is_microsoft_configured()


def missing_live_settings() -> list[str]:
    """Return required live-mode setting names that are not configured."""
    required = {
        "OUTLOOK_MODE=live": OUTLOOK_MODE == OUTLOOK_MODE_LIVE,
        "AZURE_CLIENT_ID": CLIENT_ID,
        "AZURE_CLIENT_SECRET": CLIENT_SECRET,
        "AZURE_AUTHORITY": has_authority_configuration(),
        "AZURE_REDIRECT_URI": REDIRECT_URI,
    }
    return [name for name, value in required.items() if not value]
