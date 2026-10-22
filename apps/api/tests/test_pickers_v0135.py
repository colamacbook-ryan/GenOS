"""v0.1.35 (F-1): ô chọn người / trợ lý thật (`/pickers/users`, `/pickers/agents`) + chặn UUID lạ ở các thao tác
giao/gán (Hộp thư, Vụ việc, BOT nhóm/người) — 404 thay vì 500, id giả kiểu 'u-lan' → 422 VALIDATION."""

import uuid

import pytest
from sqlalchemy import text

from gh.db import sessionmaker
from tests.conftest import Api, verify_pin
from tests.phase2 import listen, msg, org_id, put
from tests.test_rbac_api import add_user, login_as

ROLES = ("owner", "manager", "operator", "agent_staff", "auditor")
AGENTS_OK = {"owner", "manager", "operator", "agent_staff"}


async def _agent(db, org, name: str, *, enabled: bool = True) -> uuid.UUID:  # type: ignore[no-untyped-def]
    aid = (await db.execute(text("""INSERT INTO agent.identities (org_id, name, role_desc, template,
        addressing, voice, speak_when, forbidden, autonomy_level, is_enabled)
        VALUES (:o, :n, 'Báo giá', 'commercial', '{}', 'lễ phép', 'khi được tag', '{}', 3, :e)
        RETURNING id"""), {"o": org, "n": name, "e": enabled})).scalar_one()
    await db.commit()
    return aid


async def _uid(db, email: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT id FROM core.users WHERE email = :e"), {"e": email})).scalar_one()


@pytest.fixture
async def world(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    """Org đã khởi tạo, nhóm g1, một khách than phiền (một đơn vị trong Hộp thư), một người dùng 'Chị Lan Phạm'
    đang hoạt động, một người bị khoá, một agent bật + một agent tắt."""
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    [raw] = await put(sessionmaker(), org, msg("Sao chưa ai trả lời, chán quá", sender="a1", name="Chị Hà"))
    await db.commit()
    person = (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                       JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                       WHERE e.id = :r"""), {"r": raw})).scalar_one()
    unit = (await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id,
                                                                      event_type, conclusion, confidence, run_id)
        VALUES (:o, now(), :g, :p, 'Complained', 'Than phiền chưa ai trả lời', 0.95, core.uuid_v7())
        RETURNING id"""), {"o": org, "g": gid, "p": person})).scalar_one()
    await db.commit()
    lan = await _uid(db, await add_user(db, "operator"))
    await db.execute(text("UPDATE core.users SET display_name = 'Chị Lan Phạm' WHERE id = :u"), {"u": lan})
    locked = await _uid(db, await add_user(db, "manager"))
    await db.execute(text("UPDATE core.users SET display_name = 'Người đã khoá', is_active = false WHERE id = :u"),
                     {"u": locked})
    await db.commit()
    on = await _agent(db, org, "Trợ lý thương mại")
    off = await _agent(db, org, "Trợ lý đã tắt", enabled=False)
    return {"org": org, "group": gid, "person": person, "unit": unit, "lan": lan, "locked": locked,
            "agent": on, "agent_off": off}


# ─── bảng quyền ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ROLES)
async def test_picker_permissions_by_role(app, owner_api: Api, client, db, role: str) -> None:  # type: ignore[no-untyped-def]
    api = owner_api if role == "owner" else await login_as(client, db, role)
    users = await api.get("/pickers/users")
    assert users.status_code == 200, users.text
    agents = await api.get("/pickers/agents")
    assert agents.status_code == (200 if role in AGENTS_OK else 403), agents.text
    if role not in AGENTS_OK:
        assert agents.json()["code"] == "FORBIDDEN"


async def test_picker_users_hide_email_role_and_locked_users(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    items = (await owner_api.get("/pickers/users")).json()["items"]
    assert all(set(i) == {"id", "name", "me"} for i in items)
    names = [i["name"] for i in items]
    assert "Chị Lan Phạm" in names and "Người đã khoá" not in names
    assert str(world["locked"]) not in {i["id"] for i in items}
    mine = [i for i in items if i["me"]]
    assert len(mine) == 1
    owner_id = (await db.execute(text("""SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
                                         JOIN core.roles r ON r.id = ur.role_id WHERE r.code = 'owner'"""))
                ).scalar_one()
    assert mine[0]["id"] == str(owner_id)
    assert next(i for i in items if i["name"] == "Chị Lan Phạm")["me"] is False
    assert "email" not in str(items) and "@" not in str(items)


async def test_picker_users_reports_truncation(world, owner_api: Api, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Vượt trần → `truncated: true` (không im lặng mất người); dưới trần → false."""
    from gh.biz.core import pickers

    body = (await owner_api.get("/pickers/users")).json()
    assert body["truncated"] is False and len(body["items"]) >= 2
    monkeypatch.setattr(pickers, "PICKER_USERS_LIMIT", 1)
    body = (await owner_api.get("/pickers/users")).json()
    assert body["truncated"] is True and len(body["items"]) == 1


async def test_picker_agents_only_enabled(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    items = (await owner_api.get("/pickers/agents")).json()["items"]
    assert all(set(i) == {"id", "name"} for i in items)
    ids = {i["id"] for i in items}
    assert str(world["agent"]) in ids and str(world["agent_off"]) not in ids


# ─── Hộp thư «Giao cho người khác» ───────────────────────────────────────────

async def test_inbox_assign_real_user_saves_uuid(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", f"/inbox/{world['unit']}/assign", {"user_id": str(world["lan"])})
    assert r.status_code == 200, r.text
    assert r.json()["assigned_to"] == {"id": str(world["lan"]), "name": "Chị Lan Phạm"}
    saved = (await db.execute(text("""SELECT user_id FROM core.assignments WHERE subject_type = 'queue'
                                      AND subject_id = :i AND active_to IS NULL"""),
                              {"i": world["unit"]})).scalar_one()
    assert saved == world["lan"]


async def test_inbox_assign_fake_id_is_422_and_unknown_uuid_404(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", f"/inbox/{world['unit']}/assign", {"user_id": "u-lan"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION"
    assert "user_id" in r.json()["errors"]
    r2 = await owner_api.send("POST", f"/inbox/{world['unit']}/assign", {"user_id": str(uuid.uuid4())})
    assert r2.status_code == 404 and r2.json()["code"] == "NOT_FOUND"
    r3 = await owner_api.send("POST", f"/inbox/{world['unit']}/assign", {"user_id": str(world["locked"])})
    assert r3.status_code == 404


async def test_soft_deleted_active_user_cannot_be_assigned(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Người dùng xoá mềm (deleted_at) dù is_active vẫn true: không có trong /pickers/users ⇒ cũng không gán được
    qua API (Hộp thư, Vụ việc) — khớp ô chọn."""
    await db.execute(text("UPDATE core.users SET deleted_at = now() WHERE id = :u"), {"u": world["lan"]})
    await db.commit()
    ids = {i["id"] for i in (await owner_api.get("/pickers/users")).json()["items"]}
    assert str(world["lan"]) not in ids
    r = await owner_api.send("POST", f"/inbox/{world['unit']}/assign", {"user_id": str(world["lan"])})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", r.text
    r = await owner_api.send("POST", "/cases", {"title": "Giao trễ", "assignee_user_id": str(world["lan"])})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", r.text
    # Người phụ trách hồ sơ (PATCH /profile) cũng dùng chung điều kiện.
    r = await owner_api.send("PATCH", f"/profile/{world['person']}", {"owner_user_id": str(world["lan"])})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", r.text
    owner_id = (await owner_api.get("/auth/me")).json()["id"]
    r = await owner_api.send("PATCH", f"/profile/{world['person']}", {"owner_user_id": owner_id})
    assert r.status_code == 200, r.text


# ─── Vụ việc «Gán người xử lý» ───────────────────────────────────────────────

async def test_case_assignee_unknown_uuid_404_real_user_200(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    bad = await owner_api.send("POST", "/cases", {"title": "Giao trễ", "assignee_user_id": str(uuid.uuid4())})
    assert bad.status_code == 404 and bad.json()["code"] == "NOT_FOUND", bad.text
    case = (await owner_api.send("POST", "/cases", {"title": "Giao trễ"})).json()
    r = await owner_api.send("PATCH", f"/cases/{case['id']}", {"assignee_user_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", r.text
    r = await owner_api.send("PATCH", f"/cases/{case['id']}", {"assignee_user_id": "u-lan"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION"
    r = await owner_api.send("PATCH", f"/cases/{case['id']}", {"assignee_user_id": str(world["lan"])})
    assert r.status_code == 200, r.text
    saved = (await db.execute(text("SELECT assignee_user_id FROM biz.cases WHERE id = :i"),
                              {"i": case["id"]})).scalar_one()
    assert saved == world["lan"]
    # "Chưa gán" vẫn được.
    r = await owner_api.send("PATCH", f"/cases/{case['id']}", {"assignee_user_id": None})
    assert r.status_code == 200, r.text
    ok = await owner_api.send("POST", "/cases", {"title": "Hỏi lại", "assignee_user_id": str(world["lan"])})
    assert ok.status_code == 201, ok.text


# ─── BOT nhóm / người ────────────────────────────────────────────────────────

async def test_group_bot_foreign_agent_404_real_agent_200(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", f"/directory/groups/{world['group']}/bot", {"agent_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", r.text
    r = await owner_api.send("POST", f"/directory/groups/{world['group']}/bot", {"agent_id": "agent-ka"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION"
    r = await owner_api.send("POST", f"/directory/groups/{world['group']}/bot", {"agent_id": str(world["agent"])})
    assert r.status_code == 200, r.text
    saved = (await db.execute(text("SELECT assigned_agent_id FROM core.groups WHERE id = :g"),
                              {"g": world["group"]})).scalar_one()
    assert saved == world["agent"]
    r = await owner_api.send("POST", f"/directory/groups/{world['group']}/bot", {"agent_id": None})
    assert r.status_code == 200, r.text


async def test_person_bot_foreign_agent_404(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # review v0.1.65 P2-3: gán BOT cho người cần Owner + PIN policy.change
    r = await owner_api.send("POST", f"/directory/people/{world['person']}/bot", {"agent_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", r.text
    r = await owner_api.send("POST", f"/directory/people/{world['person']}/bot", {"agent_id": str(world["agent"])})
    assert r.status_code == 200, r.text
    attrs = (await db.execute(text("SELECT attrs FROM core.persons WHERE id = :p"),
                              {"p": world["person"]})).scalar_one()
    assert attrs["agent_id"] == str(world["agent"])
