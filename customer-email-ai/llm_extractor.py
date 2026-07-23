"""Optional, strictly validated LLM fallback for unresolved customer fields."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

import config

LOGGER = logging.getLogger(__name__)
FIELDS = ("customer_name", "email", "organisation", "mobile", "designation", "address")
CALLS_THIS_RUN = 0


def reset_call_budget() -> None:
    """Reset the optional LLM call budget at the start of a processing run."""
    global CALLS_THIS_RUN
    CALLS_THIS_RUN = 0


def _empty() -> dict[str, Any]:
    return {field: {"value": "", "evidence": "", "confidence": 0.0} for field in FIELDS}


def _valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value.strip()))


def _validate(payload: Any, source: str) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    result = _empty()
    aliases = {"customer_name": "customer_name", "email": "email", "organisation": "organisation", "mobile": "mobile", "designation": "designation", "address": "address"}
    for field, key in aliases.items():
        item = payload.get(key, {})
        if not isinstance(item, dict):
            continue
        value, evidence = str(item.get("value") or "").strip(), str(item.get("evidence") or "").strip()
        confidence = float(item.get("confidence", 0) or 0)
        if not 0 <= confidence <= 1:
            raise ValueError(f"Invalid confidence for {field}")
        if value and (not evidence or evidence not in source):
            continue
        if value and field == "email" and (not _valid_email(value) or config.is_internal_email(value)):
            continue
        if value and field == "organisation" and config.is_internal_company(value):
            continue
        result[field] = {"value": value, "evidence": evidence, "confidence": confidence}
    return result


def extract_with_llm(cleaned_text: str, current: dict[str, Any] | None = None, *, sender_email: str = "") -> dict[str, Any]:
    """Call the configured provider only when enabled and credentials exist."""
    global CALLS_THIS_RUN
    if not config.LLM_ENABLED or (config.LLM_PROVIDER.lower() != "bedrock" and not config.LLM_API_KEY):
        return {"fields": _empty(), "llm_used": False, "llm_model": config.LLM_MODEL, "llm_error": "disabled_or_missing_api_key"}
    if current:
        confidences = [float(current.get(key, 0) or 0) for key in ("name_confidence", "email_confidence", "organisation_confidence", "mobile_confidence", "designation_confidence")]
        if confidences and min(confidences) >= (config.BEDROCK_CONFIDENCE_THRESHOLD if config.LLM_PROVIDER.lower() == "bedrock" else 1.1):
            return {"fields": _empty(), "llm_used": False, "llm_model": config.LLM_MODEL, "llm_error": "high_confidence_local_result"}
    if CALLS_THIS_RUN >= config.LLM_MAX_CALLS_PER_RUN:
        return {"fields": _empty(), "llm_used": False, "llm_model": config.LLM_MODEL, "llm_error": "call_limit_reached"}
    CALLS_THIS_RUN += 1
    source = str(cleaned_text or "")[:(config.BEDROCK_MAX_INPUT_CHARS if config.LLM_PROVIDER.lower() == "bedrock" else config.LLM_MAX_INPUT_CHARS)]
    prompt = (
        "Return JSON only using keys customer_name,email,organisation,mobile,designation,address. "
        "Each value must have value,evidence,confidence. Extract only explicit evidence in this text; "
        "never invent, use ITSIPL details, quoted history, or replace the validated external sender email.\n\n" + source
    )
    if config.LLM_PROVIDER.lower() == "bedrock":
        from bedrock_extractor import extract_with_bedrock
        return extract_with_bedrock(source, sender_email=sender_email)
    if config.LLM_PROVIDER.lower() != "openai":
        return {"fields": _empty(), "llm_used": False, "llm_model": config.LLM_MODEL, "llm_error": "unsupported_provider"}
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": config.LLM_MODEL, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": "You extract customer contacts safely."}, {"role": "user", "content": prompt}]},
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        fields = _validate(json.loads(content), source)
        if sender_email and fields["email"]["value"] and fields["email"]["value"].lower() != sender_email.strip().lower():
            fields["email"] = _empty()["email"]
        return {"fields": fields, "llm_used": True, "llm_model": config.LLM_MODEL, "llm_error": ""}
    except Exception as exc:  # one failed fallback must never stop a batch
        LOGGER.warning("Optional LLM extraction failed: %s", exc.__class__.__name__)
        return {"fields": _empty(), "llm_used": False, "llm_model": config.LLM_MODEL, "llm_error": exc.__class__.__name__}
