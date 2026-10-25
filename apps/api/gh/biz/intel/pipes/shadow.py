# ruff: noqa: E501
"""Gói gán nhãn hằng ngày khi chạy bóng (v0.1.76, gói T3 — spec r6 §2.1 bảng "Chạy bóng", CNS-075). CHỈ chạy khi `intel.mode = shadow`.

Mỗi ngày ~20 mục gửi Sếp qua Zalo (một tin `kind='ask'`, `refs` mang tầng + xác suất chọn) để đo M1 / M11 trên chat thật:
- tầng `shadow`: mọi 🔴 bóng trong 24 giờ (`would_push_at`), Sếp trả lời «N đúng» / «N sai» (đếm M1 theo cổng); xác suất 1;
- tầng giàu tín hiệu cho phần máy KHÔNG báo (BẢO MẬT: tin gửi qua chat CHỈ mang số thứ tự + tầng, KHÔNG có chữ của đơn vị / tin thô — chúng chưa qua cổng
  riêng tư (console_pin, nhân sự, nhóm ẩn); nội dung mẫu xem và gán nhãn trên Console sau PIN): (t2) đơn vị có `kind_hint` nhưng không lên tín hiệu, (t3) đơn vị mức thấp, (t4) tin ngẫu nhiên nhỏ.
  Mỗi tầng chọn ngẫu nhiên có hạt giống cố định theo (tổ chức, ngày) với xác suất `p = chỉ tiêu / số ứng viên`; Sếp trả lời «N sót» nếu đáng lẽ phải báo.
  Số sót ước tính = Σ (sót trong mẫu tầng / p) — T9 tính; ở đây chỉ ghi `tier` và `p` vào `refs` và vào ghi chú phản hồi.
Chỉ ứng viên `chat_ok`; chữ hiển thị đã che dạng "0123…789". Đang khoá trợ lý ⇒ không gửi gì.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob
from gh.biz.intel.pipes import delivery
from gh.owner_channel import reports
from gh.telegram import backup

log = logging.getLogger("gh.intel.shadow")

WINDOW = timedelta(hours=24)
QUOTA = {"shadow": 10, "t2": 5, "t3": 3, "t4": 2}          # ~20 mục
SEND_HOUR = 20                                               # 20:00 giờ VN, sau bản tin 17:30
LINE_MAX = 110


async def is_shadow(db: AsyncSession, org_id: uuid.UUID) -> bool:
    return (await db.execute(text("SELECT settings->'intel'->>'mode' FROM core.organizations WHERE id = :o"), {"o": org_id})).scalar_one_or_none() == "shadow"


def _pick(rows: list[Any], quota: int, rnd: random.Random) -> tuple[list[Any], float]:
    if not rows:
        return [], 1.0
    k = min(quota, len(rows))
    return rnd.sample(rows, k), k / len(rows)


async def build_pack(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> list[dict[str, Any]]:
    """Mục của gói hôm nay: [{tier, p, text, signal_id|None, raw_event_id|None}]. Thuần đọc."""
    since = now - WINDOW
    rnd = random.Random(f"{org_id}:{now.astimezone(delivery_tz()).date().isoformat()}")
    out: list[dict[str, Any]] = []
    sh = (await db.execute(text("""SELECT id, title FROM biz.alerts WHERE org_id = :o AND would_push_at > :s AND privacy = 'chat_ok'
                                   AND kind NOT IN ('system', 'legacy') ORDER BY would_push_at LIMIT :n"""),
                           {"o": org_id, "s": since, "n": QUOTA["shadow"]})).all()
    out += [{"tier": "shadow", "p": 1.0, "text": r.title, "signal_id": str(r.id), "raw_event_id": None} for r in sh]
    t2 = (await db.execute(text("""
        SELECT u.id FROM clean.meaning_units u WHERE u.org_id = :o AND u.observed_at > :s AND u.superseded_by IS NULL
          AND u.kind_hint IS NOT NULL AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = u.org_id AND a.evidence::text LIKE '%' || u.id::text || '%')
        ORDER BY u.id"""), {"o": org_id, "s": since})).all()
    picked, p = _pick(t2, QUOTA["t2"], rnd)
    out += [{"tier": "t2", "p": round(p, 4), "text": None, "signal_id": None, "raw_event_id": None} for r in picked]
    t3 = (await db.execute(text("""SELECT u.id FROM clean.meaning_units u WHERE u.org_id = :o AND u.observed_at > :s
                                   AND u.superseded_by IS NULL AND u.level_hint = 'grey' AND u.kind_hint IS NULL ORDER BY u.id"""),
                           {"o": org_id, "s": since})).all()
    picked, p = _pick(t3, QUOTA["t3"], rnd)
    out += [{"tier": "t3", "p": round(p, 4), "text": None, "signal_id": None, "raw_event_id": None} for r in picked]
    t4 = (await db.execute(text("""SELECT id FROM raw.events WHERE org_id = :o AND occurred_at > :s AND direction = 'inbound'
                                   AND kind = 'text' AND length(coalesce(body_text, '')) >= 12 ORDER BY id"""), {"o": org_id, "s": since})).all()
    picked, p = _pick(t4, QUOTA["t4"], rnd)
    out += [{"tier": "t4", "p": round(p, 4), "text": None, "signal_id": None, "raw_event_id": str(r.id)} for r in picked]
    return out


def delivery_tz() -> Any:
    from gh.biz.intel import levels

    return levels.VN


def render(items: list[dict[str, Any]], label: str) -> str:
    lines = [f"Gói gán nhãn chạy thử {label} ({len(items)} mục)"]
    for i, it in enumerate(items, 1):
        if it["tier"] == "shadow":
            lines.append(delivery.display_mask(f"{i}. 🔴 {delivery._one_line(str(it['text'] or ''), LINE_MAX)}"))
        else:
            lines.append(f"{i}. • mẫu kiểm tra ({it['tier']}) — nội dung xem trên Console")
    lines.append("Trả lời: «N đúng» / «N sai» cho mục 🔴. Mục • xem và gán nhãn trên Console › Hệ thống › Tình báo (mã PIN).")
    return "\n".join(lines)


async def deliver_pack(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> str:
    """Một lượt cho một tổ chức: shadow | locked | no_channel | nothing | sent | already_sent. Bên gọi commit."""
    if not await is_shadow(db, org_id):
        return "off"
    if await backup.is_locked(db, org_id):
        return "locked"
    ch = await delivery._channel(db, org_id)
    if ch is None:
        return "no_channel"
    items = await build_pack(db, org_id, now)
    if not items:
        return "nothing"
    day = now.astimezone(delivery_tz()).date().isoformat()
    refs = [{"n": i, "signal_id": it["signal_id"], "raw_event_id": it["raw_event_id"], "tier": it["tier"], "p": it["p"], "ask_id": None,
             "ask_code": None, "cluster_ids": None} for i, it in enumerate(items, 1)]
    ok = await reports.enqueue(db, org_id, ch, kind="ask", severity="important", text_=render(items, day), dedupe_key=f"shadow-pack:{day}",
                               refs=refs, now=now, bypass_quiet=False)
    return "sent" if ok else "already_sent"


async def shadow_pack(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import clock

    now = clock.now()
    out = {"sent": 0, "errors": 0}
    if now.astimezone(delivery_tz()).hour != SEND_HOUR:
        return out
    sm = sessionmaker()
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                if await deliver_pack(db, org, now) == "sent":
                    out["sent"] += 1
                await db.commit()
        except Exception:                                       # noqa: BLE001
            log.exception("shadow_pack lỗi ở một tổ chức")
            out["errors"] += 1
    return out


JOBS: list[CronJob] = [(shadow_pack, {"minute": {0, 5, 10}})]
