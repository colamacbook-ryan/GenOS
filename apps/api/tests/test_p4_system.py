"""Giai đoạn 4.5 (Điều khiển hệ thống: Bộ não AI, Quyền hạn, Nhật ký, Dữ liệu & lưu trữ) + 4.6 (bước 10–11).

Trọng tâm: PIN + Action Log cho mọi thay đổi quyền/ranh giới/chuỗi ưu tiên, và — quan trọng nhất — 8 khoá cứng
ARCHITECTURE §7.4 không lộ ra như một ô sửa được qua API này (kiểm cả ma trận quyền lẫn `ops.policy_boundaries`).
"""

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import OWNER, Api
from tests.phase2 import listen, org_id
from tests.test_rbac_api import login_as


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


async def _seed_person(db, org, *, name: str = "Chị Lan") -> uuid.UUID:  # type: ignore[no-untyped-def]
    pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                                    VALUES (:o, :c, :n) RETURNING id"""),
                            {"o": org, "c": f"PER-{uuid.uuid4().hex[:6]}", "n": name})).scalar_one()
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar_one()
    await db.execute(text("""INSERT INTO core.person_identities (person_id, channel_id, external_id, handle,
                                                                 phone_e164)
                             VALUES (:p, :c, :x, :h, :ph)"""),
                     {"p": pid, "c": ch, "x": f"zalo-{uuid.uuid4().hex[:8]}", "h": name, "ph": "+84900000000"})
    await db.execute(text("""INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value,
                                                                snapshot_id, updated_at)
                             VALUES ('person', :p, 'heat', 72.5, core.uuid_v7(), now())"""), {"p": pid})
    nb = (await db.execute(text("""INSERT INTO memory.notebooks (org_id, subject_type, subject_id)
                                   VALUES (:o, 'person', :p) RETURNING id"""), {"o": org, "p": pid})).scalar_one()
    await db.execute(text("""INSERT INTO memory.entries (notebook_id, section, body, author)
                             VALUES (:n, 'rolling_context', 'Đang chờ báo giá container MDF', 'agent:test')"""),
                     {"n": nb})
    await db.commit()
    return pid  # type: ignore[no-any-return]


async def _seed_provider(api: Api, name: str = "Gemini test") -> str:
    await _pin(api)   # v0.1.35 (F-20): tạo nhà cung cấp AI cần PIN `ai.route_change`
    r = await api.send("POST", "/providers", {"kind": "gemini", "name": name, "keys": ["AIza-test-key-0001"],
                                               "models": ["gemini-2.5-flash"]})
    assert r.status_code == 201, r.text
    return r.json()["id"]  # type: ignore[no-any-return]


# ─── Bộ não AI: chuỗi chuyển hướng + quy tắc chuyển hướng ───────────────────

async def test_failover_rules_static(owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/failover-rules")
    assert r.status_code == 200
    keys = {i["key"] for i in r.json()}
    assert keys == {"hết hạn mức", "ngắt mạch", "hộp Antigravity", "việc nền", "hết chuỗi", "ngưỡng cảnh báo"}


async def test_provider_chain_reorder(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    p1 = await _seed_provider(api, "Gemini")
    p2 = await _seed_provider(api, "DeepSeek")
    before = {p["id"]: p["failover_rank"] for p in (await api.get("/providers")).json()}
    assert before[p1] < before[p2]
    r = await api.send("PATCH", "/providers/chain", {"provider_ids": [p2, p1]})
    assert r.status_code == 200, r.text
    ranked = sorted(r.json(), key=lambda p: p["failover_rank"])
    assert [p["id"] for p in ranked] == [p2, p1]


async def test_provider_chain_reorder_rejects_mismatched_set(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    p1 = await _seed_provider(api, "Gemini")
    await _seed_provider(api, "DeepSeek")
    r = await api.send("PATCH", "/providers/chain", {"provider_ids": [p1]})
    assert r.status_code == 422


# ─── Quyền hạn: ma trận ──────────────────────────────────────────────────────

async def test_get_permissions_shape(owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/permissions")
    assert r.status_code == 200
    body = r.json()
    assert {c["key"] for c in body["columns"]} == {"overview", "queue", "profile", "people_review", "opportunity",
                                                    "action", "audit"}
    owner_row = next(x for x in body["roles"] if x["code"] == "owner")
    assert all(v == "all" for v in owner_row["permissions"].values())
    manager_row = next(x for x in body["roles"] if x["code"] == "manager")
    assert manager_row["permissions"]["people_review.read"] == "none"   # Q4: mặc định chỉ Owner


async def test_patch_permission_owner_can_grant_manager_people_review(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    r = await api.send("PATCH", "/permissions", {"role": "manager", "permission": "people_review.read",
                                                  "scope": "team"})
    assert r.status_code == 423   # cần PIN
    await _pin(api)
    r = await api.send("PATCH", "/permissions", {"role": "manager", "permission": "people_review.read",
                                                  "scope": "team"})
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["roles"] if x["code"] == "manager")
    assert row["permissions"]["people_review.read"] == "team"
    logs = (await api.get("/audit")).json()["items"]
    assert any(item["action"] == "permission.changed" and item["target_id"] == "manager" for item in logs)


async def test_patch_permission_rejects_permission_outside_matrix(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await _pin(api)
    r = await api.send("PATCH", "/permissions", {"role": "manager", "permission": "system.manage", "scope": "all"})
    assert r.status_code == 422


async def test_patch_permission_hard_lock_owner_always_all(owner_api) -> None:  # type: ignore[no-untyped-def]
    """Bất biến của bootstrap ("Owner luôn giữ toàn quyền") — không sửa được qua API dù có PIN đúng."""
    api: Api = owner_api
    await _pin(api)
    r = await api.send("PATCH", "/permissions", {"role": "owner", "permission": "overview.read", "scope": "team"})
    assert r.status_code == 422
    row = next(x for x in (await api.get("/permissions")).json()["roles"] if x["code"] == "owner")
    assert row["permissions"]["overview.read"] == "all"


async def test_patch_permission_hard_lock_auditor_never_writes(owner_api) -> None:  # type: ignore[no-untyped-def]
    """Bất biến của bootstrap ("Auditor không bao giờ có quyền ghi") — không sửa được qua API dù có PIN đúng."""
    api: Api = owner_api
    await _pin(api)
    r = await api.send("PATCH", "/permissions", {"role": "auditor", "permission": "queue.act", "scope": "all"})
    assert r.status_code == 422
    row = next(x for x in (await api.get("/permissions")).json()["roles"] if x["code"] == "auditor")
    assert row["permissions"]["queue.act"] == "none"


async def test_permissions_requires_roles_manage(owner_api, client, db) -> None:  # type: ignore[no-untyped-def]
    manager = await login_as(client, db, "manager")
    # Manager không có `system.read` (chỉ Owner/Auditor) — xem/sửa ma trận đều 403, dù Manager thấy mục nav.
    assert (await manager.get("/permissions")).status_code == 403
    r = await manager.send("PATCH", "/permissions", {"role": "operator", "permission": "queue.act", "scope": "team"})
    assert r.status_code == 403


# ─── Quyền hạn: nhóm lắng nghe ───────────────────────────────────────────────

async def test_listening_groups_only_lists_active(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await listen(db, org, "grp-listen", mode="silent")
    r = await owner_api.get("/listening-groups")
    assert r.status_code == 200
    assert str(gid) in {g["id"] for g in r.json()}
    assert all(g["listen_mode"] != "off" for g in r.json())


# ─── Quyền hạn: ranh giới có trách nhiệm (khoá cứng) ────────────────────────

LOCKED_CODES = {"listen_authorized_only", "hide_sensitive_below_owner", "personnel_alert_requires_evidence",
                "auto_personnel_decisions", "approval_gate", "mcp_write_requires_approval"}
UNLOCKED_CODES = {"disclose_staff_observation", "observe_external_market"}


async def test_get_boundaries_locked_flags(owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/boundaries")
    assert r.status_code == 200
    by_code = {b["code"]: b for b in r.json()}
    assert LOCKED_CODES | UNLOCKED_CODES <= set(by_code)
    for c in LOCKED_CODES:
        assert by_code[c]["locked"] is True
    for c in UNLOCKED_CODES:
        assert by_code[c]["locked"] is False
    assert by_code["auto_personnel_decisions"]["enabled"] is False
    assert by_code["listen_authorized_only"]["enabled"] is True


async def test_patch_boundary_requires_pin(owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("PATCH", "/boundaries/observe_external_market", {"enabled": False})
    assert r.status_code == 423


async def test_patch_boundary_locked_cannot_toggle(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await _pin(api)
    for code, attempt in (("listen_authorized_only", False), ("auto_personnel_decisions", True),
                         ("approval_gate", False), ("mcp_write_requires_approval", False),
                         ("hide_sensitive_below_owner", False), ("personnel_alert_requires_evidence", False)):
        r = await api.send("PATCH", f"/boundaries/{code}", {"enabled": attempt})
        assert r.status_code == 422, f"{code} lẽ ra phải bị chặn (422), được {r.status_code}"
    by_code = {b["code"]: b for b in (await api.get("/boundaries")).json()}
    assert by_code["auto_personnel_decisions"]["enabled"] is False
    assert by_code["listen_authorized_only"]["enabled"] is True


async def test_patch_boundary_threshold_editable_even_when_locked(owner_api) -> None:  # type: ignore[no-untyped-def]
    """Ranh giới `approval_gate` bị khoá bật (không tắt được), nhưng NGƯỠNG tiền là do Owner đặt (ARCHITECTURE
    §7.2) — sửa `params` phải được cho phép dù `is_locked=true`."""
    api: Api = owner_api
    await _pin(api)
    r = await api.send("PATCH", "/boundaries/approval_gate", {"params": {"approval_threshold_vnd": 20_000_000}})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True and r.json()["locked"] is True
    assert r.json()["params"]["approval_threshold_vnd"] == 20_000_000


async def test_patch_boundary_threshold_rejects_negative(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await _pin(api)
    r = await api.send("PATCH", "/boundaries/approval_gate", {"params": {"approval_threshold_vnd": -5}})
    assert r.status_code == 422


async def test_patch_boundary_unlocked_toggle_allowed(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await _pin(api)
    r = await api.send("PATCH", "/boundaries/observe_external_market", {"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    logs = (await api.get("/audit")).json()["items"]
    assert any(item["action"] == "boundary.changed" and item["target_id"] == "observe_external_market"
              for item in logs)


# ─── Nhật ký ──────────────────────────────────────────────────────────────────

async def test_system_audit_log_search_and_filters(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await _seed_provider(api, "Gemini")
    r = await api.get("/audit-log?action=provider.created")
    assert r.status_code == 200
    assert any(i["action"] == "provider.created" for i in r.json()["items"])
    r2 = await api.get("/audit-log?target_type=provider")
    assert all(i["target_type"] == "provider" for i in r2.json()["items"])


async def test_system_audit_log_export_requires_manage_and_pin(owner_api, client, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    r = await api.get("/audit-log/export")
    assert r.status_code == 423
    # v0.1.35 (F-20): tạo nhà cung cấp cần PIN → kiểm 423 của xuất nhật ký TRƯỚC khi mở phiên PIN.
    await _seed_provider(api, "Gemini")
    r = await api.get("/audit-log/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "action" in r.text.splitlines()[0]
    manager = await login_as(client, db, "manager")
    r = await manager.get("/audit-log/export")
    assert r.status_code == 403   # data.manage: chỉ Owner


# ─── Dữ liệu & lưu trữ ────────────────────────────────────────────────────────

async def test_retention_policies_default_and_patch(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    got = {r["dataset"]: r for r in (await api.get("/retention-policies")).json()}
    assert got["raw.events"]["keep_days"] is None
    r = await api.send("PATCH", "/retention-policies", {"dataset": "raw.events", "keep_days": 365,
                                                         "anonymize_after_days": 90})
    assert r.status_code == 423
    await _pin(api)
    r = await api.send("PATCH", "/retention-policies", {"dataset": "raw.events", "keep_days": 365,
                                                         "anonymize_after_days": 90, "confirm_delete": True})
    assert r.status_code == 200, r.text
    got2 = {r_["dataset"]: r_ for r_ in r.json()}
    assert got2["raw.events"]["keep_days"] == 365 and got2["raw.events"]["anonymize_after_days"] == 90


async def test_person_data_export_bundle(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    pid = await _seed_person(db, org)
    r = await api.send("POST", f"/persons/{pid}/data-requests", {"kind": "export"})
    assert r.status_code == 423
    await _pin(api)
    r = await api.send("POST", f"/persons/{pid}/data-requests", {"kind": "export"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "export" and body["status"] == "completed"
    assert body["result"]["identities"] and body["result"]["identities"][0]["phone_e164"] == "+84900000000"
    assert body["result"]["scores"][0]["dimension"] == "heat"
    assert body["result"]["notebook"][0]["section"] == "rolling_context"
    hist = (await api.get(f"/persons/{pid}/data-requests")).json()
    assert hist[0]["kind"] == "export" and hist[0]["status"] == "completed"


async def test_person_data_erase_anonymizes(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    pid = await _seed_person(db, org, name="Anh Nam")
    await _pin(api)
    r = await api.send("POST", f"/persons/{pid}/data-requests", {"kind": "erase"})
    assert r.status_code == 201, r.text
    assert r.json()["result"] == {"erased": True}
    row = (await db.execute(text("SELECT display_name, deleted_at, attrs FROM core.persons WHERE id = :i"),
                            {"i": pid})).one()
    assert row.display_name == "Người dùng đã xoá" and row.deleted_at is not None
    ident = (await db.execute(text("SELECT phone_e164, handle FROM core.person_identities WHERE person_id = :i"),
                              {"i": pid})).one()
    assert ident.phone_e164 is None and ident.handle is None
    left_scores = (await db.execute(text("SELECT count(*) FROM clean.current_scores WHERE subject_id = :i"),
                                    {"i": pid})).scalar_one()
    assert left_scores == 0
    left_notes = (await db.execute(text("""SELECT count(*) FROM memory.entries e JOIN memory.notebooks n
                                          ON n.id = e.notebook_id WHERE n.subject_id = :i"""),
                                   {"i": pid})).scalar_one()
    assert left_notes == 0
    logs = (await api.get("/audit")).json()["items"]
    assert any(item["action"] == "person_data.erase" for item in logs)


async def test_person_data_restrict_flags_person(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    pid = await _seed_person(db, org)
    await _pin(api)
    r = await api.send("POST", f"/persons/{pid}/data-requests", {"kind": "restrict"})
    assert r.status_code == 201, r.text
    attrs = (await db.execute(text("SELECT attrs FROM core.persons WHERE id = :i"), {"i": pid})).scalar_one()
    assert attrs["data_restricted"] is True


# ─── Trình thiết lập bước 10–11 ──────────────────────────────────────────────

async def test_setup_step10_invites_team_with_temp_password(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    r = await api.send("PUT", "/setup/steps/10", {"invites": [
        {"display_name": "Chị Hoa", "email": "hoa@example.vn", "role": "manager"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invited"][0]["email"] == "hoa@example.vn" and body["invited"][0]["temp_password"]
    step = next(s for s in body["steps"] if s["n"] == 10)
    assert step["status"] == "done"
    r2 = await api.send("POST", "/auth/login", {"email": "hoa@example.vn",
                                                 "password": body["invited"][0]["temp_password"]})
    assert r2.status_code == 200, r2.text
    assert (await api.get("/auth/me")).json()["must_change_password"] is True


async def test_setup_step10_rejects_duplicate_email(owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("PUT", "/setup/steps/10", {"invites": [
        {"display_name": "A", "email": "dup@example.vn", "role": "manager"},
        {"display_name": "B", "email": "dup@example.vn", "role": "operator"}]})
    assert r.status_code == 422


async def test_setup_step11_configures_backup_schedule(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    r = await api.send("PUT", "/setup/steps/11", {"frequency": "weekly", "time_of_day": "03:30",
                                                   "retention_count": 4, "destination": "local"})
    assert r.status_code == 200, r.text
    assert r.json()["backup"] == {"frequency": "weekly", "time_of_day": "03:30", "retention_count": 4,
                                  "destination": "local"}
    org = await org_id(db)
    settings = (await db.execute(text("SELECT settings FROM core.organizations WHERE id = :o"),
                                 {"o": org})).scalar_one()
    assert settings["backup"]["destination"] == "local"


@pytest.mark.parametrize("dest", ["s3", "minio"])
async def test_setup_step11_rejects_remote_destination(owner_api, db, dest) -> None:  # type: ignore[no-untyped-def]
    """v0.1.40 (F-12): chỉ 'local' — S3/MinIO chưa có đường sao lưu thật ⇒ 422 tiếng Việt, không lưu gì."""
    r = await owner_api.send("PUT", "/setup/steps/11", {"frequency": "daily", "time_of_day": "02:00",
                                                         "destination": dest})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "VALIDATION"
    assert body["errors"]["destination"].startswith("Hiện chỉ sao lưu trên máy chủ này")
    settings = (await db.execute(text("SELECT settings FROM core.organizations WHERE id = :o"),
                                 {"o": await org_id(db)})).scalar_one()
    assert (settings.get("backup") or {}).get("destination") in (None, "local")


async def test_setup_step11_rejects_bad_time(owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("PUT", "/setup/steps/11", {"time_of_day": "25:99"})
    assert r.status_code == 422
