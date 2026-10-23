"""v0.1.75 (review bảo mật 22/10/2026, Nợ #45) — Gen của Sếp có chữ người ngoài trong prompt ⇒ agy qua hộp riêng.

Từ v0.1.74 Gen của Sếp đọc tin nhóm (`group.recent`), Hộp thư… và trả lời ở kênh quản trị Zalo / WhatsApp. Trước đây
`core.gen` của Owner luôn chạy agy TRONG tiến trình api (đọc được /run/secrets/gh_master_key, mật khẩu DB) ⇒ prompt
injection trong tin nhóm là đường tới bí mật. Nay:
- `placement(..., external=True)` ⇒ hộp, bất kể vai / người hỏi; `decide` / `verdict_for` / `ModelRouter.generate`
  mang cờ;
- bộ máy Gen bật cờ khi: lượt kênh quản trị, lịch sử có kết quả công cụ ngoài, hoặc (từ vòng kế) model vừa gọi công cụ
  ngoài `AGY_SAFE_TOOLS`; lõi lệnh `hoi` (bản tin / việc / sự cố vào prompt) luôn bật;
- hộp tắt ⇒ KHÔNG chạy agy trong tiến trình: nguồn khoá API đứng ra, không còn nguồn ⇒ câu `BOX_NEEDED` thân thiện.
Hộp và agy đều GIẢ (`tests/box_fakes.py`) — không chạy agy thật."""

# ruff: noqa: F811 — fixture `boxctl` dùng chung từ conftest
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.db import sessionmaker
from gh.gen import engine
from gh.owner_cmd import core
from gh.owner_cmd.core import Reply
from gh.providers import agy_policy, placement
from gh.providers.clients import Completion, Message
from gh.providers.router import ModelRouter, ModelUnavailable, Routed
from tests.conftest import Api
from tests.test_agy_placement_v0163 import _fixture_world
from tests.test_gen import ask
from tests.test_owner_cmd_v0171 import ctx_for, ocmd
from tests.test_telegram_commands_v0164 import Router as OwnerRouter

MSGS = [Message("user", "nhóm nào có gì mới?")]
GEN = "core.gen"


# ─── A. Luật chỗ chạy thuần ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role,owner", [(GEN, True), (GEN, False), ("agent:x", True), ("core.unknown", True)])
def test_external_always_means_box(role: str, owner: bool) -> None:
    assert placement.placement(role, owner=owner, external=True) == placement.BOX
    assert placement.box_required(role, owner=owner, external=True) is True


def test_owner_gen_without_outside_text_stays_inproc() -> None:
    assert placement.placement(GEN, owner=True) == placement.INPROC
    assert placement.placement(GEN, owner=True, external=False, purpose="gen.turn") == placement.INPROC


def test_decide_external_follows_the_box_state() -> None:
    assert agy_policy.decide(GEN, external=True, box="ok") == agy_policy.ALLOWED_BOX
    assert agy_policy.decide(GEN, external=True, box="down") == agy_policy.DENIED_BOX_DOWN
    assert agy_policy.decide(GEN, external=True, box="not_installed") == agy_policy.DENIED_BOX_NOT_INSTALLED
    assert agy_policy.decide(GEN, external=False, box="down") == agy_policy.ALLOWED     # cũ: Owner, không chữ ngoài
    assert engine.box_unavailable([agy_policy.REASON_BOX_DOWN]) and engine.box_unavailable(
        ["x", agy_policy.REASON_BOX_NOT_INSTALLED])
    assert not engine.box_unavailable(["alpha: 429"])


def test_tier_kw_carries_external_and_fails_closed() -> None:
    class R:
        async def generate(self, org: Any, **kw: Any) -> Any:  # pragma: no cover — chỉ để đọc chữ ký
            return None

    class Old:  # bộ định tuyến giả cũ không nhận external
        async def generate(self, org: Any, *, agent_key: str, purpose: str, messages: list[Any]) -> Any:
            return None  # pragma: no cover

    assert engine._tier_kw(R(), None, None, external=True) == {"external": True}
    assert engine._tier_kw(R(), None, None, external=False) == {}
    assert engine._tier_kw(Old(), None, None, external=True) == {}


# ─── B. ModelRouter.generate thật với hộp giả ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", ["ok", "down", "not_installed"])
async def test_router_owner_gen_external_never_runs_agy_inproc(app, db, redis, boxctl, state) -> None:  # type: ignore[no-untyped-def]
    boxctl.state = state
    w = await _fixture_world(db, redis, boxctl)
    out = await w.router.generate(w.org, agent_key=GEN, purpose="gen.turn", messages=MSGS, json_mode=False,
                                  external=True)
    assert w.inproc == []                                                   # không bao giờ trong tiến trình api
    if state == "ok":
        assert out.text == "trả lời từ hộp" and len(boxctl.calls) == 1
    else:
        assert out.provider == "alpha" and boxctl.calls == []              # nguồn khoá API giữ nguyên
    # Cùng thế giới, lượt KHÔNG có chữ ngoài: Owner vẫn trong tiến trình như cũ.
    out2 = await w.router.generate(w.org, agent_key=GEN, purpose="gen.turn", messages=MSGS, json_mode=False)
    assert out2.text == "trả lời trong tiến trình" and w.inproc == ["gemini-3.1-pro"]


async def test_router_external_only_agy_and_box_down_raises_with_box_reason(app, db, redis, boxctl) -> None:  # type: ignore[no-untyped-def]
    boxctl.state = "down"
    w = await _fixture_world(db, redis, boxctl)
    await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'antigravity_cli') WHERE org_id = :o"),
                     {"o": w.org})
    await db.commit()
    with pytest.raises(ModelUnavailable) as ei:
        await w.router.generate(w.org, agent_key=GEN, purpose="gen.zalo", messages=MSGS, external=True)
    assert agy_policy.REASON_BOX_DOWN in ei.value.reasons and engine.box_unavailable(ei.value.reasons)
    assert w.inproc == [] and boxctl.calls == []


# ─── C. Bộ máy Gen bật cờ đúng lúc ───────────────────────────────────────────────────────────────────────────────

class KwRouter:
    """Bộ định tuyến giả nhận `external` — ghi cờ của từng vòng."""

    def __init__(self, replies: list[Any]):
        self.replies = list(replies)
        self.external: list[bool] = []

    async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Any],
                       json_mode: bool = True, temperature: float = 0.2, owner: bool = True,
                       external: bool = False) -> Routed:
        assert agent_key == GEN
        self.external.append(external)
        r = self.replies.pop(0) if self.replies else {"steps": [{"kind": "done"}]}
        return Routed(orjson.dumps(r).decode(), "Giả lập", "fake-1", 10, 10)


def _tool(name: str, **args: Any) -> dict[str, Any]:
    return {"steps": [{"kind": "tool", "name": name, "args": args}]}


SAY = {"steps": [{"kind": "say", "text": "Dạ."}, {"kind": "done"}]}


async def test_engine_turns_external_after_an_outside_tool(owner_api: Api, app: Any) -> None:
    r = KwRouter([_tool("group.recent", hours="24"), SAY])
    t = await ask(owner_api, app, r, "Nhóm nào có gì mới?")
    assert t["status"] == "done" and r.external == [False, True]
    # Lượt sau trong cùng hội thoại: lịch sử có kết quả công cụ ngoài ⇒ ngay vòng đầu đã qua hộp.
    r2 = KwRouter([SAY])
    await ask(owner_api, app, r2, "Tóm lại giúp em", conversation_id=t["conversation_id"])
    assert r2.external == [True]


async def test_engine_safe_tool_keeps_owner_inproc(owner_api: Api, app: Any) -> None:
    r = KwRouter([_tool("system.health"), SAY])
    t = await ask(owner_api, app, r, "Hệ thống ổn không?")
    assert t["status"] == "done" and r.external == [False, False]


class ScriptedInproc:
    """agy GIẢ trong tiến trình: trả lần lượt các envelope."""

    kind = "antigravity_cli"
    timeout = 300.0

    def __init__(self, replies: list[Any]):
        self.replies, self.calls = list(replies), 0

    async def generate(self, model: str, messages: list[Message], **kw: Any) -> Completion:
        self.calls += 1
        return Completion(orjson.dumps(self.replies.pop(0)).decode(), 1, 1, {})


async def test_engine_box_down_refuses_instead_of_running_inproc(owner_api: Api, app: Any, db, redis, boxctl) -> None:  # type: ignore[no-untyped-def]
    """Chỉ có Antigravity, hộp tắt: vòng 1 (chưa có chữ ngoài) chạy trong tiến trình; model gọi `group.recent` ⇒ vòng 2
    cần hộp ⇒ Gen dừng lượt với câu thân thiện, agy trong tiến trình KHÔNG nhận kết quả tool."""
    boxctl.state = "down"
    w = await _fixture_world(db, redis, boxctl)
    await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'antigravity_cli') WHERE org_id = :o"),
                     {"o": w.org})
    await db.commit()
    agy = ScriptedInproc([_tool("group.recent", hours="24"), SAY])
    router = ModelRouter(sessionmaker(), redis, cli_factory=lambda: agy, box_factory=boxctl.factory)
    t = await ask(owner_api, app, router, "Nhóm nào có gì mới?")  # type: ignore[arg-type]
    assert agy.calls == 1 and boxctl.calls == []
    says = [s["step"]["text"] for s in t["steps"] if s["step"]["kind"] == "say"]
    assert says and says[-1].startswith("Câu này cần đọc tin nhắn của người ngoài")
    assert "hộp riêng" in says[-1]


# ─── D. Lõi lệnh kênh quản trị ─────────────────────────────────────────────────────────────────────────────────

class OwnerKwRouter(OwnerRouter):
    def __init__(self, *replies: Any):
        super().__init__(*replies)
        self.kw: list[dict[str, Any]] = []

    async def generate(self, org_id: Any, **kw: Any) -> Routed:  # type: ignore[override]
        self.kw.append({k: v for k, v in kw.items() if k != "messages"})
        return await super().generate(org_id, **kw)


async def test_owner_cmd_free_question_is_external(owner_api: Api, redis: Any) -> None:
    router = OwnerKwRouter("Dạ, chưa có gì mới.")
    ctx = await ctx_for(owner_api, redis, router)
    await core.handle(ocmd("hoi", "hôm nay có gì?", via="zalo"), ctx)
    assert router.kw and router.kw[0]["external"] is True


async def test_owner_cmd_box_down_reply_is_friendly(owner_api: Api, redis: Any) -> None:
    router = OwnerKwRouter(ModelUnavailable([agy_policy.REASON_BOX_DOWN], no_chain=False))
    ctx = await ctx_for(owner_api, redis, router)
    reply = await core.handle(ocmd("hoi", "hôm nay có gì?", via="whatsapp"), ctx)
    assert reply == Reply(engine.BOX_NEEDED.format(addr=core._addr(await _owner(ctx))))


async def _owner(ctx: core.Context) -> Any:
    async with ctx.sm() as db:
        return await core.owner_user(db, ctx.org_id, ctx.owner_user_id)
