# ruff: noqa: E501
"""Xác nhận mạnh (spec §8.1) — **STUB của T1**, chữ ký đã chốt; T2 thay phần thân (mở khoá hôm nay bằng PIN / mã kép qua kênh phụ, 3 lần
sai khoá 1 giờ, danh sách hạ phòng thủ). Trong lúc chờ T2, stub FAIL-CLOSED: mọi hành động trong danh sách hạ phòng thủ / `outbound`
đều trả `NeedConsole` (chỉ làm được trên Console + PIN) — không bao giờ cho qua.

Gọi: `await strong_confirm.require(db, org_id, action) -> Allowed | NeedConsole`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

#: Hành động cần xác nhận mạnh (§8.1): tin gửi khách + "hạ phòng thủ".
ACTIONS = ("outbound", "mute_defensive", "report_pref_defensive", "intel_mode_shadow", "rebind_account", "rebind_backup_channel",
           "payment_identifier_verify", "forget_person", "quiet_hours_change", "digest_off")


@dataclass(frozen=True)
class Allowed:
    action: str
    via: str


@dataclass(frozen=True)
class NeedConsole:
    action: str
    reason: str = "Việc này cần mở Console (mã PIN)"


async def require(db: Any, org_id: uuid.UUID, action: str) -> Allowed | NeedConsole:
    """Stub fail-closed: luôn `NeedConsole` (kể cả hành động lạ). T2 cài luật thật."""
    del db, org_id
    return NeedConsole(action)
