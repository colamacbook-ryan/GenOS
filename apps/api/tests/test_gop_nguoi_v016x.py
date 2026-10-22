"""v0.1.61 (gói A6, F054 + F059): hợp nhất người dời mọi bảng nghiệp vụ trỏ về hồ sơ bị gộp (và đảo ngược được),
bấm "Không phải" một lần cho cả cặp NGƯỜI (mọi cặp tài khoản anh em), dò trùng không đề xuất lại cặp đã từ chối."""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from gh.db import sessionmaker
from gh.identity import service as ident
from tests.phase2 import listen, msg, org_id, put


async def _person_of(db: Any, raw_id: Any) -> uuid.UUID:
    return (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                     JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :r"""), {"r": raw_id})).scalar_one()  # type: ignore[no-any-return]


async def _user(db: Any) -> uuid.UUID:
    return (await db.execute(text("SELECT id FROM core.users LIMIT 1"))).scalar_one()  # type: ignore[no-any-return]


async def _two_people(db: Any, org: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Hai hồ sơ cùng tên "Nguyễn Văn An" ở hai kênh ⇒ dò trùng sinh đúng một đề xuất."""
    await listen(db, org, "g1")
    sm = sessionmaker()
    ra, rb = await put(sm, org, msg("Chào anh em", sender="zA", name="Nguyễn Văn An"),
                       msg("Cần báo giá thép", sender="wB", name="Nguyễn Văn An"))
    return await _person_of(db, ra), await _person_of(db, rb)


async def _detect(org: uuid.UUID) -> int:
    async with sessionmaker()() as s:
        n = await ident.detect(s, org)
        await s.commit()
    return n


async def _merge_pending(db: Any, org: uuid.UUID) -> dict[str, Any]:
    cand = (await db.execute(text("SELECT id FROM core.identity_merge_candidates WHERE status = 'pending'"))
            ).scalar_one()
    uid = await _user(db)
    async with sessionmaker()() as s:
        out = await ident.merge(s, org, cand, uid)
        await s.commit()
    return out


async def _seed_business(db: Any, org: uuid.UUID, p: uuid.UUID, other: uuid.UUID) -> None:
    """Một dòng trỏ về hồ sơ `p` ở MỌI bảng nghiệp vụ trong PERSON_REFS (+ vài dòng KHÔNG được động tới)."""
    uid = await _user(db)
    await db.execute(text("""INSERT INTO biz.opportunities (org_id, code, person_id, need, stage, value_vnd,
                                                             confidence, first_signal_at)
                             VALUES (:o, 'OPP-9001', :p, 'Cần 3 container', 'raw_signal', 5, 'high', now())"""),
                     {"o": org, "p": p})
    await db.execute(text("""INSERT INTO biz.deals (org_id, code, person_id, amount_vnd, status)
                             VALUES (:o, 'DEAL-9001', :p, 1000, 'open')"""), {"o": org, "p": p})
    await db.execute(text("""INSERT INTO biz.market_signals (org_id, person_id, side, item)
                             VALUES (:o, :p, 'demand', 'Thép')"""), {"o": org, "p": p})
    await db.execute(text("""INSERT INTO biz.people_reviews (org_id, person_id, period_start, period_end, score, signal,
                                                              recommendation, evidence)
                             VALUES (:o, :p, '2026-09-01', '2026-09-30', 70, 'ok', 'giữ', '[{"x": 1}]')"""),
                     {"o": org, "p": p})
    await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, to_person_id, text, due_at)
                             VALUES (:o, :p, :q, 'Gửi báo giá', now())"""), {"o": org, "p": p, "q": other})
    await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, to_person_id, text, due_at)
                             VALUES (:o, :q, :p, 'Chuyển hàng', now())"""), {"o": org, "p": p, "q": other})
    await db.execute(text("""INSERT INTO biz.documents (org_id, title, storage_key, mime, bytes, created_by,
                                                          owner_person_id)
                             VALUES (:o, 'Hợp đồng', 'k/1', 'application/pdf', 10, 'test', :p)"""), {"o": org, "p": p})
    for tbl, code in (("tasks", "TSK-9001"), ("cases", "CASE-9001")):
        cols = "org_id, code, title, subject_type, subject_id" + (", kind, priority" if tbl == "cases" else "")
        vals = ":o, :c, 'Gọi lại anh An', 'person', :p" + (", 'support', 'P2'" if tbl == "cases" else "")
        await db.execute(text(f"INSERT INTO biz.{tbl} ({cols}) VALUES ({vals})"), {"o": org, "c": code, "p": p})  # noqa: S608
        # Cùng bảng nhưng chủ thể là NHÓM trùng id (không bao giờ xảy ra thật, chỉ để chứng minh có lọc subject_type).
        vals_group = vals.replace("'person'", "'group'")
        await db.execute(text(f"INSERT INTO biz.{tbl} ({cols}) VALUES ({vals_group})"),  # noqa: S608
                         {"o": org, "c": code + "-G", "p": p})
    await db.execute(text("""INSERT INTO biz.alerts (org_id, code, alert_type, title, subject_type, subject_id)
                             VALUES (:o, 'ALR-9001', 'quiet', 'Chưa trả lời', 'person', :p)"""), {"o": org, "p": p})
    await db.execute(text("""INSERT INTO biz.action_drafts (org_id, code, kind, body, autonomy_level, subject_type,
                                                          subject_id)
                             VALUES (:o, 'DRF-9001', 'message', '{}', 1, 'person', :p)"""), {"o": org, "p": p})
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             VALUES (:o, :u, 'person', :p)"""), {"o": org, "u": uid, "p": p})
    # v0.1.68: sự kiện «người lạ nhắn riêng lần đầu» của Nhật ký lắng nghe trỏ về người.
    await db.execute(text("""INSERT INTO ops.listen_events (org_id, kind, channel_type, person_id)
                             VALUES (:o, 'direct_new', 'zalo', :p)"""), {"o": org, "p": p})
    await db.commit()


# (bảng, cột trỏ người, lọc chủ thể) — đúng danh sách `ident.PERSON_REFS`, viết lại ở đây để test độc lập với mã.
_EXPECT = [("biz.opportunities", "person_id", None), ("biz.deals", "person_id", None),
           ("biz.market_signals", "person_id", None), ("biz.people_reviews", "person_id", None),
           ("biz.promises", "promiser_person_id", None), ("biz.promises", "to_person_id", None),
           ("biz.documents", "owner_person_id", None), ("biz.tasks", "subject_id", "person"),
           ("biz.alerts", "subject_id", "person"), ("biz.cases", "subject_id", "person"),
           ("biz.action_drafts", "subject_id", "person"), ("core.assignments", "subject_id", "person"),
           ("ops.listen_events", "person_id", None)]


async def _count_at(db: Any, person: uuid.UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for tbl, col, st in _EXPECT:
        where = f"{col} = :p" + (f" AND subject_type = '{st}'" if st else "")
        out[f"{tbl}.{col}"] = (await db.execute(text(f"SELECT count(*) FROM {tbl} WHERE {where}"),  # noqa: S608
                                                {"p": person})).scalar_one()
    return out


async def test_merge_moves_every_business_table_and_revert_restores(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    third = (await db.execute(text("SELECT core.next_code('PER')"))).scalar_one()
    other = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                                      VALUES (:o, :c, 'Người khác')
                                      RETURNING id"""), {"o": org, "c": third})).scalar_one()
    await _seed_business(db, org, pb, other)
    await _seed_business_keep(db, org, pa)
    assert await _detect(org) == 1
    before_keep = await _count_at(db, pa)
    before_drop = await _count_at(db, pb)
    assert all(v >= 1 for v in before_drop.values()), before_drop

    out = await _merge_pending(db, org)
    kept, dropped = uuid.UUID(out["person"]["id"]), uuid.UUID(out["merged"]["id"])
    assert {kept, dropped} == {pa, pb}
    after_keep = await _count_at(db, kept)
    after_drop = await _count_at(db, dropped)
    # Mọi bảng: hồ sơ bị gộp hết dòng, hồ sơ giữ nhận đủ (cộng dồn).
    assert all(v == 0 for v in after_drop.values()), after_drop
    stats_of = {True: before_keep, False: before_drop}
    for key in after_keep:
        assert after_keep[key] == stats_of[kept == pa][key] + stats_of[kept != pa][key], key
    # Dòng chủ thể là NHÓM không bị dời.
    assert (await db.execute(text("SELECT count(*) FROM biz.tasks WHERE subject_type = 'group' AND subject_id = :p"),
                             {"p": dropped})).scalar_one() == (1 if dropped == pb else 0)

    # Đảo ngược: mọi thứ về đúng chỗ cũ.
    log_id = (await db.execute(text("SELECT id FROM core.identity_merge_log WHERE op = 'merge'"))).scalar_one()
    async with sessionmaker()() as s:
        await ident.revert(s, org, log_id, await _user(db))
        await s.commit()
    assert await _count_at(db, pa) == before_keep
    assert await _count_at(db, pb) == before_drop


async def _seed_business_keep(db: Any, org: uuid.UUID, p: uuid.UUID) -> None:
    """Hồ sơ giữ cũng có dữ liệu riêng (mã khác) ⇒ sau gộp phải là phép CỘNG, không ghi đè."""
    uid = await _user(db)
    await db.execute(text("""INSERT INTO biz.opportunities (org_id, code, person_id, need, stage, value_vnd,
                                                             confidence, first_signal_at)
                             VALUES (:o, 'OPP-9002', :p, 'Cần thép tấm', 'raw_signal', 1, 'high', now())"""),
                     {"o": org, "p": p})
    await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, subject_type, subject_id)
                             VALUES (:o, 'TSK-9002', 'Việc của hồ sơ giữ', 'person', :p)"""), {"o": org, "p": p})
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             VALUES (:o, :u, 'person', :p)"""), {"o": org, "u": uid, "p": p})
    await db.commit()


async def test_schema_net_every_person_column_is_handled_or_excluded(app, db) -> None:  # type: ignore[no-untyped-def]
    """Lưới nhỏ: bảng/cột MỚI trỏ về người (person_id, *_person_id, subject_id) mà không nằm trong PERSON_REFS hay
    danh sách loại trừ có lý do ⇒ test đỏ — bảng mới sau này không bị sót khi gộp người."""
    rows = (await db.execute(text("""
        SELECT c.table_schema || '.' || c.table_name AS tbl, c.column_name AS col
        FROM information_schema.columns c JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE'
        WHERE c.table_schema IN ('biz', 'clean', 'core', 'ops', 'memory', 'refinery', 'raw', 'agent')
          AND (c.column_name IN ('person_id', 'subject_id', 'from_person', 'to_person')
               OR c.column_name LIKE '%\\_person\\_id')
          AND NOT EXISTS (SELECT 1 FROM pg_inherits i
                          WHERE i.inhrelid = (c.table_schema || '.' || c.table_name)::regclass)
        ORDER BY 1, 2"""))).all()
    found = {f"{r.tbl}.{r.col}" for r in rows}
    handled = {f"{t}.{c}" for t, c in ident.PERSON_REFS_COLUMNS}
    unhandled = found - handled - set(ident.PERSON_REFS_EXCLUDED)
    assert not unhandled, ("Cột trỏ về người chưa được xử lý khi gộp (thêm vào PERSON_REFS hoặc "
                           f"PERSON_REFS_EXCLUDED): {sorted(unhandled)}")
    # Danh sách khai báo không được chứa cột đã biến mất.
    stale = (handled | set(ident.PERSON_REFS_EXCLUDED)) - found
    assert not stale, f"Khai báo thừa (cột không còn trong lược đồ): {sorted(stale)}"
    for tbl, _col, _st in _EXPECT:
        assert any(tbl == t for t, _ in ident.PERSON_REFS_COLUMNS), tbl


async def test_merge_recomputes_kept_score_and_revert_restores(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    gid = (await db.execute(text("SELECT id FROM core.groups WHERE external_id = 'g1'"))).scalar_one()
    # Hồ sơ B có đơn vị ý nghĩa nóng (heat 90); hồ sơ A chưa có điểm.
    await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type,
                                                               conclusion, confidence, run_id, scores)
        VALUES (:o, now(), :g, :p, 'AskedPrice', 'Hỏi giá thép', 0.9, core.uuid_v7(), '{"heat": 90}')"""),
        {"o": org, "g": gid, "p": pb})
    # Hồ sơ A có 2 đơn vị (điểm 0) ⇒ _pick_keep giữ A; điểm nóng của B phải theo đơn vị dời sang A.
    for _ in range(2):
        await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type,
                                                                   conclusion, confidence, run_id)
            VALUES (:o, now(), :g, :p, 'Chat', 'Chào', 0.9, core.uuid_v7())"""), {"o": org, "g": gid, "p": pa})
    snap = (await db.execute(text("""INSERT INTO clean.score_snapshots (org_id, subject_type, subject_id, dimension,
                                                                         value, confidence, explanation)
        VALUES (:o, 'person', :p, 'heat', 90, 0.9, '{}') RETURNING id"""), {"o": org, "p": pb})).scalar_one()
    await db.execute(text("""INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value, trend,
                                                                snapshot_id, updated_at)
        VALUES ('person', :p, 'heat', 90, 'up', :s, now())"""), {"p": pb, "s": snap})
    await db.commit()
    await _detect(org)
    out = await _merge_pending(db, org)
    kept, dropped = uuid.UUID(out["person"]["id"]), uuid.UUID(out["merged"]["id"])
    assert (kept, dropped) == (pa, pb)

    def heat(p: uuid.UUID) -> Any:
        return db.execute(text("""SELECT value FROM clean.current_scores WHERE subject_type = 'person'
                                  AND subject_id = :p AND dimension = 'heat'"""), {"p": p})

    # Điểm không còn nằm ở hồ sơ bị gộp; hồ sơ giữ được tính lại từ đơn vị ý nghĩa vừa dời sang (heat ~90).
    assert (await heat(dropped)).scalar_one_or_none() is None
    kept_heat = (await heat(kept)).scalar_one_or_none()
    assert kept_heat is not None and 80 <= float(kept_heat) <= 90

    log_id = (await db.execute(text("SELECT id FROM core.identity_merge_log WHERE op = 'merge'"))).scalar_one()
    async with sessionmaker()() as s:
        await ident.revert(s, org, log_id, await _user(db))
        await s.commit()
    restored = (await heat(pb)).scalar_one_or_none()
    assert restored is not None and float(restored) == 90.0


async def test_revert_of_pre_v0161_log_without_refs_still_works(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    """Nhật ký gộp cũ (snapshot chưa có `refs`/`scores`) vẫn đảo ngược được như trước."""
    org = await org_id(db)
    await _two_people(db, org)
    await _detect(org)
    await _merge_pending(db, org)
    await db.execute(text("""UPDATE core.identity_merge_log SET snapshot = snapshot - 'refs' - 'scores'
                             WHERE op = 'merge'"""))
    await db.commit()
    log_id = (await db.execute(text("SELECT id FROM core.identity_merge_log WHERE op = 'merge'"))).scalar_one()
    async with sessionmaker()() as s:
        out = await ident.revert(s, org, log_id, await _user(db))
        await s.commit()
    assert out["op"] == "merge"
    live = (await db.execute(text("SELECT count(*) FROM core.persons WHERE merged_into_id IS NULL"))).scalar_one()
    assert live >= 2


async def test_people_review_period_collision_does_not_break_merge(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    """Cả hai hồ sơ cùng có đánh giá hệ thống của một kỳ (chỉ mục duy nhất theo kỳ): gộp không vỡ, dòng không va chạm
    vẫn được dời, dòng va chạm ở lại (đảo ngược vẫn đúng)."""
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    for p, period in ((pa, date(2026, 9, 1)), (pb, date(2026, 9, 1)), (pb, date(2026, 8, 1))):
        await db.execute(text("""INSERT INTO biz.people_reviews (org_id, person_id, period_start, period_end, score,
                                                                   signal, recommendation, evidence)
            VALUES (:o, :p, :s, :e, 70, 'ok', 'giữ', '[{"x": 1}]')"""),
            {"o": org, "p": p, "s": period, "e": period + timedelta(days=20)})
    await db.commit()
    await _detect(org)
    out = await _merge_pending(db, org)
    kept = uuid.UUID(out["person"]["id"])
    n = (await db.execute(text("SELECT count(*) FROM biz.people_reviews WHERE person_id = :p"),
                          {"p": kept})).scalar_one()
    assert n >= 2
    log_id = (await db.execute(text("SELECT id FROM core.identity_merge_log WHERE op = 'merge'"))).scalar_one()
    async with sessionmaker()() as s:
        await ident.revert(s, org, log_id, await _user(db))
        await s.commit()
    assert (await db.execute(text("SELECT count(*) FROM biz.people_reviews WHERE person_id = :p"),
                             {"p": pa})).scalar_one() == 1
    assert (await db.execute(text("SELECT count(*) FROM biz.people_reviews WHERE person_id = :p"),
                             {"p": pb})).scalar_one() == 2


# ─── F059: "Không phải" theo cặp NGƯỜI ───────────────────────────────────────

async def test_reject_marks_every_account_pair_of_the_same_people(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await listen(db, org, "g2", channel="whatsapp")
    sm = sessionmaker()
    # Người 1 (Lê Thị Hoa) có 2 tài khoản; gộp tay Zalo+WhatsApp thành MỘT người rồi thêm người 2 cùng tên.
    await put(sm, org, msg("a", sender="zX", name="Lê Thị Hoa"))
    await put(sm, org, msg("b", sender="wX", name="Lê Thị Hoa", channel="whatsapp", group="g2"))
    await _detect(org)
    await _merge_pending(db, org)
    await put(sm, org, msg("c", sender="zY", name="Lê Thị Hoa"))
    await _detect(org)
    pend = (await owner_api.get("/identity/candidates?status=pending")).json()
    pairs = {tuple(sorted([c["a"]["person"]["code"], c["b"]["person"]["code"]])) for c in pend}
    assert len(pend) == 2 and len(pairs) == 1, "hai đề xuất tài khoản cho cùng một cặp người"

    r = await owner_api.send("POST", f"/identity/candidates/{pend[0]['id']}/reject", {})
    assert r.status_code == 200, r.text
    assert (await owner_api.get("/identity/candidates?status=pending")).json() == []
    rejected = (await owner_api.get("/identity/candidates?status=rejected")).json()
    assert len(rejected) == 2
    assert (await db.execute(text("""SELECT count(*) FROM core.identity_merge_candidates
                                     WHERE status = 'rejected' AND decided_at IS NOT NULL"""))).scalar_one() == 2


async def test_detect_skips_pair_of_people_already_rejected(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    sm = sessionmaker()
    await put(sm, org, msg("a", sender="zX", name="Phạm Quốc Bảo"))
    await put(sm, org, msg("b", sender="wX", name="Phạm Quốc Bảo", channel="zalo"))
    await _detect(org)
    (cid,) = (await db.execute(text("SELECT id FROM core.identity_merge_candidates"))).scalars().all()
    pa, pb = (await db.execute(text("""SELECT ia.person_id, ib.person_id FROM core.identity_merge_candidates m
                                       JOIN core.person_identities ia ON ia.id = m.identity_a
                                       JOIN core.person_identities ib ON ib.id = m.identity_b"""))).one()
    r = await owner_api.send("POST", f"/identity/candidates/{cid}/reject", {})
    assert r.status_code == 200
    # Hai người đó mỗi người có thêm một tài khoản mới (tài khoản anh em chưa từng có đề xuất) ⇒ KHÔNG hỏi lại.
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE type = 'zalo'"))).scalar_one()
    for p, ext in ((pa, "zA2"), (pb, "zB2")):
        await db.execute(text("""INSERT INTO core.person_identities (person_id, channel_id, external_id, handle)
                                 VALUES (:p, :c, :x, 'Phạm Quốc Bảo')"""), {"p": p, "c": ch, "x": ext})
    await db.commit()
    await _detect(org)  # lượt đầy đủ sau khi mốc cũ
    assert (await owner_api.get("/identity/candidates?status=pending")).json() == []
    async with sessionmaker()() as s:
        await s.execute(text("DELETE FROM ops.job_watermarks"))
        await s.commit()
    await _detect(org)
    assert (await owner_api.get("/identity/candidates?status=pending")).json() == []


async def test_reject_unknown_or_decided_candidate_is_404(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", f"/identity/candidates/{uuid.uuid4()}/reject", {})
    assert r.status_code == 404
