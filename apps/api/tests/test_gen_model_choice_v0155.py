"""v0.1.55 (G3) — Gen: chọn model / mức suy nghĩ trong khung chat + định tuyến theo ý định (J3).

- `TurnIn.model_choice` {tier: auto|fast|balanced|deep, effort?: low|medium|high}: giá trị lạ ⇒ 422 MODEL_CHOICE_INVALID
  (chuỗi tiếng Việt thân thiện, problem+json). GET /gen/settings trả thêm `model_options`.
- `engine.resolve_model_choice` (THUẦN): tầng chưa có nguồn AI nào phục vụ ⇒ hạ về Tự động + câu "Em dùng chế độ Tự
  động vì …" (bước `notice`, chuỗi); mức suy nghĩ chỉ giữ khi tầng hỗ trợ; 'deep' ↔ 'strong'. v0.1.62: tầng không còn
  phụ thuộc người hỏi là Owner hay nhân viên, cũng không phụ thuộc hội thoại đã đọc nội dung ngoài (agy mở cho mọi vai).
- J3: `out_of_scope` (Jev giả hoặc quy tắc tất định) ⇒ câu mẫu, KHÔNG gọi model (0 dòng agent.model_calls mới);
  `data` đơn giản + Tự động ⇒ tầng Nhanh; Jev lỗi / chậm / độ tin thấp ⇒ đường cũ. Action Log `gen.decide` có
  route/source.
Chỉ dùng bộ định tuyến / Jev / tùy chọn model GIẢ — không gọi mạng ngoài.
"""

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker, sessionmaker
from gh.gen import decider, engine, jev
from gh.gen.decider import Decision
from gh.providers.clients import Message
from gh.providers.router import ModelRouter, Routed
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_background_cli_v0141 import FAKE_KEY
from tests.test_briefing_v0141 import Boom
from tests.test_gen import FakeRouter, ask, gen_log
from tests.test_gen_memory_v0150 import _enable_gen_for_manager
from tests.test_model_router import provider as api_provider
from tests.test_rbac_api import login_as

TURN_DONE = {"steps": [{"kind": "say", "text": "Dạ, em trả lời đây."}]}
TIERS = ("auto", "fast", "balanced", "deep")


def options_for(*, deep_efforts: tuple[str, ...] = ("low", "medium", "high"), balanced: bool = True,
                deep: bool = True) -> dict[str, Any]:
    """Giả `gh.defaults.profiles.choice_options` (hợp đồng G1). v0.1.62: tầng 'deep' chạy được bằng agy cho MỌI
    người hỏi (Owner lẫn nhân viên, hội thoại đã đọc nội dung ngoài hay chưa) ⇒ `available` như nhau."""
    return {"tiers": [
        {"tier": "auto", "available": True, "efforts": []},
        {"tier": "fast", "available": True, "efforts": []},
        {"tier": "balanced", "available": balanced, "efforts": []},
        {"tier": "deep", "available": deep, "efforts": list(deep_efforts) if deep else []},
    ]}


class Seen(list):  # type: ignore[type-arg]
    """Danh sách tổ chức đã tra (mỗi lần gọi một phần tử) + `owners`: người hỏi có phải Owner (gói G) mỗi lần."""

    def __init__(self) -> None:
        super().__init__()
        self.owners: list[bool] = []


@pytest.fixture
def options(monkeypatch: pytest.MonkeyPatch) -> Seen:
    """Thay `engine.model_options` bằng bản giả; trả danh sách tổ chức đã tra (mỗi lần gọi một phần tử)."""
    seen = Seen()

    async def fake(db: Any, org: Any, *, owner: bool = True) -> dict[str, Any]:
        seen.append(org)
        seen.owners.append(owner)
        return options_for()

    monkeypatch.setattr(engine, "model_options", fake)
    return seen


class RecRouter(ModelRouter):
    """ModelRouter thật về kiểu (engine truyền tier / effort), giả về hành vi: ghi lại lời gọi."""

    def __init__(self, replies: list[Any] | None = None):  # không gọi super(): không cần CSDL/Redis
        self.replies = list(replies or [])
        self.calls: list[dict[str, Any]] = []

    async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Message],
                       json_mode: bool = True, temperature: float = 0.2,
                       tier: str | None = None, effort: str | None = None) -> Routed:
        self.calls.append({"tier": tier, "effort": effort, "prompt": "\n".join(m.content for m in messages)})
        r = self.replies.pop(0) if self.replies else TURN_DONE
        if isinstance(r, Exception):
            raise r
        return Routed(json.dumps(r), "Giả lập", "fake-1", 1, 1)


class FakeDecider:
    """Bộ quyết định giả kiểu Jev (name='jev'): trả sẵn một Decision (hoặc None = Jev lỗi / chậm / độ tin thấp)."""

    name = "jev"

    def __init__(self, value: str | None, confidence: float = 0.9):
        self.value, self.confidence = value, confidence
        self.last_error: str | None = None if value else "độ tin cậy thấp 0.30"
        self.questions: list[str] = []

    async def intent(self, question: str) -> Decision | None:
        self.questions.append(question)
        return Decision(self.value, self.confidence, 12, "jev") if self.value else None

    async def next_target(self, question: str, candidates: list[Any]) -> Decision | None:
        return None

    async def classify(self, question: str, options: dict[str, str], context: str) -> Decision | None:
        return None


class SlowJevClient:
    """JevClient giả chậm hơn trần thời gian của JevDecider (>1,5 s)."""

    def __init__(self, delay: float, label: str, confidence: float | None = 0.9):
        self.delay, self.label, self.confidence = delay, label, confidence

    async def choose(self, question: str, labels: list[str], context: str = "") -> jev.Choice:
        await asyncio.sleep(self.delay)
        return jev.Choice(self.label, self.confidence, int(self.delay * 1000))


async def turn(api: Api, app: Any, router: Any, q: str, *, choice: Any = None, cid: str | None = None,
               decider_: Any = None) -> dict[str, Any]:
    """POST /gen/turns (kèm model_choice nếu có) rồi chờ lượt xong."""
    app.state.model_router = router
    app.state.gen_decider = decider_
    body: dict[str, Any] = {"text": q, "conversation_id": cid,
                            "context": {"route": "/overview", "screen_key": "overview"}}
    if choice is not None:
        body["model_choice"] = choice
    r = await api.send("POST", "/gen/turns", body)
    assert r.status_code == 202, r.text
    tid = r.json()["turn_id"]
    for _ in range(300):
        t: dict[str, Any] = (await api.get(f"/gen/turns/{tid}")).json()
        if t["status"] != "running":
            for _ in range(100):                     # khoá 1-lượt-mỗi-người nhả ngay sau khi lượt xong
                if not await app.state.redis.keys("gen:lock:*"):
                    break
                await asyncio.sleep(0.02)
            return t
        await asyncio.sleep(0.02)
    raise AssertionError("lượt Gen không kết thúc")


def steps_of(t: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [s["step"] for s in t["steps"] if s["step"]["kind"] == kind]


async def decide_rows() -> list[Any]:
    return [r for r in await gen_log() if r.action == "gen.decide"]


async def model_calls() -> int:
    async with admin_sessionmaker()() as db:
        return int((await db.execute(text("SELECT count(*) FROM agent.model_calls"))).scalar_one())


# ─── 1. hợp đồng API: 422 + GET /gen/settings ──────────────────────────────────

@pytest.mark.parametrize("choice", [
    {"tier": "turbo"},
    {"tier": "strong"},                      # 'strong' là tên của bộ định tuyến — chat chỉ nhận 'deep'
    {"tier": "deep", "effort": "extreme"},
    {"tier": "fast", "effort": ""},
    {"tier": 5},
    "deep",
    ["deep"],
])
async def test_invalid_model_choice_is_friendly_422(owner_api: Api, app: Any, choice: Any) -> None:
    app.state.model_router = RecRouter()
    r = await owner_api.send("POST", "/gen/turns", {"text": "Xin chào", "model_choice": choice})
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "MODEL_CHOICE_INVALID"
    assert body["title"] == "Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé"
    assert isinstance(body["detail"], str) and "tier" in body["detail"]      # "Chi tiết kỹ thuật" luôn là chuỗi
    assert "extreme" not in r.text and "turbo" not in r.text                  # không lặp lại giá trị người gửi
    # Bị từ chối TRƯỚC khi giữ khoá lượt / tạo hội thoại ⇒ hỏi lại ngay được.
    assert (await owner_api.get("/gen/conversations")).json() == []
    ok = await owner_api.send("POST", "/gen/turns", {"text": "Xin chào", "model_choice": {"tier": "auto"}})
    assert ok.status_code == 202, ok.text


@pytest.mark.parametrize("choice", [None, {}, {"tier": "auto"}, {"tier": "fast"}, {"tier": "balanced"},
                                    {"tier": "deep", "effort": "high"}, {"tier": "deep", "effort": None}])
async def test_valid_model_choice_accepted(owner_api: Api, app: Any, options: Any, choice: Any) -> None:
    t = await turn(owner_api, app, RecRouter(), "Xin chào", choice=choice)
    assert t["status"] == "done"


async def test_settings_returns_model_options(owner_api: Api, client: httpx.AsyncClient, db: Any,
                                              options: Any) -> None:
    r = await owner_api.get("/gen/settings")
    assert r.status_code == 200
    out = r.json()
    assert out["model_options"] == options_for()
    assert len(options) == 1
    assert out["available"] is True and "decider" in out                       # trường cũ giữ nguyên
    # Nhân viên: cùng đường, cùng hình dạng; gói G: máy chủ hỏi theo người hỏi (owner=False ⇒ không tính nguồn agy).
    await _enable_gen_for_manager(db)
    mgr = await login_as(client, db, "manager")
    try:
        got = (await mgr.get("/gen/settings")).json()["model_options"]
        assert got == options_for()
        assert len(options) == 2 and options.owners == [True, False]
    finally:
        await mgr.c.aclose()


async def test_settings_model_options_default_shape(owner_api: Api) -> None:
    """Không thay thế gì: dù là khung dự phòng (chưa có G1) hay `choice_options` thật, hình dạng luôn là bốn tầng."""
    mo = (await owner_api.get("/gen/settings")).json()["model_options"]
    rows = {x["tier"]: x for x in mo["tiers"]}
    assert set(rows) == set(TIERS)
    assert all(isinstance(x["available"], bool) and isinstance(x["efforts"], list) for x in rows.values())
    assert [x for x in rows["auto"]["efforts"]] == []


# ─── 2. resolve_model_choice (THUẦN) ───────────────────────────────────────────

def test_resolve_auto_and_unknown() -> None:
    opts = options_for()
    for c in (None, {}, {"tier": "auto"}, {"tier": "auto", "effort": "high"}, {"tier": "xyz"}):
        assert engine.resolve_model_choice(c, options=opts) == (None, None, None)


def test_resolve_maps_deep_to_strong_and_keeps_supported_effort() -> None:
    opts = options_for(deep_efforts=("medium", "high"))
    f = engine.resolve_model_choice
    assert f({"tier": "deep", "effort": "high"}, options=opts) == ("strong", "high", None)
    assert f({"tier": "deep", "effort": "low"}, options=opts) == ("strong", None, None)
    assert f({"tier": "deep"}, options=opts) == ("strong", None, None)
    assert f({"tier": "fast"}, options=opts) == ("fast", None, None)
    assert f({"tier": "balanced"}, options=opts) == ("balanced", None, None)


def test_resolve_effort_dropped_when_tier_has_no_efforts() -> None:
    opts = options_for()
    got = engine.resolve_model_choice({"tier": "fast", "effort": "high"}, options=opts)
    assert got == ("fast", None, None)


def test_resolve_unavailable_degrades_to_auto_with_reason() -> None:
    f = engine.resolve_model_choice
    deep = f({"tier": "deep", "effort": "high"}, options=options_for(deep=False))
    assert deep[:2] == (None, None) and isinstance(deep[2], str)
    assert deep[2].startswith("Em dùng chế độ Tự động vì ") and "Kỹ hơn" in deep[2]
    assert "Antigravity" not in deep[2] and "Sếp" not in deep[2]       # v0.1.62: không còn lý do owner-only / bên ngoài
    none = f({"tier": "balanced"}, options=options_for(balanced=False))
    assert none[:2] == (None, None) and none[2] and "Cân bằng" in none[2]
    # Bảng tuỳ chọn thiếu tầng ⇒ coi như không dùng được (không ném lỗi).
    assert f({"tier": "fast"}, options={"tiers": []})[2] is not None
    assert f({"tier": "fast"}, options={})[2] is not None


# ─── 3. tier / effort tới bộ định tuyến, hạ về Tự động có thông báo ────────────

async def test_owner_choice_reaches_router(owner_api: Api, app: Any, options: Any) -> None:
    r_auto = RecRouter()
    t = await turn(owner_api, app, r_auto, "Xin chào")
    assert t["status"] == "done" and not steps_of(t, "notice")
    assert (r_auto.calls[0]["tier"], r_auto.calls[0]["effort"]) == (None, None)           # Tự động ⇒ không ép gì
    r_deep = RecRouter()
    t = await turn(owner_api, app, r_deep, "Phân tích giúp em tình hình tuần này",
                   choice={"tier": "deep", "effort": "high"})
    c = r_deep.calls[0]
    assert (c["tier"], c["effort"]) == ("strong", "high")
    assert not steps_of(t, "notice")
    r_fast = RecRouter()
    await turn(owner_api, app, r_fast, "Xin chào lần nữa", choice={"tier": "fast", "effort": "high"})
    assert (r_fast.calls[0]["tier"], r_fast.calls[0]["effort"]) == ("fast", None)         # fast không có efforts
    r_auto2 = RecRouter()
    await turn(owner_api, app, r_auto2, "Lại hỏi", choice={"tier": "auto", "effort": "low"})
    assert (r_auto2.calls[0]["tier"], r_auto2.calls[0]["effort"]) == (None, None)


async def test_fake_router_without_tier_params_still_works(owner_api: Api, app: Any, options: Any) -> None:
    """Bộ định tuyến giả của test cũ KHÔNG nhận tier/effort ⇒ engine không truyền (không TypeError)."""
    router = FakeRouter([TURN_DONE])
    t = await ask(owner_api, app, router, "Xin chào")
    assert t["status"] == "done"
    app.state.model_router = FakeRouter([TURN_DONE])
    t = await turn(owner_api, app, app.state.model_router, "Xin chào", choice={"tier": "deep", "effort": "high"})
    assert t["status"] == "done" and len(app.state.model_router.calls) == 1


async def test_staff_deep_choice_reaches_router_like_owner(owner_api: Api, app: Any, client: httpx.AsyncClient,
                                                           db: Any, options: Any,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.1.62: nhân viên chọn "Kỹ hơn" cũng tới bộ định tuyến (trước đây hạ về Tự động vì agy chỉ cho Owner)."""
    await _enable_gen_for_manager(db)
    mgr = await login_as(client, db, "manager")
    try:
        router = RecRouter()
        t = await turn(mgr, app, router, "Xin chào", choice={"tier": "deep", "effort": "high"})
        assert t["status"] == "done"
        assert (router.calls[0]["tier"], router.calls[0]["effort"]) == ("strong", "high")
        assert not steps_of(t, "notice")
        # Tầng chưa có nguồn nào phục vụ ⇒ vẫn hạ về Tự động kèm thông báo (bước notice trước câu trả lời, KHÔNG vào
        # lịch sử gửi cho model ở lượt sau).
        async def no_deep(db: Any, org: Any, *, owner: bool = True) -> dict[str, Any]:
            return options_for(deep=False)

        monkeypatch.setattr(engine, "model_options", no_deep)
        router1 = RecRouter()
        t1 = await turn(mgr, app, router1, "Xin chào lần nữa", choice={"tier": "deep", "effort": "high"})
        assert (router1.calls[0]["tier"], router1.calls[0]["effort"]) == (None, None)
        notice = steps_of(t1, "notice")
        assert len(notice) == 1 and notice[0]["text"].startswith("Em dùng chế độ Tự động vì ")
        assert "Kỹ hơn" in notice[0]["text"] and t1["steps"][0]["step"]["kind"] == "notice"
        router2 = RecRouter()
        await turn(mgr, app, router2, "Hỏi tiếp", cid=t1["conversation_id"])
        assert "Em dùng chế độ Tự động vì" not in router2.calls[0]["prompt"]
    finally:
        await mgr.c.aclose()


async def test_tainted_conversation_keeps_deep_tier(owner_api: Api, app: Any, options: Any) -> None:
    """v0.1.62: hội thoại đã đọc nội dung bên ngoài vẫn chọn được "Kỹ hơn" (trước đây bị hạ về Tự động, không agy)."""
    # Lượt 1 đọc hộp thư (nội dung khách = bên ngoài) ⇒ hội thoại có nội dung ngoài.
    r1 = RecRouter([{"steps": [{"kind": "tool", "name": "queue.list", "args": {"tab": "all"}}]}, TURN_DONE])
    t1 = await turn(owner_api, app, r1, "Hộp thư có gì?")
    assert t1["status"] == "done"
    r2 = RecRouter()
    t2 = await turn(owner_api, app, r2, "Phân tích sâu hơn nhé", choice={"tier": "deep", "effort": "high"},
                    cid=t1["conversation_id"])
    assert (r2.calls[0]["tier"], r2.calls[0]["effort"]) == ("strong", "high")
    assert not steps_of(t2, "notice")


async def test_tool_output_midturn_keeps_tier_choice(owner_api: Api, app: Any, options: Any) -> None:
    """Chọn Kỹ hơn rồi model gọi công cụ trả nội dung ngoài ⇒ vòng sau vẫn đúng tầng đã chọn, không lỗi."""
    router = RecRouter([{"steps": [{"kind": "tool", "name": "queue.list", "args": {"tab": "all"}}]}])
    t = await turn(owner_api, app, router, "Hộp thư có gì?", choice={"tier": "deep", "effort": "high"})
    assert t["status"] == "done"
    assert [(c["tier"], c["effort"]) for c in router.calls] == [("strong", "high"), ("strong", "high")]
    assert "[kết quả queue.list]" in router.calls[1]["prompt"]       # nội dung ngoài vẫn được bọc `wrap_untrusted`
    assert not any("Antigravity CLI" in s["text"] for s in steps_of(t, "say"))


# ─── 4. J3: định tuyến theo ý định ─────────────────────────────────────────────

def _real_router(redis: Any, hits: list[httpx.Request]) -> ModelRouter:
    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(req)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(TURN_DONE)}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

    return ModelRouter(sessionmaker(), redis, transport=httpx.MockTransport(handler), claude_factory=Boom(),
                       cli_factory=Boom())


async def test_jev_out_of_scope_gets_canned_reply_without_model_calls(owner_api: Api, app: Any, db: Any,
                                                                     redis: Any, options: Any) -> None:
    org = await org_id(db)
    await api_provider(db, org, "openrouter", 1, [FAKE_KEY])
    hits: list[httpx.Request] = []
    router = _real_router(redis, hits)
    # Đối chứng: câu thường (Jev nói "guide") ⇒ gọi model thật, ghi một dòng agent.model_calls.
    before = await model_calls()
    t0 = await turn(owner_api, app, router, "Chỉ tôi cách thêm khoá", decider_=FakeDecider("guide"))
    assert t0["status"] == "done" and len(hits) == 1 and await model_calls() == before + 1
    # Jev (giả) trả out_of_scope ⇒ câu mẫu, KHÔNG gọi model: 0 dòng model_calls mới, không một request tới nhà cung cấp.
    mark = await model_calls()
    fake = FakeDecider("out_of_scope")
    t = await turn(owner_api, app, router, "Gen ơi sửa giúp tôi cái trang web của khách", decider_=fake)
    assert t["status"] == "done"
    assert await model_calls() == mark and len(hits) == 1
    says = steps_of(t, "say")
    assert len(says) == 1 and says[0]["text"].startswith("Dạ Sếp, câu này nằm ngoài việc quản trị Console")
    assert "em" in says[0]["text"] and "{addr}" not in says[0]["text"]
    assert [s["step"]["kind"] for s in t["steps"]] == ["say"]
    # Hội thoại vẫn lưu câu hỏi + câu mẫu.
    msgs = (await owner_api.get(f"/gen/conversations/{t['conversation_id']}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    rows = [r for r in await decide_rows() if r.detail.get("value") == "out_of_scope"]
    assert len(rows) == 1 and rows[0].result == "ok"
    assert rows[0].detail["route"] == "canned" and rows[0].detail["source"] == "jev"
    assert rows[0].detail["decider"] == "jev" and rows[0].detail["latency_ms"] == 12
    assert "sửa giúp" not in json.dumps(rows[0].detail, ensure_ascii=False)     # không ghi nội dung câu hỏi
    # Không có gen.answer thất bại, không có truy vấn công cụ.
    assert not [r for r in await gen_log() if r.action in ("gen.query", "gen.answer")
                and r.detail.get("turn_id") == t["turn_id"]]


async def test_jev_timeout_falls_back_to_llm(owner_api: Api, app: Any, options: Any) -> None:
    assert decider.TIMEOUT_S == 1.5 and decider.MIN_CONFIDENCE == 0.5           # hằng số J3 giữ nguyên
    slow = decider.JevDecider(SlowJevClient(1.8, decider.INTENTS["out_of_scope"]))
    router = FakeRouter([TURN_DONE])
    t0 = time.monotonic()
    t = await turn(owner_api, app, router, "Chỉ tôi cách thêm khoá Gemini", decider_=slow)
    assert time.monotonic() - t0 < 4.0                                          # không chờ Jev quá trần
    assert t["status"] == "done" and len(router.calls) == 1                     # vẫn trả lời bằng đường cũ
    assert [s["text"] for s in steps_of(t, "say")] == ["Dạ, em trả lời đây."]
    rows = await decide_rows()
    assert len(rows) == 1 and rows[0].result == "failed"
    assert rows[0].detail["route"] == "default" and rows[0].detail["source"] == "llm"
    assert rows[0].detail["value"] is None and "timeout" in str(rows[0].detail["error"])
    assert rows[0].detail["decider"] == "llm"                                   # rơi về LLM như cũ


async def test_jev_low_confidence_falls_back_to_llm(owner_api: Api, app: Any, options: Any) -> None:
    low = decider.JevDecider(SlowJevClient(0.0, decider.INTENTS["out_of_scope"], confidence=0.3))
    router = FakeRouter([TURN_DONE])
    t = await turn(owner_api, app, router, "Chỉ tôi cách thêm khoá Gemini", decider_=low)
    assert t["status"] == "done" and len(router.calls) == 1
    rows = await decide_rows()
    assert len(rows) == 1 and rows[0].result == "failed" and rows[0].detail["source"] == "llm"
    assert rows[0].detail["route"] == "default" and "thấp" in str(rows[0].detail["error"])


async def test_jev_crash_falls_back_to_llm(owner_api: Api, app: Any, options: Any) -> None:
    class Crash(FakeDecider):
        async def intent(self, question: str) -> Decision | None:
            raise RuntimeError("Jev hỏng bất ngờ")

    router = FakeRouter([TURN_DONE])
    t = await turn(owner_api, app, router, "Chỉ tôi cách thêm khoá Gemini", decider_=Crash("guide"))
    assert t["status"] == "done" and len(router.calls) == 1
    rows = await decide_rows()
    assert rows[0].result == "failed" and rows[0].detail["error"] == "RuntimeError"


async def test_unknown_intent_from_decider_is_ignored(owner_api: Api, app: Any, options: Any) -> None:
    router = FakeRouter([TURN_DONE])
    t = await turn(owner_api, app, router, "Chỉ tôi cách thêm khoá Gemini",
                   decider_=FakeDecider("khong_co_trong_danh_sach"))
    assert t["status"] == "done" and len(router.calls) == 1
    rows = await decide_rows()
    assert rows[0].result == "failed" and rows[0].detail["source"] == "llm" and rows[0].detail["route"] == "default"


async def test_rule_runs_without_jev(owner_api: Api, app: Any, options: Any) -> None:
    """Không có nguồn Jev (LlmDecider mặc định): quy tắc tất định vẫn chặn câu ngoài phạm vi, không gọi model."""
    router = FakeRouter([TURN_DONE])
    t = await turn(owner_api, app, router, "Viết code Python giúp tôi crawl trang đối thủ")
    assert t["status"] == "done" and router.calls == []
    assert steps_of(t, "say")[0]["text"].startswith("Dạ Sếp, câu này nằm ngoài việc quản trị Console")
    rows = await decide_rows()
    assert len(rows) == 1
    d = rows[0].detail
    assert (rows[0].result, d["value"], d["route"], d["source"], d["decider"]) == ("ok", "out_of_scope", "canned",
                                                                                 "rule", "rule")
    assert "crawl" not in json.dumps(d, ensure_ascii=False) and d["latency_ms"] == 0
    # Jev hỏng + quy tắc khớp ⇒ vẫn chặn (nguồn 'rule', giữ lý do Jev trong `error`).
    router2 = FakeRouter([TURN_DONE])
    t2 = await turn(owner_api, app, router2, "Viết script bash restart nginx giúp tôi", decider_=FakeDecider(None))
    assert router2.calls == [] and steps_of(t2, "say")[0]["text"].startswith("Dạ Sếp, câu này nằm ngoài")
    d2 = (await decide_rows())[-1].detail
    assert d2["source"] == "rule" and d2["route"] == "canned" and "thấp" in str(d2["error"])


async def test_no_decide_row_when_nothing_decided(owner_api: Api, app: Any, options: Any) -> None:
    router = FakeRouter([TURN_DONE])
    t = await turn(owner_api, app, router, "Chỉ tôi cách thêm khoá Gemini")
    assert t["status"] == "done" and len(router.calls) == 1
    assert await decide_rows() == []                                            # đường cũ: không thêm dòng log nào


async def test_simple_data_in_auto_mode_uses_fast_tier(owner_api: Api, app: Any, options: Any) -> None:
    q = "Hôm nay có bao nhiêu khách mới?"
    r1 = RecRouter()
    t = await turn(owner_api, app, r1, q)                                       # quy tắc tất định (không Jev)
    assert t["status"] == "done" and r1.calls[0]["tier"] == "fast"
    d = (await decide_rows())[-1].detail
    assert (d["value"], d["route"], d["source"]) == ("data", "fast", "rule")
    # Jev (giả) nói data ⇒ nguồn jev, kèm gợi ý ý định trong prompt như cũ.
    r2 = RecRouter()
    await turn(owner_api, app, r2, "Tình hình hôm nay thế nào?", decider_=FakeDecider("data"))
    assert r2.calls[0]["tier"] == "fast" and "- ý định: data" in r2.calls[0]["prompt"]
    d2 = (await decide_rows())[-1].detail
    assert (d2["value"], d2["route"], d2["source"]) == ("data", "fast", "jev")
    # Người dùng đã CHỌN tầng ⇒ lựa chọn của họ thắng, J3 không ép Nhanh.
    r3 = RecRouter()
    await turn(owner_api, app, r3, q, choice={"tier": "deep"})
    assert r3.calls[0]["tier"] == "strong"
    assert (await decide_rows())[-1].detail["route"] == "default"
    # Câu 'data' dài / có ý ghi-phân tích ⇒ không phải "đơn giản" ⇒ giữ đường cũ.
    r4 = RecRouter()
    await turn(owner_api, app, r4, "Tình hình hôm nay thế nào, so sánh với tuần trước và soạn báo cáo cho em",
               decider_=FakeDecider("data"))
    assert r4.calls[0]["tier"] is None and (await decide_rows())[-1].detail["route"] == "default"


async def test_fast_tier_skipped_when_unavailable(owner_api: Api, app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def none_fast(db: Any, org: Any, *, owner: bool = True) -> dict[str, Any]:
        o = options_for()
        o["tiers"][1]["available"] = False
        return o

    monkeypatch.setattr(engine, "model_options", none_fast)
    r = RecRouter()
    await turn(owner_api, app, r, "Hôm nay có bao nhiêu khách mới?")
    assert r.calls[0]["tier"] is None and (await decide_rows())[-1].detail["route"] == "default"


async def test_canned_replies_are_the_value_summary_source(owner_api: Api, app: Any, options: Any) -> None:
    """G4 đếm "lần tránh gọi model" = số dòng gen.decide value='out_of_scope' (Jev + quy tắc)."""
    await turn(owner_api, app, FakeRouter([]), "Viết code Python giúp tôi")
    await turn(owner_api, app, FakeRouter([]), "Gen ơi sửa trang web", decider_=FakeDecider("out_of_scope"))
    await turn(owner_api, app, FakeRouter([TURN_DONE]), "Chỉ tôi cách thêm khoá Gemini")
    async with admin_sessionmaker()() as db:
        n = (await db.execute(text("""SELECT count(*) FROM ops.action_log
                                      WHERE action = 'gen.decide'
                                        AND detail->>'value' = 'out_of_scope'"""))).scalar_one()
    assert n == 2


async def test_staff_also_gets_canned_reply(owner_api: Api, app: Any, client: httpx.AsyncClient, db: Any,
                                           options: Any) -> None:
    await _enable_gen_for_manager(db)
    mgr = await login_as(client, db, "manager")
    try:
        router = FakeRouter([TURN_DONE])
        t = await turn(mgr, app, router, "Kể chuyện cười cho vui đi")
        assert router.calls == [] and steps_of(t, "say")[0]["text"].startswith("Dạ ")
    finally:
        await mgr.c.aclose()


# ─── 5. quy tắc tất định + API công khai của decider ───────────────────────────

@pytest.mark.parametrize(("q", "want"), [
    ("Viết code Python giúp tôi", "out_of_scope"),
    ("Sửa lỗi code trong file này", "out_of_scope"),
    ("Viết script bash restart nginx giúp tôi", "out_of_scope"),
    ("Kể chuyện cười cho vui đi", "out_of_scope"),
    ("Làm thơ về mùa thu nhé", "out_of_scope"),
    ("Lập trình giúp em một bot", "out_of_scope"),
    ("Hôm nay có bao nhiêu khách mới?", "data"),
    ("có bao nhiêu việc quá hạn", "data"),
    ("Tổng số nháp chờ duyệt?", "data"),
    # Hỏi cách dùng Console (kể cả nhắc tới GitHub/Docker/máy chủ của Console) KHÔNG được chặn nhầm.
    ("Lệnh sudo loginctl enable-linger trên thẻ Cập nhật là gì?", None),
    ("Console bảo chạy sudo systemctl enable docker, lệnh đó làm gì?", None),
    ("genh auto-update enable chạy ở đâu?", None),
    ("ssh vào máy chủ của Console thì làm thế nào?", None),
    # Chủ đề kinh doanh của Sếp / khách có từ Python, Java, bóng đá, kể chuyện, thời tiết KHÔNG phải "ngoài phạm vi".
    ("Khách hỏi về khoá học Python, em tóm tắt giúp", None),
    ("Kể chuyện hôm qua khách A phàn nàn gì?", None),
    ("Khách Lan kể chuyện gì hôm nay?", None),
    ("Thời tiết xấu nên khách nào hoãn đơn?", None),
    ("Quán bán đồ bóng đá, khách nào hỏi áo đấu?", None),
    ("Có bao nhiêu khách hỏi mua cà phê Java?", "data"),
    ("Tuần này có mấy khách nhắn về bóng đá?", "data"),
    ("Kết nối GitHub ở đâu?", None),
    ("Docker báo lỗi thì xem log ở đâu?", None),
    ("Chỉ tôi cách thêm khoá Gemini", None),
    ("Hôm nay có gì cần tôi xử lý?", None),
    ("Nhắc tôi gọi lại khách lúc 3 giờ chiều", None),
    ("Có bao nhiêu khách, so sánh với tuần trước và soạn báo cáo", None),   # không "đơn giản"
    ("", None),
])
def test_rule_intent(q: str, want: str | None) -> None:
    d = decider.rule_intent(q)
    assert (d.value if d else None) == want
    if d:
        assert d.source == "rule" and d.value in decider.INTENTS and d.latency_ms == 0 and d.confidence == 1.0


def test_is_simple_question() -> None:
    assert decider.is_simple_question("Hôm nay có bao nhiêu khách mới?")
    assert not decider.is_simple_question("x " * 60)
    assert not decider.is_simple_question("Soạn giúp em nháp báo giá")
    assert not decider.is_simple_question("")


def test_decider_public_api_unchanged() -> None:
    assert set(decider.INTENTS) == {"data", "guide", "report", "out_of_scope"}
    for name in ("Decider", "JevDecider", "LlmDecider", "load_decider", "Decision", "TIMEOUT_S", "MIN_CONFIDENCE"):
        assert hasattr(decider, name)
    assert hasattr(decider.JevDecider, "intent") and hasattr(decider.LlmDecider, "classify")
