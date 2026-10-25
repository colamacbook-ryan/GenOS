# ruff: noqa: E501
"""W2b — xung đột & khiếu nại `xung_dot_khieu_nai` + cổng `khach_che_cong_khai_doa_bo` (spec r6.6.1 §4.2.1, Q15; T5).

Detector chỉ dựng `levels.SignalIn` (khách của Sếp, `public`, `said_will_leave`, `repeat_n`, lịch sử vụ, đóng việc) rồi gọi `signals.upsert`;
mức do `levels.compute` quyết. Vụ = (khách, chuỗi trả lời | cửa sổ 72 giờ) qua `levels.case_key`; lời doạ bỏ sinh thêm `quan_he_nong_nguoi`
CÙNG khoá vụ (§4B.1). Mốc vụ lấy từ `case_history` (`levels.case_anchor`): quá 24 giờ ⇒ 🟠 `digest_top` (Q15) qua đường đồng hồ của `signals`.

Điểm yếu G-KB (c): câu đùa ("xách dao qua") / càu nhàu mà khách VẪN đặt hàng không được thành 🔴 — `benign` (model nêu `tone` hoặc code bắt dấu
hiệu đùa) gỡ cờ cổng (`public`/`said_will_leave`); có đặt hàng sau đó (hint hoặc `biz.deals` thắng sau lời chê) ⇒ `closure=customer` ⇒ ⚪.
Đóng việc (A3): `close_case(by=owner|customer|staff)`.
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

log = logging.getLogger("gh.intel.conflicts")

KIND, KIND_REL = "xung_dot_khieu_nai", "quan_he_nong_nguoi"
JOKE_MARKERS = ("xách dao", "xach dao", "haha", "hihi", "hehe", "=))", "đùa", "đùa thôi", "😂", "🤣", "cho vui")
LEAVE_MARKERS = ("lần sau lấy chỗ khác", "lan sau lay cho khac", "thôi khỏi", "hủy đơn", "huỷ đơn", "không mua nữa", "bỏ chỗ này")
REPEAT_WINDOW_DAYS = 30
Tone = Literal["serious", "joke", "grumble"]
Closure = Literal["none", "staff", "customer", "owner"]


NOT_JOKE_MARKERS = ("không phải đùa", "không đùa", "chẳng đùa", "đâu có đùa", "đùa đâu", "nghiêm túc", "nói thật")


def looks_like_joke(body: str | None) -> bool:
    """Có dấu hiệu đùa — trừ khi khách NÓI RÕ không đùa ("không phải đùa đâu": phủ định của đùa, vẫn là khiếu nại thật)."""
    b = (body or "").lower()
    if any(m in b for m in NOT_JOKE_MARKERS):
        return False
    return any(m in b for m in JOKE_MARKERS)


def says_leave(body: str | None) -> bool:
    b = (body or "").lower()
    return any(m in b for m in LEAVE_MARKERS)


class ComplaintIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sent_at: datetime
    raw_event_id: uuid.UUID
    customer_person_id: uuid.UUID
    source_field: Literal["body", "transcript", "ocr", "caption"] = "body"
    span_start: int = 0
    span_end: int = Field(default=1, ge=1)
    quote: str | None = None
    tone: Tone = "serious"
    public: bool = False
    said_will_leave: bool = False
    repeat_claimed: int = 0                       # số lần khách nêu tường minh (trích dẫn đã kiểm)
    ordered_after: bool | None = None             # None ⇒ code tra `biz.deals`
    closure: Closure = "none"
    reply_chain_id: str | None = None
    thread: levels.Thread = Field(default_factory=levels.Thread)
    thread_key: str | None = None
    group_id: uuid.UUID | None = None
    confidence_content: float = 0.75
    title: str = "Khách phàn nàn"


def benign(c: ComplaintIn) -> bool:
    """Đùa / càu nhàu (model nêu hoặc code bắt dấu hiệu trong trích dẫn) — không phải khiếu nại thật."""
    return c.tone in ("joke", "grumble") or looks_like_joke(c.quote)


def effective_flags(c: ComplaintIn, ordered_after: bool) -> tuple[bool, bool, Closure]:
    """(public, said_will_leave, closure) sau khi hạ mức câu đùa/càu nhàu. Đùa mà vẫn đặt hàng ⇒ khách tự xác nhận ổn ⇒ `customer`."""
    if not benign(c):
        return c.public, c.said_will_leave, c.closure
    closure: Closure = "customer" if ordered_after and c.closure == "none" else c.closure
    return False, False, closure


def build_input(c: ComplaintIn, *, customer_entity_id: uuid.UUID, repeat_n: int, ordered_after: bool,
                history: list[levels.CaseEvent]) -> levels.SignalIn:
    public, leave, closure = effective_flags(c, ordered_after)
    gate_on = (public or leave) and closure == "none"
    return levels.SignalIn(
        kind=KIND, msg_at=c.sent_at, event_at=c.sent_at, flags=levels.Flags(public=public, said_will_leave=leave, repeat_n=repeat_n),
        gate_candidate=levels.G3 if (public or leave) else None, customer_of_owner=True, closure=closure, case_history=history,
        thread=c.thread, source_class="outsider", confidence_exist=1.0, confidence_content=c.confidence_content,
        subject_entity_id=customer_entity_id, impact_base=levels.impact_base(KIND, important=True),
        harm_what="khách chê/doạ bỏ — có thể mất khách" if gate_on or public or leave else "",
        victims=[levels.PersonRel(ref=f"u_{c.customer_person_id}", customer=True, thread_member=c.group_id is not None)])


async def is_customer(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, entity_id: uuid.UUID) -> bool:
    return (await db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM core.persons WHERE id = :p AND org_id = :o AND person_type = 'customer')
            OR EXISTS (SELECT 1 FROM mem.facts WHERE org_id = :o AND subject_id = :e AND predicate = 'customer_of' AND valid_to IS NULL
                       AND status IN ('asserted', 'owner_confirmed'))"""), {"p": person_id, "o": org_id, "e": entity_id})).scalar_one() is True


async def sent_by(db: AsyncSession, org_id: uuid.UUID, raw_event_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    """Tin gốc là tin ĐẾN do chính `person_id` gửi (định danh thuộc người đó) — tin của người khác không bao giờ thành lời chê của khách."""
    return (await db.execute(text("""SELECT 1 FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :i AND e.org_id = :o AND e.direction = 'inbound' AND pi.person_id = :p LIMIT 1"""),
                             {"i": raw_event_id, "o": org_id, "p": person_id})).first() is not None


async def _ordered_after(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, since: datetime) -> bool:
    return (await db.execute(text("""SELECT 1 FROM biz.deals WHERE org_id = :o AND person_id = :p AND won_at > :t LIMIT 1"""),
                             {"o": org_id, "p": person_id, "t": since})).first() is not None


async def _repeat_n(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, case_key: str, now: datetime, claimed: int) -> int:
    """`repeat_n` = max(code đếm tín hiệu cùng người trong 30 ngày (gồm vụ này), số lần khách nêu tường minh) — A14."""
    n = (await db.execute(text("""
        SELECT count(DISTINCT case_key) FROM biz.alerts WHERE org_id = :o AND kind = :k AND created_at > :t AND case_key <> :ck
          AND facts -> 'person_ids' ? CAST(:p AS text)"""),
                          {"o": org_id, "k": KIND, "t": now - timedelta(days=REPEAT_WINDOW_DAYS), "ck": case_key, "p": str(person_id)})).scalar_one()
    return max(int(n) + 1, claimed)


async def _open_case(db: AsyncSession, org_id: uuid.UUID, base_key: str) -> tuple[str, list[levels.CaseEvent]]:
    """(khoá vụ, lịch sử) của vụ ĐANG MỞ mới nhất ứng với `base_key` (gồm các vụ mở lại `base_key|v<epoch>`). Chưa có vụ mở ⇒ (base_key, [])."""
    r = (await db.execute(text("""SELECT case_key, facts -> 'case_events' AS ev FROM biz.alerts
                                  WHERE org_id = :o AND kind = :k AND status IN ('open', 'acknowledged')
                                    AND (case_key = :ck OR case_key LIKE :ck || '|v%') ORDER BY created_at DESC LIMIT 1"""),
                          {"o": org_id, "k": KIND, "ck": base_key})).one_or_none()
    if r is None:
        return base_key, []
    return r.case_key, [levels.CaseEvent.model_validate(e) for e in (r.ev or [])]


def _last_closure(events: list[levels.CaseEvent]) -> datetime | None:
    cl = [e.at for e in events if e.evt == "closure"]
    return max(cl) if cl else None


async def emit(db: AsyncSession, org_id: uuid.UUID, c: ComplaintIn, now: datetime, *, cold_customer: bool = False,
               extra_evidence: list[signals.EvidenceIn] | None = None, facts_extra: dict[str, Any] | None = None) -> list[signals.Signal]:
    """Ghi tín hiệu của một lời chê. Trả [xung_dot] (+ [quan_he_nong_nguoi] khi khách doạ bỏ). Không phải khách của Sếp ⇒ [].
    `cold_customer` (T12, bộ chiếu đơn vị): bên gọi đã xác định khách theo luật khởi động lạnh (`generic.cold_customer`) khi Hồ sơ chưa
    có fact `customer_of` nào; tin gốc vẫn phải do chính người đó gửi."""
    ent = (await entities.ensure_person(db, org_id, c.customer_person_id)).id
    if not (cold_customer or await is_customer(db, org_id, c.customer_person_id, ent)) or not await sent_by(db, org_id, c.raw_event_id, c.customer_person_id):
        return []
    base_key = levels.case_key(KIND, at=c.sent_at, customer_entity_id=str(ent), reply_chain_id=c.reply_chain_id)
    case_key, old_events = await _open_case(db, org_id, base_key)
    closed_at = _last_closure(old_events)
    if closed_at is not None and c.sent_at > closed_at:
        # "Khách chê lại sau khi đóng là vụ MỚI" (§4.2.1): khoá mới + mốc vụ mới; vụ cũ (đóng bởi NV, còn mở) giữ nguyên closure của nó.
        case_key, old_events = f"{base_key}|v{int(closed_at.timestamp())}", []
    ordered = c.ordered_after if c.ordered_after is not None else await _ordered_after(db, org_id, c.customer_person_id, c.sent_at)
    public, leave, closure = effective_flags(c, ordered)
    events = [levels.CaseEvent(at=c.sent_at, evt="complaint", public=public, said_will_leave=leave)]
    hist = sorted([*old_events, *([] if any(e.at == events[0].at and e.evt == "complaint" for e in old_events) else events)], key=lambda e: e.at)
    if closure != "none" and not any(e.evt == "closure" for e in hist):
        hist.append(levels.CaseEvent(at=max(c.sent_at, now) if closure == "customer" else now, evt="closure"))
    repeat_n = await _repeat_n(db, org_id, c.customer_person_id, case_key, now, c.repeat_claimed)
    li = build_input(c, customer_entity_id=ent, repeat_n=repeat_n, ordered_after=ordered, history=hist)
    ev = signals.EvidenceIn(type="raw_event", id=c.raw_event_id, source_field=c.source_field, span_start=c.span_start,
                            span_end=c.span_end, quote=c.quote)
    base: dict[str, Any] = {"thread_key": c.thread_key, "group_id": c.group_id, "subject_type": "person", "subject_id": c.customer_person_id,
                            "person_ids": [c.customer_person_id], "evidence": [ev, *(extra_evidence or [])], "case_key": case_key,
                            "detector": "conflicts", "detector_version": "1"}
    facts = {"case_events": [orjson.loads(e.model_dump_json()) for e in hist], "benign": benign(c), "ordered_after": ordered, **(facts_extra or {})}
    out = [await signals.upsert(db, org_id, signals.UpsertIn(kind=KIND, title=c.title, level_input=li, facts=facts, **base), now)]
    if leave:
        # Một vụ một 🔴 (Gen chốt): bản quan_he cùng khoá vụ KHÔNG mang cờ cổng ⇒ không push riêng, đi vào bản tin (🟠 `ongoing`).
        rel_li = li.model_copy(update={"kind": KIND_REL, "gate_candidate": None, "ongoing": True, "harm_what": "",
                                       "flags": levels.Flags(repeat_n=repeat_n)})
        out.append(await signals.upsert(db, org_id, signals.UpsertIn(kind=KIND_REL, title="Khách có thể bỏ đi", level_input=rel_li, facts=facts, **base), now))
    return out


async def close_case(db: AsyncSession, org_id: uuid.UUID, case_key: str, *, by: Literal["owner", "staff", "customer"], now: datetime) -> list[signals.Signal]:
    """Đóng việc (A3): owner/customer ⇒ ⚪ none; staff ⇒ ⚪ digest_low. Mọi tín hiệu mở cùng vụ; tin chê sau đó là vụ mới (mốc đặt lại)."""
    rows = (await db.execute(text("""SELECT id, level_input, facts FROM biz.alerts WHERE org_id = :o AND case_key = :ck
                                     AND kind IN (:a, :b) AND status IN ('open', 'acknowledged') AND level_input IS NOT NULL FOR UPDATE"""),
                             {"o": org_id, "ck": case_key, "a": KIND, "b": KIND_REL})).all()
    out = []
    for r in rows:
        li = dict(r.level_input)
        li["closure"] = by
        hist = list(li.get("case_history") or []) + [{"at": now.isoformat(), "evt": "closure", "public": False, "said_will_leave": False}]
        li["case_history"] = hist
        ev = list((r.facts or {}).get("case_events") or []) + [{"at": now.isoformat(), "evt": "closure", "public": False, "said_will_leave": False}]
        await db.execute(text("UPDATE biz.alerts SET level_input = CAST(:li AS jsonb), facts = facts || CAST(:f AS jsonb) WHERE id = :i"),
                         {"li": orjson.dumps(li).decode(), "f": orjson.dumps({"case_events": ev}).decode(), "i": r.id})
        out.append(await signals.relane(db, org_id, r.id, now))
    return out


async def from_rule(db: AsyncSession, org_id: uuid.UUID, *, event_ids: list[uuid.UUID], person_id: uuid.UUID | None,
                    group_id: uuid.UUID | None, now: datetime) -> list[signals.Signal] | None:
    """Đường nhanh khiếu nại cho luật R-03 (`runner`): dựng `ComplaintIn` từ tin gốc rồi `emit`. Chỉ lấy tin ĐẾN do chính khách gửi (định danh thuộc `person_id`); marker doạ bỏ cũng chỉ tìm trong tin đó. Trả None khi không dựng được (không có
    người / không phải khách / không có tin chữ của chính khách) ⇒ bên gọi quay về `raise_alert` cũ. `public` = nhóm có > 2 thành viên."""
    if person_id is None or not event_ids:
        return None
    rows = (await db.execute(text("""SELECT e.id, e.occurred_at, e.body_text FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                  WHERE e.org_id = :o AND e.id = ANY(:i) AND e.direction = 'inbound' AND pi.person_id = :p
                                  AND e.body_text IS NOT NULL AND e.body_text <> '' ORDER BY e.occurred_at LIMIT 20"""),
                          {"o": org_id, "i": event_ids, "p": person_id})).all()
    if not rows:
        return None
    # câu doạ bỏ có thể nằm ở BẤT KỲ tin nào của khách trong lô (không chỉ tin đầu): ưu tiên tin có marker, không có thì tin đầu
    r = next((x for x in rows if says_leave(str(x.body_text))), rows[0])
    members = 0
    if group_id is not None:
        members = int((await db.execute(text("SELECT member_count FROM core.groups WHERE id = :g AND org_id = :o"),
                                        {"g": group_id, "o": org_id})).scalar_one_or_none() or 0)
    body = str(r.body_text)
    marker = next((m for m in LEAVE_MARKERS if m in body.lower()), None)
    start = body.lower().find(marker) if marker else 0
    end = start + len(marker) if marker else min(len(body), 80)
    c = ComplaintIn(sent_at=r.occurred_at, raw_event_id=r.id, customer_person_id=person_id, span_start=start, span_end=max(end, start + 1),
                    quote=body[start:end], public=group_id is not None and members > 2, said_will_leave=marker is not None,
                    thread=levels.Thread(type="group" if group_id else "dm_owner", size=members, owner_in_thread=True, agent_in_thread=True),
                    thread_key=f"g:{group_id}" if group_id else None, group_id=group_id)
    out = await emit(db, org_id, c, now)
    return out or None


HOOKS: list[Hook] = []
JOBS: list[CronJob] = []
