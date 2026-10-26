# ruff: noqa: E501
"""v0.1.76 (gói T14, Ryan chốt D3 2026-10-25 "hỏi lúc cài") — "Thiết lập Tình báo": `GET /intel/setup` báo nhóm chưa gán bối cảnh + đã khai
nhân viên chưa; `POST /intel/setup/staff-confirmed` ghi "đã khai xong". Chỉ Owner; tổ chức khác không lộ; Nhật ký không ghi tên người.
Xong hai bước ⇒ đường khởi động lạnh T12/T13 thấy đúng nhóm kinh doanh đã xác nhận + nhân viên đã đánh dấu."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel.detectors import generic as g
from tests.conftest import Api, verify_pin
from tests.test_intel_routes_security_v0176 import _org
from tests.test_rbac_api import login_as


async def _group(db: AsyncSession, org: uuid.UUID, name: str, size: int | None = 12) -> uuid.UUID:
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', :n) RETURNING id"), {"o": org, "n": uuid.uuid4().hex[:8]})).scalar_one()
    return (await db.execute(text("""INSERT INTO core.groups (org_id, channel_id, code, name, external_id, member_count)
                                     VALUES (:o, :c, :cd, :n, :e, :m) RETURNING id"""),
                             {"o": org, "c": ch, "cd": uuid.uuid4().hex[:8], "n": name, "e": uuid.uuid4().hex, "m": size})).scalar_one()  # type: ignore[no-any-return]


async def _person(db: AsyncSession, org: uuid.UUID, name: str, group: uuid.UUID | None, ptype: str = "unknown") -> uuid.UUID:
    pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name, person_type) VALUES (:o, core.next_code('PER'), :n, :t)
                                    RETURNING id"""), {"o": org, "n": name, "t": ptype})).scalar_one()
    if group is not None:
        await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": group, "p": pid})
    return pid  # type: ignore[no-any-return]


@pytest.mark.parametrize(("method", "url"), [("GET", "/intel/setup"), ("POST", "/intel/setup/staff-confirmed")])
async def test_setup_routes_owner_only(owner_api: Api, client: httpx.AsyncClient, db: AsyncSession, method: str, url: str) -> None:
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    r = await (mgr.get(url) if method == "GET" else mgr.send(method, url, {}))
    assert r.status_code == 403, r.text


async def test_setup_two_steps_then_ready_and_cold_start_sees_labels_and_staff(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    shop = await _group(db, org, "Khách sỉ Nhà Xinh", 40)
    team = await _group(db, org, "Nhân viên cửa hàng", 6)
    other_org = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('Khác') RETURNING id"))).scalar_one()
    foreign = await _group(db, other_org, "Nhóm tổ chức khác")
    await _person(db, other_org, "Người ngoài tổ chức", foreign)
    ngoc = await _person(db, org, "Ngọc bán hàng", team)
    await _person(db, org, "Chú Bảy khách sỉ", shop)
    await _person(db, org, "Không ở nhóm nào", None)
    await db.commit()

    s = (await owner_api.get("/intel/setup")).json()
    assert s["ready"] is False and s["groups"]["done"] is False and s["staff"]["done"] is False
    assert {x["name"] for x in s["groups"]["unlabeled"]} == {"Khách sỉ Nhà Xinh", "Nhân viên cửa hàng"} and s["groups"]["unlabeled_count"] == 2
    names = {p["name"] for p in s["staff"]["people"]}
    assert {"Ngọc bán hàng", "Chú Bảy khách sỉ"} <= names and "Người ngoài tổ chức" not in names and "Không ở nhóm nào" not in names
    assert "Nhóm tổ chức khác" not in str(s) and s["gated_kinds"] and "kinh_doanh" in s["contexts"]

    # Bước 1: gán nhãn bằng API hàng loạt sẵn có
    assert (await owner_api.send("POST", "/intel/group-contexts/bulk", {"group_ids": [str(shop)], "labels": [{"context": "kinh_doanh", "is_primary": True}]})).status_code == 200
    assert (await owner_api.send("POST", "/intel/group-contexts/bulk", {"group_ids": [str(team)], "labels": [{"context": "nhan_vien", "is_primary": True}]})).status_code == 200
    s = (await owner_api.get("/intel/setup")).json()
    assert s["groups"]["done"] is True and s["groups"]["unlabeled"] == [] and s["ready"] is False

    # Bước 2: đánh dấu nhân viên bằng đường "Đánh dấu" sẵn có (Owner + PIN), rồi xác nhận đã khai xong
    await verify_pin(owner_api)
    assert (await owner_api.send("PATCH", f"/profile/{ngoc}", {"person_type": "staff"})).status_code == 200
    s = (await owner_api.get("/intel/setup")).json()
    assert s["staff"]["staff_count"] == 1 and s["staff"]["done"] is False
    assert {p["name"]: p["person_type"] for p in s["staff"]["people"]}["Ngọc bán hàng"] == "staff"
    r = await owner_api.send("POST", "/intel/setup/staff-confirmed", {})
    assert r.status_code == 200 and r.json()["staff_count"] == 1
    s = (await owner_api.get("/intel/setup")).json()
    assert s["ready"] is True and s["staff"]["done"] is True and s["staff"]["confirmed_at"]

    log = (await db.execute(text("SELECT detail, target_label FROM ops.action_log WHERE org_id = :o AND action = 'intel.setup.staff_confirmed'"), {"o": org})).one()
    assert log.detail == {"staff_count": 1} and "Ngọc" not in str(log.detail) and log.target_label is None

    # Khởi động lạnh (T12/T13) dùng đúng hai bước đó
    assert await g.business_group(db, org, shop) is True and await g.business_group(db, org, team) is False
    sets = await g.org_sets(db, org)
    assert g.hard_owner_side(await g.who_person(db, org, ngoc), sets)


async def test_setup_no_groups_staff_confirmed_without_staff_is_ready(owner_api: Api, db: AsyncSession) -> None:
    """Cửa hàng một mình, chưa có nhóm: khai "không có nhân viên" vẫn là xong."""
    org = await _org(db)
    await db.execute(text("DELETE FROM mem.group_contexts WHERE org_id = :o"), {"o": org})
    n = (await db.execute(text("SELECT count(*) FROM core.groups WHERE org_id = :o"), {"o": org})).scalar_one()
    s = (await owner_api.get("/intel/setup")).json()
    assert s["groups"]["total"] == n and s["groups"]["done"] is (n == 0)
    assert (await owner_api.send("POST", "/intel/setup/staff-confirmed", {})).json()["staff_count"] == 0
    assert (await owner_api.get("/intel/setup")).json()["staff"]["done"] is True


# ─── review T14: SECURITY + D3-a + D3-b ───────────────────────────────────────

async def _backup_security(db: AsyncSession, org: uuid.UUID) -> int:
    return int((await db.execute(text("SELECT count(*) FROM ops.backup_outbox WHERE org_id = :o AND kind = 'security'"), {"o": org})).scalar_one())


async def test_staff_mark_only_owner_with_pin_and_notifies_backup(owner_api: Api, client: httpx.AsyncClient, db: AsyncSession) -> None:
    """Đổi loại người sang / khỏi `staff` (tắt "người lạ" của luật lừa đảo, tính tin của họ là phía Sếp) = hạ phòng thủ: chỉ Owner + PIN,
    báo kênh phụ. Loại khác giữ quyền cũ (Vận hành vẫn sửa được)."""
    org = await _org(db)
    g1 = await _group(db, org, "Khách sỉ", 30)
    cust = await _person(db, org, "Chú Bảy", g1, "customer")
    await db.commit()
    op = await login_as(client, db, "operator")
    await verify_pin(op, "112233")
    assert (await op.send("PATCH", f"/profile/{cust}", {"person_type": "staff"})).status_code == 403
    assert (await op.send("PATCH", f"/profile/{cust}", {"person_type": "partner"})).status_code == 200      # loại khác: quyền cũ
    assert (await owner_api.send("PATCH", f"/profile/{cust}", {"person_type": "staff"})).status_code == 423   # Owner chưa mở PIN
    before = await _backup_security(db, org)
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", f"/profile/{cust}", {"person_type": "staff"})
    assert r.status_code == 200, r.text
    assert await _backup_security(db, org) == before + 1
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'strong_confirm.console'"))).scalar_one() >= 1
    assert (await op.send("PATCH", f"/profile/{cust}", {"person_type": "customer"})).status_code == 403     # khỏi staff: cũng chỉ Owner


async def test_unmark_staff_restores_previous_type_and_relation(owner_api: Api, db: AsyncSession) -> None:
    """Review D3-a: bật nhân viên rồi tắt ⇒ về đúng loại cũ (khách) + quan hệ cũ; GET /intel/setup trả `prev_type` để thẻ gửi đúng."""
    org = await _org(db)
    g1 = await _group(db, org, "Khách sỉ", 30)
    cust = await _person(db, org, "Chị Mai", g1, "customer")
    await db.execute(text("UPDATE core.persons SET relation_to_owner = 'direct' WHERE id = :p"), {"p": cust})
    await db.commit()
    await verify_pin(owner_api)
    assert (await owner_api.send("PATCH", f"/profile/{cust}", {"person_type": "staff", "relation_to_owner": "staff"})).status_code == 200
    s = (await owner_api.get("/intel/setup")).json()
    mai = next(p for p in s["staff"]["people"] if p["name"] == "Chị Mai")
    assert mai["person_type"] == "staff" and mai["prev_type"] == "customer"
    assert (await owner_api.send("PATCH", f"/profile/{cust}", {"person_type": "customer"})).status_code == 200
    r = (await db.execute(text("SELECT person_type, relation_to_owner, attrs FROM core.persons WHERE id = :p"), {"p": cust})).one()
    assert (r.person_type, r.relation_to_owner) == ("customer", "direct")
    # bật rồi tắt một người CHƯA phân loại, chưa khoá ⇒ về đúng trạng thái cũ: không khoá (quy tắc sàng lọc vẫn phân loại được)
    other = await _person(db, org, "Người thử", g1)
    await db.commit()
    await owner_api.send("PATCH", f"/profile/{other}", {"person_type": "staff"})
    assert (await owner_api.send("PATCH", f"/profile/{other}", {"person_type": "unknown"})).status_code == 200
    a = (await db.execute(text("SELECT person_type, relation_to_owner, attrs FROM core.persons WHERE id = :p"), {"p": other})).one()
    assert a.person_type == "unknown" and a.attrs.get("person_type_locked") is not True and a.relation_to_owner is None


async def test_setup_unlabeled_count_not_capped_and_left_groups_ignored(owner_api: Api, db: AsyncSession) -> None:
    """Review D3-b: đếm nhóm chưa gán không bị trần 300 dòng; nhóm Sếp đã rời không tính."""
    org = await _org(db)
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'nhieu') RETURNING id"), {"o": org})).scalar_one()
    await db.execute(text("""INSERT INTO core.groups (org_id, channel_id, code, name, external_id)
                             SELECT :o, :c, 'GX' || n, 'Nhóm ' || lpad(n::text, 3, '0'), 'x' || n FROM generate_series(1, 305) n"""), {"o": org, "c": ch})
    left = await _group(db, org, "Nhóm đã rời", 9)
    await db.execute(text("UPDATE core.groups SET left_at = now() WHERE id = :g"), {"g": left})
    await db.commit()
    n_active = (await db.execute(text("""SELECT count(*) FROM core.groups g WHERE g.org_id = :o AND g.left_at IS NULL AND NOT EXISTS
                                         (SELECT 1 FROM mem.group_contexts c WHERE c.group_id = g.id AND c.source = 'owner')"""), {"o": org})).scalar_one()
    s = (await owner_api.get("/intel/setup")).json()
    assert n_active >= 305 and s["groups"]["unlabeled_count"] == n_active and s["groups"]["done"] is False
    assert "Nhóm đã rời" not in {g["name"] for g in s["groups"]["unlabeled"]}
