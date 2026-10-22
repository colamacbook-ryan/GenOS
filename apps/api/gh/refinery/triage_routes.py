"""/refinery/triage — cấu hình + số liệu lọc đầu Hộp thư (v0.1.25, Đợt C1; logic ở gh.refinery.triage).

- `GET /refinery/triage/settings` — ai đăng nhập cũng đọc được (Hộp thư cần biết có bật lọc và ngưỡng điểm).
- `PATCH /refinery/triage/settings` — CHỈ Owner; ghi Action Log `refinery.triage_settings_changed`.
- `GET /refinery/triage/summary` — số đếm (trùng/rác/điểm thấp/chờ lọc + Jev: số lượt, độ trễ, độ khớp quy tắc);
  quyền `queue.read`, ĐẾM THEO PHẠM VI của người gọi (v0.1.27: `assigned`/`team` chỉ đếm mục mình thấy trong
  Hộp thư; `all` = cả tổ chức) — Gen đọc qua tool `refinery.summary`.
- `GET /refinery/triage/skipped` (v0.1.55, J2) — "Tin đã bỏ qua": tin bị lọc trước khi trích xuất (trùng hẳn / rác chắc
  chắn), CHỈ ĐỌC; quyền `queue.read` + phạm vi người/nhóm của người gọi; chữ tin che số dài với vai không phải Owner.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import current_user, require, require_owner
from gh.biz.core.scope import direct_visible_sql, scope_for
from gh.biz.queue import service as qsvc
from gh.chassis import actionlog
from gh.data.common import iso, mask_text, raw_code
from gh.db import DB
from gh.refinery import prefilter, triage

router = APIRouter(prefix="/refinery/triage", tags=["refinery"])


@router.get("/settings")
async def get_settings(user: service.CurrentUser = Depends(current_user), db: AsyncSession = DB) -> dict[str, Any]:
    return await triage.get_settings(db, user.org_id)


class TriageSettingsPatch(BaseModel):
    enabled: bool | None = None
    min_score: int | None = Field(default=None, ge=triage.MIN_SCORE_RANGE[0], le=triage.MIN_SCORE_RANGE[1])
    use_jev: bool | None = None
    prefilter: bool | None = None     # v0.1.55 (J2): lọc trước khi trích xuất


@router.patch("/settings")
async def patch_settings(body: TriageSettingsPatch, user: service.CurrentUser = Depends(require_owner),
                         db: AsyncSession = DB) -> dict[str, Any]:
    cfg = await triage.get_settings(db, user.org_id)
    changes = {k: v for k, v in body.model_dump(exclude_none=True).items() if cfg.get(k) != v}
    if changes:
        before = {k: cfg.get(k) for k in changes}
        cfg.update(changes)
        await triage.save_settings(db, user.org_id, cfg)
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="refinery.triage_settings_changed", target_type="settings",
                               target_id="triage", detail={"before": before, "after": changes}, ip=user.ip)
    return cfg


@router.get("/summary")
async def get_summary(days: int = Query(7, ge=1, le=90), user: service.CurrentUser = Depends(require("queue.read")),
                      db: AsyncSession = DB) -> dict[str, Any]:
    # Tôn trọng phạm vi queue.read (v0.1.27): phạm vi hẹp chỉ đếm mục người gọi thấy được trong Hộp thư.
    sc = await scope_for(db, user, "queue.read")
    return await triage.summary(db, user.org_id, days, scope_sql=qsvc.item_scope_sql(sc, "i", user),
                               scope=sc.level)


SKIPPED_TEXT_MAX = 500


@router.get("/skipped")
async def get_skipped(limit: int = Query(50, ge=1, le=200), days: int = Query(30, ge=1, le=90),
                      user: service.CurrentUser = Depends(require("queue.read")),
                      db: AsyncSession = DB) -> dict[str, Any]:
    """"Tin đã bỏ qua" (J2): tin KHÔNG được gửi model vì trùng hẳn / rác chắc chắn — vẫn nằm trong Kho thô để xem lại.
    Chỉ đọc. Phạm vi `queue.read`: phạm vi hẹp chỉ thấy tin của người/nhóm mình phụ trách."""
    sc = await scope_for(db, user, "queue.read")
    where, params = "TRUE", {}
    if not sc.is_all:
        pw, pp = sc.person_id_sql("p.id")
        gw, gp = sc.group_sql("g")
        where, params = f"(({pw}) OR ({gw}))", {**pp, **gp}
    # Tin 1-1 chỉ Owner/người được giao đúng người đó xem nguyên văn (review v0.1.65 P2-2) — kể cả phạm vi `all`.
    dm_sql, dm_params = direct_visible_sql(user, "e.group_id", "p.id")
    where, params = f"({where}) AND {dm_sql}", {**params, **dm_params}
    base = f"""
        FROM refinery.event_state s
        JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
        LEFT JOIN core.groups g ON g.id = e.group_id
        LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
        LEFT JOIN core.persons p ON p.id = pi.person_id
        WHERE s.org_id = :o AND s.state = 'discarded' AND s.detail->>'discarded_by' = 'prefilter'
          AND s.updated_at > now() - make_interval(days => :d) AND {where}"""  # noqa: S608 — `where` do Scope sinh
    args = {"o": user.org_id, "d": days, **params}
    total = (await db.execute(text("SELECT count(*) " + base), args)).scalar_one()
    rows = (await db.execute(text("""
        SELECT e.id, e.seq, e.kind, e.body_text, g.name AS group_name, p.display_name AS person_name,
               s.updated_at, s.detail->>'reason' AS reason """ + base + " ORDER BY s.updated_at DESC, e.id LIMIT :n"),
                             {**args, "n": limit})).all()
    owner = user.role_code == rbac.OWNER
    items = [{"id": str(r.id), "code": raw_code(r.seq), "at": iso(r.updated_at), "kind": r.kind,
              "reason": r.reason, "reason_text": prefilter.reason_text(r.reason),
              "text": (mask_text(r.body_text or "", owner) or "")[:SKIPPED_TEXT_MAX],
              "group": r.group_name, "person": r.person_name} for r in rows]
    return {"items": items, "total": int(total or 0), "days": days}
