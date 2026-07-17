"""Streamlit settings page for Outlook configuration visibility."""

from __future__ import annotations

import streamlit as st

IMPORT_ERROR: Exception | None = None
try:
    import config
    from services import graph_auth
except Exception as exc:  # pragma: no cover
    IMPORT_ERROR = exc


def render() -> None:
    """Render the Settings page."""
    st.title("Settings")
    if IMPORT_ERROR is not None:
        st.error("Settings could not load. Please check the application setup.")
        return
    rows = {
        "Application Mode": "Demo Mode" if config.is_mock_mode() else "Real Mode",
        "Outlook Account Support": "Work/School and Personal Microsoft accounts",
        "Connection Status": _connection_status(),
        "Application Version": "1.0",
    }
    for label, value in rows.items():
        st.write(f"**{label}:** {value}")

    st.subheader("Microsoft Outlook Configuration")
    status_rows = {
        "Client ID configured": "Yes" if config.CLIENT_ID else "No",
        "Client Secret configured": "Yes" if config.CLIENT_SECRET else "No",
        "Tenant ID configured": "Yes" if config.TENANT_ID else "No",
        "Redirect URI configured": "Yes" if config.REDIRECT_URI else "No",
        "Outlook live mode ready": "Yes" if config.is_microsoft_configured() else "No",
    }
    for label, value in status_rows.items():
        st.write(f"**{label}:** {value}")

    if st.button("Test Configuration", type="primary"):
        if config.is_mock_mode():
            st.success("Demo mode is ready.")
        else:
            missing = config.missing_live_settings()
            if missing:
                st.error("Live Outlook configuration is incomplete.")
            elif not config.REDIRECT_URI.startswith(("http://localhost", "https://")):
                st.error("Redirect URI must be http://localhost:8501 locally or an HTTPS Streamlit Cloud URL.")
            else:
                st.success("Live Outlook configuration is present. Complete Microsoft sign-in to test mailbox access.")


def _connection_status() -> str:
    """Return a safe settings-page connection status."""
    if config.is_mock_mode():
        return "Demo Mode"
    return "Connected" if graph_auth.is_connected() else "Not connected"


def render_page() -> None:
    """Standalone Streamlit multipage entrypoint."""
    st.set_page_config(page_title="Settings", page_icon="@", layout="wide")
    try:
        from page_context import initialize_database_safely

        if IMPORT_ERROR is not None:
            raise IMPORT_ERROR
        initialize_database_safely()
        render()
    except Exception as exc:
        st.title("Settings")
        st.error("Settings could not render. Please try again.")


if __name__ == "__main__":
    render_page()
