# ruff: noqa: E501
"""Đường truy hồi (spec §4A.2): `pack(db, org, thread_key, events) -> ContextPack`.

Trần ≈ 6.000 token (Hồ sơ ≤ 800, sự thật ≤ 2.000, quan hệ ≤ 800, lịch sử ≤ 2.400; ước lượng ~3 ký tự/token tiếng Việt).
1. SQL có cấu trúc: người gửi + mention đã nối trong cửa sổ → `mem.facts` đang hiệu lực (ưu tiên owner_confirmed, disputed,
   lời hứa/hạn mở); bối cảnh nhóm.
2. Tìm lai trong CÙNG nhóm 90 ngày, gộp RRF: đơn vị của người/thực thể trong cửa sổ + mới nhất của luồng + trigram trên
   `mem.fold(conclusion)` (xếp hạng, không lọc ngưỡng) + cosine pgvector (nếu bên gọi đưa `query_embedding`); mỗi mục kèm id tin bằng chứng. Khối xuyên nhóm CÙNG THỰC THỂ chỉ mang SỰ THẬT đã kiểm (+ `attributed_to` + id bằng chứng), KHÔNG mang nguyên
   văn tin của nhóm khác (CNS-086) — đúng theo cấu tạo: facts chỉ có giá trị đã chuẩn hoá, không có thân tin.
3. Quan hệ 1–2 bước: CTE đệ quy trên `mem.facts` có `object_entity_id` (family_of, works_at, staff_of…), tối đa 20 nút, rồi
   `clean.relationships` (cạnh nóng).
4. Mở rộng theo thời gian: bên gọi (L2) đổi cụm "tuần trước" bằng `gh.biz.mem.vi_time` (L3) thành `time_hint` rồi truyền vào.
Mọi mục mang `fact_id` / `meaning_unit_id` để câu trả lời và tín hiệu trích được nguồn. Chữ của người ngoài bọc `wrap_untrusted`
kèm "theo lời X" khi render.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import contexts, entities, profile_read
from gh.untrusted import wrap_untrusted

BUDGET = {"profile": 800, "facts": 2000, "related": 800, "history": 2400}
CHARS_PER_TOKEN = 3
HISTORY_DAYS = 90
HISTORY_TOP = 10
RELATED_MAX_NODES = 20
RRF_K = 60
TRGM_MIN = 0.05                                   # sàn độ giống trigram (chỉ bỏ cặp gần như không chung chữ)
PREDICATE_PRIORITY = {"promised_to": 0, "owes": 0, "deadline": 0, "asked_waiting": 0, "scheduled_event": 1,
                      "health_event": 1}


def est_tokens(s: str) -> int:
    return math.ceil(len(s) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class PackItem:
    text: str
    fact_id: uuid.UUID | None = None
    meaning_unit_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    status: str | None = None
    attributed_to: uuid.UUID | None = None
    attributed_name: str | None = None
    untrusted: bool = False                       # nguồn outsider/forwarded ⇒ bọc wrap_untrusted khi render
    evidence_raw_ids: tuple[uuid.UUID, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextPack:
    thread_key: str
    policy: contexts.Policy
    profile_version: int
    profile: list[PackItem] = field(default_factory=list)
    facts: list[PackItem] = field(default_factory=list)
    related: list[PackItem] = field(default_factory=list)
    history: list[PackItem] = field(default_factory=list)
    budget_used: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return sum(self.budget_used.values())

    def ids(self) -> dict[str, set[uuid.UUID]]:
        return {"facts": {i.fact_id for i in [*self.profile, *self.facts, *self.related] if i.fact_id},
                "units": {i.meaning_unit_id for i in self.history if i.meaning_unit_id}}

    def render_for_prompt(self) -> str:
        parts = [contexts.render_for_prompt(self.policy)]
        if self.profile:
            parts.append("Hồ sơ Sếp:\n" + "\n".join(f"- [f_{i.fact_id}] {i.text}" for i in self.profile))
        trusted = [i for i in self.facts if not i.untrusted]
        outside = [i for i in self.facts if i.untrusted]
        if trusted:
            parts.append("Sự thật đã biết:\n" + "\n".join(f"- [f_{i.fact_id}] {i.text}" for i in trusted))
        if outside:
            body = "\n".join(f"- [f_{i.fact_id}] theo lời {i.attributed_name or 'người ngoài'}: {i.text}" for i in outside)
            parts.append(wrap_untrusted("sự thật từ người ngoài", body))
        if self.related:
            parts.append("Quan hệ liên quan:\n" + "\n".join(f"- {i.text}" for i in self.related))
        if self.history:
            parts.append("Lịch sử gần đây của nhóm:\n" + "\n".join(f"- [u_{i.meaning_unit_id}] {i.text}" for i in self.history))
        return "\n\n".join(parts)


def _g(ev: Any, name: str) -> Any:
    return ev.get(name) if isinstance(ev, dict) else getattr(ev, name, None)


def _fit(items: list[PackItem], cap: int) -> tuple[list[PackItem], int]:
    out: list[PackItem] = []
    used = 0
    for it in items:
        c = est_tokens(it.text)
        if used + c > cap:
            continue
        out.append(it)
        used += c
    return out, used


async def _anchor_entities(db: AsyncSession, org_id: uuid.UUID, thread_key: str, events: list[Any]) -> list[uuid.UUID]:
    """Thực thể đích của người gửi trong cửa sổ + mention đã nối + (1-1) người đối thoại."""
    ids: list[uuid.UUID] = []
    persons: list[uuid.UUID] = []
    for ev in events:
        p = _g(ev, "sender_person_id")
        if p is not None:
            persons.append(p)
    if thread_key.startswith("dm:"):
        try:
            persons.append(uuid.UUID(thread_key.split(":")[1]))
        except (ValueError, IndexError):
            pass
    for p in dict.fromkeys(persons):
        try:
            ids.append((await entities.ensure_person(db, org_id, p)).id)
        except entities.EntityError:
            continue
    raw_ids = [r for r in (_g(ev, "raw_event_id") or _g(ev, "id") for ev in events) if r is not None]
    if raw_ids:
        rows = (await db.execute(text("""
            SELECT DISTINCT coalesce(e.merged_into, e.id) FROM mem.mentions m JOIN mem.entities e ON e.id = m.entity_id
            WHERE m.org_id = :o AND m.status = 'linked' AND m.raw_event_id = ANY(:r)"""), {"o": org_id, "r": raw_ids})).scalars().all()
        ids.extend(rows)
    return list(dict.fromkeys(ids))


async def _facts(db: AsyncSession, org_id: uuid.UUID, anchors: list[uuid.UUID], now: datetime) -> list[PackItem]:
    if not anchors:
        return []
    members: list[uuid.UUID] = []
    for a in anchors:
        members.extend(await entities.members(db, a))
    rows = (await db.execute(text("""
        SELECT f.id, f.predicate, f.status, f.object_value, f.object_entity_id, f.source_class, f.attributed_to,
               f.valid_from, f.valid_to, f.sensitivity, coalesce(s.merged_into, s.id) AS subj, s.display_name AS subj_name,
               o.display_name AS obj_name, a.display_name AS attr_name,
               (SELECT array_agg(x.raw_event_id) FROM mem.fact_evidence x WHERE x.fact_id = f.id AND x.raw_event_id IS NOT NULL) AS ev
        FROM mem.facts f JOIN mem.entities s ON s.id = f.subject_id
        LEFT JOIN mem.entities o ON o.id = f.object_entity_id LEFT JOIN mem.entities a ON a.id = f.attributed_to
        WHERE f.org_id = :o AND f.subject_id = ANY(:m) AND f.status IN ('owner_confirmed', 'asserted', 'disputed')
          AND f.valid_from <= :n AND (f.valid_to IS NULL OR f.valid_to > :n) AND f.evidence_n >= 1
          AND f.predicate NOT LIKE 'owner\\_%'"""), {"o": org_id, "m": members, "n": now})).all()

    def rank(r: Any) -> tuple[int, int, int, float]:
        return (0 if r.status == "owner_confirmed" else 1 if r.status == "disputed" else 2,
                PREDICATE_PRIORITY.get(r.predicate, 5), 0 if r.valid_to is None else 1, -r.valid_from.timestamp())

    out: list[PackItem] = []
    for r in sorted(rows, key=rank):
        val = r.obj_name if r.object_entity_id is not None else profile_read.value_text(r.object_value)
        flag = " (đang tranh chấp)" if r.status == "disputed" else ""
        out.append(PackItem(text=f"{r.subj_name} · {r.predicate}: {val}{flag}", fact_id=r.id, entity_id=r.subj, status=r.status,
                            attributed_to=r.attributed_to, attributed_name=r.attr_name,
                            untrusted=r.source_class in ("outsider", "forwarded"),
                            evidence_raw_ids=tuple(r.ev or ()), meta={"predicate": r.predicate, "sensitivity": r.sensitivity}))
    return out


async def _history(db: AsyncSession, org_id: uuid.UUID, thread_key: str, query_text: str, now: datetime,
                   query_embedding: list[float] | None, time_hint: tuple[datetime, datetime] | None,
                   persons: list[uuid.UUID] | None = None, anchors: list[uuid.UUID] | None = None) -> list[PackItem]:
    """Top `HISTORY_TOP` đơn vị cùng luồng (cùng nhóm / cùng 1-1) trong 90 ngày (hoặc khoảng `time_hint`), gộp RRF từ các kênh:
    (1) liên quan người/thực thể của cửa sổ (người gửi + mention đã nối) — mang việc đang dở của chính chủ thể (lời hứa, đơn, tiền);
    (2) mới nhất của luồng; (3) chữ bỏ dấu (trigram, xếp theo độ giống — KHÔNG lọc bằng ngưỡng `%` 0.3: câu hỏi ghép nhiều tin dài
    gần như không bao giờ đạt ngưỡng với một kết luận ngắn); (4) cosine pgvector nếu bên gọi đưa `query_embedding`.
    Mỗi mục mang `meaning_unit_id` + id tin bằng chứng (`evidence_raw_ids`, `meta.evidence` = [(raw_event_id, raw_received_at)])."""
    gid = contexts.group_of(thread_key)
    since, until = now - timedelta(days=HISTORY_DAYS), now
    if time_hint is not None:
        since, until = time_hint
    where = "m.org_id = :o AND m.superseded_by IS NULL AND m.observed_at >= :since AND m.observed_at <= :until"
    params: dict[str, Any] = {"o": org_id, "since": since, "until": until, "n": HISTORY_TOP * 3}
    if gid is not None:
        where += " AND m.group_id = :g"
        params["g"] = gid
    elif thread_key.startswith("dm:"):
        try:
            where += " AND m.group_id IS NULL AND m.person_id = :p"
            params["p"] = uuid.UUID(thread_key.split(":")[1])
        except (ValueError, IndexError):
            return []
    else:
        return []
    ranks: dict[uuid.UUID, float] = {}
    info: dict[uuid.UUID, Any] = {}
    cols = "m.id, m.observed_at, m.conclusion, m.kind_hint"

    def add(rows: Any, weight: float = 1.0) -> None:
        for i, r in enumerate(rows):
            ranks[r.id] = ranks.get(r.id, 0.0) + weight / (RRF_K + i + 1)
            info[r.id] = r

    members: list[uuid.UUID] = []
    for a in anchors or []:
        members.extend(await entities.members(db, a))
    if persons or members:
        add((await db.execute(text(f"""
            SELECT {cols} FROM clean.meaning_units m
            WHERE {where} AND (m.person_id = ANY(CAST(:pp AS uuid[])) OR EXISTS (
                SELECT 1 FROM mem.mentions x WHERE x.meaning_unit_id = m.id AND x.status = 'linked' AND x.entity_id = ANY(CAST(:an AS uuid[]))))
            ORDER BY m.observed_at DESC LIMIT :n"""),  # noqa: S608
            {**params, "pp": list(persons or []), "an": members})).all(), 1.5)
    add((await db.execute(text(f"SELECT {cols} FROM clean.meaning_units m WHERE {where} ORDER BY m.observed_at DESC LIMIT :n"),  # noqa: S608
                          params)).all())
    q = " ".join(query_text.split())[:500]
    if q:
        add((await db.execute(text(f"""
            SELECT {cols} FROM clean.meaning_units m
            WHERE {where} AND similarity(mem.fold(m.conclusion), mem.fold(CAST(:q AS text))) > :minsim
            ORDER BY similarity(mem.fold(m.conclusion), mem.fold(CAST(:q AS text))) DESC LIMIT :n"""),  # noqa: S608
            {**params, "q": q, "minsim": TRGM_MIN})).all())
    if query_embedding:
        vec = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"
        add((await db.execute(text(f"""
            SELECT {cols} FROM clean.meaning_units m
            WHERE {where} AND m.embedding IS NOT NULL
            ORDER BY m.embedding <=> CAST(:v AS vector) LIMIT :n"""), {**params, "v": vec})).all())  # noqa: S608
    top = sorted(ranks, key=lambda k: (-ranks[k], -info[k].observed_at.timestamp(), str(k)))[:HISTORY_TOP]
    ev: dict[uuid.UUID, list[tuple[uuid.UUID, datetime]]] = {}
    if top:
        for r in (await db.execute(text("""
            SELECT meaning_unit_id, raw_event_id, raw_received_at FROM clean.evidence
            WHERE meaning_unit_id = ANY(:ids) AND quote_status = 'verified' ORDER BY raw_received_at, raw_event_id"""),
                                   {"ids": top})).all():
            lst = ev.setdefault(r.meaning_unit_id, [])
            if r.raw_event_id not in {x[0] for x in lst}:
                lst.append((r.raw_event_id, r.raw_received_at))
    return [PackItem(text=f"{info[k].observed_at.astimezone(UTC):%d/%m} {info[k].conclusion}", meaning_unit_id=k,
                     evidence_raw_ids=tuple(x[0] for x in ev.get(k, ())),
                     meta={"kind_hint": info[k].kind_hint, "observed_at": info[k].observed_at, "evidence": ev.get(k, [])}) for k in top]


async def _related(db: AsyncSession, org_id: uuid.UUID, anchors: list[uuid.UUID]) -> list[PackItem]:
    if not anchors:
        return []
    rows = (await db.execute(text("""
        WITH RECURSIVE edges AS (
          SELECT f.id AS fact_id, coalesce(s.merged_into, s.id) AS a, coalesce(o.merged_into, o.id) AS b, f.predicate
          FROM mem.facts f JOIN mem.entities s ON s.id = f.subject_id JOIN mem.entities o ON o.id = f.object_entity_id
          WHERE f.org_id = :o AND f.status IN ('owner_confirmed', 'asserted') AND f.object_entity_id IS NOT NULL
            AND f.predicate NOT LIKE 'owner\\_%' AND (f.valid_to IS NULL OR f.valid_to > now())
        ), walk AS (
          SELECT e.fact_id, e.a AS frm, e.b AS node, e.predicate, 1 AS depth FROM edges e WHERE e.a = ANY(:an)
          UNION
          SELECT e.fact_id, e.b, e.a, e.predicate, 1 FROM edges e WHERE e.b = ANY(:an)
          UNION
          SELECT e.fact_id, w.node, CASE WHEN e.a = w.node THEN e.b ELSE e.a END, e.predicate, w.depth + 1
          FROM walk w JOIN edges e ON (e.a = w.node OR e.b = w.node) WHERE w.depth < 2 AND NOT (w.node = ANY(:an))
        )
        SELECT DISTINCT ON (w.node) w.fact_id, w.node, w.predicate, w.depth, n.display_name, fr.display_name AS frm_name
        FROM walk w JOIN mem.entities n ON n.id = w.node JOIN mem.entities fr ON fr.id = w.frm
        WHERE NOT (w.node = ANY(:an)) AND n.forgotten_at IS NULL
        ORDER BY w.node, w.depth LIMIT :lim"""), {"o": org_id, "an": anchors, "lim": RELATED_MAX_NODES})).all()
    out = [PackItem(text=f"{r.frm_name} —{r.predicate}— {r.display_name} (bước {r.depth})", fact_id=r.fact_id, entity_id=r.node,
                    meta={"predicate": r.predicate, "depth": r.depth}) for r in sorted(rows, key=lambda r: (r.depth, r.display_name))]
    room = RELATED_MAX_NODES - len(out)
    if room > 0:                                    # cạnh nóng từ clean.relationships (tương tác người ↔ người/nhóm)
        seen = {i.entity_id for i in out} | set(anchors)
        rel = (await db.execute(text("""
            SELECT DISTINCT ON (t.id) t.id AS eid, t.display_name, r.weight FROM clean.relationships r
            JOIN mem.entities s ON s.person_id = r.from_id AND r.from_type = 'person' AND s.id = ANY(:an)
            JOIN mem.entities t ON (r.to_type = 'person' AND t.person_id = r.to_id) OR (r.to_type = 'group' AND t.group_id = r.to_id)
            WHERE r.org_id = :o AND r.state <> 'cold' AND t.forgotten_at IS NULL
            ORDER BY t.id, r.weight DESC"""), {"o": org_id, "an": anchors})).all()
        for r in sorted(rel, key=lambda x: float(x.weight), reverse=True):
            cid = await entities.canonical(db, r.eid)
            if cid in seen or room <= 0:
                continue
            seen.add(cid)
            out.append(PackItem(text=f"tương tác nhiều với {r.display_name}", entity_id=cid,
                                meta={"predicate": "interacts", "depth": 1}))
            room -= 1
    return out


async def pack(db: AsyncSession, org_id: uuid.UUID, thread_key: str, events: list[Any], *,
               query_embedding: list[float] | None = None, time_hint: tuple[datetime, datetime] | None = None,
               now: datetime | None = None) -> ContextPack:
    """Gói ngữ cảnh cho MỘT luồng. `events`: đối tượng/dict có `sender_person_id`, `raw_event_id` (hoặc `id`), `text`."""
    now = now or datetime.now(UTC)
    pol = await contexts.policy(db, org_id, thread_key)
    snap = await profile_read.snapshot(db, org_id)
    out = ContextPack(thread_key=thread_key, policy=pol, profile_version=snap.version)
    prof = [PackItem(text=f"{profile_read.SECTION_LABEL_VI.get(i.section, i.section)}: {i.text}", fact_id=i.fact_id,
                     status="owner_confirmed", entity_id=i.object_entity_id) for i in snap.items]
    out.profile, out.budget_used["profile"] = _fit(prof, BUDGET["profile"])
    anchors = await _anchor_entities(db, org_id, thread_key, events)
    out.facts, out.budget_used["facts"] = _fit(await _facts(db, org_id, anchors, now), BUDGET["facts"])
    out.related, out.budget_used["related"] = _fit(await _related(db, org_id, anchors), BUDGET["related"])
    qtext = " ".join(str(_g(e, "text") or "") for e in events[-8:])
    persons = [p for p in dict.fromkeys(_g(e, "sender_person_id") for e in events) if p is not None]
    out.history, out.budget_used["history"] = _fit(
        await _history(db, org_id, thread_key, qtext, now, query_embedding, time_hint, persons, anchors), BUDGET["history"])
    out.history.sort(key=lambda i: i.meta["observed_at"])        # cắt theo hạng RRF, đọc theo thời gian: việc trước → việc sau
    return out
