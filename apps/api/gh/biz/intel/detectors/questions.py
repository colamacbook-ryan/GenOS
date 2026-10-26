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
CANDIDATE_CAP = 2000                     # trần số dòng ứng viên đọc mỗi lượt làm mới (không khoá)
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


async def work_hours_of(db: AsyncSession, org_id: uuid.UUID) -> levels.WorkHours:
    """Giờ làm của tổ chức: settings `intel.work_start` / `intel.work_end` (migration 0045; mặc định 08:00–17:30, giờ VN). Một nguồn với
    `DeliveryState.work_hours` (spec r7 (c′), CNS-190); sai định dạng ⇒ mặc định + log. Hồ sơ Sếp không được đọc ở đây."""
    s, _ = await signals.intel_settings(db, org_id)
    d = levels.WorkHours()
    return levels.WorkHours(start=signals._hhmm(s, "work_start", d.start, org_id), end=signals._hhmm(s, "work_end", d.end, org_id))


def _day_windows(day: datetime, work: levels.WorkHours) -> list[tuple[datetime, datetime]]:
    """Khung giờ làm của MỘT ngày lịch VN bắt đầu `day` (00:00). `end > start` ⇒ [start, end). `end <= start` ⇒ khung vắt nửa đêm
    [start, 24h) ∪ [0, end) (vd 18:00–02:00; `end == start` ⇒ cả ngày)."""
    ws, we = levels._minutes(work.start), levels._minutes(work.end)
    if we > ws:
        return [(day + timedelta(minutes=ws), day + timedelta(minutes=we))]
    return [(day, day + timedelta(minutes=we)), (day + timedelta(minutes=ws), day + timedelta(days=1))] if we > 0 else [(day + timedelta(minutes=ws), day + timedelta(days=1))]


def waiting_hours(asked_at: datetime, now: datetime, work: levels.WorkHours | None = None) -> float:
    """Giờ CHỜ tính trong giờ làm (spec r7 (c′) `owner_unanswered_h`): phần giao của [asked_at, now] với khung `work_hours` MỖI ngày lịch VN
    (không có lịch nghỉ cuối tuần). Câu hỏi 17:00 thứ sáu, 08:30 thứ hai (khung 08:00–17:30) ⇒ 0,5 + 9,5 + 9,5 + 0,5 giờ, không phải 63 giờ."""
    w = work or levels.WorkHours()
    a, b = levels._aware(asked_at).astimezone(levels.VN), levels._aware(now).astimezone(levels.VN)
    if b <= a:
        return 0.0
    total = 0.0
    day = a.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= b:
        for lo0, hi0 in _day_windows(day, w):
            lo, hi = max(a, lo0), min(b, hi0)
            if hi > lo:
                total += (hi - lo).total_seconds() / 3600
        day += timedelta(days=1)
    return total


def wake_at(asked_at: datetime, work: levels.WorkHours | None = None) -> datetime:
    """Mốc đồng hồ đầu tiên mà câu hỏi chặn việc có thể đổi mức: lúc đã chờ đủ `WAIT_ESCALATE_H` giờ TRONG giờ làm (khớp `waiting_hours`)."""
    w = work or levels.WorkHours()
    a = levels._aware(asked_at).astimezone(levels.VN)
    need = timedelta(hours=WAIT_ESCALATE_H)
    day = a.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(400):                                                  # ~13 tháng; khung ngắn hơn 2 giờ vẫn ra mốc ở ngày sau
        for lo0, hi0 in _day_windows(day, w):
            lo = max(a, lo0)
            if hi0 > lo:
                if hi0 - lo >= need:
                    return lo + need
                need -= hi0 - lo
        day += timedelta(days=1)
    return a + timedelta(hours=WAIT_ESCALATE_H)


def in_work_hours(now: datetime, work: levels.WorkHours | None = None) -> bool:
    w = work or levels.WorkHours()
    t = levels._aware(now).astimezone(levels.VN)
    m, ws, we = t.hour * 60 + t.minute, levels._minutes(w.start), levels._minutes(w.end)
    return ws <= m < we if we > ws else (m >= ws or m < we)


def build_input(q: QuestionIn, now: datetime, *, subject_entity_id: uuid.UUID | None = None,
                work: levels.WorkHours | None = None) -> levels.SignalIn:
    """Hàm thuần: đầu vào mức của một câu hỏi. Khách / người thân / nội bộ chặn việc là "quan trọng" (impact cơ sở 2, §4.2.1 (f))."""
    important = q.asker in ("customer", "kin") or (q.asker == "internal" and q.blocks_work)
    return levels.SignalIn(
        kind=KIND, msg_at=q.asked_at, event_at=q.asked_at, deadline_at=q.deadline_at,
        flags=levels.Flags(blocks_work=q.blocks_work, needs_owner=q.needs_owner, explicit_urgent=q.explicit_urgent),
        thread=q.thread, work=q.work, customer_of_owner=q.asker == "customer", source_class="insider" if q.asker != "customer" else "outsider",
        confidence_exist=1.0, confidence_content=q.confidence_content, subject_entity_id=subject_entity_id,
        impact_base=levels.impact_base(KIND, important=important),
        owner_unanswered_h=round(waiting_hours(q.asked_at, now, work), 3), in_work_hours=in_work_hours(now, work),
        harm_what="câu hỏi đang chờ, có thể chặn việc" if q.blocks_work else "")


async def _sent_by(db: AsyncSession, org_id: uuid.UUID, raw_event_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    return (await db.execute(text("""SELECT 1 FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :i AND e.org_id = :o AND pi.person_id = :p LIMIT 1"""),
                             {"i": raw_event_id, "o": org_id, "p": person_id})).first() is not None


async def emit(db: AsyncSession, org_id: uuid.UUID, q: QuestionIn, now: datetime) -> signals.Signal:
    """Ghi (hoặc gộp) tín hiệu câu hỏi: một câu hỏi gốc = một tín hiệu (`case_key` theo tin gốc, spec §4.2 bảng case_key)."""
    work = await work_hours_of(db, org_id)
    ent: uuid.UUID | None = None
    if q.asker_person_id is not None and not await _sent_by(db, org_id, q.raw_event_id, q.asker_person_id):
        # Tin gốc không do người này gửi ⇒ không gán câu hỏi cho họ (không để tin người khác mang nhãn khách/người thân).
        q = q.model_copy(update={"asker_person_id": None, "asker": "other"})
    if q.asker_person_id is not None:
        try:
            ent = (await entities.ensure_person(db, org_id, q.asker_person_id)).id
        except entities.EntityError:
            ent = None
    li = build_input(q, now, subject_entity_id=ent, work=work)
    summary = f"{q.summary or q.title} — {OUTSIDE_NOTE}"
    ev = signals.EvidenceIn(type="raw_event", id=q.raw_event_id, received_at=None, source_field=q.source_field,
                            span_start=q.span_start, span_end=q.span_end, quote=q.quote)
    up = signals.UpsertIn(kind=KIND, title=q.title, summary=summary[:1000], detector="questions", detector_version="1", level_input=li,
                          evidence=[ev], case_key=levels.case_key(KIND, at=q.asked_at, raw_event_id=str(q.raw_event_id)),
                          thread_key=q.thread_key, group_id=q.group_id, subject_type="person" if q.asker_person_id else None,
                          subject_id=q.asker_person_id, person_ids=[q.asker_person_id] if q.asker_person_id else [],
                          facts={"asker_kind": q.asker, "maybe_handled_outside": True, "blocks_work": q.blocks_work}, due_at=q.deadline_at)
    sig = await signals.upsert(db, org_id, up, now)
    wake = wake_at(q.asked_at, work)
    if q.blocks_work and wake > levels._aware(now):
        # `relane` đã đặt `next_eval_at` theo hạn/cửa sổ; kéo về mốc đủ 2 giờ CHỜ TRONG GIỜ LÀM nếu mốc đó đến trước (đường đồng hồ không đợi tin mới).
        await db.execute(text("""UPDATE biz.alerts SET next_eval_at = LEAST(COALESCE(next_eval_at, :w), :w)
                                 WHERE id = :i AND status IN ('open', 'acknowledged')"""), {"w": wake, "i": sig.id})
        sig = await signals.get(db, org_id, sig.id) or sig
    return sig


async def refresh_waiting(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 200) -> list[signals.Signal]:
    """Câu hỏi chặn việc còn mở, đã quá `WAIT_ESCALATE_H` giờ (đồng hồ tường — điều kiện cần; giờ chờ tính trong giờ làm ≤ giờ tường) mà `level_input` còn ghi ≤ 2 ⇒ làm mới `owner_unanswered_h`/`in_work_hours`
    rồi `relane`. Câu hỏi đã làm mới nhưng cờ `in_work_hours` đã lưu KHÁC giờ hiện tại (lần làm mới rơi vào giờ yên lặng) được chọn lại khi
    đổi giờ ⇒ không kẹt ở mức cũ. Chạy lại an toàn (cờ đã khớp giờ hiện tại thì không chọn lại). Trả các tín hiệu ĐỔI mức/phân phối. Bên gọi commit."""
    work = await work_hours_of(db, org_id)
    rows = (await db.execute(text("""
        SELECT id, level, delivery, level_input FROM biz.alerts
        WHERE org_id = :o AND kind = :k AND status IN ('open', 'acknowledged') AND level_input IS NOT NULL
          AND COALESCE((level_input -> 'flags' ->> 'blocks_work')::boolean, false)
          AND (COALESCE((level_input ->> 'owner_unanswered_h')::numeric, 0) <= :th
               OR COALESCE((level_input ->> 'in_work_hours')::boolean, true) <> :iw)     -- vào/ra giờ làm việc ⇒ đánh giá lại
          AND (level_input ->> 'msg_at')::timestamptz + make_interval(secs => :ths) <= :now
        ORDER BY created_at LIMIT :n"""),                                           # KHÔNG khoá ở đây: chỉ khoá dòng thực sự cần ghi (dưới)
                            {"o": org_id, "k": KIND, "th": WAIT_ESCALATE_H, "ths": WAIT_ESCALATE_H * 3600, "now": now, "n": CANDIDATE_CAP,
                                    "iw": in_work_hours(now, work)})).all()
    need: list[tuple[Any, float, bool]] = []
    for r in rows:
        li = dict(r.level_input)
        h, iw = round(waiting_hours(datetime.fromisoformat(str(li["msg_at"])), now, work), 3), in_work_hours(now, work)
        if h <= WAIT_ESCALATE_H and li.get("owner_unanswered_h", 0) <= WAIT_ESCALATE_H and li.get("in_work_hours", True) == iw:
            continue                                        # chưa đủ giờ chờ TRONG giờ làm ⇒ mức không thể đổi: bỏ, không khoá, không tính vào `limit`
        need.append((r, h, iw))
    out: list[signals.Signal] = []
    for r, h, iw in need[:limit]:
        cur = (await db.execute(text("SELECT level_input FROM biz.alerts WHERE id = :i AND status IN ('open', 'acknowledged') FOR UPDATE"), {"i": r.id})).scalar_one_or_none()
        if cur is None:
            continue
        li = dict(cur)
        li["owner_unanswered_h"] = h
        li["in_work_hours"] = iw
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


NEEDS_REPLY = ("AskedPrice", "AskedStatus", "RequestedSample", "RequestedPartnership")
SCAN_WINDOW_H = 24
SCAN_SLA_MIN = 60
SCAN_LIMIT = 50


async def scan_units(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> list[signals.Signal]:
    """Nối detector vào luồng thật: đơn vị ý nghĩa HỎI (`clean.meaning_units`: hỏi giá / hỏi tiến độ / xin mẫu / đề nghị hợp tác) của KHÁCH hoặc
    NGƯỜI THÂN (`owner_key_relation`) mà tin gốc quá `SCAN_SLA_MIN` phút chưa có tin ĐI trả lời đúng người đó ⇒ `emit`. Người gửi do CODE
    xác định qua `core.person_identities` (không do model). Nhân viên bị bỏ (câu nội bộ chặn việc cần cờ `blocks_work` của model).
    Tránh trùng THEO NGƯỜI HỎI với `slow_response` (cũng gom theo người): người đang có `slow_response` hoặc tín hiệu câu hỏi mở thì không
    tạo thêm; queue/jobs.py cũng bỏ người đã có tín hiệu. Lấy tin MỚI nhất trước; loại người hỏi "other" và tin đã có tín hiệu ngay trong SQL.

    T12: đường brain-out/1 ghi mọi đơn vị là `event_type = 'GroupUnit'` ⇒ nhận thêm đơn vị `kind_hint = 'cau_hoi_cho_xu_ly'` (cờ
    `blocks_work`/`needs_owner`/`explicit_urgent` của model đi theo). Khởi động lạnh (Hồ sơ chưa có fact `customer_of` nào): người hỏi trong
    nhóm Sếp ĐÃ XÁC NHẬN `kinh_doanh`, không phải Sếp/người của Sếp, được coi là khách (`generic.cold_customer`); nhóm chưa xác nhận ⇒ vẫn
    bỏ (an toàn, không spam). Các rào chống spam giữ nguyên: quá SLA chưa ai trả lời, một người một dòng."""
    from gh.biz.intel.detectors import generic as g
    from gh.biz.mem import profile_read
    from gh.data.common import NO_REPLY_KINDS, outbound_to_sql

    key_rel = (await profile_read.snapshot(db, org_id)).key_relation_entity_ids()
    sets = await g.org_sets(db, org_id)
    cold = not sets.customers
    biz = list((await db.execute(text("""SELECT DISTINCT group_id FROM mem.group_contexts WHERE org_id = :o AND source = 'owner'
                                          AND context = 'kinh_doanh'"""), {"o": org_id})).scalars().all()) if cold else []
    rows = (await db.execute(text(f"""
        SELECT c.raw_id, c.occurred_at, c.group_id, c.person_id, c.body_text, c.member_count, c.unit_flags, c.person_type
        FROM (
          SELECT e.id AS raw_id, e.received_at, e.channel_id, e.group_id, e.occurred_at, e.body_text, p.id AS person_id, p.person_type, g.member_count,
                 (SELECT mu.flags FROM clean.evidence ev JOIN clean.meaning_units mu ON mu.id = ev.meaning_unit_id AND mu.observed_at = ev.meaning_observed_at
                  WHERE ev.raw_event_id = e.id AND ev.raw_received_at = e.received_at AND mu.superseded_by IS NULL
                    AND mu.kind_hint = 'cau_hoi_cho_xu_ly' ORDER BY mu.observed_at DESC LIMIT 1) AS unit_flags,
                 ARRAY(SELECT x.id FROM core.person_identities x WHERE x.person_id = pi.person_id AND x.channel_id = e.channel_id) AS ids,
                 ARRAY(SELECT NULLIF(split_part(x.external_id, '@', 1), '') FROM core.person_identities x
                       WHERE x.person_id = pi.person_id AND x.channel_id = e.channel_id) AS ext
          FROM raw.events e
          JOIN core.person_identities pi ON pi.id = e.sender_identity_id
          JOIN core.persons p ON p.id = pi.person_id
          LEFT JOIN core.groups g ON g.id = e.group_id
          LEFT JOIN mem.entities pe ON pe.person_id = p.id AND pe.org_id = e.org_id AND pe.merged_into IS NULL
          WHERE e.org_id = :o AND e.direction = 'inbound' AND e.kind <> ALL(CAST(:nokind AS text[]))
            AND e.body_text IS NOT NULL AND e.body_text <> '' AND COALESCE(p.person_type, '') <> 'staff'
            AND (p.person_type = 'customer' OR pe.id = ANY(:key)            -- người hỏi "other" bị loại ngay trong SQL
                 OR e.group_id = ANY(CAST(:biz AS uuid[]))                  -- khởi động lạnh: nhóm kinh_doanh đã xác nhận (lọc tiếp ở dưới)
                 OR EXISTS (SELECT 1 FROM mem.facts f WHERE f.org_id = e.org_id AND f.subject_id = pe.id AND f.predicate = 'customer_of'
                            AND f.valid_to IS NULL AND f.status IN ('asserted', 'owner_confirmed')))
            AND e.occurred_at < CAST(:now AS timestamptz) - make_interval(mins => :sla)
            AND e.occurred_at > CAST(:now AS timestamptz) - make_interval(hours => :win)
            AND EXISTS (SELECT 1 FROM clean.evidence ev JOIN clean.meaning_units mu ON mu.id = ev.meaning_unit_id
                         AND mu.observed_at = ev.meaning_observed_at AND mu.superseded_by IS NULL
                        WHERE ev.raw_event_id = e.id AND ev.raw_received_at = e.received_at
                          AND (mu.event_type = ANY(CAST(:need AS text[])) OR mu.kind_hint = 'cau_hoi_cho_xu_ly'))
        ) c
        WHERE NOT EXISTS (
          SELECT 1 FROM raw.events o2 WHERE o2.channel_id = c.channel_id AND o2.direction = 'outbound'
            AND o2.received_at >= c.received_at AND o2.occurred_at > c.occurred_at
            AND CASE WHEN c.group_id IS NOT NULL THEN o2.group_id = c.group_id
                     ELSE o2.group_id IS NULL AND {outbound_to_sql("o2", "c.ids", "c.ext")} END)
          -- Chế độ tài khoản bot: Sếp/nhân viên trả lời bằng tài khoản riêng ⇒ tin ĐẾN (inbound). Tin của người phía Sếp (đã ghép mã
          -- attrs.is_owner, staff, fact staff_of, thực thể Sếp) trong CÙNG nhóm, sau câu hỏi ⇒ đã có người trả lời (G-KB Đợt 2 P4).
          AND NOT (c.group_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM raw.events i2 JOIN core.person_identities pi2 ON pi2.id = i2.sender_identity_id
              JOIN core.persons p2 ON p2.id = pi2.person_id
              LEFT JOIN mem.entities e2 ON e2.person_id = p2.id AND e2.org_id = i2.org_id
            WHERE i2.org_id = :o AND i2.group_id = c.group_id AND i2.direction = 'inbound' AND i2.received_at >= c.received_at
              AND i2.occurred_at > c.occurred_at AND p2.id <> c.person_id
              AND (p2.person_type = 'staff' OR p2.attrs ->> 'is_owner' = 'true'
                   OR COALESCE(e2.merged_into, e2.id) = ANY(CAST(:side AS uuid[])))))
          AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.alert_type = 'slow_response' AND a.evidence @> jsonb_build_array(jsonb_build_object('type', 'raw', 'id', c.raw_id::text)))
          AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.raw_event_ids @> ARRAY[c.raw_id])   -- tin đã có tín hiệu
          AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.subject_id = c.person_id AND a.status IN ('open', 'acknowledged')
                          AND (a.alert_type = 'slow_response' OR (a.kind = 'cau_hoi_cho_xu_ly' AND a.detector = 'questions')))   -- một người một dòng
        ORDER BY c.occurred_at DESC LIMIT :lim"""),  # noqa: S608 — outbound_to_sql là hằng nội bộ
                            {"o": org_id, "now": now, "sla": SCAN_SLA_MIN, "win": SCAN_WINDOW_H, "lim": SCAN_LIMIT * 4,
                             "nokind": list(NO_REPLY_KINDS), "need": list(NEEDS_REPLY), "key": list(key_rel), "biz": biz,
                             "side": [*sets.owners, *sets.staff]})).all()
    out: list[signals.Signal] = []
    seen: set[uuid.UUID] = set()
    for r in rows:
        if len(out) >= SCAN_LIMIT:
            break
        if r.person_id in seen:
            continue                                    # cùng lượt: một người một câu hỏi (tin mới nhất)
        seen.add(r.person_id)
        try:
            ent = (await entities.ensure_person(db, org_id, r.person_id)).id
        except entities.EntityError:
            continue
        customer = (await db.execute(text("""SELECT EXISTS (SELECT 1 FROM core.persons WHERE id = :p AND person_type = 'customer')
                                                 OR EXISTS (SELECT 1 FROM mem.facts WHERE org_id = :o AND subject_id = :e AND predicate = 'customer_of'
                                                            AND valid_to IS NULL AND status IN ('asserted', 'owner_confirmed'))"""),
                                    {"p": r.person_id, "o": org_id, "e": ent})).scalar_one()
        asker: AskerKind = "kin" if ent in key_rel else ("customer" if customer else "other")
        if asker == "other" and r.group_id in biz and g.cold_customer(g.Who(ent, r.person_id, r.person_type), sets, biz_group=True):
            asker = "customer"                                    # khởi động lạnh (chỉ nhóm kinh_doanh đã xác nhận)
        if asker == "other":
            continue
        uf = r.unit_flags if isinstance(r.unit_flags, dict) else {}
        body = str(r.body_text)
        q = QuestionIn(asked_at=r.occurred_at, raw_event_id=r.raw_id, span_end=min(len(body), 80), quote=body[:80], asker=asker,
                       asker_person_id=r.person_id, blocks_work=uf.get("blocks_work") is True, needs_owner=uf.get("needs_owner") is True,
                       explicit_urgent=uf.get("explicit_urgent") is True, thread=levels.Thread(type="group" if r.group_id else "dm_owner", size=int(r.member_count or 0),
                                                                           size_known=r.group_id is None or r.member_count is not None,
                                                                           owner_in_thread=True, agent_in_thread=True),
                       thread_key=f"g:{r.group_id}" if r.group_id else None, group_id=r.group_id)
        try:
            async with db.begin_nested():
                out.append(await emit(db, org_id, q, now))
        except signals.SignalError:
            log.warning("questions.scan_units: bỏ qua một tin", exc_info=True)
    return out


async def intel_questions_scan(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import clock

    sm = sessionmaker()
    out = {"signals": 0, "errors": 0}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                out["signals"] += len(await scan_units(db, org, clock.now()))
                await db.commit()
        except Exception:                                       # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_questions_scan lỗi ở một tổ chức")
            out["errors"] += 1
    return out


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
JOBS: list[CronJob] = [(intel_questions_clock, {"minute": set(range(60))}),
                       (intel_questions_scan, {"minute": set(range(1, 60, 10))})]
