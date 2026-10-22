# ruff: noqa: E501
"""v0.1.74 — Kênh quản trị Zalo / WhatsApp 1-1 dùng CHUNG bộ não Gen đầy đủ của Console.

Trước đây câu hỏi tự do ở kênh này là MỘT lượt rời (không lịch sử, không công cụ, tối đa 6 dòng) nên Sếp hỏi «nhóm nào có gì mới»
thì bot trả lời tào lao. Nay câu hỏi tự do chạy ĐÚNG động cơ một lượt của Console (`gh.gen.engine.run_channel_turn`):
  - Trí nhớ: mỗi kênh quản trị có MỘT hội thoại Gen riêng (id lưu ở `ops.owner_channels.report_prefs.gen_conversation_id`,
    không migration); lịch sử gửi kèm như ở Console. Hội thoại bị xoá (Console / hạn lưu) ⇒ tự tạo lại.
  - Công cụ CHỈ ĐỌC (và bước `propose` = ĐỀ XUẤT) áp đúng quyền của Owner đã ghép: tool gọi nội bộ (ASGI) bằng một phiên Console
    NGẮN HẠN (cơ sở `chat_gen`, KHÔNG có cửa sổ PIN, thu hồi ngay sau lượt) nên mọi RBAC / phạm vi / che dữ liệu của endpoint giữ
    nguyên; kết quả vẫn qua `mask_for_prompt` + `wrap_untrusted` như Console.
  - Nói bằng Agent ngoại giao (không xưng Gen), văn bản thường.
  - Câu tự nhiên («nhắc tôi…», «ghi nhớ…», «soạn tin cho nhóm X…») ⇒ Gen tự ĐỀ XUẤT; đề xuất vào sổ chờ `chat_pending` để Sếp «OK»
    (mọi luật v0.1.73 giữ nguyên: OK trần chỉ cho câu hỏi cuối trong 10 phút; tin gửi ngoài nêu người nhận + nguyên văn, OK riêng).
Module này KHÔNG gửi gì lên bridge và không có đường ghi: chỉ TRẢ chữ cho `dm.handle_dm` (cửa `send_auto`).
"""

import logging
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.chassis import actionlog
from gh.gen import chat_pending, diplomat, engine, proposals, store
from gh.owner_channel import chat_confirm
from gh.owner_cmd import core as owner_core
from gh.owner_cmd import text as otext

log = logging.getLogger("gh.owner_channel.gen_chat")

PREF_KEY = "gen_conversation_id"
UNSURE = "Em chưa hiểu ý Sếp lắm, Sếp nói rõ hơn một chút giúp em nhé."
MORE = "\n… (còn nữa — Sếp nhắn «tiếp» để em nói tiếp)"
#: Chừa chỗ cho phần đuôi (nhắc đề xuất…) khi lời Gen đứng một mình.
ANSWER_MAX = chat_pending.REPLY_MAX - 300

_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.S)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_BULLET = re.compile(r"^\s*[*\-]\s+", re.M)


def plain_text(s: str) -> str:
    """Bỏ Markdown còn sót (đậm, tiêu đề, backtick, gạch đầu dòng) — kênh chat hiển thị chữ thường."""
    s = _BOLD.sub(r"\2", s)
    s = _HEADING.sub("", s)
    s = _BULLET.sub("• ", s)
    return s.replace("`", "").strip()


def clip(s: str, limit: int = ANSWER_MAX) -> str:
    """Quá dài ⇒ cắt ở ranh giới câu / dòng gần nhất rồi mời Sếp hỏi tiếp (không cắt giữa chừng khó hiểu)."""
    if len(s) <= limit:
        return s
    cut = s[: limit - len(MORE)]
    k = max(cut.rfind("\n"), cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if k > limit // 2:
        cut = cut[: k + 1]
    return cut.rstrip() + MORE


async def _set_org(db: AsyncSession, org_id: uuid.UUID) -> None:
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})


async def conversation_id(ctx: owner_core.Context, user: service.CurrentUser, via: str) -> uuid.UUID:
    """Hội thoại Gen riêng của kênh quản trị (tạo lần đầu / khi hội thoại cũ đã mất) — ghi id vào `report_prefs`."""
    async with ctx.sm() as db:
        await _set_org(db, ctx.org_id)
        raw = (await db.execute(text("""SELECT report_prefs->>:k FROM ops.owner_channels
                                        WHERE org_id = :o AND channel_type = :c"""),
                                {"k": PREF_KEY, "o": ctx.org_id, "c": via})).scalar_one_or_none()
        cid: uuid.UUID | None = None
        try:
            cid = uuid.UUID(str(raw)) if raw else None
        except ValueError:
            cid = None
        if cid is not None and not await store.owned(db, user, cid):
            cid = None
        if cid is None:
            cid = await store.create_conversation(db, user, f"{owner_core.VIA_LABEL.get(via, via)} · Kênh quản trị")
            await db.execute(text("""UPDATE ops.owner_channels
                                     SET report_prefs = jsonb_set(report_prefs, ARRAY[:k], to_jsonb(CAST(:v AS text)), true)
                                     WHERE org_id = :o AND channel_type = :c"""),
                             {"k": PREF_KEY, "v": str(cid), "o": ctx.org_id, "c": via})
        await db.commit()
    return cid


async def hoi(cmd: owner_core.OwnerCommand, ctx: owner_core.Context) -> owner_core.Reply:
    """Trả lời câu hỏi tự do bằng động cơ Gen đầy đủ (xem docstring module). Không có `app` (gọi lẻ) ⇒ về một lượt như cũ."""
    if ctx.app is None:
        return await owner_core.hoi(cmd, ctx)
    user, bad = await owner_core._gen_gate(ctx, cmd.via)
    if user is None or ctx.router is None:
        return owner_core.Reply(bad or owner_core.NO_MODEL)
    via = cmd.via
    cid = await conversation_id(ctx, user, via)
    turn_id = uuid.uuid4()
    async with ctx.sm() as db:
        await _set_org(db, ctx.org_id)
        await store.add_message(db, ctx.org_id, cid, "user", {"text": cmd.text[:4000], "via": via}, turn_id)
        await actionlog.record(db, org_id=ctx.org_id, actor_type="agent", actor_id="gen", action="gen.turn",
                               target_type="gen_conversation", target_id=str(cid), autonomy_level=1,
                               detail={"on_behalf_of": str(user.id), "conversation_id": str(cid), "turn_id": str(turn_id),
                                       "text_digest": engine.digest(cmd.text), "via": via})
        await db.commit()
    inp = engine.TurnInput(turn_id=turn_id, conversation_id=cid, text=cmd.text[:4000], route=f"/{via}", screen_key=None,
                           channel=via, persona=ctx.persona)
    ses = await chat_confirm._mint(ctx.sm, ctx.org_id, user.id, via, pin=False, agent="chat_gen")
    try:
        turn = await engine.run_channel_turn(
            app=ctx.app, sm=ctx.sm, redis=ctx.redis, router=ctx.router, user=user,
            session_token=ses.cookies[service.SESSION_COOKIE], inp=inp,
            decider=getattr(ctx.app.state, "gen_decider", None))
    finally:
        await chat_confirm._revoke(ctx.sm, ctx.org_id, ses.id)
    says: list[str] = []
    made: list[dict[str, Any]] = []
    for ev in turn.steps:
        st = ev.get("step") or {}
        if st.get("kind") == "say" and str(st.get("text") or "").strip():
            says.append(str(st["text"]))
        elif st.get("kind") == "proposal" and isinstance(st.get("proposal"), dict):
            full = await proposals.load(ctx.redis, st["proposal"]["id"])
            if full is not None and full.get("status") == "pending":
                made.append(full)
    await owner_core._log(ctx, cmd, "gen.answer", model=turn.model, provider=turn.provider, proposals=len(made))
    said = otext.defang(clip(plain_text(diplomat.rename_gen(" ".join(says), ctx.persona)))) if says else ""
    if made:
        return await owner_core._chat_reply(ctx, cmd, made[: owner_core.MAX_PROPOSALS], [said] if said else [], cid,
                                            says_first=True)
    return owner_core.Reply(said or UNSURE)


__all__ = ["hoi", "plain_text", "clip", "conversation_id"]
