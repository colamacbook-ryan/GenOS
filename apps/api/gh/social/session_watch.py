"""Kiểm phiên mạng xã hội hằng ngày + sự cố "phiên đăng nhập đã hết" (F-83).

- `daily_check` (cron 09:10 giờ VN): mỗi tài khoản `active` có phiên đã lưu và chưa được kiểm trong 20 giờ qua → xếp
  một việc `health` (`via=schedule`). Không chạy khi Dừng tất cả hay trong giờ yên lặng của tổ chức.
- `evaluate_alerts` (một PHẦN của vòng `health.evaluate`): tài khoản cần đăng nhập lại / bị Facebook yêu cầu xác minh
  → mở sự cố `social.session:<id>` (chuông Owner một lần — `health.raise_once`); tài khoản ổn lại hoặc bị gỡ → đóng.

Telegram: KHÔNG ghi `ops.telegram_outbox`. Đường báo SỰ CỐ qua Telegram duy nhất là genh watchdog đọc
`run/api-health.json` (gom MỌI sự cố đang mở của api — gh.health.write_host_snapshot); ghi thêm hàng outbox sẽ gửi đôi.
Sự cố nằm trong api-health.json = đã báo Telegram.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import health
from gh.chassis import actionlog
from gh.errors import ApiError
from gh.social import service as social

log = logging.getLogger("gh.social.session_watch")

#: Phiên vừa được kiểm (việc health/read xong) trong khoảng này thì không kiểm lại.
RECENT_CHECK_HOURS = 20
KEY_PREFIX = "social.session:"
KIND = "social.session_expired"
ACTION = "social.session_check"
ACTOR = "system:social-session-check"
_PAUSE_REASONS = ("checkpoint", "captcha")


async def daily_check(db: AsyncSession, redis: Redis, now: datetime | None = None) -> int:
    """Xếp việc `health` cho từng tài khoản `active` có phiên. Trả số việc đã xếp."""
    now = now or social._now()
    if await social.halt_state(redis) is not None:
        return 0
    rows = (await db.execute(text("""
        SELECT a.id, a.org_id, o.timezone FROM core.social_accounts a
        JOIN core.organizations o ON o.id = a.org_id
        WHERE a.status = 'active' AND a.state_enc IS NOT NULL
        ORDER BY a.created_at"""))).all()
    n = 0
    for r in rows:
        try:
            tz = ZoneInfo(r.timezone or "Asia/Ho_Chi_Minh")
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("Asia/Ho_Chi_Minh")
        hour = now.astimezone(tz).hour
        if hour >= social.QUIET_HOURS[0] or hour < social.QUIET_HOURS[1]:
            continue
        try:
            recent = (await db.execute(text(f"""
                SELECT 1 FROM agent.browser_jobs
                WHERE account_id = :a AND kind IN ('health', 'read') AND status = 'done'
                  AND finished_at > now() - interval '{RECENT_CHECK_HOURS} hours' LIMIT 1"""),
                {"a": r.id})).first()
            if recent is not None:
                continue
            acc = await social._account(db, r.org_id, r.id)
            await social._guard(db, redis, acc)
            if await social._count_today(db, acc.id, "health") >= social.HEALTH_PER_DAY_MAX:
                continue
            enc = await social._state_enc(db, acc.id)
            if enc is None:
                continue
            # Phiên không mở được → tài khoản sang "cần đăng nhập" (ApiError SOCIAL_NEEDS_LOGIN) — evaluate_alerts bắt.
            sealed = await social._seal_or_needs_login(db, redis, r.org_id, acc, enc)
            await social._enqueue(db, redis, org_id=r.org_id, account=acc, kind="health", via="schedule",
                                  requested_by=None, payload={"state": sealed})
            await actionlog.record(db, org_id=r.org_id, actor_type="system", actor_id=ACTOR, action=ACTION,
                                   target_type="social_account", target_id=str(acc.id), target_label=acc.label,
                                   result="ok", detail={"platform": acc.platform, "via": "schedule"})
            await db.commit()
            n += 1
        except ApiError as e:
            await db.rollback()
            log.info("kiểm phiên %s bỏ qua: %s", r.id, e.code)
        except Exception as e:  # noqa: BLE001 — một tài khoản lỗi không chặn tài khoản khác
            await db.rollback()
            log.warning("kiểm phiên %s lỗi: %s", r.id, type(e).__name__)  # chỉ tên lớp lỗi
    return n


async def evaluate_alerts(db: AsyncSession, org_id: uuid.UUID, redis: Redis | None) -> None:
    """Mở/đóng sự cố phiên hết hạn theo trạng thái tài khoản hiện tại. Bên gọi commit."""
    rows = (await db.execute(text("""
        SELECT id, label, status, pause_reason FROM core.social_accounts
        WHERE org_id = :o AND status <> 'revoked'"""), {"o": org_id})).all()
    bad: set[str] = set()
    for r in rows:
        key = f"{KEY_PREFIX}{r.id}"
        if r.status == "needs_login" or (r.status == "paused" and r.pause_reason in _PAUSE_REASONS):
            bad.add(key)
            await health.raise_once(
                db, org_id, key=key, kind=KIND, severity="bad",
                title=f"Facebook “{r.label}”: phiên đăng nhập đã hết",
                body="Gen không đọc/gửi được nữa. Mở Tài khoản mạng xã hội và bấm Đăng nhập lại "
                     "(Sếp tự đăng nhập, tự xử lý xác minh nếu có).",
                link="/social", fingerprint=r.pause_reason or r.status, redis=redis)
        else:
            await health.clear(db, org_id, key, redis=redis)
    open_keys = (await db.execute(text("""SELECT key FROM ops.health_alerts
                                          WHERE org_id = :o AND cleared_at IS NULL AND key LIKE 'social.session:%'"""),
                                  {"o": org_id})).scalars().all()
    for key in open_keys:
        if key not in bad:
            await health.clear(db, org_id, key, redis=redis)
