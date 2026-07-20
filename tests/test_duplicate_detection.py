"""Additional duplicate checks for normalized customer keys."""

from __future__ import annotations

from duplicate_detector import detect_duplicates


def test_normalized_phone_is_used_for_duplicate_checking() -> None:
    """Two differently formatted Indian numbers should be duplicate customers."""
    results = detect_duplicates(
        [
            {
                "contact_person_name": "Amit Verma",
                "email_id": "amit@example.com",
                "organisation_name": "Northstar Technologies",
                "mobile_number": "+91-99887-66554",
                "address": "",
                "designation": "Network Engineer",
            },
            {
                "contact_person_name": "Amit Verma",
                "email_id": "amit.alt@example.com",
                "organisation_name": "Northstar Technologies",
                "mobile_number": "99887 66554",
                "address": "",
                "designation": "Network Engineer",
            },
        ]
    )

    assert [record["duplicate_status"] for record in results] == ["Duplicate", "Duplicate"]
    assert all(record["normalized_phone"] == "9988766554" for record in results)
