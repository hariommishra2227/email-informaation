"""Tests for Microsoft delegated scope detection."""

from __future__ import annotations

import base64
import json

from services import graph_auth


def _unsigned_jwt_with_claims(claims: dict[str, str]) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}."


def test_has_granted_scope_accepts_msal_scope_field_case_insensitively() -> None:
    """MSAL token responses expose granted scopes in the plain scope field."""
    token_result = {"scope": "user.read mail.read offline_access"}

    assert graph_auth.has_granted_scope("Mail.Read", token_result)


def test_granted_scopes_falls_back_to_jwt_scp_claim() -> None:
    """JWT scp can be decoded for diagnostics without signature verification."""
    token_result = {"access_token": _unsigned_jwt_with_claims({"scp": "User.Read Mail.Read"})}

    assert graph_auth.granted_scopes(token_result) == ["Mail.Read", "User.Read"]
    assert graph_auth.has_granted_scope("mail.read", token_result)
