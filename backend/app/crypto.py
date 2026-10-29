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


CANARY = "crypto.canary"
CANARY_TEXT = "financialauthority"


def data_key_problem() -> str | None:
    """The first start with a data key seals a known word under it; every later start opens it again. A key that
    does not open it is not the key this data was sealed with, and the app must not run on it: every stored token,
    password and document would be unreadable, each feature failing on its own, and anything saved now would be
    sealed under a second key. Returns the sentence to show, or None. With no data key set, there is nothing to
    check (a developer's machine; the start already warns). The erase keeps the canary, like the owner's second
    step, which is sealed under the same key."""
    if not settings.data_key:
        return None
    from sqlmodel import Session

    from .db import engine
    from .store import get_setting, set_setting

    with Session(engine) as session:
        kept = get_setting(session, CANARY)
        if not is_encrypted(kept):
            set_setting(session, CANARY, encrypt(CANARY_TEXT))
            return None
        try:
            if decrypt(kept) == CANARY_TEXT:
                return None
        except ValueError:
            pass
    return ("FA_DATA_KEY is not the key this data was sealed with: the stored tokens, passwords, the second step and the documents would not open, "
            "and anything saved now would be sealed under a different key. Put the key back (BoxPilot: the app's settings; Docker Compose: the .env file). "
            "To start over under this key instead, move the data folder away first.")
