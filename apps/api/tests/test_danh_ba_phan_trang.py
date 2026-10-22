"""Khách & Nhóm (F053): con trỏ phân trang (created_at, id) không rơi dòng cùng thời điểm tạo, tìm tên + lọc kênh
phía máy chủ, thao tác BOT hàng loạt theo bộ lọc (áp cho MỌI người khớp, không chỉ trang đã tải), và hồ sơ đã gộp
trả chuyển hướng tới hồ sơ gốc (phần hiển thị của F054)."""

import uuid
from typing import Any

from sqlalchemy import text

from tests.conftest import Api, verify_pin
from tests.phase2 import org_id
from tests.test_rbac_api import login_as


async def _channel(db, org, kind: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""INSERT INTO core.channels (org_id, type, name) VALUES (:o, :t, :n)
                                     ON CONFLICT (org_id, type, name) DO UPDATE SET name = excluded.name
                                     RETURNING id"""),
                             {"o": org, "t": kind, "n": kind.capitalize()})).scalar_one()  # type: ignore[no-any-return]


async def _persons(db, org, n: int, *, prefix: str = "Khách", channel: uuid.UUID | None = None,
                   start: int = 0) -> list[uuid.UUID]:  # type: ignore[no-untyped-def]
    """`n` người tạo trong CÙNG một giao dịch ⇒ created_at trùng hệt nhau (ca làm con trỏ cũ rơi dòng)."""
    ids = (await db.execute(text("""
        INSERT INTO core.persons (org_id, code, display_name, person_type, relation_to_owner)
        SELECT :o, 'PT-' || lpad((:s + g)::text, 5, '0'), :p || ' ' || (:s + g), 'customer', 'direct'
        FROM generate_series(1, :n) g RETURNING id"""), {"o": org, "p": prefix, "n": n, "s": start})).scalars().all()
    if channel is not None:
        await db.execute(text("""INSERT INTO core.person_identities (person_id, channel_id, external_id)
                                 SELECT x, :c, 'ext-' || x::text FROM unnest(CAST(:ids AS uuid[])) x"""),
                         {"c": channel, "ids": list(ids)})
    return list(ids)


async def _groups(db, org, channel: uuid.UUID, n: int, *, prefix: str = "Nhóm", start: int = 0) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""
        INSERT INTO core.groups (org_id, code, channel_id, external_id, name, kind, listen_mode)
        SELECT :o, 'GT-' || :c || '-' || lpad((:s + g)::text, 5, '0'), :ch, 'x-' || :c || '-' || (:s + g),
               :p || ' ' || (:s + g), 'customer', 'silent'
        FROM generate_series(1, :n) g"""),
        {"o": org, "ch": channel, "n": n, "p": prefix, "s": start, "c": channel.hex[-8:]})


async def _walk(api: Api, url: str, limit: int = 50) -> tuple[list[dict[str, Any]], int]:
    """Đi hết các trang bằng next_cursor; trả (mọi dòng, tổng do máy chủ báo)."""
    sep = "&" if "?" in url else "?"
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    total = -1
    for _ in range(40):
        u = f"{url}{sep}limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        r = await api.get(u)
        assert r.status_code == 200, r.text
        body = r.json()
        items += body["items"]
        total = body["total"]
        cursor = body["next_cursor"]
        if not cursor:
            return items, total
    raise AssertionError("phân trang không dừng")


async def _agent(db, org) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""INSERT INTO agent.identities (org_id, name, role_desc, template,
        addressing, voice, speak_when, forbidden, autonomy_level, is_enabled)
        VALUES (:o, 'Trợ lý hàng loạt', 'Báo giá', 'commercial', '{}', 'lễ phép', 'khi được tag', '{}', 3, true)
        RETURNING id"""), {"o": org})).scalar_one()  # type: ignore[no-any-return]


# ═══ Con trỏ phân trang ═════════════════════════════════════════════════════

async def test_people_pagination_does_not_drop_rows_with_same_created_at(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _persons(db, org, 120)
    await db.commit()
    same = (await db.execute(text("SELECT count(DISTINCT created_at) FROM core.persons"))).scalar_one()
    assert same == 1                                          # đúng ca lỗi: 120 người, một created_at

    items, total = await _walk(owner_api, "/directory/people")
    assert total == 120
    assert len(items) == 120 and len({i["id"] for i in items}) == 120    # không rơi, không lặp


async def test_groups_pagination_does_not_drop_rows_with_same_created_at(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    zalo = await _channel(db, org, "zalo")
    await _groups(db, org, zalo, 130)
    await db.commit()
    items, total = await _walk(owner_api, "/directory/groups")
    assert total == 130
    assert len(items) == 130 and len({i["id"] for i in items}) == 130


async def test_cursor_legacy_and_invalid(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _persons(db, org, 3)
    await db.commit()
    first = (await owner_api.get("/directory/people?limit=1")).json()
    cur = first["next_cursor"]
    assert cur and "|" in cur                                  # (created_at, id)
    # con trỏ kiểu cũ (chỉ thời điểm) vẫn dùng được — không 500
    legacy = await owner_api.get(f"/directory/people?cursor={cur.split('|')[0]}")
    assert legacy.status_code == 200, legacy.text
    bad = await owner_api.get("/directory/people?cursor=khong-phai-con-tro")
    assert bad.status_code == 422, bad.text
    bad2 = await owner_api.get("/directory/groups?cursor=2026-01-01T00:00:00Z|khong-phai-uuid")
    assert bad2.status_code == 422, bad2.text


# ═══ Tìm tên + lọc kênh ═════════════════════════════════════════════════════

async def test_people_search_by_name_or_code_and_channel(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    zalo, wa = await _channel(db, org, "zalo"), await _channel(db, org, "whatsapp")
    await _persons(db, org, 60, prefix="Khách Zalo", channel=zalo)
    await _persons(db, org, 3, prefix="Đối tác Nam", channel=wa, start=1000)
    await _persons(db, org, 1, prefix="100% Thép_Việt", start=2000)
    await db.commit()

    # người thứ 61 trở đi tìm được: tên nằm ngoài trang đầu
    r = (await owner_api.get("/directory/people?q=Đối tác nam")).json()
    assert r["total"] == 3 and len(r["items"]) == 3
    # tìm theo mã
    r = (await owner_api.get("/directory/people?q=PT-01003")).json()
    assert [i["code"] for i in r["items"]] == ["PT-01003"]
    # ký tự đặc biệt của LIKE được hiểu theo nghĩa đen
    r = (await owner_api.get("/directory/people?q=100%25 Thép_")).json()
    assert r["total"] == 1
    r = (await owner_api.get("/directory/people?q=%25")).json()
    assert r["total"] == 1                                     # chỉ dòng chứa đúng dấu %
    # lọc theo kênh (loại kênh) phía máy chủ; tổng đúng, phân trang đủ
    items, total = await _walk(owner_api, "/directory/people?channel=zalo")
    assert total == 60 and len(items) == 60
    r = (await owner_api.get("/directory/people?channel=whatsapp&q=Nam")).json()
    assert r["total"] == 3
    r = (await owner_api.get("/directory/people?channel=telegram")).json()
    assert r["total"] == 0


async def test_groups_search_by_name_and_channel_filter(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    zalo, wa = await _channel(db, org, "zalo"), await _channel(db, org, "whatsapp")
    await _groups(db, org, zalo, 70, prefix="Zalo đại lý")
    await _groups(db, org, wa, 2, prefix="WhatsApp xuất khẩu")
    await db.commit()
    r = (await owner_api.get(f"/directory/groups?channel_id={wa}")).json()
    assert r["total"] == 2 and {i["channel"]["type"] for i in r["items"]} == {"whatsapp"}
    r = (await owner_api.get("/directory/groups?q=xuất khẩu")).json()
    assert r["total"] == 2
    items, total = await _walk(owner_api, f"/directory/groups?channel_id={zalo}&q=đại lý")
    assert total == 70 and len(items) == 70


async def test_people_search_cannot_probe_masked_digits_for_non_owner(owner_api: Api, db, client) -> None:  # type: ignore[no-untyped-def]
    """Tên có dãy số dài bị che với vai dưới Owner — tìm theo dãy số đó không được thành "oracle" dò số."""
    org = await org_id(db)
    await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name, person_type, relation_to_owner)
                             VALUES (:o, 'PT-SO1', 'Khách 0912345678', 'customer', 'direct')"""), {"o": org})
    await db.commit()
    operator = await login_as(client, db, "operator")
    hidden = (await operator.get("/directory/people?q=09123")).json()
    assert hidden["total"] == 0
    shown = (await owner_api.get("/directory/people?q=09123")).json()
    assert shown["total"] == 1                                 # Owner thấy đủ
    by_code = (await operator.get("/directory/people?q=PT-SO1")).json()
    assert by_code["total"] == 1 and "0912345678" not in by_code["items"][0]["name"]
    # Cửa sổ 1–3 chữ số cũng không được dò: mask_text để lộ 3 số cuối, nên chỉ cần dò từng cửa sổ 3 số là dựng lại được.
    for window in ("678", "912", "5", "345", "0"):
        probe = (await operator.get(f"/directory/people?q={window}")).json()
        assert probe["total"] == 0, window
        owner_probe = (await owner_api.get(f"/directory/people?q={window}")).json()
        assert owner_probe["total"] >= 1, window                 # Owner vẫn tìm theo tên có số
    # Tên không có chữ số vẫn tìm được với vai dưới Owner
    plain = (await operator.get("/directory/people?q=Khách")).json()
    assert plain["total"] >= 1


# ═══ Thao tác BOT hàng loạt theo bộ lọc ═════════════════════════════════════

async def test_bulk_bot_applies_to_every_match_not_only_first_page(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)   # review v0.1.65 P2-3: gán BOT cho người cần Owner + PIN policy.change
    org = await org_id(db)
    zalo = await _channel(db, org, "zalo")
    ids = await _persons(db, org, 130, prefix="Khách", channel=zalo)
    others = await _persons(db, org, 5, prefix="Người ngoài", start=5000)
    agent = await _agent(db, org)
    await db.commit()

    r = await owner_api.send("POST", "/directory/people/bulk-bot",
                             {"filter": {"channel": "zalo"}, "agent_id": str(agent), "autonomy_level": 2,
                              "expected_total": 130})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "updated": 130}
    rows = (await db.execute(text("SELECT id, attrs FROM core.persons"))).all()
    by_id = {x.id: x.attrs for x in rows}
    assert all(by_id[i].get("agent_id") == str(agent) and by_id[i].get("autonomy_level") == 2 for i in ids)
    assert all("agent_id" not in by_id[i] for i in others)     # ngoài bộ lọc ⇒ không đổi
    log = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'directory.bulk_bot_set'"))
           ).scalars().all()
    assert len(log) == 1 and log[0]["updated"] == 130          # một dòng nhật ký, ghi đúng số

    # chỉ đổi tự trị, giữ nguyên BOT; gỡ BOT bằng agent_id = null
    r = await owner_api.send("POST", "/directory/people/bulk-bot",
                             {"filter": {"channel": "zalo"}, "autonomy_level": 4})
    assert r.status_code == 200 and r.json()["updated"] == 130
    one = (await db.execute(text("SELECT attrs FROM core.persons WHERE id = :i"), {"i": ids[0]})).scalar_one()
    assert one["agent_id"] == str(agent) and one["autonomy_level"] == 4
    r = await owner_api.send("POST", "/directory/people/bulk-bot", {"filter": {"q": "Khách 7"}, "agent_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 11                           # Khách 7, 70..79
    gone = (await db.execute(text("SELECT attrs FROM core.persons WHERE display_name = 'Khách 7'"))).scalar_one()
    assert "agent_id" not in gone and gone["autonomy_level"] == 4


async def test_bulk_bot_guards(owner_api: Api, db, client) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)
    org = await org_id(db)
    ids = await _persons(db, org, 4)
    agent = await _agent(db, org)
    await db.commit()

    # số hiển thị đã cũ (có người mới thêm vào) ⇒ 409, KHÔNG đổi gì
    r = await owner_api.send("POST", "/directory/people/bulk-bot",
                             {"filter": {}, "agent_id": str(agent), "expected_total": 3})
    assert r.status_code == 409, r.text
    assert (await db.execute(text("SELECT count(*) FROM core.persons WHERE attrs ? 'agent_id'"))).scalar_one() == 0
    # không nói đổi gì ⇒ 422
    r = await owner_api.send("POST", "/directory/people/bulk-bot", {"filter": {}})
    assert r.status_code == 422, r.text
    # BOT lạ ⇒ 404 (không 500)
    r = await owner_api.send("POST", "/directory/people/bulk-bot", {"filter": {}, "agent_id": str(uuid.uuid4())})
    assert r.status_code == 404, r.text
    # vai chỉ đọc ⇒ 403
    auditor = await login_as(client, db, "auditor")
    r = await auditor.send("POST", "/directory/people/bulk-bot", {"filter": {}, "agent_id": str(agent)})
    assert r.status_code == 403, r.text
    # review v0.1.65 P2-3: vai dưới Owner (kể cả nhân viên có người được giao) không gán BOT cho người, dù có PIN
    staff = await login_as(client, db, "agent_staff")
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             SELECT org_id, id, 'person', :s FROM core.users WHERE email = 'agent_staff@example.vn'"""),
                     {"s": ids[0]})
    await db.commit()
    await verify_pin(staff, "112233")
    r = await staff.send("POST", "/directory/people/bulk-bot", {"filter": {}, "agent_id": str(agent)})
    assert r.status_code == 403, r.text
    assert (await db.execute(text("SELECT count(*) FROM core.persons WHERE attrs ? 'agent_id'"))).scalar_one() == 0


# ═══ Hồ sơ đã gộp ═══════════════════════════════════════════════════════════

async def test_profile_of_merged_person_redirects_to_merged_into(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    keep, old = await _persons(db, org, 2)
    await db.execute(text("UPDATE core.persons SET merged_into_id = :k WHERE id = :o"), {"k": keep, "o": old})
    await db.commit()
    r = await owner_api.get(f"/profile/{old}")
    assert r.status_code == 307, r.text
    assert r.headers["location"].endswith(f"/profile/{keep}")
    ok = await owner_api.get(f"/profile/{keep}")
    assert ok.status_code == 200 and ok.json()["person"]["id"] == str(keep)
