"""v0.1.68 (gói C2) — kết quả kiểm tự ghi + một đích "thêm nguồn AI" (F028, F029 phần máy chủ, F039).

F028: nút "Lưu & kiểm tra" ở Kết nối › Gen-hub (POST /hub/link/test) và "Đọc ngay" ở /social phải ghi bản kiểm vào
`ops.boss_checks` — nếu không, Hướng dẫn nói "Đã xong" mà Việc Sếp cần làm vẫn "Chưa kiểm" và Gen vẫn nhắc "Nối
Gen-hub".
F029: mọi câu chỉ chỗ thêm nguồn AI (máy chủ) cùng một đích `/setup?buoc=nguon-ai`; việc `boss.ai` của Gen không còn trỏ
thẻ Kết nối (không có chỗ nhập khoá) mà dẫn thẳng màn Thiết lập nguồn AI.
F039: đích "Làm thử" của bài học N07 phải luôn có mặt trên màn (không phụ thuộc có sự cố hay không)."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from gh.boss_checks import service as boss
from gh.gen.coach import signals
from gh.hub_link import service as hub
from gh.providers import readiness
from tests.conftest import Api, verify_pin
from tests.phase2 import org_id
from tests.test_boss_checks_v0139 import CANARY, ENDPOINT, CanaryHub, canary_hub  # noqa: F401 — fixture
from tests.test_social import _add as social_add
from tests.test_social import _login as social_login

ROOT = Path(__file__).resolve().parents[1] / "gh"


async def _hub_row(api: Api) -> dict[str, Any]:
    ov = (await api.get("/boss-checks")).json()
    return {"done": next(r for r in ov["rows"] if r["key"] == "hub")["done"], "result": ov["results"]["hub"]}


# ─── F028 Gen-hub ───────────────────────────────────────────────────────────

async def test_hub_link_test_records_boss_check(owner_api: Api, canary_hub: CanaryHub, redis: Redis) -> None:  # noqa: F811
    await verify_pin(owner_api)
    assert (await owner_api.send("PATCH", "/hub/link", {"endpoint": ENDPOINT, "token": CANARY})).status_code == 200
    assert (await _hub_row(owner_api))["result"] is None          # chưa kiểm lần nào
    r = await owner_api.send("POST", "/hub/link/test", {})        # đường Hướng dẫn việc 14 / Kết nối bảo làm
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    row = await _hub_row(owner_api)
    assert row["done"] is True and row["result"]["status"] == "pass"
    assert row["result"]["detail"]["exposed_tools"] == 5
    # Lần kiểm lỗi sau đó ghi 'fail' (dòng hết "xong"), và token không lọt vào kết quả.
    canary_hub.mode = "401"
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.status_code == 200 and r.json()["ok"] is False
    row = await _hub_row(owner_api)
    assert row["done"] is False and row["result"]["status"] == "fail"
    assert row["result"]["error_code"] == "HUB_TOKEN_REJECTED"
    assert CANARY not in json.dumps(row)


async def test_hub_link_test_transient_not_recorded(owner_api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(db: Any, redis: Any, client: Any, *, user: Any) -> dict[str, Any]:
        return {"ok": False, "error": "Bận", "error_code": "HUB_RATE_LIMITED", "exposed_tools": [],
                "missing_tools": []}

    monkeypatch.setattr(hub, "test_link", fake)
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert (await _hub_row(owner_api))["result"] is None          # lỗi tạm không thay kết quả đã lưu


async def test_hub_link_test_refreshes_coach(owner_api: Api, canary_hub: CanaryHub, db: Any) -> None:  # noqa: F811
    """Sau khi kiểm xanh qua /hub/link/test, Gen không còn nhắc "Nối Gen-hub" (tín hiệu boss.hub.done)."""
    await verify_pin(owner_api)
    await owner_api.send("PATCH", "/hub/link", {"endpoint": ENDPOINT, "token": CANARY})
    await owner_api.send("POST", "/hub/link/test", {})
    await db.rollback()
    org = await org_id(db)
    ov = await boss.overview(db, org)
    assert next(r for r in ov["rows"] if r["key"] == "hub")["done"] is True


# ─── F028 Facebook ──────────────────────────────────────────────────────────

async def test_social_read_now_records_boss_check(owner_api: Api, redis: Redis) -> None:
    acc = await social_add(owner_api)
    await social_login(owner_api, redis, acc["id"])
    r = await owner_api.send("POST", f"/social/accounts/{acc['id']}/read", {})     # "Đọc ngay" ở /social
    assert r.status_code == 200, r.text
    ov = (await owner_api.get("/boss-checks")).json()
    fb = ov["results"]["facebook"]
    assert fb is not None and fb["status"] == "pending" and fb["detail"]["job_status"] == "queued", fb
    from gh.db import admin_sessionmaker

    async with admin_sessionmaker()() as adb:
        await adb.execute(text("UPDATE agent.browser_jobs SET status = 'done' WHERE kind = 'read'"))
        await adb.commit()
    ov = (await owner_api.get("/boss-checks")).json()
    assert ov["results"]["facebook"]["status"] == "pass"
    assert next(r for r in ov["rows"] if r["key"] == "facebook")["done"] is True


async def test_social_read_now_gen_or_schedule_does_not_record(owner_api: Api, redis: Redis, db: Any) -> None:
    """Chỉ lượt do Sếp bấm ("user") mới ghi bản kiểm; lượt theo lịch / Gen không giả làm Sếp đã kiểm."""
    acc = await social_add(owner_api)
    await social_login(owner_api, redis, acc["id"])
    from gh.social import service as social

    await db.rollback()
    org = await org_id(db)
    # Lịch (user=None) không ghi bản kiểm.
    await social.request_read(db, redis, org_id=org, account_id=uuid.UUID(acc["id"]), via="schedule", user=None)
    assert (await owner_api.get("/boss-checks")).json()["results"]["facebook"] is None


# ─── F029 một đích "thêm nguồn AI" ──────────────────────────────────────────

def test_ai_source_one_destination_server_side() -> None:
    """Câu máy chủ nói chỗ thêm nguồn AI: không còn dẫn "Kết nối › Bộ não AI" / "Cài đặt › Bộ não AI" (không có chỗ
    nhập khoá ở đó) mà dẫn màn Thiết lập nguồn AI; nguồn đã tắt bật lại ở API & Model."""
    for msg in (readiness.REASON_NO_SOURCE, readiness.REASON_BOX_DOWN):
        assert "Bộ não AI" not in msg and "Thiết lập nguồn AI" in msg, msg
    assert "Bộ não AI" not in readiness.REASON_DISABLED and "API & Model" in readiness.REASON_DISABLED
    assert readiness.FIX_AI == "/setup?buoc=nguon-ai" and readiness.FIX_API == "/api"
    assert {k: v for k, v in readiness.FIX_TO.items() if v != readiness.FIX_AI} == {"disabled": readiness.FIX_API}
    src = (ROOT / "providers" / "readiness.py").read_text(encoding="utf-8")
    body = src[src.index("REASON_NO_SOURCE ="):]               # phần câu chữ (bỏ chú thích đầu tệp)
    assert "Kết nối › Bộ não AI" not in body and "connections#brain" not in src


def test_coach_boss_ai_links_to_setup_page() -> None:
    assert "boss.ai" not in signals.TODO_TARGETS
    assert signals.TODO_LINKS["boss.ai"] == "/setup?buoc=nguon-ai"
    assert "api.bindings" not in signals.TODO_TARGETS.values() and "api.bindings" not in signals.TODO_LINKS.values()


# ─── F039 bài học N07 ───────────────────────────────────────────────────────

def test_lesson_n07_target_always_rendered() -> None:
    """Dải "Cần Sếp xử lý" (đích của "Làm thử") luôn có mặt trên Hôm nay từ v0.1.68 — lúc khoẻ chỉ còn một dòng báo yên
    tâm — nên bài học không được nói "dải này tự ẩn" (nút Làm thử trỏ vào chính dòng đó)."""
    lessons = json.loads((ROOT / "gen" / "coach" / "content" / "lessons.json").read_text(encoding="utf-8"))
    items = lessons["lessons"] if isinstance(lessons, dict) and "lessons" in lessons else lessons
    n07 = next(x for x in items if x["id"] == "N07")
    assert n07["try"]["target"] == "overview.needs_boss"
    assert "tự ẩn" not in n07["body"] and "Không có việc nào cần Sếp xử lý" in n07["body"]
