"""F128 — mã thiết lập Owner (bí mật dùng một lần) không được vào log của api: bootstrap ghi vào tệp quyền 0600
trong hộp thư dữ liệu (`Settings.host_link_dir`, genh đọc); log chỉ ghi đường dẫn tệp."""

import logging
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from gh.bootstrap import SETUP_CODE_FILE, bootstrap
from gh.config import get_settings


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(tmp_path))
    monkeypatch.setenv("GH_SETUP_TOKEN", "")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _all_logs(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(r.getMessage() + " " + str(r.args) + " " + (r.exc_text or "") for r in caplog.records)


async def test_ma_thiet_lap_khong_vao_log_ma_vao_tep_0600(db, run_dir: Path,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    res = await bootstrap(db)
    assert res.setup_token
    path = run_dir / SETUP_CODE_FILE
    assert path.is_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert res.setup_token in path.read_text(encoding="utf-8")
    logs = _all_logs(caplog)
    assert res.setup_token not in logs
    assert "token=" not in logs
    assert str(path) in logs          # log chỉ ghi đường dẫn tệp


async def test_khong_co_thu_muc_hop_thu_van_khong_lo_ma(db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(tmp_path / "khong-co"))
    monkeypatch.setenv("GH_SETUP_TOKEN", "")
    get_settings.cache_clear()
    caplog.set_level(logging.DEBUG)
    try:
        res = await bootstrap(db)
    finally:
        get_settings.cache_clear()
    assert res.setup_token
    assert res.setup_token not in _all_logs(caplog)
    assert not (tmp_path / "khong-co").exists()


async def test_chay_lai_xoa_tep_khi_ma_vo_hieu(db, run_dir: Path) -> None:
    await bootstrap(db)
    path = run_dir / SETUP_CODE_FILE
    assert path.is_file()
    # Owner đã thiết lập xong ⇒ mã vô hiệu ⇒ tệp bị xoá
    await db.execute(text("UPDATE ops.setup_state SET finished_at = now()"))
    res = await bootstrap(db)
    assert res.setup_token is None
    assert not path.exists()


async def test_ma_cau_hinh_san_khong_ghi_tep_khong_vao_log(db, run_dir: Path, monkeypatch: pytest.MonkeyPatch,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("GH_SETUP_TOKEN", "MA-DAT-SAN-K7QF-2MXD")
    get_settings.cache_clear()
    caplog.set_level(logging.DEBUG)
    res = await bootstrap(db)
    assert res.setup_token is None
    assert not (run_dir / SETUP_CODE_FILE).exists()
    assert "MA-DAT-SAN-K7QF-2MXD" not in _all_logs(caplog)
