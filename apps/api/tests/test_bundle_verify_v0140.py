"""v0.1.40 (F-12): `python -m gh.bundle verify --in <path|->` — kiểm gói đọc lại được mà KHÔNG đụng CSDL.

genh dùng lệnh này sau mỗi lần chép bản sao ngoài máy ra ổ USB/NAS (GH-EB03 nếu không đọc lại được). Hợp đồng mã
thoát: 0 OK (stdout đúng một dòng JSON, không bí mật) · 2 sai khoá / gói hỏng / `pg_restore --list` lỗi · 3 không
tương thích · 1 khác.
"""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import orjson
import psycopg
import pytest

from gh import bundle
from gh.chassis import objects as objects_mod
from gh.config import get_settings

API_DIR = Path(__file__).resolve().parents[1]
PASSWORD = "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY-23456"


def _cli(path: Path, password: str = PASSWORD) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GH_BUNDLE_PASSWORD": password}
    return subprocess.run([sys.executable, "-m", "gh.bundle", "verify", "--in", str(path)], cwd=API_DIR, env=env,
                          capture_output=True, text=True, timeout=300, check=False)


@pytest.fixture
async def exported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> Path:
    """Gói thật xuất từ `fresh_db` (pg_dump + một object), mật khẩu = PASSWORD."""
    monkeypatch.setenv("GH_OBJECTS_DIR", str(tmp_path / "objects"))
    get_settings.cache_clear()
    objects_mod.reset_object_store()
    store = objects_mod.get_object_store()
    await store.put("org-1/tai-lieu.txt", b"noi dung")
    monkeypatch.setenv("GH_BUNDLE_PASSWORD", PASSWORD)
    out = tmp_path / "ban-sao.ghbundle"
    await bundle._export(str(out))
    objects_mod.reset_object_store()
    return out


def _open(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Giải mã + đọc toàn bộ tar của gói (để dựng gói hỏng có chủ đích)."""
    raw = path.read_bytes()
    _magic, header_line, ct = raw.split(b"\n", 2)
    tar_bytes = bundle._decrypt_bundle(ct, orjson.loads(header_line), header_line, PASSWORD)
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for m in tar.getmembers():
            f = tar.extractfile(m)
            if f is not None:
                files[m.name] = f.read()
    return orjson.loads(files["manifest.json"]), files


def _build(path: Path, manifest: dict[str, Any], files: dict[str, bytes]) -> None:
    files = {**files, "manifest.json": orjson.dumps(manifest)}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    header, ct = bundle._encrypt_bundle(buf.getvalue(), PASSWORD)
    path.write_bytes(bundle.MAGIC + b"\n" + orjson.dumps(header) + b"\n" + ct)


async def test_verify_real_bundle_ok_prints_one_json_line_without_secrets(exported: Path) -> None:
    r = _cli(exported)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) == 1
    out = orjson.loads(lines[0])
    assert out["ok"] is True
    assert isinstance(out["alembic_revision"], str) and out["alembic_revision"]
    assert out["objects"] == 1
    assert out["db_dump_bytes"] > 0
    assert isinstance(out["created_at"], str)
    assert set(out) == {"ok", "alembic_revision", "objects", "db_dump_bytes", "created_at"}
    # không bí mật: không mật khẩu, không keys.json/khoá master
    assert PASSWORD not in r.stdout + r.stderr
    assert "master_key" not in r.stdout


async def test_verify_wrong_password_exit_2(exported: Path) -> None:
    r = _cli(exported, password="ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ")
    assert r.returncode == 2
    assert r.stdout == ""


async def test_verify_tampered_ciphertext_exit_2(exported: Path, tmp_path: Path) -> None:
    raw = bytearray(exported.read_bytes())  # noqa: ASYNC240
    raw[-10] ^= 1
    bad = tmp_path / "hong.ghbundle"
    bad.write_bytes(bytes(raw))
    r = _cli(bad)
    assert r.returncode == 2
    assert r.stdout == ""


async def test_verify_truncated_dump_with_matching_sha_exit_2(exported: Path, tmp_path: Path) -> None:
    """db.dump bị cắt nhưng sha256 trong manifest khớp (gói "hợp lệ" về mặt mã hoá) ⇒ `pg_restore --list` lỗi ⇒ 2."""
    manifest, files = _open(exported)
    dump = files["db.dump"][:200]
    files["db.dump"] = dump
    manifest["db_dump_sha256"] = hashlib.sha256(dump).hexdigest()
    bad = tmp_path / "cat.ghbundle"
    _build(bad, manifest, files)
    r = _cli(bad)
    assert r.returncode == 2, r.stderr
    assert "không đọc được bản CSDL" in r.stderr


async def test_verify_object_count_mismatch_exit_2(exported: Path, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, files = _open(exported)
    manifest["object_count"] = 5
    bad = tmp_path / "thieu.ghbundle"
    _build(bad, manifest, files)
    monkeypatch.setenv("GH_BUNDLE_PASSWORD", PASSWORD)
    with pytest.raises(bundle.BundleError) as exc:
        await bundle._verify(str(bad))
    assert exc.value.code == 2


async def test_verify_unknown_package_version_exit_3(exported: Path, tmp_path: Path) -> None:
    manifest, files = _open(exported)
    manifest["package_version"] = 99
    bad = tmp_path / "la.ghbundle"
    _build(bad, manifest, files)
    r = _cli(bad)
    assert r.returncode == 3


async def test_verify_does_not_touch_database(exported: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không mở kết nối CSDL nào (psycopg.connect ném lỗi vẫn qua) và dọn thư mục tạm."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("verify không được kết nối CSDL")

    monkeypatch.setenv("GH_BUNDLE_PASSWORD", PASSWORD)
    # Vá trong ngữ cảnh riêng: fixture fresh_db cần psycopg.connect thật lúc dọn CSDL.
    with pytest.MonkeyPatch.context() as m:
        m.setattr(psycopg, "connect", boom)
        m.setattr(bundle.psycopg, "connect", boom)
        m.setenv("GH_DATABASE_URL", "postgresql+asyncpg://khong:co@127.0.0.1:1/khong_co")
        m.setattr(bundle.tempfile, "tempdir", str(tmp_path))
        get_settings.cache_clear()
        out = await bundle._verify(str(exported))
    get_settings.cache_clear()
    assert out["ok"] is True and out["objects"] == 1
    assert not list(tmp_path.glob("gh-bundle-verify-*"))  # noqa: ASYNC240
