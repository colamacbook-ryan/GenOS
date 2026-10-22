"""v0.1.61 (gói A6, F047): tên hiển thị của hồ sơ tự nâng từ mã tài khoản thô lên tên thật khi gặp (danh bạ trước tin),
không đè tên Sếp đã sửa tay, không đổi tên đã tốt, không bao giờ xoá tên bằng tên rỗng."""

from typing import Any

from sqlalchemy import text

from gh.data.ingest import handle_directory
from gh.db import sessionmaker
from tests.phase2 import msg, org_id, put

WA = "84901234567@s.whatsapp.net"


async def _directory(db: Any, redis: Any, org: Any, name: str = "", external_id: str = WA) -> None:
    await handle_directory(db, redis, org, {"channel": "whatsapp", "groups": [
        {"external_id": "120363@g.us", "name": "Nhóm sỉ", "member_count": 1,
         "members": [{"external_id": external_id, "name": name, "phone": "+84901234567"}]}]})
    await db.execute(text("UPDATE core.groups SET listen_mode = 'silent'"))
    await db.commit()


async def _person(db: Any, external_id: str = WA) -> Any:
    return (await db.execute(text("""SELECT p.id, p.display_name, p.attrs, pi.handle FROM core.persons p
                                     JOIN core.person_identities pi ON pi.person_id = p.id
                                     WHERE pi.external_id = :x"""), {"x": external_id})).one()


async def _say(org: Any, name: str | None, body: str = "Cho mình xin báo giá thép", sender: str = WA) -> None:
    await put(sessionmaker(), org, msg(body, channel="whatsapp", group="120363@g.us", sender=sender, name=name or ""))


async def test_directory_before_message_name_upgrades_from_raw_id(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _directory(db, redis, org)  # thành viên chưa có tên ⇒ hồ sơ mang mã tài khoản
    p = await _person(db)
    assert p.display_name == WA and "name_source" not in p.attrs
    await _say(org, "Anh Tùng")
    p = await _person(db)
    assert p.display_name == "Anh Tùng" and p.handle == "Anh Tùng" and p.attrs["name_source"] == "channel"
    assert p.attrs["auto"] is True  # các cờ khác của hồ sơ giữ nguyên
    people = (await owner_api.get("/directory/people")).json()["items"]
    assert [x["name"] for x in people] == ["Anh Tùng"]


async def test_message_with_name_creates_profile_with_that_name(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _directory(db, redis, org, name="Anh Tùng")
    await db.commit()
    p = await _person(db)
    assert p.display_name == "Anh Tùng" and "name_source" not in p.attrs


async def test_directory_name_after_message_without_name_upgrades_too(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _directory(db, redis, org)
    await _say(org, None)          # tin không kèm tên ⇒ tên vẫn là mã
    assert (await _person(db)).display_name == WA
    await _directory(db, redis, org, name="Chị Hoa")  # lần đồng bộ danh bạ sau đã có tên
    assert (await _person(db)).display_name == "Chị Hoa"


async def test_owner_edited_name_is_never_overwritten(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _directory(db, redis, org)
    # Sếp sửa tên (dù tên sửa lại trùng mã thô thì cờ 'owner' vẫn thắng)
    await db.execute(text("""UPDATE core.persons SET attrs = attrs || '{"name_source": "owner"}'::jsonb"""))
    await db.commit()
    await _say(org, "Anh Tùng")
    assert (await _person(db)).display_name == WA
    await db.execute(text("UPDATE core.persons SET display_name = 'Tùng - bạn thân'"))
    await db.commit()
    await _say(org, "Anh Tùng khác")
    assert (await _person(db)).display_name == "Tùng - bạn thân"


async def test_good_name_is_not_churned_and_empty_name_never_wipes(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _directory(db, redis, org)
    await _say(org, "Anh Tùng")
    await _say(org, "Tùng Nguyễn")     # đổi tên hiển thị trên kênh ⇒ không đổi theo (tên đã tốt)
    await _say(org, "   ")             # tên toàn khoảng trắng
    await _say(org, None)
    p = await _person(db)
    assert p.display_name == "Anh Tùng"
    assert p.handle == "Tùng Nguyễn"   # handle vẫn là tên mới nhất của tài khoản (hành vi cũ)


async def test_name_equal_to_raw_id_is_not_an_upgrade(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _directory(db, redis, org)
    await _say(org, WA)
    p = await _person(db)
    assert p.display_name == WA and "name_source" not in p.attrs


async def test_legacy_profile_without_marker_is_upgraded(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Hồ sơ tạo trước bản này không có `name_source`: tên đang là mã thô vẫn được nâng."""
    org = await org_id(db)
    await _directory(db, redis, org)
    await db.execute(text("UPDATE core.persons SET attrs = attrs - 'name_source'"))
    await db.commit()
    await _say(org, "Anh Tùng")
    assert (await _person(db)).display_name == "Anh Tùng"


async def test_upgrade_touches_profile_so_detect_rescans_it(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _directory(db, redis, org)
    before = (await db.execute(text("SELECT updated_at FROM core.persons"))).scalar_one()
    await _say(org, "Anh Tùng")
    db.expire_all()
    after = (await db.execute(text("SELECT updated_at FROM core.persons"))).scalar_one()
    assert after > before


def test_only_known_places_write_person_display_name() -> None:
    """Lưới nhắc việc: chốt "không đè tên Sếp sửa" dựa vào `attrs.name_source = 'owner'` mà CHƯA có route nào ghi nó.
    Ai thêm một UPDATE ghi `core.persons.display_name` ở file khác (vd. route đổi tên người) phải đặt
    `name_source = 'owner'` cùng lúc rồi thêm file đó vào danh sách này — nếu quên, tên Sếp đặt kiểu số/email sẽ bị
    tin nhắn sau đè mất."""
    import re
    from pathlib import Path

    import gh

    allowed = {"data/ingest.py",              # nâng tên thô → tên thật (có chốt name_source)
               "system_api/routes.py"}        # xoá dữ liệu cá nhân theo yêu cầu (đặt tên "Người dùng đã xoá")
    pat = re.compile(r"UPDATE\s+core\.persons\b[^;\"]*?\bdisplay_name\s*=", re.S | re.I)
    root = Path(gh.__file__).parent
    found = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.py")
             if pat.search(p.read_text(encoding="utf-8"))}
    assert found == allowed, f"có nơi mới ghi core.persons.display_name: {sorted(found - allowed)}"
