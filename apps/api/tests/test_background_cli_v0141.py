"""v0.1.41 (F-86) — việc nền (sàng lọc tin, trực việc, Bản tin Gen): Claude Code CLI chỉ chạy khi Owner cho phép.

Claude Code CLI (gói Pro/Max cá nhân của Sếp) chỉ chạy việc nền khi Owner cho phép: cảnh báo + tích xác nhận + PIN
(QD-12 — điều khoản gói Claude, GIỮ NGUYÊN). v0.1.62: Antigravity CLI chạy được việc nền, không cần cho phép (luật
F-22 "agy không bao giờ chạy việc nền" đã gỡ theo quyết định của Owner 16/10/2026). Chuỗi việc nền chỉ còn Claude
Code CLI chưa được phép ⇒ sự cố `ai.background_no_source` (một chuông), tự đóng khi việc nền chạy lại được (có khoá
API, có agy chạy được, hoặc Owner cho phép Claude Code CLI).
"""

import uuid
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.db import sessionmaker
from gh.providers.clients import Completion, Message
from gh.providers.router import (
    BACKGROUND_CLI_REASON,
    BACKGROUND_CLI_RISK,
    BG_NO_SOURCE_FLAG,
    BG_NO_SOURCE_TRY,
    ModelRouter,
    ModelUnavailable,
    is_background,
)
from tests.conftest import Api, verify_pin
from tests.phase2 import org_id
from tests.test_model_router import OK, transport
from tests.test_model_router import provider as api_provider
from tests.test_rbac_api import login_as

MSGS = [Message("user", "nội dung của khách")]
FAKE_KEY = "sk-live-or-9911"   # khoá giả — không bao giờ là khoá thật


class FakeCli:
    """CLI giả: ghi lại lượt gọi, trả văn bản cố định."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def __call__(self) -> "FakeCli":
        return self

    async def generate(self, model: str, messages: list[Message], **kw: Any) -> Completion:
        self.calls += 1
        return Completion(f"{self.name}-ok", 3, 2)


async def cli_provider(db, org: uuid.UUID, kind: str, rank: int) -> uuid.UUID:  # type: ignore[no-untyped-def]
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, failover_rank, auth_state)
                                    VALUES (:o, :k, :n, :r, 'ok') RETURNING id"""),
                            {"o": org, "k": kind, "n": f"CLI {kind}", "r": rank})).scalar_one()
    await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, :m)"),
                     {"p": pid, "m": "sonnet" if kind == "claude_code_cli" else "gemini-3.1-pro"})
    await db.commit()
    return pid  # type: ignore[no-any-return]


async def set_background_cli(db, org: uuid.UUID, kinds: list[str]) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(settings, '{ai}',
                                 COALESCE(settings->'ai', '{}'::jsonb) || CAST(:p AS jsonb), true) WHERE id = :o"""),
                     {"o": org, "p": orjson.dumps({"background_cli": kinds}).decode()})
    await db.commit()


def make_router(redis, claude: FakeCli, agy: FakeCli | None = None,  # type: ignore[no-untyped-def]
                box: FakeCli | None = None):
    """`agy` = agy trong tiến trình; `box` = hộp riêng Antigravity (core.refinery… — H2; None = chưa cài)."""
    return ModelRouter(sessionmaker(), redis, transport=transport(lambda host, key: (200, OK)),
                       claude_factory=claude, cli_factory=agy or FakeCli("agy"),
                       box_factory=box or (lambda: None))


def test_is_background() -> None:
    assert is_background("refinery") and is_background("refinery.extract")
    assert is_background("duty_decide") and is_background("gen.briefing")
    assert not is_background("gen.turn") and not is_background("draft_translate")
    assert not is_background("setup_agent_try") and not is_background("refineryx")


# ─── bộ định tuyến ───────────────────────────────────────────────────────────

async def test_background_skips_cli_uses_api(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await cli_provider(db, org, "claude_code_cli", 1)
    await api_provider(db, org, "alpha", 2, [FAKE_KEY])
    claude = FakeCli("claude")
    r = make_router(redis, claude)
    out = await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert out.provider == "alpha" and claude.calls == 0
    assert out.attempts.count(BACKGROUND_CLI_REASON) == 1
    out = await r.generate(org, agent_key="agent:x", purpose="duty_decide", messages=MSGS)
    assert out.provider == "alpha" and claude.calls == 0
    # Gen của Owner (không phải việc nền) vẫn dùng CLI trước như cũ.
    out = await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False)
    assert out.text == "claude-ok" and claude.calls == 1


async def _health(db, org: uuid.UUID) -> Any:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT kind, severity, link, cleared_at FROM ops.health_alerts
                                      WHERE org_id = :o AND key = 'ai.background_no_source'"""),
                             {"o": org})).one_or_none()


async def _bells(db, kind: str) -> int:  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = :k"),  # type: ignore[no-any-return]
                             {"k": kind})).scalar_one()


async def test_cli_only_opens_incident_once_and_closes(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await cli_provider(db, org, "claude_code_cli", 1)
    claude, agy = FakeCli("claude"), FakeCli("agy")
    r = make_router(redis, claude, agy)
    with pytest.raises(ModelUnavailable) as ei:
        await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert set(ei.value.reasons) == {BACKGROUND_CLI_REASON}
    h = await _health(db, org)
    assert h is not None and h.cleared_at is None and h.kind == "ai.background_no_source"
    assert h.severity == "warn" and h.link == "/system?tab=brain"
    body = (await db.execute(text("""SELECT body FROM ops.health_alerts WHERE org_id = :o
                                     AND key = 'ai.background_no_source'"""), {"o": org})).scalar_one()
    assert "API & Model (Thêm nhà cung cấp)" in body and "Bộ não AI" in body
    assert await _bells(db, "ai.background_no_source") == 1
    assert await redis.exists(BG_NO_SOURCE_FLAG.format(org))
    # Không dùng biz.alerts cho trường hợp này.
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE alert_type LIKE 'model_chain%'"))
            ).scalar_one() == 0
    with pytest.raises(ModelUnavailable):
        await r.generate(org, agent_key="agent:x", purpose="duty_decide", messages=MSGS)
    assert await _bells(db, "ai.background_no_source") == 1          # không chuông thứ hai
    assert claude.calls == 0 and agy.calls == 0

    await api_provider(db, org, "alpha", 3, [FAKE_KEY])
    out = await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert out.provider == "alpha"
    await db.rollback()
    h = await _health(db, org)
    assert h is not None and h.cleared_at is not None                 # sự cố tự đóng
    assert not await redis.exists(BG_NO_SOURCE_FLAG.format(org))
    assert not await redis.exists(BG_NO_SOURCE_TRY.format(org))     # lần thiếu nguồn kế tiếp báo ngay


async def test_cli_only_throttles_db_writes(owner_api, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Mỗi lô việc nền hết chuỗi KHÔNG mở phiên DB / raise_once — tối đa một lần mỗi BG_NO_SOURCE_TRY_TTL."""
    from gh import health

    calls = 0
    real = health.raise_once

    async def counting(*a: Any, **kw: Any) -> Any:
        nonlocal calls
        calls += 1
        return await real(*a, **kw)

    monkeypatch.setattr(health, "raise_once", counting)
    org = await org_id(db)
    await cli_provider(db, org, "claude_code_cli", 1)
    r = make_router(redis, FakeCli("claude"))
    for _ in range(3):
        with pytest.raises(ModelUnavailable):
            await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert calls == 1
    assert 0 < await redis.ttl(BG_NO_SOURCE_TRY.format(org)) <= 600
    await redis.delete(BG_NO_SOURCE_TRY.format(org))                 # hết ngưỡng ⇒ ghi lại (raise_once không chuông 2)
    with pytest.raises(ModelUnavailable):
        await r.generate(org, agent_key="agent:x", purpose="duty_decide", messages=MSGS)
    assert calls == 2
    assert await _bells(db, "ai.background_no_source") == 1


@pytest.mark.parametrize("fix", ["api_key", "allow_cli", "agy_source"])
async def test_incident_closes_on_health_evaluate_without_background_call(owner_api, db, redis, fix) -> None:  # type: ignore[no-untyped-def]
    """Sếp thêm khoá API / cho phép CLI lúc không có việc nền nào chạy ⇒ lượt theo dõi sức khoẻ kế tiếp đóng sự cố
    (không đợi tới bản tin 07:30/17:30)."""
    from datetime import UTC, datetime

    from gh import health

    org = await org_id(db)
    await cli_provider(db, org, "claude_code_cli", 1)
    r = make_router(redis, FakeCli("claude"))
    with pytest.raises(ModelUnavailable):
        await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert (await _health(db, org)).cleared_at is None
    # Chưa có nguồn ⇒ evaluate KHÔNG đóng.
    await health.evaluate(db, redis, org, now=datetime.now(UTC), started_at=None)
    await db.commit()
    assert (await _health(db, org)).cleared_at is None

    if fix == "api_key":
        await api_provider(db, org, "alpha", 2, [FAKE_KEY])
    elif fix == "agy_source":     # v0.1.62: Antigravity CLI đăng nhập + có model chạy được việc nền ⇒ đóng sự cố
        await cli_provider(db, org, "antigravity_cli", 2)
    else:
        await set_background_cli(db, org, ["claude_code_cli"])
    await health.evaluate(db, redis, org, now=datetime.now(UTC), started_at=None)
    await db.commit()
    h = await _health(db, org)
    assert h is not None and h.cleared_at is not None
    assert not await redis.exists(BG_NO_SOURCE_FLAG.format(org))
    assert not await redis.exists(BG_NO_SOURCE_TRY.format(org))


async def test_background_uses_agy_without_opt_in_but_claude_needs_it(  # type: ignore[no-untyped-def]
        app, db, redis, boxctl) -> None:
    """agy chạy việc nền không cần cho phép (sàng lọc qua hộp, agent nội bộ / Bản tin trong tiến trình); Claude Code
    CLI vẫn bị bỏ qua tới khi Owner cho phép (F-86)."""
    org = await org_id(db)
    await cli_provider(db, org, "claude_code_cli", 1)
    await cli_provider(db, org, "antigravity_cli", 2)
    claude, agy = FakeCli("claude"), FakeCli("agy")
    r = make_router(redis, claude, agy, box=agy)
    for key, purpose in (("core.refinery", "refinery"), ("agent:x", "duty_decide"), ("core.briefing", "gen.briefing")):
        out = await r.generate(org, agent_key=key, purpose=purpose, messages=MSGS)
        assert out.text == "agy-ok" and BACKGROUND_CLI_REASON in out.attempts, key
    assert claude.calls == 0 and agy.calls == 3
    # Giá trị lạ (agy) trong cấu hình bị bỏ qua (agy không cần công tắc); Owner cho phép Claude ⇒ Claude đứng đầu chuỗi.
    await set_background_cli(db, org, ["claude_code_cli", "antigravity_cli"])
    out = await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert out.text == "claude-ok" and claude.calls == 1 and BACKGROUND_CLI_REASON not in out.attempts


# ─── API: Nguồn AI cho việc nền ─────────────────────────────────────────────

async def _ai_settings(db, org: uuid.UUID) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    await db.rollback()
    return (await db.execute(text("SELECT settings->'ai' FROM core.organizations WHERE id = :o"),  # type: ignore[no-any-return]
                             {"o": org})).scalar_one() or {}


async def test_put_background_pin_and_risk(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(settings, '{ai}',
                                 '{"daily_budget_vnd": 50000}'::jsonb, true) WHERE id = :o"""), {"o": org})
    await db.commit()
    body = {"allow_cli": ["claude_code_cli"], "accept_risk": True}
    r = await api.send("PUT", "/providers/background", body)
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED", r.text
    assert "background_cli" not in await _ai_settings(db, org)

    await verify_pin(api)
    r = await api.send("PUT", "/providers/background", {"allow_cli": ["claude_code_cli"], "accept_risk": False})
    assert r.status_code == 422 and "accept_risk" in r.json()["errors"], r.text
    assert "background_cli" not in await _ai_settings(db, org)

    r = await api.send("PUT", "/providers/background", body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["allow_cli"] == ["claude_code_cli"] and isinstance(j["accepted_at"], str)
    assert j["risk_text"] == BACKGROUND_CLI_RISK
    ai = await _ai_settings(db, org)
    assert ai["background_cli"] == ["claude_code_cli"] and ai["daily_budget_vnd"] == 50000   # giữ khoá khác
    assert ai["background_cli_accepted_by"] and ai["background_cli_accepted_at"]
    log = (await db.execute(text("""SELECT detail FROM ops.action_log WHERE action = 'ai.background_cli_changed'
                                    ORDER BY at DESC LIMIT 1"""))).scalar_one()
    assert log == {"allow_cli": ["claude_code_cli"], "accept_risk": True}


async def test_remove_cli_needs_no_pin(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    await set_background_cli(db, org, ["claude_code_cli"])
    r = await api.send("PUT", "/providers/background", {"allow_cli": []})
    assert r.status_code == 200, r.text
    assert r.json()["allow_cli"] == [] and r.json()["accepted_at"] is None
    assert (await _ai_settings(db, org))["background_cli"] == []


async def test_put_background_agy_and_manager(owner_api, client, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await verify_pin(api)
    r = await api.send("PUT", "/providers/background", {"allow_cli": ["antigravity_cli"], "accept_risk": True})
    assert r.status_code == 422, r.text
    assert "luôn chạy được việc nền" in r.json()["errors"]["allow_cli"]
    manager = await login_as(client, db, "manager")
    try:
        r = await manager.send("PUT", "/providers/background", {"allow_cli": [], "accept_risk": False})
        assert r.status_code == 403, r.text
    finally:
        await manager.c.aclose()


async def test_get_background_sources(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    await cli_provider(db, org, "claude_code_cli", 1)
    await cli_provider(db, org, "antigravity_cli", 2)
    r = await api.get("/providers/background")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["allow_cli"] == [] and j["accepted_at"] is None and j["has_api_source"] is False
    assert j["risk_text"] == BACKGROUND_CLI_RISK
    assert j["purposes"] == ["Sàng lọc tin", "Trực việc (agent soạn nháp)", "Bản tin Gen"]
    by_kind = {s["kind"]: s for s in j["sources"]}
    assert by_kind["claude_code_cli"]["used"] is False
    assert by_kind["claude_code_cli"]["reason"] == BACKGROUND_CLI_REASON
    assert by_kind["antigravity_cli"]["used"] is True and by_kind["antigravity_cli"]["reason"] is None   # v0.1.62
    assert j["has_agy_source"] is True
    for s in j["sources"]:
        assert all(isinstance(s[k], str) for k in ("provider_id", "name", "kind"))
        assert isinstance(s["used"], bool) and (s["reason"] is None or isinstance(s["reason"], str))

    await api_provider(db, org, "alpha", 3, [FAKE_KEY])
    j = (await api.get("/providers/background")).json()
    assert j["has_api_source"] is True
    alpha = next(s for s in j["sources"] if s["name"] == "alpha")
    assert alpha["used"] is True and alpha["reason"] is None
    assert FAKE_KEY not in r.text and FAKE_KEY not in str(j)
