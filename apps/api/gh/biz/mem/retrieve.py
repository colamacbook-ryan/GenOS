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

Phạm vi hiện của sự thật (fix-gkb3, `fact_visible`): spec §4A.2 chỉ nói khối xuyên nhóm CÙNG THỰC THỂ mang sự thật đã kiểm, KHÔNG nói
sự thật sinh ở nhóm X được hiện ở nhóm Y nào. Chủ thể hay gặp nhất là chính Sếp (người gửi của mọi nhóm) nên "cùng thực thể" thành
"mọi nhóm": lời hứa với con ở nhóm gia đình, lương nhân viên hiện ở nhóm giao thương 120 người lạ (G-KB kb4). Áp MẶC ĐỊNH AN TOÀN:
(1) sự thật hiện ở luồng nguồn của bằng chứng (nhóm / 1-1 có tin bằng chứng, kể cả bằng chứng của bản cũ mà Sếp đã sửa);
(2) sang NHÓM khác chỉ khi: nhóm đích có ĐÚNG MỘT nhãn bối cảnh Sếp đã xác nhận (≠ `khac`), `member_count` đã biết và
    ≤ `CROSS_GROUP_MAX_MEMBERS`, sự thật `sensitivity='normal'`, mọi nhóm nguồn đã xác nhận bối cảnh và mọi nhãn nguồn = nhãn đích
    hoặc thuộc cặp một chiều `CROSS_CONTEXT_ALLOW` (kinh_doanh → nhan_vien; Gen quyết 25/10/2026); quy tắc tiền: `promised_to`/`owes`
    dính tiền/giá không đi giữa hai nhóm `kinh_doanh` (giá riêng khách A không sang nhóm khách B);
(3) sang 1-1 với P chỉ sự thật `normal` về P hoặc do P nói, từ nhóm đã xác nhận bối cảnh, không phải gia_dinh/ban_be;
(4) bằng chứng 1-1 không bao giờ sang luồng khác.
Hệ quả: sự thật gia đình/bạn bè/nhân viên/học tập không sang nhóm giao thương, công việc…; sức khoẻ, SĐT, STK, nhân sự
(sensitivity ≠ normal) không bao giờ xuyên luồng. Áp cho khối "Sự thật đã biết" và cạnh sự thật của khối "Quan hệ" (bước 2 cần cạnh
bước 1 cũng hiện được); cạnh nóng chỉ giữ người là thành viên của chính nhóm.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import contexts, entities, profile_read
from gh.data.common import outbound_to_sql
from gh.untrusted import wrap_untrusted

BUDGET = {"profile": 800, "facts": 2000, "related": 800, "history": 2400}
CHARS_PER_TOKEN = 3
HISTORY_DAYS = 90
HISTORY_TOP = 10
PROMISE_KINDS = ("nghia_vu_loi_hua", "viec_han")  # kênh "việc còn mở của luồng" (P-T4): lời hứa/việc có hạn chưa đóng
PROMISE_DAYS = 30                                 # cửa sổ của kênh lời hứa (ngắn hơn HISTORY_DAYS: lời hứa quá cũ đã tự hết hạn)
PROMISE_WEIGHT = 2.0                              # > kênh người (1,5) để lời hứa mở không bị đơn vị của người gửi lấn hết
PROMISE_SLOTS = 3                                 # trần chỗ riêng trong HISTORY_TOP: ≤ 3/10 để không lấn hết Lịch sử
PROMISE_CLOSED = ("kept", "broken", "expired", "cancelled")
RELATED_MAX_NODES = 20
RELATED_WALK_CAP = 500                            # trần dòng đi bộ 1–2 bước trước khi lọc phạm vi (Sếp là nút trung tâm)
RRF_K = 60
TRGM_MIN = 0.05                                   # sàn độ giống trigram (chỉ bỏ cặp gần như không chung chữ)
CROSS_GROUP_MAX_MEMBERS = 50                      # nhóm đông hơn ⇒ không nhận sự thật từ luồng khác (spec r6.6.1 §14.11 #7: lõi khách ≤ 50)
#: Cặp bối cảnh (nguồn → đích) được xuyên nhóm ngoài "cùng bối cảnh" — MỘT CHIỀU. Gen quyết 25/10/2026: nhóm nội bộ nhân viên cần
#: biết lời hứa với khách mà người của Sếp đã nhận; chiều ngược (nhan_vien → kinh_doanh: lương, nhân sự) KHÔNG.
CROSS_CONTEXT_ALLOW: frozenset[tuple[str, str]] = frozenset({("kinh_doanh", "nhan_vien")})
DM_PRIVATE_CONTEXTS = frozenset({"gia_dinh", "ban_be"})   # 1-1 với P không nhận sự thật từ nhóm gia đình/bạn bè
MONEY_PREDICATES = frozenset({"promised_to", "owes"})
MONEY_GUARD_CONTEXTS = frozenset({"kinh_doanh"})          # tiền/giá không đi giữa hai nhóm cùng bối cảnh này
_MONEY_WORDS = re.compile(r"\b(giá|tiền|triệu|nghìn|ngàn|đồng|vnd|vnđ|usd|chiết khấu|giảm giá|cọc|công nợ|thanh toán|chuyển khoản)\b")
_MONEY_NUM = re.compile(r"\d[\d.,]*\s*(k|tr|đ|%|\$)(?!\w)|\$\s*\d")
SUPERSEDE_DEPTH = 20                              # đi ngược chuỗi `supersedes` (bản Sếp sửa) tìm bằng chứng gốc
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


@dataclass(frozen=True)
class Scope:
    """Luồng đang phân tích + điều kiện SQL "tin/đơn vị thuộc luồng" (alias `e` = raw.events, `m` = clean.meaning_units)."""
    thread_key: str
    group_id: uuid.UUID | None
    dm_person: uuid.UUID | None
    raw_cond: str
    unit_cond: str
    params: dict[str, Any]
    contexts: frozenset[str]                      # bối cảnh Sếp đã xác nhận của nhóm hiện tại (rỗng = chưa xác nhận / 1-1)
    members: int | None                           # `core.groups.member_count` (None = không biết ⇒ không xuyên luồng)
    dm_entities: frozenset[uuid.UUID] = frozenset()   # 1-1: thực thể của người đối thoại (gồm bản đã gộp)


@dataclass(frozen=True)
class FactRef:
    """Thông tin một sự thật mà bộ lọc phạm vi cần (không đọc lại DB)."""
    id: uuid.UUID
    sensitivity: str
    context_code: str | None = None
    predicate: str = ""
    value_text: str = ""
    subject: uuid.UUID | None = None
    attributed_to: uuid.UUID | None = None


async def scope_of(db: AsyncSession, org_id: uuid.UUID, thread_key: str, pol: contexts.Policy) -> Scope:
    gid = contexts.group_of(thread_key)
    if gid is not None:
        # CHỈ số bridge báo: core.group_members chỉ có người đã từng nhắn ⇒ đếm nó coi nhóm 120 người là nhóm nhỏ (review fix-gkb3 #2)
        n = (await db.execute(text("SELECT member_count FROM core.groups WHERE id = :g AND org_id = :o"),
                              {"g": gid, "o": org_id})).scalar_one_or_none()
        return Scope(thread_key, gid, None, "e.group_id = :sg", "m.group_id = :sg", {"sg": gid},
                     frozenset(pol.contexts) if pol.confirmed else frozenset(), None if n is None else int(n))
    pid = None
    if thread_key.startswith("dm:"):
        try:
            pid = uuid.UUID(thread_key.split(":")[1])
        except (ValueError, IndexError):
            pid = None
    if pid is None:
        return Scope(thread_key, None, None, "FALSE", "FALSE", {}, frozenset(), None)
    ids: list[uuid.UUID] = []
    ext: list[str] = []
    for r in (await db.execute(text("SELECT id, external_id FROM core.person_identities WHERE person_id = :p"), {"p": pid})).all():
        ids.append(r.id)
        b = str(r.external_id or "").split("@", 1)[0]
        if b:
            ext.append(b)
    ents: frozenset[uuid.UUID] = frozenset()
    try:
        ents = frozenset(await entities.members(db, (await entities.ensure_person(db, org_id, pid)).id))
    except entities.EntityError:
        pass
    raw = (f"(e.group_id IS NULL AND (e.sender_identity_id = ANY(CAST(:sids AS uuid[])) OR "
           f"{outbound_to_sql('e', 'CAST(:sids AS uuid[])', 'CAST(:sext AS text[])')}))")
    return Scope(thread_key, None, pid, raw, "(m.group_id IS NULL AND m.person_id = :sp)", {"sids": ids, "sext": ext, "sp": pid},
                 frozenset(), None, ents)


def mentions_money(text_: str) -> bool:
    """Giá trị có dính tiền/giá (số kèm đơn vị tiền, %, hoặc từ giá/tiền/chiết khấu…) — quy tắc tiền (Gen quyết 25/10/2026)."""
    t = " " + text_.lower() + " "
    return bool(_MONEY_WORDS.search(t) or _MONEY_NUM.search(t))


def _allowed_sources(target: str) -> frozenset[str]:
    return frozenset({target} | {src for src, dst in CROSS_CONTEXT_ALLOW if dst == target})


async def fact_visible(db: AsyncSession, org_id: uuid.UUID, scope: Scope, facts_: list[FactRef]) -> set[uuid.UUID]:
    """Lọc phạm vi hiện (xem docstring module). Trả các id được hiện ở `scope`."""
    if not facts_:
        return set()
    ids = [f.id for f in facts_]
    rows = (await db.execute(text(f"""
        WITH RECURSIVE ch(root, id, d) AS (
            SELECT f.id, f.id, 0 FROM mem.facts f WHERE f.org_id = :o AND f.id = ANY(CAST(:ids AS uuid[]))
            UNION ALL
            SELECT ch.root, p.supersedes, ch.d + 1 FROM ch JOIN mem.facts p ON p.id = ch.id
            WHERE p.supersedes IS NOT NULL AND ch.d < {SUPERSEDE_DEPTH}
        ), raws AS (
            SELECT ch.root, x.raw_event_id AS rid FROM ch JOIN mem.fact_evidence x ON x.fact_id = ch.id WHERE x.raw_event_id IS NOT NULL
            UNION
            SELECT ch.root, f.source_raw_event_id FROM ch JOIN mem.facts f ON f.id = ch.id WHERE f.source_raw_event_id IS NOT NULL
        )
        SELECT r.root, e.group_id, ({scope.raw_cond}) AS same FROM raws r JOIN raw.events e ON e.id = r.rid AND e.org_id = :o
        UNION ALL
        SELECT ch.root, m.group_id, ({scope.unit_cond}) AS same
        FROM ch JOIN mem.fact_evidence x ON x.fact_id = ch.id JOIN clean.meaning_units m ON m.id = x.meaning_unit_id"""),  # noqa: S608 — điều kiện là hằng nội bộ
        {"o": org_id, "ids": ids, **scope.params})).all()
    same: set[uuid.UUID] = set()
    src: dict[uuid.UUID, set[uuid.UUID | None]] = {}
    for r in rows:
        if r.same:
            same.add(r.root)
        src.setdefault(r.root, set()).add(r.group_id)
    out = set(same)
    target: str | None = None
    if scope.group_id is not None:
        # nhóm đích nhiều nhãn ⇒ không nhận gì từ luồng khác (an toàn: "Tiệm nhà mình + khách quen" không hút cả giày của con lẫn lương
        # nhân viên — review fix-gkb3 #1); số người không biết hoặc đông ⇒ không (review #2)
        if (len(scope.contexts) != 1 or "khac" in scope.contexts or scope.members is None
                or scope.members > CROSS_GROUP_MAX_MEMBERS):
            return out
        target = next(iter(scope.contexts))
    elif scope.dm_person is None or not scope.dm_entities:
        return out
    groups = {g for s in src.values() for g in s if g is not None}
    gctx: dict[uuid.UUID, set[str]] = {}
    if groups:
        for r in (await db.execute(text("""SELECT group_id, context FROM mem.group_contexts
                                           WHERE org_id = :o AND source = 'owner' AND group_id = ANY(CAST(:g AS uuid[]))"""),
                                   {"o": org_id, "g": list(groups)})).all():
            gctx.setdefault(r.group_id, set()).add(r.context)
    for f in facts_:
        if f.id in out or f.sensitivity != "normal":
            continue
        s = src.get(f.id, set())
        if None in s:                              # có bằng chứng 1-1 (luồng khác) ⇒ chuyện riêng một người, không xuyên luồng
            continue
        srcs = [gctx.get(g, set()) for g in s if g is not None]
        if any(not c for c in srcs):               # nhóm nguồn chưa xác nhận bối cảnh
            continue
        if target is None:
            # 1-1 với P (review #3): sự thật về P hoặc do P nói, từ nhóm đã xác nhận, không phải gia đình/bạn bè
            ok = (bool(srcs) and (f.subject in scope.dm_entities or f.attributed_to in scope.dm_entities)
                  and all(not (c & DM_PRIVATE_CONTEXTS) for c in srcs))
        else:
            allowed = _allowed_sources(target)
            ok = all(c <= allowed for c in srcs) if srcs else (f.context_code is not None and f.context_code in allowed)
            # quy tắc tiền: lời hứa/nợ dính tiền/giá không đi giữa hai nhóm khách (giá riêng khách A ≠ nhóm khách B)
            if ok and target in MONEY_GUARD_CONTEXTS and f.predicate in MONEY_PREDICATES and mentions_money(f.value_text) \
                    and (any(c & MONEY_GUARD_CONTEXTS for c in srcs) or f.context_code in MONEY_GUARD_CONTEXTS):
                ok = False
        if ok:
            out.add(f.id)
    return out


async def _facts(db: AsyncSession, org_id: uuid.UUID, anchors: list[uuid.UUID], now: datetime, scope: Scope) -> list[PackItem]:
    if not anchors:
        return []
    members: list[uuid.UUID] = []
    for a in anchors:
        members.extend(await entities.members(db, a))
    # 1-1 với P: lấy cả sự thật do P nói (`attributed_to`) dù chủ thể là người khác (review fix-gkb3 #3)
    rows = (await db.execute(text("""
        SELECT f.id, f.predicate, f.status, f.object_value, f.object_entity_id, f.source_class, f.attributed_to,
               f.valid_from, f.valid_to, f.sensitivity, f.context_code, coalesce(s.merged_into, s.id) AS subj, s.display_name AS subj_name,
               o.display_name AS obj_name, a.display_name AS attr_name,
               (SELECT array_agg(x.raw_event_id) FROM mem.fact_evidence x WHERE x.fact_id = f.id AND x.raw_event_id IS NOT NULL) AS ev
        FROM mem.facts f JOIN mem.entities s ON s.id = f.subject_id
        LEFT JOIN mem.entities o ON o.id = f.object_entity_id LEFT JOIN mem.entities a ON a.id = f.attributed_to
        WHERE f.org_id = :o AND (f.subject_id = ANY(:m) OR f.attributed_to = ANY(CAST(:dmm AS uuid[]))) AND f.status IN ('owner_confirmed', 'asserted', 'disputed')
          AND f.valid_from <= :n AND (f.valid_to IS NULL OR f.valid_to > :n) AND f.evidence_n >= 1
          AND f.predicate NOT LIKE 'owner\\_%'"""), {"o": org_id, "m": members, "n": now, "dmm": list(scope.dm_entities)})).all()
    vis = await fact_visible(db, org_id, scope, [
        FactRef(r.id, r.sensitivity, r.context_code, r.predicate, profile_read.value_text(r.object_value) if r.object_value is not None else "",
                r.subj, r.attributed_to) for r in rows])
    rows = [r for r in rows if r.id in vis]

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
    (2) mới nhất của luồng; (2b) lời hứa/việc có hạn CÒN MỞ của luồng (`PROMISE_KINDS`, chưa có `biz.promises` đóng, ≤ `PROMISE_DAYS`
    ngày) trọng số `PROMISE_WEIGHT`, giữ chỗ riêng ≤ `PROMISE_SLOTS` — để lượt sau khách báo "xong rồi" model vẫn thấy lời hứa + tin bằng
    chứng mà đóng được; cùng `where` luồng nên không rò sang nhóm/bối cảnh khác; (3) chữ bỏ dấu (trigram, xếp theo độ giống — KHÔNG lọc bằng ngưỡng `%` 0.3: câu hỏi ghép nhiều tin dài
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
    promise_ids: set[uuid.UUID] = set()
    prom_rows = (await db.execute(text(f"""
        SELECT {cols} FROM clean.meaning_units m
        WHERE {where} AND m.kind_hint = ANY(CAST(:pk AS text[]))
          AND NOT EXISTS (SELECT 1 FROM biz.promises p WHERE p.meaning_unit_id = m.id AND p.org_id = m.org_id
                          AND (p.state = ANY(CAST(:pc AS text[])) OR p.kept_at IS NOT NULL))
        ORDER BY m.observed_at DESC LIMIT :np"""),  # noqa: S608
        {**params, "since": max(since, now - timedelta(days=PROMISE_DAYS)), "pk": list(PROMISE_KINDS),
         "pc": list(PROMISE_CLOSED), "np": PROMISE_SLOTS})).all()
    if prom_rows:
        add(prom_rows, PROMISE_WEIGHT)
        promise_ids = {r.id for r in prom_rows}
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
    order = sorted(ranks, key=lambda k: (-ranks[k], -info[k].observed_at.timestamp(), str(k)))
    keep = [k for k in order if k in promise_ids][:PROMISE_SLOTS]            # chỗ riêng của lời hứa mở
    top = keep + [k for k in order if k not in keep][:HISTORY_TOP - len(keep)]
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


async def _related(db: AsyncSession, org_id: uuid.UUID, anchors: list[uuid.UUID], scope: Scope) -> list[PackItem]:
    """Quan hệ 1–2 bước. Cạnh sự thật đi qua cùng phạm vi hiện như khối "Sự thật" (`fact_visible`; bước 2 cần cả cạnh bước 1 hiện
    được); cạnh nóng (`clean.relationships`) chỉ giữ NGƯỜI là thành viên của chính nhóm này — không lộ tên nhóm/người của bối cảnh khác."""
    if not anchors:
        return []
    walk = (await db.execute(text("""
        WITH RECURSIVE edges AS (
          SELECT f.id AS fact_id, f.sensitivity, f.context_code, coalesce(s.merged_into, s.id) AS a, coalesce(o.merged_into, o.id) AS b,
                 f.predicate
          FROM mem.facts f JOIN mem.entities s ON s.id = f.subject_id JOIN mem.entities o ON o.id = f.object_entity_id
          WHERE f.org_id = :o AND f.status IN ('owner_confirmed', 'asserted') AND f.object_entity_id IS NOT NULL
            AND f.predicate NOT LIKE 'owner\\_%' AND (f.valid_to IS NULL OR f.valid_to > now())
        ), walk AS (
          SELECT e.fact_id, e.sensitivity, e.context_code, e.a AS subj, CAST(NULL AS uuid) AS parent, e.a AS frm, e.b AS node, e.predicate,
                 1 AS depth
          FROM edges e WHERE e.a = ANY(:an)
          UNION
          SELECT e.fact_id, e.sensitivity, e.context_code, e.a, CAST(NULL AS uuid), e.b, e.a, e.predicate, 1 FROM edges e WHERE e.b = ANY(:an)
          UNION
          SELECT e.fact_id, e.sensitivity, e.context_code, e.a, w.fact_id, w.node, CASE WHEN e.a = w.node THEN e.b ELSE e.a END, e.predicate,
                 w.depth + 1
          FROM walk w JOIN edges e ON (e.a = w.node OR e.b = w.node) WHERE w.depth < 2 AND NOT (w.node = ANY(:an))
        )
        SELECT w.fact_id, w.sensitivity, w.context_code, w.subj, w.parent, w.node, w.predicate, w.depth, n.display_name, fr.display_name AS frm_name
        FROM walk w JOIN mem.entities n ON n.id = w.node JOIN mem.entities fr ON fr.id = w.frm
        WHERE NOT (w.node = ANY(:an)) AND n.forgotten_at IS NULL
        ORDER BY w.depth, w.fact_id DESC LIMIT :cap"""), {"o": org_id, "an": anchors, "cap": RELATED_WALK_CAP})).all()
    vis = await fact_visible(db, org_id, scope, list({FactRef(r.fact_id, r.sensitivity, r.context_code, r.predicate, "", r.subj) for r in walk}))
    best: dict[uuid.UUID, Any] = {}
    for r in sorted(walk, key=lambda r: (r.depth, str(r.fact_id))):
        if r.fact_id in vis and (r.parent is None or r.parent in vis) and r.node not in best:
            best[r.node] = r
    rows = sorted(best.values(), key=lambda r: (r.depth, r.display_name))[:RELATED_MAX_NODES]
    out = [PackItem(text=f"{r.frm_name} —{r.predicate}— {r.display_name} (bước {r.depth})", fact_id=r.fact_id, entity_id=r.node,
                    meta={"predicate": r.predicate, "depth": r.depth}) for r in rows]
    room = RELATED_MAX_NODES - len(out)
    if room > 0 and scope.group_id is not None:     # cạnh nóng: chỉ tới người cùng nhóm này
        seen = {i.entity_id for i in out} | set(anchors)
        rel = (await db.execute(text("""
            SELECT DISTINCT ON (t.id) t.id AS eid, t.display_name, r.weight FROM clean.relationships r
            JOIN mem.entities s ON s.person_id = r.from_id AND r.from_type = 'person' AND s.id = ANY(:an)
            JOIN mem.entities t ON r.to_type = 'person' AND t.person_id = r.to_id
            JOIN core.group_members gm ON gm.group_id = :g AND gm.person_id = r.to_id AND gm.left_at IS NULL
            WHERE r.org_id = :o AND r.state <> 'cold' AND t.forgotten_at IS NULL
            ORDER BY t.id, r.weight DESC"""), {"o": org_id, "an": anchors, "g": scope.group_id})).all()
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
    scope = await scope_of(db, org_id, thread_key, pol)
    out.facts, out.budget_used["facts"] = _fit(await _facts(db, org_id, anchors, now, scope), BUDGET["facts"])
    out.related, out.budget_used["related"] = _fit(await _related(db, org_id, anchors, scope), BUDGET["related"])
    qtext = " ".join(str(_g(e, "text") or "") for e in events[-8:])
    persons = [p for p in dict.fromkeys(_g(e, "sender_person_id") for e in events) if p is not None]
    out.history, out.budget_used["history"] = _fit(
        await _history(db, org_id, thread_key, qtext, now, query_embedding, time_hint, persons, anchors), BUDGET["history"])
    out.history.sort(key=lambda i: i.meta["observed_at"])        # cắt theo hạng RRF, đọc theo thời gian: việc trước → việc sau
    return out
