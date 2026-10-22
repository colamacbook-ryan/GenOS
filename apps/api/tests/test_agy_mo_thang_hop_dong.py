# ruff: noqa: F811 — fixture `clis` nhập từ test_cli_models_v0131 (tham số test trùng tên là cách pytest dùng fixture)
"""Gói B1 (kiểm kê 16/10/2026, F001 + F087) — HỢP ĐỒNG "Antigravity CLI (agy) mở cho MỌI vai".

Quyết định của Sếp 16/10/2026: hệ thống chạy chủ đạo bằng agy. Test này khoá ý đó ở mức hành vi nhìn từ ngoài, KHÔNG
nhập tên hằng / hàm nội bộ của luật cũ (F-22 "agy chỉ cho Gen") — nên trên bản còn luật cũ nó đỏ vì HÀNH VI sai
chứ không vì thiếu tên, và ai khôi phục luật cũ dưới bất kỳ tên nào cũng bị bắt. Phủ các vai: core.gen,
core.refinery, core.briefing và agent:<id> NỘI BỘ. Khi Sếp CHỈ có nguồn agy (không khoá API, không Claude Code CLI):

  1. tầng "Chuẩn" (standard) của các vai đó có model agy và không có lý do từ chối;
  2. gán model agy cho các khoá đó, kể cả agent:<id> nội bộ, trả 200 (trước đây 409 AGY_OWNER_GEN_ONLY);
  3. `router.generate` dùng agy cho việc nền (sàng lọc, trực việc, Bản tin) mà bên gọi không phải xin cờ đặc biệt;
  4. không còn câu "chỉ dùng cho Gen" ở bất kỳ chuỗi nào hiển thị cho người dùng (API, lượt Gen, cảnh báo, giao diện).

PHÂN LỚP (gói G + H2, Sếp chốt 16/10/2026): vai có người ngoài (core.reply, core.refinery, agent gắn kênh / nhóm /
người, Gen của nhân viên) chạy agy QUA HỘP RIÊNG — hợp đồng này chạy với hộp `ok` (fixture `box`) nên MỌI vai vẫn có
model agy và gọi được (hồi quy B1). Khi hộp tắt thì các vai đó chỉ còn đúng một câu trung thực, hiển thị được, KHÔNG làm
sống lại câu "chỉ dùng cho Gen" (test_diplomatic_exception_is_one_honest_sentence); ma trận chi tiết ở
test_agy_phan_lop.py / test_agy_placement_v0163.py.

Lớp cô lập mỗi lượt của AgyClient KHÔNG đổi (test_agy_isolation_v0138); Claude Code CLI vẫn không chạy việc nền khi Sếp
chưa cho phép (F-86) — nằm ở test_agy_all_agents.py, không lặp lại ở đây.
"""

import ast
import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker, sessionmaker
from gh.gen import store
from gh.providers.clients import Message
from gh.providers.router import ModelUnavailable
from tests.conftest import Api
from tests.test_cli_models_v0131 import (
    _login,
    _provider,
    clis,  # noqa: F401 — fixture dùng chung
)
from tests.test_p4_agents import _seed_agent
from tests.test_rbac_api import login_as

MSGS = [Message("user", "nội dung của khách")]
# (khoá vai, purpose của một việc thật chạy dưới vai đó). `{id}` = id agent thật được tạo trong test.
ROLE_CASES = [
    ("core.gen", "gen.turn"),
    ("core.refinery", "refinery.extract"),
    ("core.briefing", "gen.briefing"),
    ("agent:{id}", "duty_decide"),
    ("agent:{id}", "setup_agent_try"),
]
#: Lớp core chạy agy trong tiến trình; `core.refinery` và `core.reply` (có người ngoài) chạy qua hộp (gói H2).
# v0.1.72: + core.owner_reply, core.support (hai khoá chạy trong hộp)
CORE_KEYS = ("core.gen", "core.refinery", "core.briefing", "core.owner_reply", "core.support")
DIPLOMATIC_KEY = "core.reply"
DIPLOMATIC_REASON = "Hộp riêng Antigravity chưa chạy"       # tiền tố câu của agy_policy.REASON_BOX_DOWN
OLD_RULE = re.compile(r"chỉ (?:dùng )?cho Gen|AGY_OWNER_GEN_ONLY|Antigravity chỉ", re.IGNORECASE)
REPO = Path(__file__).resolve().parents[3]


def _calls(clis: dict[str, Any]) -> list[dict[str, Any]]:
    log = Path(clis["agy_home"]).parent.parent / "agy-calls.log"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


async def _only_agy(api: Api) -> tuple[uuid.UUID, uuid.UUID]:
    """Sếp chỉ nối Antigravity CLI: đăng nhập (CLI giả), lưu 2 model agy (Pro mặc định + Flash), tắt mọi nguồn khác.
    → (org, id model gemini-3.1-pro)."""
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
    return org, mid


# ─── 1. tầng Chuẩn: mọi vai có model agy ────────────────────────────────

async def test_standard_tier_has_an_agy_model_for_every_role(owner_api, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    org, _mid = await _only_agy(owner_api)
    aid = await _seed_agent(db, org)
    r = await owner_api.get("/agents/bindings")
    assert r.status_code == 200, r.text
    items = {i["agent_key"]: i for i in r.json()["items"]}
    assert set(items) == {*CORE_KEYS, DIPLOMATIC_KEY, f"agent:{aid}"}
    for key, it in items.items():       # hộp chạy ⇒ KỂ CẢ core.reply: mọi vai có model agy, không lý do từ chối
        assert it["source"] == "standard", (key, it)
        assert it["standard_reason"] is None, (key, it["standard_reason"])          # không còn "cần khoá API (…)"
        std = it["standard"]
        assert std is not None and std["provider_name"] == "Antigravity CLI", (key, std)
        assert std["model_name"] in {"gemini-3.1-pro", "gemini-3.8-flash"}, (key, std)
    # Mục "Về mặc định" dùng cùng một sự thật cho từng dòng vai.
    dflt = {i["key"]: i for i in (await owner_api.get("/defaults")).json()["items"]}
    for key in (*CORE_KEYS, DIPLOMATIC_KEY):
        assert dflt[f"binding:{key}"]["default_text"].startswith("Chuẩn: gemini-"), (key, dflt[f"binding:{key}"])
    # Hộp tắt: vai có người ngoài nói thật vì sao (một câu), vai core không đổi.
    box.state = "down"
    items = {i["agent_key"]: i for i in (await owner_api.get("/agents/bindings")).json()["items"]}
    assert items[DIPLOMATIC_KEY]["standard"] is None and DIPLOMATIC_REASON in items[DIPLOMATIC_KEY]["standard_reason"]
    assert items["core.gen"]["standard"]["provider_name"] == "Antigravity CLI"


# ─── 2. set_binding: gán agy cho mọi khoá ⇒ 200 ─────────────────────────

async def test_set_binding_agy_is_200_for_every_role_including_agent(owner_api, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    org, mid = await _only_agy(owner_api)
    aid = await _seed_agent(db, org)
    for key in (*CORE_KEYS, DIPLOMATIC_KEY, f"agent:{aid}"):
        r = await owner_api.send("PUT", f"/agents/bindings/{key}", {"model_id": str(mid)})
        assert r.status_code == 200, (key, r.status_code, r.text)
        b = r.json()["binding"]
        assert b["model_id"] == str(mid) and b["provider_name"] == "Antigravity CLI", (key, b)
        assert "blocked_reason" not in b and "AGY_OWNER_GEN_ONLY" not in r.text, (key, b)
    listing = (await owner_api.get("/agents/bindings")).json()
    for it in listing["items"]:
        assert it["source"] == "custom" and "blocked_reason" not in (it["binding"] or {}), it
    detail = (await owner_api.get(f"/agents/{aid}")).json()
    assert detail["binding"]["model_id"] == str(mid)


# ─── 3. router.generate: agy chạy mọi vai, kể cả việc nền ────────────────

@pytest.mark.parametrize("agent_key,purpose", ROLE_CASES)
async def test_router_generate_runs_agy_for_every_role(owner_api, app, db, clis, box, agent_key, purpose) -> None:  # type: ignore[no-untyped-def]
    org, _mid = await _only_agy(owner_api)
    aid = await _seed_agent(db, org)
    n = len(_calls(clis))
    # Cố ý KHÔNG truyền cờ đặc biệt nào: việc nền mặc định phải tới được agy khi agy là nguồn duy nhất.
    out = await app.state.model_router.generate(org, agent_key=agent_key.format(id=aid), purpose=purpose,
                                                messages=MSGS, json_mode=False)
    assert out.text == "whoami:an@example.vn" and out.provider == "Antigravity CLI"
    assert len(_calls(clis)) == n + 1 and _calls(clis)[-1]["via_stdin"] is True


async def test_diplomatic_exception_is_one_honest_sentence(owner_api, app, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    """core.reply khi hộp tắt: gán agy bị từ chối (422) và lượt gọi không chạy agy — đúng một câu trung thực."""
    box.state = "down"
    org, mid = await _only_agy(owner_api)
    r = await owner_api.send("PUT", f"/agents/bindings/{DIPLOMATIC_KEY}", {"model_id": str(mid)})
    assert r.status_code == 422 and DIPLOMATIC_REASON in r.text and not OLD_RULE.search(r.text), r.text
    n = len(_calls(clis))
    with pytest.raises(ModelUnavailable) as ei:
        await app.state.model_router.generate(org, agent_key=DIPLOMATIC_KEY, purpose="draft_translate",
                                              messages=MSGS, json_mode=False)
    assert any(DIPLOMATIC_REASON in r for r in ei.value.reasons) and len(_calls(clis)) == n


async def test_binding_to_agy_drives_the_background_call(owner_api, app, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    """Gán agy cho vai nền / agent rồi gọi thật: bộ định tuyến dùng đúng model đã gán (không từ chối vì vai)."""
    org, mid = await _only_agy(owner_api)
    aid = await _seed_agent(db, org)
    for key, purpose in (("core.refinery", "refinery.extract"), (f"agent:{aid}", "duty_decide")):
        assert (await owner_api.send("PUT", f"/agents/bindings/{key}", {"model_id": str(mid)})).status_code == 200
        n = len(_calls(clis))
        out = await app.state.model_router.generate(org, agent_key=key, purpose=purpose, messages=MSGS, json_mode=False)
        assert out.model == "gemini-3.1-pro" and len(_calls(clis)) == n + 1, key


# ─── 4. không còn câu "chỉ dùng cho Gen" ở chỗ người dùng nhìn thấy ──────

async def test_no_gen_only_wording_in_any_displayed_text(owner_api, app, client, db, clis, box) -> None:  # type: ignore[no-untyped-def]
    org, _mid = await _only_agy(owner_api)
    aid = await _seed_agent(db, org)
    shown: dict[str, str] = {}
    for path in ("/agents/bindings", "/defaults", "/providers", f"/agents/{aid}"):
        r = await owner_api.get(path)
        assert r.status_code == 200, (path, r.text)
        shown[path] = json.dumps(r.json(), ensure_ascii=False)
    # Việc nền thật chạy xong thì không để lại cảnh báo mang luật cũ.
    for key in ("core.refinery", "core.briefing", f"agent:{aid}"):
        await app.state.model_router.generate(org, agent_key=key, purpose="duty_decide", messages=MSGS, json_mode=False)
    async with admin_sessionmaker()() as adb:
        alerts = (await adb.execute(text("SELECT concat_ws(' ', title, summary, suggested_action) FROM biz.alerts "
                                         "WHERE org_id = :o"), {"o": org})).scalars().all()
    shown["biz.alerts"] = " ".join(alerts)
    # Lượt Gen của nhân viên (trước đây: "…chỉ dùng cho Gen của Sếp (luật an toàn)").
    cfg = await store.get_settings(db, org)
    await store.save_settings(db, org, {**cfg, "enabled": True, "roles": ["owner", "manager"]})
    await db.commit()
    manager = await login_as(client, db, "manager")
    try:
        r = await manager.send("POST", "/gen/turns", {"text": "Hôm nay có gì?", "conversation_id": None,
                                                      "context": {"route": "/overview", "screen_key": "overview"}})
        assert r.status_code == 202, r.text
        tid = r.json()["turn_id"]
        for _ in range(300):
            t = (await manager.get(f"/gen/turns/{tid}")).json()
            if t["status"] != "running":
                break
            await asyncio.sleep(0.05)
        shown["gen.turn(manager)"] = json.dumps(t, ensure_ascii=False)
        assert t["status"] != "running", t
    finally:
        await manager.c.aclose()
    for where, blob in shown.items():
        assert not OLD_RULE.search(blob), (where, OLD_RULE.search(blob).group(0) if OLD_RULE.search(blob) else "")


def _string_literals(py: Path) -> list[str]:
    """Chuỗi hằng trong mã (bỏ docstring + chú thích): chỉ phần có thể thành câu hiển thị cho người dùng."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_ids.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids]


def test_no_gen_only_wording_in_api_source_strings() -> None:
    bad = []
    for py in sorted((REPO / "apps/api/gh").rglob("*.py")):
        bad += [(str(py.relative_to(REPO)), s[:90]) for s in _string_literals(py) if OLD_RULE.search(s)]
    assert not bad, bad


def test_no_gen_only_wording_in_web_and_contract_sources() -> None:
    bad = []
    for root in ("apps/web/src", "packages/contracts/src"):
        for f in sorted((REPO / root).rglob("*")):
            if f.suffix in {".ts", ".tsx"} and f.is_file():
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if line.lstrip().startswith(("//", "*", "/*")):      # chú thích lịch sử được phép
                        continue
                    if OLD_RULE.search(line):
                        bad.append((f"{f.relative_to(REPO)}:{i}", line.strip()[:90]))
    assert not bad, bad
