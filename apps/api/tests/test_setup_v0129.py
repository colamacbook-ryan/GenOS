"""v0.1.29 — bước 4 "Để sau" được (Boss 30/09: có công cụ, dùng hay không do Owner quyết, kèm cảnh báo rõ).

- Để sau bước 4 khi chưa có model → Hoàn tất được; `GET /setup/follow-up` có mục 4 chưa xong ("Chưa có model").
- Chọn model sau Hoàn tất (`PUT /setup/steps/4` từ `/guide/4`) → mục 4 tự xong.
- Để sau khi ĐÃ có nguồn gọi thử OK kèm model → vẫn chép model đó vào `agent.models` (v0.1.55: KHÔNG còn ghi
  `agent.bindings` — hồ sơ tiêu chuẩn tự phủ; mục 4 "xong" = có nguồn bật kèm model).
"""

from sqlalchemy import text

from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_ux_v0128 import _provider


async def _bound(db, org) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return dict((await db.execute(text("""SELECT b.agent_key, m.model_name FROM agent.bindings b
                                           JOIN agent.models m ON m.id = b.model_id WHERE b.org_id = :o"""),
                                  {"o": org})).all())


async def test_skip_step4_without_model_then_fix_after_finish(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    r = await api.send("POST", "/setup/steps/4/skip")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["steps"][3]["status"] == "skipped" and st["steps"][3]["required"] is False
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    assert items[4]["done"] is False and items[4]["status"] == "skipped"
    assert await _bound(db, org) == {}
    # Hoàn tất được dù chưa có model.
    r = await api.send("PUT", "/setup/steps/12")
    assert r.status_code == 200, r.text and r.json()["finished"]
    # Sau Hoàn tất: chọn model từ trang hướng dẫn → lưu được, mục 4 tự xong.
    pid = await _provider(api, db, "Nguồn mới", ok=True, tested=["qwen2.5-7b"])
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [pid]})
    assert r.status_code == 200, r.text
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    assert items[4]["done"] is True
    assert await _bound(db, org) == {}      # v0.1.55: bước 4 không còn ghi agent.bindings
    log = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'setup.step_skipped' "
                                 "AND target_id = '4'"))).scalar_one()
    assert log == 1


async def test_skip_step4_with_tested_model_still_adds_model_row(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    await _provider(api, db, "Lỗi", ok=False, tested=["x"])                  # nguồn lỗi không bao giờ được chọn
    await _provider(api, db, "Tốt", ok=True, tested=["text-embedding-3-small", "llama-3.1-8b"])
    assert (await api.send("POST", "/setup/steps/4/skip")).status_code == 200
    assert await _bound(db, org) == {}      # v0.1.55: không ghi agent.bindings
    models = (await db.execute(text("""SELECT m.model_name FROM agent.models m
                                       JOIN agent.providers p ON p.id = m.provider_id
                                       WHERE p.org_id = :o ORDER BY m.model_name"""), {"o": org})).scalars().all()
    assert models == ["llama-3.1-8b"]       # model đã gọi thử của nguồn tốt; nguồn lỗi không bao giờ được chọn
    items = {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}
    assert items[4]["done"] is True


async def test_step4_follow_up_tracks_real_data_after_provider_deleted(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    pid = await _provider(api, db, "Tạm", ok=True, tested=["m-1"])
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [pid]})).status_code == 200
    assert {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}[4]["done"] is True
    assert (await api.send("DELETE", f"/providers/{pid}")).status_code == 204
    # Bước 4 từng "done" nhưng giờ không còn model → lại hiện "Chưa có model".
    assert {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}[4]["done"] is False
