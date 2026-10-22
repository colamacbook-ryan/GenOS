"""v0.1.49 (QD-16) — "Việc Sếp cần làm" › Gen-hub lưu thêm quyền ĐỌC (lịch/mail/việc/Drive) của lần Kiểm tra.

`read_scopes` / `read_missing` chỉ để hiển thị "Quyền đọc thêm (không bắt buộc)": Đạt/Lỗi và dòng "xong" của Gen-hub
KHÔNG phụ thuộc chúng. Chỉ lưu khi Kiểm tra XANH (lượt đỏ trả quyền cũ / toàn False — gợi ý "tick thêm quyền" cạnh
"Lỗi" là chỉ sai cách sửa). Khoá lạ trong kết quả Kiểm tra (vd token) không bao giờ vào `detail`."""

from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.boss_checks import service as boss
from gh.hub_link import service as hub
from tests.conftest import Api, verify_pin
from tests.phase2 import org_id

TOKEN = "ghtok_SieuBiMat_1234567890abcdef"
SCOPES = {"calendar": False, "mail": False, "tasks": True, "drive": True}
MISSING = ["đọc lịch", "đọc mail"]


def _fake_test_link(result: dict[str, Any]) -> Any:
    async def fake(db: Any, redis: Any, client: Any, *, user: Any) -> dict[str, Any]:
        return result

    return fake


async def test_hub_pass_keeps_read_scopes(owner_api: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "test_link", _fake_test_link({
        "ok": True, "latency_ms": 42, "exposed_tools": ["a", "b"], "missing_tools": [], "read_scopes": SCOPES,
        "read_missing": MISSING, "write_tools": ["mcp-1__gmail_send"], "token": TOKEN, "account": "boss@example.com"}))
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/boss-checks/hub/run", {})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "pass" and out["error_code"] is None
    assert out["detail"]["read_scopes"] == SCOPES and out["detail"]["read_missing"] == MISSING
    assert set(out["detail"]) == {"latency_ms", "exposed_tools", "missing_tools", "read_scopes", "read_missing"}
    # Lưu CSDL: giữ quyền đọc, KHÔNG có khoá lạ (token, email, write_tools).
    await db.rollback()
    stored = (await db.execute(text("SELECT detail FROM ops.boss_checks WHERE check_key = 'hub'"))).scalar_one()
    assert stored["read_scopes"] == SCOPES and stored["read_missing"] == MISSING
    raw = orjson.dumps(stored).decode()
    assert TOKEN not in raw and "boss@example.com" not in raw and "write_tools" not in raw
    # Hàng Gen-hub vẫn "xong" dù thiếu quyền đọc lịch/mail (không bắt buộc).
    ov = (await owner_api.get("/boss-checks")).json()
    row = next(x for x in ov["rows"] if x["key"] == "hub")
    assert row["done"] is True
    assert ov["results"]["hub"]["detail"]["read_missing"] == MISSING


async def test_hub_old_result_without_scopes(owner_api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kết quả Kiểm tra kiểu cũ (không có read_scopes) ⇒ không có khoá quyền đọc (web ẩn dòng) — không lỗi."""
    monkeypatch.setattr(hub, "test_link", _fake_test_link({"ok": True, "latency_ms": 5, "exposed_tools": [],
                                                           "missing_tools": []}))
    await verify_pin(owner_api)
    out = (await owner_api.send("POST", "/boss-checks/hub/run", {})).json()
    assert out["status"] == "pass" and "read_scopes" not in out["detail"] and "read_missing" not in out["detail"]


async def test_hub_fail_does_not_record_scopes(owner_api: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kiểm tra đỏ (token bị từ chối, mạng…) ⇒ KHÔNG lưu quyền đọc: `test_link.fail()` trả quyền cũ trong CSDL (toàn
    False khi chưa từng xanh) — web sẽ hiện "Lịch ✗ … tick thêm quyền" cạnh "Lỗi", chỉ sai cách sửa."""
    monkeypatch.setattr(hub, "test_link", _fake_test_link({
        "ok": False, "error": "Token Gen-hub sai", "error_code": "HUB_AUTH", "latency_ms": 3, "exposed_tools": [],
        "missing_tools": ["kho_tom_tat"], "read_scopes": dict.fromkeys(SCOPES, False),
        "read_missing": ["đọc lịch", "đọc mail", "đọc việc (Google Tasks)", "tìm tệp Drive"]}))
    await verify_pin(owner_api)
    out = (await owner_api.send("POST", "/boss-checks/hub/run", {})).json()
    assert out["status"] == "fail" and out["error_code"] == "HUB_AUTH"
    assert "read_scopes" not in out["detail"] and "read_missing" not in out["detail"]
    await db.rollback()
    stored = (await db.execute(text("SELECT detail FROM ops.boss_checks WHERE check_key = 'hub'"))).scalar_one()
    assert "read_scopes" not in (stored or {}) and "read_missing" not in (stored or {})


async def test_clean_detail_whitelist(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    rec = await boss.record(db, org, "hub", "pass", detail={"read_scopes": SCOPES, "read_missing": MISSING,
                                                             "token": TOKEN})
    await db.commit()
    assert rec["detail"] == {"read_scopes": SCOPES, "read_missing": MISSING}


# ─── v0.1.50 (F-81): quyền GHI Kho (kho_create, kho_update) — cũng chỉ để hiển thị ───────────────────────────────

WSCOPES = {"kho": False, "kho_create": True, "kho_update": False}
WMISSING = ["ghi Kho (kho_update)"]


async def test_hub_pass_keeps_write_scopes(owner_api: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "test_link", _fake_test_link({
        "ok": True, "latency_ms": 42, "exposed_tools": ["a"], "missing_tools": [], "read_scopes": SCOPES,
        "read_missing": MISSING, "write_scopes": WSCOPES, "write_missing": WMISSING, "token": TOKEN}))
    await verify_pin(owner_api)
    out = (await owner_api.send("POST", "/boss-checks/hub/run", {})).json()
    assert out["status"] == "pass" and out["detail"]["write_scopes"] == WSCOPES
    assert out["detail"]["write_missing"] == WMISSING
    await db.rollback()
    stored = (await db.execute(text("SELECT detail FROM ops.boss_checks WHERE check_key = 'hub'"))).scalar_one()
    assert stored["write_scopes"] == WSCOPES and TOKEN not in orjson.dumps(stored).decode()
    # Thiếu quyền ghi KHÔNG làm dòng Gen-hub "chưa xong".
    ov = (await owner_api.get("/boss-checks")).json()
    assert next(x for x in ov["rows"] if x["key"] == "hub")["done"] is True
    assert ov["results"]["hub"]["detail"]["write_missing"] == WMISSING


async def test_hub_fail_does_not_record_write_scopes(owner_api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "test_link", _fake_test_link({
        "ok": False, "error": "Token Gen-hub sai", "error_code": "HUB_AUTH", "latency_ms": 3, "exposed_tools": [],
        "missing_tools": [], "write_scopes": WSCOPES, "write_missing": WMISSING}))
    await verify_pin(owner_api)
    out = (await owner_api.send("POST", "/boss-checks/hub/run", {})).json()
    assert out["status"] == "fail" and "write_scopes" not in out["detail"] and "write_missing" not in out["detail"]


async def test_clean_detail_whitelist_write_scopes(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    rec = await boss.record(db, org, "hub", "pass", detail={"write_scopes": WSCOPES, "write_missing": WMISSING,
                                                             "token": TOKEN})
    await db.commit()
    assert rec["detail"] == {"write_scopes": WSCOPES, "write_missing": WMISSING}


async def test_kho_write_row_is_optional_and_not_runnable(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    assert (await owner_api.send("POST", "/boss-checks/kho_write/run", {})).status_code == 404
    ov = (await owner_api.get("/boss-checks")).json()
    row = next(x for x in ov["rows"] if x["key"] == "kho_write")
    assert row["row"] == 9 and row["optional"] is True and row["done"] is False
    assert ov["required_total"] == boss.REQUIRED_TOTAL == sum(1 for x in ov["rows"] if not x["optional"]) == 1
    await boss.record(db, org, "kho_write", "pass")
    await db.commit()
    ov = (await owner_api.get("/boss-checks")).json()
    assert next(x for x in ov["rows"] if x["key"] == "kho_write")["done"] is True
    assert ov["required_done"] == 0  # tuỳ chọn: không tính vào số bắt buộc
