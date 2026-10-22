"""v0.1.45 (F-20) — PIN đúng chỗ hạ rào.

- PATCH /agents/{id}: PIN `policy.change` CHỈ khi đổi autonomy_level / forbidden / limits / channel_scopes (khác giá
  trị đang lưu). Đổi tên/mô tả… hay gửi lại đúng giá trị cũ không đòi PIN.
- POST /cli/login: PIN `cli.switch_account` trước khi ghi nhật ký / khởi động CLI.
- Hướng dẫn bước 9 sau Hoàn tất: PIN `policy.change`; bước 10 sau Hoàn tất với danh sách mời khác rỗng: PIN
  `user.manage`. Đang thiết lập lần đầu (chưa Hoàn tất) không đòi PIN.
- Đối chứng âm: cập nhật hệ thống và "Sao lưu ngay" KHÔNG gắn PIN.
- Thứ tự: 403 (Manager/Auditor) trước 423.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from tests.conftest import Api, verify_pin
from tests.phase2 import listen, org_id
from tests.test_rbac_api import login_as

PIN = "PIN_REQUIRED"


async def _seed(db) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Agent (mức 4, 1 điều cấm, giới hạn mặc định, phạm vi zalo · nhóm g1) + một kênh telegram để đổi phạm vi."""
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    zalo = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                             {"o": org})).scalar_one()
    aid = (await db.execute(text("""
        INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when, forbidden,
                                      autonomy_level, is_enabled)
        VALUES (:o, 'Trợ lý thương mại', 'Báo giá', 'commercial', '{}'::jsonb, 'lễ phép', 'khi được hỏi',
                ARRAY['không tự cam kết giá'], 4, true) RETURNING id"""), {"o": org})).scalar_one()
    await db.execute(text("INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id) VALUES (:a, :c, :g)"),
                     {"a": aid, "c": zalo, "g": gid})
    await db.commit()
    return {"org": org, "agent": aid, "zalo": str(zalo), "group": str(gid)}


def _patch_cases(s: dict[str, Any]) -> list[tuple[str, dict[str, Any], bool]]:
    """(tên, thân PATCH, cần PIN?)."""
    same_scope = [{"channel_id": s["zalo"], "group_id": s["group"]}]
    return [
        ("autonomy_level khác", {"autonomy_level": 3}, True),
        ("forbidden khác", {"forbidden": ["không tự cam kết giá", "không huỷ đơn"]}, True),
        ("limits khác", {"limits": {"decisions_per_min": 5}}, True),
        ("channel_scopes khác", {"channel_scopes": [{"channel_id": s["zalo"]}]}, True),
        ("channel_scopes rỗng", {"channel_scopes": []}, True),
        ("chỉ đổi tên", {"name": "Tên mới"}, False),
        ("đổi mô tả, giọng, lúc nói", {"role_desc": "Mô tả mới", "voice": "vui", "speak_when": "luôn"}, False),
        ("autonomy_level bằng mức hiện tại + tên mới", {"autonomy_level": 4, "name": "Tên mới 2"}, False),
        ("forbidden gửi lại (khác khoảng trắng)", {"forbidden": ["  không tự cam kết giá ", ""]}, False),
        ("limits gửi lại mặc định", {"limits": {"decisions_per_min": 20}}, False),
        ("channel_scopes gửi lại y nguyên", {"channel_scopes": same_scope}, False),
    ]


CASE_NAMES = [c[0] for c in _patch_cases({"zalo": "", "group": ""})]


@pytest.mark.parametrize("case", CASE_NAMES)
async def test_patch_agent_pin_table_without_pin(owner_api: Api, db, case: str) -> None:  # type: ignore[no-untyped-def]
    s = await _seed(db)
    name, body, needs_pin = next(c for c in _patch_cases(s) if c[0] == case)
    before = (await owner_api.get(f"/agents/{s['agent']}")).json()
    r = await owner_api.send("PATCH", f"/agents/{s['agent']}", body)
    if needs_pin:
        assert r.status_code == 423, (name, r.text)
        assert r.json()["code"] == PIN
        after = (await owner_api.get(f"/agents/{s['agent']}")).json()
        assert after == before, name  # không ghi gì
    else:
        assert r.status_code == 200, (name, r.text)


@pytest.mark.parametrize("case", CASE_NAMES)
async def test_patch_agent_pin_table_with_pin(owner_api: Api, db, case: str) -> None:  # type: ignore[no-untyped-def]
    s = await _seed(db)
    _, body, _needs = next(c for c in _patch_cases(s) if c[0] == case)
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", f"/agents/{s['agent']}", body)
    assert r.status_code == 200, r.text
    out = r.json()
    if "autonomy_level" in body:
        assert out["autonomy_level"] == body["autonomy_level"]
    if "limits" in body:
        assert out["limits"]["decisions_per_min"] == body["limits"]["decisions_per_min"]


async def test_patch_agent_validation_before_pin(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """422 (kênh không tồn tại) đứng trước 423; 404 cũng trước 423."""
    s = await _seed(db)
    r = await owner_api.send("PATCH", f"/agents/{s['agent']}", {"channel_scopes": [{"channel_id": str(uuid.uuid4())}]})
    assert r.status_code == 422, r.text
    r = await owner_api.send("PATCH", f"/agents/{uuid.uuid4()}", {"autonomy_level": 1})
    assert r.status_code == 404, r.text


async def test_cli_login_needs_pin_and_starts_nothing(owner_api: Api, app, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", "/cli/login")
    assert r.status_code == 423, r.text
    assert r.json()["code"] == PIN
    assert app.state.cli_logins.sessions == {}
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'cli.login_started'"))).scalar_one()
    assert n == 0
    r = await owner_api.send("POST", "/cli/login?kind=claude_code_cli")
    assert r.status_code == 423, r.text


async def test_cli_login_with_pin_starts(owner_api: Api, app) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/cli/login")
    assert r.status_code == 202, r.text
    login_id = r.json()["login_id"]
    # Cùng phiên đăng nhập: trạng thái / huỷ không đòi PIN lại (route không gắn PIN).
    assert (await owner_api.get(f"/cli/login/{login_id}")).status_code == 200
    assert (await owner_api.send("POST", f"/cli/login/{login_id}/cancel")).status_code == 204


async def _finish(api: Api) -> None:
    assert (await api.send("POST", "/setup/steps/4/skip")).status_code == 200
    r = await api.send("PUT", "/setup/steps/12")
    assert r.status_code == 200, r.text
    assert r.json()["finished"] is True


INVITE = {"display_name": "Lan", "email": "lan@example.vn", "role": "operator"}


async def test_setup_9_10_after_finish_need_pin(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    s = await _seed(db)
    await _finish(owner_api)
    r = await owner_api.send("PUT", "/setup/steps/9", {"autonomy_level": 3, "ack_boundaries": True})
    assert r.status_code == 423, r.text
    assert r.json()["code"] == PIN
    lvl = (await db.execute(text("SELECT autonomy_level FROM agent.identities WHERE id = :i"),
                            {"i": s["agent"]})).scalar_one()
    assert lvl == 4
    # 422 trước 423 (chưa xác nhận ranh giới).
    r = await owner_api.send("PUT", "/setup/steps/9", {"autonomy_level": 3, "ack_boundaries": False})
    assert r.status_code == 422, r.text

    r = await owner_api.send("PUT", "/setup/steps/10", {"invites": [INVITE]})
    assert r.status_code == 423, r.text
    assert r.json()["code"] == PIN
    n = (await db.execute(text("SELECT count(*) FROM core.users WHERE email = 'lan@example.vn'"))).scalar_one()
    assert n == 0
    # Danh sách rỗng sau Hoàn tất: không đòi PIN (v0.1.68, F038: và cũng không phải "đã mời" ⇒ 409, không phải 423).
    r = await owner_api.send("PUT", "/setup/steps/10", {"invites": []})
    assert r.status_code == 409 and r.json()["code"] == "STEP_INCOMPLETE", r.text

    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/setup/steps/9", {"autonomy_level": 3, "ack_boundaries": True})
    assert r.status_code == 200, r.text
    r = await owner_api.send("PUT", "/setup/steps/10", {"invites": [INVITE]})
    assert r.status_code == 200, r.text
    assert [i["email"] for i in r.json()["invited"]] == ["lan@example.vn"]


async def test_setup_9_after_finish_same_level_no_pin_and_targets_setup_agent(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Sửa review v0.1.45: mở lại bước 9 chỉ để xác nhận ranh giới (mức không đổi / null) KHÔNG đòi PIN và KHÔNG đổi
    agent khác; bước 9 nhắm đúng agent của bước 8, không phải agent mới tạo gần nhất."""
    s = await _seed(db)
    org = s["org"]
    await db.execute(text("""UPDATE ops.setup_state SET completed = jsonb_set(completed, '{setup_agent_id}',
                                    to_jsonb(CAST(:a AS text))) WHERE org_id = :o"""), {"a": str(s["agent"]), "o": org})
    newer = (await db.execute(text("""
        INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when, forbidden,
                                      autonomy_level, is_enabled, created_at)
        VALUES (:o, 'Agent mới', 'Khác', 'commercial', '{}'::jsonb, 'lễ phép', 'khi được hỏi', ARRAY[]::text[], 1, true,
                now() + interval '1 minute') RETURNING id"""), {"o": org})).scalar_one()
    await db.commit()
    await _finish(owner_api)

    r = await owner_api.get("/setup/steps/9")
    assert r.status_code == 200, r.text
    assert r.json()["agent"] == {"id": str(s["agent"]), "name": "Trợ lý thương mại", "autonomy_level": 4}
    for body in ({"autonomy_level": 4, "ack_boundaries": True}, {"autonomy_level": None, "ack_boundaries": True}):
        r = await owner_api.send("PUT", "/setup/steps/9", body)
        assert r.status_code == 200, (body, r.text)
        assert r.json()["agent"]["id"] == str(s["agent"]) and r.json()["agent"]["autonomy_level"] == 4
    lv = dict((await db.execute(text("SELECT id, autonomy_level FROM agent.identities WHERE org_id = :o"),
                                {"o": org})).all())
    assert lv[s["agent"]] == 4 and lv[newer] == 1
    # Đổi mức thật → vẫn cần PIN.
    r = await owner_api.send("PUT", "/setup/steps/9", {"autonomy_level": 3, "ack_boundaries": True})
    assert r.status_code == 423, r.text


async def test_setup_9_10_before_finish_no_pin(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Đang thiết lập lần đầu (chưa Hoàn tất): hành vi cũ, không đòi PIN."""
    await _seed(db)
    r = await owner_api.send("PUT", "/setup/steps/9", {"autonomy_level": 3, "ack_boundaries": True})
    assert r.status_code == 200, r.text
    r = await owner_api.send("PUT", "/setup/steps/10", {"invites": [INVITE]})
    assert r.status_code == 200, r.text


async def test_update_and_backup_never_need_pin(owner_api: Api) -> None:
    """Đối chứng âm: theo kế hoạch đã bỏ cập nhật và sao lưu khỏi danh sách PIN."""
    for method, path in (("POST", "/system/update"), ("POST", "/system/update/check"), ("POST", "/system/backups")):
        r = await owner_api.send(method, path)
        assert r.status_code != 423, (path, r.text)


@pytest.mark.parametrize("role", ["manager", "auditor"])
async def test_forbidden_before_pin(client, db, owner_api: Api, role: str) -> None:  # type: ignore[no-untyped-def]
    s = await _seed(db)
    member = await login_as(client, db, role)
    checks = [
        ("PATCH", f"/agents/{s['agent']}", {"autonomy_level": 1}),
        ("POST", "/cli/login", None),
        ("PUT", "/setup/steps/9", {"autonomy_level": 3, "ack_boundaries": True}),
        ("PUT", "/setup/steps/10", {"invites": [INVITE]}),
    ]
    await _finish(owner_api)
    for method, path, body in checks:
        r = await member.send(method, path, body)
        assert r.status_code == 403, (role, path, r.text)
