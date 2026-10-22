"""v0.1.39 — bảng kết quả kiểm thật "Việc Sếp cần làm" (ops.boss_checks, migration 0025) + /boss-checks.

Gen-hub giả bằng `httpx.MockTransport` (token đặc trưng để dò rò rỉ), Jev giả qua `model_router.transport`, Facebook
qua worker trình duyệt GIẢ của test_social (không có Facebook thật). Kiểm: chỉ Owner, PIN cho hub/agy_switch, lỗi
nghiệp vụ vẫn 200 + 'fail' kèm mã lỗi thống nhất, không bí mật/email đầy đủ trong CSDL, quy tắc 'done' từng dòng."""

import uuid
from pathlib import Path
from typing import Any

import httpx
import orjson
import psycopg
import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from gh.boss_checks import service as boss
from gh.db import admin_sessionmaker
from tests.conftest import PG, Api, verify_pin
from tests.phase2 import org_id
from tests.test_rbac_api import login_as
from tests.test_social import _add as social_add
from tests.test_social import _login as social_login

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "sql" / "0025_v0139_boss_checks.sql"
CANARY = "gh-tok-CANARY-123456"
ENDPOINT = "http://127.0.0.1:9911/mcp"
PREFIX = "mcp-58450__"


class CanaryHub:
    """Gen-hub `/mcp` giả với token đặc trưng; `mode`: ok | 401 | echo (lỗi lặp lại header Authorization)."""

    def __init__(self) -> None:
        self.mode = "ok"

    def handle(self, req: httpx.Request) -> httpx.Response:
        body = orjson.loads(req.content)
        auth = req.headers.get("authorization")
        if auth != f"Bearer {CANARY}" or self.mode == "401":
            return httpx.Response(401, text=f"unauthorized: {auth}")
        if self.mode == "echo" and body["method"] == "tools/call":
            return httpx.Response(500, text=f"boom authorization={auth} token={CANARY}")
        if body["method"] == "tools/list":
            ro = {"readOnlyHint": True}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                {"name": PREFIX + n, "description": n, "inputSchema": {}, "annotations": ro}
                for n in ("kho_tom_tat", "kho_search", "kho_get", "kho_find_by_id", "kho_list")]}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"content": [{"type": "text", "text": "VIEC-1 xong"}]}})


@pytest.fixture
def canary_hub(app: Any) -> CanaryHub:
    h = CanaryHub()
    app.state.mcp_transport = httpx.MockTransport(h.handle)
    return h


async def _db_text(sql: str) -> str:
    async with admin_sessionmaker()() as db:
        return orjson.dumps((await db.execute(text(sql))).all(), default=str).decode()


async def _run(api: Api, key: str, body: dict[str, Any] | None = None) -> httpx.Response:
    return await api.send("POST", f"/boss-checks/{key}/run", body or {})


# ─── (a) migration ──────────────────────────────────────────────────────────

async def test_migration_0025_is_rerunnable(fresh_db: str) -> None:
    sql = SQL_FILE.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        n = c.execute("SELECT count(*) FROM pg_policies WHERE tablename = 'boss_checks'").fetchone()
        idx = c.execute("SELECT count(*) FROM pg_indexes WHERE indexname = 'boss_checks_key_idx'").fetchone()
    assert n is not None and n[0] == 1
    assert idx is not None and idx[0] == 1


# ─── (b) Gen-hub ────────────────────────────────────────────────────────────

async def test_hub_run_pass_fail_and_token_never_stored(owner_api: Api, canary_hub: CanaryHub) -> None:
    # Chưa cấu hình → vẫn 200, 'fail' với mã của dịch vụ.
    await verify_pin(owner_api)
    r = await _run(owner_api, "hub")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fail" and r.json()["error_code"] == "HUB_LINK_NOT_CONFIGURED"
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": ENDPOINT, "token": CANARY})
    assert r.status_code == 200, r.text
    r = await _run(owner_api, "hub")
    out = r.json()
    assert r.status_code == 200 and out["status"] == "pass", out
    assert out["error_code"] is None and out["detail"]["exposed_tools"] == 5 and out["detail"]["missing_tools"] == []
    assert isinstance(out["detail"]["latency_ms"], int) and out["runs"] == 2
    canary_hub.mode = "401"
    out = (await _run(owner_api, "hub")).json()
    assert out["status"] == "fail" and out["error_code"] == "HUB_TOKEN_REJECTED"
    assert out["message"].startswith("401: Token Gen-hub")
    canary_hub.mode = "echo"
    out = (await _run(owner_api, "hub")).json()
    assert out["status"] == "fail" and CANARY not in orjson.dumps(out).decode()
    assert CANARY not in await _db_text("SELECT message, detail::text FROM ops.boss_checks")
    assert CANARY not in await _db_text("SELECT * FROM ops.action_log")
    assert CANARY not in (await owner_api.get("/boss-checks")).text


# ─── (c) quyền ──────────────────────────────────────────────────────────────

async def test_pin_owner_only_and_unknown_key(owner_api: Api, client: httpx.AsyncClient, db: Any) -> None:
    for key in ("hub", "agy_switch"):
        r = await _run(owner_api, key, {"profile_id": str(uuid.uuid4())})
        assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED", r.text
    r = await _run(owner_api, "agy_login")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    assert (await _run(owner_api, "khong_co")).status_code == 404
    await verify_pin(owner_api)
    r = await _run(owner_api, "agy_switch", {})
    assert r.status_code == 422
    for role in ("operator", "manager"):
        api = await login_as(client, db, role)
        assert (await api.get("/boss-checks")).status_code == 403
        assert (await _run(api, "jev")).status_code == 403
    assert await _db_text("SELECT id FROM ops.boss_checks") == "[]"      # 403/404/422/423 không ghi gì


# ─── (d) Jev ────────────────────────────────────────────────────────────────

async def test_jev_not_configured_error_and_pass(owner_api: Api, app: Any) -> None:
    out = (await _run(owner_api, "jev")).json()
    assert out["status"] == "fail" and out["error_code"] == "JEV_NOT_CONFIGURED" and "Jev" in out["message"]
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/providers", {"kind": "system_one", "name": "Jev (System One)",
                                                    "keys": ["sk-or-v1-khoa-thu-nghiem-jev"]})
    assert r.status_code == 201, r.text
    app.state.model_router.transport = httpx.MockTransport(lambda req: httpx.Response(500, text="hỏng"))
    out = (await _run(owner_api, "jev")).json()
    assert out["status"] == "fail" and out["error_code"] == "JEV_ERROR", out
    assert "account" not in out
    app.state.model_router.transport = httpx.MockTransport(lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": '{"choice": "có", "confidence": 0.97}'}}]}))
    out = (await _run(owner_api, "jev")).json()
    assert out["status"] == "pass" and out["error_code"] is None and out["runs"] == 3, out
    assert "sk-or-v1-khoa" not in await _db_text("SELECT message, detail::text FROM ops.boss_checks")
    ov = (await owner_api.get("/boss-checks")).json()
    assert next(r for r in ov["rows"] if r["key"] == "jev")["done"] is True
    assert ov["required_done"] == 0                                    # Jev không bắt buộc


# ─── (d2) nguồn AI (v0.1.55) ────────────────────────────────────────────────

async def test_ai_source_run_probes_first_ready_source_and_never_stores_the_key(owner_api: Api, app: Any,
                                                                                 db: Any) -> None:
    """`POST /boss-checks/ai_source/run` (Owner, không PIN): chưa có nguồn → fail AI_NO_SOURCE; có nguồn thì gọi thử
    nguồn đầu chuỗi sẵn sàng (router.test_provider); Đạt ⇒ dòng `ai` xong, required 1/1. Khoá không vào CSDL kết quả."""
    secret = "sk-test-key-ai-source-9876"
    out = (await _run(owner_api, "ai_source")).json()
    assert out["status"] == "fail" and out["error_code"] == "AI_NO_SOURCE" and "nguồn AI" in out["message"], out
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/providers", {"kind": "openai_compat", "name": "Nguồn thử",
                                                    "endpoint": "https://127.0.0.1:9/v1", "keys": [secret],
                                                    "models": ["m1"]})
    assert r.status_code == 201, r.text
    app.state.model_router.transport = httpx.MockTransport(lambda req: httpx.Response(500, text=f"hỏng {secret}"))
    out = (await _run(owner_api, "ai_source")).json()
    assert out["status"] == "fail" and out["key"] == "ai_source" and out["error_code"], out
    ov = (await owner_api.get("/boss-checks")).json()
    assert next(r for r in ov["rows"] if r["key"] == "ai")["done"] is False and ov["required_done"] == 0
    app.state.model_router.transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": [{"id": "m1"}]}))
    out = (await _run(owner_api, "ai_source")).json()
    assert out["status"] == "pass" and out["error_code"] is None and out["runs"] == 3, out
    ov = (await owner_api.get("/boss-checks")).json()
    assert next(r for r in ov["rows"] if r["key"] == "ai")["done"] is True
    assert (ov["required_done"], ov["required_total"]) == (1, 1)
    assert secret not in await _db_text("SELECT message, detail::text, error_code FROM ops.boss_checks")
    assert secret not in await _db_text("SELECT detail::text, target_label FROM ops.action_log")


async def test_ai_source_run_names_the_missing_key_of_the_first_source(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, is_enabled, failover_rank)
                             VALUES (:o, 'openai_compat', 'Chưa có khoá', true, 1)"""), {"o": org})
    await db.commit()
    out = (await _run(owner_api, "ai_source")).json()
    assert out["status"] == "fail" and out["error_code"] == "AI_KEY_MISSING" and "khoá API" in out["message"], out


async def test_ai_source_run_is_owner_only(owner_api: Api, client: httpx.AsyncClient, db: Any) -> None:
    for role in ("operator", "manager"):
        api = await login_as(client, db, role)
        assert (await _run(api, "ai_source")).status_code == 403
    assert await _db_text("SELECT id FROM ops.boss_checks") == "[]"


# ─── (e) Facebook ───────────────────────────────────────────────────────────

async def test_facebook_no_account_pending_then_resolved(owner_api: Api, redis: Redis) -> None:
    out = (await _run(owner_api, "facebook")).json()
    assert out["status"] == "fail" and out["error_code"] == "SOCIAL_NO_ACCOUNT"
    assert out["message"].startswith("Chưa có tài khoản Facebook")
    acc = await social_add(owner_api)
    # Có tài khoản nhưng chưa đăng nhập → lỗi nghiệp vụ của dịch vụ, vẫn 200.
    out = (await _run(owner_api, "facebook")).json()
    assert out["status"] == "fail" and out["error_code"] == "SOCIAL_NOT_ACTIVE"
    await social_login(owner_api, redis, acc["id"])
    out = (await _run(owner_api, "facebook")).json()
    assert out["status"] == "pending" and out["detail"]["job_status"] == "queued", out
    ov = (await owner_api.get("/boss-checks")).json()
    assert ov["results"]["facebook"]["status"] == "pending"
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE agent.browser_jobs SET status = 'done', error = 'x' WHERE kind = 'read'"))
        await db.commit()
    ov = (await owner_api.get("/boss-checks")).json()
    fb = ov["results"]["facebook"]
    assert fb["status"] == "pass" and fb["detail"]["job_status"] == "done" and fb["error_code"] is None
    assert next(r for r in ov["rows"] if r["key"] == "facebook")["done"] is True
    # Bấm lại ngay (cách < 10 phút) → 429 SOCIAL_RATE_LIMIT là lỗi TẠM: trả về nhưng KHÔNG ghi, "Đạt" giữ nguyên.
    out = (await _run(owner_api, "facebook", {"account_id": acc["id"]})).json()
    assert out["status"] == "fail" and out["error_code"] == "SOCIAL_RATE_LIMIT" and out["transient"] is True, out
    ov = (await owner_api.get("/boss-checks")).json()
    assert ov["results"]["facebook"]["status"] == "pass" and ov["results"]["facebook"]["runs"] == 3
    assert next(r for r in ov["rows"] if r["key"] == "facebook")["done"] is True
    # Lượt đọc kế (lùi giờ lượt trước cho qua khoảng cách tối thiểu) → lỗi ở worker.
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE agent.browser_jobs SET created_at = now() - interval '2 days', "
                              "finished_at = now() - interval '2 days' WHERE kind = 'read'"))
        await db.commit()
    out = (await _run(owner_api, "facebook", {"account_id": acc["id"]})).json()
    assert out["status"] == "pending", out
    async with admin_sessionmaker()() as db:
        await db.execute(text("""UPDATE agent.browser_jobs SET status = 'failed', error = 'LỖI THÔ cookie=xs-123'
                                 WHERE kind = 'read' AND status = 'queued'"""))
        await db.commit()
    fb = (await owner_api.get("/boss-checks")).json()["results"]["facebook"]
    assert fb["status"] == "fail" and fb["error_code"] == "SOCIAL_READ_FAILED" and fb["runs"] == 4
    assert "cookie" not in fb["message"] and "LỖI THÔ" not in await _db_text("SELECT * FROM ops.boss_checks")
    # Việc đã biến mất (tài khoản bị gỡ) → SOCIAL_JOB_MISSING.
    async with admin_sessionmaker()() as db:
        org = await org_id(db)
        await boss.record(db, org, "facebook", "pending", ref_id=uuid.uuid4(), detail={"job_status": "queued"})
        assert await boss.resolve_pending(db, org) == 1
        await db.commit()
        latest = await boss.latest(db, org)
    assert latest["facebook"] is not None and latest["facebook"]["error_code"] == "SOCIAL_JOB_MISSING"


async def test_facebook_stale_job_closed_and_cancelled_has_own_code(owner_api: Api, redis: Redis) -> None:
    """Việc đọc treo quá STALE_AFTER (worker trình duyệt chết/chưa chạy) → GET /boss-checks tự đóng việc (failed +
    WORKER_TIMEOUT) và chốt ô Facebook là lỗi, không kẹt "Đang chạy…" mãi. Việc bị huỷ lẻ → SOCIAL_READ_CANCELLED
    (không phải "Dừng tất cả")."""
    acc = await social_add(owner_api)
    await social_login(owner_api, redis, acc["id"])
    out = (await _run(owner_api, "facebook", {"account_id": acc["id"]})).json()
    assert out["status"] == "pending", out
    async with admin_sessionmaker()() as db:   # mới 10 phút: vẫn đang chạy
        await db.execute(text("UPDATE agent.browser_jobs SET status = 'running', "
                              "created_at = now() - interval '10 minutes' WHERE kind = 'read'"))
        await db.commit()
    fb = (await owner_api.get("/boss-checks")).json()["results"]["facebook"]
    assert fb["status"] == "pending" and fb["detail"]["job_status"] == "running"
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE agent.browser_jobs SET created_at = now() - interval '16 minutes' "
                              "WHERE kind = 'read'"))
        await db.commit()
    fb = (await owner_api.get("/boss-checks")).json()["results"]["facebook"]
    assert fb["status"] == "fail" and fb["error_code"] == "WORKER_TIMEOUT", fb
    assert fb["message"] == boss.SOCIAL_TIMEOUT_MSG and fb["detail"]["job_status"] == "failed"
    assert "WORKER_TIMEOUT" in await _db_text("SELECT status, error FROM agent.browser_jobs WHERE kind = 'read'")
    # Lượt mới (lùi giờ lượt trước cho qua khoảng cách tối thiểu) rồi bị huỷ lẻ.
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE agent.browser_jobs SET created_at = now() - interval '2 days', "
                              "finished_at = now() - interval '2 days' WHERE kind = 'read'"))
        await db.commit()
    out = (await _run(owner_api, "facebook", {"account_id": acc["id"]})).json()
    assert out["status"] == "pending", out
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE agent.browser_jobs SET status = 'cancelled' "
                              "WHERE kind = 'read' AND status = 'queued'"))
        await db.commit()
    fb = (await owner_api.get("/boss-checks")).json()["results"]["facebook"]
    assert fb["status"] == "fail" and fb["error_code"] == "SOCIAL_READ_CANCELLED", fb
    assert "Dừng tất cả" not in fb["message"]


# ─── (f) tổng quan + quy tắc 'done' ─────────────────────────────────────────

async def test_overview_rows_and_done_rules(owner_api: Api, db: Any) -> None:
    ov = (await owner_api.get("/boss-checks")).json()
    # v0.1.55 (G2): thêm dòng 0 "ai" đứng đầu — số dòng 1–9 giữ nguyên (nhãn Gen, e2e).
    assert [r["row"] for r in ov["rows"]] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert [r["key"] for r in ov["rows"]] == ["ai", "hub", "facebook", "agy", "claude", "jev", "telegram", "remote",
                                                  "facebook_reply", "kho_write"]
    assert ov["rows"][0]["title"] == "Có ít nhất 1 nguồn AI chạy được" and ov["rows"][0]["checks"] == ["ai_source"]
    # Bỏ agy_switch khỏi dòng Google / Antigravity (bài kiểm của nhà phát triển).
    assert ov["rows"][3]["title"] == "Google / Antigravity" and ov["rows"][3]["checks"] == ["agy_login", "agy_call"]
    assert all("agy_switch" not in r["checks"] for r in ov["rows"])
    assert ov["rows"][6]["title"] == "Telegram (báo động & bản tin)" and ov["rows"][6]["checks"] == ["telegram"]
    # Chỉ dòng "ai" bắt buộc; mọi dòng kết nối khác là tuỳ chọn.
    assert [r["optional"] for r in ov["rows"]] == [False, True, True, True, True, True, True, True, True, True]
    # v0.1.47 (F-79): dòng 8 Facebook trả lời — không bắt buộc, không chạy được từ nút Kiểm tra.
    assert ov["rows"][8]["title"] == "Facebook trả lời" and ov["rows"][8]["checks"] == ["facebook_reply"]
    assert "facebook_reply" not in boss.RUNNABLE
    # v0.1.50 (F-81): dòng 9 Gen ghi Kho — không bắt buộc, không có nút Kiểm tra (máy chủ tự ghi 'pass' sau lần ghi).
    assert ov["rows"][9]["title"] == "Gen ghi Kho" and ov["rows"][9]["checks"] == ["kho_write"]
    assert "kho_write" in boss.CHECK_KEYS and "kho_write" not in boss.RUNNABLE
    assert "ai_source" in boss.CHECK_KEYS and "ai_source" in boss.RUNNABLE
    assert ov["required_total"] == boss.REQUIRED_TOTAL == sum(1 for r in ov["rows"] if not r["optional"]) == 1
    assert ov["required_done"] == 0
    assert set(ov["results"]) == set(boss.CHECK_KEYS) and all(v is None for v in ov["results"].values())
    org = await org_id(db)

    async def rows() -> dict[str, bool]:
        body = (await owner_api.get("/boss-checks")).json()
        return {r["key"]: r["done"] for r in body["rows"]} | {"_n": body["required_done"]}

    # agy chỉ cần lần gọi thử mới nhất đạt (không còn đòi đổi qua lại hai tài khoản).
    await boss.record(db, org, "agy_call", "pass")
    await db.commit()
    assert (await rows())["agy"] is True
    await boss.record(db, org, "agy_call", "fail", error_code="AUTH_EXPIRED")
    await db.commit()
    assert (await rows())["agy"] is False                               # lần gọi thử mới nhất lỗi
    await boss.record(db, org, "claude_login", "pass")
    await db.commit()
    assert (await rows())["claude"] is False
    await _usable(db, org, "claude_code_cli")                      # B2: nguồn Claude Code phải còn dùng được
    await boss.record(db, org, "claude_call", "pass")
    await boss.record(db, org, "hub", "pass")
    await db.commit()
    got = await rows()
    # Claude gọi thử đạt ⇒ cũng đủ chứng cứ "có nguồn AI chạy được" (dòng ai bắt buộc duy nhất).
    assert got["claude"] is True and got["hub"] is True and got["facebook"] is False and got["ai"] is True
    assert got["_n"] == 1


async def _usable(db: Any, org: Any, kind: str, name: str | None = None) -> uuid.UUID:
    """v0.1.63 (B2): bằng chứng chỉ tính khi có nguồn AI DÙNG ĐƯỢC — thêm nguồn có khoá / phiên CLI + một model."""
    pid = (await db.execute(text("INSERT INTO agent.providers (org_id, kind, name) VALUES (:o, :k, :n) RETURNING id"),
                            {"o": org, "k": kind, "n": name or kind})).scalar_one()
    if kind in ("antigravity_cli", "claude_code_cli"):
        await db.execute(text("""INSERT INTO agent.cli_profiles (org_id, provider_id, token_enc, is_active)
                                 VALUES (:o, :p, :t, true)"""), {"o": org, "p": pid, "t": b"\x00t"})
    else:
        await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4)
                                 VALUES (:p, 'K', :s, '1234')"""), {"p": pid, "s": b"\x00e"})
    await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'm')"), {"p": pid})
    await db.commit()
    return pid  # type: ignore[no-any-return]


async def test_ai_source_row_done_rules(owner_api: Api, db: Any) -> None:
    """Dòng `ai`: Đạt khi (a) có lượt agent.model_calls 'ok' trong 30 ngày (không tính Jev / embedding), HOẶC (b)
    Claude / Google gọi thử đạt, HOẶC (c) bấm Kiểm tra (`ai_source` đạt). Lượt lỗi / quá 30 ngày không tính."""
    org = await org_id(db)

    async def ai() -> tuple[bool, dict[str, Any] | None]:
        body = (await owner_api.get("/boss-checks")).json()
        return next(r for r in body["rows"] if r["key"] == "ai")["done"], body["results"]["ai_source"]

    assert await ai() == (False, None)
    kinds = {"gemini": uuid.uuid4(), "system_one": uuid.uuid4()}
    for kind, pid in kinds.items():
        await db.execute(text("INSERT INTO agent.providers (id, org_id, kind, name) VALUES (:i, :o, :k, :n)"),
                         {"i": pid, "o": org, "k": kind, "n": kind})
    models = {}
    for kind, pid in kinds.items():
        models[kind] = (await db.execute(text("""INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'm')
                                                 RETURNING id"""), {"p": pid})).scalar_one()
    await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4)
                             VALUES (:p, 'K', :s, '1234')"""),
                     {"p": kinds["gemini"], "s": b"\x00e"})            # B2: nguồn dùng được

    async def call(kind: str, status: str, purpose: str = "reply", ago: str = "1 hour") -> None:
        await db.execute(text(f"""INSERT INTO agent.model_calls (org_id, at, model_id, agent_key, purpose, status)
                                  VALUES (:o, now() - interval '{ago}', :m, 'core.gen', :p, :s)"""),
                         {"o": org, "m": models[kind], "p": purpose, "s": status})
        await db.commit()

    await call("gemini", "error")
    await call("gemini", "ok", ago="40 days")          # quá 30 ngày
    await call("gemini", "ok", purpose="embedding")    # embedding không sinh văn bản
    await call("system_one", "ok")                     # Jev không sinh văn bản
    assert await ai() == (False, None)
    await call("gemini", "ok")
    done, res = await ai()
    assert done is True and res is not None and res["status"] == "pass"
    assert res["detail"] == {"via": "model_calls"} and res["runs"] == 0
    assert (await owner_api.get("/boss-checks")).json()["required_done"] == 1


async def test_ai_source_row_done_by_cli_call_or_manual_check(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    await _usable(db, org, "antigravity_cli")                      # B2: bằng chứng chỉ tính khi nguồn còn dùng được
    await boss.record(db, org, "agy_call", "pass")
    await db.commit()
    body = (await owner_api.get("/boss-checks")).json()
    ai_row = next(r for r in body["rows"] if r["key"] == "ai")
    assert ai_row["done"] is True and body["results"]["ai_source"]["detail"] == {"via": "agy_call"}
    await db.execute(text("DELETE FROM ops.boss_checks WHERE org_id = :o"), {"o": org})
    await db.commit()
    assert next(r for r in (await owner_api.get("/boss-checks")).json()["rows"] if r["key"] == "ai")["done"] is False
    await boss.record(db, org, "ai_source", "fail", error_code="AI_NO_SOURCE", message="Chưa có nguồn AI")
    await db.commit()
    body = (await owner_api.get("/boss-checks")).json()
    assert next(r for r in body["rows"] if r["key"] == "ai")["done"] is False
    assert body["results"]["ai_source"]["status"] == "fail" and body["required_done"] == 0
    await boss.record(db, org, "ai_source", "pass", detail={"latency_ms": 9, "probe_model": "m"})
    await db.commit()
    body = (await owner_api.get("/boss-checks")).json()
    assert next(r for r in body["rows"] if r["key"] == "ai")["done"] is True and body["required_done"] == 1


async def test_failed_ai_check_is_not_masked_by_an_older_successful_call(owner_api: Api, db: Any) -> None:
    """F-R6: Sếp bấm Kiểm tra dòng 0 và LỖI sau lần gọi model thành công cũ ⇒ vẫn 'fail' (không che bằng bằng chứng cũ);
    lượt gọi / Claude gọi thử đạt MỚI HƠN lần kiểm lỗi mới đổi lại thành Đạt."""
    org = await org_id(db)
    pid = uuid.uuid4()
    await db.execute(text("INSERT INTO agent.providers (id, org_id, kind, name) VALUES (:i, :o, 'gemini', 'g')"),
                     {"i": pid, "o": org})
    mid = (await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'm') RETURNING id"),
                            {"p": pid})).scalar_one()
    await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4)
                             VALUES (:p, 'K', :s, '1234')"""), {"p": pid, "s": b"\x00e"})   # B2: nguồn dùng được
    await _usable(db, org, "claude_code_cli")           # …và Claude Code dùng được để `claude_call` đạt có giá trị
    await db.commit()

    async def call(ago: str) -> None:
        await db.execute(text(f"""INSERT INTO agent.model_calls (org_id, at, model_id, agent_key, purpose, status)
                                  VALUES (:o, now() - interval '{ago}', :m, 'core.gen', 'reply', 'ok')"""),
                         {"o": org, "m": mid})
        await db.commit()

    async def ai() -> tuple[bool, str | None, int]:
        body = (await owner_api.get("/boss-checks")).json()
        row = next(r for r in body["rows"] if r["key"] == "ai")
        res = body["results"]["ai_source"]
        return row["done"], res["status"] if res else None, body["required_done"]

    await call("20 days")
    assert await ai() == (True, "pass", 1)                       # chưa kiểm lần nào: bằng chứng cũ đủ
    await boss.record(db, org, "ai_source", "fail", error_code="AI_NO_SOURCE", message="Khoá bị thu hồi")
    await db.commit()
    assert await ai() == (False, "fail", 0)                      # kiểm LỖI mới hơn lượt gọi cũ ⇒ phải nói sự thật
    await boss.record(db, org, "claude_call", "pass")             # Claude gọi thử đạt SAU lần kiểm lỗi
    await db.commit()
    assert await ai() == (True, "pass", 1)                       # bằng chứng MỚI HƠN lần kiểm lỗi ⇒ Đạt trở lại
    await boss.record(db, org, "ai_source", "fail", error_code="AI_NO_SOURCE", message="Lại lỗi")
    await db.commit()
    assert await ai() == (False, "fail", 0)                      # kiểm lỗi lần nữa, mới hơn mọi bằng chứng
    await call("0 seconds")
    assert await ai() == (True, "pass", 1)                       # lượt gọi thật mới hơn cả lần kiểm lỗi


async def test_overview_has_no_switch_counter_and_runs_count_every_record(owner_api: Api, db: Any) -> None:
    """v0.1.68 (F040): `switch_passes` đã gỡ (web không còn đọc; trước đây mỗi `overview` — Hôm nay, Việc Sếp cần làm,
    Gen cùng gọi — chạy một truy vấn thừa chỉ để trả trường đó). `results.agy_switch.runs` vẫn là tổng số bản ghi (cả
    lỗi); dòng agy chỉ phụ thuộc `agy_call` (v0.1.55)."""
    org = await org_id(db)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())

    async def ov() -> dict[str, Any]:
        return (await owner_api.get("/boss-checks")).json()  # type: ignore[no-any-return]

    await boss.record(db, org, "agy_call", "pass")
    await boss.record(db, org, "agy_switch", "pass", detail={"target_profile": a})
    await boss.record(db, org, "agy_switch", "fail", error_code="AGY_ACCOUNT_MISMATCH", detail={"target_profile": b})
    await db.commit()
    got = await ov()
    assert "switch_passes" not in got and got["results"]["agy_switch"]["runs"] == 2
    assert next(r for r in got["rows"] if r["key"] == "agy")["done"] is True
    assert not hasattr(boss, "switch_passes") and not hasattr(boss, "SWITCHES_NEEDED")


# ─── (g) /system/health ─────────────────────────────────────────────────────

async def test_health_has_boss_checks_without_changing_overall(owner_api: Api, db: Any) -> None:
    before = (await owner_api.get("/system/health")).json()
    org = await org_id(db)
    await boss.record(db, org, "claude_call", "fail", error_code="AUTH_EXPIRED", message="hết hạn",
                      detail={"account_masked": "b***@example.vn"})
    await boss.record(db, org, "hub", "pass", detail={"latency_ms": 12})
    await db.commit()
    after = (await owner_api.get("/system/health")).json()
    assert after["overall"] == before["overall"]
    got = {c["key"]: c for c in after["boss_checks"]}
    assert set(got) == {"claude_call", "hub"}
    assert got["claude_call"]["status"] == "fail" and got["claude_call"]["error_code"] == "AUTH_EXPIRED"
    assert set(got["hub"]) == {"key", "status", "error_code", "checked_at"}     # không lộ detail cho system.read
    assert "b***@example.vn" not in (await owner_api.get("/system/health")).text


# ─── (h) bộ lọc bí mật + giữ 50 bản ─────────────────────────────────────────

async def test_record_whitelists_detail_redacts_message_and_trims(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    out = await boss.record(db, org, "jev", "fail", error_code="JEV_ERROR",
                            message="Jev: HTTP 401 khoá sk-abcdefghijklmnop1234 của binh@example.vn " + "x" * 400,
                            detail={"latency_ms": 5, "token": "bi-mat", "email": "binh@example.vn",
                                    "account_masked": "b***@example.vn"})
    await db.commit()
    assert out["detail"] == {"latency_ms": 5, "account_masked": "b***@example.vn"}
    blob = await _db_text("SELECT message, detail::text FROM ops.boss_checks")
    assert "sk-abcdefghijklmnop" not in blob and "bi-mat" not in blob and "binh@example.vn" not in blob
    assert len(out["message"]) <= 300
    with pytest.raises(ValueError):
        await boss.record(db, org, "khong_co", "pass")
    await db.rollback()
    for _ in range(boss.KEEP_PER_KEY + 3):
        await boss.record(db, org, "hub", "fail", error_code="HUB_ERROR")
    await db.commit()
    assert (await boss.latest(db, org))["hub"]["runs"] == boss.KEEP_PER_KEY  # type: ignore[index]
    assert boss.mask_email("binh@example.vn") == "b***@example.vn"
    assert boss.mask_email("không phải email") is None and boss.mask_email(None) is None
