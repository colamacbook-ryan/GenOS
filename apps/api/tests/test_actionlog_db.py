"""Action Log: chuỗi băm, phát hiện sửa ngầm; kho thô và nhật ký chỉ INSERT (khoá cứng 5)."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from gh.bootstrap import bootstrap
from gh.chassis import actionlog


async def _org(db) -> uuid.UUID:  # type: ignore[no-untyped-def]
    res = await bootstrap(db)
    await db.commit()
    return res.org_id


async def test_chain_verifies_and_detects_tampering(db) -> None:  # type: ignore[no-untyped-def]
    org = await _org(db)
    for i in range(5):
        await actionlog.record(db, org_id=org, actor_type="user", actor_id="user:x", action=f"test.{i}",
                               detail={"i": i, "vi": "Tiếng Việt có dấu"}, ip="10.0.0.1")
    await db.commit()
    report = await actionlog.verify_chain(db, org)
    assert report.ok and report.checked == 5

    target = (await db.execute(text("SELECT id FROM ops.action_log WHERE action = 'test.2'"))).scalar_one()
    # Mô phỏng kẻ có quyền superuser tắt trigger để sửa ngầm — ALTER TABLE ... DISABLE TRIGGER cần quyền chủ
    # bảng/superuser, `db` (role `gh_app` khi GH_TEST_APP_ROLE=1 — mục v0.1.1/1a) không có, nên phải qua
    # admin_sessionmaker() thật (đúng vai trò kẻ tấn công giả định trong kịch bản này).
    from gh.db import admin_sessionmaker

    async with admin_sessionmaker()() as adm:
        await adm.execute(text("ALTER TABLE ops.action_log DISABLE TRIGGER USER"))
        await adm.execute(text("UPDATE ops.action_log SET detail = '{\"i\": 99}' WHERE id = :i"), {"i": target})
        await adm.execute(text("ALTER TABLE ops.action_log ENABLE TRIGGER USER"))
        await adm.commit()
    report = await actionlog.verify_chain(db, org)
    assert not report.ok
    assert report.broken_at == str(target)
    assert report.checked == 2


async def test_chain_is_linear_under_concurrency(fresh_db: str) -> None:
    import asyncio

    from gh.db import sessionmaker

    async with sessionmaker()() as s:
        org = await _org(s)

    async def writer(n: int) -> None:
        async with sessionmaker()() as s:
            await actionlog.record(s, org_id=org, actor_type="system", actor_id="system:t", action=f"c.{n}")
            await s.commit()

    await asyncio.gather(*(writer(i) for i in range(20)))
    async with sessionmaker()() as s:
        report = await actionlog.verify_chain(s, org)
    assert report.ok and report.checked == 20


@pytest.mark.parametrize("sql", [
    "UPDATE ops.action_log SET action = 'x'",
    "DELETE FROM ops.action_log",
    "TRUNCATE ops.action_log",
])
async def test_action_log_is_insert_only(db, sql: str) -> None:  # type: ignore[no-untyped-def]
    org = await _org(db)
    await actionlog.record(db, org_id=org, actor_type="system", actor_id="system:t", action="a")
    await db.commit()
    # TRUNCATE: role ứng dụng gh_app (GH_TEST_APP_ROLE=1 — mục v0.1.1/1a) không có quyền TRUNCATE (chỉ cấp
    # SELECT/INSERT/UPDATE/DELETE — migration 0014, cố ý không cấp TRUNCATE) nên bị chặn NGAY từ tầng quyền,
    # trước khi chạm tới trigger append-only — một lớp phòng thủ nữa, không phải hồi quy.
    match = "append-only" if sql != "TRUNCATE ops.action_log" else "append-only|permission denied"
    with pytest.raises(DBAPIError, match=match):
        await db.execute(text(sql))
    await db.rollback()


@pytest.mark.parametrize("sql", [
    "UPDATE raw.events SET body_text = 'sửa'",
    "DELETE FROM raw.events",
    "TRUNCATE raw.events",
])
async def test_raw_store_is_insert_only(db, sql: str) -> None:  # type: ignore[no-untyped-def]
    await _org(db)
    cols = (await db.execute(text("""
        SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
        WHERE table_schema = 'raw' AND table_name = 'events' ORDER BY ordinal_position"""))).all()
    required = [c for c in cols if c.is_nullable == "NO" and c.column_default is None]
    values = {"uuid": "core.uuid_v7()", "text": "'x'", "jsonb": "'{}'", "timestamp with time zone": "now()",
              "bytea": "'\\x00'", "integer": "0", "smallint": "0", "bigint": "0", "boolean": "false"}
    names = ", ".join(c.column_name for c in required)
    vals = ", ".join(values.get(c.data_type, "'x'") for c in required)
    await db.execute(text(f"INSERT INTO raw.events ({names}) VALUES ({vals})"))
    await db.commit()
    # Xem chú thích ở test_action_log_is_insert_only: TRUNCATE bị chặn sớm hơn (thiếu quyền) khi chạy bằng gh_app.
    match = "append-only" if sql != "TRUNCATE raw.events" else "append-only|permission denied"
    with pytest.raises(DBAPIError, match=match):
        await db.execute(text(sql))
    await db.rollback()


async def test_personnel_alert_requires_evidence(db) -> None:  # type: ignore[no-untyped-def]
    org = await _org(db)
    with pytest.raises(DBAPIError):
        await db.execute(text("""INSERT INTO biz.alerts (org_id, code, alert_type, title, personnel_related)
                                 VALUES (:o, 'ALR-0001', 'slow_response', 'x', true)"""), {"o": org})
    await db.rollback()


async def _set_scope(db, role: str, perm: str, scope: str) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""UPDATE core.role_permissions rp SET scope = :s FROM core.roles r
                             WHERE rp.role_id = r.id AND r.code = :r AND rp.permission_code = :p"""),
                     {"s": scope, "r": role, "p": perm})


async def test_bootstrap_is_idempotent_and_restores_hard_locks(db) -> None:  # type: ignore[no-untyped-def]
    org = await _org(db)
    # Ai đó sửa DB trực tiếp: tắt khoá cứng, cho Auditor quyền ghi, hạ quyền Owner.
    await db.execute(text("UPDATE ops.policy_boundaries SET is_enabled = true WHERE code = 'auto_personnel_decisions'"))
    await db.execute(text("UPDATE ops.policy_boundaries SET is_enabled = false WHERE code = 'listen_authorized_only'"))
    await _set_scope(db, "auditor", "action.approve", "all")
    await _set_scope(db, "owner", "audit.read", "none")
    # Owner chỉnh hợp lệ một ô không khoá: cho Manager xem đánh giá nhân sự.
    await _set_scope(db, "manager", "people_review.read", "team")
    await db.commit()
    again = await bootstrap(db)
    await db.commit()
    assert again.org_id == org
    b = dict((await db.execute(text("SELECT code, is_enabled FROM ops.policy_boundaries"))).all())
    assert b["auto_personnel_decisions"] is False and b["listen_authorized_only"] is True
    scope = dict((await db.execute(text("""
        SELECT r.code || ':' || rp.permission_code, rp.scope FROM core.role_permissions rp
        JOIN core.roles r ON r.id = rp.role_id"""))).all())
    assert scope["auditor:action.approve"] == "none"
    assert scope["owner:audit.read"] == "all"
    assert scope["manager:people_review.read"] == "team"      # chỉnh của Owner được giữ
    assert (await db.execute(text("SELECT count(*) FROM core.organizations"))).scalar() == 1


async def test_nightly_verify_raises_owner_alert_on_break(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from gh.worker import partition_maintenance, verify_action_log

    await partition_maintenance({})
    ok = await verify_action_log({})
    assert all(v["ok"] for v in ok.values())
    # Cùng lý do ở test_chain_verifies_and_detects_tampering: cần superuser để tắt trigger.
    from gh.db import admin_sessionmaker

    async with admin_sessionmaker()() as adm:
        await adm.execute(text("ALTER TABLE ops.action_log DISABLE TRIGGER USER"))
        await adm.execute(text("UPDATE ops.action_log SET action = 'sua.ngam' WHERE action = 'setup.owner_created'"))
        await adm.execute(text("ALTER TABLE ops.action_log ENABLE TRIGGER USER"))
        await adm.commit()
    bad = await verify_action_log({})
    assert not any(v["ok"] for v in bad.values())
    alert = (await db.execute(text("SELECT alert_type, priority, recipient_user_id, evidence FROM biz.alerts"))).one()
    assert alert.alert_type == "data_conflict" and alert.priority == "P1" and alert.recipient_user_id
    assert alert.evidence[0]["type"] == "action_log"
