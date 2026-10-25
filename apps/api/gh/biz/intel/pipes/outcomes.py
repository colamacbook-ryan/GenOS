# ruff: noqa: E501
"""Kết quả của tín hiệu (v0.1.76, gói T9 — spec r4 W9, M5): tự suy mỗi 15 phút + Sếp ghi tay.

Tự suy (`infer`, CHỈ điền chỗ `outcome IS NULL` — không bao giờ đè kết quả Sếp/nhân viên đã ghi, chạy lại không đổi gì):
| lời hứa gắn tín hiệu `kept_at`            | acted / saved (auto)    |
| lời hứa vỡ (`broken`) mà tín hiệu chưa ai xử lý | ignored / lost (auto)   |
| ask của tín hiệu đã `confirmed`/`executed`    | acted / unknown (auto)  |
| tín hiệu `acknowledged` / `resolved`           | acted / unknown (auto)  |
| tín hiệu `expired` (hết hạn không ai làm)      | ignored / unknown (auto)|
"Sai" (`dismissed`) KHÔNG phải kết quả. Bỏ qua `system`/`legacy`. Mọi truy vấn lọc theo `org_id`.
Route `POST /signals/{id}/outcome` (Owner; cùng cổng `access.require_alert` như `/signals`: `console_pin` ⇒ 423 khi chưa PIN, org khác ⇒ 404).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require_owner
from gh.biz.hooks import CronJob
from gh.biz.intel import access, signals
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import not_found

log = logging.getLogger("gh.intel.outcomes")
router = APIRouter(tags=["intel"])

_BASE = "org_id = :o AND outcome IS NULL AND kind NOT IN ('system', 'legacy')"
#: (điều kiện SQL trên bảng biz.alerts bí danh `a`, outcome, result) — thứ tự = ưu tiên (luật trên thắng).
_RULES: tuple[tuple[str, str, str], ...] = (
    ("a.status <> 'dismissed' AND EXISTS (SELECT 1 FROM biz.promises p WHERE p.signal_id = a.id AND p.org_id = a.org_id AND p.kept_at IS NOT NULL)", "acted", "saved"),
    ("a.status NOT IN ('acknowledged', 'resolved', 'dismissed') AND EXISTS (SELECT 1 FROM biz.promises p WHERE p.signal_id = a.id "
     "AND p.org_id = a.org_id AND p.kept_at IS NULL AND p.broken IS TRUE)", "ignored", "lost"),
    ("a.status <> 'dismissed' AND EXISTS (SELECT 1 FROM ops.asks q WHERE q.signal_id = a.id AND q.org_id = a.org_id AND q.state IN ('confirmed', 'executed'))", "acted", "unknown"),
    ("a.status IN ('acknowledged', 'resolved')", "acted", "unknown"),
    ("a.status = 'expired'", "ignored", "unknown"),
)


async def infer(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> dict[str, int]:
    """Một lượt cho một tổ chức. Trả `{set}` = số tín hiệu vừa được điền kết quả. Bên gọi commit."""
    n = 0
    for cond, outcome, result in _RULES:
        r = await db.execute(text(f"""UPDATE biz.alerts a SET outcome = :oc, outcome_result = :rs, outcome_by = 'auto', outcome_at = :now,
                                      updated_at = now() WHERE a.{_BASE} AND ({cond})"""),  # noqa: S608
                             {"o": org_id, "oc": outcome, "rs": result, "now": now})
        n += int(r.rowcount or 0)                                # type: ignore[attr-defined]
    return {"set": n}


async def intel_outcomes(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import clock

    now = clock.now()
    sm = sessionmaker()
    out = {"set": 0, "errors": 0}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                out["set"] += (await infer(db, org, now))["set"]
                await db.commit()
        except Exception:                                       # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_outcomes lỗi ở một tổ chức")
            out["errors"] += 1
    return out


JOBS: list[CronJob] = [(intel_outcomes, {"minute": {0, 15, 30, 45}})]


class OutcomeIn(BaseModel):
    outcome: Literal["acted", "ignored"] = "acted"
    result: Literal["saved", "lost", "neutral", "unknown"]
    value_vnd: int | None = Field(default=None, ge=0, le=10**13)
    note: str | None = Field(default=None, max_length=500)


@router.post("/signals/{signal_id}/outcome")
async def post_outcome(signal_id: uuid.UUID, body: OutcomeIn, user: service.CurrentUser = Depends(require_owner),
                       db: AsyncSession = DB) -> dict[str, Any]:
    if await access.require_alert(db, user, signal_id) is None:
        raise not_found("Tín hiệu")
    ok = await signals.set_outcome(db, user.org_id, signal_id, outcome=body.outcome, result=body.result, by="owner", value_vnd=body.value_vnd,
                                   note=body.note)
    if not ok:
        raise not_found("Tín hiệu")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="intel.signal.outcome",
                           target_type="signal", target_id=str(signal_id), result="ok", detail={"outcome": body.outcome, "result": body.result})
    return {"ok": True, "outcome": body.outcome, "result": body.result}
