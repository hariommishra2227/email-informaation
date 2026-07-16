"""Shared Streamlit setup for app and standalone multipage files."""

from __future__ import annotations

from typing import Any

import streamlit as st

import config
from storage import database


def initialize_safe_session_state() -> None:
    """Create session defaults used across Streamlit pages."""
    defaults: dict[str, Any] = {
        "app_user_id": config.DEFAULT_USER_ID,
        "app_user_email": config.APP_USER_EMAIL,
        "customers": [],
        "processed_messages": set(),
        "selected_outlook_messages": [],
        "outlook_mode": config.OUTLOOK_MODE,
        "email_text": "",
        "extracted_customer": {},
        "pdf_preview_text": "",
        "txt_preview_text": "",
        "processed_pdf_signatures": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def selected_user() -> str:
    """Return the fixed internal user id for the single-user app."""
    initialize_safe_session_state()
    return config.DEFAULT_USER_ID


def initialize_database_safely() -> bool:
    """Initialize SQLite without allowing failures to blank the page."""
    try:
        database.initialize_database()
        return True
    except Exception as exc:
        st.error(f"Database initialization failed: {exc}")
        return False


def ensure_user_safely(user_id: str) -> bool:
    """Ensure the selected user exists without blanking the page on failure."""
    try:
        database.ensure_user(user_id, email=config.APP_USER_EMAIL, display_name=config.APP_USER_EMAIL)
        return True
    except Exception as exc:
        st.error(f"User setup failed: {exc}")
        return False


def outlook_mode_banner() -> None:
    """Show the current Outlook connection mode."""
    if config.is_mock_mode():
        st.info("Demo Outlook Mode - no Microsoft account is connected.")
    else:
        st.success("Connected to Microsoft Outlook")
