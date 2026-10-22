"""Việc nền của cụm Hàng đợi & Hành động: cảnh báo sớm (spec E9) quét theo ngưỡng thời gian.

`biz.alerts` đã có từ giai đoạn 1 (`db/sql/0002_phase1.sql`) và một phần loại đã được sinh nơi khác: than phiền
lặp lại do `gh.refinery.runner` (luật R-03) sinh ngay khi sàng lọc, hạn mức/chuỗi model do `gh.providers.router`
sinh khi gọi model. Việc quét ở đây (mỗi 15 phút, theo tổ chức) phủ các loại còn lại của spec E9 mà không gắn
liền một sự kiện sàng lọc cụ thể: khách đang lạnh, phản hồi chậm bất thường, cơ hội nóng chưa ai nhận, đối thủ
xuất hiện, deadline (lời hứa) bị bỏ quên.

Chống trùng: loại theo **trạng thái đang kéo dài** (khách lạnh, cơ hội chưa nhận) dedupe theo (tổ chức, loại,
đối tượng) trong `DEDUPE_DAYS` ngày; loại theo **một sự kiện cụ thể** (đối thủ, phản hồi chậm) dedupe bằng
chứa đúng chứng cứ đó trong `evidence` (an toàn dù lượt quét chạy lại hay chồng lấn cửa sổ thời gian).
"""

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import realtime
from gh.biz.hooks import CronJob, Hook
from gh.data.common import NO_REPLY_KINDS, outbound_to_sql
from gh.providers.router import raise_alert
from gh.refinery import triage

log = logging.getLogger("gh.biz.queue.jobs")

COOLING_DAYS = 14
COOLING_MIN_UNITS = 2
RESPONSE_SLA_MIN = 60
#: Kết luận của tin cho thấy khách ĐANG CHỜ trả lời (v0.1.67, F050): hỏi giá, đề nghị hợp tác, phàn nàn, xin mẫu,
#: hỏi tiến độ. Tin chào / cảm ơn / reaction (không có kết luận loại này) không bao giờ sinh cảnh báo "Chưa trả lời".
NEEDS_REPLY_EVENTS = ("AskedPrice", "RequestedPartnership", "Complained", "RequestedSample", "AskedStatus")
RESPONSE_WINDOW_H = 24
UNCLAIMED_AGE_H = 24
COMPETITOR_WINDOW_H = 2
DEDUPE_DAYS = 7
SCAN_LIMIT = 50


async def _already_open(db: AsyncSession, org_id: uuid.UUID, alert_type: str, subject_type: str,
                        subject_id: uuid.UUID) -> bool:
    return bool((await db.execute(text("""
        SELECT 1 FROM biz.alerts WHERE org_id = :o AND alert_type = :t AND subject_type = :st AND subject_id = :si
          AND created_at > now() - make_interval(days => :d)"""),
        {"o": org_id, "t": alert_type, "st": subject_type, "si": subject_id, "d": DEDUPE_DAYS})).first())


async def _already_evidenced(db: AsyncSession, org_id: uuid.UUID, alert_type: str, ref: dict[str, Any]) -> bool:
    import orjson

    return bool((await db.execute(text("""
        SELECT 1 FROM biz.alerts WHERE org_id = :o AND alert_type = :t
          AND evidence @> CAST(:ref AS jsonb)"""),
        {"o": org_id, "t": alert_type, "ref": orjson.dumps([ref]).decode()})).first())


async def _notify(redis: Any, org_id: uuid.UUID) -> None:
    if redis is not None:
        await realtime.publish(redis, "alert.new", {}, org_id=org_id)


# ─── khách đang lạnh / sắp mất ────────────────────────────────────────────────

async def _scan_customer_cooling(db: AsyncSession, org_id: uuid.UUID, redis: Any = None) -> int:
    rows = (await db.execute(text("""
        SELECT p.id, p.display_name, max(mu.observed_at) AS last_at, count(*) AS n,
               (array_agg(mu.id ORDER BY mu.observed_at DESC))[1] AS last_unit
        FROM core.persons p JOIN clean.meaning_units mu ON mu.person_id = p.id AND mu.superseded_by IS NULL
        WHERE p.org_id = :o AND p.deleted_at IS NULL AND p.merged_into_id IS NULL AND p.person_type = 'customer'
        GROUP BY p.id
        HAVING max(mu.observed_at) < now() - make_interval(days => :d) AND count(*) >= :n
        LIMIT :lim"""), {"o": org_id, "d": COOLING_DAYS, "n": COOLING_MIN_UNITS, "lim": SCAN_LIMIT})).all()
    n = 0
    for r in rows:
        if await _already_open(db, org_id, "customer_cooling", "person", r.id):
            continue
        await raise_alert(db, org_id, alert_type="customer_cooling", priority="P2",
                          title=f"{r.display_name} có thể đang lạnh dần", subject_type="person", subject_id=r.id,
                          summary=f"Không có tương tác mới trong {COOLING_DAYS} ngày qua",
                          suggested="Chủ động hỏi thăm hoặc gọi lại",
                          evidence=[{"type": "meaning_unit", "id": str(r.last_unit)}])
        n += 1
    return n


# ─── nhân viên phản hồi chậm bất thường ───────────────────────────────────────

async def _scan_slow_response(db: AsyncSession, org_id: uuid.UUID, redis: Any = None) -> int:
    """Tin ĐẾN cần trả lời mà quá `RESPONSE_SLA_MIN` phút chưa có tin ĐI gửi cho đúng người đó (v0.1.67, F050).

    - "Cần trả lời" = có kết luận trong `NEEDS_REPLY_EVENTS`, loại reaction / nhãn dán / tin hệ thống, bỏ người gửi là
      nhân viên.
    - "Đã trả lời": nhóm ⇒ có tin đi sau đó trong CÙNG nhóm; tin 1-1 ⇒ có tin đi sau đó GỬI CHO ĐÚNG khách này
      (`outbound_to_sql`) — Sếp nhắn khách A không che khách B.
    - Một người (và một nhóm / cuộc 1-1) một cảnh báo: lấy tin chờ lâu nhất làm bằng chứng; tin sau của cùng người
      không sinh thêm dòng cùng tiêu đề. Tin 1-1 chưa gắn được người gửi (person_id rỗng) thì KHÔNG gom: `DISTINCT ON`
      coi mọi NULL là một ⇒ mỗi tin một khoá riêng (review v0.1.67), không để một cuộc bị bỏ quên che cuộc khác."""
    rows = (await db.execute(text(f"""
        SELECT DISTINCT ON (c.person_id, (CASE WHEN c.person_id IS NULL AND c.group_id IS NULL THEN c.raw_id END),
                            c.group_id, c.channel_id)
               c.raw_id, c.person_id, c.person_name, c.owner_user_id, u.display_name AS owner_name
        FROM (
          SELECT e.id AS raw_id, e.received_at, e.channel_id, e.group_id, e.occurred_at, p.id AS person_id,
                 p.display_name AS person_name, p.owner_user_id,
                 ARRAY(SELECT x.id FROM core.person_identities x
                       WHERE x.person_id = pi.person_id AND x.channel_id = e.channel_id) AS ids,
                 ARRAY(SELECT NULLIF(split_part(x.external_id, '@', 1), '') FROM core.person_identities x
                       WHERE x.person_id = pi.person_id AND x.channel_id = e.channel_id) AS ext
          FROM raw.events e
          JOIN core.channels ch ON ch.id = e.channel_id
          LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
          LEFT JOIN core.persons p ON p.id = pi.person_id
          WHERE ch.org_id = :o AND e.direction = 'inbound' AND e.kind <> ALL(CAST(:nokind AS text[]))
            AND COALESCE(p.person_type, '') <> 'staff'
            AND e.occurred_at < now() - make_interval(mins => :sla)
            AND e.occurred_at > now() - make_interval(hours => :win)
            AND EXISTS (SELECT 1 FROM clean.evidence ev
                        JOIN clean.meaning_units mu ON mu.id = ev.meaning_unit_id
                         AND mu.observed_at = ev.meaning_observed_at AND mu.superseded_by IS NULL
                        WHERE ev.raw_event_id = e.id AND ev.raw_received_at = e.received_at
                          AND mu.event_type = ANY(CAST(:need AS text[])))
        ) c
        LEFT JOIN core.users u ON u.id = c.owner_user_id
        WHERE NOT EXISTS (
          SELECT 1 FROM raw.events o2
          WHERE o2.channel_id = c.channel_id AND o2.direction = 'outbound'
            AND o2.received_at >= c.received_at AND o2.occurred_at > c.occurred_at
            AND CASE WHEN c.group_id IS NOT NULL THEN o2.group_id = c.group_id
                     ELSE o2.group_id IS NULL AND {outbound_to_sql("o2", "c.ids", "c.ext")} END)
        ORDER BY c.person_id, (CASE WHEN c.person_id IS NULL AND c.group_id IS NULL THEN c.raw_id END),
                 c.group_id, c.channel_id, c.occurred_at
        LIMIT :lim"""),  # noqa: S608 — outbound_to_sql là hằng nội bộ
        {"o": org_id, "sla": RESPONSE_SLA_MIN, "win": RESPONSE_WINDOW_H, "lim": SCAN_LIMIT,
         "nokind": list(NO_REPLY_KINDS), "need": list(NEEDS_REPLY_EVENTS)})).all()
    n = 0
    for r in rows:
        ref = {"type": "raw", "id": str(r.raw_id)}
        if await _already_evidenced(db, org_id, "slow_response", ref):
            continue
        who = r.person_name or "một khách"
        title = f"Chưa trả lời {who} sau {RESPONSE_SLA_MIN} phút"
        summary = f"Phụ trách: {r.owner_name}" if r.owner_name else "Chưa có người phụ trách"
        await raise_alert(db, org_id, alert_type="slow_response", priority="P2", title=title, summary=summary,
                          suggested="Trả lời ngay hoặc giao cho người khác", subject_type="person",
                          subject_id=r.person_id, evidence=[ref], personnel_related=r.owner_user_id is not None)
        n += 1
    return n


# ─── cơ hội nóng nhưng chưa ai nhận ────────────────────────────────────────────

async def _scan_unclaimed_opportunity(db: AsyncSession, org_id: uuid.UUID, redis: Any = None) -> int:
    rows = (await db.execute(text("""
        SELECT id, code, need FROM biz.opportunities
        WHERE org_id = :o AND owner_user_id IS NULL AND stage NOT IN ('won', 'lost', 'dormant')
          AND created_at < now() - make_interval(hours => :h)
        LIMIT :lim"""), {"o": org_id, "h": UNCLAIMED_AGE_H, "lim": SCAN_LIMIT})).all()
    n = 0
    for r in rows:
        if await _already_open(db, org_id, "unclaimed_opportunity", "opportunity", r.id):
            continue
        await raise_alert(db, org_id, alert_type="unclaimed_opportunity", priority="P1",
                          title=f"{r.code} chưa ai nhận sau {UNCLAIMED_AGE_H} giờ", summary=r.need,
                          suggested="Gán người phụ trách", subject_type="opportunity", subject_id=r.id,
                          evidence=[{"type": "opportunity", "id": str(r.id), "code": r.code}], redis=redis)
        n += 1
    return n


# ─── đối thủ xuất hiện trong hội thoại ─────────────────────────────────────────

async def _scan_competitor(db: AsyncSession, org_id: uuid.UUID, redis: Any = None) -> int:
    rows = (await db.execute(text("""
        SELECT mu.id, mu.person_id, mu.group_id, mu.conclusion FROM clean.meaning_units mu
        WHERE mu.org_id = :o AND mu.event_type = 'MentionsCompetitor' AND mu.superseded_by IS NULL
          AND mu.created_at > now() - make_interval(hours => :h)
        LIMIT :lim"""), {"o": org_id, "h": COMPETITOR_WINDOW_H, "lim": SCAN_LIMIT})).all()
    n = 0
    for r in rows:
        ref = {"type": "meaning_unit", "id": str(r.id)}
        if await _already_evidenced(db, org_id, "competitor", ref):
            continue
        await raise_alert(db, org_id, alert_type="competitor", priority="P2", title="Đối thủ xuất hiện trong hội thoại",
                          summary=r.conclusion, suggested="Xem lại giá / điều khoản đang chào",
                          subject_type="person" if r.person_id else "group", subject_id=r.person_id or r.group_id,
                          evidence=[ref])
        n += 1
    return n


# ─── deadline / lời hứa bị bỏ quên ─────────────────────────────────────────────

async def _scan_forgotten_deadline(db: AsyncSession, org_id: uuid.UUID, redis: Any = None) -> int:
    rows = (await db.execute(text("""
        SELECT id, promiser_person_id, text, meaning_unit_id FROM biz.promises
        WHERE org_id = :o AND kept_at IS NULL AND due_at < now() AND broken IS NOT TRUE
        LIMIT :lim"""), {"o": org_id, "lim": SCAN_LIMIT})).all()
    n = 0
    for r in rows:
        await db.execute(text("UPDATE biz.promises SET broken = true WHERE id = :i"), {"i": r.id})
        evidence = [{"type": "meaning_unit", "id": str(r.meaning_unit_id)}] if r.meaning_unit_id else []
        await raise_alert(db, org_id, alert_type="forgotten_deadline", priority="P1",
                          title="Một lời hứa đã quá hạn mà chưa thực hiện", summary=r.text,
                          suggested="Liên hệ lại và cập nhật tiến độ", subject_type="person",
                          subject_id=r.promiser_person_id, evidence=evidence, redis=redis)
        n += 1
    return n


async def early_warning_scan(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    redis = ctx.get("redis_bus")
    out: dict[str, int] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        async with sm() as db:
            n = (await _scan_customer_cooling(db, org, redis)) + (await _scan_slow_response(db, org, redis)) + \
                (await _scan_unclaimed_opportunity(db, org, redis)) + (await _scan_competitor(db, org, redis)) + \
                (await _scan_forgotten_deadline(db, org, redis))
            await db.commit()
        if n:
            await _notify(redis, org)
        out[str(org)] = n
    return out


# ─── nhắc việc đến giờ (v0.1.24 — Đợt A4, Gen v2) ─────────────────────────────

REMINDER_BATCH = 200


def _local(dt: Any, tz: str | None) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        zone = ZoneInfo(tz or "Asia/Ho_Chi_Minh")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return str(dt.astimezone(zone).strftime("%H:%M %d/%m/%Y"))


async def due_reminders(db: AsyncSession, redis: Any = None) -> int:
    """Việc có `remind_at` đã tới, chưa nhắc, chưa xong → thông báo chuông cho người phụ trách (chưa giao ai → các
    Owner). Đánh dấu `reminded_at` cùng transaction (FOR UPDATE SKIP LOCKED: hai worker không nhắc trùng)."""
    from gh.notifications import notify, owner_ids, pending_mark, pending_reset
    from gh.telegram import service as telegram

    rows = (await db.execute(text("""
        UPDATE biz.tasks t SET reminded_at = now()
        WHERE t.id IN (SELECT id FROM biz.tasks
                       WHERE remind_at IS NOT NULL AND remind_at <= now() AND reminded_at IS NULL
                         AND status NOT IN ('done', 'cancelled')
                       ORDER BY remind_at LIMIT :n FOR UPDATE SKIP LOCKED)
        RETURNING t.id, t.org_id, t.code, t.title, t.priority, t.assignee_user_id, t.due_at, t.remind_at,
                  (SELECT o.timezone FROM core.organizations o WHERE o.id = t.org_id) AS tz"""),
        {"n": REMINDER_BATCH})).all()
    sent = 0
    for r in rows:
        # Mỗi dòng một savepoint (v0.1.27): một việc lỗi (dữ liệu hỏng, người nhận không còn…) không làm hỏng cả lô —
        # các nhắc khác vẫn gửi; dòng lỗi vẫn giữ `reminded_at` để không lặp lỗi mỗi phút (ghi log để xem lại).
        mark = pending_mark(db)
        try:
            async with db.begin_nested():
                owners = await owner_ids(db, r.org_id)
                to = [r.assignee_user_id] if r.assignee_user_id else owners
                due = f" · hạn {_local(r.due_at, r.tz)}" if r.due_at else ""
                await notify(db, r.org_id, to, kind="task.reminder", title=f"Nhắc việc: {r.title}",
                             body=f"{r.code} · {r.priority}{due}", link="/tasks", redis=redis)
                # v0.1.44 (F-8c): người nhận là Owner (chưa giao ai ⇒ các Owner; hoặc giao cho một Owner) ⇒ thêm một
                # tin Telegram (hộp thư đi, cùng savepoint — dòng lỗi thì tin cũng không xếp).
                if not r.assignee_user_id or r.assignee_user_id in owners:
                    await telegram.enqueue(db, r.org_id, "reminder",
                                           telegram.reminder_text(r.title, r.code, r.priority,
                                                                  _local(r.due_at, r.tz) if r.due_at else None),
                                           dedupe_key=f"reminder:{r.id}:{r.remind_at.isoformat()}")
            sent += 1
        except Exception:  # noqa: BLE001 — cô lập lỗi từng dòng
            pending_reset(db, mark)  # bỏ sự kiện WS của thông báo vừa bị hoàn tác
            log.exception("Nhắc việc lỗi cho %s (%s) — bỏ qua, các nhắc khác vẫn gửi", r.code, r.id)
    return sent


async def task_reminder_scan(ctx: dict[str, Any]) -> int:
    from gh.db import sessionmaker

    async with sessionmaker()() as db:
        n = await due_reminders(db, ctx.get("redis_bus"))
        await db.commit()
    return n


# v0.1.25 (Đợt C1): lọc đầu Hộp thư — hook sau sàng lọc + quét vét mỗi 5 phút (gh.refinery.triage).
HOOKS: list[Hook] = [*triage.HOOKS]
JOBS: list[CronJob] = [(early_warning_scan, {"minute": set(range(3, 60, 15))}),
                       (task_reminder_scan, {"minute": set(range(60))}),  # mỗi phút
                       *triage.JOBS]
