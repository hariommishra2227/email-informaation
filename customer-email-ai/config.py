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
    """Read a setting from environment variables first, then Streamlit secrets."""
    env_value = os.getenv(name)
    if env_value is not None:
        return env_value
    if st is None:
        return default
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def _microsoft_secret_value(key: str, env_name: str) -> str:
    """Read a Microsoft setting from Streamlit secrets, then environment variables."""
    if st is not None:
        try:
            microsoft_secrets = st.secrets.get("microsoft", {})
            value = microsoft_secrets.get(key, "")
            if value is not None and str(value).strip():
                return str(value).strip()
        except Exception:
            pass
    return os.getenv(env_name, "").strip()

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(_secret_value("DATABASE_PATH", str(BASE_DIR / "customer_data.db")))

OUTLOOK_MODE_MOCK = "mock"
OUTLOOK_MODE_LIVE = "live"

APP_USER_EMAIL = _secret_value("APP_USER_EMAIL", "Demo account").strip() or "Demo account"
DEFAULT_USER_ID = _secret_value("APP_USER_ID", "default_user").strip() or "default_user"

MICROSOFT_CLIENT_ID = _microsoft_secret_value("client_id", "MICROSOFT_CLIENT_ID")
MICROSOFT_CLIENT_SECRET = _microsoft_secret_value("client_secret", "MICROSOFT_CLIENT_SECRET")
MICROSOFT_TENANT_ID = _microsoft_secret_value("tenant_id", "MICROSOFT_TENANT_ID")
MICROSOFT_REDIRECT_URI = _microsoft_secret_value("redirect_uri", "MICROSOFT_REDIRECT_URI")

# Backward-compatible aliases for older code paths and tests.
AZURE_CLIENT_ID = MICROSOFT_CLIENT_ID
AZURE_CLIENT_SECRET = MICROSOFT_CLIENT_SECRET
AZURE_TENANT_ID = MICROSOFT_TENANT_ID
AZURE_REDIRECT_URI = MICROSOFT_REDIRECT_URI
AZURE_AUTHORITY = (
    f"https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}"
    if MICROSOFT_TENANT_ID
    else ""
)

GRAPH_SCOPES = ["User.Read", "Mail.Read", "offline_access", "openid", "profile", "email"]
OUTLOOK_MODE = OUTLOOK_MODE_LIVE if AZURE_CLIENT_ID and AZURE_CLIENT_SECRET and AZURE_TENANT_ID and AZURE_REDIRECT_URI else OUTLOOK_MODE_MOCK


def get_microsoft_client_id() -> str:
    """Return the configured Microsoft application client id."""
    return MICROSOFT_CLIENT_ID


def get_microsoft_client_secret() -> str:
    """Return the configured Microsoft application client secret."""
    return MICROSOFT_CLIENT_SECRET


def get_microsoft_tenant_id() -> str:
    """Return the configured Microsoft tenant id."""
    return MICROSOFT_TENANT_ID


def get_microsoft_redirect_uri() -> str:
    """Return the configured Microsoft OAuth redirect URI."""
    return MICROSOFT_REDIRECT_URI


def is_microsoft_configured() -> bool:
    """Return True only when every required Microsoft OAuth setting exists."""
    return all(
        (
            get_microsoft_client_id(),
            get_microsoft_client_secret(),
            get_microsoft_tenant_id(),
            get_microsoft_redirect_uri(),
        )
    )


def is_mock_mode() -> bool:
    """Return whether the app is running without Microsoft credentials."""
    return not is_microsoft_configured()


def missing_live_settings() -> list[str]:
    """Return required live-mode setting names that are not configured."""
    required = {
        "MICROSOFT_CLIENT_ID": get_microsoft_client_id(),
        "MICROSOFT_CLIENT_SECRET": get_microsoft_client_secret(),
        "MICROSOFT_TENANT_ID": get_microsoft_tenant_id(),
        "MICROSOFT_REDIRECT_URI": get_microsoft_redirect_uri(),
    }
    return [name for name, value in required.items() if not value]
