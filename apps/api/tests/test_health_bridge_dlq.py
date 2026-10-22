"""v0.1.62 (gói B5): sức khoẻ cầu nối tin nhắn, hàng lỗi có lối xử lý, sự cố model nói đúng loại nguồn.

- F051: bridge im > 2 phút (mà còn kênh "kết nối") ⇒ `bridge.silent` (chuông + dải) và khối `bridge` ở /system/health;
  có nhịp lại ⇒ đóng; chưa dùng cầu nối ⇒ không khối, không sự cố.
- F064/F102: hàng lỗi tin khách vào ⇒ `dlq.inbound` (chuông), "Phát lại" (chỉ `gh.bridge.inbound`) và "Đã xem, xoá"
  (XTRIM) cần PIN + system.manage + Action Log; hàng khác không phát lại được.
- F007: khoá API bị từ chối ⇒ "thêm khoá mới" → /api; CLI hết đăng nhập ⇒ đăng nhập lại → Kết nối.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from gh import health
from gh.chassis.bus import BRIDGE_INBOUND, DLQ_STREAMS_KEY
from gh.db import sessionmaker
from gh.providers.router import ModelRouter
from tests.conftest import Api, verify_pin
from tests.phase2 import org_id
from tests.test_rbac_api import login_as

INBOUND_DLQ = f"{BRIDGE_INBOUND}.dlq"


async def bells(db: Any, kind: str) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("SELECT title, body, link FROM core.notifications WHERE kind = :k"),
                                  {"k": kind})).all())


async def evaluate(redis: Any, org: Any, *, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    async with sessionmaker()() as s:
        await health.evaluate(s, redis, org, now=now, started_at=now)
        await s.commit()


async def zalo_session(db: Any, org: Any, *, beat_ago: timedelta | None) -> str:
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar_one()
    beat = None if beat_ago is None else datetime.now(UTC) - beat_ago
    sid = (await db.execute(text("""
        INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at, last_heartbeat_at)
        VALUES (:c, :o, 'Zalo Sếp', 'active', :b, :b) RETURNING id"""),
                            {"c": ch, "o": org, "b": beat or datetime.now(UTC) - timedelta(hours=3)})).scalar_one()
    await db.commit()
    return str(sid)


async def inbound_dlq(redis: Any, n: int) -> None:
    for i in range(n):
        await redis.xadd(INBOUND_DLQ, {"event_id": f"e{i}", "type": "message", "org_id": "", "correlation_id": f"c{i}",
                                       "actor": "bridge:zalo", "occurred_at": "2026-10-16T00:00:00+00:00",
                                       "schema_version": "1", "payload": f'{{"external_msg_id":"m{i}"}}',
                                       "error": "csdl tạm mất", "group": "api"})
    await redis.sadd(DLQ_STREAMS_KEY, INBOUND_DLQ)


# ─── F051: nhịp tim cầu nối ─────────────────────────────────────────────────────────────────────────────────

async def test_bridge_silent_rings_once_then_clears_on_beat(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await zalo_session(db, org, beat_ago=timedelta(minutes=5))
    await redis.delete(health.BRIDGE_HEARTBEAT_KEY)
    await evaluate(redis, org)
    await evaluate(redis, org)
    rows = await bells(db, "bridge.silent")
    assert len(rows) == 1 and rows[0].link == health.HEALTH_LINK
    assert "ngừng 5 phút" in rows[0].title and "KHÔNG vào được" in rows[0].body
    [issue] = [i for i in await health.active_issues(db, org) if i["kind"] == "bridge.silent"]
    assert issue["severity"] == "bad" and issue["action"] == "Xem sức khoẻ"

    r = await owner_api.get("/system/health")
    body = r.json()
    assert body["bridge"]["state"] == "silent" and body["bridge"]["silent_minutes"] >= 5
    assert body["overall"] == "bad"

    # bridge sống lại ⇒ đóng sự cố; chuông lần sau là chuông mới
    await redis.set(health.BRIDGE_HEARTBEAT_KEY, datetime.now(UTC).isoformat(), ex=45)
    await evaluate(redis, org)
    await db.commit()
    assert [i for i in await health.active_issues(db, org) if i["kind"] == "bridge.silent"] == []
    assert (await owner_api.get("/system/health")).json()["bridge"]["state"] == "ok"
    await redis.delete(health.BRIDGE_HEARTBEAT_KEY)
    await evaluate(redis, org)
    assert len(await bells(db, "bridge.silent")) == 2


async def test_bridge_grace_and_unused_bridge_are_not_incidents(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await redis.delete(health.BRIDGE_HEARTBEAT_KEY)
    # chưa có kênh nào "kết nối" và không nhịp ⇒ chưa dùng cầu nối: không sự cố, không khối
    await evaluate(redis, org)
    assert await bells(db, "bridge.silent") == []
    assert "bridge" not in (await owner_api.get("/system/health")).json()
    # nhịp phiên mới 1 phút trước (khoá 45 giây đã hết hạn) ⇒ còn trong 2 phút ân hạn
    sid = await zalo_session(db, org, beat_ago=timedelta(minutes=1))
    await evaluate(redis, org)
    assert await bells(db, "bridge.silent") == []
    assert (await owner_api.get("/system/health")).json()["bridge"]["state"] == "ok"
    # phiên đã kết thúc ⇒ không còn kênh cần cầu nối ⇒ đóng dù nhịp cũ
    await db.execute(text("UPDATE core.channel_sessions SET last_heartbeat_at = now() - interval '9 minutes' "
                          "WHERE id = :i"), {"i": sid})
    await db.commit()
    await evaluate(redis, org)
    assert len(await bells(db, "bridge.silent")) == 1
    await db.execute(text("UPDATE core.channel_sessions SET state = 'ended', ended_at = now() WHERE id = :i"),
                     {"i": sid})
    await db.commit()
    await evaluate(redis, org)
    await db.commit()
    assert [i for i in await health.active_issues(db, org) if i["kind"] == "bridge.silent"] == []


# ─── F064/F102: hàng lỗi ────────────────────────────────────────────────────────────────────────────────────

async def test_inbound_dlq_raises_alert_and_replay_moves_back(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await inbound_dlq(redis, 3)
    await evaluate(redis, org)
    await evaluate(redis, org)
    rows = await bells(db, "dlq.inbound")
    assert len(rows) == 1 and rows[0].title == "3 tin khách chưa nhập kho được"
    [issue] = [i for i in await health.active_issues(db, org) if i["kind"] == "dlq.inbound"]
    assert issue["severity"] == "warn" and issue["action"] == "Xem & phát lại"

    # cần PIN
    r = await owner_api.send("POST", f"/system/queues/{BRIDGE_INBOUND}/replay", None)
    assert r.status_code == 423, r.text
    await verify_pin(owner_api)
    before = await redis.xlen(BRIDGE_INBOUND)
    r = await owner_api.send("POST", f"/system/queues/{BRIDGE_INBOUND}/replay", None)
    assert r.status_code == 200, r.text
    assert r.json() == {"stream": BRIDGE_INBOUND, "replayed": 3, "remaining": 0}
    assert await redis.xlen(INBOUND_DLQ) == 0
    assert await redis.xlen(BRIDGE_INBOUND) == before + 3
    back = await redis.xrange(BRIDGE_INBOUND)
    # thông tin lỗi không theo tin sang đường vào
    assert all(b"error" not in f and b"group" not in f for _, f in back[-3:])
    assert back[-1][1][b"type"] == b"message" and b"external_msg_id" in back[-1][1][b"payload"]
    assert not await redis.sismember(DLQ_STREAMS_KEY, INBOUND_DLQ)
    # sự cố đóng ngay, không đợi vòng theo dõi
    await db.commit()
    assert [i for i in await health.active_issues(db, org) if i["kind"] == "dlq.inbound"] == []
    # Action Log: chỉ số lượng, không nội dung tin
    log = (await db.execute(text("""SELECT target_id, detail::text AS d FROM ops.action_log
                                    WHERE action = 'queue.replayed'"""))).all()
    assert len(log) == 1 and log[0].target_id == BRIDGE_INBOUND
    assert '"replayed": 3' in log[0].d and "external_msg_id" not in log[0].d and "csdl" not in log[0].d


async def test_replay_only_for_inbound_and_unknown_is_404(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    await redis.xadd("gh.test.other.dlq", {"error": "x"})
    await redis.sadd(DLQ_STREAMS_KEY, "gh.test.other.dlq")
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/system/queues/gh.test.other/replay", None)
    assert r.status_code == 409 and r.json()["code"] == "QUEUE_NOT_REPLAYABLE"
    assert await redis.xlen("gh.test.other.dlq") == 1  # không đụng
    r = await owner_api.send("POST", "/system/queues/gh.khong.co/replay", None)
    assert r.status_code == 404
    r = await owner_api.send("POST", "/system/queues/gh.khong.co/clear", None)
    assert r.status_code == 404


async def test_clear_trims_up_to_now_keeps_newer_and_logs(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    for i in range(4):
        await redis.xadd("gh.test.other.dlq", {"error": f"x{i}"})
    await redis.sadd(DLQ_STREAMS_KEY, "gh.test.other.dlq")
    r = await owner_api.send("POST", "/system/queues/gh.test.other/clear", None)
    assert r.status_code == 423
    assert await redis.xlen("gh.test.other.dlq") == 4
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/system/queues/gh.test.other/clear", None)
    assert r.status_code == 200, r.text
    assert r.json() == {"stream": "gh.test.other", "cleared": 4, "remaining": 0}
    assert await redis.xlen("gh.test.other.dlq") == 0
    assert not await redis.sismember(DLQ_STREAMS_KEY, "gh.test.other.dlq")
    body = (await owner_api.get("/system/health")).json()
    assert all(q["stream"] != "gh.test.other" for q in body["queues"])
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'queue.cleared' "
                               "AND target_id = 'gh.test.other'"))).scalar_one()
    assert n == 1
    # hàng lỗi inbound xoá xong ⇒ sự cố dlq.inbound đóng
    await inbound_dlq(redis, 2)
    await evaluate(redis, org)
    assert len(await bells(db, "dlq.inbound")) == 1
    r = await owner_api.send("POST", f"/system/queues/{BRIDGE_INBOUND}/clear", None)
    assert r.status_code == 200 and r.json()["cleared"] == 2
    await db.commit()
    assert [i for i in await health.active_issues(db, org) if i["kind"] == "dlq.inbound"] == []


async def test_queue_actions_need_system_manage(owner_api: Api, app, client, db, redis) -> None:  # type: ignore[no-untyped-def]
    await inbound_dlq(redis, 1)
    manager = await login_as(client, db, "manager")
    await verify_pin(manager, "112233")  # PIN của Quản lý (đặt ở login_as) — quyền vẫn bị chặn dù có PIN
    for action in ("replay", "clear"):
        r = await manager.send("POST", f"/system/queues/{BRIDGE_INBOUND}/{action}", None)
        assert r.status_code == 403, r.text
    assert await redis.xlen(INBOUND_DLQ) == 1


# ─── F007: model hết xác thực nói đúng loại nguồn ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(("kind", "title", "link", "action"), [
    ("openai_compat", "Khoá API của Alpha bị từ chối — thêm khoá mới", "/api", "Thêm khoá API mới"),
    ("gemini", "Khoá API của Alpha bị từ chối — thêm khoá mới", "/api", "Thêm khoá API mới"),
    ("claude_code_cli", "Model Alpha cần đăng nhập lại", "/connections#brain", "Đăng nhập lại model"),
    ("antigravity_cli", "Model Alpha cần đăng nhập lại", "/connections#brain", "Đăng nhập lại model"),
])
async def test_model_expired_text_and_target_follow_source_kind(owner_api: Api, app, db, redis,  # type: ignore[no-untyped-def]
                                                                kind: str, title: str, link: str,
                                                                action: str) -> None:
    org = await org_id(db)
    p = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                  VALUES (:o, :k, 'Alpha', 'https://alpha.test/v1', 1) RETURNING id, name"""),
                          {"o": org, "k": kind})).one()
    await db.commit()
    await ModelRouter(sessionmaker(), redis)._set_auth_state(p, "expired")
    [bell] = await bells(db, "model.auth_expired")
    assert bell.title == title and bell.link == link
    [issue] = await health.active_issues(db, org)
    assert issue["title"] == title and issue["link"] == link and issue["action"] == action
    if link == "/api":
        assert "đăng nhập" not in (issue["title"] + issue["body"]).lower()


async def test_old_open_model_alert_is_rewritten_by_watch(owner_api: Api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Sự cố mở từ bản cũ (câu "đăng nhập lại" + /system?tab=brain) được vòng theo dõi viết lại, không chuông thêm."""
    org = await org_id(db)
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank,
                                                                 auth_state)
                                    VALUES (:o, 'openai_compat', 'Cũ', 'https://cu.test/v1', 1, 'expired')
                                    RETURNING id"""), {"o": org})).scalar_one()
    await db.commit()
    async with sessionmaker()() as s:
        await health.raise_once(s, org, key=f"model.auth_expired:{pid}", kind="model.auth_expired", severity="warn",
                                title="Model Cũ cần đăng nhập lại", body="Bấm để đăng nhập lại.",
                                link="/system?tab=brain")
        await s.commit()
    await evaluate(redis, org)
    await db.commit()
    [issue] = await health.active_issues(db, org)
    assert issue["link"] == "/api" and issue["title"].startswith("Khoá API của Cũ bị từ chối")
    assert len(await bells(db, "model.auth_expired")) == 1
