"""Giai đoạn 5.5 — Row-Level Security theo org_id (migration 0012, ARCHITECTURE §8.3).

Vai trò kết nối của ứng dụng trong môi trường này (`postgres`, xem gh/config.py) là SUPERUSER và Postgres
KHÔNG áp RLS cho superuser theo định nghĩa — nên các test này tự tạo một vai trò KHÔNG phải superuser/chủ
bảng (`SET LOCAL ROLE`) để chứng minh policy thật sự lọc đúng, đúng cách một vai trò ứng dụng phi-superuser
(việc trình cài/giai đoạn 6 cần thiết lập) sẽ trải nghiệm. Xem chú thích đầu db/sql/0012_p5_rls.sql.
"""

import contextlib
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from sqlalchemy import text

from tests.conftest import PG, TEMPLATE_LOCK_KEY
from tests.phase2 import org_id


@contextlib.asynccontextmanager
async def _cluster_lock() -> AsyncIterator[None]:
    """Khoá tư vấn CẤP CỤM — cùng khoá `TEMPLATE_LOCK_KEY` mà `template_db` (conftest) giữ khi migrate (migration 0014
    có `ALTER ROLE gh_app`). `CREATE ROLE gh_rls_test` / `GRANT … TO gh_app` cũng chạm vai trò dùng chung cả cụm; CI
    v0.1.57 chạy hai lượt pytest song song nên phải xếp hàng. Khoá tư vấn Postgres tính THEO TỪNG CSDL ⇒ phải lấy trên
    kết nối tới CSDL `postgres` (như conftest), không phải trên CSDL test; đóng kết nối thì khoá tự nhả."""
    async with await psycopg.AsyncConnection.connect(f"{PG}/postgres", autocommit=True) as lock:
        await lock.execute("SELECT pg_advisory_lock(%s)", (TEMPLATE_LOCK_KEY,))
        yield


async def _as_low_priv(db, table: str) -> None:  # type: ignore[no-untyped-def]
    """Tạo/cấp quyền cho `gh_rls_test` bằng kết nối SUPERUSER riêng (`gh.db.admin_sessionmaker`) — CREATE ROLE
    và GRANT (không WITH GRANT OPTION) cần superuser/chủ sở hữu, còn `db` (phiên của test) có thể đang kết
    nối bằng `gh_app` (GH_TEST_APP_ROLE=1 — mục v0.1.1/1a) nên không tự CREATE ROLE/GRANT được. Cũng GRANT
    thành viên `gh_rls_test` cho `gh_app` để `SET LOCAL ROLE gh_rls_test` bên dưới luôn hợp lệ dù `db` đang
    login bằng vai trò nào."""
    from gh.db import admin_sessionmaker

    async with _cluster_lock(), admin_sessionmaker()() as adm:
        await adm.execute(text("""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gh_rls_test') THEN
              CREATE ROLE gh_rls_test;
            END IF;
          END $$"""))
        schema = table.split(".")[0]
        await adm.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO gh_rls_test"))
        await adm.execute(text(f"GRANT SELECT, INSERT ON {table} TO gh_rls_test"))
        # core.next_code() dùng để sinh mã công khai (PER-0042…) — cần quyền trên bảng đếm + hàm (SECURITY INVOKER).
        await adm.execute(text("GRANT USAGE ON SCHEMA core TO gh_rls_test"))
        await adm.execute(text("GRANT SELECT, INSERT, UPDATE ON core.code_sequences TO gh_rls_test"))
        await adm.execute(text("GRANT EXECUTE ON FUNCTION core.next_code(text, int) TO gh_rls_test"))
        await adm.execute(text("GRANT gh_rls_test TO gh_app"))
        await adm.commit()
    await db.execute(text("SET LOCAL ROLE gh_rls_test"))


async def _person(db, org: uuid.UUID, name: str) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                             VALUES (:o, core.next_code('PER'), :n)"""), {"o": org, "n": name})


async def test_rls_isolates_rows_by_org_for_non_superuser_role(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org_a = await org_id(db)
    org_b = (await db.execute(text(
        "INSERT INTO core.organizations (name) VALUES ('Tổ chức B (chỉ để test RLS)') RETURNING id"))).scalar_one()
    await _person(db, org_a, "Người A")
    await _person(db, org_b, "Người B")

    await _as_low_priv(db, "core.persons")

    # Chưa đặt app.org_id (đúng tình huống worker nền / job lịch không qua request HTTP): thấy cả hai — không
    # phá luồng hiện có, vì các luồng đó đã tự lọc org_id ở tầng service (xem chú thích migration).
    rows = (await db.execute(text("SELECT display_name FROM core.persons ORDER BY display_name"))).scalars().all()
    assert rows == ["Người A", "Người B"]

    # Đặt app.org_id = org_a (mô phỏng SET LOCAL của gh/auth/deps.py::optional_user sau khi đăng nhập).
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_a)})
    rows = (await db.execute(text("SELECT display_name FROM core.persons"))).scalars().all()
    assert rows == ["Người A"]                                    # KHÔNG thấy dòng của tổ chức khác

    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_b)})
    rows = (await db.execute(text("SELECT display_name FROM core.persons"))).scalars().all()
    assert rows == ["Người B"]


async def test_rls_with_check_blocks_insert_into_wrong_org(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org_a = await org_id(db)
    org_b = (await db.execute(text(
        "INSERT INTO core.organizations (name) VALUES ('Tổ chức B (chỉ để test RLS)') RETURNING id"))).scalar_one()
    await _as_low_priv(db, "core.persons")
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_a)})

    # Ghi đúng org_id đang hoạt động: được phép.
    await _person(db, org_a, "Ghi đúng tổ chức")

    # Cố ghi lệch org_id (vd. lỗi lập trình quên lọc): WITH CHECK của policy phải chặn.
    with pytest.raises(Exception, match="row-level security|row_level_security"):
        await _person(db, org_b, "Ghi nhầm tổ chức")


async def test_migration_lists_expected_tables_with_policy(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    rows = (await db.execute(text(
        "SELECT schemaname || '.' || tablename FROM pg_tables t "
        "WHERE EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = t.schemaname AND p.tablename = t.tablename)"
        " ORDER BY 1"))).scalars().all()
    for expected in ("core.persons", "core.groups", "raw.events", "clean.meaning_units", "biz.opportunities",
                     "biz.people_reviews"):
        assert expected in rows, f"thiếu policy RLS trên {expected}"
