# ruff: noqa: E501
"""fix-gkb3 — phạm vi hiện của sự thật trong gói truy hồi (lỗi riêng tư G-KB kb4).

Lỗi: `retrieve._facts` lấy sự thật theo CHỦ THỂ (người gửi + mention), không lọc nhóm nguồn. Chủ thể hay gặp nhất là Sếp (người gửi
ở mọi nhóm) ⇒ lời hứa mua giày cho con (nhóm gia đình), lương nhân viên (nhóm nhân viên) hiện trong prompt nhóm giao thương ~120 người lạ.
Quy tắc (mặc định an toàn, spec §4A.2 không nói phạm vi): hiện ở luồng nguồn của bằng chứng; luồng khác chỉ khi cùng bối cảnh đã xác
nhận, sensitivity normal, nhóm không đông người. Xem docstring `gh.biz.mem.retrieve`.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import contexts, entities, facts, retrieve
from tests.mem_helpers import mk_entity, mk_org, mk_person_row, mkfact


async def _user(db, org):  # type: ignore[no-untyped-def]
    uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                    VALUES (:o, :e, 'Sếp', 'x') RETURNING id"""), {"o": org, "e": f"{uuid.uuid4().hex[:6]}@x.vn"})).scalar_one()
    await db.commit()
    return uid


async def _group(db, org, name, ctx, user, members=None):  # type: ignore[no-untyped-def]
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one_or_none()
    if ch is None:
        ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"),
                               {"o": org})).scalar_one()
    gid = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name, member_count)
                                    VALUES (:o, core.next_code('GRP'), :c, :x, :n, :m) RETURNING id"""),
                            {"o": org, "c": ch, "x": uuid.uuid4().hex, "n": name, "m": members})).scalar_one()
    await db.commit()
    if ctx is not None:
        await contexts.set_group_contexts(db, org, gid, [{"context": ctx, "is_primary": True}], user_id=user)
        await db.commit()
    return gid


async def _raw(db, org, group):  # type: ignore[no-untyped-def]
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one()
    m = f"m-{uuid.uuid4().hex}"
    rid = (await db.execute(text("""
        INSERT INTO raw.events (org_id, occurred_at, channel_id, group_id, external_msg_id, kind, body_text, payload, content_hash)
        VALUES (:o, now() - interval '1 day', :c, :g, :m, 'text', 'tin', '{}'::jsonb, decode(md5(:m), 'hex')) RETURNING id"""),
                             {"o": org, "c": ch, "g": group, "m": m})).scalar_one()
    await db.commit()
    return rid


class World:
    pass


async def _world(db):  # type: ignore[no-untyped-def]
    w = World()
    w.org = await mk_org(db)
    w.user = await _user(db, w.org)
    w.gia_dinh = await _group(db, w.org, "Gia đình nhỏ", "gia_dinh", w.user, members=10)
    w.dai_gia_dinh = await _group(db, w.org, "Đại gia đình", "gia_dinh", w.user, members=10)
    w.giao_thuong = await _group(db, w.org, "Hội doanh nhân – Giao thương", "giao_thuong", w.user, members=10)
    w.cong_viec = await _group(db, w.org, "Team Backend", "cong_viec", w.user, members=10)
    w.hoc_tap = await _group(db, w.org, "Lớp MBA", "hoc_tap", w.user, members=10)
    w.nhan_vien = await _group(db, w.org, "Nhân viên quán", "nhan_vien", w.user, members=10)
    w.kinh_doanh = await _group(db, w.org, "Khách sỉ", "kinh_doanh", w.user, members=10)
    w.kinh_doanh2 = await _group(db, w.org, "Khách VIP", "kinh_doanh", w.user, members=10)
    w.p_minh = await mk_person_row(db, w.org, "Minh")
    w.minh = (await entities.ensure_person(db, w.org, w.p_minh)).id
    w.p_an = await mk_person_row(db, w.org, "An")
    w.an = (await entities.ensure_person(db, w.org, w.p_an)).id
    w.now = datetime.now(UTC)
    await db.commit()
    return w


async def _pack(db, w, group, *senders):  # type: ignore[no-untyped-def]
    ev = [{"raw_event_id": uuid.uuid4(), "sender_person_id": p, "text": "tin mới"} for p in (senders or (w.p_minh,))]
    return await retrieve.pack(db, w.org, f"g:{group}", ev, now=w.now)


def _ids(pack):  # type: ignore[no-untyped-def]
    return {i.fact_id for i in pack.facts}


async def _fact(db, w, subject, value, group, predicate="promised_to", **kw):  # type: ignore[no-untyped-def]
    f = await facts.assert_fact(db, mkfact(w.org, subject, value, w.now - timedelta(days=1), predicate=predicate,
                                           raw=await _raw(db, w.org, group), **kw))
    await db.commit()
    return f


async def test_fixgkb3_family_fact_of_owner_never_reaches_business_work_school_groups(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    shoes = await _fact(db, w, w.minh, "mua giày đá banh size 39 cho Bin", w.gia_dinh)
    for g in (w.giao_thuong, w.cong_viec, w.hoc_tap, w.nhan_vien, w.kinh_doanh):
        assert shoes.id not in _ids(await _pack(db, w, g)), g
    assert shoes.id in _ids(await _pack(db, w, w.gia_dinh))                 # nhóm nguồn vẫn thấy


async def test_fixgkb3_staff_salary_and_friend_birthday_not_in_trade_group(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    salary = await _fact(db, w, w.minh, "lương tháng 9 cho nhân viên", w.nhan_vien, predicate="owes")
    w.ban_be = await _group(db, w.org, "Bạn thân", "ban_be", w.user, members=6)
    bday = await _fact(db, w, w.minh, "tới dự sinh nhật tối thứ Sáu", w.ban_be)
    quote = await _fact(db, w, w.minh, "gửi báo giá cold brew cuối tuần", w.giao_thuong)
    got = _ids(await _pack(db, w, w.giao_thuong))
    assert salary.id not in got and bday.id not in got
    assert quote.id in got                                                    # việc của chính nhóm giao thương vẫn hiện
    rendered = (await _pack(db, w, w.giao_thuong)).render_for_prompt()
    assert "lương" not in rendered and "sinh nhật" not in rendered


async def test_fixgkb3_owner_promise_to_customer_stays_in_that_business_group(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    keep = await _fact(db, w, w.minh, "giữ giá cũ 320k cho An nếu chốt trước 30/9", w.kinh_doanh)
    an_needs = await _fact(db, w, w.an, "50kg Blend Honey", w.kinh_doanh, predicate="needs")
    got = _ids(await _pack(db, w, w.kinh_doanh, w.p_an, w.p_minh))
    assert {keep.id, an_needs.id} <= got
    assert keep.id not in _ids(await _pack(db, w, w.gia_dinh))
    assert keep.id not in _ids(await _pack(db, w, w.giao_thuong))
    # quy tắc tiền (Gen 25/10): giá riêng của khách nhóm này KHÔNG sang nhóm khách khác; lời hứa không dính tiền thì sang được
    assert keep.id not in _ids(await _pack(db, w, w.kinh_doanh2))
    sample = await _fact(db, w, w.minh, "gửi mẫu 3 loại hạt thứ 6", w.kinh_doanh)
    assert sample.id in _ids(await _pack(db, w, w.kinh_doanh2))
    # kinh_doanh → nhan_vien vẫn nhận cả lời hứa dính giá (nhóm nội bộ cần biết)
    assert keep.id in _ids(await _pack(db, w, w.nhan_vien))


async def test_fixgkb3_cross_group_needs_same_confirmed_context_normal_sensitivity_and_small_group(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    health = await _fact(db, w, w.minh, "đau lưng phải nghỉ 3 ngày", w.gia_dinh, predicate="health_event")
    shoes = await _fact(db, w, w.minh, "mua giày cho Bin", w.gia_dinh)
    # cùng gia_dinh: chuyện thường sang được, sức khoẻ (sensitive) thì không
    big_family = _ids(await _pack(db, w, w.dai_gia_dinh))
    assert shoes.id in big_family and health.id not in big_family
    assert health.id in _ids(await _pack(db, w, w.gia_dinh))                 # nhóm nguồn vẫn thấy
    # nhóm đông người (120) cùng bối cảnh giao_thuong: không nhận sự thật từ nhóm khác
    crowd = await _group(db, w.org, "Hội 120 người", "giao_thuong", w.user, members=120)
    offer = await _fact(db, w, w.minh, "bán sỉ cold brew 250 chai/tuần", w.giao_thuong, predicate="offers")
    assert offer.id not in _ids(await _pack(db, w, crowd))
    small = await _group(db, w.org, "Hội nhỏ 20 người", "giao_thuong", w.user, members=20)
    assert offer.id in _ids(await _pack(db, w, small))
    # nhóm nguồn chưa xác nhận bối cảnh ⇒ không xuyên nhóm; nhóm đích chưa xác nhận ⇒ không nhận gì từ nhóm khác
    unconf = await _group(db, w.org, "Nhóm chưa gán", None, w.user)
    loose = await _fact(db, w, w.minh, "hẹn cà phê thứ 3", unconf)
    assert loose.id not in _ids(await _pack(db, w, w.kinh_doanh))
    assert loose.id in _ids(await _pack(db, w, unconf)) and offer.id not in _ids(await _pack(db, w, unconf))


async def test_fixgkb3_owner_corrected_fact_keeps_source_thread(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    """Bản Sếp sửa chỉ có bằng chứng `owner_edit` (không có tin) — phạm vi lấy theo chuỗi `supersedes` về bằng chứng gốc."""
    w = await _world(db)
    f = await _fact(db, w, w.minh, "mua giày size 38", w.gia_dinh)
    fixed = await facts.correct(db, w.org, f.id, actor_user_id=w.user, new_value="mua giày size 39")
    await db.commit()
    assert fixed.id != f.id
    assert fixed.id in _ids(await _pack(db, w, w.gia_dinh))
    assert fixed.id not in _ids(await _pack(db, w, w.giao_thuong))


async def test_fixgkb3_related_block_filters_cross_context_edges_and_hot_edges(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    bin_ = await mk_entity(db, w.org, "Bin")
    edge = await facts.assert_fact(db, mkfact(w.org, bin_, None, w.now - timedelta(days=3), predicate="family_of", object_entity=w.minh,
                                              raw=await _raw(db, w.org, w.gia_dinh)))
    biz = await mk_entity(db, w.org, "Công ty Sao Mai", "org")
    job = await facts.assert_fact(db, mkfact(w.org, w.minh, None, w.now - timedelta(days=3), predicate="works_at", object_entity=biz,
                                             raw=await _raw(db, w.org, w.cong_viec)))
    await db.commit()
    rel_gt = {i.fact_id for i in (await _pack(db, w, w.giao_thuong)).related}
    assert edge.id not in rel_gt and job.id not in rel_gt
    assert edge.id not in {i.fact_id for i in (await _pack(db, w, w.dai_gia_dinh)).related}   # family_of = sensitive ⇒ không xuyên nhóm
    assert edge.id in {i.fact_id for i in (await _pack(db, w, w.gia_dinh)).related}
    assert job.id in {i.fact_id for i in (await _pack(db, w, w.cong_viec)).related}
    # cạnh nóng: chỉ người là thành viên nhóm hiện tại; không bao giờ tên nhóm khác
    p_vo = await mk_person_row(db, w.org, "Vợ")
    vo = await entities.ensure_person(db, w.org, p_vo)
    await db.execute(text("""INSERT INTO clean.relationships (org_id, from_type, from_id, to_type, to_id, kind, weight, state)
                             VALUES (:o, 'person', :a, 'person', :b, 'interacts', 9, 'hot'), (:o, 'person', :a, 'group', :g, 'interacts', 8, 'hot')"""),
                     {"o": w.org, "a": w.p_minh, "b": p_vo, "g": w.gia_dinh})
    await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": w.gia_dinh, "p": p_vo})
    await db.commit()
    gt = await _pack(db, w, w.giao_thuong)
    assert all(i.entity_id != vo.id for i in gt.related) and "Gia đình" not in gt.render_for_prompt()
    assert any(i.entity_id == vo.id for i in (await _pack(db, w, w.gia_dinh)).related)


async def test_fixgkb3_profile_and_history_blocks_stay_scoped(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    """Kiểm khối khác: Lịch sử chỉ lấy đơn vị cùng nhóm (đã lọc từ L1); Hồ sơ Sếp chỉ gồm vị từ owner_* đã xác nhận, không mang
    sự thật thường của nhóm nào."""
    w = await _world(db)
    await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, conclusion, confidence, run_id)
                             VALUES (:o, now() - interval '1 day', :g, :p, 'Promise', 'Minh hứa mua giày cho Bin', 0.9, core.uuid_v7())"""),
                     {"o": w.org, "g": w.gia_dinh, "p": w.p_minh})
    await _fact(db, w, w.minh, "mua giày cho Bin", w.gia_dinh)
    await db.commit()
    gt = await _pack(db, w, w.giao_thuong)
    assert gt.history == [] and gt.profile == [] and "giày" not in gt.render_for_prompt()
    assert any("giày" in i.text for i in (await _pack(db, w, w.gia_dinh)).history)


async def test_fixgkb3_evidence_from_dm_never_crosses_into_groups(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    rid = await _raw(db, w.org, None)
    f = await facts.assert_fact(db, mkfact(w.org, w.an, "nợ 20 triệu tiền hàng", w.now - timedelta(days=1), predicate="owes", raw=rid))
    await db.commit()
    assert f.id not in _ids(await _pack(db, w, w.kinh_doanh, w.p_an))


async def test_fixgkb3_customer_promise_flows_business_to_staff_group_but_never_back(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    """Gen quyết (25/10): cho phép kinh_doanh → nhan_vien (nhóm nội bộ cần biết lời hứa với khách người của Sếp đã nhận);
    KHÔNG cho chiều ngược (lương, nhân sự không sang nhóm khách). Vẫn cần sensitivity normal, nhóm ≤ 50, bối cảnh đã xác nhận."""
    w = await _world(db)
    p_vy = await mk_person_row(db, w.org, "Vy QL")
    vy = (await entities.ensure_person(db, w.org, p_vy)).id
    promise = await _fact(db, w, vy, "gửi báo giá hộp quà Tết cho khách trong tuần", w.kinh_doanh)
    salary = await _fact(db, w, vy, "lương tháng 9 chưa nhận", w.nhan_vien, predicate="owes")
    leave = await _fact(db, w, vy, "nói sẽ nghỉ việc cuối tháng", w.nhan_vien, predicate="said_will_leave")
    staff = _ids(await _pack(db, w, w.nhan_vien, p_vy))
    assert promise.id in staff                                               # chiều cho phép
    biz = _ids(await _pack(db, w, w.kinh_doanh, p_vy))
    assert salary.id not in biz and leave.id not in biz                      # chiều ngược: không
    # điều kiện vẫn giữ: nhóm nhân viên đông (> 50) hoặc chưa xác nhận ⇒ không nhận
    big_staff = await _group(db, w.org, "Nhân viên chuỗi 80 người", "nhan_vien", w.user, members=80)
    assert promise.id not in _ids(await _pack(db, w, big_staff, p_vy))
    hr_biz = await _fact(db, w, vy, "nói sẽ nghỉ việc nếu không tăng lương", w.kinh_doanh, predicate="said_will_leave")
    assert hr_biz.id not in _ids(await _pack(db, w, w.nhan_vien, p_vy))       # sensitivity hr ⇒ không xuyên nhóm
    # giao thương / gia đình không nhận lời hứa với khách
    assert promise.id not in _ids(await _pack(db, w, w.giao_thuong, p_vy))
    assert promise.id not in _ids(await _pack(db, w, w.gia_dinh, p_vy))


async def test_fixgkb3_money_rule_between_two_customer_groups(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    owe = await _fact(db, w, w.an, "còn nợ 12 triệu tiền hàng tháng 9", w.kinh_doanh, predicate="owes")
    disc = await _fact(db, w, w.minh, "chiết khấu 8% cho đơn từ 50kg", w.kinh_doanh)
    for f in (owe, disc):
        assert f.id not in _ids(await _pack(db, w, w.kinh_doanh2, w.p_an, w.p_minh))
        assert f.id in _ids(await _pack(db, w, w.kinh_doanh, w.p_an, w.p_minh))
    assert disc.id in _ids(await _pack(db, w, w.nhan_vien))
    offer = await _fact(db, w, w.minh, "Arabica 390k/kg", w.kinh_doanh, predicate="offers")   # không phải lời hứa/nợ ⇒ quy tắc tiền không áp
    assert offer.id in _ids(await _pack(db, w, w.kinh_doanh2))


async def test_fixgkb3_multi_label_target_and_source_never_cross(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    shoes = await _fact(db, w, w.minh, "mua giày cho Bin", w.gia_dinh)
    salary = await _fact(db, w, w.minh, "lương tháng 9 cho nhân viên", w.nhan_vien, predicate="owes")
    sample = await _fact(db, w, w.minh, "gửi mẫu 3 loại hạt thứ 6", w.kinh_doanh)
    mixed = await _group(db, w.org, "Tiệm nhà mình + khách quen", None, w.user, members=40)
    await contexts.set_group_contexts(db, w.org, mixed, [{"context": "kinh_doanh", "is_primary": True}, {"context": "gia_dinh"},
                                                         {"context": "nhan_vien"}], user_id=w.user)
    await db.commit()
    got = _ids(await _pack(db, w, mixed))
    assert shoes.id not in got and salary.id not in got and sample.id not in got   # đích nhiều nhãn ⇒ không nhận gì từ luồng khác
    own = await _fact(db, w, w.minh, "giao 5kg chiều mai", mixed)
    assert own.id in _ids(await _pack(db, w, mixed))                                  # việc của chính nhóm vẫn hiện
    # nguồn nhiều nhãn (kinh_doanh + gia_dinh) ⇒ không sang nhóm kinh_doanh khác
    assert own.id not in _ids(await _pack(db, w, w.kinh_doanh2))


async def test_fixgkb3_unknown_member_count_blocks_cross_group(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    """`core.group_members` chỉ có người đã nhắn ⇒ không dùng để đoán cỡ nhóm; `member_count` NULL ⇒ không xuyên luồng."""
    w = await _world(db)
    offer = await _fact(db, w, w.minh, "bán sỉ cold brew", w.giao_thuong, predicate="offers")
    crowd = await _group(db, w.org, "Hội 120 người (bridge chưa báo số)", "giao_thuong", w.user, members=None)
    for i in range(10):
        p = await mk_person_row(db, w.org, f"P{i}")
        await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": crowd, "p": p})
    await db.commit()
    assert offer.id not in _ids(await _pack(db, w, crowd))
    mine = await _fact(db, w, w.minh, "đăng ký gala tháng 11", crowd)
    assert mine.id in _ids(await _pack(db, w, crowd))                                  # cùng luồng vẫn hiện


async def test_fixgkb3_dm_with_person_sees_their_own_group_promise(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    an_promise = await _fact(db, w, w.an, "chuyển 50 triệu trước 30/10", w.kinh_doanh)
    said_by_an = await _fact(db, w, w.minh, "ghé kho lấy mẫu thứ 7", w.kinh_doanh, speaker=w.an, cls="member", attributed_to=w.an)
    w.ban_be = await _group(db, w.org, "Bạn thân", "ban_be", w.user, members=6)
    an_family = await _fact(db, w, w.an, "dự đám cưới em gái 2/11", w.ban_be, predicate="scheduled_event")
    not_an = await _fact(db, w, w.minh, "gửi mẫu cho Kim Chi", w.kinh_doanh)
    unconf = await _group(db, w.org, "Nhóm chưa gán", None, w.user, members=5)
    an_unconf = await _fact(db, w, w.an, "lấy 20kg tuần sau", unconf)
    ba = await _fact(db, w, w.an, "đau lưng phải nghỉ", w.kinh_doanh, predicate="health_event")
    got = _ids(await retrieve.pack(db, w.org, f"dm:{w.p_an}", [{"sender_person_id": w.p_an, "text": "x"}], now=w.now))
    assert an_promise.id in got and said_by_an.id in got
    assert an_family.id not in got and not_an.id not in got and an_unconf.id not in got and ba.id not in got


async def test_fixgkb3_related_step2_requires_visible_parent_edge(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await _world(db)
    bin_ = await mk_entity(db, w.org, "Bin")
    school = await mk_entity(db, w.org, "Trường Lê Lợi", "org")
    e1 = await facts.assert_fact(db, mkfact(w.org, bin_, None, w.now - timedelta(days=3), predicate="family_of", object_entity=w.minh,
                                            raw=await _raw(db, w.org, w.gia_dinh)))
    e2 = await facts.assert_fact(db, mkfact(w.org, bin_, None, w.now - timedelta(days=3), predicate="works_at", object_entity=school,
                                            raw=await _raw(db, w.org, w.cong_viec)))
    await db.commit()
    rel = (await _pack(db, w, w.cong_viec)).related
    assert all(i.fact_id not in (e1.id, e2.id) for i in rel) and all(i.entity_id != school for i in rel)
    fam = {i.fact_id for i in (await _pack(db, w, w.gia_dinh)).related}
    assert e1.id in fam and e2.id not in fam
