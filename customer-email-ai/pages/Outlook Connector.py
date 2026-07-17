"""Streamlit Outlook Connector page."""

from __future__ import annotations

from datetime import date, datetime
import logging

import pandas as pd
import streamlit as st

IMPORT_ERROR: Exception | None = None
try:
    import config
    from excel_exporter import EXCEL_FILE_NAME, export_customers_to_excel
    from services import graph_auth, graph_client
    from services.customer_service import get_customers, to_export_rows
    from services.email_processor import process_outlook_message
    from storage import database
except Exception as exc:  # pragma: no cover
    IMPORT_ERROR = exc


LOGGER = logging.getLogger(__name__)


def _date_value(value: str) -> date:
    """Parse an Outlook timestamp for date filters."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return date.min


def _format_received(value: str) -> str:
    """Return an Outlook timestamp as a readable business date."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d %b %Y, %I:%M %p").lstrip("0")
    except ValueError:
        return value


def render(user_id: str) -> None:
    """Render the Outlook Inbox page."""
    st.title("Customer Email Extraction")
    st.caption("Outlook Connection -> Inbox Emails -> Select Emails -> Extract Customer Information -> Review Extracted Records -> Save to Customer Registry -> Download Excel")
    if IMPORT_ERROR is not None:
        LOGGER.exception("Outlook connector import failed.")
        st.error("Outlook Connector could not load. Please check the application setup.")
        return

    st.session_state.setdefault("imported_outlook_message_ids", [])
    st.session_state.setdefault("outlook_messages_cache", [])
    st.session_state.setdefault("outlook_import_summary", None)
    st.session_state.setdefault("selected_outlook_messages", [])

    can_load_inbox = _render_connection_panel()
    st.write("")
    limit, search_text, read_filter, date_range = _render_filters()
    st.write("")
    refresh_clicked, import_selected_clicked, import_unread_clicked = _render_quick_actions(user_id)

    if can_load_inbox and (refresh_clicked or not st.session_state.outlook_messages_cache):
        try:
            messages = graph_client.list_inbox_messages(user_id, limit=int(limit))
            st.session_state.outlook_messages_cache = messages
        except Exception as exc:
            LOGGER.exception("Outlook inbox refresh failed.")
            st.error(_friendly_exception_message(exc))
            messages = st.session_state.outlook_messages_cache[: int(limit)]
    else:
        messages = st.session_state.outlook_messages_cache[: int(limit)]

    for message in messages:
        try:
            database.upsert_outlook_message(message)
        except Exception:
            LOGGER.exception("Could not cache Outlook message metadata.")

    status_rows = {
        row["message_id"]: row["processing_status"]
        for row in database.list_outlook_message_rows(user_id)
    }

    filtered = _filter_messages(messages, search_text, read_filter, date_range)
    selected_ids = _render_inbox_list(filtered, status_rows)

    if import_selected_clicked:
        _import_messages(user_id, messages, selected_ids)
    if import_unread_clicked:
        _import_messages(user_id, messages, [message.message_id for message in messages if not message.is_read])

    _render_import_result()
    st.write("")
    _render_customer_preview(user_id)


def _render_connection_panel() -> bool:
    """Render Outlook connection state and return whether inbox loading can continue."""
    st.subheader("Outlook Connection")
    status_label = "Demo Mode" if config.is_mock_mode() else ("Connected" if graph_auth.is_connected() else "Outlook not connected")
    mode_label = "Demo Mode" if config.is_mock_mode() else "Real Mode"
    account = "Demo account" if config.is_mock_mode() else "Not connected"

    if config.is_mock_mode():
        st.markdown("`Demo Mode`")
        missing = ", ".join(config.missing_live_settings())
        st.info(
            "Demo email data is shown because Microsoft Outlook is not fully configured. "
            f"Missing configuration: {missing or 'Microsoft credentials'}."
        )
    else:
        graph_auth.handle_auth_callback()
        if graph_auth.auth_error():
            st.error(graph_auth.auth_error())
        account_data = graph_auth.connected_user()
        account = account_data.get("mail") or account_data.get("userPrincipalName") or "Not connected"

    status_cols = st.columns([0.2, 0.3, 0.14, 0.2, 0.16])
    with status_cols[0]:
        st.metric("Connection status", status_label)
    with status_cols[1]:
        st.metric("Connected account", account)
    with status_cols[2]:
        st.metric("Current mode", mode_label)
    login_disabled = _login_is_disabled()
    with status_cols[3]:
        if config.is_mock_mode() or login_disabled:
            st.button("Sign in with Microsoft", disabled=True, use_container_width=True)
        elif not graph_auth.is_connected():
            try:
                st.link_button("Sign in with Microsoft", graph_auth.create_login_url(), type="primary", use_container_width=True)
            except Exception as exc:
                LOGGER.exception("Could not create Microsoft login URL.")
                st.error(_friendly_exception_message(exc))
        else:
            st.button("Sign in with Microsoft", disabled=True, use_container_width=True)
    with status_cols[4]:
        if st.button("Disconnect", disabled=not graph_auth.is_connected(), use_container_width=True):
            graph_auth.logout_user()
            st.session_state.outlook_messages_cache = []
            st.session_state.selected_outlook_messages = []
            st.rerun()

    if login_disabled:
        st.caption("Microsoft login will be available after Client ID, Client Secret, Tenant ID and Redirect URI are configured.")

    if config.is_mock_mode():
        return True

    if not config.REDIRECT_URI.startswith(("http://localhost", "https://")):
        st.error("The redirect URL needs to be corrected before Outlook can connect.")
        return False

    if config.missing_live_settings():
        st.warning("Outlook sign-in is not configured yet.")
        return False

    if not graph_auth.is_connected():
        return False

    try:
        user = graph_auth.connected_user() or graph_client.get_current_user()
        graph_auth.set_connected_user(user)
    except Exception as exc:
        LOGGER.exception("Could not read connected Microsoft profile.")
        st.error(_friendly_exception_message(exc))
        return False

    return True


def _login_is_disabled() -> bool:
    """Return whether the Microsoft login button should be disabled."""
    if config.is_mock_mode():
        return True
    return not config.is_microsoft_configured()


def _render_quick_actions(user_id: str) -> tuple[bool, bool, bool]:
    """Render the business workflow action row."""
    st.subheader("Inbox Emails")
    action_cols = st.columns([0.25, 0.25, 0.25, 0.25])
    with action_cols[0]:
        refresh_clicked = st.button("Refresh Inbox", type="primary", use_container_width=True)
    with action_cols[1]:
        import_selected_clicked = st.button("Extract Selected Emails", use_container_width=True)
    with action_cols[2]:
        import_unread_clicked = st.button("Extract All Unread", use_container_width=True)
    with action_cols[3]:
        _render_excel_export(user_id, label="Export Excel")
    return refresh_clicked, import_selected_clicked, import_unread_clicked


def _render_filters() -> tuple[int, str, str, tuple[date, date] | list]:
    """Render simple visible filters and advanced options."""
    st.subheader("Simple Filters")
    filter_cols = st.columns([0.68, 0.32])
    with filter_cols[0]:
        search_text = st.text_input("Search sender or subject", "")
    with filter_cols[1]:
        read_filter = st.selectbox("Email status", ["All", "Unread", "Read"])
    with st.expander("Advanced Filters"):
        advanced_cols = st.columns([0.5, 0.5])
        with advanced_cols[0]:
            date_range = st.date_input("Date range", value=[])
        with advanced_cols[1]:
            limit = st.number_input("Maximum emails", min_value=1, max_value=500, value=50, step=25)
    return int(limit), search_text, read_filter, date_range


def _filter_messages(messages: list, search_text: str, read_filter: str, date_range: tuple[date, date] | list) -> list:
    """Filter Outlook messages by business-facing controls."""
    filtered = messages
    if search_text.strip():
        needle = search_text.strip().lower()
        filtered = [
            message for message in filtered
            if needle in message.sender_name.lower()
            or needle in message.sender_email.lower()
            or needle in message.subject.lower()
        ]
    if read_filter != "All":
        want_read = read_filter == "Read"
        filtered = [message for message in filtered if message.is_read is want_read]
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
        filtered = [
            message for message in filtered
            if start_date <= _date_value(message.received_datetime) <= end_date
        ]
    return filtered


def _render_inbox_list(messages: list, status_rows: dict[str, str]) -> list[str]:
    """Render selectable inbox rows and return selected message ids."""
    st.subheader("Select Emails")
    if not messages:
        st.info("No emails found for the selected filters.")
        st.session_state.selected_outlook_messages = []
        return []

    table_rows = [
        {
            "Select": message.message_id in st.session_state.selected_outlook_messages,
            "Sender": message.sender_name,
            "Sender Email": message.sender_email,
            "Subject": message.subject,
            "Received": _format_received(message.received_datetime),
            "Status": "Read" if message.is_read else "Unread",
            "Processing": status_rows.get(message.message_id, "Pending"),
            "Attachment": "Yes" if message.has_attachments else "No",
            "Message ID": message.message_id,
        }
        for message in messages
    ]
    disabled_columns = [column for column in table_rows[0] if column not in {"Select"}]
    edited = st.data_editor(
        pd.DataFrame(table_rows),
        hide_index=True,
        use_container_width=True,
        disabled=disabled_columns,
        column_config={
            "Select": st.column_config.CheckboxColumn("Select"),
            "Message ID": st.column_config.TextColumn("Message ID", disabled=True),
        },
    )
    selected_ids = edited.loc[edited["Select"], "Message ID"].tolist() if not edited.empty else []
    st.session_state.selected_outlook_messages = selected_ids
    st.caption(f"{len(messages)} fetched. {len(selected_ids)} selected. Status is refreshed after extraction.")
    return selected_ids


def _render_excel_export(user_id: str, label: str = "Export to Excel") -> None:
    """Render Outlook registry Excel export button."""
    rows = get_customers(user_id)
    if not rows:
        st.button(label, disabled=True, use_container_width=True)
        return
    st.download_button(
        label,
        data=export_customers_to_excel(to_export_rows(rows)),
        file_name=EXCEL_FILE_NAME,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


def _import_messages(user_id: str, messages: list, message_ids: list[str]) -> None:
    """Import selected mock/live Outlook messages."""
    st.subheader("Extract Customer Information")
    by_id = {message.message_id: message for message in messages}
    summary = {
        "emails_processed": 0,
        "customers_extracted": 0,
        "duplicates_skipped": 0,
        "incomplete_records": 0,
        "failed_records": 0,
    }
    imported_ids = set(st.session_state.get("imported_outlook_message_ids", []))
    if not message_ids:
        st.session_state.outlook_import_summary = summary
        st.warning("Select at least one email to import.")
        return
    progress = st.progress(0)
    for index, message_id in enumerate(message_ids, start=1):
        summary["emails_processed"] += 1
        if message_id in imported_ids:
            summary["duplicates_skipped"] += 1
            continue
        message = by_id.get(message_id)
        if not message:
            summary["failed_records"] += 1
            continue
        try:
            result = process_outlook_message(user_id, message)
            if result.status == "Already Processed":
                summary["duplicates_skipped"] += 1
            elif result.status == "Duplicate":
                summary["duplicates_skipped"] += 1
                imported_ids.add(message_id)
            elif result.status == "Incomplete":
                summary["incomplete_records"] += 1
                summary["customers_extracted"] += 1
                imported_ids.add(message_id)
            elif result.status == "Failed":
                summary["failed_records"] += 1
            else:
                summary["customers_extracted"] += 1
                imported_ids.add(message_id)
        except Exception:
            LOGGER.exception("Outlook message import failed.")
            summary["failed_records"] += 1
        progress.progress(index / len(message_ids))
    st.session_state.imported_outlook_message_ids = sorted(imported_ids)
    st.session_state.outlook_import_summary = summary


def _render_import_result() -> None:
    """Render the latest import summary."""
    summary = st.session_state.get("outlook_import_summary")
    if not summary:
        return
    st.subheader("Extraction Summary")
    result_cols = st.columns(5)
    labels = [
        ("Emails processed", "emails_processed"),
        ("Customers extracted", "customers_extracted"),
        ("Duplicates skipped", "duplicates_skipped"),
        ("Incomplete records", "incomplete_records"),
        ("Failed records", "failed_records"),
    ]
    for index, (label, key) in enumerate(labels):
        with result_cols[index]:
            st.metric(label, summary.get(key, 0))
    if summary.get("failed_records"):
        st.warning("Some emails could not be imported. The technical details were written to the logs.")
    elif summary.get("emails_processed"):
        st.success("Customer information was saved to the Customer Registry.")


def _render_customer_preview(user_id: str) -> None:
    """Render extracted Outlook customers below the inbox."""
    st.subheader("Review Extracted Records")
    rows = [row for row in get_customers(user_id) if row.get("source") == "Outlook"]
    if not rows:
        st.info("Extracted Outlook customer records will appear here after import.")
        return
    preview = pd.DataFrame(rows)
    column_map = {
        "contact_name": "Contact Name",
        "organisation": "Organisation",
        "email": "Email",
        "mobile": "Mobile",
        "designation": "Designation",
        "subject": "Subject",
        "source": "Source",
        "status": "Status",
    }
    preview = preview[[column for column in column_map if column in preview.columns]].rename(columns=column_map)
    st.dataframe(preview, hide_index=True, use_container_width=True)
    st.download_button(
        "Download Excel Report",
        data=export_customers_to_excel(to_export_rows(rows)),
        file_name=EXCEL_FILE_NAME,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


def _friendly_exception_message(exc: Exception) -> str:
    """Return a simple UI message for technical failures."""
    message = str(exc)
    lower = message.lower()
    if "expired" in lower:
        return "Your Outlook session expired. Please sign in again."
    if "secret" in lower or "invalid_client" in lower:
        return "The Microsoft client secret is invalid or expired. Ask the administrator to update Streamlit Secrets with a new Secret Value."
    if "redirect" in lower or "aadsts50011" in lower:
        return "The Microsoft redirect URI does not match the Entra app registration."
    if "tenant" in lower:
        return "The Microsoft tenant ID is invalid or does not match this app registration."
    if "permission" in lower or "consent" in lower:
        return "Outlook permissions are not approved yet. Your Microsoft administrator may need to grant access."
    if "mail.read" in lower:
        return "The Mail.Read permission is missing or has not been approved."
    if "network" in lower:
        return "Network failure while contacting Microsoft. Please try again."
    if "graph" in lower:
        return "Microsoft Graph could not complete the request. Please try again."
    if "database" in lower:
        return "The database could not save Outlook data. Please try again."
    if "mailbox" in lower:
        return "This Microsoft account does not have an available mailbox."
    if "configuration" in lower or "missing" in lower:
        return "Outlook is not configured yet. Please check Settings."
    return "Outlook could not complete the request. Please try again."


def render_page() -> None:
    """Standalone Streamlit multipage entrypoint."""
    st.set_page_config(page_title="Outlook Connector", page_icon="@", layout="wide")
    try:
        from page_context import ensure_user_safely, initialize_database_safely, selected_user

        if IMPORT_ERROR is not None:
            raise IMPORT_ERROR
        user_id = selected_user()
        initialize_database_safely()
        ensure_user_safely(user_id)
        render(user_id)
    except Exception as exc:
        LOGGER.exception("Outlook Connector failed to render.")
        st.title("Customer Email Extraction")
        st.error("Outlook Connector could not render. Please try again.")


if __name__ == "__main__":
    render_page()
