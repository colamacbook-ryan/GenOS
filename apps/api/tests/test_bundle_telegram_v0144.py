"""v0.1.44 (F-8c) — token bot Telegram (ops.notify_channels.token_enc) đi theo gói hồ sơ: nhập sang máy có khoá master
KHÁC ⇒ được mã hoá lại, giải được bằng khoá mới (không còn mở được bằng khoá cũ); run/telegram.json đồng bộ lại
bằng khoá mới."""

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from gh import bundle, crypto
from gh.config import get_settings
from gh.db import admin_sessionmaker, sessionmaker
from gh.telegram import service as tsvc
from tests.conftest import API_DIR, Api
from tests.test_bundle import _set_master_key, _use_objects_dir
from tests.test_bundle_social_v0138 import KEY_A, _drop, _migrate
from tests.test_telegram_v0144 import CHAT, TOKEN


@pytest.fixture(autouse=True)
def _master_key_a(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    _set_master_key(monkeypatch, KEY_A)
    _use_objects_dir(monkeypatch, tmp_path / "src-objects")


def test_target_declared() -> None:
    t = next(t for t in bundle.REENCRYPT_TARGETS if t.table == "ops.notify_channels")
    assert (t.id_col, t.secret_col, t.aad, t.on_fail) == ("org_id", "token_enc", tsvc.TOKEN_AAD, "raise")


async def test_import_reencrypts_telegram_token(owner_api: Api, redis: Redis, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    async with admin_sessionmaker()() as s:
        org = (await s.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        await s.execute(text("""INSERT INTO ops.notify_channels (org_id, token_enc, chat_id, bot_username)
                                VALUES (:o, :t, :c, 'gen_sep_bot')"""),
                        {"o": org, "t": crypto.encrypt(TOKEN.encode(), b"telegram_token"), "c": CHAT})
        await s.commit()
    target_db, _key_b = await _migrate(monkeypatch, tmp_path)
    try:
        async with admin_sessionmaker()() as s:
            row = (await s.execute(text("SELECT token_enc, chat_id FROM ops.notify_channels"))).one()
        assert crypto.decrypt(bytes(row.token_enc), b"telegram_token").decode() == TOKEN      # khoá B (hiện hành)
        with pytest.raises(Exception):  # noqa: B017 — khoá A không còn mở được: đã thật sự mã hoá lại
            crypto.decrypt(bytes(row.token_enc), b"telegram_token", key=base64.b64decode(KEY_A))
        # Lifespan gọi sync_host_file — tệp cho genh dùng khoá mới.
        host = tmp_path / "run"
        host.mkdir()
        monkeypatch.setattr(get_settings(), "host_link_dir", str(host))
        assert await tsvc.sync_host_file(sessionmaker()) is True
        data = json.loads((host / "telegram.json").read_text())
        plain = crypto.decrypt(base64.b64decode(data["enc"]), b"telegram_notify")
        assert json.loads(plain) == {"token": TOKEN, "chat_id": CHAT}
    finally:
        await _drop(target_db)


async def test_import_old_bundle_without_notify_channels(owner_api: Api, redis: Redis, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Gói xuất từ v0.1.43 trở về trước (revision 0028 — chưa có ops.notify_channels) nhập sang máy khoá master KHÁC:
    bước mã hoá lại phải bỏ qua bảng chưa tồn tại (genh migrate SAU khi nhập) thay vì UndefinedTable làm cuộn lại
    mọi bí mật khác (kẹt ở khoá cũ vĩnh viễn)."""
    totp = b"bi-mat-totp-gia-lap"
    async with admin_sessionmaker()() as s:
        user = (await s.execute(text("SELECT id FROM core.users ORDER BY created_at LIMIT 1"))).scalar_one()
        await s.execute(text("UPDATE core.users SET totp_secret_enc = :e WHERE id = :i"),
                        {"e": crypto.encrypt(totp, b"totp_secret"), "i": user})
        # Mô phỏng CSDL ở revision 0028: chưa có các bảng của migration 0029.
        await s.execute(text("DROP TABLE ops.telegram_outbox, ops.notify_channels"))
        # Từ v0.1.45 head là 0030 (chạy lại an toàn) — hạ mọi revision sau 0028 về 0028.
        await s.execute(text("UPDATE alembic_version SET version_num = '0028' WHERE version_num > '0028'"))
        await s.commit()
    target_db, _key_b = await _migrate(monkeypatch, tmp_path)
    try:
        async with admin_sessionmaker()() as s:
            assert (await s.execute(text("SELECT to_regclass('ops.notify_channels')"))).scalar() is None
            enc = (await s.execute(text("SELECT totp_secret_enc FROM core.users WHERE id = :i"),
                                   {"i": user})).scalar_one()
        assert crypto.decrypt(bytes(enc), b"totp_secret") == totp                       # khoá B (hiện hành)
        with pytest.raises(Exception):  # noqa: B017 — đã thật sự mã hoá lại, không còn ở khoá A
            crypto.decrypt(bytes(enc), b"totp_secret", key=base64.b64decode(KEY_A))
        # Như genh: `alembic upgrade heads` sau khi nhập — bảng Telegram xuất hiện, trống.
        env = {**os.environ, "GH_ADMIN_DATABASE_URL": ""}
        await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "upgrade", "heads"], cwd=API_DIR,
                                env=env, check=True)
        async with admin_sessionmaker()() as s:
            assert (await s.execute(text("SELECT count(*) FROM ops.notify_channels"))).scalar_one() == 0
    finally:
        await _drop(target_db)
