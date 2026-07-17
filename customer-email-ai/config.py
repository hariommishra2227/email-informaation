"""Application configuration for demo mode and Microsoft Outlook live mode."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:
        """Fallback when python-dotenv has not been installed yet."""
        return None


load_dotenv()

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

LEGACY_AZURE_CLIENT_ID = _secret_value("AZURE_CLIENT_ID", "")
LEGACY_AZURE_CLIENT_SECRET = _secret_value("AZURE_CLIENT_SECRET", "")
LEGACY_AZURE_REDIRECT_URI = _secret_value("AZURE_REDIRECT_URI", "")
LEGACY_AZURE_AUTHORITY = _secret_value("AZURE_AUTHORITY", "")

RESOLVED_CLIENT_ID = MICROSOFT_CLIENT_ID or LEGACY_AZURE_CLIENT_ID
RESOLVED_CLIENT_SECRET = MICROSOFT_CLIENT_SECRET or LEGACY_AZURE_CLIENT_SECRET
RESOLVED_TENANT_ID = MICROSOFT_TENANT_ID
RESOLVED_REDIRECT_URI = MICROSOFT_REDIRECT_URI or LEGACY_AZURE_REDIRECT_URI
RESOLVED_AUTHORITY = (
    f"https://login.microsoftonline.com/{RESOLVED_TENANT_ID}"
    if RESOLVED_TENANT_ID
    else LEGACY_AZURE_AUTHORITY
)

# Canonical resolved Microsoft Outlook configuration used by the app.
CLIENT_ID = RESOLVED_CLIENT_ID
CLIENT_SECRET = RESOLVED_CLIENT_SECRET
TENANT_ID = RESOLVED_TENANT_ID
REDIRECT_URI = RESOLVED_REDIRECT_URI
AUTHORITY = RESOLVED_AUTHORITY

# Backward-compatible aliases for older code paths and tests.
AZURE_CLIENT_ID = CLIENT_ID
AZURE_CLIENT_SECRET = CLIENT_SECRET
AZURE_TENANT_ID = TENANT_ID
AZURE_REDIRECT_URI = REDIRECT_URI
AZURE_AUTHORITY = AUTHORITY

GRAPH_SCOPES = ["User.Read", "Mail.Read", "offline_access", "openid", "profile", "email"]
OUTLOOK_MODE = OUTLOOK_MODE_LIVE if CONFIGURED_OUTLOOK_MODE == OUTLOOK_MODE_LIVE else OUTLOOK_MODE_MOCK


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
        "MICROSOFT_CLIENT_ID": CLIENT_ID,
        "MICROSOFT_CLIENT_SECRET": CLIENT_SECRET,
        "MICROSOFT_TENANT_ID": has_authority_configuration(),
        "MICROSOFT_REDIRECT_URI": REDIRECT_URI,
    }
    return [name for name, value in required.items() if not value]
