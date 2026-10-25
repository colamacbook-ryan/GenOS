"""Telegram 2 chiều — lệnh và trả lời của Gen (v0.1.64, T2) · v0.1.71 (G5): LỚP MỎNG trên lõi chung `gh.owner_cmd`.

`handle(cmd, ctx) -> (reply_text, buttons)` nhận `TelegramCommand` ĐÃ XÁC THỰC (gh.telegram.inbound), đổi sang
`OwnerCommand(via="telegram")` rồi gọi `gh.owner_cmd.core.handle`; `Reply` đổi lại thành (chữ, [(chữ nút, URL)]).
Toàn bộ nghiệp vụ (lệnh /hom_nay /viec /suc_khoe /nhap, hỏi tự do chỉ đọc, che dữ liệu, bọc "không tin cậy", defang,
Action Log) nằm ở lõi chung và dùng lại cho kênh quản trị Zalo/WhatsApp — hành vi Telegram KHÔNG đổi.

Luật cứng: Gen chỉ ĐỀ XUẤT. Telegram trả "Đã soạn đề xuất #N" kèm MỘT nút LINK "Mở Console để Xác nhận" — KHÔNG có nút
Duyệt, KHÔNG nhận mã PIN. Gửi tin khách, ghi Kho, mcp_write, đổi cài đặt: không có đường nào từ module này (test
quét import: tests/test_telegram_commands_v0164.py, tests/test_owner_cmd_v0171.py).

Các hàm `_hom_nay`, `_viec`, `_suc_khoe`, `_todos`, `_read_context` giữ làm điểm móc: lõi gọi chúng qua `overrides`
tra tại thời điểm gọi, nên test vá (`monkeypatch.setattr(commands, "_todos", …)`) vẫn có tác dụng như trước khi tách.
"""

import logging
from typing import Any

from sqlalchemy import text

from gh.owner_cmd import core as oc
from gh.owner_cmd.core import (  # noqa: F401 — hằng/tên cũ của module này, test và poller còn dùng
    CONSOLE_BUTTON,
    CONTEXT_MAX,
    FAILED,
    GEN_OFF,
    HELP,
    MAX_LIST,
    MAX_PROPOSALS,
    MODEL_DOWN,
    NEED_TEXT,
    NO_MODEL,
    NO_OWNER,
    NOT_UNDERSTOOD,
    NOTHING,
    UNKNOWN,
    Button,
    Context,
    owner_user,
    short_id,
)
from gh.telegram import backup, inbound

log = logging.getLogger("gh.telegram.commands")

VIA: oc.Via = "telegram"
PURPOSE = oc.purpose_of(VIA)  # "gen.telegram"
#: Các loại lệnh Telegram hiểu — phải khớp lõi chung (`oc.KINDS`).
KINDS = ("start", "tro_giup", "help", "hom_nay", "viec", "suc_khoe", "nhap", "hoi")
#: v0.1.76: lệnh riêng của Telegram (kênh phụ), không thuộc lõi chung.
TELEGRAM_ONLY_KINDS = (inbound.LOCK_KIND,)
LOCKED_TEXT = ("Đã KHOÁ trợ lý: kênh quản trị và chế độ hỗ trợ nhóm đã tắt, tin đang chờ gửi đã huỷ. "
               "Mở khoá và bật lại từng phần trên Console (cần mã PIN).")
ALREADY_LOCKED = "Trợ lý đã khoá từ trước. Mở khoá trên Console (cần mã PIN)."
LOCK_DENIED = "Em không khoá được từ tài khoản này. Sếp khoá trên Console."

Reply = tuple[str, list[Button]]


async def _hom_nay(ctx: Context) -> oc.Reply:
    return await oc.hom_nay(ctx)


async def _todos(ctx: Context) -> list[dict[str, Any]]:
    return await oc.todos(ctx)


async def _viec(ctx: Context) -> oc.Reply:
    return await oc.viec(ctx, todos_fn=_todos)  # `_todos` tra lúc gọi ⇒ test vá `commands._todos` có tác dụng


async def _suc_khoe(ctx: Context) -> oc.Reply:
    return await oc.suc_khoe(ctx)


async def _read_context(ctx: Context) -> str:
    return await oc.read_context(ctx, VIA)


async def _lock(cmd: inbound.TelegramCommand, ctx: Context) -> Reply:
    """v0.1.76 (T2): lệnh khẩn "khoá trợ lý" — CHỈ khoá (không có đường mở khoá ở đây).
    `backup.lock_assistant` kiểm LẠI người ra lệnh theo `from.id` + `chat.id` đã ghép."""
    try:
        async with ctx.sm() as db:
            await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(ctx.org_id)})
            res = await backup.lock_assistant(db, ctx.org_id, via="telegram", tg_user_id=cmd.user_id,
                                              chat_id=cmd.chat_id)
            await db.commit()
    except Exception:  # noqa: BLE001 — không được làm sập vòng nhận tin; Sếp còn Console
        log.exception("telegram: khoá trợ lý lỗi")
        return oc.FAILED, []
    if not res.ok:
        return LOCK_DENIED, []
    return (ALREADY_LOCKED if res.already else LOCKED_TEXT), []


async def handle(cmd: inbound.TelegramCommand, ctx: Context) -> Reply:
    """Trả (chữ trả lời, nút link). Nút chỉ có khi vừa soạn đề xuất: "Mở Console để Xác nhận". Không ném."""
    if cmd.kind == inbound.LOCK_KIND:
        return await _lock(cmd, ctx)
    owner_cmd = oc.OwnerCommand(
        via=VIA, kind=cmd.kind, text=cmd.text, message_ref=str(cmd.message_id), received_at=cmd.received_at
    )
    reply = await oc.handle(
        owner_cmd, ctx, overrides={"hom_nay": _hom_nay, "viec": _viec, "suc_khoe": _suc_khoe}
    )
    return reply.text, list(reply.links)


__all__ = ["Context", "handle", "short_id", "owner_user", "CONSOLE_BUTTON"]
