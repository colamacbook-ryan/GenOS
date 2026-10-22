"""v0.1.43 (F-25) — gán model: chỉ còn ba khoá lõi được ModelRouter dùng thật.

- GET /agents/bindings: core.refinery, core.reply ('Soạn lại / dịch nháp'), core.gen + agent:<id>; không còn
  core.intent/core.scoring/core.indexing (hàng cũ trong DB để nguyên, tự ẩn).
- PUT/DELETE /agents/bindings/core.intent → 422.
- Bước 4 thiết lập không còn tự gán khoá lõi (v0.1.55: hồ sơ tiêu chuẩn tự phủ).
- Dịch / soạn lại nháp không gắn agent gọi model bằng khoá core.reply.
- Bảng chi phí: khoá cũ core.reply_fast hiện nhãn tiếng Việt.
"""

import uuid
from pathlib import Path

import psycopg
from sqlalchemy import text

from gh.ai_cost import _labels
from gh.providers.clients import Message
from gh.providers.router import Routed
from tests.conftest import PG, Api
from tests.phase2 import org_id
from tests.test_p4_agents import _seed_agent, _seed_provider_model
from tests.test_ux_v0128 import _provider

CORE = {"core.refinery", "core.reply", "core.gen", "core.briefing",
        "core.owner_reply", "core.support"}  # v0.1.55 (G1): + Bản tin Gen; v0.1.72: + Sếp gọi bot ở nhóm, Chế độ hỗ trợ
DEAD = {"core.intent", "core.scoring", "core.indexing"}


class _KeyRouter:
    """Giả ModelRouter, ghi lại (agent_key, purpose) của mỗi lần gọi."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def generate(self, org_id: uuid.UUID, *, agent_key: str, purpose: str, messages: list[Message],
                       json_mode: bool = True, temperature: float = 0.2) -> Routed:
        self.calls.append((agent_key, purpose))
        return Routed("Hello", "fake", "fake-model", 10, 10)

    async def embed(self, org_id: uuid.UUID, texts: list[str]) -> list[list[float]] | None:
        return None


async def test_bindings_list_only_live_core_keys(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    aid = await _seed_agent(db, org)
    _pid, mid = await _seed_provider_model(api, db)
    # Hàng cũ của khoá đã bỏ vẫn nằm trong DB (không xoá) — danh sách tự ẩn.
    await db.execute(text("""INSERT INTO agent.bindings (org_id, agent_key, model_id, context_tokens)
                             VALUES (:o, 'core.intent', :m, 6000)"""), {"o": org, "m": mid})
    await db.commit()

    items = (await api.get("/agents/bindings")).json()["items"]
    keys = {i["agent_key"] for i in items}
    assert keys == CORE | {f"agent:{aid}"}
    assert not keys & DEAD
    labels = {i["agent_key"]: i["label"] for i in items}
    assert labels["core.reply"] == "Soạn lại / dịch nháp"

    for key in sorted(DEAD):
        r = await api.send("PUT", f"/agents/bindings/{key}", {"model_id": mid})
        assert r.status_code == 422, (key, r.text)
        r = await api.send("DELETE", f"/agents/bindings/{key}")
        assert r.status_code == 422, (key, r.text)
    still = (await db.execute(text("SELECT count(*) FROM agent.bindings WHERE org_id = :o "
                                   "AND agent_key = 'core.intent'"), {"o": org})).scalar_one()
    assert still == 1


async def test_setup_step4_writes_no_bindings(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """v0.1.55 (G2): bước 4 không còn tự gán khoá lõi nào — hồ sơ tiêu chuẩn tự phủ (gh.defaults.profiles)."""
    api: Api = owner_api
    org = await org_id(db)
    good = await _provider(api, db, "Model nội bộ", ok=True, tested=["qwen2.5-7b"])
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [good]})
    assert r.status_code == 200, r.text
    bound = set((await db.execute(text("SELECT agent_key FROM agent.bindings WHERE org_id = :o"),
                                  {"o": org})).scalars().all())
    assert bound == set()


async def test_translate_and_regenerate_without_agent_use_core_reply(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    r = await api.send("POST", "/drafts", {"kind": "message", "title": "x", "text": "Chào anh"})
    assert r.status_code in (200, 201), r.text
    d = r.json()
    router = _KeyRouter()
    app.state.model_router = router
    r = await api.send("POST", f"/drafts/{d['id']}/translate", {"lang": "en"})
    assert r.status_code == 200, r.text
    r = await api.send("POST", f"/drafts/{d['id']}/regenerate", {})
    assert r.status_code == 200, r.text
    assert router.calls == [("core.reply", "draft_translate"), ("core.reply", "draft_regenerate")]


async def test_cost_labels_map_legacy_reply_fast(db) -> None:  # type: ignore[no-untyped-def]
    got = await _labels(db, uuid.uuid4(), ["core.reply", "core.reply_fast", "core.scoring"])
    assert got == {"core.reply": "Soạn lại / dịch nháp", "core.reply_fast": "Soạn lại / dịch nháp (cũ)",
                   "core.scoring": "core.scoring"}


SQL_0028 = Path(__file__).resolve().parents[3] / "db" / "sql" / "0028_v0143_drop_dead_bindings.sql"


async def test_migration_0028_drops_dead_bindings_and_is_rerunnable(owner_api, db, fresh_db: str) -> None:  # type: ignore[no-untyped-def]
    """Hàng gán model mồ côi của khoá đã bỏ (bước 4 trước v0.1.43 tự tạo) bị dọn; khoá đang dùng giữ nguyên."""
    org = await org_id(db)
    _pid, mid = await _seed_provider_model(owner_api, db)
    for key in [*sorted(DEAD), "core.reply"]:
        await db.execute(text("""INSERT INTO agent.bindings (org_id, agent_key, model_id, context_tokens)
                                 VALUES (:o, :k, :m, 6000)"""), {"o": org, "k": key, "m": mid})
    await db.commit()
    sql = SQL_0028.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        rows = c.execute("SELECT agent_key FROM agent.bindings ORDER BY agent_key").fetchall()
    assert [r[0] for r in rows] == ["core.reply"]
