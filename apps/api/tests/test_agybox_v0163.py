"""v0.1.63 H1 — hộp riêng Antigravity `agy-box`: giao thức ký HMAC, server, client, trạng thái hộp, khối sức khoẻ.

CLI giả: tests/fixtures/fake_agy_box_probe.py (và fake_agy_multi.py cho luồng model/đăng nhập)."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from gh.agybox import client as boxclient
from gh.agybox import protocol, server
from gh.agybox.client import AgyBoxClient, BoxUnauthorized
from gh.providers.clients import (
    AuthFailed,
    BadRequest,
    Message,
    ModelRejected,
    ProviderError,
    RateLimited,
)
from tests.test_cli_models_v0131 import _wrapper

FIX = Path(__file__).parent / "fixtures"
API_DIR = Path(__file__).resolve().parents[1]
KEY = bytes(range(32))
KEY_B64 = base64.b64encode(KEY).decode()
URL = "http://agy-box:8090"


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


SESSION = json.dumps({"access_token": "tok", "expiry": 4102444800})


def _signin(home: Path) -> None:
    """Kho phiên agy của api/worker (hộp không có kho phiên — client gửi tệp token theo từng yêu cầu, protocol 2)."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "antigravity-oauth-token").write_text(SESSION)


@pytest.fixture
def box(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Server thật (ASGI) + agy giả; client nói chuyện qua ASGITransport (không mở cổng)."""
    keyfile = tmp_path / "gh_agy_key"
    keyfile.write_text(KEY_B64)
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    _signin(home)
    clock = Clock()
    cfg = server.Config(key_file=str(keyfile), cli_binary=_wrapper(tmp_path, "agy", FIX / "fake_agy_box_probe.py"),
                        max_jobs=2)
    app = server.create_app(cfg, clock=clock)
    transport = httpx.ASGITransport(app=app)

    def make(timeout: float = 30.0, key_file: str | None = None) -> AgyBoxClient:
        return AgyBoxClient(URL, key_file or str(keyfile), timeout, cli_home=str(home), transport=transport,
                            clock=clock)

    class Box:
        pass

    b = Box()
    b.cfg, b.app, b.clock, b.transport, b.make = cfg, app, clock, transport, make
    b.keyfile, b.home, b.tmp = keyfile, home, tmp_path
    return b


async def _raw(box: Any, method: str, path: str, body: bytes, *, key: bytes = KEY, ts_shift: int = 0,
               sign: bool = True, headers: dict[str, str] | None = None) -> httpx.Response:
    h = dict(headers or {})
    if sign:
        h.update(protocol.sign_request(key, method, path, body, box.clock() + ts_shift))
    async with httpx.AsyncClient(transport=box.transport, base_url=URL) as c:
        return await c.request(method, path, content=body, headers=h)


def _body(**kw: Any) -> bytes:
    base = {"request_id": "r-1", "model": "gemini-3.8-flash", "effort": None, "prompt": "xin chào", "json_mode": False,
            "timeout_s": 20, "session": SESSION}
    base.update(kw)
    return json.dumps(base).encode()


# ─── giao thức ───────────────────────────────────────────────────────────────────────────────────────────────

def test_signature_binds_method_path_ts_body() -> None:
    now = 1_800_000_000.0
    h = protocol.sign_request(KEY, "POST", "/v1/generate", b"{}", now)
    assert protocol.verify_request(KEY, "POST", "/v1/generate", b"{}", h, now) is None
    assert protocol.verify_request(KEY, "POST", "/v1/models", b"{}", h, now) == "bad_signature"      # đổi đường
    assert protocol.verify_request(KEY, "GET", "/v1/generate", b"{}", h, now) == "bad_signature"     # đổi phương thức
    assert protocol.verify_request(KEY, "POST", "/v1/generate", b"{ }", h, now) == "bad_signature"   # đổi thân
    assert protocol.verify_request(bytes(32), "POST", "/v1/generate", b"{}", h, now) == "bad_signature"
    assert protocol.verify_request(KEY, "POST", "/v1/generate", b"{}", {}, now) == "missing"
    assert protocol.verify_request(KEY, "POST", "/v1/generate", b"{}", h, now + 61) == "stale"
    assert protocol.verify_request(KEY, "POST", "/v1/generate", b"{}", h, now - 61) == "stale"
    assert protocol.verify_request(KEY, "POST", "/v1/generate", b"{}", h, now + 59) is None


def test_decode_key_hex_and_base64_and_rejects_short() -> None:
    assert protocol.decode_key(KEY.hex()) == KEY
    assert protocol.decode_key(KEY_B64 + "\n") == KEY
    with pytest.raises(ValueError):
        protocol.decode_key(base64.b64encode(b"ngan").decode())


# ─── server: xác thực, giới hạn ─────────────────────────────────────────────────────────────────────────────

async def test_healthz_open_and_minimal(box) -> None:  # type: ignore[no-untyped-def]
    r = await _raw(box, "GET", "/healthz", b"", sign=False)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["service"] == "agy-box" and j["protocol"] == 2
    assert j["agy_version"] == "1.2.9" and j["cli_logged_in"] is None      # hộp không giữ phiên
    assert set(j) == {"ok", "service", "protocol", "agy_version", "cli_logged_in"}


async def test_bad_signature_expired_missing_replayed_all_401(box) -> None:  # type: ignore[no-untyped-def]
    body = _body()
    wrong = await _raw(box, "POST", "/v1/generate", body, key=bytes(32))
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "unauthorized"
    assert protocol.SIG_HEADER not in wrong.headers                       # 401 không ký
    old = await _raw(box, "POST", "/v1/generate", body, ts_shift=-61)
    assert old.status_code == 401
    future = await _raw(box, "POST", "/v1/generate", body, ts_shift=61)
    assert future.status_code == 401
    none = await _raw(box, "POST", "/v1/generate", body, sign=False)
    assert none.status_code == 401
    good = await _raw(box, "POST", "/v1/generate", body)
    assert good.status_code == 200                                        # đúng khoá, đúng giờ ⇒ qua
    assert protocol.SIG_HEADER in good.headers                            # trả lời đã xác thực thì có ký
    # Phát lại đúng bản ký đó trong cửa sổ 60 giây ⇒ 401
    mbody = json.dumps({"request_id": "r-2", "session": SESSION}).encode()
    hdr = protocol.sign_request(KEY, "POST", "/v1/models", mbody, box.clock())
    async with httpx.AsyncClient(transport=box.transport, base_url=URL) as c:
        first = await c.post("/v1/models", content=mbody, headers=hdr)
        again = await c.post("/v1/models", content=mbody, headers=hdr)
    assert first.status_code == 200 and again.status_code == 401


async def test_body_over_limit_413_and_prompt_over_limit_413(box) -> None:  # type: ignore[no-untyped-def]
    huge = await _raw(box, "POST", "/v1/generate", b"x" * (protocol.MAX_BODY_BYTES + 1), sign=False)
    assert huge.status_code == 413 and huge.json()["error"]["code"] == "too_large"
    big_prompt = await _raw(box, "POST", "/v1/generate", _body(prompt="a" * (protocol.MAX_PROMPT_BYTES + 1)))
    assert big_prompt.status_code == 413
    ok_edge = await _raw(box, "POST", "/v1/generate", _body(prompt="a" * protocol.MAX_PROMPT_BYTES))
    assert ok_edge.status_code == 200


async def test_unsigned_models_is_401_and_healthz_200(box) -> None:  # type: ignore[no-untyped-def]
    """Hợp đồng với e2e_agy_box.sh của H3: /healthz 200, POST /v1/models không ký ⇒ 401."""
    assert (await _raw(box, "GET", "/healthz", b"", sign=False)).status_code == 200
    assert (await _raw(box, "POST", "/v1/models", b"{}", sign=False)).status_code == 401


async def test_unknown_route_and_method(box) -> None:  # type: ignore[no-untyped-def]
    assert (await _raw(box, "GET", "/v1/generate", b"")).status_code == 405
    assert (await _raw(box, "POST", "/v1/khong-co", b"{}")).status_code == 404


async def test_bad_json_and_fields_400(box) -> None:  # type: ignore[no-untyped-def]
    for raw in (b"khong phai json", b"[]", _body(prompt=""), _body(prompt=5), _body(request_id="a b"),
                _body(model="--dangerously-skip-permissions")):
        r = await _raw(box, "POST", "/v1/generate", raw)
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", raw


async def test_session_is_required_and_checked(box) -> None:  # type: ignore[no-untyped-def]
    """Review v0.1.63 (P1-1): hộp không có kho phiên — thiếu / sai khuôn `session` ⇒ 400, agy không được chạy."""
    for sess in (None, "", "khong-phai-json", "[]", json.dumps({"a": "x" * 70_000}), 5):
        body = json.loads(_body())
        if sess is None:
            body.pop("session")
        else:
            body["session"] = sess
        r = await _raw(box, "POST", "/v1/generate", json.dumps(body).encode())
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", sess
    m = await _raw(box, "POST", "/v1/models", b'{"request_id": "r-9"}')
    assert m.status_code == 400


async def test_box_turn_home_is_fresh_and_never_persisted(box, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Mỗi lượt trong hộp: HOME tạm chỉ có tệp token gửi kèm; agy ghi cấu hình độc / làm mới token cũng không còn gì ở
    lượt sau, và kho phiên của api không bị đụng tới."""
    app = server.create_app(server.Config(key_file=str(box.keyfile),
                                          cli_binary=_wrapper(tmp_path, "agy2", FIX / "fake_agy_home.py")),
                            clock=box.clock)
    c = AgyBoxClient(URL, str(box.keyfile), 20, cli_home=str(box.home), transport=httpx.ASGITransport(app=app),
                     clock=box.clock)
    before = sorted(p.name for p in box.home.iterdir())
    for head in ("POISON", "REFRESH"):
        assert (await c.generate("gemini-3.8-flash", [Message("user", head)], json_mode=False,
                                 temperature=0)).text == "echo:" + head
    out = json.loads((await c.generate("gemini-3.8-flash", [Message("user", "LIST")], json_mode=False,
                                       temperature=0)).text)
    assert out["files"] == [".gemini/antigravity-cli/antigravity-oauth-token"]
    assert sorted(p.name for p in box.home.iterdir()) == before
    assert (box.home / "antigravity-oauth-token").read_text() == SESSION            # không chép ngược từ hộp


async def test_client_without_session_fails_before_network(box) -> None:  # type: ignore[no-untyped-def]
    (box.home / "antigravity-oauth-token").write_text("khong-phai-json")
    with pytest.raises(AuthFailed):
        await box.make().generate("gemini-3.8-flash", [Message("user", "x")], json_mode=False, temperature=0)
    (box.home / "antigravity-oauth-token").unlink()
    (box.home / "antigravity-oauth-token.before-login").write_text(SESSION)
    with pytest.raises(ProviderError) as e:
        await box.make().discover_models()
    assert not isinstance(e.value, AuthFailed)


# ─── server + client: luồng thật với agy giả ─────────────────────────────────────────────────────────────────

async def test_generate_roundtrip_via_client(box) -> None:  # type: ignore[no-untyped-def]
    c = box.make()
    assert c.kind == "antigravity_cli"
    out = await c.generate("gemini-3.8-flash", [Message("system", "S"), Message("user", "hỏi gì đó")],
                           json_mode=True, temperature=0.2, effort="high")
    assert out.text == "echo:[system]\nS\n\nhỏi gì đó"
    assert (out.tokens_in, out.tokens_out) == (3, 4)
    assert "ms" in out.raw


async def test_models_roundtrip_via_client(box) -> None:  # type: ignore[no-untyped-def]
    c = box.make()
    models = await c.discover_models()
    assert [m["id"] for m in models] == ["gemini-3.8-flash"]
    assert await c.list_models() == ["gemini-3.8-flash"]
    assert c.last_models_raw and "Available models" in c.last_models_raw


async def test_client_rejects_oversize_prompt_before_network(box) -> None:  # type: ignore[no-untyped-def]
    c = box.make()
    with pytest.raises(BadRequest):
        await c.generate("gemini-3.8-flash", [Message("user", "a" * 130_000)], json_mode=False, temperature=0)


async def test_wrong_key_client_gets_box_unauthorized(box, tmp_path) -> None:  # type: ignore[no-untyped-def]
    other = tmp_path / "other_key"
    other.write_text(base64.b64encode(bytes(32)).decode())
    with pytest.raises(BoxUnauthorized) as e:
        await box.make(key_file=str(other)).generate("gemini-3.8-flash", [Message("user", "x")], json_mode=False,
                                                     temperature=0)
    assert isinstance(e.value, ProviderError) and not isinstance(e.value, AuthFailed)   # không đánh dấu CLI hết hạn


async def test_clock_skew_between_client_and_box_is_401(box) -> None:  # type: ignore[no-untyped-def]
    c = box.make()
    c.clock = lambda: box.clock() - 120                # đồng hồ client lệch 2 phút
    with pytest.raises(BoxUnauthorized):
        await c.discover_models()


async def test_timeout_maps_to_provider_error(box) -> None:  # type: ignore[no-untyped-def]
    c = box.make(timeout=1.0)
    with pytest.raises(ProviderError) as e:
        await c.generate("gemini-3.8-flash", [Message("user", "SLEEP")], json_mode=False, temperature=0)
    assert not isinstance(e.value, (AuthFailed, RateLimited))
    assert "quá" in str(e.value)


async def test_error_mapping_from_box(box, tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = box.make()
    with pytest.raises(RateLimited):
        await c.generate("gemini-3.8-flash", [Message("user", "RATE")], json_mode=False, temperature=0)
    with pytest.raises(ProviderError) as e:
        await c.generate("gemini-3.8-flash", [Message("user", "BOOM")], json_mode=False, temperature=0)
    assert not isinstance(e.value, (AuthFailed, RateLimited, BadRequest))
    with pytest.raises(BadRequest):
        await c.generate("--dangerously-skip-permissions", [Message("user", "x")], json_mode=False, temperature=0)
    (box.home / "antigravity-oauth-token").unlink()
    with pytest.raises(AuthFailed):
        await c.generate("gemini-3.8-flash", [Message("user", "x")], json_mode=False, temperature=0)


async def test_model_rejected_keeps_details(tmp_path) -> None:  # type: ignore[no-untyped-def]
    keyfile = tmp_path / "k"
    keyfile.write_text(KEY_B64)
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    token = {"email": "an@example.vn"}
    claims = base64.urlsafe_b64encode(json.dumps(token).encode()).decode().rstrip("=")
    home.mkdir(parents=True)
    (home / "antigravity-oauth-token").write_text(json.dumps({"access_token": "t", "id_token": f"h.{claims}.s"}))
    clock = Clock()
    app = server.create_app(server.Config(key_file=str(keyfile),
                                          cli_binary=_wrapper(tmp_path, "agy", FIX / "fake_agy_multi.py")), clock=clock)
    c = AgyBoxClient(URL, str(keyfile), 20, cli_home=str(home), transport=httpx.ASGITransport(app=app), clock=clock)
    with pytest.raises(ModelRejected) as e:
        await c.generate("khong-co-model", [Message("user", "x")], json_mode=False, temperature=0)
    assert e.value.what == "model" and "gemini-3.8-flash" in e.value.available


async def test_busy_when_max_jobs_reached(box, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    box.cfg.max_jobs = 1
    app = server.create_app(box.cfg, clock=box.clock)
    t = httpx.ASGITransport(app=app)
    mk = lambda: AgyBoxClient(URL, str(box.keyfile), 30, cli_home=str(box.home), transport=t,  # noqa: E731
                              clock=box.clock)
    slow = asyncio.create_task(mk().generate("gemini-3.8-flash", [Message("user", "SLEEP")], json_mode=False,
                                             temperature=0))
    await asyncio.sleep(0.5)
    with pytest.raises(RateLimited) as e:
        await mk().generate("gemini-3.8-flash", [Message("user", "x")], json_mode=False, temperature=0)
    assert e.value.retry_after == protocol.BUSY_RETRY_AFTER_S
    slow.cancel()
    await asyncio.gather(slow, return_exceptions=True)


async def test_tampered_response_signature_rejected(box) -> None:  # type: ignore[no-untyped-def]
    inner = box.transport

    class Evil(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            r = await inner.handle_async_request(request)
            body = (await r.aread()).replace(b"echo", b"EVIL")
            headers = {k: v for k, v in r.headers.items() if k.lower() != "content-length"}
            return httpx.Response(r.status_code, headers=headers, content=body)

    c = AgyBoxClient(URL, str(box.keyfile), 20, cli_home=str(box.home), transport=Evil(), clock=box.clock)
    with pytest.raises(ProviderError, match="chữ ký"):
        await c.generate("gemini-3.8-flash", [Message("user", "x")], json_mode=False, temperature=0)


# ─── cô lập: hộp không đọc được biến GH_* nào ngoài danh sách, agy con không thấy biến GH_* ────────────────────

def test_config_reads_only_allowlisted_env(tmp_path: Path) -> None:
    keyfile = tmp_path / "k"
    env = {"GH_AGYBOX_KEY_FILE": str(keyfile), "GH_CLI_HOME": "/h", "GH_CLI_BINARY": "agy", "GH_AGYBOX_MAX_JOBS": "3",
           "GH_DATABASE_URL": "postgresql://x", "GH_ADMIN_DATABASE_URL": "y", "GH_REDIS_URL": "redis://r",
           "GH_MASTER_KEY_FILE": "/run/secrets/gh_master_key", "GH_BRIDGE_KEY_FILE": "b", "GH_BROWSER_KEY_FILE": "c"}
    cfg = server.load_config(env)
    assert (cfg.key_file, cfg.cli_binary, cfg.max_jobs) == (str(keyfile), "agy", 1)   # P3: luôn một lượt
    assert not any("postgres" in str(v) or "redis" in str(v) or "master" in str(v) for v in vars(cfg).values())
    scrubbed = dict(env)
    server.scrub_environ(scrubbed)
    # GH_CLI_HOME của ảnh api cũng bị xoá: hộp không có kho phiên (review v0.1.63, P1-1).
    assert sorted(scrubbed) == sorted(["GH_AGYBOX_KEY_FILE", "GH_CLI_BINARY", "GH_AGYBOX_MAX_JOBS"])
    with pytest.raises(RuntimeError):
        server.load_config({})                                           # thiếu GH_AGYBOX_KEY_FILE


def test_server_imports_no_db_redis_or_settings() -> None:
    code = ("import sys, gh.agybox.server;"
            "bad=[m for m in ('asyncpg','redis','arq','gh.config','gh.db','gh.crypto') if m in sys.modules];"
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=API_DIR, capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "HOME": "/tmp"})
    assert r.returncode == 0, r.stdout + r.stderr


async def test_agy_child_env_names(box, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for k in ("GH_DATABASE_URL", "GH_MASTER_KEY", "GH_REDIS_URL", "GH_AGYBOX_KEY_FILE"):
        monkeypatch.setenv(k, "x")
    r = await _raw(box, "POST", "/v1/generate", _body(prompt="ENV"))
    assert r.status_code == 200
    names = json.loads(r.json()["text"])
    assert not [n for n in names if n.startswith("GH_")], names


# ─── trạng thái hộp ─────────────────────────────────────────────────────────────────────────────────────────

async def test_box_status_ok_down_not_installed(box, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    boxclient.reset_status_cache()
    st = await boxclient.box_status(URL, str(box.keyfile), transport=box.transport, clock=box.clock, use_cache=False)
    assert st["state"] == "ok" and st["agy_version"] == "1.2.9" and st["checked_at"].endswith("Z")

    class Refused(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

    down = await boxclient.box_status(URL, str(box.keyfile), transport=Refused(), clock=box.clock, use_cache=False)
    assert down["state"] == "down" and down["agy_version"] is None
    assert (await boxclient.box_status("", str(box.keyfile), use_cache=False))["state"] == "not_installed"
    assert (await boxclient.box_status(URL, str(tmp_path / "khong-co"), use_cache=False))["state"] == "not_installed"
    monkeypatch.delenv("GH_AGYBOX_URL", raising=False)
    assert (await boxclient.box_status(use_cache=False))["state"] == "not_installed"


async def test_box_status_cache_ttl(box) -> None:  # type: ignore[no-untyped-def]
    boxclient.reset_status_cache()
    calls = []

    class Count(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json={"ok": True, "service": "agy-box", "protocol": 1, "agy_version": "1.2.9",
                                             "cli_logged_in": True})

    for _ in range(3):
        await boxclient.box_status(URL, str(box.keyfile), transport=Count(), clock=box.clock)
    assert len(calls) == 1
    box.clock.t += boxclient.STATUS_TTL_S + 1
    await boxclient.box_status(URL, str(box.keyfile), transport=Count(), clock=box.clock)
    assert len(calls) == 2
    boxclient.reset_status_cache()


# ─── khối sức khoẻ `agy_box` + sự cố `agy_box.down` ─────────────────────────────────────────────────────────────

def _fake_status(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    async def fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"state": state, "checked_at": "2026-10-16T10:00:00Z",
                "agy_version": "1.2.9" if state == "ok" else None, "cli_logged_in": None}

    monkeypatch.setattr(boxclient, "box_status", fake)


async def _evaluate(redis: Any, org: Any, now: Any) -> None:
    from gh import health
    from gh.db import sessionmaker

    async with sessionmaker()() as s:
        await health.evaluate(s, redis, org, now=now, started_at=now)
        await s.commit()


async def _alert(db: Any) -> Any:
    from sqlalchemy import text

    await db.commit()
    return (await db.execute(text("""SELECT severity, title, link, cleared_at FROM ops.health_alerts
                                     WHERE key = 'agy_box.down'"""))).one_or_none()


async def _bells(db: Any) -> int:
    from sqlalchemy import text

    await db.commit()
    q = text("SELECT count(*) FROM core.notifications WHERE kind = 'agy_box.down'")
    return int((await db.execute(q)).scalar_one())


async def test_health_block_absent_when_not_installed(owner_api, app, db, redis) -> None:  # type: ignore[no-untyped-def]
    from gh import health
    from tests.phase2 import org_id

    org = await org_id(db)
    snap = await health.collect(db, redis, org)
    assert "agy_box" not in snap                      # giữ nguyên khuôn cũ khi chưa cấu hình hộp (dev, bản cài cũ)
    r = await owner_api.get("/system/health")
    assert r.status_code == 200 and "agy_box" not in r.json()


async def test_health_block_down_makes_overall_warn(owner_api, app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from gh import health
    from tests.phase2 import org_id

    org = await org_id(db)
    _fake_status(monkeypatch, "down")
    snap = await health.collect(db, redis, org)
    assert snap["agy_box"]["state"] == "down" and snap["overall"] in ("warn", "bad")
    _fake_status(monkeypatch, "ok")
    snap = await health.collect(db, redis, org)
    assert snap["agy_box"] == {"state": "ok", "checked_at": "2026-10-16T10:00:00Z", "agy_version": "1.2.9"}


async def test_down_incident_needs_grace_rings_once_and_clears(owner_api, app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from gh import health
    from tests.phase2 import org_id

    org = await org_id(db)
    now = datetime.now(UTC)
    _fake_status(monkeypatch, "down")
    await _evaluate(redis, org, now)
    assert await _alert(db) is None and await redis.get(health.AGY_BOX_DOWN_SINCE_KEY) is not None   # mới thấy tắt: chờ
    await _evaluate(redis, org, now + timedelta(seconds=health.AGY_BOX_DOWN_GRACE_SECONDS - 5))
    assert await _alert(db) is None                                                                   # chưa đủ ân hạn
    await _evaluate(redis, org, now + timedelta(seconds=health.AGY_BOX_DOWN_GRACE_SECONDS + 5))
    a = await _alert(db)
    assert a is not None and a.severity == "warn" and a.cleared_at is None and a.link == health.HEALTH_LINK
    assert a.title == health.AGY_BOX_DOWN_TITLE
    await _evaluate(redis, org, now + timedelta(seconds=health.AGY_BOX_DOWN_GRACE_SECONDS + 60))
    assert await _bells(db) == 1                                                                      # không chuông lặp
    [issue] = [i for i in await health.active_issues(db, org) if i["kind"] == "agy_box.down"]
    assert issue["severity"] == "warn"
    _fake_status(monkeypatch, "ok")
    await _evaluate(redis, org, now + timedelta(seconds=health.AGY_BOX_DOWN_GRACE_SECONDS + 90))
    assert (await _alert(db)).cleared_at is not None and await redis.get(health.AGY_BOX_DOWN_SINCE_KEY) is None


async def test_blip_shorter_than_grace_never_alerts(owner_api, app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from tests.phase2 import org_id

    org = await org_id(db)
    now = datetime.now(UTC)
    _fake_status(monkeypatch, "down")
    await _evaluate(redis, org, now)
    _fake_status(monkeypatch, "ok")
    await _evaluate(redis, org, now + timedelta(seconds=30))
    _fake_status(monkeypatch, "down")
    await _evaluate(redis, org, now + timedelta(seconds=200))            # lần tắt mới: mốc đếm lại từ đầu
    assert await _alert(db) is None


async def test_not_installed_never_alerts(owner_api, app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from tests.phase2 import org_id

    org = await org_id(db)
    _fake_status(monkeypatch, "not_installed")
    await _evaluate(redis, org, datetime.now(UTC))
    assert await _alert(db) is None


def test_down_body_tells_to_start_not_update() -> None:
    """Review v0.1.63 (P3): hộp tắt ⇒ `genh start` dựng lại hộp (không tải bản mới); `genh update` là việc khác."""
    from gh import health

    assert "genh start" in health.AGY_BOX_DOWN_BODY and "genh update" not in health.AGY_BOX_DOWN_BODY
