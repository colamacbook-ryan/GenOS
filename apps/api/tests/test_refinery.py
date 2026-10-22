"""Sàng lọc: quy tắc loại nhiễu, model trích xuất có chứng cứ, loại mã bịa, model chết giữ chờ, chạy song song,
bộ kích hoạt chu kỳ HOẶC ngưỡng."""

import asyncio
import time

from sqlalchemy import text

from gh.db import sessionmaker
from gh.refinery.runner import Refinery
from gh.refinery.scheduler import Scheduler, tick_key
from tests.phase2 import FakeRouter, install_presets, listen, msg, org_id, put, refs_in, states, texts_in

BUY = "Cần 3 container thép cuộn, giá bao nhiêu vậy em?"


def unit(ref: str, conf: float = 0.9, **kw: object) -> dict[str, object]:
    return {"evidence": [ref], "event_type": "AskedPrice", "side": "demand", "conclusion": "Hỏi giá 3 container thép",
            "entities": {"product": "thép cuộn", "qty": 3, "unit": "container"}, "confidence": conf,
            "rules": {"R-01": 0.9}, "signals": {"heat": 70, "potential": 60}, **kw}


def by_text(noise_words: tuple[str, ...] = ()) -> FakeRouter:
    """Model giả: tin mua hàng → unit, còn lại → noise."""
    def reply(messages):  # type: ignore[no-untyped-def]
        units, noise = [], []
        for ref, body in texts_in(messages).items():
            (units.append(unit(ref)) if "container" in body else noise.append(ref))
        return {"units": units, "noise": noise}
    return FakeRouter(reply)


async def setup_org(db) -> object:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await install_presets(db, org)
    await listen(db, org, "g1")
    return org


async def test_rules_discard_greetings_without_model(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg("ok cả nhà"), msg("Chào mọi người"))
    router = FakeRouter()
    st = await Refinery(sm, redis, router).run(org, "manual")  # type: ignore[arg-type]
    assert (st.total, st.noise, st.clean) == (2, 2, 0)
    assert router.calls == []                   # không tốn lượt model cho nhiễu
    assert await states(db, org) == {"discarded": 2}
    detail = (await db.execute(text("SELECT label, detail FROM refinery.event_state LIMIT 1"))).one()
    assert detail.label == "Noise" and detail.detail["discarded_by"] == "R-06"


async def test_model_extraction_writes_clean_unit_with_evidence(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    [eid] = await put(sm, org, msg(BUY))
    st = await Refinery(sm, redis, by_text()).run(org, "manual")  # type: ignore[arg-type]
    assert (st.clean, st.status) == (1, "done")
    u = (await db.execute(text("""SELECT id, event_type, side, confidence, rule_codes, score, person_id
                                  FROM clean.meaning_units"""))).one()
    assert (u.event_type, u.side, float(u.confidence)) == ("AskedPrice", "demand", 0.9)
    assert "R-01" in u.rule_codes and u.score > 0 and u.person_id is not None
    ev = (await db.execute(text("SELECT raw_event_id, quote FROM clean.evidence WHERE meaning_unit_id = :u"),
                           {"u": u.id})).one()
    assert ev.raw_event_id == eid and ev.quote == BUY    # trích từ bản ghi thô, không từ model
    assert await states(db, org) == {"clean": 1}
    cur = (await db.execute(text("SELECT count(*) FROM clean.current_scores"))).scalar()
    assert cur >= 1
    nb = (await db.execute(text("SELECT count(*) FROM memory.entries"))).scalar()
    assert nb >= 1                                          # sổ tay người + nhóm được ghi


async def test_fabricated_reference_is_rejected(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY))
    router = FakeRouter(lambda m: {"units": [unit("E99"), unit(refs_in(m)[0], evidence=[refs_in(m)[0], "E42"])],
                                   "noise": []})
    st = await Refinery(sm, redis, router).run(org, "manual")  # type: ignore[arg-type]
    assert (await db.execute(text("SELECT count(*) FROM clean.meaning_units"))).scalar() == 0
    assert st.clean == 0 and st.lowconf == 1              # tin không được kết luận hợp lệ → chờ người xem
    err = (await db.execute(text("SELECT error FROM refinery.runs WHERE id = :i"), {"i": st.run_id})).scalar()
    assert "E99" in err and "E42" in err


async def test_low_confidence_goes_to_lowconf_with_detail(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY))
    router = FakeRouter(lambda m: {"units": [unit(refs_in(m)[0], conf=0.3)], "noise": []})
    st = await Refinery(sm, redis, router).run(org, "manual")  # type: ignore[arg-type]
    assert st.lowconf == 1
    d = (await db.execute(text("SELECT state, detail FROM refinery.event_state"))).one()
    assert d.state == "lowconf" and d.detail["lowconf"][0]["confidence"] == 0.3


async def test_model_down_keeps_events_pending_and_alerts_nothing_lost(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY), msg("Kho còn 20 tấn thép tấm, ai cần inbox"))
    st = await Refinery(sm, redis, FakeRouter(down=True)).run(org, "manual")  # type: ignore[arg-type]
    assert st.status == "failed" and "giữ chờ" in (st.error or "")
    assert await states(db, org) == {"pending": 2}
    run = (await db.execute(text("SELECT status FROM refinery.runs WHERE id = :i"), {"i": st.run_id})).scalar()
    assert run == "failed"
    # Model sống lại → lượt sau xử lý đủ.
    st2 = await Refinery(sm, redis, by_text()).run(org, "manual")  # type: ignore[arg-type]
    assert st2.processed == 2


async def test_parallel_runs_never_claim_same_event(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, *[msg(f"Cần {i} container thép, báo giá giúp") for i in range(1, 31)])
    await db.execute(text("UPDATE refinery.schedule SET batch_size = 10 WHERE org_id = :o"), {"o": org})
    await db.commit()

    async def slow(m):  # type: ignore[no-untyped-def]
        return {"units": [unit(r) for r in refs_in(m)], "noise": []}

    class Slow(FakeRouter):
        async def generate(self, *a, **kw):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.05)
            return await super().generate(*a, **kw)

    router = Slow(lambda m: {"units": [unit(r) for r in refs_in(m)], "noise": []})
    runs = await asyncio.gather(*[Refinery(sm, redis, router).run(org, "manual") for _ in range(4)])  # type: ignore[arg-type]
    assert sum(r.total for r in runs) == 30
    dup = (await db.execute(text("""SELECT raw_event_id FROM clean.evidence GROUP BY 1 HAVING count(*) > 1"""))).all()
    assert dup == []
    assert await states(db, org) == {"clean": 30}


async def schedule(db, org, interval: int, threshold: int) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("UPDATE refinery.schedule SET interval_seconds = :i, count_threshold = :c WHERE org_id = :o"),
                     {"i": interval, "c": threshold, "o": org})
    await db.commit()


async def _scheduler(sm, redis, router) -> Scheduler:  # type: ignore[no-untyped-def]
    return Scheduler(sm, redis, Refinery(sm, redis, router))


async def test_threshold_triggers_before_interval(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await schedule(db, org, 3600, 3)
    sch = await _scheduler(sm, redis, by_text())
    await put(sm, org, msg(BUY), msg("Cần 2 container nữa"))
    assert await sch.tick(wait=True) == []               # 2 < ngưỡng 3, chưa tới chu kỳ
    await put(sm, org, msg("Cần 5 container gấp"))
    [st] = await sch.tick(wait=True)
    assert st.trigger == "threshold" and st.total == 3


async def test_interval_triggers_without_threshold(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await schedule(db, org, 600, 1000)
    sch = await _scheduler(sm, redis, by_text())
    await put(sm, org, msg(BUY))
    assert await sch.tick(wait=True) == []
    await redis.set(tick_key(org), str(time.time() - 601))    # chu kỳ 10 phút đã trôi qua
    [st] = await sch.tick(wait=True)
    assert st.trigger == "schedule" and st.total == 1


async def test_fast_path_and_manual_queue(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await setup_org(db)
    sm = sessionmaker()
    await schedule(db, org, 3600, 1000)
    sch = await _scheduler(sm, redis, by_text())
    # QD 17/10: nhóm «Lắng nghe im lặng» không bao giờ trả lời nên tin tag ở đó không đi đường nhanh — dùng nhóm
    # «Nghe hết, chỉ trả lời khi được tag» (g1 đổi sang tagged_only) cho tin tag agent.
    await db.execute(text("UPDATE core.groups SET listen_mode = 'tagged_only' WHERE external_id = 'g1'"))
    await db.commit()
    await put(sm, org, msg("@bot cần 4 container, báo giá", mentions=True), msg(BUY))
    [fast] = await sch.tick(wait=True)
    assert fast.trigger == "fast" and fast.total == 1        # chỉ tin tag agent đi đường nhanh
    rid = (await db.execute(text("""INSERT INTO refinery.runs (org_id, trigger, status) VALUES (:o, 'manual', 'queued')
                                    RETURNING id"""), {"o": org})).scalar()
    await db.commit()
    [manual] = await sch.tick(wait=True)
    assert manual.run_id == rid and manual.total == 1
    assert await states(db, org) == {"clean": 2}


async def test_ws_events_match_contract(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from gh import realtime

    sent: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    async def capture(_redis, type, data, *, org_id=None):  # type: ignore[no-untyped-def]
        sent.append((type, data))
    monkeypatch.setattr(realtime, "publish", capture)
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, msg(BUY), msg("ok cả nhà"))
    st = await Refinery(sm, redis, by_text()).run(org, "manual")  # type: ignore[arg-type]
    progress = [d for t, d in sent if t == "refinery.progress"]
    assert progress[-1]["status"] == "done" and progress[-1]["processed"] == 2
    [run] = [d for t, d in sent if t == "refinery.run"]      # bản ghi lượt chạy, cùng hình dạng GET /refinery/runs
    assert run["id"] == str(st.run_id) and run["started_at"] and run["clean_count"] == 1 and run["status"] == "done"
    states_ev = [d for t, d in sent if t == "raw.state"]
    assert {d["state"] for d in states_ev} == {"clean", "discarded"}
    [header] = [d for t, d in sent if t == "header"]         # độ tin cậy hôm nay: 1 sạch, 0 thấp, nhiễu không tính
    assert header["data_confidence"] == 1.0


async def test_header_data_confidence_is_clean_over_clean_plus_lowconf_today(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    c = owner_api
    org = await setup_org(db)
    sm = sessionmaker()
    assert (await c.get("/header")).json()["data_confidence"] is None    # chưa sàng lọc gì hôm nay
    await put(sm, org, msg(BUY), msg(BUY + " gấp"), msg(BUY + " nhé"), msg(BUY + " ạ"), msg("ok cả nhà"))

    def reply(m):  # type: ignore[no-untyped-def]
        refs = sorted(refs_in(m))
        body = texts_in(m)
        units = [unit(r, conf=0.3 if i == 0 else 0.9) for i, r in enumerate(x for x in refs if "container" in body[x])]
        return {"units": units, "noise": [r for r in refs if "container" not in body[r]]}
    await Refinery(sm, redis, FakeRouter(reply)).run(org, "manual")  # type: ignore[arg-type]
    assert await states(db, org) == {"clean": 3, "lowconf": 1, "discarded": 1}
    assert (await c.get("/header")).json()["data_confidence"] == 0.75
    # Tin sàng lọc từ hôm qua (theo múi giờ tổ chức) không tính.
    await db.execute(text("""UPDATE refinery.event_state SET updated_at = now() - interval '2 days'
                             WHERE org_id = :o AND state = 'lowconf'"""), {"o": org})
    await db.commit()
    assert (await c.get("/header")).json()["data_confidence"] == 1.0
