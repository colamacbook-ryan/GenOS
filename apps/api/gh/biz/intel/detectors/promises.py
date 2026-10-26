# ruff: noqa: E501
"""Detector W1 (spec r6.6.1 §6.2, §4.2.1, §4D.3-4): nghĩa vụ & lời hứa (`nghia_vu_loi_hua`) + việc & hạn (`viec_han`).

Nguồn: `mem.facts` (`promised_to` / `owes` / `deadline`, hạn do CODE tính ở `object_value.due_at`). Chiếu sang `biz.promises` (MODULE NÀY LÀ BÊN GHI
DUY NHẤT) và tín hiệu qua `signals.upsert`; không tự ghi `biz.alerts` (rào AST của T1).

- Chiều lời hứa: Sếp/người của Sếp (`staff_of`) → ai đó = có tín hiệu (cổng 4 nếu bên nhận là khách `customer_of`); người khác → Sếp / người quan trọng =
  CHỈ theo dõi (sổ, không tín hiệu, CNS-087b); giữa hai người ngoài ⇒ bỏ.
- Lời hứa có điều kiện: `condition.met ≠ yes` ⇒ `state='conditional'` (sổ giữ hạn danh nghĩa, cột `due_at` NOT NULL; bộ quét "quá hạn" cũ phải bỏ qua
  `state='conditional'` — việc của gói T5); hạn trong tín hiệu chỉ tính từ khi điều kiện đạt; không bao giờ vào cổng 4.
- ĐIỂM YẾU G-KB (a): hạn tới gần leo thang KHÔNG cần tin mới — `level_input` lưu hạn thật; T1 `next_eval_at`/`reevaluate_due` tính lại theo đồng hồ.
  Phần đồng hồ mà `levels.compute` không tự thấy (dấu hiệu trễ của việc có hạn: hạn đã trôi qua) do `project` đặt lại mỗi lượt quét.
- ĐIỂM YẾU (b): fact bị `facts.close_kept` đóng ⇒ `sync_kept` đóng nghĩa vụ trong sổ (`kept`) và tín hiệu (`da_giu` ⇒ A16 ⇒ ⚪).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob
from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import generic as g
from gh.biz.mem import entities, profile_read, vi_time
from gh.chassis import actionlog

log = logging.getLogger("gh.intel.promises")

PREDICATES = ("promised_to", "owes", "deadline")
WINDOW_DAYS = 90
SUBMISSION = re.compile(r"\b(nop (ho so|thau|bai|don|du thau)|ho so (thau|du thau|thi|xin)|du thau|goi thau|bai thi|ky thi|thi tuyen)\b")
CUTOVER = re.compile(r"\b(cutover|go-?live|chuyen doi he thong|chuyen he thong|nang cap he thong)\b")


@dataclass
class PromiseCtx:
    kind: str
    text: str
    valid_from: datetime
    due_at: datetime | None = None
    to_customer: bool = False
    owner_work: bool = False
    obstacle: bool = False
    completed: bool = False
    condition_met: str | None = None          # None = không điều kiện; "yes" | "no" (no/unknown = chưa đạt)
    source_class: str = "insider"
    agreement: float | None = None
    confidence_exist: float = 0.8
    nudged: bool = False
    blocks_work: bool = False
    explicit_urgent: bool = False
    subject_entity_id: uuid.UUID | None = None
    thread: levels.Thread | None = None
    granularity: str | None = None             # r7 (c′): `vi_time.Due.granularity` của hạn (lưu cạnh `deadline_at`)
    nudged_by_customer: bool = False           # r7 (c′): khách (người được hứa) đã nhắc lời hứa — code kiểm người gửi (xem `nudged_by_promisee`)
    fixed_occasion: bool = False               # r7 (c′): dịp không lặp lại — model + từ khoá trong trích dẫn verified


# ─── r7 (c′): trường điền cho đường bảng (P3b) ───────────────────────────────

_GRANULARITIES = frozenset(vi_time.GRANULARITIES)
CUSTOMER_NUDGE_VERSIONS = (None, "brain-out/1")
PROMISE_KINDS = ("nghia_vu_loi_hua", "viec_han")


def deadline_granularity(v: dict[str, Any] | None, text: str, anchor: datetime, due: datetime | None) -> str | None:
    """Độ mịn của hạn. Ưu tiên giá trị code đã lưu ở `entities.v.deadline_granularity` (verify ghi từ `vi_time.Due.granularity`). Dự phòng cho
    đơn vị cũ chưa lưu: tính lại bằng `vi_time.parse` trên chữ lời hứa, CHỈ nhận khi hạn tính lại trùng đúng `due` VÀ độ mịn không mơ hồ
    (`ambiguous`, hoặc `week` — cùng một mốc có thể là tuần hay ngày) ⇒ ngược lại None (dùng `deadline_at` như r6.6.1). Không hạn ⇒ None."""
    if due is None:
        return None
    stored = v.get("deadline_granularity") if isinstance(v, dict) else None
    if stored in _GRANULARITIES:
        return str(stored)
    d = vi_time.parse(text, anchor) if text else vi_time.Due(at=None)
    if d.at is None or d.granularity is None or d.at != due or d.ambiguous or d.granularity == "week":
        return None
    return d.granularity


def _occasion_re() -> re.Pattern[str]:
    from gh.refinery.verify import OCCASION_RE  # MỘT nguồn từ khoá dịp với verify (import muộn: tránh vòng)
    return OCCASION_RE


def occasion_in(body: str | None) -> bool:
    return bool(body) and _occasion_re().search(str(body).lower()) is not None


def _raw_of_message(message_id: Any) -> uuid.UUID | None:
    """Mã tin `m_<raw id>` (`runner.GEv.message_id`) ⇒ id tin gốc."""
    if not isinstance(message_id, str) or not message_id.startswith("m_"):
        return None
    try:
        return uuid.UUID(message_id[2:])
    except ValueError:
        return None


async def fixed_occasion_ok(db: AsyncSession, org_id: uuid.UUID, v: dict[str, Any], quotes: list[tuple[uuid.UUID | None, str]]) -> bool:
    """`fixed_occasion` = model khai (đã qua verify P3a) VÀ từ khoá dịp (`verify.OCCASION_RE`) có trong ĐÚNG tin `occasion_message_id` (thân tin gốc,
    như verify kiểm). Đơn vị không lưu `occasion_message_id` ⇒ dự phòng: trích dẫn verified của đơn vị."""
    if v.get("fixed_occasion") is not True:
        return False
    rid = _raw_of_message(v.get("occasion_message_id"))
    if rid is not None:
        body = (await db.execute(text("SELECT body_text FROM raw.events WHERE id = :i AND org_id = :o"), {"i": rid, "o": org_id})).scalar_one_or_none()
        return occasion_in(body) or any(r == rid and occasion_in(q) for r, q in quotes)
    return any(occasion_in(q) for _, q in quotes)


async def _message_meta(db: AsyncSession, org_id: uuid.UUID, message_id: Any) -> tuple[uuid.UUID | None, datetime] | None:
    """(người gửi, giờ gửi) của tin `m_<raw id>`."""
    rid = _raw_of_message(message_id)
    if rid is None:
        return None
    r = (await db.execute(text("""SELECT pi.person_id, e.occurred_at FROM raw.events e LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                  WHERE e.id = :i AND e.org_id = :o"""), {"i": rid, "o": org_id})).one_or_none()
    return None if r is None else (r.person_id, r.occurred_at)


async def _raw_senders(db: AsyncSession, org_id: uuid.UUID, raws: list[uuid.UUID]) -> list[Any]:
    return list((await db.execute(text("""SELECT e.occurred_at, pi.person_id FROM raw.events e LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                          WHERE e.org_id = :o AND e.id = ANY(:r)"""), {"o": org_id, "r": raws})).all())


async def nudged_by_promisee(db: AsyncSession, org_id: uuid.UUID, promisee: uuid.UUID | None,
                             units: list[tuple[dict[str, Any], dict[str, Any], list[uuid.UUID]]], promise_at: datetime | None = None) -> bool:
    """`Promise.nudged_by_customer` (spec r7 (c′)). `units` = [(entities.v, flags, raw id của trích dẫn verified)]. Tin nhắc phải gửi SAU giờ lời hứa
    thật `promise_at` (đường fact: `valid_from`; đường đơn vị: giờ tin sớm nhất do người KHÁC người được hứa gửi trong bằng chứng — `msg_at` của đơn vị
    là tin MỚI nhất nên không dùng được). `brain-out/2`: `nudge_message_id` còn giữ sau verify VÀ người gửi tin đó = người được hứa; `nudge_rejected` ⇒
    false. `brain-out/1` (thiếu `nudge_message_id`): `flags.nudged` VÀ ≥ 1 trích dẫn verified do người được hứa gửi sau lời hứa. Không rõ người được
    hứa ⇒ false."""
    if promisee is None:
        return False
    for v, fl, raws in units:
        cv, mid = v.get("contract_version"), v.get("nudge_message_id")
        if cv == "brain-out/2":
            if not mid or v.get("nudge_rejected") is True:
                continue
            meta = await _message_meta(db, org_id, mid)
            if meta is None or meta[0] != promisee:
                continue
            at = promise_at or await _promise_time(db, org_id, promisee, raws)
            if at is not None and meta[1] > at:
                return True
        elif cv in CUSTOMER_NUDGE_VERSIONS and fl.get("nudged") is True and not mid and raws:
            at = promise_at or await _promise_time(db, org_id, promisee, raws)
            if at is not None and any(r.person_id == promisee and r.occurred_at > at for r in await _raw_senders(db, org_id, raws)):
                return True
    return False


async def _promise_time(db: AsyncSession, org_id: uuid.UUID, promisee: uuid.UUID, raws: list[uuid.UUID]) -> datetime | None:
    """Giờ lời hứa thật của đơn vị: tin sớm nhất do người CÓ danh tính khác người được hứa gửi (dòng không rõ người gửi bị bỏ)."""
    ts = [r.occurred_at for r in await _raw_senders(db, org_id, raws) if r.person_id is not None and r.person_id != promisee]
    return min(ts) if ts else None


async def _unit_views(db: AsyncSession, org_id: uuid.UUID, raws: list[uuid.UUID]) -> list[tuple[dict[str, Any], dict[str, Any], list[uuid.UUID], list[tuple[uuid.UUID | None, str]]]]:
    """Các đơn vị LỜI HỨA/VIỆC có bằng chứng ở `raws`: (entities.v, flags, raw id trích dẫn verified, [(raw id, chữ trích dẫn verified)])."""
    units = (await db.execute(text("""
        SELECT DISTINCT mu.id, mu.observed_at, mu.flags, mu.entities FROM clean.meaning_units mu
        JOIN clean.evidence ce ON ce.meaning_unit_id = mu.id AND ce.meaning_observed_at = mu.observed_at
        WHERE mu.org_id = :o AND mu.superseded_by IS NULL AND mu.kind_hint = ANY(:k) AND ce.raw_event_id = ANY(:r)"""),
                             {"o": org_id, "r": raws, "k": list(PROMISE_KINDS)})).all()
    out: list[tuple[dict[str, Any], dict[str, Any], list[uuid.UUID], list[tuple[uuid.UUID | None, str]]]] = []
    for u in units:
        ev = (await db.execute(text("""SELECT raw_event_id, quote FROM clean.evidence WHERE meaning_unit_id = :u AND meaning_observed_at = :t
                                       AND quote_status = 'verified'"""), {"u": u.id, "t": u.observed_at})).all()
        e = u.entities if isinstance(u.entities, dict) else {}
        ev_v = e.get("v")
        v: dict[str, Any] = ev_v if isinstance(ev_v, dict) else {}
        out.append((v, dict(u.flags or {}), [x.raw_event_id for x in ev if x.raw_event_id], [(x.raw_event_id, str(x.quote)) for x in ev if x.quote]))
    return out


async def _staff_commit_covers(db: AsyncSession, org_id: uuid.UUID, raws: list[uuid.UUID | None]) -> bool:
    """Đã có thẻ lời hứa của NV hẹn giờ (`project_staff_commitments`) mang tin NV = một trong `raws` ⇒ đường khác không đẻ thẻ thứ hai."""
    ids = [str(r) for r in raws if r is not None]
    return bool(ids) and (await db.execute(text("SELECT 1 FROM biz.alerts WHERE org_id = :o AND facts ->> 'staff_msg_id' = ANY(:r) LIMIT 1"),
                                           {"o": org_id, "r": ids})).first() is not None


async def _sent_by_subject(db: AsyncSession, org_id: uuid.UUID, raw_id: uuid.UUID | None, subj: uuid.UUID, sets: g.OrgSets) -> bool:
    """P-T2: tin gốc của lời hứa do CHÍNH người hứa gửi (hoặc tin ĐI từ tài khoản của Sếp khi người hứa là Sếp)."""
    if raw_id is None:
        return False
    r = (await db.execute(text("""SELECT e.direction, pi.person_id FROM raw.events e LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                  WHERE e.id = :i AND e.org_id = :o"""), {"i": raw_id, "o": org_id})).one_or_none()
    if r is None:
        return False
    if r.direction == "outbound" and sets.is_owner(subj):
        return True
    return r.person_id is not None and r.person_id == await _person_of(db, subj)


def promise_source_class(promiser: g.Who | None, sender: g.Who | None) -> str:
    """P-T2 (spec r7.1 §4D.4, đường đơn vị): người hứa nói về lời hứa của CHÍNH mình (người gửi = người hứa) ⇒ `insider` (0,90) kể cả trong nhóm
    đông; người khác kể lại ⇒ `member`."""
    return "insider" if promiser is not None and sender is not None and promiser.entity_id == sender.entity_id else "member"


def work_type(body: str) -> str:
    f = g.fold(body)
    if CUTOVER.search(f):
        return "cutover_at_risk"
    if SUBMISSION.search(f):
        return "submission_today"
    return "deadline_today"


def level_input(c: PromiseCtx, now: datetime) -> levels.SignalIn:
    from gh.refinery.verify import parse_money_vnd

    conditional = c.condition_met is not None
    met = c.condition_met == "yes"
    deadline = None if (conditional and not met) else c.due_at          # hạn chỉ tính từ khi điều kiện đạt
    flags = levels.Flags(conditional=conditional, nudged=c.nudged, blocks_work=c.blocks_work, explicit_urgent=c.explicit_urgent)
    pm = levels.Promise(to_customer=c.to_customer, completed=c.completed, obstacle_reported=c.obstacle,
                        condition_met="yes" if met else ("no" if conditional else "none"),
                        nudged_by_customer=c.nudged_by_customer, fixed_occasion=c.fixed_occasion)
    cand: str | None = None
    work = levels.Work()
    if c.kind == "nghia_vu_loi_hua" and c.to_customer:
        cand = levels.G4
    elif c.owner_work:
        typ = work_type(c.text)
        passed = deadline is not None and deadline < now
        work = levels.Work(type=typ, owner_work=True, done=c.completed,                                       # type: ignore[arg-type]
                           late_signs=passed or c.nudged or c.blocks_work or c.explicit_urgent)
        cand = levels.G6
    money = parse_money_vnd(c.text)
    important = c.to_customer or c.owner_work
    return levels.SignalIn(
        kind=c.kind, msg_at=c.valid_from, event_at=c.valid_from, deadline_at=deadline, gate_candidate=cand, flags=flags, promise=pm, work=work,
        status="da_giu" if c.completed else "dang_mo", source_class=c.source_class,                          # type: ignore[arg-type]
        deadline_granularity=c.granularity if deadline is not None else None,                                 # type: ignore[arg-type]
        confidence_exist=c.confidence_exist, confidence_content=g.conf_content(c.source_class, c.agreement),
        impact_base=levels.impact_base(c.kind, important=important), money_vnd=money, money_basis="quoted" if money else None,
        harm_what=("lời hứa với khách có thể vỡ: " if c.to_customer else "có thể lỡ hạn: ") + g.safe(c.text, 100),
        thread=c.thread or levels.Thread(type="dm_agent", agent_in_thread=True), subject_entity_id=c.subject_entity_id)


def _due(v: Any) -> datetime | None:
    return profile_read.parse_when(v.get("due_at")) if isinstance(v, dict) else None


def _condition(v: Any, flagged: bool) -> str | None:
    cond = v.get("condition") if isinstance(v, dict) else None
    if isinstance(cond, dict):
        return "yes" if cond.get("met") == "yes" else "no"
    return "no" if flagged else None


async def _unit_flags(db: AsyncSession, org_id: uuid.UUID, raws: list[uuid.UUID]) -> dict[str, bool]:
    rows = (await db.execute(text("""
        SELECT mu.flags FROM clean.meaning_units mu JOIN clean.evidence ce ON ce.meaning_unit_id = mu.id AND ce.meaning_observed_at = mu.observed_at
        WHERE mu.org_id = :o AND ce.raw_event_id = ANY(:r)"""), {"o": org_id, "r": raws})).all()
    out: dict[str, bool] = {}
    for r in rows:
        for k, v in (r.flags or {}).items():
            out[k] = out.get(k, False) or v is True
    return out


async def _ctx(db: AsyncSession, org_id: uuid.UUID, f: Any, sets: g.OrgSets, *, subj: uuid.UUID, to: uuid.UUID | None) -> PromiseCtx:
    v = f.object_value if isinstance(f.object_value, dict) else {"text": str(f.object_value or "")}
    raws = list({*([f.source_raw_event_id] if f.source_raw_event_id else []), *(await db.execute(
        text("SELECT raw_event_id FROM mem.fact_evidence WHERE fact_id = :f AND raw_event_id IS NOT NULL"), {"f": f.id})).scalars().all()})
    uf = await _unit_flags(db, org_id, raws) if raws else {}
    gid, size = await g.raw_group(db, f.source_raw_event_id)
    kind = "viec_han" if f.predicate == "deadline" else "nghia_vu_loi_hua"
    to_customer = to is not None and to in sets.customers
    owner_side = sets.is_owner(subj) or subj in sets.staff                  # việc của Sếp (không phải việc của nhóm/tổ chức bất kỳ)
    obstacle = False
    if to_customer and gid is not None:
        pid = await _person_of(db, subj)
        # trở ngại phải do CHÍNH người hứa báo (người gửi tin hoặc được nhắc tên trong đơn vị), trong cùng nhóm, sau lời hứa
        obstacle = (await db.execute(text("""
            SELECT 1 FROM clean.meaning_units mu WHERE mu.org_id = :o AND mu.group_id = :g AND mu.kind_hint = 'nghia_vu_loi_hua'
              AND mu.superseded_by IS NULL AND mu.observed_at > :vf AND mu.flags ->> 'blocks_work' = 'true'
              AND ((CAST(:p AS uuid) IS NOT NULL AND mu.person_id = :p)
                   OR EXISTS (SELECT 1 FROM mem.mentions m WHERE m.meaning_unit_id = mu.id AND m.entity_id = :s))
            LIMIT 1"""), {"o": org_id, "g": gid, "vf": f.valid_from, "p": pid, "s": subj})).first() is not None
    views = await _unit_views(db, org_id, raws) if raws else []
    due = _due(v)
    nudged_c = to_customer and await nudged_by_promisee(db, org_id, await _person_of(db, to), [(x[0], x[1], x[2]) for x in views], f.valid_from)
    occasion = False
    for x in views:
        occasion = occasion or await fixed_occasion_ok(db, org_id, x[0], x[3])
    source_class = f.source_class
    if kind == "nghia_vu_loi_hua" and owner_side and source_class == "member" and await _sent_by_subject(db, org_id, f.source_raw_event_id, subj, sets):
        source_class = "insider"                                          # P-T2: Sếp/NV tự nói lời hứa của mình
    return PromiseCtx(
        kind=kind, text=str(v.get("text") or ""), valid_from=f.valid_from, due_at=due, to_customer=to_customer,
        owner_work=(owner_side or uf.get("blocks_work", False)) and not to_customer, obstacle=obstacle,
        condition_met=_condition(v, uf.get("conditional", False)), source_class=source_class,
        agreement=float(f.agreement) if f.agreement is not None else None, confidence_exist=float(f.confidence_exist),
        nudged=uf.get("nudged", False), blocks_work=uf.get("blocks_work", False), explicit_urgent=uf.get("explicit_urgent", False),
        subject_entity_id=to if kind == "nghia_vu_loi_hua" and to else subj, thread=g.thread_of(gid, size),
        granularity=deadline_granularity(next((x[0] for x in views if x[0].get("deadline_granularity")), None), str(v.get("text") or ""), f.valid_from, due),
        nudged_by_customer=nudged_c, fixed_occasion=occasion)


_FACT_COLS = """f.id, f.subject_id, f.predicate, f.object_value, f.object_entity_id, f.valid_from, f.valid_to, f.status, f.source_class,
                f.source_raw_event_id, f.agreement, f.confidence_exist"""


async def _parties(db: AsyncSession, f: Any) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    subj = await g.canonical(db, f.subject_id)
    v = f.object_value if isinstance(f.object_value, dict) else {}
    to = await g.canonical(db, uuid.UUID(str(v["to_entity_id"]))) if v.get("to_entity_id") else None
    return subj, to


async def _person_of(db: AsyncSession, ent: uuid.UUID | None) -> uuid.UUID | None:
    if ent is None:
        return None
    return (await db.execute(text("SELECT person_id FROM mem.entities WHERE id = :i"), {"i": ent})).scalar_one_or_none()


def _case_key(c: PromiseCtx, f: Any, promise_id: uuid.UUID | None) -> str:
    if c.kind == "viec_han":
        return levels.case_key("viec_han", at=f.valid_from, deadline_fact_id=str(f.id))
    return levels.case_key("nghia_vu_loi_hua", at=f.valid_from, promise_id=str(promise_id or f.id))


async def _write_row(db: AsyncSession, org_id: uuid.UUID, f: Any, c: PromiseCtx, p_from: uuid.UUID, p_to: uuid.UUID | None,
                     *, closed: bool) -> uuid.UUID | None:
    """Tạo/cập nhật dòng sổ `biz.promises` (chỉ lời hứa/nợ, có người hứa là hồ sơ người). Dòng đã `kept/broken/expired/cancelled` không bị đè."""
    conditional = c.condition_met is not None and c.condition_met != "yes"
    state = "kept" if closed else ("conditional" if conditional else "open")
    if c.due_at is None:
        return None                                                       # chưa có hạn ⇒ không có dòng sổ (cột due_at NOT NULL); tín hiệu vẫn theo dõi
    due = c.due_at                                                       # lời hứa có điều kiện: giữ hạn danh nghĩa (hạn thật tính khi điều kiện đạt)
    cond = None if c.condition_met is None else orjson.dumps(
        {"met": c.condition_met, "nominal_due": c.due_at.isoformat() if c.due_at else None}).decode()
    row = (await db.execute(text("SELECT id, state FROM biz.promises WHERE org_id = :o AND fact_id = :f"), {"o": org_id, "f": f.id})).one_or_none()
    if row is None:
        mu = (await db.execute(text("SELECT meaning_unit_id FROM mem.fact_evidence WHERE fact_id = :f AND meaning_unit_id IS NOT NULL LIMIT 1"),
                               {"f": f.id})).scalar_one_or_none()
        rid = (await db.execute(text("""
            INSERT INTO biz.promises (org_id, promiser_person_id, to_person_id, text, due_at, kept_at, state, conditions, fact_id, to_customer, meaning_unit_id)
            VALUES (:o, :p, :t, :x, :d, :k, :s, CAST(:c AS jsonb), :f, :tc, :mu) RETURNING id"""),
                                 {"o": org_id, "p": p_from, "t": p_to, "x": g.safe(c.text, 300) or "(không rõ nội dung)", "d": due,
                                  "k": f.valid_to if closed else None, "s": state, "c": cond, "f": f.id, "tc": c.to_customer, "mu": mu})).scalar_one()
        return rid
    if row.state in ("open", "conditional"):
        await db.execute(text("""UPDATE biz.promises SET state = :s, due_at = :d, conditions = CAST(:c AS jsonb), to_customer = :tc,
                                 kept_at = CASE WHEN :s = 'kept' THEN :k ELSE kept_at END WHERE id = :i"""),
                         {"s": state, "d": due, "c": cond, "tc": c.to_customer, "k": f.valid_to, "i": row.id})
    return row.id


async def _signal_for(db: AsyncSession, org_id: uuid.UUID, f: Any, c: PromiseCtx, key: uuid.UUID | None, now: datetime,
                      *, person_ids: list[uuid.UUID]) -> signals.Signal | None:
    ck = _case_key(c, f, key)
    old = (await db.execute(text("""SELECT id, status, level_input, facts ? 'unit_id' AS from_unit FROM biz.alerts
                                    WHERE org_id = :o AND kind = :k AND (case_key = :c OR facts ->> 'fact_id' = :f)
                                    ORDER BY (case_key = :c) DESC, created_at DESC LIMIT 1"""),
                            {"o": org_id, "k": c.kind, "c": ck, "f": str(f.id)})).one_or_none()
    if old is not None and old.from_unit:
        return None                                                       # đường đơn vị (T12) đã làm chủ lời hứa này: không đẻ thẻ thứ hai
    li = level_input(c, now)
    if old is not None and (old.status not in signals.OPEN or old.level_input == li.model_dump(mode="json")):
        return None                                                       # đã đóng (không sống lại) hoặc không đổi
    due = c.due_at if c.due_at and (c.condition_met in (None, "yes")) else None
    up = signals.UpsertIn(
        kind=c.kind, title="Lời hứa với khách sắp tới hạn" if c.to_customer else ("Nghĩa vụ cần theo dõi" if c.kind == "nghia_vu_loi_hua"
                                                                                  else "Việc có hạn"),
        summary=g.safe(c.text, 300) or None, detector="promises", detector_version="1", level_input=li,
        evidence=[signals.EvidenceIn(type="fact", id=f.id)], case_key=ck, fact_ids=[f.id], due_at=due,       # KHÔNG truyền raw_event_id: T1 gộp theo tin gốc, hai lời hứa cùng một tin sẽ thành một tín hiệu
        
        expires_at=max(now + timedelta(days=7), (due or now) + timedelta(days=3)), person_ids=person_ids,
        thread_key=None, facts={"fact_id": str(f.id)})
    return await signals.upsert(db, org_id, up, now)


# `pg_input_is_valid` (PG16): ngày sai như 2026-02-30 ⇒ NULL (coi như không có hạn) thay vì làm CAST lỗi chết cả lượt quét của tổ chức.
_DUE_TS = r"""CASE WHEN jsonb_typeof(f.object_value) = 'object'
      AND f.object_value ->> 'due_at' ~ '^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}(:?\d{2})?)?)?$'
      AND pg_input_is_valid(f.object_value ->> 'due_at', 'timestamptz')
      THEN CAST(f.object_value ->> 'due_at' AS timestamptz) END"""


async def project(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 500) -> list[signals.Signal]:
    """Quét sự thật nghĩa vụ/hạn: ghi sổ + tạo/cập nhật tín hiệu. Chạy lại an toàn (một fact ⇒ một dòng sổ, một tín hiệu).
    Lọc ở SQL (chỉ chiều Sếp/nhân viên hoặc bên nhận là Sếp/người quan trọng; bỏ fact không hạn, quá hạn > 3 ngày, đã đóng đã có sổ, đã bỏ qua)
    và xếp theo hạn GẦN `now` nhất — fact cũ không chặn lời hứa mới."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('intel_promises:' || :o))"), {"o": str(org_id)})
    sets = await g.org_sets(db, org_id)
    side = [*sets.owners, *sets.staff]
    tos = [*sets.owners, *sets.key_relations]
    rows = (await db.execute(text(f"""
        SELECT * FROM (
          SELECT {_FACT_COLS}, d.due_ts IS NULL AS nodue,
                 row_number() OVER (PARTITION BY (d.due_ts IS NULL)
                                    ORDER BY abs(extract(epoch FROM (COALESCE(d.due_ts, f.valid_from) - :now)))) AS rn
          FROM mem.facts f JOIN mem.entities se ON se.id = f.subject_id
          CROSS JOIN LATERAL (SELECT {_DUE_TS} AS due_ts) d
          LEFT JOIN LATERAL (SELECT COALESCE(te.merged_into, te.id) AS to_c FROM mem.entities te
                             WHERE te.id::text = f.object_value ->> 'to_entity_id' AND te.org_id = :o LIMIT 1) t ON jsonb_typeof(f.object_value) = 'object'
          WHERE f.org_id = :o AND f.predicate = ANY(:p) AND f.status IN ('asserted', 'owner_confirmed') AND f.valid_from >= :since
            -- fact không hạn / quá hạn > 3 ngày CHỈ vào lượt quét khi chưa có sổ hoặc tín hiệu (không chiếm chỗ mãi: chống lời hứa không hạn xả hàng loạt)
            AND (d.due_ts >= :dmin
                 OR NOT (EXISTS (SELECT 1 FROM biz.promises p WHERE p.fact_id = f.id)
                         OR EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.facts ->> 'fact_id' = f.id::text)))
            AND NOT EXISTS (SELECT 1 FROM ops.action_log al WHERE al.org_id = :o AND al.action = 'intel.promises.skip' AND al.target_id = f.id::text)
            AND ((f.predicate = 'deadline' AND f.valid_to IS NULL AND d.due_ts >= :dmin)
                 OR (f.predicate <> 'deadline'
                     AND (COALESCE(se.merged_into, se.id) = ANY(CAST(:side AS uuid[])) OR t.to_c = ANY(CAST(:tos AS uuid[])))
                     AND (f.valid_to IS NULL OR (d.due_ts IS NOT NULL AND NOT EXISTS (SELECT 1 FROM biz.promises p WHERE p.fact_id = f.id)))))
        ) x WHERE x.rn <= CASE WHEN x.nodue THEN CAST(:nn AS int) ELSE CAST(:n AS int) END
        ORDER BY x.nodue, x.rn LIMIT :n"""),  # noqa: S608
        {"o": org_id, "p": list(PREDICATES), "since": now - timedelta(days=WINDOW_DAYS), "dmin": now - timedelta(days=3), "now": now,
         "side": side, "tos": tos, "n": limit, "nn": max(1, limit // 5)})).all()
    out: list[signals.Signal] = []
    for f in rows:
        try:
            async with db.begin_nested():
                sig = await _project_one(db, org_id, f, sets, now)
            if sig is not None:
                out.append(sig)
        except signals.SignalError as e:
            log.warning("promises: bỏ qua fact %s: %s", f.id, type(e).__name__)
            await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:promises", action="intel.promises.skip",
                                   target_type="fact", target_id=str(f.id), result="ok", detail={"why": type(e).__name__})
    return out


async def _project_one(db: AsyncSession, org_id: uuid.UUID, f: Any, sets: g.OrgSets, now: datetime) -> signals.Signal | None:
    subj, to = await _parties(db, f)
    if subj is None:
        return None
    if await _staff_commit_covers(db, org_id, [f.source_raw_event_id]):
        return None                                                       # tin NV đã có thẻ lời hứa hẹn giờ (project_staff_commitments)
    c = await _ctx(db, org_id, f, sets, subj=subj, to=to)
    closed = f.valid_to is not None
    if f.predicate == "deadline":
        if closed or (c.due_at is not None and c.due_at < now - timedelta(days=3)):
            return None
        if not c.owner_work:
            return None                                                   # việc không phải của Sếp / không chặn việc Sếp
        return await _signal_for(db, org_id, f, c, None, now, person_ids=[])
    owner_side = sets.is_owner(subj) or subj in sets.staff
    other_to_owner = to is not None and (sets.is_owner(to) or to in sets.key_relations)
    if not (owner_side or other_to_owner):
        return None                                                       # giữa hai người ngoài
    p_from, p_to = await _person_of(db, subj), await _person_of(db, to)
    key: uuid.UUID | None = None
    if p_from is not None:
        key = await _write_row(db, org_id, f, c, p_from, p_to, closed=closed)
    if closed or not owner_side:
        return None                                                       # đã giữ ⇒ chỉ sổ; chiều người khác → Sếp ⇒ chỉ theo dõi
    sig = await _signal_for(db, org_id, f, c, key, now, person_ids=[p for p in (p_from, p_to) if p])
    if sig is not None and key is not None:
        await db.execute(text("UPDATE biz.promises SET signal_id = :s WHERE id = :i"), {"s": sig.id, "i": key})
    return sig


# ─── đơn vị (T12): lời hứa / việc có hạn model trích mà chưa có (hoặc chưa đủ) sự thật ─────────────────────────

async def _fact_of_unit(db: AsyncSession, org_id: uuid.UUID, uid: uuid.UUID) -> Any:
    return (await db.execute(text(f"""SELECT {_FACT_COLS} FROM mem.fact_evidence fe JOIN mem.facts f ON f.id = fe.fact_id
                                       WHERE fe.meaning_unit_id = :u AND f.org_id = :o AND f.predicate = ANY(:p)
                                       ORDER BY f.recorded_at LIMIT 1"""), {"u": uid, "o": org_id, "p": list(PREDICATES)})).first()  # noqa: S608


def parties_of_unit(p: Any, sets: g.OrgSets, *, outbound: bool, biz_group: bool) -> tuple[g.Who | None, g.Who | None, bool, bool]:
    """(người hứa, bên nhận, phía Sếp?, bên nhận là khách?) — hàm thuần trên tra cứu đã đọc.

    Người hứa = `people[role=nguoi_hua]` (vai nối CHẮC thực thể), không có ⇒ người gửi; bên nhận = `nguoi_duoc_hua`. "Phía Sếp" theo dữ liệu:
    Sếp, `staff_of`, `person_type = staff`, hoặc tin ĐI từ tài khoản của Sếp mà người hứa là người gửi. Khách theo dữ liệu: `customer_of` /
    `person_type = customer`. KHỞI ĐỘNG LẠNH (bảo thủ, T12 tự chọn vì spec chưa nói): chỉ trong nhóm Sếp đã xác nhận `kinh_doanh`, và chỉ
    suy MỘT phía khi phía kia đã chắc theo dữ liệu — người hứa chắc là phía Sếp ⇒ bên nhận (không phải người của Sếp) là khách khi Hồ sơ chưa
    có fact `customer_of` nào; bên nhận chắc là khách ⇒ người hứa (không phải khách) là phía Sếp khi chưa có fact `staff_of` nào. Hai phía
    cùng mù ⇒ không suy (khách hứa "mai em chuyển khoản" không bao giờ bị đọc thành lời hứa của Sếp)."""
    promiser = (p.roles.get("nguoi_hua") or [p.sender])[0]
    to = (p.roles.get("nguoi_duoc_hua") or [None])[0]
    if promiser is not None and to is not None and promiser.entity_id == to.entity_id:
        to = None
    side_hard = g.hard_owner_side(promiser, sets) or (outbound and promiser is not None and p.sender is not None
                                                      and promiser.entity_id == p.sender.entity_id)
    cust_hard = g.hard_customer(to, sets)
    side = side_hard or (cust_hard and biz_group and not sets.staff and promiser is not None and not g.hard_customer(promiser, sets))
    cust = cust_hard or (side_hard and g.cold_customer(to, sets, biz_group=biz_group))
    return promiser, to, side, cust


async def project_units(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 200) -> list[signals.Signal]:
    """Đơn vị `nghia_vu_loi_hua` / `viec_han` (đường brain-out/1) ⇒ tín hiệu + sổ, khi đường sự thật CHƯA làm chủ (đơn vị không có sự thật
    `promised_to/owes/deadline`, hoặc sự thật đó chưa thành tín hiệu vì người hứa chưa có `staff_of` — cài mới). Ai tới trước làm chủ: tín hiệu
    đơn vị mang `facts.fact_id` (nếu có) để `_signal_for` của đường sự thật không đẻ thẻ thứ hai và `sync_kept` đóng được. Chiều lời hứa như
    đường sự thật (CNS-087b): chỉ phía Sếp → ai đó mới có tín hiệu. Hạn = `deadline_at` đã kiểm (`vi_time.parse`, lưu ở `entities.v`)."""
    from gh.biz.intel.detectors import units as U

    rows = (await db.execute(text(U.pending_sql("intel.promises.unit_done")),
                             {"o": org_id, "k": ["nghia_vu_loi_hua", "viec_han"], "since": now - timedelta(days=U.WINDOW_DAYS), "n": limit})).all()
    if not rows:
        return []
    sets = await g.org_sets(db, org_id)
    out: list[signals.Signal] = []
    for u in rows:
        ev = await U.evidence_of(db, u)
        if not ev:
            continue
        try:
            async with db.begin_nested():
                sig = await _project_unit(db, org_id, u, ev, sets, now)
            if sig is not None:
                out.append(sig)
        except signals.SignalError as e:
            log.warning("promises: bỏ qua đơn vị %s: %s", u.id, type(e).__name__)
            await U.mark(db, org_id, u.id, type(e).__name__, action="intel.promises.unit_done")
        except Exception as e:  # noqa: BLE001 — một đơn vị hỏng không chặn cả lô (savepoint đã hoàn tác phần của nó)
            await U.failed(db, org_id, u.id, e, done_action="intel.promises.unit_done")
    return out


async def _project_unit(db: AsyncSession, org_id: uuid.UUID, u: Any, ev: list[Any], sets: g.OrgSets, now: datetime) -> signals.Signal | None:
    from gh.biz.intel.detectors import units as U

    f = await _fact_of_unit(db, org_id, u.id)
    if f is not None and (await db.execute(text("""SELECT 1 FROM biz.alerts WHERE org_id = :o AND facts ->> 'fact_id' = :f LIMIT 1"""),
                                           {"o": org_id, "f": str(f.id)})).first() is not None:
        await U.mark(db, org_id, u.id, "fact_path", action="intel.promises.unit_done")      # đường sự thật đã làm chủ
        return None
    if await _staff_commit_covers(db, org_id, [e.raw_event_id for e in ev]):
        await U.mark(db, org_id, u.id, "staff_commit_path", action="intel.promises.unit_done")   # tin NV đã có thẻ lời hứa (hẹn giờ)
        return None
    p = await U.people(db, org_id, u, sets, [e.raw_event_id for e in ev])
    biz = await g.business_group(db, org_id, p.group_id)
    promiser, to, side, cust = parties_of_unit(p, sets, outbound=p.outbound, biz_group=biz)
    v = U.verified(u)
    fl = dict(u.flags or {})
    kind = str(u.kind_hint)
    conditional = fl.get("conditional") is True
    quotes = [(r.raw_event_id, str(r.quote)) for r in (await db.execute(text("""SELECT raw_event_id, quote FROM clean.evidence WHERE meaning_unit_id = :u
                                             AND meaning_observed_at = :t AND quote_status = 'verified' AND quote IS NOT NULL"""), {"u": u.id, "t": u.observed_at})).all()]
    mine = [(v, fl, [e.raw_event_id for e in ev], quotes)]
    cust_to = kind == "nghia_vu_loi_hua" and cust
    c = PromiseCtx(
        kind=kind, text=str(u.conclusion or ""), valid_from=U._when(v.get("msg_at")) or u.observed_at, due_at=U._when(v.get("deadline_at")),
        to_customer=kind == "nghia_vu_loi_hua" and cust, owner_work=(side or fl.get("blocks_work") is True) and not (kind == "nghia_vu_loi_hua" and cust),
        completed=str(v.get("status_hint") or "") in U.STATUS_CLOSED,
        condition_met=("yes" if v.get("condition_met") == "yes" else "no") if conditional else None,
        source_class=promise_source_class(promiser, p.sender),
        confidence_exist=float(u.confidence or 0.8), nudged=fl.get("nudged") is True, blocks_work=fl.get("blocks_work") is True,
        explicit_urgent=fl.get("explicit_urgent") is True,
        subject_entity_id=(to.entity_id if to else None) if kind == "nghia_vu_loi_hua" else (promiser.entity_id if promiser else None),
        thread=g.thread_of(p.group_id, p.size),
        nudged_by_customer=cust_to and await nudged_by_promisee(db, org_id, to.person_id if to else None, [(x[0], x[1], x[2]) for x in mine]),
        fixed_occasion=await fixed_occasion_ok(db, org_id, v, quotes))
    c.granularity = deadline_granularity(v, c.text, c.valid_from, c.due_at)
    if (kind == "nghia_vu_loi_hua" and not side) or (kind == "viec_han" and not c.owner_work) or c.completed:
        await U.mark(db, org_id, u.id, "not_owner_side" if not c.completed else "completed", action="intel.promises.unit_done")
        return None                                                        # người khác → Sếp / giữa người ngoài: chỉ theo dõi (CNS-087b)
    key: uuid.UUID | None = None
    if kind == "nghia_vu_loi_hua" and promiser is not None and promiser.person_id is not None and c.due_at is not None:
        key = (await db.execute(text("""SELECT id FROM biz.promises WHERE org_id = :o AND (meaning_unit_id = :u OR fact_id = :f)
                                        ORDER BY (meaning_unit_id = :u) DESC LIMIT 1"""),
                                {"o": org_id, "u": u.id, "f": f.id if f is not None else None})).scalar_one_or_none()
        if key is None:
            cond = None if c.condition_met is None else orjson.dumps({"met": c.condition_met, "nominal_due": c.due_at.isoformat()}).decode()
            key = (await db.execute(text("""
                INSERT INTO biz.promises (org_id, promiser_person_id, to_person_id, text, due_at, state, conditions, fact_id, to_customer, meaning_unit_id)
                VALUES (:o, :p, :t, :x, :d, :s, CAST(:c AS jsonb), :f, :tc, :mu) RETURNING id"""),
                {"o": org_id, "p": promiser.person_id, "t": to.person_id if to else None, "x": g.safe(c.text, 300) or "(không rõ nội dung)",
                 "d": c.due_at, "s": "conditional" if c.condition_met == "no" else "open", "c": cond, "f": f.id if f is not None else None,
                 "tc": c.to_customer, "mu": u.id})).scalar_one()
    li = level_input(c, now)
    ck = (levels.case_key(kind, at=c.valid_from, promise_id=str(key or f"unit:{u.id}")) if kind == "nghia_vu_loi_hua"
          else levels.case_key(kind, at=c.valid_from, raw_event_id=str(ev[0].raw_event_id)))
    due = c.due_at if c.due_at and c.condition_met in (None, "yes") else None
    fx: dict[str, Any] = {"unit_id": str(u.id), **({"fact_id": str(f.id)} if f is not None else {}), "cold_start": bool(side and cust) and not (
        g.hard_owner_side(promiser, sets) and g.hard_customer(to, sets))}
    up = signals.UpsertIn(
        kind=kind, title="Lời hứa với khách sắp tới hạn" if c.to_customer else ("Nghĩa vụ cần theo dõi" if kind == "nghia_vu_loi_hua" else "Việc có hạn"),
        summary=g.safe(c.text, 300) or None, detector="promises", detector_version="1", level_input=li, evidence=U.unit_evidence(u, ev),
        case_key=ck, fact_ids=[f.id] if f is not None else [], due_at=due, expires_at=max(now + timedelta(days=7), (due or now) + timedelta(days=3)),
        person_ids=[x.person_id for x in (promiser, to) if x is not None and x.person_id], thread_key=f"g:{p.group_id}" if p.group_id else None,
        group_id=p.group_id, facts=fx)
    sig = await signals.upsert(db, org_id, up, now)
    if key is not None:
        await db.execute(text("UPDATE biz.promises SET signal_id = :s WHERE id = :i"), {"s": sig.id, "i": key})
    return sig


# ─── NV hẹn giờ với khách ("qua 15h em xử lý") ⇒ lời hứa với khách để cửa G4 canh (spec r7 (c′) `staff_owned`, CNS-179) ─────────────

STAFF_COMMIT_ACTION = "intel.promises.staff_commit"
COMPLAINT_KINDS = ("xung_dot_khieu_nai", "quan_he_nong_nguoi")


async def project_staff_commitments(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 100) -> list[signals.Signal]:
    """Đơn vị khiếu nại/quan hệ mà NV ĐÃ XÁC NHẬN trả lời đang xử lý (`status_hint = dang_xu_ly`) và HẸN GIỜ (`deadline_at` đã kiểm) ⇒ một
    `nghia_vu_loi_hua` (người hứa = NV, bên nhận = khách) để cổng 4 canh hạn. Chỉ khi chưa có đường lời hứa nào cho các tin này (đơn vị
    `nghia_vu_loi_hua` / sự thật `promised_to`/`owes` cùng tin gốc) — ai tới trước làm chủ, không đẻ thẻ thứ hai. Thẻ khiếu nại giữ nguyên
    (`staff_owned` ở đường của nó); thẻ lời hứa này mang `facts.staff_unit_id` (KHÔNG `unit_id`: `units.pending_sql` coi `unit_id` là "đơn vị đã chiếu")."""
    from gh.biz.intel.detectors import units as U

    rows = (await db.execute(text(f"""
        SELECT {U._UNIT_COLS} FROM clean.meaning_units mu
        WHERE mu.org_id = :o AND mu.kind_hint = ANY(:k) AND mu.superseded_by IS NULL AND mu.observed_at >= :since
          AND mu.entities #>> '{{v,status_hint}}' = 'dang_xu_ly' AND mu.entities #>> '{{v,deadline_at}}' IS NOT NULL
          AND mu.entities #>> '{{v,status_by_person_id}}' IS NOT NULL AND mu.person_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.facts ->> 'staff_unit_id' = mu.id::text)
          AND NOT EXISTS (SELECT 1 FROM ops.action_log al WHERE al.org_id = :o AND al.action = '{STAFF_COMMIT_ACTION}' AND al.target_id = mu.id::text)
          AND EXISTS (SELECT 1 FROM clean.evidence ce WHERE ce.meaning_unit_id = mu.id AND ce.meaning_observed_at = mu.observed_at
                        AND ce.quote_status = 'verified' AND ce.raw_event_id IS NOT NULL)
        ORDER BY mu.observed_at DESC LIMIT :n"""),  # noqa: S608 — hằng nội bộ
                             {"o": org_id, "k": list(COMPLAINT_KINDS), "since": now - timedelta(days=U.WINDOW_DAYS), "n": limit})).all()
    out: list[signals.Signal] = []
    for u in rows:
        try:
            async with db.begin_nested():
                sig = await _staff_commitment(db, org_id, u, now)
            if sig is not None:
                out.append(sig)
        except signals.SignalError as e:
            log.warning("promises: bỏ qua lời hẹn của NV (đơn vị %s): %s", u.id, type(e).__name__)
            await U.mark(db, org_id, u.id, type(e).__name__, action=STAFF_COMMIT_ACTION)
    return out


async def _staff_commitment(db: AsyncSession, org_id: uuid.UUID, u: Any, now: datetime, *, refresh: bool = False) -> signals.Signal | None:
    """Một đơn vị ⇒ một thẻ lời hứa của NV, hoặc dấu `STAFF_COMMIT_ACTION` (mọi kết cục KHÔNG thể đổi về sau). Chỉ để mở (không đánh dấu) khi NV
    chưa được Sếp xác nhận — Sếp xác nhận sau thì lượt sau lấy lại."""
    from gh.biz.intel import person_role
    from gh.biz.intel.detectors import units as U

    async def skip(why: str) -> None:
        if not refresh:                                                    # làm mới thẻ đã có: không đánh dấu gì
            await U.mark(db, org_id, u.id, why, action=STAFF_COMMIT_ACTION)

    v = U.verified(u)
    due, status_at, by = U._when(v.get("deadline_at")), U._when(v.get("status_at")), U._uuid(v.get("status_by_person_id"))
    if due is None or status_at is None or by is None or u.person_id is None:
        await skip("missing_fields")
        return None
    if due < now - timedelta(days=3):
        await skip("stale")
        return None
    ev = await U.evidence_of(db, u)
    raws = [e.raw_event_id for e in ev]
    if not raws:
        return None
    cust_ent = (await entities.ensure_person(db, org_id, u.person_id)).id
    if not await person_role.is_customer(db, org_id, u.person_id, cust_ent):
        await skip("not_customer")
        return None
    w_staff = await g.who_person(db, org_id, by)
    if w_staff is None or not g.staff_trusted(w_staff.person_type, locked=w_staff.locked, marked_at=w_staff.staff_marked_at):
        return None                                                        # NV chưa xác nhận: để mở (Sếp xác nhận sau thì lấy lại)
    # tin kích hoạt = tin KHÁCH mới nhất (trong bằng chứng) trước tin NV; tin NV phải sau nó. Khách nhắn SAU tin NV không chặn (đó là luật `staff_owned`,
    # không phải luật sinh lời hứa) — nó thành `nudged_by_customer`.
    trig = (await db.execute(text("""SELECT max(e.occurred_at) FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.org_id = :o AND e.id = ANY(:r) AND e.direction = 'inbound' AND pi.person_id = :p AND e.occurred_at <= :s"""),
                            {"o": org_id, "r": raws, "p": u.person_id, "s": status_at})).scalar_one_or_none()
    if trig is None or status_at <= levels._aware(trig):
        await skip("no_trigger_before_staff")
        return None
    staff_msg = (await db.execute(text("""SELECT e.id, e.received_at, e.body_text FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                          WHERE e.org_id = :o AND e.id = ANY(:r) AND pi.person_id = :p ORDER BY e.occurred_at DESC LIMIT 1"""),
                                 {"o": org_id, "r": raws, "p": by})).one_or_none()
    if staff_msg is None:
        await skip("no_staff_message")
        return None
    # hạn phải nằm trong CHÍNH tin NV (khách nêu "15h", NV chỉ nói "em đang kiểm tra kho" ⇒ NV chưa hẹn gì)
    said = vi_time.parse(str(staff_msg.body_text or ""), status_at)
    if said.at is None or said.at != due:
        await skip("deadline_not_from_staff")
        return None
    has_path = (await db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM clean.meaning_units m2 JOIN clean.evidence e2 ON e2.meaning_unit_id = m2.id AND e2.meaning_observed_at = m2.observed_at
                        WHERE m2.org_id = :o AND m2.kind_hint = 'nghia_vu_loi_hua' AND m2.superseded_by IS NULL AND e2.raw_event_id = :m)
            OR EXISTS (SELECT 1 FROM mem.facts f WHERE f.org_id = :o AND f.predicate IN ('promised_to', 'owes') AND f.source_raw_event_id = :m)"""),
                                  {"o": org_id, "m": staff_msg.id})).scalar_one()
    if not refresh and not has_path:
        has_path = await _staff_commit_covers(db, org_id, [staff_msg.id])      # một tin NV = một thẻ
    if has_path:
        await skip("has_promise_path")
        return None
    q = (await db.execute(text("""SELECT quote, source_field, span_start, span_end FROM clean.evidence WHERE meaning_unit_id = :u AND meaning_observed_at = :t
                                  AND raw_event_id = :r AND quote_status = 'verified' ORDER BY span_start LIMIT 1"""),
                          {"u": u.id, "t": u.observed_at, "r": staff_msg.id})).one_or_none()
    if q is None:
        await skip("no_staff_quote")
        return None
    gid, size = await g.raw_group(db, staff_msg.id)
    nudged = await _customer_spoke_after(db, org_id, u.person_id, gid, status_at)
    text_ = str(u.conclusion or "")
    c = PromiseCtx(kind="nghia_vu_loi_hua", text=text_, valid_from=status_at, due_at=due, to_customer=True, source_class="insider",   # P-T2: NV tự hẹn
                   confidence_exist=float(u.confidence or 0.8), subject_entity_id=cust_ent, thread=g.thread_of(gid, size), nudged=nudged,
                   nudged_by_customer=nudged, granularity=deadline_granularity(v, text_, U._when(v.get("msg_at")) or status_at, due))
    key = (await db.execute(text("SELECT id FROM biz.promises WHERE org_id = :o AND (meaning_unit_id = :u OR to_person_id = :t AND promiser_person_id = :p AND text = :x AND due_at = :d) LIMIT 1"),
                            {"o": org_id, "u": u.id, "t": u.person_id, "p": by, "x": g.safe(text_, 300) or "(không rõ nội dung)", "d": due})).scalar_one_or_none()
    if key is None:
        key = (await db.execute(text("""
            INSERT INTO biz.promises (org_id, promiser_person_id, to_person_id, text, due_at, state, fact_id, to_customer, meaning_unit_id)
            VALUES (:o, :p, :t, :x, :d, 'open', NULL, true, :mu) RETURNING id"""),
                                {"o": org_id, "p": by, "t": u.person_id, "x": g.safe(text_, 300) or "(không rõ nội dung)", "d": due, "mu": u.id})).scalar_one()
    li = level_input(c, now)
    up = signals.UpsertIn(
        kind="nghia_vu_loi_hua", title="Lời hứa với khách sắp tới hạn", summary=g.safe(text_, 300) or None, detector="promises", detector_version="1",
        level_input=li, evidence=[signals.EvidenceIn(type="raw_event", id=staff_msg.id, received_at=staff_msg.received_at, source_field=q.source_field,
                                                     span_start=int(q.span_start), span_end=max(int(q.span_end), int(q.span_start) + 1), quote=q.quote)],
        case_key=levels.case_key("nghia_vu_loi_hua", at=status_at, promise_id=f"staffmsg:{staff_msg.id}"), due_at=due,   # khoá theo TIN NV (một tin = một thẻ)
        expires_at=max(now + timedelta(days=7), due + timedelta(days=3)), person_ids=[by, u.person_id],
        thread_key=f"g:{gid}" if gid else None, group_id=gid,
        facts={"staff_unit_id": str(u.id), "staff_msg_id": str(staff_msg.id), "staff_ref": w_staff.ref, "staff_customer_person_id": str(u.person_id),
               "staff_status_at": status_at.isoformat(), "staff_group_id": str(gid) if gid else None})
    sig = await signals.upsert(db, org_id, up, now)
    await db.execute(text("UPDATE biz.promises SET signal_id = :s WHERE id = :i"), {"s": sig.id, "i": key})
    return sig


async def _customer_spoke_after(db: AsyncSession, org_id: uuid.UUID, customer: uuid.UUID, gid: uuid.UUID | None, after: datetime) -> bool:
    """Khách (người được hứa) có tin ĐẾN trong cùng luồng sau mốc `after` (giờ tin NV hẹn) — `nudged_by_customer` của lời hứa NV."""
    return (await db.execute(text("""SELECT 1 FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.org_id = :o AND pi.person_id = :c AND e.direction = 'inbound' AND e.occurred_at > :t
                                       AND e.group_id IS NOT DISTINCT FROM :g LIMIT 1"""), {"o": org_id, "c": customer, "t": after, "g": gid})).first() is not None


async def refresh_staff_commitments(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 100) -> list[signals.Signal]:
    """Đánh giá lại khi có tin mới: thẻ lời hứa NV hẹn giờ còn mở, `nudged_by_customer` còn false mà khách đã nhắn sau tin NV ⇒ dựng lại đầu vào qua `signals.upsert`
    (không cần đợi tin NV mới). Chạy lại an toàn (cờ đã bật thì không chọn lại)."""
    from gh.biz.intel.detectors import units as U

    rows = (await db.execute(text("""
        SELECT id, level, delivery, facts FROM biz.alerts
        WHERE org_id = :o AND kind = 'nghia_vu_loi_hua' AND detector = 'promises' AND status IN ('open', 'acknowledged') AND facts ? 'staff_msg_id'
          AND level_input IS NOT NULL AND NOT COALESCE((level_input #>> '{promise,nudged_by_customer}')::boolean, false)
        ORDER BY created_at DESC LIMIT :n"""), {"o": org_id, "n": limit})).all()
    out: list[signals.Signal] = []
    for r in rows:
        f = r.facts if isinstance(r.facts, dict) else {}
        try:
            at, cust = datetime.fromisoformat(str(f["staff_status_at"])), uuid.UUID(str(f["staff_customer_person_id"]))
            gid = uuid.UUID(str(f["staff_group_id"])) if f.get("staff_group_id") else None
            uid = uuid.UUID(str(f["staff_unit_id"]))
        except (KeyError, ValueError):
            continue
        if not await _customer_spoke_after(db, org_id, cust, gid, at):
            continue
        u = (await db.execute(text(f"SELECT {U._UNIT_COLS} FROM clean.meaning_units mu WHERE mu.org_id = :o AND mu.id = :u"), {"o": org_id, "u": uid})).first()  # noqa: S608
        if u is None:
            continue
        sig = await _staff_commitment(db, org_id, u, now, refresh=True)           # dựng lại đầu vào (nay có khách nhắc) qua `signals.upsert` — detector không tự ghi biz.alerts
        if sig is not None and (sig.level, sig.delivery) != (r.level, r.delivery):
            out.append(sig)
    return out


async def sync_kept(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> int:
    """Đóng nghĩa vụ + tín hiệu của lời hứa/việc đã xong. Hai chiều (không chỉ theo `biz.promises`):
    1) SỔ: fact đã bị `facts.close_kept` đóng (`valid_to` có, vẫn `asserted`) hoặc Sếp bấm "đã giữ" (`kept_at`) ⇒ `kept`; fact `retracted` ⇒ `cancelled`.
    2) TÍN HIỆU của detector này còn mở mà fact đã đóng/thu hồi, hoặc sổ đã `kept/cancelled` ⇒ `da_giu` (A16 ⇒ ⚪, tự `resolved`) / `dismissed`.
       Chạy được cả khi Sếp chưa có hồ sơ người (không có dòng sổ) và cho fact `deadline`.
    Trả số thay đổi (dòng sổ + tín hiệu); chạy lại ⇒ 0."""
    n = 0
    for p in (await db.execute(text("""
        SELECT p.id, p.state, p.kept_at, f.status, f.valid_to FROM biz.promises p JOIN mem.facts f ON f.id = p.fact_id
        WHERE p.org_id = :o AND p.state IN ('open', 'conditional')
          AND (p.kept_at IS NOT NULL OR f.status = 'retracted' OR (f.valid_to IS NOT NULL AND f.status IN ('asserted', 'owner_confirmed')))
        LIMIT 500"""), {"o": org_id})).all():
        cancelled = p.status == "retracted" and p.kept_at is None
        await db.execute(text("UPDATE biz.promises SET state = :s, kept_at = COALESCE(kept_at, :k) WHERE id = :i"),
                         {"s": "cancelled" if cancelled else "kept", "k": None if cancelled else (p.valid_to or now), "i": p.id})
        n += 1
    sets: g.OrgSets | None = None
    rows = (await db.execute(text(f"""
        SELECT a.id AS aid, a.case_key AS ck, a.kind AS akind, {_FACT_COLS}, f.status = 'retracted' AS retracted
        FROM biz.alerts a JOIN mem.facts f ON f.id = CAST(a.facts ->> 'fact_id' AS uuid)
        WHERE a.org_id = :o AND a.detector = 'promises' AND a.status IN ('open', 'acknowledged') AND a.facts ? 'fact_id'
          AND (f.status = 'retracted' OR (f.valid_to IS NOT NULL AND f.status IN ('asserted', 'owner_confirmed'))
               OR EXISTS (SELECT 1 FROM biz.promises p WHERE p.fact_id = f.id AND (p.kept_at IS NOT NULL OR p.state IN ('kept', 'cancelled'))))
        LIMIT 500"""), {"o": org_id})).all()  # noqa: S608
    for f in rows:
        n += 1
        if f.retracted:
            await signals.transition(db, org_id, f.aid, "dismissed", now=now, actor_type="system", actor_id="system:promises")
            continue
        subj, to = await _parties(db, f)
        if subj is None:
            await signals.transition(db, org_id, f.aid, "resolved", now=now, actor_type="system", actor_id="system:promises")
            continue
        sets = sets or await g.org_sets(db, org_id)
        c = await _ctx(db, org_id, f, sets, subj=subj, to=to)
        c.completed = True
        up = signals.UpsertIn(kind=f.akind, title="Lời hứa đã giữ" if c.kind == "nghia_vu_loi_hua" else "Việc đã xong", summary=g.safe(c.text, 300) or None,
                              detector="promises", detector_version="1", level_input=level_input(c, now),
                              evidence=[signals.EvidenceIn(type="fact", id=f.id)], case_key=f.ck, fact_ids=[f.id],
                              facts={"fact_id": str(f.id), "kept": True})
        await signals.upsert(db, org_id, up, now)
    return n


async def intel_promises(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import clock

    sm = sessionmaker()
    n = k = errors = 0
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                now = clock.now()
                k += await sync_kept(db, org, now)                       # đóng trước, để lời hứa đã giữ không bị chiếu lại
                n += len(await project(db, org, now))
                n += len(await project_units(db, org, now))             # T12: đơn vị chưa có sự thật / cài mới chưa có staff_of
                n += len(await project_staff_commitments(db, org, now))  # r7: NV hẹn giờ với khách ⇒ lời hứa để cổng 4 canh
                n += len(await refresh_staff_commitments(db, org, now))  # khách nhắc sau tin NV ⇒ bật `nudged_by_customer`
                await db.commit()
        except Exception:                                                # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_promises lỗi ở một tổ chức")
            errors += 1
    return {"projected": n, "kept": k, "errors": errors}


HOOKS: list[Any] = []
JOBS: list[CronJob] = [(intel_promises, {"minute": set(range(1, 60, 5))})]
