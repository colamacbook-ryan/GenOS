# ruff: noqa: F811 — fixture `clis` nhập từ test_cli_models_v0131 (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.58 (hotfix) — máy Boss: Gen không trả lời, "Kiểm tra kết nối" đỏ 'CLI không nhận model ""'.

Gốc rễ: agy của Boss không đánh dấu model "current" ⇒ lượt gọi thử KHÔNG gửi `--effort`; agy từ chối và nêu
`(available: low, medium, high)`; `_cli_probe` không thử lại với một mức trong danh sách đó ⇒ mọi ứng viên trượt,
câu báo lỗi dựng bằng model "" ⇒ ô chọn model bị ẩn, Boss kẹt. Nguồn lại mất dòng model nên mọi vai "Chuẩn: chưa có
nguồn phù hợp".

Phần 1 (thuần, client giả): `_cli_probe` / `friendly_probe_error` / `_complete`.
Phần 2 (DB + CLI giả `fake_agy_multi.py` ở chế độ FAKE_AGY_REQUIRE_EFFORT=1): "Owner chỉ có Antigravity CLI, không
khoá API".
"""

import asyncio
import base64
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from gh.db import sessionmaker
from gh.providers import router as router_mod
from gh.providers.clients import AgyClient, Completion, Message, ModelRejected, rejection
from gh.providers.router import (
    PROBE_MAX_CALLS,
    ModelRouter,
    ModelUnavailable,
    effort_from_rejection,
    friendly_probe_error,
)
from tests.test_cli_models_v0131 import (
    _login,
    _provider,
    clis,  # noqa: F401 — fixture dùng chung
)

NEED_EFFORT = ('invalid model selection (--model "{m}" --effort ""): Invalid model "{m}" '
               '(available: low, medium, high)')


class FakeAgy:
    """Client giả: agy trên máy Boss — model nào cũng đòi `--effort` (low/medium/high) và không nhận mức nào khác."""

    def __init__(self, *, accept: tuple[str, ...] = ("low", "medium", "high"), models_ok: tuple[str, ...] = (),
                 effort_error: bool = False) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.accept, self.models_ok, self.effort_error = accept, models_ok, effort_error

    async def generate(self, model: str, messages: list[Message], *, json_mode: bool, temperature: float,
                       effort: str | None = None) -> Completion:
        self.calls.append((model, effort))
        if self.models_ok and model not in self.models_ok:
            raise rejection(f'invalid model selection (--model "{model}" --effort ""): Invalid model "{model}" '
                            f'(available: {", ".join(self.models_ok)})') or AssertionError
        if not effort:
            raise rejection(NEED_EFFORT.format(m=model)) or AssertionError
        if effort not in self.accept or self.effort_error:
            raise rejection(f'invalid model selection (--model "{model}" --effort "{effort}"): invalid --effort '
                            f'"{effort}" (valid: {", ".join(self.accept)})') or AssertionError
        return Completion("ok", 1, 1, {})


def _router(client: Any) -> ModelRouter:
    r = ModelRouter(None, None)  # type: ignore[arg-type]
    r.cli_factory = lambda: client
    return r


# ─── (1) _cli_probe ─────────────────────────────────────────────────────────

async def test_probe_retries_same_model_with_medium_when_cli_requires_effort() -> None:
    fake = FakeAgy()
    model, effort, _c = await _router(fake)._cli_probe(fake, [("gemini-3.8-flash", None), ("gemini-3.1-pro", None)])
    assert (model, effort) == ("gemini-3.8-flash", "medium")
    # cùng model, không bỏ sang model kế
    assert fake.calls == [("gemini-3.8-flash", None), ("gemini-3.8-flash", "medium")]


async def test_probe_takes_first_listed_effort_when_medium_not_offered() -> None:
    fake = FakeAgy(accept=("low", "high"))

    async def gen(model: str, messages: list[Message], *, json_mode: bool, temperature: float,
                  effort: str | None = None) -> Completion:
        fake.calls.append((model, effort))
        if not effort:
            raise rejection('invalid model selection (--model "gemini-3.1-pro" --effort ""): Invalid model '
                            '"gemini-3.1-pro" (available: low, high)') or AssertionError
        return Completion("ok", 1, 1, {})

    fake.generate = gen  # type: ignore[method-assign]
    model, effort, _c = await _router(fake)._cli_probe(fake, [("gemini-3.1-pro", None)])
    assert (model, effort) == ("gemini-3.1-pro", "low")


async def test_probe_effort_rejected_retries_without_effort() -> None:
    """what == 'effort' khi ĐÃ gửi mức ⇒ thử lại cùng model KHÔNG gửi mức."""
    calls: list[tuple[str, str | None]] = []

    async def gen(model: str, messages: list[Message], *, json_mode: bool, temperature: float,
                  effort: str | None = None) -> Completion:
        calls.append((model, effort))
        if effort:
            raise ModelRejected("invalid --effort", what="effort", raw="invalid --effort")
        return Completion("ok", 1, 1, {})

    fake = SimpleNamespace(generate=gen)
    model, effort, _c = await _router(fake)._cli_probe(fake, [("gemini-3.8-flash", "high")])
    assert (model, effort) == ("gemini-3.8-flash", None)
    assert calls == [("gemini-3.8-flash", "high"), ("gemini-3.8-flash", None)]


async def test_probe_gives_up_after_four_calls_and_reports_last_attempt() -> None:
    fake = FakeAgy(effort_error=True)       # CLI đòi mức nhưng từ chối mọi mức
    with pytest.raises(ModelRejected) as ei:
        await _router(fake)._cli_probe(fake, [("m1", None), ("m2", "high"), ("m3", None), ("m4", None)])
    assert 1 <= len(fake.calls) <= PROBE_MAX_CALLS == 4
    e = ei.value
    assert (e.model, e.effort) == fake.calls[-1]            # gắn model / mức của lượt cuối
    msg = friendly_probe_error(e, e.model or "", e.effort)
    assert "“”" not in msg and '""' not in msg and e.model in msg


async def test_probe_non_effort_rejection_moves_on_to_next_model() -> None:
    fake = FakeAgy(models_ok=("gemini-3.1-pro",))
    # model 1 sai tên (danh sách CLI nêu là TÊN MODEL, không phải mức) ⇒ không thử mức, sang model kế.
    model, effort, _c = await _router(fake)._cli_probe(fake, [("gemini-9", None), ("gemini-3.1-pro", None)])
    assert (model, effort) == ("gemini-3.1-pro", "medium")
    assert fake.calls[0] == ("gemini-9", None) and fake.calls[1] == ("gemini-3.1-pro", None)


# ─── (2) câu báo lỗi ────────────────────────────────────────────────────────

def test_friendly_error_names_efforts_and_never_prints_empty_model() -> None:
    e = rejection(NEED_EFFORT.format(m="gemini-3.8-flash"))
    assert e is not None
    assert friendly_probe_error(e, "gemini-3.8-flash") == (
        "CLI cần chọn mức suy nghĩ cho model “gemini-3.8-flash” (nhận: Thấp, Vừa, Cao)")
    empty = friendly_probe_error(e, "")
    assert empty == "CLI cần chọn mức suy nghĩ (nhận: Thấp, Vừa, Cao)"
    # Đã gửi mức mà CLI vẫn nêu danh sách mức ⇒ KHÔNG bảo "cần chọn mức" (đã chọn rồi): nêu model + mức + danh sách.
    assert friendly_probe_error(e, "gemini-3.8-flash", "medium") == (
        "CLI không nhận model “gemini-3.8-flash” với mức “Vừa” (CLI nêu: Thấp, Vừa, Cao)")
    assert friendly_probe_error(e, "", "medium") == (
        "CLI không nhận model đã chọn với mức “Vừa” (CLI nêu: Thấp, Vừa, Cao)")
    # Lỗi model thật + model rỗng: không in “” (câu cũ: 'CLI không nhận model “”').
    other = rejection('Invalid model "x" (available: gemini-3.8-flash, gemini-3.1-pro)')
    assert other is not None
    assert "“”" not in friendly_probe_error(other, "") and "model đã chọn" in friendly_probe_error(other, "")
    eff = ModelRejected("x", what="effort")
    assert "“”" not in friendly_probe_error(eff, "", "high")


def test_effort_from_rejection_only_for_effort_lists() -> None:
    e = rejection(NEED_EFFORT.format(m="x"))
    assert e is not None
    assert effort_from_rejection("antigravity_cli", e) == "medium"
    assert effort_from_rejection("antigravity_cli", e, tried={"medium"}) == "low"
    assert effort_from_rejection("antigravity_cli", e, tried={"low", "medium", "high"}) is None
    assert effort_from_rejection("gemini", e) is None                       # nguồn khoá API không có mức suy nghĩ
    names = rejection('Invalid model "x" (available: gemini-3.8-flash, gemini-3.1-pro)')
    assert names is not None and effort_from_rejection("antigravity_cli", names) is None


# ─── (3) _complete: chưa gửi mức mà CLI đòi mức ──────────────────────────────

async def test_complete_recalls_once_with_valid_effort() -> None:
    fake = FakeAgy()
    p, m = SimpleNamespace(kind="antigravity_cli"), SimpleNamespace(model_name="gemini-3.8-flash")
    c = await _router(fake)._complete(p, None, m, [Message("user", "hi")], json_mode=False, temperature=0.2,
                                      effort=None, implicit=False, where="inproc")
    assert c.text == "ok" and fake.calls == [("gemini-3.8-flash", None), ("gemini-3.8-flash", "medium")]
    # Lượt gọi lại vẫn bị từ chối ⇒ ném lỗi, KHÔNG gọi lần thứ ba.
    bad = FakeAgy(effort_error=True)
    with pytest.raises(ModelRejected):
        await _router(bad)._complete(p, None, m, [Message("user", "hi")], json_mode=False, temperature=0.2,
                                     effort=None, implicit=False, where="inproc")
    assert len(bad.calls) == 2


async def test_complete_does_not_retry_for_api_sources_or_when_effort_was_sent() -> None:
    fake = FakeAgy(effort_error=True)
    m = SimpleNamespace(model_name="gemini-3.8-flash")
    with pytest.raises(ModelRejected):          # đã gửi mức (không phải mức do hồ sơ tự thêm) ⇒ không thử lại
        await _router(fake)._complete(SimpleNamespace(kind="antigravity_cli"), None, m, [Message("user", "hi")],
                                      json_mode=False, temperature=0.2, effort="high", implicit=False, where="inproc")
    assert len(fake.calls) == 1


# ─── (4) DB: Owner chỉ có Antigravity CLI, không khoá API ────────────────────

@pytest.fixture
def needs_effort(clis):  # type: ignore[no-untyped-def]
    """agy giả ở chế độ máy Boss (FAKE_AGY_REQUIRE_EFFORT=1): `agy models` không có "(current)"; không --effort ⇒ 3."""
    path = Path(clis["agy"])
    path.write_text(path.read_text().replace("exec ", "FAKE_AGY_REQUIRE_EFFORT=1 exec ", 1))
    return clis


def _calls(clis: dict[str, Any]) -> list[dict[str, Any]]:
    log = Path(clis["agy_home"]).parent.parent / "agy-calls.log"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


async def _boss_state(api: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """Đăng nhập agy, nguồn khác tắt, agy 0 dòng model (như máy Boss sau v0.1.57). → (org, provider)."""
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    pid = uuid.UUID(p["id"])
    async with sessionmaker()() as db:
        org = (await db.execute(text("SELECT org_id FROM agent.providers WHERE id = :i"), {"i": pid})).scalar_one()
        await db.execute(text("DELETE FROM agent.models WHERE provider_id = :p"), {"p": pid})
        await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'antigravity_cli') WHERE org_id = :o"),
                         {"o": org})
        await db.commit()
    return org, pid


async def _models(pid: uuid.UUID) -> list[Any]:
    async with sessionmaker()() as db:
        return list((await db.execute(text("""SELECT model_name, effort, is_default FROM agent.models
                                              WHERE provider_id = :p ORDER BY model_name"""), {"p": pid})).all())


async def _gen(app: Any, org: uuid.UUID) -> Any:
    return await app.state.model_router.generate(org, agent_key="core.gen", purpose="gen.turn",
                                                 messages=[Message("user", "hi")], json_mode=False)


async def test_test_button_green_saves_one_model_and_gen_answers(owner_api, app, needs_effort) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    org, pid = await _boss_state(api)
    r = await api.send("POST", f"/providers/{pid}/test")
    t = r.json()
    assert r.status_code == 200 and t["ok"] is True, t
    assert (t["probe_model"], t["probe_effort"]) == ("gemini-3.8-flash", "medium")
    assert "“”" not in json.dumps(t, ensure_ascii=False)
    rows = await _models(pid)
    # v0.1.66 (F132): lưu SẴN mọi model `agy models` liệt kê (xếp theo tên); model thăm dò giữ mức đã chạy được.
    assert [(m.model_name, m.effort, m.is_default) for m in rows] == [
        ("claude-sonnet-4-6-thinking", None, False),
        ("gemini-3.1-pro", None, False),
        ("gemini-3.8-flash", "medium", False),
    ]
    # Gọi lần 2 không thêm dòng, không ghi thêm nhật ký tự thêm.
    assert (await api.send("POST", f"/providers/{pid}/test")).json()["ok"] is True
    assert len(await _models(pid)) == 3
    async with sessionmaker()() as db:
        n = (await db.execute(text("""SELECT count(*) FROM ops.action_log WHERE org_id = :o
                                      AND action = 'provider.model_auto'"""), {"o": org})).scalar_one()
    assert n == 1
    # Lượt Gen của Owner trả lời được.
    out = await _gen(app, org)
    assert out.text == "whoami:an@example.vn" and out.model == "gemini-3.8-flash"


async def test_existing_model_is_never_touched_by_test_button(owner_api, needs_effort) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    _org, pid = await _boss_state(api)
    r = await api.send("POST", f"/providers/{pid}/models",
                       {"model_name": "gemini-3.1-pro", "effort": "high", "make_default": True})
    assert r.status_code == 201, r.text
    assert (await api.send("POST", f"/providers/{pid}/test")).json()["ok"] is True
    assert [(m.model_name, m.effort, m.is_default) for m in await _models(pid)] == [("gemini-3.1-pro", "high", True)]


async def test_gen_turn_self_heals_when_test_button_never_pressed(owner_api, app, needs_effort) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    org, pid = await _boss_state(api)
    assert await _models(pid) == []
    n0 = len(_calls(needs_effort))
    out = await _gen(app, org)                       # Boss chưa bấm Kiểm tra: lượt Gen tự lành
    assert out.text == "whoami:an@example.vn"
    rows = await _models(pid)
    assert [(m.model_name, m.effort, m.is_default) for m in rows] == [
        ("claude-sonnet-4-6-thinking", None, False),
        ("gemini-3.1-pro", None, False),
        ("gemini-3.8-flash", "medium", False),
    ]
    healed = len(_calls(needs_effort)) - n0
    assert healed == 3                               # gọi thử (không mức → bị từ chối, rồi 'medium') + chính lượt Gen
    await _gen(app, org)
    assert len(_calls(needs_effort)) - n0 == healed + 1          # lượt sau không gọi thử lại
    # Khoá Redis 10 phút: mất lại dòng model thì KHÔNG tự lành lần nữa (đúng một lần / 10 phút).
    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM agent.models WHERE provider_id = :p"), {"p": pid})
        await db.commit()
    n1 = len(_calls(needs_effort))
    with pytest.raises(ModelUnavailable):
        await _gen(app, org)
    assert len(_calls(needs_effort)) == n1 and await _models(pid) == []


async def test_heal_skipped_when_chain_has_another_source(owner_api, app, needs_effort, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Review F-R1: chuỗi đã có nguồn khác ⇒ KHÔNG gọi thử agy (không tốn tới 60 giây của lượt Gen)."""
    api = owner_api
    org, pid = await _boss_state(api)
    async with sessionmaker()() as db:
        cid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, failover_rank, auth_state)
                                        VALUES (:o, 'claude_code_cli', 'Claude CLI', 5, 'ok') RETURNING id"""),
                                {"o": org})).scalar_one()
        await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'sonnet')"), {"p": cid})
        await db.commit()
    router = app.state.model_router
    healed: list[uuid.UUID] = []

    async def spy(o: uuid.UUID) -> bool:
        healed.append(o)
        return False

    async def answer(*_a: Any, **_k: Any) -> Completion:
        return Completion("claude-ok", 1, 1, {})

    monkeypatch.setattr(router, "_heal_agy", spy)
    monkeypatch.setattr(router, "_complete", answer)
    n0 = len(_calls(needs_effort))
    out = await _gen(app, org)
    assert out.text == "claude-ok" and healed == []
    assert len(_calls(needs_effort)) == n0 and await _models(pid) == []


async def test_heal_that_hangs_is_cut_and_gen_turn_goes_on(owner_api, app, needs_effort, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Review F-R1: `test_provider` treo ⇒ cắt ở AGY_HEAL_TIMEOUT, lượt Gen đi tiếp đường cũ (hết chuỗi → báo gọn),
    không treo; khoá Redis 10 phút giữ nguyên nên lượt sau không gọi thử lại."""
    api = owner_api
    org, pid = await _boss_state(api)
    router = app.state.model_router
    started: list[uuid.UUID] = []

    async def hang(provider_id: uuid.UUID) -> dict[str, Any]:
        started.append(provider_id)
        await asyncio.sleep(3600)
        return {"ok": True}

    monkeypatch.setattr(router, "test_provider", hang)
    monkeypatch.setattr(router_mod, "AGY_HEAL_TIMEOUT", 0.2)
    with pytest.raises(ModelUnavailable):
        await asyncio.wait_for(_gen(app, org), 10)
    assert started == [pid] and await _models(pid) == []
    with pytest.raises(ModelUnavailable):
        await asyncio.wait_for(_gen(app, org), 10)
    assert started == [pid]


@pytest.mark.parametrize("agent_key,purpose", [("core.gen", "gen.briefing"), ("agent:{id}", "agent.task")])
async def test_background_and_other_roles_also_self_heal_agy(  # type: ignore[no-untyped-def]
        owner_api, app, needs_effort, box, agent_key, purpose) -> None:
    """Chuỗi rỗng ở việc nền / vai core cũng tự lành (gọi thử một lần rồi lưu model) và lượt gọi trả lời bằng agy. H2:
    chỉ vai chạy agy TRONG TIẾN TRÌNH (Gen của Owner, agent nội bộ) — vai qua hộp (kể cả Bản tin, review v0.1.63 P2-3)
    không kích hoạt lượt thử."""
    api = owner_api
    org, pid = await _boss_state(api)
    n0 = len(_calls(needs_effort))
    out = await app.state.model_router.generate(org, agent_key=agent_key.format(id=uuid.uuid4()), purpose=purpose,
                                                messages=[Message("user", "nội dung của khách")], json_mode=False)
    assert out.text == "whoami:an@example.vn"
    assert len(_calls(needs_effort)) > n0            # agy được gọi
    # và lưu sẵn mọi model agy liệt kê (v0.1.66, F132)
    names = [m.model_name for m in await _models(pid)]
    assert names == ["claude-sonnet-4-6-thinking", "gemini-3.1-pro", "gemini-3.8-flash"]


async def test_box_roles_do_not_self_heal_agy_inproc(  # type: ignore[no-untyped-def]
        owner_api, app, needs_effort, box) -> None:
    """Sàng lọc tin người lạ (core.refinery) qua hộp: chuỗi rỗng ⇒ hết chuỗi, KHÔNG gọi thử agy trong tiến trình."""
    org, pid = await _boss_state(owner_api)
    n0 = len(_calls(needs_effort))
    for key, purpose in (("core.refinery", "refinery.extract"), ("core.briefing", "gen.briefing")):
        with pytest.raises(ModelUnavailable):
            await app.state.model_router.generate(org, agent_key=key, purpose=purpose,
                                                  messages=[Message("user", "tin của người lạ")], json_mode=False)
    assert len(_calls(needs_effort)) == n0 and box.calls == [] and await _models(pid) == []


async def test_bindings_show_agy_model_for_every_role(owner_api, needs_effort, box) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    _org, pid = await _boss_state(api)

    async def items() -> dict[str, Any]:
        r = await api.get("/agents/bindings")
        assert r.status_code == 200, r.text
        return {i["agent_key"]: i for i in r.json()["items"]}

    before = await items()          # 0 model: lý do cụ thể, đường tự sửa
    assert before["core.gen"]["standard"] is None
    assert before["core.gen"]["standard_reason"] == "chưa có model — bấm Kiểm tra kết nối ở Antigravity CLI"
    assert (await api.send("POST", f"/providers/{pid}/test")).json()["ok"] is True
    after = await items()
    gen = after["core.gen"]
    assert gen["source"] == "standard" and gen["standard_reason"] is None
    assert (gen["standard"]["model_name"], gen["standard"]["effort"]) == ("gemini-3.8-flash", "medium")
    # v0.1.62: agy mở cho mọi vai ⇒ sàng lọc, Bản tin, soạn lại cũng "Chuẩn: <model agy>" (hết lý do "cần khoá API").
    for key in ("core.refinery", "core.briefing"):
        assert after[key]["standard"]["model_name"] == "gemini-3.8-flash", key
        assert after[key]["standard_reason"] is None, key
    # H2: core.reply (ngoại giao) chạy agy qua hộp: hộp chạy ⇒ có model chuẩn; hộp tắt ⇒ câu trung thực.
    assert after["core.reply"]["standard"]["model_name"] == "gemini-3.8-flash"
    box.state = "down"
    off = await items()
    reply_off = off["core.reply"]
    assert reply_off["standard"] is None and "Hộp riêng Antigravity chưa chạy" in reply_off["standard_reason"]
    box.state = "ok"
    assert all(isinstance(i["standard_reason"], str | None) for i in after.values())
    assert "cần khoá API" not in json.dumps(after, ensure_ascii=False)
    # Mục "Về mặc định" dùng cùng lý do (một sự thật cho cả hai màn).
    dflt = {i["key"]: i for i in (await api.get("/defaults")).json()["items"]}
    assert dflt["binding:core.gen"]["default_text"].startswith("Chuẩn: gemini-3.8-flash (tự chọn)")
    assert dflt["binding:core.refinery"]["default_text"].startswith("Chuẩn: gemini-3.8-flash (tự chọn)")


def _signed_in_home(clis: dict[str, Any]) -> Path:
    home = Path(clis["agy_home"])
    home.mkdir(parents=True, exist_ok=True)
    claims = base64.urlsafe_b64encode(json.dumps({"email": "an@example.vn"}).encode()).decode().rstrip("=")
    (home / "antigravity-oauth-token").write_text(json.dumps({"access_token": "t", "id_token": f"h.{claims}.s",
                                                                "expiry": 4102444800}))
    return home


async def test_real_agy_client_sends_effort_after_requirement(needs_effort) -> None:  # type: ignore[no-untyped-def]
    """Đường thật qua tiến trình agy giả: lượt không mức bị từ chối đúng câu của agy, lượt có mức qua."""
    home = _signed_in_home(needs_effort)
    client = AgyClient(needs_effort["agy"], str(home), timeout=30)
    with pytest.raises(ModelRejected) as ei:
        await client.generate("gemini-3.8-flash", [Message("user", "hi")], json_mode=False, temperature=0)
    assert ei.value.available == ["low", "medium", "high"]
    ok = await client.generate("gemini-3.8-flash", [Message("user", "hi")], json_mode=False, temperature=0,
                               effort="medium")
    assert ok.text == "whoami:an@example.vn"
