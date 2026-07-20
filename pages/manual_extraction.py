"""Streamlit page for PDF, TXT, bulk TXT, and pasted-text extraction."""

from __future__ import annotations

from typing import Any

import streamlit as st

IMPORT_ERROR: Exception | None = None
try:
    from bulk_email_processor import process_uploaded_txt_file
    from duplicate_detector import normalize_mobile
    from extractor import EmailExtractionEngine
    from services.email_processor import build_customer_record
    from services.customer_service import save_customer
except Exception as exc:  # pragma: no cover
    IMPORT_ERROR = exc


def decode_txt_file(uploaded_file: Any) -> str:
    """Decode an uploaded TXT file safely for standalone page execution."""
    try:
        return uploaded_file.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        return uploaded_file.getvalue().decode("latin-1", errors="ignore")


def decode_pdf_file(uploaded_file: Any) -> str:
    """Extract readable text from an uploaded PDF page by page."""
    from io import BytesIO

    import pdfplumber

    try:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        extracted_pages: list[str] = []
        with pdfplumber.open(BytesIO(uploaded_file.getvalue())) as pdf:
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
    """Score extraction completeness from weighted legacy fields."""
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


def process_pdf_upload(uploaded_file: Any) -> None:
    """Keep the legacy PDF entrypoint available for app.py compatibility."""
    st.session_state.pdf_preview_text = decode_pdf_file(uploaded_file)


@st.cache_resource
def get_extraction_engine() -> EmailExtractionEngine:
    """Return a cached extraction engine."""
    if IMPORT_ERROR is not None:
        raise IMPORT_ERROR
    return EmailExtractionEngine()


def render(
    user_id: str,
    decode_txt_file,
    decode_pdf_file,
    extraction_confidence,
    process_pdf_upload,
) -> None:
    """Render the manual upload and extraction workflow."""
    st.title("Manual Extraction")
    if IMPORT_ERROR is not None:
        st.error(f"Manual Extraction import failed: {IMPORT_ERROR}")
        return
    st.session_state.setdefault("email_text", "")
    st.session_state.setdefault("extracted_customer", {})
    st.session_state.setdefault("pdf_preview_text", "")
    st.session_state.setdefault("txt_preview_text", "")
    st.session_state.setdefault("processed_pdf_signatures", [])

    left, right = st.columns([0.42, 0.58], gap="large")
    with left:
        txt_file = st.file_uploader("Upload TXT file", type=["txt"])
        if txt_file is not None:
            st.session_state.txt_preview_text = decode_txt_file(txt_file)
            with st.expander("TXT extracted text preview", expanded=False):
                st.code(st.session_state.txt_preview_text, language="text")
            if st.button("Extract TXT", use_container_width=True):
                _extract_and_save(user_id, st.session_state.txt_preview_text, "TXT", txt_file.name)

        bulk_txt_file = st.file_uploader("Upload Multiple Emails", type=["txt"], key="bulk_email_upload")
        if bulk_txt_file is not None and st.button("Process Bulk TXT", use_container_width=True):
            _process_bulk(user_id, bulk_txt_file)

        pdf_file = st.file_uploader("Upload PDF file", type=["pdf"])
        if pdf_file is not None and st.button("Extract PDF", use_container_width=True):
            st.session_state.pdf_preview_text = decode_pdf_file(pdf_file)
            if st.session_state.pdf_preview_text.startswith("No readable text found"):
                st.warning(st.session_state.pdf_preview_text)
            else:
                _extract_and_save(user_id, st.session_state.pdf_preview_text, "PDF", pdf_file.name)
        if st.session_state.pdf_preview_text:
            with st.expander("PDF extracted text preview", expanded=False):
                st.code(st.session_state.pdf_preview_text, language="text")

        st.session_state.email_text = st.text_area(
            "Paste Email Text",
            value=st.session_state.email_text,
            height=220,
        )
        if st.button("Extract Manual Text", type="primary", use_container_width=True):
            _extract_and_save(user_id, st.session_state.email_text, "Manual", "")

    with right:
        customer = st.session_state.get("extracted_customer", {})
        confidence = extraction_confidence(customer) if customer else 0
        st.metric("Extraction Confidence", f"{confidence}%")
        st.progress(confidence)
        st.json(customer or {})


def _extract_and_save(user_id: str, text: str, source: str, source_message_id: str) -> None:
    """Extract one manual/uploaded record and save it to SQLite."""
    if not text.strip():
        st.warning("Provide text before extracting.")
        return
    customer = build_customer_record(
        user_id=user_id,
        text=text,
        source=source,
        source_message_id=source_message_id,
        engine=get_extraction_engine(),
    )
    save_customer(customer)
    st.session_state.extracted_customer = customer.to_legacy_dict()
    st.success(f"{source} customer saved as {customer.status}.")


def _process_bulk(user_id: str, uploaded_file: Any) -> None:
    """Process multiple TXT emails through the existing bulk processor."""
    records = process_uploaded_txt_file(uploaded_file, extraction_engine=get_extraction_engine())
    if not records:
        st.warning("No emails were found in the uploaded TXT file.")
        return
    for index, record in enumerate(records, start=1):
        text = "\n".join(str(record.get(field, "")) for field in record)
        customer = build_customer_record(
            user_id=user_id,
            text=text,
            source="TXT",
            source_message_id=f"{uploaded_file.name}#{index}",
            sender_email=str(record.get("email_id", "")),
            engine=get_extraction_engine(),
        )
        if not customer.normalized_mobile:
            customer.normalized_mobile = normalize_mobile(customer.mobile)
        save_customer(customer)
    st.success(f"Processed {len(records)} email(s) from the bulk TXT upload.")


def render_page() -> None:
    """Standalone Streamlit multipage entrypoint."""
    st.set_page_config(page_title="Manual Extraction", page_icon="@", layout="wide")
    try:
        from page_context import ensure_user_safely, initialize_database_safely, selected_user

        if IMPORT_ERROR is not None:
            raise IMPORT_ERROR
        user_id = selected_user()
        initialize_database_safely()
        ensure_user_safely(user_id)
        render(user_id, decode_txt_file, decode_pdf_file, extraction_confidence, process_pdf_upload)
    except Exception as exc:
        st.title("Manual Extraction")
        st.error(f"Manual Extraction failed to render: {exc}")


if __name__ == "__main__":
    render_page()
