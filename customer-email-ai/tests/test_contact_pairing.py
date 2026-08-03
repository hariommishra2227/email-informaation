from extractor import EmailExtractionEngine


def test_contact_pairing_regressions():
    engine = EmailExtractionEngine()
    cases = [
        ("Rahul Sharma\nrahul.sharma@abc.com", "Rahul Sharma", "rahul.sharma@abc.com"),
        ("Neha Verma\nSales Manager\nAcme Ltd\nsales@abc.com", "Neha Verma", "sales@abc.com"),
        ("Please write to sales@abc.com", "", "sales@abc.com"),
        ("Old Name\nold@example.com\n\nCurrent Name\ncurrent@example.com", "Current Name", "current@example.com"),
        ("New Name\nnew@example.com\n\n---------- Forwarded message ----------\nOld Name\nold@example.com", "New Name", "new@example.com"),
        ("Wrong Name\n\nright@example.com", "", "right@example.com"),
        ("R Sharma\nr.sharma@example.com", "R Sharma", "r.sharma@example.com"),
        ("Priya Menon\nunrelated@example.com", "", "unrelated@example.com"),
    ]
    for body, expected_name, expected_email in cases:
        result = engine.extract(body)
        assert result["email_id"] == expected_email
        assert result["contact_person_name"] == expected_name


def test_two_contacts_remain_separate_records():
    contacts = EmailExtractionEngine().extract_contacts(
        "Asha Rao\nasha@example.com\n\nVikram Das\nvikram@example.com"
    )
    assert [(c["name"], c["email"]) for c in contacts] == [
        ("Asha Rao", "asha@example.com"), ("Vikram Das", "vikram@example.com")
    ]
    assert all("confidence" in contact for contact in contacts)
