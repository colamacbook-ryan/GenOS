"""Lưới L2 (v0.1.66, gói B3) — "model hiển thị = model chạy" + MỘT bảng tầng.

Hợp đồng (một nguồn sự thật cho chọn model):
(a) `catalog.tier(m) == catalog.tier_of(kind, m)` cho mọi model trong danh mục + tên mẫu; nhãn gợi ý ô chọn model
    (`describe`) dùng đúng tầng đó (flash = Cân bằng, flash-lite = Nhanh) — F025, F132;
(b) `profiles.standard_with_reasons(role)` (cái Sếp THẤY ở "Chuẩn: …") == mắt xích đầu thật sự chạy của
    `router._chain` (model + mức suy nghĩ) cho mọi vai × cấu hình nguồn/model/is_default × tầng ép — F005, F020;
(c) Gen không bị J3 ép tầng "Nhanh" khi Sếp đã ghim model cho core.gen (dòng gán hoặc "Dùng model này") — F006;
(d) nhiệt độ của hồ sơ vai Gen được truyền vào lượt gọi model — F021;
(e) "Kiểm tra kết nối" xanh của Antigravity CLI lưu sẵn mọi model agy liệt kê (chỉ khi nguồn chưa có model nào) — F132;
(f) nhãn model từ mã (`claude-sonnet-4-6-thinking` → "Claude Sonnet 4.6 Thinking") — F146;
(g) lỗi Gen "thiếu khoá" nói đúng việc cần làm, không bảo "thử lại sau" — F010 (phần câu lỗi).
Cấu hình mới mà thêm nơi chọn model riêng ⇒ thêm vào ma trận (b) trong cùng PR.
"""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from gh.db import sessionmaker
from gh.defaults import profiles
from gh.gen import engine
from gh.providers import catalog
from gh.providers.clients import Message
from gh.providers.router import ModelRouter, ModelUnavailable, Routed, adopt_probed_model
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_defaults_v0155 import AGY, API, CLAUDE, add_source, bind
from tests.test_gen import FakeRouter
from tests.test_gen_model_choice_v0155 import TURN_DONE, decide_rows, turn

KINDS = (AGY, CLAUDE, API, "gemini", "deepseek")


# ─── (a) một bảng tầng ────────────────────────────────────────────────────────

SAMPLES = ("gemini-3.8-flash", "gemini-3.1-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro",
           "claude-sonnet-4-6-thinking", "claude-opus-4-6", "claude-haiku-4-5", "gpt-oss-120b", "gpt-oss-20b",
           "google/gemini-2.5-flash", "deepseek-chat", "haiku", "sonnet", "opus", "fable", "gpt-4o-mini")


def test_catalog_has_one_tier_table() -> None:
    for kind, entries in ((AGY, catalog.AGY_FALLBACK), (CLAUDE, catalog.CLAUDE_CODE_MODELS)):
        for m in entries:
            mid = str(m["id"])
            assert catalog.tier(mid) == catalog.tier_of(kind, mid) == m["tier"], (kind, mid)
    for kind in KINDS:
        for name in SAMPLES:
            assert catalog.tier(name) == catalog.tier_of(kind, name), (kind, name)
            assert catalog.describe(kind, name)["tier"] == catalog.tier_of(kind, name), (kind, name)


def test_flash_is_balanced_everywhere_and_hint_follows() -> None:
    built = catalog.build(AGY, ["gemini-3.8-flash-low", "claude-opus-4-6", "gemini-3.1-pro-high"])
    by_id = {m["id"]: m for g in built["model_groups"] for m in g["models"]}
    assert by_id["gemini-3.8-flash"]["tier"] == "balanced" and by_id["gemini-3.8-flash"]["hint"] == "cân bằng"
    assert by_id["gemini-3.1-pro"]["tier"] == "strong" and by_id["claude-opus-4-6"]["tier"] == "strong"
    assert catalog.describe(API, "gemini-2.5-flash-lite")["hint"] == "nhanh, rẻ"


@pytest.mark.parametrize(("slug", "label"), [
    ("claude-sonnet-4-6-thinking", "Claude Sonnet 4.6 Thinking"),
    ("claude-opus-4-6", "Claude Opus 4.6"),
    ("gemini-3.8-flash", "Gemini 3.8 Flash"),
    ("gpt-oss-120b", "GPT OSS 120b"),
])
def test_pretty_label_keeps_version_numbers(slug: str, label: str) -> None:
    assert catalog.describe(AGY, slug)["label"] == label


# ─── (b) hiển thị = chạy ──────────────────────────────────────────────────────

# (tên, danh sách nguồn: (kind, tên, hạng, [model], model mặc định | None, effort theo model))
CONFIGS: list[tuple[str, list[tuple[str, str, int, list[str], str | None]]]] = [
    ("F005: flash + pro(mặc định)", [(API, "alpha", 1, ["gemini-2.5-flash", "gemini-2.5-pro"], "gemini-2.5-pro")]),
    ("API đủ ba tầng, không chốt", [(API, "alpha", 1, ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
                                    None)]),
    ("agy ba model", [(AGY, "Antigravity", 1, ["gemini-3.8-flash", "gemini-3.1-pro", "claude-sonnet-4-6-thinking"],
                       None)]),
    ("agy chốt pro", [(AGY, "Antigravity", 1, ["gemini-3.8-flash", "gemini-3.1-pro"], "gemini-3.1-pro")]),
    ("hai nguồn API, nguồn đầu chốt model", [(API, "alpha", 1, ["z-pro", "a-flash-lite"], "z-pro"),
                                             (API, "beta", 2, ["b-flash"], None)]),
    ("agy hạng 1 + API hạng 2 chốt", [(AGY, "Antigravity", 1, ["gemini-3.8-flash", "gemini-3.1-pro"], None),
                                      (API, "beta", 2, ["gemini-2.5-flash", "gemini-2.5-pro"], "gemini-2.5-pro")]),
    ("Claude CLI + agy", [(CLAUDE, "Claude Code", 1, ["haiku", "sonnet", "opus"], None),
                          (AGY, "Antigravity", 2, ["gemini-3.8-flash", "gemini-3.1-pro"], "gemini-3.8-flash")]),
]
ROLES = ("core.gen", "core.briefing", "core.refinery", "core.reply", f"agent:{uuid.uuid4()}")
TIERS = (None, "fast", "balanced", "strong")


async def _wipe(db: Any) -> None:
    for t in ("agent.bindings", "agent.models", "agent.provider_keys", "agent.providers"):
        await db.execute(text(f"DELETE FROM {t}"))        # noqa: S608 — tên bảng cố định
    await db.commit()


def _runs_first(chain: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    """Mắt xích đầu tiên THẬT SỰ được gọi: bỏ Claude Code CLI ở việc nền chưa được phép (`generate` bỏ qua)."""
    for link in chain:
        if link["provider"].kind == CLAUDE and profiles.role_background(role):
            continue
        if link["provider"].kind in profiles.API_KINDS and not link["keys"]:
            continue
        return link
    return None


async def test_displayed_model_is_the_model_that_runs(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    r = ModelRouter(sessionmaker(), redis)
    for name, sources in CONFIGS:
        await _wipe(db)
        for kind, pname, rank, models, default in sources:
            await add_source(db, org, kind, pname, rank, models, default=default)
        for role in ROLES:
            async with sessionmaker()() as s:
                std, why = await profiles.standard_with_reasons(s, org, [role])
                providers, models = await profiles.load_rows(s, org)
                for tier in TIERS:
                    chain = await r._chain(s, org, role, tier=tier)
                    link = _runs_first(chain, role)
                    cands = profiles.resolve(providers, models, role, background=profiles.role_background(role),
                                             bg_cli_allowed=set(), tier_override=tier)
                    shown = (cands[0] if cands else None) if tier else std[role]
                    where = f"[{name}] vai={role} tầng={tier}"
                    if shown is None:
                        assert link is None or role == "core.reply" or why[role], where
                        continue
                    assert link is not None, where
                    p, m = link["provider"], link["model"]
                    assert (shown["provider_name"], shown["model_name"]) == (p.name, m.model_name), where
                    eff, _implicit = ModelRouter._effort(role, p, m, link, None)
                    assert shown["effort"] == eff, f"{where}: hiển thị {shown['effort']} ≠ chạy {eff}"
                    assert shown["source"] == link["source"], where


async def test_binding_row_wins_in_chain_and_is_not_the_standard(app: Any, db: Any, redis: Any) -> None:
    """Dòng gán của Sếp đứng đầu chuỗi; "Chuẩn" chỉ hiện khi KHÔNG có dòng gán (web hiện dòng gán thay Chuẩn)."""
    org = await org_id(db)
    _, ids = await add_source(db, org, API, "alpha", 1, ["gemini-2.5-flash", "gemini-2.5-pro"])
    await bind(db, org, "core.gen", ids["gemini-2.5-pro"])
    async with sessionmaker()() as s:
        chain = await ModelRouter(sessionmaker(), redis)._chain(s, org, "core.gen")
    assert (chain[0]["model"].model_name, chain[0]["source"]) == ("gemini-2.5-pro", "binding")


# ─── (c) J3 không đẩy model Sếp đã ghim xuống cuối ─────────────────────────────

class TempRouter(ModelRouter):
    """Bộ định tuyến giả ghi lại tier + temperature của từng lượt Gen."""

    def __init__(self) -> None:  # không gọi super(): không cần CSDL / Redis
        self.calls: list[dict[str, Any]] = []

    async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Message],
                       json_mode: bool = True, temperature: float = 0.2, tier: str | None = None,
                       effort: str | None = None, owner: bool = True) -> Routed:
        self.calls.append({"tier": tier, "temperature": temperature, "effort": effort})
        return Routed(json.dumps(TURN_DONE), "Giả lập", "fake-1", 1, 1)


@pytest.fixture
def fast_options(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(db: Any, org: Any, *, owner: bool = True) -> dict[str, Any]:
        return {"tiers": [{"tier": t, "available": True, "efforts": []} for t in ("auto", "fast", "balanced", "deep")]}

    monkeypatch.setattr(engine, "model_options", fake)


Q = "Hôm nay có bao nhiêu khách mới?"


async def test_j3_fast_tier_not_forced_when_owner_pinned_gen(owner_api: Api, app: Any, db: Any,
                                                             fast_options: None) -> None:
    org = await org_id(db)
    # Chưa ghim gì ⇒ J3 vẫn đẩy câu số liệu ngắn sang tầng Nhanh như cũ.
    r0 = TempRouter()
    await turn(owner_api, app, r0, Q)
    assert r0.calls[0]["tier"] == "fast"
    # Sếp ghim model cho core.gen (dòng gán) ⇒ J3 không ép tầng, model Sếp chọn chạy đầu chuỗi.
    _, ids = await add_source(db, org, API, "alpha", 1, ["gemini-2.5-flash", "gemini-2.5-pro"])
    await bind(db, org, "core.gen", ids["gemini-2.5-pro"])
    r1 = TempRouter()
    await turn(owner_api, app, r1, Q)
    assert r1.calls[0]["tier"] is None
    assert (await decide_rows())[-1].detail["route"] == "default"
    # Lựa chọn tường minh trong khung chat vẫn thắng dòng gán (đúng như trước).
    r2 = TempRouter()
    await turn(owner_api, app, r2, Q, choice={"tier": "fast"})
    assert r2.calls[0]["tier"] == "fast"


async def test_j3_fast_tier_not_forced_when_source_default_is_pinned(owner_api: Api, app: Any, db: Any,
                                                                     fast_options: None) -> None:
    org = await org_id(db)
    await add_source(db, org, API, "alpha", 1, ["gemini-2.5-flash", "gemini-2.5-pro"], default="gemini-2.5-pro")
    r = TempRouter()
    await turn(owner_api, app, r, Q)
    assert r.calls[0]["tier"] is None                    # "Dùng model này" cũng là ghim của Sếp


# ─── (d) nhiệt độ hồ sơ vào lượt gọi ───────────────────────────────────────────

async def test_gen_turn_sends_profile_temperature(owner_api: Api, app: Any, fast_options: None) -> None:
    r = TempRouter()
    await turn(owner_api, app, r, "Xin chào")
    assert r.calls[0]["temperature"] == profiles.PROFILES[profiles.ROLE_GEN][profiles.KIND_API].temperature == 0.3
    # Bộ định tuyến giả của test cũ KHÔNG nhận `temperature` ⇒ engine không truyền (không TypeError).
    fake = FakeRouter([TURN_DONE])
    t = await turn(owner_api, app, fake, "Xin chào")
    assert t["status"] == "done"


# ─── (e) Kiểm tra xanh lưu sẵn các model agy liệt kê ────────────────────────────

LISTED = {"ok": True, "probe_model": "gemini-3.8-flash", "probe_effort": "high", "models_source": "cli",
          "model_groups": [
              {"label": "Gemini", "models": [
                  {"id": "gemini-3.8-flash", "source": "cli", "default_effort": None},
                  {"id": "gemini-3.1-pro", "source": "cli", "default_effort": None}]},
              {"label": "Claude (qua Antigravity)", "models": [
                  {"id": "claude-sonnet-4-6-thinking", "source": "cli", "default_effort": None}]},
              {"label": "Gemini", "models": [
                  {"id": "gemini-9-nhap", "source": "catalog", "default_effort": None}]}]}


async def _names(db: Any, pid: uuid.UUID) -> list[tuple[str, str | None, bool]]:
    await db.rollback()
    return [tuple(r) for r in (await db.execute(text("""SELECT model_name, effort, is_default FROM agent.models
                                                        WHERE provider_id = :p ORDER BY id"""), {"p": pid})).all()]


async def test_green_probe_adopts_every_listed_agy_model_once(app: Any, db: Any) -> None:
    org = await org_id(db)
    pid, _ = await add_source(db, org, AGY, "Antigravity", 1, [])
    assert await adopt_probed_model(db, org, pid, "Antigravity", LISTED) is True
    await db.commit()
    got = await _names(db, pid)
    # Model thăm dò đứng đầu (giữ mức đã chạy được); model CLI liệt kê thêm sau; mục dự phòng (catalog) KHÔNG thêm.
    assert got == [("gemini-3.8-flash", "high", False), ("gemini-3.1-pro", None, False),
                   ("claude-sonnet-4-6-thinking", None, False)]
    # Nguồn đã có model (Sếp đã chọn / đã xoá bớt) ⇒ không thêm lại, không đổi gì.
    await db.execute(text("DELETE FROM agent.models WHERE model_name = 'gemini-3.1-pro'"))
    await db.commit()
    assert await adopt_probed_model(db, org, pid, "Antigravity", LISTED) is False
    await db.commit()
    assert [n for n, _e, _d in await _names(db, pid)] == ["gemini-3.8-flash", "claude-sonnet-4-6-thinking"]


async def test_probe_does_not_adopt_catalog_models_for_other_kinds(app: Any, db: Any) -> None:
    org = await org_id(db)
    pid, _ = await add_source(db, org, CLAUDE, "Claude Code", 1, [])
    res = {"ok": True, "probe_model": "sonnet", "probe_effort": None,
           "model_groups": [{"label": "Claude", "models": [{"id": "haiku", "source": "catalog"},
                                                             {"id": "opus", "source": "catalog"}]}]}
    assert await adopt_probed_model(db, org, pid, "Claude Code", res) is True
    await db.commit()
    assert [n for n, _e, _d in await _names(db, pid)] == ["sonnet"]


async def test_choice_options_serve_all_tiers_after_adopting(app: Any, db: Any) -> None:
    """F132: sau Kiểm tra xanh "Kỹ hơn" có nguồn phục vụ (agy mời cả pro); "Nhanh" chỉ khi có model tầng nhanh."""
    org = await org_id(db)
    pid, _ = await add_source(db, org, AGY, "Antigravity", 1, [])
    await adopt_probed_model(db, org, pid, "Antigravity", LISTED)
    await db.commit()
    async with sessionmaker()() as s:
        out = await profiles.choice_options(s, org)
    avail = {r["tier"]: r["available"] for r in out["tiers"]}
    assert avail == {"auto": True, "fast": False, "balanced": True, "deep": True}


# ─── (g) câu lỗi Gen khi thiếu khoá ────────────────────────────────────────────

async def test_missing_key_error_tells_to_add_key_not_retry(owner_api: Api, app: Any, fast_options: None) -> None:
    class NoKey(TempRouter):
        async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Message], **kw: Any
                           ) -> Routed:
            raise ModelUnavailable(["alpha: không còn khoá khả dụng"], no_chain=False)

    t = await turn(owner_api, app, NoKey(), "Xin chào")
    text_out = " ".join(s["step"]["text"] for s in t["steps"] if s["step"]["kind"] == "say")
    assert "khoá" in text_out and "thử lại sau" not in text_out
    # Lỗi tạm thật (mạng, hạn mức) vẫn nói "thử lại sau ít phút".

    class Down(TempRouter):
        async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Message], **kw: Any
                           ) -> Routed:
            raise ModelUnavailable(["alpha: 429"], no_chain=False)

    t2 = await turn(owner_api, app, Down(), "Xin chào lần nữa")
    assert "thử lại sau ít phút" in " ".join(s["step"]["text"] for s in t2["steps"] if s["step"]["kind"] == "say")
