"""Job `gen_coach` — chuông "Hôm nay Sếp còn n việc cần làm" (v0.1.54, g1-api).

Chạy 09:05 / 11:05 / 14:05 giờ worker (Asia/Ho_Chi_Minh). Với mỗi tổ chức, mỗi Owner:
1. cập nhật mốc ổn định `stable_since` (mỗi mốc, kể cả khi không chuông);
2. nếu `engine.bell_due` (bật + chuông bật + không hoãn tất cả + không ổn định + ngoài giờ yên lặng theo múi giờ tổ
   chức + có việc P0/P1 mới, hoặc cùng tập mà lần chuông trước ≥ 3 ngày) ⇒ `store.claim_bell` — MỘT câu UPDATE
   nguyên tử: tối đa một chuông mỗi Owner mỗi ngày, và không chuông khi Sếp đã xem thẻ hôm nay — rồi
   `notifications.notify` (kind 'gen.coach'). Chạy lại cùng ngày không sinh chuông thứ hai.

Không gọi model, không đẩy Telegram, không ghi agent.gen_messages. Liên kết chuông: `/overview?gen=coach` khi Gen đang
bật cho Owner, không thì `/guide/viec-sep` (trang Việc Sếp cần làm).
"""

import asyncio
import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh import notifications
from gh.gen import store as gen_store
from gh.gen.coach import engine, store
from gh.gen.coach import signals as sg

log = logging.getLogger("gh.gen.coach")

KIND = "gen.coach"
LINK_GEN = "/overview?gen=coach"
LINK_NO_GEN = "/guide/viec-sep"
BELL_BODY = "Em đã xếp sẵn việc ưu tiên trên thẻ Hôm nay của Sếp — mở ra xem khi Sếp tiện."


def bell_title(n: int) -> str:
    return f"Hôm nay Sếp còn {n} việc cần làm"


async def _one_org(sm: async_sessionmaker[AsyncSession], redis: Any, org: uuid.UUID, now: datetime) -> dict[str, int]:
    out = {"owners": 0, "bells": 0}
    async with sm() as db:
        await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org)})
        owners = await notifications.owner_ids(db, org)
        if not owners:
            return out
        tz = await store.org_timezone(db, org)
        cfg = await gen_store.get_settings(db, org)
        gen_on = bool(cfg.get("enabled")) and "owner" in (cfg.get("roles") or [])
        sig = await sg.collect(db, redis, org)
        today_start = engine.day_start_utc(now, tz)
        for uid in owners:
            out["owners"] += 1
            prefs = await store.ensure_prefs(db, org, uid)
            items = await store.list_items(db, uid)
            tp = engine.plan_todos(sig, items, now)
            stable_since, stable = engine.settle(sig, prefs, tp, now)
            if stable_since != prefs.stable_since:
                await store.set_stable(db, org, uid, stable_since)
                prefs = replace(prefs, stable_since=stable_since)
            shown = tp.visible[:engine.MAX_TODOS]
            keys = [t.key for t in shown if t.level in ("P0", "P1")]
            if not engine.bell_due(prefs, keys, stable, now, tz):
                continue
            if not await store.claim_bell(db, uid, today_start, keys, now):
                continue
            await notifications.notify(db, org, [uid], kind=KIND, title=bell_title(len(shown)), body=BELL_BODY,
                                       link=LINK_GEN if gen_on else LINK_NO_GEN, redis=redis)
            out["bells"] += 1
        await db.commit()
    return out


async def run_coach(sm: async_sessionmaker[AsyncSession], redis: Any, now: datetime | None = None) -> dict[str, Any]:
    """Một lượt cron cho MỌI tổ chức → mọi Owner. Trả số liệu để ghi dấu cron (không chứa dữ liệu của Sếp)."""
    now = now or datetime.now(UTC)
    out: dict[str, Any] = {"orgs": 0, "owners": 0, "bells": 0, "errors": 0}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at"))).scalars().all()
    for org in orgs:
        try:
            res = await _one_org(sm, redis, org, now)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — một tổ chức lỗi không chặn tổ chức khác
            log.exception("Gen hướng dẫn: lượt chuông lỗi (%s)", org)
            out["errors"] += 1
            continue
        out["orgs"] += 1
        out["owners"] += res["owners"]
        out["bells"] += res["bells"]
    return out
