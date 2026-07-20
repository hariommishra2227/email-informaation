"""Tests for the duplicate detection engine.

This harness provides a mix of exact and fuzzy record scenarios.
"""

from __future__ import annotations

from duplicate_detector import detect_duplicates, normalize_email, normalize_mobile


TEST_RECORDS = [
    {
        "contact_person_name": "Sarah Johnson",
        "email_id": "sarah@acmesolutions.com",
        "organisation_name": "Acme Solutions",
        "mobile_number": "+91 98765 43210",
        "address": "123 Market Street, Dallas, TX",
        "designation": "IT Manager",
    },
    {
        "contact_person_name": "Sarah Johnson",
        "email_id": "sarah@acmesolutions.com",
        "organisation_name": "Acme Solutions",
        "mobile_number": "+91 98765 43210",
        "address": "123 Market Street, Dallas, TX",
        "designation": "IT Manager",
    },
    {
        "contact_person_name": "Priya Menon",
        "email_id": "priya@deltasystems.com",
        "organisation_name": "Delta Systems",
        "mobile_number": "+91 99887 66554",
        "address": "40 Harbor Avenue, New York, NY",
        "designation": "Project Manager",
    },
    {
        "contact_person_name": "Priya Menon",
        "email_id": "priya.menon@deltasystems.com",
        "organisation_name": "Delta Systems",
        "mobile_number": "99887-66554",
        "address": "40 Harbor Avenue, New York, NY",
        "designation": "Project Manager",
    },
    {
        "contact_person_name": "Daniel Lee",
        "email_id": "daniel.lee@northwindretail.com",
        "organisation_name": "Northwind Retail",
        "mobile_number": "+1 555 765 4321",
        "address": "87 River Rd, Seattle, WA",
        "designation": "Purchase Manager",
    },
    {
        "contact_person_name": "Daniel Le",
        "email_id": "danielle.lee@northwindretail.com",
        "organisation_name": "Northwind Retail",
        "mobile_number": "+1 555 987 1111",
        "address": "87 River Rd, Seattle, WA",
        "designation": "Purchase Manager",
    },
    {
        "contact_person_name": "John Smith",
        "email_id": "john.smith@verdeventures.com",
        "organisation_name": "Verde Ventures",
        "mobile_number": "+44 203 555 0147",
        "address": "9 Maple Court, Portland, OR",
        "designation": "Business Development Manager",
    },
    {
        "contact_person_name": "John Smyth",
        "email_id": "john.smyth@verdeventures.com",
        "organisation_name": "Verde Ventures",
        "mobile_number": "+44 203 555 0148",
        "address": "9 Maple Court, Portland, OR",
        "designation": "Business Development Manager",
    },
    {
        "contact_person_name": "Omar Rahman",
        "email_id": "omar@apextelecom.com",
        "organisation_name": "Apex Telecom",
        "mobile_number": "+44 20 7946 0958",
        "address": "21 Queen Street, London, UK",
        "designation": "System Administrator",
    },
    {
        "contact_person_name": "Omar Rahman",
        "email_id": "omar.rahman@apextelecom.com",
        "organisation_name": "Apex Telecom",
        "mobile_number": "+44 20 7946 0959",
        "address": "21 Queen Street, London, UK",
        "designation": "System Administrator",
    },
    {
        "contact_person_name": "Sofia Martinez",
        "email_id": "sofia@orbitalsystems.com",
        "organisation_name": "Orbital Systems",
        "mobile_number": "+1 555 222 3334",
        "address": "16 Broadway, Denver, CO",
        "designation": "Sales Manager",
    },
    {
        "contact_person_name": "Sofia Martinez",
        "email_id": "sofia.martinez@orbitalsystems.com",
        "organisation_name": "Orbital Systems",
        "mobile_number": "+1 555 222 3334",
        "address": "16 Broadway, Denver, CO",
        "designation": "Sales Manager",
    },
    {
        "contact_person_name": "Liam White",
        "email_id": "liam.white@bluebirdlogistics.com",
        "organisation_name": "Bluebird Logistics",
        "mobile_number": "+1 555 444 2222",
        "address": "88 Elm Street, Chicago, IL",
        "designation": "Director",
    },
    {
        "contact_person_name": "Michelle White",
        "email_id": "michelle.white@bluebirdlogistics.com",
        "organisation_name": "Bluebird Logistics",
        "mobile_number": "+1 555 444 2223",
        "address": "88 Elm Street, Chicago, IL",
        "designation": "Director",
    },
    {
        "contact_person_name": "Emily Carter",
        "email_id": "emily.carter@northstartech.com",
        "organisation_name": "Northstar Tech",
        "mobile_number": "+1 555 333 4444",
        "address": "12 Harbor Street, Boston, MA",
        "designation": "System Administrator",
    },
    {
        "contact_person_name": "",
        "email_id": "",
        "organisation_name": "",
        "mobile_number": "",
        "address": "",
        "designation": "",
    },
]


def _assert_status(results: list[dict[str, object]], expected_statuses: list[str]) -> None:
    """Assert representative statuses for the test dataset."""
    for result, expected_status in zip(results, expected_statuses):
        assert result["duplicate_status"] == expected_status, result


def test_normalize_email_removes_spaces_and_lowercases() -> None:
    """Email normalization removes internal spaces and lowercases the value."""
    assert normalize_email(" Sarah @AcmeSolutions.COM ") == "sarah@acmesolutions.com"


def test_normalize_mobile_uses_last_10_digits() -> None:
    """Mobile normalization removes separators and compares the last 10 digits."""
    assert normalize_mobile("+91 98765-43210") == "9876543210"
    assert normalize_mobile("+1 555 765 4321") == "5557654321"


def test_detect_duplicates_for_sample_customer_records() -> None:
    """Detect exact duplicates, fuzzy duplicates, unique records, and blanks."""
    results = detect_duplicates(TEST_RECORDS)
    _assert_status(results, [
        "Duplicate",
        "Duplicate",
        "Duplicate",
        "Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Duplicate",
        "Duplicate",
        "Unique",
        "Unique",
        "Unique",
        "Unique",
    ])

    assert len(results) == 16
    assert results[0]["mobile_number"] == "+91 98765 43210"
    assert results[0]["normalized_phone"] == "9876543210"
    assert all("confidence_score" in result for result in results)
    assert all(
        result["confidence_score"] == 100
        for result in results
        if result["duplicate_status"] == "Duplicate"
    )
    assert all(
        result["confidence_score"] >= 85
        for result in results
        if result["duplicate_status"] == "Possible Duplicate"
    )
    assert all(
        result["confidence_score"] == 0
        for result in results
        if result["duplicate_status"] == "Unique"
    )


def test_detect_duplicates_rejects_invalid_input() -> None:
    """Invalid input raises clear errors."""
    try:
        detect_duplicates("not a list")  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("TypeError was not raised for non-list input")

    try:
        detect_duplicates([{"email_id": "missing-fields@example.com"}])
    except KeyError:
        pass
    else:
        raise AssertionError("KeyError was not raised for missing record fields")


def main() -> None:
    """Run the duplicate detection test data and assert representative outcomes."""
    test_normalize_email_removes_spaces_and_lowercases()
    test_normalize_mobile_uses_last_10_digits()
    test_detect_duplicates_for_sample_customer_records()
    test_detect_duplicates_rejects_invalid_input()

    results = detect_duplicates(TEST_RECORDS)
    _assert_status(results, [
        "Duplicate",
        "Duplicate",
        "Duplicate",
        "Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Possible Duplicate",
        "Duplicate",
        "Duplicate",
        "Unique",
        "Unique",
        "Unique",
        "Unique",
    ])

    for result in results:
        print(result)


if __name__ == "__main__":
    main()
