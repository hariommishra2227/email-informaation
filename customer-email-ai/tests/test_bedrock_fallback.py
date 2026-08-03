import json
from unittest.mock import Mock, patch

import bedrock_extractor
import config
from llm_extractor import extract_with_llm, reset_call_budget


def test_bedrock_disabled_does_not_call(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    with patch("bedrock_extractor._client") as client:
        assert extract_with_llm("text")["llm_used"] is False
        client.assert_not_called()


def test_bedrock_valid_response_is_evidence_checked(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_PROVIDER", "bedrock")
    reset_call_budget()
    body = Mock()
    body.read.return_value = json.dumps({"content": [{"text": json.dumps({"customer_name": {"value": "Anita Rao", "evidence": "Anita Rao", "confidence": 0.9}})}]}).encode()
    with patch("bedrock_extractor._client") as client:
        client.return_value.invoke_model.return_value = {"body": body}
        result = extract_with_llm("Regards\nAnita Rao", current={"name_confidence": 0.1})
    assert result["fields"]["customer_name"]["value"] == "Anita Rao"


def test_bedrock_failure_returns_local_fallback(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_PROVIDER", "bedrock")
    reset_call_budget()
    with patch("bedrock_extractor._client", side_effect=PermissionError()):
        result = extract_with_llm("text", current={"name_confidence": 0.1})
    assert result["llm_used"] is False
