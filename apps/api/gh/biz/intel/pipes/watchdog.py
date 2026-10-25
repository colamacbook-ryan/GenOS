# ruff: noqa: E501
"""Canh sức khoẻ tài khoản agent (v0.1.76, gói T10 — spec §8.1 "Canh sức khoẻ tài khoản agent", R3). Chạy mỗi phút, bốn loại sự cố:

1. **Phiên bridge** (`agent.session:<kênh>`): phiên mới nhất của kênh đã kết thúc (hết hạn/lỗi/bị đăng xuất — trừ khi chính Sếp đăng xuất trên Console),
   đòi quét lại QR sau khi từng chạy, hoặc nhịp `last_heartbeat_at` quá 2 phút.
2. **Im lặng toàn cục** (`agent.silent`): không có tin vào lâu hơn 3 lần khoảng lặng TRUNG VỊ của cùng khung giờ (14 ngày gần nhất), sàn 30 phút,
   cần ≥ 20 mẫu (ít hơn thì không đoán).
3. **Một nhóm**: (a) `agent.group_removed:<nhóm>` — bot bị mời ra / nhóm giải tán (sự kiện `bot_removed` do bridge phát, ingest ghi `ops.listen_events`;
   nhóm đã rời mà không có sự kiện đó là Sếp tự rời, KHÔNG phải sự cố); (b) `agent.group_quiet:<nhóm>` — nhóm im quá 5 lần nhịp riêng (sàn 6 giờ,
   ≥ 15 mẫu, chỉ xét 07:00–22:59 giờ VN và ban đêm giữ nguyên sự cố đang mở, không đóng oan). Tối đa 20 nhóm mỗi lượt.
4. **Dead-man bản tin** (`agent.digest_late`): bản tin 07:30/17:30 chưa tới Sếp (không có `sent_at` ở Telegram/kênh quản trị) sau mốc + 10 phút, trong 3 giờ.
   Chỉ khi có đường nhận bản tin (Telegram bật cờ `briefing`, hoặc có báo cáo digest chờ) — không có đường nào thì không có gì để "trễ".

Mỗi sự cố: MỘT dòng `ops.health_alerts` theo key (một chuông, `health.raise_once`), báo qua KÊNH PHỤ đúng một lần lúc mở (`backup.notify`, không qua chính tài
khoản đang hỏng) và một dòng "đã hồi phục" khi đóng. `recovered_lines` cho bản tin kế (T3 chèn). Tên nhóm là chữ của người lạ: bỏ xuống dòng/điều khiển, cắt ngắn
trước khi vào tin báo."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import health
from gh.biz.hooks import CronJob
from gh.telegram import backup

log = logging.getLogger("gh.intel.watchdog")

VN = ZoneInfo("Asia/Ho_Chi_Minh")
PREFIX = "agent."
CHANNELS = ("zalo", "whatsapp")
CHANNEL_NAME = {"zalo": "Zalo", "whatsapp": "WhatsApp"}
HEARTBEAT_MAX = timedelta(minutes=2)
ENDED_LOOKBACK = timedelta(days=7)
LOGOUT_WINDOW = timedelta(minutes=2)
SILENCE_FACTOR, SILENCE_MIN_GAPS, SILENCE_FLOOR, SILENCE_HISTORY = 3, 20, timedelta(minutes=30), timedelta(days=14)
GROUP_FACTOR, GROUP_MIN_GAPS, GROUP_FLOOR, GROUP_HISTORY, GROUP_MAX = 5, 15, timedelta(hours=6), timedelta(days=30), 20
GROUP_HOURS = range(7, 23)
GROUP_LISTEN_MODES = ("tagged_only", "silent", "proactive")
KICK_LOOKBACK = timedelta(days=7)
DEADMAN_GRACE, DEADMAN_WINDOW = timedelta(minutes=10), timedelta(hours=3)
LINK = "/connections"
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class Incident:
    key: str
    kind: str
    severity: str
    title: str
    body: str
    link: str = LINK


def _clean(name: str | None, limit: int = 60) -> str:
    return _CTRL.sub(" ", name or "").strip()[:limit] or "(không tên)"


def _human(d: timedelta) -> str:
    m = int(d.total_seconds() // 60)
    return f"{m // 60} giờ" if m >= 120 else f"{m} phút"


# ─── (1) phiên bridge ────────────────────────────────────────────────────────

async def _sessions(db: AsyncSession, org: Any, now: datetime) -> list[Incident]:
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (c.id) c.type, s.state, s.started_at, s.qr_issued_at, s.last_heartbeat_at, s.ended_at,
               EXISTS (SELECT 1 FROM core.channel_sessions p WHERE p.channel_id = c.id AND p.id <> s.id AND p.started_at IS NOT NULL) AS had_run,
               EXISTS (SELECT 1 FROM ops.action_log a WHERE a.org_id = c.org_id AND a.action = 'channel.logout' AND a.target_id = c.type
                         AND s.ended_at IS NOT NULL AND a.at BETWEEN s.ended_at - make_interval(secs => :w) AND s.ended_at + make_interval(secs => :w)) AS by_owner
        FROM core.channels c JOIN core.channel_sessions s ON s.channel_id = c.id
        WHERE c.org_id = :o AND c.type = ANY(:t)
        ORDER BY c.id, GREATEST(s.started_at, s.qr_issued_at, s.ended_at) DESC NULLS LAST, s.id DESC"""),
        {"o": org, "t": list(CHANNELS), "w": LOGOUT_WINDOW.total_seconds()})).all()
    out: list[Incident] = []
    for r in rows:
        name, key = CHANNEL_NAME[r.type], f"{PREFIX}session:{r.type}"
        if r.state in ("expired", "error", "logged_out"):
            if r.ended_at is None or now - r.ended_at > ENDED_LOOKBACK or r.by_owner:
                continue
            why = {"expired": "đã hết hạn phiên", "error": "gặp lỗi", "logged_out": "đã bị đăng xuất"}[r.state]
            out.append(Incident(key, "agent.session", "bad", f"Tài khoản {name} của Gen {why}",
                                f"Gen không nghe và không gửi được tin trên {name}. Mở Kết nối để nối lại tài khoản."))
        elif r.state == "pending_qr" and r.had_run:
            out.append(Incident(key, "agent.session", "bad", f"Tài khoản {name} của Gen cần quét lại mã QR",
                                f"Gen đang không nghe được tin trên {name}. Mở Kết nối và quét lại mã QR."))
        elif r.state == "active" and r.ended_at is None:
            seen = r.last_heartbeat_at or r.started_at or r.qr_issued_at
            if seen is not None and now - seen > HEARTBEAT_MAX:
                out.append(Incident(key, "agent.session", "bad", f"Tài khoản {name} của Gen đã im {_human(now - seen)}",
                                    f"Cầu nối {name} không báo nhịp tim — tin khách có thể đang không vào. Mở Sức khoẻ hệ thống để xem cách khởi động lại.",
                                    health.HEALTH_LINK))
    return out


# ─── (2) im lặng toàn cục ────────────────────────────────────────────────────

async def _silence(db: AsyncSession, org: Any, now: datetime) -> list[Incident]:
    r = (await db.execute(text("""
        WITH ev AS (SELECT occurred_at, lag(occurred_at) OVER (ORDER BY occurred_at) AS prev FROM raw.events
                    WHERE org_id = :o AND direction = 'inbound' AND occurred_at > :since AND occurred_at <= :now)
        SELECT (SELECT max(occurred_at) FROM ev) AS last,
               count(*) FILTER (WHERE prev IS NOT NULL) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM occurred_at - prev)) AS med
        FROM ev WHERE prev IS NOT NULL AND occurred_at - prev < interval '24 hours'
          AND extract(hour FROM occurred_at AT TIME ZONE 'Asia/Ho_Chi_Minh') = :h"""),
        {"o": org, "since": now - SILENCE_HISTORY, "now": now, "h": now.astimezone(VN).hour})).one()
    if r.last is None or r.n < SILENCE_MIN_GAPS or r.med is None:
        return []
    limit = max(timedelta(seconds=SILENCE_FACTOR * float(r.med)), SILENCE_FLOOR)
    gap = now - r.last
    if gap <= limit:
        return []
    return [Incident(f"{PREFIX}silent", "agent.silent", "warn", f"Không có tin nào vào suốt {_human(gap)} — bất thường so với giờ này mọi ngày",
                     "Có thể tài khoản của Gen bị khoá hoặc mất kết nối dù cầu nối vẫn báo bình thường. Mở Kết nối để kiểm tra.")]


# ─── (3) một nhóm ────────────────────────────────────────────────────────────

async def _groups(db: AsyncSession, org: Any, now: datetime) -> tuple[list[Incident], bool]:
    out: list[Incident] = []
    kicked = (await db.execute(text("""
        SELECT g.id, g.name FROM core.groups g
        WHERE g.org_id = :o AND g.left_at IS NOT NULL
          AND EXISTS (SELECT 1 FROM ops.listen_events e WHERE e.org_id = g.org_id AND e.group_id = g.id AND e.kind = 'bot_removed'
                        AND e.at > :since AND e.at >= g.left_at - interval '1 hour')"""), {"o": org, "since": now - KICK_LOOKBACK})).all()
    for g in kicked:
        out.append(Incident(f"{PREFIX}group_removed:{g.id}", "agent.group_removed", "warn", f"Gen bị mời ra khỏi nhóm «{_clean(g.name)}»",
                            "Gen không còn nghe nhóm này (hoặc nhóm đã giải tán). Vào lại nhóm nếu Sếp muốn Gen nghe tiếp."))
    evaluate_quiet = now.astimezone(VN).hour in GROUP_HOURS
    if evaluate_quiet:
        rows = (await db.execute(text("""
            WITH ev AS (SELECT group_id, occurred_at, lag(occurred_at) OVER (PARTITION BY group_id ORDER BY occurred_at) AS prev FROM raw.events
                        WHERE org_id = :o AND direction = 'inbound' AND group_id IS NOT NULL AND occurred_at > :since AND occurred_at <= :now),
                 st AS (SELECT group_id, count(*) FILTER (WHERE prev IS NOT NULL) AS n, max(occurred_at) AS last,
                               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM occurred_at - prev)) AS med
                        FROM ev GROUP BY group_id)
            SELECT g.id, g.name, st.last, st.med FROM st JOIN core.groups g ON g.id = st.group_id
            WHERE g.org_id = :o AND g.left_at IS NULL AND g.listen_mode = ANY(:modes) AND st.n >= :n AND st.med IS NOT NULL"""),
            {"o": org, "since": now - GROUP_HISTORY, "now": now, "modes": list(GROUP_LISTEN_MODES), "n": GROUP_MIN_GAPS})).all()
        quiet = []
        for g in rows:
            limit = max(timedelta(seconds=GROUP_FACTOR * float(g.med)), GROUP_FLOOR)
            gap = now - g.last
            if gap > limit:
                quiet.append((gap / limit, g, gap))
        for _, g, gap in sorted(quiet, key=lambda t: -t[0])[:GROUP_MAX]:
            out.append(Incident(f"{PREFIX}group_quiet:{g.id}", "agent.group_quiet", "warn", f"Nhóm «{_clean(g.name)}» im lặng bất thường ({_human(gap)})",
                                "Nhóm này im lâu hơn nhiều so với nhịp thường ngày — có thể Gen đã bị loại khỏi nhóm mà không có thông báo. Mở nhóm trên điện thoại để kiểm tra."))
    return out, evaluate_quiet


# ─── (4) dead-man bản tin ────────────────────────────────────────────────────

async def _deadman(db: AsyncSession, org: Any, now: datetime) -> list[Incident]:
    from gh.gen import briefing, store

    slot = briefing.slot_for(now)
    if not (slot.at + DEADMAN_GRACE <= now < slot.at + DEADMAN_WINDOW):
        return []
    cfg = await store.get_settings(db, org)
    if not cfg.get("enabled"):
        return []
    r = (await db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM ops.notify_channels WHERE org_id = :o AND enabled AND briefing) AS tg_path,
               EXISTS (SELECT 1 FROM ops.owner_outbox WHERE org_id = :o AND kind = 'digest' AND created_at >= :s) AS digest_queued,
               EXISTS (SELECT 1 FROM ops.telegram_outbox WHERE org_id = :o AND kind = 'briefing' AND sent_at >= :s)
                 OR EXISTS (SELECT 1 FROM ops.owner_outbox WHERE org_id = :o AND kind = 'digest' AND sent_at >= :s) AS delivered"""),
        {"o": org, "s": slot.at})).one()
    if r.delivered or not (r.tg_path or r.digest_queued):
        return []
    return [Incident(f"{PREFIX}digest_late", "agent.digest_late", "bad", f"Bản tin {slot.label} chưa tới Sếp",
                     f"Đã quá {_human(now - slot.at)} so với giờ gửi mà chưa có bản tin nào được gửi đi. Bản tin vẫn xem được trên Console (Tổng quan).", "/overview")]


# ─── gom + áp dụng ───────────────────────────────────────────────────────────

async def evaluate(db: AsyncSession, org: Any, now: datetime) -> tuple[dict[str, Incident], tuple[str, ...]]:
    """(sự cố cần mở, tiền tố key KHÔNG được đóng lượt này vì không đánh giá được)."""
    found: list[Incident] = [*await _sessions(db, org, now), *await _silence(db, org, now)]
    groups, quiet_evaluated = await _groups(db, org, now)
    found += groups
    found += await _deadman(db, org, now)
    return {i.key: i for i in found}, () if quiet_evaluated else (f"{PREFIX}group_quiet:",)


async def reconcile(db: AsyncSession, redis: Any, org: Any, now: datetime) -> dict[str, int]:
    """Mở/đóng sự cố của một tổ chức theo `evaluate`. Bên gọi commit."""
    desired, keep = await evaluate(db, org, now)
    opened = (await db.execute(text("SELECT key, title FROM ops.health_alerts WHERE org_id = :o AND key LIKE 'agent.%' AND cleared_at IS NULL"),
                               {"o": org})).all()
    stamp = now.strftime("%Y%m%d%H%M")
    raised = cleared = 0
    for inc in desired.values():
        fresh = await health.raise_once(db, org, key=inc.key, kind=inc.kind, severity=inc.severity, title=inc.title, body=inc.body, link=inc.link, redis=redis)
        if fresh:
            raised += 1
            await backup.notify(db, org, f"{inc.title}. {inc.body}", kind="incident", dedupe_key=f"{inc.key}:{stamp}")
    for row in opened:
        if row.key in desired or any(row.key.startswith(p) for p in keep):
            continue
        if await health.clear(db, org, row.key, redis=redis):
            cleared += 1
            await backup.notify(db, org, f"Đã hồi phục: {row.title}", kind="incident", dedupe_key=f"ok:{row.key}:{stamp}")
    return {"raised": raised, "cleared": cleared}


async def recovered_lines(db: AsyncSession, org: Any, *, since: datetime) -> list[str]:
    """Dòng "đã hồi phục" cho bản tin kế (T3 chèn): các sự cố của watchdog đóng từ `since`."""
    rows = (await db.execute(text("""SELECT title FROM ops.health_alerts WHERE org_id = :o AND key LIKE 'agent.%' AND cleared_at >= :s
                                     ORDER BY cleared_at"""), {"o": org, "s": since})).scalars().all()
    return [f"Đã hồi phục: {t}" for t in rows]


async def run_once(now: datetime | None = None, redis: Any = None) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import clock

    now = now or clock.now()
    sm = sessionmaker()
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    out = {"orgs": len(orgs), "raised": 0, "cleared": 0, "errors": 0}
    for org in orgs:
        try:
            async with sm() as db:
                r = await reconcile(db, redis, org, now)
                await db.commit()
            out["raised"] += r["raised"]
            out["cleared"] += r["cleared"]
        except Exception:                                       # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("watchdog lỗi ở một tổ chức")
            out["errors"] += 1
    return out


async def watchdog_tick(ctx: dict[str, Any]) -> dict[str, int]:
    return await run_once(redis=ctx.get("redis"))


async def backup_flush(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    return await backup.flush(sessionmaker())


JOBS: list[CronJob] = [(watchdog_tick, {"minute": set(range(60))}), (backup_flush, {"minute": set(range(60))})]
