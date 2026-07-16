"""Application configuration for local mock mode and Outlook live mode."""

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

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(_secret_value("DATABASE_PATH", str(BASE_DIR / "customer_data.db")))

OUTLOOK_MODE_MOCK = "mock"
OUTLOOK_MODE_LIVE = "live"
OUTLOOK_MODE = _secret_value("OUTLOOK_MODE", OUTLOOK_MODE_MOCK).strip().lower() or OUTLOOK_MODE_MOCK
if OUTLOOK_MODE not in {OUTLOOK_MODE_MOCK, OUTLOOK_MODE_LIVE}:
    OUTLOOK_MODE = OUTLOOK_MODE_MOCK

APP_USER_EMAIL = _secret_value("APP_USER_EMAIL", "boss@company.com").strip() or "boss@company.com"
DEFAULT_USER_ID = _secret_value("APP_USER_ID", "default_user").strip() or "default_user"
AZURE_CLIENT_ID = _secret_value("AZURE_CLIENT_ID", "").strip()
AZURE_CLIENT_SECRET = _secret_value("AZURE_CLIENT_SECRET", "").strip()
AZURE_REDIRECT_URI = _secret_value("AZURE_REDIRECT_URI", "http://localhost:8501").strip()
AZURE_AUTHORITY = _secret_value("AZURE_AUTHORITY", "https://login.microsoftonline.com/common").strip()

GRAPH_SCOPES = ["User.Read", "Mail.Read", "offline_access", "openid", "profile"]


def is_mock_mode() -> bool:
    """Return whether the app is running without Microsoft credentials."""
    return OUTLOOK_MODE == OUTLOOK_MODE_MOCK


def missing_live_settings() -> list[str]:
    """Return required live-mode setting names that are not configured."""
    required = {
        "AZURE_CLIENT_ID": AZURE_CLIENT_ID,
        "AZURE_CLIENT_SECRET": AZURE_CLIENT_SECRET,
        "AZURE_REDIRECT_URI": AZURE_REDIRECT_URI,
        "AZURE_AUTHORITY": AZURE_AUTHORITY,
    }
    return [name for name, value in required.items() if not value]
