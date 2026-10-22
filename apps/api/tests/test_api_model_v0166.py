"""v0.1.66 (gói C3) — phía máy chủ của màn API & Model:

- F008: gỡ được một model đã thêm nhầm (DELETE /providers/{id}/models/{mid}) — gỡ luôn dòng gán trỏ vào nó, ghi Action
  Log `provider.model_deleted`, model lạ ⇒ 404; (xoá khoá đã có sẵn, nay có nút ở web);
- F009: hạn mức ngày / lượt-phút đặt qua POST /providers/{id}/models và KHÔNG bị xoá khi chỉ bấm "Dùng model này";
- F015 / F134: "Khoá & phiên" nói cùng nhãn với mọi màn (lần Kiểm tra lỗi ⇒ "Lỗi kết nối", không "Cần đăng nhập") và
  không còn dòng "khoá phiên QR" của Zalo / WhatsApp trong thẻ tài khoản Google.
"""

import uuid

from sqlalchemy import text

from tests.conftest import Api, verify_pin
from tests.phase2 import org_id
from tests.test_defaults_v0155 import AGY, API, add_source, bind


async def _quota(db, pid, name):  # type: ignore[no-untyped-def]
    await db.rollback()
    r = (await db.execute(text("""SELECT daily_quota, rate_limit_per_min, is_default FROM agent.models
                                  WHERE provider_id = :p AND model_name = :m"""), {"p": pid, "m": name})).one()
    return r.daily_quota, r.rate_limit_per_min, r.is_default


async def test_delete_model_removes_bindings_and_logs(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # v0.1.70: thêm / gỡ model cần PIN ai.route_change
    org = await org_id(db)
    pid, ids = await add_source(db, org, API, "alpha", 1, ["m-flash", "m-pro"])
    await bind(db, org, "core.gen", ids["m-pro"])
    r = await owner_api.send("DELETE", f"/providers/{pid}/models/{ids['m-pro']}")
    assert r.status_code == 204, r.text
    await db.rollback()
    left = (await db.execute(text("SELECT model_name FROM agent.models WHERE provider_id = :p"),
                             {"p": pid})).scalars().all()
    assert left == ["m-flash"]
    # vai quay về model chuẩn
    assert (await db.execute(text("SELECT count(*) FROM agent.bindings"))).scalar_one() == 0
    log = (await db.execute(text("""SELECT detail FROM ops.action_log
                                 WHERE action = 'provider.model_deleted'"""))).all()
    assert len(log) == 1 and log[0].detail["model_name"] == "m-pro"
    # Model đã gỡ / không thuộc nguồn này ⇒ 404 (không đụng model của nguồn khác).
    assert (await owner_api.send("DELETE", f"/providers/{pid}/models/{ids['m-pro']}")).status_code == 404
    other, oids = await add_source(db, org, API, "beta", 2, ["b-1"])
    assert (await owner_api.send("DELETE", f"/providers/{pid}/models/{oids['b-1']}")).status_code == 404
    assert (await owner_api.send("DELETE", f"/providers/{uuid.uuid4()}/models/{ids['m-flash']}")).status_code == 404


async def test_quota_is_kept_when_only_choosing_default(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # v0.1.70: thêm / gỡ model cần PIN ai.route_change
    org = await org_id(db)
    pid, _ = await add_source(db, org, API, "alpha", 1, ["m-flash"])
    r = await owner_api.send("POST", f"/providers/{pid}/models",
                             {"model_name": "m-flash", "daily_quota": 500, "rate_limit_per_min": 20})
    assert r.status_code == 201, r.text
    assert await _quota(db, pid, "m-flash") == (500, 20, False)
    # "Dùng model này" chỉ gửi model_name + make_default ⇒ hạn mức giữ nguyên.
    r = await owner_api.send("POST", f"/providers/{pid}/models", {"model_name": "m-flash", "make_default": True})
    assert r.status_code == 201, r.text
    assert await _quota(db, pid, "m-flash") == (500, 20, True)
    # Gửi null ⇒ bỏ hạn mức.
    r = await owner_api.send("POST", f"/providers/{pid}/models",
                             {"model_name": "m-flash", "daily_quota": None, "rate_limit_per_min": 20})
    assert r.status_code == 201, r.text
    assert await _quota(db, pid, "m-flash") == (None, 20, True)


async def test_credentials_label_error_is_connection_error_and_no_qr_rows(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await verify_pin(owner_api)
    pid, _ = await add_source(db, org, AGY, "Antigravity", 1, ["gemini-3.8-flash"])
    for state, label in (("ok", "Hoạt động"), ("error", "Lỗi kết nối"), ("expired", "Hết hạn"),
                         ("unconfigured", "Chưa đăng nhập")):
        await db.execute(text("UPDATE agent.providers SET auth_state = :s WHERE id = :p"), {"s": state, "p": pid})
        await db.commit()
        rows = (await owner_api.get("/providers/credentials")).json()
        cli = next(c for c in rows if c["name"].startswith("Antigravity"))
        assert cli["state_label"] == label, (state, cli)
    assert not [c for c in rows if "QR" in c["name"] or c["icon"] == "qr-code"]


# ─── review v0.1.66 (P2-2): gỡ model KHÔNG được xoá dây chuyền bảng giá khi model còn lịch sử chi phí ─────────────────

async def _call(db, org, model, days_ago: int) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO agent.model_calls (org_id, at, model_id, agent_key, purpose, tokens_in,
                                                            tokens_out, latency_ms, status)
                             VALUES (:o, now() - make_interval(days => :d), :m, 'core.gen', 'test', 1000, 500, 10,
                                     'ok')"""),
                     {"o": org, "d": days_ago, "m": model})
    await db.commit()


async def test_delete_model_with_recent_calls_is_refused_and_keeps_price(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # v0.1.70: thêm / gỡ model cần PIN ai.route_change
    org = await org_id(db)
    pid, ids = await add_source(db, org, API, "alpha", 1, ["m-flash", "m-pro"])
    await bind(db, org, "core.gen", ids["m-pro"])
    await db.execute(text("""INSERT INTO agent.model_prices (model_id, org_id, in_vnd_per_mtok, out_vnd_per_mtok)
                             VALUES (:m, :o, 100, 200)"""), {"m": ids["m-pro"], "o": org})
    await _call(db, org, ids["m-pro"], 3)
    r = await owner_api.send("DELETE", f"/providers/{pid}/models/{ids['m-pro']}")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "MODEL_HAS_CALLS"
    assert "30 ngày" in body["title"] and "Tắt model" in body["title"] and "m-pro" in body["title"]
    # Không mất gì: model, dòng gán, bảng giá (xoá dây chuyền sẽ làm chi phí lịch sử tụt về 0).
    await db.rollback()
    assert (await db.execute(text("SELECT count(*) FROM agent.models WHERE id = :m"),
                             {"m": ids["m-pro"]})).scalar_one() == 1
    assert (await db.execute(text("SELECT count(*) FROM agent.bindings"))).scalar_one() == 1
    assert (await db.execute(text("SELECT count(*) FROM agent.model_prices"))).scalar_one() == 1
    # Không ghi Action Log "đã gỡ".
    assert (await db.execute(text("""SELECT count(*) FROM ops.action_log
                                  WHERE action = 'provider.model_deleted'"""))).scalar_one() == 0


async def test_delete_model_with_only_old_calls_is_allowed(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # v0.1.70: thêm / gỡ model cần PIN ai.route_change
    org = await org_id(db)
    pid, ids = await add_source(db, org, API, "alpha", 1, ["m-flash", "m-pro"])
    await _call(db, org, ids["m-pro"], 45)          # quá 30 ngày ⇒ gỡ được
    await _call(db, org, ids["m-flash"], 2)         # lượt gọi của model KHÁC không cản
    r = await owner_api.send("DELETE", f"/providers/{pid}/models/{ids['m-pro']}")
    assert r.status_code == 204, r.text


async def test_model_can_be_disabled_and_enabled_keeping_history(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # v0.1.70: thêm / gỡ model cần PIN ai.route_change
    org = await org_id(db)
    pid, ids = await add_source(db, org, API, "alpha", 1, ["m-flash", "m-pro"], default="m-pro")
    await _call(db, org, ids["m-pro"], 1)
    r = await owner_api.send("POST", f"/providers/{pid}/models", {"model_name": "m-pro", "enabled": False})
    assert r.status_code == 201, r.text
    m = next(x for x in r.json()["models"] if x["model_name"] == "m-pro")
    assert m["enabled"] is False and not m.get("is_default")      # tắt ⇒ không còn là model đã chốt
    # Hạn mức không bị đụng, lịch sử còn nguyên, bật lại được.
    r = await owner_api.send("POST", f"/providers/{pid}/models", {"model_name": "m-pro", "enabled": True})
    assert r.status_code == 201, r.text
    assert next(x for x in r.json()["models"] if x["model_name"] == "m-pro")["enabled"] is True
    # Không gửi `enabled` (vd chỉ sửa hạn mức) ⇒ giữ trạng thái đang có.
    await owner_api.send("POST", f"/providers/{pid}/models", {"model_name": "m-pro", "enabled": False})
    r = await owner_api.send("POST", f"/providers/{pid}/models", {"model_name": "m-pro", "daily_quota": 9})
    assert next(x for x in r.json()["models"] if x["model_name"] == "m-pro")["enabled"] is False


# ─── review v0.1.66 (P3): model cuối của Jev / CLI không gỡ được; Action Log ghi vai bị gỡ gán ─────────────────────

async def test_last_model_of_jev_or_cli_cannot_be_deleted(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # v0.1.70: thêm / gỡ model cần PIN ai.route_change
    org = await org_id(db)
    jev, jids = await add_source(db, org, "system_one", "Jev", 1, ["typesafe/jev-1.13"])
    r = await owner_api.send("DELETE", f"/providers/{jev}/models/{jids['typesafe/jev-1.13']}")
    assert r.status_code == 409 and r.json()["code"] == "LAST_MODEL", r.text
    assert "Jev" in r.json()["title"]
    # CLI: gỡ hết model thì bộ tự lành thêm lại ngay ở lượt gọi sau ⇒ chặn từ đầu, nói rõ đường đi khác.
    agy, aids = await add_source(db, org, AGY, "Antigravity", 2, ["gemini-3.8-flash", "gemini-3.1-pro"])
    assert (await owner_api.send("DELETE", f"/providers/{agy}/models/{aids['gemini-3.1-pro']}")).status_code == 204
    r = await owner_api.send("DELETE", f"/providers/{agy}/models/{aids['gemini-3.8-flash']}")
    assert r.status_code == 409 and r.json()["code"] == "LAST_MODEL", r.text
    assert "Tắt model" in r.json()["title"]
    await db.rollback()
    assert (await db.execute(text("SELECT count(*) FROM agent.models WHERE provider_id = ANY(:p)"),
                             {"p": [jev, agy]})).scalar_one() == 2


async def test_delete_model_log_lists_unbound_roles(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # v0.1.70: thêm / gỡ model cần PIN ai.route_change
    org = await org_id(db)
    pid, ids = await add_source(db, org, API, "alpha", 1, ["m-flash", "m-pro"])
    await bind(db, org, "core.gen", ids["m-pro"])
    await bind(db, org, "core.reply", ids["m-pro"])
    await bind(db, org, "core.refinery", ids["m-flash"])
    assert (await owner_api.send("DELETE", f"/providers/{pid}/models/{ids['m-pro']}")).status_code == 204
    await db.rollback()
    detail = (await db.execute(text("""SELECT detail FROM ops.action_log
                                       WHERE action = 'provider.model_deleted'"""))).scalar_one()
    assert detail["model_name"] == "m-pro" and detail["unbound"] == ["core.gen", "core.reply"]
