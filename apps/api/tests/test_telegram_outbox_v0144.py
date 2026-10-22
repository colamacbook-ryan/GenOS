"""v0.1.44 (F-8c) — hộp thư đi Telegram: bản tin + nhắc việc một chiều, worker `telegram_flush`.

- Bản tin: đúng MỘT tin mỗi tổ chức mỗi khung giờ (không mỗi Owner), chạy lại không thêm; cờ briefing tắt ⇒ không xếp.
- Nhắc việc: chỉ việc của Owner (chưa giao ai, hoặc giao cho Owner); việc của nhân viên ⇒ không có tin Telegram.
- flush: gửi được ⇒ sent_at; 429 ⇒ lùi theo retry_after; mạng ⇒ attempts+1, lùi 2^attempts phút; bot bị chặn ⇒
  failed_code + sự cố telegram.failed (chuông); gửi lại được ⇒ đóng sự cố.
- KHÔNG qua bridge/Zalo (EventBus.publish bị cấm trong test)."""

from datetime import timedelta
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh import crypto
from gh.biz.queue.jobs import due_reminders
from gh.chassis.bus import EventBus
from gh.db import sessionmaker
from gh.gen import briefing
from gh.telegram import service as tsvc
from gh.worker import JOB_LABELS, WorkerSettings
from tests.phase2 import org_id
from tests.test_briefing_v0141 import _router, _today
from tests.test_rbac_api import add_user
from tests.test_telegram_v0144 import CHAT, TOKEN

SENT_PATH = f"/bot{TOKEN}/sendMessage"


class Sink:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.reply: tuple[int, dict[str, Any]] = (200, {"ok": True, "result": {}})
        self.timeout = False

    def handle(self, req: httpx.Request) -> httpx.Response:
        assert req.url.path == SENT_PATH
        if self.timeout:
            raise httpx.ReadTimeout("timeout", request=req)
        self.sent.append(orjson.loads(req.content))
        return httpx.Response(*self.reply[:1], json=self.reply[1])


@pytest.fixture(autouse=True)
def _remote_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.1.70 (F092): link Console chỉ chèn khi đã bật Truy cập từ xa — ở đây coi như đã cấu hình địa chỉ."""
    monkeypatch.setattr(tsvc, "public_url", lambda: "https://gen.tail1234.ts.net")


@pytest.fixture(autouse=True)
def _no_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("Không được gửi gì qua bridge/Zalo")

    monkeypatch.setattr(EventBus, "publish", boom)


async def _configure(db: Any, *, briefing_on: bool = True, reminders_on: bool = True, enabled: bool = True) -> Any:
    org = await org_id(db)
    await db.execute(text("""INSERT INTO ops.notify_channels (org_id, token_enc, chat_id, bot_username, enabled,
                                                              briefing, reminders)
                             VALUES (:o, :t, :c, 'gen_sep_bot', :e, :b, :r)"""),
                     {"o": org, "t": crypto.encrypt(TOKEN.encode(), tsvc.TOKEN_AAD), "c": CHAT, "e": enabled,
                      "b": briefing_on, "r": reminders_on})
    await db.commit()
    return org


async def _second_owner(db: Any) -> None:
    org = await org_id(db)
    uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                    VALUES (:o, 'owner2@example.vn', 'Owner 2', 'x') RETURNING id"""),
                            {"o": org})).scalar_one()
    await db.execute(text("""INSERT INTO core.user_roles (user_id, role_id)
                             SELECT :u, id FROM core.roles WHERE code = 'owner'"""), {"u": uid})
    await db.commit()


async def _outbox(db: Any) -> list[Any]:
    await db.rollback()
    return list((await db.execute(text("""SELECT kind, dedupe_key, text, attempts, sent_at, failed_code,
                                                 next_attempt_at, now() AS now
                                          FROM ops.telegram_outbox ORDER BY created_at"""))).all())


def test_cron_registered() -> None:
    cj = next(c for c in WorkerSettings.cron_jobs if c.name == "cron:telegram_flush")
    assert cj.minute == set(range(60)) and cj.timeout_s is not None and cj.timeout_s <= 120
    assert JOB_LABELS["telegram_flush"] == "Gửi tin Telegram"


async def test_briefing_enqueues_one_per_org(owner_api: Any, db: Any, redis: Any) -> None:
    await _second_owner(db)                           # Owner thứ hai: vẫn chỉ một tin cho tổ chức
    await _configure(db)
    morning = _today(7, 31)
    r = _router(redis)
    org = await org_id(db)
    assert (await briefing.run_briefing(sessionmaker(), redis, r, now=morning))[str(org)] == "sent"
    await briefing.run_briefing(sessionmaker(), redis, r, now=morning)
    await briefing.run_briefing(sessionmaker(), redis, r, now=morning + timedelta(hours=1))
    rows = await _outbox(db)
    assert len(rows) == 1
    slot = briefing.slot_for(morning)
    assert rows[0].kind == "briefing" and rows[0].dedupe_key == f"briefing:{slot.at.isoformat()}"
    body = rows[0].text
    assert body.startswith(f"Bản tin Gen · {slot.label}")
    assert "Mở Console: https://gen.tail1234.ts.net/overview" in body
    assert body.endswith("(Tin một chiều — mọi thao tác Sếp xác nhận trong Console.)")
    assert TOKEN not in body
    # Hai Owner ⇒ hai chuông, nhưng chỉ một tin Telegram.
    bells = (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'gen.briefing'"))).scalar_one()
    assert bells == 2


async def test_briefing_flag_off_no_enqueue(owner_api: Any, db: Any, redis: Any) -> None:
    await _configure(db, briefing_on=False)
    await briefing.run_briefing(sessionmaker(), redis, _router(redis), now=_today(7, 31))
    assert await _outbox(db) == []


async def test_briefing_text_lists_sections() -> None:
    sections = [{"key": "tasks_due", "title": "Việc đến hạn", "count": 2, "lines": ["Gọi lại anh Bình — hạn 09:00"]},
                {"key": "incidents", "title": "Sự cố cần Sếp", "count": 0, "lines": []}]
    body = tsvc.briefing_text("sáng 03/10", "Sếp ưu tiên gọi anh Bình.", sections)
    assert body.splitlines()[:5] == ["Bản tin Gen · sáng 03/10", "", "Sếp ưu tiên gọi anh Bình.", "",
                                     "• Việc đến hạn (2): Gọi lại anh Bình — hạn 09:00"]
    assert "Sự cố cần Sếp" not in body


async def test_reminders_only_for_owner_tasks(owner_api: Any, db: Any) -> None:
    await _configure(db)
    await add_user(db, "operator")
    org = await org_id(db)
    staff = (await db.execute(text("SELECT id FROM core.users WHERE email = 'operator@example.vn'"))).scalar_one()
    owner = (await db.execute(text("SELECT id FROM core.users WHERE email = 'owner@example.vn'"))).scalar_one()
    await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, status, priority, assignee_user_id, remind_at,
                                                    due_at)
                             VALUES (:o, 'TSK-8001', 'Việc của Sếp', 'todo', 'P1', :ow, now() - interval '1 minute',
                                     now() + interval '2 hours'),
                                    (:o, 'TSK-8002', 'Việc chưa giao', 'todo', 'P2', NULL, now() - interval '1 minute',
                                     NULL),
                                    (:o, 'TSK-8003', 'Việc của nhân viên', 'todo', 'P2', :st,
                                     now() - interval '1 minute', NULL)"""),
                     {"o": org, "ow": owner, "st": staff})
    await db.commit()
    async with sessionmaker()() as s:
        assert await due_reminders(s) == 3
        await s.commit()
    rows = await _outbox(db)
    assert sorted(r.text.splitlines()[0] for r in rows) == ["Nhắc việc: Việc chưa giao", "Nhắc việc: Việc của Sếp"]
    assert all(r.kind == "reminder" and r.dedupe_key.startswith("reminder:") for r in rows)
    mine = next(r for r in rows if "Việc của Sếp" in r.text)
    assert mine.text.splitlines()[1].startswith("TSK-8001 · P1 · hạn ")
    assert "Mở Console: https://gen.tail1234.ts.net/tasks" in mine.text
    async with sessionmaker()() as s:
        assert await due_reminders(s) == 0
    assert len(await _outbox(db)) == 2


async def test_reminders_flag_off(owner_api: Any, db: Any) -> None:
    await _configure(db, reminders_on=False)
    org = await org_id(db)
    await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, status, remind_at)
                             VALUES (:o, 'TSK-8101', 'Việc chưa giao', 'todo', now() - interval '1 minute')"""),
                     {"o": org})
    await db.commit()
    async with sessionmaker()() as s:
        assert await due_reminders(s) == 1
        await s.commit()
    assert await _outbox(db) == []


async def _enqueue(db: Any, org: Any, n: int = 1) -> None:
    for i in range(n):
        assert await tsvc.enqueue(db, org, "reminder", f"Nhắc việc: thử {i}", f"thu:{i}")
    assert not await tsvc.enqueue(db, org, "reminder", "trùng", "thu:0")          # trùng dedupe_key
    await db.commit()


async def test_flush_send_retry_and_failure(owner_api: Any, db: Any) -> None:
    org = await _configure(db)
    await _enqueue(db, org)
    sink = Sink()
    out = await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle))
    assert out == {"sent": 1, "retry": 0, "failed": 0}
    assert sink.sent == [{"chat_id": CHAT, "text": "Nhắc việc: thử 0", "disable_web_page_preview": True}]
    assert (await _outbox(db))[0].sent_at is not None
    assert (await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle)))["sent"] == 0

    # 429 ⇒ lùi theo retry_after, không tăng attempts.
    await db.execute(text("DELETE FROM ops.telegram_outbox"))
    await _enqueue(db, org)
    sink.reply = (429, {"ok": False, "parameters": {"retry_after": 120}})
    out = await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle))
    row = (await _outbox(db))[0]
    assert out["retry"] == 1 and row.attempts == 0 and row.sent_at is None
    assert timedelta(seconds=100) < row.next_attempt_at - row.now <= timedelta(seconds=121)

    # Mạng ⇒ attempts+1, lùi 2^1 phút.
    await db.execute(text("UPDATE ops.telegram_outbox SET next_attempt_at = now()"))
    await db.commit()
    sink.timeout = True
    await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle))
    row = (await _outbox(db))[0]
    assert row.attempts == 1 and row.failed_code is None
    assert timedelta(seconds=100) < row.next_attempt_at - row.now <= timedelta(minutes=2, seconds=1)

    # Bot bị chặn ⇒ failed_code + sự cố telegram.failed (chuông một lần).
    await db.execute(text("UPDATE ops.telegram_outbox SET next_attempt_at = now()"))
    await db.commit()
    sink.timeout = False
    sink.reply = (403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})
    out = await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle))
    assert out["failed"] == 1 and (await _outbox(db))[0].failed_code == "TELEGRAM_BOT_BLOCKED"
    alert = (await db.execute(text("""SELECT kind, severity, title, link, cleared_at FROM ops.health_alerts
                                      WHERE key = 'telegram.failed'"""))).one()
    assert alert.kind == "telegram.failed" and alert.severity == "warn" and alert.cleared_at is None
    assert alert.title == "Gen chưa gửi được tin Telegram cho Sếp" and alert.link == "/connections#telegram"
    bells = (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'telegram.failed'"))
             ).scalar_one()
    assert bells == 1
    issues = (await owner_api.get("/system/health")).json()["issues"]
    assert any(i["key"] == "telegram.failed" and i["action"] == "Mở cấu hình Telegram" for i in issues)

    # Gửi được lại ⇒ đóng sự cố.
    sink.reply = (200, {"ok": True, "result": {}})
    assert await tsvc.enqueue(db, org, "reminder", "Nhắc việc: lại", "thu:lai")
    await db.commit()
    out = await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle))
    assert out["sent"] == 1
    await db.rollback()
    cleared = (await db.execute(text("SELECT cleared_at FROM ops.health_alerts WHERE key = 'telegram.failed'"))
               ).scalar_one()
    assert cleared is not None


async def test_flush_error_after_send_keeps_sent_at(owner_api: Any, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lỗi sau khi gửi (đóng sự cố hỏng) không được xoá `sent_at` của tin đã tới tay Sếp — lượt sau không gửi trùng."""
    from gh import health

    org = await _configure(db)
    await _enqueue(db, org, 3)

    async def broken_clear(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("CSDL hỏng giữa lượt")

    monkeypatch.setattr(health, "clear", broken_clear)
    sink = Sink()
    out = await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle))
    assert out["sent"] == 3 and len(sink.sent) == 3
    assert all(r.sent_at is not None for r in await _outbox(db))
    assert (await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle)))["sent"] == 0
    assert len(sink.sent) == 3


async def test_flush_skips_old_and_cleans(owner_api: Any, db: Any) -> None:
    org = await _configure(db)
    await _enqueue(db, org, 2)
    await db.execute(text("""UPDATE ops.telegram_outbox SET created_at = now() - interval '2 days'
                             WHERE dedupe_key = 'thu:0'"""))
    await db.execute(text("""INSERT INTO ops.telegram_outbox (org_id, kind, dedupe_key, text, sent_at, created_at)
                             VALUES (:o, 'reminder', 'cu', 'cũ', now() - interval '8 days',
                                     now() - interval '8 days')"""),
                     {"o": org})
    await db.commit()
    sink = Sink()
    out = await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(sink.handle))
    assert out["sent"] == 1 and [m["text"] for m in sink.sent] == ["Nhắc việc: thử 1"]
    keys = {r.dedupe_key for r in await _outbox(db)}
    assert keys == {"thu:0", "thu:1"}                                   # tin đã gửi > 7 ngày bị dọn


async def test_disabled_config_no_enqueue_and_no_send(owner_api: Any, db: Any) -> None:
    org = await _configure(db, enabled=False)
    assert not await tsvc.enqueue(db, org, "briefing", "x", "b:1")
    await db.commit()
    assert await _outbox(db) == []


async def test_untrusted_text_is_defanged() -> None:
    """Tóm tắt AI và dòng đầu của mục (chữ khách/Kho) không thành link/@nhắc bấm được trên Telegram của Sếp."""
    sections = [{"key": "tasks_due", "title": "Việc đến hạn", "count": 1,
                 "lines": ["Khách gửi http://evil.example.com/dang-nhap và @gia_mao_bot"]}]
    body = tsvc.briefing_text("sáng 03/10", "Sếp vào www.lua-dao.vn ngay, hỏi a@b.vn. Bản v0.1.44 ổn.", sections)
    assert "://" not in body.split("Mở Console:")[0]
    assert "evil[.]example[.]com" in body and "www[.]lua-dao[.]vn" in body and "[@]gia_mao_bot" in body
    assert "a[@]b[.]vn" in body and "v0.1.44" in body
    assert "Mở Console: " in body and "/overview" in body                  # link Console của chính Gen vẫn còn
    r = tsvc.reminder_text("Gọi lại theo https://t.me/abc", "VIEC-1", "P1", None)
    assert "https[:]//t[.]me/abc" in r and "/tasks" in r
