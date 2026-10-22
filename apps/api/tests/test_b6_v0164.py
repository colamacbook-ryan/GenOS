"""Gói B6 (v0.1.64): sàng lọc ghi loại người vào hồ sơ (F045), tin lỗi không kẹt (F055), Sếp tự đánh dấu loại người /
nhân viên (F045, F141), tiêu đề cảnh báo thân thiện (P2)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.db import sessionmaker
from gh.refinery.runner import Refinery
from gh.refinery.scheduler import Scheduler
from tests.phase2 import FakeRouter, install_presets, listen, msg, org_id, put, refs_in, states

JOB = "Em có 3 năm kinh nghiệm bán hàng, đang tìm việc mới"
BUY = "Cần 3 container thép cuộn, giá bao nhiêu vậy em?"


def unit(ref: str, rule: str = "R-05", conf: float = 0.9, event_type: str = "AskedPrice") -> dict[str, object]:
    return {"evidence": [ref], "event_type": event_type, "side": "demand", "conclusion": "Người này tìm việc",
            "entities": {}, "confidence": conf, "rules": {rule: 0.9}, "signals": {"heat": 50}}


def job_router(rule: str = "R-05") -> FakeRouter:
    return FakeRouter(lambda m: {"units": [unit(r, rule) for r in refs_in(m)], "noise": []})


async def setup_org(db) -> object:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await install_presets(db, org)
    await listen(db, org, "g1")
    return org


async def person_of(db, raw_id) -> object:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                     JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :r"""), {"r": raw_id})).scalar_one()


async def ptype(db, pid) -> tuple[str, str | None]:  # type: ignore[no-untyped-def]
    await db.rollback()
    r = (await db.execute(text("SELECT person_type, relation_to_owner FROM core.persons WHERE id = :p"),
                          {"p": pid})).one()
    return r.person_type, r.relation_to_owner


# ─── F045: quy tắc "set person_type" ghi ngược vào hồ sơ người ──────────────────────────────────────────────

async def test_rule_person_type_is_written_to_person(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    [raw] = await put(sm, org, msg(JOB, sender="cv1", name="Anh Việt"))
    pid = await person_of(db, raw)
    assert (await ptype(db, pid))[0] == "unknown"
    st = await Refinery(sm, redis, job_router()).run(org, "manual")  # type: ignore[arg-type]
    assert st.clean >= 1
    assert (await ptype(db, pid))[0] == "candidate"
    ent = (await db.execute(text("SELECT entities FROM clean.meaning_units WHERE person_id = :p"),
                            {"p": pid})).scalar_one()
    assert ent["person_type"] == "candidate"
    # tab "Ứng viên" của Hộp thư dùng đúng cột này.
    n = (await db.execute(text("SELECT count(*) FROM core.persons WHERE org_id = :o AND person_type = 'candidate'"),
                          {"o": org})).scalar_one()
    assert n == 1


async def test_person_type_is_not_written_from_outbound_only_evidence(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """P3 review v0.1.64: bằng chứng toàn tin GỬI ĐI (có thể là tài khoản Sếp/nhân viên) mà model bị dẫn dắt ⇒
    không được gán loại người cho người gửi; chỉ người đến từ tin NHẬN VÀO mới được ghi."""
    org = await setup_org(db)
    sm = sessionmaker()
    [out_raw, in_raw] = await put(
        sm, org, {**msg(JOB, sender="staff1", name="Tài khoản Sếp"), "direction": "outbound"},
        msg(JOB, sender="cv1", name="Anh Việt"))
    p_out, p_in = await person_of(db, out_raw), await person_of(db, in_raw)
    st = await Refinery(sm, redis, job_router()).run(org, "manual")  # type: ignore[arg-type]
    assert st.clean >= 2
    assert (await ptype(db, p_out))[0] == "unknown"  # người gửi tin gửi đi: không bị gán
    assert (await ptype(db, p_in))[0] == "candidate"  # người nhắn vào: vẫn ghi như cũ
    n = (await db.execute(text("""SELECT count(*) FROM ops.action_log WHERE action = 'person.type_inferred'
                                  AND target_id = :p"""), {"p": str(p_out)})).scalar_one()
    assert n == 0


async def test_person_type_uses_inbound_person_when_evidence_is_mixed(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    [out_raw, in_raw] = await put(
        sm, org, {**msg("Bên em đang tuyển", sender="staff1", name="Nhân viên"), "direction": "outbound"},
        msg(JOB, sender="cv1", name="Anh Việt"))
    p_out, p_in = await person_of(db, out_raw), await person_of(db, in_raw)

    def both(m: object) -> dict[str, object]:
        refs = refs_in(m)  # type: ignore[arg-type]
        return {"units": [{**unit(refs[0]), "evidence": refs}], "noise": []}

    await Refinery(sm, redis, FakeRouter(both)).run(org, "manual")  # type: ignore[arg-type]
    assert (await ptype(db, p_in))[0] == "candidate"
    assert (await ptype(db, p_out))[0] == "unknown"


async def test_rule_never_overwrites_a_value_the_owner_set(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    [raw_a, raw_b] = await put(sm, org, msg(JOB, sender="a1", name="Chị A"), msg(JOB, sender="b1", name="Anh B"))
    pa, pb = await person_of(db, raw_a), await person_of(db, raw_b)
    # A: Sếp đặt 'staff'. B: Sếp chủ động đặt về 'unknown' (khoá, không để máy nâng lại).
    assert (await owner_api.send("PATCH", f"/profile/{pa}", {"person_type": "staff"})).status_code == 200
    assert (await owner_api.send("PATCH", f"/profile/{pb}", {"person_type": "unknown"})).status_code == 200
    await Refinery(sm, redis, job_router()).run(org, "manual")  # type: ignore[arg-type]
    assert (await ptype(db, pa))[0] == "staff"
    assert (await ptype(db, pb))[0] == "unknown"


async def test_rule_ignores_unknown_value_and_does_not_downgrade(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    [raw] = await put(sm, org, msg(JOB, sender="c1", name="Chị C"))
    pid = await person_of(db, raw)
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE id = :p"), {"p": pid})
    await db.commit()
    await Refinery(sm, redis, job_router()).run(org, "manual")  # type: ignore[arg-type]
    assert (await ptype(db, pid))[0] == "customer"        # chỉ nâng từ 'unknown'


async def test_owner_sets_person_type_and_relation_from_profile(app, db, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    [raw] = await put(sm, org, msg(BUY, sender="d1", name="Anh D"))
    pid = await person_of(db, raw)
    r = await owner_api.send("PATCH", f"/profile/{pid}", {"person_type": "staff", "relation_to_owner": "staff"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["person"]["type"] == "staff" and body["person"]["relation_to_owner"] == "staff"
    assert await ptype(db, pid) == ("staff", "staff")
    # Thanh bên: có nhân viên ⇒ hai mục Đánh giá / Chăm sóc hết ẩn.
    nav = (await owner_api.get("/navigation")).json()
    hidden = {i["key"]: i.get("hidden") for g in nav for i in g.get("items", [])}
    assert not hidden.get("people") and not hidden.get("care")
    log = (await db.execute(text("""SELECT detail FROM ops.action_log WHERE action = 'profile.updated'
                                    AND target_id = :t ORDER BY at DESC LIMIT 1"""), {"t": str(pid)})).scalar_one()
    assert log["person_type"] == "staff" and log["relation_to_owner"] == "staff"
    # Giá trị lạ bị từ chối bằng lỗi thân thiện, hồ sơ không đổi.
    for bad in ({"person_type": "robot"}, {"relation_to_owner": "bạn thân"}):
        resp = await owner_api.send("PATCH", f"/profile/{pid}", bad)
        assert resp.status_code == 422, resp.text
    assert await ptype(db, pid) == ("staff", "staff")


# ─── F055: tin lỗi không kẹt ──────────────────────────────────────────────────────────────────────────────

async def attempts_and_state(db) -> tuple[str, int]:  # type: ignore[no-untyped-def]
    await db.rollback()
    r = (await db.execute(text("""SELECT state, attempts FROM refinery.event_state
                                  ORDER BY updated_at DESC LIMIT 1"""))).one()
    return r.state, r.attempts


async def test_dead_model_runs_do_not_burn_attempts(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY))
    for _ in range(4):                                       # model chết nhiều lượt liên tiếp
        await Refinery(sm, redis, FakeRouter(down=True)).run(org, "manual")  # type: ignore[arg-type]
    assert await attempts_and_state(db) == ("pending", 0)
    # JSON hỏng đúng 1 lượt (không phải lỗi hạ tầng) → vẫn pending, mới thử 1 lần.
    bad = FakeRouter(lambda m: "đây không phải JSON")
    await Refinery(sm, redis, bad).run(org, "manual")  # type: ignore[arg-type]
    assert await attempts_and_state(db) == ("pending", 1)
    # Model tốt trở lại → tin được xử lý, không mất.
    ok = FakeRouter(lambda m: {"units": [unit(r, "R-01") for r in refs_in(m)], "noise": []})
    st = await Refinery(sm, redis, ok).run(org, "manual")  # type: ignore[arg-type]
    assert st.clean >= 1 and (await states(db, org)).get("error", 0) == 0


async def test_three_bad_json_runs_end_in_error_and_pipeline_counts_it(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY))
    bad = FakeRouter(lambda m: "hỏng")
    for _ in range(3):
        await Refinery(sm, redis, bad).run(org, "manual")  # type: ignore[arg-type]
    assert (await attempts_and_state(db))[0] == "error"
    pipe = (await owner_api.get("/data/pipeline")).json()
    assert pipe["raw_error"] == 1 and pipe["raw_pending"] == 0


async def age_error(db, seconds: int, retries: int | None = None) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""UPDATE refinery.event_state SET updated_at = :t,
                             detail = detail || CAST(:d AS jsonb) WHERE state = 'error'"""),
                     {"t": datetime.now(UTC) - timedelta(seconds=seconds),
                      "d": f'{{"error_retries": {retries}}}' if retries is not None else "{}"})
    await db.commit()


async def test_scheduler_requeues_old_errors_a_bounded_number_of_times(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY))
    bad = FakeRouter(lambda m: "hỏng")
    ref = Refinery(sm, redis, bad)                                   # type: ignore[arg-type]
    for _ in range(3):
        await ref.run(org, "manual")
    sched = Scheduler(sm, redis, ref)
    await sched.tick(wait=True)
    assert (await attempts_and_state(db))[0] == "error"              # vừa lỗi ⇒ chưa thử lại ngay
    await age_error(db, 3600)
    await sched.tick(wait=True)
    assert await attempts_and_state(db) == ("pending", 0)
    d = (await db.execute(text("SELECT detail FROM refinery.event_state"))).scalar_one()
    assert d["error_retries"] == 1
    # Hết số lần thử lại cho phép ⇒ nằm yên ở 'error' (hiện ở dải pipeline), không lặp vô hạn.
    await db.execute(text("UPDATE refinery.event_state SET state = 'error'"))
    await db.commit()
    await age_error(db, 3600, retries=3)
    await sched.tick(wait=True)
    assert (await attempts_and_state(db))[0] == "error"


async def test_requeued_error_is_processed_when_model_is_good(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY))
    for _ in range(3):
        await Refinery(sm, redis, FakeRouter(lambda m: "hỏng")).run(org, "manual")  # type: ignore[arg-type]
    await age_error(db, 3600)
    ok = FakeRouter(lambda m: {"units": [unit(r, "R-01") for r in refs_in(m)], "noise": []})
    ref = Refinery(sm, redis, ok)                                    # type: ignore[arg-type]
    await Scheduler(sm, redis, ref).tick(wait=True)
    st = await ref.run(org, "manual")
    assert st.clean >= 1


async def test_whole_run_crashes_are_counted_and_end_in_error(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """P3 review v0.1.64: cả lượt sập (attempts hoàn lại) ⇒ trước đây thử lại vô hạn. Nay đếm số lần sập trong detail,
    đủ MAX_CRASHES ⇒ `error` (vẫn được bộ lập lịch nhận lại có giới hạn, hiện ở dải pipeline)."""
    from gh.refinery import runner

    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY))

    async def boom(self, *a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("sập cả lượt")

    monkeypatch.setattr(Refinery, "_apply", boom)
    ref = Refinery(sm, redis, FakeRouter(lambda m: {"units": [unit(r, "R-01") for r in refs_in(m)], "noise": []}))  # type: ignore[arg-type]
    for i in range(1, runner.MAX_CRASHES):
        await ref.run(org, "manual")
        await db.rollback()
        r = (await db.execute(text("SELECT state, attempts, detail FROM refinery.event_state"))).one()
        assert (r.state, r.attempts, r.detail["crashes"]) == ("pending", 0, i)  # chưa đủ ⇒ vẫn thử lại, attempts hoàn
    st = await ref.run(org, "manual")
    await db.rollback()
    r = (await db.execute(text("SELECT state, detail FROM refinery.event_state"))).one()
    assert r.state == "error" and r.detail["crashes"] == runner.MAX_CRASHES and "sập cả lượt" in r.detail["last_error"]
    assert st.errors == 1
    # đã `error` ⇒ lượt sau không nhận lại ngay (không vòng vô hạn)
    st2 = await ref.run(org, "manual")
    assert st2.total == 0


async def test_scheduler_stuck_recovery_counts_crashes_too(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Lượt chết giữa chừng (worker bị giết) liên tục vì cùng một tin ⇒ cũng bị đếm, không hồi sinh vô hạn."""
    from gh.refinery import runner

    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY))
    sched = Scheduler(sm, redis, Refinery(sm, redis, FakeRouter(down=True)))  # type: ignore[arg-type]
    for i in range(1, runner.MAX_CRASHES + 1):
        run_id = await sched.refinery.start_run(org, "manual")
        await db.execute(text("""UPDATE refinery.event_state SET state = 'processing', run_id = :r, attempts = 1
                                 WHERE state = 'pending'"""), {"r": run_id})
        await db.execute(text("UPDATE refinery.runs SET status = 'failed' WHERE id = :r"), {"r": run_id})
        await db.commit()
        await sched.tick(wait=True)
        await db.rollback()
        r = (await db.execute(text("SELECT state, attempts, detail FROM refinery.event_state"))).one()
        assert r.detail["crashes"] == i and r.attempts == 0
        assert r.state == ("error" if i == runner.MAX_CRASHES else "pending")


# ─── P2: tiêu đề cảnh báo thân thiện ─────────────────────────────────────────────────────────────────────

def test_alert_title_is_vietnamese_and_has_no_internal_codes() -> None:
    from gh.refinery.runner import alert_title

    assert alert_title("Phàn nàn", "Kho Hà Nội", None, False) == "Phàn nàn · nhóm Kho Hà Nội"
    assert alert_title("Phàn nàn", None, "Chị Lan", False) == "Phàn nàn · tin riêng của Chị Lan"
    # Tín hiệu nhân sự: không đưa tên người vào tiêu đề.
    assert alert_title("Tín hiệu nhân sự", None, "Chị Lan", True) == "Tín hiệu nhân sự · tin riêng"
    assert alert_title("Phàn nàn", None, None, False) == "Phàn nàn · tin riêng"


async def test_rule_alert_title_uses_label_and_group_name(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg("Giao hàng chậm quá, tôi thất vọng", sender="k1", name="Chị Khách"))
    router = FakeRouter(lambda m: {"units": [unit(r, "R-03", event_type="Complained") for r in refs_in(m)],
                                   "noise": []})
    await Refinery(sm, redis, router).run(org, "manual")  # type: ignore[arg-type]
    title = (await db.execute(text("""SELECT title FROM biz.alerts
                                      WHERE alert_type = 'repeated_complaint'"""))).scalar_one()
    assert title == "Phàn nàn · nhóm Nhóm g1"
    assert "Complained" not in title and "G-" not in title
