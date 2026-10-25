# ruff: noqa: E501
"""v0.1.76 (gói T10) — kênh phụ `gh.telegram.backup`: `notify`, `send_code` (chỉ lưu BĂM), `flush`, lệnh "khoá trợ lý" (chỉ Sếp, nhận theo ID).

- notify: có Telegram ⇒ hàng `telegram`; không có ⇒ hàng `console` (đã ghi nhận); trùng dedupe_key ⇒ không thêm.
- send_code: trả băm (không phải mã); mã rõ chỉ nằm trong hàng đợi tới lúc gửi rồi bị XOÁ; hết hạn/lỗi cũng xoá; không log mã/token.
- khoá trợ lý: Owner Console / tài khoản Telegram đã ghép / ID Owner kênh quản trị; tên trùng, người lạ, nhân viên ⇒ bị từ chối, không đổi gì."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh import crypto
from gh.db import sessionmaker
from gh.telegram import backup
from gh.telegram import service as tsvc
from tests.mem_helpers import mk_org
from tests.test_telegram_v0144 import CHAT, TOKEN

TG_USER = "555000111"


class Sink:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.status = status
        self.body = body or {"ok": True, "result": {}}

    def transport(self) -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            self.sent.append(orjson.loads(req.content))
            return httpx.Response(self.status, json=self.body)

        return httpx.MockTransport(handle)


SHARED_CHAT = "-100777"          # chat chung (nhóm): KHÔNG được nhận mã


async def _telegram(db: Any, org: uuid.UUID, *, paired: bool = True, chat: str = CHAT, owner_chat: str = CHAT) -> None:
    await db.execute(text("""INSERT INTO ops.notify_channels (org_id, token_enc, chat_id, bot_username, enabled, owner_chat_id, owner_tg_user_id)
                             VALUES (:o, :t, :c, 'gen_bot', true, :oc, :ou)"""),
                     {"o": org, "t": crypto.encrypt(TOKEN.encode(), tsvc.TOKEN_AAD), "c": chat,
                      "oc": owner_chat if paired else None, "ou": TG_USER if paired else None})
    await db.commit()


async def _user(db: Any, org: uuid.UUID, role: str, email: str) -> uuid.UUID:
    uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                    VALUES (:o, :e, 'Người thử', 'x') RETURNING id"""), {"o": org, "e": email})).scalar_one()
    rid = (await db.execute(text("SELECT id FROM core.roles WHERE org_id = :o AND code = :r"), {"o": org, "r": role})).scalar_one_or_none()
    if rid is None:
        rid = (await db.execute(text("INSERT INTO core.roles (org_id, code, name, is_system) VALUES (:o, :r, :r, true) RETURNING id"), {"o": org, "r": role})).scalar_one()
    await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"), {"u": uid, "r": rid})
    await db.commit()
    return uid  # type: ignore[no-any-return]


async def _rows(db: Any) -> list[Any]:
    await db.rollback()
    return list((await db.execute(text("SELECT channel, kind, dedupe_key, text, sent_at, failed_code, attempts FROM ops.backup_outbox ORDER BY created_at"))).all())


# ─── notify ──────────────────────────────────────────────────────────────────

async def test_notify_telegram_then_flush_sends_plain_text(db: Any, caplog: pytest.LogCaptureFixture) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    caplog.set_level(logging.DEBUG)
    assert await backup.notify(db, org, "Zalo của Gen mất kết nối", dedupe_key="k1") is True
    assert await backup.notify(db, org, "Zalo của Gen mất kết nối", dedupe_key="k1") is False
    await db.commit()
    rows = await _rows(db)
    assert [(r.channel, r.kind, r.sent_at) for r in rows] == [("telegram", "incident", None)]
    sink = Sink()
    stats = await backup.flush(sessionmaker(), transport=sink.transport())
    assert stats["sent"] == 1
    assert sink.sent[0]["chat_id"] == CHAT and "mất kết nối" in sink.sent[0]["text"]
    assert (await _rows(db))[0].sent_at is not None
    assert TOKEN not in caplog.text


async def test_notify_without_telegram_falls_back_to_console_row(db: Any) -> None:
    org = await mk_org(db)
    assert await backup.notify(db, org, "sự cố", kind="security", dedupe_key="s1") is True
    await db.commit()
    rows = await _rows(db)
    assert [(r.channel, r.kind) for r in rows] == [("console", "security")] and rows[0].sent_at is not None


async def test_notify_rejects_unknown_kind_and_code_kind(db: Any) -> None:
    org = await mk_org(db)
    for kind in ("lạ", "code"):                                 # kind 'code' chỉ đi qua send_code
        with pytest.raises(ValueError):
            await backup.notify(db, org, "x", kind=kind)


async def test_flush_fatal_error_marks_failed_without_leaking(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    await backup.notify(db, org, "sự cố", dedupe_key="k")
    await db.commit()
    sink = Sink(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    stats = await backup.flush(sessionmaker(), transport=sink.transport())
    assert stats["failed"] == 1
    r = (await _rows(db))[0]
    assert r.sent_at is None and r.failed_code


# ─── mã qua kênh phụ: chỉ lưu băm ────────────────────────────────────────────

async def test_send_code_returns_hash_and_never_stores_plain_code(db: Any, caplog: pytest.LogCaptureFixture) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    caplog.set_level(logging.DEBUG)
    h = await backup.send_code(db, org)
    await db.commit()
    assert isinstance(h, str) and len(h) == 64
    pending = (await _rows(db))[0]
    assert pending.kind == "code" and pending.channel == "telegram"
    code = next(tok for tok in pending.text.split() if tok.isdigit() and len(tok) == 4)
    assert backup.hash_code(org, code) == h and h != code
    sink = Sink()
    await backup.flush(sessionmaker(), transport=sink.transport())
    assert code in sink.sent[0]["text"]                           # Sếp nhận được mã
    after = (await _rows(db))[0]
    assert after.sent_at is not None and code not in after.text   # DB không còn mã rõ sau khi gửi
    assert code not in caplog.text and TOKEN not in caplog.text
    assert backup.verify_code(org, code, h) is True
    assert backup.verify_code(org, "0000" if code != "0000" else "1111", h) is False
    assert backup.verify_code(uuid.uuid4(), code, h) is False     # băm gắn với tổ chức


async def test_send_code_unavailable_without_telegram(db: Any) -> None:
    org = await mk_org(db)
    with pytest.raises(backup.BackupUnavailable):
        await backup.send_code(db, org)
    assert await _rows(db) == []


async def test_send_code_expired_unsent_is_scrubbed(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    await backup.send_code(db, org)
    await db.execute(text("UPDATE ops.backup_outbox SET created_at = now() - interval '20 minutes'"))
    await db.commit()
    sink = Sink()
    stats = await backup.flush(sessionmaker(), transport=sink.transport())
    assert sink.sent == [] and stats["failed"] == 1
    r = (await _rows(db))[0]
    assert r.failed_code == "expired" and not any(t.isdigit() and len(t) == 4 for t in r.text.split())


async def test_send_code_failed_send_is_scrubbed(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    await backup.send_code(db, org)
    await db.commit()
    await backup.flush(sessionmaker(), transport=Sink(403, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}).transport())
    r = (await _rows(db))[0]
    assert r.failed_code and not any(t.isdigit() and len(t) == 4 for t in r.text.split())


# ─── khoá trợ lý ─────────────────────────────────────────────────────────────

async def _assistant_world(db: Any, org: uuid.UUID, owner_ext: str = "zl-owner-1") -> dict[str, Any]:
    """Một nhóm đang bật hỗ trợ, một kênh quản trị đang bật, một báo cáo chờ gửi."""
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'zalo-bot') RETURNING id"), {"o": org})).scalar_one()
    g = (await db.execute(text("INSERT INTO core.groups (org_id, code, channel_id, external_id, name) VALUES (:o, 'GRP-ZL-0001', :c, 'G1', 'Khách sỉ') RETURNING id"),
                          {"o": org, "c": ch})).scalar_one()
    await db.execute(text("INSERT INTO ops.group_support (org_id, group_id, enabled_via) VALUES (:o, :g, 'zalo')"), {"o": org, "g": g})
    await db.execute(text("""INSERT INTO ops.owner_channels (org_id, channel_type, owner_external_id, owner_thread_id, enabled, paired_at)
                             VALUES (:o, 'zalo', :x, :x, true, now())"""), {"o": org, "x": owner_ext})
    await db.execute(text("INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, text) VALUES (:o, 'zalo', 'alert', 'urgent', 'chờ gửi')"), {"o": org})
    await db.commit()
    return {"group": g}


async def _state(db: Any, org: uuid.UUID) -> dict[str, Any]:
    await db.rollback()
    enabled = (await db.execute(text("SELECT enabled FROM ops.owner_channels WHERE org_id = :o"), {"o": org})).scalar_one()
    support = (await db.execute(text("SELECT count(*) FROM ops.group_support WHERE org_id = :o AND disabled_at IS NULL"), {"o": org})).scalar_one()
    pending = (await db.execute(text("SELECT count(*) FROM ops.owner_outbox WHERE org_id = :o AND sent_at IS NULL AND failed_code IS NULL"), {"o": org})).scalar_one()
    return {"owner_channel": enabled, "support": support, "pending": pending, "locked": await backup.is_locked(db, org)}


UNLOCKED = {"owner_channel": True, "support": 1, "pending": 1, "locked": False}


async def test_lock_by_console_owner_stops_assistant_and_alerts(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    await _assistant_world(db, org)
    owner = await _user(db, org, "owner", "o@example.vn")
    res = await backup.lock_assistant(db, org, via="console", user_id=owner)
    await db.commit()
    assert res.ok and not res.already
    assert await _state(db, org) == {"owner_channel": False, "support": 0, "pending": 0, "locked": True}
    kinds = [(r.kind, r.channel) for r in await _rows(db)]
    assert ("security", "telegram") in kinds                      # báo qua kênh phụ
    log_rows = (await db.execute(text("SELECT action, result FROM ops.action_log WHERE org_id = :o AND action = 'assistant.lock'"), {"o": org})).all()
    assert [(r.action, r.result) for r in log_rows] == [("assistant.lock", "ok")]
    again = await backup.lock_assistant(db, org, via="console", user_id=owner)
    assert again.ok and again.already                              # chạy lại an toàn


async def test_lock_denied_for_non_owner_console_user(db: Any) -> None:
    org = await mk_org(db)
    await _assistant_world(db, org)
    staff = await _user(db, org, "staff", "s@example.vn")
    res = await backup.lock_assistant(db, org, via="console", user_id=staff)
    await db.commit()
    assert not res.ok and res.code == "NOT_OWNER"
    assert await _state(db, org) == UNLOCKED
    assert (await backup.lock_assistant(db, org, via="console", user_id=uuid.uuid4())).ok is False
    assert (await backup.lock_assistant(db, org, via="console", user_id=None)).ok is False
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'assistant.lock_denied'"))).scalar_one() == 3


async def test_lock_from_telegram_matches_paired_ids_only(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    await _assistant_world(db, org)
    for kw in ({"tg_user_id": "999", "chat_id": CHAT},            # người lạ trong đúng chat
               {"tg_user_id": TG_USER, "chat_id": "12345"},       # đúng người, sai chat
               {"tg_user_id": None, "chat_id": CHAT},
               {"tg_user_id": "", "chat_id": ""}):
        res = await backup.lock_assistant(db, org, via="telegram", **kw)
        assert not res.ok and res.code == "NOT_OWNER", kw
    assert await _state(db, org) == UNLOCKED
    ok = await backup.lock_assistant(db, org, via="telegram", tg_user_id=TG_USER, chat_id=CHAT)
    await db.commit()
    assert ok.ok and (await _state(db, org))["locked"] is True


async def test_lock_from_telegram_denied_when_not_paired(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org, paired=False)
    res = await backup.lock_assistant(db, org, via="telegram", tg_user_id=TG_USER, chat_id=CHAT)
    assert not res.ok and res.code == "NOT_OWNER"


async def test_lock_from_owner_channel_by_id_not_by_name(db: Any) -> None:
    org = await mk_org(db)
    await _assistant_world(db, org, owner_ext="zl-owner-1")
    await db.execute(text("UPDATE ops.owner_channels SET owner_display = 'Anh Ryan' WHERE org_id = :o"), {"o": org})
    await db.commit()
    # người khác tự đặt tên hiển thị trùng tên Sếp: chỉ ID mới được tính
    for ext in ("zl-impostor", "Anh Ryan", "", None):
        res = await backup.lock_assistant(db, org, via="owner_channel", channel="zalo", sender_external_id=ext)
        assert not res.ok and res.code == "NOT_OWNER", ext
    assert await _state(db, org) == UNLOCKED
    ok = await backup.lock_assistant(db, org, via="owner_channel", channel="zalo", sender_external_id="zl-owner-1")
    await db.commit()
    assert ok.ok and (await _state(db, org))["locked"] is True


async def test_lock_unknown_via_denied(db: Any) -> None:
    org = await mk_org(db)
    res = await backup.lock_assistant(db, org, via="web", user_id=uuid.uuid4())
    assert not res.ok and res.code == "BAD_VIA"


async def test_unlock_only_console_owner_and_does_not_reenable_channels(db: Any) -> None:
    org = await mk_org(db)
    await _assistant_world(db, org)
    owner = await _user(db, org, "owner", "o@example.vn")
    staff = await _user(db, org, "staff", "s@example.vn")
    await backup.lock_assistant(db, org, via="console", user_id=owner)
    await db.commit()
    assert (await backup.unlock_assistant(db, org, user_id=staff)).ok is False
    assert (await _state(db, org))["locked"] is True
    assert (await backup.unlock_assistant(db, org, user_id=owner)).ok is True
    await db.commit()
    st = await _state(db, org)
    assert st["locked"] is False and st["owner_channel"] is False  # bật lại kênh quản trị là bước riêng, có chủ ý của Sếp


# ─── mã chỉ tới chat riêng của Sếp ───────────────────────────────────────────

async def test_send_code_requires_paired_owner(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org, paired=False)                       # có kênh chung nhưng chưa ghép Sếp
    with pytest.raises(backup.BackupUnavailable):
        await backup.send_code(db, org)
    await db.execute(text("UPDATE ops.notify_channels SET owner_chat_id = :c"), {"c": CHAT})   # có chat nhưng thiếu from.id
    await db.commit()
    with pytest.raises(backup.BackupUnavailable):
        await backup.send_code(db, org)
    assert await _rows(db) == []
    sink = Sink()
    assert (await backup.flush(sessionmaker(), transport=sink.transport()))["sent"] == 0 and sink.sent == []


async def test_code_never_goes_to_shared_chat(db: Any, caplog: pytest.LogCaptureFixture) -> None:
    org = await mk_org(db)
    await _telegram(db, org, chat=SHARED_CHAT, owner_chat=CHAT)
    caplog.set_level(logging.DEBUG)
    h = await backup.send_code(db, org)
    await backup.notify(db, org, "sự cố", dedupe_key="n1")           # notify thường vẫn theo kênh cũ
    await db.commit()
    sink = Sink()
    await backup.flush(sessionmaker(), transport=sink.transport())
    by_text = {("Mã xác nhận" in m["text"]): m["chat_id"] for m in sink.sent}
    assert by_text[True] == CHAT and by_text[False] == SHARED_CHAT
    assert all(m["chat_id"] == CHAT for m in sink.sent if "Mã xác nhận" in m["text"])
    assert h not in caplog.text and TOKEN not in caplog.text


async def test_code_not_sent_if_owner_chat_changed_after_creation(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org, chat=SHARED_CHAT, owner_chat=CHAT)
    await backup.send_code(db, org)
    await db.execute(text("UPDATE ops.notify_channels SET owner_chat_id = '424242'"))     # Sếp ghép lại sang chat khác
    await db.commit()
    sink = Sink()
    await backup.flush(sessionmaker(), transport=sink.transport())
    assert sink.sent == []
    r = (await _rows(db))[0]
    assert r.failed_code == "OWNER_CHAT_CHANGED" and not any(t.isdigit() and len(t) == 4 for t in r.text.split())
