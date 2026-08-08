"""Focused regression tests for reliable location and contact-number extraction."""

from __future__ import annotations

import pytest

from extractor import EmailExtractionEngine


@pytest.mark.parametrize(
    ("label", "number"),
    [
        ("Mobile", "9876543210"),
        ("Mob", "+91 9876543210"),
        ("Phone", "+1 212 555 1234"),
        ("Phone No", "011-12345678"),
        ("Contact", "+91-98765-43210"),
        ("Contact No", "9876543210"),
        ("Tel", "+1 212 555 1234"),
        ("Telephone", "+91 9876543210"),
        ("M", "+91-98765-43210"),
        ("T", "+1 212 555 1234"),
    ],
)
def test_labelled_contact_numbers_preserve_readable_format(label: str, number: str) -> None:
    assert EmailExtractionEngine().extract_mobile_numbers(f"{label}: {number}") == [number]


@pytest.mark.parametrize(
    "text",
    [
        "Invoice No: 9876543210",
        "Order No: 9876543210",
        "Date: 12-08-2026",
        "PIN: 560029",
        "GST No: 29ABCDE1234F1Z5",
        "Ticket No: 9876543210",
    ],
)
def test_business_identifiers_are_not_contact_numbers(text: str) -> None:
    assert EmailExtractionEngine().extract_mobile_numbers(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Office: Noida, Uttar Pradesh", "Noida, Uttar Pradesh"),
        ("Address: Bangalore, Karnataka 560029", "Bangalore, Karnataka"),
        ("Location: Mumbai", "Mumbai"),
        ("City: Pune", "Pune"),
        ("Registered Office: Hyderabad, Telangana 500081", "Hyderabad, Telangana"),
        ("Corporate Office: Chennai, Tamil Nadu 600001", "Chennai, Tamil Nadu"),
    ],
)
def test_explicit_location_labels_are_extracted(text: str, expected: str) -> None:
    result = EmailExtractionEngine().extract(text, graph_sender_email="person@example.com")
    assert result["location"] == expected


def test_location_is_derived_from_structured_postal_address() -> None:
    text = """Address:
12 Business Park Road,
Pune, Maharashtra 411001"""
    result = EmailExtractionEngine().extract(text, graph_sender_email="person@example.com")
    assert result["location"] == "Pune, Maharashtra"


def test_missing_location_and_phone_remain_blank() -> None:
    result = EmailExtractionEngine().extract("Thank you for your enquiry.", graph_sender_email="person@example.com")
    assert result["location"] == ""
    assert result["mobile_number"] == ""
