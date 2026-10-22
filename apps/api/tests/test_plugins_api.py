"""API plugin: plugin nền không gỡ/tắt được (409), mọi lần thử đều vào nhật ký."""

from sqlalchemy import text

from tests.conftest import OWNER, Api


async def test_list_plugins_shows_chassis(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    items = (await api.get("/plugins")).json()
    pkgs = [p["package"] for p in items]
    assert pkgs[:2] == ["@gen/chassis-kernel", "@gen/chassis-bus"]
    assert {p["package"] for p in items} >= {"@gen/chassis-store", "@gen/chassis-policy", "@gen/chassis-auth"}
    assert all(p["origin"] == "core" and not p["removable"] for p in items)
    assert all(not p["can_disable"] for p in items if p["package"].startswith("@gen/chassis-"))
    assert {p["package"] for p in items if p["can_disable"]} == {"@gen/channel-zalo", "@gen/channel-whatsapp"}
    assert all(p["health"] == "healthy" and p["breaker"]["state"] == "closed" for p in items)
    nav = (await api.get("/navigation")).json()
    plug = [n for n in nav[1]["groups"] if n["key"] == "plugins"][0]
    # F-41 (v0.1.42): màn Plugin đóng băng — có trong cây nhưng ẩn, không badge; API /plugins vẫn chạy.
    assert plug.get("hidden") is True and plug["badge"] is None


async def test_chassis_plugin_locked(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    assert (await api.send("DELETE", "/plugins/@gen/chassis-store")).status_code == 423
    await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await api.send("DELETE", "/plugins/@gen/chassis-store")
    assert r.status_code == 409 and r.json()["code"] == "PLUGIN_LOCKED"
    r = await api.send("PATCH", "/plugins/@gen/chassis-policy/toggle", {"enabled": False})
    assert r.status_code == 409 and r.json()["code"] == "PLUGIN_LOCKED"
    r = await api.send("DELETE", "/plugins/@gen/khong-co")
    assert r.status_code == 404
    rows = (await db.execute(text("SELECT action, result, target_id FROM ops.action_log "
                                  "WHERE action LIKE 'plugin.%' ORDER BY at"))).all()
    assert [(r.action, r.result) for r in rows] == [("plugin.uninstall", "blocked"), ("plugin.disable", "blocked"),
                                                    ("plugin.uninstall", "blocked")]
    assert (await db.execute(text("SELECT count(*) FROM ops.plugins"))).scalar() == 9


async def test_every_successful_write_is_logged(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """Quét: mọi route ghi đã gọi trong luồng trên đều để lại dòng nhật ký; chuỗi vẫn hợp lệ."""
    api: Api = owner_api
    await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    await api.send("POST", "/setup/steps/10/skip")
    await api.send("PUT", "/setup/steps/3", {"org_name": "Genesis 2", "timezone": "Asia/Ho_Chi_Minh",
                                             "currency": "VND", "self_name": "Anh", "bot_calls_me": "Sếp"})
    actions = [r[0] for r in (await db.execute(text("SELECT action FROM ops.action_log ORDER BY at"))).all()]
    for a in ("setup.step_saved", "setup.owner_created", "auth.pin_verified", "setup.step_skipped"):
        assert a in actions
    assert actions.count("setup.step_saved") == 3
    assert (await api.get("/audit/verify")).json()["ok"] is True
