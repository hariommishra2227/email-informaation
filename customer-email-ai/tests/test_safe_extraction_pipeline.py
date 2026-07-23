from extractor import EmailExtractionEngine


def test_cleaning_removes_warning_and_old_reply():
    e = EmailExtractionEngine()
    text = "Warning! This message was sent from outside your organization\nHello\nRegards\nAnita Rao\n\n-----Original Message-----\nFrom: old@example.com"
    cleaned = e.clean_email_content(text)
    assert "Warning" not in cleaned and "old@example.com" not in cleaned
    assert "Anita Rao" in e.extract_signature(cleaned)


def test_subject_is_not_customer_name_and_signature_fields_are_used():
    result = EmailExtractionEngine().extract(
        "Subject: Payment Details\nHello\nRegards\nAnita Rao\nSales Manager\nAcme Technologies\nMobile: +91 9876543210",
        graph_sender_email="anita@acme.example",
        graph_sender_name="Anita Rao",
    )
    assert result["customer_name"] == "Anita Rao"
    assert result["mobile_number"] == "+91 9876543210"
    assert result["designation"] == "Sales Manager"


def test_po_and_free_email_are_not_mobile_or_organisation():
    result = EmailExtractionEngine().extract(
        "PO Number: 9876543210\nRegards\nUnknown Sender",
        graph_sender_email="person@gmail.com",
    )
    assert result["mobile_number"] == ""
    assert result["organisation_name"] == ""


def test_business_domain_can_supply_organisation():
    result = EmailExtractionEngine().extract("Hello", graph_sender_email="person@northwind.example")
    assert result["organisation_name"] == "Northwind"

