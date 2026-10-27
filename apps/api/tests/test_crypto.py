import base64
import os
import subprocess
import sys
from pathlib import Path

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


def test_production_hasher_keeps_strong_argon2_parameters() -> None:
    """tests/conftest.py thay `crypto._hasher` bằng tham số tối thiểu cho nhanh — chốt rằng mã sản phẩm (nạp trong một
    tiến trình con sạch, không qua conftest) vẫn băm argon2id tham số mạnh và xác minh được hash tham số thấp cũ."""
    code = ("from gh import crypto; h = crypto._hasher; s = crypto.hash_secret('246810'); "
            "print(h.time_cost, h.memory_cost, h.parallelism, s.split('$')[3], crypto.verify_secret(s, '246810'))")
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], check=True,
                         capture_output=True, text=True, timeout=60).stdout.split()
    t, m, p, params, ok = out
    assert int(t) >= 3 and int(m) >= 64 * 1024 and int(p) >= 1, out
    assert params == f"m={m},t={t},p={p}" and ok == "True"
    # Test chạy hasher rẻ (cùng thuật toán argon2id) — override của conftest đang có hiệu lực.
    assert crypto._hasher.time_cost == 1 and crypto.hash_secret("x").startswith("$argon2id$v=19$m=8,t=1,p=1$")
