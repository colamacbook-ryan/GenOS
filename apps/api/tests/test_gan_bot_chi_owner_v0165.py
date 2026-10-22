# ruff: noqa: F811 — fixture `w` nhập từ test_p3_duty (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.65 (review P2-3) — gán/bỏ BOT cho NGƯỜI chỉ Owner + mã PIN `policy.change` (vì gán BOT bỏ qua chế độ
im lặng của tin 1-1)."""

from typing import Any

from sqlalchemy import text

from tests.conftest import Api, verify_pin
from tests.phase2 import msg
from tests.test_p3_duty import BUY, make_agent, refine, w  # noqa: F401
from tests.test_rbac_api import login_as


async def _person(db: Any, external_id: str) -> Any:
    return (await db.execute(text("SELECT person_id FROM core.person_identities WHERE external_id = :x"),
                             {"x": external_id})).scalar_one()


async def _bot_of(db: Any, person: Any) -> str | None:
    return (await db.execute(text("SELECT attrs->>'agent_id' FROM core.persons WHERE id = :p"), {"p": person})).scalar()


async def test_assigning_bot_to_a_person_needs_owner_and_pin(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    owner: Api = w["api"]
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    await refine(w, redis, msg(BUY, group=None, sender="khachA", name="Khách A"))
    pa = await _person(db, "khachA")
    body = {"agent_id": str(lan)}
    # vai có profile.write (Vận hành, Quản lý) KHÔNG được gán/bỏ BOT cho người — dù có PIN
    for role in ("operator", "manager"):
        staff = await login_as(client, db, role)
        await verify_pin(staff, "112233")
        r = await staff.send("POST", f"/directory/people/{pa}/bot", body)
        assert r.status_code == 403, (role, r.text)
        r = await staff.send("POST", "/directory/people/bulk-bot", {"agent_id": str(lan)})
        assert r.status_code == 403, (role, r.text)
        r = await staff.send("POST", f"/directory/people/{pa}/bot", {"autonomy_level": 6})
        assert r.status_code == 403, (role, r.text)
    assert await _bot_of(db, pa) is None
    # Owner chưa mở phiên PIN ⇒ 423, chưa đổi gì
    r = await owner.send("POST", f"/directory/people/{pa}/bot", body)
    assert r.status_code == 423, r.text
    r = await owner.send("POST", "/directory/people/bulk-bot", {"agent_id": str(lan)})
    assert r.status_code == 423, r.text
    assert await _bot_of(db, pa) is None
    # Owner có PIN ⇒ gán được, rồi bỏ gán cũng cần PIN
    await verify_pin(owner)
    assert (await owner.send("POST", f"/directory/people/{pa}/bot", body)).status_code == 200
    assert await _bot_of(db, pa) == str(lan)
    assert (await owner.send("POST", f"/directory/people/{pa}/bot", {"agent_id": None})).status_code == 200
    assert await _bot_of(db, pa) is None
    r = await owner.send("POST", "/directory/people/bulk-bot", {"agent_id": str(lan), "filter": {}})
    assert r.status_code == 200, r.text
    assert await _bot_of(db, pa) == str(lan)


async def test_group_bot_assignment_unchanged_for_staff(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    """Phạm vi sửa chỉ là gán BOT cho NGƯỜI (đường bỏ qua im lặng của tin 1-1): BOT của nhóm không đổi quyền."""
    op = await login_as(client, db, "operator")
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    r = await op.send("POST", f"/directory/groups/{w['g1']}/bot", {"agent_id": str(lan)})
    assert r.status_code == 200, r.text
