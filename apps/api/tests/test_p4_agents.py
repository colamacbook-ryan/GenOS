"""Giai đoạn 4.1 (Danh tính Agent) + 4.2 (API & Model, phần agent.bindings): tạo/sửa/nhân bản/tắt agent, phạm vi
nghe (`agent.channel_scopes`), mẫu có sẵn không mặc định bắt buộc (spec E13), PIN cho tạo/tắt/nhân bản, gán model
theo agent/mục đích + tham số core (`agent.bindings`). `GET /agents/decisions` (nền chung, `gh.biz.core.routes`)
phải còn hoạt động đúng sau khi thêm router `/agents/{id}` — kiểm thứ tự route không nuốt nhau."""

import uuid

from sqlalchemy import text

from tests.conftest import OWNER, Api
from tests.phase2 import listen, org_id
from tests.test_rbac_api import login_as

AGENT_BODY = {
    "name": "Trợ lý thương mại",
    "role_desc": "Theo dõi cơ hội, nhắc việc quá hạn",
    "voice": "Thân thiện, chuyên nghiệp",
    "speak_when": "Khi được hỏi trực tiếp hoặc có việc cần báo",
    "template": "commercial",
    "addressing": {"owner": "Sếp", "self": "em", "customer": "anh/chị"},
    "forbidden": ["Không tự cam kết giá"],
    "autonomy_level": 4,
}


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


async def _channel_group(db):  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar_one()
    return org, ch, gid


async def _seed_agent(db, org, *, name: str = "Trợ lý thương mại", enabled: bool = True) -> uuid.UUID:  # type: ignore[no-untyped-def]
    """Tạo thẳng qua DB (không qua API) khi test không nhắm vào chính `POST /agents` — tránh PIN của bước tạo
    lẫn với PIN đang kiểm ở hành động khác trong cùng test."""
    aid = (await db.execute(text("""
        INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when, forbidden,
                                      autonomy_level, is_enabled)
        VALUES (:o, :n, 'Báo giá, follow khách', 'commercial', '{}'::jsonb, 'lễ phép', 'khi được hỏi',
                ARRAY['không tự cam kết giá'], 4, :e) RETURNING id"""),
        {"o": org, "n": name, "e": enabled})).scalar_one()
    await db.commit()
    return aid  # type: ignore[no-any-return]


async def _seed_provider_model(api: Api, db) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    # v0.1.35 (F-20): tạo / sửa nhà cung cấp AI cần PIN `ai.route_change`.
    await _pin(api)
    r = await api.send("POST", "/providers", {"kind": "gemini", "name": "Gemini test",
                                              "keys": ["AIza-test-key-0001"], "models": ["gemini-2.5-flash"]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    mid = r.json()["models"][0]["id"]
    return pid, mid


# ─── mẫu có sẵn (4.1) ───────────────────────────────────────────────────────

async def test_templates_listed_mascot_off_by_default(owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/agents/templates")
    assert r.status_code == 200
    items = {t["code"]: t for t in r.json()}
    assert set(items) == {"commercial", "key_account", "admin", "cs", "recruiter", "secretary", "mascot"}
    assert items["mascot"]["default_enabled"] is False
    assert all(items[c]["default_enabled"] is True for c in items if c != "mascot")
    assert items["commercial"]["name"] == "Trợ lý thương mại"


# ─── tạo (PIN) ──────────────────────────────────────────────────────────────

async def test_create_needs_pin_attaches_scope_and_logs(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, ch, gid = await _channel_group(db)
    api: Api = owner_api
    body = {**AGENT_BODY, "channel_scopes": [{"channel_id": str(ch), "group_id": str(gid)}]}
    assert (await api.send("POST", "/agents", body)).status_code == 423
    await _pin(api)
    r = await api.send("POST", "/agents", body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["name"] == "Trợ lý thương mại" and out["is_enabled"] is True and out["autonomy_level"] == 4
    assert out["channel_scopes"] == [{"channel_id": str(ch), "channel_type": "zalo", "group_id": str(gid),
                                      "group_name": "Nhóm g1"}]
    assert out["limits"] == {"decisions_per_min": 20, "drafts_per_hour": 30}   # mặc định chung, chưa chỉnh
    assert out["binding"] is None
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'agent.created' AND "
                               "target_id = :i"), {"i": out["id"]})).scalar_one()
    assert n == 1


async def test_create_rejects_unknown_template_and_bad_scope(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    await _channel_group(db)
    api: Api = owner_api
    await _pin(api)
    r = await api.send("POST", "/agents", {**AGENT_BODY, "template": "unknown"})
    assert r.status_code == 422
    bad_channel = str(uuid.uuid4())
    r = await api.send("POST", "/agents", {**AGENT_BODY, "channel_scopes": [{"channel_id": bad_channel}]})
    assert r.status_code == 422 and "channel_scopes" in r.json()["errors"]
    r = await api.send("POST", "/agents", {**AGENT_BODY, "limits": {"bogus_key": 5}})
    assert r.status_code == 422


# ─── liệt kê / sửa (không cần PIN) ────────────────────────────────────────────

async def test_list_and_patch_replaces_scope_with_pin(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, ch, gid = await _channel_group(db)
    gid2 = await listen(db, org, "g2")
    aid = await _seed_agent(db, org)
    await db.execute(text("INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id) VALUES (:a, :c, :g)"),
                     {"a": aid, "c": ch, "g": gid})
    await db.commit()
    api: Api = owner_api

    items = (await api.get("/agents")).json()
    assert any(i["id"] == str(aid) for i in items)
    got = next(i for i in items if i["id"] == str(aid))
    assert got["channel_scopes"][0]["group_id"] == str(gid)

    detail = await api.get(f"/agents/{aid}")
    assert detail.status_code == 200 and detail.json()["name"] == "Trợ lý thương mại"

    body = {"autonomy_level": 3, "limits": {"decisions_per_min": 5},
            "channel_scopes": [{"channel_id": str(ch), "group_id": str(gid2)}]}
    # v0.1.45 (F-20): đổi rào chắn (mức tự trị/giới hạn/phạm vi kênh) cần PIN policy.change; đổi tên thì không.
    r = await api.send("PATCH", f"/agents/{aid}", body)
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED", r.text
    r = await api.send("PATCH", f"/agents/{aid}", {"name": "Trợ lý thương mại"})
    assert r.status_code == 200, r.text
    await _pin(api)
    r = await api.send("PATCH", f"/agents/{aid}", body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["autonomy_level"] == 3
    assert out["limits"] == {"decisions_per_min": 5, "drafts_per_hour": 30}
    assert [s["group_id"] for s in out["channel_scopes"]] == [str(gid2)]

    # phạm vi cũ (g1) không còn — bị thay hẳn, không cộng dồn
    left = (await db.execute(text("SELECT count(*) FROM agent.channel_scopes WHERE agent_id = :a AND group_id = :g"),
                             {"a": aid, "g": gid})).scalar_one()
    assert left == 0


async def test_manager_has_no_system_read_auditor_does(owner_api, client, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    await _seed_agent(db, org)
    manager = await login_as(client, db, "manager")
    assert (await manager.get("/agents")).status_code == 403
    auditor = await login_as(client, db, "auditor")
    assert (await auditor.get("/agents")).status_code == 200


# ─── nhân bản / tắt (PIN) ─────────────────────────────────────────────────────

async def test_clone_starts_disabled_and_copies_scope_needs_pin(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, ch, gid = await _channel_group(db)
    aid = await _seed_agent(db, org)
    await db.execute(text("INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id) VALUES (:a, :c, :g)"),
                     {"a": aid, "c": ch, "g": gid})
    await db.commit()
    api: Api = owner_api
    assert (await api.send("POST", f"/agents/{aid}/clone", {"name": "Bản sao"})).status_code == 423
    await _pin(api)
    r = await api.send("POST", f"/agents/{aid}/clone", {"name": "Bản sao"})
    assert r.status_code == 201, r.text
    clone = r.json()
    assert clone["is_enabled"] is False and clone["name"] == "Bản sao"
    assert clone["channel_scopes"] == [{"channel_id": str(ch), "channel_type": "zalo", "group_id": str(gid),
                                        "group_name": "Nhóm g1"}]
    # nguồn không đổi
    src = (await api.get(f"/agents/{aid}")).json()
    assert src["is_enabled"] is True


async def test_clone_can_skip_scope_copy(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, ch, gid = await _channel_group(db)
    aid = await _seed_agent(db, org)
    await db.execute(text("INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id) VALUES (:a, :c, :g)"),
                     {"a": aid, "c": ch, "g": gid})
    await db.commit()
    api: Api = owner_api
    await _pin(api)
    r = await api.send("POST", f"/agents/{aid}/clone", {"name": "Bản sao trơn", "copy_channel_scopes": False})
    assert r.status_code == 201
    assert r.json()["channel_scopes"] == []


async def test_disable_toggle_enable_needs_pin(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """v0.1.67 (F089): TẮT không cần PIN, BẬT lại cần PIN (chi tiết ở test_agents_that_v0167)."""
    org, *_ = await _channel_group(db)
    aid = await _seed_agent(db, org)
    api: Api = owner_api
    r = await api.send("PATCH", f"/agents/{aid}/disable", {"enabled": False})
    assert r.status_code == 200 and r.json()["is_enabled"] is False
    assert (await api.send("PATCH", f"/agents/{aid}/disable", {"enabled": True})).status_code == 423
    await _pin(api)
    r = await api.send("PATCH", f"/agents/{aid}/disable", {"enabled": True})
    assert r.status_code == 200 and r.json()["is_enabled"] is True


# ─── gán model theo agent/mục đích — agent.bindings (4.2) ─────────────────────

async def test_bindings_list_set_and_delete(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    aid = await _seed_agent(db, org)
    api: Api = owner_api
    _pid, mid = await _seed_provider_model(api, db)

    listing = (await api.get("/agents/bindings")).json()
    keys = {i["agent_key"] for i in listing["items"]}
    # v0.1.43 (F-25): chỉ còn các khoá lõi được ModelRouter dùng thật (v0.1.55: + core.briefing — Bản tin Gen).
    assert keys == {"core.refinery", "core.reply", "core.gen", "core.briefing", "core.owner_reply", "core.support",
                    f"agent:{aid}"}   # v0.1.72: + 2 khoá chạy trong hộp
    assert all(i["binding"] is None for i in listing["items"])
    assert any(m["id"] == mid for m in listing["models"])

    r = await api.send("PUT", "/agents/bindings/core.refinery", {"model_id": mid})
    assert r.status_code == 200, r.text
    b = r.json()["binding"]
    assert b["model_id"] == mid and b["temperature"] == 0.3 and b["context_tokens"] == 6000 and b["rule_codes"] == []

    r = await api.send("PUT", f"/agents/bindings/agent:{aid}",
                       {"model_id": mid, "temperature": 0.7, "context_tokens": 4000, "rule_codes": ["r1"]})
    assert r.status_code == 200, r.text
    assert r.json()["binding"]["temperature"] == 0.7 and r.json()["binding"]["rule_codes"] == ["r1"]

    detail = (await api.get(f"/agents/{aid}")).json()
    assert detail["binding"]["model_id"] == mid and detail["binding"]["context_tokens"] == 4000

    r = await api.send("PUT", "/agents/bindings/bogus_key", {"model_id": mid})
    assert r.status_code == 422

    r = await api.send("PUT", "/agents/bindings/core.reply", {"model_id": str(uuid.uuid4())})
    assert r.status_code == 404

    r = await api.send("DELETE", f"/agents/bindings/agent:{aid}")
    assert r.status_code == 204
    detail = (await api.get(f"/agents/{aid}")).json()
    assert detail["binding"] is None


async def test_bindings_read_needs_system_read(owner_api, client, db) -> None:  # type: ignore[no-untyped-def]
    await _channel_group(db)
    manager = await login_as(client, db, "manager")
    assert (await manager.get("/agents/bindings")).status_code == 403


# ─── /agents/decisions (nền chung) vẫn hoạt động sau khi thêm router /agents/{id} ─────────────────────────────

async def test_agents_decisions_route_not_shadowed_by_agent_id(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """Đăng ký `agents_router` SAU `biz.routers()` trong `gh.app`: nếu sai thứ tự, `/agents/decisions` sẽ bị
    `/agents/{agent_id}` nuốt mất (cố ép "decisions" thành UUID) và trả 422 thay vì danh sách quyết định."""
    r = await owner_api.get("/agents/decisions")
    assert r.status_code == 200, r.text
    assert r.json() == {"items": [], "next_cursor": None, "total": 0}
