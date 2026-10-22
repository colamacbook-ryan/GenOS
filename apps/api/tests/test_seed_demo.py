"""PLAN §5.1: seed dữ liệu mẫu đi qua đúng luồng raw → refinery → clean; chứng cứ lần được về đúng tin thô;
xoá sạch; chạy lại không lỗi/không trùng."""

from sqlalchemy import text

from gh.db import sessionmaker
from gh.seed_demo import (
    ALLOW_ENV,
    EXIT_REFUSED,
    MSG_HAS_REAL_DATA,
    MSG_NOT_ALLOWED,
    MSG_PREFIX,
    NS,
    clear_demo,
    run_cli,
    seed_demo,
)
from tests.conftest import Api


async def _counts(db, org):  # type: ignore[no-untyped-def]
    async def n(sql: str) -> int:
        return (await db.execute(text(sql), {"o": org})).scalar_one()  # type: ignore[no-any-return]

    return {
        "raw": await n("SELECT count(*) FROM raw.events e JOIN core.channels c ON c.id = e.channel_id "
                       "WHERE c.org_id = :o"),
        "units": await n("SELECT count(*) FROM clean.meaning_units WHERE org_id = :o"),
        "opps": await n("SELECT count(*) FROM biz.opportunities WHERE org_id = :o"),
        "signals": await n("SELECT count(*) FROM biz.market_signals WHERE org_id = :o"),
        "alerts": await n("SELECT count(*) FROM biz.alerts WHERE org_id = :o"),
        "agents": await n("SELECT count(*) FROM agent.identities WHERE org_id = :o"),
    }


async def test_seed_produces_evidence_that_traces_back_to_raw_message(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    out = await seed_demo(sessionmaker(), redis)
    org = out["org_id"]

    # Cơ hội MDF của Nguyễn Văn Mẫu (OPP-1842 thiết kế) — bấm "vì sao" phải lần được về đúng tin thô.
    opp = (await db.execute(text("""
        SELECT o.value_vnd, p.display_name, o.need FROM biz.opportunities o JOIN core.persons p ON p.id = o.person_id
        WHERE o.org_id = :o AND p.display_name = 'Nguyễn Văn Mẫu'"""), {"o": org})).one()
    assert opp.display_name == "Nguyễn Văn Mẫu" and opp.value_vnd == 1_200_000_000

    unit = (await db.execute(text("""
        SELECT mu.id FROM clean.meaning_units mu JOIN core.persons p ON p.id = mu.person_id
        WHERE mu.org_id = :o AND p.display_name = 'Nguyễn Văn Mẫu' AND mu.side = 'demand'"""), {"o": org})).one()
    ev = (await db.execute(text("SELECT raw_event_id, quote FROM clean.evidence WHERE meaning_unit_id = :u"),
                           {"u": unit.id})).one()
    raw_text = (await db.execute(text("SELECT body_text FROM raw.events WHERE id = :i"), {"i": ev.raw_event_id})
                ).scalar_one()
    assert raw_text == ev.quote
    assert "3 cont" in raw_text and "1.2 tỷ" in raw_text

    # Cảnh báo bất mãn thật (R-03 tất định trên 3 tin liên tiếp không ai trả lời) — chứng cứ trỏ về raw + đơn
    # vị ý nghĩa, khớp meaningItems ALR-0233 thiết kế.
    alert = (await db.execute(text("""
        SELECT a.title, a.priority, a.evidence, p.display_name FROM biz.alerts a
        JOIN core.persons p ON p.id = a.subject_id
        WHERE a.org_id = :o AND a.alert_type = 'repeated_complaint'"""), {"o": org})).one()
    assert alert.priority == "P1" and alert.display_name == "Đặng Văn Mẫu Bảy"
    raw_ref = next(e for e in alert.evidence if e["type"] == "raw")
    raw_text2 = (await db.execute(text("SELECT body_text FROM raw.events WHERE id = :i"), {"i": raw_ref["id"]})
                ).scalar_one()
    assert "không ai trả lời" in raw_text2

    # Ghép cầu-cung (biz.matches) thật sự chấm điểm, không phải số bịa.
    assert out["matches"] >= 1

    # Tín hiệu ứng viên qua R-05.
    candidate = (await db.execute(text("SELECT person_type FROM core.persons WHERE display_name = 'Bùi Văn Mẫu Tám'")
                                  )).scalar_one()
    assert candidate == "candidate"

    c = await _counts(db, org)
    assert c["units"] > 0 and c["opps"] > 0 and c["signals"] > 0 and c["alerts"] > 0 and c["agents"] == 4


async def test_seed_is_idempotent(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    out1 = await seed_demo(sm, redis)
    org = out1["org_id"]
    c1 = await _counts(db, org)
    out2 = await seed_demo(sm, redis)
    assert out2["org_id"] == org
    c2 = await _counts(db, org)
    assert c1 == c2   # chạy lại không tạo trùng raw, không sinh thêm đơn vị ý nghĩa/cơ hội/cảnh báo


async def test_clear_removes_derived_data_but_keeps_raw_events(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    out = await seed_demo(sm, redis)
    org = out["org_id"]
    before = await _counts(db, org)
    assert before["raw"] > 0 and before["units"] > 0

    cleared = await clear_demo(sm, redis)
    assert cleared["org_id"] == org
    after = await _counts(db, org)
    assert after["units"] == 0 and after["opps"] == 0 and after["signals"] == 0 and after["alerts"] == 0
    assert after["agents"] == 0
    # raw.events KHÔNG bị đụng tới (bất biến — trigger raw_events_append_only).
    assert after["raw"] == before["raw"]
    demo_raw = (await db.execute(text("""SELECT count(*) FROM raw.events e JOIN core.channels c
                                        ON c.id = e.channel_id JOIN raw.event_keys k ON k.event_id = e.id
                                        WHERE c.org_id = :o AND k.external_msg_id LIKE :p"""),
                                 {"o": org, "p": f"{MSG_PREFIX}%"})).scalar_one()
    assert demo_raw == before["raw"] > 0

    # Tin thô mẫu quay về 'pending' để seed lại phục dựng đúng.
    pending = (await db.execute(text("""SELECT count(*) FROM refinery.event_state s
                                       JOIN raw.event_keys k ON k.event_id = s.event_id
                                       WHERE s.org_id = :o AND k.external_msg_id LIKE :p AND s.state = 'pending'"""),
                                {"o": org, "p": f"{MSG_PREFIX}%"})).scalar_one()
    assert pending == demo_raw


async def test_clear_then_reseed_twice_is_clean(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    out = await seed_demo(sm, redis)
    org = out["org_id"]
    await clear_demo(sm, redis)
    await seed_demo(sm, redis)
    c1 = await _counts(db, org)
    await seed_demo(sm, redis)   # gọi lại lần hai liên tiếp — không lỗi, không trùng
    c2 = await _counts(db, org)
    assert c1 == c2 and c1["units"] > 0 and c1["opps"] > 0

    # Không có gì khác của tổ chức bị đụng tới ngoài không gian tên `seed-demo-*`.
    stray = (await db.execute(text("""SELECT count(*) FROM core.person_identities
                                     WHERE external_id NOT LIKE :p AND external_id NOT LIKE 'seed-demo-probe'"""),
                              {"p": f"{NS}-%"})).scalar_one()
    assert stray == 0   # tổ chức mới tinh trong test — không có dữ liệu thật nào để so sánh, chỉ canh không lỗi


# ─── v0.1.57: khoá an toàn — dữ liệu mẫu (bịa) không được làm rối dữ liệu thật của Owner ──────────────────────────

async def _persons(db) -> int:  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT count(*) FROM core.persons"))).scalar_one()  # type: ignore[no-any-return]


async def test_seed_cli_refuses_without_flag_or_env(app, db, redis, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = await run_cli("seed", force=False, sm=sessionmaker(), redis=redis, env={})
    assert rc == EXIT_REFUSED == 2
    err = capsys.readouterr().err
    assert MSG_NOT_ALLOWED in err and "Chi tiết kỹ thuật:" in err and ALLOW_ENV in err
    assert await _persons(db) == 0   # không nạp gì

    # Giá trị khác "1" cũng không tính là cho phép.
    rc = await run_cli("seed", force=False, sm=sessionmaker(), redis=redis, env={ALLOW_ENV: "true"})
    assert rc == EXIT_REFUSED
    assert await _persons(db) == 0


async def test_seed_cli_runs_with_flag_on_empty_db_and_again_on_seeded_db(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    assert await run_cli("seed", force=True, sm=sm, redis=redis, env={}) == 0          # cờ --force
    assert await _persons(db) > 0
    # Biến môi trường cũng đủ; và dữ liệu của chính bộ mẫu KHÔNG bị tính là "dữ liệu thật" (chạy lại được).
    assert await run_cli("seed", force=False, sm=sm, redis=redis, env={ALLOW_ENV: "1"}) == 0


async def test_seed_cli_refuses_when_db_has_real_data_even_with_flag(owner_api: Api, db, redis, capsys) -> None:  # type: ignore[no-untyped-def]
    # owner_api: trình thiết lập đã tạo Owner thật ⇒ có người dùng ngoài bootstrap.
    for force, env in ((True, {}), (False, {ALLOW_ENV: "1"})):
        rc = await run_cli("seed", force=force, sm=sessionmaker(), redis=redis, env=env)
        assert rc == EXIT_REFUSED
        err = capsys.readouterr().err
        assert MSG_HAS_REAL_DATA in err and "Chi tiết kỹ thuật:" in err and "core.users" in err
    assert await _persons(db) == 0   # không nạp gì vào DB của Owner
