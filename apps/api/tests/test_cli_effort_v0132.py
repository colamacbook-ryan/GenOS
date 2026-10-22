# ruff: noqa: F811 — fixture `clis` nhập từ test_cli_models_v0131 (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.32 — Boss 01/10: "high" KHÔNG phải tên model mà là MỨC SUY NGHĨ (effort). Trước đây Console lưu và gửi
`--model gemini-3.8-flash-high` (kèm `--effort medium` cố định) → agy 1.2.9 từ chối "CLI không nhận model".

CLI giả mô phỏng đúng cờ của agy 1.2.9 thật (tests/fixtures/fake_agy_multi.py: `--model <gốc> --effort <mức>`, lỗi
"invalid model selection …", "Invalid model %q (available: %s)") và claude 2.1.285 (`--effort`).
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from gh.providers import catalog
from gh.providers.clients import AgyClient, Message, redact, rejection
from tests.conftest import verify_pin
from tests.test_cli_models_v0131 import (
    _login,
    _provider,
    clis,  # noqa: F401 — fixture dùng chung
)

SQL = Path(__file__).resolve().parents[3] / "db" / "sql" / "0023_v0132_model_effort.sql"


def _calls(clis: dict[str, Any]) -> list[dict[str, str]]:
    # v0.1.38 (F-22): CLI giả ghi thêm cwd/via_stdin/argv mỗi lượt — test này chỉ so model + mức suy nghĩ.
    log = Path(clis["agy_home"]).parent.parent / "agy-calls.log"
    rows = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return [{"model": r["model"], "effort": r["effort"]} for r in rows]


# ─── dữ liệu model: nguồn + tách biến thể ───────────────────────────────────

def test_split_variant_and_effort_lists() -> None:
    assert catalog.split_variant("antigravity_cli", "gemini-3.8-flash-high") == ("gemini-3.8-flash", "high")
    assert catalog.split_variant("antigravity_cli", "gemini-3.1-pro") == ("gemini-3.1-pro", None)
    assert catalog.split_variant("claude_code_cli", "opus") == ("opus", None)
    # Review v0.1.32: "gemini-3.5-flash-extra-low" có trong tệp chạy agy 1.2.9 — tên riêng, không tách "-extra" + low.
    xl = "gemini-3.5-flash-extra-low"
    assert catalog.split_variant("antigravity_cli", xl) == (xl, None)
    assert [m["id"] for m in catalog.parse_agy_models(xl + "\n")] == [xl]
    assert catalog.valid_efforts("antigravity_cli") == ("low", "medium", "high")
    assert catalog.valid_efforts("claude_code_cli") == ("low", "medium", "high", "xhigh", "max")


def test_every_fallback_entry_carries_its_source() -> None:
    """Boss 01/10: tên model / mức suy nghĩ phải có nguồn (CLI hoặc tài liệu chính thức), không đoán."""
    for kind in ("antigravity_cli", "claude_code_cli"):
        for m in catalog.fallback(kind):
            assert m["source_ref"], m
            assert isinstance(m["verified"], bool)
            assert not m["id"].endswith(("-low", "-medium", "-high")), m      # không còn biến thể làm tên model
    agy = {m["id"]: m for m in catalog.fallback("antigravity_cli")}
    assert set(agy) == {"gemini-3.8-flash", "gemini-3.1-pro"}             # bỏ claude-*-4-6 không có nguồn
    assert all(m["verified"] is False for m in agy.values())               # Console ghi "chưa xác minh"
    assert agy["gemini-3.1-pro"]["efforts"] == ["low", "high"]
    cc = {m["id"]: m for m in catalog.fallback("claude_code_cli")}
    assert cc["haiku"]["efforts"] == [] and cc["opus"]["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert "code.claude.com/docs/en/model-config" in cc["opus"]["source_ref"]


def test_list_never_collapses_to_the_saved_model() -> None:
    """Ảnh Boss: ô chọn chỉ còn "gemini-3.8-flash-high". CLI liệt kê ≤ 1 model → thêm mục dự phòng (chưa xác minh)."""
    built = catalog.build("antigravity_cli", catalog.parse_agy_models("gemini-3.8-flash-high\n"),
                          saved=[("gemini-3.8-flash-high", None)])
    ids = built["models"]
    assert ids[0] == "gemini-3.8-flash" and "gemini-3.1-pro" in ids and len(ids) >= 2
    first = built["model_groups"][0]["models"][0]
    assert first["verified"] is True and first["efforts"] == ["high"]
    # Model đã lưu mà CLI không liệt kê vẫn hiện.
    built = catalog.build("antigravity_cli", ["gemini-3.8-flash-low", "gemini-3.1-pro-high"],
                          saved=[("x-model", "low")])
    assert built["models"] == ["gemini-3.8-flash", "gemini-3.1-pro", "x-model"]


def test_rejection_detection_is_precise() -> None:
    real = ('invalid model selection (--model "gemini-3.8-flash-high" --effort "medium"): Invalid model '
            '"gemini-3.8-flash-high" (available: gemini-3.8-flash, gemini-3.1-pro)')
    r = rejection(real)
    assert r is not None and r.what == "model" and r.available == ["gemini-3.8-flash", "gemini-3.1-pro"]
    e = rejection('invalid --effort "medium" (valid: low, high)')
    assert e is not None and e.what == "effort"
    assert rejection("There's an issue with the selected model (x). It may not exist") is not None
    assert rejection('unknown model "gemini-9"') is not None and rejection("unknown model name foo") is not None
    # agy 1.2.9: "--effort is not supported for the current model" / "… for model %q" = MỨC sai, không phải model sai.
    for msg in ("--effort is not supported for the current model",
                '--effort is not supported for model "claude-sonnet-4-6-thinking"'):
        eff = rejection(msg)
        assert eff is not None and eff.what == "effort", msg
    # Không phải lỗi "không biết model" → KHÔNG báo "CLI không nhận model".
    for msg in ("Invalid model tier 3", "model returned invalid JSON", "context window: model output not supported "
                "for tool", "429 RESOURCE_EXHAUSTED", "authentication failed or timed out",
                'unknown model tier: "x"', "unknown model key gemini31", "The model is not available right now, "
                "please try again later (503)"):
        assert rejection(msg) is None, msg


def test_redact_hides_tokens_links_and_emails() -> None:
    raw = ('{"access_token":"ya29.a0AfB_secret","email":"a@example.test"}\n'
           "https://accounts.google.com/o/oauth2/auth?client_id=1&code_challenge=abc&state=xyz\n"
           "Bearer sk-ant-oat01-ZZZZZZZZZZZZ eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.sig\n")
    out = redact(raw)
    for secret in ("ya29.", "secret", "a@example.test", "code_challenge", "state=xyz", "sk-ant", "eyJhbGci"):
        assert secret not in out, secret
    assert "a***@example.test" in out and "https://accounts.google.com/o/oauth2/auth?…" in out


# ─── gọi CLI đúng cờ ───────────────────────────────────────────────────────

async def test_agy_invocation_splits_legacy_variant_name(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    await _login(owner_api, "antigravity_cli", "4/an")
    c = AgyClient(clis["agy"], str(clis["agy_home"]), timeout=30)
    # v0.1.38 (F-22): dạng `--model=<gốc>` / `--effort=<mức>` — tên không bao giờ bị hiểu thành một cờ.
    assert c.model_args("gemini-3.8-flash-high") == ["--model=gemini-3.8-flash", "--effort=high"]
    assert c.model_args("gemini-3.1-pro") == ["--model=gemini-3.1-pro"]          # không mức → CLI tự chọn
    assert c.model_args("gemini-3.1-pro", "low") == ["--model=gemini-3.1-pro", "--effort=low"]
    assert c.model_args("gemini-3.1-pro", "--x") == ["--model=gemini-3.1-pro"]   # mức lạ không bao giờ lên argv
    out = await c.generate("gemini-3.8-flash-high", [Message("user", "hi")], json_mode=False, temperature=0)
    assert out.text == "whoami:an@example.vn"
    assert _calls(clis)[-1] == {"model": "gemini-3.8-flash", "effort": "high"}


async def test_choose_model_and_effort_validated_with_real_flags(owner_api, app, clis) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    t = (await api.send("POST", f"/providers/{p['id']}/test")).json()
    assert t["ok"] and t["at"] and t["probe_model"] == "gemini-3.8-flash" and t["probe_effort"] == "high"
    flash = t["model_groups"][0]["models"][0]
    assert flash["efforts"] == ["low", "medium", "high"] and flash["default_effort"] == "high"
    assert "gemini-3.8-flash-high" in t["models_raw"]

    r = await api.send("POST", f"/providers/{p['id']}/models",
                       {"model_name": "gemini-3.1-pro", "effort": "low", "make_default": True})
    assert r.status_code == 201, r.text
    saved = {m["model_name"]: m for m in r.json()["models"]}
    assert saved["gemini-3.1-pro"]["effort"] == "low" and saved["gemini-3.1-pro"]["is_default"]
    assert _calls(clis)[-1] == {"model": "gemini-3.1-pro", "effort": "low"}

    # Mức suy nghĩ model không có → 422 nói đúng là mức suy nghĩ, kèm lỗi gốc cho "Chi tiết kỹ thuật"; không lưu.
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "gemini-3.1-pro", "effort": "medium"})
    assert r.status_code == 422, r.text
    body = r.json()
    assert "mức suy nghĩ" in body["errors"]["model_name"] and "valid: low, high" in body["technical"]
    # Model lạ → 422 kèm danh sách CLI tự nêu.
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "gemini-9-ultra"})
    assert r.status_code == 422 and "CLI nhận: gemini-3.8-flash" in r.json()["errors"]["model_name"]
    # Tên biến thể cũ gửi lên → lưu thành model gốc + mức.
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "gemini-3.8-flash-medium"})
    assert r.status_code == 201, r.text
    saved = {m["model_name"]: m["effort"] for m in r.json()["models"]}
    # (v0.1.66, F132: Kiểm tra xanh đầu tiên đã lưu sẵn cả model Claude qua Antigravity mà agy liệt kê.)
    assert saved == {"gemini-3.1-pro": "low", "gemini-3.8-flash": "medium", "claude-sonnet-4-6-thinking": None}
    # Chỉ sửa hạn mức (không gửi effort) → giữ mức, không gọi thử lại.
    n = len(_calls(clis))
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "gemini-3.1-pro", "daily_quota": 50})
    assert r.status_code == 201 and len(_calls(clis)) == n
    assert {m["model_name"]: m["effort"] for m in r.json()["models"]}["gemini-3.1-pro"] == "low"

    # Bộ định tuyến (worker) gọi đúng model mặc định + mức đã lưu.
    from gh.db import sessionmaker

    async with sessionmaker()() as db:
        org = (await db.execute(text("SELECT org_id FROM agent.providers WHERE id = :i"),
                                {"i": uuid.UUID(p["id"])})).scalar_one()
        await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'antigravity_cli') WHERE org_id = :o"),
                         {"o": org})
        await db.commit()
    # Antigravity CLI không còn tham số allow_agy — gọi như lượt Gen của Owner (agy trong tiến trình).
    await app.state.model_router.generate(org, agent_key="core.gen", purpose="test",
                                          messages=[Message("user", "hi")], json_mode=False)
    assert _calls(clis)[-1] == {"model": "gemini-3.1-pro", "effort": "low"}


async def test_claude_code_effort_flag(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "claude_code_cli", "c/boss")
    p = await _provider(api, "claude_code_cli")
    r = await api.send("POST", f"/providers/{p['id']}/models",
                       {"model_name": "opus", "effort": "xhigh", "make_default": True})
    assert r.status_code == 201, r.text
    assert r.json()["models"][0]["effort"] == "xhigh"
    t = (await api.send("POST", f"/providers/{p['id']}/test")).json()
    assert t["ok"] and (t["probe_model"], t["probe_effort"]) == ("opus", "xhigh")
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "opus", "effort": "ultra"})
    assert r.status_code == 422
    # Review v0.1.32: mức suy nghĩ theo TỪNG model — haiku không có (tài liệu Claude Code) → 422, không lưu.
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "haiku", "effort": "high"})
    assert r.status_code == 422 and "không chỉnh được mức suy nghĩ" in r.json()["errors"]["effort"], r.text
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "haiku"})
    assert r.status_code == 201, r.text


# ─── chuyển dữ liệu cũ (migration 0023) ────────────────────────────────────

async def test_migration_splits_saved_variant_names(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    from gh.db import sessionmaker

    pid = uuid.UUID(p["id"])
    async with sessionmaker()() as db:
        for name, default in (("gemini-3.8-flash-high", True), ("gemini-3.8-flash-low", False),
                              ("gemini-3.1-pro-low", False), ("gemini-3.5-flash-extra-low", False)):
            await db.execute(text("""INSERT INTO agent.models (provider_id, model_name, is_default)
                                     VALUES (:p, :m, :d)"""), {"p": pid, "m": name, "d": default})
        await db.commit()
        dml = SQL.read_text()[SQL.read_text().index("UPDATE agent.models m"):]
        raw = await (await db.connection()).get_raw_connection()
        await raw.driver_connection.execute(dml)          # type: ignore[union-attr]
        await raw.driver_connection.execute(dml)          # type: ignore[union-attr] — chạy lại vẫn an toàn
        await db.commit()
        rows = {r.model_name: r.effort for r in (await db.execute(text(
            "SELECT model_name, effort FROM agent.models WHERE provider_id = :p"), {"p": pid})).all()}
    # Dòng mặc định thắng tên gốc; dòng trùng còn lại chỉ ghi effort (lúc gọi vẫn tách hậu tố).
    assert rows == {"gemini-3.8-flash": "high", "gemini-3.8-flash-low": "low", "gemini-3.1-pro": "low",
                    "gemini-3.5-flash-extra-low": None}     # tên riêng — không tách


async def test_ensure_tested_models_splits_pre_upgrade_probe_model(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    """Review v0.1.32: `last_test.probe_model` của bản ≤ v0.1.31 còn tên biến thể → model chép từ lần gọi thử lưu model
    gốc + mức (v0.1.55: `ensure_tested_models`, không còn tự gán agent.bindings)."""
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    from gh.db import sessionmaker
    from gh.setup.routes import _tested_choice, ensure_tested_models

    assert _tested_choice(type("P", (), {"kind": "antigravity_cli", "probe_model": "gemini-3.8-flash-high",
                                          "probe_effort": None, "test_models": []})()) == ("gemini-3.8-flash", "high")
    pid = uuid.UUID(p["id"])
    async with sessionmaker()() as db:
        org = (await db.execute(text("SELECT org_id FROM agent.providers WHERE id = :i"), {"i": pid})).scalar_one()
        await db.execute(text("DELETE FROM agent.bindings WHERE org_id = :o"), {"o": org})
        await db.execute(text("""UPDATE agent.providers SET is_enabled = (id = :i), last_test = CAST(:t AS jsonb)
                                 WHERE org_id = :o"""),
                         {"i": pid, "o": org, "t": json.dumps({"ok": True, "probe_model": "gemini-3.8-flash-high",
                                                               "models": ["gemini-3.8-flash-high"]})})
        await db.commit()
        await db.execute(text("DELETE FROM agent.models WHERE provider_id = :i"), {"i": pid})
        assert await ensure_tested_models(db, org) == 1
        await db.commit()
        row = (await db.execute(text("SELECT model_name, effort FROM agent.models WHERE provider_id = :i"),
                                {"i": pid})).one()
        assert (await db.execute(text("SELECT count(*) FROM agent.bindings WHERE org_id = :o"),
                                 {"o": org})).scalar_one() == 0
    assert (row.model_name, row.effort) == ("gemini-3.8-flash", "high")


# ─── Chẩn đoán (chỉ Owner) ─────────────────────────────────────────────────

async def test_diagnose_returns_redacted_raw_output(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    r = await api.send("POST", f"/providers/{p['id']}/diagnose")
    assert r.status_code == 200, r.text
    d = r.json()
    assert [s["label"] for s in d["steps"]] == ["Phiên bản", "Danh sách model", "Model của tài khoản (/model)",
                                                "Mức suy nghĩ (/effort)", "Gọi thử 1 lượt"]
    assert d["steps"][0]["stdout"].strip() == "1.2.9" and d["steps"][0]["exit_code"] == 0
    assert "gemini-3.8-flash-high" in d["steps"][1]["stdout"]
    # agy -p "/model", "/effort": bản ghi tab-separated, không tốn lượt (changelog agy 1.1.11).
    # v0.1.38 (F-22): `-p=/model` (dạng `=`: `-p` không nuốt cờ kế tiếp), `--model=<gốc>`.
    assert d["steps"][2]["command"] == "agy -p=/model" and "gemini-3.1-pro\tlow,high" in d["steps"][2]["stdout"]
    assert d["steps"][3]["command"].startswith("agy -p=/effort --model=gemini-3.8-flash")
    assert "high\tcurrent" in d["steps"][3]["stdout"]
    call = d["steps"][4]
    assert call["exit_code"] == 0 and "--model=gemini-3.8-flash" in call["command"]
    assert "--disable-slash-commands" in call["command"] and "(stdin:" in call["command"]   # prompt qua stdin
    assert "a***@example.vn" in call["stdout"] and "an@example.vn" not in call["stdout"]


async def test_diagnose_is_owner_only(owner_api, client, db, clis) -> None:  # type: ignore[no-untyped-def]
    """Chẩn đoán chạy lệnh CLI thật (tốn lượt) và lộ đầu ra thô → chỉ Owner; Quản lý (có system.manage) bị 403."""
    from tests.test_rbac_api import login_as

    await _login(owner_api, "antigravity_cli", "4/an")
    p = await _provider(owner_api, "antigravity_cli")
    manager = await login_as(client, db, "manager")
    n = len(_calls(clis))
    r = await manager.send("POST", f"/providers/{p['id']}/diagnose")
    assert r.status_code == 403, r.text
    assert len(_calls(clis)) == n   # không lệnh CLI nào chạy


async def test_diagnose_claude_not_logged_in_skips_call(owner_api, app, clis) -> None:  # type: ignore[no-untyped-def]
    await _login(owner_api, "claude_code_cli", "c/boss")
    p = await _provider(owner_api, "claude_code_cli")
    (Path(clis["claude_home"]) / ".credentials.json").unlink()
    d = (await owner_api.send("POST", f"/providers/{p['id']}/diagnose")).json()
    assert d["steps"][0]["stdout"].startswith("2.1.285")
    assert d["steps"][1]["exit_code"] == 1
    assert d["steps"][2]["note"] == "bỏ qua — CLI chưa đăng nhập"


@pytest.mark.parametrize("kind", ["gemini"])
async def test_diagnose_rejects_api_key_sources(owner_api, kind) -> None:  # type: ignore[no-untyped-def]
    # v0.1.35 (F-20): tạo / sửa nhà cung cấp AI cần PIN `ai.route_change`.
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/providers", {"kind": kind, "name": "G", "keys": ["AIzaSyTESTKEY000000"]})
    assert r.status_code in (200, 201), r.text
    r = await owner_api.send("POST", f"/providers/{r.json()['id']}/diagnose")
    assert r.status_code == 409
