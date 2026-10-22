# ruff: noqa: E501
"""Tin 1-1 của Sếp tới bot (kênh quản trị). Lõi lệnh dùng chung với Telegram (`gh.owner_cmd`: chỉ đọc + ĐỀ
XUẤT), thêm lệnh
nhóm (`group_cmds`: đổi cấu hình). Lời đáp đi qua cửa `send_auto(owner_dm)` — mọi kiểm tra Owner làm LẠI ở đó."""

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.chassis.bus import EventBus
from gh.gen import chat_pending
from gh.owner_channel import chat_confirm, gen_chat, group_cmds
from gh.owner_channel.autosend import send_auto
from gh.owner_channel.basis import Basis, Trigger
from gh.owner_channel.identity import owner_ids
from gh.owner_cmd import core as owner_core
from gh.owner_cmd import parse as owner_parse

log = logging.getLogger("gh.owner_channel.dm")

REPLY_MAX = chat_pending.REPLY_MAX
RATE_TEXT = "Sếp nhắn nhanh quá, em chờ một chút rồi mới trả lời tiếp được ạ."
GROUP_HELP = (
    "\nLệnh nhóm (chỉ ở kênh này):\n"
    "/nhom — các nhóm em đang ở\n"
    "/nghe <mã> · /bo <mã> — trả lời câu hỏi nhóm mới\n"
    "/ho_tro — xem nhóm đang bật hỗ trợ · /ho_tro <mã nhóm> bat|tat\n"
    "/xac_nhan <mã> — xác nhận bật hỗ trợ"
)


async def build_reply(sm: async_sessionmaker[AsyncSession], redis: Redis, router: Any, org_id: uuid.UUID, oi: Any,
                      p: dict[str, Any], app: Any = None) -> str:
    decision = chat_confirm.parse_decision(str(p.get("text") or ""))
    if decision is not None:        # v0.1.73: «OK» / «HUỶ» trả lời đề xuất Gen vừa hỏi trong chính kênh này
        return await chat_confirm.handle(sm, redis, app, org_id, oi, decision, str(p.get("external_msg_id") or ""))
    kind, args = owner_parse.parse_text(str(p.get("text") or ""))
    if kind in group_cmds.KINDS:
        async with sm() as db:
            out = await group_cmds.handle(db, redis, org_id, oi, kind, args)
            await db.commit()
        return out
    cmd = owner_core.OwnerCommand(via=oi.channel, kind=kind, text=args, message_ref=str(p.get("external_msg_id") or ""),
                                  received_at=datetime.now(UTC))
    # v0.1.74: câu hỏi tự do ở kênh quản trị chạy bằng động cơ Gen đầy đủ (công cụ đọc + trí nhớ hội thoại + đề xuất vào sổ chờ OK)
    ctx = owner_core.Context(sm=sm, redis=redis, router=router, org_id=org_id, owner_user_id=oi.owner_user_id, app=app,
                             hoi_fn=gen_chat.hoi)
    reply = await owner_core.handle(cmd, ctx)
    text_ = reply.plain()
    if kind in ("start", "tro_giup", "help"):
        text_ += GROUP_HELP
    return text_


async def handle_dm(sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus, router: Any,
                    org_id: uuid.UUID, p: dict[str, Any], app: Any = None) -> str:
    """Consumer `dm`. Trả nhãn kết quả ('replied' | 'rate' | 'ignored:<lý do>' | 'refused:<mã>'). Không ném."""
    ch, msg_id = str(p.get("channel") or ""), str(p.get("external_msg_id") or "")
    t0 = time.time()
    try:
        async with sm() as db:
            oi = await owner_ids(db, org_id, ch)
            await db.rollback()
        if not oi.enabled or str(oi.session_id) != str(p.get("session_id")):
            return "ignored:not_enabled"
        trigger = Trigger("owner_message", f"{ch}:{msg_id}")
        if not await owner_core.rate_ok(redis, org_id, ch):
            if await redis.set(f"gh:owner:ratemsg:{org_id}:{ch}", 1, nx=True, ex=60):
                async with sm() as db:
                    await send_auto(db, bus, redis, org_id=org_id, channel=ch, basis=Basis.OWNER_DM, trigger=trigger,
                                    text=RATE_TEXT)
            return "rate"
        full = await build_reply(sm, redis, router, org_id, oi, p, app)
        body = full[:REPLY_MAX]
        if len(full) > REPLY_MAX:       # phòng hờ: bị cắt ⇒ không giữ trong sổ chờ
            await chat_pending.forget_since(redis, org_id, ch, t0)
        async with sm() as db:
            r = await send_auto(db, bus, redis, org_id=org_id, channel=ch, basis=Basis.OWNER_DM, trigger=trigger,
                                text=body)
        if not r.ok:                    # guard chặn / gửi lỗi ⇒ Sếp không thấy câu hỏi
            await chat_pending.forget_since(redis, org_id, ch, t0)
        else:                           # tin khác ⇒ câu hỏi cũ hết là «câu hỏi cuối»
            last = await chat_pending.last_ask(redis, org_id, ch)
            if last is not None and last[1] < t0:
                await chat_pending.mark_last(redis, org_id, ch, None)
        return "replied" if r.ok else f"refused:{r.code}"
    except Exception:  # noqa: BLE001 — một tin hỏng không được chặn các tin sau
        log.exception("kênh quản trị: xử lý tin 1-1 lỗi")
        try:
            await chat_pending.forget_since(redis, org_id, ch, t0)
        except Exception:  # noqa: BLE001
            log.warning("kênh quản trị: không gỡ được sổ chờ sau lỗi", exc_info=True)
        return "error"

