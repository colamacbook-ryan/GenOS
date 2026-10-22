"""v0.1.64 (T1) — Telegram 2 chiều: NHẬN tin và XÁC THỰC.

Chỉ tài khoản Sếp đã ghép (mã 6 số, sống 10 phút) mới thành lệnh. chat_id lạ / nhóm / kênh / tin chuyển tiếp / bot /
tin cũ hơn 5 phút / update_id lặp ⇒ bỏ im lặng + Action Log `telegram.rejected` (chỉ lý do, KHÔNG nội dung).
Telegram giả
bằng `httpx.MockTransport` — không gọi Telegram thật.
"""

import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import orjson
import psycopg
import pytest
from sqlalchemy import text

from gh import crypto
from gh.db import admin_sessionmaker, sessionmaker
from gh.telegram import inbound, poller
from tests.conftest import PG, Api, verify_pin
from tests.test_telegram_v0144 import CHAT, TOKEN, TOKEN2, FakeTelegram

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "sql" / "0037_v0164_telegram_inbound.sql"
OWNER_TG = 987654321  # from.id / chat.id (chat riêng) của Sếp
NOW = datetime(2026, 10, 17, 3, 0, tzinfo=UTC)
SECRET_WORDS = "BÍ-MẬT-NỘI-DUNG-TIN-12345"


class FakeBot(FakeTelegram):
    """Bot giả có getUpdates đúng ngữ nghĩa `offset` (chỉ trả update_id ≥ offset)."""

    def handle(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/getUpdates") and req.content:
            body = orjson.loads(req.content)
            self.calls.append((req.url.path, body))
            off = int(body.get("offset") or 0)
            return httpx.Response(200, json={"ok": True, "result": [u for u in self.updates if u["update_id"] >= off]})
        return super().handle(req)

    def sent(self) -> list[dict[str, Any]]:
        return [b for p, b in self.calls if p.endswith("/sendMessage") and b]

    def polls(self) -> list[dict[str, Any]]:
        return [b for p, b in self.calls if p.endswith("/getUpdates") and b]


@pytest.fixture
def bot(app: Any) -> FakeBot:
    f = FakeBot()
    app.state.telegram_transport = httpx.MockTransport(f.handle)
    return f


def msg(
    update_id: int,
    text_: str = "/hom_nay",
    *,
    chat_id: int = OWNER_TG,
    user_id: int = OWNER_TG,
    chat_type: str = "private",
    date: datetime = NOW,
    **over: Any,
) -> dict[str, Any]:
    m: dict[str, Any] = {
        "message_id": update_id + 1000,
        "date": int(date.timestamp()),
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": "Sếp"},
        "text": text_,
    }
    m.update(over)
    return {"update_id": update_id, "message": m}


CFG = inbound.InboundConfig(
    org_id=uuid.uuid4(), owner_chat_id=str(OWNER_TG), owner_tg_user_id=str(OWNER_TG), last_update_id=5
)

# (tên, update, lý do bị bỏ) — mọi ca xác thực bắt buộc của T1.
REJECTS: list[tuple[str, dict[str, Any], str]] = [
    ("chat_id lạ", msg(10, chat_id=999, user_id=OWNER_TG), inbound.R_STRANGER_CHAT),
    ("người gửi lạ cùng chat", msg(11, user_id=777), inbound.R_STRANGER_USER),
    ("nhóm", msg(12, chat_id=-100123, chat_type="group"), inbound.R_GROUP),
    ("siêu nhóm", msg(13, chat_id=-100124, chat_type="supergroup"), inbound.R_GROUP),
    ("chat kênh", msg(14, chat_id=-100125, chat_type="channel"), inbound.R_CHANNEL),
    ("bài đăng kênh", {"update_id": 15, "channel_post": msg(15)["message"]}, inbound.R_CHANNEL),
    ("tin chuyển tiếp (forward_origin)", msg(16, forward_origin={"type": "user"}), inbound.R_FORWARDED),
    ("tin chuyển tiếp (forward_from)", msg(17, forward_from={"id": 1}), inbound.R_FORWARDED),
    ("tin chuyển tiếp (forward_date)", msg(18, forward_date=1), inbound.R_FORWARDED),
    (
        "bot khác",
        {"update_id": 19, "message": {**msg(19)["message"], "from": {"id": OWNER_TG, "is_bot": True}}},
        inbound.R_BOT,
    ),
    ("tin qua bot khác (via_bot)", msg(20, via_bot={"id": 9, "is_bot": True}), inbound.R_BOT),
    ("tin cũ hơn 5 phút", msg(21, date=NOW - timedelta(minutes=5, seconds=2)), inbound.R_STALE),
    ("tin từ tương lai", msg(22, date=NOW + timedelta(minutes=6)), inbound.R_STALE),
    ("update_id lặp (= offset)", msg(5), inbound.R_DUPLICATE),
    ("update_id cũ hơn offset", msg(3), inbound.R_DUPLICATE),
    ("sửa tin", {"update_id": 23, "edited_message": msg(23)["message"]}, inbound.R_NOT_MESSAGE),
    ("nút gọi lại", {"update_id": 24, "callback_query": {"id": "1", "data": "approve"}}, inbound.R_NOT_MESSAGE),
    (
        "không có chữ",
        {"update_id": 25, "message": {k: v for k, v in msg(25)["message"].items() if k != "text"}},
        inbound.R_NO_TEXT,
    ),
    (
        "không có người gửi",
        {"update_id": 26, "message": {k: v for k, v in msg(26)["message"].items() if k != "from"}},
        inbound.R_STRANGER_USER,
    ),
    ("update_id không phải số", {"update_id": "27", "message": msg(27)["message"]}, inbound.R_NOT_MESSAGE),
    ("rác", "không phải dict", inbound.R_NOT_MESSAGE),
]


@pytest.mark.parametrize(("name", "update", "reason"), REJECTS, ids=[r[0] for r in REJECTS])
def test_evaluate_rejects(name: str, update: Any, reason: str) -> None:
    assert inbound.evaluate(update, CFG, now=NOW) == reason
    assert inbound.accept(update, CFG, now=NOW) is None


def test_chua_ghep_thi_bo() -> None:
    cfg = inbound.InboundConfig(org_id=CFG.org_id, owner_chat_id=None, owner_tg_user_id=None)
    assert inbound.evaluate(msg(10), cfg, now=NOW) == inbound.R_UNPAIRED


def test_accept_dung_chat_da_ghep() -> None:
    cmd = inbound.accept(msg(10, "/nhap nhắc tôi gọi anh Nam"), CFG, now=NOW + timedelta(minutes=4, seconds=59))
    assert cmd == inbound.TelegramCommand(
        kind="nhap", text="nhắc tôi gọi anh Nam", chat_id=str(OWNER_TG), message_id=1010, received_at=NOW
    )
    free = inbound.accept(msg(11, "  hôm nay có khách nào nóng không?  "), CFG, now=NOW)
    assert free is not None and free.kind == "hoi" and free.text == "hôm nay có khách nào nóng không?"
    named = inbound.accept(msg(12, "/Hom_Nay@gen_sep_bot"), CFG, now=NOW)
    assert named is not None and named.kind == "hom_nay" and named.text == ""


# ─── hạ tầng: bản cài + ghép ────────────────────────────────────────────────


async def _org() -> Any:
    async with admin_sessionmaker()() as s:
        return (await s.execute(text("SELECT id FROM core.organizations"))).scalar_one()


async def _row() -> Any:
    async with admin_sessionmaker()() as s:
        return (await s.execute(text("SELECT * FROM ops.notify_channels"))).one()


async def _configure(api: Api) -> None:
    await verify_pin(api)
    r = await api.send("PUT", "/notify/telegram", {"token": TOKEN, "chat_id": CHAT})
    assert r.status_code == 200, r.text


async def _pair(api: Api, bot: FakeBot, redis: Any, *, update_id: int = 1) -> None:
    """Qua đúng luồng thật: Console cấp mã ⇒ Sếp gửi /start <mã> ⇒ poll_org."""
    await _configure(api)
    r = await api.send("POST", "/notify/telegram/pair/start", {})
    assert r.status_code == 200, r.text
    code = r.json()["pair"]["code"]
    bot.updates = [msg(update_id, f"/start {code}", date=datetime.now(UTC))]
    out = await poller.poll_org(sessionmaker(), redis, None, await _org(), transport=httpx.MockTransport(bot.handle))
    assert out["results"] == ["paired"], out


async def _rejected_logs() -> list[Any]:
    async with admin_sessionmaker()() as s:
        return (
            await s.execute(
                text("""SELECT detail, result, actor_type FROM ops.action_log
                                        WHERE action = 'telegram.rejected' ORDER BY at""")
            )
        ).all()


async def test_migration_0037_rerunnable_and_default_off(owner_api: Api, bot: FakeBot, fresh_db: str) -> None:
    sql = SQL_FILE.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
    await _configure(owner_api)  # bản cài có Telegram một chiều sẵn ⇒ nhận tin mặc định TẮT
    row = await _row()
    assert row.inbound_enabled is False and row.owner_chat_id is None and row.inbound_offset == 0
    body = (await owner_api.get("/notify/telegram")).json()
    assert body["inbound"]["enabled"] is False and body["inbound"]["paired"] is False


async def test_pair_code_is_returned_once_and_stored_only_hashed(owner_api: Api, bot: FakeBot) -> None:
    await _configure(owner_api)
    r = await owner_api.send("POST", "/notify/telegram/pair/start", {})
    assert r.status_code == 200, r.text
    body = r.json()
    code = body["pair"]["code"]
    assert len(code) == 6 and code.isdigit() and body["pair"]["bot_username"] == "gen_sep_bot"
    assert body["inbound"]["pair_pending"] is True and code not in json.dumps(body["inbound"])
    row = await _row()
    assert code.encode() not in bytes(row.pair_code_hash) and row.pair_expires_at is not None
    assert (row.pair_expires_at - datetime.now(UTC)) <= inbound.PAIR_TTL
    assert code not in (await owner_api.get("/notify/telegram")).text  # GET không bao giờ lộ mã
    async with admin_sessionmaker()() as s:
        logs = (
            (await s.execute(text("SELECT detail::text FROM ops.action_log WHERE action = 'telegram.pair_start'")))
            .scalars()
            .all()
        )
    assert len(logs) == 1 and code not in logs[0]


async def test_pair_code_hash_is_keyed_by_server_master_key(
    owner_api: Api, bot: FakeBot, redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P3 review v0.1.64: sha256 trơn của mã 6 số dò hết 10^6 khả năng khi lộ DB ⇒ phải là HMAC khoá máy chủ."""
    await _configure(owner_api)
    code = (await owner_api.send("POST", "/notify/telegram/pair/start", {})).json()["pair"]["code"]
    org = await _org()
    stored = bytes((await _row()).pair_code_hash)
    assert stored != hashlib.sha256(f"gh-telegram-pair:{org}:{code}".encode()).digest()  # không còn băm trơn
    assert stored != hashlib.sha256(f"{org}:{code}".encode()).digest()
    # băm phụ thuộc khoá master của máy chủ: đổi khoá ⇒ cùng mã ra băm khác (lộ DB mà thiếu khoá thì không dò được mã)
    assert inbound._hash(org, code) == stored
    real_key, other = crypto.master_key, os.urandom(32)
    monkeypatch.setattr(crypto, "master_key", lambda: other)
    assert inbound._hash(org, code) != stored
    monkeypatch.setattr(crypto, "master_key", real_key)
    bot.updates = [msg(2, f"/start {code}", date=datetime.now(UTC))]
    out = await poller.poll_org(sessionmaker(), redis, None, org, transport=httpx.MockTransport(bot.handle))
    assert out["results"] == ["paired"], out  # đúng khoá ⇒ ghép được


async def test_pair_start_without_pin_is_423(owner_api: Api, bot: FakeBot) -> None:
    await verify_pin(owner_api)
    assert (await owner_api.send("PUT", "/notify/telegram", {"token": TOKEN, "chat_id": CHAT})).status_code == 200
    async with admin_sessionmaker()() as s:
        await s.execute(text("UPDATE core.sessions SET pin_verified_until = NULL"))
        await s.commit()
    for method, url, body in (
        ("POST", "/notify/telegram/pair/start", {}),
        ("PUT", "/notify/telegram/inbound", {"enabled": False}),
        ("DELETE", "/notify/telegram/pair", None),
    ):
        r = await owner_api.send(method, url, body)
        assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED", (url, r.text)


async def test_pair_start_before_token_is_409(owner_api: Api, bot: FakeBot) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/notify/telegram/pair/start", {})
    assert r.status_code == 409 and r.json()["code"] == "TELEGRAM_NOT_CONFIGURED"


async def test_pairing_full_flow(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    row = await _row()
    assert row.owner_chat_id == str(OWNER_TG) and row.owner_tg_user_id == str(OWNER_TG)
    assert row.inbound_enabled is True and row.pair_code_hash is None and row.paired_at is not None
    assert row.inbound_offset == 1 and row.owner_user_id is not None
    body = (await owner_api.get("/notify/telegram")).json()
    assert body["inbound"]["paired"] is True and body["inbound"]["enabled"] is True
    assert body["inbound"]["owner_masked"] == "•••4321" and str(OWNER_TG) not in json.dumps(body)
    assert len(bot.sent()) == 1 and bot.sent()[0]["chat_id"] == str(OWNER_TG)  # lời xác nhận ghép
    assert "reply_markup" not in bot.sent()[0]
    async with admin_sessionmaker()() as s:
        n = (await s.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'telegram.paired'"))).scalar_one()
    assert n == 1


async def test_pair_wrong_code_is_silent_and_locks_after_5(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _configure(owner_api)
    code = (await owner_api.send("POST", "/notify/telegram/pair/start", {})).json()["pair"]["code"]
    wrong = "000000" if code != "000000" else "111111"
    t = httpx.MockTransport(bot.handle)
    org = await _org()
    for i in range(1, 6):
        bot.updates = [msg(i, f"/start {wrong}", user_id=555, chat_id=555, date=datetime.now(UTC))]
        out = await poller.poll_org(sessionmaker(), redis, None, org, transport=t)
        assert out["results"][0].startswith("rejected:pair_"), out
    assert bot.sent() == []  # im lặng với người lạ
    row = await _row()
    assert row.pair_code_hash is None and row.owner_chat_id is None  # mã đã bị huỷ sau 5 lần sai
    bot.updates = [msg(6, f"/start {code}", user_id=555, chat_id=555, date=datetime.now(UTC))]
    out = await poller.poll_org(sessionmaker(), redis, None, org, transport=t)
    assert (await _row()).owner_chat_id is None  # mã đúng nhưng đã huỷ ⇒ không ghép được


async def test_pair_code_expires_after_10_minutes(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _configure(owner_api)
    code = (await owner_api.send("POST", "/notify/telegram/pair/start", {})).json()["pair"]["code"]
    async with admin_sessionmaker()() as s:
        await s.execute(text("UPDATE ops.notify_channels SET pair_expires_at = now() - interval '1 second'"))
        await s.commit()
    bot.updates = [msg(1, f"/start {code}", date=datetime.now(UTC))]
    out = await poller.poll_org(sessionmaker(), redis, None, await _org(), transport=httpx.MockTransport(bot.handle))
    assert out["updates"] == 0 or (await _row()).owner_chat_id is None
    assert (await _row()).owner_chat_id is None and bot.sent() == []


async def test_pair_rejects_group_forward_bot_and_old(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _configure(owner_api)
    code = (await owner_api.send("POST", "/notify/telegram/pair/start", {})).json()["pair"]["code"]
    now = datetime.now(UTC)
    bot.updates = [
        msg(1, f"/start {code}", chat_id=-100, chat_type="group", date=now),
        msg(2, f"/start {code}", forward_origin={"type": "user"}, date=now),
        {
            "update_id": 3,
            "message": {**msg(3, f"/start {code}", date=now)["message"], "from": {"id": OWNER_TG, "is_bot": True}},
        },
        msg(4, f"/start {code}", date=now - timedelta(minutes=6)),
    ]
    out = await poller.poll_org(sessionmaker(), redis, None, await _org(), transport=httpx.MockTransport(bot.handle))
    assert [r.split(":")[0] for r in out["results"]] == ["rejected"] * 4
    assert (await _row()).owner_chat_id is None and bot.sent() == []


# ─── nhận tin sau khi ghép ───────────────────────────────────────────────────


async def test_rejections_are_silent_logged_without_content_and_bell(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    bot.calls.clear()
    now = datetime.now(UTC)
    bot.updates = [
        msg(10, SECRET_WORDS, chat_id=999, user_id=999, date=now),  # chat_id lạ
        msg(11, SECRET_WORDS, chat_id=-1, chat_type="group", date=now),  # nhóm
        {"update_id": 12, "channel_post": msg(12, SECRET_WORDS, date=now)["message"]},  # kênh
        msg(13, SECRET_WORDS, forward_origin={"type": "user"}, date=now),  # chuyển tiếp
        msg(14, SECRET_WORDS, date=now - timedelta(minutes=9)),  # cũ
        {
            "update_id": 15,
            "message": {**msg(15, SECRET_WORDS, date=now)["message"], "from": {"id": OWNER_TG, "is_bot": True}},
        },  # bot
        msg(1, SECRET_WORDS, date=now),  # update_id lặp (≤ offset 1)
    ]
    bot.updates = [u for u in bot.updates if u["update_id"] >= 1]
    t = httpx.MockTransport(bot.handle)
    sm = sessionmaker()
    org = await _org()
    out = await poller.poll_org(sm, redis, None, org, transport=t)
    # update_id=1 < offset+1=2 nên Telegram không trả lại; nạp thẳng để thử nhánh trùng
    out2 = await poller.poll_org(sm, redis, None, org, transport=t)
    assert out["results"] == [
        "rejected:stranger_chat",
        "rejected:group",
        "rejected:channel",
        "rejected:forwarded",
        "rejected:stale",
        "rejected:bot",
    ], out
    assert out2["updates"] == 0
    assert bot.sent() == []  # IM LẶNG: không trả lời ai
    logs = await _rejected_logs()
    assert [lg.detail["reason"] for lg in logs] == ["stranger_chat", "group", "channel", "forwarded", "stale", "bot"]
    assert all(lg.result == "blocked" and lg.actor_type == "system" for lg in logs)
    assert all(set(lg.detail) <= {"reason", "update_id", "chat_type"} for lg in logs)
    async with admin_sessionmaker()() as s:
        dump = orjson.dumps(
            [
                (await s.execute(text(q))).all()
                for q in ("SELECT * FROM ops.action_log", "SELECT * FROM core.notifications")
            ],
            default=str,
        ).decode()
    assert SECRET_WORDS not in dump  # không nội dung tin trong log/chuông
    assert TOKEN not in dump
    # P3 review v0.1.64: tin bị bỏ nhưng CHÍNH Sếp gửi (nhóm, chuyển tiếp, cũ) không tính vào chuông "người lạ";
    # chỉ đếm stranger_chat + channel + bot = 3 ⇒ chưa chuông.
    assert int(await redis.get(f"gh:tg:rej:{org}")) == 3
    async with admin_sessionmaker()() as s:
        assert not (await s.execute(text("SELECT 1 FROM core.notifications WHERE kind = 'telegram.rejected'"))).all()
    assert (await _row()).inbound_offset == 15  # offset đã tiến qua cả tin bị bỏ
    # thêm 2 người lạ ⇒ đủ 5 lần/giờ ⇒ ĐÚNG một chuông "người lạ nhắn bot"
    bot.updates = [msg(16, "a", chat_id=998, user_id=998, date=now), msg(17, "b", chat_id=997, user_id=997, date=now)]
    await poller.poll_org(sm, redis, None, org, transport=t)
    async with admin_sessionmaker()() as s:
        bells = (
            await s.execute(text("SELECT title, link FROM core.notifications WHERE kind = 'telegram.rejected'"))
        ).all()
    assert len(bells) == 1 and bells[0].link == "/connections#telegram"
    assert (await _row()).inbound_offset == 17


async def test_rejection_action_log_is_capped_per_reason_per_hour(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    """P3 review v0.1.64: bị spam ⇒ Action Log không phình vô hạn: tối đa N dòng/giờ/lý do, còn lại đếm ở Redis."""
    await _pair(owner_api, bot, redis)
    org = await _org()
    n = inbound.REJECT_LOG_PER_REASON
    total = n + 7
    now = datetime.now(UTC)
    bot.updates = [msg(10 + i, "spam", chat_id=999, user_id=999, date=now) for i in range(total)]
    bot.updates += [msg(200 + i, "nhóm", chat_id=-5, chat_type="group", date=now) for i in range(2)]
    out = await poller.poll_org(sessionmaker(), redis, None, org, transport=httpx.MockTransport(bot.handle))
    assert len(out["results"]) == total + 2
    logs = await _rejected_logs()
    stranger = [lg for lg in logs if lg.detail["reason"] == "stranger_chat"]
    assert len(stranger) == n + 1  # N dòng + đúng MỘT dòng báo "đã giới hạn"
    assert sum(1 for lg in stranger if lg.detail.get("throttled")) == 1
    assert len([lg for lg in logs if lg.detail["reason"] == "group"]) == 2  # giới hạn theo TỪNG lý do
    assert int(await redis.get(f"gh:tg:rejlog:{org}:stranger_chat")) == total  # phần còn lại vẫn được đếm
    async with admin_sessionmaker()() as s:  # chuông "người lạ" vẫn kêu đúng một lần dù bớt dòng nhật ký
        bells = (await s.execute(text("SELECT 1 FROM core.notifications WHERE kind = 'telegram.rejected'"))).all()
    assert len(bells) == 1


async def test_owner_stale_backlog_and_unpaired_chatter_do_not_ring_stranger_bell(
    owner_api: Api, bot: FakeBot, redis: Any
) -> None:
    """P3 review v0.1.64: tin tồn cũ của chính Sếp (stale) / tin khi chưa ghép không được báo nhầm "người lạ"."""
    await _configure(owner_api)
    org = await _org()
    sm = sessionmaker()
    t = httpx.MockTransport(bot.handle)
    r = await owner_api.send("POST", "/notify/telegram/pair/start", {})
    code = r.json()["pair"]["code"]
    now = datetime.now(UTC)
    # đang chờ ghép: 6 tin thường (chưa ghép ⇒ unpaired) rồi mới tới /start <mã>
    bot.updates = [msg(i, "xin chào", date=now) for i in range(1, 7)] + [msg(7, f"/start {code}", date=now)]
    out = await poller.poll_org(sm, redis, None, org, transport=t)
    assert out["results"] == ["rejected:unpaired"] * 6 + ["paired"], out
    assert await redis.get(f"gh:tg:rej:{org}") is None
    # sau ghép: 6 tin cũ (hơn 5 phút) của chính Sếp
    old = now - timedelta(minutes=30)
    bot.updates = [msg(i, "tin cũ", date=old) for i in range(8, 14)]
    out = await poller.poll_org(sm, redis, None, org, transport=t)
    assert out["results"] == ["rejected:stale"] * 6, out
    assert await redis.get(f"gh:tg:rej:{org}") is None
    async with admin_sessionmaker()() as s:
        assert not (await s.execute(text("SELECT 1 FROM core.notifications WHERE kind = 'telegram.rejected'"))).all()


async def test_duplicate_update_id_is_rejected_and_not_counted_for_bell(
    owner_api: Api, bot: FakeBot, redis: Any
) -> None:
    await _pair(owner_api, bot, redis)
    org = await _org()
    sm = sessionmaker()
    # Telegram giả TRẢ LẠI update_id đã xử lý (offset bị bỏ qua): phải bị coi là trùng, không chạy lệnh lần hai.
    always = [msg(2, "/tro_giup", date=datetime.now(UTC))]
    bot.handle = lambda req: httpx.Response(200, json={"ok": True, "result": always})  # type: ignore[method-assign]
    t = httpx.MockTransport(bot.handle)
    first = await poller.poll_org(sm, redis, None, org, transport=t)
    again = await poller.poll_org(sm, redis, None, org, transport=t)
    assert first["results"] == ["handled:tro_giup"] and again["results"] == ["rejected:duplicate"]
    reasons = [lg.detail["reason"] for lg in await _rejected_logs()]
    assert reasons == ["duplicate"]
    assert await redis.get(f"gh:tg:rej:{org}") is None  # trùng không tính vào 5 lần/giờ


async def test_poll_uses_offset_plus_one_and_allowed_updates(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis, update_id=7)
    bot.updates = []
    await poller.poll_org(
        sessionmaker(), redis, None, await _org(), transport=httpx.MockTransport(bot.handle), timeout_s=20
    )
    last = bot.polls()[-1]
    assert last["offset"] == 8 and last["timeout"] == 20 and last["allowed_updates"] == ["message"]


async def test_inbound_off_by_default_does_not_poll(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _configure(owner_api)  # đã lưu Telegram nhưng KHÔNG ghép, KHÔNG bật
    out = await poller.run_inbound(sessionmaker(), redis, None, transport=httpx.MockTransport(bot.handle), budget_s=1)
    assert out == {"orgs": 0, "updates": 0} and bot.polls() == []


async def test_toggle_off_stops_polling_and_commands(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    r = await owner_api.send("PUT", "/notify/telegram/inbound", {"enabled": False})
    assert r.status_code == 200 and r.json()["inbound"] == {**r.json()["inbound"], "enabled": False, "paired": True}
    bot.calls.clear()
    out = await poller.run_inbound(sessionmaker(), redis, None, transport=httpx.MockTransport(bot.handle), budget_s=1)
    assert out["orgs"] == 0 and bot.polls() == []
    r = await owner_api.send("PUT", "/notify/telegram/inbound", {"enabled": True})
    assert r.status_code == 200 and r.json()["inbound"]["enabled"] is True


async def test_toggle_on_needs_pairing(owner_api: Api, bot: FakeBot) -> None:
    await _configure(owner_api)
    r = await owner_api.send("PUT", "/notify/telegram/inbound", {"enabled": True})
    assert r.status_code == 409 and r.json()["code"] == "TELEGRAM_NOT_PAIRED"
    assert (await _row()).inbound_enabled is False


async def test_unpair_clears_everything(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    r = await owner_api.send("DELETE", "/notify/telegram/pair")
    assert r.status_code == 200 and r.json()["inbound"]["paired"] is False
    row = await _row()
    assert row.owner_chat_id is None and row.owner_tg_user_id is None and row.inbound_enabled is False
    # tin của chính Sếp sau khi gỡ ghép ⇒ bị bỏ (không còn chủ)
    cfg = inbound.config_of(row)
    assert inbound.evaluate(msg(50, date=datetime.now(UTC)), cfg) == inbound.R_UNPAIRED


async def test_changing_bot_token_drops_pairing(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    assert (await _row()).inbound_offset == 1
    r = await owner_api.send("PUT", "/notify/telegram", {"token": TOKEN2})
    assert r.status_code == 200, r.text
    row = await _row()
    assert row.owner_chat_id is None and row.inbound_enabled is False and row.inbound_offset == 0


async def test_same_token_keeps_pairing(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    r = await owner_api.send("PUT", "/notify/telegram", {"token": TOKEN, "briefing": False})
    assert r.status_code == 200
    assert (await _row()).owner_chat_id == str(OWNER_TG)


async def test_find_chat_does_not_poll_while_inbound_active(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    bot.calls.clear()
    body = (await owner_api.send("POST", "/notify/telegram/find-chat", {})).json()
    assert body["error_code"] == "TELEGRAM_INBOUND_ACTIVE" and body["chats"] == [] and body["message"]
    assert bot.polls() == [] and not [c for c in bot.calls if c[0].endswith("getUpdates")]


async def test_find_chat_with_token_in_body_also_does_not_poll_while_inbound_active(
    owner_api: Api, bot: FakeBot, redis: Any
) -> None:
    """P3 review v0.1.64: nhánh có token trong thân bỏ qua kiểm "đang nhận tin" ⇒ getUpdates song song gây 409."""
    await _pair(owner_api, bot, redis)
    bot.calls.clear()
    for payload in ({"token": TOKEN}, {"token": TOKEN2}):
        body = (await owner_api.send("POST", "/notify/telegram/find-chat", payload)).json()
        assert body["error_code"] == "TELEGRAM_INBOUND_ACTIVE" and body["chats"] == [], payload
    assert not [c for c in bot.calls if c[0].endswith("getUpdates")]
    # tắt nhận tin (vẫn đã ghép) ⇒ tìm chat bằng token trong thân lại dùng được
    r = await owner_api.send("PUT", "/notify/telegram/inbound", {"enabled": False})
    assert r.status_code == 200, r.text
    body = (await owner_api.send("POST", "/notify/telegram/find-chat", {"token": TOKEN})).json()
    assert body["error_code"] != "TELEGRAM_INBOUND_ACTIVE"


T0 = datetime(2026, 10, 18, 12, 0, 50, tzinfo=UTC)  # đồng hồ đóng băng — test không phụ thuộc giờ chạy


async def test_rate_limit_20_per_minute(redis: Any) -> None:
    org = uuid.uuid4()
    assert [await inbound.rate_ok(redis, org, T0) for _ in range(21)] == [True] * 20 + [False]
    assert await inbound.rate_ok(redis, org, T0 + timedelta(minutes=1)) is True  # đúng 60 giây sau ⇒ đã ra khỏi cửa sổ
    assert await inbound.rate_ok(redis, uuid.uuid4(), T0) is True  # tổ chức khác không bị ảnh hưởng


async def test_rate_limit_is_sliding_not_clock_minute(redis: Any) -> None:
    """Hồi quy v0.1.68: cửa sổ cố định theo phút đồng hồ cho lọt 40 lệnh trong ~2 giây quanh ranh giới phút."""
    org = uuid.uuid4()
    before = T0 + timedelta(seconds=9, milliseconds=500)  # 12:00:59.5 (phút đồng hồ cũ)
    after = before + timedelta(seconds=1)  # 12:01:00.5 (phút đồng hồ mới, cách 1 giây)
    assert [await inbound.rate_ok(redis, org, before) for _ in range(20)] == [True] * 20
    assert [await inbound.rate_ok(redis, org, after) for _ in range(3)] == [False] * 3  # vẫn trong 60 giây liền kề
    assert await inbound.rate_ok(redis, org, before + timedelta(seconds=59, milliseconds=999)) is False
    assert await inbound.rate_ok(redis, org, before + timedelta(seconds=60)) is True  # hết 60 giây ⇒ mở lại


async def test_rate_limit_counts_each_command_at_its_own_time(redis: Any) -> None:
    org = uuid.uuid4()
    for _ in range(10):  # 10 lệnh ở giây 0, 10 lệnh ở giây 30
        assert await inbound.rate_ok(redis, org, T0)
    for _ in range(10):
        assert await inbound.rate_ok(redis, org, T0 + timedelta(seconds=30))
    assert await inbound.rate_ok(redis, org, T0 + timedelta(seconds=59)) is False
    # giây 60: 10 lệnh đầu rời cửa sổ ⇒ đúng 10 chỗ trống; lệnh bị từ chối trước đó không chiếm chỗ
    assert [await inbound.rate_ok(redis, org, T0 + timedelta(seconds=60)) for _ in range(11)] == [True] * 10 + [False]
    assert await redis.zcard(f"gh:tg:rate:{org}") == 20


async def test_poll_error_is_reported_without_url_or_token(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    bot.handle = lambda req: httpx.Response(409, json={"ok": False, "description": "Conflict: webhook"})  # type: ignore[method-assign]
    out = await poller.poll_org(sessionmaker(), redis, None, await _org(), transport=httpx.MockTransport(bot.handle))
    assert out["error_code"] == "TELEGRAM_POLL_CONFLICT"
    body = (await owner_api.get("/notify/telegram")).json()
    assert body["inbound"]["error_code"] == "TELEGRAM_POLL_CONFLICT" and body["inbound"]["message"]
    assert TOKEN not in json.dumps(body)


async def test_run_inbound_lock_prevents_double_poll(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    org = await _org()
    await redis.set(poller.LOCK_KEY.format(org), "1", ex=60)
    bot.calls.clear()
    out = await poller.run_inbound(sessionmaker(), redis, None, transport=httpx.MockTransport(bot.handle), budget_s=1)
    assert out["updates"] == 0 and bot.polls() == []
    await redis.delete(poller.LOCK_KEY.format(org))
    t0 = time.monotonic()
    await poller.run_inbound(
        sessionmaker(), redis, None, transport=httpx.MockTransport(bot.handle), budget_s=1, long_poll_s=0
    )
    assert bot.polls() and time.monotonic() - t0 < 5 and await redis.get(poller.LOCK_KEY.format(org)) is None


def test_worker_registers_inbound_poller_as_cron_job() -> None:
    from gh.worker import JOB_LABELS, WorkerSettings

    names = {getattr(f, "__name__", getattr(f, "name", "")) for f in WorkerSettings.functions}
    assert "telegram_inbound" in names and "telegram_inbound" in JOB_LABELS
    cron_names = {c.name for c in WorkerSettings.cron_jobs}
    assert "cron:telegram_inbound" in cron_names
    job = next(c for c in WorkerSettings.cron_jobs if c.name == "cron:telegram_inbound")
    assert job.timeout_s == poller.JOB_TIMEOUT_S and poller.BUDGET_S + poller.LONG_POLL_S < poller.JOB_TIMEOUT_S
