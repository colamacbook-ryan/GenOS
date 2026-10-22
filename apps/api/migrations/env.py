"""Alembic chạy đồng bộ bằng psycopg; schema viết bằng SQL thuần ở db/sql (xem docs/handoff/03)."""

from alembic import context
from sqlalchemy import create_engine, pool

from gh.config import get_settings


def sync_url() -> str:
    # Migrate cần DDL (tạo bảng/role/policy) → dùng URL superuser (GH_ADMIN_DATABASE_URL, rỗng ⇒
    # GH_DATABASE_URL) chứ không phải role ứng dụng gh_app (migration 0014 GRANT quyền cho gh_app, nhưng
    # gh_app không có quyền tạo bảng/role).
    return get_settings().effective_admin_database_url.replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(url=sync_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(sync_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
