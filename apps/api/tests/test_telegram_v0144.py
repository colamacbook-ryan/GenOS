"""v0.1.44 (F-8c) — kênh "Báo động & bản tin" qua Telegram: cấu hình mã hoá, Gửi thử, Tìm chat_id, run/telegram.json.

Telegram giả bằng `httpx.MockTransport` (app.state.telegram_transport) — KHÔNG tạo bot/tài khoản Telegram thật. Token
giả đặc trưng để dò rò rỉ trong log (cả JsonFormatter), phản hồi, Action Log, ops.boss_checks."""

import base64
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

import httpx
import orjson
import psycopg
import pytest
from sqlalchemy import text

from gh import crypto
from gh.app import JsonFormatter, RedactFilter
from gh.config import get_settings
from gh.db import admin_sessionmaker
from gh.telegram import service as tsvc
from tests.conftest import PG, Api, verify_pin
from tests.test_rbac_api import login_as

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "sql" / "0029_v0144_telegram.sql"
TOKEN = "123456789:AAFakeTokenForTestOnly_abcdefghijkl"
TOKEN2 = "987654321:BBOtherFakeTokenOnly_zyxwvutsrqponm"
CHAT = "987654321"
# Vector cố định chung với `go test` của genh — chống lệch định dạng phong bì GH1.
VECTOR_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
VECTOR_BLOB = ("R0gxAQEBAQEBAQEBAQEBcDa822qwcq3EjBvydVHl/Eae0cnW0+lWvy+7VUcXKoAnR65d/L2duDWg+nq0YHK+"
               "AgICAgICAgICAgIC4Apt1NK8X3nxp5PKAE3vJSvRllbVFT6QKTuFbBwOJ4S/iowHbEK8trUT3dCImyr9FL5k9CzC"
               "tEYg8CnafG0xPbjhaU8GEmin34k+9X0cd0uL5rEAfHt9yDbxIe8FrQ4=")
VECTOR_PLAIN = b'{"token":"123456789:AAFakeTokenForTestOnly_abcdefghijkl","chat_id":"987654321"}'


class FakeTelegram:
    """Bot API giả: ghi lại mọi request; `send` = (mã HTTP, thân) cho sendMessage; `me` cho getMe; `updates`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.send: tuple[int, dict[str, Any]] = (200, {"ok": True, "result": {"message_id": 1}})
        self.me: tuple[int, dict[str, Any]] = (200, {"ok": True, "result": {"id": 1, "username": "gen_sep_bot"}})
        self.updates: list[dict[str, Any]] = []
        self.timeout = False

    def handle(self, req: httpx.Request) -> httpx.Response:
        body = orjson.loads(req.content) if req.content else None
        self.calls.append((req.url.path, body))
        if self.timeout:
            raise httpx.ConnectTimeout("timed out", request=req)
        method = req.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(self.me[0], json=self.me[1])
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": self.updates})
        return httpx.Response(self.send[0], json=self.send[1])


@pytest.fixture
def fake_tg(app: Any) -> FakeTelegram:
    f = FakeTelegram()
    app.state.telegram_transport = httpx.MockTransport(f.handle)
    return f


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "host_link_dir", str(d))
    return d


async def _save(api: Api, **body: Any) -> httpx.Response:
    return await api.send("PUT", "/notify/telegram", {"token": TOKEN, "chat_id": CHAT, **body})


async def _db_dump() -> str:
    async with admin_sessionmaker()() as s:
        rows = []
        for sql in ("SELECT * FROM ops.action_log", "SELECT * FROM ops.boss_checks",
                    "SELECT * FROM ops.telegram_outbox",
                    "SELECT org_id, chat_id, bot_username FROM ops.notify_channels"):
            rows.append((await s.execute(text(sql))).all())
        return orjson.dumps(rows, default=str).decode()


# ─── migration ──────────────────────────────────────────────────────────────

async def test_migration_0029_is_rerunnable(fresh_db: str) -> None:
    sql = SQL_FILE.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        n = c.execute("""SELECT count(*) FROM pg_policies
                         WHERE tablename IN ('notify_channels', 'telegram_outbox')""").fetchone()
    assert n is not None and n[0] == 2


# ─── PUT / GET ──────────────────────────────────────────────────────────────

async def test_put_needs_pin_and_stores_token_encrypted(owner_api: Api, fake_tg: FakeTelegram) -> None:
    r = await _save(owner_api)
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    await verify_pin(owner_api)
    r = await _save(owner_api, briefing=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True and body["enabled"] is True and body["briefing"] is False
    assert body["bot_username"] == "gen_sep_bot" and body["chat_id_masked"] == "•••4321"
    assert TOKEN not in r.text and CHAT not in r.text
    assert fake_tg.calls[0][0] == f"/bot{TOKEN}/getMe"
    async with admin_sessionmaker()() as s:
        row = (await s.execute(text("SELECT token_enc, chat_id FROM ops.notify_channels"))).one()
    assert TOKEN.encode() not in bytes(row.token_enc)
    assert crypto.decrypt(bytes(row.token_enc), b"telegram_token").decode() == TOKEN
    assert row.chat_id == CHAT

    g = await owner_api.get("/notify/telegram")
    assert g.status_code == 200
    assert TOKEN not in g.text and "token" not in g.json() and CHAT not in g.text
    assert set(g.json()) >= {"configured", "enabled", "bot_username", "chat_id_masked", "briefing", "reminders",
                             "updated_at", "last_test", "host"}
    # Lưu lại không kèm token: giữ token cũ, không gọi getMe lần nữa.
    n = len(fake_tg.calls)
    r = await owner_api.send("PUT", "/notify/telegram", {"chat_id": "-100123456789", "reminders": False})
    assert r.status_code == 200, r.text
    assert len(fake_tg.calls) == n and r.json()["chat_id_masked"] == "•••6789" and r.json()["briefing"] is False


async def test_put_keeps_chat_id_when_blank(owner_api: Api, fake_tg: FakeTelegram) -> None:
    """Đã cấu hình: bỏ trống chat_id (Sếp chỉ thấy •••4321) ⇒ giữ chat_id cũ; `{}` = "Lưu lại" (key_mismatch)."""
    await verify_pin(owner_api)
    assert (await _save(owner_api)).status_code == 200
    n = len(fake_tg.calls)
    r = await owner_api.send("PUT", "/notify/telegram", {"briefing": False})
    assert r.status_code == 200, r.text
    assert r.json()["chat_id_masked"] == "•••4321" and r.json()["briefing"] is False
    r = await owner_api.send("PUT", "/notify/telegram", {"chat_id": "  "})
    assert r.status_code == 200 and r.json()["chat_id_masked"] == "•••4321"
    r = await owner_api.send("PUT", "/notify/telegram", {})
    assert r.status_code == 200 and r.json()["configured"] is True and r.json()["briefing"] is False
    assert len(fake_tg.calls) == n  # không gọi getMe khi không đổi token
    async with admin_sessionmaker()() as s:
        assert (await s.execute(text("SELECT chat_id FROM ops.notify_channels"))).scalar_one() == CHAT


async def test_put_validation_and_rejected_token(owner_api: Api, fake_tg: FakeTelegram) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/notify/telegram", {"token": "abc", "chat_id": "x12"})
    assert r.status_code == 422 and set(r.json()["errors"]) == {"token", "chat_id"}
    r = await owner_api.send("PUT", "/notify/telegram", {"chat_id": CHAT})
    assert r.status_code == 422 and "token" in r.json()["errors"]
    # Chưa cấu hình: chat_id bắt buộc.
    r = await owner_api.send("PUT", "/notify/telegram", {"token": TOKEN})
    assert r.status_code == 422 and set(r.json()["errors"]) == {"chat_id"}
    fake_tg.me = (401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    r = await _save(owner_api)
    assert r.status_code == 409 and r.json()["code"] == "TELEGRAM_TOKEN_REJECTED"
    assert TOKEN not in r.text


async def test_staff_forbidden(owner_api: Api, client: httpx.AsyncClient, db: Any, fake_tg: FakeTelegram) -> None:
    staff = await login_as(client, db, "manager")
    assert (await staff.get("/notify/telegram")).status_code == 403
    assert (await staff.send("POST", "/notify/telegram/test", {})).status_code == 403


# ─── Gửi thử + boss_checks ──────────────────────────────────────────────────

async def test_send_test_payload_and_boss_check(owner_api: Api, fake_tg: FakeTelegram) -> None:
    await verify_pin(owner_api)
    assert (await _save(owner_api)).status_code == 200
    r = await owner_api.send("POST", "/notify/telegram/test", {})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["key"] == "telegram" and out["status"] == "pass" and out["host_requested"] is False
    assert out["detail"] == {"bot_username": "gen_sep_bot", "chat_masked": "•••4321"}
    path, payload = fake_tg.calls[-1]
    assert path == f"/bot{TOKEN}/sendMessage"
    assert payload is not None and set(payload) == {"chat_id", "text", "disable_web_page_preview"}
    assert payload["chat_id"] == CHAT and payload["disable_web_page_preview"] is True
    assert payload["text"].startswith("Gen-Harness · Tin thử từ Console")
    # F092: chưa bật Truy cập từ xa ⇒ không in link localhost
    assert "localhost" not in payload["text"] and "Mở Console từ máy chủ" in payload["text"]
    assert "mọi thao tác Sếp xác nhận trong Console" in payload["text"]
    ov = (await owner_api.get("/boss-checks")).json()
    assert next(x for x in ov["rows"] if x["key"] == "telegram")["done"] is True
    g = (await owner_api.get("/notify/telegram")).json()
    assert g["last_test"]["status"] == "pass" and g["last_test"]["error_code"] is None


@pytest.mark.parametrize(("status", "body", "code"), [
    (401, {"ok": False, "description": "Unauthorized"}, "TELEGRAM_TOKEN_REJECTED"),
    (404, {"ok": False, "description": "Not Found"}, "TELEGRAM_TOKEN_REJECTED"),
    (403, {"ok": False, "description": "Forbidden: bot was blocked by the user"}, "TELEGRAM_BOT_BLOCKED"),
    (400, {"ok": False, "description": "Bad Request: chat not found"}, "TELEGRAM_CHAT_NOT_FOUND"),
    (502, {"ok": False, "description": "Bad Gateway"}, "TELEGRAM_UNREACHABLE"),
])
async def test_error_mapping_recorded(owner_api: Api, fake_tg: FakeTelegram, status: int, body: dict[str, Any],
                                      code: str) -> None:
    await verify_pin(owner_api)
    assert (await _save(owner_api)).status_code == 200
    fake_tg.send = (status, body)
    r = await owner_api.send("POST", "/boss-checks/telegram/run", {})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "fail" and out["error_code"] == code and out.get("transient") is not True
    assert out["runs"] == 1 and out["message"] and TOKEN not in r.text


async def test_timeout_and_rate_limit(owner_api: Api, fake_tg: FakeTelegram) -> None:
    await verify_pin(owner_api)
    assert (await _save(owner_api)).status_code == 200
    fake_tg.timeout = True
    out = (await owner_api.send("POST", "/notify/telegram/test", {})).json()
    assert out["status"] == "fail" and out["error_code"] == "TELEGRAM_UNREACHABLE" and out["runs"] == 1
    fake_tg.timeout = False
    fake_tg.send = (429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 7}})
    out = (await owner_api.send("POST", "/notify/telegram/test", {})).json()
    assert out["transient"] is True and out["error_code"] == "TELEGRAM_RATE_LIMITED" and out["id"] is None
    ov = (await owner_api.get("/boss-checks")).json()
    assert ov["results"]["telegram"]["runs"] == 1                     # 429 không ghi bản kiểm
    assert ov["results"]["telegram"]["error_code"] == "TELEGRAM_UNREACHABLE"


async def test_not_configured(owner_api: Api, fake_tg: FakeTelegram) -> None:
    out = (await owner_api.send("POST", "/boss-checks/telegram/run", {})).json()
    assert out["status"] == "fail" and out["error_code"] == "TELEGRAM_NOT_CONFIGURED"
    assert out["message"] == "Chưa nối Telegram — làm theo hướng dẫn ở Kết nối › Telegram"
    assert out["host_requested"] is False and fake_tg.calls == []


# ─── không rò token ─────────────────────────────────────────────────────────

async def test_token_never_leaks(owner_api: Api, fake_tg: FakeTelegram, caplog: pytest.LogCaptureFixture,
                                 host: Path) -> None:
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    await verify_pin(owner_api)
    for r in [await _save(owner_api), await owner_api.get("/notify/telegram"),
              await owner_api.send("POST", "/notify/telegram/test", {}),
              await owner_api.send("POST", "/notify/telegram/find-chat", {"token": TOKEN2}),
              await owner_api.send("POST", "/notify/telegram/find-chat", {})]:
        texts.append(r.text)
    fake_tg.timeout = True
    texts.append((await owner_api.send("POST", "/notify/telegram/test", {})).text)
    fake_tg.timeout = False
    texts.append((await owner_api.send("DELETE", "/notify/telegram")).text)
    fmt = JsonFormatter()
    logs = [fmt.format(rec) for rec in caplog.records] + [rec.getMessage() for rec in caplog.records]
    dump = await _db_dump()
    host_files = "".join(p.read_text() for p in host.rglob("*.json"))  # noqa: ASYNC240
    for secret in (TOKEN, TOKEN2, TOKEN.split(":")[1], TOKEN2.split(":")[1]):
        assert all(secret not in t for t in texts), secret
        assert all(secret not in line for line in logs), secret
        assert secret not in dump and secret not in host_files, secret
    assert any(f"/bot{TOKEN}/sendMessage" == c[0] for c in fake_tg.calls)


def test_log_redaction_of_bot_token() -> None:
    rec = logging.LogRecord("gh.test", logging.ERROR, __file__, 1, "gọi https://api.telegram.org/bot%s/sendMessage",
                            (TOKEN,), None)
    rec.detail = f"token {TOKEN}"  # type: ignore[attr-defined]
    line = JsonFormatter().format(rec)
    assert TOKEN not in line and TOKEN.split(":")[1] not in line and "api.telegram.org/***/sendMessage" in line
    rec2 = logging.LogRecord("gh.test", logging.INFO, __file__, 1, "tok=%s", (TOKEN,), None)
    assert RedactFilter().filter(rec2) is True
    assert TOKEN not in logging.Formatter("%(message)s").format(rec2)


# ─── run/telegram.json + yêu cầu gửi thử cho genh ───────────────────────────

async def test_host_file_contract(owner_api: Api, fake_tg: FakeTelegram, host: Path) -> None:
    await verify_pin(owner_api)
    assert (await _save(owner_api, reminders=False)).status_code == 200
    f = host / "telegram.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o644
    data = json.loads(f.read_text())
    assert set(data) == {"schema", "enabled", "enc", "briefing", "reminders", "updated_at"}
    assert data["schema"] == 1 and data["enabled"] is True and data["briefing"] is True and data["reminders"] is False
    assert data["updated_at"].endswith("Z")
    plain = crypto.decrypt(base64.b64decode(data["enc"]), b"telegram_notify")
    assert json.loads(plain) == {"token": TOKEN, "chat_id": CHAT}
    assert plain == json.dumps({"token": TOKEN, "chat_id": CHAT}, separators=(",", ":")).encode()
    assert not list(host.glob(".*.tmp"))  # noqa: ASYNC240
    # Tắt ⇒ enabled=false (không enc).
    assert (await owner_api.send("PUT", "/notify/telegram", {"chat_id": CHAT, "enabled": False})).status_code == 200
    data = json.loads(f.read_text())
    assert data["enabled"] is False and "enc" not in data
    assert (await owner_api.send("PUT", "/notify/telegram", {"chat_id": CHAT, "enabled": True})).status_code == 200
    assert json.loads(f.read_text())["enabled"] is True
    r = await owner_api.send("DELETE", "/notify/telegram")
    assert r.status_code == 200 and r.json()["configured"] is False
    data = json.loads(f.read_text())
    assert data == {"schema": 1, "enabled": False, "updated_at": data["updated_at"]}


async def test_delete_needs_pin(owner_api: Api, fake_tg: FakeTelegram, client: httpx.AsyncClient) -> None:
    await verify_pin(owner_api)
    assert (await _save(owner_api)).status_code == 200
    fresh = Api(client.__class__(transport=client._transport, base_url="http://test"))
    from tests.conftest import OWNER

    r = await fresh.send("POST", "/auth/login", {"email": OWNER["email"], "password": OWNER["password"]})
    assert r.status_code == 200
    r = await fresh.send("DELETE", "/notify/telegram")
    assert r.status_code == 423


async def test_sync_host_file_skips_without_dir(fresh_db: str, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
    from gh.db import sessionmaker

    monkeypatch.setattr(get_settings(), "host_link_dir", str(tmp_path / "khong-co"))
    assert await tsvc.sync_host_file(sessionmaker()) is False


def test_fixed_vector_decrypts() -> None:
    mk = crypto.decode_key(VECTOR_KEY)
    assert crypto.decrypt(base64.b64decode(VECTOR_BLOB), b"telegram_notify", key=mk) == VECTOR_PLAIN
    # Phong bì do api tạo bằng cùng khoá cũng giải được theo cùng định dạng (GH1 | nonce | dek | nonce | ct).
    blob = crypto.encrypt(VECTOR_PLAIN, b"telegram_notify", key=mk)
    assert blob[:3] == b"GH1" and len(blob) == 3 + 12 + 48 + 12 + len(VECTOR_PLAIN) + 16
    assert crypto.decrypt(blob, b"telegram_notify", key=mk) == VECTOR_PLAIN


async def test_test_requests_host_and_reads_watchdog_status(owner_api: Api, fake_tg: FakeTelegram,
                                                            host: Path) -> None:
    (host / "genh.json").write_text(json.dumps({"version": "v0.1.44", "requests": ["update", "watchdog"]}))
    (host / "watchdog-status.json").write_text(json.dumps({
        "schema": 1, "last_run_at": "2026-10-03T01:02:03Z", "state": "issues",
        "incidents": [{"key": "disk.low", "severity": "bad", "title": "Ổ đĩa sắp hết chỗ",
                       "since": "2026-10-03T00:00:00Z"}, {"key": "Không hợp lệ!", "title": "x"}, "rác"],
        "telegram": "ok", "telegram_error_code": "", "last_sent_at": "", "schedule": "systemd",
        "test": {"at": "2026-10-03T01:00:00Z", "ok": True, "error_code": ""}}))
    await verify_pin(owner_api)
    assert (await _save(owner_api)).status_code == 200
    out = (await owner_api.send("POST", "/notify/telegram/test", {})).json()
    assert out["status"] == "pass" and out["host_requested"] is True
    req = json.loads((host / "request" / "watchdog.json").read_text())
    assert set(req) == {"schema", "action", "requested_at"} and req["action"] == "test" and req["schema"] == 1
    h = (await owner_api.get("/notify/telegram")).json()["host"]
    assert h["supported"] is True and h["schedule"] == "systemd" and h["state"] == "issues"
    assert h["telegram"] == "ok" and h["telegram_error_code"] is None
    assert h["incidents"] == [{"key": "disk.low", "severity": "bad", "title": "Ổ đĩa sắp hết chỗ",
                               "since": "2026-10-03T00:00:00Z"}]
    assert h["test"] == {"at": "2026-10-03T01:00:00Z", "ok": True, "error_code": None}


async def test_find_chat(owner_api: Api, fake_tg: FakeTelegram) -> None:
    fake_tg.updates = [
        {"update_id": 1, "message": {"chat": {"id": 111, "type": "private", "first_name": "Cũ"}}},
        {"update_id": 2, "message": {"chat": {"id": -100, "type": "group", "title": "Nhóm"}}},
        {"update_id": 3, "message": {"chat": {"id": int(CHAT), "type": "private", "first_name": "Anh",
                                              "last_name": "Cơ", "username": "anhco"}}},
    ]
    r = await owner_api.send("POST", "/notify/telegram/find-chat", {"token": TOKEN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error_code"] is None
    assert body["chats"] == [{"chat_id": CHAT, "name": "Anh Cơ", "username": "anhco"},
                             {"chat_id": "111", "name": "Cũ", "username": None}]
    assert fake_tg.calls[-1][0] == f"/bot{TOKEN}/getUpdates"
    async with admin_sessionmaker()() as s:
        assert (await s.execute(text("SELECT count(*) FROM ops.notify_channels"))).scalar_one() == 0  # không lưu
    # Không có token và chưa cấu hình ⇒ mã thân thiện, không gọi Telegram.
    n = len(fake_tg.calls)
    body = (await owner_api.send("POST", "/notify/telegram/find-chat", {})).json()
    assert body["error_code"] == "TELEGRAM_NOT_CONFIGURED" and len(fake_tg.calls) == n
    fake_tg.updates = []
    body = (await owner_api.send("POST", "/notify/telegram/find-chat", {"token": TOKEN})).json()
    assert body["chats"] == [] and body["message"]
    r = await owner_api.send("POST", "/notify/telegram/find-chat", {"token": "sai-dang"})
    assert r.status_code == 422 and "token" in r.json()["errors"]


# ─── vòng đời sự cố/kết quả Gửi thử theo cấu hình (review v0.1.44) ─────────────────────────────────────────────

async def _raise_failed(code: str = "TELEGRAM_BOT_BLOCKED") -> None:
    from gh import health

    async with admin_sessionmaker()() as s:
        org = (await s.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        await health.raise_once(s, org, key=tsvc.ALERT_KEY, kind=tsvc.ALERT_KEY, severity="warn",
                                title=tsvc.ALERT_TITLE, body=tsvc.ALERT_BODIES[code], link=tsvc.ALERT_LINK,
                                fingerprint=code)
        await s.commit()


async def test_delete_clears_failed_alert_and_last_test(owner_api: Api, fake_tg: FakeTelegram) -> None:
    """Tắt Telegram là đã xử lý cảnh báo telegram.failed — không còn đường gửi nào để tự đóng nó (ngõ cụt)."""
    await verify_pin(owner_api)
    assert (await _save(owner_api)).status_code == 200
    assert (await owner_api.send("POST", "/notify/telegram/test", {})).json()["status"] == "pass"
    await _raise_failed()
    issues = (await owner_api.get("/system/health")).json()["issues"]
    assert any(i["key"] == "telegram.failed" for i in issues)
    assert (await owner_api.send("DELETE", "/notify/telegram")).status_code == 200
    issues = (await owner_api.get("/system/health")).json()["issues"]
    assert not any(i["key"] == "telegram.failed" for i in issues)
    g = (await owner_api.get("/notify/telegram")).json()
    assert g["configured"] is False and g["last_test"] is None
    ov = (await owner_api.get("/boss-checks")).json()
    assert next(x for x in ov["rows"] if x["key"] == "telegram")["done"] is False
    assert ov["results"]["telegram"] is None


async def test_new_token_or_chat_forgets_last_test(owner_api: Api, fake_tg: FakeTelegram) -> None:
    """Kết quả Gửi thử thuộc cấu hình cũ: đổi token/chat_id ⇒ về "Chưa kiểm"; chỉ bật/tắt mục hoặc Lưu lại cùng token
    ⇒ giữ. Bấm Gửi thử khi CHƯA nối rồi nối ⇒ không còn dòng lỗi "Chưa nối Telegram" cũ."""
    await verify_pin(owner_api)
    out = (await owner_api.send("POST", "/boss-checks/telegram/run", {})).json()
    assert out["error_code"] == "TELEGRAM_NOT_CONFIGURED"
    assert (await _save(owner_api)).status_code == 200
    assert (await owner_api.get("/notify/telegram")).json()["last_test"] is None
    assert (await owner_api.send("POST", "/notify/telegram/test", {})).json()["status"] == "pass"
    assert (await owner_api.send("PUT", "/notify/telegram", {"briefing": False})).status_code == 200
    assert (await _save(owner_api)).status_code == 200                     # cùng token + chat_id ⇒ giữ kết quả
    assert (await owner_api.get("/notify/telegram")).json()["last_test"]["status"] == "pass"
    assert (await owner_api.send("PUT", "/notify/telegram", {"token": TOKEN2})).status_code == 200
    assert (await owner_api.get("/notify/telegram")).json()["last_test"] is None
    assert (await owner_api.send("POST", "/notify/telegram/test", {})).json()["status"] == "pass"
    assert (await owner_api.send("PUT", "/notify/telegram", {"chat_id": "-1001234"})).status_code == 200
    assert (await owner_api.get("/notify/telegram")).json()["last_test"] is None
    ov = (await owner_api.get("/boss-checks")).json()
    assert next(x for x in ov["rows"] if x["key"] == "telegram")["done"] is False


async def test_failed_alert_for_non_owner_has_no_dead_button(owner_api: Api, client: httpx.AsyncClient, db: Any,
                                                             fake_tg: FakeTelegram) -> None:
    """Thẻ Telegram chỉ Owner có ⇒ Manager thấy sự cố nhưng không có nút tới /connections#telegram (nút chết) và
    thân sự cố bảo nhờ Owner, không bảo bấm Đổi token/chat_id."""
    from gh import health

    await _raise_failed("TELEGRAM_TOKEN_REJECTED")
    owner_issue = next(i for i in (await owner_api.get("/system/health")).json()["issues"]
                       if i["key"] == "telegram.failed")
    assert owner_issue["link"] == "/connections#telegram" and owner_issue["action"] == "Mở cấu hình Telegram"
    await db.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                             SELECT id, 'system.read', 'all' FROM core.roles WHERE code = 'manager'
                             ON CONFLICT (role_id, permission_code) DO UPDATE SET scope = 'all'"""))
    await db.commit()
    manager = await login_as(client, db, "manager")
    r = await manager.get("/system/health")
    assert r.status_code == 200, r.text
    issue = next(i for i in r.json()["issues"] if i["key"] == "telegram.failed")
    assert issue["link"] is None and issue["action"] == "Nhờ Owner xử lý"
    assert issue["body"] == health.NON_OWNER_BODIES[("telegram.failed", "TELEGRAM_TOKEN_REJECTED")]
    assert "Đổi token" not in issue["body"] and "nhờ Owner" in issue["body"]
