"""v0.1.28 — sửa theo rà soát UX (ux-audit):

- C1: bước 4 tự dùng model đầu tiên khi gọi thử OK mà Owner chưa bấm "Dùng model này", gán cho các agent lõi; không có
  model nào → chưa cho qua.
- N1: nguồn gọi thử lỗi xếp CUỐI chuỗi; xoá được nguồn (DELETE /providers/{id}); nhãn "Khoá & phiên" khớp mọi màn.
- N3/N4: "Để sau" ở bước 7 nạp bộ quy tắc khởi đầu; ở bước 11 đặt lịch sao lưu mặc định hằng ngày 02:00.
- V4: mật khẩu tạm không có ký tự dễ nhầm.
"""

import json

from sqlalchemy import text

from gh import crypto
from tests.conftest import Api, verify_pin
from tests.phase2 import org_id


async def _provider(api: Api, db, name: str, *, ok: bool, models: list[str] | None = None,  # type: ignore[no-untyped-def]
                    tested: list[str] | None = None) -> str:
    # v0.1.35 (F-20): tạo / sửa nhà cung cấp AI cần PIN `ai.route_change`.
    await verify_pin(api)
    r = await api.send("POST", "/providers", {"kind": "openai_compat", "name": name, "endpoint": "https://127.0.0.1:9/v1",
                                              "keys": ["sk-test-key-123456"], "models": models or []})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    last = {"ok": ok, "latency_ms": 12 if ok else None, "models": tested or [],
            "error": None if ok else "mạng: All connection attempts failed"}
    await db.execute(text("UPDATE agent.providers SET auth_state = :s, last_test = CAST(:t AS jsonb) WHERE id = :i"),
                     {"s": "ok" if ok else "error", "t": json.dumps(last), "i": pid})
    await db.commit()
    return str(pid)


async def test_step4_auto_picks_tested_model_and_sinks_failed_source(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """v0.1.55 (G2): bước 4 vẫn chép model đã gọi thử + đẩy nguồn lỗi xuống cuối, nhưng KHÔNG ghi agent.bindings —
    hồ sơ tiêu chuẩn tự chọn model theo vai (dòng gán hiện "Chuẩn")."""
    api: Api = owner_api
    org = await org_id(db)
    bad = await _provider(api, db, "Model sai", ok=False)                       # tạo trước → rank 1
    good = await _provider(api, db, "Model nội bộ", ok=True, tested=["text-embedding-3-small", "qwen2.5-7b"])
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [good]})
    assert r.status_code == 200, r.text
    models = (await db.execute(text("SELECT model_name FROM agent.models WHERE provider_id = :p"),
                               {"p": good})).scalars().all()
    assert models == ["qwen2.5-7b"]                                              # bỏ model embedding
    bound = dict((await db.execute(text("""SELECT b.agent_key, m.model_name FROM agent.bindings b
                                            JOIN agent.models m ON m.id = b.model_id WHERE b.org_id = :o"""),
                                   {"o": org})).all())
    assert bound == {}
    ranks = dict((await db.execute(text("SELECT id::text, failover_rank FROM agent.providers WHERE org_id = :o"),
                                   {"o": org})).all())
    assert ranks[good] == 1 and ranks[bad] == 2                                  # nguồn lỗi xuống cuối
    items = {i["agent_key"]: i for i in (await api.get("/agents/bindings")).json()["items"]}
    assert items["core.gen"]["binding"] is None


async def test_step4_refuses_when_no_source_has_a_model(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    pid = await _provider(api, db, "Chưa có model", ok=True, tested=[])
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [pid]})
    assert r.status_code == 409 and r.json()["code"] == "STEP_INCOMPLETE" and "model" in r.json()["title"]
    # Owner bấm "Dùng model này" → qua được, không ghi đè gán model đã có.
    assert (await api.send("POST", f"/providers/{pid}/models", {"model_name": "my-model"})).status_code == 201
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [pid]})).status_code == 200


async def test_delete_provider_and_consistent_credential_label(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    bad = await _provider(api, db, "Model sai", ok=False)
    creds = {c["name"]: c for c in (await api.get("/providers/credentials")).json()}
    assert creds["Model sai"]["state"] == "bad" and creds["Model sai"]["state_label"] == "Lỗi kết nối"
    good = await _provider(api, db, "Tốt", ok=True, models=["m1"])
    mid = (await db.execute(text("SELECT id FROM agent.models WHERE provider_id = :p"), {"p": good})).scalar_one()
    assert (await api.send("PUT", "/agents/bindings/core.gen", {"model_id": str(mid)})).status_code == 200
    assert (await api.send("DELETE", f"/providers/{bad}")).status_code == 204
    assert (await api.send("DELETE", f"/providers/{good}")).status_code == 204
    left = (await db.execute(text("""SELECT count(*) FROM agent.providers
                                     WHERE org_id = :o AND kind <> 'antigravity_cli'"""), {"o": org})).scalar_one()
    assert left == 0
    n_bind = (await db.execute(text("SELECT count(*) FROM agent.bindings WHERE org_id = :o"), {"o": org})).scalar_one()
    assert n_bind == 0
    assert (await api.send("DELETE", f"/providers/{bad}")).status_code == 404
    log = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'provider.deleted'"))).scalar_one()
    assert log == 2


async def test_skip_7_seeds_starter_rules_and_skip_11_sets_default_backup(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    assert (await api.send("POST", "/setup/steps/7/skip")).status_code == 200
    rules = (await db.execute(text("SELECT code, is_enabled FROM refinery.rules WHERE org_id = :o ORDER BY code"),
                              {"o": org})).all()
    assert [r.code for r in rules] == ["R-01", "R-02", "R-03", "R-04", "R-05", "R-06"]
    assert all(r.is_enabled for r in rules)
    assert (await api.send("POST", "/setup/steps/11/skip")).status_code == 200
    cfg = (await db.execute(text("SELECT settings -> 'backup' FROM core.organizations WHERE id = :o"),
                            {"o": org})).scalar()
    assert cfg == {"frequency": "daily", "time_of_day": "02:00", "retention_count": 7, "destination": "local"}
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    assert items[7]["done"] and items[11]["done"]


async def test_skip_7_keeps_existing_rules(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """Không đụng quy tắc Owner đã có (vd đã tạo ở màn Quy tắc trước khi quay lại trình thiết lập)."""
    api: Api = owner_api
    org = await org_id(db)
    r = await api.send("POST", "/rules", {"name": "Riêng", "kind": "intent", "threshold": 0.7,
                                          "conditions": [{"type": "keyword_any", "values": ["giá"]}],
                                          "outputs": [{"set": "intent", "value": "AskedPrice"}]})
    assert r.status_code in (200, 201), r.text
    assert (await api.send("POST", "/setup/steps/7/skip")).status_code == 200
    n = (await db.execute(text("SELECT count(*) FROM refinery.rules WHERE org_id = :o"), {"o": org})).scalar_one()
    assert n == 1


async def test_skip_7_again_does_not_reseed_after_owner_removed_defaults(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """Bấm "Để sau" lần hai (quay lại bước 7) sau khi Owner đã xoá bộ mặc định → không nạp lại."""
    api: Api = owner_api
    org = await org_id(db)
    assert (await api.send("POST", "/setup/steps/7/skip")).status_code == 200
    await db.execute(text("""DELETE FROM refinery.rule_versions
                             WHERE rule_id IN (SELECT id FROM refinery.rules WHERE org_id = :o)"""), {"o": org})
    await db.execute(text("DELETE FROM refinery.rules WHERE org_id = :o"), {"o": org})
    await db.commit()
    assert (await api.send("POST", "/setup/steps/7/skip")).status_code == 200
    n = (await db.execute(text("SELECT count(*) FROM refinery.rules WHERE org_id = :o"), {"o": org})).scalar_one()
    assert n == 0


def test_temp_password_has_no_ambiguous_characters() -> None:
    for _ in range(200):
        pw = crypto.temp_password()
        assert len(pw) == 14 and pw.count("-") == 2
        assert not set(pw) & set("0O1lIo5S")
