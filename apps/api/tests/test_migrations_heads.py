"""v0.1.33 (F-13) — cây migration chỉ được có ĐÚNG 1 head.

`tests/conftest.py` (và container `migrate` của compose) chạy `alembic upgrade heads` (số nhiều) nên nếu hai nhánh
cùng thêm một migration nối vào cùng `down_revision`, cả bộ test vẫn xanh — trong khi thứ tự áp hai nhánh trên máy
Boss không xác định, bảng `alembic_version` có nhiều dòng và `gh/bundle.py` (đọc `alembic_version LIMIT 1`, so thứ
tự revision theo một chuỗi thẳng) so sai khi nhập/khôi phục gói. Test này là chốt chặn: chỉ đọc thư mục migrations
(ScriptDirectory), không chạy env.py, không cần DB. Gặp lỗi thì gộp các head bằng
`alembic merge -m "gộp head" <head1> <head2>`.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API_DIR = Path(__file__).resolve().parents[1]


def _script_directory() -> ScriptDirectory:
    cfg = Config(str(API_DIR / "alembic.ini"))
    # alembic.ini ghi `script_location = migrations` (tương đối) — ghim tuyệt đối để test chạy được từ mọi cwd.
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    return ScriptDirectory.from_config(cfg)


def test_migrations_have_exactly_one_head() -> None:
    heads = _script_directory().get_heads()
    assert len(heads) == 1, (
        f"cây migration có {len(heads)} head: {', '.join(sorted(heads))} — phải gộp bằng "
        f"`alembic merge` để chỉ còn 1 head (nhiều head ⇒ thứ tự migrate không xác định, gh/bundle.py so revision sai)"
    )
