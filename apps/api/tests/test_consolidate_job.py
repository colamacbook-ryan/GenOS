# ruff: noqa: E501
"""Job đêm hợp nhất (L4): tóm tắt thực thể, đối soát sau gộp, đóng tranh chấp theo bằng chứng, đề xuất Hồ sơ, Action Log, cửa sổ giờ."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import consolidate, disputes, entities, facts, jobs, profile
from gh.biz.mem import job_consolidate as jc
from tests.mem_helpers import T, mk_entity, mk_org, mkfact, pay
from tests.test_owner_profile_support import mk_person, mk_raw, mk_world


async def _commit(db) -> None:  # type: ignore[no-untyped-def]
    await db.commit()


async def test_consolidate_summaries_lines_map_to_fact_ids_and_skip_sensitive(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan, mai = await mk_entity(db, org, "Chị Lan"), await mk_entity(db, org, "Chị Mai")
    a = await facts.assert_fact(db, mkfact(org, lan, "Hà Nội", T(1), predicate="lives_in", speaker=lan))
    await facts.assert_fact(db, mkfact(org, lan, "0901234567", T(1), predicate="contact_phone", speaker=lan))     # nhạy cảm ⇒ không vào
    await facts.assert_fact(db, mkfact(org, lan, "giá sỉ", T(1), predicate="prefers", cls="outsider", attributed_to=mai))
    await facts.assert_fact(db, mkfact(org, lan, "x", T(1), predicate="said_will_leave"))                          # nhân sự ⇒ không vào
    await _commit(db)
    r = await consolidate.rebuild_summaries(db, org)
    await _commit(db)
    assert r == {"summaries_built": 1, "summaries_removed": 0}
    s = (await db.execute(text("SELECT summary, fact_ids, projection_version FROM mem.entity_summaries WHERE entity_id = :e"), {"e": lan})).one()
    lines = s.summary.split("\n")
    assert len(lines) == len(s.fact_ids) == 2 and s.projection_version == 1
    assert lines[0] == "Ở: Hà Nội" or any(x.startswith("Ở: Hà Nội") for x in lines)
    assert "0901" not in s.summary and "Đã nói" not in s.summary
    out = next(x for x in lines if x.startswith("Thích"))
    assert "theo lời Chị Mai" in out and "chưa kiểm" in out                                                        # chữ người ngoài mang nguồn
    assert a.id in s.fact_ids
    assert (await consolidate.rebuild_summaries(db, org))["summaries_built"] == 0                                  # không đổi ⇒ không dựng lại
    await facts.assert_fact(db, mkfact(org, lan, "HCM", T(5), predicate="lives_in", speaker=lan))
    await _commit(db)
    assert (await consolidate.rebuild_summaries(db, org))["summaries_built"] == 1
    # hết sự thật ⇒ xoá tóm tắt
    await db.execute(text("UPDATE mem.facts SET status = 'retracted', valid_to = valid_from WHERE subject_id = :e"), {"e": lan})
    await _commit(db)
    r2 = await consolidate.rebuild_summaries(db, org)
    assert r2["summaries_removed"] == 1
    assert (await db.execute(text("SELECT count(*) FROM mem.entity_summaries"))).scalar_one() == 0


async def test_consolidate_summaries_are_org_scoped(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    o1, o2 = await mk_org(db), await mk_org(db, "Org 2")
    e1, e2 = await mk_entity(db, o1, "A"), await mk_entity(db, o2, "B")
    await facts.assert_fact(db, mkfact(o1, e1, "HN", T(1), predicate="lives_in", speaker=e1))
    await facts.assert_fact(db, mkfact(o2, e2, "HCM", T(1), predicate="lives_in", speaker=e2))
    await _commit(db)
    await consolidate.rebuild_summaries(db, o1)
    rows = (await db.execute(text("SELECT entity_id, org_id, summary FROM mem.entity_summaries"))).all()
    assert [(r.entity_id, r.org_id) for r in rows] == [(e1, o1)] and "HCM" not in rows[0].summary


async def _conflict(db, org):  # type: ignore[no-untyped-def]
    """Hai thực thể cùng người, mỗi bên một nơi ở (đơn trị) — gộp KHÔNG đối soát ⇒ chờ job đêm."""
    a, b = await mk_entity(db, org, "Lan"), await mk_entity(db, org, "Lan Kho")
    fa = await facts.assert_fact(db, mkfact(org, a, "HN", T(1), predicate="lives_in", speaker=a))
    fb = await facts.assert_fact(db, mkfact(org, b, "HCM", T(1), predicate="lives_in", speaker=b))
    await entities.merge(db, org, b, a, reconcile=False)
    await _commit(db)
    return a, b, fa, fb


async def test_consolidate_reconcile_after_merge_is_idempotent(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, _b, fa, fb = await _conflict(db, org)
    r = await consolidate.reconcile_merged(db, org)
    await _commit(db)
    assert r == {"merged_targets": 1, "reconcile_disputes": 1}
    sts = {x.id: x.status for x in (await db.execute(text("SELECT id, status FROM mem.facts"))).all()}
    assert sts == {fa.id: "disputed", fb.id: "disputed"}
    assert (await consolidate.reconcile_merged(db, org))["reconcile_disputes"] == 0
    assert len(await disputes.list(db, org)) == 1


async def _new_evidence(db, fact_id, org, hours_after_open: list[int]) -> None:  # type: ignore[no-untyped-def]
    opened = (await db.execute(text("SELECT opened_at FROM mem.disputes WHERE :f = ANY(fact_ids)"), {"f": fact_id})).scalar_one()
    for h in hours_after_open:
        await db.execute(text("""INSERT INTO mem.fact_evidence (org_id, fact_id, kind, raw_event_id, quote_status, span, created_at)
                                 VALUES (:o, :f, 'raw_event', :r, 'verified', CAST(:sp AS jsonb), :t)"""),
                         {"o": org, "f": fact_id, "r": uuid.uuid4(), "t": opened + timedelta(hours=h),
                          "sp": '{"source_field": "body", "span_start": 0, "span_end": 5}'})
    await db.commit()


async def test_consolidate_resolves_dispute_only_with_two_newer_insider_evidences_on_two_days(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, _b, fa, fb = await _conflict(db, org)
    await consolidate.reconcile_merged(db, org)
    await _commit(db)
    assert (await consolidate.resolve_by_evidence(db, org))["disputes_resolved"] == 0                 # chưa có bằng chứng mới
    await _new_evidence(db, fa.id, org, [1, 2])                                                       # 2 bằng chứng nhưng CÙNG ngày
    assert (await consolidate.resolve_by_evidence(db, org))["disputes_resolved"] == 0
    await _new_evidence(db, fa.id, org, [30])                                                         # thêm một ngày khác
    await _new_evidence(db, fb.id, org, [3])                                                          # bên kia cũng có bằng chứng mới ⇒ chưa đóng
    assert (await consolidate.resolve_by_evidence(db, org))["disputes_resolved"] == 0
    await db.execute(text("DELETE FROM mem.fact_evidence WHERE fact_id = :f AND created_at > (SELECT opened_at FROM mem.disputes LIMIT 1)"), {"f": fb.id})
    await _commit(db)
    assert (await consolidate.resolve_by_evidence(db, org))["disputes_resolved"] == 1
    await _commit(db)
    d = (await db.execute(text("SELECT resolved_by, winner_fact_id FROM mem.disputes"))).one()
    assert (d.resolved_by, d.winner_fact_id) == ("consolidator", fa.id)
    sts = {x.id: x.status for x in (await db.execute(text("SELECT id, status FROM mem.facts"))).all()}
    assert sts == {fa.id: "asserted", fb.id: "superseded"}
    _ = a


async def test_consolidate_never_auto_resolves_payment_account_or_owner_confirmed(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    e = await mk_entity(db, org, "Lan")
    f1 = await facts.assert_fact(db, mkfact(org, e, pay("a"), T(1), predicate="payment_account", speaker=e))
    f2 = await facts.assert_fact(db, mkfact(org, e, pay("b"), T(2), predicate="payment_account", speaker=e))
    assert f2.status == "disputed"
    await _commit(db)
    await _new_evidence(db, f2.id, org, [1, 30])
    assert (await consolidate.resolve_by_evidence(db, org))["disputes_resolved"] == 0
    assert (await db.execute(text("SELECT resolved_at FROM mem.disputes"))).scalar_one() is None
    _ = f1


async def test_consolidate_expiry_runs_in_run_org(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    _a, _b, fa, fb = await _conflict(db, org)
    await consolidate.reconcile_merged(db, org)
    await db.execute(text("UPDATE mem.disputes SET opened_at = now() - interval '31 days'"))
    await _commit(db)
    stats = await consolidate.run_org(db, org)
    await _commit(db)
    assert stats["disputes_expired"] == 1
    sts = {x.id: x.status for x in (await db.execute(text("SELECT id, status FROM mem.facts"))).all()}
    assert sts == {fa.id: "retracted", fb.id: "retracted"}


async def test_consolidate_proposes_profile_from_owner_group_messages_with_caps(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    _, other_i = await mk_person(db, w.org, w.channel, "Chị Lan")
    now = datetime.now(UTC)
    at = lambda m: now - timedelta(minutes=m)  # noqa: E731
    await mk_raw(db, w.org, w.channel, identity=w.owner_identity, group=w.group, at=at(50), body="Quý này anh ưu tiên mở quán thứ 2. Anh không nhận đơn dưới 10 triệu. Tối thứ 7 là dành cho gia đình.")
    await mk_raw(db, w.org, w.channel, identity=w.owner_identity, group=w.group, at=at(40), body="Anh ưu tiên mở quán thứ 2 nhé", payload={"forwarded": True})
    await mk_raw(db, w.org, w.channel, identity=other_i, group=w.group, at=at(30), body="Em ưu tiên mở quán thứ 5, em không nhận đơn lẻ")      # người khác
    await mk_raw(db, w.org, w.channel, identity=w.owner_identity, group=None, at=at(20), body="Đừng báo chuyện họ hàng xa nhé")                    # 1-1, không phải nhóm
    r = await consolidate.propose_profile(db, w.org, now=now)
    await _commit(db)
    assert r["profile_proposed"] == consolidate.PROPOSALS_PER_RUN == 2
    rows = (await db.execute(text("SELECT status, confirmed_by, created_by, source_class FROM mem.facts WHERE predicate LIKE 'owner\\_%'"))).all()
    assert len(rows) == 2 and all((x.status, x.confirmed_by, x.created_by) == ("proposed", None, "consolidator") for x in rows)
    r2 = await consolidate.propose_profile(db, w.org, now=now)                                          # câu thứ 3 của tin đầu; không sinh lại 2 câu cũ
    await _commit(db)
    assert r2["profile_proposed"] == 1
    r3 = await consolidate.propose_profile(db, w.org, now=now)
    assert r3["profile_proposed"] == 0
    texts = (await db.execute(text("SELECT object_value FROM mem.facts WHERE predicate LIKE 'owner\\_%'"))).scalars().all()
    assert len(texts) == 3 and not any("quán thứ 5" in t or "họ hàng xa" in t for t in texts)         # tin người ngoài / 1-1 không thành đề xuất


async def test_consolidate_respects_open_proposal_cap_and_rejections(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    now = datetime.now(UTC)
    for i in range(consolidate.PROPOSALS_OPEN_CAP):
        raw = await mk_raw(db, w.org, w.channel, identity=w.owner_identity, group=w.group, at=now - timedelta(days=3, minutes=i),
                           body=f"Quý này anh ưu tiên mục số {i} nhé")
        assert await profile.propose(db, w.org, section="goals", text_value=f"Quý này anh ưu tiên mục số {i} nhé", raw_event_id=raw)
    await mk_raw(db, w.org, w.channel, identity=w.owner_identity, group=w.group, at=now - timedelta(minutes=5), body="Anh không nhận đơn dưới 5 triệu")
    await _commit(db)
    assert (await consolidate.propose_profile(db, w.org, now=now))["profile_proposed"] == 0           # đã đủ trần đang chờ
    pid = (await db.execute(text("SELECT id FROM mem.facts WHERE status = 'proposed' LIMIT 1"))).scalar_one()
    await profile.decide(db, w.org, pid, "reject", user_id=w.user, action_log_id=uuid.uuid4())
    await _commit(db)
    assert (await consolidate.propose_profile(db, w.org, now=now))["profile_proposed"] == 1
    rid = (await db.execute(text("SELECT id FROM mem.facts WHERE status = 'proposed' AND object_value::text LIKE '%5 triệu%'"))).scalar_one()
    await profile.decide(db, w.org, rid, "reject", user_id=w.user, action_log_id=uuid.uuid4())
    await _commit(db)
    r = await consolidate.propose_profile(db, w.org, now=now)
    assert r["profile_proposed"] == 0 and r["profile_skipped_rejected"] == 1                         # Sếp đã từ chối ⇒ không đề xuất lại


async def test_consolidate_run_org_writes_action_log_counts_only_and_job_window(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    await mk_raw(db, w.org, w.channel, identity=w.owner_identity, group=w.group, at=datetime.now(UTC) - timedelta(minutes=5),
                 body="Quý này anh ưu tiên mở quán thứ 2 nhé")
    stats = await consolidate.run_org(db, w.org)
    await _commit(db)
    assert stats["profile_proposed"] == 1
    log = (await db.execute(text("SELECT actor_type, actor_id, detail FROM ops.action_log WHERE action = 'mem.consolidate'"))).one()
    assert (log.actor_type, log.actor_id) == ("system", "system:mem.consolidate")
    assert log.detail["profile_proposed"] == 1 and "quán" not in str(log.detail)                     # số đếm, không nội dung
    assert all(isinstance(v, int) for v in log.detail.values())
    # cửa sổ 02:00–05:00 giờ VN
    assert consolidate.in_window(datetime(2026, 10, 23, 20, 0, tzinfo=UTC))                          # 03:00 VN
    assert not consolidate.in_window(datetime(2026, 10, 23, 2, 0, tzinfo=UTC))                       # 09:00 VN
    assert not consolidate.in_window(datetime(2026, 10, 23, 22, 0, tzinfo=UTC))                      # 05:00 VN (đúng giờ đóng)
    if not consolidate.in_window():
        assert (await jc.mem_consolidate({}))["skipped"]
    forced = await jc.mem_consolidate({"force": True})
    assert str(w.org) in forced and "error" not in forced[str(w.org)]


def test_consolidate_jobs_are_collected_by_mem_jobs() -> None:
    names = {fn.__name__: kw for fn, kw in jobs.JOBS}
    assert names["mem_consolidate"] == {"hour": {2}, "minute": {10}}
    assert names["mem_heat_weekly"] == {"hour": {1}, "minute": {40}}
    assert "evidence_reverify" in names
