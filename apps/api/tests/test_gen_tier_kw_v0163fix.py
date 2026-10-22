"""Gói sửa review v0.1.63 — P3: `_tier_kw` không đọc được chữ ký bộ định tuyến thì Gen của nhân viên KHÔNG được rơi về
Owner (bộ định tuyến mặc định owner=True ⇒ agy trong tiến trình). Đóng an toàn: vẫn truyền owner=False."""

import inspect
from typing import Any

import pytest

from gh.gen import engine


class _Router:
    async def generate(self, org: Any, **kw: Any) -> Any:  # pragma: no cover — chỉ để đọc chữ ký
        return None


def _unreadable(*_a: Any, **_k: Any) -> Any:
    raise ValueError("không đọc được chữ ký")


def test_staff_keeps_owner_false_when_signature_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inspect, "signature", _unreadable)
    assert engine._tier_kw(_Router(), "fast", None, owner=False) == {"owner": False}
    assert engine._tier_kw(_Router(), None, None, owner=False) == {"owner": False}
    assert engine._tier_kw(_Router(), "fast", None, owner=True) == {}               # Owner: như cũ


def test_readable_signature_unchanged() -> None:
    assert engine._tier_kw(_Router(), "fast", "low", owner=False) == {"tier": "fast", "effort": "low", "owner": False}
