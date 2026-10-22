"""Phạm vi và ngữ cảnh của agent trực kênh (ARCHITECTURE §5 bước 1 — tất định, có ghi lại).

- `candidates(...)`: cặp (agent đang bật, đơn vị ý nghĩa) nằm trong phạm vi và chưa có quyết định.
- `build(...)`: ngữ cảnh gửi model. Mỗi mục có mã ngắn `C1…Cn` (model chỉ được trích các mã này) và một
  `EvidenceRef` thật (`ref`) — danh sách ref là đúng những gì đã đưa vào ngữ cảnh → `agent.decisions.context_refs`
  và `sources` của bản nháp. Dữ liệu liên quan chỉ lấy trong phạm vi của agent (nhóm/kênh được gán).
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.core.explain import DIMENSION_LABELS
from gh.biz.relations.service import persons_of_agent
from gh.chassis.masking import mask_for_prompt
from gh.data.common import DIRECT_MODE_SQL
from gh.memory.notebook import SECTIONS

RELATED_DAYS = 30
RELATED_UNITS = 8
SIMILAR_UNITS = 3
NOTEBOOK_ENTRIES = 12
HISTORY_ROWS = 5
DEFAULT_CONTEXT_TOKENS = 6000
OPEN_STAGES_EXCLUDED = ("won", "lost", "dormant")


@dataclass
class Candidate:
    agent_id: uuid.UUID
    unit_id: uuid.UUID
    observed_at: datetime
    group_id: uuid.UUID | None
    person_id: uuid.UUID | None
    channel_id: uuid.UUID
    channel_type: str
    listen_mode: str | None
    tagged: bool
    from_us: bool


@dataclass
class Item:
    handle: str                       # C1, C2… — mã model được phép trích
    kind: str                         # trigger | group | person | score | notebook | unit | opportunity | task | draft
    ref: dict[str, Any]               # EvidenceRef: {"type", "id", "code"?}
    label: str                        # nhãn hiện ở "Dữ liệu agent đã dùng"
    data: dict[str, Any]              # phần model thấy

    def line(self) -> str:
        # v0.1.71 (P2-3): phần model thấy là bản đã che OTP / mật khẩu / token / SĐT (giữ 3 số cuối) / số tài khoản;
        # số tiền, ngày giờ, mã đối tượng giữ nguyên. `ref`/`label` (hiện ở «Dữ liệu agent đã dùng») không đổi.
        shown = mask_for_prompt(orjson.loads(orjson.dumps({'kind': self.kind, **self.data}, default=str)))
        return f"{self.handle} {orjson.dumps(shown).decode()}"


@dataclass
class Context:
    agent: Any
    candidate: Candidate
    level: int
    items: list[Item] = field(default_factory=list)
    guardrails: list[str] = field(default_factory=list)
    group_name: str | None = None
    person_name: str | None = None

    @property
    def trigger(self) -> Item:
        return self.items[0]

    def by_handle(self) -> dict[str, Item]:
        return {i.handle: i for i in self.items}

    def refs(self) -> list[dict[str, Any]]:
        return [i.ref for i in self.items]

    def sources(self) -> list[dict[str, Any]]:
        return [{"label": i.label, "ref": i.ref} for i in self.items]


# ─── phạm vi ──────────────────────────────────────────────────────────────────

_CANDIDATES = """
WITH u AS (
  SELECT mu.id, mu.observed_at, mu.group_id, mu.person_id, g.listen_mode,
         COALESCE(g.channel_id, r.channel_id) AS channel_id,
         COALESCE(r.tagged, false) AS tagged, COALESCE(r.from_us, false) AS from_us
  FROM clean.meaning_units mu
  LEFT JOIN core.groups g ON g.id = mu.group_id
  LEFT JOIN LATERAL (
    SELECT (array_agg(e.channel_id))[1] AS channel_id, bool_or(e.mentions_agent) AS tagged,
           bool_and(e.direction = 'outbound') AS from_us
    FROM clean.evidence ev JOIN raw.events e ON e.id = ev.raw_event_id AND e.received_at = ev.raw_received_at
    WHERE ev.meaning_unit_id = mu.id) r ON true
  WHERE mu.org_id = :o AND mu.superseded_by IS NULL AND {filter}
)
SELECT a.id AS agent_id, u.id AS unit_id, u.observed_at, u.group_id, u.person_id, u.listen_mode, u.channel_id,
       u.tagged, u.from_us, c.type AS channel_type
FROM u
JOIN agent.identities a ON a.org_id = :o AND a.is_enabled
JOIN core.channels c ON c.id = u.channel_id
JOIN core.organizations o ON o.id = :o
LEFT JOIN core.persons pp ON pp.id = u.person_id AND pp.org_id = :o
WHERE (
  -- v0.1.65 (B8, F080): tin 1-1 của người được GÁN BOT này — agent đó phụ trách, kể cả khi kênh để tin 1-1 im lặng
  -- và agent không có phạm vi cả kênh (Sếp gán đích danh = bật trả lời cho người đó).
  (u.group_id IS NULL AND pp.attrs->>'agent_id' = a.id::text)
  OR (EXISTS (SELECT 1 FROM agent.channel_scopes s
               WHERE s.agent_id = a.id AND s.channel_id = u.channel_id
                 AND (s.group_id IS NULL OR s.group_id = u.group_id)
                 AND (s.hours IS NULL OR cardinality(s.hours) = 0
                      OR EXISTS (SELECT 1 FROM unnest(s.hours) h WHERE h @> u.observed_at)))
       OR (u.group_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM core.groups ag WHERE ag.id = u.group_id AND ag.assigned_agent_id = a.id)))
       AND (CASE WHEN u.group_id IS NULL
                 -- v0.1.65: tin 1-1 mặc định IM LẶNG (`silent`: lưu, không đưa cho agent trực); chỉ `reply` mới có
                 THEN {direct_mode} = 'reply'
                 -- QD 17/10 + sổ câu hỏi #22: nhóm `silent` (Lắng nghe im lặng) KHÔNG vào ứng viên — không bao giờ
                 -- trả lời nên không tốn một lượt LLM chỉ để ra silent/note; tin vẫn được lưu và sàng lọc như thường.
                 ELSE u.listen_mode = 'proactive' OR (u.listen_mode = 'tagged_only' AND u.tagged) END)
  )
  AND NOT EXISTS (SELECT 1 FROM agent.decisions d WHERE d.agent_id = a.id AND d.trigger_unit_id = u.id)
  -- v0.1.72: tin của chính Sếp (tài khoản bot đã ghép, attrs.is_owner) là LỆNH GỌI bot —
  -- gh.owner_channel.group_reply trả lời tại chỗ; agent trực không soạn thêm một nháp trùng.
  AND COALESCE(pp.attrs->>'is_owner', '') <> 'true'
ORDER BY u.tagged DESC, u.observed_at, a.id
LIMIT :n
"""


async def candidates(db: AsyncSession, org_id: uuid.UUID, *, unit_ids: list[uuid.UUID] | None = None,
                     since_hours: float | None = None, min_age_s: float = 0, limit: int = 500) -> list[Candidate]:
    """Cặp (agent, đơn vị) cần quyết định: agent đang bật, đơn vị trong phạm vi kênh/nhóm (hoặc nhóm gán cho
    agent), nhóm chủ động (`proactive`; nhóm "chỉ khi được tag" → chỉ đơn vị có tin tag; nhóm
    `silent` không vào — #22), chưa có quyết định. Tin tag trước."""
    params: dict[str, Any]
    if unit_ids is not None:
        if not unit_ids:
            return []
        flt, params = "mu.id = ANY(CAST(:ids AS uuid[]))", {"ids": [str(i) for i in unit_ids]}
    else:
        flt = ("mu.created_at > now() - make_interval(secs => :since) "
               "AND mu.created_at < now() - make_interval(secs => :age)")
        params = {"since": float((since_hours or 24) * 3600), "age": float(min_age_s)}
    sql = _CANDIDATES.format(filter=flt, direct_mode=DIRECT_MODE_SQL)
    rows = (await db.execute(text(sql), {"o": org_id, "n": limit, **params})).all()
    return [Candidate(r.agent_id, r.unit_id, r.observed_at, r.group_id, r.person_id, r.channel_id, r.channel_type,
                      r.listen_mode, bool(r.tagged), bool(r.from_us)) for r in rows]


@dataclass
class Reach:
    """Phần dữ liệu agent được đọc: nhóm trong phạm vi + kênh được gán cả kênh (gồm tin 1-1)."""

    groups: list[uuid.UUID]
    channels: list[uuid.UUID]
    persons: list[uuid.UUID] = field(default_factory=list)   # v0.1.65 (B8): người được gán BOT này (tin 1-1 của họ)

    def unit_sql(self, alias: str = "mu") -> str:
        return f"""({alias}.group_id = ANY(CAST(:reach_g AS uuid[]))
            OR ({alias}.group_id IS NULL AND {alias}.person_id = ANY(CAST(:reach_p AS uuid[])))
            OR ({alias}.group_id IS NULL AND EXISTS (
            SELECT 1 FROM clean.evidence rev JOIN raw.events re ON re.id = rev.raw_event_id
                   AND re.received_at = rev.raw_received_at
            WHERE rev.meaning_unit_id = {alias}.id AND re.channel_id = ANY(CAST(:reach_c AS uuid[])))))"""

    def params(self) -> dict[str, Any]:
        return {"reach_g": [str(g) for g in self.groups], "reach_c": [str(c) for c in self.channels],
                "reach_p": [str(p) for p in self.persons]}


async def reach(db: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID) -> Reach:
    groups = (await db.execute(text("""
        SELECT g.id FROM core.groups g
        WHERE g.org_id = :o AND (g.assigned_agent_id = :a OR EXISTS (
            SELECT 1 FROM agent.channel_scopes s WHERE s.agent_id = :a AND s.channel_id = g.channel_id
              AND (s.group_id IS NULL OR s.group_id = g.id)))"""), {"o": org_id, "a": agent_id})).scalars().all()
    channels = (await db.execute(text("""SELECT channel_id FROM agent.channel_scopes
                                         WHERE agent_id = :a AND group_id IS NULL"""),
                                 {"a": agent_id})).scalars().all()
    return Reach(list(groups), list(channels), await persons_of_agent(db, org_id, agent_id))


# ─── ngữ cảnh ─────────────────────────────────────────────────────────────────

def _short(s: str | None, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _tokens(item: Item) -> int:
    return max(1, len(item.line()) // 3)


def _out_of_reach(refs: Any, r: Reach) -> bool:
    """Mục sổ tay trỏ tới nhóm ngoài phạm vi agent → không đưa vào ngữ cảnh."""
    allowed = {str(g) for g in r.groups}
    for ref in refs or []:
        if isinstance(ref, dict) and ref.get("type") == "group" and str(ref.get("id")) not in allowed:
            return True
    return False


async def build(db: AsyncSession, org_id: uuid.UUID, agent: Any, cand: Candidate, level: int,
                context_tokens: int | None = None) -> Context:
    ctx = Context(agent=agent, candidate=cand, level=level)
    r = await reach(db, org_id, cand.agent_id)
    items: list[Item] = []

    def add(kind: str, ref: dict[str, Any], label: str, data: dict[str, Any]) -> None:
        if any(i.ref == ref for i in items):
            return
        items.append(Item(f"C{len(items) + 1}", kind, ref, label, data))

    # 1. đơn vị kích hoạt + trích dẫn nguyên văn
    u = (await db.execute(text("""SELECT id, event_type, side, conclusion, entities, confidence, observed_at
                                  FROM clean.meaning_units WHERE id = :i"""), {"i": cand.unit_id})).one()
    quotes = (await db.execute(text("""
        SELECT COALESCE(ev.quote, e.body_text) AS quote, e.direction, e.mentions_agent FROM clean.evidence ev
        JOIN raw.events e ON e.id = ev.raw_event_id AND e.received_at = ev.raw_received_at
        WHERE ev.meaning_unit_id = :i ORDER BY e.occurred_at"""), {"i": cand.unit_id})).all()
    add("trigger", {"type": "meaning_unit", "id": str(u.id)}, f"{u.event_type}: {_short(u.conclusion, 80)}",
        {"event_type": u.event_type, "side": u.side, "conclusion": u.conclusion, "entities": u.entities,
         "confidence": float(u.confidence), "observed_at": u.observed_at,
         "quotes": [_short(q.quote, 400) for q in quotes], "tagged_agent": cand.tagged, "from_our_side": cand.from_us})

    # 2. ID nhóm → hồ sơ nhóm
    if cand.group_id:
        g = (await db.execute(text("""SELECT id, code, name, kind, listen_mode, member_count FROM core.groups
                                      WHERE id = :g"""), {"g": cand.group_id})).one()
        ctx.group_name = g.name
        add("group", {"type": "group", "id": str(g.id), "code": g.code}, f"Nhóm {g.name}",
            {"code": g.code, "name": g.name, "group_kind": g.kind, "listen_mode": g.listen_mode,
             "members": g.member_count, "channel": cand.channel_type})

    # 3. ID người → hồ sơ sống + người phụ trách
    if cand.person_id:
        p = (await db.execute(text("""
            SELECT p.id, p.code, p.display_name, p.person_type, p.organization_name, p.title, p.relation_to_owner,
                   u.display_name AS owner_name
            FROM core.persons p LEFT JOIN core.users u ON u.id = p.owner_user_id WHERE p.id = :p"""),
            {"p": cand.person_id})).one()
        ctx.person_name = p.display_name
        add("person", {"type": "person", "id": str(p.id), "code": p.code}, f"Hồ sơ {p.display_name}",
            {"code": p.code, "name": p.display_name, "person_type": p.person_type, "org_name": p.organization_name,
             "title": p.title, "relation_to_owner": p.relation_to_owner, "owner": p.owner_name})

    # 4. điểm hiện tại + lý do
    subjects = [("person", cand.person_id, ctx.person_name), ("group", cand.group_id, ctx.group_name)]
    for st, sid, name in subjects:
        if sid is None:
            continue
        scores = (await db.execute(text("""
            SELECT c.dimension, c.value, c.trend, s.confidence, s.explanation FROM clean.current_scores c
            LEFT JOIN clean.score_snapshots s ON s.id = c.snapshot_id
            WHERE c.subject_type = :t AND c.subject_id = :s ORDER BY c.value DESC"""),
            {"t": st, "s": sid})).all()
        # Lý do của điểm chỉ giữ yếu tố có chứng cứ nằm trong phạm vi agent (điểm người gộp mọi nhóm).
        factor_units = {str(e.get("id")) for s in scores for f in ((s.explanation or {}).get("factors") or [])
                        if isinstance(f, dict) for e in (f.get("evidence") or []) if isinstance(e, dict)}
        visible = {str(x) for x in (await db.execute(text(f"""
            SELECT mu.id FROM clean.meaning_units mu WHERE mu.id = ANY(CAST(:ids AS uuid[])) AND {r.unit_sql()}"""),
            {"ids": sorted(factor_units), **r.params()})).scalars().all()} if factor_units else set()
        for s in scores:
            dim = DIMENSION_LABELS.get(s.dimension, s.dimension)
            why = [f.get("label") for f in ((s.explanation or {}).get("factors") or [])
                   if isinstance(f, dict) and all(str(e.get("id")) in visible for e in (f.get("evidence") or [])
                                                  if isinstance(e, dict))][:2]
            add("score", {"type": "score", "id": f"{st}:{sid}:{s.dimension}"},
                f"{name} — {dim} {float(s.value):.0f}",
                {"subject": st, "dimension": s.dimension, "label": dim, "value": float(s.value), "trend": s.trend,
                 "confidence": float(s.confidence) if s.confidence is not None else None, "because": why})

    # 5. sổ tay nhận thức của ID người và ID nhóm (mục ghim trước; "Giới hạn cho agent" là luật)
    for st, sid, _name in subjects:
        if sid is None:
            continue
        entries = (await db.execute(text("""
            SELECT e.id, e.section, e.body, e.refs, e.is_pinned, e.author, e.created_at FROM memory.entries e
            JOIN memory.notebooks n ON n.id = e.notebook_id
            WHERE n.org_id = :o AND n.subject_type = :t AND n.subject_id = :s AND e.archived_at IS NULL
            ORDER BY (e.section = 'guardrails') DESC, e.is_pinned DESC, e.created_at DESC LIMIT :n"""),
            {"o": org_id, "t": st, "s": sid, "n": NOTEBOOK_ENTRIES * 2})).all()
        taken = 0
        for e in entries:
            if taken >= NOTEBOOK_ENTRIES or _out_of_reach(e.refs, r):
                continue
            taken += 1
            if e.section == "guardrails":
                ctx.guardrails.append(e.body)
            add("notebook", {"type": "notebook_entry", "id": str(e.id)},
                f"Sổ tay {'nhóm' if st == 'group' else 'người'} · {SECTIONS.get(e.section, e.section)}: "
                f"{_short(e.body, 60)}",
                {"subject": st, "section": e.section, "section_label": SECTIONS.get(e.section, e.section),
                 "body": _short(e.body, 400), "pinned": e.is_pinned, "at": e.created_at})

    # 6. dữ liệu sạch liên quan (cùng nhóm hoặc cùng người, trong phạm vi) + gần nghĩa (pgvector)
    related = (await db.execute(text(f"""
        SELECT mu.id, mu.event_type, mu.conclusion, mu.observed_at, g.code AS g_code, p.code AS p_code
        FROM clean.meaning_units mu LEFT JOIN core.groups g ON g.id = mu.group_id
        LEFT JOIN core.persons p ON p.id = mu.person_id
        WHERE mu.org_id = :o AND mu.superseded_by IS NULL AND mu.id <> :u
          AND mu.observed_at > now() - make_interval(days => :days)
          AND (mu.group_id = :g OR mu.person_id = :p) AND {r.unit_sql()}
        ORDER BY mu.observed_at DESC LIMIT :n"""),  # noqa: S608 — unit_sql là hằng
        {"o": org_id, "u": cand.unit_id, "days": RELATED_DAYS, "g": cand.group_id, "p": cand.person_id,
         "n": RELATED_UNITS, **r.params()})).all()
    similar = (await db.execute(text(f"""
        SELECT mu.id, mu.event_type, mu.conclusion, mu.observed_at, g.code AS g_code, p.code AS p_code
        FROM clean.meaning_units mu LEFT JOIN core.groups g ON g.id = mu.group_id
        LEFT JOIN core.persons p ON p.id = mu.person_id,
             (SELECT embedding FROM clean.meaning_units WHERE id = :u AND embedding IS NOT NULL LIMIT 1) t
        WHERE mu.org_id = :o AND mu.superseded_by IS NULL AND mu.id <> :u AND mu.embedding IS NOT NULL
          AND mu.observed_at > now() - interval '90 days' AND {r.unit_sql()}
        ORDER BY mu.embedding <=> t.embedding LIMIT :n"""),  # noqa: S608 — unit_sql là hằng
        {"o": org_id, "u": cand.unit_id, "n": SIMILAR_UNITS, **r.params()})).all()
    for m, reason in [*((x, "related") for x in related), *((x, "similar") for x in similar)]:
        add("unit", {"type": "meaning_unit", "id": str(m.id)}, f"{m.event_type}: {_short(m.conclusion, 80)}",
            {"event_type": m.event_type, "conclusion": _short(m.conclusion, 300), "observed_at": m.observed_at,
             "group": m.g_code, "person": m.p_code, "why": reason})

    # 7. lịch sử tương quan: cơ hội / việc đang mở, bản nháp gần đây cho cùng người / nhóm
    opps = (await db.execute(text("""
        SELECT id, code, need, stage, value_vnd FROM biz.opportunities
        WHERE org_id = :o AND stage <> ALL(:closed) AND (person_id = :p OR source_group_id = :g)
          AND (source_group_id IS NULL OR source_group_id = ANY(CAST(:reach_g AS uuid[])))
        ORDER BY updated_at DESC LIMIT :n"""),
        {"o": org_id, "closed": list(OPEN_STAGES_EXCLUDED), "p": cand.person_id, "g": cand.group_id,
         "n": HISTORY_ROWS, **r.params()})).all()
    for o in opps:
        add("opportunity", {"type": "opportunity", "id": str(o.id), "code": o.code}, f"{o.code} · {_short(o.need, 60)}",
            {"code": o.code, "need": _short(o.need, 200), "stage": o.stage, "value_vnd": o.value_vnd})
    tasks = (await db.execute(text("""
        SELECT id, code, title, status, due_at FROM biz.tasks
        WHERE org_id = :o AND completed_at IS NULL AND status NOT IN ('done', 'cancelled')
          AND ((subject_type = 'person' AND subject_id = :p) OR (subject_type = 'group' AND subject_id = :g))
        ORDER BY due_at NULLS LAST LIMIT :n"""),
        {"o": org_id, "p": cand.person_id, "g": cand.group_id, "n": HISTORY_ROWS})).all()
    for t in tasks:
        add("task", {"type": "task", "id": str(t.id), "code": t.code}, f"{t.code} · {_short(t.title, 60)}",
            {"code": t.code, "title": _short(t.title, 200), "status": t.status, "due_at": t.due_at})
    recent = (await db.execute(text("""
        SELECT id, code, status, title, body->>'text' AS body, created_at, agent_id FROM biz.action_drafts
        WHERE org_id = :o AND created_at > now() - interval '14 days'
          AND (group_id = :g OR (subject_type = 'person' AND subject_id = :p))
        ORDER BY created_at DESC LIMIT :n"""),
        {"o": org_id, "g": cand.group_id, "p": cand.person_id, "n": HISTORY_ROWS})).all()
    for d in recent:
        add("draft", {"type": "draft", "id": str(d.id), "code": d.code}, f"{d.code} · {_short(d.title, 60)}",
            {"code": d.code, "status": d.status, "text": _short(d.body, 300), "at": d.created_at,
             "by_this_agent": d.agent_id == cand.agent_id})

    # Ngân sách token của binding: bỏ bớt từ cuối (lịch sử → dữ liệu liên quan), giữ đơn vị kích hoạt + hồ sơ.
    budget = context_tokens or DEFAULT_CONTEXT_TOKENS
    keep = min(3, len(items))
    while len(items) > keep and sum(_tokens(i) for i in items) > budget:
        items.pop()
    ctx.items = items
    return ctx


# ─── kiểm mã bịa ──────────────────────────────────────────────────────────────

_ID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_CODE_RE = re.compile(r"\b(?:PER|GRP|OPP|TSK|ACT|ALR|RAW|SYS|DEAL|CASE)-(?:[A-Z]{2}-)?\d+\b")


def mentioned_ids(s: str) -> set[str]:
    """UUID và mã đối tượng (PER-…, OPP-…, ACT-…) xuất hiện trong một đoạn chữ."""
    return {m.lower() for m in _ID_RE.findall(s)} | set(_CODE_RE.findall(s))
