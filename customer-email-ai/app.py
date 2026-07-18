"""Streamlit dashboard for customer email extraction and duplicate detection."""

from __future__ import annotations

from html import escape
from io import BytesIO
from typing import Any

import pdfplumber
import streamlit as st

from bulk_email_processor import process_uploaded_txt_file
from duplicate_detector import detect_duplicates, normalize_mobile
from excel_exporter import EXCEL_FILE_NAME, export_customers_to_excel
from extractor import EmailExtractionEngine


CUSTOMER_FIELDS = (
    "contact_person_name",
    "email_id",
    "organisation_name",
    "mobile_number",
    "address",
    "designation",
    "subject",
)
INTERNAL_CUSTOMER_FIELDS = (
    "normalized_phone",
    "input_source",
    "extraction_confidence",
)
REGISTRY_FIELDS = CUSTOMER_FIELDS + INTERNAL_CUSTOMER_FIELDS

FIELD_LABELS = {
    "contact_person_name": "Contact Person Name",
    "email_id": "Email",
    "organisation_name": "Organisation",
    "mobile_number": "Mobile",
    "address": "Address",
    "designation": "Designation",
    "subject": "Subject",
}

STATUS_COLORS = {
    "Unique": ("#047857", "#d1fae5", "🟢"),
    "Possible Duplicate": ("#b45309", "#fef3c7", "🟡"),
    "Duplicate": ("#b91c1c", "#fee2e2", "🔴"),
}

STATUS_FILTERS = ("All", "Unique", "Duplicate", "Possible Duplicate")


@st.cache_resource
def get_extraction_engine() -> EmailExtractionEngine:
    """Return a cached extraction engine instance."""
    return EmailExtractionEngine()


def initialize_session_state() -> None:
    """Create Streamlit session state values used by the dashboard."""
    if "email_text" not in st.session_state:
        st.session_state.email_text = ""
    if "extracted_customer" not in st.session_state:
        st.session_state.extracted_customer = empty_customer()
    if "customers" not in st.session_state:
        st.session_state.customers = []
    if "last_status" not in st.session_state:
        st.session_state.last_status = ""
    if "emails_processed" not in st.session_state:
        st.session_state.emails_processed = 0
    if "message" not in st.session_state:
        st.session_state.message = None
    if "last_bulk_upload_signature" not in st.session_state:
        st.session_state.last_bulk_upload_signature = ""
    if "last_txt_upload_signature" not in st.session_state:
        st.session_state.last_txt_upload_signature = ""
    if "processed_pdf_signatures" not in st.session_state:
        st.session_state.processed_pdf_signatures = []
    if "pdf_preview_text" not in st.session_state:
        st.session_state.pdf_preview_text = ""
    if "txt_preview_text" not in st.session_state:
        st.session_state.txt_preview_text = ""


def empty_customer() -> dict[str, str]:
    """Return an empty customer record."""
    customer = {field: "" for field in REGISTRY_FIELDS}
    customer["extraction_confidence"] = "0"
    return customer


def reset_workspace() -> None:
    """Clear pasted text and the current extracted customer form."""
    st.session_state.email_text = ""
    st.session_state.extracted_customer = empty_customer()
    st.session_state.last_status = ""
    st.session_state.pdf_preview_text = ""
    st.session_state.txt_preview_text = ""

    for field in CUSTOMER_FIELDS:
        st.session_state.pop(f"field_{field}", None)
    st.session_state.pop("email_text_input", None)


def reset_session() -> None:
    """Clear the full dashboard session."""
    reset_workspace()
    st.session_state.customers = []
    st.session_state.emails_processed = 0
    st.session_state.last_bulk_upload_signature = ""
    st.session_state.last_txt_upload_signature = ""
    st.session_state.processed_pdf_signatures = []
    st.session_state.message = ("success", "Session reset successfully.")


def set_message(level: str, message: str) -> None:
    """Store a user-facing message for display."""
    st.session_state.message = (level, message)


def render_message() -> None:
    """Render the latest user-facing message."""
    message_data = st.session_state.get("message")
    if not message_data:
        return

    level, message = message_data
    if level == "success":
        st.success(message)
    elif level == "warning":
        st.warning(message)
    else:
        st.error(message)


def decode_txt_file(uploaded_file: Any) -> str:
    """Decode an uploaded TXT file safely."""
    try:
        return uploaded_file.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        return uploaded_file.getvalue().decode("latin-1", errors="ignore")


def decode_pdf_file(uploaded_file: Any) -> str:
    """Extract readable text from an uploaded PDF page by page."""
    try:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)

        extracted_pages: list[str] = []
        file_bytes = BytesIO(uploaded_file.getvalue())

        with pdfplumber.open(file_bytes) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    extracted_pages.append(page_text)

        if not extracted_pages:
            return "No readable text found. This PDF may be scanned and requires OCR."

        return "\n".join(extracted_pages)
    except Exception:
        return "No readable text found. This PDF may be scanned and requires OCR."


def extraction_confidence(customer: dict[str, Any]) -> int:
    """Score extraction completeness from the requested weighted fields."""
    scoring_fields = (
        ("email_id", 30),
        ("contact_person_name", 15),
        ("organisation_name", 15),
        ("mobile_number", 15),
        ("designation", 10),
        ("subject", 10),
        ("address", 5),
    )
    return min(100, sum(points for field, points in scoring_fields if str(customer.get(field, "")).strip()))


def _with_extraction_metadata(customer: dict[str, Any], input_source: str) -> dict[str, str]:
    """Attach source and extraction confidence metadata to an extracted customer."""
    customer = {
        **customer,
        "contact_person_name": customer.get("contact_person_name") or customer.get("customer_name") or customer.get("name", ""),
        "email_id": customer.get("email_id") or customer.get("email", ""),
        "organisation_name": customer.get("organisation_name") or customer.get("company", ""),
        "mobile_number": customer.get("mobile_number") or customer.get("phone", ""),
    }
    enriched_customer = {
        field: str(customer.get(field, "")).strip()
        for field in REGISTRY_FIELDS
    }
    enriched_customer["input_source"] = input_source
    enriched_customer["normalized_phone"] = normalize_mobile(enriched_customer.get("mobile_number", ""))
    enriched_customer["extraction_confidence"] = str(extraction_confidence(enriched_customer))
    return enriched_customer


def extract_customer(email_text: str, input_source: str) -> None:
    """Extract customer details from email text into session state."""
    if not email_text.strip():
        set_message("warning", "Provide text for this extraction source before extracting.")
        return

    try:
        engine = get_extraction_engine()
        st.session_state.extracted_customer = _with_extraction_metadata(
            engine.extract(email_text),
            input_source,
        )
        for field, value in st.session_state.extracted_customer.items():
            st.session_state[f"field_{field}"] = value

        if st.session_state.extracted_customer.get("email_id"):
            add_extracted_customer_to_registry(st.session_state.extracted_customer)

        st.session_state.emails_processed += 1
        st.session_state.last_status = ""
        set_message("success", "Extraction completed. Review the fields before adding the customer.")
    except Exception as exc:
        set_message("error", f"Extraction failed: {exc}")


def add_customer(customer: dict[str, str]) -> None:
    """Add a customer to memory and refresh duplicate statuses."""
    cleaned_customer = {
        field: str(customer.get(field, "")).strip()
        for field in REGISTRY_FIELDS
    }
    if not cleaned_customer.get("input_source"):
        cleaned_customer["input_source"] = "Manual Paste"
    cleaned_customer["normalized_phone"] = normalize_mobile(cleaned_customer.get("mobile_number", ""))
    cleaned_customer["extraction_confidence"] = str(extraction_confidence(cleaned_customer))

    if not any(cleaned_customer.get(field) for field in CUSTOMER_FIELDS):
        set_message("warning", "Add at least one customer field before saving.")
        return

    try:
        st.session_state.customers.append(cleaned_customer)
        st.session_state.customers = detect_duplicates(st.session_state.customers)
        st.session_state.last_status = st.session_state.customers[-1]["duplicate_status"]
        set_message("success", f"Customer added as {st.session_state.last_status}.")
    except Exception as exc:
        set_message("error", f"Customer could not be added: {exc}")


def add_extracted_customer_to_registry(customer: dict[str, str]) -> None:
    """Persist an extracted customer into the registry when a valid email exists."""
    cleaned_customer = {
        field: str(customer.get(field, "")).strip()
        for field in REGISTRY_FIELDS
    }
    cleaned_customer["normalized_phone"] = normalize_mobile(cleaned_customer.get("mobile_number", ""))
    cleaned_customer["extraction_confidence"] = str(extraction_confidence(cleaned_customer))

    if not cleaned_customer.get("email_id"):
        return

    if any(existing.get("email_id") == cleaned_customer["email_id"] for existing in st.session_state.customers):
        return

    st.session_state.customers.append(cleaned_customer)
    st.session_state.customers = detect_duplicates(st.session_state.customers)
    st.session_state.last_status = st.session_state.customers[-1]["duplicate_status"]


def process_bulk_upload(uploaded_file: Any) -> None:
    """Process a TXT upload containing multiple emails."""
    try:
        progress_bar = st.progress(0)

        def update_progress(processed_count: int, total_count: int) -> None:
            progress_bar.progress(processed_count / total_count)

        records = process_uploaded_txt_file(
            uploaded_file,
            progress_callback=update_progress,
            extraction_engine=get_extraction_engine(),
        )
        records = [_with_extraction_metadata(record, "TXT") for record in records]

        if not records:
            set_message("warning", "No emails were found in the uploaded TXT file.")
            return

        st.session_state.customers = detect_duplicates(st.session_state.customers + records)
        st.session_state.emails_processed += len(records)
        set_message("success", f"Processed {len(records)} emails from the bulk upload.")
    except Exception as exc:
        set_message("error", f"Bulk email processing failed: {exc}")


def process_pdf_upload(uploaded_file: Any) -> None:
    """Extract one uploaded PDF on explicit user action without touching manual text."""
    pdf_signature = uploaded_file_signature(uploaded_file)
    if pdf_signature in st.session_state.processed_pdf_signatures:
        set_message("warning", "This PDF has already been extracted in this session.")
        return

    st.session_state.pdf_preview_text = decode_pdf_file(uploaded_file)
    if st.session_state.pdf_preview_text.startswith("No readable text found"):
        set_message("warning", st.session_state.pdf_preview_text)
        return

    extract_customer(st.session_state.pdf_preview_text, "PDF")
    st.session_state.processed_pdf_signatures.append(pdf_signature)


def uploaded_file_signature(uploaded_file: Any) -> str:
    """Return a stable signature for an uploaded file."""
    return f"{uploaded_file.name}:{uploaded_file.size}"


def count_status(customers: list[dict[str, Any]], status: str) -> int:
    """Count customers with a given duplicate status."""
    return sum(1 for customer in customers if customer.get("duplicate_status") == status)


def status_badge(status: str) -> str:
    """Return an HTML badge for a duplicate status."""
    text_color, background, icon = STATUS_COLORS.get(status, ("#374151", "#f3f4f6", ""))
    return (
        f"<span class='status-badge' style='color:{text_color};"
        f"background:{background};'>{icon} {status}</span>"
    )


def filter_customers(
    customers: list[dict[str, Any]],
    search_query: str,
    status_filter: str,
) -> list[dict[str, Any]]:
    """Filter customers by search text and duplicate status."""
    query = search_query.strip().lower()
    filtered_customers = customers

    if status_filter != "All":
        filtered_customers = [
            customer
            for customer in filtered_customers
            if customer.get("duplicate_status") == status_filter
        ]

    if not query:
        return filtered_customers

    return [
        customer
        for customer in filtered_customers
        if query in " ".join(str(value).lower() for value in customer.values())
    ]


def render_styles() -> None:
    """Apply dashboard styling."""
    st.markdown(
        """
        <style>
            html, body, [class*="css"] {
                font-family: Inter, Segoe UI, Arial, sans-serif;
            }

            .stApp {
                background: #f8fafc;
            }

            .main .block-container {
                padding-top: 1.25rem;
                padding-bottom: 2.5rem;
                max-width: 1500px;
            }

            .company-header {
                background: linear-gradient(135deg, #0f172a 0%, #164e63 58%, #0f766e 100%);
                border-radius: 12px;
                color: #ffffff;
                margin-bottom: 1.1rem;
                padding: 1.35rem 1.5rem;
                box-shadow: 0 18px 45px rgba(15, 23, 42, 0.16);
            }

            .brand-row {
                align-items: center;
                display: flex;
                gap: 0.75rem;
                margin-bottom: 0.75rem;
            }

            .brand-mark {
                align-items: center;
                background: rgba(255, 255, 255, 0.16);
                border: 1px solid rgba(255, 255, 255, 0.26);
                border-radius: 10px;
                display: inline-flex;
                font-size: 1.25rem;
                height: 42px;
                justify-content: center;
                width: 42px;
            }

            .brand-kicker {
                color: #bae6fd;
                font-size: 0.78rem;
                font-weight: 800;
                letter-spacing: 0.08em;
                text-transform: uppercase;
            }

            .dashboard-title {
                color: #ffffff;
                font-size: 2.35rem;
                font-weight: 800;
                line-height: 1.12;
                margin: 0;
            }

            .dashboard-subtitle {
                color: #dbeafe;
                font-size: 1.02rem;
                margin: 0.35rem 0 0;
            }

            .panel {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                box-shadow: 0 8px 28px rgba(15, 23, 42, 0.06);
                padding: 1rem;
            }

            .metric-card {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
                min-height: 112px;
                padding: 1rem 1.05rem;
            }

            .metric-topline {
                align-items: center;
                display: flex;
                justify-content: space-between;
                margin-bottom: 0.55rem;
            }

            .metric-icon {
                align-items: center;
                background: #e0f2fe;
                border-radius: 8px;
                color: #0369a1;
                display: inline-flex;
                font-size: 1.05rem;
                height: 34px;
                justify-content: center;
                width: 34px;
            }

            .metric-label {
                color: #64748b;
                font-size: 0.82rem;
                font-weight: 700;
                text-transform: uppercase;
            }

            .metric-value {
                color: #0f172a;
                font-size: 2rem;
                font-weight: 800;
                line-height: 1;
            }

            .section-heading {
                align-items: center;
                color: #0f172a;
                display: flex;
                font-size: 1rem;
                font-weight: 800;
                gap: 0.45rem;
                margin: 0.15rem 0 0.85rem;
            }

            .status-badge {
                border-radius: 999px;
                display: inline-block;
                font-size: 0.78rem;
                font-weight: 800;
                padding: 0.28rem 0.62rem;
                white-space: nowrap;
            }

            table.customer-table {
                border-collapse: collapse;
                width: 100%;
                font-size: 0.88rem;
                overflow: hidden;
            }

            table.customer-table th {
                background: #f9fafb;
                border-bottom: 1px solid #e5e7eb;
                color: #334155;
                font-weight: 800;
                padding: 0.72rem;
                text-align: left;
            }

            table.customer-table td {
                border-bottom: 1px solid #f3f4f6;
                color: #111827;
                padding: 0.72rem;
                vertical-align: top;
            }

            .confidence-label {
                color: #334155;
                font-size: 0.88rem;
                font-weight: 800;
                margin-bottom: 0.25rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_metric_card(label: str, value: int, icon: str) -> None:
    """Render one dashboard metric card."""
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-topline">
                <div class="metric-label">{label}</div>
                <div class="metric-icon">{icon}</div>
            </div>
            <div class="metric-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_dashboard_cards() -> None:
    """Render summary dashboard cards."""
    customers = st.session_state.customers
    emails_processed = st.session_state.emails_processed
    customers_extracted = len(customers)
    unique_customers = count_status(customers, "Unique")
    duplicates = count_status(customers, "Duplicate")

    card_columns = st.columns(4)
    with card_columns[0]:
        render_metric_card("Total Emails Processed", emails_processed, "✉")
    with card_columns[1]:
        render_metric_card("Customers Extracted", customers_extracted, "▣")
    with card_columns[2]:
        render_metric_card("Unique Customers", unique_customers, "✓")
    with card_columns[3]:
        render_metric_card("Duplicate Customers", duplicates, "!")


def render_extracted_fields() -> dict[str, str]:
    """Render editable extracted fields and return their current values."""
    customer = st.session_state.extracted_customer
    updated_customer: dict[str, str] = {}

    for field in CUSTOMER_FIELDS:
        updated_customer[field] = st.text_input(
            FIELD_LABELS[field],
            value=str(customer.get(field, "")),
            key=f"field_{field}",
        )

    for field in INTERNAL_CUSTOMER_FIELDS:
        updated_customer[field] = str(customer.get(field, ""))
    updated_customer["extraction_confidence"] = str(extraction_confidence(updated_customer))
    st.session_state.extracted_customer = updated_customer
    return updated_customer


def render_customer_table(customers: list[dict[str, Any]]) -> None:
    """Render the in-memory customer table."""
    if not customers:
        st.info("No matching customers found.")
        return

    rows = []
    for customer in customers:
        rows.append(
            "<tr>"
            f"<td>{escape(str(customer.get('contact_person_name', '')))}</td>"
            f"<td>{escape(str(customer.get('organisation_name', '')))}</td>"
            f"<td>{escape(str(customer.get('email_id', '')))}</td>"
            f"<td>{escape(str(customer.get('mobile_number', '')))}</td>"
            f"<td>{escape(str(customer.get('input_source', '')))}</td>"
            f"<td>{escape(str(customer.get('designation', '')))}</td>"
            f"<td>{escape(str(customer.get('subject', '')))}</td>"
            f"<td>{status_badge(str(customer.get('duplicate_status', 'Unique')))}</td>"
            "</tr>"
        )

    table_html = (
        "<table class='customer-table'>"
        "<thead><tr>"
        "<th>Name</th><th>Organisation</th><th>Email</th>"
        "<th>Mobile</th><th>Source</th><th>Designation</th><th>Subject</th><th>Status</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def main() -> None:
    """Run the Streamlit dashboard."""
    st.set_page_config(
        page_title="Customer Email Extraction AI",
        page_icon="✉",
        layout="wide",
    )
    initialize_session_state()
    render_styles()

    st.markdown(
        """
        <div class="company-header">
            <div class="brand-row">
                <div class="brand-mark">✉</div>
                <div>
                    <div class="brand-kicker">Executive Customer Intelligence</div>
                    <div class="dashboard-title">Customer Email Extraction AI</div>
                    <div class="dashboard-subtitle">
                        AI Powered Customer Information Extraction & Duplicate Detection
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_message()

    render_dashboard_cards()
    st.write("")

    left_panel, right_panel = st.columns([0.42, 0.58], gap="large")

    with left_panel:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.markdown("<div class='section-heading'>▣ Email Intake</div>", unsafe_allow_html=True)

        txt_file = st.file_uploader("Upload TXT file", type=["txt"])
        if txt_file is not None:
            st.session_state.txt_preview_text = decode_txt_file(txt_file)

        if st.session_state.get("txt_preview_text"):
            with st.expander("TXT extracted text preview", expanded=False):
                st.code(st.session_state.txt_preview_text, language="text")
            if st.button("Extract TXT", type="secondary", use_container_width=True):
                st.session_state.last_txt_upload_signature = uploaded_file_signature(txt_file) if txt_file else ""
                extract_customer(st.session_state.txt_preview_text, "TXT")

        bulk_txt_file = st.file_uploader(
            "Upload Multiple Emails",
            type=["txt"],
            key="bulk_email_upload",
        )
        if (
            bulk_txt_file is not None
            and uploaded_file_signature(bulk_txt_file) != st.session_state.last_bulk_upload_signature
        ):
            st.session_state.last_bulk_upload_signature = uploaded_file_signature(bulk_txt_file)
            process_bulk_upload(bulk_txt_file)

        pdf_file = st.file_uploader("Upload PDF file", type=["pdf"])
        if pdf_file is not None:
            st.caption(f"Ready to extract: {pdf_file.name}")
            if st.button("Extract PDF", type="secondary", use_container_width=True):
                process_pdf_upload(pdf_file)

        if st.session_state.get("pdf_preview_text"):
            with st.expander("PDF extracted text preview", expanded=False):
                st.code(st.session_state.pdf_preview_text, language="text")

        email_text = st.text_area(
            "Paste Email Text",
            value=st.session_state.email_text,
            height=220,
            key="email_text_input",
        )
        st.session_state.email_text = email_text

        action_columns = st.columns(2)
        with action_columns[0]:
            if st.button("Extract", type="primary", use_container_width=True):
                extract_customer(st.session_state.email_text, "Manual Paste")
        with action_columns[1]:
            if st.button("Clear", use_container_width=True):
                reset_workspace()
                st.rerun()

        st.markdown("<div class='section-heading'>✓ Extracted Customer Profile</div>", unsafe_allow_html=True)
        confidence_score = extraction_confidence(st.session_state.extracted_customer)
        st.markdown(
            f"<div class='confidence-label'>Extraction Confidence: {confidence_score}%</div>",
            unsafe_allow_html=True,
        )
        st.progress(confidence_score)
        updated_customer = render_extracted_fields()

        if st.button("Add Customer", use_container_width=True):
            add_customer(updated_customer)
            if st.session_state.last_status:
                st.markdown(status_badge(st.session_state.last_status), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with right_panel:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.markdown("<div class='section-heading'>▤ Customer Registry</div>", unsafe_allow_html=True)

        controls = st.columns([0.5, 0.28, 0.22])
        with controls[0]:
            search_query = st.text_input("Search customers", placeholder="Search name, email, organisation...")
        with controls[1]:
            status_filter = st.selectbox("Status filter", STATUS_FILTERS)
        with controls[2]:
            st.write("")
            if st.button("Reset Session", use_container_width=True):
                reset_session()
                st.rerun()

        filtered_customers = filter_customers(
            st.session_state.customers,
            search_query,
            status_filter,
        )
        render_customer_table(filtered_customers)

        st.write("")
        if st.session_state.customers:
            excel_buffer = export_customers_to_excel(st.session_state.customers)
            st.download_button(
                "Download Excel Report",
                data=excel_buffer,
                file_name=EXCEL_FILE_NAME,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.button("Download Excel Report", disabled=True, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)


def upgraded_main() -> None:
    """Run the Outlook-ready Streamlit app."""
    try:
        import config as app_config
        from page_context import initialize_database_safely, initialize_outlook_session_state, selected_user
        from services import graph_auth
        from services import graph_client
        from storage import database

        st.set_page_config(
            page_title="Dashboard",
            page_icon="@",
            layout="wide",
        )
        initialize_session_state()
        initialize_outlook_session_state()
        render_styles()

        if not app_config.is_mock_mode():
            graph_auth.handle_auth_callback()

        st.title("Dashboard")
        st.caption("Track Outlook emails, extracted customers and duplicate records.")
        if not app_config.is_mock_mode() and graph_auth.auth_error():
            st.error(graph_auth.auth_error())
        elif not app_config.is_mock_mode() and graph_auth.is_connected():
            st.success("Microsoft Outlook is connected. Open Outlook Connector to load inbox emails.")

        user_id = selected_user()
        database_ready = initialize_database_safely()
        if database_ready:
            try:
                database.ensure_user(user_id, email=app_config.APP_USER_EMAIL, display_name=app_config.APP_USER_EMAIL)
            except Exception as exc:
                st.error(f"User setup failed: {exc}")

        st.page_link("pages/Outlook Connector.py", label="Open Outlook Connector", icon="@", use_container_width=True)

        if database_ready:
            try:
                if app_config.is_mock_mode() and not database.list_outlook_message_rows(user_id):
                    for message in graph_client.list_inbox_messages(user_id, limit=50):
                        database.upsert_outlook_message(message)
            except Exception as exc:
                st.exception(exc)
            outlook_rows = database.list_outlook_message_rows(user_id)
            customer_rows = database.list_customers(user_id)
            labels = [
                ("Inbox Emails", len(outlook_rows)),
                ("Unread Emails", sum(1 for row in outlook_rows if not row.get("is_read"))),
                ("Customers Extracted", len(customer_rows)),
                ("Unique Records", sum(1 for row in customer_rows if row.get("status") == "Unique")),
                ("Duplicates", sum(1 for row in customer_rows if row.get("status") == "Duplicate")),
                ("Incomplete Records", sum(1 for row in customer_rows if row.get("status") == "Incomplete")),
            ]
            metric_columns = st.columns(3)
            for index, (label, value) in enumerate(labels):
                with metric_columns[index % 3]:
                    st.metric(label, value)
        else:
            st.warning("Dashboard metrics are unavailable until the local database can be initialized.")
    except Exception as exc:
        st.title("Dashboard")
        st.exception(exc)


main = upgraded_main


if __name__ == "__main__":
    main()
