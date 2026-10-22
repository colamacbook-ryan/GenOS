"""v0.1.47 (F-83) — kiểm phiên Facebook hằng ngày → sự cố + chuông; Telegram qua genh watchdog (api-health.json).

Worker GIẢ như test_social.py: đọc việc từ gh:browser:jobs (kiểm chữ ký), trả kết quả đã ký qua handle_result.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from gh import health
from gh.config import get_settings
from gh.db import sessionmaker
from gh.social import service as social
from gh.social import session_watch
from gh.worker import JOB_LABELS, WorkerSettings
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_rbac_api import login_as
from tests.test_social import _add, _deliver, _jobs, _login, _result

NOON_VN = datetime(2026, 10, 1, 2, 10, tzinfo=UTC)      # 09:10 giờ VN
NIGHT_VN = datetime(2026, 10, 1, 16, 10, tzinfo=UTC)    # 23:10 giờ VN (giờ yên lặng)


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "genh.json").write_text(json.dumps({"version": "v0.1.47", "updater": "systemd"}))
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


async def _check(redis: Redis, now: datetime = NOON_VN) -> int:
    async with sessionmaker()() as s:
        n = await session_watch.daily_check(s, redis, now)
        await s.commit()
    return n


async def _evaluate(redis: Redis) -> None:
    async with sessionmaker()() as s:
        await session_watch.evaluate_alerts(s, await org_id(s), redis)
        await s.commit()


async def _health_jobs(redis: Redis) -> list[dict[str, Any]]:
    return [j for j in await _jobs(redis) if j["kind"] == "health"]


async def _alerts(db: Any) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("""SELECT key, kind, severity, link, fingerprint FROM ops.health_alerts
                                          WHERE cleared_at IS NULL"""))).all())


async def _bells(db: Any) -> int:
    await db.commit()
    return int((await db.execute(text("SELECT count(*) FROM core.notifications "
                                      "WHERE kind = 'social.session_expired'"))).scalar_one())


async def _fail_health(redis: Redis, code: str) -> None:
    job = (await _health_jobs(redis))[-1]
    assert await _deliver(redis, _result(job, "failed", {"code": code})) == "ok"


async def test_daily_check_enqueues_health_per_active_account(owner_api: Api, redis: Redis, db: Any) -> None:
    a = await _add(owner_api, "FB một")
    await _login(owner_api, redis, a["id"])
    b = await _add(owner_api, "FB hai")
    await _login(owner_api, redis, b["id"])
    assert await _check(redis) == 2
    jobs = await _health_jobs(redis)       # _jobs đã kiểm chữ ký bằng khoá browser
    assert len(jobs) == 2 and {j["account_id"] for j in jobs} == {a["id"], b["id"]}
    assert all(j["payload"].get("state") for j in jobs)
    await db.commit()
    via = (await db.execute(text("SELECT via FROM agent.browser_jobs WHERE kind = 'health'"))).scalars().all()
    assert via == ["schedule", "schedule"]
    log = (await db.execute(text("SELECT actor_id FROM ops.action_log WHERE action = 'social.session_check'"))
           ).scalars().all()
    assert log == ["system:social-session-check"] * 2
    assert await _check(redis) == 0        # đang bận (mỗi tài khoản một việc một lúc)


async def test_daily_check_skips_recent_done_and_limit(owner_api: Api, redis: Redis, db: Any) -> None:
    a = await _add(owner_api, "FB một")
    await _login(owner_api, redis, a["id"])
    b = await _add(owner_api, "FB hai")
    await _login(owner_api, redis, b["id"])
    # a: vừa có việc read xong (phiên vừa được kiểm); b: đã đạt trần health trong 24 giờ.
    await db.execute(text("""INSERT INTO agent.browser_jobs (org_id, account_id, kind, via, status, finished_at)
                             SELECT org_id, id, 'read', 'gen', 'done', now() - interval '2 hours'
                             FROM core.social_accounts WHERE id = :a"""), {"a": a["id"]})
    for _ in range(social.HEALTH_PER_DAY_MAX):
        await db.execute(text("""INSERT INTO agent.browser_jobs (org_id, account_id, kind, via, status, error)
                                 SELECT org_id, id, 'health', 'schedule', 'failed', 'ERROR'
                                 FROM core.social_accounts WHERE id = :a"""), {"a": b["id"]})
    await db.commit()
    assert await _check(redis) == 0
    # Việc xong từ 21 giờ trước thì không còn tính là "vừa kiểm".
    await db.execute(text("UPDATE agent.browser_jobs SET finished_at = now() - interval '21 hours' "
                          "WHERE kind = 'read' AND status = 'done'"))
    await db.commit()
    assert await _check(redis) == 1


async def test_daily_check_halted_and_quiet_hours(owner_api: Api, redis: Redis) -> None:
    a = await _add(owner_api)
    await _login(owner_api, redis, a["id"])
    assert await _check(redis, NIGHT_VN) == 0
    r = await owner_api.send("POST", "/social/halt", {})
    assert r.status_code == 200, r.text
    assert await _check(redis) == 0
    assert not await _health_jobs(redis)


async def test_expired_session_opens_one_incident_and_one_bell(owner_api: Api, redis: Redis, db: Any) -> None:
    a = await _add(owner_api)
    await _login(owner_api, redis, a["id"])
    assert await _check(redis) == 1
    await _fail_health(redis, "LOGGED_OUT")
    assert (await owner_api.get(f"/social/accounts/{a['id']}")).json()["status"] == "needs_login"
    await _evaluate(redis)
    alerts = await _alerts(db)
    assert [(x.key, x.kind, x.severity, x.link) for x in alerts] == [
        (f"social.session:{a['id']}", "social.session_expired", "bad", "/social")]
    assert await _bells(db) == 1
    await _evaluate(redis)
    assert await _bells(db) == 1 and len(await _alerts(db)) == 1       # raise_once: không chuông thứ hai
    assert health.ACTIONS["social.session_expired"] == "Đăng nhập lại"
    assert "social.session_expired" in health.NON_OWNER_NO_LINK


async def test_incident_runs_inside_health_evaluate(owner_api: Api, redis: Redis, db: Any) -> None:
    a = await _add(owner_api)
    await _login(owner_api, redis, a["id"])
    assert await _check(redis) == 1
    await _fail_health(redis, "LOGGED_OUT")
    now = datetime.now(UTC)
    async with sessionmaker()() as s:
        await health.evaluate(s, redis, await org_id(s), now=now, started_at=now)
        await s.commit()
    assert [x.key for x in await _alerts(db) if x.kind == "social.session_expired"] == [f"social.session:{a['id']}"]


async def test_telegram_via_watchdog_snapshot_not_outbox(owner_api: Api, redis: Redis, db: Any, link: Path) -> None:
    a = await _add(owner_api)
    await _login(owner_api, redis, a["id"])
    assert await _check(redis) == 1
    await _fail_health(redis, "LOGGED_OUT")
    await db.commit()
    before = (await db.execute(text("SELECT count(*) FROM ops.telegram_outbox"))).scalar_one()
    await _evaluate(redis)
    async with sessionmaker()() as s:
        assert await health.write_host_snapshot(s, await org_id(s), now=datetime.now(UTC))
    snap = json.loads((link / health.API_HEALTH_FILE).read_text())
    keys = [x["key"] for x in snap["alerts"]]
    assert f"social.session:{a['id']}" in keys                          # genh watchdog sẽ gửi Telegram từ đây
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM ops.telegram_outbox"))).scalar_one() == before


async def test_checkpoint_and_captcha_open_incident_relogin_and_revoke_close(owner_api: Api, redis: Redis,
                                                                           db: Any) -> None:
    a = await _add(owner_api, "FB một")
    await _login(owner_api, redis, a["id"])
    b = await _add(owner_api, "FB hai")
    await _login(owner_api, redis, b["id"])
    for acc, code in ((a, "CHECKPOINT"), (b, "CAPTCHA")):
        await db.execute(text("UPDATE core.social_accounts SET status = 'paused', pause_reason = :r WHERE id = :i"),
                         {"r": code.lower(), "i": acc["id"]})
    await db.commit()
    await _evaluate(redis)
    assert {x.key for x in await _alerts(db)} == {f"social.session:{a['id']}", f"social.session:{b['id']}"}
    # Đăng nhập lại thành công → active → sự cố đóng.
    await db.execute(text("UPDATE core.social_accounts SET status = 'active', pause_reason = NULL WHERE id = :i"),
                     {"i": a["id"]})
    await db.commit()
    await _evaluate(redis)
    assert {x.key for x in await _alerts(db)} == {f"social.session:{b['id']}"}
    # Gỡ tài khoản → sự cố đóng.
    await db.execute(text("UPDATE core.social_accounts SET status = 'revoked' WHERE id = :i"), {"i": b["id"]})
    await db.commit()
    await _evaluate(redis)
    assert await _alerts(db) == []


async def test_manager_sees_ask_owner_body_for_every_fingerprint(owner_api: Api, client: httpx.AsyncClient,
                                                                 redis: Redis, db: Any) -> None:
    """Người không phải Owner không mở được /social ⇒ thân sự cố phải bảo nhờ Owner (mọi fingerprint: needs_login,
    key_changed, checkpoint, captcha…), không bảo "bấm Đăng nhập lại"."""
    accs = [await _add(owner_api, f"FB {i}") for i in range(3)]
    for a in accs:
        await _login(owner_api, redis, a["id"])
    for a, (st, reason) in zip(accs, (("needs_login", "key_changed"), ("paused", "checkpoint"),
                                      ("paused", "captcha")), strict=True):
        await db.execute(text("UPDATE core.social_accounts SET status = :s, pause_reason = :r WHERE id = :i"),
                         {"s": st, "r": reason, "i": a["id"]})
    await db.commit()
    await _evaluate(redis)
    issues = (await owner_api.get("/system/health")).json()["issues"]
    owner = [i for i in issues if i["kind"] == "social.session_expired"]
    assert len(owner) == 3 and all("bấm Đăng nhập lại" in i["body"] for i in owner)
    await db.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                             SELECT id, 'system.read', 'all' FROM core.roles WHERE code = 'manager'
                             ON CONFLICT (role_id, permission_code) DO UPDATE SET scope = 'all'"""))
    await db.commit()
    manager = await login_as(client, db, "manager")
    r = await manager.get("/system/health")
    assert r.status_code == 200, r.text
    mine = [i for i in r.json()["issues"] if i["kind"] == "social.session_expired"]
    assert len(mine) == 3
    for i in mine:
        assert i["body"] == health.NON_OWNER_KIND_BODIES["social.session_expired"]
        assert i["link"] is None and i["action"] == "Nhờ Owner xử lý"
        assert "bấm Đăng nhập lại" not in i["body"] and "nhờ Owner" in i["body"]


def test_worker_has_session_check_cron_and_label() -> None:
    crons = {c.name.removeprefix("cron:"): c for c in WorkerSettings.cron_jobs}
    c = crons["social_session_check"]
    assert c.hour == {9} and c.minute == {10}
    assert JOB_LABELS["social_session_check"] == "Kiểm phiên mạng xã hội"
