# ruff: noqa: E501
"""Độ nóng quan hệ theo tuần (L4, spec §4A.1 `mem.relation_heat_weekly`). Tính CHỈ từ metadata của `raw.events` (tần suất, có qua có lại,
ai chủ động, độ trễ trả lời) — không đọc nội dung tin. Dựng lại được (xoá-ghi lại cả cửa sổ), chạy lại an toàn.

`entity_a` = thực thể Sếp; `entity_b` = người (tin 1-1) hoặc nhóm. Tuần bắt đầu thứ Hai theo giờ Việt Nam.
`series(db, org, a, b, weeks)` trả chuỗi tuần (cũ → mới, bù 0) cho Bước 2 vẽ và cho T5.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import entities, profile

VN = ZoneInfo("Asia/Ho_Chi_Minh")
PROJECTION_VERSION = 1
SESSION_GAP = timedelta(hours=6)       # im lặng ≥ 6 giờ rồi nhắn lại = bắt đầu một cuộc trò chuyện mới ("chủ động")
REPLY_MAX = timedelta(hours=24)
NO_REPLY_KINDS = ("reaction", "sticker", "system")
DEFAULT_WEEKS = 12
MAX_WEEKS = 52
FALLBACK_INBOUND = timedelta(days=7)
BATCH = 5000                           # số tin đọc mỗi lô (khoá theo (occurred_at, id)) — bộ nhớ không phụ thuộc cỡ cửa sổ


def state_of(heat: float) -> str:
    return "hot" if heat >= 60 else "warm" if heat >= 30 else "cool" if heat >= 8 else "cold"


def week_start(t: datetime) -> date:
    local = t.astimezone(VN)
    return (local - timedelta(days=local.weekday())).date()


def score(msgs_ab: int, msgs_ba: int, init_a: int, init_b: int, reply_median_s: int | None) -> float:
    """0–100: lượng (log), có qua có lại, cân bằng chủ động, nhanh trả lời. Thuần hàm — dễ kiểm."""
    total = msgs_ab + msgs_ba
    if total <= 0:
        return 0.0
    volume = min(1.0, math.log1p(total) / math.log1p(30))
    recip = min(msgs_ab, msgs_ba) / max(msgs_ab, msgs_ba) if msgs_ab and msgs_ba else 0.0
    init = min(init_a, init_b) / max(init_a, init_b) if init_a and init_b else 0.0
    resp = 1.0 / (1.0 + reply_median_s / 3600.0) if reply_median_s is not None else 0.0
    return round(100.0 * (0.40 * volume + 0.30 * recip + 0.15 * init + 0.15 * resp), 2)


@dataclass
class _Cell:
    ab: int = 0
    ba: int = 0
    ia: int = 0
    ib: int = 0
    replies: list[int] = field(default_factory=list)


@dataclass
class _Last:
    at: datetime
    side: str
    replied: bool = False


def _bare(v: Any) -> str | None:
    s = str(v).split("@", 1)[0] if v else ""
    return s or None


async def _messages(db: AsyncSession, org_id: uuid.UUID, since: datetime, until: datetime) -> AsyncIterator[Any]:
    """Tin (không gồm reaction/nhãn dán/hệ thống) theo thứ tự thời gian, đọc từng lô `BATCH` bằng khoá (occurred_at, id) — không nạp cả
    cửa sổ vào bộ nhớ; trạng thái phiên/trả lời do bên gọi giữ xuyên lô."""
    ct: datetime | None = None
    ci: uuid.UUID | None = None
    while True:
        rows = (await db.execute(text("""
            SELECT e.id, e.occurred_at, e.group_id, e.channel_id, e.direction, e.sender_identity_id, pi.person_id AS sender_person,
                   NULLIF(split_part(e.payload->>'threadId', '@', 1), '') AS k1,
                   NULLIF(split_part(e.payload->'key'->>'remoteJid', '@', 1), '') AS k2,
                   NULLIF(split_part(e.payload->'key'->>'remoteJidAlt', '@', 1), '') AS k3,
                   NULLIF(split_part(e.payload->>'recipient', '@', 1), '') AS k4
            FROM raw.events e LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            WHERE e.org_id = :o AND e.occurred_at >= :s AND e.occurred_at < :n AND e.received_at >= :s - interval '3 days'
              AND e.kind <> ALL(:nr) AND (CAST(:ct AS timestamptz) IS NULL OR (e.occurred_at, e.id) > (CAST(:ct AS timestamptz), CAST(:ci AS uuid)))
            ORDER BY e.occurred_at, e.id LIMIT :lim"""),
                                  {"o": org_id, "s": since, "n": until, "nr": list(NO_REPLY_KINDS), "ct": ct, "ci": ci, "lim": BATCH})).all()
        for r in rows:
            yield r
        if len(rows) < BATCH:
            return
        ct, ci = rows[-1].occurred_at, rows[-1].id


async def rebuild(db: AsyncSession, org_id: uuid.UUID, *, weeks: int = DEFAULT_WEEKS, now: datetime | None = None) -> dict[str, int]:
    """Dựng lại độ nóng `weeks` tuần gần nhất (gồm tuần hiện tại). Trả {pairs, rows, deleted}. Bên gọi commit."""
    weeks = max(1, min(MAX_WEEKS, weeks))
    now = now or datetime.now(UTC)
    first_week = week_start(now) - timedelta(weeks=weeks - 1)
    since = datetime.combine(first_week, datetime.min.time(), tzinfo=VN).astimezone(UTC)
    owner = await entities.owner(db, org_id)
    owner_people = set(await profile.owner_person_ids(db, org_id))
    ident = {(r.channel_id, r.external_id): r.person_id for r in (await db.execute(text("""
        SELECT i.channel_id, i.external_id, i.person_id FROM core.person_identities i JOIN core.persons p ON p.id = i.person_id
        WHERE p.org_id = :o"""), {"o": org_id})).all()}
    ent_cache: dict[tuple[str, uuid.UUID], uuid.UUID | None] = {}

    async def ent_person(pid: uuid.UUID) -> uuid.UUID | None:
        k = ("p", pid)
        if k not in ent_cache:
            try:
                e = await entities.ensure_person(db, org_id, pid)
                ent_cache[k] = None if e.forgotten else e.id        # người đã quên: không sinh lại độ nóng
            except entities.EntityError:
                ent_cache[k] = None
        return ent_cache[k]

    async def ent_group(gid: uuid.UUID) -> uuid.UUID | None:
        k = ("g", gid)
        if k not in ent_cache:
            try:
                g = await entities.ensure_group(db, org_id, gid)
                ent_cache[k] = None if g.forgotten else g.id
            except entities.EntityError:
                ent_cache[k] = None
        return ent_cache[k]

    cells: dict[tuple[uuid.UUID, date], _Cell] = {}
    last: dict[uuid.UUID, _Last] = {}
    last_inbound_dm: dict[uuid.UUID, tuple[uuid.UUID, datetime]] = {}      # kênh → (người, lúc) cho tin đi không ghi người nhận
    async for r in _messages(db, org_id, since, now):
        at = r.occurred_at
        owner_side = r.direction == "outbound" or (r.sender_person is not None and r.sender_person in owner_people)
        b: uuid.UUID | None
        if r.group_id is not None:
            b = await ent_group(r.group_id)
        elif r.direction == "outbound":
            person = next((ident[(r.channel_id, k)] for k in (r.k1, r.k2, r.k3, r.k4) if k and (r.channel_id, k) in ident), None)
            if person is None and not any((r.k1, r.k2, r.k3, r.k4)):
                prev = last_inbound_dm.get(r.channel_id)
                if prev is not None and at - prev[1] <= FALLBACK_INBOUND:
                    person = prev[0]
            b = await ent_person(person) if person is not None else None
        else:
            if r.sender_person is None or r.sender_person in owner_people:
                continue
            last_inbound_dm[r.channel_id] = (r.sender_person, at)
            b = await ent_person(r.sender_person)
        if b is None or b == owner.id:
            continue
        side = "a" if owner_side else "b"
        cell = cells.setdefault((b, week_start(at)), _Cell())
        prev_msg = last.get(b)
        if prev_msg is None or at - prev_msg.at >= SESSION_GAP:
            if side == "a":
                cell.ia += 1
            else:
                cell.ib += 1
        elif prev_msg.side != side and not prev_msg.replied and at - prev_msg.at < REPLY_MAX and side == "b":
            cell.replies.append(int((at - prev_msg.at).total_seconds()))
            prev_msg.replied = True
        if side == "a":
            cell.ab += 1
        else:
            cell.ba += 1
        if prev_msg is None or prev_msg.side != side or at - prev_msg.at >= SESSION_GAP:
            last[b] = _Last(at, side)
        else:
            last[b].at = at
    keep: set[tuple[uuid.UUID, date]] = set()
    pairs: set[uuid.UUID] = set()
    for (b, wk), c in sorted(cells.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        med = int(statistics.median(c.replies)) if c.replies else None
        h = score(c.ab, c.ba, c.ia, c.ib, med)
        await db.execute(text("""
            INSERT INTO mem.relation_heat_weekly (org_id, entity_a, entity_b, week, msgs_ab, msgs_ba, initiated_a, initiated_b,
                                                  reply_median_s, heat, state, projection_version)
            VALUES (:o, :a, :b, :w, :ab, :ba, :ia, :ib, :m, :h, :st, :v)
            ON CONFLICT (org_id, entity_a, entity_b, week) DO UPDATE SET msgs_ab = EXCLUDED.msgs_ab, msgs_ba = EXCLUDED.msgs_ba,
              initiated_a = EXCLUDED.initiated_a, initiated_b = EXCLUDED.initiated_b, reply_median_s = EXCLUDED.reply_median_s,
              heat = EXCLUDED.heat, state = EXCLUDED.state, projection_version = EXCLUDED.projection_version"""),
                         {"o": org_id, "a": owner.id, "b": b, "w": wk, "ab": c.ab, "ba": c.ba, "ia": c.ia, "ib": c.ib, "m": med,
                          "h": h, "st": state_of(h), "v": PROJECTION_VERSION})
        keep.add((b, wk))
        pairs.add(b)
    old = (await db.execute(text("SELECT entity_b, week FROM mem.relation_heat_weekly WHERE org_id = :o AND entity_a = :a AND week >= :w"),
                            {"o": org_id, "a": owner.id, "w": first_week})).all()
    stale = [(r.entity_b, r.week) for r in old if (r.entity_b, r.week) not in keep]
    for b, wk in stale:
        await db.execute(text("DELETE FROM mem.relation_heat_weekly WHERE org_id = :o AND entity_a = :a AND entity_b = :b AND week = :w"),
                         {"o": org_id, "a": owner.id, "b": b, "w": wk})
    return {"pairs": len(pairs), "rows": len(keep), "deleted": len(stale)}


# ─── đọc ────────────────────────────────────────────────────────────────────

def _zero(w: date) -> dict[str, Any]:
    return {"week": w, "msgs_ab": 0, "msgs_ba": 0, "initiated_a": 0, "initiated_b": 0, "reply_median_s": None, "heat": 0.0, "state": "cold"}


async def series_many(db: AsyncSession, org_id: uuid.UUID, a: uuid.UUID, bs: list[uuid.UUID], weeks: int = DEFAULT_WEEKS,
                      *, now: datetime | None = None) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Chuỗi tuần của (a, mỗi b), cũ → mới, đúng `weeks` phần tử (tuần thiếu = 0, `cold`). `a`/`b` được chuẩn hoá về thực thể đích;
    khoá kết quả là id đã chuẩn hoá. Chỉ đọc hàng của `org_id` (không lộ chéo tổ chức)."""
    weeks = max(1, min(MAX_WEEKS, weeks))
    now = now or datetime.now(UTC)
    cur = week_start(now)
    wks = [cur - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    ca = await entities.canonical(db, a)
    cbs = [await entities.canonical(db, b) for b in bs]
    rows = (await db.execute(text("""
        SELECT entity_b, week, msgs_ab, msgs_ba, initiated_a, initiated_b, reply_median_s, heat, state
        FROM mem.relation_heat_weekly WHERE org_id = :o AND entity_a = :a AND entity_b = ANY(:b) AND week >= :w"""),
                          {"o": org_id, "a": ca, "b": cbs, "w": wks[0]})).all()
    got: dict[tuple[uuid.UUID, date], Any] = {(r.entity_b, r.week): r for r in rows}
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for cb in cbs:
        line = []
        for w in wks:
            r = got.get((cb, w))
            line.append(_zero(w) if r is None else {"week": w, "msgs_ab": r.msgs_ab, "msgs_ba": r.msgs_ba, "initiated_a": r.initiated_a,
                                                     "initiated_b": r.initiated_b, "reply_median_s": r.reply_median_s,
                                                     "heat": float(r.heat), "state": r.state})
        out[cb] = line
    return out


async def series(db: AsyncSession, org_id: uuid.UUID, a: uuid.UUID, b: uuid.UUID, weeks: int = DEFAULT_WEEKS,
                 *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Hợp đồng cho T5/Bước 2: `heat.series(a, b, weeks)` (kèm `db`, `org`)."""
    res = await series_many(db, org_id, a, [b], weeks, now=now)
    return next(iter(res.values()))


def cooling(line: list[dict[str, Any]]) -> bool:
    """Nguội theo nhịp RIÊNG của quan hệ: 2 tuần gần nhất < 50% trung bình các tuần trước đó (vốn ≥ 15)."""
    if len(line) < 4:
        return False
    recent, before = line[-2:], line[:-2]
    base = sum(x["heat"] for x in before) / len(before)
    return base >= 15 and sum(x["heat"] for x in recent) / 2 < 0.5 * base
