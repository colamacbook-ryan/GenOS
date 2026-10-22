"""v0.1.55 (G1) — hồ sơ model tiêu chuẩn theo vai + "Chế độ tiêu chuẩn" / "Về mặc định".

(a) GET /defaults: đủ mục, `customized` tính đúng (settings = mặc định / khác mặc định / có dòng gán), nhân viên 403;
(b) reset từng mục xoá đúng khoá/dòng + MỘT dòng Action Log 'defaults.reset' không chứa giá trị cài đặt;
(c) reset-all cần PIN (423), có PIN thì KHÔNG đổi khoá API, mã PIN, Gen-hub, kênh báo động, tài khoản mạng xã hội;
(d) apply-standard chỉ xoá các dòng gán lõi;
(e) ma trận `profiles.resolve` (thuần): agy dùng được ở MỌI vai kể cả việc nền (v0.1.62), Claude CLI chỉ khi được
    phép, đúng tầng, haiku không effort;
(f) `_chain`: dòng gán thắng hồ sơ, hồ sơ thắng hạng; mức suy nghĩ theo thứ tự ưu tiên;
(g) migration 0034 chạy hai lần không đổi dòng gán/settings cũ; bản cài cũ ⇒ 'Đã tuỳ chỉnh' + gợi ý apply_standard.
"""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import orjson
import psycopg
import pytest
from sqlalchemy import text

from gh import crypto
from gh.db import sessionmaker
from gh.defaults import profiles, registry
from gh.defaults.routes import router as defaults_router
from gh.providers import agy_policy, catalog
from gh.providers.clients import AuthFailed, Completion, Message, ModelRejected
from gh.providers.router import KEY_AAD, ModelRouter, ModelUnavailable
from gh.setup.routes import DEFAULT_BACKUP
from tests.conftest import PG, Api, verify_pin
from tests.phase2 import org_id
from tests.test_model_router import OK, transport
from tests.test_p4_agents import _seed_agent
from tests.test_rbac_api import add_user, login_as

SQL_0034 = Path(__file__).resolve().parents[3] / "db" / "sql" / "0034_v0155_defaults.sql"
CONFIRM = {"confirm": True}
MSGS = [Message("user", "xin chào")]


def _mount(app: Any) -> None:
    """Router /defaults phải được `gh/app.py` gắn sẵn (prefix /api/v1)."""
    assert any(getattr(r, "original_router", None) is defaults_router
               or str(getattr(r, "path", "")).startswith("/api/v1/defaults") for r in app.routes)


@pytest.fixture
async def dapi(owner_api: Api, app: Any) -> Api:
    _mount(app)
    return owner_api


# ─── dữ liệu mẫu ──────────────────────────────────────────────────────────────

async def set_setting(db: Any, org: uuid.UUID, key: str, value: Any) -> None:
    await db.execute(text("""UPDATE core.organizations
                             SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), ARRAY[CAST(:k AS text)],
                                                      CAST(:v AS jsonb), true) WHERE id = :o"""),
                     {"o": org, "k": key, "v": json.dumps(value)})
    await db.commit()


async def setting(db: Any, org: uuid.UUID, key: str) -> Any:
    await db.rollback()
    return (await db.execute(text("SELECT settings->CAST(:k AS text) FROM core.organizations WHERE id = :o"),
                             {"o": org, "k": key})).scalar_one_or_none()


async def add_source(db: Any, org: uuid.UUID, kind: str, name: str, rank: int, models: list[str], *,
                     key: bool | None = None, default: str | None = None) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """Nguồn + model đã bật. Khoá API (kind không phải CLI) tự có một khoá giả trừ khi key=False."""
    cli = kind in ("claude_code_cli", "antigravity_cli")
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank,
                                                                 auth_state)
                                    VALUES (:o, :k, :n, :e, :r, 'ok') RETURNING id"""),
                            {"o": org, "k": kind, "n": name, "e": None if cli else f"https://{name}.test/v1",
                             "r": rank})).scalar_one()
    if not cli and key is not False:
        await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4, rotation_order)
                                 VALUES (:p, 'KEY-01', :s, 'aaaa', 0)"""),
                         {"p": pid, "s": crypto.encrypt(f"sk-{name}-aaaa".encode(), KEY_AAD)})
    ids: dict[str, uuid.UUID] = {}
    for m in models:
        # `agent.models.id` = core.uuid_v7(): hai dòng chèn cùng một mili-giây có thứ tự id NGẪU NHIÊN (~50%) ⇒ chờ để
        # model liệt kê trước luôn có id nhỏ hơn (các ca dưới giả định "tạo trước ⇒ legacy chọn theo id").
        await db.execute(text("SELECT pg_sleep(0.003)"))
        ids[m] = (await db.execute(text("""INSERT INTO agent.models (provider_id, model_name, is_default)
                                           VALUES (:p, :m, :d) RETURNING id"""),
                                   {"p": pid, "m": m, "d": m == default})).scalar_one()
    await db.commit()
    return pid, ids


async def bind(db: Any, org: uuid.UUID, agent_key: str, model_id: uuid.UUID, effort: str | None = None) -> None:
    await db.execute(text("""INSERT INTO agent.bindings (org_id, agent_key, model_id, context_tokens, effort)
                             VALUES (:o, :k, :m, 6000, :e)
                             ON CONFLICT (org_id, agent_key) DO UPDATE SET model_id = :m, effort = :e"""),
                     {"o": org, "k": agent_key, "m": model_id, "e": effort})
    await db.commit()


async def bound_keys(db: Any) -> set[str]:
    await db.rollback()
    return set((await db.execute(text("SELECT agent_key FROM agent.bindings"))).scalars().all())


async def items_of(api: Api) -> dict[str, dict[str, Any]]:
    r = await api.get("/defaults")
    assert r.status_code == 200, r.text
    return {i["key"]: i for i in r.json()["items"]}


async def reset_logs(db: Any) -> list[Any]:
    await db.rollback()
    return list((await db.execute(text("""SELECT target_id, detail FROM ops.action_log
                                          WHERE action = 'defaults.reset' ORDER BY at, id"""))).all())


async def owner_uid(db: Any) -> uuid.UUID:
    await db.rollback()
    return (await db.execute(text("SELECT id FROM core.users WHERE email = 'owner@example.vn'"))).scalar_one()


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (a) GET /defaults
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

CORE_ITEMS = {"binding:core.gen", "binding:core.briefing", "binding:core.refinery", "binding:core.reply",
              "binding:core.owner_reply", "binding:core.support"}   # v0.1.72: + 2 khoá chạy trong hộp
STATIC_ITEMS = {"gen", "coach", "triage", "refinery.schedule", "jev.preset", "ai_cost", "backup", "autonomy",
                "kho_label"}


async def test_list_has_every_item_and_nothing_customized_on_a_fresh_install(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    aid = await _seed_agent(db, org)
    r = await dapi.get("/defaults")
    assert r.status_code == 200, r.text
    body = r.json()
    keys = {i["key"] for i in body["items"]}
    assert keys == STATIC_ITEMS | CORE_ITEMS | {f"binding:agent:{aid}"}
    assert body["customized_count"] == 0 and not any(i["customized"] for i in body["items"])
    for it in body["items"]:   # web chỉ nhận chữ/số/bool — không object thô
        assert set(it) == {"key", "label", "scope", "group", "default_text", "current_text", "customized", "resettable"}
        assert all(isinstance(it[k], str) and it[k] for k in ("key", "label", "scope", "group", "default_text",
                                                               "current_text"))
        assert it["scope"] in ("org", "user") and isinstance(it["customized"], bool)
    by = {i["key"]: i for i in body["items"]}
    assert by["jev.preset"]["resettable"] is False and by["coach"]["scope"] == "user"
    assert all(by[k]["resettable"] for k in STATIC_ITEMS - {"jev.preset"})
    assert by["binding:core.briefing"]["label"].endswith("Bản tin Gen")


async def test_archived_agent_is_not_listed_in_binding_rows(dapi: Api, db: Any) -> None:
    """Agent đã Lưu trữ (archived_at) biến khỏi bảng Gán model — kể cả dòng gán còn sót (không hiện "Agent đã xoá")."""
    org = await org_id(db)
    live = await _seed_agent(db, org, name="Agent còn dùng")
    old = await _seed_agent(db, org, name="Agent đã lưu trữ")
    _, ids = await add_source(db, org, "openai_compat", "alpha", 1, ["alpha-flash"])
    await bind(db, org, f"agent:{old}", ids["alpha-flash"])   # dòng gán mồ côi (lưu trữ thật đã xoá nó)
    await db.execute(text("UPDATE agent.identities SET archived_at = now(), is_enabled = false WHERE id = :i"),
                     {"i": old})
    await db.commit()
    keys = {i["key"] for i in (await dapi.get("/defaults")).json()["items"]}
    assert f"binding:agent:{live}" in keys
    assert f"binding:agent:{old}" not in keys


async def test_customized_is_computed_from_real_data(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    # Giá trị = mặc định ⇒ KHÔNG "Đã đổi"; khác mặc định ⇒ "Đã đổi".
    await set_setting(db, org, "triage", {"enabled": True, "min_score": 30, "use_jev": True})
    await set_setting(db, org, "backup", DEFAULT_BACKUP)
    await set_setting(db, org, "gen", {"enabled": False, "roles": ["owner"], "retention_days": 90})
    by = await items_of(dapi)
    assert not any(by[k]["customized"] for k in ("triage", "backup", "gen"))   # `enabled` của Gen không tính
    await set_setting(db, org, "triage", {"enabled": True, "min_score": 55, "use_jev": True})
    await set_setting(db, org, "backup", {**DEFAULT_BACKUP, "frequency": "weekly"})
    await set_setting(db, org, "gen", {"enabled": True, "roles": ["owner", "manager"], "retention_days": 90})
    await set_setting(db, org, "ai_cost", {"daily_budget_vnd": 500000})
    by = await items_of(dapi)
    assert by["triage"]["customized"] and by["backup"]["customized"] and by["gen"]["customized"]
    assert by["ai_cost"]["customized"] and "500.000" in by["ai_cost"]["current_text"]
    assert "hằng tuần" in by["backup"]["current_text"] and "55" in by["triage"]["current_text"]
    # Lịch sàng lọc + dòng gán model.
    await db.execute(text("""INSERT INTO refinery.schedule (org_id, interval_seconds) VALUES (:o, 300)
                             ON CONFLICT (org_id) DO UPDATE SET interval_seconds = 300"""), {"o": org})
    _, ids = await add_source(db, org, "openai_compat", "alpha", 1, ["alpha-flash"])
    await bind(db, org, "core.reply", ids["alpha-flash"])
    by = await items_of(dapi)
    assert by["refinery.schedule"]["customized"] and by["binding:core.reply"]["customized"]
    assert not by["binding:core.gen"]["customized"]
    assert "alpha-flash" in by["binding:core.reply"]["current_text"]
    assert by["binding:core.gen"]["current_text"].startswith("Chuẩn: alpha-flash")
    got = await dapi.get("/defaults")
    assert {i["key"] for i in got.json()["items"] if i["customized"]} == {
        "gen", "ai_cost", "backup", "triage", "refinery.schedule", "binding:core.reply"}
    assert got.json()["customized_count"] == 6


async def test_coach_customized_only_when_the_row_differs_from_defaults(dapi: Api, db: Any) -> None:
    org, uid = await org_id(db), await owner_uid(db)
    await db.execute(text("INSERT INTO agent.gen_coach_prefs (user_id, org_id) VALUES (:u, :o)"), {"u": uid, "o": org})
    await db.commit()
    assert not (await items_of(dapi))["coach"]["customized"]     # dòng mặc định (cron tự tạo) không phải "Đã đổi"
    await db.execute(text("UPDATE agent.gen_coach_prefs SET bell = false WHERE user_id = :u"), {"u": uid})
    await db.commit()
    assert (await items_of(dapi))["coach"]["customized"]


@pytest.mark.parametrize("role", ["manager", "operator", "agent_staff", "auditor"])
async def test_non_owner_gets_403(dapi: Api, client: Any, db: Any, role: str) -> None:
    other = await login_as(client, db, role)
    try:
        for r in (await other.get("/defaults"),
                  await other.send("POST", "/defaults/triage/reset", CONFIRM),
                  await other.send("POST", "/defaults/apply-standard", CONFIRM),
                  await other.send("POST", "/defaults/reset-all", CONFIRM)):
            assert r.status_code == 403, r.text
    finally:
        await other.c.aclose()
    assert await reset_logs(db) == []


async def test_suggestions_apply_standard_and_background_key(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    sug = {s["key"]: s for s in (await dapi.get("/defaults")).json()["suggestions"]}
    assert set(sug) == {"background_key_missing"}                 # chưa có nguồn nào
    assert sug["background_key_missing"]["title"] == "Cần 1 khoá API để chạy lọc tin và bản tin"
    _, ids = await add_source(db, org, "openai_compat", "alpha", 1, ["alpha-flash"])
    await bind(db, org, "core.gen", ids["alpha-flash"])
    sug = (await dapi.get("/defaults")).json()["suggestions"]
    assert sug == []                                              # có khoá API; chỉ 1 dòng gán lõi
    await bind(db, org, "core.refinery", ids["alpha-flash"])
    sug = {s["key"]: s for s in (await dapi.get("/defaults")).json()["suggestions"]}
    assert set(sug) == {"apply_standard"}
    assert sug["apply_standard"]["title"] == "Áp model chuẩn theo vai? (đang dùng 1 model cho mọi việc)"
    assert sug["apply_standard"]["to"] == "/system?tab=brain#chuan"
    assert all(set(s) == {"key", "title", "body", "to"} for s in sug.values())
    # Nguồn khoá API tắt nhưng Owner đã cho CLI chạy việc nền ⇒ không nhắc thiếu khoá.
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE org_id = :o"), {"o": org})
    await set_setting(db, org, "ai", {"background_cli": ["claude_code_cli"]})
    assert "background_key_missing" not in {s["key"] for s in (await dapi.get("/defaults")).json()["suggestions"]}


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (b) reset từng mục
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

async def test_reset_triage_removes_the_key_and_logs_only_the_key(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    await set_setting(db, org, "triage", {"enabled": False, "min_score": 77, "use_jev": False})
    r = await dapi.send("POST", "/defaults/triage/reset", CONFIRM)
    assert r.status_code == 200, r.text
    assert r.json()["key"] == "triage" and r.json()["customized"] is False
    await db.rollback()
    assert (await db.execute(text("SELECT settings ? 'triage' FROM core.organizations WHERE id = :o"),
                             {"o": org})).scalar_one() is False
    logs = await reset_logs(db)
    assert len(logs) == 1 and logs[0].target_id == "triage" and logs[0].detail == {"value": "triage"}
    assert "77" not in orjson.dumps(logs[0].detail).decode()


async def test_reset_backup_rewrites_defaults_instead_of_deleting(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    await set_setting(db, org, "backup", {"frequency": "weekly", "time_of_day": "03:30", "retention_count": 3,
                                          "destination": "local"})
    assert (await dapi.send("POST", "/defaults/backup/reset", CONFIRM)).status_code == 200
    assert await setting(db, org, "backup") == DEFAULT_BACKUP        # xoá khoá = tắt sao lưu ⇒ phải còn khoá
    assert not (await items_of(dapi))["backup"]["customized"]


async def test_reset_coach_deletes_only_the_callers_row(dapi: Api, db: Any) -> None:
    org, uid = await org_id(db), await owner_uid(db)
    other = await add_user(db, "manager")
    other_id = (await db.execute(text("SELECT id FROM core.users WHERE email = :e"), {"e": other})).scalar_one()
    for u in (uid, other_id):
        await db.execute(text("INSERT INTO agent.gen_coach_prefs (user_id, org_id, bell) VALUES (:u, :o, false)"),
                         {"u": u, "o": org})
    await db.commit()
    assert (await dapi.send("POST", "/defaults/coach/reset", CONFIRM)).status_code == 200
    await db.rollback()
    left = set((await db.execute(text("SELECT user_id FROM agent.gen_coach_prefs"))).scalars().all())
    assert left == {other_id}


async def test_reset_binding_deletes_exactly_that_row(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    aid = await _seed_agent(db, org)
    _, ids = await add_source(db, org, "openai_compat", "alpha", 1, ["alpha-flash"])
    for k in ("core.gen", "core.reply", f"agent:{aid}"):
        await bind(db, org, k, ids["alpha-flash"])
    r = await dapi.send("POST", "/defaults/binding:core.gen/reset", CONFIRM)
    assert r.status_code == 200, r.text
    assert await bound_keys(db) == {"core.reply", f"agent:{aid}"}
    assert (await dapi.send("POST", f"/defaults/binding:agent:{aid}/reset", CONFIRM)).status_code == 200
    assert await bound_keys(db) == {"core.reply"}
    assert [x.target_id for x in await reset_logs(db)] == ["binding:core.gen", f"binding:agent:{aid}"]


async def test_reset_gen_keeps_the_enabled_switch(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    await set_setting(db, org, "gen", {"enabled": False, "roles": ["owner", "manager"], "retention_days": 30})
    assert (await dapi.send("POST", "/defaults/gen/reset", CONFIRM)).status_code == 200
    assert await setting(db, org, "gen") == {"enabled": False, "roles": ["owner"], "retention_days": 90}


async def test_reset_ai_cost_drops_the_cap_but_keeps_owner_prices(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    _, ids = await add_source(db, org, "openai_compat", "alpha", 1, ["alpha-flash"])
    await db.execute(text("""INSERT INTO agent.model_prices (model_id, org_id, in_vnd_per_mtok, out_vnd_per_mtok)
                             VALUES (:m, :o, 100, 300)"""), {"m": ids["alpha-flash"], "o": org})
    await db.commit()
    await set_setting(db, org, "ai_cost", {"daily_budget_vnd": 500000})
    assert (await dapi.send("POST", "/defaults/ai_cost/reset", CONFIRM)).status_code == 200
    assert (await setting(db, org, "ai_cost")) == {}
    price = (await db.execute(text("SELECT in_vnd_per_mtok, out_vnd_per_mtok FROM agent.model_prices"))).one()
    assert (float(price.in_vnd_per_mtok), float(price.out_vnd_per_mtok)) == (100.0, 300.0)


async def test_reset_refinery_schedule_restores_defaults(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    await db.execute(text("""INSERT INTO refinery.schedule (org_id, interval_seconds, count_threshold, batch_size,
                                                            min_confidence)
                             VALUES (:o, 60, 10, 20, 0.9)
                             ON CONFLICT (org_id) DO UPDATE SET interval_seconds = 60, count_threshold = 10,
                                batch_size = 20, min_confidence = 0.9"""), {"o": org})
    await db.commit()
    assert (await dapi.send("POST", "/defaults/refinery.schedule/reset", CONFIRM)).status_code == 200
    await db.rollback()
    r = (await db.execute(text("""SELECT interval_seconds, count_threshold, batch_size, min_confidence
                                  FROM refinery.schedule WHERE org_id = :o"""), {"o": org})).one()
    assert (r.interval_seconds, r.count_threshold, r.batch_size, float(r.min_confidence)) == (900, 500, 250, 0.6)


async def test_reset_errors(dapi: Api, db: Any) -> None:
    r = await dapi.send("POST", "/defaults/khong.co/reset", CONFIRM)
    assert r.status_code == 404 and r.json()["code"] == "DEFAULTS_KEY_UNKNOWN"
    r = await dapi.send("POST", "/defaults/jev.preset/reset", CONFIRM)          # chỉ hiển thị
    assert r.status_code == 409 and r.json()["code"] == "DEFAULTS_NOT_RESETTABLE"
    assert (await dapi.send("POST", "/defaults/triage/reset", {"confirm": False})).status_code == 422
    assert (await dapi.send("POST", "/defaults/triage/reset", {})).status_code == 422
    assert (await dapi.send("POST", "/defaults/apply-standard", {"confirm": False})).status_code == 422
    assert await reset_logs(db) == []                                             # lỗi không ghi Action Log


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (c) reset-all: cần PIN, không chạm khoá API / PIN / Gen-hub / kênh báo động / tài khoản mạng xã hội
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

PROTECTED = {
    "provider_keys": "SELECT id, provider_id, label, secret_enc, last4, is_enabled FROM agent.provider_keys",
    "providers": "SELECT id, org_id, kind, name, endpoint, is_enabled, failover_rank FROM agent.providers",
    "models": "SELECT id, provider_id, model_name, effort, is_default, is_enabled FROM agent.models",
    "users": "SELECT id, email, password_hash, pin_hash FROM core.users",
    "hub_links": "SELECT * FROM agent.hub_links",
    "notify_channels": "SELECT * FROM ops.notify_channels",
    "social_accounts": "SELECT * FROM core.social_accounts",
    "org_identity": "SELECT id, name, timezone, currency FROM core.organizations",
}


async def snapshot(db: Any) -> dict[str, tuple[int, str]]:
    await db.rollback()
    out: dict[str, tuple[int, str]] = {}
    for name, sql in PROTECTED.items():
        rows = [str(tuple(r)) for r in (await db.execute(text(sql))).all()]
        out[name] = (len(rows), hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest())
    return out


async def seed_protected(db: Any, org: uuid.UUID) -> None:
    await add_source(db, org, "openai_compat", "alpha", 1, ["alpha-flash"])
    await db.execute(text("INSERT INTO agent.hub_links (org_id, enabled) VALUES (:o, true)"), {"o": org})
    await db.execute(text("""INSERT INTO ops.notify_channels (org_id, token_enc, chat_id)
                             VALUES (:o, :t, '12345')"""), {"o": org, "t": crypto.encrypt(b"123:telegram-token", b"x")})
    await db.execute(text("""INSERT INTO core.social_accounts (org_id, platform, label)
                             VALUES (:o, 'facebook', 'Trang A')"""), {"o": org})
    await db.commit()


async def test_reset_all_needs_pin_then_resets_everything_resettable(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    aid = await _seed_agent(db, org)
    await seed_protected(db, org)
    _, ids = await add_source(db, org, "gemini", "beta", 2, ["beta-pro"])
    for k in ("core.gen", "core.refinery", f"agent:{aid}"):
        await bind(db, org, k, ids["beta-pro"], "high" if k == "core.gen" else None)
    await set_setting(db, org, "triage", {"min_score": 90})
    await set_setting(db, org, "backup", {"frequency": "monthly", "time_of_day": "05:00", "retention_count": 2,
                                          "destination": "local"})
    await set_setting(db, org, "ai_cost", {"daily_budget_vnd": 1000})
    await set_setting(db, org, "ai", {"background_cli": ["claude_code_cli"]})   # quyết định rủi ro của Sếp — không đụng
    await set_setting(db, org, "autonomy_level", 2)
    before = await snapshot(db)
    assert before["users"][0] >= 1 and (await db.execute(text(
        "SELECT pin_hash IS NOT NULL FROM core.users WHERE email = 'owner@example.vn'"))).scalar_one()

    r = await dapi.send("POST", "/defaults/reset-all", CONFIRM)
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED", r.text
    assert await bound_keys(db) == {"core.gen", "core.refinery", f"agent:{aid}"}   # chưa PIN ⇒ không đổi gì
    assert await reset_logs(db) == []

    await verify_pin(dapi)
    r = await dapi.send("POST", "/defaults/reset-all", CONFIRM)
    assert r.status_code == 200, r.text
    assert await bound_keys(db) == set()
    assert await setting(db, org, "triage") is None and await setting(db, org, "backup") == DEFAULT_BACKUP
    assert await setting(db, org, "ai_cost") == {} and await setting(db, org, "autonomy_level") == 4
    assert await setting(db, org, "ai") == {"background_cli": ["claude_code_cli"]}
    assert await snapshot(db) == before                      # khoá API, PIN, Gen-hub, kênh báo động, MXH, tổ chức
    by = await items_of(dapi)
    assert (await dapi.get("/defaults")).json()["customized_count"] == 0 and not by["triage"]["customized"]
    logs = await reset_logs(db)
    assert len(logs) == 1 and logs[0].target_id == "all" and logs[0].detail["value"] == "all"
    assert "90" not in orjson.dumps(logs[0].detail).decode() and "monthly" not in orjson.dumps(logs[0].detail).decode()


async def test_org_autonomy_is_its_own_item_counted_and_pin_guarded(dapi: Api, db: Any) -> None:
    """F-R8: mức tự trị của tổ chức là MỘT MỤC của sổ (hiện chữ mặc định/đang dùng, đếm vào "Đã đổi"), không còn bị
    "Về mặc định tất cả" đổi ngầm. Về mặc định riêng mục này cũng cần phiên PIN (nâng mức tự trị)."""
    org = await org_id(db)
    it = (await items_of(dapi))["autonomy"]
    assert it["label"] == "Mức tự trị của tổ chức" and it["resettable"] is True and not it["customized"]
    assert it["default_text"] == "Soạn sẵn chờ duyệt (mức 4)" == it["current_text"]
    await set_setting(db, org, "autonomy_level", 3)
    body = (await dapi.get("/defaults")).json()
    it = {i["key"]: i for i in body["items"]}["autonomy"]
    assert it["customized"] and it["current_text"] == "Gợi ý hành động (mức 3)"
    assert body["customized_count"] == 1                          # CHỈ mức tự trị khác mặc định vẫn đếm: nút không tắt
    r = await dapi.send("POST", "/defaults/autonomy/reset", CONFIRM)
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED", r.text
    assert await setting(db, org, "autonomy_level") == 3 and await reset_logs(db) == []
    await verify_pin(dapi)
    r = await dapi.send("POST", "/defaults/autonomy/reset", CONFIRM)
    assert r.status_code == 200 and r.json()["customized"] is False, r.text
    assert await setting(db, org, "autonomy_level") == 4
    assert [x.target_id for x in await reset_logs(db)] == ["autonomy"]


async def test_reset_all_requires_confirm(dapi: Api, db: Any) -> None:
    await verify_pin(dapi)
    assert (await dapi.send("POST", "/defaults/reset-all", {"confirm": False})).status_code == 422
    assert await reset_logs(db) == []


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (d) apply-standard
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

async def test_apply_standard_only_removes_core_bindings(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    aid = await _seed_agent(db, org)
    await seed_protected(db, org)
    _, ids = await add_source(db, org, "gemini", "beta", 2, ["beta-flash"])
    for k in ("core.gen", "core.briefing", "core.refinery", "core.reply", f"agent:{aid}"):
        await bind(db, org, k, ids["beta-flash"])
    before = await snapshot(db)
    assert "apply_standard" in {s["key"] for s in (await dapi.get("/defaults")).json()["suggestions"]}
    r = await dapi.send("POST", "/defaults/apply-standard", CONFIRM)
    assert r.status_code == 200 and r.json() == {"removed": 4}, r.text
    assert await bound_keys(db) == {f"agent:{aid}"}
    assert await snapshot(db) == before                                         # nguồn và khoá giữ nguyên
    assert "apply_standard" not in {s["key"] for s in (await dapi.get("/defaults")).json()["suggestions"]}
    logs = await reset_logs(db)
    assert len(logs) == 1 and logs[0].target_id == "apply_standard" and logs[0].detail["value"] == "apply_standard"


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (e) ma trận profiles.resolve (hàm thuần)
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

CLAUDE = "claude_code_cli"
AGY = "antigravity_cli"
API = "openai_compat"
SOURCES: dict[str, tuple[dict[str, Any], list[str]]] = {
    "claude": ({"id": "p-claude", "kind": CLAUDE, "name": "Claude", "failover_rank": 1}, ["haiku", "sonnet", "opus"]),
    "agy": ({"id": "p-agy", "kind": AGY, "name": "Agy", "failover_rank": 2}, ["gemini-3.8-flash", "gemini-3.1-pro"]),
    "api": ({"id": "p-api", "kind": API, "name": "Api", "failover_rank": 3, "has_key": True},
            ["g/flash-lite", "g/flash", "g/pro"]),
}


def rows_of(*names: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    provs = [SOURCES[n][0] for n in names]
    models = [{"id": f"{n}:{m}", "provider_id": SOURCES[n][0]["id"], "model_name": m, "is_enabled": True}
              for n in names for m in SOURCES[n][1]]
    return provs, models


def picks(names: tuple[str, ...], role: str, *, background: bool = False, bg_cli: set[str] | None = None,
          tier: str | None = None) -> list[tuple[str, str, str | None]]:
    p, m = rows_of(*names)
    # Ma trận hồ sơ chạy với hộp riêng ĐANG CHẠY (vai qua hộp vẫn dùng được agy); hộp tắt: test_agy_placement_v0163.
    got = profiles.resolve(p, m, role, background=background, bg_cli_allowed=bg_cli or set(), tier_override=tier,
                           agy=agy_policy.decide(role, box="ok"))
    return [(c["provider_kind"], c["model_name"], c["effort"]) for c in got]


BG_ROLES = ["core.briefing", "core.refinery", "agent:7"]
SETS = [("claude",), ("agy",), ("api",), ("claude", "agy", "api"), ("agy", "api"), ("claude", "agy")]


@pytest.mark.parametrize("role", BG_ROLES)
@pytest.mark.parametrize("names", SETS)
@pytest.mark.parametrize("bg_cli", [set(), {CLAUDE}])
@pytest.mark.parametrize("background", [True, False])
def test_background_roles_use_agy_and_claude_only_when_allowed(role: str, names: tuple[str, ...], bg_cli: set[str],
                                                               background: bool) -> None:
    got = picks(names, role, background=background, bg_cli=bg_cli)
    kinds = [k for k, _m, _e in got]
    assert (AGY in kinds) == ("agy" in names)                     # v0.1.62: agy chạy được việc nền, không cần cho phép
    assert (CLAUDE in kinds) == ("claude" in names and CLAUDE in bg_cli)        # F-86: CLI chỉ khi Owner cho phép
    assert (API in kinds) == ("api" in names)                     # thiếu khoá API ⇒ KHÔNG tự chuyển sang Claude CLI
    if "api" in names:
        want = "g/flash-lite" if role != "agent:7" else "g/flash"
        assert (API, want, None) in got
    if "agy" in names:      # agy không có model tầng "nhanh" ⇒ tầng gần nhất (flash), gửi mức rõ ràng "low"
        assert (AGY, "gemini-3.8-flash", "low") in got
    if names == ("agy",):
        assert got == [(AGY, "gemini-3.8-flash", "low")]


def test_background_tiers_on_claude_cli_when_allowed() -> None:
    assert picks(("claude",), "core.briefing", bg_cli={CLAUDE}) == [(CLAUDE, "haiku", None)]    # haiku ⇒ không effort
    assert picks(("claude",), "core.refinery", bg_cli={CLAUDE}) == [(CLAUDE, "haiku", None)]
    assert picks(("claude",), "agent:1", bg_cli={CLAUDE}) == [(CLAUDE, "sonnet", None)]


def test_gen_picks_the_right_tier_and_effort_per_source() -> None:
    assert picks(("claude",), "core.gen") == [(CLAUDE, "sonnet", "medium")]
    assert picks(("api",), "core.gen") == [(API, "g/flash", None)]                              # khoá API: không effort
    assert picks(("agy",), "core.gen") == [(AGY, "gemini-3.8-flash", "medium")]                 # agy flash = balanced
    assert picks(("claude", "agy", "api"), "core.gen") == [
        (CLAUDE, "sonnet", "medium"), (AGY, "gemini-3.8-flash", "medium"), (API, "g/flash", None)]
    # Cờ `background` của lượt gọi chỉ chặn Claude CLI chưa được phép; agy vẫn chạy (v0.1.62).
    assert picks(("claude", "agy", "api"), "core.gen", background=True) == [
        (AGY, "gemini-3.8-flash", "medium"), (API, "g/flash", None)]


def test_reply_uses_agy_only_through_the_box() -> None:
    """H2: core.reply nói chuyện với người ngoài ⇒ agy chạy QUA HỘP: hộp tắt thì chỉ còn Claude CLI và API."""
    assert picks(("claude", "agy", "api"), "core.reply") == [
        (CLAUDE, "sonnet", "low"), (AGY, "gemini-3.8-flash", "low"), (API, "g/flash", None)]
    assert picks(("agy",), "core.reply") == [(AGY, "gemini-3.8-flash", "low")]
    p, m = rows_of("claude", "agy", "api")
    down = profiles.resolve(p, m, "core.reply", background=False, bg_cli_allowed=set(),
                            agy=agy_policy.decide("core.reply", box="down"))
    assert [c["provider_kind"] for c in down] == [CLAUDE, API]
    assert profiles.resolve(*rows_of("agy"), "core.reply", background=False, bg_cli_allowed=set(),
                            agy=agy_policy.decide("core.reply", box="not_installed")) == []


def test_tier_override_forces_the_tier_but_keeps_the_rules() -> None:
    assert picks(("claude",), "core.gen", tier="deep") == [(CLAUDE, "opus", "medium")]
    assert picks(("claude",), "core.gen", tier="fast") == [(CLAUDE, "haiku", None)]
    assert picks(("api",), "core.gen", tier="strong") == [(API, "g/pro", None)]
    # gemini-3.1-pro của agy chỉ nhận low/high ⇒ mức "medium" của hồ sơ bị bỏ.
    assert picks(("agy",), "core.gen", tier="strong") == [(AGY, "gemini-3.1-pro", None)]
    # Sàng lọc (việc nền) ép tầng mạnh: agy chạy được (mức "low" của hồ sơ pro nhận), Claude CLI thì không.
    assert picks(("claude", "agy", "api"), "core.refinery", bg_cli=set(), tier="strong") == [
        (AGY, "gemini-3.1-pro", "low"), (API, "g/pro", None)]
    assert picks(("claude",), "core.refinery", tier="strong") == []                            # CLI chưa được phép nền
    assert picks(("api",), "core.gen", tier="auto") == [(API, "g/flash", None)]


def test_missing_tier_uses_nearest_and_never_invents_models() -> None:
    def api_only(models: list[str], role: str = "core.refinery") -> list[str]:
        p = [{"id": "x", "kind": API, "name": "X", "failover_rank": 1}]
        m = [{"id": f"m{i}", "provider_id": "x", "model_name": n, "is_enabled": True} for i, n in enumerate(models)]
        return [c["model_name"] for c in profiles.resolve(p, m, role, background=True, bg_cli_allowed=set())]
    assert api_only(["a/pro"]) == ["a/pro"]                       # thiếu nhanh ⇒ lấy tầng gần nhất (duy nhất)
    assert api_only(["a/pro", "a/flash"]) == ["a/flash"]          # nhanh thiếu ⇒ gần nhất là balanced
    assert api_only(["a/pro", "a/flash-lite"], "core.reply") == ["a/flash-lite"]   # hoà khoảng cách ⇒ tầng rẻ hơn
    assert api_only(["a/pro", "a/flash-lite", "a/flash"], "core.reply") == ["a/flash"]
    assert api_only([]) == []                                     # không tự thêm dòng


def test_disabled_embedding_nokey_and_unknown_roles_are_skipped() -> None:
    p, m = rows_of("api")
    for row in m:
        row["is_enabled"] = row["model_name"] != "g/flash"
    m.append({"id": "e", "provider_id": "p-api", "model_name": "text-embedding-3", "is_enabled": True})
    got = profiles.resolve(p, m, "core.gen", background=False, bg_cli_allowed=set())
    assert [c["model_name"] for c in got] == ["g/flash-lite"]     # flash tắt ⇒ gần nhất (tie ⇒ rẻ hơn); embedding bị bỏ
    assert profiles.resolve([{**p[0], "has_key": False}], m, "core.gen", background=False,
                            bg_cli_allowed=set()) == []
    assert profiles.resolve([{**p[0], "is_enabled": False}], m, "core.gen", background=False,
                            bg_cli_allowed=set()) == []
    assert profiles.resolve(p, m, "core.embedding", background=False, bg_cli_allowed=set()) == []
    assert profiles.resolve([{"id": "j", "kind": "system_one", "name": "Jev"}], m, "core.gen", background=False,
                            bg_cli_allowed=set()) == []


def test_resolve_orders_by_failover_rank() -> None:
    p, m = rows_of("claude", "api")
    p[0]["failover_rank"], p[1]["failover_rank"] = 5, 1
    got = profiles.resolve(p, m, "core.gen", background=False, bg_cli_allowed=set())
    assert [c["provider_kind"] for c in got] == [API, CLAUDE]


def reason_of(names: tuple[str, ...], role: str, *, with_models: bool = True, bg_cli: set[str] | None = None,
              no_key: bool = False) -> str:
    """`missing_reason` (v0.1.58) trên dữ liệu mẫu; `with_models=False` ⇒ mọi nguồn 0 model."""
    p, m = rows_of(*names)
    if not with_models:
        m = []
    if no_key:
        p = [{**x, "has_key": False} if x["kind"] == API else x for x in p]
    return profiles.missing_reason(p, m, role, bg_cli or set(), agy_policy.decide(role, box="ok"))


def test_only_agy_with_model_gives_every_core_role_a_candidate() -> None:
    """v0.1.62: chỉ có agy (có model) ⇒ các vai core có ứng viên, hết lý do "cần khoá API (Antigravity chỉ dùng cho
    Gen)". Gói G: core.reply (ngoại giao) là ngoại lệ — xem test_reply_skips_agy_because_it_is_the_diplomatic_layer."""
    for role in ("core.gen", "core.briefing", "core.refinery", "agent:7"):
        assert picks(("agy",), role) == [(AGY, "gemini-3.8-flash", "medium" if role == "core.gen" else "low")], role


def test_missing_reason_agy_without_model_points_at_the_test_button() -> None:
    # Các vai core (kể cả vai nền): agy được dùng nhưng 0 model ⇒ bảo bấm Kiểm tra kết nối ở nguồn đó (đường tự sửa của
    # Sếp) — kể cả vai qua hộp khi hộp chạy. Hộp tắt (H2): vai qua hộp (core.reply, core.refinery) nói đúng lý do hộp,
    # KHÔNG bảo bấm Kiểm tra kết nối ở Agy.
    for role in ("core.gen", "core.refinery", "core.briefing", "agent:7", "core.reply"):
        assert reason_of(("agy",), role, with_models=False) == "chưa có model — bấm Kiểm tra kết nối ở Agy", role
    p, m = rows_of("agy")
    for role in ("core.reply", "core.refinery"):
        assert profiles.missing_reason(p, [], role, set(), agy_policy.decide(role, box="down")) \
            == agy_policy.REASON_BOX_DOWN


def test_missing_reason_source_allowed_but_empty_and_fallback() -> None:
    # Có khoá API mà 0 model ⇒ nguồn được phép nhưng chưa có model.
    # Nguồn khoá API 0 model: "Kiểm tra kết nối" chỉ tự thêm model cho CLI ⇒ bảo chọn model, không bảo bấm kiểm tra.
    assert reason_of(("api",), "core.refinery", with_models=False) == "chưa chọn model ở Api"
    # Claude CLI chưa được cho chạy việc nền ⇒ cần khoá API hoặc Antigravity CLI; core.reply dùng được Claude CLI.
    assert reason_of(("claude",), "core.briefing") == "cần khoá API hoặc đăng nhập Antigravity CLI"
    assert profiles.REASON_NEED_API == "cần khoá API hoặc đăng nhập Antigravity CLI"
    assert reason_of(("claude",), "core.reply", with_models=False) == "chưa có model — bấm Kiểm tra kết nối ở Claude"
    # Nguồn khoá API không có khoá thì không "được phép".
    # Gói G: agy chưa dùng cho core.reply nên câu không còn nhắc Antigravity CLI.
    assert reason_of(("api",), "core.reply", no_key=True) == "cần khoá API hoặc Claude Code CLI"
    # Không nguồn nào / vai lạ ⇒ câu cũ.
    assert reason_of((), "core.gen") == "chưa có nguồn phù hợp"
    assert profiles.missing_reason([], [], "core.embedding", set()) == "chưa có nguồn phù hợp"
    assert profiles.standard_text(None) == "Chuẩn: chưa có nguồn phù hợp"
    why = "cần khoá API hoặc Claude Code CLI"
    assert profiles.standard_text(None, why) == f"Chuẩn: {why}"
    assert profiles.standard_text({"model_name": "gemini-3.8-flash"}, "bỏ qua") == "Chuẩn: gemini-3.8-flash (tự chọn)"


def test_tier_of_and_effort_rules() -> None:
    assert [catalog.tier_of(CLAUDE, n) for n in ("haiku", "sonnet", "opus", "fable")] == [
        "fast", "balanced", "strong", "strong"]
    assert catalog.tier_of(AGY, "gemini-3.8-flash") == "balanced" and catalog.tier_of(AGY, "gemini-3.1-pro") == "strong"
    assert [catalog.tier_of(API, n) for n in ("gemini-2.5-flash-lite", "gpt-4o-mini", "claude-haiku-4", "x-nano",
                                                "gemini-2.5-flash", "claude-sonnet-4", "gemini-2.5-pro",
                                                "claude-opus-4", "deepseek-chat")] == [
        "fast", "fast", "fast", "fast", "balanced", "balanced", "strong", "strong", "balanced"]
    assert catalog.tier_of(API, "text-embedding-3") is None and catalog.tier_of("system_one", "jev") is None
    assert profiles.effort_for(CLAUDE, "haiku", "medium") is None
    assert profiles.effort_for(CLAUDE, "sonnet", "xhigh") == "xhigh" and profiles.effort_for(API, "x", "low") is None
    assert profiles.effort_for(AGY, "gemini-3.1-pro", "medium") is None and \
        profiles.effort_for(AGY, "gemini-3.8-flash", "medium") == "medium"


async def test_choice_options_contract(dapi: Api, db: Any) -> None:
    org = await org_id(db)

    async def opts() -> dict[str, dict[str, Any]]:
        async with sessionmaker()() as s:
            out = await profiles.choice_options(s, org)
        assert [t["tier"] for t in out["tiers"]] == ["auto", "fast", "balanced", "deep"]
        return {t["tier"]: t for t in out["tiers"]}

    none = await opts()
    assert not any(t["available"] for t in none.values()) and all(t["efforts"] == [] for t in none.values())
    await add_source(db, org, AGY, "Agy", 1, ["gemini-3.8-flash", "gemini-3.1-pro"])
    mixed = await opts()
    assert mixed["auto"]["available"] and not mixed["fast"]["available"]
    assert mixed["balanced"] == {"tier": "balanced", "available": True, "efforts": ["low", "medium", "high"]}
    assert mixed["deep"] == {"tier": "deep", "available": True, "efforts": ["low", "high"]}
    # v0.1.62: tầng chỉ có agy phục vụ được cho MỌI người hỏi (hàm không còn nhận owner / tainted).
    assert mixed["balanced"]["available"] and mixed["deep"]["available"]
    await add_source(db, org, CLAUDE, "Claude", 2, ["haiku", "sonnet", "opus"])
    got = await opts()
    assert got["fast"] == {"tier": "fast", "available": True, "efforts": []}                    # haiku không có effort
    assert got["balanced"]["efforts"] == ["low", "medium", "high"] and got["deep"]["available"]
    # Nguồn khoá API chưa có khoá ⇒ không dùng được; có khoá thì phục vụ được, không có mức suy nghĩ.
    await db.execute(text("DELETE FROM agent.models"))
    await db.execute(text("DELETE FROM agent.providers"))
    await db.commit()
    pid, _ = await add_source(db, org, API, "alpha", 3, ["g/flash"], key=False)
    assert not any(t["available"] for t in (await opts()).values())
    await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4, rotation_order)
                             VALUES (:p, 'K', :s, 'aaaa', 0)"""),
                     {"p": pid, "s": crypto.encrypt(b"sk-alpha-aaaa", KEY_AAD)})
    await db.commit()
    api_only = await opts()
    assert api_only["balanced"] == {"tier": "balanced", "available": True, "efforts": []}
    assert api_only["auto"]["available"] and not api_only["deep"]["available"]


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (f) _chain: dòng gán thắng hồ sơ, hồ sơ thắng hạng; mức suy nghĩ
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def router_for(redis: Any, *, claude: Any = None, agy: Any = None, handler: Any = None, box: Any = None) -> ModelRouter:
    """`agy` = agy trong tiến trình (core.gen của Owner, Bản tin, agent nội bộ); `box` = hộp riêng (core.refinery…)."""
    return ModelRouter(sessionmaker(), redis, transport=transport(handler or (lambda host, key: (200, OK))),
                       claude_factory=claude or (lambda: None), cli_factory=agy or (lambda: None),
                       box_factory=box or (lambda: None))


async def chain_of(r: ModelRouter, org: uuid.UUID, key: str, **kw: Any) -> list[tuple[str, str, str]]:
    async with sessionmaker()() as s:
        return [(c["provider"].name, c["model"].model_name, c["source"]) for c in await r._chain(s, org, key, **kw)]


async def test_chain_binding_beats_profile_and_profile_beats_rank(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    # alpha: "z-pro" tạo trước (legacy chọn nó theo id) nhưng vai sàng lọc muốn tầng nhanh ⇒ hồ sơ chọn flash-lite.
    _, a = await add_source(db, org, API, "alpha", 1, ["z-pro", "a-flash-lite"])
    _, b = await add_source(db, org, API, "beta", 2, ["b-flash"])
    r = router_for(redis)
    chain = await chain_of(r, org, "core.refinery")
    assert chain[:2] == [("alpha", "a-flash-lite", "profile"), ("beta", "b-flash", "profile")]
    assert ("alpha", "z-pro", "rank") in chain[2:]                    # hạng cũ chỉ còn là phần đuôi
    assert [c[:2] for c in chain].count(("alpha", "a-flash-lite")) == 1
    # Vai Gen (cân bằng): alpha chỉ có pro/flash-lite ⇒ gần nhất, hoà ⇒ rẻ hơn.
    assert (await chain_of(r, org, "core.gen"))[0] == ("alpha", "a-flash-lite", "profile")
    # Dòng gán của Owner thắng hồ sơ: beta lên đầu dù alpha hạng 1.
    await bind(db, org, "core.refinery", b["b-flash"])
    chain = await chain_of(r, org, "core.refinery")
    assert chain[0] == ("beta", "b-flash", "binding") and chain[1] == ("alpha", "a-flash-lite", "profile")
    assert [c[:2] for c in chain].count(("beta", "b-flash")) == 1
    # Lượt gọi ép tầng (chat chọn "Kỹ hơn"): lựa chọn của lượt này đứng trước dòng gán thường trực.
    chain = await chain_of(r, org, "core.refinery", tier="strong")
    assert chain[0] == ("alpha", "z-pro", "profile") and ("beta", "b-flash", "binding") in chain
    # Owner đã chốt model ("Dùng model này" = is_default) ⇒ giữ như trước khi không ép tầng.
    await db.execute(text("UPDATE agent.models SET is_default = true WHERE id = :m"), {"m": a["z-pro"]})
    await db.commit()
    await db.execute(text("DELETE FROM agent.bindings"))
    await db.commit()
    assert (await chain_of(r, org, "core.refinery"))[0] == ("alpha", "z-pro", "default")
    assert (await chain_of(r, org, "core.refinery", tier="fast"))[0] == ("alpha", "a-flash-lite", "profile")
    assert {c[2] for c in await chain_of(r, org, "core.embedding")} <= {"default", "rank"}      # khoá lạ: chỉ hạng


async def test_generate_sends_the_profile_model_to_the_provider(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    await add_source(db, org, API, "alpha", 1, ["z-pro", "a-flash-lite", "m-flash"])
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(orjson.loads(req.content)["model"])
        return httpx.Response(200, json=OK)

    r = ModelRouter(sessionmaker(), redis, transport=httpx.MockTransport(handler))
    await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False)
    await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False, tier="strong")
    await r.generate(org, agent_key="core.briefing", purpose="gen.briefing", messages=MSGS)
    assert seen == ["a-flash-lite", "m-flash", "z-pro", "a-flash-lite"]


async def test_provider_level_failure_is_not_retried_on_the_tail_link(app: Any, db: Any, redis: Any) -> None:
    """F-R3: hồ sơ chọn model X, đuôi `_chain` thêm model Y của CÙNG nguồn. Nguồn quá giờ / 5xx ở X ⇒ chỉ MỘT lượt gọi,
    MỘT dòng model_calls 'error', ngắt mạch tăng 1; model Y không bị thử lại."""
    org = await org_id(db)
    alpha, _ = await add_source(db, org, API, "alpha", 1, ["z-pro", "a-flash-lite"])
    await add_source(db, org, API, "beta", 2, ["b-flash"])
    assert len([c for c in await chain_of(router_for(redis), org, "core.refinery") if c[0] == "alpha"]) == 2
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        host = req.headers["host"].split(":")[0]
        seen.append((host, orjson.loads(req.content)["model"]))
        if host == "alpha.test":
            raise httpx.ReadTimeout("quá giờ", request=req)
        return httpx.Response(200, json=OK)

    r = ModelRouter(sessionmaker(), redis, transport=httpx.MockTransport(handler))
    out = await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert out.provider == "beta" and seen == [("alpha.test", "a-flash-lite"), ("beta.test", "b-flash")]
    rows = (await db.execute(text("""SELECT mo.model_name, c.status FROM agent.model_calls c
                                     JOIN agent.models mo ON mo.id = c.model_id ORDER BY c.id"""))).all()
    assert [(x.model_name, x.status) for x in rows] == [("a-flash-lite", "error"), ("b-flash", "ok")]
    assert int(await redis.get(f"gh:pfail:{alpha}") or 0) == 1
    # Chỉ còn nguồn lỗi: hết chuỗi sau ĐÚNG một lượt gọi (không nhân đôi thời gian chờ).
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE name = 'beta'"))
    await db.commit()
    seen.clear()
    await redis.delete(f"gh:pfail:{alpha}")
    with pytest.raises(ModelUnavailable) as e:
        await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert seen == [("alpha.test", "a-flash-lite")]
    assert any("không thử lại model khác" in x for x in e.value.reasons)
    assert int(await redis.get(f"gh:pfail:{alpha}") or 0) == 1


async def test_model_level_failure_still_falls_back_to_the_tail_model(app: Any, db: Any, redis: Any) -> None:
    """F-R3: lỗi cấp MODEL (400 từ chối model) thì model khác của cùng nguồn vẫn là đường lui hợp lệ."""
    org = await org_id(db)
    await add_source(db, org, API, "alpha", 1, ["z-pro", "a-flash-lite"])
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        model = orjson.loads(req.content)["model"]
        seen.append(model)
        if model == "a-flash-lite":
            return httpx.Response(400, json={"error": "model not supported"})
        return httpx.Response(200, json=OK)

    r = ModelRouter(sessionmaker(), redis, transport=httpx.MockTransport(handler))
    out = await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert out.model == "z-pro"
    assert list(dict.fromkeys(seen)) == ["a-flash-lite", "z-pro"]            # 400 có thể thử lại không json_mode


class ExpiredCli:
    """CLI giả hết phiên đăng nhập: mọi lượt đều AuthFailed."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self) -> "ExpiredCli":
        return self

    async def generate(self, model: str, messages: list[Message], **kw: Any) -> Completion:
        self.calls.append(model)
        raise AuthFailed("phiên hết hạn")


async def test_cli_auth_failure_is_not_retried_on_its_other_model(app: Any, db: Any, redis: Any) -> None:
    """F-R3: CLI (không có khoá) báo hết phiên ở model hồ sơ chọn ⇒ không gọi lại CLI đó bằng model đuôi."""
    org = await org_id(db)
    await add_source(db, org, CLAUDE, "Claude CLI", 1, ["sonnet", "haiku"])
    cli = ExpiredCli()
    r = router_for(redis, claude=cli)
    with pytest.raises(ModelUnavailable) as e:
        await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False)
    assert cli.calls == ["sonnet"] and any("xác thực lỗi" in x for x in e.value.reasons)


class EffortCli:
    """CLI giả ghi lại (model, effort) mỗi lượt; `reject` = tập mức bị CLI từ chối."""

    def __init__(self, reject: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.reject = reject or set()

    def __call__(self) -> "EffortCli":
        return self

    async def generate(self, model: str, messages: list[Message], **kw: Any) -> Completion:
        eff = kw.get("effort")
        self.calls.append((model, eff))
        if eff in self.reject:
            raise ModelRejected(f'invalid --effort "{eff}"', what="effort")
        return Completion("cli-ok", 3, 2)


async def test_effort_priority_param_binding_profile_model(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    _, ids = await add_source(db, org, CLAUDE, "Claude CLI", 1, ["sonnet", "haiku"])
    await db.execute(text("UPDATE agent.models SET effort = 'max' WHERE id = :m"), {"m": ids["sonnet"]})
    await db.commit()
    cli = EffortCli()
    r = router_for(redis, claude=cli)
    await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False)
    await r.generate(org, agent_key="core.reply", purpose="draft_translate", messages=MSGS, json_mode=False)
    await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False, effort="high")
    assert cli.calls == [("sonnet", "medium"), ("sonnet", "low"), ("sonnet", "high")]   # tham số > hồ sơ > model
    # Dòng gán có effort riêng thắng hồ sơ; dòng gán KHÔNG có effort giữ mức Owner đã đặt cho model (hồ sơ không đè).
    await bind(db, org, "core.gen", ids["sonnet"], "xhigh")
    await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False)
    await bind(db, org, "core.gen", ids["sonnet"], None)
    await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False)
    assert cli.calls[3:] == [("sonnet", "xhigh"), ("sonnet", "max")]
    # haiku không hỗ trợ mức nào ⇒ không gửi (kể cả khi tham số/hồ sơ đòi).
    await db.execute(text("DELETE FROM agent.bindings"))
    await db.commit()
    await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False, tier="fast",
                     effort="high")
    assert cli.calls[-1] == ("haiku", None)


async def test_profile_effort_rejected_by_cli_is_retried_without_it(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    await add_source(db, org, CLAUDE, "Claude CLI", 1, ["sonnet"])
    cli = EffortCli(reject={"medium"})
    r = router_for(redis, claude=cli)
    out = await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False)
    assert out.text == "cli-ok" and cli.calls == [("sonnet", "medium"), ("sonnet", None)]
    # Mức do Sếp/bên gọi chỉ định mà CLI từ chối ⇒ KHÔNG âm thầm bỏ mức.
    cli2 = EffortCli(reject={"high"})
    r2 = router_for(redis, claude=cli2)
    with pytest.raises(ModelUnavailable):
        await r2.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False,
                          effort="high")
    assert cli2.calls == [("sonnet", "high")]


async def test_background_without_api_or_agy_source_keeps_old_behaviour(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    await add_source(db, org, CLAUDE, "Claude CLI", 1, ["sonnet", "haiku"])
    cli = EffortCli()
    r = router_for(redis, claude=cli)
    for key, purpose in (("core.briefing", "gen.briefing"), ("core.refinery", "refinery"), ("agent:1", "duty_decide")):
        with pytest.raises(ModelUnavailable) as e:
            await r.generate(org, agent_key=key, purpose=purpose, messages=MSGS, tier="fast")
        assert e.value.reasons                                  # hết chuỗi như cũ, KHÔNG tự chuyển sang Claude CLI
    assert cli.calls == []


async def test_background_with_agy_runs_on_agy_with_profile_effort(app: Any, db: Any, redis: Any,
                                                                   boxctl: Any) -> None:  # noqa: F811
    """Có Antigravity CLI ⇒ việc nền chạy bằng agy (hồ sơ: tầng gần "nhanh" nhất = flash, mức "low"): sàng lọc tin
    (người lạ), Bản tin (review v0.1.63 P2-3) và trực việc của agent (P3: `duty_decide` đọc tin khách) đều qua hộp
    riêng; Claude CLI chưa được phép nên không bị gọi."""
    org = await org_id(db)
    await add_source(db, org, CLAUDE, "Claude CLI", 1, ["sonnet", "haiku"])
    await add_source(db, org, AGY, "Agy", 2, ["gemini-3.8-flash", "gemini-3.1-pro"])
    cli, agy, boxed = EffortCli(), EffortCli(), EffortCli()
    r = router_for(redis, claude=cli, agy=agy, box=boxed)
    for key, purpose in (("core.briefing", "gen.briefing"), ("core.refinery", "refinery"), ("agent:1", "duty_decide")):
        out = await r.generate(org, agent_key=key, purpose=purpose, messages=MSGS, tier="fast")
        assert out.text == "cli-ok", key
    assert cli.calls == [] and agy.calls == []
    assert boxed.calls == [("gemini-3.8-flash", "low")] * 3


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# API gán model: effort, source, model hồ sơ đang phủ
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

async def test_bindings_api_effort_source_and_standard(dapi: Api, db: Any) -> None:
    org = await org_id(db)
    _, c = await add_source(db, org, CLAUDE, "Claude CLI", 1, ["haiku", "sonnet"])
    _, a = await add_source(db, org, API, "alpha", 2, ["a-flash"])
    _, g = await add_source(db, org, AGY, "Agy", 3, ["gemini-3.8-flash", "gemini-3.1-pro"])
    listing = (await dapi.get("/agents/bindings")).json()
    by = {i["agent_key"]: i for i in listing["items"]}
    assert set(by) >= {"core.gen", "core.briefing", "core.refinery", "core.reply"}
    assert by["core.briefing"]["label"] == "Bản tin Gen"
    assert all(i["source"] == "standard" and i["binding"] is None for i in by.values())
    std = {k: v["standard"] for k, v in by.items()}
    assert std["core.gen"]["model_name"] == "sonnet" and std["core.gen"]["effort"] == "medium"
    assert std["core.gen"]["tier"] == "balanced" and std["core.gen"]["tier_label"] == "Cân bằng"
    assert std["core.refinery"]["model_name"] == "a-flash" and std["core.refinery"]["effort"] is None
    assert std["core.reply"]["model_name"] == "sonnet" and std["core.reply"]["effort"] == "low"
    # PUT có effort: kiểm theo catalog.
    r = await dapi.send("PUT", "/agents/bindings/core.gen", {"model_id": str(c["sonnet"]), "effort": "high"})
    assert r.status_code == 200 and r.json()["binding"]["effort"] == "high" and r.json()["source"] == "custom"
    r = await dapi.send("PUT", "/agents/bindings/core.gen", {"model_id": str(c["haiku"]), "effort": "high"})
    assert r.status_code == 422 and "effort" in r.json()["errors"]
    r = await dapi.send("PUT", "/agents/bindings/core.gen", {"model_id": str(a["a-flash"]), "effort": "low"})
    assert r.status_code == 422 and "không chỉnh được mức suy nghĩ" in r.json()["errors"]["effort"]
    r = await dapi.send("PUT", "/agents/bindings/core.gen", {"model_id": str(g["gemini-3.1-pro"]), "effort": "medium"})
    assert r.status_code == 422 and "Thấp" in r.json()["errors"]["effort"]
    r = await dapi.send("PUT", "/agents/bindings/core.gen", {"model_id": str(c["sonnet"]), "effort": "ultra"})
    assert r.status_code == 422
    r = await dapi.send("PUT", "/agents/bindings/core.gen", {"model_id": str(c["sonnet"])})   # bỏ effort ⇒ về null
    assert r.status_code == 200 and r.json()["binding"]["effort"] is None
    await bind(db, org, "core.briefing", a["a-flash"])
    items = {i["agent_key"]: i for i in (await dapi.get("/agents/bindings")).json()["items"]}
    assert items["core.gen"]["source"] == "custom" and items["core.gen"]["standard"] is None
    assert items["core.briefing"]["binding"]["effort"] is None and items["core.refinery"]["source"] == "standard"
    # v0.1.58: `standard_reason` luôn có mặt, null khi đã có model chuẩn hoặc đã có dòng gán.
    assert all("standard_reason" in i for i in items.values())
    assert items["core.gen"]["standard_reason"] is None and items["core.refinery"]["standard_reason"] is None


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (g) migration 0034
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

async def test_migration_0034_is_rerunnable_and_keeps_old_data_byte_for_byte(dapi: Api, db: Any,
                                                                             fresh_db: str) -> None:
    org = await org_id(db)
    _, ids = await add_source(db, org, API, "alpha", 1, ["a-flash"])
    for k in ("core.gen", "core.refinery", "core.reply"):          # bản cài cũ: một model cho mọi việc
        await bind(db, org, k, ids["a-flash"])
    await set_setting(db, org, "triage", {"enabled": True, "min_score": 41, "use_jev": False})
    await db.rollback()
    url = f"{PG}/{fresh_db}"
    old_cols = "agent_key, org_id, model_id, temperature, context_tokens, rule_codes"
    with psycopg.connect(url, autocommit=True) as c:
        # Dựng lại tình trạng TRƯỚC 0034: bỏ cột effort + ràng buộc.
        c.execute("ALTER TABLE agent.bindings DROP CONSTRAINT IF EXISTS bindings_effort_check")
        c.execute("ALTER TABLE agent.bindings DROP COLUMN IF EXISTS effort")

        def snap() -> tuple[Any, ...]:
            rows = c.execute(f"SELECT {old_cols} FROM agent.bindings ORDER BY agent_key").fetchall()
            s = c.execute("SELECT settings::text FROM core.organizations").fetchall()
            return (str(rows), str(s))

        before = snap()
        sql = SQL_0034.read_text(encoding="utf-8")
        c.execute(sql)
        first = snap()
        c.execute(sql)                                              # chạy lại lần hai không lỗi
        assert snap() == first == before
        assert c.execute("""SELECT count(*) FROM agent.bindings WHERE effort IS NOT NULL""").fetchone() == (0,)
        row = c.execute("SELECT count(*) FROM pg_constraint WHERE conname = 'bindings_effort_check'").fetchone()
        assert row == (1,)
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE agent.bindings SET effort = 'ultra'")
    # Bản cài cũ: dòng gán còn nguyên ⇒ 'Đã tuỳ chỉnh' + gợi ý "Áp model chuẩn theo vai?".
    by = await items_of(dapi)
    assert all(by[f"binding:{k}"]["customized"] for k in ("core.gen", "core.refinery", "core.reply"))
    assert "apply_standard" in {s["key"] for s in (await dapi.get("/defaults")).json()["suggestions"]}


def test_registry_helpers_expose_the_contract() -> None:
    assert registry.CORE_BINDING_KEYS == ("core.gen", "core.briefing", "core.refinery", "core.reply",
                                          "core.owner_reply", "core.support")
    assert profiles.normalize_tier("deep") == "strong" and profiles.normalize_tier("auto") is None
    assert profiles.normalize_tier("balanced") == "balanced" and profiles.normalize_tier("zzz") is None
