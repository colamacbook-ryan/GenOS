# ruff: noqa: E501
"""Tin 1-1 của Sếp tới bot (kênh quản trị). Lõi lệnh dùng chung với Telegram (`gh.owner_cmd`: chỉ đọc + ĐỀ
XUẤT), thêm lệnh
nhóm (`group_cmds`: đổi cấu hình). Lời đáp đi qua cửa `send_auto(owner_dm)` — mọi kiểm tra Owner làm LẠI ở đó."""

import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.biz.intel import levels, mutes, signals, strong_confirm
from gh.biz.intel.pipes import coverage, delivery
from gh.biz.mem import facts
from gh.chassis import actionlog
from gh.chassis.bus import EventBus
from gh.gen import chat_pending
from gh.owner_channel import chat_confirm, gen_chat, group_cmds
from gh.owner_channel.autosend import send_auto
from gh.owner_channel.basis import Basis, Trigger
from gh.owner_channel.identity import owner_ids
from gh.owner_cmd import core as owner_core
from gh.owner_cmd import parse as owner_parse
from gh.refinery import clock
from gh.telegram import backup
from gh.textnorm import strip_accents

log = logging.getLogger("gh.owner_channel.dm")

REPLY_MAX = chat_pending.REPLY_MAX
RATE_TEXT = "Sếp nhắn nhanh quá, em chờ một chút rồi mới trả lời tiếp được ạ."
GROUP_HELP = (
    "\nLệnh nhóm (chỉ ở kênh này):\n"
    "/nhom — các nhóm em đang ở\n"
    "/nghe <mã> · /bo <mã> — trả lời câu hỏi nhóm mới\n"
    "/ho_tro — xem nhóm đang bật hỗ trợ · /ho_tro <mã nhóm> bat|tat\n"
    "/xac_nhan <mã> — xác nhận bật hỗ trợ\n"
    "Tình báo: «sai 2» · «đúng 1» · «không cần báo 3» · «em sót: …» · «sửa 2: …» · «tắt báo loại/nhóm …» · "
    "«tắt bản tin» / «bật bản tin» · «giờ yên lặng 22:00-07:00»"
)


# ─── lệnh tình báo (v0.1.76, T3) ─────────────────────────────────────────────
# Chạy TRƯỚC `chat_confirm`; «N ok» vẫn thuộc `chat_confirm` (không khớp ở đây). Mọi lệnh qua cổng «Owner còn hợp lệ»; hành động nhạy
# cảm gọi `strong_confirm.require` (hạ phòng thủ ⇒ chỉ Console; tắt bản tin / đổi giờ yên lặng ⇒ làm được nhưng báo kênh phụ).
CONSOLE = "Việc này cần mở Console (mã PIN) — em không làm từ chat được ạ."
NOT_FOUND_REF = "Em không có mục số {n} trong bản tin gần nhất."
KIND_ALIASES = {"lua dao": "rui_ro_lua_dao", "an toan": "suc_khoe_an_toan", "suc khoe": "suc_khoe_an_toan",
                "suc khoe an toan": "suc_khoe_an_toan", "rui ro": "rui_ro_lua_dao", "rui ro lua dao": "rui_ro_lua_dao"}
KIND_LABELS = {"nghia vu loi hua": "nghia_vu_loi_hua", "nghia vu": "nghia_vu_loi_hua", "viec han": "viec_han", "viec & han": "viec_han",
               "cau hoi cho xu ly": "cau_hoi_cho_xu_ly", "cau hoi": "cau_hoi_cho_xu_ly", "su kien dip": "su_kien_dip", "su kien": "su_kien_dip",
               "xung dot khieu nai": "xung_dot_khieu_nai", "khieu nai": "xung_dot_khieu_nai", "quan he nong nguoi": "quan_he_nong_nguoi",
               "quan he": "quan_he_nong_nguoi", "co hoi ket noi": "co_hoi_ket_noi", "co hoi": "co_hoi_ket_noi",
               "thi truong thong tin": "thi_truong_thong_tin", "thi truong": "thi_truong_thong_tin"}
USAGE_QUIET = "Cách dùng: «giờ yên lặng 22:00-07:00» (giờ bắt đầu - giờ kết thúc, giờ Việt Nam)."
_FB = {"sai": "wrong", "dung": "correct", "khong can bao": "unimportant"}
_FB_RX = re.compile(r"^(?:(sai|dung|khong can bao) (\d{1,2})|(\d{1,2}) (sai|dung|khong can bao))$")
_MUTE_RX = re.compile(r"^(?:tat|dung|ngung) bao (loai|nhom) (.+?)(?: nua)?$")
_TIME_RX = re.compile(r"(\d{1,2})(?:[:h](\d{2})?)?")


def _fold(s: str) -> str:
    return " ".join(re.sub(r"[.!?,;…]+", " ", strip_accents(s).lower()).split())


def resolve_kind(name: str) -> str | None:
    f = _fold(name).replace("_", " ")
    code = f.replace(" ", "_")
    if code in levels.KINDS:
        return code
    return KIND_ALIASES.get(f) or KIND_LABELS.get(f)


def _hhmm(h: str, m: str | None) -> str | None:
    hh, mm = int(h), int(m or 0)
    return f"{hh:02d}:{mm:02d}" if hh < 24 and mm < 60 else None


def parse_intel(raw: str) -> tuple[str, Any] | None:
    """Nhận diện lệnh tình báo; None ⇒ không phải (đi tiếp đường cũ). Hàm thuần."""
    raw = raw.strip()
    head, colon, rest = raw.partition(":")
    hf = _fold(head)
    if colon:
        m = re.fullmatch(r"(?:em )?sot(?: (.+))?", hf)
        if m:
            return "sot", (m.group(1), rest.strip())
        m = re.fullmatch(r"sua(?: (\d{1,2}))?", hf)
        if m:
            return "sua", (int(m.group(1)) if m.group(1) else None, rest.strip())
    f = _fold(raw)
    m = _FB_RX.match(f)
    if m:
        return "feedback", (_FB[m.group(1) or m.group(4)], int(m.group(2) or m.group(3)))
    m = _MUTE_RX.match(f)
    if m:
        return "mute", (m.group(1), m.group(2))
    if f in ("tat ban tin", "bat ban tin", "bat lai ban tin"):
        return "digest", f.startswith("tat")
    m = re.match(r"^(?:gio )?yen lang(?: (.*))?$", f)
    if m:
        times = _TIME_RX.findall(m.group(1) or "")
        pair = [_hhmm(h, mm) for h, mm in times]
        return "quiet", (pair[0], pair[1]) if len(pair) == 2 and all(pair) else None
    if re.fullmatch(r"(?:che do bong|chay thu|chay that|bong)(?: bat| tat)?", f):
        return "mode", None
    return None


async def _last_refs(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    r = (await db.execute(text("""SELECT refs FROM ops.owner_outbox WHERE org_id = :o AND kind IN ('digest', 'alert') AND sent_at IS NOT NULL
                                  AND jsonb_array_length(refs) > 0 ORDER BY sent_at DESC, created_at DESC LIMIT 1"""), {"o": org_id})).scalar_one_or_none()
    return [x for x in (r or []) if isinstance(x, dict)]


async def _feedback(db: AsyncSession, org_id: uuid.UUID, owner: Any, verdict: str, n: int) -> str:
    ref = next((x for x in await _last_refs(db, org_id) if x.get("n") == n), None)
    if ref is None:
        return NOT_FOUND_REF.format(n=n)
    ids = [uuid.UUID(str(i)) for i in (ref.get("cluster_ids") or [ref.get("signal_id")]) if i]
    defensive = False
    for sid in ids:
        row = (await db.execute(text("SELECT kind, gate, candidate FROM biz.alerts WHERE id = :i AND org_id = :o"), {"i": sid, "o": org_id})).one_or_none()
        if row is None:
            return NOT_FOUND_REF.format(n=n)
        defensive = defensive or row.kind in mutes.DEFENSIVE_KINDS or (row.gate or row.candidate) in levels.BYPASS_GATES
    if defensive and verdict in ("wrong", "unimportant"):
        # «sai» đóng tín hiệu, «không cần báo» hạ ngưỡng ⇒ với lừa đảo/an toàn là HẠ PHÒNG THỦ: chỉ Console + PIN (không đổi gì, không ghi gì)
        res = await strong_confirm.require(db, org_id, "mute_defensive")
        return f"Mục {n} là vụ lừa đảo/an toàn nên em không đóng/hạ từ chat được. " + getattr(res, "reason", CONSOLE)
    try:
        for sid in ids:
            await signals.feedback(db, org_id, sid, verdict=verdict, verdict_by="owner", now=clock.now(), user_id=owner.id)
    except signals.SignalError:
        return NOT_FOUND_REF.format(n=n)
    return {"wrong": f"Em ghi nhận mục {n} báo sai, em sẽ chú ý hơn ạ.", "correct": f"Em ghi nhận mục {n} đúng ạ.",
            "unimportant": f"Em ghi nhận mục {n} không cần báo, em sẽ bớt báo loại này ạ."}[verdict]


async def _missed(db: AsyncSession, org_id: uuid.UUID, owner: Any, kind_word: str | None, content: str) -> str:
    kind = resolve_kind(kind_word) if kind_word else None
    if kind_word and kind is None:
        return "Em chưa hiểu loại «" + kind_word[:30] + "». Sếp nói «em sót lừa đảo: …» hoặc «em sót: …» ạ."
    if not content:
        return "Sếp nói rõ chuyện bị sót giúp em: «em sót: <chuyện gì>» (có thể dán nguyên tin)."
    _, gate = await coverage.record_missed(db, org_id, user_id=owner.id, text_=content, kind=kind)
    return "Em đã ghi nhận chuyện bị sót" + (" (em xếp vào vụ lừa đảo/an toàn để rà ngay)" if gate else "") + ", cảm ơn Sếp ạ."


async def _correct(db: AsyncSession, org_id: uuid.UUID, owner: Any, n: int | None, value: str) -> str:
    if n is None or not value:
        return "Sếp nói rõ mục nào và đúng là gì: «sửa 2: <giá trị đúng>» (số mục theo bản tin gần nhất)."
    ref = next((x for x in await _last_refs(db, org_id) if x.get("n") == n), None)
    if ref is None:
        return NOT_FOUND_REF.format(n=n)
    if ref.get("cluster_ids"):
        return "Mục này gộp nhiều chuyện — Sếp sửa trên Console (mặt «Tôi») giúp em."
    fids = (await db.execute(text("SELECT fact_ids FROM biz.alerts WHERE id = :i AND org_id = :o"),
                             {"i": uuid.UUID(str(ref["signal_id"])), "o": org_id})).scalar_one_or_none() or []
    if len(fids) != 1:
        return "Mục này không gắn đúng một sự thật để sửa — Sếp sửa trên Console (mặt «Tôi») giúp em."
    old = await facts.get(db, org_id, fids[0])
    if old is None:
        return NOT_FOUND_REF.format(n=n)
    if old.sensitivity != "normal" or old.object_entity_id is not None or old.predicate == "payment_account":
        return CONSOLE                                  # STK/SĐT/nhân sự, hoặc đối tượng là thực thể: chỉ Console + PIN
    log_id = await actionlog.record(db, org_id=org_id, actor_type="user", actor_id=owner.actor_id, action="mem.fact_correct", target_type="fact",
                                    target_id=str(old.id), target_label=old.predicate, detail={"retract": False, "predicate": old.predicate, "via": "chat"})
    try:
        await facts.correct(db, org_id, old.id, actor_user_id=owner.id, new_value=value[:500], action_log_id=log_id)
    except facts.FactWriteError:
        return "Em chưa sửa được mục này — Sếp sửa trên Console (mặt «Tôi») giúp em."
    return f"Em đã sửa mục {n} thành «{value[:80]}»."


async def _mute(db: AsyncSession, org_id: uuid.UUID, oi: Any, owner: Any, what: str, name: str) -> str:
    if what == "loai":
        kind = resolve_kind(name)
        if kind is None:
            return "Em chưa hiểu loại «" + name[:30] + "». Ví dụ: «tắt báo loại sự kiện»."
        if kind in mutes.DEFENSIVE_KINDS:
            res = await strong_confirm.require(db, org_id, "mute_defensive")
            return getattr(res, "reason", CONSOLE)      # từ chat luôn NeedConsole
        await mutes.create(db, org_id, scope_type="kind", scope_value=kind, source="chat", user_id=owner.id)
        return f"Đã tắt báo loại «{name[:30]}» từ giờ (lừa đảo/an toàn thì em vẫn luôn báo)."
    from gh.owner_channel import group_cmds

    g = await group_cmds._find_group(db, org_id, oi.channel, name)
    if g is None:
        return "Em không tìm thấy nhóm đó. Gõ /nhom để xem mã nhóm."
    await mutes.create(db, org_id, scope_type="group", scope_value=str(g.id), source="chat", user_id=owner.id)
    return f"Đã tắt báo nhóm «{str(g.name)[:60]}» từ giờ (lừa đảo/an toàn trong nhóm em vẫn báo)."


async def intel_command(db: AsyncSession, org_id: uuid.UUID, oi: Any, cmd: tuple[str, Any]) -> str:
    """Chạy một lệnh tình báo đã nhận diện; commit do bên gọi. Trả chữ trả lời."""
    from gh.owner_channel import group_cmds

    checked = await group_cmds._owner_gate(db, org_id, oi)
    if checked is None:
        return owner_core.no_owner_text(oi.channel)
    owner = await owner_core.owner_user(db, org_id, checked.owner_user_id)
    if owner is None:
        return owner_core.no_owner_text(oi.channel)
    kind, arg = cmd
    if kind == "feedback":
        return await _feedback(db, org_id, owner, arg[0], arg[1])
    if kind == "sot":
        return await _missed(db, org_id, owner, arg[0], arg[1])
    if kind == "sua":
        return await _correct(db, org_id, owner, arg[0], arg[1])
    if kind == "mute":
        return await _mute(db, org_id, oi, owner, arg[0], arg[1])
    if kind == "digest":
        if not arg:
            await delivery.set_digest(db, org_id, off=False)
            return "Em đã bật lại bản tin 07:30 và 17:30."
        res = await strong_confirm.require(db, org_id, "digest_off")
        if not isinstance(res, strong_confirm.Allowed):
            return getattr(res, "reason", CONSOLE)
        await delivery.set_digest(db, org_id, off=True)
        return "Em đã tắt bản tin. Chuyện gấp trong ngày em vẫn báo; Sếp nhắn «bật bản tin» để bật lại."
    if kind == "quiet":
        if arg is None:
            return USAGE_QUIET
        res = await strong_confirm.require(db, org_id, "quiet_hours_change")
        if not isinstance(res, strong_confirm.Allowed):
            return getattr(res, "reason", CONSOLE)
        await delivery.set_quiet_hours(db, org_id, arg[0], arg[1])
        return f"Em đã đổi giờ yên lặng thành {arg[0]}–{arg[1]} (giờ VN). Lừa đảo/an toàn em vẫn báo ngay."
    if kind == "mode":
        res = await strong_confirm.require(db, org_id, "intel_mode_shadow")
        return getattr(res, "reason", CONSOLE)          # đổi chế độ chạy thử/chạy thật chiều nào cũng chỉ Console + PIN
    return "Em chưa có lệnh đó."



async def build_reply(sm: async_sessionmaker[AsyncSession], redis: Redis, router: Any, org_id: uuid.UUID, oi: Any,
                      p: dict[str, Any], app: Any = None) -> str:
    intel = parse_intel(str(p.get("text") or ""))
    if intel is not None:
        async with sm() as db:
            out = await intel_command(db, org_id, oi, intel)
            await db.commit()
        return out
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
    text_ = delivery.display_mask(reply.plain())     # tool Owner giữ nguyên STK/SĐT tới model (Q9) ⇒ che hiển thị ở cửa ra Zalo (§8.1)
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
        async with sm() as db:
            if await backup.is_locked(db, org_id):
                return "ignored:locked"          # khoá trợ lý: không gửi gì ra Zalo, kể cả tin đã xếp hàng trước khi khoá
            await db.rollback()
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

