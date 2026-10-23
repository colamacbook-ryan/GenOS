# ruff: noqa: E501
"""Đồng hồ của sàng lọc (v0.1.76, gói L2): `clock.now()`.

Mọi chỗ trong `gh.refinery` (nhịp, trần ngày, cửa sổ gần, giãn nhịp) lấy giờ ở đây, không gọi `datetime.now()` thẳng, để bộ
chấm G-KB (L6) chạy replay "nén thời gian" trên đồng hồ giả (spec §4D.1, §11 L2).

An toàn: đồng hồ giả CHỈ bật khi `GH_ENV=eval`. Ở môi trường khác `freeze/advance` ném `RuntimeError` và `now()` luôn là giờ
thật — một lệnh gọi nhầm trong sản phẩm không thể làm sai nhịp hay trần chi phí. Giờ trả về luôn có múi giờ (UTC).
"""

import os
from datetime import UTC, datetime, timedelta

ENV_EVAL = "eval"
_fake: datetime | None = None


def enabled() -> bool:
    """Đồng hồ giả có được phép ở môi trường này không (đọc `GH_ENV` lúc gọi, để test đổi được)."""
    return os.environ.get("GH_ENV", "") == ENV_EVAL


def is_fake() -> bool:
    return _fake is not None and enabled()


def now() -> datetime:
    if _fake is not None and enabled():
        return _fake
    return datetime.now(UTC)


def _require_eval() -> None:
    if not enabled():
        raise RuntimeError("Đồng hồ giả chỉ bật khi GH_ENV=eval")


def freeze(at: datetime) -> datetime:
    """Đặt đồng hồ giả tại `at` (phải có múi giờ)."""
    _require_eval()
    global _fake
    if at.tzinfo is None:
        raise ValueError("Giờ giả phải có múi giờ")
    _fake = at.astimezone(UTC)
    return _fake


def advance(delta: timedelta) -> datetime:
    """Tiến đồng hồ giả (phải đã `freeze`)."""
    _require_eval()
    global _fake
    if _fake is None:
        raise RuntimeError("Chưa freeze đồng hồ giả")
    _fake = _fake + delta
    return _fake


def reset() -> None:
    """Về giờ thật. Luôn an toàn gọi (không cần eval)."""
    global _fake
    _fake = None
