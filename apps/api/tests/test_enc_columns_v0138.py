"""F-17 (v0.1.38) — mọi cột bí mật `*_enc` (bytea) trong CSDL đã migrate PHẢI nằm trong `bundle.REENCRYPT_TARGETS`.

Thiếu một cột → gói chuyển máy mang bí mật mã hoá bằng khoá master CŨ sang máy mới mà không mã hoá lại → đọc lỗi
(chính là lỗi phiên mạng xã hội bị sót trước v0.1.38). Thêm cột `*_enc` mới thì phải khai ở `gh/bundle.py` cùng AAD
khớp nơi ghi.
"""

import uuid
from typing import Any

import orjson
from sqlalchemy import text

from gh import bundle, crypto
from gh.db import admin_sessionmaker
from gh.social import protocol
from gh.social import service as social


async def test_every_enc_column_is_reencrypted_on_import(fresh_db: str) -> None:
    async with admin_sessionmaker()() as s:
        rows = (await s.execute(text(r"""
            SELECT c.table_schema, c.table_name, c.column_name
              FROM information_schema.columns c
              JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
             WHERE c.data_type = 'bytea' AND c.column_name LIKE '%\_enc' ESCAPE '\'
               AND t.table_type = 'BASE TABLE'
               AND c.table_schema NOT IN ('pg_catalog', 'information_schema')"""))).all()
    in_db = {f"{r.table_schema}.{r.table_name}.{r.column_name}" for r in rows}
    declared = {f"{t.table}.{t.secret_col}" for t in bundle.REENCRYPT_TARGETS}
    missing = sorted(in_db - declared)
    stale = sorted(declared - in_db)
    assert not missing, f"Cột bí mật chưa khai trong gh/bundle.py::REENCRYPT_TARGETS (cần thêm): {missing}"
    assert not stale, f"REENCRYPT_TARGETS khai cột không còn trong CSDL: {stale}"


async def test_social_target_aad_matches_store_state(fresh_db: str) -> None:
    """AAD theo dòng của mục social trong bundle phải khớp y hệt AAD `service._store_state` dùng khi ghi."""
    target = next(t for t in bundle.REENCRYPT_TARGETS if t.table == "core.social_accounts")
    assert target.secret_col == "state_enc" and target.on_fail == "needs_login" and "org_id" in target.extra_cols
    async with admin_sessionmaker()() as s:
        org = uuid.uuid4()
        await s.execute(text("INSERT INTO core.organizations (id, name) VALUES (:i, 'Org AAD')"), {"i": org})
        acc = (await s.execute(text("""INSERT INTO core.social_accounts (org_id, platform, label)
                                       VALUES (:o, 'facebook_personal', 'Facebook') RETURNING id"""),
                               {"o": org})).scalar_one()
        state = {"cookies": [{"name": "c_user", "value": "1"}], "origins": []}
        blob = protocol.seal(crypto.browser_key(), orjson.dumps(state), protocol.account_aad(org, acc))
        await social._store_state(s, org, acc, blob)
        row: Any = (await s.execute(text("SELECT id, org_id, state_enc FROM core.social_accounts WHERE id = :i"),
                                    {"i": acc})).mappings().one()
        await s.rollback()
    aad = target.aad_for(row)
    assert orjson.loads(crypto.decrypt(bytes(row["state_enc"]), aad)) == state
