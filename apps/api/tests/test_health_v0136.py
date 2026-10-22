"""v0.1.36 (F-6, F-3): chuông tự khử trùng lặp (ops.health_alerts) + GET /system/health + vòng theo dõi sức khoẻ.

- Kênh rớt / model hết đăng nhập: sự kiện lặp lại KHÔNG sinh chuông thứ hai; hết sự cố rồi quay lại mới có chuông mới.
- `evaluate`: ổ đĩa sắp đầy, cập nhật lỗi (fingerprint theo finished_at), quá 36 giờ chưa sao lưu, bộ xử lý nền im.
- `/system/health` cần `system.read`, phản ánh dòng sự cố đang mở; `/ready` không đổi.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import orjson
import psycopg
import pytest
from sqlalchemy import text

from gh import backup, health
from gh.chassis import objects
from gh.config import get_settings
from gh.data.ingest import handle_status
from gh.db import sessionmaker
from gh.providers.router import ModelRouter
from tests.conftest import PG, Api, verify_pin
from tests.phase2 import org_id
from tests.test_rbac_api import login_as

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "sql" / "0024_v0136_health_alerts.sql"


async def bells(db: Any, kind: str) -> list[Any]:
    await db.commit()  # đọc ảnh chụp mới nhất (các bước ghi ở session khác)
    return list((await db.execute(text("SELECT title, body, link FROM core.notifications WHERE kind = :k"),
                                  {"k": kind})).all())


async def zalo_session(db: Any, org: Any, state: str = "active", label: str = "Zalo Sếp") -> str:
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar_one()
    sid = (await db.execute(text("""
        INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at)
        VALUES (:c, :o, :l, :s, now()) RETURNING id"""),
                            {"c": ch, "o": org, "l": label, "s": state})).scalar_one()
    await db.commit()
    return str(sid)


async def status(app: Any, org: Any, type_: str, payload: dict[str, Any]) -> None:
    async with sessionmaker()() as s:
        await handle_status(s, app.state.redis, app.state.bus, org, type_, payload)
        await s.commit()


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "genh.json").write_text(json.dumps({"version": "v0.1.35", "updater": "systemd"}))
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[objects.ObjectStore]:
    monkeypatch.setenv("GH_OBJECTS_DIR", str(tmp_path / "objects"))
    get_settings.cache_clear()
    objects.reset_object_store()
    yield objects.get_object_store()
    objects.reset_object_store()
    get_settings.cache_clear()


async def evaluate(redis: Any, org: Any, *, now: datetime | None = None, started_at: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    async with sessionmaker()() as s:
        await health.evaluate(s, redis, org, now=now, started_at=started_at or now)
        await s.commit()


# ─── F-6a: kênh rớt ────────────────────────────────────────────────────────────────────────────────────────

async def test_channel_down_rings_once_and_again_after_reconnect(owner_api: Api, app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await zalo_session(db, org)
    await status(app, org, "session.ended", {"session_id": sid, "reason": "expired", "error": "token=bi-mat"})
    rows = await bells(db, "channel.down")
    assert len(rows) == 1
    assert rows[0].link == "/connections"
    assert rows[0].title == "Kênh Zalo đã ngắt kết nối"
    assert rows[0].body == "Zalo Sếp: phiên đã hết hạn — đăng nhập lại để tiếp tục nhận tin."
    assert "bi-mat" not in rows[0].body
    # bridge gửi lại (cùng lý do, rồi lý do khác) ⇒ vẫn MỘT chuông
    await status(app, org, "session.ended", {"session_id": sid, "reason": "expired"})
    await status(app, org, "session.ended", {"session_id": sid, "reason": "error"})
    assert len(await bells(db, "channel.down")) == 1
    issues = await health.active_issues(db, org)
    assert [(i["key"], i["action"]) for i in issues] == [("channel.down:zalo", "Đăng nhập lại")]

    # đăng nhập lại (phiên mới) ⇒ đóng sự cố; rớt lần nữa ⇒ chuông thứ hai
    sid2 = await zalo_session(db, org, state="pending_qr")
    await status(app, org, "session.active", {"session_id": sid2, "account": {"id": "z1", "name": "Sếp"}})
    await db.commit()
    assert await health.active_issues(db, org) == []
    await status(app, org, "session.ended", {"session_id": sid2, "reason": "logged_out"})
    rows = await bells(db, "channel.down")
    assert len(rows) == 2
    assert any("đã bị đăng xuất" in r.body for r in rows)


async def test_pending_qr_expiry_does_not_ring(owner_api: Api, app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await zalo_session(db, org, state="pending_qr")
    await status(app, org, "session.ended", {"session_id": sid, "reason": "expired"})
    assert await bells(db, "channel.down") == []


async def test_console_logout_does_not_ring(owner_api: Api, app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await zalo_session(db, org)
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/channels/zalo/logout")
    assert r.status_code == 204, r.text
    await status(app, org, "session.ended", {"session_id": sid, "reason": "logged_out"})
    assert await bells(db, "channel.down") == []


# ─── F-6b: model cần đăng nhập lại ─────────────────────────────────────────────────────────────────────────

async def test_model_auth_expired_rings_once(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    p = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                  VALUES (:o, 'openai_compat', 'Alpha', 'https://alpha.test/v1', 1)
                                  RETURNING id, name"""), {"o": org})).one()
    await db.commit()
    r = ModelRouter(sessionmaker(), redis)
    await r._set_auth_state(p, "expired")
    await r._set_auth_state(p, "expired")
    rows = await bells(db, "model.auth_expired")
    assert len(rows) == 1
    # F007 (v0.1.62): 'openai_compat' là khoá API ⇒ "thêm khoá mới" → API & Model (CLI: xem test_health_bridge_dlq).
    assert rows[0].link == "/api" and rows[0].title == "Khoá API của Alpha bị từ chối — thêm khoá mới"
    issues = await health.active_issues(db, org)
    assert issues[0]["severity"] == "warn" and issues[0]["action"] == "Thêm khoá API mới"
    await r._set_auth_state(p, "ok")
    assert await health.active_issues(db, org) == []
    await r._set_auth_state(p, "expired")
    assert len(await bells(db, "model.auth_expired")) == 2
    # nhà cung cấp bị tắt ⇒ vòng theo dõi dọn dòng sự cố cũ
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE id = :i"), {"i": p.id})
    await db.commit()
    await evaluate(redis, org)
    assert await health.active_issues(db, org) == []


async def test_model_alert_failure_never_breaks_routing(owner_api: Api, app, db, redis,  # type: ignore[no-untyped-def]
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Chuông/sự cố lỗi ⇒ `_set_auth_state` không ném (định tuyến model không hỏng), auth_state vẫn được ghi; vòng
    theo dõi mở bù sự cố sau đó."""
    org = await org_id(db)
    p = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                  VALUES (:o, 'openai_compat', 'Gamma', 'https://gamma.test/v1', 1)
                                  RETURNING id, name"""), {"o": org})).one()
    await db.commit()

    async def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("notifications down")

    monkeypatch.setattr(health, "raise_model_expired", boom)
    monkeypatch.setattr(health, "clear", boom)
    r = ModelRouter(sessionmaker(), redis)
    await r._set_auth_state(p, "expired")
    await db.commit()
    state = (await db.execute(text("SELECT auth_state FROM agent.providers WHERE id = :i"), {"i": p.id})).scalar_one()
    assert state == "expired"
    assert await bells(db, "model.auth_expired") == []
    await r._set_auth_state(p, "ok")
    await db.commit()
    state = (await db.execute(text("SELECT auth_state FROM agent.providers WHERE id = :i"), {"i": p.id})).scalar_one()
    assert state == "ok"


async def test_channel_down_clears_when_channel_gone_or_bridge_disabled(owner_api: Api, app, db,  # type: ignore[no-untyped-def]
                                                                        redis) -> None:
    """Sếp xoá kênh / tắt plugin cầu nối thay vì đăng nhập lại ⇒ dòng "Đăng nhập lại" không treo mãi."""
    org = await org_id(db)
    async with sessionmaker()() as s:
        await health.raise_once(s, org, key="channel.down:nochannel", kind="channel.down", severity="bad",
                                title="Kênh cũ đã ngắt kết nối", body="…", link="/system?tab=channels")
        await s.commit()
    plugin = (await db.execute(text("""INSERT INTO ops.plugins (package, name, layer, origin, version, is_enabled)
                                       VALUES ('@test/bridge-v0136', 'Cầu nối thử', 'channel', 'local_file', '1.0.0',
                                               true) RETURNING id"""))).scalar_one()
    await db.execute(text("""INSERT INTO core.channels (org_id, type, name, plugin_id)
                             VALUES (:o, 'bridgetest', 'Kênh thử', :p)"""), {"o": org, "p": plugin})
    await db.commit()
    async with sessionmaker()() as s:
        await health.raise_once(s, org, key="channel.down:bridgetest", kind="channel.down", severity="bad",
                                title="Kênh thử đã ngắt kết nối", body="…", link="/system?tab=channels")
        await s.commit()
    await evaluate(redis, org)
    assert [i["key"] for i in await health.active_issues(db, org)] == ["channel.down:bridgetest"]
    await db.execute(text("UPDATE ops.plugins SET is_enabled = false WHERE id = :i"), {"i": plugin})
    await db.commit()
    await evaluate(redis, org)
    assert await health.active_issues(db, org) == []


async def test_model_already_expired_is_picked_up_by_watch(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Nhà cung cấp đã 'expired' từ trước v0.1.36 (hoặc do nút "Gọi thử") — vòng theo dõi vẫn mở sự cố, một chuông."""
    org = await org_id(db)
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank,
                                                                 auth_state)
                                    VALUES (:o, 'openai_compat', 'Beta', 'https://beta.test/v1', 1, 'expired')
                                    RETURNING id"""), {"o": org})).scalar_one()
    await db.commit()
    await evaluate(redis, org)
    await evaluate(redis, org)
    rows = await bells(db, "model.auth_expired")
    assert len(rows) == 1 and rows[0].title == "Khoá API của Beta bị từ chối — thêm khoá mới"
    assert [i["key"] for i in await health.active_issues(db, org)] == [f"model.auth_expired:{pid}"]


# ─── evaluate: ổ đĩa, cập nhật ─────────────────────────────────────────────────────────────────────────────

async def test_disk_low_rings_once_then_clears(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    (link / "disk-status.json").write_text(json.dumps({
        "state": "low", "free_bytes": 3 << 30, "min_bytes": 5 << 30, "path": "/srv/bi-mat/docker",
        "checked_at": "2026-10-01T00:00:00Z"}))
    for _ in range(3):
        await evaluate(redis, org)
    rows = await bells(db, "disk.low")
    assert len(rows) == 1
    assert rows[0].title == "Ổ đĩa sắp hết chỗ"
    assert rows[0].body == "Còn 3,0 GB trống, cần tối thiểu 5,0 GB — cập nhật tự động đang tạm dừng."
    assert "/srv" not in rows[0].body and rows[0].link == "/system?tab=storage"
    (link / "disk-status.json").write_text(json.dumps({"state": "ok", "free_bytes": 50 << 30,
                                                       "min_bytes": 5 << 30}))
    await evaluate(redis, org)
    await db.commit()
    cleared = (await db.execute(text("SELECT cleared_at FROM ops.health_alerts WHERE key = 'disk.low'"))).scalar_one()
    assert cleared is not None
    assert len(await bells(db, "disk.low")) == 1


async def test_update_failed_rings_per_attempt(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    status_file = link / "update-status.json"
    status_file.write_text(json.dumps({"state": "failed", "from": "v0.1.35", "to": "v0.1.36",
                                       "finished_at": _iso(datetime.now(UTC) - timedelta(hours=3)),
                                       "message": "lỗi migrate"}))
    (link / "update-blocked.json").write_text(json.dumps({"version": "v0.1.36"}))
    await evaluate(redis, org)
    await evaluate(redis, org)
    rows = await bells(db, "update.failed")
    assert len(rows) == 1
    assert rows[0].title == "Cập nhật lên v0.1.36 chưa thành công"
    assert rows[0].body == "Hệ thống đã tự quay về bản cũ, dữ liệu an toàn. Bấm để xem và thử lại."
    # lần thử khác (finished_at mới) ⇒ thêm đúng 1 chuông
    status_file.write_text(json.dumps({"state": "failed", "from": "v0.1.35", "to": "v0.1.36",
                                       "finished_at": _iso(datetime.now(UTC) - timedelta(hours=1))}))
    (link / "update-blocked.json").write_text(json.dumps({"version": "v0.1.36", "rollback_failed": True}))
    await evaluate(redis, org)
    await evaluate(redis, org)
    rows = await bells(db, "update.failed")
    assert len(rows) == 2
    assert any(r.body == "Tự quay về bản cũ cũng lỗi — cần hỗ trợ ngay." for r in rows)
    status_file.write_text(json.dumps({"state": "done", "to": "v0.1.36"}))
    await evaluate(redis, org)
    await db.commit()
    assert await health.active_issues(db, org) == []


async def test_update_failed_older_than_26h_is_not_an_issue(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    """Thẻ cập nhật ở web thôi báo lỗi sau 26 giờ (updateModel.ts RECENT_MS) ⇒ dải/chuông/thẻ Sức khoẻ cũng vậy."""
    org = await org_id(db)
    status_file = link / "update-status.json"
    status_file.write_text(json.dumps({"state": "failed", "from": "v0.1.35", "to": "v0.1.36",
                                       "finished_at": _iso(datetime.now(UTC) - timedelta(hours=23))}))
    await evaluate(redis, org)
    assert [i["key"] for i in await health.active_issues(db, org)] == ["update.failed"]
    # cùng lần lỗi đó, giờ đã 27 giờ ⇒ đóng sự cố, /system/health không còn báo lỗi
    status_file.write_text(json.dumps({"state": "failed", "from": "v0.1.35", "to": "v0.1.36",
                                       "finished_at": _iso(datetime.now(UTC) - timedelta(hours=27))}))
    await evaluate(redis, org)
    await db.commit()
    assert await health.active_issues(db, org) == []
    body = (await owner_api.get("/system/health")).json()
    assert body["update"]["state"] == "failed" and body["update"]["failed"] is False
    assert len(await bells(db, "update.failed")) == 1


# ─── F-3: quá 36 giờ chưa sao lưu ──────────────────────────────────────────────────────────────────────────

def _entry(hours_ago: float, trigger: str) -> backup.BackupEntry:
    at = datetime.now(UTC) - timedelta(hours=hours_ago)
    return backup.BackupEntry(key=f"backups/{at:%Y%m%dT%H%M%SZ}.enc", taken_at=at, database="gh", size_bytes=1,
                              sha256="x", trigger=trigger)


async def test_backup_stale_rings_and_clears(owner_api: Api, app, db, redis, store) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await evaluate(redis, org)  # chưa cấu hình lịch sao lưu ⇒ không chuông
    assert await bells(db, "backup.stale") == []
    await db.execute(text("""UPDATE core.organizations SET settings = settings ||
                             '{"backup": {"frequency": "daily", "time_of_day": "02:00"}}'::jsonb WHERE id = :o"""),
                     {"o": org})
    await db.commit()
    await backup._write_manifest(store, [_entry(37, "pre-update")])
    await evaluate(redis, org)
    await evaluate(redis, org)
    rows = await bells(db, "backup.stale")
    assert len(rows) == 1
    assert rows[0].title == "Đã hơn 36 giờ chưa có bản sao lưu mới"
    assert rows[0].body.startswith("Bản gần nhất lúc ") and rows[0].link == "/system?tab=storage&focus=backup"
    assert "Mở mục Sao lưu và bấm Sao lưu ngay" in rows[0].body
    issues = await health.active_issues(db, org)
    # Nút trên dải chỉ MỞ mục Sao lưu (không tự chạy sao lưu) ⇒ nhãn nói đúng việc nút làm.
    assert [i["action"] for i in issues] == ["Mở mục Sao lưu"]

    # bản pre-update 2 giờ trước CŨNG tính ⇒ không chuông thêm, sự cố đóng
    await backup._write_manifest(store, [_entry(37, "scheduled"), _entry(2, "pre-update")])
    await evaluate(redis, org)
    await db.commit()
    assert len(await bells(db, "backup.stale")) == 1
    assert await health.active_issues(db, org) == []
    r = await owner_api.get("/system/health")
    b = r.json()["backup"]
    assert b["configured"] is True and b["stale"] is False and b["age_hours"] == pytest.approx(2, abs=0.2)
    assert b["frequency"] == "daily" and b["stale_after"] == "36 giờ"


async def test_backup_stale_follows_weekly_frequency(owner_api: Api, app, db, redis, store) -> None:  # type: ignore[no-untyped-def]
    """Lịch hằng tuần: bản gần nhất 3 ngày trước là bình thường; quá một tuần + 12 giờ mới báo."""
    org = await org_id(db)
    await db.execute(text("""UPDATE core.organizations SET settings = settings ||
                             '{"backup": {"frequency": "weekly", "time_of_day": "02:00"}}'::jsonb WHERE id = :o"""),
                     {"o": org})
    await db.commit()
    await backup._write_manifest(store, [_entry(72, "scheduled")])
    await evaluate(redis, org)
    assert await bells(db, "backup.stale") == []
    b = (await owner_api.get("/system/health")).json()["backup"]
    assert b["stale"] is False and b["frequency"] == "weekly" and b["stale_after"] == "một tuần"

    await backup._write_manifest(store, [_entry(8 * 24 + 1, "scheduled")])
    await evaluate(redis, org)
    rows = await bells(db, "backup.stale")
    assert len(rows) == 1 and rows[0].title == "Đã hơn một tuần chưa có bản sao lưu mới"
    assert (await owner_api.get("/system/health")).json()["backup"]["stale"] is True


def test_backup_stale_limits_per_frequency() -> None:
    now = datetime.now(UTC)
    assert not health._backup_stale(True, now - timedelta(days=20), None, now, "monthly")
    assert health._backup_stale(True, now - timedelta(days=32), None, now, "monthly")
    assert health._backup_stale(True, now - timedelta(hours=37), None, now, "daily")
    assert health._backup_stale(True, now - timedelta(hours=37), None, now, "khác")  # giá trị lạ ⇒ hằng ngày
    assert not health._backup_stale(True, now - timedelta(hours=37), None, now, "weekly")


# ─── bộ xử lý nền + GET /system/health ─────────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def test_worker_heartbeat_states(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    now = datetime.now(UTC)
    # chưa từng thấy + api vừa khởi động ⇒ 'unknown', không chuông
    r = await owner_api.get("/system/health")
    assert r.status_code == 200, r.text
    assert r.json()["worker"]["state"] == "unknown"
    await evaluate(redis, org, now=now, started_at=now - timedelta(minutes=1))
    assert await bells(db, "worker.silent") == []

    await redis.set(health.HEARTBEAT_KEY, _iso(now))
    await redis.set(f"{health.CRON_LAST_PREFIX}scheduled_backup_scan",
                    orjson.dumps({"at": _iso(now - timedelta(minutes=3)), "ok": True, "ms": 12}))
    await redis.sadd(health.CRON_NAMES_KEY, "scheduled_backup_scan")  # như worker._tracked
    body = (await owner_api.get("/system/health")).json()
    assert body["worker"]["state"] == "ok" and body["worker"]["last_seen_at"] is not None
    assert body["crons"] == [{"name": "scheduled_backup_scan", "last_at": _iso(now - timedelta(minutes=3)),
                              "ok": True}]

    await redis.set(health.HEARTBEAT_KEY, _iso(now - timedelta(minutes=11)))
    await redis.delete(f"{health.CRON_LAST_PREFIX}scheduled_backup_scan")
    body = (await owner_api.get("/system/health")).json()
    assert body["worker"]["state"] == "silent" and body["worker"]["silent_minutes"] == 11
    assert body["overall"] == "bad"
    await evaluate(redis, org)
    await evaluate(redis, org)
    rows = await bells(db, "worker.silent")
    assert len(rows) == 1 and rows[0].title.startswith("Bộ xử lý nền đã ngừng ")
    assert rows[0].link == "/system?tab=storage"
    # thấy lại ⇒ đóng sự cố
    await redis.set(health.HEARTBEAT_KEY, _iso(datetime.now(UTC)))
    await evaluate(redis, org)
    await db.commit()
    assert await health.active_issues(db, org) == []


async def test_system_health_shape_permissions_and_ready(owner_api: Api, app, client, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    async with sessionmaker()() as s:
        assert await health.raise_once(s, org, key="worker.silent", kind="worker.silent", severity="bad",
                                       title="Bộ xử lý nền đã ngừng 12 phút", body="…", link="/system?tab=storage")
        assert not await health.raise_once(s, org, key="worker.silent", kind="worker.silent", severity="bad",
                                           title="Bộ xử lý nền đã ngừng 13 phút", body="…",
                                           link="/system?tab=storage")
        await s.commit()
    await redis.xadd("gh.test.inbound.dlq", {"error": "x"})
    r = await owner_api.get("/system/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"checked_at", "overall", "worker", "browser", "queues", "crons", "backup", "update", "disk",
                         "issues"}
    assert body["overall"] == "bad"
    assert body["browser"] == {"state": "off", "last_heartbeat_at": None}
    assert {"stream": "gh.test.inbound", "dlq": 1} in body["queues"]
    assert body["update"]["state"] == "unknown" and body["disk"]["state"] == "unknown"
    [issue] = body["issues"]
    assert issue["key"] == "worker.silent" and issue["action"] == "Xem sức khoẻ"
    assert issue["title"] == "Bộ xử lý nền đã ngừng 13 phút"  # làm mới nội dung, không chuông thứ hai
    assert isinstance(issue["raised_at"], str)
    assert len(await bells(db, "worker.silent")) == 1

    ready = await client.get("/api/v1/ready")
    assert "worker" not in ready.json()

    manager = await login_as(client, db, "manager")
    assert (await manager.get("/system/health")).status_code == 403


async def test_migration_0024_is_rerunnable(fresh_db: str) -> None:
    sql = SQL_FILE.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        n = c.execute("SELECT count(*) FROM pg_policies WHERE tablename = 'health_alerts'").fetchone()
    assert n is not None and n[0] == 1
