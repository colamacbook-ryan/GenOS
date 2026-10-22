# ruff: noqa: E501
"""Hai việc của model trong NHÓM (đều chạy trong hộp riêng agy — placement `core.owner_reply`, `core.support`):

1. `handle_owner_call` — Sếp (nhận diện theo ID) tag bot ở nhóm đang nghe ⇒ trả lời TẠI CHỖ qua `send_auto(owner_tag)`.
   Nếu bộ lọc chặn (giá/tiền/hợp đồng/nhân sự/thông tin riêng) hoặc mức tự trị tổ chức ≤ 2 ⇒ trong nhóm chỉ nói
   «Dạ em gửi
   riêng Sếp ạ»; nội dung đầy đủ gửi riêng Sếp qua kênh quản trị, kèm bản nháp chờ Xác nhận + PIN (đường cũ).
2. `handle_support` — thành viên hỏi trong nhóm đã bật Chế độ hỗ trợ: phân loại → soạn → bộ lọc tất định →
`send_auto(support)`.
   Nhãn nhạy cảm / độ tin thấp / bị lọc ⇒ câu mẫu «xin chuyển anh/chị phụ trách» + báo Sếp (sự kiện đặc biệt).

Model không có công cụ; ngữ cảnh chỉ NHÓM HIỆN TẠI. Module này KHÔNG phát gì lên bridge — chỉ gọi `send_auto`.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.chassis.bus import EventBus
from gh.chassis.masking import mask_for_model
from gh.data import listen_events as lev
from gh.data.ingest import sync_listen_sets
from gh.gen import diplomat
from gh.owner_channel import inbound as oin
from gh.owner_channel import prompts, reports
from gh.owner_channel.autosend import send_auto, support_limits
from gh.owner_channel.basis import Basis, Trigger
from gh.owner_channel.draft_hand import group_level, hand_to_workbench
from gh.owner_channel.identity import is_owner, owner_ids
from gh.owner_cmd import text as otext
from gh.providers.router import ModelUnavailable

log = logging.getLogger("gh.owner_channel.group_reply")

CALL_PHRASE = "Dạ em gửi riêng Sếp ạ."
ESCALATE_PHRASE = "Dạ câu này em xin chuyển anh/chị phụ trách trả lời ạ."
ROLE_OWNER_REPLY = "core.owner_reply"
ROLE_SUPPORT = "core.support"
ABUSE_LIMIT = 3
ABUSE_WINDOW_S = 600
SENDER_BURST_PER_MIN = 10
OWNER_LEVEL_DM_ONLY = 2
NO_MODEL_DM = ("Em chưa có model AI để trả lời trong nhóm. Sếp mở Console › Kết nối để thêm khoá hoặc Antigravity CLI "
               "(vai chạy trong hộp riêng).")


async def _event(db: AsyncSession, org_id: uuid.UUID, raw_event_id: str) -> Any:
    try:
        eid = uuid.UUID(raw_event_id)
    except ValueError:
        return None
    return (await db.execute(text("""
        SELECT e.id, e.body_text, e.group_id, e.sender_identity_id, e.received_at, i.external_id AS sender_ext,
               i.person_id, p.display_name AS sender_name, g.name AS group_name, g.external_id AS group_ext,
               g.assigned_agent_id, c.type AS channel
        FROM raw.events e JOIN core.groups g ON g.id = e.group_id JOIN core.channels c ON c.id = g.channel_id
        LEFT JOIN core.person_identities i ON i.id = e.sender_identity_id
        LEFT JOIN core.persons p ON p.id = i.person_id
        WHERE e.id = :i AND e.org_id = :o"""), {"i": eid, "o": org_id})).one_or_none()


async def _history(db: AsyncSession, group_id: uuid.UUID, before: datetime, limit: int,
                   owner_ext: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT e.body_text, e.direction, i.external_id AS ext, p.display_name
        FROM raw.events e LEFT JOIN core.person_identities i ON i.id = e.sender_identity_id
        LEFT JOIN core.persons p ON p.id = i.person_id
        WHERE e.group_id = :g AND e.received_at < :b AND e.body_text IS NOT NULL AND e.kind = 'text'
        ORDER BY e.received_at DESC LIMIT :n"""), {"g": group_id, "b": before, "n": limit})).all()
    out = []
    for r in reversed(rows):
        who = "Bot" if r.direction == "outbound" else ("Sếp" if r.ext in owner_ext else (r.display_name or "Thành viên"))
        out.append({"who": who, "text": r.body_text})
    return out


async def _units(db: AsyncSession, group_id: uuid.UUID, limit: int = 8) -> str:
    """Dữ liệu sạch CỦA NHÓM NÀY (đơn vị ý nghĩa gần đây) — không nhóm khác, không điểm số."""
    try:
        rows = (await db.execute(text("""
            SELECT mu.summary FROM clean.meaning_units mu
            WHERE mu.group_id = :g AND mu.superseded_by IS NULL ORDER BY mu.observed_at DESC LIMIT :n"""),
                                 {"g": group_id, "n": limit})).scalars().all()
    except Exception:  # noqa: BLE001 — thiếu dữ liệu sạch không chặn trả lời
        return ""
    if not rows:
        return ""
    return prompts.render_history([{"who": "Tóm tắt", "text": s} for s in rows if s], "du_lieu_nhom", 2000)


async def _bot_name(db: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID | None) -> tuple[str, str]:
    """(tên bot, tên tổ chức). v0.1.73: có agent ngoại giao do Sếp chọn thì LUÔN dùng tên đó; chưa chọn ⇒ như cũ (agent được
    giao nhóm, hoặc «Trợ lý»). Giọng nói lấy riêng ở `_persona_block`."""
    dip = await diplomat.load(db, org_id)
    org = (await db.execute(text("SELECT name FROM core.organizations WHERE id = :o"), {"o": org_id})).scalar_one()
    name = None
    if agent_id is not None:
        name = (await db.execute(text("SELECT name FROM agent.identities WHERE id = :a AND org_id = :o"),
                                 {"a": agent_id, "o": org_id})).scalar_one_or_none()
    return (dip.name if dip.configured else str(name or "Trợ lý")), str(org)


async def _persona_block(db: AsyncSession, org_id: uuid.UUID) -> str:
    return (await diplomat.load(db, org_id)).block()


def _clean(answer: str) -> str:
    return answer.strip().strip("`").strip()


async def _dm_owner(sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus, org_id: uuid.UUID,
                    channel: str, key: str, body: str) -> None:
    """Gửi riêng Sếp qua hàng đợi báo cáo (mức `urgent` ⇒ không chờ giờ yên lặng) rồi đẩy ngay, không chờ cron."""
    async with sm() as db:
        await reports.enqueue(db, org_id, channel, kind="reply", severity="urgent", text_=body, dedupe_key=key)
        await db.commit()
    await reports.flush(sm, bus, redis)


async def handle_owner_call(sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus, router: Any,
                            org_id: uuid.UUID, p: dict[str, Any]) -> str:
    """`call`: Sếp tag bot trong nhóm. Trả nhãn kết quả. Không ném."""
    ch, eid = str(p.get("channel") or ""), str(p.get("raw_event_id") or "")
    try:
        async with sm() as db:
            oi = await owner_ids(db, org_id, ch)
            ev = await _event(db, org_id, eid)
            if not oi.enabled or ev is None or not is_owner(oi, ev.sender_ext):
                return "ignored"
            gid = ev.group_id
            level = await group_level(db, org_id, gid)
            bot, _org_name = await _bot_name(db, org_id, ev.assigned_agent_id)
            persona = await _persona_block(db, org_id)
            hist = await _history(db, gid, ev.received_at, prompts.MAX_OWNER_CONTEXT_MESSAGES, oi.ids)
            units = await _units(db, gid)
            await db.rollback()
        trigger = Trigger("raw_event", eid)
        await _record_called(sm, redis, org_id, ch, gid)
        if router is None:
            await _dm_owner(sm, redis, bus, org_id, ch, f"owner_call:{eid}", NO_MODEL_DM)
            return "no_model"
        canary = prompts.new_canary()
        msgs = prompts.owner_reply_messages(
            bot_name=bot, addr="Sếp", group_name=str(ev.group_name or ""), canary=canary,
            history=prompts.render_history(hist, "tin_nhom"), units=units, question=str(ev.body_text or ""), persona=persona)
        try:
            routed = await router.generate(org_id, agent_key=ROLE_OWNER_REPLY, purpose="owner_reply", messages=msgs,
                                           json_mode=False)
        except ModelUnavailable:
            await _dm_owner(sm, redis, bus, org_id, ch, f"owner_call:{eid}",
                            "Em đang không gọi được model AI để trả lời trong nhóm, Sếp thử lại sau ít phút.")
            return "model_down"
        answer = _clean(routed.text)
        if level <= OWNER_LEVEL_DM_ONLY:
            await _dm_owner(sm, redis, bus, org_id, ch, f"owner_call:{eid}",
                            f"Nhóm «{str(ev.group_name)[:60]}» (mức tự trị của tổ chức chỉ cho em ghi nhận nên "
                            f"em không đăng trong nhóm):\n{answer}")
            return "dm_only"
        async with sm() as db:
            res = await send_auto(db, bus, redis, org_id=org_id, channel=ch, basis=Basis.OWNER_TAG, trigger=trigger,
                                  text=answer, group_id=gid, canary=canary)
        if res.ok:
            return "replied"
        if res.code != "GUARD_BLOCKED":
            return f"refused:{res.code}"
        # bị lọc: trong nhóm chỉ «gửi riêng», nội dung qua Sếp + nháp
        async with sm() as db:
            await send_auto(db, bus, redis, org_id=org_id, channel=ch, basis=Basis.OWNER_TAG, trigger=trigger,
                            text=CALL_PHRASE, group_id=gid)
        async with sm() as db:
            code = await hand_to_workbench(db, redis, org_id=org_id, channel=ch, group_id=gid, text=answer,
                                           created_by=oi.owner_user_id)
            await db.commit()
        tail = (f"\nEm đã soạn nháp {code} ở Bàn làm việc — Sếp mở Console, Xác nhận + mã PIN nếu muốn đăng vào nhóm."
                if code else "")
        await _dm_owner(sm, redis, bus, org_id, ch, f"owner_call:{eid}",
                        f"Nhóm «{str(ev.group_name)[:60]}»: em không đăng câu này trong nhóm (có giá/hợp đồng/nhân sự/"
                        f"thông tin riêng). Nội dung em định trả lời:\n{answer}{tail}\n{otext.public_url()}/workbench")
        return "handed_over"
    except Exception:  # noqa: BLE001
        log.exception("Sếp gọi bot ở nhóm: xử lý lỗi")
        return "error"


async def _record_called(sm: async_sessionmaker[AsyncSession], redis: Redis, org_id: uuid.UUID, ch: str,
                         gid: uuid.UUID) -> None:
    async with sm() as db:
        cid = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                                {"o": org_id, "t": ch})).scalar_one_or_none()
        await lev.record(db, org_id, "owner_called", channel_id=cid, channel_type=ch, group_id=gid,
                         detail={"not_listening": False})
        await db.commit()
        pending = lev.take_pending(db)
    await lev.publish_pending(sm, redis, org_id, pending)


def _parse_label(raw: str) -> tuple[str, float]:
    try:
        d = orjson.loads(raw)
        label, conf = str(d.get("label")), float(d.get("confidence"))
    except (orjson.JSONDecodeError, TypeError, ValueError, AttributeError):
        return "out_of_scope", 0.0
    return (label if label in prompts.CLASSIFY_LABELS else "out_of_scope"), max(0.0, min(1.0, conf))


async def _escalate(sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus, org_id: uuid.UUID, ch: str,
                    ev: Any, trigger: Trigger, why: str) -> str:
    """Câu mẫu trong nhóm (tối đa 1 lần / thành viên / 30 phút) + báo Sếp + Nhật ký lắng nghe."""
    member = str(ev.sender_ext or ev.person_id)
    if await redis.set(f"gh:support:esc:{ev.group_id}:{member}", "1", nx=True, ex=1800):
        async with sm() as db:
            await send_auto(db, bus, redis, org_id=org_id, channel=ch, basis=Basis.SUPPORT, trigger=trigger,
                            text=ESCALATE_PHRASE, group_id=ev.group_id)
    summary = otext.defang(otext.line(str(mask_for_model(str(ev.body_text or ""))), 200))
    async with sm() as db:
        cid = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                                {"o": org_id, "t": ch})).scalar_one_or_none()
        await lev.record(db, org_id, "support_escalated", channel_id=cid, channel_type=ch, group_id=ev.group_id,
                         detail={"why": why})
        await reports.enqueue(
            db, org_id, ch, kind="alert", severity="important", dedupe_key=f"support_esc:{ev.id}",
            text_=(f"Nhóm «{str(ev.group_name)[:60]}»: {str(ev.sender_name or 'một thành viên')[:40]} hỏi câu em không tự "
                   f"trả lời (lý do: {why}): «{summary}»"),
            links=[("Soạn trả lời", f"{otext.public_url()}/workbench")])
        await db.commit()
        pending = lev.take_pending(db)
    await lev.publish_pending(sm, redis, org_id, pending)
    return f"escalated:{why}"


async def _abuse(sm: async_sessionmaker[AsyncSession], redis: Redis, org_id: uuid.UUID, ch: str, ev: Any) -> None:
    """≥ 3 lần bị chặn rò lời nhắc / lạm dụng trong 10 phút ở một nhóm ⇒ tự tắt hỗ trợ của nhóm + báo Sếp."""
    key = f"gh:support:abuse:{ev.group_id}"
    n = int(await redis.incr(key))
    if n == 1:
        await redis.expire(key, ABUSE_WINDOW_S)
    if n < ABUSE_LIMIT:
        return
    async with sm() as db:
        gone = await oin.disable_support(db, org_id, ch, reason="abuse_guard", group_id=ev.group_id)
        if gone:
            await reports.enqueue(db, org_id, ch, kind="alert", severity="important",
                                  dedupe_key=f"support_abuse:{ev.group_id}:{datetime.now(UTC):%Y%m%d%H}",
                                  text_=f"Em đã tự tắt chế độ hỗ trợ nhóm «{str(ev.group_name)[:60]}»: có người liên tục cố "
                                        "làm em lộ lời nhắc / làm trái luật.")
            await sync_listen_sets(db, redis, org_id)
        await db.commit()
        pending = lev.take_pending(db)
    await lev.publish_pending(sm, redis, org_id, pending)


async def handle_support(sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus, router: Any,
                         org_id: uuid.UUID, p: dict[str, Any]) -> str:
    """`support_ask`: thành viên tag/trả lời bot trong nhóm đã bật hỗ trợ. Trả nhãn kết quả. Không ném."""
    ch, eid = str(p.get("channel") or ""), str(p.get("raw_event_id") or "")
    try:
        if not await redis.set(f"gh:support:claim:{eid}", "1", nx=True, ex=3600):
            return "claimed"
        async with sm() as db:
            ev = await _event(db, org_id, eid)
            if ev is None:
                return "ignored"
            now = datetime.now(UTC)
            sup = (await db.execute(text("""SELECT id, enabled_at, limits FROM ops.group_support
                                            WHERE group_id = :g AND disabled_at IS NULL
                                              AND (until_at IS NULL OR until_at > :n)"""),
                                    {"g": ev.group_id, "n": now})).one_or_none()
            if sup is None:
                return "ignored"
            lim = await support_limits(db, org_id, sup.limits if isinstance(sup.limits, dict) else None)
            gap_ok = (await db.execute(text("""
                SELECT count(*) FROM ops.auto_sends WHERE org_id = :o AND basis = 'support' AND group_id = :g
                  AND created_at > :h"""), {"o": org_id, "g": ev.group_id, "h": now - timedelta(hours=1)})).scalar_one()
            if int(gap_ok) >= lim["per_group_hour"]:
                return "quota"
            first = (await db.execute(text("""
                SELECT 1 FROM ops.auto_sends a JOIN raw.events r ON r.id::text = a.trigger_ref AND r.org_id = a.org_id
                WHERE a.org_id = :o AND a.basis = 'support' AND a.group_id = :g AND a.created_at >= :since
                  AND r.sender_identity_id = :s LIMIT 1"""),
                                     {"o": org_id, "g": ev.group_id, "since": sup.enabled_at,
                                      "s": ev.sender_identity_id})).first() is None
            oi = await owner_ids(db, org_id, ch)
            bot, org_name = await _bot_name(db, org_id, ev.assigned_agent_id)
            persona = await _persona_block(db, org_id)
            hist = await _history(db, ev.group_id, ev.received_at, prompts.MAX_CONTEXT_MESSAGES, oi.ids)
            await db.rollback()
        # Nghi bot: một người gửi > 10 tin/phút ⇒ không trả lời (không tốn model).
        sender = str(ev.sender_ext or ev.person_id)
        burst_key = f"gh:support:burst:{ev.group_id}:{sender}:{int(now.timestamp() // 60)}"
        if int(await redis.incr(burst_key)) == 1:
            await redis.expire(burst_key, 120)
        if int(await redis.get(burst_key) or 0) > SENDER_BURST_PER_MIN:
            return "burst"
        trigger = Trigger("raw_event", eid)
        if router is None:
            await _hold_notice(sm, redis, bus, org_id, ch)
            return "no_model"
        history = prompts.render_history(hist, "tin_nhom")
        try:
            cls = await router.generate(org_id, agent_key=ROLE_SUPPORT, purpose="support_classify",
                                        messages=prompts.classify_messages(history, str(ev.body_text or "")),
                                        json_mode=True, temperature=0.0)
        except ModelUnavailable:
            await _hold_notice(sm, redis, bus, org_id, ch)
            return "model_down"
        label, conf = _parse_label(cls.text)
        if label == "abuse":
            await _abuse(sm, redis, org_id, ch, ev)
        if label not in prompts.ANSWERABLE or conf < prompts.MIN_CONFIDENCE:
            return await _escalate(sm, redis, bus, org_id, ch, ev, trigger, label if conf >= prompts.MIN_CONFIDENCE else "low_confidence")
        canary = prompts.new_canary()
        try:
            routed = await router.generate(org_id, agent_key=ROLE_SUPPORT, purpose="support_reply", json_mode=False,
                                           messages=prompts.support_messages(
                                               bot_name=bot, org_name=org_name, history=history, canary=canary,
                                               question=str(ev.body_text or ""), max_len=lim["max_len"], intro=first, persona=persona))
        except ModelUnavailable:
            await _hold_notice(sm, redis, bus, org_id, ch)
            return "model_down"
        body = _clean(routed.text)
        async with sm() as db:
            res = await send_auto(db, bus, redis, org_id=org_id, channel=ch, basis=Basis.SUPPORT, trigger=trigger,
                                  text=body, group_id=ev.group_id, canary=canary)
        if res.ok:
            return "replied"
        if res.code == "GUARD_BLOCKED":
            if "G-LEAK" in res.rules:
                await _abuse(sm, redis, org_id, ch, ev)
            return await _escalate(sm, redis, bus, org_id, ch, ev, trigger, "guard")
        return f"refused:{res.code}"
    except Exception:  # noqa: BLE001
        log.exception("chế độ hỗ trợ: xử lý lỗi")
        return "error"


async def _hold_notice(sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus, org_id: uuid.UUID,
                       ch: str) -> None:
    """Hộp riêng / model không chạy: hỗ trợ im lặng + báo Sếp một lần mỗi giờ."""
    async with sm() as db:
        await reports.enqueue(db, org_id, ch, kind="alert", severity="important",
                              dedupe_key=f"support_paused:{datetime.now(UTC):%Y%m%d%H}",
                              text_="Chế độ hỗ trợ tạm dừng: em không gọi được model AI (hộp riêng Antigravity chưa chạy "
                                    "hoặc chưa có nguồn AI). Em im lặng ở các nhóm hỗ trợ cho tới khi có lại.")
        await db.commit()


def register(handlers: dict[str, Any]) -> None:
    """Đăng ký consumer `call` và `support_ask` vào bảng xử lý của `jobs`."""
    handlers["call"] = handle_owner_call
    handlers["support_ask"] = handle_support
