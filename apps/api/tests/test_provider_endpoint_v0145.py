"""v0.1.45 (F-49, phần ghi) — địa chỉ nhà cung cấp AI được kiểm lúc GHI (POST /providers, thêm khoá).

Có khoá + http:// tới IP công cộng ⇒ 422 (phải https://); http:// trong mạng nội bộ / cùng máy (Ollama, LM Studio)
vẫn được — cùng quy tắc với máy chủ MCP và Gen-hub; vùng mạng cấm (169.254.x siêu dữ liệu đám mây, 0.0.0.0) ⇒ 422 qua
`mcp_client.pin_endpoint(endpoint, True)`; không phân giải được lúc ghi ⇒ cho qua (kiểm lại lúc gọi)."""

import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker
from tests.conftest import Api, verify_pin

KEY = "sk-test-khoa-endpoint-0001"


def _body(endpoint: str, keys: list[str] | None = None) -> dict[str, object]:
    return {"kind": "openai_compat", "name": "OpenAI tương thích", "endpoint": endpoint,
            "keys": [KEY] if keys is None else keys, "models": ["gpt-x"]}


@pytest.mark.parametrize(("endpoint", "needle"), [
    ("http://169.254.169.254/v1", "vùng mạng bị cấm"),
    ("https://169.254.169.254/v1", "vùng mạng bị cấm"),
    ("https://0.0.0.0/v1", "vùng mạng bị cấm"),
    ("http://example.com/v1", "https://"),
    ("ftp://example.com/v1", "https://"),
    ("http://", "không hợp lệ"),
    ("https://example.com:99999/v1", "không hợp lệ"),
])
async def test_create_provider_rejects_bad_endpoint(owner_api: Api, endpoint: str, needle: str) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/providers", _body(endpoint))
    assert r.status_code == 422, r.text
    assert needle in r.text
    assert all(p["kind"] != "openai_compat" for p in (await owner_api.get("/providers")).json())


async def test_create_provider_https_public_ok(owner_api: Api) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/providers", _body("https://example.com/v1"))
    assert r.status_code == 201, r.text
    assert r.json()["endpoint"] == "https://example.com/v1"


@pytest.mark.parametrize("endpoint", ["http://10.0.0.7:11434/v1", "http://192.168.1.20:1234/v1",
                                      "http://localhost:11434/v1"])
async def test_create_provider_http_lan_with_key_ok(owner_api: Api, endpoint: str) -> None:
    """Sửa review v0.1.45: LLM trong LAN (Ollama/LM Studio/vLLM) qua http:// kèm khoá giả vẫn tạo được."""
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/providers", _body(endpoint))
    assert r.status_code == 201, r.text
    assert r.json()["endpoint"] == endpoint


async def test_add_key_to_legacy_http_provider_rejected(owner_api: Api) -> None:
    async with admin_sessionmaker()() as s:
        pid = (await s.execute(text("""
            INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
            SELECT id, 'openai_compat', 'Cũ qua http', 'http://llm.example.com/v1', 9 FROM core.organizations
            RETURNING id"""))).scalar_one()
        await s.commit()
    await verify_pin(owner_api)
    r = await owner_api.send("POST", f"/providers/{pid}/keys", {"secret": KEY})
    assert r.status_code == 422, r.text
    assert "https://" in r.text
    async with admin_sessionmaker()() as s:
        n = (await s.execute(text("SELECT count(*) FROM agent.provider_keys WHERE provider_id = :p"),
                             {"p": pid})).scalar_one()
    assert n == 0
