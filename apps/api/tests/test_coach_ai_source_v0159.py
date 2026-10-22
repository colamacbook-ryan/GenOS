# ruff: noqa: F811 — fixture `clis` nhập từ test_cli_models_v0131 (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.59 (g6) — thẻ "Hôm nay của Sếp" đọc lại NGAY sau khi Owner kiểm nguồn AI, không chờ cache 60 giây.

Máy Owner v0.1.58: "Kiểm tra kết nối" của Antigravity CLI xanh, Gen trả lời được, nhưng thẻ vẫn báo "Gen chưa có model
để trả lời" và "Đã đạt 0/1 việc bắt buộc", đòi bấm Kiểm tra lần nữa. Hai gốc rễ:
(1) `signals.collect` cache tín hiệu 60 giây (`CACHE_KEY`) mà không ai xoá khi trạng thái đổi;
(2) `POST /providers/{pid}/test` không ghi `ops.boss_checks` ⇒ dòng bắt buộc "ai" chưa đạt.

Bản v0.1.59: lượt gọi thử CLI XANH ghi `ai_source` + `agy_call` (hoặc `claude_call`); mọi nơi đổi thứ thẻ đọc gọi
`coach_cache.invalidate` SAU khi đã commit. CLI giả: tests/fixtures/fake_agy_multi.py (fixture `clis`).
"""

import asyncio
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import text

from gh.db import admin_sessionmaker, sessionmaker
from gh.gen.coach import cache as coach_cache
from gh.gen.coach import signals as sg
from tests.conftest import Api, verify_pin
from tests.test_actionlog_db import _set_scope
from tests.test_cli_models_v0131 import _login, _provider, clis  # noqa: F401 — fixture dùng chung
from tests.test_rbac_api import login_as

AGY = "antigravity_cli"
CLAUDE = "claude_code_cli"
TODAY = "/gen/coach/today"
MODEL_MISSING_TITLE = "Gen chưa có model để trả lời"


def _keys(card: dict[str, Any]) -> list[str]:
    return [t["key"] for t in card["todos"]]


def _row(ov: dict[str, Any], key: str) -> dict[str, Any]:
    return next(r for r in ov["rows"] if r["key"] == key)


async def _org_of(pid: str) -> uuid.UUID:
    async with sessionmaker()() as db:
        return (
            await db.execute(text("SELECT org_id FROM agent.providers WHERE id = :i"), {"i": uuid.UUID(pid)})
        ).scalar_one()


async def _cli_without_models(api: Api, kind: str, code: str) -> tuple[uuid.UUID, str]:
    """Đăng nhập CLI giả, tắt nguồn khác, xoá mọi model: đúng trạng thái "chưa có model" trước khi bấm Kiểm tra."""
    await _login(api, kind, code)
    pid = (await _provider(api, kind))["id"]
    org = await _org_of(pid)
    async with sessionmaker()() as db:
        await db.execute(
            text("UPDATE agent.providers SET is_enabled = (kind = :k) WHERE org_id = :o"), {"k": kind, "o": org}
        )
        await db.execute(text("DELETE FROM agent.models WHERE provider_id = :p"), {"p": uuid.UUID(pid)})
        await db.commit()
    return org, pid


async def _api_source(api: Api, name: str = "Nguồn khoá v0159") -> tuple[uuid.UUID, str, str]:
    """Nguồn khoá API (openai_compat, có một model) → (org, provider_id, model_id)."""
    await verify_pin(api)
    r = await api.send(
        "POST",
        "/providers",
        {
            "kind": "openai_compat",
            "name": name,
            "endpoint": "https://127.0.0.1:9/v1",
            "keys": ["sk-test-key-v0159-4321"],
            "models": ["m1"],
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    org = await _org_of(pid)
    async with sessionmaker()() as db:
        mid = (
            await db.execute(text("SELECT id FROM agent.models WHERE provider_id = :p"), {"p": uuid.UUID(pid)})
        ).scalar_one()
    return org, pid, str(mid)


async def _warm(app: Any, org: uuid.UUID) -> None:
    """Giả lập cache tín hiệu vừa được dựng (đúng khoá như `signals.collect`, còn hạn 60 giây)."""
    await app.state.redis.set(sg.CACHE_KEY.format(org), b"{}", ex=60)


async def _cached(app: Any, org: uuid.UUID) -> bool:
    return bool(await app.state.redis.exists(sg.CACHE_KEY.format(org)))


async def _rows(check_key: str, org: uuid.UUID) -> list[Any]:
    async with sessionmaker()() as db:
        return list(
            (
                await db.execute(
                    text("""SELECT status, detail FROM ops.boss_checks
                                              WHERE org_id = :o AND check_key = :k"""),
                    {"o": org, "k": check_key},
                )
            ).all()
        )


async def _card(api: Api) -> dict[str, Any]:
    r = await api.get(TODAY)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


# ─── (a)–(d): đúng luồng Owner ───────────────────────────────────────────────────────────────────────────────────


async def test_green_test_button_refreshes_card_and_boss_row_at_once(owner_api: Api, app: Any, clis: Any) -> None:
    api = owner_api
    org, pid = await _cli_without_models(api, AGY, "4/an")
    # (a) lượt mở thẻ khi CHƯA có model: có việc model.missing, và cache tín hiệu đã được dựng.
    card = await _card(api)
    assert "model.missing" in _keys(card)
    assert next(t for t in card["todos"] if t["key"] == "model.missing")["title"] == MODEL_MISSING_TITLE
    assert await _cached(app, org)
    # (b) Kiểm tra kết nối Antigravity giả → xanh.
    t = (await api.send("POST", f"/providers/{pid}/test")).json()
    assert t["ok"] is True, t
    # (c) dòng "ai" của Việc Sếp cần làm đạt ngay, và lượt CLI xanh được ghi đúng hai bằng chứng.
    ov = (await api.get("/boss-checks")).json()
    assert _row(ov, "ai")["done"] is True and ov["required_done"] == 1
    assert ov["results"]["ai_source"]["status"] == "pass" and ov["results"]["agy_call"]["status"] == "pass"
    assert [r.status for r in await _rows("ai_source", org)] == ["pass"]
    assert [r.status for r in await _rows("agy_call", org)] == ["pass"]
    assert set((await _rows("agy_call", org))[0].detail) >= {"probe_model", "models_count", "latency_ms"}
    # (d) thẻ Hôm nay đọc lại NGAY (cache đã bị xoá, không chờ 60 giây): hết model.missing, không boss.ai.
    card = await _card(api)
    assert "model.missing" not in _keys(card) and "boss.ai" not in _keys(card)
    assert card["progress"]["required_done"] == 1


async def test_claude_cli_green_test_records_ai_source_and_claude_call(owner_api: Api, app: Any, clis: Any) -> None:
    api = owner_api
    await _login(api, CLAUDE, "c/boss")
    p = await _provider(api, CLAUDE)
    org = await _org_of(p["id"])
    t = (await api.send("POST", f"/providers/{p['id']}/test")).json()
    assert t["ok"] is True, t
    assert [r.status for r in await _rows("ai_source", org)] == ["pass"]
    assert [r.status for r in await _rows("claude_call", org)] == ["pass"]
    assert _row((await api.get("/boss-checks")).json(), "claude")["done"] is True
    assert await _rows("agy_call", org) == []  # chỉ Antigravity mới ghi agy_call


async def test_failed_cli_test_writes_no_evidence_but_still_refreshes_card(
    owner_api: Api, app: Any, clis: Any, monkeypatch: Any
) -> None:
    api = owner_api
    org, pid = await _cli_without_models(api, AGY, "4/an")
    await _card(api)
    assert await _cached(app, org)

    async def red(_provider_id: uuid.UUID) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "CLI chưa đăng nhập",
            "error_code": "AGY_NOT_LOGGED_IN",
            "models": [],
            "latency_ms": 3,
        }

    monkeypatch.setattr(app.state.model_router, "test_provider", red)
    t = (await api.send("POST", f"/providers/{pid}/test")).json()
    assert t["ok"] is False
    assert await _rows("ai_source", org) == [] and await _rows("agy_call", org) == []
    assert _row((await api.get("/boss-checks")).json(), "ai")["done"] is False
    assert not await _cached(app, org)  # trạng thái nguồn đã đổi ⇒ vẫn làm mới thẻ


# ─── (e) nguồn khoá API: chỉ liệt kê model, KHÔNG ghi ai_source ─────────────────────────────────────────────────


async def test_api_key_source_green_test_records_no_ai_source(owner_api: Api, app: Any) -> None:
    api = owner_api
    org, pid, _mid = await _api_source(api)
    app.state.model_router.transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": [{"id": "m1"}]})
    )
    await _warm(app, org)
    t = (await api.send("POST", f"/providers/{pid}/test")).json()
    assert t["ok"] is True, t
    assert await _rows("ai_source", org) == []
    assert _row((await api.get("/boss-checks")).json(), "ai")["done"] is False
    assert not await _cached(app, org)


# ─── (f) invalidate: không ném lỗi; chỉ xoá khoá của đúng tổ chức ────────────────────────────────────────────────


async def test_invalidate_none_redis_never_raises(caplog: Any) -> None:
    with caplog.at_level(logging.WARNING, logger="gh.gen.coach"):
        await coach_cache.invalidate(None, uuid.uuid4())
    assert [r.levelno for r in caplog.records if r.name == "gh.gen.coach"] == [logging.WARNING]


async def test_invalidate_broken_redis_only_warns_and_hides_error_text(caplog: Any) -> None:
    class Broken:
        async def incr(self, *_a: Any) -> int:
            raise ConnectionError("redis down")

        async def delete(self, *_a: Any) -> int:
            raise ConnectionError("redis down")

    with caplog.at_level(logging.WARNING, logger="gh.gen.coach"):
        await coach_cache.invalidate(Broken(), uuid.uuid4())
    msgs = [r.getMessage() for r in caplog.records if r.name == "gh.gen.coach"]
    assert len(msgs) == 1 and "ConnectionError" in msgs[0] and "redis down" not in msgs[0]


async def test_invalidate_deletes_only_this_orgs_key(redis: Any) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    await redis.set(sg.CACHE_KEY.format(a), b"{}")
    await redis.set(sg.CACHE_KEY.format(b), b"{}")
    await coach_cache.invalidate(redis, a)
    assert not await redis.exists(sg.CACHE_KEY.format(a))
    assert await redis.exists(sg.CACHE_KEY.format(b))
    # bộ đếm phiên bản chỉ tăng ở tổ chức vừa làm mới, và có hạn dùng
    assert int(await redis.get(sg.CACHE_VER_KEY.format(a))) == 1 and not await redis.exists(sg.CACHE_VER_KEY.format(b))
    assert 0 < await redis.ttl(sg.CACHE_VER_KEY.format(a)) <= sg.CACHE_VER_TTL_S
    await coach_cache.invalidate(redis, a)
    assert int(await redis.get(sg.CACHE_VER_KEY.format(a))) == 2


# ─── mọi nơi đổi thứ thẻ đọc đều làm mới cache ngay (sau commit) ──────────────────────────────────────────────────


async def test_binding_put_and_delete_clear_card_cache(owner_api: Api, app: Any) -> None:
    org, _pid, mid = await _api_source(owner_api)
    await _warm(app, org)
    r = await owner_api.send("PUT", "/agents/bindings/core.refinery", {"model_id": mid})
    assert r.status_code == 200, r.text
    assert not await _cached(app, org)
    await _warm(app, org)
    r = await owner_api.send("DELETE", "/agents/bindings/core.refinery")
    assert r.status_code == 204, r.text
    assert not await _cached(app, org)


async def test_add_model_clears_card_cache(owner_api: Api, app: Any) -> None:
    org, pid, _mid = await _api_source(owner_api)
    await _warm(app, org)
    r = await owner_api.send("POST", f"/providers/{pid}/models", {"model_name": "m2"})
    assert r.status_code == 201, r.text
    assert not await _cached(app, org)


async def test_boss_check_run_clears_card_cache(owner_api: Api, app: Any) -> None:
    org, _pid, _mid = await _api_source(owner_api)
    app.state.model_router.transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": [{"id": "m1"}]})
    )
    await _warm(app, org)
    r = await owner_api.send("POST", "/boss-checks/ai_source/run", {})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pass"
    assert not await _cached(app, org)


async def test_heal_agy_clears_card_cache_after_adopting_model(owner_api: Api, app: Any, clis: Any) -> None:
    org, pid = await _cli_without_models(owner_api, AGY, "4/an")
    await _warm(app, org)
    assert await app.state.model_router._heal_agy(org) is True
    assert not await _cached(app, org)
    async with sessionmaker()() as db:
        n = (
            await db.execute(text("SELECT count(*) FROM agent.models WHERE provider_id = :p"), {"p": uuid.UUID(pid)})
        ).scalar_one()
    assert n == 3          # v0.1.66 (F132): lưu sẵn mọi model agy liệt kê


# ─── review v0.1.59: ai_source chỉ ghi cho nguồn đang BẬT; chỉ Owner tích hộ "Việc Sếp cần làm" ─────────────────────


async def test_disabled_cli_green_test_does_not_write_ai_source(owner_api: Api, app: Any, clis: Any) -> None:
    api = owner_api
    org, pid = await _cli_without_models(api, AGY, "4/an")
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE id = :i"), {"i": uuid.UUID(pid)})
        await db.commit()
    t = (await api.send("POST", f"/providers/{pid}/test")).json()
    assert t["ok"] is True, t
    # chuỗi của Gen không dùng nguồn đang tắt ⇒ lượt gọi thử này không được GHI bản 'pass' cho dòng bắt buộc "nguồn AI"…
    assert await _rows("ai_source", org) == []
    # …nhưng vẫn là bằng chứng thật cho dòng Kết nối của chính CLI đó. (Quy tắc có từ v0.1.55 `_derived_ai`: Google /
    # Claude gọi thử đạt ⇒ dòng "ai" suy ra đạt — không đổi ở bản vá này.)
    assert [r.status for r in await _rows("agy_call", org)] == ["pass"]


async def test_enabled_cli_after_failed_ai_source_flips_to_pass(owner_api: Api, app: Any, clis: Any) -> None:
    """Đối chứng của ca trên: nguồn BẬT xanh thì ai_source đạt, kể cả khi trước đó có bản 'fail'."""
    api = owner_api
    org, pid = await _cli_without_models(api, AGY, "4/an")
    async with admin_sessionmaker()() as db:
        await db.execute(
            text("""INSERT INTO ops.boss_checks (org_id, check_key, status, error_code, message)
                    VALUES (:o, 'ai_source', 'fail', 'AI_KEY_MISSING', 'x')"""),
            {"o": org},
        )
        await db.commit()
    assert (await api.send("POST", f"/providers/{pid}/test")).json()["ok"] is True
    assert (await api.get("/boss-checks")).json()["results"]["ai_source"]["status"] == "pass"


async def test_non_owner_with_system_manage_cannot_tick_boss_checks(
    owner_api: Api, app: Any, clis: Any, db: Any
) -> None:
    org, pid = await _cli_without_models(owner_api, AGY, "4/an")
    await _set_scope(db, "manager", "system.manage", "all")  # có system.manage vẫn không phải Owner
    await db.commit()
    mgr = await login_as(owner_api.c, db, "manager")
    try:
        r = await mgr.send("POST", f"/providers/{pid}/test")
        assert r.status_code == 200 and r.json()["ok"] is True, r.text  # được gọi thử nguồn…
    finally:
        await mgr.c.aclose()
    assert await _rows("ai_source", org) == [] and await _rows("agy_call", org) == []  # …nhưng không tích hộ Sếp
    assert _row((await owner_api.get("/boss-checks")).json(), "ai")["done"] is False


# ─── review v0.1.59: mọi nơi đổi nguồn / khoá / model / bật tắt đều làm mới thẻ ─────────────────────────────────────


async def test_create_provider_clears_card_cache(owner_api: Api, app: Any) -> None:
    org, _pid, _mid = await _api_source(owner_api)
    await _warm(app, org)
    org2, _pid2, _mid2 = await _api_source(owner_api, "Nguồn khoá thứ hai")
    assert org2 == org and not await _cached(app, org)


async def test_patch_provider_enabled_clears_card_cache(owner_api: Api, app: Any) -> None:
    org, pid, _mid = await _api_source(owner_api)
    await _warm(app, org)
    r = await owner_api.send("PATCH", f"/providers/{pid}", {"enabled": False})
    assert r.status_code == 200, r.text
    assert not await _cached(app, org)


async def test_add_and_delete_key_clear_card_cache(owner_api: Api, app: Any) -> None:
    org, pid, _mid = await _api_source(owner_api)
    await _warm(app, org)
    r = await owner_api.send("POST", f"/providers/{pid}/keys", {"secret": "sk-test-key-v0159-9999"})
    assert r.status_code == 201, r.text
    assert not await _cached(app, org)
    async with sessionmaker()() as db:
        kid = (
            await db.execute(
                text("SELECT id FROM agent.provider_keys WHERE provider_id = :p ORDER BY created_at DESC LIMIT 1"),
                {"p": uuid.UUID(pid)},
            )
        ).scalar_one()
    await _warm(app, org)
    r = await owner_api.send("DELETE", f"/providers/{pid}/keys/{kid}")
    assert r.status_code == 204, r.text
    assert not await _cached(app, org)


async def test_delete_provider_clears_card_cache(owner_api: Api, app: Any) -> None:
    org, pid, _mid = await _api_source(owner_api)
    await _warm(app, org)
    r = await owner_api.send("DELETE", f"/providers/{pid}")
    assert r.status_code == 204, r.text
    assert not await _cached(app, org)


async def test_setup_step4_clears_card_cache(owner_api: Api, app: Any, clis: Any) -> None:
    await _login(owner_api, AGY, "4/an")
    p = await _provider(owner_api, AGY)
    org = await _org_of(p["id"])
    r = await owner_api.send("POST", f"/providers/{p['id']}/models", {"model_name": "gemini-3.1-pro"})
    assert r.status_code == 201, r.text
    await _warm(app, org)
    r = await owner_api.send("PUT", "/setup/steps/4", {"provider_ids": [p["id"]]})
    assert r.status_code == 200, r.text
    assert not await _cached(app, org)


# ─── thứ tự: commit TRƯỚC, làm mới cache SAU (đổi ngược vẫn qua được test "cache đã bị xoá", test này thì không) ─────


async def test_invalidate_runs_only_after_commit(owner_api: Api, app: Any, monkeypatch: Any) -> None:
    org, pid, _mid = await _api_source(owner_api)
    await _warm(app, org)
    seen: list[tuple[str, int]] = []

    def spy(name: str, real: Any) -> Any:
        async def wrapped(*args: Any, **kw: Any) -> Any:
            # Tại đúng khoảnh khắc làm mới cache, một phiên KHÁC đã đọc được dòng model mới (= đã commit).
            async with admin_sessionmaker()() as other:
                n = (
                    await other.execute(
                        text("SELECT count(*) FROM agent.models WHERE provider_id = :p AND model_name = 'm-sau'"),
                        {"p": uuid.UUID(pid)},
                    )
                ).scalar_one()
            seen.append((name, int(n)))
            return await real(*args, **kw)

        return wrapped

    redis = app.state.redis
    monkeypatch.setattr(redis, "incr", spy("incr", redis.incr))
    monkeypatch.setattr(redis, "delete", spy("delete", redis.delete))
    r = await owner_api.send("POST", f"/providers/{pid}/models", {"model_name": "m-sau"})
    assert r.status_code == 201, r.text
    assert seen and all(n == 1 for _name, n in seen), seen
    assert {name for name, _n in seen} == {"incr", "delete"}


# ─── đua: lượt gom đang chạy không được ghi bản cũ vào cache sau khi cache vừa bị làm mới ───────────────────────────


async def test_collect_in_flight_cannot_repopulate_stale_cache(owner_api: Api, app: Any, monkeypatch: Any) -> None:
    org, _pid, _mid = await _api_source(owner_api)
    await app.state.redis.delete(sg.CACHE_KEY.format(org), sg.CACHE_VER_KEY.format(org))
    started, release = asyncio.Event(), asyncio.Event()
    real = sg._src_memory
    gate = {"on": True}

    async def slow(db: Any, redis: Any, org_id: uuid.UUID) -> dict[str, Any]:
        if gate["on"]:
            started.set()
            await release.wait()
        return await real(db, redis, org_id)

    monkeypatch.setattr(sg, "_src_memory", slow)
    task = asyncio.create_task(owner_api.get(TODAY))
    await asyncio.wait_for(started.wait(), 5)  # lượt gom đã bắt đầu đọc CSDL (dữ liệu "trước khi Sếp đổi")
    await coach_cache.invalidate(app.state.redis, org)  # Sếp vừa đổi cấu hình + commit ⇒ làm mới cache
    release.set()
    assert (await task).status_code == 200
    assert not await _cached(app, org)  # bản đọc trước đó KHÔNG được ghi trở lại
    # đối chứng: lượt gom kế tiếp (không bị xen ngang) vẫn ghi cache bình thường, TTL ≤ 60 giây
    gate["on"] = False
    assert (await owner_api.get(TODAY)).status_code == 200
    assert await _cached(app, org)
    assert 0 < await app.state.redis.ttl(sg.CACHE_KEY.format(org)) <= 60
