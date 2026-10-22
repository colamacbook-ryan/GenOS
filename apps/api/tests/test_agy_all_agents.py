# ruff: noqa: F811 — fixture `clis` nhập từ test_cli_models_v0131 (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.62 — Antigravity CLI (agy) MỞ cho MỌI vai (quyết định của Owner, 16/10/2026).

Luật v0.1.38 (F-22) "agy chỉ dùng cho Gen của Sếp" đã gỡ: Owner được báo rủi ro (agy có công cụ đọc tệp / chạy lệnh và
chạy cùng container với khoá hệ thống ⇒ nội dung bên ngoài có thể lừa agy đọc tệp trong container) và chọn "mở thẳng".
Khi Owner CHỈ có nguồn Antigravity CLI (không khoá API, không Claude Code CLI) thì mọi vai chạy được bằng agy:

  (a) hồ sơ tiêu chuẩn giải ra agy cho core.refinery, core.briefing, core.reply, core.gen, agent:<uuid>;
  (b) `ModelRouter.generate` với purpose việc nền (refinery / duty_decide / gen.briefing) chọn được agy;
  (c) lượt Gen của nhân viên và lượt đã đọc nội dung bên ngoài (tainted) dùng được agy;
  (d) GET /agents/bindings không còn lý do "cần khoá API" và không còn `blocked_reason`;
  (e) PUT gán model agy cho mọi khoá (core.* và agent:<id>) thành công;
  (f) Claude Code CLI VẪN không chạy việc nền khi Owner chưa cho phép (F-86 / QD-12 — điều khoản gói Claude);
  (g) sức khoẻ không kêu `ai.background_no_source` khi có agy chạy được; gợi ý "cần 1 khoá API" cũng biến mất.

Lớp cô lập mỗi lượt của AgyClient KHÔNG đổi (xem test_agy_isolation_v0138). Bước kế tiếp (chưa làm): tách agy ra
container riêng không gắn khoá.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from gh import health
from gh.db import admin_sessionmaker, sessionmaker
from gh.defaults import profiles, registry
from gh.errors import MODEL_UNAVAILABLE_HINT, model_unavailable
from gh.gen import store
from gh.providers import router as router_mod
from gh.providers.clients import Message
from gh.providers.router import BACKGROUND_CLI_REASON, ModelRouter, ModelUnavailable, Routed
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_background_cli_v0141 import FakeCli, cli_provider, make_router
from tests.test_cli_models_v0131 import (
    _login,
    _provider,
    clis,  # noqa: F401 — fixture dùng chung
)
from tests.test_p4_agents import _seed_agent
from tests.test_rbac_api import login_as
from tests.test_ux_v0128 import _provider as _key_provider

MSGS = [Message("user", "nội dung của khách")]
BG_CASES = [("core.refinery", "refinery.extract"), ("core.briefing", "gen.briefing"), ("agent:{id}", "duty_decide")]
# Gói G: core.reply (ngoại giao) KHÔNG nằm ở đây — ngoại lệ có chủ đích, xem test_agy_phan_lop.py.
ALL_CASES = [*BG_CASES, ("core.gen", "gen.turn"), ("agent:{id}", "setup_agent_try")]


def _calls(clis: dict[str, Any]) -> list[dict[str, Any]]:
    log = Path(clis["agy_home"]).parent.parent / "agy-calls.log"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


async def _agy_only(api: Api) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Đăng nhập agy (CLI giả), lưu model gemini-3.1-pro (mặc định) + gemini-3.8-flash, chỉ bật nguồn agy.
    → (org, provider, model gemini-3.1-pro)."""
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    for name, default in (("gemini-3.1-pro", True), ("gemini-3.8-flash", False)):
        r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": name, "make_default": default})
        assert r.status_code == 201, r.text
    pid = uuid.UUID(p["id"])
    async with sessionmaker()() as db:
        org = (await db.execute(text("SELECT org_id FROM agent.providers WHERE id = :i"), {"i": pid})).scalar_one()
        mid = (await db.execute(text("SELECT id FROM agent.models WHERE provider_id = :p AND model_name = :m"),
                                {"p": pid, "m": "gemini-3.1-pro"})).scalar_one()
        await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'antigravity_cli') WHERE org_id = :o"),
                         {"o": org})
        await db.commit()
    return org, pid, mid


# ─── (a) hồ sơ tiêu chuẩn giải ra agy cho mọi vai ────────────────────────

async def test_profiles_resolve_agy_for_every_role(owner_api, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    org, _pid, _mid = await _agy_only(owner_api)
    aid = await _seed_agent(db, org)
    keys = ["core.gen", "core.refinery", "core.briefing", f"agent:{aid}"]
    async with sessionmaker()() as s:
        std, why = await profiles.standard_with_reasons(s, org, [*keys, "core.reply"])
    assert std["core.reply"] is not None and why["core.reply"] is None             # hộp chạy ⇒ ngoại giao cũng có agy
    box.state = "not_installed"
    async with sessionmaker()() as s:
        std_off, why_off = await profiles.standard_with_reasons(s, org, ["core.reply", "core.refinery", "core.gen"])
    assert std_off["core.reply"] is None and std_off["core.refinery"] is None          # chưa cài hộp: lý do trung thực
    assert why_off["core.reply"] == why_off["core.refinery"] and "Hộp riêng Antigravity" in why_off["core.reply"]
    assert std_off["core.gen"] is not None                                              # vai core không phụ thuộc hộp
    box.state = "ok"
    for k in keys:
        c = std[k]
        assert c is not None and why[k] is None, (k, why[k])
        assert c["provider_kind"] == "antigravity_cli", k
    # v0.1.66 (F005): Sếp đã bấm "Dùng model này" cho gemini-3.1-pro ⇒ "Chuẩn: …" hiện đúng model chạy thật (pro).
    for k in keys:
        assert std[k]["model_name"] == "gemini-3.1-pro" and std[k]["source"] == "default", k
    # Bỏ ghim ⇒ hồ sơ chuẩn chọn theo vai.
    async with sessionmaker()() as s:
        await s.execute(text("UPDATE agent.models SET is_default = false WHERE provider_id = :p"), {"p": _pid})
        await s.commit()
    async with sessionmaker()() as s:
        std, _why = await profiles.standard_with_reasons(s, org, keys)
    # Tầng theo vai: sàng lọc / Bản tin = nhanh (agy chỉ có flash = cân bằng ⇒ tầng gần nhất, mức thấp); Gen = cân bằng.
    assert (std["core.gen"]["model_name"], std["core.gen"]["effort"]) == ("gemini-3.8-flash", "medium")
    for k in ("core.refinery", "core.briefing", f"agent:{aid}"):
        assert (std[k]["model_name"], std[k]["effort"]) == ("gemini-3.8-flash", "low"), k
    # Việc nền cũng được: khoá nền (BACKGROUND_ROLES) không còn bị loại agy.
    for k in ("core.refinery", "core.briefing", f"agent:{aid}"):
        assert profiles.role_background(k) is True


# ─── (b) router.generate việc nền chọn được agy ──────────────────────────

@pytest.mark.parametrize("agent_key,purpose", ALL_CASES)
async def test_router_uses_agy_for_every_role_and_purpose(owner_api, app, db, clis, agent_key, purpose, box) -> None:  # type: ignore[no-untyped-def]
    org, _pid, _mid = await _agy_only(owner_api)
    aid = await _seed_agent(db, org)
    n = len(_calls(clis))
    out = await app.state.model_router.generate(org, agent_key=agent_key.format(id=aid), purpose=purpose,
                                                messages=MSGS, json_mode=False)
    assert out.text == "whoami:an@example.vn" and len(_calls(clis)) == n + 1     # agy ĐƯỢC gọi (qua stdin)
    assert _calls(clis)[-1]["via_stdin"] is True


async def test_background_purposes_are_background_but_agy_runs(owner_api, app, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    """Các purpose này `is_background` (luật F-86 áp cho Claude Code CLI) mà agy vẫn chạy, không tự cho phép Claude."""
    org, _pid, _mid = await _agy_only(owner_api)
    aid = await _seed_agent(db, org)
    for key, purpose in BG_CASES:
        assert router_mod.is_background(purpose)
        out = await app.state.model_router.generate(org, agent_key=key.format(id=aid), purpose=purpose, messages=MSGS,
                                                    json_mode=False)
        assert out.text == "whoami:an@example.vn"
    assert await router_mod.background_cli_allowed(db, org) == set()


async def test_router_has_no_agy_gate_left() -> None:
    """Luật cũ đã gỡ sạch khỏi API công khai của bộ định tuyến (không còn `allow_agy`, hằng / hàm owner-only)."""
    import inspect

    assert "allow_agy" not in inspect.signature(ModelRouter.generate).parameters
    for name in ("AGY_OWNER_ONLY_REASON", "AGY_ONLY_TITLE", "AGY_ONLY_HINT", "AGY_ADD_SOURCE", "AGY_ALERT_TITLE",
                 "agy_only", "agy_only_alert_title"):
        assert not hasattr(router_mod, name), name
    assert not hasattr(profiles, "role_flags")
    assert "allow_agy" not in inspect.signature(profiles.resolve).parameters
    assert "allow_agy" not in inspect.signature(profiles.provider_allowed).parameters


async def test_chain_exhausted_with_agy_is_generic_not_agy_only(owner_api, app, db, clis) -> None:  # type: ignore[no-untyped-def]
    """agy lỗi ⇒ cảnh báo hết chuỗi chung (`model_chain_exhausted`), không còn cảnh báo riêng `model_chain_agy_only`."""
    org, _pid, _mid = await _agy_only(owner_api)
    r = ModelRouter(sessionmaker(), app.state.redis)
    await r._chain_exhausted(org, ["Antigravity CLI: lỗi"], purpose="refinery.extract")
    rows = (await db.execute(text("SELECT alert_type, priority FROM biz.alerts WHERE org_id = :o "
                                  "AND alert_type LIKE 'model_chain%'"), {"o": org})).all()
    assert [(a.alert_type, a.priority) for a in rows] == [("model_chain_exhausted", "P1")]


# ─── (c) lượt Gen: Owner, nhân viên, nội dung ngoài đều dùng được agy ─────

async def _turn(api: Api, q: str, conversation_id: str | None = None) -> dict[str, Any]:
    r = await api.send("POST", "/gen/turns", {"text": q, "conversation_id": conversation_id,
                                              "context": {"route": "/overview", "screen_key": "overview"}})
    assert r.status_code == 202, r.text
    tid = r.json()["turn_id"]
    for _ in range(300):
        t: dict[str, Any] = (await api.get(f"/gen/turns/{tid}")).json()
        if t["status"] != "running":
            return t
        await asyncio.sleep(0.05)
    raise AssertionError("lượt Gen không kết thúc")


def _says(t: dict[str, Any]) -> list[str]:
    return [s["step"]["text"] for s in t["steps"] if s["step"]["kind"] == "say"]


async def test_gen_turn_owner_uses_agy_staff_does_not(owner_api, client, db, clis) -> None:  # type: ignore[no-untyped-def]
    org, _pid, _mid = await _agy_only(owner_api)
    n = len(_calls(clis))
    t = await _turn(owner_api, "Sáng nay có gì?")
    assert len(_calls(clis)) > n and _calls(clis)[-1]["via_stdin"] is True           # Owner: agy được gọi (qua stdin)
    assert not any("chỉ dùng cho Gen" in s for s in _says(t)), t

    cfg = await store.get_settings(db, org)
    await store.save_settings(db, org, {**cfg, "enabled": True, "roles": ["owner", "manager"]})
    await db.commit()
    manager = await login_as(client, db, "manager")
    try:
        n = len(_calls(clis))
        t = await _turn(manager, "Hôm nay có gì?")
        assert len(_calls(clis)) == n          # gói G: nhân viên KHÔNG dùng agy (Gen của nhân viên dùng khoá API)
        assert not any("chỉ dùng cho Gen" in s or "luật an toàn" in s for s in _says(t)), t
        async with admin_sessionmaker()() as adb:
            failed = (await adb.execute(text("""SELECT detail FROM ops.action_log WHERE action = 'gen.answer'
                                                 AND result = 'failed' AND detail ? 'reasons'"""))).scalars().all()
        assert not any("Antigravity CLI chỉ dùng cho Gen" in " ".join(d["reasons"]) for d in failed)
    finally:
        await manager.c.aclose()


class _ScriptedRouter(ModelRouter):
    """ModelRouter thật về kiểu (cùng chữ ký `generate`), giả về hành vi: trả lần lượt các envelope kịch bản."""

    def __init__(self, replies: list[dict[str, Any]]):  # không gọi super(): không cần CSDL/Redis
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Message],
                       json_mode: bool = True, temperature: float = 0.2, tier: str | None = None,
                       effort: str | None = None) -> Routed:
        self.prompts.append("\n".join(m.content for m in messages))
        r = self.replies.pop(0) if self.replies else {"steps": [{"kind": "done"}]}
        return Routed(json.dumps(r), "Antigravity CLI", "gemini-3.1-pro", 1, 1)


async def _turn_with(api: Api, app: Any, router: Any, q: str, conversation_id: str | None = None) -> dict[str, Any]:
    app.state.model_router = router
    return await _turn(api, q, conversation_id)


async def test_gen_tainted_turn_and_conversation_still_reach_agy(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    """Kết quả công cụ (nội dung khách) vẫn được bọc `wrap_untrusted` rồi GỬI cho agy ở vòng sau — không còn bị chặn;
    hỏi tiếp trong hội thoại đã có nội dung ngoài cũng gọi được agy ngay từ vòng đầu."""
    router = _ScriptedRouter([{"steps": [{"kind": "tool", "name": "queue.list", "args": {"tab": "all"}}]},
                              {"steps": [{"kind": "say", "text": "Dạ, hộp thư đây."}, {"kind": "done"}]}])
    t = await _turn_with(owner_api, app, router, "Hộp thư có gì?")
    assert t["status"] == "done" and len(router.prompts) == 2          # vòng 2 (có kết quả công cụ) VẪN tới agy
    assert "[kết quả queue.list]" in router.prompts[1]
    assert any("Dạ, hộp thư đây." in s for s in _says(t)), t
    assert not any("luật an toàn" in s for s in _says(t)), t

    router2 = _ScriptedRouter([{"steps": [{"kind": "say", "text": "Dạ."}, {"kind": "done"}]}])
    t2 = await _turn_with(owner_api, app, router2, "Xin chào", conversation_id=t["conversation_id"])
    assert t2["status"] == "done" and len(router2.prompts) == 1
    assert any(s == "Dạ." for s in _says(t2)), t2


async def test_gen_internal_tool_keeps_agy(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    router = _ScriptedRouter([{"steps": [{"kind": "tool", "name": "screens.list", "args": {}}]},
                              {"steps": [{"kind": "say", "text": "Dạ."}, {"kind": "done"}]}])
    t = await _turn_with(owner_api, app, router, "Có những màn nào?")
    assert t["status"] == "done" and len(router.prompts) == 2
    assert "[kết quả screens.list]" in router.prompts[1]


async def test_no_model_message_no_longer_blames_agy_rule(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    class _Down(_ScriptedRouter):
        async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Message],
                           json_mode: bool = True, temperature: float = 0.2, tier: str | None = None,
                           effort: str | None = None) -> Routed:
            raise ModelUnavailable(["Antigravity CLI: lỗi tạm"], no_chain=False)

    t = await _turn_with(owner_api, app, _Down([]), "Xin chào")
    says = " ".join(_says(t))
    assert "chưa gọi được model" in says and "chỉ dùng cho Gen" not in says and "luật an toàn" not in says


# ─── (d)(e) gán model cho agent ──────────────────────────────────────────

async def test_bindings_have_no_api_key_reason_and_agy_assignable_to_every_key(owner_api, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    org, _pid, mid = await _agy_only(api)
    aid = await _seed_agent(db, org)
    items = {i["agent_key"]: i for i in (await api.get("/agents/bindings")).json()["items"]}
    # v0.1.72: + core.owner_reply, core.support (Sếp gọi bot ở nhóm, Chế độ hỗ trợ).
    assert set(items) == {"core.gen", "core.refinery", "core.briefing", "core.reply",
                          "core.owner_reply", "core.support", f"agent:{aid}"}
    for k, it in items.items():                                              # (d)
        assert it["source"] == "standard" and it["standard_reason"] is None, k
        # v0.1.66 (F005): fixture đã chốt gemini-3.1-pro ("Dùng model này") ⇒ "Chuẩn" nói đúng model chạy thật.
        assert it["standard"]["model_name"] == "gemini-3.1-pro", k         # "Chuẩn: <model agy>" cho mọi dòng
        assert "blocked_reason" not in (it["binding"] or {}), k
    assert "cần khoá API" not in json.dumps(items, ensure_ascii=False)
    assert "chỉ dùng cho Gen" not in json.dumps(items, ensure_ascii=False)

    for key in ("core.refinery", "core.briefing", "core.gen", "core.owner_reply", "core.support",
                f"agent:{aid}"):    # (e) v0.1.72: + hai khoá chạy trong hộp
        r = await api.send("PUT", f"/agents/bindings/{key}", {"model_id": str(mid)})
        assert r.status_code == 200, (key, r.text)
        b = r.json()["binding"]
        assert b["model_id"] == str(mid) and b["model_name"] == "gemini-3.1-pro" and "blocked_reason" not in b
    listing = (await api.get("/agents/bindings")).json()
    models = {m["model_name"] for m in listing["models"]}
    assert {"gemini-3.1-pro", "gemini-3.8-flash"} <= models      # ô chọn model liệt kê model agy cho mọi dòng
    got = {i["agent_key"]: i for i in listing["items"]}
    assert all(got[k]["source"] == "custom" and "blocked_reason" not in got[k]["binding"]
               for k in got if k != "core.reply")
    detail = (await api.get(f"/agents/{aid}")).json()
    assert detail["binding"]["model_id"] == str(mid)

    # Dòng gán agy cho khoá việc nền chạy thật bằng agy (không có lý do bị từ chối).
    r = await api.send("PUT", "/agents/bindings/core.refinery", {"model_id": str(mid)})
    assert r.status_code == 200
    out = await api.send("PUT", "/agents/bindings/agent:" + str(uuid.uuid4()), {"model_id": str(mid)})
    assert out.status_code == 404                  # agent không tồn tại: lỗi thường, không phải 409


async def test_binding_to_agy_is_used_by_background_call(owner_api, app, clis, box) -> None:  # type: ignore[no-untyped-def]
    org, _pid, mid = await _agy_only(owner_api)
    async with sessionmaker()() as db:
        await db.execute(text("""INSERT INTO agent.bindings (org_id, agent_key, model_id, context_tokens)
                                 VALUES (:o, 'core.refinery', :m, 4000)"""), {"o": org, "m": mid})
        await db.commit()
    n = len(_calls(clis))
    out = await app.state.model_router.generate(org, agent_key="core.refinery", purpose="refinery.extract",
                                                messages=MSGS, json_mode=False)
    assert out.model == "gemini-3.1-pro" and len(_calls(clis)) == n + 1


# ─── Hướng dẫn bước 4 / 8 ────────────────────────────────────────────────

async def _bound(org: uuid.UUID) -> dict[str, str]:
    async with sessionmaker()() as db:
        return dict((await db.execute(text("""SELECT b.agent_key, m.model_name FROM agent.bindings b
                                               JOIN agent.models m ON m.id = b.model_id WHERE b.org_id = :o"""),
                                      {"o": org})).all())


async def test_setup_step4_with_agy_only_completes_without_bindings(owner_api, db, clis) -> None:  # type: ignore[no-untyped-def]
    """v0.1.55 (G2): bước 4 KHÔNG ghi `agent.bindings` — hồ sơ tiêu chuẩn tự chọn model theo vai; v0.1.62: agy là ứng
    viên của MỌI vai nên chỉ có agy là đủ."""
    api = owner_api
    org, pid, _mid = await _agy_only(api)
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [str(pid)]})
    assert r.status_code == 200, r.text
    assert await _bound(org) == {}

    key = await _key_provider(api, db, "Khoá API", ok=True, tested=["qwen2.5-7b"])
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [str(pid), key]})
    assert r.status_code == 200, r.text
    assert await _bound(org) == {}


async def test_step8_try_with_agy_only_answers(owner_api, app, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    """Trò chuyện thử agent (purpose setup_agent_try) chạy bằng agy — trước đây trả câu giải thích luật owner-only."""
    org, _pid, _mid = await _agy_only(owner_api)
    await db.execute(text("UPDATE ops.setup_state SET completed = CAST(:c AS jsonb) WHERE org_id = :o"),
                     {"c": '{"steps": {"1": "done", "2": "done", "3": "done", "4": "done", "5": "done", '
                           '"6": "done", "7": "done"}}', "o": org})
    await db.commit()
    n = len(_calls(clis))
    r = await owner_api.send("PUT", "/setup/steps/8", {"name": "Trợ lý Mai", "role_desc": "Chăm sóc khách hàng",
                                                        "try_message": "Chào bạn"})
    assert r.status_code == 200, r.text
    agent = r.json()["agent"]
    assert agent["try_error"] is None and agent["try_reasons"] == [] and agent["try_reply"]
    assert len(_calls(clis)) > n


# ─── tên model agy sai regex (lớp cô lập AgyClient giữ nguyên) ─────────────

async def test_add_agy_model_rejects_flag_like_name(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    n = len(_calls(clis))
    for bad in ("--x", "a b", "x" * 81):
        r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": bad})
        assert r.status_code == 422, r.text
        assert r.json()["errors"]["model_name"] == "Tên model chỉ gồm chữ, số và . _ : - (tối đa 80 ký tự)"
    assert len(_calls(clis)) == n                                # không gọi thử CLI


# ─── lỗi 503 MODEL_UNAVAILABLE: chỉ còn câu chung ─────────────────────────

def test_model_unavailable_is_always_the_generic_message() -> None:
    e = model_unavailable("Chưa có model nào chạy được để dịch", ["Antigravity CLI: lỗi tạm", "alpha: 429"])
    assert e.title == "Chưa có model nào chạy được để dịch" and e.detail == MODEL_UNAVAILABLE_HINT
    assert "Antigravity CLI chỉ dùng cho Gen" not in e.detail


# ─── (f) Claude Code CLI vẫn KHÔNG chạy việc nền khi Owner chưa cho phép ───

async def test_claude_cli_still_not_background_without_owner_consent(app, db, redis, boxctl) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await cli_provider(db, org, "claude_code_cli", 1)
    claude, agy = FakeCli("claude"), FakeCli("agy")
    r = make_router(redis, claude)
    for key, purpose in (("core.refinery", "refinery"), ("agent:x", "duty_decide"), ("core.briefing", "gen.briefing")):
        with pytest.raises(ModelUnavailable) as ei:             # chuỗi chỉ có Claude CLI chưa được phép ⇒ hết chuỗi
            await r.generate(org, agent_key=key, purpose=purpose, messages=MSGS)
        assert BACKGROUND_CLI_REASON in ei.value.reasons
    assert claude.calls == 0
    # Thêm agy: việc nền chạy bằng agy, Claude vẫn đứng ngoài.
    await cli_provider(db, org, "antigravity_cli", 2)
    r = make_router(redis, claude, agy, box=agy)
    out = await r.generate(org, agent_key="core.refinery", purpose="refinery", messages=MSGS)
    assert out.text == "agy-ok" and claude.calls == 0 and agy.calls == 1
    # Lượt Gen (không phải việc nền) vẫn dùng Claude trước như cũ.
    out = await r.generate(org, agent_key="core.gen", purpose="gen.turn", messages=MSGS, json_mode=False)
    assert out.text == "claude-ok" and claude.calls == 1


# ─── (g) sức khoẻ: không kêu `ai.background_no_source` khi có agy ─────────

async def _incident(db, org: uuid.UUID) -> Any:  # type: ignore[no-untyped-def]
    await db.rollback()
    return (await db.execute(text("""SELECT cleared_at FROM ops.health_alerts WHERE org_id = :o
                                      AND key = 'ai.background_no_source'"""), {"o": org})).one_or_none()


async def test_health_does_not_raise_background_no_source_when_agy_runs(app, db, redis, boxctl) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await cli_provider(db, org, "antigravity_cli", 1)
    fake = FakeCli("agy")
    r = make_router(redis, FakeCli("claude"), fake, box=fake)
    for key, purpose in (("core.refinery", "refinery"), ("agent:x", "duty_decide"), ("core.briefing", "gen.briefing")):
        out = await r.generate(org, agent_key=key, purpose=purpose, messages=MSGS)
        assert out.text == "agy-ok"
    assert await _incident(db, org) is None                         # việc nền chạy được ⇒ không có sự cố
    await health.evaluate(db, redis, org, now=datetime.now(UTC), started_at=None)
    await db.commit()
    assert await _incident(db, org) is None
    assert await router_mod.has_agy_source(db, org) is True
    # Gợi ý "Cần 1 khoá API để chạy lọc tin và bản tin" cũng biến mất khi có agy chạy được.
    assert "background_key_missing" not in {s["key"] for s in await registry.suggestions(db, org)}


async def test_health_suggestion_appears_without_any_background_source(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await cli_provider(db, org, "claude_code_cli", 1)               # chỉ Claude CLI chưa được phép ⇒ không có nguồn nền
    assert await router_mod.has_agy_source(db, org) is False
    keys = {s["key"] for s in await registry.suggestions(db, org)}
    assert "background_key_missing" in keys
    s = next(x for x in await registry.suggestions(db, org) if x["key"] == "background_key_missing")
    assert "Antigravity CLI" in s["body"] and "Chỉ dùng khoá API" not in s["body"]
