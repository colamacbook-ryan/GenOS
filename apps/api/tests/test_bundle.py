"""Gói hồ sơ Owner `.ghbundle` (HANDOFF-v0.1.1 §1b) — xem docstring `gh/bundle.py` cho hợp đồng đầy đủ.

Kiểm ở hai mức:
1. Đơn vị (không đụng CSDL/tiến trình con): phong bì ngoài (`_encrypt_bundle`/`_decrypt_bundle`) — sai mật
   khẩu, gói bị sửa một byte, header phiên bản lạ.
2. Vòng lặp thật: `pg_dump`/`pg_restore`/`ObjectStore` đĩa cục bộ thật, xuất từ một CSDL (khoá master A) →
   nhập vào một CSDL TRỐNG khác (khoá master B) → bí mật (`agent.mcp_servers.auth_enc`,
   `core.users.totp_secret_enc`) đọc lại đúng bằng khoá B; object thường được mang sang, `backups/…` thì không.
"""

import base64
import os
import tarfile
import uuid

import orjson
import psycopg
import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import text

from gh import bundle, crypto
from gh import db as dbmod
from gh.chassis import objects as objects_mod
from gh.config import get_settings
from tests.conftest import PG


def _admin(sql: str) -> None:
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
        c.execute(sql)


def _random_master_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _use_objects_dir(monkeypatch, path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_OBJECTS_DIR", str(path))
    get_settings.cache_clear()
    objects_mod.reset_object_store()


def _use_database(monkeypatch, async_url: str) -> None:  # type: ignore[no-untyped-def]
    # Xoá GH_ADMIN_DATABASE_URL còn sót lại từ fixture `fresh_db` (chạy dưới GH_TEST_APP_ROLE=1 — xem
    # conftest.py::_use_db) trỏ về CSDL nguồn cũ: nếu không, `effective_admin_database_url` (gh/config.py)
    # sẽ KHÔNG rơi về GH_DATABASE_URL mới mà dùng thẳng admin URL cũ — khiến pg_restore --clean chạy nhầm
    # vào CSDL nguồn (đã có sẵn schema/dữ liệu/phân vùng) thay vì CSDL đích của bài test, gây lỗi
    # "cannot drop inherited constraint" từ các bảng phân vùng pg_partman.
    monkeypatch.setenv("GH_DATABASE_URL", async_url)
    monkeypatch.delenv("GH_ADMIN_DATABASE_URL", raising=False)
    get_settings.cache_clear()


def _set_master_key(monkeypatch, b64_key: str) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_MASTER_KEY", b64_key)
    get_settings.cache_clear()


# ═══ phong bì ngoài (đơn vị, không đụng CSDL) ═══════════════════════════════════════════════════════════════

def test_encrypt_decrypt_roundtrip() -> None:
    header, ct = bundle._encrypt_bundle(b"noi dung tar gia lap", "mat-khau-du-manh-123")
    out = bundle._decrypt_bundle(ct, header, orjson.dumps(header), "mat-khau-du-manh-123")
    assert out == b"noi dung tar gia lap"


def test_wrong_password_raises_bundle_error_code_2() -> None:
    header, ct = bundle._encrypt_bundle(b"bi mat", "mat-khau-dung-1234")
    with pytest.raises(bundle.BundleError) as exc:
        bundle._decrypt_bundle(ct, header, orjson.dumps(header), "mat-khau-sai-nhung-du-dai")
    assert exc.value.code == 2
    assert isinstance(exc.value.__cause__, InvalidTag)


def test_tampered_byte_raises_bundle_error_code_2() -> None:
    header, ct = bundle._encrypt_bundle(b"bi mat khac", "mat-khau-dung-1234")
    tampered = bytes([ct[0] ^ 1]) + ct[1:]
    with pytest.raises(bundle.BundleError) as exc:
        bundle._decrypt_bundle(tampered, header, orjson.dumps(header), "mat-khau-dung-1234")
    assert exc.value.code == 2


# ═══ CLI end-to-end (không cần CSDL) — magic/version/mật khẩu qua đúng đường `_import` đọc tệp ════════════

async def test_import_unknown_header_version_exit_3(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_BUNDLE_PASSWORD", "mat-khau-bat-ky-123")
    header, ct = bundle._encrypt_bundle(b"khong quan trong", "mat-khau-bat-ky-123")
    header["v"] = 999
    blob = bundle.MAGIC + b"\n" + orjson.dumps(header) + b"\n" + ct
    p = tmp_path / "x.ghbundle"
    p.write_bytes(blob)

    with pytest.raises(bundle.BundleError) as exc:
        await bundle._import(str(p))
    assert exc.value.code == 3


def test_bundle_over_gcm_limit_has_clear_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """v0.1.40: gói > 2 GiB (giới hạn AES-GCM một khối) ⇒ BundleError tiếng Việt rõ ràng, không OverflowError."""
    monkeypatch.setattr(bundle, "GCM_MAX_BYTES", 10)
    with pytest.raises(bundle.BundleError) as exc:
        bundle._encrypt_bundle(b"x" * 11, "mat-khau-bat-ky-123")
    assert exc.value.code == 1 and "lớn hơn 2 GiB chưa hỗ trợ" in str(exc.value)

    class _Overflow:
        def __init__(self, key: bytes) -> None:
            pass

        def encrypt(self, *a: object) -> bytes:
            raise OverflowError("Data or associated data too long. Max 2**31 - 1 bytes")

        def decrypt(self, *a: object) -> bytes:
            raise OverflowError("Data or associated data too long. Max 2**31 - 1 bytes")

    monkeypatch.setattr(bundle, "GCM_MAX_BYTES", 2**31 - 1)
    header, ct = bundle._encrypt_bundle(b"nho", "mat-khau-bat-ky-123")
    monkeypatch.setattr(bundle, "AESGCM", _Overflow)
    with pytest.raises(bundle.BundleError, match="lớn hơn 2 GiB"):
        bundle._encrypt_bundle(b"nho", "mat-khau-bat-ky-123")
    with pytest.raises(bundle.BundleError, match="lớn hơn 2 GiB"):
        bundle._decrypt_bundle(ct, header, orjson.dumps(header), "mat-khau-bat-ky-123")


async def test_import_wrong_magic_exit_3(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_BUNDLE_PASSWORD", "mat-khau-bat-ky-123")
    p = tmp_path / "x.ghbundle"
    p.write_bytes(b"GHBUNDLE9\n{}\nrac")
    with pytest.raises(bundle.BundleError) as exc:
        await bundle._import(str(p))
    assert exc.value.code == 3


async def test_export_missing_password_exit_1(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("GH_BUNDLE_PASSWORD", raising=False)
    with pytest.raises(bundle.BundleError) as exc:
        await bundle._export(str(tmp_path / "x.ghbundle"))
    assert exc.value.code == 1


async def test_export_short_password_exit_1(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_BUNDLE_PASSWORD", "ngan")
    with pytest.raises(bundle.BundleError) as exc:
        await bundle._export(str(tmp_path / "x.ghbundle"))
    assert exc.value.code == 1


# ═══ vòng lặp thật: pg_dump/pg_restore/ObjectStore, hai khoá master khác nhau ══════════════════════════════

async def test_export_import_round_trip_two_master_keys_and_objects(
    tmp_path, monkeypatch, fresh_db  # type: ignore[no-untyped-def]
) -> None:
    """Xuất từ `fresh_db` (khoá master A) → nhập vào một CSDL trống khác (khoá master B, mô phỏng máy mới):
    bí mật (agent.mcp_servers.auth_enc, core.users.totp_secret_enc) đọc lại đúng bằng khoá B; object thường
    được mang sang, `backups/…` thì không (cả trong tar lẫn ở đích)."""
    key_a, key_b = _random_master_key(), _random_master_key()
    src_objects, dst_objects = tmp_path / "src-objects", tmp_path / "dst-objects"
    bundle_path = tmp_path / "owner.ghbundle"

    # ─── máy nguồn: khoá master A, dữ liệu + bí mật + object thật ───
    _set_master_key(monkeypatch, key_a)
    _use_objects_dir(monkeypatch, src_objects)

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mcp_secret, totp_secret = "mcp-token-bi-mat-abc", "JBSWY3DPEHPK3PXP"
    async with dbmod.sessionmaker()() as db:
        await db.execute(text("INSERT INTO core.organizations (id, name) VALUES (:i, 'Test Org')"), {"i": org_id})
        await db.execute(text("""INSERT INTO core.users (id, org_id, email, display_name, password_hash,
                              totp_secret_enc) VALUES (:i, :o, 'anh@example.vn', 'Anh Test', 'x',
                              :t)"""),
                         {"i": user_id, "o": org_id,
                          "t": crypto.encrypt(totp_secret.encode(), b"totp_secret")})
        await db.execute(text("""INSERT INTO agent.mcp_servers (org_id, name, transport, endpoint, auth_enc)
                              VALUES (:o, 'kho-hang', 'http+sse', 'https://mcp.example', :a)"""),
                         {"o": org_id, "a": crypto.encrypt(mcp_secret.encode(), b"mcp_server_auth")})
        await db.commit()

    store = objects_mod.get_object_store()
    await store.put("org-1/tai-lieu.txt", b"noi dung tai lieu that")
    await store.put("backups/2026-01-01.pgcustom.enc", b"khong duoc mang sang")

    monkeypatch.setenv("GH_BUNDLE_PASSWORD", "mat-khau-goi-rat-dai-va-manh")
    await bundle._export(str(bundle_path))

    # gói không chứa object backups/ (kiểm trực tiếp trong tar, không chỉ ở đích)
    raw = bundle_path.read_bytes()
    _magic, header_line, ct = raw.split(b"\n", 2)
    header = orjson.loads(header_line)
    tar_bytes = bundle._decrypt_bundle(ct, header, header_line, "mat-khau-goi-rat-dai-va-manh")
    tmp_tar = tmp_path / "check.tar"
    tmp_tar.write_bytes(tar_bytes)
    with tarfile.open(tmp_tar) as tar:
        names = tar.getnames()
    assert "objects/org-1/tai-lieu.txt" in names
    assert not any(n.startswith("objects/backups/") for n in names)

    # ─── máy đích: CSDL trống khác, khoá master B, thư mục object khác ───
    target_db = f"gh_bundle_target_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {target_db}")
    try:
        _use_database(monkeypatch, f"{PG.replace('postgresql://', 'postgresql+asyncpg://')}/{target_db}")
        _set_master_key(monkeypatch, key_b)
        _use_objects_dir(monkeypatch, dst_objects)
        await dbmod.dispose_engine()

        await bundle._import(str(bundle_path))

        # bí mật đọc được đúng bằng khoá master HIỆN HÀNH (B) sau khi nhập
        async with dbmod.sessionmaker()() as db:
            row = (await db.execute(text(
                "SELECT auth_enc FROM agent.mcp_servers WHERE org_id = :o"), {"o": org_id})).scalar_one()
            assert crypto.decrypt(bytes(row), b"mcp_server_auth").decode() == mcp_secret
            row2 = (await db.execute(text(
                "SELECT totp_secret_enc FROM core.users WHERE id = :i"), {"i": user_id})).scalar_one()
            assert crypto.decrypt(bytes(row2), b"totp_secret").decode() == totp_secret
            # giải mã bằng khoá CŨ (A) giờ phải thất bại — dữ liệu thật sự đã mã hoá lại, không còn là bản cũ
            with pytest.raises(InvalidTag):
                crypto.decrypt(bytes(row), b"mcp_server_auth", key=base64.b64decode(key_a))

        # object thường được mang sang đúng nội dung; backups/ thì tuyệt đối không có ở đích
        dst_store = objects_mod.get_object_store()
        assert await dst_store.get("org-1/tai-lieu.txt") == b"noi dung tai lieu that"
        with pytest.raises(objects_mod.ObjectNotFound):
            await dst_store.get("backups/2026-01-01.pgcustom.enc")
        assert not (dst_objects / "backups").exists()
    finally:
        await dbmod.dispose_engine()
        _admin(f"DROP DATABASE IF EXISTS {target_db} WITH (FORCE)")


async def test_import_terminates_lingering_connection_to_target_db(  # type: ignore[no-untyped-def]
    tmp_path, monkeypatch, fresh_db
) -> None:
    """HANDOFF-v0.1.2 mục 2: một kết nối `psycopg` khác (mô phỏng tiến trình cũ chưa dừng hẳn) vẫn mở tới CSDL
    đích khi `_import` chạy — `pg_restore --clean` phải KHÔNG bị chặn/treo (kết nối lạ bị `_terminate_other_
    connections` ngắt trước) và import vẫn thành công."""
    key = _random_master_key()
    _set_master_key(monkeypatch, key)
    _use_objects_dir(monkeypatch, tmp_path / "objects")

    org_id = uuid.uuid4()
    async with dbmod.sessionmaker()() as db:
        await db.execute(text("INSERT INTO core.organizations (id, name) VALUES (:i, 'Org Lingering')"),
                         {"i": org_id})
        await db.commit()

    monkeypatch.setenv("GH_BUNDLE_PASSWORD", "mat-khau-goi-rat-dai-va-manh")
    bundle_path = tmp_path / "x.ghbundle"
    await bundle._export(str(bundle_path))

    target_db = f"gh_bundle_lingering_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {target_db}")
    lingering = psycopg.connect(f"{PG}/{target_db}", autocommit=True)
    try:
        _use_database(monkeypatch, f"{PG.replace('postgresql://', 'postgresql+asyncpg://')}/{target_db}")
        await dbmod.dispose_engine()

        await bundle._import(str(bundle_path))  # không được treo/ném lỗi "đang được truy cập" dù `lingering` còn mở

        async with dbmod.sessionmaker()() as db:
            row = (await db.execute(text(
                "SELECT name FROM core.organizations WHERE id = :i"), {"i": org_id})).scalar_one()
        assert row == "Org Lingering"
    finally:
        try:
            lingering.close()
        except psycopg.Error:
            pass
        await dbmod.dispose_engine()
        _admin(f"DROP DATABASE IF EXISTS {target_db} WITH (FORCE)")


def test_terminate_other_connections_ignores_when_admin_db_unreachable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Không kết nối được CSDL `postgres` để chạy `pg_terminate_backend` (vd. quyền hạn chế) → chỉ log cảnh
    báo, không ném lỗi (chỉ là phòng thủ thêm — xem docstring `_terminate_other_connections`)."""
    n = bundle._terminate_other_connections("postgresql://khong-ton-tai:sai@localhost:1/khong_ton_tai")
    assert n == 0


async def test_import_same_master_key_skips_reencryption(
    tmp_path, monkeypatch, fresh_db  # type: ignore[no-untyped-def]
) -> None:
    """Nhập lại vào CHÍNH khoá master cũ (vd. cùng máy, hoặc khôi phục tại chỗ) → bí mật không đổi bytes."""
    key = _random_master_key()
    _set_master_key(monkeypatch, key)
    _use_objects_dir(monkeypatch, tmp_path / "objects")

    org_id = uuid.uuid4()
    async with dbmod.sessionmaker()() as db:
        await db.execute(text("INSERT INTO core.organizations (id, name) VALUES (:i, 'Org 2')"), {"i": org_id})
        await db.execute(text("""INSERT INTO agent.mcp_servers (org_id, name, transport, endpoint, auth_enc)
                              VALUES (:o, 'srv', 'stdio', 'local', :a)"""),
                         {"o": org_id, "a": crypto.encrypt(b"token-khong-doi", b"mcp_server_auth")})
        await db.commit()
        before = (await db.execute(text(
            "SELECT auth_enc FROM agent.mcp_servers WHERE org_id = :o"), {"o": org_id})).scalar_one()

    monkeypatch.setenv("GH_BUNDLE_PASSWORD", "mat-khau-goi-rat-dai-va-manh")
    bundle_path = tmp_path / "x.ghbundle"
    await bundle._export(str(bundle_path))

    target_db = f"gh_bundle_target_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {target_db}")
    try:
        _use_database(monkeypatch, f"{PG.replace('postgresql://', 'postgresql+asyncpg://')}/{target_db}")
        await dbmod.dispose_engine()
        await bundle._import(str(bundle_path))

        async with dbmod.sessionmaker()() as db:
            after = (await db.execute(text(
                "SELECT auth_enc FROM agent.mcp_servers WHERE org_id = :o"), {"o": org_id})).scalar_one()
        assert bytes(after) == bytes(before)  # khoá cũ == khoá hiện tại → bỏ qua bước mã hoá lại (bytes y hệt)
    finally:
        await dbmod.dispose_engine()
        _admin(f"DROP DATABASE IF EXISTS {target_db} WITH (FORCE)")
