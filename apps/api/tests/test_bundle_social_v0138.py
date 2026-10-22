"""F-17 (v0.1.38) — gói chuyển máy không bỏ sót phiên mạng xã hội.

Trước v0.1.38 `core.social_accounts.state_enc` (mã hoá bằng khoá master, AAD theo dòng `social:<org_id>:<id>`) KHÔNG
nằm trong `bundle.REENCRYPT_TARGETS` → nhập gói sang máy khoá khác thì đọc/kiểm tài khoản lỗi 500 (InvalidTag), và
`schedule_tick` chết cả lượt vì một tài khoản hỏng. Kiểm:
1. gói mới: phiên được mã hoá lại → đọc được ngay trên máy mới (2xx, worker giải được phiên);
2. gói cũ (mô phỏng v0.1.37: bỏ mục social khỏi REENCRYPT_TARGETS) → 409 SOCIAL_NEEDS_LOGIN, tài khoản sang Cần đăng
   nhập lại (`key_changed`), 1 thông báo Owner, gọi lại vẫn 409 — không bao giờ 500;
3. blob không giải được bằng khoá cũ lúc nhập → nhập vẫn thành công, dòng đó thành needs_login, bí mật khác đúng;
4. lịch đọc: một tài khoản hỏng/ném lỗi lạ không chặn tài khoản khác.
"""

import base64
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import orjson
import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from gh import bundle, crypto
from gh import db as dbmod
from gh.db import admin_sessionmaker, sessionmaker
from gh.social import protocol
from gh.social import service as social
from tests.conftest import PG, Api
from tests.test_bundle import _admin, _random_master_key, _set_master_key, _use_database, _use_objects_dir
from tests.test_social import STATE, _add, _jobs, _login

KEY_A = _random_master_key()
PASSWORD = "mat-khau-goi-rat-dai-va-manh"
NEEDS_LOGIN_TEXT = "Phiên đã lưu không mở được trên máy này (chuyển máy hoặc đổi khoá) — bấm Đăng nhập lại."


@pytest.fixture(autouse=True)
def _master_key_a(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Máy nguồn dùng khoá master A NGAY TỪ ĐẦU (trước khi app khởi tạo/setup ghi bí mật nào)."""
    _set_master_key(monkeypatch, KEY_A)
    _use_objects_dir(monkeypatch, tmp_path / "src-objects")


async def _account_row(account_id: str) -> Any:
    async with admin_sessionmaker()() as s:
        return (await s.execute(text("""SELECT org_id, status, pause_reason, state_enc, last_health
                                         FROM core.social_accounts WHERE id = :i"""), {"i": account_id})).one()


async def _overwrite_state_with_foreign_key(account_id: str) -> None:
    """Ghi đè phiên bằng blob mã hoá khoá master C (không ai có) — như phiên từ máy khác chưa mã hoá lại."""
    row = await _account_row(account_id)
    aad = f"social:{protocol.account_aad(row.org_id, account_id)}".encode()
    blob = crypto.encrypt(orjson.dumps(STATE), aad, key=os.urandom(32))
    async with admin_sessionmaker()() as s:
        await s.execute(text("UPDATE core.social_accounts SET state_enc = :e WHERE id = :i"),
                        {"e": blob, "i": account_id})
        await s.commit()


async def _notifications(kind: str) -> list[Any]:
    async with admin_sessionmaker()() as s:
        return list((await s.execute(text("SELECT title, body, link FROM core.notifications WHERE kind = :k"),
                                     {"k": kind})).all())


async def _migrate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> tuple[str, str]:
    """Xuất (khoá A) → nhập vào CSDL trống khác với khoá B. Trả (tên CSDL đích, khoá B) — người gọi tự DROP."""
    bundle_path = tmp_path / f"{uuid.uuid4().hex[:6]}.ghbundle"
    monkeypatch.setenv("GH_BUNDLE_PASSWORD", PASSWORD)
    await bundle._export(str(bundle_path))
    key_b = _random_master_key()
    target_db = f"gh_bundle_target_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {target_db}")
    _use_database(monkeypatch, f"{PG.replace('postgresql://', 'postgresql+asyncpg://')}/{target_db}")
    _set_master_key(monkeypatch, key_b)
    _use_objects_dir(monkeypatch, tmp_path / f"dst-objects-{uuid.uuid4().hex[:6]}")
    await dbmod.dispose_engine()
    await bundle._import(str(bundle_path))
    await dbmod.dispose_engine()
    return target_db, key_b


async def _drop(target_db: str) -> None:
    await dbmod.dispose_engine()
    _admin(f"DROP DATABASE IF EXISTS {target_db} WITH (FORCE)")


# ─── (1) gói mới: phiên được mã hoá lại, đọc/kiểm được ngay trên máy mới ─────────────────────────────────────

async def test_new_bundle_reencrypts_social_session(owner_api: Api, redis: Redis, tmp_path: Any,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    acc = await _add(owner_api)
    await _login(owner_api, redis, acc["id"])
    target_db, _key_b = await _migrate(monkeypatch, tmp_path)
    try:
        row = await _account_row(acc["id"])
        assert row.status == "active" and row.state_enc is not None
        aad = f"social:{protocol.account_aad(row.org_id, acc['id'])}".encode()
        assert orjson.loads(crypto.decrypt(bytes(row.state_enc), aad)) == STATE        # khoá B (hiện hành)
        with pytest.raises(Exception):  # noqa: B017 — bản cũ (khoá A) không còn mở được: đã thật sự mã hoá lại
            crypto.decrypt(bytes(row.state_enc), aad, key=base64.b64decode(KEY_A))

        r = await owner_api.send("POST", f"/social/accounts/{acc['id']}/read", {})
        assert r.status_code == 200, r.text
        job = (await _jobs(redis))[-1]
        assert job["kind"] == "read" and job["account_id"] == acc["id"]
        plain = protocol.unseal(crypto.browser_key(), job["payload"]["state"],
                                protocol.account_aad(row.org_id, acc["id"]))
        assert orjson.loads(plain) == STATE                                             # worker giải được phiên
    finally:
        await _drop(target_db)


# ─── (2) gói cũ v0.1.37 (không mã hoá lại phiên) → 409 SOCIAL_NEEDS_LOGIN, không bao giờ 500 ────────────────

async def test_old_bundle_session_unreadable_needs_login(owner_api: Api, redis: Redis, tmp_path: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    acc1 = await _add(owner_api, "Facebook 1")
    await _login(owner_api, redis, acc1["id"])
    acc2 = await _add(owner_api, "Facebook 2")
    await _login(owner_api, redis, acc2["id"])
    # Mô phỏng gói v0.1.37: danh sách mã hoá lại chưa có mục social.
    monkeypatch.setattr(bundle, "REENCRYPT_TARGETS",
                        [t for t in bundle.REENCRYPT_TARGETS if t.table != "core.social_accounts"])
    target_db, _key_b = await _migrate(monkeypatch, tmp_path)
    try:
        jobs_before = len(await _jobs(redis))
        for acc, path in ((acc1, "check"), (acc2, "read")):
            r = await owner_api.send("POST", f"/social/accounts/{acc['id']}/{path}", {})
            assert r.status_code == 409, r.text
            body = r.json()
            assert body["code"] == "SOCIAL_NEEDS_LOGIN"
            assert body["title"] == NEEDS_LOGIN_TEXT
            row = await _account_row(acc["id"])
            assert (row.status, row.pause_reason, row.state_enc) == ("needs_login", "key_changed", None)
            assert row.last_health["ok"] is False and row.last_health["state"] == "KEY_CHANGED"
        assert len(await _jobs(redis)) == jobs_before                                  # không xếp việc nào
        notes = await _notifications("social.needs_login")
        assert sorted(n.title for n in notes) == ["Facebook 1: cần đăng nhập lại", "Facebook 2: cần đăng nhập lại"]
        assert all(n.link == "/social" and "Đăng nhập lại" in n.body for n in notes)
        async with admin_sessionmaker()() as s:
            acts = (await s.execute(text("""SELECT count(*) FROM ops.action_log
                                            WHERE action = 'social.session_unreadable'"""))).scalar_one()
        assert acts == 2
        # Gọi lại: vẫn 409 với mã rõ ràng, không bao giờ 500.
        for acc in (acc1, acc2):
            for path in ("check", "read"):
                r = await owner_api.send("POST", f"/social/accounts/{acc['id']}/{path}", {})
                assert r.status_code == 409, r.text
                assert r.json()["code"] in ("SOCIAL_NOT_ACTIVE", "SOCIAL_NO_SESSION")
        assert len(await _notifications("social.needs_login")) == 2                   # không báo trùng
        a = (await owner_api.get(f"/social/accounts/{acc1['id']}")).json()
        assert a["status"] == "needs_login" and a["pause_reason"] == "key_changed" and not a["has_session"]
    finally:
        await _drop(target_db)


# ─── (3) blob không giải được bằng khoá cũ lúc nhập → nhập vẫn xong, dòng đó needs_login ─────────────────────

async def test_import_with_undecryptable_session_marks_needs_login(owner_api: Api, redis: Redis, tmp_path: Any,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    good = await _add(owner_api, "Facebook tốt")
    await _login(owner_api, redis, good["id"])
    bad = await _add(owner_api, "Facebook hỏng")
    await _login(owner_api, redis, bad["id"])
    await _overwrite_state_with_foreign_key(bad["id"])
    org = (await _account_row(good["id"])).org_id
    async with admin_sessionmaker()() as s:
        await s.execute(text("""INSERT INTO agent.mcp_servers (org_id, name, transport, endpoint, auth_enc)
                                VALUES (:o, 'kho-hang', 'http+sse', 'https://mcp.example', :a)"""),
                        {"o": org, "a": crypto.encrypt(b"mcp-token-bi-mat", b"mcp_server_auth")})
        await s.commit()

    target_db, _key_b = await _migrate(monkeypatch, tmp_path)                          # không ném
    try:
        b = await _account_row(bad["id"])
        assert (b.status, b.pause_reason, b.state_enc) == ("needs_login", "key_changed", None)
        g = await _account_row(good["id"])
        assert g.status == "active"
        assert orjson.loads(crypto.decrypt(bytes(g.state_enc),
                                           f"social:{protocol.account_aad(org, good['id'])}".encode())) == STATE
        async with admin_sessionmaker()() as s:
            auth = (await s.execute(text("SELECT auth_enc FROM agent.mcp_servers WHERE org_id = :o"),
                                    {"o": org})).scalar_one()
        assert crypto.decrypt(bytes(auth), b"mcp_server_auth") == b"mcp-token-bi-mat"
    finally:
        await _drop(target_db)


async def test_reencrypt_needs_login_keeps_revoked_status(owner_api: Api, redis: Redis,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Dòng đã gỡ (revoked) mà còn blob hỏng: chỉ xoá phiên, KHÔNG hồi sinh thành needs_login."""
    acc = await _add(owner_api)
    await _login(owner_api, redis, acc["id"])
    await _overwrite_state_with_foreign_key(acc["id"])
    async with admin_sessionmaker()() as s:
        await s.execute(text("UPDATE core.social_accounts SET status = 'revoked' WHERE id = :i"), {"i": acc["id"]})
        await s.commit()
    await bundle._reencrypt_secrets(base64.b64decode(KEY_A))
    # khoá hiện hành = A → hàm bỏ qua; đổi sang khoá B để thật sự chạy nhánh mã hoá lại
    _set_master_key(monkeypatch, _random_master_key())
    await bundle._reencrypt_secrets(base64.b64decode(KEY_A))
    row = await _account_row(acc["id"])
    assert (row.status, row.pause_reason, row.state_enc) == ("revoked", "key_changed", None)


# ─── (4) lịch đọc: một tài khoản hỏng không chặn tài khoản khác ──────────────────────────────────────────────

async def _two_scheduled(owner_api: Api, redis: Redis) -> tuple[dict[str, Any], dict[str, Any]]:
    out = []
    for label in ("Facebook 1", "Facebook 2"):
        acc = await _add(owner_api, label)
        await _login(owner_api, redis, acc["id"])
        r = await owner_api.send("PATCH", f"/social/accounts/{acc['id']}",
                                 {"schedule": {"enabled": True, "times": ["08:00"]}})
        assert r.status_code == 200, r.text
        out.append(acc)
    return out[0], out[1]


AT_0800_VN = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)


async def _read_jobs(account_id: str) -> list[str]:
    async with admin_sessionmaker()() as s:
        return list((await s.execute(text("SELECT via FROM agent.browser_jobs WHERE kind = 'read' AND account_id = :a"),
                                     {"a": account_id})).scalars().all())


async def test_schedule_tick_isolates_unreadable_session(owner_api: Api, redis: Redis) -> None:
    acc1, acc2 = await _two_scheduled(owner_api, redis)
    await _overwrite_state_with_foreign_key(acc1["id"])
    async with sessionmaker()() as s:
        assert await social.schedule_tick(s, redis, AT_0800_VN) == 1
        await s.commit()
    assert await _read_jobs(acc2["id"]) == ["schedule"]
    assert await _read_jobs(acc1["id"]) == []
    row = await _account_row(acc1["id"])
    assert (row.status, row.pause_reason, row.state_enc) == ("needs_login", "key_changed", None)
    async with admin_sessionmaker()() as s:
        actor = (await s.execute(text("""SELECT actor_type FROM ops.action_log
                                          WHERE action = 'social.session_unreadable'"""))).scalar_one()
    assert actor == "system"                                                           # lịch: không có người dùng
    assert len(await _notifications("social.needs_login")) == 1


async def test_schedule_tick_survives_unexpected_error(owner_api: Api, redis: Redis,
                                                       monkeypatch: pytest.MonkeyPatch,
                                                       caplog: pytest.LogCaptureFixture) -> None:
    acc1, acc2 = await _two_scheduled(owner_api, redis)
    real = social.request_read

    async def flaky(db: Any, redis_: Redis, **kw: Any) -> dict[str, Any]:
        if str(kw["account_id"]) == acc1["id"]:
            raise RuntimeError("bi-mat-khong-duoc-log")
        return await real(db, redis_, **kw)

    monkeypatch.setattr(social, "request_read", flaky)
    async with sessionmaker()() as s:
        assert await social.schedule_tick(s, redis, AT_0800_VN) == 1                   # không ném
        await s.commit()
    assert await _read_jobs(acc2["id"]) == ["schedule"]
    assert "RuntimeError" in caplog.text and "bi-mat-khong-duoc-log" not in caplog.text
