"""v0.1.25 — Đợt C1: lọc đầu Hộp thư (gh.refinery.triage): dò trùng, rác, điểm; job idempotent; Jev qua Decider;
API cấu hình (chỉ Owner + Action Log), Hộp thư có dấu + "Ẩn rác & trùng", tool Gen `refinery.summary`, RLS."""

import uuid
from datetime import UTC, datetime, timedelta

import orjson
from sqlalchemy import text

from gh.biz.hooks import HookCtx
from gh.db import sessionmaker
from gh.gen import decider as decmod
from gh.gen import tools as gen_tools
from gh.gen.envelope import DATA_TOOL_NAMES
from gh.refinery import triage
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_rbac_api import login_as
from tests.test_rls import _as_low_priv

LONG = "Bên em cần mua 3 container ván MDF E1 17mm giao Bình Dương trong tháng 10, báo giá giúp em nhé"
LONG_NEAR = "Bên em cần mua 3 container ván MDF E1 18mm giao Bình Dương trong tháng 10, báo giá giúp em nhé"
SPAM = "KHUYẾN MÃI SỐC!!! Nhận quà miễn phí, click ngay http://abc.xyz và www.win.top đăng ký ngay"


# ─── thuần (không DB) ─────────────────────────────────────────────────────────

def test_normalize_and_hashes() -> None:
    assert triage.normalize("  Đơn HÀNG   số 1!! https://x.vn/a ") == "don hang so 1"
    a, b = triage.normalize(LONG), triage.normalize(LONG_NEAR)
    assert triage.text_hash(a) != triage.text_hash(b)
    assert triage.hamming(triage.simhash64(a), triage.simhash64(b)) <= triage.PREFILTER_BITS
    assert triage.jaccard(triage.trigrams(a), triage.trigrams(b)) >= triage.NEAR_JACCARD
    other = triage.normalize("Anh ơi lịch họp chiều nay dời sang 4 giờ nhé, phòng họp tầng 2")
    assert triage.jaccard(triage.trigrams(a), triage.trigrams(other)) < 0.3
    s = triage.simhash64(a)
    assert -(1 << 63) <= s < (1 << 63)


def test_heuristics_spam_and_quality() -> None:
    h = triage.heuristic(SPAM, event_type="OfferedSupply", confidence=0.6, entities={})
    assert h.spam and h.strong_spam and h.quality <= 10 and "link" in (h.spam_reason or "")
    good = triage.heuristic(LONG, event_type="AskedPrice", confidence=0.9, entities={"product": "MDF", "qty": 3})
    assert not good.spam and good.quality >= 80 and "cơ hội" in good.reason
    empty = triage.heuristic("👍👍", event_type="Other", confidence=0.5, entities={})
    assert empty.spam and empty.strong_spam
    meh = triage.heuristic("ok", event_type="Other", confidence=0.3, entities={})
    assert not meh.spam and meh.quality < 50


def test_find_duplicate_rules() -> None:
    t0 = datetime.now(UTC)
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    first = uuid.UUID(int=1)

    def cand(norm: str, subj: uuid.UUID | None) -> triage.Candidate:
        return triage.Candidate(first, t0, subj, triage.text_hash(norm), triage.simhash64(norm), len(norm), None,
                                norm)

    long_n, near_n, short_n = triage.normalize(LONG), triage.normalize(LONG_NEAR), triage.normalize("giá bao nhiêu?")
    kw = {"item_id": uuid.UUID(int=2), "observed_at": t0 + timedelta(minutes=1)}
    # tin dài giống hệt ở người khác → trùng (tin rải)
    assert triage.find_duplicate([cand(long_n, p1)], subject_id=p2, h=triage.text_hash(long_n),
                                 sim=triage.simhash64(long_n), text_len=len(long_n), **kw) == (first, "exact")
    assert triage.find_duplicate([cand(long_n, p1)], subject_id=p2, h=triage.text_hash(near_n),
                                 sim=triage.simhash64(near_n), text_len=len(near_n), norm=near_n,
                                 **kw) == (first, "near")
    # tin ngắn: chỉ trùng khi cùng người
    assert triage.find_duplicate([cand(short_n, p1)], subject_id=p2, h=triage.text_hash(short_n),
                                 sim=triage.simhash64(short_n), text_len=len(short_n), **kw) == (None, None)
    assert triage.find_duplicate([cand(short_n, p1)], subject_id=p1, h=triage.text_hash(short_n),
                                 sim=triage.simhash64(short_n), text_len=len(short_n), **kw) == (first, "exact")
    # mục gốc phải xuất hiện TRƯỚC
    assert triage.find_duplicate([cand(long_n, p1)], subject_id=p2, h=triage.text_hash(long_n),
                                 sim=triage.simhash64(long_n), text_len=len(long_n), item_id=uuid.UUID(int=0),
                                 observed_at=t0 - timedelta(minutes=1)) == (None, None)


def test_gen_tool_registered() -> None:
    assert "refinery.summary" in DATA_TOOL_NAMES
    t = gen_tools.TOOLS["refinery.summary"]
    assert t.path == "/refinery/triage/summary" and t.permissions == ("queue.read",)


# ─── DB ───────────────────────────────────────────────────────────────────────

async def _unit(db, org, text_: str, *, person_id=None, event_type: str = "AskedPrice",  # type: ignore[no-untyped-def]
                confidence: float = 0.9, entities=None, minutes_ago: int = 0) -> uuid.UUID:
    uid: uuid.UUID = (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, person_id, event_type, conclusion, entities,
                                         confidence, run_id)
        VALUES (:o, now() - make_interval(mins => :m), :p, :et, :c, CAST(:e AS jsonb), :conf, core.uuid_v7())
        RETURNING id"""),
        {"o": org, "m": minutes_ago, "p": person_id, "et": event_type, "c": text_,
         "e": orjson.dumps(entities or {}).decode(), "conf": confidence})).scalar_one()
    return uid


async def _person(db, org, name: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    pid: uuid.UUID = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                                               VALUES (:o, core.next_code('PER'), :n) RETURNING id"""),
                                       {"o": org, "n": name})).scalar_one()
    return pid


async def _seed(db) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    p1, p2 = await _person(db, org, "Chị Lan"), await _person(db, org, "Anh Bình")
    ids = {
        "org": org,
        "orig": await _unit(db, org, LONG, person_id=p1, entities={"product": "MDF"}, minutes_ago=50),
        "exact": await _unit(db, org, LONG, person_id=p2, minutes_ago=40),
        "near": await _unit(db, org, LONG_NEAR, person_id=p2, minutes_ago=30),
        "spam": await _unit(db, org, SPAM, person_id=p1, event_type="OfferedSupply", minutes_ago=20),
        "short1": await _unit(db, org, "giá bao nhiêu?", person_id=p1, minutes_ago=10),
        "short2": await _unit(db, org, "giá bao nhiêu?", person_id=p2, minutes_ago=5),
    }
    await db.commit()
    return ids


async def _marks(db) -> dict[uuid.UUID, object]:  # type: ignore[no-untyped-def]
    rows = (await db.execute(text("SELECT * FROM refinery.item_marks"))).all()
    return {r.item_id: r for r in rows}


async def test_sweep_marks_dedupe_spam_and_is_idempotent(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    w = await _seed(db)
    sm = sessionmaker()
    assert await triage.run_org(sm, w["org"]) == 6
    m = await _marks(db)
    assert m[w["orig"]].duplicate_of is None and m[w["orig"]].source == "heuristic"  # type: ignore[attr-defined]
    assert (m[w["exact"]].duplicate_of, m[w["exact"]].duplicate_kind) == (w["orig"], "exact")  # type: ignore[attr-defined]
    assert (m[w["near"]].duplicate_of, m[w["near"]].duplicate_kind) == (w["orig"], "near")  # type: ignore[attr-defined]
    assert m[w["spam"]].is_spam and m[w["spam"]].quality <= 10  # type: ignore[attr-defined]
    assert m[w["short2"]].duplicate_of is None  # type: ignore[attr-defined]  # tin ngắn khác người ≠ trùng
    assert 0 <= m[w["orig"]].quality <= 100  # type: ignore[attr-defined]
    # chạy lại (quét định kỳ / hook lặp) không ghi thêm, không đổi gì
    assert await triage.run_org(sm, w["org"]) == 0
    assert await triage.triage_hook(HookCtx(w["org"], [w["orig"]], None, sm, redis, None, None)) is None
    assert (await db.execute(text("SELECT count(*) FROM refinery.item_marks"))).scalar_one() == 6


async def test_hook_marks_only_given_units_and_respects_disabled(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    w = await _seed(db)
    sm = sessionmaker()
    await triage.triage_hook(HookCtx(w["org"], [w["spam"]], None, sm, redis, None, None))
    assert set(await _marks(db)) == {w["spam"]}
    await triage.save_settings(db, w["org"], {**triage.DEFAULTS, "enabled": False})
    await db.commit()
    assert await triage.run_org(sm, w["org"]) == 0
    assert set(await _marks(db)) == {w["spam"]}


class FakeJev:
    name = "jev"

    def __init__(self, value: str | None):
        self.value, self.calls = value, 0

    async def intent(self, question: str) -> decmod.Decision | None:
        return None

    async def next_target(self, question: str, candidates: list) -> decmod.Decision | None:  # type: ignore[type-arg]
        return None

    async def classify(self, question: str, options: dict[str, str], context: str) -> decmod.Decision | None:
        self.calls += 1
        assert set(options) == set(triage.JEV_OPTIONS)
        return None if self.value is None else decmod.Decision(self.value, 0.9, 12, "jev")


async def test_jev_decider_scores_and_falls_back(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    w = await _seed(db)
    jev = FakeJev("high")
    assert await triage.mark_units(db, w["org"], [w["orig"]], decider=jev) == 1
    r = (await _marks(db))[w["orig"]]
    assert r.source == "jev" and r.latency_ms == 12 and "Jev: giá trị cao" in r.reason  # type: ignore[attr-defined]
    assert r.quality == round(0.6 * 85 + 0.4 * r.heuristic_quality)  # type: ignore[attr-defined]
    # Jev lỗi liên tục → quy tắc, và ngừng gọi sau JEV_MAX_FAILS lần
    broken = FakeJev(None)
    n = await triage.mark_units(db, w["org"], None, decider=broken)
    assert n == 5 and broken.calls <= triage.JEV_MAX_FAILS + triage.JEV_PARALLEL
    assert {x.source for k, x in (await _marks(db)).items() if k != w["orig"]} == {"heuristic"}  # type: ignore[attr-defined]
    # LlmDecider (chưa cấu hình Jev) → không gọi gì, dùng quy tắc
    assert await decmod.LlmDecider().classify("x", {"a": "b"}, "") is None


async def test_api_settings_owner_only_and_logged(app, db, client, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/refinery/triage/settings")
    assert r.status_code == 200 and r.json() == triage.DEFAULTS
    r = await owner_api.send("PATCH", "/refinery/triage/settings", {"min_score": 45, "use_jev": False})
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": True, "min_score": 45, "use_jev": False, "prefilter": True}
    row = (await db.execute(text("""SELECT detail FROM ops.action_log
                                    WHERE action = 'refinery.triage_settings_changed'"""))).scalar_one()
    assert row["after"] == {"min_score": 45, "use_jev": False} and row["before"]["min_score"] == 30
    r = await owner_api.send("PATCH", "/refinery/triage/settings", {"min_score": 101})
    assert r.status_code == 422
    manager = await login_as(client, db, "manager")
    assert (await manager.get("/refinery/triage/settings")).status_code == 200
    assert (await manager.send("PATCH", "/refinery/triage/settings", {"enabled": False})).status_code == 403


async def test_inbox_payload_hide_junk_and_summary(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    w = await _seed(db)
    await triage.run_org(sessionmaker(), w["org"])
    page = (await owner_api.get("/inbox")).json()
    by_id = {i["id"]: i for i in page["items"]}
    assert page["triage"] == {"enabled": True, "min_score": 30, "hidden": 0}
    assert by_id[str(w["exact"])]["triage"]["duplicate_of"] == str(w["orig"])
    assert by_id[str(w["spam"])]["triage"]["spam"] is True
    hidden = (await owner_api.get("/inbox", params={"hide_junk": "true"})).json()
    ids = {i["id"] for i in hidden["items"]}
    assert str(w["orig"]) in ids and not ({str(w["exact"]), str(w["near"]), str(w["spam"])} & ids)
    assert hidden["triage"]["hidden"] >= 3 and hidden["total"] == len(page["items"]) - hidden["triage"]["hidden"]
    detail = (await owner_api.get(f"/inbox/{w['spam']}")).json()
    assert detail["triage"]["spam"] is True

    s = (await owner_api.get("/refinery/triage/summary")).json()
    assert s["total"] == 6 and s["duplicates"] == 2 and s["exact_duplicates"] == 1 and s["near_duplicates"] == 1
    assert s["spam"] == 1 and s["pending"] == 0 and s["jev"]["count"] == 0

    # tắt lọc đầu → Hộp thư không còn dấu, hide_junk bị bỏ qua
    await owner_api.send("PATCH", "/refinery/triage/settings", {"enabled": False})
    off = (await owner_api.get("/inbox", params={"hide_junk": "true"})).json()
    assert off["triage"]["enabled"] is False and all(i["triage"] is None for i in off["items"])
    assert len(off["items"]) == len(page["items"])


async def test_item_marks_rls_isolates_orgs(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org_a = await org_id(db)
    org_b = (await db.execute(text(
        "INSERT INTO core.organizations (name) VALUES ('Tổ chức B (RLS lọc đầu)') RETURNING id"))).scalar_one()
    for org in (org_a, org_b):
        await db.execute(text("""
            INSERT INTO refinery.item_marks (org_id, item_type, item_id, observed_at, text_hash, simhash, text_len,
                                             quality, source, heuristic_quality, heuristic_spam)
            VALUES (:o, 'unit', core.uuid_v7(), now(), '\\x00', 0, 1, 50, 'heuristic', 50, false)"""), {"o": org})
    await _as_low_priv(db, "refinery.item_marks")
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_a)})
    orgs = (await db.execute(text("SELECT org_id FROM refinery.item_marks"))).scalars().all()
    assert orgs == [org_a]
