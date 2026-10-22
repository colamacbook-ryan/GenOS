"""v0.1.40 (F-12): /system/offsite — Bản sao ngoài máy qua hộp thư genh, Bộ khôi phục, Tải gói mang đi.

Hộp thư run/ (0777) là dữ liệu KHÔNG tin cậy: state/error_code lạ ⇒ 'unknown', thông điệp do API ghép từ bảng cố
định. Khoá khôi phục chỉ rời máy chủ khi Owner + PIN, không bao giờ vào nhật ký thao tác hay argv tiến trình con.
"""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from gh.config import get_settings
from gh.db import admin_sessionmaker
from gh.system_api import offsite
from tests.conftest import OWNER, Api
from tests.test_rbac_api import login_as

KEY = "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY-23456"


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Hộp thư genh có watcher nhận update/restore/offsite + tệp Khoá khôi phục."""
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "genh.json").write_text(json.dumps({"version": "v0.1.40", "updater": "systemd",
                                             "requests": ["update", "restore", "offsite"]}))
    key_file = tmp_path / "gh_offsite_key"
    key_file.write_text(KEY + "\n")
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    monkeypatch.setenv("GH_OFFSITE_KEY_FILE", str(key_file))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _status(d: Path, **kw: Any) -> None:
    base = {"schema": 1, "configured": True, "dest": "/media/usb/gen-harness", "state": "ok", "error_code": "",
            "last_attempt_at": "", "last_success_at": "", "last_file": "x.ghbundle", "last_size_bytes": 1234,
            "verified": True, "kept": 2, "schedule": "systemd", "key_id": "0badc0de"}
    (d / "offsite-status.json").write_text(json.dumps({**base, **kw}))


async def _pin(api: Api) -> None:
    assert (await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 200


async def _log(action: str) -> list[dict[str, Any]]:
    async with admin_sessionmaker()() as db:
        rows = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = :a ORDER BY at"),
                                 {"a": action})).scalars().all()
    return [dict(r) for r in rows]


async def _all_log_text() -> str:
    async with admin_sessionmaker()() as db:
        rows = (await db.execute(text("SELECT action, detail::text, coalesce(target_label, '') FROM ops.action_log")
                                 )).all()
    return "\n".join(" ".join(map(str, r)) for r in rows)


# ─── GET ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_get_not_configured(owner_api: Api, link: Path) -> None:
    r = await owner_api.get("/system/offsite")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["configured"] is False and b["state"] == "not_configured" and b["dest"] is None
    assert b["stale"] is True and b["last_success_at"] is None and b["age_days"] is None
    assert b["message"] == "Chưa chọn nơi lưu bản sao ngoài máy"
    assert b["request"] == {"state": "idle", "action": None, "requested_at": None}
    assert b["can_request"] is True and b["key_present"] is True
    assert b["manual_command"] is None                                   # không có yêu cầu chờ ⇒ không có lệnh


async def test_get_without_host_link(owner_api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(tmp_path / "khong-co"))
    monkeypatch.setenv("GH_OFFSITE_KEY_FILE", str(tmp_path / "khong-co-khoa"))
    get_settings.cache_clear()
    b = (await owner_api.get("/system/offsite")).json()
    get_settings.cache_clear()
    assert b["can_request"] is False and b["key_present"] is False and b["state"] == "not_configured"


async def test_get_ok(owner_api: Api, link: Path) -> None:
    now = datetime.now(UTC)
    _status(link, last_attempt_at=_iso(now - timedelta(days=2)), last_success_at=_iso(now - timedelta(days=2)))
    b = (await owner_api.get("/system/offsite")).json()
    assert b["configured"] is True and b["state"] == "ok" and b["error_code"] is None
    assert b["dest"] == "/media/usb/gen-harness"
    assert b["age_days"] == 2 and b["stale"] is False
    assert b["last_size_bytes"] == 1234 and b["verified"] is True
    assert b["key_id"] == "0badc0de" and b["schedule"] == "systemd"
    assert b["message"] == "Bản sao ngoài máy gần nhất đã kiểm đọc lại được"


async def test_get_failed_not_mounted(owner_api: Api, link: Path) -> None:
    now = datetime.now(UTC)
    _status(link, state="not_mounted", error_code="GH-EB01", last_attempt_at=_iso(now),
            last_success_at=_iso(now - timedelta(days=9)))
    b = (await owner_api.get("/system/offsite")).json()
    assert b["state"] == "not_mounted" and b["error_code"] == "GH-EB01" and b["stale"] is True
    assert b["message"].startswith("Chưa thấy ổ USB/NAS")


async def test_get_failed_request_undeletable_says_fix_permissions(owner_api: Api, link: Path) -> None:
    """v0.1.53 (F-97): genh không xoá được tệp yêu cầu (GH-E94C) ⇒ câu riêng (kiểm quyền run/request), không phải câu
    chung "chưa thành công" kèm gợi ý cắm ổ/thử lại — thử lại vẫn lỗi y như cũ."""
    _status(link, state="failed", error_code="GH-E94C", last_attempt_at=_iso(datetime.now(UTC)))
    b = (await owner_api.get("/system/offsite")).json()
    assert b["state"] == "failed" and b["error_code"] == "GH-E94C"
    assert b["message"] == ("Máy chủ không xoá được tệp yêu cầu — chưa làm gì. Nhờ người quản trị kiểm quyền thư mục "
                            "run/request trong thư mục cài đặt rồi thử lại")
    assert "cắm" not in b["message"].lower()


async def test_untrusted_values_are_filtered(owner_api: Api, link: Path) -> None:
    _status(link, state="<script>hack</script>", error_code="rm -rf /", dest="/x\n" + "a" * 500,
            schedule="evil", key_id="not-hex!", last_size_bytes="lots", message="Chữ lạ từ tệp")
    b = (await owner_api.get("/system/offsite")).json()
    assert b["state"] == "unknown" and b["error_code"] == "unknown"
    assert b["message"] == "Không đọc được trạng thái bản sao ngoài máy"
    assert "Chữ lạ" not in json.dumps(b, ensure_ascii=False)
    assert len(b["dest"]) <= 200 and "\n" not in b["dest"]
    assert b["schedule"] is None and b["key_id"] is None and b["last_size_bytes"] is None
    # state lỗi + mã lạ ⇒ thông điệp chung cố định
    _status(link, state="failed", error_code="GH-EBZZ", last_attempt_at=_iso(datetime.now(UTC)))
    b = (await owner_api.get("/system/offsite")).json()
    assert b["state"] == "failed" and b["error_code"] == "unknown"
    assert b["message"] == "Lần sao lưu ra ổ ngoài gần nhất chưa thành công"


# ─── PUT destination / POST run / disable ────────────────────────────────────────────────────────────────

async def test_destination_needs_owner_and_pin(client, db, owner_api: Api, link: Path) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                             SELECT id, 'system.manage', 'all' FROM core.roles WHERE code = 'manager'
                             ON CONFLICT (role_id, permission_code) DO UPDATE SET scope = 'all'"""))
    await db.commit()
    mgr = await login_as(client, db, "manager")
    assert (await mgr.send("PUT", "/system/offsite/destination", {"path": "/media/usb"})).status_code == 403
    assert (await mgr.send("POST", "/system/offsite/disable")).status_code == 403
    assert (await mgr.get("/system/offsite/recovery-kit")).status_code == 403
    assert (await mgr.get("/system/offsite/portable")).status_code == 403
    r = await owner_api.send("PUT", "/system/offsite/destination", {"path": "/media/usb"})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    assert not (link / "request" / "offsite.json").exists()


@pytest.mark.parametrize("path", ["media/usb", "usb\\sao-luu", "/media/usb\n/etc", "/a\x07b", "", "/" + "a" * 400,
                                  "/media/" + "ổ" * 140])
async def test_destination_rejects_bad_paths(owner_api: Api, link: Path, path: str) -> None:
    await _pin(owner_api)
    r = await owner_api.send("PUT", "/system/offsite/destination", {"path": path})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "VALIDATION" and r.json()["errors"]["path"]
    assert not (link / "request" / "offsite.json").exists()


@pytest.mark.parametrize("path", ["/media/usb/gen-harness", "D:\\GenHarness", "\\\\nas\\sao-luu"])
async def test_destination_writes_request(owner_api: Api, link: Path, path: str) -> None:
    await _pin(owner_api)
    r = await owner_api.send("PUT", "/system/offsite/destination", {"path": path})
    assert r.status_code == 202, r.text
    req = json.loads((link / "request" / "offsite.json").read_text())
    assert set(req) == {"id", "action", "path", "requested_at", "by"}
    assert req["action"] == "set" and req["path"] == path and req["requested_at"].endswith("Z")
    assert not list((link / "request").glob("*.tmp"))
    assert r.json()["request"]["state"] == "requested" and r.json()["request"]["action"] == "set"
    assert [d["path"] for d in await _log("offsite.destination_requested")] == [path]
    # đang có yêu cầu ⇒ 409
    r = await owner_api.send("POST", "/system/offsite/run")
    assert r.status_code == 409 and r.json()["code"] == "OFFSITE_IN_PROGRESS"


async def test_unavailable_without_offsite_watcher(owner_api: Api, link: Path) -> None:
    (link / "genh.json").write_text(json.dumps({"version": "v0.1.39", "updater": "systemd",
                                                "requests": ["update", "restore"]}))
    await _pin(owner_api)
    r = await owner_api.send("PUT", "/system/offsite/destination", {"path": "/media/usb"})
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "OFFSITE_UNAVAILABLE"
    assert body["title"] == "Máy chủ chưa nhận lệnh từ Console — chạy lệnh sau một lần trên máy chủ"
    assert body["manual_command"] == 'genh offsite set "/media/usb"'
    assert (await owner_api.get("/system/offsite")).json()["can_request"] is False
    # Lệnh theo đúng việc đang làm — KHÔNG bao giờ là `set "<path>"` (Owner chép nguyên văn là hỏng).
    r = await owner_api.send("POST", "/system/offsite/run")
    assert r.status_code == 409 and r.json()["manual_command"] == "genh offsite run"
    r = await owner_api.send("POST", "/system/offsite/disable")
    assert r.status_code == 409 and r.json()["manual_command"] == "genh offsite disable"
    # Đường dẫn làm lệnh đổi nghĩa trong dấu nháy kép ⇒ không ghép lệnh.
    r = await owner_api.send("PUT", "/system/offsite/destination", {"path": "/media/$(rm -rf ~)"})
    assert r.status_code == 409 and r.json()["manual_command"] is None


@pytest.mark.parametrize(("req", "cmd"), [
    ({"action": "run"}, "genh offsite run"),
    ({"action": "disable"}, "genh offsite disable"),
    ({"action": "set", "path": "/media/usb/gen"}, 'genh offsite set "/media/usb/gen"'),
    ({"action": "set", "path": "D:\\GenBackup"}, 'genh offsite set "D:\\GenBackup"'),
    ({"action": "set", "path": "/x`reboot`"}, None),
    ({"action": "set", "path": "tuong-doi"}, None),
    ({"action": "set"}, None),
    ({"action": "xoa-het"}, None),
])
async def test_get_manual_command_follows_pending_request(owner_api: Api, link: Path, req: dict[str, str],
                                                         cmd: str | None) -> None:
    """Yêu cầu bị kẹt (Windows / máy cài trước v0.1.40) ⇒ lệnh chạy tay theo action/path của CHÍNH yêu cầu đó; tệp
    run/ không tin cậy nên path qua cùng bộ lọc."""
    old = _iso(datetime.now(UTC) - timedelta(minutes=30))
    (link / "request" / "offsite.json").write_text(json.dumps({"id": "x", **req, "requested_at": old}))
    b = (await owner_api.get("/system/offsite")).json()
    assert b["request"]["state"] == "stalled"
    assert b["manual_command"] == cmd


async def test_run_writes_request_and_respects_update_restore(owner_api: Api, link: Path) -> None:
    (link / "update-status.json").write_text(json.dumps({"state": "running",
                                                         "started_at": _iso(datetime.now(UTC))}))
    (link / "genh-heartbeat.json").write_text(json.dumps({"at": _iso(datetime.now(UTC))}))
    r = await owner_api.send("POST", "/system/offsite/run")
    assert r.status_code == 409 and r.json()["code"] == "UPDATE_IN_PROGRESS"
    (link / "update-status.json").unlink()
    (link / "restore-status.json").write_text(json.dumps({"state": "running"}))
    r = await owner_api.send("POST", "/system/offsite/run")
    assert r.status_code == 409 and r.json()["code"] == "RESTORE_IN_PROGRESS"
    (link / "restore-status.json").unlink()
    _status(link, state="running", last_attempt_at=_iso(datetime.now(UTC)))
    r = await owner_api.send("POST", "/system/offsite/run")
    assert r.status_code == 409 and r.json()["code"] == "OFFSITE_IN_PROGRESS"
    _status(link, state="ok")
    r = await owner_api.send("POST", "/system/offsite/run")
    assert r.status_code == 202, r.text
    req = json.loads((link / "request" / "offsite.json").read_text())
    assert req["action"] == "run" and "path" not in req
    assert len(await _log("offsite.run_requested")) == 1


async def test_disable_writes_request(owner_api: Api, link: Path) -> None:
    await _pin(owner_api)
    r = await owner_api.send("POST", "/system/offsite/disable")
    assert r.status_code == 202, r.text
    assert json.loads((link / "request" / "offsite.json").read_text())["action"] == "disable"


# ─── Bộ khôi phục ────────────────────────────────────────────────────────────────────────────────────────────

async def test_recovery_kit_owner_pin_no_store_and_never_logged(owner_api: Api, link: Path) -> None:
    r = await owner_api.get("/system/offsite/recovery-kit")
    assert r.status_code == 423
    await _pin(owner_api)
    r = await owner_api.get("/system/offsite/recovery-kit")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    b = r.json()
    assert b["key"] == KEY
    assert b["key_id"] == hashlib.sha256(KEY.encode()).hexdigest()[:8]
    assert any("genh import --yes" in s for s in b["steps"])
    assert b["warning"] == "Cất Bộ khôi phục TÁCH khỏi ổ USB: ai có cả hai sẽ đọc được toàn bộ dữ liệu"
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", b["created_hint"])
    assert len(await _log("offsite.recovery_kit_viewed")) == 1
    assert KEY not in await _all_log_text()


async def test_recovery_kit_missing_key(owner_api: Api, link: Path, monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    monkeypatch.setenv("GH_OFFSITE_KEY_FILE", str(tmp_path / "khong-co"))
    get_settings.cache_clear()
    await _pin(owner_api)
    r = await owner_api.get("/system/offsite/recovery-kit")
    assert r.status_code == 409 and r.json()["code"] == "OFFSITE_KEY_MISSING"
    r = await owner_api.get("/system/offsite/portable")
    assert r.status_code == 409 and r.json()["code"] == "OFFSITE_KEY_MISSING"


# ─── Tải gói mang đi ────────────────────────────────────────────────────────────────────────────────────────

def _assert_same_origin_frame(r: Any) -> None:
    """Proxy Caddy đặt `?X-Frame-Options DENY` / `?CSP frame-ancestors 'none'` (chỉ khi api chưa đặt) ⇒ api phải tự
    đặt cho phép khung cùng gốc, nếu không trang lỗi JSON bị chặn trong khung tải ẩn và web không báo lỗi được."""
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'self'"


class FakeProc:
    def __init__(self, out: Path, gate: asyncio.Event | None, rc: int = 0, err: bytes = b"") -> None:
        self.out, self.gate, self.returncode, self.err = out, gate, rc, err

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.gate is not None:
            await self.gate.wait()
        if self.returncode == 0:
            self.out.write_bytes(b"GHBUNDLE1\n{}\n" + b"x" * 1000)
        return b"", self.err

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


async def test_portable_streams_bundle_with_key_in_env_only(owner_api: Api, link: Path, redis,  # type: ignore[no-untyped-def]
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    gate = asyncio.Event()
    started = asyncio.Event()

    async def fake_exec(*argv: str, env: dict[str, str], cwd: str) -> FakeProc:
        out = Path(argv[argv.index("--out") + 1])
        calls.append({"argv": list(argv), "env": env, "out": out})
        started.set()
        return FakeProc(out, gate)

    monkeypatch.setattr(offsite, "_exec", fake_exec)
    locked = await owner_api.get("/system/offsite/portable")
    assert locked.status_code == 423
    _assert_same_origin_frame(locked)  # trang lỗi PIN cũng đọc được trong khung tải cùng gốc
    await _pin(owner_api)

    first = asyncio.create_task(owner_api.get("/system/offsite/portable"))
    await asyncio.wait_for(started.wait(), 10)
    second = await owner_api.get("/system/offsite/portable")  # lần 2 song song ⇒ khoá Redis
    assert second.status_code == 409 and second.json()["code"] == "PORTABLE_IN_PROGRESS"
    _assert_same_origin_frame(second)
    gate.set()
    r = await first
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"GHBUNDLE1\n")
    assert r.headers["cache-control"] == "no-store"
    _assert_same_origin_frame(r)
    other = await owner_api.get("/system/offsite")  # đường khác: không nới — Caddy đặt DENY + frame-ancestors 'none'
    assert "x-frame-options" not in other.headers and "content-security-policy" not in other.headers
    assert re.search(r'filename="gen-harness-mang-di-\d{8}-\d{4}\.ghbundle"', r.headers["content-disposition"])

    [call] = calls
    assert call["argv"][1:] == ["-m", "gh.bundle", "export", "--out", str(call["out"])]
    assert KEY not in " ".join(call["argv"])
    assert call["env"]["GH_BUNDLE_PASSWORD"] == KEY
    assert not call["out"].exists()  # tệp tạm đã xoá sau khi gửi
    assert await redis.get(offsite.PORTABLE_LOCK_KEY) is None  # nhả khoá
    [detail] = await _log("offsite.portable_downloaded")
    assert detail["size_bytes"] == len(r.content)
    assert KEY not in await _all_log_text()


async def test_portable_failure_is_friendly_and_cleans_up(owner_api: Api, link: Path, redis,  # type: ignore[no-untyped-def]
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    outs: list[Path] = []

    async def fake_exec(*argv: str, env: dict[str, str], cwd: str) -> FakeProc:
        out = Path(argv[argv.index("--out") + 1])
        outs.append(out)
        return FakeProc(out, None, rc=1, err=f"lỗi pg_dump với {KEY}".encode())

    monkeypatch.setattr(offsite, "_exec", fake_exec)
    await _pin(owner_api)
    r = await owner_api.get("/system/offsite/portable")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "PORTABLE_FAILED" and body["title"] == "Không tạo được gói mang đi"
    _assert_same_origin_frame(r)
    assert KEY not in r.text
    assert not outs[0].exists()
    assert await redis.get(offsite.PORTABLE_LOCK_KEY) is None
    assert await _log("offsite.portable_downloaded") == []


async def test_portable_cleanup_runs_when_browser_disconnects(tmp_path: Path) -> None:
    """Trình duyệt ngắt giữa lượt tải (send ném lỗi) ⇒ vẫn xoá tệp tạm + nhả khoá (BackgroundTask thì không chạy)."""
    out = tmp_path / "goi.ghbundle"
    out.write_bytes(b"GHBUNDLE1\n" + b"x" * 200_000)
    cleaned: list[bool] = []

    async def cleanup() -> None:
        out.unlink()
        cleaned.append(True)

    resp = offsite._CleanupFileResponse(out, media_type="application/octet-stream", filename="g.ghbundle",
                                        cleanup=cleanup)
    sent: list[str] = []

    async def receive() -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        sent.append(message["type"])
        if message["type"] == "http.response.body":
            raise OSError("trình duyệt đã ngắt")

    scope = {"type": "http", "method": "GET", "headers": [], "path": "/", "query_string": b""}
    with pytest.raises(OSError):
        await resp(scope, receive, send)
    assert sent[0] == "http.response.start" and cleaned == [True] and not out.exists()
