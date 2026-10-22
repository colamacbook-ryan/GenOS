"""Mọi route dùng phiên CSDL phải commit TRƯỚC khi gửi phản hồi (scope="function").

Nếu không, client nhận 200 rồi gửi ngay request kế tiếp (vd. nhập PIN xong thử lại thao tác) có thể đọc trước khi
commit → 423 lần nữa dù PIN đúng. Lỗi này chỉ lộ ra dưới máy chủ thật nên được khoá bằng kiểm tra cấu trúc.
"""

from fastapi.routing import APIRoute

from gh.app import create_app
from gh.db import get_db


def _walk(dep, out):  # type: ignore[no-untyped-def]
    for d in dep.dependencies:
        if d.call is get_db:
            out.append(d)
        _walk(d, out)


def test_every_db_dependency_commits_before_response() -> None:
    app = create_app(with_lifespan=False)
    routes = [r for r in app.router.routes if isinstance(r, APIRoute)]
    if not routes:  # FastAPI mới gói router con
        routes = [r for inc in app.router.routes for r in getattr(getattr(inc, "original_router", None), "routes", [])
                  if isinstance(r, APIRoute)]
    found = []
    for r in routes:
        _walk(r.dependant, found)
    assert found, "không tìm thấy route nào dùng get_db"
    assert all(d.scope == "function" for d in found)
