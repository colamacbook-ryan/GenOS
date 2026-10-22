import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from gh import crypto
from gh.config import get_settings


@pytest.fixture
def key_file(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    p = tmp_path / "gh_master_key"
    p.write_text(base64.b64encode(os.urandom(32)).decode() + "\n")
    monkeypatch.setenv("GH_MASTER_KEY_FILE", str(p))
    get_settings.cache_clear()
    yield p
    get_settings.cache_clear()


def test_envelope_encryption_roundtrip_and_tamper(key_file) -> None:  # type: ignore[no-untyped-def]
    blob = crypto.encrypt("khoá API bí mật".encode(), b"agent.provider_keys:1")
    assert blob.startswith(b"GH1") and "bí mật".encode() not in blob
    assert crypto.decrypt(blob, b"agent.provider_keys:1").decode() == "khoá API bí mật"
    with pytest.raises(InvalidTag):
        crypto.decrypt(blob, b"agent.provider_keys:2")          # sai ngữ cảnh
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(InvalidTag):
        crypto.decrypt(tampered, b"agent.provider_keys:1")


def test_wrong_key_length_rejected(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "k"
    p.write_text(base64.b64encode(b"ngan").decode())
    monkeypatch.setenv("GH_MASTER_KEY_FILE", str(p))
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError):
            crypto.master_key()
    finally:
        get_settings.cache_clear()


def test_production_requires_master_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_ENV", "production")
    monkeypatch.delenv("GH_MASTER_KEY_FILE", raising=False)
    monkeypatch.setenv("GH_MASTER_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            crypto.master_key()
    finally:
        get_settings.cache_clear()


def test_password_and_pin_hashing() -> None:
    h = crypto.hash_secret("246810")
    assert h.startswith("$argon2id$")
    assert crypto.verify_secret(h, "246810") and not crypto.verify_secret(h, "246811")
    assert not crypto.verify_secret(None, "246810")
