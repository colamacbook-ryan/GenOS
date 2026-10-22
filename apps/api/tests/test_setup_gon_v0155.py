"""v0.1.55 (G2 Thiết lập gọn) — trình thiết lập chỉ hỏi 4 thứ (mã thiết lập, Owner + PIN, tên tổ chức, nguồn AI);
mọi trường khác có mặc định ở máy chủ. Hình dạng payload PUT /setup/steps/1..12 giữ nguyên (mock-api.ts, do_setup).

- Bước 3: hai ô xưng hô mặc định "Sếp".
- Bước 4: KHÔNG còn ghi `agent.bindings` (hồ sơ tiêu chuẩn tự phủ); vẫn bảo đảm có dòng `agent.models`.
- Theo dõi bước 4 "xong" = có ít nhất một nhà cung cấp BẬT (không tính embedding / Jev) có model.
- Bước 7: 900 giây / 500 tin / lô 250 / tin cậy 0,6 + bộ quy tắc khởi đầu bật sẵn khi không gửi `rule_codes`.
- Bước 8: chọn mẫu là xong (thiếu tên / vai trò điền mặc định; không có `try_message` thì không thử trò chuyện).
- Bước 9: web thay ô tích bằng một dòng ghi chú nhưng GỬI `ack_boundaries: true`; thiếu trường hoặc `false` ⇒ 422.
- Bước 11: lịch sao lưu hằng ngày 02:00, giữ 7 bản khi đi qua / bỏ qua bước.
"""

import json
from typing import Any

import httpx
from sqlalchemy import text

from gh.refinery import presets
from gh.setup import routes as setup_routes
from tests.conftest import OWNER, Api
from tests.phase2 import org_id
from tests.test_ux_v0128 import _provider


def test_step11_note_states_the_real_gfs_retention() -> None:
    """Review P2-3: bước 11 từng nói "giữ 7 bản gần nhất" — thật ra vòng đời GFS giữ 7 ngày/4 tuần/12 tháng
    (`gh.backup`). Câu ở web phải khớp các hằng của máy chủ, không tự bịa số."""
    from pathlib import Path

    from gh import backup

    step11 = Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "setup" / "Step11Backup.tsx"
    src = step11.read_text(encoding="utf-8")
    want = f"giữ {backup.DAILY_KEEP} bản theo ngày, {backup.WEEKLY_KEEP} theo tuần, {backup.MONTHLY_KEEP} theo tháng"
    assert src.count(want) == 2  # hai câu: lịch đã ghi / chưa ghi
    assert "giữ 7 bản gần nhất" not in src


DEFAULT_BACKUP = {"frequency": "daily", "time_of_day": "02:00", "retention_count": 7, "destination": "local"}


async def _steps_1_2(client: httpx.AsyncClient) -> Api:
    api = Api(client)
    r = await api.send("PUT", "/setup/steps/1", {"token": "test-setup-token"})
    assert r.status_code == 200, r.text
    r = await api.send("PUT", "/setup/steps/2", {"token": "test-setup-token", **OWNER})
    assert r.status_code == 200, r.text
    return api


async def _bindings(db: Any) -> int:
    return int((await db.execute(text("SELECT count(*) FROM agent.bindings"))).scalar_one())


async def _settings(db: Any, org: Any) -> dict[str, Any]:
    await db.rollback()
    return dict((await db.execute(text("SELECT settings FROM core.organizations WHERE id = :o"),
                                  {"o": org})).scalar_one())


async def _addressing(db: Any, org: Any) -> dict[str, Any]:
    return dict((await db.execute(text("SELECT addressing FROM core.users WHERE org_id = :o"),
                                  {"o": org})).scalar_one())


async def _followup(api: Api) -> dict[int, dict[str, Any]]:
    return {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}


# ─── bước 3 ──────────────────────────────────────────────────────────────────────────────────────────────────

async def test_step3_needs_only_the_org_name_and_addresses_default_to_sep(client: httpx.AsyncClient, db: Any) -> None:
    api = await _steps_1_2(client)
    r = await api.send("PUT", "/setup/steps/3", {"org_name": ""})
    assert r.status_code == 422                                   # tên tổ chức vẫn bắt buộc
    r = await api.send("PUT", "/setup/steps/3", {"org_name": "Genesis Việt"})
    assert r.status_code == 200, r.text
    assert r.json()["console_ready"] is True
    org = await org_id(db)
    row = (await db.execute(text("SELECT name, timezone, currency FROM core.organizations WHERE id = :o"),
                            {"o": org})).one()
    assert (row.name, row.timezone, row.currency) == ("Genesis Việt", "Asia/Ho_Chi_Minh", "VND")
    addressing = await _addressing(db, org)
    assert addressing == {"self": "Sếp", "bot_calls_me": "Sếp"} == {
        "self": setup_routes.DEFAULT_ADDRESSING, "bot_calls_me": setup_routes.DEFAULT_ADDRESSING}


async def test_step3_explicit_addressing_still_wins_and_blank_is_rejected(client: httpx.AsyncClient, db: Any) -> None:
    api = await _steps_1_2(client)
    r = await api.send("PUT", "/setup/steps/3", {"org_name": "Genesis Việt", "self_name": "   "})
    assert r.status_code == 422 and "self_name" in json.dumps(r.json(), ensure_ascii=False)   # rỗng ≠ thiếu
    r = await api.send("PUT", "/setup/steps/3", {"org_name": "Genesis Việt", "self_name": "Anh", "bot_calls_me": "Chị"})
    assert r.status_code == 200, r.text
    org = await org_id(db)
    addressing = await _addressing(db, org)
    assert addressing == {"self": "Anh", "bot_calls_me": "Chị"}


# ─── bước 4 ──────────────────────────────────────────────────────────────────────────────────────────────────

async def test_step4_writes_no_agent_bindings_but_keeps_the_model_row(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    good = await _provider(api, db, "Nguồn tốt", ok=True, tested=["text-embedding-3-small", "qwen2.5-7b"])
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [good]})
    assert r.status_code == 200, r.text
    assert await _bindings(db) == 0                                              # KHÔNG ghi agent.bindings
    models = (await db.execute(text("SELECT model_name FROM agent.models WHERE provider_id = :p"),
                               {"p": good})).scalars().all()
    assert models == ["qwen2.5-7b"]                                              # vẫn có model (bỏ embedding)
    assert (await _followup(api))[4]["done"] is True
    items = (await api.get("/agents/bindings")).json()["items"]
    assert items and all(i["binding"] is None for i in items)           # màn API & Model: chưa gán riêng agent nào
    # Lưu lại không đổi gì, không sinh binding.
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [good]})).status_code == 200
    assert await _bindings(db) == 0


async def test_skip_step4_adds_tested_model_without_bindings(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    await _provider(api, db, "Tốt", ok=True, tested=["llama-3.1-8b"])
    assert (await api.send("POST", "/setup/steps/4/skip")).status_code == 200
    assert await _bindings(db) == 0
    assert (await _followup(api))[4]["done"] is True
    # chạy lại an toàn: bỏ qua lần nữa không nhân đôi model
    assert (await api.send("POST", "/setup/steps/4/skip")).status_code == 200
    assert int((await db.execute(text("SELECT count(*) FROM agent.models"))).scalar_one()) == 1


async def test_followup_step4_means_an_enabled_source_with_a_real_model(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    org = await org_id(db)
    assert (await _followup(api))[4]["done"] is False

    async def add(kind: str, model: str, *, enabled: bool = True, model_enabled: bool = True) -> Any:
        pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, is_enabled)
                                        VALUES (:o, :k, :n, :e) RETURNING id"""),
                                {"o": org, "k": kind, "n": f"{kind}-{model}", "e": enabled})).scalar_one()
        await db.execute(text("""INSERT INTO agent.models (provider_id, model_name, is_enabled)
                                 VALUES (:p, :m, :e)"""), {"p": pid, "m": model, "e": model_enabled})
        # B2 (F010): "xong" cần nguồn DÙNG ĐƯỢC — nguồn khoá API phải có khoá bật.
        await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4)
                                 VALUES (:p, 'K', :s, '1234')"""), {"p": pid, "s": b"\x00e"})
        await db.commit()
        return pid

    await add("system_one", "jev-1")                                        # Jev không sinh văn bản
    await add("openai_compat", "text-embedding-3-small")                   # model embedding
    await add("gemini", "gemini-x", enabled=False)                         # nguồn đang tắt
    await add("openai_compat", "gpt-x", model_enabled=False)               # model đã tắt
    assert (await _followup(api))[4]["done"] is False
    pid = await add("gemini", "gemini-2.5-flash")
    assert (await _followup(api))[4]["done"] is True
    assert await _bindings(db) == 0                                        # không cần binding nào
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE id = :i"), {"i": pid})
    await db.commit()
    assert (await _followup(api))[4]["done"] is False                      # tắt nguồn ⇒ lại "Chưa có model"


# ─── bước 7 ──────────────────────────────────────────────────────────────────────────────────────────────────

async def test_step7_defaults_900_500_250_06_and_default_rules_on(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    good = await _provider(api, db, "Nguồn tốt", ok=True, tested=["qwen2.5-7b"])
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [good]})).status_code == 200
    r = await api.send("PUT", "/setup/steps/7", {})
    assert r.status_code == 200, r.text
    sched = (await api.get("/refinery/schedule")).json()
    assert (sched["interval_seconds"], sched["count_threshold"]) == (900, 500)
    row = (await db.execute(text("""SELECT interval_seconds, count_threshold, batch_size, min_confidence::float AS mc
                                    FROM refinery.schedule"""))).one()
    assert (row.interval_seconds, row.count_threshold, row.batch_size, row.mc) == (900, 500, 250, 0.6)
    assert setup_routes.STEP7_DEFAULTS == {"interval_seconds": 900, "count_threshold": 500, "batch_size": 250,
                                           "min_confidence": 0.6}
    rules = {x["code"]: x["enabled"] for x in (await api.get("/rules")).json()}
    assert rules == {p["code"]: bool(p["enabled"]) for p in presets.PRESETS} and all(rules.values())
    # chọn "ngành nào" = gửi đúng bộ quy tắc; danh sách rỗng vẫn nghĩa là tắt hết (khác với thiếu trường)
    r = await api.send("PUT", "/setup/steps/7", {"rule_codes": ["R-01", "R-06"]})
    assert r.status_code == 200, r.text
    rules = {x["code"]: x["enabled"] for x in (await api.get("/rules")).json()}
    assert [c for c, on in rules.items() if on] == ["R-01", "R-06"]
    assert (await api.send("PUT", "/setup/steps/7", {"rule_codes": []})).status_code == 200
    assert not any(x["enabled"] for x in (await api.get("/rules")).json())
    assert (await api.send("PUT", "/setup/steps/7", {"rule_codes": ["R-99"]})).status_code == 422
    assert (await api.send("PUT", "/setup/steps/7", {})).status_code == 200      # chạy lại không nhân đôi quy tắc
    assert len((await api.get("/rules")).json()) == len(presets.PRESETS)


# ─── bước 8–9 ────────────────────────────────────────────────────────────────────────────────────────────────

async def test_step8_template_only_has_no_try_chat_and_step9_defaults(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    good = await _provider(api, db, "Nguồn tốt", ok=True, tested=["qwen2.5-7b"])
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [good]})).status_code == 200
    r = await api.send("PUT", "/setup/steps/8", {"template": "commercial"})
    assert r.status_code == 200, r.text
    agent = r.json()["agent"]
    assert agent["name"] == setup_routes.STEP8_DEFAULT_NAME
    assert agent["try_reply"] is None and agent["try_error"] is None and agent["try_error_code"] is None
    assert agent["try_reasons"] == []
    row = (await db.execute(text("SELECT name, role_desc, template FROM agent.identities"))).one()
    assert (row.name, row.role_desc, row.template) == (setup_routes.STEP8_DEFAULT_NAME,
                                                       setup_routes.STEP8_DEFAULT_ROLE, "commercial")
    assert int((await db.execute(text("SELECT count(*) FROM agent.model_calls"))).scalar_one()) == 0   # không gọi model
    # Bước 9: thiếu `ack_boundaries` hoặc `false` ⇒ API vẫn đòi xác nhận (F-N2: không xác nhận ngầm).
    for body in ({}, {"ack_boundaries": False}, {"autonomy_level": 4}):
        r = await api.send("PUT", "/setup/steps/9", body)
        assert r.status_code == 422 and "ack_boundaries" in json.dumps(r.json(), ensure_ascii=False), body
    # Web gửi `true` rõ ràng, thiếu mức ⇒ mức mặc định 4.
    r = await api.send("PUT", "/setup/steps/9", {"ack_boundaries": True})
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["autonomy_level"] == 4 and r.json()["hard_boundaries"]


async def test_step8_with_try_message_still_tries_the_chat(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    good = await _provider(api, db, "Nguồn tốt", ok=True, tested=["qwen2.5-7b"])
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [good]})).status_code == 200
    r = await api.send("PUT", "/setup/steps/8", {"name": "Trợ lý A", "role_desc": "Làm A", "try_message": "Chào"})
    assert r.status_code == 200, r.text
    agent = r.json()["agent"]
    assert agent["name"] == "Trợ lý A"
    assert agent["try_reply"] is not None or agent["try_error"]        # có thử: trả lời, hoặc lỗi thân thiện (chuỗi)
    assert agent["try_error"] is None or isinstance(agent["try_error"], str)


# ─── bước 10–11 ──────────────────────────────────────────────────────────────────────────────────────────────

async def test_step11_defaults_daily_0200_keep_7_on_pass_and_on_skip(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    org = await org_id(db)
    assert "backup" not in await _settings(db, org)
    r = await api.send("PUT", "/setup/steps/11", {})
    assert r.status_code == 200, r.text
    assert r.json()["backup"] == DEFAULT_BACKUP == setup_routes.DEFAULT_BACKUP
    assert (await _settings(db, org))["backup"] == DEFAULT_BACKUP


async def test_skip_step11_writes_the_default_schedule_once_and_never_overrides(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    org = await org_id(db)
    assert (await api.send("POST", "/setup/steps/11/skip")).status_code == 200
    assert (await _settings(db, org))["backup"] == DEFAULT_BACKUP
    assert (await _followup(api))[11]["done"] is True
    # Owner đổi lịch rồi bấm lại "Để sau" ⇒ không bị đè bằng mặc định
    custom = {"frequency": "weekly", "time_of_day": "04:30", "retention_count": 3, "destination": "local"}
    await db.execute(text("UPDATE core.organizations SET settings = settings || CAST(:s AS jsonb) WHERE id = :o"),
                     {"s": json.dumps({"backup": custom}), "o": org})
    await db.commit()
    assert (await api.send("POST", "/setup/steps/11/skip")).status_code == 200
    assert (await _settings(db, org))["backup"] == custom


async def test_step10_is_a_suggestion_skippable_and_never_required(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    r = await api.send("POST", "/setup/steps/10/skip")                   # nút "Để sau" của thẻ gợi ý mời đội ngũ
    assert r.status_code == 200, r.text
    steps = {s["n"]: s for s in r.json()["steps"]}
    assert steps[10]["status"] == "skipped" and steps[10]["required"] is False
    assert (await _followup(api))[10]["done"] is False                   # vẫn còn trong "Việc thiết lập tiếp"
    assert int((await db.execute(text("SELECT count(*) FROM core.users"))).scalar_one()) == 1


# ─── cả trình thiết lập: chỉ 4 lần nhập ─────────────────────────────────────────────────────────────────────

async def test_whole_wizard_with_four_inputs(client: httpx.AsyncClient, db: Any) -> None:
    """Mã thiết lập, Owner + PIN, tên tổ chức, nguồn AI — mọi bước khác đi bằng mặc định / "Để sau"."""
    api = await _steps_1_2(client)                                                    # (1) mã thiết lập (2) Owner + PIN
    assert (await api.send("PUT", "/setup/steps/3", {"org_name": "Genesis Việt"})).status_code == 200    # (3) tên
    good = await _provider(api, db, "Nguồn tốt", ok=True, tested=["qwen2.5-7b"])                 # (4) nguồn AI
    assert (await api.send("PUT", "/setup/steps/4", {"provider_ids": [good]})).status_code == 200
    assert (await api.send("PUT", "/setup/steps/7", {})).status_code == 200
    assert (await api.send("PUT", "/setup/steps/8", {"template": "secretary"})).status_code == 200
    assert (await api.send("PUT", "/setup/steps/9", {"ack_boundaries": True})).status_code == 200
    for n in (5, 6, 10, 11):
        assert (await api.send("POST", f"/setup/steps/{n}/skip")).status_code == 200, n
    r = await api.send("PUT", "/setup/steps/12", {})
    assert r.status_code == 200, r.text
    assert r.json()["finished"] is True
    assert await _bindings(db) == 0
    org = await org_id(db)
    assert (await _settings(db, org))["backup"] == DEFAULT_BACKUP
    items = await _followup(api)
    assert items[4]["done"] is True and items[11]["done"] is True and items[7]["done"] and items[8]["done"]
