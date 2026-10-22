"""P2-2 (A10, gói G) — CHỈ api sinh / giao mã thiết lập; worker không sinh lại.

Lỗi cũ: `app.py` và `worker.py` đều gọi `bootstrap()`; worker khởi động sau sinh mã mới, ghi đè hash trong
`ops.setup_state` ⇒ mã trong `run/setup-code.json` (api đã giao cho trình cài / Owner) trở nên sai. Sửa: `bootstrap()`
có cờ `mint_setup_code` — api để mặc định (True), worker truyền False.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import orjson
import pytest
from sqlalchemy import text

from gh.bootstrap import SETUP_CODE_FILE, bootstrap
from gh.config import get_settings
from gh.crypto import token_digest

GH = Path(__file__).resolve().parents[1] / "gh"


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(tmp_path))
    monkeypatch.setenv("GH_SETUP_TOKEN", "")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def _hash(db) -> bytes | None:  # type: ignore[no-untyped-def]
    row = (await db.execute(text("SELECT setup_token_hash FROM ops.setup_state"))).scalar_one_or_none()
    return None if row is None else bytes(row)


def _file_token(run_dir: Path) -> str:
    return str(orjson.loads((run_dir / SETUP_CODE_FILE).read_bytes())["setup_token"])


async def test_worker_bootstrap_does_not_overwrite_the_code_api_delivered(db, run_dir: Path) -> None:  # type: ignore[no-untyped-def]
    api = await bootstrap(db)                                       # api: sinh + giao mã
    assert api.setup_token and _file_token(run_dir) == api.setup_token
    hashed = await _hash(db)
    assert hashed == bytes(token_digest(api.setup_token))
    before = (run_dir / SETUP_CODE_FILE).read_bytes()

    worker = await bootstrap(db, mint_setup_code=False)             # worker khởi động sau
    assert worker.setup_token is None and worker.setup_finished is False
    assert (run_dir / SETUP_CODE_FILE).read_bytes() == before       # tệp mã không bị ghi lại
    assert await _hash(db) == hashed                                # hash không bị ghi đè
    assert token_digest(_file_token(run_dir)) == await _hash(db)    # mã trong tệp VẪN khớp hash


async def test_worker_first_then_api_still_delivers_a_working_code(db, run_dir: Path) -> None:  # type: ignore[no-untyped-def]
    worker = await bootstrap(db, mint_setup_code=False)             # worker lên trước api
    assert worker.setup_token is None
    assert await _hash(db) is None and not (run_dir / SETUP_CODE_FILE).exists()   # không sinh gì
    api = await bootstrap(db)
    assert api.setup_token and _file_token(run_dir) == api.setup_token
    assert await _hash(db) == bytes(token_digest(api.setup_token))


async def test_worker_bootstrap_still_invalidates_the_code_after_owner_created(db, run_dir: Path) -> None:  # type: ignore[no-untyped-def]
    await bootstrap(db)
    assert (run_dir / SETUP_CODE_FILE).is_file()
    await db.execute(text("UPDATE ops.setup_state SET finished_at = now()"))
    res = await bootstrap(db, mint_setup_code=False)
    assert res.setup_token is None and res.setup_finished is True
    assert not (run_dir / SETUP_CODE_FILE).exists() and await _hash(db) is None


def _bootstrap_calls(py: str) -> list[ast.Call]:
    tree = ast.parse((GH / py).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "bootstrap"]


def test_only_the_api_process_mints_the_setup_code() -> None:
    """Chốt bằng mã nguồn: worker.py gọi bootstrap(mint_setup_code=False); app.py (api) gọi mặc định (không tắt cờ)."""
    def flag(call: ast.Call) -> object:
        return next((k.value.value for k in call.keywords if k.arg == "mint_setup_code"
                     and isinstance(k.value, ast.Constant)), "mặc định")

    worker = _bootstrap_calls("worker.py")
    assert len(worker) == 1 and flag(worker[0]) is False
    api = _bootstrap_calls("app.py")
    assert len(api) == 1 and flag(api[0]) in ("mặc định", True)
