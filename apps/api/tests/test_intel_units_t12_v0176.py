# ruff: noqa: E501
"""v0.1.76 (gói T12) — đơn vị model (`clean.meaning_units`, đường brain-out/1, `event_type = 'GroupUnit'`) ⇒ `biz.alerts` cho MỌI loại.

Lỗi chặn phát hành: trước T12 chỉ 2 loại (`generic.UNIT_KINDS`) được chiếu; lừa đảo có cờ `credentials_entered` mà luật (a) hụt rơi im lặng,
lời hứa/khiếu nại/câu hỏi ở chế độ brain không bao giờ thành tín hiệu, lời hứa ở cài mới (chưa `staff_of`) không bao giờ ra.
Test e2e trên PostgreSQL thật: đơn vị (đúng cột runner ghi, kể cả `entities.v` đã kiểm) ⇒ detector chủ sở hữu ⇒ `signals.upsert` ⇒ mức."""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta
from typing import Any

import orjson
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import fraud as F
from gh.biz.intel.detectors import promises, questions
from gh.biz.intel.detectors import units as U
from gh.biz.mem import entities, facts
from tests.mem_helpers import mk_org, mkfact
from tests.test_owner_profile_support import mk_person, mk_raw, mk_world

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=levels.VN)          # T6 14:00 giờ VN (ngoài giờ yên lặng)


# ─── bất biến: mọi loại có chủ sở hữu ────────────────────────────────────────

def test_t12_every_kind_has_a_unit_projector_or_declared_owner() -> None:
    """Thêm `kind` vào `levels.KINDS` mà quên khai chủ chiếu đơn vị ⇒ đỏ. Chủ khai báo phải là hàm có thật."""
    assert set(U.OWNERS) == set(levels.KINDS)
    for kind, owner in U.OWNERS.items():
        if owner == "units":
            assert kind in U.UNIT_KINDS
            continue
        mod, fn = owner.split(".")
        assert callable(getattr(importlib.import_module(f"gh.biz.intel.detectors.{mod}"), fn)), kind
    from gh.biz.intel.detectors import generic
    assert set(generic.UNIT_KINDS) == {k for k, v in U.OWNERS.items() if v == "generic.project_units"}


def test_t12_units_job_registered() -> None:
    from gh.biz.intel import jobs

    assert "intel_units" in {fn.__name__ for fn, _ in jobs.JOBS}


def test_t12_runner_persists_verified_fields() -> None:
    """runner lưu phần ĐÃ KIỂM (hạn, mốc, trạng thái, giao dịch, vai người) ở `entities.v` — bộ chiếu không đọc lời model thô."""
    from types import SimpleNamespace

    from gh.refinery.runner import verified_fields

    vu = SimpleNamespace(msg_at=NOW, event_at=NOW - timedelta(hours=1), event_time_unparsed=False, deadline_at=NOW + timedelta(hours=3),
                         deadline_unparsed=False, ongoing=True, money_vnd=5_000_000, status_by_person_id=None, status_at=None,
                         txn_state="pending", txn_at=NOW, delivery_form="direct")
    u = SimpleNamespace(status_hint="dang_mo", condition=SimpleNamespace(met="unknown"))
    v = verified_fields(vu, u, [{"role": "nguoi_hua", "entity_id": None, "surface": "em"}])
    assert v["deadline_at"] == (NOW + timedelta(hours=3)).isoformat() and v["txn_state"] == "pending" and v["ongoing"] is True
    assert v["people"][0]["role"] == "nguoi_hua" and orjson.dumps(v)


# ─── dựng dữ liệu ─────────────────────────────────────────────────────────────

async def _install(db: AsyncSession, org: uuid.UUID) -> None:
    s = {"installed_at": "2026-09-01T00:00:00+07:00", "m1_passed_gates": list(levels.REGULAR_GATES)}
    await db.execute(text("UPDATE core.organizations SET settings = settings || jsonb_build_object('intel', CAST(:s AS jsonb)) WHERE id = :o"),
                     {"s": orjson.dumps(s).decode(), "o": org})
    await db.commit()


async def _world(db: AsyncSession, *, size: int = 20, business: bool = False) -> Any:
    w = await mk_world(db, await mk_org(db))
    await _install(db, w.org)
    await db.execute(text("UPDATE core.groups SET member_count = :n WHERE id = :g"), {"n": size, "g": w.group})
    if business:
        await db.execute(text("INSERT INTO mem.group_contexts (org_id, group_id, context, source, is_primary) VALUES (:o, :g, 'kinh_doanh', 'owner', true)"),
                         {"o": w.org, "g": w.group})
    await db.commit()
    return w


async def _person(db: AsyncSession, w: Any, name: str, *, ptype: str | None = None) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    pid, iid = await mk_person(db, w.org, w.channel, name)
    if ptype:
        await db.execute(text("UPDATE core.persons SET person_type = :t WHERE id = :p"), {"t": ptype, "p": pid})
    ent = (await entities.ensure_person(db, w.org, pid)).id
    await db.commit()
    return pid, iid, ent


async def _unit(db: AsyncSession, w: Any, raw: uuid.UUID, kind: str, *, person: uuid.UUID | None, flags: dict[str, Any] | None = None,
                people: list[tuple[str, uuid.UUID | None]] = (), v: dict[str, Any] | None = None, gate: str | None = None,
                harm: str = "", at: datetime = NOW - timedelta(minutes=20), conclusion: str = "đơn vị thử") -> uuid.UUID:
    """Đúng các cột runner ghi cho một đơn vị brain-out/1 (event_type 'GroupUnit', entities.v đã kiểm, bằng chứng verified, mention)."""
    vv = {"people": [{"role": r, "entity_id": str(e) if e else None, "surface": "người"} for r, e in people], "status_hint": "dang_mo",
          "msg_at": at.isoformat(), "event_at": at.isoformat(), "ongoing": bool((flags or {}).get("ongoing")), **(v or {})}
    uid = (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, conclusion, entities, confidence, run_id,
                                         kind_hint, flags, gate_candidate, harm)
        VALUES (:o, :t, :g, :p, 'GroupUnit', :c, CAST(:e AS jsonb), 0.8, :r, :k, CAST(:f AS jsonb), :gc, CAST(:h AS jsonb)) RETURNING id"""),
                             {"o": w.org, "t": at, "g": w.group, "p": person, "c": conclusion,
                              "e": orjson.dumps({"unit_ref": "u1", "event_ref": "e1", "kind": kind, "v": vv}).decode(), "r": uuid.uuid4(),
                              "k": kind, "f": orjson.dumps(flags or {}).decode(), "gc": gate, "h": orjson.dumps({"what": harm}).decode()})).scalar_one()
    rec = (await db.execute(text("SELECT received_at FROM raw.events WHERE id = :i"), {"i": raw})).scalar_one()
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, quote_status,
                                                          source_field, span_start, span_end)
                             VALUES (:u, :t, :r, :rr, 'trích', 'verified', 'body', 0, 5)"""), {"u": uid, "t": at, "r": raw, "rr": rec})
    for _, e in people:
        if e is not None:
            await db.execute(text("""INSERT INTO mem.mentions (org_id, meaning_unit_id, meaning_observed_at, raw_event_id, surface, entity_id, method, status)
                                     VALUES (:o, :u, :t, :r, 'người', :e, 'llm', 'linked')"""), {"o": w.org, "u": uid, "t": at, "r": raw, "e": e})
    await db.commit()
    return uid  # type: ignore[no-any-return]


async def _rows(db: AsyncSession, org: uuid.UUID, kind: str | None = None) -> list[Any]:
    return list((await db.execute(text("""SELECT id, kind, level, gate, candidate, delivery, detector, case_key, level_input, facts, status
                                         FROM biz.alerts WHERE org_id = :o AND (CAST(:k AS text) IS NULL OR kind = :k) ORDER BY created_at"""),
                                  {"o": org, "k": kind})).all())


async def _staff_of(db: AsyncSession, w: Any, ent: uuid.UUID) -> None:
    await facts.assert_fact(db, mkfact(w.org, ent, predicate="staff_of", object_entity=w.owner, at=NOW - timedelta(days=9)))
    await db.commit()


async def _customer_of(db: AsyncSession, w: Any, ent: uuid.UUID) -> None:
    await facts.assert_fact(db, mkfact(w.org, ent, predicate="customer_of", object_entity=w.owner, at=NOW - timedelta(days=9)))
    await db.commit()


# ─── lừa đảo ─────────────────────────────────────────────────────────────────

CRED_BODY = "chị ơi em làm theo hướng dẫn của bên đó xong xuôi hết rồi, giờ sao ạ"


async def test_t12_credentials_entered_caught_only_by_model_is_red_push_now(db: AsyncSession) -> None:
    """Luật (a) không thấy gì (fraud.scan rỗng) — chỉ model nêu `credentials_entered` ⇒ 🔴 `lua_dao_dang_dien_ra` đẩy ngay; chạy lại không đổi."""
    assert not F.scan(CRED_BODY).any
    w = await _world(db)
    pid, iid, ent = await _person(db, w, "Ngọc")
    await _staff_of(db, w, ent)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body=CRED_BODY, group=w.group, at=NOW - timedelta(minutes=20))
    uid = await _unit(db, w, raw, levels.FR, person=pid, flags={"credentials_entered": True, "ongoing": True},
                      people=[("nguoi_bi_anh_huong", ent)], gate=levels.G1, harm="có thể bị rút tiền trong tài khoản đại lý")
    out = await U.project(db, w.org, NOW)
    await db.commit()
    assert [(s.level, s.gate, s.delivery) for s in out] == [("red", levels.G1, "push_now")]
    r = (await _rows(db, w.org))[0]
    assert r.detector == "units" and r.facts["unit_id"] == str(uid) and r.level_input["victims"][0]["ref"] == f"u_{pid}"
    assert await U.project(db, w.org, NOW) == []
    assert len(await _rows(db, w.org)) == 1


async def test_t12_fraud_unit_merges_into_fast_path_case_one_red(db: AsyncSession) -> None:
    """Một vụ một 🔴: tín hiệu luật cứng (raw_tail, khoá raw|) đã đẩy ⇒ đơn vị model cùng tin gốc GỘP vào cùng dòng, không đẩy lần hai."""
    w = await _world(db)
    pid, iid, ent = await _person(db, w, "Ngọc")
    await _staff_of(db, w, ent)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="Em lỡ nhập OTP rồi anh ơi", group=w.group, at=NOW - timedelta(minutes=20))
    li = levels.SignalIn(kind=levels.FR, msg_at=NOW - timedelta(minutes=20), hard_rule_hit=["credentials_entered"], flags=levels.Flags(credentials_entered=True),
                         source_class="rule", confidence_exist=1.0, confidence_content=1.0, victims=[levels.PersonRel(ref=f"u_{pid}", team=True, thread_member=True)],
                         thread=levels.Thread(type="group", size=20, agent_in_thread=True), impact_base=3)
    ev = signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=10)
    first = await signals.upsert(db, w.org, signals.UpsertIn(kind=levels.FR, title="t", detector="raw_tail", level_input=li, evidence=[ev],
                                                             case_key=levels.case_key(levels.FR, at=NOW, raw_event_id=str(raw))), NOW)
    assert (first.level, first.delivery) == ("red", "push_now")
    await db.commit()
    await _unit(db, w, raw, levels.FR, person=pid, flags={"credentials_entered": True}, people=[("nguoi_bi_anh_huong", ent)], harm="mất tiền")
    out = await U.project(db, w.org, NOW)
    await db.commit()
    assert [s.id for s in out] == [first.id] and out[0].delivery == "merge"
    assert len(await _rows(db, w.org)) == 1


async def test_t12_impersonation_change_delivery_address_reaches_alerts(db: AsyncSession) -> None:
    """Mạo danh Sếp đổi địa chỉ giao hàng (không tiền/OTP): `impersonated` từ `people[role=nguoi_bi_mao_danh]` ⇒ khoá vụ `imp|`, 🟠 (không mất)."""
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Anh Cơ (giả)")
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="anh đổi địa chỉ giao hàng sang kho mới nhé", group=w.group, at=NOW - timedelta(minutes=20))
    await _unit(db, w, raw, levels.FR, person=pid, people=[("nguoi_bi_mao_danh", w.owner)], harm="hàng giao nhầm chỗ, mất hàng")
    out = await U.project(db, w.org, NOW)
    await db.commit()
    assert len(out) == 1 and out[0].level == "amber"
    r = (await _rows(db, w.org))[0]
    assert r.case_key.startswith("rui_ro_lua_dao:imp|") and r.level_input["impersonated"] and r.level_input["impersonated"][0]["is_owner"]


async def test_t12_fraud_without_hard_rule_runs_d2_before_write(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không luật cứng ⇒ (d2) TRƯỚC khi ghi: (d2) không đồng ý ⇒ không bao giờ đẩy 🔴; (d2) đồng ý ⇒ 🔴."""
    from gh.refinery.extract import ModelReply

    async def allowed(*a: Any, **k: Any) -> bool:
        return True

    monkeypatch.setattr(F, "_d2_allowed", allowed)
    for verdict, want in (("disagree", "amber"), ("agree", "red")):
        w = await _world(db)
        pid, iid, ent = await _person(db, w, "Ngọc")
        await _staff_of(db, w, ent)
        raw = await mk_raw(db, w.org, w.channel, identity=iid, body=CRED_BODY, group=w.group, at=NOW - timedelta(minutes=20))
        await _unit(db, w, raw, levels.FR, person=pid, flags={"credentials_entered": True}, people=[("nguoi_bi_anh_huong", ent)], harm="mất tiền")
        calls: list[int] = []

        async def call(messages: Any, verdict: str = verdict, calls: list[int] = calls) -> Any:
            calls.append(1)
            return ModelReply(text=orjson.dumps({"gate": None, "flags_confirmed": [], "verdict": verdict, "reason": "x"}).decode(), model="fake")

        out = await U.project(db, w.org, NOW, call=call)
        await db.commit()
        assert calls and len(out) == 1 and out[0].level == want, verdict
        r = (await _rows(db, w.org))[0]
        assert r.facts["d2"]["verdict"] == verdict
        if want != "red":
            assert (await db.execute(text("SELECT pushed_at FROM biz.alerts WHERE id = :i"), {"i": r.id})).scalar_one() is None


# ─── lời hứa ─────────────────────────────────────────────────────────────────

async def test_t12_promise_to_customer_about_to_break_is_red_gate4(db: AsyncSession) -> None:
    w = await _world(db)
    spid, siid, sent = await _person(db, w, "NV Hà")
    cpid, _, cent = await _person(db, w, "Chị Tư")
    await _staff_of(db, w, sent)
    await _customer_of(db, w, cent)
    raw = await mk_raw(db, w.org, w.channel, identity=siid, body="dạ 5h chiều nay em giao đủ 200 bao cho chị", group=w.group, at=NOW - timedelta(hours=2))
    uid = await _unit(db, w, raw, "nghia_vu_loi_hua", person=spid, people=[("nguoi_hua", sent), ("nguoi_duoc_hua", cent)],
                      v={"deadline_at": (NOW + timedelta(hours=3)).isoformat()}, gate=levels.G4, at=NOW - timedelta(hours=2),
                      conclusion="NV hứa giao 200 bao cho khách chiều nay")
    out = await promises.project_units(db, w.org, NOW)
    await db.commit()
    assert [(s.level, s.gate) for s in out] == [("red", levels.G4)]
    p = (await db.execute(text("SELECT to_customer, promiser_person_id, signal_id FROM biz.promises WHERE org_id = :o AND meaning_unit_id = :u"),
                          {"o": w.org, "u": uid})).one()
    assert p.to_customer and p.promiser_person_id == spid and p.signal_id == out[0].id
    assert await promises.project_units(db, w.org, NOW) == []


async def test_t12_promises_cold_start_owner_promise_in_confirmed_business_group(db: AsyncSession) -> None:
    """Cài mới: Hồ sơ chưa có `customer_of`/`staff_of`. Sếp (tin ĐI) hứa với người trong nhóm đã xác nhận `kinh_doanh` ⇒ bên nhận là khách
    (luật khởi động lạnh) ⇒ cổng 4. Nhóm CHƯA xác nhận ⇒ không suy khách (không cổng 4)."""
    for business, want_gate in ((True, levels.G4), (False, None)):
        w = await _world(db, business=business)
        cpid, _, cent = await _person(db, w, "Anh Bảy")
        raw = await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body="ok chiều nay anh gửi hàng cho em nhé", group=w.group,
                           direction="outbound", at=NOW - timedelta(hours=1))
        await _unit(db, w, raw, "nghia_vu_loi_hua", person=w.owner_person, people=[("nguoi_hua", w.owner), ("nguoi_duoc_hua", cent)],
                    v={"deadline_at": (NOW + timedelta(hours=2)).isoformat()}, at=NOW - timedelta(hours=1))
        out = await promises.project_units(db, w.org, NOW)
        await db.commit()
        assert len(out) == 1, business
        r = (await _rows(db, w.org))[0]
        assert r.level_input["promise"]["to_customer"] is business and (r.gate == want_gate or (want_gate is None and r.gate != levels.G4))
        assert r.facts["cold_start"] is business


async def test_t12_promises_cold_start_never_guesses_both_sides(db: AsyncSession) -> None:
    """Hai phía cùng mù (người lạ hứa với người lạ, cả hai tin ĐẾN): không suy ⇒ không tín hiệu (khách hứa chuyển khoản ≠ lời hứa của Sếp).
    Bên nhận CHẮC là khách (person_type) + chưa có `staff_of` nào ⇒ người hứa (không phải khách) là phía Sếp."""
    w = await _world(db, business=True)
    apid, aiid, aent = await _person(db, w, "Người A")
    _, _, bent = await _person(db, w, "Người B")
    raw = await mk_raw(db, w.org, w.channel, identity=aiid, body="mai em chuyển khoản nha", group=w.group, at=NOW - timedelta(hours=1))
    await _unit(db, w, raw, "nghia_vu_loi_hua", person=apid, people=[("nguoi_hua", aent), ("nguoi_duoc_hua", bent)],
                v={"deadline_at": (NOW + timedelta(hours=20)).isoformat()}, at=NOW - timedelta(hours=1))
    assert await promises.project_units(db, w.org, NOW) == []
    _, _, kent = await _person(db, w, "Khách C", ptype="customer")
    raw2 = await mk_raw(db, w.org, w.channel, identity=aiid, body="dạ 4h em giao cho chị", group=w.group, at=NOW - timedelta(hours=1))
    await _unit(db, w, raw2, "nghia_vu_loi_hua", person=apid, people=[("nguoi_hua", aent), ("nguoi_duoc_hua", kent)],
                v={"deadline_at": (NOW + timedelta(hours=2)).isoformat()}, at=NOW - timedelta(hours=1))
    out = await promises.project_units(db, w.org, NOW)
    assert len(out) == 1 and out[0].gate == levels.G4


async def test_t12_promise_fact_path_and_unit_path_never_double(db: AsyncSession) -> None:
    """Đơn vị có sự thật `promised_to`: đường đơn vị làm chủ (mang `facts.fact_id`) ⇒ đường sự thật không đẻ thẻ thứ hai."""
    w = await _world(db)
    spid, siid, sent = await _person(db, w, "NV Hà")
    _, _, cent = await _person(db, w, "Chị Tư", ptype="customer")
    raw = await mk_raw(db, w.org, w.channel, identity=siid, body="5h em giao", group=w.group, at=NOW - timedelta(hours=2))
    uid = await _unit(db, w, raw, "nghia_vu_loi_hua", person=spid, people=[("nguoi_hua", sent), ("nguoi_duoc_hua", cent)],
                      v={"deadline_at": (NOW + timedelta(hours=3)).isoformat()}, at=NOW - timedelta(hours=2))
    await facts.assert_fact(db, mkfact(w.org, sent, {"text": "giao 200 bao", "to_entity_id": str(cent), "due_at": (NOW + timedelta(hours=3)).isoformat()},
                                       NOW - timedelta(hours=2), predicate="promised_to", raw=raw))
    fid = (await db.execute(text("SELECT id FROM mem.facts WHERE org_id = :o AND predicate = 'promised_to'"), {"o": w.org})).scalar_one()
    await db.execute(text("UPDATE mem.fact_evidence SET meaning_unit_id = :u WHERE fact_id = :f"), {"u": uid, "f": fid})
    await _staff_of(db, w, sent)                                       # đường sự thật cũng đủ điều kiện ⇒ ai tới trước làm chủ
    await db.commit()
    out = await promises.project_units(db, w.org, NOW)
    await db.commit()
    assert len(out) == 1
    await promises.project(db, w.org, NOW)
    await db.commit()
    rows = await _rows(db, w.org, "nghia_vu_loi_hua")
    assert len(rows) == 1 and rows[0].facts["fact_id"] == str(fid)


# ─── khiếu nại / câu hỏi ─────────────────────────────────────────────────────

async def test_t12_customer_public_complaint_threat_routes_to_conflicts_gate3(db: AsyncSession) -> None:
    w = await _world(db)
    cpid, ciid, _ = await _person(db, w, "Anh Bình", ptype="customer")
    raw = await mk_raw(db, w.org, w.channel, identity=ciid, body="giao trễ lần thứ ba rồi, thôi lần sau lấy chỗ khác", group=w.group,
                       at=NOW - timedelta(hours=1))
    uid = await _unit(db, w, raw, "xung_dot_khieu_nai", person=cpid, flags={"public": True, "said_will_leave": True},
                      people=[("nguoi_gui", None)], gate=levels.G3, at=NOW - timedelta(hours=1))
    out = await U.project(db, w.org, NOW)
    await db.commit()
    got = {s.kind: (s.level, s.gate, s.delivery) for s in out}
    assert got["xung_dot_khieu_nai"] == ("red", levels.G3, "push_now") and "quan_he_nong_nguoi" in got and got["quan_he_nong_nguoi"][0] != "red"
    rows = await _rows(db, w.org, "xung_dot_khieu_nai")
    assert rows[0].detector == "conflicts" and rows[0].facts["unit_id"] == str(uid)
    assert await U.project(db, w.org, NOW) == []


async def test_t12_non_customer_complaint_still_projected_without_gate3(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Người trong nhóm")
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="nói vậy là không được", group=w.group, at=NOW - timedelta(hours=1))
    await _unit(db, w, raw, "xung_dot_khieu_nai", person=pid, flags={"public": True}, at=NOW - timedelta(hours=1))
    out = await U.project(db, w.org, NOW)
    assert len(out) == 1 and out[0].gate is None and out[0].kind == "xung_dot_khieu_nai"


async def _question_unit(db: AsyncSession, w: Any, pid: uuid.UUID, iid: uuid.UUID, now: datetime) -> None:
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="bao xi măng này bao nhiêu một bao vậy shop?", group=w.group, at=now - timedelta(hours=3))
    await _unit(db, w, raw, "cau_hoi_cho_xu_ly", person=pid, flags={"needs_owner": True}, at=now - timedelta(hours=3))


async def test_t12_pending_question_from_groupunit_is_scanned(db: AsyncSession) -> None:
    from datetime import UTC
    now = datetime.now(UTC)
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Khách hỏi giá", ptype="customer")
    await _question_unit(db, w, pid, iid, now)
    out = await questions.scan_units(db, w.org, now)
    await db.commit()
    assert len(out) == 1
    r = (await _rows(db, w.org, "cau_hoi_cho_xu_ly"))[0]
    assert r.facts["asker_kind"] == "customer" and r.level_input["flags"]["needs_owner"] is True


async def test_t12_pending_question_cold_start_only_in_confirmed_business_group(db: AsyncSession) -> None:
    """Chưa có `customer_of` nào: nhóm `kinh_doanh` đã xác nhận ⇒ người hỏi là khách; nhóm chưa xác nhận ⇒ bỏ (không spam)."""
    from datetime import UTC
    now = datetime.now(UTC)
    for business, n in ((True, 1), (False, 0)):
        w = await _world(db, business=business)
        pid, iid, _ = await _person(db, w, "Người hỏi")
        await _question_unit(db, w, pid, iid, now)
        assert len(await questions.scan_units(db, w.org, now)) == n, business
        await db.commit()


async def test_t12_pending_question_cold_start_skips_staff(db: AsyncSession) -> None:
    """Nhân viên (staff_of) hỏi trong nhóm kinh_doanh lúc cài mới vẫn không bị coi là khách."""
    from datetime import UTC
    now = datetime.now(UTC)
    w = await _world(db, business=True)
    pid, iid, ent = await _person(db, w, "NV hỏi")
    await _staff_of(db, w, ent)
    await _question_unit(db, w, pid, iid, now)
    assert await questions.scan_units(db, w.org, now) == []


async def test_t12_customer_complaint_cold_start_in_confirmed_business_group(db: AsyncSession) -> None:
    """Hồ sơ chưa có `customer_of` nào, nhóm `kinh_doanh` đã xác nhận: người chê (không phải người của Sếp) là khách ⇒ cổng 3."""
    w = await _world(db, business=True)
    pid, iid, _ = await _person(db, w, "Chị Mai")
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="hàng lỗi mà không ai trả lời, lần sau lấy chỗ khác", group=w.group,
                       at=NOW - timedelta(hours=1))
    await _unit(db, w, raw, "xung_dot_khieu_nai", person=pid, flags={"public": True, "said_will_leave": True}, at=NOW - timedelta(hours=1))
    out = await U.project(db, w.org, NOW)
    assert {s.kind: s.gate for s in out}.get("xung_dot_khieu_nai") == levels.G3


async def test_t12_safety_unit_merging_into_raw_tail_case_does_not_push_twice(db: AsyncSession) -> None:
    """Một vụ một 🔴 (an toàn): raw_tail đã đẩy với người gửi `u_<person>`; đơn vị model (generic) cùng tin, cùng người ⇒ `merge`, không
    "leo thang" giả vì cùng một người mang hai cách viết ref (e_/u_)."""
    from gh.biz.intel import raw_tail
    from gh.biz.intel.detectors import generic as G

    w = await _world(db)
    pid, iid, ent = await _person(db, w, "Lan")
    await _staff_of(db, w, ent)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="em bị tai nạn, đang đi cấp cứu", group=w.group, at=NOW - timedelta(minutes=10))
    victims = await raw_tail._victim(db, w.org, pid, frozenset())
    li = levels.SignalIn(kind=levels.SK, msg_at=NOW - timedelta(minutes=10), hard_rule_hit=["emergency"], flags=levels.Flags(emergency=True),
                         source_class="rule", confidence_exist=1.0, confidence_content=1.0, victims=[victims[0].model_copy(update={"team": True})],
                         thread=levels.Thread(type="group", size=20, agent_in_thread=True), impact_base=3, harm_what="nguy hiểm tính mạng")
    ev = signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=10)
    first = await signals.upsert(db, w.org, signals.UpsertIn(kind=levels.SK, title="t", detector="raw_tail", level_input=li, evidence=[ev],
                                                             case_key=levels.case_key(levels.SK, at=NOW, raw_event_id=str(raw))), NOW)
    assert first.delivery == "push_now"
    await db.commit()
    await _unit(db, w, raw, levels.SK, person=pid, flags={"emergency": True, "ongoing": True}, people=[("nguoi_bi_anh_huong", ent)],
                harm="nguy hiểm tính mạng", at=NOW - timedelta(minutes=10))
    out = await G.project_units(db, w.org, NOW)
    assert [s.id for s in out] == [first.id] and out[0].delivery == "merge", out


# ─── review T12 MINOR 3/4: một đơn vị hỏng không chặn cả lô; một lượt mỗi tổ chức ────────────────────────────

async def test_t12_broken_unit_does_not_block_fraud_unit_and_gives_up_after_max_fails(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    w = await _world(db)
    pid, iid, ent = await _person(db, w, "Ngọc")
    await _staff_of(db, w, ent)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body=CRED_BODY, group=w.group, at=NOW - timedelta(minutes=30))
    await _unit(db, w, raw, levels.FR, person=pid, flags={"credentials_entered": True}, people=[("nguoi_bi_anh_huong", ent)], harm="mất tiền",
                at=NOW - timedelta(minutes=30))
    raw2 = await mk_raw(db, w.org, w.channel, identity=iid, body="x", group=w.group, at=NOW - timedelta(minutes=10))
    bad = await _unit(db, w, raw2, "co_hoi_ket_noi", person=pid, at=NOW - timedelta(minutes=10), v={"msg_at": "2026-10-23T13:50:00"})   # mốc thiếu múi giờ
    real = U.signal_input

    def boom(kind: str, *a: Any, **k: Any) -> levels.SignalIn:
        if kind == "co_hoi_ket_noi":
            raise RuntimeError("hỏng")
        return real(kind, *a, **k)

    monkeypatch.setattr(U, "signal_input", boom)
    out = await U.project(db, w.org, NOW)            # đơn vị mới nhất (hỏng) xếp TRƯỚC đơn vị lừa đảo
    await db.commit()
    assert [(s.kind, s.level) for s in out] == [(levels.FR, "red")]
    for _ in range(U.MAX_FAILS - 1):
        assert await U.project(db, w.org, NOW) == []
        await db.commit()
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE org_id = :o AND action = 'intel.units.done' AND target_id = :t"),
                          {"o": w.org, "t": str(bad)})).scalar_one()
    assert n == 1                                    # đủ MAX_FAILS ⇒ thôi lấy lại
    monkeypatch.setattr(U, "signal_input", real)
    assert await U.project(db, w.org, NOW) == []


async def test_t12_naive_timestamp_in_verified_fields_is_handled(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Người A")
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="x", group=w.group, at=NOW - timedelta(minutes=10))
    await _unit(db, w, raw, "co_hoi_ket_noi", person=pid, at=NOW - timedelta(minutes=10), v={"msg_at": "2026-10-23T06:50:00"})
    out = await U.project(db, w.org, NOW)
    assert len(out) == 1


async def test_t12_units_one_run_per_org(db: AsyncSession) -> None:
    from gh.db import sessionmaker

    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Người A")
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="x", group=w.group, at=NOW - timedelta(minutes=10))
    await _unit(db, w, raw, "co_hoi_ket_noi", person=pid, at=NOW - timedelta(minutes=10))
    async with sessionmaker()() as other:
        await other.execute(text("SELECT pg_advisory_xact_lock(hashtext('intel_units:' || :o))"), {"o": str(w.org)})
        assert await U.project(db, w.org, NOW) == []
        await other.rollback()
    await db.rollback()
    assert len(await U.project(db, w.org, NOW)) == 1
