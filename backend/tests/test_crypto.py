import pytest

from app import crypto


def test_roundtrip_and_fresh_nonces():
    a = crypto.encrypt("app-password-123")
    b = crypto.encrypt("app-password-123")
    assert a != b
    assert a.startswith("fa1:") and crypto.is_encrypted(a)
    assert crypto.decrypt(a) == "app-password-123"
    assert crypto.decrypt(b) == "app-password-123"


def test_tamper_fails():
    token = crypto.encrypt("secret")
    broken = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        crypto.decrypt(broken)


def test_not_encrypted_rejected():
    with pytest.raises(ValueError):
        crypto.decrypt("plain text")
