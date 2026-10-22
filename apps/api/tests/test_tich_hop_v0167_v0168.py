"""Tích hợp v0.1.67 (Lưu trữ agent, tên agent duy nhất) × v0.1.68 (trình thiết lập nói thật) — chỗ hai bản gặp nhau:
- agent đã Lưu trữ KHÔNG còn là «agent của bước 8» (làm lại bước 8 tạo agent mới, không sửa agent đã cất);
- theo dõi bước 8 / 9 «xong» chỉ tính agent chưa Lưu trữ;
- bước 8 không tạo / đổi sang tên trùng agent đang dùng (agent Lưu trữ thì không tính)."""

from typing import Any

from sqlalchemy import text

from tests.conftest import Api


async def _followup(api: Api) -> dict[int, dict[str, Any]]:
    return {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}


async def _step8(api: Api, name: str, role: str = "Chăm khách") -> Any:
    return await api.send("PUT", "/setup/steps/8", {"name": name, "role_desc": role, "template": "secretary"})


async def _insert_agent(db: Any, name: str, *, archived: bool = False) -> None:
    await db.execute(text("""INSERT INTO agent.identities (org_id, name, role_desc, addressing, voice, speak_when,
                                                           autonomy_level, is_enabled, archived_at)
                             SELECT id, :n, 'Làm việc', '{}'::jsonb, 'lịch sự', 'khi được hỏi', 4, :en,
                                    CASE WHEN :ar THEN now() END FROM core.organizations LIMIT 1"""),
                     {"n": name, "en": not archived, "ar": archived})
    await db.commit()


async def test_followup_8_9_count_only_unarchived_agents(owner_api: Api, db: Any) -> None:
    """Dữ liệu thật (agent tạo ở Console, không qua bước 8): chỉ agent đã Lưu trữ ⇒ việc 8 / 9 chưa xong."""
    api: Api = owner_api
    fu = await _followup(api)
    assert fu[8]["done"] is False and fu[9]["done"] is False
    await _insert_agent(db, "Cất đi", archived=True)
    fu = await _followup(api)
    assert fu[8]["done"] is False and fu[9]["done"] is False
    await _insert_agent(db, "Đang dùng")
    fu = await _followup(api)
    assert fu[8]["done"] is True and fu[9]["done"] is True


async def test_archived_setup_agent_is_not_reused_by_step8(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    r = await _step8(api, "Mai")
    assert r.status_code == 200, r.text
    first = r.json()["agent"]["id"]
    await db.execute(text("UPDATE agent.identities SET archived_at = now(), is_enabled = false"))
    await db.commit()
    r = await _step8(api, "Mai")                                       # tên của agent đã Lưu trữ đặt lại được
    assert r.status_code == 200, r.text
    second = r.json()["agent"]["id"]
    assert second != first                                             # không «sửa» agent đã cất đi
    await db.rollback()
    rows = (await db.execute(text("SELECT id, archived_at FROM agent.identities"))).all()
    assert len(rows) == 2 and sum(1 for x in rows if x.archived_at is None) == 1
    assert str(next(x.id for x in rows if x.archived_at is None)) == second


async def test_step9_targets_the_live_agent_not_the_archived_one(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    assert (await _step8(api, "Cũ")).status_code == 200
    await db.execute(text("UPDATE agent.identities SET archived_at = now(), is_enabled = false"))
    await db.commit()
    new_id = (await _step8(api, "Mới")).json()["agent"]["id"]
    r = await api.send("PUT", "/setup/steps/9", {"ack_boundaries": True})
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["id"] == new_id


async def test_step8_rejects_a_name_already_used_by_a_live_agent(owner_api: Api, db: Any) -> None:
    api: Api = owner_api
    assert (await _step8(api, "Lan")).status_code == 200
    await _insert_agent(db, "Hoa")
    r = await _step8(api, " hoa ")      # làm lại bước 8 đổi sang tên của agent KHÁC (không phân biệt hoa thường)
    assert r.status_code == 422, r.text
    assert "Đã có agent tên" in r.text
    r = await _step8(api, "LAN")                  # giữ tên của chính agent bước 8 ⇒ không tự va chạm
    assert r.status_code == 200, r.text
