"""Test harness for the customer email extraction engine.

This module prints the extraction result for ten realistic business email cases.
"""

from __future__ import annotations

import json

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


def main() -> None:
    """Execute the test cases and print the extracted JSON for each scenario."""
    engine = EmailExtractionEngine()

    for title, email_text in TEST_CASES:
        print(f"\n--- {title} ---")
        extracted = engine.extract(email_text)
        print(json.dumps(extracted, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
