"""Optional, evidence-constrained Amazon Bedrock extraction provider."""

from __future__ import annotations

import json
import logging
from typing import Any

import config

LOGGER = logging.getLogger(__name__)
FIELDS = ("customer_name", "email", "organisation", "mobile_number", "designation", "address")


def _empty() -> dict[str, Any]:
    return {field: {"value": "", "evidence": "", "confidence": 0.0} for field in FIELDS}


def _client():
    import boto3
    return boto3.client("bedrock-runtime", region_name=config.AWS_REGION,
                        config=__import__("botocore.config", fromlist=["Config"]).Config(
                            read_timeout=config.BEDROCK_TIMEOUT_SECONDS, connect_timeout=5,
                            retries={"max_attempts": 2, "mode": "standard"}))


def check_bedrock_configuration(*, invoke: bool = False) -> dict[str, Any]:
    """Check configuration/client creation; invoke only when explicitly requested."""
    if not config.AWS_REGION or not config.BEDROCK_MODEL_ID:
        return {"ok": False, "message": "Bedrock configuration is incomplete."}
    try:
        client = _client()
        if invoke:
            client.invoke_model(modelId=config.BEDROCK_MODEL_ID, body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1, "messages": [{"role": "user", "content": "Return {}"}]}), contentType="application/json", accept="application/json")
        return {"ok": True, "message": "Bedrock is configured."}
    except Exception as exc:
        LOGGER.warning("Bedrock configuration check failed: %s", exc.__class__.__name__)
        return {"ok": False, "message": "Bedrock is unavailable or access is denied."}


def extract_with_bedrock(cleaned_text: str, *, sender_email: str = "") -> dict[str, Any]:
    source = str(cleaned_text or "")[:config.BEDROCK_MAX_INPUT_CHARS]
    result = {"fields": _empty(), "llm_used": False, "llm_model": config.BEDROCK_MODEL_ID, "llm_error": ""}
    prompt = "Return JSON only with keys customer_name,email,organisation,mobile_number,designation,address. Each value must be an object with value,evidence,confidence. Use only exact evidence from the latest email below; never infer or invent.\n\n" + source
    try:
        response = _client().invoke_model(modelId=config.BEDROCK_MODEL_ID, body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 700, "temperature": 0, "messages": [{"role": "user", "content": prompt}]}), contentType="application/json", accept="application/json")
        payload = json.loads(response["body"].read() if hasattr(response["body"], "read") else response["body"])
        content = payload.get("content", [{}])[0].get("text", "")
        data = json.loads(content)
        if set(data) - set(FIELDS):
            raise ValueError("unexpected_fields")
        for field in FIELDS:
            item = data.get(field, {})
            if not isinstance(item, dict):
                continue
            value, evidence = str(item.get("value") or "").strip(), str(item.get("evidence") or "").strip()
            confidence = float(item.get("confidence", 0) or 0)
            if value and evidence and evidence in source and 0 <= confidence <= 1:
                result["fields"][field] = {"value": value, "evidence": evidence, "confidence": confidence}
        result["llm_used"] = True
    except Exception as exc:
        result["llm_error"] = exc.__class__.__name__
        LOGGER.warning("Optional Bedrock extraction failed: %s", exc.__class__.__name__)
    return result
