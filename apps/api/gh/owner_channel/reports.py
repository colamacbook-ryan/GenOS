# ruff: noqa: E501
"""Báo chủ động cho Sếp qua kênh quản trị. Sự kiện đặc biệt (Sếp quyết định 18/10/2026) VÀ — từ v0.1.76 (Q6, Sếp chốt 22/10) —
bản tin 07:30/17:30 (`kind` bản tin, do `gh.biz.intel.pipes.delivery` dựng; tắt được bằng «tắt bản tin»): bản tin miễn
`min_severity` và `max_per_hour`; giờ yên lặng tách khỏi `urgent` bằng `bypass_quiet`; ghi `refs` ("1 ok / sai 2" trỏ đúng mục).
ĐANG KHOÁ TRỢ LÝ ⇒ không xếp, không gửi. Mức tối thiểu để đẩy là «quan trọng» (`settings.owner_report.min_severity`);
thấp hơn thì
bỏ (không dồn vào đâu cả). Giờ yên lặng 21:00–07:00 giờ VN: tin chờ tới 07:00 (trừ mức `urgent`). Trần `max_per_hour`.

`enqueue` chỉ xếp hàng `ops.owner_outbox`; gửi đi là `flush`, qua cửa `send_auto(owner_dm)` (giữ mọi kiểm tra ở cửa đó).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.data.common import org_settings
from gh.owner_channel.identity import owner_ids

log = logging.getLogger("gh.owner_channel.reports")

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SEVERITY_RANK = {"info": 0, "important": 1, "urgent": 2}
DEFAULTS: dict[str, Any] = {"quiet_start": 21, "quiet_end": 7, "max_per_hour": 6, "min_severity": "important"}
TEXT_MAX = 1500


def prefs_of(settings: dict[str, Any], channel_prefs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Cài đặt báo cáo: mặc định ← `settings.owner_report` ← `ops.owner_channels.report_prefs`. Khoá lạ/sai
    kiểu bị bỏ."""
    out = dict(DEFAULTS)
    for src in (settings.get("owner_report"), channel_prefs):
        if not isinstance(src, dict):
            continue
        for k in ("quiet_start", "quiet_end", "max_per_hour"):
            v = src.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= (23 if k != "max_per_hour" else 60):
                out[k] = v
        if src.get("min_severity") in SEVERITY_RANK:
            out["min_severity"] = src["min_severity"]
    return out


def _hhmm(v: Any) -> int | None:
    if isinstance(v, str) and len(v) == 5 and v[2] == ":" and v[:2].isdigit() and v[3:].isdigit():
        h, m = int(v[:2]), int(v[3:])
        if h < 24 and m < 60:
            return h * 60 + m
    return None


def intel_quiet(settings: dict[str, Any]) -> tuple[int, int] | None:
    """Giờ yên lặng MỘT nguồn (CNS-117): `settings.intel.quiet_start/quiet_end` "HH:MM" (phút trong ngày) khi có đủ và hợp lệ."""
    intel = settings.get("intel")
    if not isinstance(intel, dict):
        return None
    a, b = _hhmm(intel.get("quiet_start")), _hhmm(intel.get("quiet_end"))
    return (a, b) if a is not None and b is not None else None


def release_time(now: datetime, severity: str, prefs: dict[str, Any], bypass_quiet: bool | None = None) -> datetime:
    """Giờ sớm nhất được gửi. `bypass_quiet` (mặc định = severity là `urgent`, giữ hành vi cũ) ⇒ gửi ngay; còn lại, trong giờ
    yên lặng thì chờ tới `quiet_end` (giờ VN)."""
    if (severity == "urgent") if bypass_quiet is None else bypass_quiet:
        return now
    start = int(prefs.get("quiet_start_min", int(prefs["quiet_start"]) * 60))
    end = int(prefs.get("quiet_end_min", int(prefs["quiet_end"]) * 60))
    if start == end:
        return now
    local = now.astimezone(VN_TZ)
    m = local.hour * 60 + local.minute
    quiet = (m >= start or m < end) if start > end else (start <= m < end)
    if not quiet:
        return now
    wake = local.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    if wake <= local:
        wake += timedelta(days=1)
    return wake.astimezone(UTC)


def _links_text(body: str, links: list[tuple[str, str]]) -> str:
    out = body.strip()
    for label, url in links:
        out += f"\n{label}: {url}"
    return out[:TEXT_MAX]


async def enqueue(db: AsyncSession, org_id: uuid.UUID, channel: str, *, kind: str, severity: str, text_: str,
                  dedupe_key: str, links: list[tuple[str, str]] | None = None, now: datetime | None = None,
                  refs: list[dict[str, Any]] | None = None, bypass_quiet: bool | None = None) -> bool:
    """Xếp một báo cáo vào hàng đợi kênh quản trị (trong transaction người gọi). Trả True nếu đã xếp. Không xếp
    khi: kênh
    quản trị chưa bật (hoặc không phải tài khoản bot), mức dưới ngưỡng, trùng `dedupe_key`, hoặc quá `max_per_hour`."""
    if kind not in ("ask", "alert", "reply", "digest") or severity not in SEVERITY_RANK:
        raise ValueError("loại/mức báo cáo không hợp lệ")
    oi = await owner_ids(db, org_id, channel)
    if not oi.enabled:
        return False
    from gh.telegram import backup  # import muộn: tránh vòng import

    if await backup.is_locked(db, org_id):
        return False                    # khoá trợ lý: không gì ra Zalo
    now = now or datetime.now(UTC)
    chan_prefs = (await db.execute(text("""SELECT report_prefs FROM ops.owner_channels
                                           WHERE org_id = :o AND channel_type = :c"""),
                                   {"o": org_id, "c": channel})).scalar_one_or_none()
    settings = await org_settings(db, org_id)
    prefs = prefs_of(settings, chan_prefs)
    quiet = intel_quiet(settings)
    if quiet is not None:
        prefs["quiet_start_min"], prefs["quiet_end_min"] = quiet
    is_digest = kind == "digest"
    if not is_digest and SEVERITY_RANK[severity] < SEVERITY_RANK[str(prefs["min_severity"])]:
        return False
    recent = (await db.execute(text("""
        SELECT count(*) FROM ops.owner_outbox WHERE org_id = :o AND channel_type = :c
          AND created_at > :since"""), {"o": org_id, "c": channel, "since": now - timedelta(hours=1)})).scalar_one()
    if int(recent) >= int(prefs["max_per_hour"]) and severity != "urgent" and not is_digest:
        log.info("owner_outbox: vượt %s tin/giờ, bỏ báo cáo %s", prefs["max_per_hour"], kind)
        return False
    not_before = release_time(now, severity, prefs, bypass_quiet)
    row = (await db.execute(text("""
        INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text, buttons,
                                      created_at, not_before, next_attempt_at, refs)
        VALUES (:o, :c, :k, :s, :d, :t, CAST(:b AS jsonb), :now, :nb, :nb, CAST(:refs AS jsonb))
        ON CONFLICT (org_id, channel_type, dedupe_key) DO NOTHING RETURNING id"""),
        {"o": org_id, "c": channel, "k": kind, "s": severity, "d": dedupe_key,
         "t": _links_text(text_, links or []),
         "b": orjson.dumps([{"label": a, "url": b} for a, b in links or []]).decode(),
         "now": now, "nb": not_before, "refs": orjson.dumps(refs or []).decode()})).scalar_one_or_none()
    return row is not None


MAX_ATTEMPTS = 5
MAX_AGE = timedelta(hours=24)
BATCH = 20
#: Mã từ chối của cửa `send_auto` mà thử lại cũng vô ích ⇒ bỏ hẳn dòng (kênh tắt / không phải bot / câu bị chặn).
PERMANENT = {"OWNER_CHANNEL_OFF", "NOT_BOT_ACCOUNT", "GUARD_BLOCKED", "TRIGGER_NOT_FOUND", "CHANNEL_NOT_SUPPORTED"}


async def flush(sm: Any, bus: Any, redis: Any, now: datetime | None = None) -> dict[str, int]:
    """Gửi các báo cáo tới hạn trong `ops.owner_outbox` qua cửa `send_auto(owner_dm)`. Chạy mỗi phút (cron
    worker). Không ném.
    Mỗi dòng thử tối đa 5 lần trong 24 giờ (lùi 2^n phút); trùng tin kích hoạt (worker khác đã gửi) tính là đã gửi."""
    from gh.owner_channel.autosend import send_auto
    from gh.owner_channel.basis import Basis, Trigger

    now = now or datetime.now(UTC)
    stats = {"sent": 0, "failed": 0, "retry": 0}
    async with sm() as db:
        rows = (await db.execute(text("""
            SELECT id, org_id, channel_type, text, attempts, created_at FROM ops.owner_outbox
            WHERE sent_at IS NULL AND failed_code IS NULL AND not_before <= :now AND next_attempt_at <= :now
            ORDER BY created_at LIMIT :n"""), {"now": now, "n": BATCH})).all()
        await db.rollback()
    locked: dict[Any, bool] = {}
    for r in rows:
        try:
            if r.org_id not in locked:
                from gh.telegram import backup

                async with sm() as db:
                    locked[r.org_id] = await backup.is_locked(db, r.org_id)
            if locked[r.org_id]:
                continue                # khoá trợ lý: giữ nguyên hàng, không gửi gì
            async with sm() as db:
                res = await send_auto(db, bus, redis, org_id=r.org_id, channel=r.channel_type, basis=Basis.OWNER_DM,
                                      trigger=Trigger("report", str(r.id)), text=r.text, now=now)
            async with sm() as db:
                if res.ok or res.code == "DUPLICATE_TRIGGER":
                    await db.execute(text("UPDATE ops.owner_outbox SET sent_at = :now WHERE id = :i"),
                                     {"now": now, "i": r.id})
                    stats["sent"] += 1
                else:
                    attempts = int(r.attempts) + 1
                    final = res.code in PERMANENT or attempts >= MAX_ATTEMPTS or now - r.created_at > MAX_AGE
                    await db.execute(text("""
                        UPDATE ops.owner_outbox SET attempts = :a, next_attempt_at = :nx,
                               failed_code = CASE WHEN :fin THEN CAST(:code AS text) END WHERE id = :i"""),
                                     {"a": attempts, "nx": now + timedelta(minutes=2 ** attempts), "fin": final,
                                      "code": str(res.code)[:60], "i": r.id})
                    stats["failed" if final else "retry"] += 1
                await db.commit()
        except Exception:  # noqa: BLE001 — một dòng hỏng không chặn các dòng sau
            log.exception("owner_outbox: gửi báo cáo lỗi")
    return stats
