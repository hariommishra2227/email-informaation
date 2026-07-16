"""Streamlit customer registry page."""

from __future__ import annotations

from io import StringIO

import pandas as pd
import streamlit as st

IMPORT_ERROR: Exception | None = None
try:
    from excel_exporter import EXCEL_FILE_NAME, export_customers_to_excel
    from services.customer_service import get_customers, to_export_rows
except Exception as exc:  # pragma: no cover
    IMPORT_ERROR = exc


DISPLAY_COLUMNS = {
    "contact_name": "Name",
    "organisation": "Organisation",
    "email": "Email",
    "mobile": "Mobile",
    "designation": "Designation",
    "address": "Address",
    "subject": "Subject",
    "source": "Source",
    "confidence": "Confidence",
    "status": "Status",
    "created_at": "Imported Date",
    "user_id": "User ID",
}


def render(user_id: str) -> None:
    """Render the customer registry page."""
    st.title("Customer Registry")
    if IMPORT_ERROR is not None:
        st.error(f"Customer Registry import failed: {IMPORT_ERROR}")
        return
    rows = get_customers(user_id)
    controls = st.columns([0.5, 0.25, 0.25])
    with controls[0]:
        search = st.text_input("Search", "")
    with controls[1]:
        status_filter = st.selectbox("Status filter", ["All", "Unique", "Duplicate", "Incomplete", "Failed"])
    with controls[2]:
        source_filter = st.selectbox("Source filter", ["All", "Outlook", "PDF", "TXT", "Manual"])

    if status_filter != "All":
        rows = [row for row in rows if row.get("status") == status_filter]
    if source_filter != "All":
        rows = [row for row in rows if row.get("source") == source_filter]
    if search.strip():
        needle = search.strip().lower()
        rows = [row for row in rows if needle in " ".join(str(value).lower() for value in row.values())]

    if not rows:
        st.info("No customers match the current filters.")
        return

    display = pd.DataFrame(rows)
    display = display[[column for column in DISPLAY_COLUMNS if column in display.columns]].rename(columns=DISPLAY_COLUMNS)
    st.dataframe(display, hide_index=True, use_container_width=True)

    export_rows = to_export_rows(rows)
    csv_buffer = StringIO()
    display.to_csv(csv_buffer, index=False)
    download_cols = st.columns(2)
    with download_cols[0]:
        st.download_button(
            "Download Excel",
            data=export_customers_to_excel(export_rows),
            file_name=EXCEL_FILE_NAME,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with download_cols[1]:
        st.download_button(
            "Download CSV",
            data=csv_buffer.getvalue(),
            file_name="customer_registry.csv",
            mime="text/csv",
            use_container_width=True,
        )


def render_page() -> None:
    """Standalone Streamlit multipage entrypoint."""
    st.set_page_config(page_title="Customer Registry", page_icon="@", layout="wide")
    try:
        from page_context import ensure_user_safely, initialize_database_safely, selected_user

        if IMPORT_ERROR is not None:
            raise IMPORT_ERROR
        user_id = selected_user()
        initialize_database_safely()
        ensure_user_safely(user_id)
        render(user_id)
    except Exception as exc:
        st.title("Customer Registry")
        st.error(f"Customer Registry failed to render: {exc}")


if __name__ == "__main__":
    render_page()
