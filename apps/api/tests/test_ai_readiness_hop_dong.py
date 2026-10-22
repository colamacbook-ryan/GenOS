# ruff: noqa: F811 — fixture `clis` nhập từ test_cli_models_v0131 (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.63 (gói B2) — MỘT hàm "nguồn AI dùng được": `gh.providers.readiness`.

Lưới L1 = ma trận (loại nguồn × khoá / phiên × model × bật / tắt × hạng × vai × hộp riêng) → `usable_sources` và
`ai_ready`; rồi bốn finding đóng ở đường HTTP:
- F002: "Kiểm tra" nguồn AI ở Việc Sếp cần làm gọi thử nguồn CLI xanh mà chưa có dòng model ⇒ lưu model
(adopt_probed_model);
- F010: bước 4 "Việc thiết lập tiếp" chỉ xong khi nguồn DÙNG ĐƯỢC (khoá bật / phiên chưa hết hạn / có
model), không chỉ có dòng model;
- F014: bằng chứng cũ (lượt gọi model, Claude / Google gọi thử đạt, lần Kiểm tra cũ) không làm dòng "nguồn
AI" xanh khi nguồn
  đã tắt / gỡ khoá / hết phiên;
- F035: nút Kiểm tra không chỉ thử nguồn sẵn sàng ĐẦU TIÊN: nguồn đầu hỏng mà nguồn sau chạy được thì vẫn Đạt.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh import crypto
from gh.boss_checks import service as boss
from gh.providers import readiness
from gh.providers.readiness import FakeBox
from gh.providers.router import KEY_AAD
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_agy_phan_lop import _channel
from tests.test_cli_models_v0131 import _login, _provider, clis  # noqa: F401 — fixture dùng chung
from tests.test_p4_agents import _seed_agent

AGY = "antigravity_cli"
CLAUDE = "claude_code_cli"
SETUP = "/setup?buoc=nguon-ai"
API = "/api"          # v0.1.68 (F029): nguồn đã tắt bật lại ở API & Model; mọi lỗi khác về một đích SETUP


@pytest.fixture(autouse=True)
def _has_org(owner_api: Api) -> None:
    """Mọi ca cần tổ chức đã thiết lập (fixture `owner_api` dựng Owner + tổ chức); `db` đơn thuần thì chưa có."""


# ─── dựng dữ liệu ───────────────────────────────────────────────────────────────────────────────────────────────

async def _src(db: Any, kind: str = "gemini", *, name: str | None = None, rank: int | None = 1, enabled: bool = True,
               key: bool | str = True, secret: str = "sk-test-readiness-1234",
               session: bool | None = None, models: tuple[str, ...] = ("m1",),
               auth_state: str = "ok", expires_at: datetime | None = None) -> uuid.UUID:
    """Một nguồn trong CSDL. `key`: có khoá API bật (False = không; "off" = có nhưng tắt). `session`: hồ sơ CLI đang
    dùng (mặc định có với CLI, không với nguồn khoá API)."""
    org = await org_id(db)
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank,
                                                                 is_enabled, auth_state)
                                    VALUES (:o, :k, :n, :u, :r, :e, :a) RETURNING id"""),
                            {"o": org, "k": kind, "n": name or f"{kind}-{rank}", "r": rank, "e": enabled,
                             "a": auth_state, "u": None if kind in (AGY, CLAUDE) else "https://127.0.0.1:9/v1"})
           ).scalar_one()
    if key:
        await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4, is_enabled)
                                 VALUES (:p, 'K1', :s, '1234', :e)"""),
                         {"p": pid, "s": crypto.encrypt(secret.encode(), KEY_AAD), "e": key != "off"})
    if session if session is not None else kind in (AGY, CLAUDE):
        await db.execute(text("""INSERT INTO agent.cli_profiles (org_id, provider_id, email, token_enc, is_active,
                                                                 expires_at)
                                 VALUES (:o, :p, 'sep@example.com', :t, true, :x)"""),
                         {"o": org, "p": pid, "t": b"\x00tok", "x": expires_at})
    for i, mname in enumerate(models):
        await db.execute(text("INSERT INTO agent.models (provider_id, model_name, is_default) VALUES (:p, :m, :d)"),
                         {"p": pid, "m": mname, "d": i == 0})
    await db.commit()
    return pid  # type: ignore[no-any-return]


async def _ready(db: Any, **kw: Any) -> dict[str, Any]:
    return await readiness.ai_ready(db, await org_id(db), **kw)


def _shape(r: dict[str, Any]) -> None:
    assert set(r) == {"ok", "reason_code", "reason_vi", "fix_to"}
    if r["ok"]:
        assert r["reason_code"] is None and r["reason_vi"] is None and r["fix_to"] is None
    else:
        assert r["reason_code"] in readiness.REASON_CODES
        assert r["reason_vi"] and r["fix_to"].startswith("/")


# ─── L1: ma trận ai_ready / usable_sources ──────────────────────────────────────────────────────────────────────

async def test_no_provider_is_no_source(db: Any) -> None:
    r = await _ready(db)
    _shape(r)
    assert (r["ok"], r["reason_code"], r["fix_to"]) == (False, "no_source", SETUP)
    assert "khoá API" in r["reason_vi"] and "Thiết lập nguồn AI" in r["reason_vi"]
    assert await readiness.usable_sources(db, await org_id(db)) == []


async def test_jev_and_embedding_are_not_text_sources(db: Any) -> None:
    await _src(db, "system_one", rank=1)
    await _src(db, "embedding", rank=2)
    assert (await _ready(db))["reason_code"] == "no_source"


async def test_all_disabled_is_disabled(db: Any) -> None:
    await _src(db, "gemini", enabled=False)
    r = await _ready(db)
    _shape(r)
    assert (r["reason_code"], r["fix_to"]) == ("disabled", API) and "tắt" in r["reason_vi"]


@pytest.mark.parametrize("key", [False, "off"], ids=["khong-co-khoa", "khoa-da-tat"])
async def test_api_source_without_enabled_key_is_no_key(db: Any, key: Any) -> None:
    await _src(db, "gemini", name="Gemini của Sếp", key=key)
    r = await _ready(db)
    _shape(r)
    assert (r["reason_code"], r["fix_to"]) == ("no_key", SETUP)
    assert "khoá API" in r["reason_vi"] and "Gemini của Sếp" in r["reason_vi"]


@pytest.mark.parametrize("kind,label", [(AGY, "Google"), (CLAUDE, "Claude Code")])
async def test_cli_never_logged_in_is_no_key_with_login_hint(db: Any, kind: str, label: str) -> None:
    await _src(db, kind, key=False, session=False, models=())
    r = await _ready(db)
    _shape(r)
    assert r["reason_code"] == "no_key" and "đăng nhập" in r["reason_vi"].lower() and label in r["reason_vi"]


@pytest.mark.parametrize("models", [(), ("text-embedding-3-small",)], ids=["khong-model", "chi-embedding"])
async def test_key_but_no_text_model_is_no_model(db: Any, models: tuple[str, ...]) -> None:
    await _src(db, "gemini", models=models)
    r = await _ready(db)
    _shape(r)
    assert (r["reason_code"], r["fix_to"]) == ("no_model", SETUP) and "model" in r["reason_vi"]


async def test_disabled_model_is_no_model(db: Any) -> None:
    pid = await _src(db, "gemini")
    await db.execute(text("UPDATE agent.models SET is_enabled = false WHERE provider_id = :p"), {"p": pid})
    await db.commit()
    assert (await _ready(db))["reason_code"] == "no_model"


async def test_api_source_with_key_and_model_is_ready(db: Any) -> None:
    pid = await _src(db, "gemini", name="Gemini", models=("gemini-2.5-flash", "gemini-2.5-pro"))
    r = await _ready(db)
    _shape(r)
    assert r["ok"] is True
    got = await readiness.usable_sources(db, await org_id(db))
    assert [(s["provider_id"], s["name"], s["kind"], s["model_name"], s["models"]) for s in got] == [
        (str(pid), "Gemini", "gemini", "gemini-2.5-flash", 2)]       # model mặc định đứng đầu


async def test_usable_sources_follow_chain_order_and_skip_unusable(db: Any) -> None:
    c = await _src(db, "deepseek", name="C", rank=None)               # hạng trống ⇒ cuối chuỗi
    b = await _src(db, "openai_compat", name="B", rank=2)
    await _src(db, "gemini", name="A-không-khoá", rank=1, key=False)
    a2 = await _src(db, "gemini", name="A2", rank=1)
    await _src(db, "gemini", name="Tắt", rank=1, enabled=False)
    got = await readiness.usable_sources(db, await org_id(db))
    assert [s["provider_id"] for s in got] == [str(a2), str(b), str(c)]
    assert [s["rank"] for s in got] == [1, 2, None]


async def test_cli_with_session_and_model_is_ready(db: Any) -> None:
    await _src(db, AGY, key=False, models=("gemini-3.8-flash",))
    assert (await _ready(db))["ok"] is True


async def test_cli_expired_by_auth_state(db: Any) -> None:
    await _src(db, AGY, key=False, auth_state="expired")
    r = await _ready(db)
    _shape(r)
    assert (r["reason_code"], r["fix_to"]) == ("cli_expired", SETUP) and "hết hạn" in r["reason_vi"]


async def test_cli_expired_by_past_token_that_cannot_refresh(db: Any) -> None:
    await _src(db, CLAUDE, key=False, expires_at=datetime.now(UTC) - timedelta(hours=2))
    assert (await _ready(db))["reason_code"] == "cli_expired"


async def test_cli_with_future_expiry_is_ready(db: Any) -> None:
    await _src(db, CLAUDE, key=False, expires_at=datetime.now(UTC) + timedelta(days=3))
    assert (await _ready(db))["ok"] is True


async def test_cli_last_real_check_error_is_error_but_api_error_is_not(db: Any) -> None:
    await _src(db, AGY, key=False, auth_state="error")
    r = await _ready(db)
    _shape(r)
    assert r["reason_code"] == "error" and "Kiểm tra" in r["reason_vi"]
    # nguồn khoá API: `auth_state` không tự về 'ok' sau lượt gọi thật ⇒ không dùng để kết luận hỏng.
    await _src(db, "gemini", rank=2, auth_state="error")
    assert (await _ready(db))["ok"] is True


async def test_reports_the_closest_to_working_reason(db: Any) -> None:
    """Nhiều nguồn cùng hỏng ⇒ nói lý do ít việc sửa nhất: hết phiên (đăng nhập lại) gần hơn thiếu khoá."""
    await _src(db, "gemini", rank=1, key=False)
    await _src(db, AGY, rank=2, key=False, auth_state="expired")
    assert (await _ready(db))["reason_code"] == "cli_expired"
    await _src(db, "deepseek", rank=3, models=())
    assert (await _ready(db))["reason_code"] == "no_model"


async def test_readiness_is_per_org(db: Any) -> None:
    await _src(db, "gemini")
    other = uuid.uuid4()
    r = await readiness.ai_ready(db, other)
    assert r["reason_code"] == "no_source"
    assert await readiness.usable_sources(db, other) == []


async def test_ai_ready_never_raises(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("DB hỏng")

    monkeypatch.setattr(readiness, "source_states", boom)
    r = await _ready(db)
    _shape(r)
    assert r["ok"] is False and r["reason_code"] == "error"


async def test_ai_ready_sql_error_does_not_poison_caller_transaction(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review v0.1.68 (P3): lỗi SQL trong `ai_ready` bị nuốt thành `reason_code='error'`, nhưng Postgres đã đánh dấu
    cả giao dịch hỏng — không có savepoint thì mọi câu lệnh sau của người gọi đều ném InFailedSQLTransaction."""
    org = await org_id(db)
    await _src(db, "gemini", rank=1)

    async def bad_sql(session: Any, *_a: Any, **_k: Any) -> Any:
        await session.execute(text("SELECT * FROM bang_khong_ton_tai_v0168"))

    monkeypatch.setattr(readiness, "source_states", bad_sql)
    r = await readiness.ai_ready(db, org)
    assert r["ok"] is False and r["reason_code"] == "error"
    # Người gọi vẫn dùng được phiên: câu lệnh kế tiếp chạy bình thường, dữ liệu trước đó còn nguyên.
    n = (await db.execute(text("SELECT count(*) FROM agent.providers WHERE org_id = :o"), {"o": org})).scalar_one()
    assert n == 1


# ─── L1: vai (agy_policy + việc nền) ────────────────────────────────────────────────────────────────────────────

async def test_core_reply_uses_agy_only_through_the_box(db: Any) -> None:
    """H2: `core.reply` (soạn lại / dịch nháp tin khách) chạy agy QUA HỘP; hộp chưa cài / tắt ⇒ không có agy (không rơi
    về trong tiến trình), hộp chạy ⇒ có."""
    org = await org_id(db)
    await _src(db, AGY, key=False, rank=1, models=("gemini-3.8-flash",))
    assert (await readiness.ai_ready(db, org, "core.gen"))["ok"] is True
    for state in ("not_installed", "down"):
        gate = FakeBox(required=True, state=state)
        assert await readiness.usable_sources(db, org, "core.reply", box=gate) == []
        r = await readiness.ai_ready(db, org, "core.reply", box=gate)
        _shape(r)
        assert r["ok"] is False and r["reason_code"] == "box_down" and "Antigravity" in r["reason_vi"]
    assert len(await readiness.usable_sources(db, org, "core.reply", box=FakeBox(required=True, state="ok"))) == 1
    api = await _src(db, "gemini", rank=2)
    assert [s["provider_id"] for s in await readiness.usable_sources(
        db, org, "core.reply", box=FakeBox(required=True, state="down"))] == [str(api)]
    assert len(await readiness.usable_sources(db, org)) == 2          # role=None: không lọc theo vai


async def test_staff_gen_uses_agy_only_through_the_box(db: Any) -> None:
    org = await org_id(db)
    await _src(db, AGY, key=False, models=("gemini-3.8-flash",))
    down = FakeBox(required=True, state="down")
    assert await readiness.usable_sources(db, org, "core.gen", owner=False, box=down) == []
    assert len(await readiness.usable_sources(db, org, "core.gen", owner=True)) == 1
    assert len(await readiness.usable_sources(db, org, "core.gen", owner=False,
                                              box=FakeBox(required=True, state="ok"))) == 1


async def test_background_roles_skip_claude_cli_until_owner_allows(db: Any) -> None:
    org = await org_id(db)
    await _src(db, CLAUDE, key=False, models=("claude-sonnet-4-6",))
    assert len(await readiness.usable_sources(db, org, "core.gen")) == 1
    assert await readiness.usable_sources(db, org, "core.refinery") == []      # F-86: việc nền
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(settings, '{ai}',
                             '{"background_cli": ["claude_code_cli"]}')"""))
    await db.commit()
    assert len(await readiness.usable_sources(db, org, "core.refinery")) == 1


# ─── L1: box_down (hộp riêng Antigravity — điểm nối cho H2) ──────────────────────────────────────────────────────

async def test_box_down_blocks_only_agy_for_roles_that_require_the_box(db: Any) -> None:
    org = await org_id(db)
    agy = await _src(db, AGY, key=False, models=("gemini-3.8-flash",))
    down = FakeBox(required=True, state="down")
    r = await readiness.ai_ready(db, org, "core.refinery", box=down)
    _shape(r)
    assert (r["ok"], r["reason_code"], r["fix_to"]) == (False, "box_down", SETUP)
    assert "Hộp riêng Antigravity" in r["reason_vi"]
    api = await _src(db, "gemini", rank=2)
    got = await readiness.usable_sources(db, org, "core.refinery", box=down)
    assert [s["provider_id"] for s in got] == [str(api)]                    # chuyển sang nguồn kế, không rơi về agy
    # hộp chạy ⇒ agy lại dùng được; vai không bắt buộc hộp ⇒ không bị chặn dù hộp tắt.
    assert [s["provider_id"] for s in await readiness.usable_sources(
        db, org, "core.refinery", box=FakeBox(required=True, state="ok"))] == [str(agy), str(api)]
    assert len(await readiness.usable_sources(db, org, "core.refinery", box=FakeBox(required=False, state="down"))) == 2
    # "chưa cài" cũng là hộp không chạy (đóng an toàn).
    assert [s["provider_id"] for s in await readiness.usable_sources(
        db, org, "core.refinery", box=FakeBox(required=True, state="not_installed"))] == [str(api)]
    # không có vai ⇒ không hỏi hộp.
    assert len(await readiness.usable_sources(db, org, box=down)) == 2


async def test_box_gate_receives_role_and_agent_key(db: Any) -> None:
    org = await org_id(db)
    await _src(db, AGY, key=False, models=("gemini-3.8-flash",))
    seen: list[tuple[str | None, str | None]] = []

    def required(role: str | None, agent: str | None) -> bool:
        seen.append((role, agent))
        return False

    await readiness.ai_ready(db, org, "core.refinery", box=FakeBox(required=required))
    assert seen == [("core.refinery", "core.refinery")]


async def test_default_gate_follows_final_policy_and_box_state(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không tiêm gì: cổng mặc định = `placement.box_required` + `agybox.client.box_status` ⇒ kết luận của readiness
    khớp chính sách cuối của H2 (`agy_policy`): vai ngoài (core.refinery / core.reply / Gen nhân viên) cần hộp `ok`,
    vai core (Gen của Sếp) không bao giờ bị hộp chặn."""
    from gh.agybox import client as box_client
    from gh.providers import agy_policy

    org = await org_id(db)
    await _src(db, AGY, key=False, models=("gemini-3.8-flash",))
    state = {"v": "not_installed"}

    async def box_status(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"state": state["v"]}

    monkeypatch.setattr(box_client, "box_status", box_status)
    r = await readiness.ai_ready(db, org, "core.refinery")
    assert (r["ok"], r["reason_code"]) == (False, "box_down")
    assert r["reason_vi"] == agy_policy.REASON_BOX_NOT_INSTALLED               # nói đúng "chưa cài"
    state["v"] = "down"
    r = await readiness.ai_ready(db, org, "core.refinery")
    assert (r["ok"], r["reason_code"], r["reason_vi"]) == (False, "box_down", agy_policy.REASON_BOX_DOWN)
    assert (await readiness.ai_ready(db, org, "core.reply"))["reason_code"] == "box_down"
    assert (await readiness.ai_ready(db, org, "core.gen", owner=False))["reason_code"] == "box_down"   # Gen nhân viên
    assert (await readiness.ai_ready(db, org, "core.gen"))["ok"] is True       # Gen của Sếp: trong tiến trình
    assert (await readiness.ai_ready(db, org, "core.briefing"))["reason_code"] == "box_down"   # P2-3: Bản tin qua hộp
    state["v"] = "ok"
    for role, owner in (("core.refinery", True), ("core.reply", True), ("core.gen", False), ("core.gen", True),
                        ("core.briefing", True)):
        assert (await readiness.ai_ready(db, org, role, owner=owner))["ok"] is True, (role, owner)


async def test_readiness_agrees_with_router_for_diplomatic_agent(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent ngoại giao (gắn kênh) cần hộp, agent nội bộ thì không — readiness tra DB như `agy_policy.verdict_for`."""
    from gh.agybox import client as box_client
    from gh.providers import agy_policy

    org = await org_id(db)
    await _src(db, AGY, key=False, models=("gemini-3.8-flash",))

    async def down(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"state": "down"}

    monkeypatch.setattr(box_client, "box_status", down)
    inner = await _seed_agent(db, org)
    dip = await _seed_agent(db, org)
    ch = await _channel(db, org)
    await db.execute(text("INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id) VALUES (:a, :c, NULL)"),
                     {"a": dip, "c": ch})
    await db.commit()
    for aid, want in ((inner, True), (dip, False)):      # hộp tắt: nội bộ vẫn dùng agy, ngoại giao thì không
        v = await agy_policy.verdict_for(db, org, f"agent:{aid}")
        r = await readiness.ai_ready(db, org, f"agent:{aid}")
        assert r["ok"] is v.allowed is want, (aid, r, v)
        assert want or r["reason_code"] == "box_down"


# ─── F002: Kiểm tra nguồn AI ở Việc Sếp cần làm lưu model cho nguồn CLI gọi thử xanh ────────────────────────────────

async def _models_of(db: Any, pid: str) -> list[str]:
    await db.rollback()
    return list((await db.execute(text("SELECT model_name FROM agent.models WHERE provider_id = :p"),
                                  {"p": uuid.UUID(pid)})).scalars().all())


async def test_f002_ai_source_run_adopts_model_of_green_cli(owner_api: Api, db: Any, clis: Any) -> None:
    api = owner_api
    await _login(api, AGY, "4/an")
    pid = (await _provider(api, AGY))["id"]
    await db.execute(text("DELETE FROM agent.models WHERE provider_id = :p"), {"p": uuid.UUID(pid)})
    await db.commit()
    org = await org_id(db)
    r = await readiness.ai_ready(db, org)
    assert r["reason_code"] == "no_model"                                   # đăng nhập rồi nhưng chưa có dòng model
    out = (await api.send("POST", "/boss-checks/ai_source/run", {})).json()
    assert out["status"] == "pass", out
    assert await _models_of(db, pid), "gọi thử xanh phải lưu model đã chạy được (adopt_probed_model)"
    assert (await readiness.ai_ready(db, org))["ok"] is True
    q = text("SELECT count(*) FROM ops.action_log WHERE action = 'provider.model_auto'")
    assert (await db.execute(q)).scalar_one() == 1


@pytest.mark.parametrize("key,kind", [("agy_call", AGY), ("claude_call", CLAUDE)])
async def test_f002_cli_call_check_adopts_model_too(owner_api: Api, db: Any, clis: Any, key: str, kind: str) -> None:
    api = owner_api
    await _login(api, kind, "4/an")
    pid = (await _provider(api, kind))["id"]
    await db.execute(text("DELETE FROM agent.models WHERE provider_id = :p"), {"p": uuid.UUID(pid)})
    await db.commit()
    out = (await api.send("POST", f"/boss-checks/{key}/run", {})).json()
    assert out["status"] == "pass", out
    assert await _models_of(db, pid)


async def test_f002_adopt_does_not_touch_a_source_that_already_has_models(owner_api: Api, db: Any, clis: Any) -> None:
    api = owner_api
    await _login(api, AGY, "4/an")
    pid = (await _provider(api, AGY))["id"]
    await db.execute(text("DELETE FROM agent.models WHERE provider_id = :p"), {"p": uuid.UUID(pid)})
    await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'sep-chon-san')"),
                     {"p": uuid.UUID(pid)})
    await db.commit()
    before = await _models_of(db, pid)
    assert before == ["sep-chon-san"]
    assert (await api.send("POST", "/boss-checks/ai_source/run", {})).json()["status"] == "pass"
    assert await _models_of(db, pid) == before


# ─── F010: bước 4 chỉ xong khi nguồn DÙNG ĐƯỢC ──────────────────────────────────────────────────────────────────

async def _step4(api: Api) -> dict[str, Any]:
    return next(i for i in (await api.get("/setup/follow-up")).json() if i["n"] == 4)


async def test_f010_step4_needs_a_usable_source_not_just_a_model_row(owner_api: Api, db: Any) -> None:
    api = owner_api
    assert (await _step4(api))["done"] is False
    pid = await _src(db, "gemini", key=False)                              # có dòng model, KHÔNG có khoá
    assert (await _step4(api))["done"] is False
    await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4)
                             VALUES (:p, 'K', :s, '9999')"""), {"p": pid, "s": b"\x00e"})
    await db.commit()
    assert (await _step4(api))["done"] is True
    await db.execute(text("UPDATE agent.provider_keys SET is_enabled = false WHERE provider_id = :p"), {"p": pid})
    await db.commit()
    assert (await _step4(api))["done"] is False                           # khoá bị tắt ⇒ lại chưa xong


async def test_f010_step4_cli_needs_a_live_session(owner_api: Api, db: Any) -> None:
    api = owner_api
    pid = await _src(db, AGY, key=False, session=False, models=("gemini-3.8-flash",))   # model còn, phiên đã mất
    assert (await _step4(api))["done"] is False
    await db.execute(text("""INSERT INTO agent.cli_profiles (org_id, provider_id, token_enc, is_active)
                             VALUES ((SELECT org_id FROM agent.providers WHERE id = :p), :p, :t, true)"""),
                     {"p": pid, "t": b"\x00t"})
    await db.commit()
    assert (await _step4(api))["done"] is True
    await db.execute(text("UPDATE agent.providers SET auth_state = 'expired' WHERE id = :p"), {"p": pid})
    await db.commit()
    assert (await _step4(api))["done"] is False                           # phiên hết hạn ⇒ chưa xong


# ─── F014: bằng chứng cũ không che sự thật hiện tại ──────────────────────────────────────────────────────────────

async def _call_ok(db: Any, pid: uuid.UUID, ago: str = "1 hour") -> None:
    org = await org_id(db)
    q = text("SELECT id FROM agent.models WHERE provider_id = :p LIMIT 1")
    mid = (await db.execute(q, {"p": pid})).scalar_one()
    await db.execute(text(f"""INSERT INTO agent.model_calls (org_id, at, model_id, agent_key, purpose, status)
                              VALUES (:o, now() - interval '{ago}', :m, 'core.gen', 'reply', 'ok')"""),
                     {"o": org, "m": mid})
    await db.commit()


async def _boss(api: Api) -> tuple[bool, dict[str, Any], int]:
    body = (await api.get("/boss-checks")).json()
    row = next(r for r in body["rows"] if r["key"] == "ai")
    return row["done"], body["results"]["ai_source"], body["required_done"]


async def test_f014_old_model_call_does_not_count_after_source_is_disabled(owner_api: Api, db: Any) -> None:
    pid = await _src(db, "gemini")
    await _call_ok(db, pid)
    assert (await _boss(owner_api))[0] is True                              # nguồn dùng được + lượt gọi thật gần đây
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE id = :p"), {"p": pid})
    await db.commit()
    done, res, req = await _boss(owner_api)
    assert (done, req) == (False, 0)
    assert res["status"] == "fail" and res["error_code"] == "AI_DISABLED" and "tắt" in res["message"]
    assert res["detail"].get("fix_to") == API


async def test_f014_old_model_call_does_not_count_after_key_removed(owner_api: Api, db: Any) -> None:
    pid = await _src(db, "gemini", name="Gemini")
    await _call_ok(db, pid, "20 days")
    assert (await _boss(owner_api))[0] is True
    await db.execute(text("DELETE FROM agent.provider_keys WHERE provider_id = :p"), {"p": pid})
    await db.commit()
    done, res, _ = await _boss(owner_api)
    assert done is False and res["status"] == "fail" and res["error_code"] == "AI_KEY_MISSING"
    assert "khoá API" in res["message"]


async def test_f014_call_of_a_removed_source_does_not_count_for_another_source(owner_api: Api, db: Any) -> None:
    """Lượt gọi thật cũ của nguồn A (đã tắt) không chứng minh nguồn B (dùng được nhưng chưa từng chạy)."""
    a = await _src(db, "gemini", rank=1)
    await _call_ok(db, a)
    await _src(db, "deepseek", rank=2)
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE id = :p"), {"p": a})
    await db.commit()
    done, res, _ = await _boss(owner_api)
    assert done is False                                    # B sẵn sàng nhưng chưa có bằng chứng chạy được
    assert res is None or res["status"] != "pass"


async def test_f014_cli_call_pass_does_not_count_when_cli_session_expired(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    pid = await _src(db, AGY, key=False)
    await boss.record(db, org, "agy_call", "pass")
    await db.commit()
    assert (await _boss(owner_api))[0] is True
    await db.execute(text("UPDATE agent.providers SET auth_state = 'expired' WHERE id = :p"), {"p": pid})
    await db.commit()
    done, res, _ = await _boss(owner_api)
    assert done is False and res["status"] == "fail" and res["error_code"] == "AI_CLI_EXPIRED"


async def test_f014_manual_pass_is_stale_once_no_source_is_usable(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    await boss.record(db, org, "ai_source", "pass", detail={"latency_ms": 9, "probe_model": "m"})
    await db.commit()
    done, res, req = await _boss(owner_api)                                 # chưa có nguồn nào mà "đạt" ⇒ nói dối
    assert (done, req) == (False, 0)
    assert res["status"] == "fail" and res["error_code"] == "AI_NO_SOURCE"


async def test_f014_manual_pass_of_a_source_that_is_gone(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    a = await _src(db, "gemini", rank=1)
    await _src(db, "deepseek", rank=2)
    await boss.record(db, org, "ai_source", "pass", detail={"provider_id": str(a), "probe_model": "m"})
    await db.commit()
    assert (await _boss(owner_api))[0] is True
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE id = :p"), {"p": a})
    await db.commit()
    assert (await _boss(owner_api))[0] is False                            # A (nguồn đã đạt) tắt; B chưa từng kiểm


# ─── F035: nút Kiểm tra thử nguồn sẵn sàng KẾ TIẾP khi nguồn đầu hỏng ────────────────────────────────────────────

async def test_f035_first_source_fails_second_passes(owner_api: Api, app: Any, db: Any) -> None:
    await _src(db, "openai_compat", name="Nguồn hỏng", rank=1, secret="sk-hong-0001")
    second = await _src(db, "openai_compat", name="Nguồn tốt", rank=2, secret="sk-tot-0002")

    def handler(req: httpx.Request) -> httpx.Response:
        if "sk-hong-0001" in req.headers.get("authorization", ""):
            return httpx.Response(500, text="hỏng")
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    app.state.model_router.transport = httpx.MockTransport(handler)
    out = (await owner_api.send("POST", "/boss-checks/ai_source/run", {})).json()
    assert out["status"] == "pass", out
    assert out["detail"].get("provider_id") == str(second)
    done, res, req = await _boss(owner_api)
    assert (done, req) == (True, 1) and res["status"] == "pass"


async def test_f035_all_sources_fail_reports_the_first_failure(owner_api: Api, app: Any, db: Any) -> None:
    await _src(db, "openai_compat", name="Một", rank=1)
    await _src(db, "gemini", name="Hai", rank=2)
    app.state.model_router.transport = httpx.MockTransport(lambda req: httpx.Response(500, text="hỏng"))
    out = (await owner_api.send("POST", "/boss-checks/ai_source/run", {})).json()
    assert out["status"] == "fail" and out["error_code"], out
    assert (await _boss(owner_api))[0] is False


async def test_f035_skips_source_blocked_by_missing_key_and_probes_a_real_one(owner_api: Api, app: Any,
                                                                               db: Any) -> None:
    await _src(db, "gemini", name="Không khoá", rank=1, key=False)
    real = await _src(db, "gemini", name="Có khoá", rank=2)
    app.state.model_router.transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"models": [{"name": "models/m1"}], "data": [{"id": "m1"}]}))
    out = (await owner_api.send("POST", "/boss-checks/ai_source/run", {})).json()
    assert out["status"] == "pass" and out["detail"].get("provider_id") == str(real), out


async def test_ai_source_run_messages_come_from_readiness(owner_api: Api, db: Any) -> None:
    out = (await owner_api.send("POST", "/boss-checks/ai_source/run", {})).json()
    assert (out["status"], out["error_code"]) == ("fail", "AI_NO_SOURCE")
    assert out["message"] == readiness.REASON_NO_SOURCE
    await _src(db, "gemini", name="G", models=())
    # chưa có model, nguồn khoá API: không có gì để tự lưu ⇒ nói thật là chưa có model, không gọi thử mù.
    out = (await owner_api.send("POST", "/boss-checks/ai_source/run", {})).json()
    assert out["status"] == "fail" and out["error_code"] in {"AI_NO_MODEL", "PROVIDER_ERROR", "AUTH_EXPIRED"}

