# ruff: noqa: E501
"""W2a — câu hỏi chờ xử lý `cau_hoi_cho_xu_ly` (spec r6.6.1 §4B.1, §4.2.1; T5).

Phủ ba nguồn câu hỏi: KHÁCH hỏi chưa ai trả lời, NỘI BỘ chặn việc (thợ hỏi vật tư, NV xin Sếp duyệt) và NGƯỜI THÂN hỏi Sếp. Mặc định
tín hiệu ghi "có thể đã xử lý ngoài nhóm" (vùng mù chat 1-1, Q7). Detector KHÔNG gán mức: chỉ dựng `levels.SignalIn` rồi gọi `signals.upsert`;
loại này chỉ lên 🔴 qua cổng `su_co_cong_viec_gap` (do `levels.compute` quyết).

Điểm yếu G-KB (a): câu hỏi chặn việc chờ quá `WAIT_ESCALATE_H` giờ thì urgency +1 (`levels.axes`: `blocks_work and owner_unanswered_h > 2`).
`owner_unanswered_h` là giá trị CODE đo theo đồng hồ, nhưng `signals.relane` chỉ đọc lại `level_input` đã lưu ⇒ nếu không ai làm mới nó thì
câu hỏi nằm yên ⚪ cho tới khi có tin mới. Vì vậy: (1) `emit` đặt `next_eval_at` = mốc 2 giờ; (2) việc nền `intel_questions_clock` (mỗi phút)
làm mới `owner_unanswered_h`/`in_work_hours` của câu hỏi tới mốc rồi `relane` — không cần đợi tin mới.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob, Hook
from gh.biz.intel import levels, signals
from gh.biz.mem import entities

log = logging.getLogger("gh.intel.questions")

KIND = "cau_hoi_cho_xu_ly"
WAIT_ESCALATE_H = 2.0                    # cùng ngưỡng `levels.axes` (owner_unanswered_h > 2)
OUTSIDE_NOTE = "có thể đã xử lý ngoài nhóm"
AskerKind = Literal["customer", "internal", "kin", "other"]


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asked_at: datetime
    raw_event_id: uuid.UUID
    source_field: Literal["body", "transcript", "ocr", "caption"] = "body"
    span_start: int = 0
    span_end: int = Field(default=1, ge=1)
    quote: str | None = None
    asker: AskerKind = "other"
    asker_person_id: uuid.UUID | None = None
    blocks_work: bool = False                      # câu nội bộ chặn việc (cờ model, urgency +1 khi quá hạn trả lời)
    needs_owner: bool = False
    explicit_urgent: bool = False
    deadline_at: datetime | None = None
    work: levels.Work = Field(default_factory=levels.Work)
    thread: levels.Thread = Field(default_factory=levels.Thread)
    thread_key: str | None = None
    group_id: uuid.UUID | None = None
    confidence_content: float = 0.765
    title: str = "Câu hỏi đang chờ xử lý"
    summary: str | None = None


def waiting_hours(asked_at: datetime, now: datetime) -> float:
    return levels._age(now, asked_at)


def wake_at(asked_at: datetime) -> datetime:
    """Mốc đồng hồ đầu tiên mà câu hỏi chặn việc có thể đổi mức."""
    return levels._aware(asked_at) + timedelta(hours=WAIT_ESCALATE_H)


def in_work_hours(now: datetime, quiet: levels.QuietHours | None = None) -> bool:
    return not levels.in_quiet(now, quiet or levels.QuietHours())


def build_input(q: QuestionIn, now: datetime, *, subject_entity_id: uuid.UUID | None = None,
                quiet: levels.QuietHours | None = None) -> levels.SignalIn:
    """Hàm thuần: đầu vào mức của một câu hỏi. Khách / người thân / nội bộ chặn việc là "quan trọng" (impact cơ sở 2, §4.2.1 (f))."""
    important = q.asker in ("customer", "kin") or (q.asker == "internal" and q.blocks_work)
    return levels.SignalIn(
        kind=KIND, msg_at=q.asked_at, event_at=q.asked_at, deadline_at=q.deadline_at,
        flags=levels.Flags(blocks_work=q.blocks_work, needs_owner=q.needs_owner, explicit_urgent=q.explicit_urgent),
        thread=q.thread, work=q.work, customer_of_owner=q.asker == "customer", source_class="insider" if q.asker != "customer" else "outsider",
        confidence_exist=1.0, confidence_content=q.confidence_content, subject_entity_id=subject_entity_id,
        impact_base=levels.impact_base(KIND, important=important),
        owner_unanswered_h=round(waiting_hours(q.asked_at, now), 3), in_work_hours=in_work_hours(now, quiet),
        harm_what="câu hỏi đang chờ, có thể chặn việc" if q.blocks_work else "")


async def emit(db: AsyncSession, org_id: uuid.UUID, q: QuestionIn, now: datetime) -> signals.Signal:
    """Ghi (hoặc gộp) tín hiệu câu hỏi: một câu hỏi gốc = một tín hiệu (`case_key` theo tin gốc, spec §4.2 bảng case_key)."""
    st = await signals.delivery_state(db, org_id, now)
    ent: uuid.UUID | None = None
    if q.asker_person_id is not None:
        try:
            ent = (await entities.ensure_person(db, org_id, q.asker_person_id)).id
        except entities.EntityError:
            ent = None
    li = build_input(q, now, subject_entity_id=ent, quiet=st.quiet_hours)
    summary = f"{q.summary or q.title} — {OUTSIDE_NOTE}"
    ev = signals.EvidenceIn(type="raw_event", id=q.raw_event_id, received_at=None, source_field=q.source_field,
                            span_start=q.span_start, span_end=q.span_end, quote=q.quote)
    up = signals.UpsertIn(kind=KIND, title=q.title, summary=summary[:1000], detector="questions", detector_version="1", level_input=li,
                          evidence=[ev], case_key=levels.case_key(KIND, at=q.asked_at, raw_event_id=str(q.raw_event_id)),
                          thread_key=q.thread_key, group_id=q.group_id, subject_type="person" if q.asker_person_id else None,
                          subject_id=q.asker_person_id, person_ids=[q.asker_person_id] if q.asker_person_id else [],
                          facts={"asker_kind": q.asker, "maybe_handled_outside": True, "blocks_work": q.blocks_work}, due_at=q.deadline_at)
    sig = await signals.upsert(db, org_id, up, now)
    if q.blocks_work and wake_at(q.asked_at) > levels._aware(now):
        # `relane` đã đặt `next_eval_at` theo hạn/cửa sổ; kéo về mốc 2 giờ nếu mốc đó đến trước (đường đồng hồ không đợi tin mới).
        await db.execute(text("""UPDATE biz.alerts SET next_eval_at = LEAST(COALESCE(next_eval_at, :w), :w)
                                 WHERE id = :i AND status IN ('open', 'acknowledged')"""), {"w": wake_at(q.asked_at), "i": sig.id})
        sig = await signals.get(db, org_id, sig.id) or sig
    return sig


async def refresh_waiting(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 200) -> list[signals.Signal]:
    """Câu hỏi chặn việc còn mở, đã quá `WAIT_ESCALATE_H` giờ mà `level_input` còn ghi ≤ 2 ⇒ làm mới `owner_unanswered_h`/`in_work_hours`
    rồi `relane`. Chạy lại an toàn (đã làm mới thì không chọn lại). Trả các tín hiệu ĐỔI mức/phân phối. Bên gọi commit."""
    st = await signals.delivery_state(db, org_id, now)
    rows = (await db.execute(text("""
        SELECT id, level, delivery, level_input FROM biz.alerts
        WHERE org_id = :o AND kind = :k AND status IN ('open', 'acknowledged') AND level_input IS NOT NULL
          AND COALESCE((level_input -> 'flags' ->> 'blocks_work')::boolean, false)
          AND COALESCE((level_input ->> 'owner_unanswered_h')::numeric, 0) <= :th
          AND (level_input ->> 'msg_at')::timestamptz + make_interval(secs => :ths) <= :now
        ORDER BY created_at LIMIT :n FOR UPDATE"""),
                            {"o": org_id, "k": KIND, "th": WAIT_ESCALATE_H, "ths": WAIT_ESCALATE_H * 3600, "now": now, "n": limit})).all()
    out: list[signals.Signal] = []
    for r in rows:
        li = dict(r.level_input)
        asked = datetime.fromisoformat(str(li["msg_at"]))
        li["owner_unanswered_h"] = round(waiting_hours(asked, now), 3)
        li["in_work_hours"] = in_work_hours(now, st.quiet_hours)
        await db.execute(text("UPDATE biz.alerts SET level_input = CAST(:li AS jsonb) WHERE id = :i"),
                         {"li": orjson.dumps(li).decode(), "i": r.id})
        sig = await signals.relane(db, org_id, r.id, now)
        if (sig.level, sig.delivery) != (r.level, r.delivery):
            out.append(sig)
    return out


async def mark_answered(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID, now: datetime, *, by: Literal["owner", "staff"] = "owner") -> signals.Signal:
    """Có người trả lời (tin hoàn thành đã kiểm, A16) ⇒ `status=da_xu_ly` ⇒ ⚪ `none`. Sếp bấm "Đã xử lý" đi qua `signals.transition`."""
    r = (await db.execute(text("SELECT level_input FROM biz.alerts WHERE id = :i AND org_id = :o AND kind = :k FOR UPDATE"),
                          {"i": signal_id, "o": org_id, "k": KIND})).one_or_none()
    if r is None or r.level_input is None:
        raise signals.SignalError("không tìm thấy câu hỏi")
    li: dict[str, Any] = dict(r.level_input)
    li["status"] = "da_xu_ly"
    await db.execute(text("UPDATE biz.alerts SET level_input = CAST(:li AS jsonb), facts = facts || CAST(:f AS jsonb) WHERE id = :i"),
                     {"li": orjson.dumps(li).decode(), "f": orjson.dumps({"answered_by": by}).decode(), "i": signal_id})
    return await signals.relane(db, org_id, signal_id, now)


async def intel_questions_clock(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import clock

    sm = sessionmaker()
    out = {"changed": 0, "errors": 0}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                out["changed"] += len(await refresh_waiting(db, org, clock.now()))
                await db.commit()
        except Exception:                                       # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_questions_clock lỗi ở một tổ chức")
            out["errors"] += 1
    return out


HOOKS: list[Hook] = []
JOBS: list[CronJob] = [(intel_questions_clock, {"minute": set(range(60))})]
