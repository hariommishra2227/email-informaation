"""Regression tests for evidence-based customer identity extraction."""

from __future__ import annotations

from pathlib import Path

import config
from extractor import EmailExtractionEngine
from excel_exporter import export_customers_to_excel
from services.email_processor import build_customer_record
from openpyxl import load_workbook


def test_generic_domain_labels_are_never_client_names() -> None:
    engine = EmailExtractionEngine()
    cases = {
        "ibm@email.ibm.com": "Email",
        "marketing@interest.skyhighsecurity.com": "Interest",
        "notice@mail.example.org": "Mail",
        "alert@notifications.example.org": "Notifications",
    }
    for sender, forbidden in cases.items():
        result = engine.extract("Hello", graph_sender_email=sender)
        assert result["organisation_name"] != forbidden
        assert result["organisation_name"] == ""


def test_explicit_company_evidence_is_preserved_without_domain_guessing() -> None:
    result = EmailExtractionEngine().extract(
        "Regards\nShiju Chacko\nCompany: Skyhigh Security",
        graph_sender_email="marketing@interest.skyhighsecurity.com",
        graph_sender_name="Marketing Team",
    )
    assert result["organisation_name"] == "Skyhigh Security"
    assert result["contact_person_name"] == "Shiju Chacko"


def test_company_and_team_display_names_are_not_people() -> None:
    engine = EmailExtractionEngine()
    for label in ("Zoho Team", "Marketing Team", "Rashi Peripherals"):
        result = engine.extract("Hello", graph_sender_email="info@example.org", graph_sender_name=label)
        assert result["contact_person_name"] == ""


def test_generic_and_automated_addresses_do_not_create_fake_people() -> None:
    engine = EmailExtractionEngine()
    for sender, label in (
        ("no.reply@example.org", "Notifications Team"),
        ("noreply@example.org", "Support Team"),
        ("marketing@example.org", "Marketing Team"),
        ("support@example.org", "Support Team"),
    ):
        result = engine.extract("Hello", graph_sender_email=sender, graph_sender_name=label)
        assert result["contact_person_name"] == ""
        assert result["email"] == sender


def test_structured_postal_address_provides_full_address_and_location() -> None:
    text = """Regards
IBM India,
No.12, Subramanya Arcade,
Bannerghatta Main Road,
Bangaluru - India - 560 029"""
    result = EmailExtractionEngine().extract(text, graph_sender_email="ibm@email.ibm.com", graph_sender_name="IBM")
    assert "No.12, Subramanya Arcade" in result["address"]
    assert "Bannerghatta Main Road" in result["address"]
    assert result["location"] == "Bangaluru"
    assert result["contact_person_name"] == ""


def test_missing_contact_details_remain_blank() -> None:
    result = EmailExtractionEngine().extract("Hello", graph_sender_email="info@example.org", graph_sender_name="Support")
    assert result["mobile_number"] == ""
    assert result["address"] == ""
    assert result["location"] == ""


def test_graph_subject_and_received_date_remain_authoritative(monkeypatch) -> None:
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    monkeypatch.setattr("services.email_processor._status_for_customer", lambda *args: "Unique")
    record = build_customer_record(
        user_id="u", text="Subject: Body subject", source="Outlook",
        sender_email="person@example.org", sender_name="Shiju Chacko",
        subject="Graph subject", received_datetime="2026-08-08T10:15:00Z",
    )
    assert record.subject == "Graph subject"
    assert record.email_date == "08-Aug-2026 10:15"


def test_azure_and_microsoft_configuration_alias_precedence() -> None:
    assert config._resolve_alias("azure-tenant", "microsoft-tenant") == "azure-tenant"
    assert config._resolve_alias("", "microsoft-tenant") == "microsoft-tenant"
    assert config._resolve_alias("", "", "fallback") == "fallback"


def test_inbox_preview_columns_never_become_customer_export_columns() -> None:
    raw_inbox_row = {
        "Select": True, "Sender": "Sender", "Sender Email": "sender@example.org",
        "Status": "Unread", "Processing": "Pending", "Attachment": "No", "Message ID": "m1",
    }
    workbook = load_workbook(export_customers_to_excel([raw_inbox_row]), read_only=True)
    headers = list(next(workbook.active.values))
    assert headers == [
        "Client Name", "Contact Person Name", "Contact Email", "Phone Number",
        "Full Address", "Location", "Subject", "Email Date",
    ]
    assert not set(raw_inbox_row) & set(headers)


def test_outlook_page_labels_inbox_as_preview_and_customer_export() -> None:
    source = (Path(__file__).resolve().parents[1] / "pages" / "Outlook Connector.py").read_text(encoding="utf-8")
    assert "Inbox Preview — Select Emails for Extraction" in source
    assert "Preview/raw mailbox data only" in source
    assert "Customer Records" in source
    assert "Download Customer Excel" in source
    assert "Extract customer emails first to enable Excel download." in source
    assert 'create_large_excel_export(user_id, source="Outlook")' in source
