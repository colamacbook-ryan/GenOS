"""decode_key nhận khoá hex (genh sinh) lẫn base64 (make secrets) — xem gh/crypto.py."""

import base64
import os

import pytest

from gh.crypto import decode_key


def test_decode_key_hex_va_base64_cung_ket_qua() -> None:
    raw = os.urandom(32)
    assert decode_key(raw.hex()) == raw
    assert decode_key(base64.b64encode(raw).decode()) == raw
    assert decode_key("  " + raw.hex() + "\n") == raw


@pytest.mark.parametrize("bad", ["", "abc", "zz" * 32, base64.b64encode(os.urandom(16)).decode()])
def test_decode_key_sai_dinh_dang(bad: str) -> None:
    with pytest.raises(ValueError):
        decode_key(bad)
