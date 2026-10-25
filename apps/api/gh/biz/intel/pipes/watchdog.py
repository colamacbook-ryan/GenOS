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
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import health, notifications
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
SILENCE_FACTOR, SILENCE_MIN_GAPS, SILENCE_FLOOR, SILENCE_HISTORY = 3, 20, timedelta(minutes=30), timedelta(days=28)
GROUP_FACTOR, GROUP_MIN_GAPS, GROUP_FLOOR, GROUP_HISTORY, GROUP_MAX = 5, 15, timedelta(hours=6), timedelta(days=30), 20
GROUP_HOURS = range(7, 23)
GROUP_LISTEN_MODES = ("tagged_only", "silent", "proactive")
KICK_LOOKBACK = timedelta(days=7)
DEADMAN_GRACE, DEADMAN_WINDOW = timedelta(minutes=10), timedelta(hours=3)
LINK = "/connections"
_STRIP_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})      # điều khiển, định dạng (bidi, zero-width), dấu xuống dòng/đoạn Unicode
ENDED_STATES = ("expired", "error", "logged_out")


@dataclass(frozen=True)
class Incident:
    key: str
    kind: str
    severity: str
    title: str
    body: str
    link: str = LINK
    fingerprint: str = ""                 # `last:<giờ tin cuối>` cho im lặng: bằng chứng dương để biết khi nào thật sự thông
    covered_by: tuple[str, ...] = ()      # sự cố cùng gốc đã có chuông ở nơi khác (channel.down / bridge.silent): không mở chuông thứ hai


def _clean(name: str | None, limit: int = 60) -> str:
    cleaned = "".join(" " if unicodedata.category(ch) in _STRIP_CATEGORIES else ch for ch in (name or ""))
    return " ".join(cleaned.split())[:limit] or "(không tên)"


def _human(d: timedelta) -> str:
    m = int(d.total_seconds() // 60)
    return f"{m // 60} giờ" if m >= 120 else f"{m} phút"


# ─── (1) phiên bridge ────────────────────────────────────────────────────────

async def _sessions(db: AsyncSession, org: Any, now: datetime) -> tuple[list[Incident], dict[str, str]]:
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
    states = {r.type: r.state for r in rows}
    for r in rows:
        name, key = CHANNEL_NAME[r.type], f"{PREFIX}session:{r.type}"
        down = (f"channel.down:{r.type}",)
        if r.state in ENDED_STATES:
            if r.ended_at is None or now - r.ended_at > ENDED_LOOKBACK or r.by_owner:
                continue
            why = {"expired": "đã hết hạn phiên", "error": "gặp lỗi", "logged_out": "đã bị đăng xuất"}[r.state]
            out.append(Incident(key, "agent.session", "bad", f"Tài khoản {name} của Gen {why}",
                                f"Gen không nghe và không gửi được tin trên {name}. Mở Kết nối để nối lại tài khoản.", covered_by=down))
        elif r.state == "pending_qr" and r.had_run:
            out.append(Incident(key, "agent.session", "bad", f"Tài khoản {name} của Gen cần quét lại mã QR",
                                f"Gen đang không nghe được tin trên {name}. Mở Kết nối và quét lại mã QR.", covered_by=down))
        elif r.state == "active" and r.ended_at is None:
            seen = r.last_heartbeat_at or r.started_at or r.qr_issued_at
            if seen is not None and now - seen > HEARTBEAT_MAX:
                out.append(Incident(key, "agent.session", "bad", f"Tài khoản {name} của Gen đã im {_human(now - seen)}",
                                    f"Cầu nối {name} không báo nhịp tim — tin khách có thể đang không vào. Mở Sức khoẻ hệ thống để xem cách khởi động lại.",
                                    health.HEALTH_LINK, covered_by=(*down, "bridge.silent")))
    return out, states


# ─── (2) im lặng toàn cục ────────────────────────────────────────────────────

async def _silence(db: AsyncSession, org: Any, now: datetime) -> list[Incident]:
    r = (await db.execute(text("""
        WITH ev AS (SELECT occurred_at, lag(occurred_at) OVER (ORDER BY occurred_at) AS prev FROM raw.events
                    WHERE org_id = :o AND direction = 'inbound' AND received_at > :since AND occurred_at > :since AND occurred_at <= :now)
        SELECT (SELECT max(occurred_at) FROM ev) AS last,
               count(*) FILTER (WHERE prev IS NOT NULL) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM occurred_at - prev)) AS med
        FROM ev WHERE prev IS NOT NULL AND occurred_at - prev < interval '24 hours'
          AND extract(hour FROM occurred_at AT TIME ZONE 'Asia/Ho_Chi_Minh') = :h
          AND (extract(isodow FROM occurred_at AT TIME ZONE 'Asia/Ho_Chi_Minh') >= 6) = :weekend"""),   # cùng loại ngày: Chủ nhật so với cuối tuần
        {"o": org, "since": now - SILENCE_HISTORY, "now": now, "h": now.astimezone(VN).hour,
         "weekend": now.astimezone(VN).isoweekday() >= 6})).one()
    if r.last is None or r.n < SILENCE_MIN_GAPS or r.med is None:
        return []
    limit = max(timedelta(seconds=SILENCE_FACTOR * float(r.med)), SILENCE_FLOOR)
    gap = now - r.last
    if gap <= limit:
        return []
    return [Incident(f"{PREFIX}silent", "agent.silent", "warn", f"Không có tin nào vào suốt {_human(gap)} — bất thường so với giờ này mọi ngày",
                     "Có thể tài khoản của Gen bị khoá hoặc mất kết nối dù cầu nối vẫn báo bình thường. Mở Kết nối để kiểm tra.",
                     fingerprint=f"last:{r.last.isoformat()}")]


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
                        WHERE org_id = :o AND direction = 'inbound' AND group_id IS NOT NULL AND received_at > :since AND occurred_at > :since AND occurred_at <= :now),
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
                                "Nhóm này im lâu hơn nhiều so với nhịp thường ngày — có thể Gen đã bị loại khỏi nhóm mà không có thông báo. Mở nhóm trên điện thoại để kiểm tra.",
                                fingerprint=f"last:{g.last.isoformat()}"))
    return out, evaluate_quiet


# ─── (4) dead-man bản tin ────────────────────────────────────────────────────

async def _briefing_runs(db: AsyncSession, org: Any) -> bool:
    """Bản tin có chạy cho tổ chức này không — đúng điều kiện `briefing._one_org` bỏ qua (Gen tắt, không có vai Owner được dùng, không có Owner)."""
    from gh.gen import store

    cfg = await store.get_settings(db, org)
    if not cfg.get("enabled") or "owner" not in (cfg.get("roles") or []):
        return False
    return bool(await notifications.owner_ids(db, org))


async def _deadman(db: AsyncSession, org: Any, now: datetime) -> list[Incident]:
    from gh.gen import briefing

    slot = briefing.slot_for(now)
    if not (slot.at + DEADMAN_GRACE <= now < slot.at + DEADMAN_WINDOW):
        return []
    if not await _briefing_runs(db, org):
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

@dataclass(frozen=True)
class Evaluation:
    desired: dict[str, Incident]
    keep: tuple[str, ...]            # tiền tố key KHÔNG đóng lượt này (không đánh giá được, vd. nhóm im ban đêm)
    session_states: dict[str, str]


async def evaluate(db: AsyncSession, org: Any, now: datetime) -> Evaluation:
    sessions, states = await _sessions(db, org, now)
    found: list[Incident] = [*sessions, *await _silence(db, org, now)]
    groups, quiet_evaluated = await _groups(db, org, now)
    found += groups
    found += await _deadman(db, org, now)
    return Evaluation({i.key: i for i in found}, () if quiet_evaluated else (f"{PREFIX}group_quiet:",), states)


async def _newer_than(db: AsyncSession, org: Any, fingerprint: str, now: datetime, group: str | None = None) -> bool:
    """Bằng chứng dương: có tin vào MỚI HƠN tin cuối lúc mở sự cố (`fingerprint = last:<giờ>`)."""
    if not fingerprint.startswith("last:"):
        return False
    try:
        since = datetime.fromisoformat(fingerprint[5:])
    except ValueError:
        return False
    return bool((await db.execute(text("""SELECT EXISTS (SELECT 1 FROM raw.events WHERE org_id = :o AND direction = 'inbound'
                                            AND occurred_at > :s AND occurred_at <= :n AND (CAST(:g AS uuid) IS NULL OR group_id = CAST(:g AS uuid)))"""),
                                  {"o": org, "s": since, "n": now, "g": group})).scalar_one())


async def _resolution(db: AsyncSession, org: Any, key: str, fingerprint: str, now: datetime, ev: Evaluation) -> str:
    """Sự cố đang mở mà lượt này không còn thấy: `recovered` (có BẰNG CHỨNG DƯƠNG là đã khỏi ⇒ báo hồi phục), `silent` (hết cửa sổ/hết điều kiện đánh giá,
    KHÔNG phải đã khỏi ⇒ đóng im lặng) hoặc `hold` (mặc định — chưa chứng minh được ⇒ giữ mở)."""
    kind, _, ref = key[len(PREFIX):].partition(":")
    if kind == "session":
        state = ev.session_states.get(ref, "gone")
        return "recovered" if state == "active" else ("silent" if state in (*ENDED_STATES, "gone") else "hold")
    if kind in ("group_removed", "group_quiet"):
        g = (await db.execute(text("SELECT left_at, listen_mode FROM core.groups WHERE org_id = :o AND id = CAST(:g AS uuid)"), {"o": org, "g": ref})).one_or_none()
        if g is None:
            return "silent"
        if kind == "group_removed":
            return "recovered" if g.left_at is None else "silent"
        if g.left_at is not None or g.listen_mode not in GROUP_LISTEN_MODES:
            return "silent"
        return "recovered" if await _newer_than(db, org, fingerprint, now, ref) else "hold"
    if kind == "silent":
        return "recovered" if await _newer_than(db, org, fingerprint, now) else "hold"
    if kind == "digest_late":
        from gh.gen import briefing

        since = briefing.slot_for(now).at          # có bản tin nào tới tay Sếp từ mốc gần nhất ⇒ đã thông; chưa ⇒ giữ mở (không tự hết theo cửa sổ)
        sent = (await db.execute(text("""
            SELECT EXISTS (SELECT 1 FROM ops.telegram_outbox WHERE org_id = :o AND kind = 'briefing' AND sent_at >= :t)
                OR EXISTS (SELECT 1 FROM ops.owner_outbox WHERE org_id = :o AND kind = 'digest' AND sent_at >= :t)"""), {"o": org, "t": since})).scalar_one()
        if sent:
            return "recovered"
        return "hold" if await _briefing_runs(db, org) else "silent"
    return "hold"


async def reconcile(db: AsyncSession, redis: Any, org: Any, now: datetime) -> dict[str, int]:
    """Mở/đóng sự cố của một tổ chức theo `evaluate`. Bên gọi commit."""
    ev = await evaluate(db, org, now)
    opened = (await db.execute(text("SELECT key, title, fingerprint FROM ops.health_alerts WHERE org_id = :o AND key LIKE 'agent.%' AND cleared_at IS NULL"),
                               {"o": org})).all()
    own_open = {r.key for r in opened}
    external = {r.key: r.raised_at for r in (await db.execute(text("""SELECT key, raised_at FROM ops.health_alerts
        WHERE org_id = :o AND cleared_at IS NULL AND (key = 'bridge.silent' OR key LIKE 'channel.down:%')"""), {"o": org})).all()}
    stamp = now.strftime("%Y%m%d%H%M")
    raised = cleared = 0
    for inc in ev.desired.values():
        cover = next((k for k in inc.covered_by if k in external), None)
        if cover is not None and inc.key not in own_open:
            # Đã có chuông của sự cố này (ingest `channel.down:*` / `bridge.silent`): không mở chuông thứ hai, chỉ báo kênh phụ MỘT lần mỗi đợt.
            if await backup.notify(db, org, f"{inc.title}. {inc.body}", kind="incident", dedupe_key=f"{inc.key}:cover:{cover}:{external[cover].strftime('%Y%m%d%H%M%S')}"):
                raised += 1
            continue
        fresh = await health.raise_once(db, org, key=inc.key, kind=inc.kind, severity=inc.severity, title=inc.title, body=inc.body, link=inc.link, fingerprint=inc.fingerprint, redis=redis)
        if fresh:
            raised += 1
            await backup.notify(db, org, f"{inc.title}. {inc.body}", kind="incident", dedupe_key=f"{inc.key}:{stamp}")
    for row in opened:
        if row.key in ev.desired or any(row.key.startswith(p) for p in ev.keep):
            continue
        outcome = await _resolution(db, org, row.key, row.fingerprint, now, ev)
        if outcome == "hold":
            continue
        if await health.clear(db, org, row.key, redis=redis):
            cleared += 1
            # Lý do đóng ghi vào `fingerprint` của dòng đã đóng (bảng không có cột riêng; mở lại thì raise_once ghi đè): `recovered_lines` chỉ lấy `closed:recovered`.
            await db.execute(text("UPDATE ops.health_alerts SET fingerprint = :f WHERE org_id = :o AND key = :k AND cleared_at IS NOT NULL"),
                             {"f": f"closed:{outcome}", "o": org, "k": row.key})
            if outcome == "recovered":
                await backup.notify(db, org, f"Đã hồi phục: {row.title}", kind="incident", dedupe_key=f"ok:{row.key}:{stamp}")
    return {"raised": raised, "cleared": cleared}


async def recovered_lines(db: AsyncSession, org: Any, *, since: datetime) -> list[str]:
    """Dòng "đã hồi phục" cho bản tin kế (T3 chèn): các sự cố của watchdog đóng từ `since`."""
    rows = (await db.execute(text("""SELECT title FROM ops.health_alerts WHERE org_id = :o AND cleared_at >= :s
                                       AND ((key LIKE 'agent.%' AND fingerprint = 'closed:recovered') OR key LIKE 'channel.down:%' OR key = 'bridge.silent')
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


async def health_block(db: AsyncSession, org: Any, now: datetime) -> dict[str, Any] | None:
    """Khối `intel` của `health.collect`: kênh phụ, khoá trợ lý, phiên kênh, số sự cố watchdog đang mở. None khi chưa có gì để hiện
    (giữ nguyên khuôn cũ cho bản cài chưa dùng Tình báo). Không chứa token/chat_id."""
    tg = bool((await db.execute(text("SELECT enabled FROM ops.notify_channels WHERE org_id = :o"), {"o": org})).scalar_one_or_none())
    sessions = (await db.execute(text("""
        SELECT DISTINCT ON (c.id) c.type, s.state, COALESCE(s.last_heartbeat_at, s.started_at, s.qr_issued_at) AS seen
        FROM core.channels c JOIN core.channel_sessions s ON s.channel_id = c.id
        WHERE c.org_id = :o AND c.type = ANY(:t)
        ORDER BY c.id, GREATEST(s.started_at, s.qr_issued_at, s.ended_at) DESC NULLS LAST, s.id DESC"""), {"o": org, "t": list(CHANNELS)})).all()
    incidents = int((await db.execute(text("SELECT count(*) FROM ops.health_alerts WHERE org_id = :o AND key LIKE 'agent.%' AND cleared_at IS NULL"),
                                      {"o": org})).scalar_one())
    locked = await backup.is_locked(db, org)
    if not (tg or locked or incidents or sessions):
        return None
    return {"backup_channel": "telegram" if tg else "none", "assistant_locked": locked, "incidents": incidents,
            "sessions": [{"channel": s.type, "state": s.state, "heartbeat_age_s": int((now - s.seen).total_seconds()) if s.seen else None} for s in sessions]}
