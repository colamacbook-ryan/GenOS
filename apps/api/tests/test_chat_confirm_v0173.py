# ruff: noqa: E501, F811
"""v0.1.73 — Xác nhận TRONG CHAT ở kênh quản trị 1-1 (Zalo / WhatsApp): Gen hỏi «Trả lời OK để em làm (mã #X)», Sếp «OK» ⇒ làm
đúng đường Xác nhận của Console (phiên ngắn hạn `chat_ok`, thu hồi ngay), ghi Action Log (via, confirmation=chat).
An toàn: người lạ, tin trong nhóm, hết hạn, nhiều đề xuất không mã, xác nhận một lần, tin gửi ngoài cần OK + mã riêng."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.chassis.bus import BRIDGE_OUTBOUND
from gh.db import admin_sessionmaker, sessionmaker
from gh.gen import chat_pending, proposals
from gh.owner_channel import chat_confirm, dm, jobs
from gh.owner_channel import inbound as oin
from gh.owner_channel.identity import owner_ids
from tests.conftest import Api
from tests.owner_helpers import OWNER_WA, OWNER_ZALO, FakeBus, ago, bot_world
from tests.test_p3_core import _claims, _outbound, world  # noqa: F401 — fixture `world` dùng chung
from tests.test_telegram_commands_v0164 import Router, _reminder

SM = sessionmaker
MEMORY = {"steps": [{"kind": "propose", "proposal": {"type": "memory_note", "fields": {
    "text": "Sếp thích báo cáo ngắn", "reason": "Sếp dặn trong chat"}}}]}


def reminder(title: str) -> dict[str, Any]:
    r = _reminder()
    r["steps"][1]["proposal"]["fields"]["title"] = title
    return r


class Chat:
    """Một cuộc chat 1-1 với bot qua `dm.handle_dm` (cùng đường consumer thật), đếm tin ra bus giả."""

    def __init__(self, app: Any, redis: Any, org: uuid.UUID, sid: uuid.UUID, ch: str = "zalo") -> None:
        self.app, self.redis, self.org, self.sid, self.ch = app, redis, org, sid, ch
        self.bus = FakeBus()

    async def say(self, body: str, router: Any = None, **kw: Any) -> str:
        mid = uuid.uuid4().hex
        await self.redis.set(f"gh:owner:dm:{self.ch}:{mid}", str(int(time.time())), ex=600)
        before = len(self.bus.on(BRIDGE_OUTBOUND))
        p = {"channel": self.ch, "session_id": str(self.sid), "external_msg_id": mid, "text": body, "occurred_at": ago(seconds=1), **kw}
        res = await dm.handle_dm(SM(), self.redis, self.bus, router, self.org, p, self.app)
        assert res == "replied", res
        out = self.bus.on(BRIDGE_OUTBOUND)
        assert len(out) == before + 1
        assert out[-1][1]["thread_type"] == "user"
        return str(out[-1][1]["text"])


@pytest.fixture
async def chat(world, app, db, redis) -> Chat:  # type: ignore[no-untyped-def]
    org = world["org"]
    sid = await bot_world(db, org, "zalo")
    return Chat(app, redis, org, sid)


async def count(table: str, where: str = "true") -> int:
    async with admin_sessionmaker()() as s:
        return int((await s.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"))).scalar_one())  # noqa: S608


async def alog(action: str) -> list[Any]:
    async with admin_sessionmaker()() as s:
        return list((await s.execute(text("SELECT actor_type, actor_id, result, detail FROM ops.action_log WHERE action = :a ORDER BY at"), {"a": action})).all())


async def live_sessions() -> list[Any]:
    async with admin_sessionmaker()() as s:
        return list((await s.execute(text("""SELECT revoked_at, pin_verified_until FROM core.sessions WHERE user_agent LIKE 'chat_ok:%'"""))).all())


def code_in(reply: str) -> str:
    import re

    m = re.search(r"#[0-9A-F]{6}", reply)
    assert m, reply
    return m.group(0)


# ─── phân tích lời trả lời ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("ok", ("ok", None)), ("OK", ("ok", None)), ("Ok.", ("ok", None)), ("oke", ("ok", None)), ("OK em", ("ok", None)),
    ("OK #a3f9c2", ("ok", "A3F9C2")), ("ok #A3F9C2!", ("ok", "A3F9C2")), ("ok a3f9", ("ok", "A3F9")),
    ("đồng ý", ("ok", None)), ("DONG Y", ("ok", None)), ("Đồng ý #A3F9C2", ("ok", "A3F9C2")), ("làm đi", ("ok", None)),
    ("lam di", ("ok", None)), ("làm đi nhé", ("ok", None)),
    ("huỷ", ("no", None)), ("hủy", ("no", None)), ("HUY #A3F9C2", ("no", "A3F9C2")), ("thôi", ("no", None)), ("huỷ bỏ", ("no", None)),
    ("ok tất cả", ("all", None)), ("OK hết", ("all", None)),
    ("ok để em xem lại tình hình hôm nay", None), ("tối nay ok không", None), ("/nhap ok", None), ("", None),
    ("okela", None), ("cho em hỏi", None),
])
def test_parse_decision(raw: str, expect: tuple[str, str | None] | None) -> None:
    got = chat_confirm.parse_decision(raw)
    assert (None if got is None else (got.verb, got.code)) == expect


# ─── luồng chính ──────────────────────────────────────────────────────────────

async def test_nhap_asks_in_chat_without_console_or_pin_and_does_nothing_yet(chat: Chat) -> None:
    before = await count("biz.tasks")
    reply = await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    code = code_in(reply)
    assert "Trả lời OK để em làm (mã " + code + "), hoặc HUỶ." in reply and "Gọi anh Nam" in reply
    assert "Mở Console" not in reply and "mã PIN" not in reply and "CHƯA làm gì" not in reply and "http" not in reply
    assert await count("biz.tasks") == before
    assert await chat.redis.zcard(chat_pending.key(chat.org, "zalo")) == 1


async def test_ok_with_single_pending_runs_the_console_confirm_path_and_logs(chat: Chat) -> None:
    before = await count("biz.tasks")
    code = code_in(await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam"))))
    reply = await chat.say("OK")
    assert reply.startswith(f"Đã làm xong {code}") and "TSK" in reply
    assert await count("biz.tasks") == before + 1
    # đúng đường Console: gen.proposal_confirmed (người bấm = Owner, via=gen) + dấu vết chat riêng
    [c] = await alog("gen.proposal_confirmed")
    [x] = await alog("gen.proposal_chat_confirmed")
    assert c.actor_type == "user" and x.actor_type == "user" and x.actor_id == c.actor_id and x.result == "ok"
    assert x.detail["via"] == "zalo" and x.detail["confirmation"] == "chat" and x.detail["type"] == "reminder"
    assert x.detail["proposal_id"] == c.detail["proposal_id"] and "Gọi anh Nam" not in orjson.dumps(x.detail).decode()
    # phiên chat_ok đã thu hồi, không còn cửa sổ PIN
    ses = await live_sessions()
    assert ses and all(r.revoked_at is not None and r.pin_verified_until is None for r in ses)
    assert await chat.redis.zcard(chat_pending.key(chat.org, "zalo")) == 0


async def test_ok_is_one_shot_second_ok_does_nothing(chat: Chat) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    await chat.say("OK")
    n = await count("biz.tasks")
    assert "không có đề xuất nào đang chờ" in await chat.say("OK")
    assert await count("biz.tasks") == n and len(await alog("gen.proposal_confirmed")) == 1


async def test_claim_blocks_a_concurrent_second_confirm(chat: Chat) -> None:
    reply = await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    pid = (await chat.redis.zrange(chat_pending.key(chat.org, "zalo"), 0, -1))[0].decode()
    assert await chat.redis.set(f"gh:owner:chatok:{pid}", "1", nx=True, ex=60)       # lượt trước đang làm
    before = await count("biz.tasks")
    assert "đã được xử lý rồi" in await chat.say("OK " + code_in(reply))
    assert await count("biz.tasks") == before


@pytest.mark.parametrize("word", ["đồng ý", "Làm đi", "ok", "OKE"])
async def test_accepted_ok_words_confirm(chat: Chat, word: str) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    assert (await chat.say(word)).startswith("Đã làm xong")


async def test_memory_note_in_chat_needs_no_pin(chat: Chat) -> None:
    await chat.say("/nhap nhớ giúp tôi thích báo cáo ngắn", Router(MEMORY))
    reply = await chat.say("ok")
    assert "em đã ghi nhớ" in reply
    assert await count("agent.gen_memory_notes") == 1


async def test_cancel_drops_the_proposal_and_later_ok_has_nothing(chat: Chat) -> None:
    code = code_in(await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam"))))
    before = await count("biz.tasks")
    assert (await chat.say("huỷ")).startswith(f"Đã huỷ đề xuất {code}")
    assert "không có đề xuất nào đang chờ" in await chat.say("OK")
    assert await count("biz.tasks") == before
    [c] = await alog("gen.proposal_chat_cancelled")
    assert c.detail["confirmation"] == "chat" and c.detail["via"] == "zalo"
    assert all(r.revoked_at is not None for r in await live_sessions())


async def test_two_pending_need_a_code_and_only_that_one_runs(chat: Chat) -> None:
    c1 = code_in(await chat.say("/nhap một", Router(reminder("Việc một"))))
    c2 = code_in(await chat.say("/nhap hai", Router(reminder("Việc hai"))))
    assert c1 != c2
    before = await count("biz.tasks")
    ask = await chat.say("OK")
    assert c1 in ask and c2 in ask and "kèm mã" in ask and await count("biz.tasks") == before
    assert "Đã làm xong " + c2 in await chat.say(f"ok {c2}")
    assert await count("biz.tasks") == before + 1
    assert "không thấy đề xuất #FFFFFF" in await chat.say("ok #ffffff")
    assert (await chat.say("huỷ")).startswith(f"Đã huỷ đề xuất {c1}")        # còn đúng một ⇒ huỷ không cần mã
    assert await count("biz.tasks") == before + 1


async def test_ok_all_is_refused(chat: Chat) -> None:
    await chat.say("/nhap một", Router(reminder("Việc một")))
    before = await count("biz.tasks")
    assert "không làm hàng loạt" in await chat.say("ok tất cả")
    assert await count("biz.tasks") == before


async def test_ok_after_expiry_reports_expired_and_does_nothing(chat: Chat) -> None:
    code = code_in(await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam"))))
    pid = (await chat.redis.zrange(chat_pending.key(chat.org, "zalo"), 0, -1))[0].decode()
    await chat.redis.delete(proposals.key(pid))                       # Redis đã xoá đề xuất sau 24 giờ
    before = await count("biz.tasks")
    assert f"Đề xuất {code} đã hết hạn" in await chat.say("OK")
    assert await count("biz.tasks") == before and await alog("gen.proposal_chat_confirmed") == []
    assert "không có đề xuất nào đang chờ" in await chat.say("OK")    # báo hết hạn một lần rồi sạch sổ


async def test_ok_with_code_of_expired_proposal(chat: Chat) -> None:
    code = code_in(await chat.say("/nhap một", Router(reminder("Việc một"))))
    pid = (await chat.redis.zrange(chat_pending.key(chat.org, "zalo"), 0, -1))[0].decode()
    await chat.redis.delete(proposals.key(pid))
    await chat.say("/nhap hai", Router(reminder("Việc hai")))
    assert f"Đề xuất {code} đã hết hạn" in await chat.say(f"OK {code}")


async def test_proposal_made_elsewhere_cannot_be_confirmed_from_chat(chat: Chat, owner_api: Api) -> None:
    """Đề xuất soạn ở Console / Telegram (không hỏi trong chat) không nằm trong sổ ⇒ «OK» không động tới."""
    reply = await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")), )
    pid = (await chat.redis.zrange(chat_pending.key(chat.org, "zalo"), 0, -1))[0].decode()
    await chat.redis.zrem(chat_pending.key(chat.org, "zalo"), pid)       # như thể soạn ở nơi khác
    assert pid and "mã" in reply
    before = await count("biz.tasks")
    assert "không có đề xuất nào đang chờ" in await chat.say("OK")
    assert await count("biz.tasks") == before
    assert (await proposals.load(chat.redis, pid))["status"] == "pending"


async def test_telegram_nhap_keeps_console_flow_and_is_not_registered(chat: Chat, owner_api: Api, app: Any) -> None:
    from gh.owner_cmd import core
    from gh.owner_cmd.core import Context, OwnerCommand

    async with admin_sessionmaker()() as s:
        owner = (await s.execute(text("SELECT id FROM core.users LIMIT 1"))).scalar_one()
    ctx = Context(sm=SM(), redis=chat.redis, router=Router(reminder("Gọi anh Nam")), org_id=chat.org, owner_user_id=owner)
    reply = await core.handle(OwnerCommand(via="telegram", kind="nhap", text="x", message_ref="1", received_at=datetime.now(UTC)), ctx)
    assert "CHƯA làm gì" in reply.text and reply.links and reply.links[0][0] == core.CONSOLE_BUTTON
    assert await chat.redis.zcard(chat_pending.key(chat.org, "telegram")) == 0
    assert core.help_text("telegram") == core.HELP and "Console" in core.HELP and core.help_text("whatsapp") == core.HELP_CHAT


async def test_help_in_chat_explains_ok_not_console(chat: Chat) -> None:
    reply = await chat.say("/tro_giup")
    assert "OK" in reply and "Không cần mở Console" in reply and "mở Console bấm Xác nhận" not in reply


# ─── tin gửi người ngoài ──────────────────────────────────────────────────────

async def outbound_proposal(chat: Chat, world: dict[str, Any], text_: str = "Dạ chào chị Lan, bên em báo giá 12 triệu ạ.",
                            title: str = "Báo giá chị Lan") -> dict[str, Any]:
    """Đề xuất `draft_message` gửi vào nhóm g1 (id nhóm đã 'thấy' — /nhap hiện chưa tra được id nhóm, xem Nợ)."""
    from gh.auth import service  # noqa: F401
    from gh.gen import envelope
    from gh.owner_cmd import core

    async with SM()() as db:
        await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(chat.org)})
        oi = await owner_ids(db, chat.org, "zalo")
        user = await core.owner_user(db, chat.org, oi.owner_user_id)
        assert user is not None
        tz = await proposals.org_tz(db, chat.org)
        prop = envelope.Propose.model_validate({"kind": "propose", "proposal": {"type": "draft_message", "fields": {
            "title": title, "text": text_, "subject": {"type": "group", "id": str(world["group"])}}}}).proposal
        p, err = await proposals.build(db, user, prop, {str(world["group"])}, tz, turn_id=uuid.uuid4(), conversation_id=uuid.uuid4(), redis=chat.redis)
        assert p is not None, err
        await db.rollback()
    await proposals.save(chat.redis, p)
    await chat_pending.register(chat.redis, chat.org, "zalo", p["id"])
    return p


async def test_outbound_question_names_recipient_and_verbatim_text(chat: Chat, world: dict[str, Any]) -> None:
    p = await outbound_proposal(chat, world)
    q = chat_pending.question(p)
    assert "GỬI TIN cho nhóm «Nhóm g1»" in q and "│ Dạ chào chị Lan, bên em báo giá 12 triệu ạ." in q
    assert f"Trả lời «OK {chat_pending.code_of(p['id'])}»" in q and chat_pending.kind_of(p) == "outbound"


async def test_outbound_bare_ok_is_refused_needs_code_and_nothing_is_sent(chat: Chat, world: dict[str, Any]) -> None:
    p = await outbound_proposal(chat, world)
    reply = await chat.say("OK")
    assert reply == chat_confirm.NEED_CODE_OUTBOUND.format(question=chat_pending.question(p, "zalo", with_code=True))
    assert "Nhóm g1" in reply and "Dạ chào chị Lan, bên em báo giá 12 triệu ạ." in reply and "OK " + chat_pending.code_of(p["id"]) in reply
    assert await count("biz.action_drafts") == 0 and await _outbound(chat.redis) == []
    assert (await proposals.load(chat.redis, p["id"]))["status"] == "pending"


async def test_outbound_ok_with_code_goes_through_drafts_path_with_chat_basis(chat: Chat, world: dict[str, Any]) -> None:
    p = await outbound_proposal(chat, world)
    code = chat_pending.code_of(p["id"])
    reply = await chat.say(f"OK {code}")
    assert reply.startswith(f"Đã gửi {code} cho nhóm «Nhóm g1»"), reply
    [d] = await _outbound(chat.redis)
    assert d["thread_type"] == "group" and d["text"] == "Dạ chào chị Lan, bên em báo giá 12 triệu ạ." and "basis" not in _claims(d["permit"])
    assert _claims(d["permit"])["draft_id"]                                       # đi qua bản nháp (drafts.py), không phải send_auto
    [x] = await alog("gen.proposal_chat_confirmed")
    assert x.detail["kind"] == "outbound" and x.detail["outbound"] in ("approved", "sent") and "báo giá" not in orjson.dumps(x.detail).decode()
    assert await count("biz.action_drafts", "status IN ('approved', 'sent')") == 1
    assert all(r.revoked_at is not None and r.pin_verified_until is None for r in await live_sessions())


async def test_two_outbound_need_separate_codes_no_batch(chat: Chat, world: dict[str, Any]) -> None:
    a = await outbound_proposal(chat, world, "Tin A", "A")
    b = await outbound_proposal(chat, world, "Tin B", "B")
    assert "kèm mã" in await chat.say("OK") and "không làm hàng loạt" in await chat.say("ok tất cả")
    assert await _outbound(chat.redis) == []
    await chat.say(f"OK {chat_pending.code_of(b['id'])}")
    sent = await _outbound(chat.redis)
    assert [s["text"] for s in sent] == ["Tin B"]
    assert (await proposals.load(chat.redis, a["id"]))["status"] == "pending"


async def test_overlong_outbound_and_social_stay_console_only(chat: Chat, world: dict[str, Any]) -> None:
    long_p = await outbound_proposal(chat, world, "x" * (chat_pending.OUTBOUND_TEXT_MAX + 1), "Dài")
    assert chat_pending.kind_of(long_p) == "console"
    assert chat_pending.kind_of({"type": "social_reply", "fields": {}}) == "console"
    assert "chưa làm được từ chat" in chat_pending.question(long_p)
    assert chat_confirm.NOT_HERE in await chat.say(f"OK {chat_pending.code_of(long_p['id'])}")
    assert await count("biz.action_drafts") == 0


async def test_outbound_cancel_sends_nothing(chat: Chat, world: dict[str, Any]) -> None:
    p = await outbound_proposal(chat, world)
    assert "Đã huỷ" in await chat.say(f"huỷ {chat_pending.code_of(p['id'])}")
    assert await _outbound(chat.redis) == [] and await count("biz.action_drafts") == 0


# ─── an toàn: ai được xác nhận ────────────────────────────────────────────────

async def test_stranger_and_group_ok_never_reach_the_consumer(chat: Chat, db: Any, redis: Any) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    base = {"channel": "zalo", "session_id": str(chat.sid), "occurred_at": ago(seconds=1), "kind": "text", "body_text": "OK",
            "forwarded": False}
    async with SM()() as s:
        # người lạ nhắn 1-1
        assert not await oin.handle_owner_dm(s, redis, chat.org, {**base, "external_msg_id": "s1", "sender_external_id": "999", "sender_alt_id": None})
        # đúng ID Sếp nhưng là tin trong nhóm
        for extra in ({"external_group_id": "g1"}, {"group_id": "g1"}, {"thread_type": "group"}):
            assert not await oin.handle_owner_dm(s, redis, chat.org, {**base, "external_msg_id": f"g-{len(extra)}{list(extra)[0]}", "sender_external_id": OWNER_ZALO, "sender_alt_id": None, **extra})
        # đúng Sếp nhưng là tin chuyển tiếp
        assert not await oin.handle_owner_dm(s, redis, chat.org, {**base, "external_msg_id": "f1", "sender_external_id": OWNER_ZALO, "sender_alt_id": None, "forwarded": True})
        assert oin.take_pending(s) == []
        # đúng Sếp, 1-1 ⇒ vào
        assert await oin.handle_owner_dm(s, redis, chat.org, {**base, "external_msg_id": "ok1", "sender_external_id": OWNER_ZALO, "sender_alt_id": None})
        assert [t for t, _ in oin.take_pending(s)] == ["dm"]
    assert await count("biz.tasks") == 0


async def test_ok_after_channel_unpaired_or_disabled_does_nothing(chat: Chat, db: Any) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    await db.execute(text("UPDATE ops.owner_channels SET enabled = false"))
    await db.commit()
    mid = uuid.uuid4().hex
    await chat.redis.set(f"gh:owner:dm:zalo:{mid}", str(int(time.time())), ex=60)
    res = await dm.handle_dm(SM(), chat.redis, chat.bus, None, chat.org, {"channel": "zalo", "session_id": str(chat.sid), "external_msg_id": mid, "text": "OK"}, chat.app)
    assert res == "ignored:not_enabled" and await count("biz.tasks") == 0


async def test_wrong_channel_isolation_whatsapp_cannot_confirm_zalo_proposal(chat: Chat, db: Any, app: Any, redis: Any) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    wa_sid = await bot_world(db, chat.org, "whatsapp")
    wa = Chat(app, redis, chat.org, wa_sid, "whatsapp")
    assert "không có đề xuất nào đang chờ" in await wa.say("OK")
    assert await count("biz.tasks") == 0
    assert OWNER_WA


async def test_end_to_end_through_the_inbound_consumer_dispatch(chat: Chat) -> None:
    """jobs.dispatch truyền `app` tới handler `dm` (đường consumer thật)."""
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    mid = uuid.uuid4().hex
    await chat.redis.set(f"gh:owner:dm:zalo:{mid}", str(int(time.time())), ex=60)

    class Ev:
        type = "dm"
        stream = "gh.owner.inbound"
        message_id = None
        payload = {"channel": "zalo", "session_id": str(chat.sid), "external_msg_id": mid, "text": "OK",
                   "occurred_at": datetime.now(UTC).isoformat()}

    assert await jobs.dispatch(SM(), chat.redis, chat.bus, None, chat.org, Ev(), chat.app) == "replied"
    assert await count("biz.tasks") == 1
    assert chat.bus.on(BRIDGE_OUTBOUND)[-1][1]["text"].startswith("Đã làm xong")
    assert timedelta(0) < timedelta(seconds=1)


async def test_without_app_ok_answers_politely_and_changes_nothing(chat: Chat) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    chat.app = None
    assert chat_confirm.NOT_HERE in await chat.say("OK")
    assert await count("biz.tasks") == 0


async def test_confirm_failure_is_friendly_and_retryable(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    code = code_in(await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam"))))

    async def boom(app: Any, cookies: Any, csrf: Any, call: Any, ip: Any = None) -> tuple[int, Any]:
        return 403, {"code": "FORBIDDEN", "title": "Không đủ quyền"}

    monkeypatch.setattr(proposals, "call_as_user", boom)
    reply = await chat.say("OK")
    assert reply.startswith(f"Em chưa làm được đề xuất {code}") and "không đủ quyền" in reply
    monkeypatch.undo()
    assert (await chat.say("OK")).startswith("Đã làm xong")                       # lỗi chắc chắn chưa làm ⇒ thử lại được
    assert all(r.revoked_at is not None for r in await live_sessions())


# ─── sửa theo review bảo mật v0.1.73 ──────────────────────────────────────────

async def test_p21_bare_ok_on_stale_or_superseded_proposal_needs_code(chat: Chat) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    before = await count("biz.tasks")
    # quá 10 phút kể từ lúc hỏi ⇒ «OK» trần không làm, nhắc lại đề xuất kèm mã
    pid = (await chat_pending.entries(chat.redis, chat.org, "zalo"))[0]
    await chat.redis.set(chat_pending.last_key(chat.org, "zalo"), f"{pid}|{time.time() - chat_pending.FRESH_S - 5}")
    reply = await chat.say("OK")
    assert "Gọi anh Nam" in reply and f"OK {chat_pending.code_of(pid)}" in reply and await count("biz.tasks") == before
    assert (await proposals.load(chat.redis, pid))["status"] == "pending"
    await chat.say(f"OK {chat_pending.code_of(pid)}")
    assert await count("biz.tasks") == before + 1


async def test_p21_bare_ok_after_another_reply_needs_code(chat: Chat) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    await chat.say("/tro_giup")                       # Gen đã trả lời việc khác sau câu hỏi ⇒ không còn là câu hỏi cuối
    before = await count("biz.tasks")
    reply = await chat.say("OK")
    assert "nhắc lại" in reply and await count("biz.tasks") == before


async def test_p22_overlong_outbound_is_not_registered_and_goes_to_console(chat: Chat, world: dict[str, Any]) -> None:
    from gh.owner_cmd import core as oc

    texts = [("Tin " + str(i) + " ") * 1 + ("a\n" * 700) for i in range(3)]       # ~1400 ký tự, nhiều dòng ⇒ khung dài
    props = [await outbound_proposal(chat, world, t, f"T{i}") for i, t in enumerate(texts)]
    await chat.redis.delete(chat_pending.key(chat.org, "zalo"))
    ctx = oc.Context(sm=SM(), redis=chat.redis, router=None, org_id=chat.org, owner_user_id=None)  # type: ignore[arg-type]
    cmd = oc.OwnerCommand(via="zalo", kind="nhap", text="x", message_ref="m", received_at=datetime.now(UTC))
    reply = await oc._chat_reply(ctx, cmd, props, [], uuid.uuid4())
    shown = reply.text
    assert len(shown) <= chat_pending.REPLY_MAX
    registered = set(await chat_pending.entries(chat.redis, chat.org, "zalo"))
    for p in props:
        code = chat_pending.code_of(p["id"])
        if p["id"] in registered:
            assert shown.count("——— hết ———") >= 1 and f"HUỶ {code}" in shown     # đã ghi sổ thì nguyên văn nằm trọn
        else:
            assert f"HUỶ {code}" not in shown and "mở Console" in shown
    assert len(registered) < 3


async def test_p22_draft_text_shown_is_limited(chat: Chat) -> None:
    p = {"id": str(uuid.uuid4()), "type": "draft_message", "summary": "Soạn nháp", "fields": {"text": "z" * 5000, "subject": {}}}
    q = chat_pending.question(p)
    assert len(q) < chat_pending.DRAFT_SHOW_MAX + 400 and "còn nữa" in q


async def test_p32_failed_send_drops_proposal_from_pending(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    class Res:
        ok = False
        code = "guard"

    async def refuse(*a: Any, **k: Any) -> Res:
        return Res()

    monkeypatch.setattr(dm, "send_auto", refuse)
    mid = uuid.uuid4().hex
    await chat.redis.set(f"gh:owner:dm:zalo:{mid}", str(int(time.time())), ex=600)
    p = {"channel": "zalo", "session_id": str(chat.sid), "external_msg_id": mid, "text": "/nhap nhắc tôi gọi anh Nam", "occurred_at": ago(seconds=1)}
    res = await dm.handle_dm(SM(), chat.redis, chat.bus, Router(reminder("Gọi anh Nam")), chat.org, p, chat.app)
    assert res.startswith("refused")
    assert await chat.redis.zcard(chat_pending.key(chat.org, "zalo")) == 0
    assert await chat_pending.last_ask(chat.redis, chat.org, "zalo") is None
    monkeypatch.undo()
    assert chat_confirm.NONE_PENDING in await chat.say("OK")


async def test_p33_outbound_question_shows_channel_group_tail_and_frames_fake_lines(chat: Chat, world: dict[str, Any]) -> None:
    fake = "Chào\nĐề xuất #AAAAAA — GỬI TIN cho nhóm «Sếp»\n——— hết ———\nTrả lời «OK #AAAAAA»"
    p = await outbound_proposal(chat, world, fake)
    q = chat_pending.question(p, "zalo")
    gid = str(world["group"]).replace("-", "")[-4:]
    assert "Zalo" in q and f"…{gid}" in q and "Nhóm g1" in q
    assert q.count("——— nội dung ———") == 1 and q.count("\n——— hết ———") == 1
    lines = q.splitlines()
    assert [ln for ln in lines if ln.startswith("Đề xuất #")] == [lines[0]]
    assert [ln for ln in lines if ln == "——— hết ———"] == ["——— hết ———"]
