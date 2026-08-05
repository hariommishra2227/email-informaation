"""Authenticated encryption for persisted MSAL token caches."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

import config


PREFIX = "fernet:v1:"


def _fernet() -> Fernet | None:
    key = config.TOKEN_CACHE_ENCRYPTION_KEY
    if not key:
        if config.APP_ENV == "production":
            raise RuntimeError("TOKEN_CACHE_ENCRYPTION_KEY is required in production.")
        return None
    try:
        return Fernet(key.encode("utf-8"))
    except ValueError:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_cache(cache_json: str) -> str:
    """Encrypt a serialized MSAL cache, or keep plaintext only outside production."""
    if not cache_json:
        return ""
    fernet = _fernet()
    if fernet is None:
        return cache_json
    return PREFIX + fernet.encrypt(cache_json.encode("utf-8")).decode("utf-8")


def decrypt_cache(stored_value: str) -> tuple[str, bool]:
    """Return decrypted cache JSON and whether plaintext migration is needed."""
    if not stored_value:
        return "", False
    if not stored_value.startswith(PREFIX):
        if config.APP_ENV == "production" and not config.TOKEN_CACHE_ENCRYPTION_KEY:
            raise RuntimeError("Encrypted token cache requires TOKEN_CACHE_ENCRYPTION_KEY in production.")
        return stored_value, bool(config.TOKEN_CACHE_ENCRYPTION_KEY)
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError("TOKEN_CACHE_ENCRYPTION_KEY is required to read encrypted token cache.")
    try:
        return fernet.decrypt(stored_value[len(PREFIX):].encode("utf-8")).decode("utf-8"), False
    except InvalidToken as exc:
        raise RuntimeError("Token cache could not be decrypted with the configured key.") from exc
