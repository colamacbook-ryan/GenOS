# ruff: noqa: F811 — fixture `clis` nhập từ test_cli_models_v0131 (tham số test trùng tên là cách pytest dùng fixture)
"""Gói H2 (v0.1.63) — "vai nào chạy agy ở đâu" (quyết định Sếp 16/10/2026).

- core (core.gen của Owner, core.briefing, agent nội bộ không gắn kênh / nhóm / người) ⇒ agy TRONG TIẾN TRÌNH;
- lớp ngoại giao (core.reply, agent gắn kênh / nhóm / người), sàng lọc tin người lạ (core.refinery), Gen của nhân viên,
  lượt thử agent ⇒ agy QUA HỘP RIÊNG (`AgyBoxClient`, hợp đồng `docs/api/agybox-protocol.md`);
- hộp chưa cài / tắt ⇒ các vai qua hộp chuyển nguồn AI kế, KHÔNG BAO GIỜ rơi về agy trong tiến trình kèm lý do thật;
- MỘT điểm quyết định: `placement` (luật chỗ chạy, thuần) ⊕ trạng thái hộp = `agy_policy.decide`.

Hộp GIẢ (fixture `box`, `tests/box_fakes.py`) điều khiển được trạng thái `ok` | `down` | `not_installed`.
"""

import ast
import inspect
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from gh.agybox.client import AgyBoxClient, BoxUnavailable
from gh.db import sessionmaker
from gh.defaults import profiles
from gh.providers import agy_policy, placement
from gh.providers.clients import AgyClient, AuthFailed, Completion, Message
from gh.providers.router import ModelRouter, ModelUnavailable, breaker_key, fail_key
from tests.box_fakes import BoxCtl
from tests.phase2 import org_id
from tests.test_agy_mo_thang_hop_dong import _only_agy
from tests.test_cli_models_v0131 import clis  # noqa: F401 — fixture dùng chung
from tests.test_model_router import OK, provider, transport
from tests.test_p4_agents import _seed_agent

MSGS = [Message("user", "nội dung của khách")]
GEN, BRIEFING, REPLY, REFINERY = "core.gen", "core.briefing", "core.reply", "core.refinery"
STATES = ("ok", "down", "not_installed")
REASON_OF = {"down": agy_policy.REASON_BOX_DOWN, "not_installed": agy_policy.REASON_BOX_NOT_INSTALLED}
API_ANSWER = '{"ok": true}'
REPO = Path(__file__).resolve().parents[3]


# ─── A. luật chỗ chạy (thuần) ────────────────────────────────────────────

PLACEMENT_CASES: list[tuple[str, Any, bool, str | None, str]] = [
    # (vai, agent, owner, purpose, nơi chạy)
    (GEN, None, True, "gen.turn", "inproc"),
    (GEN, None, False, "gen.turn", "box"),                      # Gen của nhân viên
    # Review v0.1.63 (P2-2/P2-3): Bản tin đưa tên hiển thị khách, tiêu đề việc, lý do giữ nháp vào prompt ⇒ qua hộp.
    (BRIEFING, None, True, "gen.briefing", "box"),
    (BRIEFING, None, False, "gen.briefing", "box"),             # chỉ core.gen phân biệt người hỏi
    (REPLY, None, True, "draft_translate", "box"),
    (REPLY, None, False, "draft_regenerate", "box"),
    (REFINERY, None, True, "refinery", "box"),                  # sàng lọc tin người lạ
    (REFINERY, None, False, "rule_test", "box"),
    ("agent:7", None, True, "agent.task", "inproc"),           # agent nội bộ
    ("agent:7", False, True, "agent.task", "inproc"),
    ("agent:7", placement.AgentRef(), True, "agent.task", "inproc"),
    ("agent:7", True, True, "agent.task", "box"),              # agent ngoại giao
    ("agent:7", placement.AgentRef(diplomatic=True), True, "agent.task", "box"),
    ("agent:7", False, True, "setup_agent_try", "box"),         # thử agent luôn qua hộp
    # Review v0.1.63 (P3): trực việc (`duty_decide`) đọc tin khách ⇒ luôn qua hộp, kể cả agent chưa "ngoại giao".
    ("agent:7", False, True, "duty_decide", "box"),
    ("agent:7", None, False, "duty_decide", "box"),
    ("agent:7", False, True, "draft_translate", "box"),         # nháp có agent_id đã bị gỡ khỏi nhóm: vẫn là tin khách
    ("agent:7", None, False, "draft_regenerate", "box"),
    ("agent:7", "agent:7", True, "agent.task", "inproc"),      # B2 truyền khoá đầy đủ: không đủ tin ⇒ nội bộ
    ("agent:*", "agent:7", True, None, "inproc"),               # B2: role = profiles.role_of(key)
    (GEN, None, True, "setup_agent_try", "box"),
    ("core.embedding", None, True, None, "box"),                # khoá lạ: đóng an toàn
    ("core.something_new", None, True, "x", "box"),
]


@pytest.mark.parametrize("role,agent,owner,purpose,expected", PLACEMENT_CASES)
def test_placement_matrix(role: str, agent: Any, owner: bool, purpose: str | None, expected: str) -> None:
    assert placement.placement(role, agent, owner=owner, purpose=purpose) == expected
    assert placement.box_required(role, agent, owner=owner, purpose=purpose) is (expected == "box")


def test_box_required_matches_the_b2_call_shape() -> None:
    """B2 (`readiness.SoftBoxGate`) gọi `box_required(profiles.role_of(key), key)` — chỉ hai tham số vị trí."""
    key = f"agent:{uuid.uuid4()}"
    for k, expected in ((GEN, False), (BRIEFING, True), (REPLY, True), (REFINERY, True), (key, False)):
        role = profiles.role_of(k)
        assert role is not None
        assert placement.box_required(role, k if k.startswith("agent:") else None) is expected, k
    params = list(inspect.signature(placement.box_required).parameters)
    assert params[:2] == ["role", "agent_key"]


def test_placement_table_is_the_single_source_and_read_only() -> None:
    assert dict(placement.AGY_PLACEMENT) == {GEN: "inproc", BRIEFING: "box", "agent:*": "inproc",
                                            REPLY: "box", REFINERY: "box",
                                            "core.owner_reply": "box", "core.support": "box"}
    with pytest.raises(TypeError):
        placement.AGY_PLACEMENT["core.reply"] = "inproc"  # type: ignore[index]
    assert placement.BOX_PURPOSES == {"setup_agent_try", "draft_translate", "draft_regenerate", "duty_decide",
                                      "owner_reply", "support_classify", "support_reply"}
    # Hai tên chỗ chạy là hằng của hợp đồng — B2 so khớp theo chuỗi.
    assert (placement.INPROC, placement.BOX) == ("inproc", "box")


# ─── B. một điểm quyết định: placement ⊕ trạng thái hộp ──────────────────

@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("owner", [True, False])
@pytest.mark.parametrize("role,agent,purpose", [
    (GEN, None, "gen.turn"), (BRIEFING, None, "gen.briefing"), (REPLY, None, "draft_translate"),
    (REFINERY, None, "refinery"), ("agent:7", False, "agent.task"), ("agent:7", True, "agent.task"),
    ("agent:7", False, "setup_agent_try"), ("agent:7", False, "duty_decide"),
])
def test_decide_is_placement_plus_box_state(role: str, agent: bool, purpose: str, owner: bool, state: str) -> None:
    v = agy_policy.decide(role, owner=owner, diplomatic=bool(agent), box=state, purpose=purpose)
    where = placement.placement(role, agent, owner=owner, purpose=purpose)
    assert v.where == where                                   # không có hai nguồn sự thật về chỗ chạy
    if where == "inproc":
        assert v.allowed and v.reason is None and v.code is None   # hộp ra sao cũng không ảnh hưởng vai core
    elif state == "ok":
        assert v.allowed and v.reason is None
    else:
        assert not v.allowed and v.reason == REASON_OF[state] and v.code == f"box_{state}"


def test_decide_default_is_closed_and_reasons_are_honest_vietnamese() -> None:
    assert not agy_policy.decide(REPLY).allowed                          # không nói trạng thái hộp ⇒ đóng an toàn
    assert agy_policy.decide(GEN).allowed
    for reason in REASON_OF.values():
        assert "Hộp riêng Antigravity" in reason and "nguồn AI kế" in reason
        assert "chờ hộp riêng" not in reason and "bản sau" not in reason
    assert "chưa chạy" in agy_policy.REASON_BOX_DOWN and "chưa được cài" in agy_policy.REASON_BOX_NOT_INSTALLED


@pytest.mark.parametrize("state", STATES)
def test_box_required_agrees_with_every_decision(state: str) -> None:
    """Hợp đồng cho B2: `box_required` ⇔ `decide(...).where == "box"`; B2 ghép với `box_status()` ⇒ `box_down`."""
    for role, agent, owner, purpose, _expected in PLACEMENT_CASES:
        dip = agent if isinstance(agent, bool) else bool(getattr(agent, "diplomatic", False))
        v = agy_policy.decide(role, owner=owner, diplomatic=dip, box=state, purpose=purpose)
        assert placement.box_required(role, agent, owner=owner, purpose=purpose) is (v.where == "box")


# ─── C. ModelRouter.generate: vai × hộp × người hỏi ⇒ inproc / box / nguồn kế ─────

class FakeInproc:
    """AgyClient giả TRONG TIẾN TRÌNH: ghi lượt gọi."""

    kind = "antigravity_cli"
    timeout = 300.0

    def __init__(self, calls: list[str]):
        self.calls = calls

    async def generate(self, model: str, messages: list[Message], **kw: Any) -> Completion:
        self.calls.append(model)
        return Completion("trả lời trong tiến trình", 1, 1, {})


async def _fixture_world(db, redis, box: BoxCtl) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    """Hai nguồn: Antigravity CLI (hạng 1, model gemini-3.1-pro) và API "alpha" (hạng 2). Router dùng client giả."""
    org = await org_id(db)
    agy = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, failover_rank, auth_state)
                                    VALUES (:o, 'antigravity_cli', 'Antigravity CLI', 1, 'ok') RETURNING id"""),
                            {"o": org})).scalar_one()
    await db.execute(text("""INSERT INTO agent.models (provider_id, model_name, is_default)
                             VALUES (:p, 'gemini-3.1-pro', true)"""), {"p": agy})
    await db.commit()
    await provider(db, org, "alpha", 2, ["sk-alpha-aaaa"])
    inproc_calls: list[str] = []
    t = transport(lambda host, key: (200, OK))
    router = ModelRouter(sessionmaker(), redis, transport=t, cli_factory=lambda: FakeInproc(inproc_calls),
                         box_factory=box.factory)
    return SimpleNamespace(org=org, agy=agy, router=router, inproc=inproc_calls, api=t.seen)  # type: ignore[attr-defined]


async def _diplomatic_agent(db, org: uuid.UUID) -> uuid.UUID:  # type: ignore[no-untyped-def]
    aid = await _seed_agent(db, org, name="Trợ lý khách")
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o ORDER BY created_at LIMIT 1"),
                           {"o": org})).scalar_one()
    await db.execute(text("INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id) VALUES (:a, :c, NULL)"),
                     {"a": aid, "c": ch})
    await db.commit()
    return aid


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("owner", [True, False])
@pytest.mark.parametrize("role,purpose", [
    (GEN, "gen.turn"), (BRIEFING, "gen.briefing"), (REPLY, "draft_translate"), (REFINERY, "refinery"),
    ("internal", "agent.task"), ("diplomatic", "agent.task"), ("internal", "setup_agent_try"),
    ("internal", "duty_decide"),
    ("internal", "draft_translate"), ("internal", "draft_regenerate"),
])
async def test_generate_matrix_role_by_box_by_asker(app, db, redis, boxctl, role, purpose, owner, state) -> None:  # type: ignore[no-untyped-def]
    ctl = boxctl
    ctl.state = state
    w = await _fixture_world(db, redis, ctl)
    key = role
    if role == "internal":
        key = f"agent:{await _seed_agent(db, w.org)}"
    elif role == "diplomatic":
        key = f"agent:{await _diplomatic_agent(db, w.org)}"
    # Bảng kỳ vọng viết tay (không gọi `placement`): core.gen Owner / agent nội bộ ⇒ trong tiến trình; Bản tin qua hộp.
    inproc_case = ((role == GEN and owner and purpose == "gen.turn")
                   or (role == "internal" and purpose == "agent.task"))
    expected = "inproc" if inproc_case else "box"

    out = await w.router.generate(w.org, agent_key=key, purpose=purpose, messages=MSGS, json_mode=False, owner=owner)

    if expected == "inproc":
        assert out.provider == "Antigravity CLI" and out.text == "trả lời trong tiến trình"
        assert w.inproc == ["gemini-3.1-pro"] and ctl.calls == []          # hộp ra sao cũng không dính vai core
    elif state == "ok":
        assert out.provider == "Antigravity CLI" and out.text == "trả lời từ hộp"
        assert len(ctl.calls) == 1 and w.inproc == [] and not w.api         # đúng client: hộp, không inproc
        assert not out.attempts
    else:
        assert out.provider == "alpha" and out.text == API_ANSWER             # nguồn kế
        assert w.inproc == [] and ctl.calls == []                             # KHÔNG rơi về agy trong tiến trình
        assert REASON_OF[state] in out.attempts                               # lý do tiếng Việt thật


async def test_generate_box_roles_never_reach_inproc_even_with_a_legacy_binding(app, db, redis, boxctl) -> None:  # type: ignore[no-untyped-def]
    """Bản cài cũ gán agy cho core.reply / core.refinery: hộp tắt ⇒ không chạy agy; hết nguồn ⇒ lỗi."""
    ctl = boxctl
    ctl.state = "down"
    w = await _fixture_world(db, redis, ctl)
    mid = (await db.execute(text("SELECT id FROM agent.models WHERE provider_id = :p"), {"p": w.agy})).scalar_one()
    for key in (REPLY, REFINERY):
        await db.execute(text("INSERT INTO agent.bindings (org_id, agent_key, model_id, context_tokens) "
                              "VALUES (:o, :k, :m, 4000)"), {"o": w.org, "k": key, "m": mid})
    await db.commit()
    for key in (REPLY, REFINERY):
        out = await w.router.generate(w.org, agent_key=key, purpose="p", messages=MSGS, json_mode=False)
        assert out.provider == "alpha" and agy_policy.REASON_BOX_DOWN in out.attempts, key
    assert w.inproc == [] and ctl.calls == []
    await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'antigravity_cli') WHERE org_id = :o"),
                     {"o": w.org})
    await db.commit()
    with pytest.raises(ModelUnavailable) as ei:
        await w.router.generate(w.org, agent_key=REPLY, purpose="p", messages=MSGS, json_mode=False)
    assert agy_policy.REASON_BOX_DOWN in ei.value.reasons and ei.value.no_chain is True
    assert w.inproc == []


async def test_generate_agent_changes_place_when_channels_are_attached(app, db, redis, boxctl) -> None:  # type: ignore[no-untyped-def]
    ctl = boxctl
    w = await _fixture_world(db, redis, ctl)
    aid = await _seed_agent(db, w.org)
    key = f"agent:{aid}"
    out = await w.router.generate(w.org, agent_key=key, purpose="agent.task", messages=MSGS, json_mode=False)
    assert out.text == "trả lời trong tiến trình" and not ctl.calls                 # chưa gắn kênh ⇒ nội bộ ⇒ inproc
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o ORDER BY created_at LIMIT 1"),
                           {"o": w.org})).scalar_one()
    await db.execute(text("INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id) VALUES (:a, :c, NULL)"),
                     {"a": aid, "c": ch})
    await db.commit()
    out = await w.router.generate(w.org, agent_key=key, purpose="agent.task", messages=MSGS, json_mode=False)
    assert out.text == "trả lời từ hộp" and len(ctl.calls) == 1 and len(w.inproc) == 1   # gắn kênh ⇒ ngoại giao ⇒ hộp
    ctl.state = "down"
    out = await w.router.generate(w.org, agent_key=key, purpose="agent.task", messages=MSGS, json_mode=False)
    assert out.provider == "alpha" and len(ctl.calls) == 1 and len(w.inproc) == 1        # hộp tắt ⇒ nguồn kế


# ─── D. lỗi của hộp không kéo agy trong tiến trình ───────────────────────

async def test_box_failures_have_their_own_breaker_and_do_not_touch_inproc(app, db, redis, boxctl) -> None:  # type: ignore[no-untyped-def]
    ctl = boxctl
    ctl.fail = BoxUnavailable("Hộp riêng Antigravity chưa chạy")
    w = await _fixture_world(db, redis, ctl)
    for _ in range(3):
        out = await w.router.generate(w.org, agent_key=REPLY, purpose="p", messages=MSGS, json_mode=False)
        assert out.provider == "alpha"                                       # hộp lỗi ⇒ nguồn kế, không agy inproc
    assert await redis.exists(breaker_key(w.agy, "box")) and not await redis.exists(breaker_key(w.agy))
    assert await redis.exists(fail_key(w.agy, "box")) == 0                  # đã mở mạch và xoá bộ đếm
    n = len(ctl.calls)
    await w.router.generate(w.org, agent_key=REPLY, purpose="p", messages=MSGS, json_mode=False)
    assert len(ctl.calls) == n                                               # mạch hộp mở: không dội hộp thêm
    out = await w.router.generate(w.org, agent_key=GEN, purpose="gen.turn", messages=MSGS, json_mode=False)
    assert out.provider == "Antigravity CLI" and out.text == "trả lời trong tiến trình"   # vai core vẫn chạy
    assert w.inproc == ["gemini-3.1-pro"]


async def test_box_login_failure_does_not_expire_the_inproc_login(app, db, redis, boxctl) -> None:  # type: ignore[no-untyped-def]
    ctl = boxctl
    ctl.fail = AuthFailed("agy trong hộp chưa đăng nhập")
    w = await _fixture_world(db, redis, ctl)
    out = await w.router.generate(w.org, agent_key=REFINERY, purpose="refinery", messages=MSGS, json_mode=False)
    assert out.provider == "alpha"
    assert any("xác thực lỗi" in a for a in out.attempts)
    state = (await db.execute(text("SELECT auth_state FROM agent.providers WHERE id = :i"), {"i": w.agy})).scalar_one()
    assert state == "ok"                                                     # nhãn phiên agy trong tiến trình không đổi


async def test_box_roles_do_not_trigger_the_inproc_self_heal(app, db, redis, boxctl, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Chuỗi rỗng + vai qua hộp ⇒ KHÔNG gọi thử agy trong tiến trình (`_heal_agy`); vai core thì có."""
    ctl = boxctl
    w = await _fixture_world(db, redis, ctl)
    await db.execute(text("DELETE FROM agent.models WHERE provider_id = :p"), {"p": w.agy})
    await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'antigravity_cli') WHERE org_id = :o"),
                     {"o": w.org})
    await db.commit()
    healed: list[str] = []

    async def fake_heal(org: uuid.UUID) -> bool:
        healed.append("x")
        return False

    monkeypatch.setattr(w.router, "_heal_agy", fake_heal)
    for key in (REPLY, REFINERY, BRIEFING):
        with pytest.raises(ModelUnavailable):
            await w.router.generate(w.org, agent_key=key, purpose="p", messages=MSGS, json_mode=False)
    assert healed == []
    with pytest.raises(ModelUnavailable):
        await w.router.generate(w.org, agent_key=GEN, purpose="gen.turn", messages=MSGS, json_mode=False)
    assert healed == ["x"]


# ─── E. không còn đường nào chạy agy trong tiến trình cho vai phải qua hộp (quét mã) ─────

GH = REPO / "apps/api/gh"


def _router_calls(tree: ast.AST, names: set[str]) -> list[ast.Call]:
    """Lời gọi `<...router|self>._client/_complete(...)` (không lẫn `_client` của module khác)."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in names and isinstance(n.func.value, ast.Name | ast.Attribute)
            and ("router" in ast.unparse(n.func.value) or ast.unparse(n.func.value) == "self")]


def _calls_named(tree: ast.AST, names: set[str]) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr in names)
                 or (isinstance(n.func, ast.Name) and n.func.id in names))]


def test_scan_every_client_creation_names_its_place() -> None:
    sig = inspect.signature(ModelRouter._client)
    where = sig.parameters["where"]
    assert where.kind is inspect.Parameter.KEYWORD_ONLY and where.default is sig.empty
    sig = inspect.signature(ModelRouter._complete)
    assert sig.parameters["where"].default is sig.empty
    offenders = []
    for py in sorted(GH.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        if py.name != "router.py" and "router" not in py.read_text(encoding="utf-8"):
            continue
        for call in _router_calls(tree, {"_client", "_complete"}):
            if not any(k.arg == "where" for k in call.keywords):
                offenders.append(f"{py.relative_to(REPO)}:{call.lineno}")
    assert not offenders, offenders


def test_scan_inproc_agy_is_built_only_where_the_policy_allows() -> None:
    """`AgyClient(` chỉ ở: định nghĩa, router (nhà máy mặc định), hộp, canary. `cli_factory` chỉ ở router."""
    allowed_ctor = {"gh/providers/clients.py", "gh/providers/router.py", "gh/providers/agy_canary.py",
                    "gh/agybox/server.py"}
    ctor, factory = set(), set()
    for py in sorted(GH.rglob("*.py")):
        rel = str(py.relative_to(REPO / "apps/api"))
        tree = ast.parse(py.read_text(encoding="utf-8"))
        if _calls_named(tree, {"AgyClient"}):
            ctor.add(rel)
        if any(isinstance(n, ast.Attribute) and n.attr == "cli_factory" for n in ast.walk(tree)):
            factory.add(rel)
    assert ctor <= allowed_ctor, ctor - allowed_ctor
    assert factory <= {"gh/providers/router.py"}, factory


def test_scan_router_reaches_inproc_only_through_the_where_branch() -> None:
    src = (GH / "providers/router.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # `self.cli_factory()` gọi đúng MỘT chỗ (`_client`, nhánh `where == INPROC`); `self.box_factory()` ở nhánh BOX.
    users = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "self" and n.func.attr in {"cli_factory"}]
    assert len(users) == 1
    client_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_client")
    body = ast.get_source_segment(src, client_fn) or ""
    assert re.search(r"if where == placement\.BOX:\s+client = self\.box_factory\(\)", body)
    assert re.search(r"elif where == placement\.INPROC:\s+client = self\.cli_factory\(\)", body)
    # Đường gọi model của `generate` truyền chỗ chạy theo quyết định, không hằng INPROC.
    gen_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "generate")
    gen_src = ast.get_source_segment(src, gen_fn) or ""
    assert "where=via" in gen_src and "self.cli_factory" not in gen_src


def test_scan_no_old_wording_left() -> None:
    bad = []
    pat = re.compile(r"chờ hộp riêng|agy mở cho (?:MỌI|mọi) vai|CLI mở cho MỌI vai|REASON_DIPLOMATIC|REASON_STAFF_GEN")
    for root in ("apps/api/gh", "apps/web/src", "packages/contracts/src"):
        for f in sorted((REPO / root).rglob("*")):
            if f.suffix in {".py", ".ts", ".tsx"} and f.is_file() and pat.search(f.read_text(encoding="utf-8")):
                bad.append(str(f.relative_to(REPO)))
    assert not bad, bad


# ─── F. set_binding, danh sách gán model, hồ sơ chuẩn, ô chọn model theo hộp ─────

async def test_set_binding_follows_the_box_state(owner_api, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    org, mid = await _only_agy(owner_api)
    inner = await _seed_agent(db, org, name="Trợ lý nội bộ")
    ext = await _diplomatic_agent(db, org)
    box_keys = (REPLY, REFINERY, BRIEFING, f"agent:{ext}")
    inproc_keys = (GEN, f"agent:{inner}")
    for state in ("down", "not_installed"):
        box.state = state
        for key in box_keys:
            r = await owner_api.send("PUT", f"/agents/bindings/{key}", {"model_id": str(mid)})
            assert r.status_code == 422 and REASON_OF[state] in r.text, (state, key, r.text)
        listing = {i["agent_key"]: i for i in (await owner_api.get("/agents/bindings")).json()["items"]}
        for key in box_keys:
            assert listing[key]["agy_blocked_reason"] == REASON_OF[state] and listing[key]["standard"] is None, key
            assert listing[key]["standard_reason"] == REASON_OF[state], key
        for key in inproc_keys:
            assert listing[key]["agy_blocked_reason"] is None, key
            assert listing[key]["standard"]["provider_name"] == "Antigravity CLI", key
    async with sessionmaker()() as s:
        n = (await s.execute(text("SELECT count(*) FROM agent.bindings WHERE org_id = :o"), {"o": org})).scalar_one()
        assert n == 0
    for key in inproc_keys:                                                  # hộp tắt không cản vai core
        assert (await owner_api.send("PUT", f"/agents/bindings/{key}", {"model_id": str(mid)})).status_code == 200, key
    box.state = "ok"
    for key in box_keys:
        r = await owner_api.send("PUT", f"/agents/bindings/{key}", {"model_id": str(mid)})
        assert r.status_code == 200, (key, r.text)
    listing = {i["agent_key"]: i for i in (await owner_api.get("/agents/bindings")).json()["items"]}
    assert all(i["agy_blocked_reason"] is None for i in listing.values())


async def test_standard_profile_and_choice_options_follow_the_box(owner_api, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    org, _mid = await _only_agy(owner_api)
    async with sessionmaker()() as s:
        box.state = "ok"
        std = await profiles.standard_for(s, org, [GEN, REPLY, REFINERY, BRIEFING])
        assert all((c or {}).get("provider_name") == "Antigravity CLI" for c in std.values())  # box ok: mọi vai có agy
        assert any(t["available"] for t in (await profiles.choice_options(s, org, owner=False))["tiers"])
        box.state = "down"
        std, why = (await profiles.standard_with_reasons(s, org, [GEN, REPLY, REFINERY, BRIEFING]))
        assert std[GEN] and std[BRIEFING] is None and std[REPLY] is None and std[REFINERY] is None
        assert why[REPLY] == why[REFINERY] == why[BRIEFING] == agy_policy.REASON_BOX_DOWN
        owner_tiers = (await profiles.choice_options(s, org, owner=True))["tiers"]
        staff_tiers = (await profiles.choice_options(s, org, owner=False))["tiers"]
        assert any(t["available"] for t in owner_tiers) and not any(t["available"] for t in staff_tiers)


# ─── G. hợp đồng với AgyBoxClient thật (cùng giao diện để router hoán đổi) ────────

def test_box_client_has_the_agy_client_interface_the_router_needs() -> None:
    for name in ("generate", "discover_models", "list_models", "diagnose"):
        assert callable(getattr(AgyBoxClient, name)) and callable(getattr(AgyClient, name))
    assert AgyBoxClient.kind == AgyClient.kind == "antigravity_cli"
    assert AgyBoxClient.from_env.__name__ == "from_env"
    # Mặc định của router: hộp chưa cài (không có GH_AGYBOX_URL) ⇒ nhà máy trả None, không ném.
    r = ModelRouter(None, None)  # type: ignore[arg-type]
    assert r.box_factory() is None
