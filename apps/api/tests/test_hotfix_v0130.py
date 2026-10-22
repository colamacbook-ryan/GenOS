"""v0.1.30 (hotfix) — lỗi "không có model" đúng khuôn lỗi chung.

Trước đây `ModelUnavailable` → `ApiError(503, …, detail={"reasons": […]})` và bước 8 trả `try_error = e.detail` (đối
tượng) → web vẽ đối tượng làm React child → màn /guide/8 sập (React error #31 "object with keys {reasons}").
Giờ: `detail` là câu chữ, `reasons` (danh sách chuỗi) ở cấp ngoài cùng; `try_error` luôn là chuỗi.
"""

from sqlalchemy import text

from gh.errors import MODEL_UNAVAILABLE_HINT, _body, model_unavailable
from tests.conftest import Api
from tests.phase2 import FakeRouter, org_id
from tests.test_rbac_api import login_as
from tests.test_system_update import link  # noqa: F401 — fixture dùng chung (hộp thư genh giả + GitHub giả)


def test_model_unavailable_body_has_code_message_and_top_level_reasons() -> None:
    e = model_unavailable("Chưa có model nào chạy được để dịch", ["gemini: 429", "", "deepseek: đang ngắt mạch"])
    body = _body(e.status, e.code, e.title, e.detail, e.extra)
    assert body["status"] == 503 and body["code"] == "MODEL_UNAVAILABLE"
    assert body["title"] == "Chưa có model nào chạy được để dịch"
    assert isinstance(body["detail"], str) and body["detail"] == MODEL_UNAVAILABLE_HINT
    assert body["reasons"] == ["gemini: 429", "deepseek: đang ngắt mạch"]


async def test_translate_503_detail_is_text_and_reasons_top_level(owner_api: Api, app) -> None:  # type: ignore[no-untyped-def]
    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "x", "text": "Chào anh"})).json()
    app.state.model_router = FakeRouter(down=True)
    for path, payload in ((f"/drafts/{d['id']}/translate", {"lang": "en"}), (f"/drafts/{d['id']}/regenerate", {})):
        r = await owner_api.send("POST", path, payload)
        assert r.status_code == 503
        body = r.json()
        assert body["code"] == "MODEL_UNAVAILABLE"
        assert isinstance(body["detail"], str) and "API & Model" in body["detail"]
        assert body["reasons"] == ["gemini: 429", "deepseek: đang ngắt mạch"]


async def test_step8_try_error_is_string_with_code_and_reasons(owner_api: Api, db, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await db.execute(text("UPDATE ops.setup_state SET completed = CAST(:c AS jsonb) WHERE org_id = :o"),
                     {"c": '{"steps": {"1": "done", "2": "done", "3": "done", "4": "done", "5": "done", '
                           '"6": "done", "7": "done"}}', "o": org})
    await db.commit()
    app.state.model_router = FakeRouter(down=True)
    r = await owner_api.send("PUT", "/setup/steps/8", {"name": "Trợ lý Mai", "role_desc": "Chăm sóc khách hàng",
                                                        "try_message": "Chào bạn"})
    assert r.status_code == 200, r.text
    agent = r.json()["agent"]
    assert agent["try_reply"] is None
    assert isinstance(agent["try_error"], str) and agent["try_error"] == MODEL_UNAVAILABLE_HINT
    assert agent["try_error_code"] == "MODEL_UNAVAILABLE"
    assert agent["try_reasons"] == ["gemini: 429", "deepseek: đang ngắt mạch"]


# ── "mất nút update": bộ đệm 10 phút + POST /system/update/check ────────────────────────────────────────────────

async def test_update_check_bypasses_cache_and_is_rate_limited(owner_api: Api, client, db, link, redis,  # type: ignore[no-untyped-def]  # noqa: F811
                                                               monkeypatch) -> None:
    from gh.system_api import update as upd

    assert upd.LATEST_CACHE_SECONDS <= 600
    await redis.delete(upd.LATEST_CACHE_KEY, upd.CHECK_LOCK_KEY)
    r = (await owner_api.get("/system/update")).json()
    assert r["latest"] == "v0.1.17" and r["checked_at"]
    assert 0 < await redis.ttl(upd.LATEST_CACHE_KEY) <= 600

    # GitHub phát hành bản mới trong lúc bộ đệm còn hạn: GET vẫn trả bản đệm, "Kiểm tra bản mới" thấy ngay.
    async def newer(repo: str) -> dict[str, str | None]:
        return {"tag": "v0.1.18", "url": "https://example/v0.1.18", "published_at": None, "notes": ""}

    monkeypatch.setattr(upd, "fetch_latest", newer)
    assert (await owner_api.get("/system/update")).json()["latest"] == "v0.1.17"
    r = await owner_api.send("POST", "/system/update/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["latest"] == "v0.1.18" and body["update_available"] and body["throttled"] is False

    # Bấm dồn trong 30 giây: không hỏi GitHub lại, trả bản đệm + throttled.
    async def boom(repo: str) -> None:
        raise AssertionError("không được hỏi GitHub khi đang giới hạn")

    monkeypatch.setattr(upd, "fetch_latest", boom)
    again = (await owner_api.send("POST", "/system/update/check")).json()
    assert again["throttled"] is True and again["latest"] == "v0.1.18"

    # Hỏi GitHub lỗi khi bắt buộc: giữ bản đệm cũ, không mất nút cập nhật.
    async def down(repo: str) -> None:
        return None

    monkeypatch.setattr(upd, "fetch_latest", down)
    await redis.delete(upd.CHECK_LOCK_KEY)
    kept = (await owner_api.send("POST", "/system/update/check")).json()
    assert kept["latest"] == "v0.1.18" and kept["update_available"]

    auditor = await login_as(client, db, "auditor")
    assert (await auditor.send("POST", "/system/update/check")).status_code == 403
