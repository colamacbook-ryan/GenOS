# ruff: noqa: E501
"""Detector chung (W5b, spec r6.6.1 §6.2): chiếu đơn vị đã kiểm có `kind_hint ∈ {suc_khoe_an_toan, thi_truong_thong_tin}` và sự thật
`scheduled_event` (⇒ `su_kien_dip`) sang tín hiệu qua `signals.upsert`. Detector KHÔNG gán mức: chỉ dựng `levels.SignalIn`.

- Sức khoẻ/an toàn: đối tượng, cỡ luồng, nguồn, luật cứng do CODE tra (mentions, Hồ sơ, `event_state`); cổng `an_toan` do `levels.compute`.
  Sự cố hệ thống/vận hành (`gate_candidate = su_co_cong_viec_gap`, không `emergency`) ⇒ `work.type = system_incident` ⇒ cổng 6 (cần
  `blocks_work` — không chặn việc ⇒ 🟠, A21).
- Dịp/sự kiện: 🟠 khi còn ≤ 3 ngày và Sếp có liên quan (impact cơ sở 2 ⇒ `levels.axes` + urgency 2); không liên quan ⇒ ⚪.
- Thị trường: impact cơ sở 1 ⇒ ⚪ (🟠 khi model nêu `needs_owner`); khoá chống trùng theo băm chữ đã bỏ dấu.
Mỗi đơn vị/sự thật chiếu đúng một lần (`facts.unit_id` / `facts.fact_id` trên tín hiệu); chạy lại an toàn.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob
from gh.biz.intel import levels, signals
from gh.biz.mem import profile_read
from gh.data.common import mask_text

log = logging.getLogger("gh.intel.generic")

CONTENT_FACTOR = 0.85                       # §4D.4: chưa qua (d) = 0,85 × độ tin nguồn
FORWARD_KEYS = ("forwarded", "is_forwarded", "forward")
UNIT_KINDS = ("suc_khoe_an_toan", "thi_truong_thong_tin")
KIN = re.compile(r"\b(me|bo|ba|ma|ong|ba noi|ba ngoai|con|vo|chong|anh|chi|em|co|chu|di|duong|cau)\b")
WINDOW_DAYS = 14


def conf_content(source_class: str, agreement: float | None = None) -> float:
    rel = signals.SOURCE_RELIABILITY.get(source_class, 0.75)
    return round(min(1.0, (agreement if agreement is not None else CONTENT_FACTOR) * rel), 3)


def fold(s: str) -> str:
    from gh.textnorm import strip_accents
    return strip_accents(s.lower())


def safe(s: str | None, n: int) -> str:
    return (mask_text(s or "", False) or "")[:n]


# ─── tra cứu theo tổ chức (một lần mỗi lượt quét) ─────────────────────────────

@dataclass
class OrgSets:
    owner: uuid.UUID | None = None
    staff: frozenset[uuid.UUID] = frozenset()
    customers: frozenset[uuid.UUID] = frozenset()
    key_relations: frozenset[uuid.UUID] = frozenset()
    #: MỌI thực thể là Sếp: thực thể `is_owner` (tài khoản Console) + thực thể của hồ sơ người đã ghép mã ở chế độ tài khoản bot
    #: (`core.persons.attrs.is_owner`, nhận theo ID tài khoản — G-KB Đợt 2 P3). Rỗng ⇒ chỉ `owner` (dựng tay trong test cũ).
    owners: frozenset[uuid.UUID] = frozenset()

    def __post_init__(self) -> None:
        if self.owner is not None and self.owner not in self.owners:
            self.owners = self.owners | {self.owner}

    def is_owner(self, ent: uuid.UUID | None) -> bool:
        return ent is not None and ent in self.owners


async def paired_owner_person_ids(db: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    """Hồ sơ người của Sếp do ghép mã / nhận theo ID tài khoản ở chế độ tài khoản bot (`attrs.is_owner`, chỉ code đặt — ingest/pairing)."""
    return list((await db.execute(text("""SELECT id FROM core.persons WHERE org_id = :o AND attrs ->> 'is_owner' = 'true'
                                            AND deleted_at IS NULL"""), {"o": org_id})).scalars().all())


async def org_sets(db: AsyncSession, org_id: uuid.UUID) -> OrgSets:
    owner = (await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND is_owner AND merged_into IS NULL"),
                              {"o": org_id})).scalar_one_or_none()
    paired = frozenset((await db.execute(text("""
        SELECT DISTINCT COALESCE(e.merged_into, e.id) FROM core.persons p JOIN mem.entities e ON e.person_id = p.id AND e.org_id = p.org_id
        WHERE p.org_id = :o AND p.attrs ->> 'is_owner' = 'true' AND p.deleted_at IS NULL"""), {"o": org_id})).scalars().all())

    async def subj(pred: str) -> frozenset[uuid.UUID]:
        rows = (await db.execute(text("""SELECT DISTINCT COALESCE(e.merged_into, e.id) FROM mem.facts f JOIN mem.entities e ON e.id = f.subject_id
                                         WHERE f.org_id = :o AND f.predicate = :p AND f.valid_to IS NULL
                                           AND f.status IN ('asserted', 'owner_confirmed')"""), {"o": org_id, "p": pred})).scalars().all()
        return frozenset(rows)

    prof = await profile_read.snapshot(db, org_id)
    return OrgSets(owner, await subj("staff_of"), await subj("customer_of"), prof.key_relation_entity_ids(), paired)


async def canonical(db: AsyncSession, entity_id: uuid.UUID | None) -> uuid.UUID | None:
    if entity_id is None:
        return None
    from gh.biz.mem import entities
    try:
        return await entities.canonical(db, entity_id)
    except entities.EntityError:
        return None


@dataclass(frozen=True)
class Who:
    """Dữ kiện TRA CỨU về một thực thể người (T12): mã người, loại người trong `core.persons`, ref dùng chung với fraud/raw_tail."""
    entity_id: uuid.UUID
    person_id: uuid.UUID | None = None
    person_type: str | None = None
    locked: bool = False                    # `attrs.person_type_locked` — loại do Sếp tự đặt
    staff_marked_at: datetime | None = None  # lúc Sếp đánh dấu nhân viên (Owner + PIN, v0.1.76)

    @property
    def ref(self) -> str:
        return f"u_{self.person_id}" if self.person_id else f"e_{self.entity_id}"   # cùng cách viết với fraud/raw_tail ⇒ gộp người đúng


async def _ref(db: AsyncSession, ent: uuid.UUID) -> str:
    w = await who(db, ent)
    return w.ref if w else f"e_{ent}"


async def who(db: AsyncSession, entity_id: uuid.UUID | None) -> Who | None:
    ent = await canonical(db, entity_id)
    if ent is None:
        return None
    r = (await db.execute(text("""SELECT e.person_id, p.person_type, COALESCE(p.attrs ->> 'person_type_locked', '') = 'true' AS locked,
                                         p.attrs ->> 'staff_marked_at' AS marked
                                  FROM mem.entities e LEFT JOIN core.persons p ON p.id = e.person_id WHERE e.id = :e"""), {"e": ent})).one_or_none()
    if r is None:
        return Who(ent)
    return Who(ent, r.person_id, r.person_type, bool(r.locked), parse_marked(r.marked))


async def who_person(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID | None) -> Who | None:
    if person_id is None:
        return None
    from gh.biz.mem import entities
    try:
        ent = (await entities.ensure_person(db, org_id, person_id)).id
    except entities.EntityError:
        return None
    return await who(db, ent)


STAFF_TRUST_DAYS = 7


def staff_trusted(person_type: str | None, *, locked: bool, marked_at: datetime | None, at: datetime) -> bool:
    """MỘT luật cho mọi detector (security review T14, L-90): vai NHÂN VIÊN chỉ được tin để hạ phòng thủ lừa đảo (bỏ "người lạ", bỏ xét mạo
    danh, tin "đã xử lý" là phía Sếp) khi Sếp tự đặt (`attrs.person_type_locked`) VÀ đã đánh dấu ≥ 7 ngày. Khoá mà chưa có `staff_marked_at`
    = dữ liệu Sếp đặt trước v0.1.76 (Nợ #97) ⇒ tin. Quy tắc/seed tự gán (không khoá) ⇒ không tin."""
    if person_type != "staff" or not locked:
        return False
    if marked_at is None:
        return True
    m = marked_at if marked_at.tzinfo is not None else marked_at.replace(tzinfo=at.tzinfo)
    return (at - m).total_seconds() >= STAFF_TRUST_DAYS * 86400


def parse_marked(v: object) -> datetime | None:
    try:
        return datetime.fromisoformat(v) if isinstance(v, str) and v else None
    except ValueError:
        return None


def trusted_owner_side(w: Who | None, sets: OrgSets, at: datetime) -> bool:
    """Phía Sếp ĐỦ TIN để hạ phòng thủ lừa đảo/an toàn (tin "đã xử lý"): Sếp, fact `staff_of`, hoặc nhân viên theo `staff_trusted`."""
    return w is not None and (sets.is_owner(w.entity_id) or w.entity_id in sets.staff
                              or staff_trusted(w.person_type, locked=w.locked, marked_at=w.staff_marked_at, at=at))


def hard_customer(w: Who | None, sets: OrgSets) -> bool:
    """Khách theo DỮ LIỆU (fact `customer_of` hoặc `core.persons.person_type = customer`) — không suy đoán."""
    return w is not None and (w.entity_id in sets.customers or w.person_type == "customer")


def hard_owner_side(w: Who | None, sets: OrgSets) -> bool:
    """Sếp / người của Sếp theo DỮ LIỆU (chủ, fact `staff_of`, `person_type = staff`)."""
    return w is not None and (sets.is_owner(w.entity_id) or w.entity_id in sets.staff or w.person_type == "staff")


async def business_group(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID | None) -> bool:
    """Nhóm Sếp ĐÃ XÁC NHẬN nhãn `kinh_doanh` (nguồn `owner`) — điều kiện của mọi suy luận khởi động lạnh (cold start, T12)."""
    if group_id is None:
        return False
    from gh.biz.mem import contexts

    pol = await contexts.policy(db, org_id, f"g:{group_id}")
    return pol.confirmed and "kinh_doanh" in pol.contexts


def cold_customer(w: Who | None, sets: OrgSets, *, biz_group: bool) -> bool:
    """Khởi động lạnh (Hồ sơ chưa có fact `customer_of` nào): trong nhóm Sếp đã xác nhận là `kinh_doanh`, người KHÔNG phải Sếp/người
    của Sếp/người thân được coi là khách. Có dù một fact `customer_of` ⇒ tắt (chỉ tin dữ liệu). Bảo thủ: nhóm chưa xác nhận ⇒ không suy."""
    return (w is not None and biz_group and not sets.customers and not hard_owner_side(w, sets)
            and w.entity_id not in sets.key_relations)


def who_rel(w: Who, sets: OrgSets, *, thread_member: bool = False) -> levels.PersonRel:
    """MỘT hàm dựng vai người cho detector chiếu đơn vị (units, generic…): tra cứu `OrgSets` (fact) CỘNG loại người Sếp đã đánh dấu ở
    `core.persons.person_type` (khách ⇒ customer, nhân viên ⇒ team). P-T1 (G-KB sau T14): generic bỏ qua person_type ⇒ khách mất cổng an toàn."""
    r = person_rel(w.entity_id, sets, ref=w.ref, thread_member=thread_member)
    return r.model_copy(update={"customer": r.customer or w.person_type == "customer", "team": r.team or w.person_type == "staff"})


def person_rel(ent: uuid.UUID, sets: OrgSets, *, ref: str, thread_member: bool = False, kin_of_member: bool = False) -> levels.PersonRel:
    return levels.PersonRel(ref=ref, is_owner=sets.is_owner(ent), owner_key_relation=ent in sets.key_relations, customer=ent in sets.customers,
                            team=ent in sets.staff, thread_member=thread_member, kin_of_member=kin_of_member, present_in_thread=thread_member)


async def raw_group(db: AsyncSession, raw_id: uuid.UUID | None) -> tuple[uuid.UUID | None, int | None]:
    """(group_id, cỡ nhóm) của tin gốc; tin riêng / không rõ ⇒ (None, 0); nhóm không rõ cỡ (`member_count` NULL) ⇒ cỡ None."""
    if raw_id is None:
        return None, 0
    r = (await db.execute(text("""SELECT e.group_id, g.member_count FROM raw.events e LEFT JOIN core.groups g ON g.id = e.group_id
                                  WHERE e.id = :i LIMIT 1"""), {"i": raw_id})).one_or_none()
    if r is None:
        return None, 0
    return r.group_id, (None if r.group_id is not None and r.member_count is None else int(r.member_count or 0))


def thread_of(group_id: uuid.UUID | None, size: int | None) -> levels.Thread:
    return levels.Thread(type="group" if group_id else "dm_agent", size=size or 0, size_known=size is not None, owner_in_thread=False,
                         agent_in_thread=True)


def flags_of(raw: dict[str, Any] | None) -> levels.Flags:
    d: dict[str, Any] = {}
    for k, v in (raw or {}).items():
        f = levels.Flags.model_fields.get(k)
        if f is None:
            continue
        if k == "repeat_n":
            if isinstance(v, int) and not isinstance(v, bool):
                d[k] = v
        elif v is True:
            d[k] = True
    return levels.Flags(**d)


# ─── đơn vị (sức khoẻ/an toàn, thị trường) ────────────────────────────────────

@dataclass
class UnitCtx:
    kind: str
    unit_id: uuid.UUID
    observed_at: datetime
    conclusion: str = ""
    flags: dict[str, Any] = field(default_factory=dict)
    harm_what: str = ""
    gate_candidate: str | None = None
    confidence_exist: float = 0.8
    source_class: str = "member"
    forwarded: bool = False
    victims: list[levels.PersonRel] = field(default_factory=list)
    thread: levels.Thread = field(default_factory=levels.Thread)
    hard_rules: list[str] = field(default_factory=list)
    owner_work: bool = False
    victim_entity: uuid.UUID | None = None          # thực thể nạn nhân đầu tiên (khoá vụ an toàn theo người)


def is_system_incident(c: UnitCtx) -> bool:
    return (c.kind == levels.SK and c.gate_candidate == levels.G6 and not c.flags.get("emergency") and not c.hard_rules)


def unit_input(c: UnitCtx, now: datetime) -> levels.SignalIn:
    f = flags_of(c.flags)
    ongoing = c.flags.get("ongoing") is True
    forwarded = c.forwarded or c.source_class == "forwarded"
    base: dict[str, Any] = dict(
        kind=c.kind, msg_at=c.observed_at, event_at=c.observed_at, ongoing=ongoing, flags=f, harm_what=c.harm_what,
        gate_candidate=c.gate_candidate, thread=c.thread, victims=c.victims, hard_rule_hit=list(c.hard_rules),
        source_class="forwarded" if forwarded else c.source_class, delivery_form="forwarded" if forwarded else "direct",
        non_forwarded_sources_n=0 if forwarded else 1, confidence_exist=c.confidence_exist,
        confidence_content=conf_content("forwarded" if forwarded else c.source_class))
    if c.kind == levels.SK:
        core = any(p.is_owner or p.owner_key_relation or p.team or p.customer for p in c.victims)
        base["impact_base"] = levels.impact_base(levels.SK, emergency=f.emergency, subject_owner_side=core)
        base["irreversible"] = bool(c.flags.get("irreversible"))
        if is_system_incident(c):
            base["work"] = levels.Work(type="system_incident", owner_work=c.owner_work or f.blocks_work)
    else:
        base["impact_base"] = levels.impact_base(c.kind)
    return levels.SignalIn(**base)


async def _unit_ctx(db: AsyncSession, org_id: uuid.UUID, u: Any, sets: OrgSets, raws: list[uuid.UUID]) -> UnitCtx:
    group_id, size = await raw_group(db, raws[0])
    forwarded = False
    for r in (await db.execute(text("SELECT payload FROM raw.events WHERE id = ANY(:r) AND org_id = :o"), {"r": raws, "o": org_id})).all():
        forwarded = forwarded or any(bool((r.payload or {}).get(k)) for k in FORWARD_KEYS)
    hard = [str(x) for x in (await db.execute(text("""
        SELECT DISTINCT c ->> 'hard' FROM refinery.event_state s, jsonb_array_elements(COALESCE(s.detail -> 'cands', '[]')) c
        WHERE s.org_id = :o AND s.event_id = ANY(:r) AND c ->> 'hard' IS NOT NULL"""), {"o": org_id, "r": raws})).scalars().all()]
    sender = None
    if u.person_id:
        sender = (await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND person_id = :p AND merged_into IS NULL"),
                                   {"o": org_id, "p": u.person_id})).scalar_one_or_none()
    members: set[uuid.UUID] = set()
    if group_id is not None:
        members = set((await db.execute(text("""SELECT e.id FROM core.group_members gm JOIN mem.entities e ON e.person_id = gm.person_id AND e.org_id = :o
                                                WHERE gm.group_id = :g AND gm.left_at IS NULL"""), {"o": org_id, "g": group_id})).scalars().all())
    # AN TOÀN FAIL-HIGH: nhóm chưa có danh sách thành viên thì người được nêu tên coi như thành viên (đừng làm mất cổng vì thiếu dữ liệu)

    def in_group(ent: uuid.UUID) -> bool:
        return ent == sender or ent in members or (group_id is not None and not members)

    victims: list[levels.PersonRel] = []
    victim_ents: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for m in (await db.execute(text("SELECT surface, entity_id FROM mem.mentions WHERE meaning_unit_id = :u AND org_id = :o"),
                               {"u": u.id, "o": org_id})).all():
        ent = await canonical(db, m.entity_id)
        if ent is not None and ent not in seen:
            seen.add(ent)
            w = await who(db, ent)
            victims.append(who_rel(w, sets, thread_member=in_group(ent)) if w else
                           person_rel(ent, sets, ref=f"e_{ent}", thread_member=in_group(ent)))   # ref chung với fraud/raw_tail (T12)
            victim_ents.append(ent)
        elif ent is None and sender is not None and KIN.search(fold(m.surface or "")):
            victims.append(levels.PersonRel(ref=f"kin_{len(victims)}", kin_of_member=True))
    if not victims and sender is not None:
        ws = await who(db, sender)
        victims.append(who_rel(ws, sets, thread_member=True) if ws else person_rel(sender, sets, ref=await _ref(db, sender), thread_member=True))
        victim_ents.append(sender)
    insider = sender is not None and sender in victim_ents       # người gửi nói về chính mình ⇒ người trong cuộc
    harm = u.harm.get("what") if isinstance(u.harm, dict) and isinstance(u.harm.get("what"), str) else ""
    return UnitCtx(kind=u.kind_hint, unit_id=u.id, observed_at=u.observed_at, conclusion=u.conclusion or "", flags=dict(u.flags or {}),
                   harm_what=harm or "", gate_candidate=u.gate_candidate, confidence_exist=float(u.confidence or 0.8),
                   source_class="insider" if insider else "member", forwarded=forwarded, victims=victims,
                   thread=thread_of(group_id, size), hard_rules=hard, owner_work=(sender in sets.staff or sets.is_owner(sender)) if sender else False,
                   victim_entity=victim_ents[0] if victim_ents else None)


async def _skip(db: AsyncSession, org_id: uuid.UUID, target_type: str, target_id: uuid.UUID, err: Exception) -> None:
    from gh.chassis import actionlog
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:generic", action="intel.generic.skip",
                           target_type=target_type, target_id=str(target_id), result="ok", detail={"why": type(err).__name__})


async def project_units(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 200) -> list[signals.Signal]:
    rows = (await db.execute(text("""
        SELECT mu.id, mu.observed_at, mu.group_id, mu.person_id, mu.conclusion, mu.kind_hint, mu.flags, mu.gate_candidate, mu.harm, mu.confidence
        FROM clean.meaning_units mu
        WHERE mu.org_id = :o AND mu.kind_hint = ANY(:k) AND mu.superseded_by IS NULL AND mu.observed_at >= :since
          AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.facts ->> 'unit_id' = mu.id::text)
          AND EXISTS (SELECT 1 FROM clean.evidence ce WHERE ce.meaning_unit_id = mu.id AND ce.meaning_observed_at = mu.observed_at
                        AND ce.quote_status = 'verified' AND ce.raw_event_id IS NOT NULL)
          AND NOT EXISTS (SELECT 1 FROM ops.action_log al WHERE al.org_id = :o AND al.action = 'intel.generic.skip' AND al.target_id = mu.id::text)
        ORDER BY mu.observed_at DESC LIMIT :n"""), {"o": org_id, "k": list(UNIT_KINDS), "since": now - timedelta(days=WINDOW_DAYS), "n": limit})).all()
    if not rows:
        return []
    sets = await org_sets(db, org_id)
    out: list[signals.Signal] = []
    for u in rows:
        ev = (await db.execute(text("""SELECT DISTINCT raw_event_id, raw_received_at FROM clean.evidence
                                       WHERE meaning_unit_id = :u AND meaning_observed_at = :t AND quote_status = 'verified'
                                         AND raw_event_id IS NOT NULL"""), {"u": u.id, "t": u.observed_at})).all()
        if not ev:
            continue                                         # chưa có trích dẫn đã kiểm ⇒ không thành tín hiệu
        try:
            async with db.begin_nested():
                c = await _unit_ctx(db, org_id, u, sets, [e.raw_event_id for e in ev])
                li = unit_input(c, now)
                victim_ent = str(c.victim_entity) if c.victim_entity else None
                ck = (levels.case_key(c.kind, at=u.observed_at, entity_id=victim_ent) if c.kind == levels.SK and victim_ent
                      else levels.case_key(c.kind, at=u.observed_at, raw_event_id=str(ev[0].raw_event_id),
                                           simhash=hashlib.sha1(fold(c.conclusion).encode()).hexdigest()[:16]
                                           if c.kind == "thi_truong_thong_tin" else None))
                title = ("Sự cố vận hành" if is_system_incident(c) else "Sự cố sức khoẻ / an toàn") if c.kind == levels.SK else "Thông tin thị trường"
                up = signals.UpsertIn(
                    kind=c.kind, title=title, summary=safe(c.conclusion, 300) or None, detector="generic", detector_version="1", level_input=li,
                    evidence=[signals.EvidenceIn(type="meaning_unit", id=u.id, raw_event_id=e.raw_event_id, received_at=e.raw_received_at)
                              for e in ev],
                    case_key=ck, thread_key=f"g:{u.group_id}" if u.group_id else None, group_id=u.group_id,
                    subject_type="group" if u.group_id else None, subject_id=u.group_id,
                    person_ids=[u.person_id] if u.person_id else [], facts={"unit_id": str(u.id)})
                out.append(await signals.upsert(db, org_id, up, now))
        except signals.SignalError as e:
            log.warning("generic: bỏ qua đơn vị %s: %s", u.id, type(e).__name__)
            await _skip(db, org_id, "meaning_unit", u.id, e)                 # đánh dấu: không lấy lại mãi, không chiếm chỗ đơn vị mới
    return out


# ─── dịp / sự kiện (sự thật scheduled_event) ──────────────────────────────────

@dataclass
class EventCtx:
    text: str
    valid_from: datetime
    at: datetime
    related: bool = False
    source_class: str = "insider"
    agreement: float | None = None
    confidence_exist: float = 0.8
    subject_entity_id: uuid.UUID | None = None
    thread: levels.Thread = field(default_factory=levels.Thread)


def event_input(c: EventCtx, now: datetime) -> levels.SignalIn:
    return levels.SignalIn(
        kind="su_kien_dip", msg_at=c.valid_from, event_at=c.valid_from, deadline_at=c.at, source_class=c.source_class,  # type: ignore[arg-type]
        confidence_exist=c.confidence_exist, confidence_content=conf_content(c.source_class, c.agreement), thread=c.thread,
        impact_base=levels.impact_base("su_kien_dip", important=c.related), harm_what=safe(c.text, 120),
        subject_entity_id=c.subject_entity_id)


async def project_events(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 200) -> list[signals.Signal]:
    rows = (await db.execute(text("""
        SELECT f.id, f.subject_id, f.object_value, f.valid_from, f.source_class, f.source_raw_event_id, f.agreement, f.confidence_exist
        FROM mem.facts f WHERE f.org_id = :o AND f.predicate = 'scheduled_event' AND f.valid_to IS NULL
          AND f.status IN ('asserted', 'owner_confirmed') AND f.valid_from >= :since
          AND NOT EXISTS (SELECT 1 FROM ops.action_log al WHERE al.org_id = :o AND al.action = 'intel.generic.skip' AND al.target_id = f.id::text)
          AND jsonb_typeof(f.object_value) = 'object' AND COALESCE(f.object_value ->> 'at', f.object_value ->> 'due_at') ~ '^\\d{4}-\\d{2}-\\d{2}'
        ORDER BY f.valid_from DESC LIMIT :n"""), {"o": org_id, "since": now - timedelta(days=60), "n": limit})).all()
    if not rows:
        return []
    sets = await org_sets(db, org_id)
    out: list[signals.Signal] = []
    for f in rows:
        v = f.object_value if isinstance(f.object_value, dict) else {"text": str(f.object_value or "")}
        at = profile_read.parse_when(v.get("at")) or profile_read.parse_when(v.get("due_at"))
        if at is None or at < now - timedelta(days=1) or at > now + timedelta(days=60):
            continue                                         # không rõ ngày / đã qua / còn xa: chưa phải tín hiệu
        subj = await canonical(db, f.subject_id)
        to = await canonical(db, uuid.UUID(v["to_entity_id"])) if v.get("to_entity_id") else None
        related = (f.source_class == "owner" or any(e is not None and (sets.is_owner(e) or e in sets.key_relations) for e in (subj, to)))
        gid, size = await raw_group(db, f.source_raw_event_id)
        c = EventCtx(text=str(v.get("text") or ""), valid_from=f.valid_from, at=at, related=related, source_class=f.source_class,
                     agreement=float(f.agreement) if f.agreement is not None else None, confidence_exist=float(f.confidence_exist),
                     subject_entity_id=subj, thread=thread_of(gid, size))
        li = event_input(c, now)
        old = (await db.execute(text("SELECT id, status, level_input FROM biz.alerts WHERE org_id = :o AND facts ->> 'fact_id' = :f"),
                                {"o": org_id, "f": str(f.id)})).first()
        if old is not None and (old.status not in signals.OPEN or old.level_input == li.model_dump(mode="json")):
            continue
        try:
            async with db.begin_nested():
                up = signals.UpsertIn(
                    kind="su_kien_dip", title="Sắp tới dịp / sự kiện", summary=safe(c.text, 300) or None, detector="generic",
                    detector_version="1", level_input=li, evidence=[signals.EvidenceIn(type="fact", id=f.id)],         # không truyền raw_event_id: T1 gộp theo tin gốc
                    case_key=levels.case_key("su_kien_dip", at=f.valid_from, entity_id=str(subj) if subj else None,
                                             event_date=at.astimezone(levels.VN).date().isoformat(), raw_event_id=str(f.source_raw_event_id)
                                             if f.source_raw_event_id else None),
                    thread_key=f"g:{gid}" if gid else None, group_id=gid, fact_ids=[f.id], due_at=at,
                    expires_at=at + timedelta(days=1), facts={"fact_id": str(f.id)})
                out.append(await signals.upsert(db, org_id, up, now))
        except signals.SignalError as e:
            log.warning("generic: bỏ qua sự kiện %s: %s", f.id, type(e).__name__)
            await _skip(db, org_id, "fact", f.id, e)
    return out


async def project(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> list[signals.Signal]:
    return [*await project_units(db, org_id, now), *await project_events(db, org_id, now)]


async def intel_generic(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import clock

    sm = sessionmaker()
    n = errors = 0
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                n += len(await project(db, org, clock.now()))
                await db.commit()
        except Exception:                                    # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_generic lỗi ở một tổ chức")
            errors += 1
    return {"projected": n, "errors": errors}


HOOKS: list[Any] = []
JOBS: list[CronJob] = [(intel_generic, {"minute": set(range(3, 60, 5))})]
