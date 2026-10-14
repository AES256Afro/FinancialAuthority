"""Field-level encryption for secrets kept in the database: AES-GCM keyed from FA_DATA_KEY.

A stored value looks like ``fa1:<base64 nonce+ciphertext>``. Rotating the key is a re-encrypt job (M8.5).
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

PREFIX = "fa1:"
_AAD = b"financialauthority/field/v1"


class DataKeyMissing(RuntimeError):
    pass


def _key() -> bytes:
    if not settings.data_key:
        raise DataKeyMissing("FA_DATA_KEY is not set; secrets cannot be stored")
    return hashlib.sha256(("fa:data-key:" + settings.data_key).encode()).digest()


def encrypt(value: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_key()).encrypt(nonce, value.encode("utf-8"), _AAD)
    return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt(token: str) -> str:
    if not token.startswith(PREFIX):
        raise ValueError("not an encrypted field")
    raw = base64.urlsafe_b64decode(token[len(PREFIX) :].encode("ascii"))
    try:
        return AESGCM(_key()).decrypt(raw[:12], raw[12:], _AAD).decode("utf-8")
    except InvalidTag as error:
        raise ValueError("the field does not decrypt with this data key") from error


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(PREFIX)
