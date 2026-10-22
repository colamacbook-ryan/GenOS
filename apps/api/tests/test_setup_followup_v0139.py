"""v0.1.39 (F-28) — "Hướng dẫn thiết lập" có thêm 2 việc ngoài trình thiết lập: 13 Kết nối Facebook, 14 Nối Gen-hub.

- `GET /setup/follow-up` luôn có n=13/14; `done` suy từ dữ liệu thật (Facebook đang đăng nhập — v0.1.68: `needs_login`
  không tính; Gen-hub Kiểm tra xanh ít nhất một lần).
- Không phải bước của trình thiết lập: các mục 4–11 giữ nguyên, Hoàn tất (`PUT /setup/steps/12`) không bị 13/14 chặn.
"""

from typing import Any

from sqlalchemy import text

from tests.conftest import Api
from tests.phase2 import org_id


async def _items(api: Api) -> dict[int, dict[str, Any]]:
    r = await api.get("/setup/follow-up")
    assert r.status_code == 200, r.text
    return {i["n"]: i for i in r.json()}


async def _social(db, org: object, label: str, status: str,  # type: ignore[no-untyped-def]
                  platform: str = "facebook_personal") -> None:
    await db.execute(text("""INSERT INTO core.social_accounts (org_id, platform, label, status)
                             VALUES (:o, :p, :l, :s)"""), {"o": org, "l": label, "s": status, "p": platform})
    await db.commit()


async def test_follow_up_has_facebook_and_hub_items(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    before = await _items(api)
    assert sorted(before) == [4, 5, 6, 7, 8, 9, 10, 11, 13, 14]
    assert before[13] == {"n": 13, "key": "social", "title": "Kết nối Facebook", "status": "todo", "done": False}
    assert before[14] == {"n": 14, "key": "hub", "title": "Nối Gen-hub", "status": "todo", "done": False}
    org = await org_id(db)

    # Tài khoản mới thêm nhưng chưa đăng nhập → chưa xong.
    await _social(db, org, "Chưa đăng nhập", "pending_login")
    assert (await _items(api))[13]["done"] is False
    # Đã đăng nhập (active) → xong.
    await _social(db, org, "Facebook của Sếp", "active")
    items = await _items(api)
    assert items[13]["done"] is True
    assert items[14]["done"] is False

    # Gen-hub: có dòng nhưng chưa từng Kiểm tra xanh → chưa; last_ok_at có giá trị → xong.
    await db.execute(text("INSERT INTO agent.hub_links (org_id) VALUES (:o)"), {"o": org})
    await db.commit()
    assert (await _items(api))[14]["done"] is False
    await db.execute(text("UPDATE agent.hub_links SET last_ok_at = now() WHERE org_id = :o"), {"o": org})
    await db.commit()
    after = await _items(api)
    assert after[14]["done"] is True
    # Các mục 4–11 không đổi.
    assert {n: after[n] for n in range(4, 12)} == {n: before[n] for n in range(4, 12)}


async def test_needs_login_and_revoked_do_not_count(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    await _social(db, org, "Đã gỡ", "revoked")
    assert (await _items(api))[13]["done"] is False
    await _social(db, org, "Cần đăng nhập lại", "needs_login")
    assert (await _items(api))[13]["done"] is False     # v0.1.68 (F028): phiên hết, phải đăng nhập lại ⇒ chưa xong
    await _social(db, org, "Tạm dừng", "paused")
    assert (await _items(api))[13]["done"] is True


async def test_other_platform_does_not_count_as_facebook(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    await _social(db, org, "Nền tảng khác", "active", platform="zalo_personal")
    assert (await _items(api))[13]["done"] is False


async def test_items_13_14_do_not_block_finish(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    assert (await api.send("POST", "/setup/steps/4/skip")).status_code == 200
    r = await api.send("PUT", "/setup/steps/12")
    assert r.status_code == 200, r.text
    assert r.json()["finished"] is True
    # 13/14 không phải bước của trình thiết lập.
    assert all(s["n"] <= 12 for s in r.json()["steps"])
    items = await _items(api)
    assert items[13]["done"] is False and items[14]["done"] is False
