from __future__ import annotations

import json
from pathlib import Path

from models import CustomerRecord
from storage import database


def test_provenance_migration_is_idempotent_and_preserves_rows(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "customers.db"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)
    database.initialize_database()
    record_id = database.insert_customer(CustomerRecord(user_id="u", email="a@example.com", review_status="Needs Review"))
    database.initialize_database()
    rows = database.list_customers("u")
    assert rows[0]["id"] == record_id
    assert "email_source" in rows[0]


def test_review_correction_creates_audit_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "customers.db")
    database.initialize_database()
    record_id = database.insert_customer(CustomerRecord(user_id="u", contact_name="Old", email="a@example.com"))
    database.update_customer_review(record_id, {"contact_name": "Corrected", "review_status": "Approved"}, reviewed_by="reviewer", notes="Verified")
    row = database.list_customers("u")[0]
    audit = database.list_review_audit(record_id)
    assert row["contact_name"] == "Corrected"
    assert row["name_source"] == "manual_review"
    assert row["name_confidence"] == 1.0
    assert row["review_status"] == "Approved"
    assert audit[0]["old_value"] == "Old"
    assert audit[0]["new_value"] == "Corrected"


def test_llm_disabled_is_safe(monkeypatch) -> None:
    import llm_extractor

    monkeypatch.setattr(llm_extractor.config, "LLM_ENABLED", False)
    result = llm_extractor.extract_with_llm("Alice at Example")
    assert result["llm_used"] is False
    assert result["fields"]["email"]["value"] == ""


def test_llm_rejects_missing_evidence_and_internal_company(monkeypatch) -> None:
    import llm_extractor

    payload = {field: {"value": "", "evidence": "", "confidence": 0} for field in llm_extractor.FIELDS}
    payload["organisation"] = {"value": "ITSIPL", "evidence": "ITSIPL", "confidence": 0.99}
    payload["customer_name"] = {"value": "Alice Example", "evidence": "not in source", "confidence": 0.9}
    assert llm_extractor._validate(payload, "Alice Example customer") ["organisation"]["value"] == ""
    assert llm_extractor._validate(payload, "Alice Example customer")["customer_name"]["value"] == ""

