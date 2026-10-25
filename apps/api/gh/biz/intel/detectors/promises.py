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
from gh.biz.mem import profile_read
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
                        condition_met="yes" if met else ("no" if conditional else "none"))
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
    return PromiseCtx(
        kind=kind, text=str(v.get("text") or ""), valid_from=f.valid_from, due_at=_due(v), to_customer=to_customer,
        owner_work=(owner_side or uf.get("blocks_work", False)) and not to_customer, obstacle=obstacle,
        condition_met=_condition(v, uf.get("conditional", False)), source_class=f.source_class,
        agreement=float(f.agreement) if f.agreement is not None else None, confidence_exist=float(f.confidence_exist),
        nudged=uf.get("nudged", False), blocks_work=uf.get("blocks_work", False), explicit_urgent=uf.get("explicit_urgent", False),
        subject_entity_id=to if kind == "nghia_vu_loi_hua" and to else subj, thread=g.thread_of(gid, size))


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
    p = await U.people(db, org_id, u, sets, [e.raw_event_id for e in ev])
    biz = await g.business_group(db, org_id, p.group_id)
    promiser, to, side, cust = parties_of_unit(p, sets, outbound=p.outbound, biz_group=biz)
    v = U.verified(u)
    fl = dict(u.flags or {})
    kind = str(u.kind_hint)
    conditional = fl.get("conditional") is True
    c = PromiseCtx(
        kind=kind, text=str(u.conclusion or ""), valid_from=U._when(v.get("msg_at")) or u.observed_at, due_at=U._when(v.get("deadline_at")),
        to_customer=kind == "nghia_vu_loi_hua" and cust, owner_work=(side or fl.get("blocks_work") is True) and not (kind == "nghia_vu_loi_hua" and cust),
        completed=str(v.get("status_hint") or "") in U.STATUS_CLOSED,
        condition_met=("yes" if v.get("condition_met") == "yes" else "no") if conditional else None,
        source_class="insider" if (promiser is not None and p.sender is not None and promiser.entity_id == p.sender.entity_id) else "member",
        confidence_exist=float(u.confidence or 0.8), nudged=fl.get("nudged") is True, blocks_work=fl.get("blocks_work") is True,
        explicit_urgent=fl.get("explicit_urgent") is True,
        subject_entity_id=(to.entity_id if to else None) if kind == "nghia_vu_loi_hua" else (promiser.entity_id if promiser else None),
        thread=g.thread_of(p.group_id, p.size))
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
                await db.commit()
        except Exception:                                                # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_promises lỗi ở một tổ chức")
            errors += 1
    return {"projected": n, "kept": k, "errors": errors}


HOOKS: list[Any] = []
JOBS: list[CronJob] = [(intel_promises, {"minute": set(range(1, 60, 5))})]
