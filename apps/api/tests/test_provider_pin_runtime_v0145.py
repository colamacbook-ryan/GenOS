"""v0.1.45 (F-49) — ghim DNS lúc GỌI nhà cung cấp AI: dòng cũ trỏ siêu dữ liệu đám mây / tên dịch vụ compose (chèn
thẳng DB, không qua route ghi) bị chặn trước khi có request nào ra ngoài; địa chỉ hợp lệ đi tới IP đã ghim với Host
gốc + SNI. Không ép https lúc gọi."""

from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh import crypto
from gh.db import admin_sessionmaker, sessionmaker
from gh.gen import jev
from gh.providers.clients import BadRequest, Message, OpenAICompatClient, QuotaExhausted, RateLimited, _raise_for
from gh.providers.router import KEY_AAD, ModelRouter, ModelUnavailable
from tests.conftest import FAKE_PUBLIC_IP, Api
from tests.phase2 import org_id

KEY = "sk-khoa-gia-provider-123456"
OK = {"choices": [{"message": {"content": "xin chào"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class Recorder:
    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m1"}]})
        return httpx.Response(200, json=OK)


async def _provider(db: Any, endpoint: str, name: str = "Nguồn cũ") -> str:
    org = await org_id(db)
    async with admin_sessionmaker()() as adb:
        pid = (await adb.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                         VALUES (:o, 'openai_compat', :n, :e, 0) RETURNING id"""),
                                 {"o": org, "n": name, "e": endpoint})).scalar_one()
        await adb.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4,
                                  rotation_order) VALUES (:p, 'KEY-01', :s, :f, 0)"""),
                          {"p": pid, "s": crypto.encrypt(KEY.encode(), KEY_AAD), "f": KEY[-4:]})
        await adb.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'm1')"), {"p": pid})
        await adb.commit()
    return str(pid)


@pytest.mark.parametrize("endpoint", ["https://169.254.169.254/v1", "https://db/v1", "http://0.0.0.0:8080/v1",
                                      "https://gen-harness-redis-1/v1"])
async def test_provider_test_blocked_for_forbidden_endpoint(owner_api: Api, app: Any, db: Any,
                                                            endpoint: str) -> None:
    rec = Recorder()
    app.state.model_router.transport = httpx.MockTransport(rec.handle)
    pid = await _provider(db, endpoint)
    r = await owner_api.send("POST", f"/providers/{pid}/test", {})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is False and "vùng mạng bị cấm" in out["error"], out
    assert KEY not in r.text
    assert rec.seen == []


async def test_router_generate_blocked_for_forbidden_endpoint(app: Any, db: Any, redis: Any) -> None:
    rec = Recorder()
    await _provider(db, "https://169.254.169.254/v1")
    router = ModelRouter(sessionmaker(), redis, transport=httpx.MockTransport(rec.handle))
    with pytest.raises(ModelUnavailable) as e:
        await router.generate(await org_id(db), agent_key="core.refinery", purpose="test",
                              messages=[Message("user", "xin chào")])
    assert "vùng mạng bị cấm" in "; ".join(e.value.reasons)
    assert rec.seen == []


async def test_valid_endpoint_goes_to_pinned_ip(owner_api: Api, app: Any, db: Any) -> None:
    rec = Recorder()
    app.state.model_router.transport = httpx.MockTransport(rec.handle)
    pid = await _provider(db, "https://api.nha-cung-cap.vn/v1")
    r = await owner_api.send("POST", f"/providers/{pid}/test", {})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert len(rec.seen) == 1
    req = rec.seen[0]
    assert req.url.host == FAKE_PUBLIC_IP and req.url.path == "/v1/models"
    assert req.headers["host"] == "api.nha-cung-cap.vn"
    assert req.extensions.get("sni_hostname") == "api.nha-cung-cap.vn"


async def test_http_provider_not_forced_https_and_lan_ok() -> None:
    """Dòng cũ http + khoá (vd Ollama trong LAN) vẫn chạy lúc gọi — ép https làm ở bước ghi cấu hình."""
    rec = Recorder()
    c = OpenAICompatClient("http://10.0.0.7:11434/v1", KEY, transport=httpx.MockTransport(rec.handle))
    assert (await c.list_models()) == ["m1"]
    assert rec.seen[0].url.host == "10.0.0.7" and rec.seen[0].url.port == 11434
    c2 = OpenAICompatClient("http://localhost:11434/v1", KEY, transport=httpx.MockTransport(rec.handle))
    assert (await c2.list_models()) == ["m1"]
    with pytest.raises(BadRequest, match="vùng mạng bị cấm"):
        await OpenAICompatClient("http://redis:6379/v1", KEY, transport=httpx.MockTransport(rec.handle)).list_models()
    # Sai dạng (cổng ngoài 0–65535) → "không hợp lệ", không gán nhầm "vùng mạng bị cấm" hay lộ chữ "máy chủ MCP".
    with pytest.raises(BadRequest, match="không hợp lệ") as bad:
        await OpenAICompatClient("http://10.0.0.7:99999/v1", KEY,
                                 transport=httpx.MockTransport(rec.handle)).list_models()
    assert "vùng mạng bị cấm" not in str(bad.value) and "MCP" not in str(bad.value)
    assert len(rec.seen) == 2


async def test_error_body_redacted() -> None:
    def echo(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"lỗi: {req.headers['authorization']} " + "y" * 500)

    c = OpenAICompatClient("https://api.nha-cung-cap.vn/v1", KEY, transport=httpx.MockTransport(echo))
    with pytest.raises(Exception) as e:  # noqa: PT011 — ProviderError
        await c.list_models()
    assert KEY not in str(e.value) and len(str(e.value)) < 220


async def test_jev_pinned() -> None:
    rec: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        rec.append(req)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"choice": "có"}'}}]})

    c = jev.JevClient("https://169.254.169.254/v1", KEY, transport=httpx.MockTransport(handle))
    with pytest.raises(jev.JevError, match="vùng mạng bị cấm"):
        await c.ping()
    assert rec == []
    ok = jev.JevClient("https://openrouter.ai/api/v1", KEY, transport=httpx.MockTransport(handle))
    assert (await ok.ping()).label == "có"
    assert rec[0].url.host == FAKE_PUBLIC_IP and rec[0].headers["host"] == "openrouter.ai"


def test_quota_marker_deep_in_429_body_is_classified() -> None:
    """Gemini để dấu hết hạn mức ngày ("…PerDay…") sâu trong JSON — phân loại trên thân đủ 4000 ký tự (đã che),
    chỉ cắt 200 ký tự cho thông điệp."""
    body = ('{"error": {"code": 429, "message": "Resource exhausted", "details": [' + " " * 400
            + '{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}}')
    with pytest.raises(QuotaExhausted) as e:
        _raise_for(httpx.Response(429, text=body))
    assert len(str(e.value)) < 260
    with pytest.raises(RateLimited):
        _raise_for(httpx.Response(429, text='{"error": "slow down"}'))
