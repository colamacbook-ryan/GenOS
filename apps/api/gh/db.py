"""Kết nối PostgreSQL (SQLAlchemy async). SSOT duy nhất của hệ thống."""

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from gh.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_admin_engine: AsyncEngine | None = None
_admin_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


def get_admin_engine() -> AsyncEngine:
    """Kết nối superuser (`GH_ADMIN_DATABASE_URL`, rỗng ⇒ `database_url`) — dùng cho DDL lúc chạy: bảo trì/tạo
    phân vùng pg_partman (gh/worker.py::partition_maintenance), backup/restore (gh/backup.py). KHÔNG dùng cho
    đường request HTTP thường (route/service dùng `sessionmaker()` như cũ, qua role `gh_app`)."""
    global _admin_engine, _admin_sessionmaker
    if _admin_engine is None:
        _admin_engine = create_async_engine(get_settings().effective_admin_database_url, pool_pre_ping=True)
        _admin_sessionmaker = async_sessionmaker(_admin_engine, expire_on_commit=False)
    return _admin_engine


def admin_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_admin_engine()
    assert _admin_sessionmaker is not None
    return _admin_sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker, _admin_engine, _admin_sessionmaker
    if _engine is not None:
        await _engine.dispose()
    if _admin_engine is not None:
        await _admin_engine.dispose()
    _engine, _sessionmaker = None, None
    _admin_engine, _admin_sessionmaker = None, None


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependency FastAPI: một transaction cho mỗi request, commit khi thành công.

    Luôn khai báo `DB`: commit xong rồi mới gửi phản hồi, để request kế tiếp
    của client (vd. thử lại sau khi nhập PIN) đọc được dữ liệu vừa ghi.
    """
    async with sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


# Mặc định của FastAPI chạy phần sau `yield` SAU khi đã gửi phản hồi → client có thể đọc trước khi commit.
DB = Depends(get_db, scope="function")
