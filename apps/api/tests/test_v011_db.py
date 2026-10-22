"""v0.1.1 mục 1a — migration 0014 (ARCHITECTURE §8.3, PLAN §5.6 lỗi 🟠/🟡).

Kiểm 3 việc: 2 chỉ mục còn thiếu, thuộc tính + GRANT của role `gh_app` (ứng dụng, không superuser), và dọn
core.sessions hết hạn/thu hồi quá hạn (gh/worker.py::expire_sessions).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from gh.auth import service as auth_service


async def test_indexes_from_0014_exist(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    rows = (await db.execute(text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'refinery' AND tablename = 'event_state'"
    ))).scalars().all()
    assert "event_state_run_id_idx" in rows

    rows = (await db.execute(text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'core' AND tablename = 'sessions'"
    ))).scalars().all()
    assert "sessions_user_id_idx" in rows


async def test_gh_app_role_is_login_non_superuser_non_bypassrls(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    row = (await db.execute(text(
        "SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'gh_app'"))).one()
    # rolcanlogin phụ thuộc GH_APP_DB_PASSWORD lúc migrate — tests/conftest.py luôn đặt nên ở đây phải LOGIN.
    assert row.rolcanlogin is True
    assert row.rolsuper is False
    assert row.rolbypassrls is False


@pytest.mark.parametrize("table", [
    "core.sessions", "raw.events", "refinery.event_state", "clean.meaning_units", "memory.notebooks",
    "biz.tasks", "agent.model_calls", "ops.action_log", "analytics.dim_date",
])
async def test_gh_app_has_dml_grants_on_every_app_schema(app, db, redis, table: str) -> None:  # type: ignore[no-untyped-def]
    for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        ok = (await db.execute(text("SELECT has_table_privilege('gh_app', :t, :p)"),
                               {"t": table, "p": priv})).scalar()
        assert ok, f"gh_app thiếu {priv} trên {table}"


async def test_gh_app_default_privileges_cover_new_partition(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Bảng phân vùng mới (partman) tạo SAU migration 0014 vẫn phải được gh_app SELECT/INSERT/UPDATE/DELETE
    được, nhờ ALTER DEFAULT PRIVILEGES đặt theo current_user lúc migrate. `partman.run_maintenance()` là DDL
    (tạo bảng) nên luôn chạy qua superuser (`admin_sessionmaker`), đúng như gh/worker.py::partition_maintenance
    — không phải qua `db` (có thể đang là gh_app khi GH_TEST_APP_ROLE=1, không có quyền EXECUTE hàm này)."""
    from gh.db import admin_sessionmaker

    async with admin_sessionmaker()() as adm:
        await adm.execute(text("SELECT partman.run_maintenance()"))
        await adm.commit()
    child = (await db.execute(text(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'events' ORDER BY c.relname DESC LIMIT 1"
    ))).scalar_one()
    for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        ok = (await db.execute(text("SELECT has_table_privilege('gh_app', :t, :p)"),
                               {"t": f"raw.{child}", "p": priv})).scalar()
        assert ok, f"gh_app thiếu {priv} trên phân vùng mới raw.{child}"


async def test_expire_sessions_purges_only_old_expired_or_revoked(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    from gh.worker import expire_sessions

    user_id = (await db.execute(text("SELECT id FROM core.users LIMIT 1"))).scalar_one()

    now = datetime.now(UTC)

    async def insert(expires_at: datetime, revoked_at: datetime | None) -> object:
        return (await db.execute(text("""
            INSERT INTO core.sessions (user_id, token_hash, expires_at, revoked_at)
            VALUES (:u, gen_random_bytes(32), :exp, :rev) RETURNING id"""),
            {"u": user_id, "exp": expires_at, "rev": revoked_at})).scalar_one()

    still_valid = await insert(now + timedelta(hours=1), None)                       # còn hiệu lực — giữ
    expired_recent = await insert(now - timedelta(days=1), None)                     # hết hạn nhưng gần đây — giữ
    expired_old = await insert(now - timedelta(days=31), None)                       # hết hạn lâu — xoá
    revoked_recent = await insert(now + timedelta(hours=1), now - timedelta(days=1))  # thu hồi gần đây — giữ
    revoked_old = await insert(now + timedelta(hours=1), now - timedelta(days=31))    # thu hồi lâu — xoá
    await db.commit()

    n = await expire_sessions({})
    assert n >= 2

    remaining = set((await db.execute(text("SELECT id FROM core.sessions"))).scalars().all())
    assert still_valid in remaining
    assert expired_recent in remaining
    assert revoked_recent in remaining
    assert expired_old not in remaining
    assert revoked_old not in remaining


async def test_purge_expired_sessions_respects_older_than_days(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    user_id = (await db.execute(text("SELECT id FROM core.users LIMIT 1"))).scalar_one()
    now = datetime.now(UTC)
    sid = (await db.execute(text("""
        INSERT INTO core.sessions (user_id, token_hash, expires_at)
        VALUES (:u, gen_random_bytes(32), :exp) RETURNING id"""),
        {"u": user_id, "exp": now - timedelta(days=5)})).scalar_one()

    assert await auth_service.purge_expired_sessions(db, older_than_days=30) == 0
    remaining = (await db.execute(text("SELECT id FROM core.sessions WHERE id = :s"), {"s": sid})).scalar()
    assert remaining == sid

    assert await auth_service.purge_expired_sessions(db, older_than_days=1) == 1
    remaining = (await db.execute(text("SELECT id FROM core.sessions WHERE id = :s"), {"s": sid})).scalar()
    assert remaining is None
