"""v0.1.35 (F-14): chặn tái phát — mọi phản hồi lỗi đúng khuôn problem+json và `detail` LUÔN là chuỗi hoặc null.

(A) gọi thật qua client sinh từng mã lỗi chính; (B) quét tĩnh mã nguồn `gh/**` tìm `ApiError/conflict/forbidden`
nhận literal dict/list/set/tuple làm detail; (C) `_body` tự chữa detail không phải chuỗi."""

import ast
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from gh.errors import _body
from tests.conftest import OWNER, Api
from tests.phase2 import FakeRouter
from tests.test_rbac_api import login_as

GH_DIR = Path(__file__).resolve().parents[1] / "gh"


def _check(r: httpx.Response, status: int, code: str | None = None) -> dict[str, Any]:
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json"), (status, r.headers["content-type"])
    body: dict[str, Any] = r.json()
    assert isinstance(body["title"], str) and body["title"].strip(), body
    assert isinstance(body["code"], str) and body["code"].strip(), body
    assert body["status"] == status, body
    assert body["detail"] is None or isinstance(body["detail"], str), body
    if code is not None:
        assert body["code"] == code, body
    return body


# ── (A) bảng lỗi thật ────────────────────────────────────────────────────────────────────────────────────────

async def test_setup_required_428_is_problem_json(client) -> None:  # type: ignore[no-untyped-def]
    _check(await Api(client).get("/auth/me"), 428, "SETUP_REQUIRED")


async def test_error_table_is_problem_json(owner_api: Api, client, db, app) -> None:  # type: ignore[no-untyped-def]
    # 401: chưa đăng nhập (client mới, cùng app đã thiết lập).
    anon = Api(client.__class__(transport=client._transport, base_url="http://test"))
    _check(await anon.get("/auth/me"), 401, "UNAUTHENTICATED")
    _check(await anon.send("POST", "/auth/login", {"email": "ai@x.vn", "password": "sai"}), 401,
           "INVALID_CREDENTIALS")
    # 403: vai trò thiếu quyền.
    operator = await login_as(client, db, "operator")
    _check(await operator.get("/users"), 403, "FORBIDDEN")
    # 404: đối tượng không tồn tại.
    _check(await owner_api.get(f"/drafts/{uuid.uuid4()}"), 404, "NOT_FOUND")
    # 409: conflict có sẵn (không tạo được Owner thứ hai).
    _check(await anon.send("PUT", "/setup/steps/2", {"token": "test-setup-token", **OWNER, "email": "b@x.vn"}),
           409, "OWNER_EXISTS")
    # 422: body sai kiểu → validation_error_handler.
    body = _check(await anon.send("POST", "/auth/login", {"email": 123}), 422, "VALIDATION")
    assert isinstance(body["errors"], dict) and body["errors"]
    # 423 PIN_REQUIRED: route có PIN sẵn (mời người dùng).
    _check(await owner_api.send("POST", "/users", {"display_name": "Bé Na", "email": "na@example.vn",
                                                    "role": "operator"}), 423, "PIN_REQUIRED")
    # 422 field_errors (sau khi xác nhận PIN).
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 200
    body = _check(await owner_api.send("POST", "/account/pin", {"current_password": OWNER["password"],
                                                                "new_pin": "12", "new_pin_confirm": "12"}),
                  422, "VALIDATION")
    assert body["errors"] == {"new_pin": "PIN gồm đúng 6 chữ số"}
    # 503 MODEL_UNAVAILABLE (cùng cách test_hotfix_v0130).
    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "x", "text": "Chào anh"})).json()
    app.state.model_router = FakeRouter(down=True)
    body = _check(await owner_api.send("POST", f"/drafts/{d['id']}/translate", {"lang": "en"}), 503,
                  "MODEL_UNAVAILABLE")
    assert all(isinstance(x, str) for x in body["reasons"])

    # 500 INTERNAL.
    async def boom() -> None:
        raise PermissionError(13, "Permission denied", "/var/lib/gh/khoa")

    app.add_api_route("/api/v1/__t/os", boom, methods=["GET"])
    body = _check(await owner_api.get("/__t/os"), 500, "INTERNAL")
    assert body["error_id"] in body["detail"] and "/var/lib" not in str(body)
    # 423 PIN_LOCKED (cuối cùng — khoá PIN của Owner): detail chuỗi, `locked_until` ở khoá ngoài.
    r = None
    for _ in range(6):
        r = await owner_api.send("POST", "/auth/pin/verify", {"pin": "111111"})
        if r.status_code == 423:
            break
    assert r is not None
    body = _check(r, 423, "PIN_LOCKED")
    assert isinstance(body["locked_until"], str) and body["locked_until"]
    # detail không chứa giờ ISO UTC thô (web hiện thẳng) — giờ mở khoá chỉ ở `locked_until`.
    assert body["detail"] == "Thử lại sau ít phút" and body["locked_until"] not in body["detail"]
    # Thao tác cần PIN khi đang khoá (khoá thu hồi phiên PIN → 423 PIN_REQUIRED/LOCKED). v0.1.61: PUT /auth/pin đã gỡ.
    r = await owner_api.send("POST", "/users", {"display_name": "Bé Na", "email": "na@example.vn", "role": "operator"})
    _check(r, 423)


# ── (B) quét tĩnh ────────────────────────────────────────────────────────────────────────────────────────────

BAD_NODES = (ast.Dict, ast.List, ast.Set, ast.Tuple, ast.DictComp, ast.ListComp, ast.SetComp)
#: tên hàm → vị trí (0-based) của đối số `detail`.
DETAIL_POS = {"ApiError": 3, "conflict": 2, "forbidden": 0}


def _callee(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def find_non_string_details(root: Path) -> list[str]:
    bad: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee(node)
            if name not in DETAIL_POS:
                continue
            pos = DETAIL_POS[name]
            cands = [node.args[pos]] if len(node.args) > pos else []
            cands += [kw.value for kw in node.keywords if kw.arg == "detail"]
            if any(isinstance(c, BAD_NODES) for c in cands):
                bad.append(f"{path.relative_to(root.parent).as_posix()}:{node.lineno}")
    return bad


def test_static_scan_detail_never_literal_collection() -> None:
    assert find_non_string_details(GH_DIR) == []


def test_static_scan_detects_violation(tmp_path: Path) -> None:
    pkg = tmp_path / "gh"
    pkg.mkdir()
    (pkg / "x.py").write_text(
        "raise ApiError(423, 'PIN_LOCKED', 't', {'locked_until': 1})\n"
        "raise conflict('C', 't', detail=[1])\n"
        "raise errors.forbidden({'a': 1})\n"
        "raise ApiError(400, 'OK', 't', 'chuỗi', extra={'a': 1})\n"
        "raise conflict('C', 't', {k: 1 for k in 'ab'})\n", encoding="utf-8")
    assert find_non_string_details(pkg) == ["gh/x.py:1", "gh/x.py:2", "gh/x.py:3", "gh/x.py:5"]


# ── (C) _body tự chữa ────────────────────────────────────────────────────────────────────────────────────────

def test_body_moves_non_string_detail_to_context(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="gh.errors")
    b = _body(409, "X", "Xung đột", {"operation": "abc"}, {})
    assert b["detail"] is None and b["context"] == {"operation": "abc"}
    assert "detail không phải chuỗi" in caplog.text
    # Đã có `context` thì giữ nguyên, không ghi đè.
    b = _body(409, "X", "Xung đột", ["a"], {"context": "sẵn"})
    assert b["detail"] is None and b["context"] == "sẵn"
    assert _body(400, "X", "t", "chuỗi", {})["detail"] == "chuỗi"
    assert _body(400, "X", "t", None, {})["detail"] is None
