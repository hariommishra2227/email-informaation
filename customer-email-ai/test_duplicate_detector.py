"""Tests for the duplicate detection engine.

This harness provides a mix of exact and fuzzy record scenarios.
"""

from __future__ import annotations

from duplicate_detector import detect_duplicates


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
        "contact_person_name": "Danielle Lee",
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
]


def _assert_status(results: list[dict[str, object]], expected_statuses: list[str]) -> None:
    """Assert representative statuses for the test dataset."""
    for result, expected_status in zip(results, expected_statuses):
        assert result["duplicate_status"] == expected_status, result


def main() -> None:
    """Run the duplicate detection test data and assert representative outcomes."""
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
    ])

    for result in results:
        print(result)


if __name__ == "__main__":
    main()
