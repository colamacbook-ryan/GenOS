# ruff: noqa: E501
"""Tiện ích dựng đầu vào `levels.compute` cho test T1 (thuần, không DB). Chính sách bối cảnh đọc từ seed 0043 — một nguồn."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from gh.biz.intel import levels as L
from gh.biz.mem import contexts as C
from gh.biz.mem.profile_read import ProfileItem, ProfileSnapshot

SQL_0043 = Path(__file__).resolve().parents[3] / "db" / "sql" / "0043_v0176_tri_nho.sql"
NOW = datetime(2026, 10, 23, 14, 0, tzinfo=L.VN)           # T6 23/10/2026 14:00 giờ VN
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


@cache
def seed_policies() -> dict[str, dict[str, Any]]:
    sql = SQL_0043.read_text(encoding="utf-8")
    return {m.group(1): json.loads(m.group(2))
            for m in re.finditer(r"\('(\w+)', '[^']*', \$j\$(\{.*?\})\$j\$::jsonb\)", sql, re.S)}


def policy(code: str | None = "kinh_doanh") -> C.Policy:
    """`None` = nhóm chưa xác nhận (hàng "chưa xác nhận" của L1)."""
    if code is None:
        return C.unconfirmed("g:test")
    return C.merge_policies("g:test", [(code, seed_policies()[code])])


def profile(key_relations: tuple[uuid.UUID, ...] = ()) -> ProfileSnapshot:
    items = tuple(ProfileItem(uuid.uuid4(), "key_relations", "owner_key_relation", "người quan trọng", e,
                              datetime(2026, 1, 1, tzinfo=L.VN), 1) for e in key_relations)
    return ProfileSnapshot(ORG, 1, uuid.uuid4(), items)


def state(**kw: Any) -> L.DeliveryState:
    base: dict[str, Any] = {"intel_mode": "live", "days_since_install": 40, "m1_passed_gates": list(L.REGULAR_GATES)}
    base.update(kw)
    return L.DeliveryState.model_validate(base)


def person(ref: str = "p1", **kw: Any) -> L.PersonRel:
    return L.PersonRel(ref=ref, **kw)


def sig(kind: str | None, *, ago_h: float = 0.5, due_in_h: float | None = None, now: datetime = NOW, **kw: Any) -> L.SignalIn:
    """Tín hiệu đã kiểm, mặc định: nhóm 20 người có Sếp + agent, nạn nhân là nhân viên, đủ `harm`, độ chắc medium."""
    flags = kw.pop("flags", {})
    data: dict[str, Any] = {
        "kind": kind, "msg_at": now - timedelta(hours=ago_h), "event_at": now - timedelta(hours=ago_h),
        "deadline_at": None if due_in_h is None else now + timedelta(hours=due_in_h),
        "harm_what": "mất tiền / mất khách trong hôm nay", "thread": {"type": "group", "size": 20, "owner_in_thread": True,
                                                                     "agent_in_thread": True},
        "victims": [person("v1", team=True)], "source_class": "insider",
        "confidence_exist": 1.0, "confidence_content": 0.765, "flags": flags,
    }
    data.update(kw)
    return L.SignalIn.model_validate(data)


def compute(s: L.SignalIn, *, ctx: str | None = "kinh_doanh", st: L.DeliveryState | None = None,
            prof: ProfileSnapshot | None = None, now: datetime = NOW) -> L.Level:
    return L.compute(s, prof or profile(), policy(ctx), st or state(), now)
