"""Test harness for the customer email extraction engine.

This module prints the extraction result for ten realistic business email cases.
"""

from __future__ import annotations

import json
from io import BytesIO

import pdfplumber

from extractor import EmailExtractionEngine


TEST_CASES: list[tuple[str, str]] = [
    (
        "Normal signature",
        """
        Hello,
        I am interested in your services.
        Best regards,
        Sarah Johnson
        Acme Solutions
        sarah@acmesolutions.com
        +1 555 123 4567
        IT Manager
        123 Market Street, Dallas, TX
        """,
    ),
    (
        "HTML email",
        """
        <html><body><p>Hi Team,</p><p>My name is Daniel Lee and I am the Purchase Manager at Northwind Retail.</p><p>Email: daniel.lee@northwindretail.com</p><p>Phone: +1 555 765 4321</p><p>Office: 87 River Rd, Seattle, WA</p></body></html>
        """,
    ),
    (
        "Missing phone",
        """
        Dear Support,
        This is Priya Menon from Delta Systems.
        Please contact me at priya@deltasystems.com.
        Regards,
        Priya Menon
        Project Manager
        40 Harbor Avenue, New York, NY
        """,
    ),
    (
        "Missing designation",
        """
        Hello,
        My name is Andrew Smith.
        I work at Bluebird Logistics.
        Email: andrew@bluebirdlogistics.com
        Mobile: +1 555 444 2222
        88 Elm Street, Chicago, IL
        """,
    ),
    (
        "Missing address",
        """
        Hi,
        I am Jennifer Wang, Sales Manager at Zenith Industries.
        Contact me at jennifer.wang@zenithindustries.com or +44 203 555 0147.
        """,
    ),
    (
        "Forwarded email",
        """
        ---------- Forwarded message ----------
        From: Michael Brown <michael@anchorglobal.com>
        Subject: Vendor inquiry
        Michael Brown
        CEO
        +1 555 777 8888
        12 Atlantic Avenue, Boston, MA
        """,
    ),
    (
        "Reply email",
        """
        Re: Requisition details
        Thanks,
        Robert Green
        robert.green@freshbridge.com
        Director
        300 Pine Lane, Austin, TX
        """,
    ),
    (
        "Multiple email addresses",
        """
        Hello,
        Please reach me at lisa.morris@orbitaltech.com or lisa@orbitaltech.com.
        I am the Network Engineer at Orbital Technologies.
        Phone: +1 555 111 2233.
        16 Broadway, Denver, CO
        """,
    ),
    (
        "Multiple phone numbers",
        """
        Hello,
        I am Elaine Hart, Business Development Manager at Verde Ventures.
        Office: +1 555 987 6543
        Mobile: +1 555 222 3334
        Email: elaine@verdeventures.com
        9 Maple Court, Portland, OR
        """,
    ),
    (
        "International phone number",
        """
        Dear team,
        I am Omar Rahman, System Administrator at Apex Telecom.
        Contact: omar@apextelecom.com
        Phone: +44 20 7946 0958
        21 Queen Street, London, UK
        """,
    ),
]

PHONE_TEST_CASES: list[tuple[str, str, str]] = [
    (
        "Indian mobile with country code",
        "Hello,\nMobile: +91 9876543210\nRegards,\nAnita Rao",
        "+91 9876543210",
    ),
    (
        "Indian mobile with country code and hyphens",
        "Hello,\nMobile: +91-98765-43210\nRegards,\nAnita Rao",
        "+91-98765-43210",
    ),
    (
        "Indian mobile without country code",
        "Hello,\nMobile: 9876543210\nRegards,\nAnita Rao",
        "9876543210",
    ),
    (
        "Indian phone with hyphen",
        "Hello,\nPhone: 98765-43210\nRegards,\nAnita Rao",
        "98765-43210",
    ),
    (
        "Indian phone with space",
        "Hello,\nPhone: 98765 43210\nRegards,\nAnita Rao",
        "98765 43210",
    ),
    (
        "Indian phone with parentheses",
        "Hello,\nPhone: (98765) 43210\nRegards,\nAnita Rao",
        "(98765) 43210",
    ),
    (
        "Indian contact with leading zero",
        "Hello,\nContact: 09876543210\nRegards,\nAnita Rao",
        "09876543210",
    ),
    (
        "International phone",
        "Hello,\nTel: +1 415 555 2671\nRegards,\nSam Taylor",
        "+1 415 555 2671",
    ),
    (
        "No phone number",
        "Hello,\nPlease reach me at no.phone@example.com.\nRegards,\nSam Taylor",
        "",
    ),
    (
        "Date and invoice only",
        "Invoice 9876543210 is dated 15/07/2026. GST: 29ABCDE1234F1Z5.",
        "",
    ),
]


def test_mobile_number_extraction() -> None:
    """Verify reliable customer mobile number extraction."""
    engine = EmailExtractionEngine()

    for title, email_text, expected_mobile in PHONE_TEST_CASES:
        extracted = engine.extract(email_text)
        assert extracted["mobile_number"] == expected_mobile, title
        if expected_mobile:
            assert extracted["normalized_phone"], title


def test_email_only_record_is_accepted() -> None:
    """A record should still be accepted when only an email is present."""
    engine = EmailExtractionEngine()
    extracted = engine.extract("Please contact priya@deltasystems.com for details.")

    assert extracted["email_id"] == "priya@deltasystems.com"
    assert extracted["contact_person_name"] == ""
    assert extracted["organisation_name"] == ""
    assert extracted["mobile_number"] == ""
    assert extracted["address"] == ""
    assert extracted["designation"] == ""


def test_name_and_email_are_extracted() -> None:
    """Name and email should be extracted even when the email is written in a normal business format."""
    engine = EmailExtractionEngine()
    extracted = engine.extract(
        "Contact: Rajesh Kumar\nEmail: rajesh.kumar@abctech.com\nRegards,\nRajesh Kumar"
    )

    assert extracted["contact_person_name"] == "Rajesh Kumar"
    assert extracted["email_id"] == "rajesh.kumar@abctech.com"


def test_company_email_and_phone_are_extracted() -> None:
    """A normal business email should populate company, email, and phone even when labels vary."""
    engine = EmailExtractionEngine()
    extracted = engine.extract(
        "Rajesh Kumar\nIT Manager\nABC Technologies Pvt. Ltd.\nEmail: rajesh.kumar@abctech.com\nMobile: +91 9876543210"
    )

    assert extracted["contact_person_name"] == "Rajesh Kumar"
    assert extracted["organisation_name"] == "ABC Technologies Pvt. Ltd."
    assert extracted["email_id"] == "rajesh.kumar@abctech.com"
    assert extracted["mobile_number"] == "+91 9876543210"
    assert extracted["normalized_phone"] == "9876543210"


def test_pdf_sample_business_email_extracts_one_customer() -> None:
    """The PDF text sample should parse into one email-backed customer record."""
    engine = EmailExtractionEngine()
    extracted = engine.extract(
        """
        Sample Customer Email

        Subject: Request for Endpoint Security Quotation

        Rajesh Kumar
        IT Manager
        ABC Technologies Pvt. Ltd.

        Email: rajesh.kumar@abctech.com
        Mobile: +91 9876543210
        Address: Sector 62, Noida, Uttar Pradesh
        """
    )

    assert {
        key: extracted[key]
        for key in (
            "contact_person_name",
            "email_id",
            "organisation_name",
            "mobile_number",
            "normalized_phone",
            "address",
            "designation",
            "subject",
        )
    } == {
        "contact_person_name": "Rajesh Kumar",
        "email_id": "rajesh.kumar@abctech.com",
        "organisation_name": "ABC Technologies Pvt. Ltd.",
        "mobile_number": "+91 9876543210",
        "normalized_phone": "9876543210",
        "address": "Sector 62, Noida, Uttar Pradesh",
        "designation": "IT Manager",
        "subject": "Request for Endpoint Security Quotation",
    }
    assert extracted["customer_name"] == "Rajesh Kumar"
    assert extracted["name"] == "Rajesh Kumar"
    assert extracted["company"] == "ABC Technologies Pvt. Ltd."
    assert extracted["email"] == "rajesh.kumar@abctech.com"
    assert extracted["phone"] == "+91 9876543210"


def test_pdf_text_is_extracted_from_in_memory_pdf_bytes() -> None:
    """A valid PDF file body should yield readable text that can be joined across pages."""
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n"
        b"<< /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj\n"
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n"
        b"endobj\n"
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
        b"4 0 obj\n"
        b"<< /Length 74 >>\n"
        b"stream\n"
        b"BT\n"
        b"/F1 12 Tf\n"
        b"72 72 Td\n"
        b"(Customer Name: Rajesh Kumar Email: rajesh.kumar@abctech.com) Tj\n"
        b"ET\n"
        b"endstream\n"
        b"endobj\n"
        b"5 0 obj\n"
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 6\n"
        b"0000000000 65535 f \n"
        b"0000000010 00000 n \n"
        b"0000000063 00000 n \n"
        b"0000000124 00000 n \n"
        b"0000000248 00000 n \n"
        b"0000000360 00000 n \n"
        b"trailer\n"
        b"<< /Root 1 0 R /Size 6 >>\n"
        b"startxref\n"
        b"433\n"
        b"%%EOF\n"
    )

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        page_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    assert "rajesh.kumar@abctech.com" in page_text
    assert "Rajesh Kumar" in page_text


def main() -> None:
    """Execute the test cases and print the extracted JSON for each scenario."""
    engine = EmailExtractionEngine()
    test_mobile_number_extraction()

    for title, email_text in TEST_CASES:
        print(f"\n--- {title} ---")
        extracted = engine.extract(email_text)
        print(json.dumps(extracted, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
