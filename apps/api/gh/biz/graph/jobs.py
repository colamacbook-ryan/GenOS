"""Việc nền của cụm Bản đồ quan hệ: dựng lại `clean.relationships` — nguồn dữ liệu **duy nhất** của toàn màn
(chưa cụm nào khác ghi bảng này, xem `docs/api/phase-3-graph.md`) — và việc định kỳ cho worker.

Bốn `kind` đúng enum đã ghi chú sẵn trong schema (`clean.relationships.kind`):
- `interacts` (Người↔Người): hai người cùng nhắn trong cùng nhóm, cùng ngày, trong `window_days` gần nhất.
  Trọng số = tổng (theo mọi nhóm chung) của `min(số tin của A trong ngày đó, số tin của B trong ngày đó)` —
  càng nhiều ngày cả hai cùng hoạt động trong cùng nhóm, cạnh càng nặng. `topic` = sản phẩm được nhắc nhiều
  nhất (`entities->>'product'`, cùng quy ước đã dùng ở `gh.biz.queue.routes._signals`) trong (các) nhóm chung.
- `shares_members` (Nhóm↔Nhóm): trọng số = hệ số chồng lấp (`shared / least(tổng A, tổng B)`, 0–1), `interactions`
  = số thành viên chung.
- `owns` (Người→Nhóm): người có vai trò `admin` trong nhóm (`core.group_members.role`) — "ai đang nắm". Trọng số
  = số tin người đó gửi trong nhóm (mặc định 1.0 nếu admin chưa từng nhắn, vẫn là ownership hợp lệ).
- `bridges` (Người→Nhóm): người là **cầu nối** — thành viên của ≥2 nhóm mà, nếu bỏ người đó ra, cặp nhóm đó
  không còn chung thành viên nào khác (loại bỏ chính người đó khỏi tập giao). Trọng số = số cặp nhóm người đó
  bắc cầu (tính trên toàn bộ tổ chức, dùng chung cho mọi cạnh `bridges` của người đó).

Ngưỡng "lạnh" (PLAN §3.5: "lạnh > 30 ngày") **cố định 30 ngày**, tách khỏi `window_days` (cửa sổ tính trọng số,
mặc định 90 ngày — hai khách chưa lạnh vẫn có thể có `weight` thấp nếu ít tương tác dù trong cửa sổ).

`HOOKS`: rỗng — đồ thị quan hệ cần nhìn toàn cục (thành viên nhóm, đồng xuất hiện) nên tính lại theo lịch,
không theo từng đơn vị ý nghĩa một (khác các hook nhẹ của cụm khác).
`JOBS`: `(hàm arq async (ctx) -> Any, dict tham số arq.cron)` — worker tự đăng ký.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob, Hook

WINDOW_DAYS = 90
COLD_DAYS = 30
MANAGED_KINDS = ("interacts", "shares_members", "owns", "bridges")


def edge_state(last_at: datetime | None) -> str:
    """`active` | `cold` — PLAN §3.5: lạnh khi > 30 ngày không tương tác, không có `last_at` cũng là lạnh."""
    if last_at is None:
        return "cold"
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=UTC)
    return "cold" if (datetime.now(UTC) - last_at).days > COLD_DAYS else "active"


# v0.1.40 (F-16): mỗi kind (trừ bridges) ghi bằng MỘT câu INSERT … SELECT … ON CONFLICT thay vì một câu cho mỗi
# cạnh. `clock_timestamp()`, không `now()`: cả lượt `recompute_org` chạy trong MỘT transaction, mà `now()` đứng yên ở
# thời điểm transaction bắt đầu — sớm hơn `run_start` — khiến bước dọn cạnh rác cuối hàm (`computed_at < run_start`)
# xoá nhầm mọi cạnh vừa upsert. `state` tính trong SQL KHỚP `edge_state()`: `.days > 30` ⟺ khoảng cách ≥ 31 ngày.
_STATE_SQL = (f"CASE WHEN src.last_at IS NULL OR clock_timestamp() - src.last_at >= interval '{COLD_DAYS + 1} days' "
              "THEN 'cold' ELSE 'active' END")
_UPSERT_TAIL = """
ON CONFLICT (org_id, from_type, from_id, to_type, to_id, kind, window_days) DO UPDATE SET
  weight = EXCLUDED.weight, interactions = EXCLUDED.interactions, last_at = EXCLUDED.last_at,
  state = EXCLUDED.state, topic = EXCLUDED.topic, computed_at = EXCLUDED.computed_at"""


def _upsert_sql(select_sql: str, *, from_type: str, from_col: str, to_type: str, to_col: str, kind: str,
                weight: str, interactions: str, topic: str = "NULL") -> str:
    """`INSERT INTO clean.relationships … SELECT … FROM (<select_sql>) src ON CONFLICT …` — một câu cho cả kind."""
    return f"""
INSERT INTO clean.relationships (org_id, from_type, from_id, to_type, to_id, kind, window_days, weight,
                                 interactions, last_at, state, topic, computed_at)
SELECT :o, '{from_type}', src.{from_col}, '{to_type}', src.{to_col}, '{kind}', :w, round(({weight})::numeric, 3),
       ({interactions})::int, src.last_at, {_STATE_SQL}, {topic}, clock_timestamp()
FROM ({select_sql}) src""" + _UPSERT_TAIL


async def _run_upsert(db: AsyncSession, sql: str, org_id: uuid.UUID, window_days: int) -> int:
    res = await db.execute(text(sql), {"o": org_id, "w": window_days})
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


# ─── Người↔Người: đồng xuất hiện trong cùng nhóm, cùng ngày ───────────────────────────────────────────────

_INTERACTS_SQL = """
WITH activity AS (
  SELECT pi.person_id, e.group_id, date_trunc('day', e.occurred_at) AS day,
         max(e.occurred_at) AS last_at, count(*) AS n
  FROM raw.events e
  JOIN core.channels c ON c.id = e.channel_id
  JOIN core.person_identities pi ON pi.id = e.sender_identity_id
  WHERE c.org_id = :o AND e.group_id IS NOT NULL AND e.direction = 'inbound'
    AND e.occurred_at > now() - make_interval(days => :w)
  GROUP BY 1, 2, 3
),
group_topic AS (
  SELECT group_id, topic FROM (
    SELECT group_id, entities->>'product' AS topic, count(*) AS n,
           row_number() OVER (PARTITION BY group_id ORDER BY count(*) DESC) AS rn
    FROM clean.meaning_units
    WHERE org_id = :o AND superseded_by IS NULL AND entities ? 'product'
      AND observed_at > now() - make_interval(days => :w)
    GROUP BY group_id, entities->>'product'
  ) t WHERE rn = 1
),
pairs AS (
  SELECT a.person_id AS pa, b.person_id AS pb, a.group_id,
         least(a.n, b.n) AS co_n, greatest(a.last_at, b.last_at) AS co_last
  FROM activity a JOIN activity b ON a.group_id = b.group_id AND a.day = b.day AND a.person_id < b.person_id
)
SELECT p.pa, p.pb, count(*) AS interactions, sum(p.co_n) AS weight, max(p.co_last) AS last_at,
       (array_agg(gt.topic ORDER BY p.co_n DESC) FILTER (WHERE gt.topic IS NOT NULL))[1] AS topic
FROM pairs p LEFT JOIN group_topic gt ON gt.group_id = p.group_id
GROUP BY p.pa, p.pb
"""


_INTERACTS_UPSERT = _upsert_sql(_INTERACTS_SQL, from_type="person", from_col="pa", to_type="person", to_col="pb",
                                kind="interacts", weight="src.weight", interactions="src.interactions",
                                topic="src.topic")


async def _recompute_interacts(db: AsyncSession, org_id: uuid.UUID, window_days: int) -> int:
    return await _run_upsert(db, _INTERACTS_UPSERT, org_id, window_days)


# v0.1.65 (F058): nhóm Sếp đã rời (`core.groups.left_at`) không còn tham gia thành viên chung / người nắm / cầu nối.

# ─── Nhóm↔Nhóm: thành viên chung ───────────────────────────────────────────────────────────────────────────

_SHARES_SQL = """
WITH gm AS (
  SELECT gm.group_id, gm.person_id FROM core.group_members gm
  JOIN core.groups g ON g.id = gm.group_id WHERE g.org_id = :o AND gm.left_at IS NULL AND g.left_at IS NULL
),
totals AS (SELECT group_id, count(*) AS total FROM gm GROUP BY group_id),
activity AS (
  SELECT e.group_id, max(e.occurred_at) AS last_at
  FROM raw.events e JOIN core.channels c ON c.id = e.channel_id
  WHERE c.org_id = :o AND e.group_id IS NOT NULL AND e.direction = 'inbound'
    AND e.occurred_at > now() - make_interval(days => :w)
  GROUP BY e.group_id
),
pairs AS (
  SELECT a.group_id AS ga, b.group_id AS gb, count(*) AS shared
  FROM gm a JOIN gm b ON a.person_id = b.person_id AND a.group_id < b.group_id
  GROUP BY a.group_id, b.group_id
)
SELECT p.ga, p.gb, p.shared, p.shared::numeric / least(ta.total, tb.total) AS weight,
       greatest(aa.last_at, ab.last_at) AS last_at
FROM pairs p
JOIN totals ta ON ta.group_id = p.ga JOIN totals tb ON tb.group_id = p.gb
LEFT JOIN activity aa ON aa.group_id = p.ga LEFT JOIN activity ab ON ab.group_id = p.gb
"""


_SHARES_UPSERT = _upsert_sql(_SHARES_SQL, from_type="group", from_col="ga", to_type="group", to_col="gb",
                             kind="shares_members", weight="src.weight", interactions="src.shared")


async def _recompute_shares(db: AsyncSession, org_id: uuid.UUID, window_days: int) -> int:
    return await _run_upsert(db, _SHARES_UPSERT, org_id, window_days)


# ─── ai đang nắm: admin nhóm ───────────────────────────────────────────────────────────────────────────────

_OWNS_SQL = """
WITH admins AS (
  SELECT gm.group_id, gm.person_id FROM core.group_members gm
  JOIN core.groups g ON g.id = gm.group_id
  WHERE g.org_id = :o AND gm.left_at IS NULL AND g.left_at IS NULL AND gm.role = 'admin'
),
activity AS (
  SELECT pi.person_id, e.group_id, count(*) AS n, max(e.occurred_at) AS last_at
  FROM raw.events e JOIN core.channels c ON c.id = e.channel_id
  JOIN core.person_identities pi ON pi.id = e.sender_identity_id
  WHERE c.org_id = :o AND e.group_id IS NOT NULL AND e.direction = 'inbound'
    AND e.occurred_at > now() - make_interval(days => :w)
  GROUP BY 1, 2
)
SELECT a.person_id, a.group_id, COALESCE(act.n, 0) AS interactions, act.last_at
FROM admins a LEFT JOIN activity act ON act.person_id = a.person_id AND act.group_id = a.group_id
"""


_OWNS_UPSERT = _upsert_sql(_OWNS_SQL, from_type="person", from_col="person_id", to_type="group", to_col="group_id",
                           kind="owns", weight="CASE WHEN src.interactions > 0 THEN src.interactions ELSE 1 END",
                           interactions="COALESCE(src.interactions, 0)")


async def _recompute_owns(db: AsyncSession, org_id: uuid.UUID, window_days: int) -> int:
    return await _run_upsert(db, _OWNS_UPSERT, org_id, window_days)


# ─── cầu nối: thành viên duy nhất nối hai nhóm không liên quan trực tiếp ──────────────────────────────────

_MEMBERS_SQL = """
SELECT gm.group_id, gm.person_id FROM core.group_members gm
JOIN core.groups g ON g.id = gm.group_id WHERE g.org_id = :o AND gm.left_at IS NULL AND g.left_at IS NULL
"""
_MEMBER_ACTIVITY_SQL = """
SELECT pi.person_id, e.group_id, max(e.occurred_at) AS last_at
FROM raw.events e JOIN core.channels c ON c.id = e.channel_id
JOIN core.person_identities pi ON pi.id = e.sender_identity_id
WHERE c.org_id = :o AND e.group_id IS NOT NULL AND e.direction = 'inbound'
  AND e.occurred_at > now() - make_interval(days => :w)
GROUP BY 1, 2
"""
_BRIDGES_UPSERT = _upsert_sql("""
SELECT b.person_id, b.group_id, b.weight, b.last_at
FROM unnest(CAST(:persons AS uuid[]), CAST(:groups AS uuid[]), CAST(:weights AS int[]),
            CAST(:lasts AS timestamptz[])) AS b(person_id, group_id, weight, last_at)""",
                              from_type="person", from_col="person_id", to_type="group", to_col="group_id",
                              kind="bridges", weight="src.weight", interactions="src.weight")


async def _recompute_bridges(db: AsyncSession, org_id: uuid.UUID, window_days: int) -> int:
    rows = (await db.execute(text(_MEMBERS_SQL), {"o": org_id})).all()
    groups: dict[uuid.UUID, set[uuid.UUID]] = {}
    persons: dict[uuid.UUID, set[uuid.UUID]] = {}
    for r in rows:
        groups.setdefault(r.group_id, set()).add(r.person_id)
        persons.setdefault(r.person_id, set()).add(r.group_id)
    act_rows = (await db.execute(text(_MEMBER_ACTIVITY_SQL), {"o": org_id, "w": window_days})).all()
    last_activity = {(r.person_id, r.group_id): r.last_at for r in act_rows}

    edges: list[tuple[uuid.UUID, uuid.UUID, int, datetime | None]] = []
    for person_id, gset in persons.items():
        if len(gset) < 2:
            continue
        glist = sorted(gset, key=str)
        bridged_pairs = 0
        bridged_groups: set[uuid.UUID] = set()
        for i in range(len(glist)):
            for j in range(i + 1, len(glist)):
                gi, gj = glist[i], glist[j]
                shared_excl = (groups[gi] & groups[gj]) - {person_id}
                if not shared_excl:
                    bridged_pairs += 1
                    bridged_groups.add(gi)
                    bridged_groups.add(gj)
        if not bridged_pairs:
            continue
        for g in bridged_groups:
            edges.append((person_id, g, bridged_pairs, last_activity.get((person_id, g))))
    if not edges:
        return 0
    await db.execute(text(_BRIDGES_UPSERT), {
        "o": org_id, "w": window_days, "persons": [e[0] for e in edges], "groups": [e[1] for e in edges],
        "weights": [e[2] for e in edges], "lasts": [e[3] for e in edges]})
    return len(edges)


async def recompute_org(db: AsyncSession, org_id: uuid.UUID, window_days: int = WINDOW_DAYS) -> dict[str, int]:
    """Dựng lại toàn bộ 4 `kind` do cụm này quản lý cho một tổ chức — UPSERT theo cạnh, rồi xoá cạnh không còn
    hợp lệ (không được đụng tới ở lượt chạy này, phát hiện qua `computed_at < run_start`).

    `run_start` đọc từ `clock_timestamp()` của chính CSDL (không phải đồng hồ Python) — để so sánh được với
    `computed_at` (cũng ghi bằng `clock_timestamp()` ở `_upsert_sql`) dù cả hàm chạy trong một transaction.

    v0.1.40 (F-16): mọi truy vấn raw.events giới hạn trong cửa sổ `window_days`; mỗi kind ghi bằng MỘT câu upsert."""
    run_start = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    counts = {"interacts": await _recompute_interacts(db, org_id, window_days),
              "shares_members": await _recompute_shares(db, org_id, window_days),
              "owns": await _recompute_owns(db, org_id, window_days),
              "bridges": await _recompute_bridges(db, org_id, window_days)}
    await db.execute(text("""
        DELETE FROM clean.relationships
        WHERE org_id = :o AND window_days = :w AND kind = ANY(:kinds) AND computed_at < :run_start"""),
        {"o": org_id, "w": window_days, "kinds": list(MANAGED_KINDS), "run_start": run_start})
    return counts


async def graph_recompute(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    out: dict[str, int] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        async with sm() as db:
            counts = await recompute_org(db, org)
            await db.commit()
        out[str(org)] = sum(counts.values())
    return out


HOOKS: list[Hook] = []
JOBS: list[CronJob] = [(graph_recompute, {"minute": set(range(7, 60, 15))})]
