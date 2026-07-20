"""Focused tests for Streamlit extraction workflow helpers."""

from __future__ import annotations

import sys
from types import SimpleNamespace


class SessionStateStub(dict):
    """Mapping-compatible session state with Streamlit-style attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


streamlit_stub = SimpleNamespace(
    cache_resource=lambda function=None, **_: function if function is not None else (lambda wrapped: wrapped),
    session_state=SessionStateStub(),
)
sys.modules.setdefault("streamlit", streamlit_stub)

import app


class UploadedFileStub:
    """Small uploaded-file stand-in for app helper tests."""

    name = "sample.pdf"
    size = 123


def test_extraction_confidence_uses_weighted_fields() -> None:
    """Extraction confidence should reflect only fields actually found."""
    customer = {
        "email_id": "rajesh.kumar@abctech.com",
        "contact_person_name": "Rajesh Kumar",
        "organisation_name": "ABC Technologies Pvt. Ltd.",
        "mobile_number": "+91 9876543210",
        "designation": "IT Manager",
        "subject": "Request for Endpoint Security Quotation",
        "address": "",
    }

    assert app.extraction_confidence(customer) == 95
    customer["address"] = "Sector 62, Noida, Uttar Pradesh"
    assert app.extraction_confidence(customer) == 100


def test_pdf_upload_does_not_touch_manual_text_and_runs_once(monkeypatch) -> None:
    """PDF processing should not copy text into the manual textarea or rerun the same PDF."""
    session_state = SimpleNamespace(
        email_text="",
        pdf_preview_text="",
        processed_pdf_signatures=[],
        extracted_customer={},
        customers=[],
        emails_processed=0,
        last_status="",
        message=None,
    )
    extracted_sources: list[str] = []

    monkeypatch.setattr(app.st, "session_state", session_state)
    monkeypatch.setattr(app, "decode_pdf_file", lambda uploaded_file: "Email: rajesh.kumar@abctech.com")

    def fake_extract_customer(email_text: str, input_source: str) -> None:
        extracted_sources.append(input_source)
        session_state.extracted_customer = {
            "email_id": "rajesh.kumar@abctech.com",
            "input_source": input_source,
        }
        session_state.customers.append(session_state.extracted_customer)
        session_state.emails_processed += 1

    monkeypatch.setattr(app, "extract_customer", fake_extract_customer)

    uploaded_file = UploadedFileStub()
    app.process_pdf_upload(uploaded_file)
    app.process_pdf_upload(uploaded_file)

    assert session_state.email_text == ""
    assert session_state.pdf_preview_text == "Email: rajesh.kumar@abctech.com"
    assert extracted_sources == ["PDF"]
    assert len(session_state.customers) == 1
