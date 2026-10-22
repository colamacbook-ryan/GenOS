"""API giai đoạn 2: quyền theo vai trò, PIN cho thao tác nhạy cảm, trọng số phải đủ 100%, trình thiết lập bước 4–7/12,
WebSocket lọc theo quyền và che số nhạy cảm, vector mã hoá dùng chung với bridge."""

import base64

from sqlalchemy import text

from gh import crypto
from gh.auth import service
from gh.config import get_settings
from gh.db import sessionmaker
from gh.realtime import Hub
from tests.conftest import OWNER, Api, verify_pin
from tests.phase2 import listen, msg, org_id, put
from tests.test_rbac_api import login_as

WEIGHTS = [{"dimension": d, "value": v} for d, v in (("heat", 30), ("potential", 25), ("churn_risk", 20), ("fit", 12),
                                                      ("engagement", 8), ("data_confidence", 5))]


async def test_data_endpoints_follow_roles(owner_api, client, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, msg("Chuyển khoản vào tài khoản 0123 4567 8910 giúp chị"))
    owner: Api = owner_api
    assert (await owner.get("/raw")).json()["items"][0]["text"].endswith("0123 4567 8910 giúp chị")
    auditor = await login_as(client, db, "auditor")
    items = (await auditor.get("/raw")).json()["items"]
    assert "8910" not in items[0]["text"] and items[0]["text"].endswith("910 giúp chị")   # khoá cứng 8
    assert (await auditor.send("PUT", "/rules/weights", WEIGHTS)).status_code == 403    # chỉ đọc
    manager = await login_as(client, db, "manager")
    assert (await manager.get("/raw")).status_code == 403
    assert (await manager.get("/channels")).status_code == 403


async def test_weights_must_sum_to_100(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    bad = [dict(w) for w in WEIGHTS]
    bad[0]["value"] = 31
    r = await api.send("PUT", "/rules/weights", bad)
    assert r.status_code == 422 and "100" in r.json()["errors"]["_"]
    r = await api.send("PUT", "/rules/weights", WEIGHTS)
    assert r.status_code == 200


async def test_sensitive_operations_need_pin(owner_api, redis) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    assert (await api.send("POST", "/channels/zalo/login", {"accept_risk": True})).status_code == 423
    assert (await api.get("/raw/export")).status_code == 423
    await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await api.send("POST", "/channels/zalo/login", {"accept_risk": False})
    assert r.status_code == 422                                    # phải xác nhận rủi ro tài khoản cá nhân
    r = await api.send("POST", "/channels/zalo/login", {"accept_risk": True})
    assert r.status_code == 503 and r.json()["code"] == "BRIDGE_OFFLINE"
    await redis.set("gh:bridge:heartbeat", "1")
    r = await api.send("POST", "/channels/zalo/login", {"accept_risk": True, "account_label": "Zalo Sếp"})
    assert r.status_code == 202
    cards = {c["type"]: c for c in (await api.get("/channels")).json()}
    assert cards["zalo"]["state"] == "pending_qr"
    r = await api.get("/raw/export")
    assert r.status_code == 200 and r.content.startswith("﻿".encode())


async def test_setup_steps_4_to_7_and_finish(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    s = (await api.get("/setup/state")).json()
    assert s["console_ready"] and s["current_step"] == 4
    # v0.1.35 (F-20): tạo / sửa nhà cung cấp AI cần PIN `ai.route_change`.
    await verify_pin(api)
    r = await api.send("POST", "/providers", {"kind": "gemini", "name": "Gemini", "keys": ["AIza-test-key-1234"],
                                              "models": ["gemini-2.5-flash"]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["keys"][0]["last4"] == "1234" and "secret" not in str(r.json()["keys"])
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [pid]})
    assert r.status_code == 409 and r.json()["code"] == "STEP_INCOMPLETE"   # chưa gọi thử thành công
    await db.execute(text("""UPDATE agent.providers SET auth_state = 'ok', last_test = '{"ok": true}' WHERE id = :i"""),
                     {"i": pid})
    await db.commit()
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [pid]})).status_code == 200

    assert (await api.send("PUT", "/setup/steps/5", {})).status_code == 409
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar()
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at)
                             VALUES (:c, :o, 'Zalo Sếp', 'active', now())"""), {"c": ch, "o": org})
    await db.commit()
    assert (await api.send("PUT", "/setup/steps/5", {})).status_code == 200

    gid = await listen(db, org, "g-setup", mode="off")
    r = await api.send("PUT", "/setup/steps/6", {"groups": [{"id": str(gid), "listen_mode": "off"}]})
    assert r.status_code == 409
    r = await api.send("PUT", "/setup/steps/6", {"groups": [{"id": str(gid), "listen_mode": "silent",
                                                             "view_scope": "owner"}]})
    assert r.status_code == 200, r.text

    presets = (await api.get("/setup/rule-presets")).json()
    assert [p["code"] for p in presets] == ["R-01", "R-02", "R-03", "R-04", "R-05", "R-06"]
    r = await api.send("PUT", "/setup/steps/7", {"interval_seconds": 900, "count_threshold": 200, "min_confidence": 0.7,
                                                 "rule_codes": ["R-01", "R-02", "R-06"], "weights": WEIGHTS})
    assert r.status_code == 200, r.text
    rules = {r["code"]: r["enabled"] for r in (await api.get("/rules")).json()}
    assert rules == {"R-01": True, "R-02": True, "R-03": False, "R-04": False, "R-05": False, "R-06": True}
    sched = (await api.get("/refinery/schedule")).json()
    assert (sched["interval_seconds"], sched["count_threshold"]) == (900, 200)
    # Lưu lại bước 7 không nhân đôi quy tắc.
    await api.send("PUT", "/setup/steps/7", {"interval_seconds": 900, "count_threshold": 200, "min_confidence": 0.7,
                                             "rule_codes": ["R-01"], "weights": []})
    assert len((await api.get("/rules")).json()) == 6

    fr = (await api.get("/setup/first-run")).json()
    assert set(fr) == {"raw_collected", "classifying", "clean", "lowconf", "discarded", "run"}
    done = {s["n"]: s["status"] for s in (await api.get("/setup/state")).json()["steps"]}
    assert all(done[n] == "done" for n in (1, 2, 3, 4, 5, 6, 7))
    # Bước 8–9 không bắt buộc (làm sau ở Console) → hoàn tất được ngay.
    r = await api.send("PUT", "/setup/steps/12", {})
    assert r.status_code == 200, r.text
    assert (await api.get("/setup/state")).json()["finished"]
    # Sau Hoàn tất: bước "Để sau" vẫn làm tiếp được từ trang Hướng dẫn thiết lập, bước bắt buộc/skip thì không.
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    # v0.1.29: bước 4 cũng là việc tiếp theo được ("Chưa có model") — ở đây đã có model nên đã xong.
    assert set(items) == set(range(4, 12)) | {13, 14} and items[4]["done"] and not items[11]["done"]
    r = await api.send("PUT", "/setup/steps/11", {"frequency": "daily", "time_of_day": "03:00"})
    assert r.status_code == 200, r.text
    assert r.json()["finished"] and r.json()["backup"]["time_of_day"] == "03:00"
    r = await api.send("PUT", "/setup/steps/10", {"invites": [{"display_name": "Lan", "email": "lan@example.com",
                                                                "role": "operator"}]})
    assert r.status_code == 200, r.text
    assert r.json()["invited"][0]["temp_password"]
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    assert items[10]["done"] and items[11]["done"]
    assert (await api.send("POST", "/setup/steps/6/skip")).status_code == 409
    assert (await api.send("PUT", "/setup/steps/12", {})).status_code == 409


async def test_setup_finishes_with_only_steps_1_to_4(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """Owner làm 1–4 (Owner, tổ chức, bộ não AI): bỏ qua 5–11 rồi hoàn tất — không bắt quét QR hay dựng agent.
    (v0.1.29: bước 4 cũng "Để sau" được — xem tests/test_setup_v0129.py.)"""
    api: Api = owner_api
    # v0.1.35 (F-20): tạo / sửa nhà cung cấp AI cần PIN `ai.route_change`.
    await verify_pin(api)
    r = await api.send("POST", "/providers", {"kind": "gemini", "name": "Gemini", "keys": ["AIza-test-key-1234"],
                                              "models": ["gemini-2.5-flash"]})
    pid = r.json()["id"]
    await db.execute(text("""UPDATE agent.providers SET auth_state = 'ok', last_test = '{"ok": true}' WHERE id = :i"""),
                     {"i": pid})
    await db.commit()
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [pid]})).status_code == 200
    for n in range(5, 12):
        r = await api.send("POST", f"/setup/steps/{n}/skip")
        assert r.status_code == 200, (n, r.text)
    r = await api.send("PUT", "/setup/steps/12", {})
    assert r.status_code == 200, r.text
    assert (await api.get("/setup/state")).json()["finished"]
    # Sau Hoàn tất: bước "Để sau" vẫn làm tiếp được từ trang Hướng dẫn thiết lập, bước bắt buộc/skip thì không.
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    # v0.1.28 (UX N3/N4): "Để sau" dùng mặc định — bước 7 có bộ quy tắc khởi đầu, bước 11 có lịch 02:00 hằng ngày.
    assert set(items) == set(range(4, 12)) | {13, 14} and items[4]["done"] and items[11]["done"] and items[7]["done"] \
        and not items[10]["done"]
    r = await api.send("PUT", "/setup/steps/11", {"frequency": "daily", "time_of_day": "03:00"})
    assert r.status_code == 200, r.text
    assert r.json()["finished"] and r.json()["backup"]["time_of_day"] == "03:00"
    r = await api.send("PUT", "/setup/steps/10", {"invites": [{"display_name": "Lan", "email": "lan@example.com",
                                                                "role": "operator"}]})
    assert r.status_code == 200, r.text
    assert r.json()["invited"][0]["temp_password"]
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    assert items[10]["done"] and items[11]["done"]
    assert (await api.send("POST", "/setup/steps/6/skip")).status_code == 409
    assert (await api.send("PUT", "/setup/steps/12", {})).status_code == 409


async def test_refinery_run_now_queues_once(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    r = await api.send("POST", "/refinery/run", {})
    assert r.status_code == 202
    r2 = await api.send("POST", "/refinery/run", {})
    assert r2.status_code == 409 and r2.json()["code"] == "REFINERY_BUSY"
    runs = (await api.get("/refinery/runs")).json()
    assert runs[0]["status"] == "queued" and runs[0]["trigger"] == "manual"


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, t: str) -> None:
        self.sent.append(t)


def user(role: str, org: object, perms: dict[str, str]) -> service.CurrentUser:
    u = service.CurrentUser.__new__(service.CurrentUser)
    object.__setattr__(u, "role_code", role)
    object.__setattr__(u, "org_id", org)
    object.__setattr__(u, "permissions", perms)
    return u


async def test_ws_dispatch_filters_by_permission_org_and_masks(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    hub = Hub(redis)
    gid = await listen(db, await org_id(db), "g1")      # nhóm thật, «Mọi thành viên» (v0.1.71: WS xét «Quyền xem»)
    owner, auditor, manager, other = FakeWs(), FakeWs(), FakeWs(), FakeWs()
    hub.clients = {owner: user("owner", "o1", {"data.read": "all", "system.manage": "all"}),  # type: ignore[dict-item]
                   auditor: user("auditor", "o1", {"data.read": "all"}),
                   manager: user("manager", "o1", {"data.read": "none"}),
                   other: user("owner", "o2", {"data.read": "all"})}
    # tin NHÓM (có `group`): tin 1-1 chỉ Owner nhận, xem test_tin_1_1_chi_owner_v0165
    await hub.dispatch({"type": "raw.new", "data": {"text": "STK 0123456789", "payload": {"x": 1},
                                                    "group": {"id": str(gid)}}, "at": "t", "org_id": "o1"})
    await hub.dispatch({"type": "cli.login", "data": {"status": "waiting_code"}, "at": "t", "org_id": "o1"})
    assert len(owner.sent) == 2 and "0123456789" in owner.sent[0]
    assert len(auditor.sent) == 1 and "0123456789" not in auditor.sent[0] and "payload" not in auditor.sent[0]
    assert manager.sent == [] and other.sent == []


def test_crypto_vectors_match_bridge(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    key = base64.b64encode(bytes(range(32))).decode()
    monkeypatch.setenv("GH_BRIDGE_KEY", key)
    get_settings.cache_clear()
    try:
        assert crypto.hmac_sign(b"abc").hex() == "575b8b86d194ac0a6cd104ea301c7eb9d457195926a88a67de21d02d5839cb9c"
        blob = ("AAECAwQFBgcICQoLIrO1LxmxqW4NJ2zfq1gXWOrP9l7nfjz8GJy99euBb24KDy/X9y2eQzuEyeSJMmz/dqF+rK2kNs+qaUVJDz4=")
        plain = crypto.transport_decrypt(blob, "zalo:11111111-1111-1111-1111-111111111111")
        assert plain == b'{"imei":"imei-1","cookie":[],"userAgent":"UA"}'
        again = crypto.transport_encrypt(plain, "zalo:x")
        assert crypto.transport_decrypt(again, "zalo:x") == plain
    finally:
        get_settings.cache_clear()


async def test_follow_up_lists_deferred_steps_and_detects_real_completion(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """Bước "Để sau" hiện ở Tổng quan; làm xong ở Console (có dữ liệu thật) thì tự `done` — không bấm tay."""
    api: Api = owner_api
    org = await org_id(db)
    for n in (5, 8, 11):
        assert (await api.send("POST", f"/setup/steps/{n}/skip")).status_code == 200
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    assert items[5]["status"] == "skipped" and not items[5]["done"]
    # v0.1.28 (UX N4): "Để sau" ở bước 11 đặt lịch sao lưu mặc định → đã có sao lưu tự động.
    assert not items[8]["done"] and items[11]["done"]
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar()
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at)
                             VALUES (:c, :o, 'Zalo Sếp', 'active', now())"""), {"c": ch, "o": org})
    await db.execute(text("""INSERT INTO agent.identities (org_id, name, role_desc, addressing, voice, speak_when,
                             autonomy_level) VALUES (:o, 'Mai', 'CSKH', '{}'::jsonb, 'x', 'y', 4)"""), {"o": org})
    await db.commit()
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    assert items[5]["done"] and items[8]["done"] and items[9]["done"]
    assert items[11]["done"]
