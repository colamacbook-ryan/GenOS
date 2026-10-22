"""Lưu trạng thái Gen hướng dẫn (v0.1.54, g1-api): agent.gen_coach_prefs (tuỳ chọn + mốc theo Owner) và
agent.gen_coach_items (trạng thái từng việc / mẹo / bài học) — migration 0033.

Mọi hàm nhận phiên DB đã đặt `app.org_id` (RLS org_isolation như các route khác; worker không đặt thì policy cho qua và
các câu SQL ở đây vẫn lọc theo user_id / org_id). Không commit — bên gọi commit. Hàm KHÔNG gọi model và KHÔNG đụng
agent.gen_messages.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.gen.coach import engine
from gh.gen.coach.engine import Item, Prefs

DEFAULT_TZ = "Asia/Ho_Chi_Minh"
#: Trường tuỳ chọn Owner được sửa (tên cột) — danh sách trắng cho câu UPDATE động.
PREF_FIELDS = ("enabled", "bell", "lessons_per_day", "quiet_start", "quiet_end", "snooze_until")

_PREFS_COLS = ("enabled, bell, lessons_per_day, quiet_start, quiet_end, snooze_until, stable_since, last_seen_at, "
               "last_bell_at, last_bell_keys")


def _prefs(r: Any) -> Prefs:
    return Prefs(enabled=bool(r.enabled), bell=bool(r.bell), lessons_per_day=int(r.lessons_per_day),
                 quiet_start=int(r.quiet_start), quiet_end=int(r.quiet_end), snooze_until=r.snooze_until,
                 stable_since=r.stable_since, last_seen_at=r.last_seen_at, last_bell_at=r.last_bell_at,
                 last_bell_keys=tuple(r.last_bell_keys or ()))


def _item(r: Any) -> Item:
    return Item(key=r.item_key, status=r.status, snooze_until=r.snooze_until, shown_count=int(r.shown_count),
                first_shown_at=r.first_shown_at, last_shown_at=r.last_shown_at)


async def org_timezone(db: AsyncSession, org_id: uuid.UUID) -> ZoneInfo:
    name = (await db.execute(text("SELECT timezone FROM core.organizations WHERE id = :o"),
                             {"o": org_id})).scalar_one_or_none()
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


async def get_prefs(db: AsyncSession, user_id: uuid.UUID) -> Prefs:
    """Tuỳ chọn của Owner; chưa có hàng ⇒ mặc định (KHÔNG ghi)."""
    r = (await db.execute(text(f"SELECT {_PREFS_COLS} FROM agent.gen_coach_prefs WHERE user_id = :u"),  # noqa: S608
                          {"u": user_id})).one_or_none()
    return _prefs(r) if r is not None else Prefs()


async def ensure_prefs(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Prefs:
    """Như `get_prefs` nhưng tạo hàng mặc định nếu chưa có (upsert) — dùng ở cron và trước `claim_bell`."""
    await db.execute(text("""INSERT INTO agent.gen_coach_prefs (user_id, org_id) VALUES (:u, :o)
                             ON CONFLICT (user_id) DO NOTHING"""), {"u": user_id, "o": org_id})
    return await get_prefs(db, user_id)


async def update_prefs(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, changes: dict[str, Any]) -> Prefs:
    """Ghi các tuỳ chọn trong `PREF_FIELDS` (upsert). `snooze_until=None` là bỏ hoãn."""
    cols = [c for c in PREF_FIELDS if c in changes]
    if cols:
        names = ", ".join(cols)
        marks = ", ".join(f":{c}" for c in cols)
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        await db.execute(text(f"""INSERT INTO agent.gen_coach_prefs (user_id, org_id, {names})
                                  VALUES (:u, :o, {marks})
                                  ON CONFLICT (user_id) DO UPDATE SET {sets}, updated_at = now()"""),  # noqa: S608
                         {"u": user_id, "o": org_id, **{c: changes[c] for c in cols}})
    return await get_prefs(db, user_id)


async def set_stable(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, stable_since: datetime | None) -> None:
    """Ghi mốc ổn định (chỉ khi khác giá trị đang lưu) — ngoại lệ ghi duy nhất của `GET /today` không `mark_shown`."""
    await db.execute(text("""
        INSERT INTO agent.gen_coach_prefs (user_id, org_id, stable_since) VALUES (:u, :o, :s)
        ON CONFLICT (user_id) DO UPDATE SET stable_since = EXCLUDED.stable_since, updated_at = now()
        WHERE agent.gen_coach_prefs.stable_since IS DISTINCT FROM EXCLUDED.stable_since"""),
        {"u": user_id, "o": org_id, "s": stable_since})


async def list_items(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Item]:
    rows = (await db.execute(text("""SELECT item_key, status, snooze_until, shown_count, first_shown_at, last_shown_at
                                     FROM agent.gen_coach_items WHERE user_id = :u"""), {"u": user_id})).all()
    return {r.item_key: _item(r) for r in rows}


async def upsert_items(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, items: Iterable[Item]) -> None:
    params = [{"u": user_id, "o": org_id, "k": it.key, "s": it.status, "su": it.snooze_until, "sc": it.shown_count,
               "f": it.first_shown_at, "l": it.last_shown_at} for it in items]
    if not params:
        return
    await db.execute(text("""
        INSERT INTO agent.gen_coach_items (user_id, item_key, org_id, status, snooze_until, shown_count,
                                           first_shown_at, last_shown_at, updated_at)
        VALUES (:u, :k, :o, :s, :su, :sc, :f, :l, now())
        ON CONFLICT (user_id, item_key) DO UPDATE SET status = EXCLUDED.status, snooze_until = EXCLUDED.snooze_until,
               shown_count = EXCLUDED.shown_count, first_shown_at = EXCLUDED.first_shown_at,
               last_shown_at = EXCLUDED.last_shown_at, updated_at = now()"""), params)


async def mark_shown(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, now: datetime, keys: Iterable[str],
                     items: dict[str, Item]) -> None:
    """Sếp vừa XEM thẻ (`GET /today?mark_shown=1`): ghi bài học đã hiện ≥ 3 ngày không phản hồi thành 'snoozed', ghi các
    mục đang hiện (shown / shown_count / first_shown_at / last_shown_at) và `prefs.last_seen_at`."""
    await upsert_items(db, org_id, user_id, engine.stale_lessons(items, now))
    await upsert_items(db, org_id, user_id, [engine.after_shown(items.get(k), k, now) for k in keys])
    await db.execute(text("""
        INSERT INTO agent.gen_coach_prefs (user_id, org_id, last_seen_at) VALUES (:u, :o, :n)
        ON CONFLICT (user_id) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at, updated_at = now()"""),
        {"u": user_id, "o": org_id, "n": now})


async def apply_action(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, item_key: str, action: str,
                       days: int | None, now: datetime) -> Item | None:
    """understood / snooze / done / dismiss / restore trên một mục. Trả mục sau khi ghi (None: không có gì để ghi)."""
    cur = (await db.execute(text("""SELECT item_key, status, snooze_until, shown_count, first_shown_at, last_shown_at
                                    FROM agent.gen_coach_items WHERE user_id = :u AND item_key = :k"""),
                            {"u": user_id, "k": item_key})).one_or_none()
    new = engine.after_action(_item(cur) if cur is not None else None, item_key, action, days, now)
    if new is not None:
        await upsert_items(db, org_id, user_id, [new])
    return new


async def claim_bell(db: AsyncSession, user_id: uuid.UUID, today_start_utc: datetime, keys: list[str],
                     now: datetime) -> bool:
    """Cổng chuông NGUYÊN TỬ — MỘT câu UPDATE: chỉ thắng khi hôm nay chưa chuông VÀ Sếp chưa xem thẻ hôm nay. Hai tiến
    trình cron chạy cùng lúc cũng chỉ một bên nhận `RETURNING`."""
    row = (await db.execute(text("""
        UPDATE agent.gen_coach_prefs SET last_bell_at = :now, last_bell_keys = :keys, updated_at = now()
        WHERE user_id = :u AND (last_bell_at IS NULL OR last_bell_at < :today_start)
          AND (last_seen_at IS NULL OR last_seen_at < :today_start)
        RETURNING user_id"""), {"now": now, "keys": keys, "u": user_id, "today_start": today_start_utc})).first()
    return row is not None
