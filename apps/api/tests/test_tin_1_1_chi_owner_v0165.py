# ruff: noqa: F811 — fixture `w` nhập từ test_p3_duty (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.65 (review P2-2) — tin 1-1 (group_id IS NULL) chỉ Owner xem nguyên văn, trừ người được giao ĐÚNG người đó.
Migration 0038 bật lưu 1-1 cho bản cũ ⇒ Vận hành (queue.read/profile.read ALL) và Kiểm soát (data.read ALL) không được
thấy tin riêng của khách qua Hộp thư, Kho thô, Kho sạch, hồ sơ, tìm kiếm, chứng cứ, WebSocket."""

import uuid
from typing import Any

from sqlalchemy import text

from gh import realtime
from gh.auth import rbac, service
from gh.db import sessionmaker
from gh.gen.tools import ToolRunner
from tests.phase2 import listen, msg, org_id, put
from tests.test_p3_duty import BUY, refine, w  # noqa: F401
from tests.test_rbac_api import login_as


class _Ctx:
    """Bối cảnh: khách A nhắn 1-1, khách B nhắn trong nhóm g1; Owner + hai vai thấp."""

    def __init__(self) -> None:
        self.ua = self.ug = self.pa = self.pb = self.raw_a = self.raw_g = None  # type: ignore[assignment]
        self.op: Any = None
        self.aud: Any = None
        self.op_id: uuid.UUID | None = None


async def _ctx(w, db, redis, client) -> _Ctx:  # type: ignore[no-untyped-def]
    c = _Ctx()
    c.ua, c.ug = await refine(w, redis, msg(BUY, group=None, sender="khachA", name="Khách A"),
                              msg(BUY, group="g1", sender="khachB", name="Khách B"))
    c.pa = (await db.execute(text("SELECT person_id FROM core.person_identities WHERE external_id = 'khachA'"))
            ).scalar_one()
    c.pb = (await db.execute(text("SELECT person_id FROM core.person_identities WHERE external_id = 'khachB'"))
            ).scalar_one()
    c.raw_a = (await db.execute(text("SELECT raw_event_id FROM clean.evidence WHERE meaning_unit_id = :u"),
                                {"u": c.ua})).scalar_one()
    c.raw_g = (await db.execute(text("SELECT raw_event_id FROM clean.evidence WHERE meaning_unit_id = :u"),
                                {"u": c.ug})).scalar_one()
    c.op = await login_as(client, db, "operator")
    c.aud = await login_as(client, db, "auditor")
    c.op_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'operator@example.vn'"))).scalar_one()
    return c


async def _assign(db: Any, person: uuid.UUID, user: uuid.UUID) -> None:
    await db.execute(text("UPDATE core.persons SET owner_user_id = :u WHERE id = :p"), {"u": user, "p": person})
    await db.commit()


def _ids(r: Any, key: str = "items") -> set[str]:
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()[key]}


# ─── Hộp thư ──────────────────────────────────────────────────────────────────

async def test_inbox_hides_direct_units_from_non_owner_unless_assigned(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]
    assert {str(c.ua), str(c.ug)} <= _ids(await owner.get("/inbox"))
    r = await c.op.get("/inbox")
    assert str(c.ug) in _ids(r) and str(c.ua) not in _ids(r)
    assert r.json()["total"] == len([i for i in r.json()["items"]])   # đếm cũng không lộ
    assert (await c.op.get(f"/inbox/{c.ua}")).status_code == 404
    assert (await c.op.get(f"/inbox/{c.ug}")).status_code == 200
    assert str(c.ua) not in _ids(await c.aud.get("/inbox"))
    await _assign(db, c.pa, c.op_id)                                 # giao ĐÚNG người đó cho Vận hành
    assert str(c.ua) in _ids(await c.op.get("/inbox"))
    assert (await c.op.get(f"/inbox/{c.ua}")).status_code == 200
    assert str(c.ua) not in _ids(await c.aud.get("/inbox"))          # Kiểm soát vẫn không


# ─── Kho thô / Kho sạch ───────────────────────────────────────────────────────

async def test_raw_store_hides_direct_events_from_auditor(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]
    ro = await owner.get("/raw?since=all")
    assert {str(c.raw_a), str(c.raw_g)} <= _ids(ro)
    r = await c.aud.get("/raw?since=all")
    assert str(c.raw_g) in _ids(r) and str(c.raw_a) not in _ids(r)
    assert r.json()["total"] == ro.json()["total"] - 1               # tổng không tính tin 1-1
    assert (await c.aud.get(f"/raw/{c.raw_a}")).status_code == 404
    assert (await c.aud.get(f"/raw/{c.raw_g}")).status_code == 200
    assert (await owner.get(f"/raw/{c.raw_a}")).status_code == 200


async def test_clean_store_hides_direct_units_and_evidence_from_auditor(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]
    assert {str(c.ua), str(c.ug)} <= _ids(await owner.get("/clean?since=all"))
    r = await c.aud.get("/clean?since=all")
    assert str(c.ug) in _ids(r) and str(c.ua) not in _ids(r)
    assert (await c.aud.get(f"/clean/{c.ua}/evidence")).status_code == 404
    assert (await c.aud.get(f"/clean/{c.ug}/evidence")).status_code == 200
    assert (await owner.get(f"/clean/{c.ua}/evidence")).status_code == 200
    # lọc thẳng theo người 1-1 cũng không lòi ra
    assert _ids(await c.aud.get(f"/clean?since=all&person_id={c.pa}")) == set()


async def test_skipped_messages_hide_direct_text_from_operator(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    for raw in (c.raw_a, c.raw_g):
        await db.execute(text("""UPDATE refinery.event_state SET state = 'discarded',
                                   detail = '{"discarded_by": "prefilter", "reason": "duplicate"}'
                                 WHERE event_id = :e"""), {"e": raw})
    await db.commit()
    owner: Any = w["api"]
    assert {str(c.raw_a), str(c.raw_g)} <= _ids(await owner.get("/refinery/triage/skipped"))
    r = await c.op.get("/refinery/triage/skipped")
    assert str(c.raw_g) in _ids(r) and str(c.raw_a) not in _ids(r)
    assert r.json()["total"] == 1


# ─── Hồ sơ, tìm kiếm, chứng cứ ────────────────────────────────────────────────

async def test_profile_timeline_hides_direct_units_unless_assigned(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]

    def tl(r: Any) -> set[str]:
        assert r.status_code == 200, r.text
        return {t["id"] for t in r.json()["timeline"]}

    assert str(c.ua) in tl(await owner.get(f"/profile/{c.pa}"))
    r = await c.op.get(f"/profile/{c.pa}")
    assert tl(r) == set() and r.json()["summary"] == []
    assert str(c.ug) in tl(await c.op.get(f"/profile/{c.pb}"))      # khách trong nhóm: vẫn thấy
    await _assign(db, c.pa, c.op_id)
    assert str(c.ua) in tl(await c.op.get(f"/profile/{c.pa}"))


async def test_search_hides_direct_units_unless_assigned(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]

    def people(r: Any) -> set[str]:
        assert r.status_code == 200, r.text
        return {i["person"]["id"] for i in r.json()["items"]}

    assert {str(c.pa), str(c.pb)} <= people(await owner.get("/search?q=container"))
    got = people(await c.op.get("/search?q=container"))
    assert str(c.pb) in got and str(c.pa) not in got
    assert str(c.pa) not in people(await c.op.get("/search"))
    await _assign(db, c.pa, c.op_id)
    assert str(c.pa) in people(await c.op.get("/search?q=container"))


async def test_explain_and_evidence_hide_direct_units_and_quotes(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]
    assert (await owner.get(f"/explain/meaning_unit/{c.ua}")).status_code == 200
    assert (await c.op.get(f"/explain/meaning_unit/{c.ua}")).status_code == 404
    assert (await c.op.get(f"/explain/meaning_unit/{c.ug}")).status_code == 200
    assert (await c.op.get(f"/explain/raw/{c.raw_a}")).status_code == 404
    assert (await c.op.get(f"/explain/raw/{c.raw_g}")).status_code == 200
    # cảnh báo dẫn chứng là đơn vị 1-1: người xem thấp không nhận đơn vị đó trong chứng cứ
    org = w["org"]
    alert = (await db.execute(text("""
        INSERT INTO biz.alerts (org_id, code, alert_type, title, summary, priority, status, evidence)
        VALUES (:o, core.next_code('ALT'), 'rule', 'Cảnh báo thử', 'tóm tắt', 'P2', 'open', CAST(:ev AS jsonb))
        RETURNING id"""), {"o": org, "ev": f'[{{"type": "meaning_unit", "id": "{c.ua}"}}, '
                                          f'{{"type": "meaning_unit", "id": "{c.ug}"}}]'})).scalar_one()
    await db.commit()
    got = (await owner.get(f"/explain/alert/{alert}")).json()["units"]
    assert {u["id"] for u in got} == {str(c.ua), str(c.ug)}
    got = (await c.op.get(f"/explain/alert/{alert}")).json()["units"]
    assert {u["id"] for u in got} == {str(c.ug)}
    await _assign(db, c.pa, c.op_id)
    got = (await c.op.get(f"/explain/alert/{alert}")).json()["units"]
    assert {u["id"] for u in got} == {str(c.ua), str(c.ug)}


# ─── WebSocket ────────────────────────────────────────────────────────────────

class _Ws:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, t: str) -> None:
        self.sent.append(t)


async def test_ws_raw_new_for_direct_message_goes_to_owner_only(app, db) -> None:  # type: ignore[no-untyped-def]
    hub = realtime.Hub(None)  # type: ignore[arg-type]
    org = uuid.uuid4()
    gid = await listen(db, await org_id(db), "gws")      # nhóm thật «Mọi thành viên» (v0.1.71: WS xét «Quyền xem»)

    def user(role: str) -> service.CurrentUser:
        return service.CurrentUser(id=uuid.uuid4(), org_id=org, email="", display_name="", role_code=role,
                                   role_name="", role_id=uuid.uuid4(), team_id=None, session_id=uuid.uuid4(),
                                   pin_verified_until=None, addressing={}, permissions={"data.read": "all"})

    ow, au = _Ws(), _Ws()
    hub.clients = {ow: user("owner"), au: user("auditor")}  # type: ignore[dict-item]
    await hub.dispatch({"type": "raw.new", "org_id": str(org), "to_user": None,
                        "data": {"id": "1", "group": None, "text": "tin riêng"}})
    assert len(ow.sent) == 1 and au.sent == []
    await hub.dispatch({"type": "raw.new", "org_id": str(org), "to_user": None,
                        "data": {"id": "2", "group": {"id": str(gid)}, "text": "tin nhóm"}})
    assert len(ow.sent) == 2 and len(au.sent) == 1


async def test_ingest_of_direct_message_publishes_without_leaking_to_auditor(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    """Kiểm đường thật: tin 1-1 mới vào Kho (silent) — bản ghi thô không hiện ở danh sách của Kiểm soát."""
    aud = await login_as(client, db, "auditor")
    [ev] = await put(sessionmaker(), w["org"], msg("chuyện riêng", group=None, sender="khachC", name="Khách C"))
    r = await aud.get("/raw?since=all")
    assert str(ev) not in _ids(r)


async def test_gen_of_staff_reads_direct_messages_only_through_the_same_filtered_api(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    """Gen của nhân viên đọc Hộp thư, tìm kiếm, hồ sơ qua chính các API trên bằng phiên người hỏi ⇒ không lộ 1-1."""
    c = await _ctx(w, db, redis, client)
    token = c.op.c.cookies.get(service.SESSION_COOKIE)
    org = w["org"]
    me = service.CurrentUser(id=c.op_id, org_id=org, email="", display_name="", role_code="operator", role_name="",
                             role_id=uuid.uuid4(), team_id=None, session_id=uuid.uuid4(), pin_verified_until=None,
                             addressing={}, permissions=dict(rbac.DEFAULT_MATRIX["operator"]))
    run = ToolRunner(w["api"].c._transport.app, me, token)  # type: ignore[attr-defined]
    inbox = await run.run("queue.list", {"tab": "all"})
    assert inbox.ok and str(c.ug) in inbox.text and str(c.ua) not in inbox.text
    found = await run.run("profile.search", {"q": "container"})
    assert found.ok and str(c.pb) in found.text and str(c.pa) not in found.text
    prof = await run.run("profile.get", {"id": str(c.pa)})
    assert prof.ok and str(c.ua) not in prof.text
