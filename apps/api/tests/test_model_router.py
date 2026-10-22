"""Bộ định tuyến model: 429 xoay khoá, hết chuỗi → cảnh báo (một lần/giờ), hạn mức dưới 20% → cảnh báo,
ngắt mạch sau 3 lỗi liên tiếp, mọi lượt gọi ghi agent.model_calls."""

import httpx
import pytest
from sqlalchemy import text

from gh import crypto
from gh.db import sessionmaker
from gh.providers.clients import Message
from gh.providers.router import KEY_AAD, ModelRouter, ModelUnavailable, breaker_key, cooldown_key
from tests.conftest import FAKE_PUBLIC_IP
from tests.phase2 import org_id

MSGS = [Message("user", "xin chào")]
OK = {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


async def provider(db, org, name: str, rank: int, keys: list[str], quota: int | None = None):  # type: ignore[no-untyped-def]
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                    VALUES (:o, 'openai_compat', :n, :e, :r) RETURNING id"""),
                            {"o": org, "n": name, "e": f"https://{name}.test/v1", "r": rank})).scalar_one()
    for i, k in enumerate(keys):
        await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4, rotation_order)
                                 VALUES (:p, :l, :s, :f, :r)"""),
                         {"p": pid, "l": f"KEY-{i + 1:02d}", "s": crypto.encrypt(k.encode(), KEY_AAD), "f": k[-4:],
                          "r": i})
    await db.execute(text("INSERT INTO agent.models (provider_id, model_name, daily_quota) VALUES (:p, 'm1', :q)"),
                     {"p": pid, "q": quota})
    await db.commit()
    return pid


def transport(behaviour):  # type: ignore[no-untyped-def]
    """behaviour(host, bearer) → (status, json). Ghi lại các lượt gọi."""
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        key = req.headers["authorization"].removeprefix("Bearer ")
        # v0.1.45 (F-49): nhà cung cấp gọi qua IP đã ghim — tên máy gốc nằm ở header Host.
        host = req.headers["host"].split(":")[0]
        assert req.url.host == FAKE_PUBLIC_IP, req.url
        seen.append((host, key))
        status, body = behaviour(host, key)
        return httpx.Response(status, json=body)
    t = httpx.MockTransport(handler)
    t.seen = seen  # type: ignore[attr-defined]
    return t


async def alerts(db, kind: str) -> int:  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE alert_type = :t"), {"t": kind})).scalar()  # type: ignore[no-any-return]


async def test_429_rotates_to_next_key(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await provider(db, org, "alpha", 1, ["sk-key-one-1111", "sk-key-two-2222"])
    t = transport(lambda host, key: (429, {"error": "slow down"}) if key.endswith("1111") else (200, OK))
    r = ModelRouter(sessionmaker(), redis, transport=t)
    out = await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)
    assert out.text == '{"ok": true}' and out.provider == "alpha"
    assert [k[-4:] for _, k in t.seen] == ["1111", "2222"]  # type: ignore[attr-defined]
    k1 = (await db.execute(text("SELECT id FROM agent.provider_keys WHERE last4 = '1111'"))).scalar()
    assert await redis.exists(cooldown_key(k1))
    calls = (await db.execute(text("SELECT status FROM agent.model_calls ORDER BY id"))).scalars().all()
    assert calls == ["rate_limited", "ok"]
    # Lượt sau bỏ qua khoá đang nghỉ.
    await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)
    assert [k[-4:] for _, k in t.seen][-1] == "2222"  # type: ignore[attr-defined]


async def test_chain_exhausted_raises_and_alerts_once(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await provider(db, org, "alpha", 1, ["sk-alpha-aaaa"])
    await provider(db, org, "beta", 2, ["sk-beta-bbbb"])
    t = transport(lambda host, key: (429, {}))
    r = ModelRouter(sessionmaker(), redis, transport=t)
    with pytest.raises(ModelUnavailable) as e:
        await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)
    assert {h for h, _ in t.seen} == {"alpha.test", "beta.test"}  # type: ignore[attr-defined]
    assert "429" in str(e.value)
    await redis.delete(*[k async for k in redis.scan_iter("gh:cooldown:*")] or ["x"])
    with pytest.raises(ModelUnavailable):
        await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)
    assert await alerts(db, "model_chain_exhausted") == 1       # tối đa một lần / giờ


async def test_quota_below_20_percent_alerts_once_and_fails_over(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await provider(db, org, "alpha", 1, ["sk-alpha-aaaa"], quota=5)
    await provider(db, org, "beta", 2, ["sk-beta-bbbb"])
    t = transport(lambda host, key: (200, OK))
    r = ModelRouter(sessionmaker(), redis, transport=t)
    for _ in range(4):
        await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)
    assert await alerts(db, "model_quota_low") == 1
    await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)
    assert await alerts(db, "model_quota_low") == 1
    out = await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)
    assert out.provider == "beta"                                # alpha hết hạn mức ngày → chuyển tiếp


async def test_breaker_opens_after_three_failures(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pid = await provider(db, org, "alpha", 1, ["sk-alpha-aaaa"])
    await provider(db, org, "beta", 2, ["sk-beta-bbbb"])
    t = transport(lambda host, key: (500, {}) if host == "alpha.test" else (200, OK))
    r = ModelRouter(sessionmaker(), redis, transport=t)
    for _ in range(3):
        assert (await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)).provider == "beta"
    assert await redis.exists(breaker_key(pid))
    before = len(t.seen)  # type: ignore[attr-defined]
    await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)
    assert [h for h, _ in t.seen[before:]] == ["beta.test"]  # type: ignore[attr-defined]


async def test_binding_puts_bound_provider_first(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await provider(db, org, "alpha", 1, ["sk-alpha-aaaa"])
    beta = await provider(db, org, "beta", 2, ["sk-beta-bbbb"])
    mid = (await db.execute(text("SELECT id FROM agent.models WHERE provider_id = :p"), {"p": beta})).scalar()
    await db.execute(text("""INSERT INTO agent.bindings (org_id, agent_key, model_id, context_tokens)
                             VALUES (:o, 'core.refinery', :m, 8000)"""),
                     {"o": org, "m": mid})
    await db.commit()
    r = ModelRouter(sessionmaker(), redis, transport=transport(lambda h, k: (200, OK)))
    assert (await r.generate(org, agent_key="core.refinery", purpose="test", messages=MSGS)).provider == "beta"
