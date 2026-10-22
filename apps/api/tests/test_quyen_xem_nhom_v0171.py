# ruff: noqa: F811 — fixture `w` nhập từ test_p3_duty (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.71 (giả lập toàn luồng P2-1) — «Quyền xem» của nhóm (`core.groups.view_scope`) nay được ÁP THẬT ở mọi API đọc
tin / đơn vị / nhóm: «Chỉ Sếp» (`owner`) ẩn nội dung với mọi vai khác, «Sếp + quản lý» (`manager`) thêm vai Quản lý,
«Mọi thành viên» (`all_members`) cho mọi vai có quyền theo ma trận. Trước đây máy chủ chỉ lưu và hiển thị."""

import uuid
from typing import Any

from sqlalchemy import text

from gh import realtime
from gh.auth import rbac, service
from gh.gen.tools import ToolRunner
from tests.phase2 import listen, msg
from tests.test_p3_duty import BUY, refine, w  # noqa: F401
from tests.test_rbac_api import login_as


class _Ctx:
    """Ba nhóm cùng nội dung: gO «Chỉ Sếp», gM «Sếp + quản lý», gA «Mọi thành viên»; mỗi nhóm một khách."""

    def __init__(self) -> None:
        self.g: dict[str, uuid.UUID] = {}
        self.u: dict[str, uuid.UUID] = {}
        self.p: dict[str, uuid.UUID] = {}
        self.raw: dict[str, uuid.UUID] = {}
        self.op: Any = None
        self.aud: Any = None
        self.mgr: Any = None
        self.mgr_id: uuid.UUID | None = None


_SCOPES = {"O": "owner", "M": "manager", "A": "all_members"}


async def _ctx(w, db, redis, client) -> _Ctx:  # type: ignore[no-untyped-def]
    c = _Ctx()
    org = w["org"]
    for k, scope in _SCOPES.items():
        gid = await listen(db, org, f"g{k}", mode="proactive")
        await db.execute(text("UPDATE core.groups SET view_scope = :s WHERE id = :g"), {"s": scope, "g": gid})
        c.g[k] = gid
    await db.commit()
    units = await refine(w, redis, *[msg(BUY, group=f"g{k}", sender=f"khach{k}", name=f"Khách {k}") for k in _SCOPES])
    for k, u in zip(_SCOPES, units, strict=True):
        c.u[k] = u
        c.p[k] = (await db.execute(text("SELECT person_id FROM core.person_identities WHERE external_id = :e"),
                                   {"e": f"khach{k}"})).scalar_one()
        c.raw[k] = (await db.execute(text("SELECT raw_event_id FROM clean.evidence WHERE meaning_unit_id = :u"),
                                     {"u": u})).scalar_one()
    c.op = await login_as(client, db, "operator")
    c.aud = await login_as(client, db, "auditor")
    c.mgr = await login_as(client, db, "manager")
    c.mgr_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'manager@example.vn'"))).scalar_one()
    return c


async def _give_manager_everything(db: Any, c: _Ctx, org: uuid.UUID) -> None:
    """Vai Quản lý (phạm vi `team`) chỉ thấy nhóm/khách được giao ⇒ giao cả ba nhóm và ba khách cho người này."""
    for k in _SCOPES:
        for st, sid in (("group", c.g[k]), ("person", c.p[k])):
            await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                                     VALUES (:o, :u, :t, :s)"""), {"o": org, "u": c.mgr_id, "t": st, "s": sid})
    await db.commit()


def _ids(r: Any, key: str = "items") -> set[str]:
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()[key]}


# ─── Hộp thư ──────────────────────────────────────────────────────────────────

async def test_inbox_follows_group_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]
    assert {str(u) for u in c.u.values()} <= _ids(await owner.get("/inbox"))
    r = await c.op.get("/inbox")                       # Vận hành: chỉ «Mọi thành viên»
    assert _ids(r) & {str(u) for u in c.u.values()} == {str(c.u["A"])}
    assert r.json()["total"] == len(r.json()["items"])                      # số đếm cũng không lộ
    assert (await c.op.get(f"/inbox/{c.u['O']}")).status_code == 404
    assert (await c.op.get(f"/inbox/{c.u['M']}")).status_code == 404
    assert (await c.op.get(f"/inbox/{c.u['A']}")).status_code == 200
    assert str(c.u["O"]) not in _ids(await c.aud.get("/inbox"))
    await _give_manager_everything(db, c, w["org"])
    got = _ids(await c.mgr.get("/inbox")) & {str(u) for u in c.u.values()}
    assert got == {str(c.u["M"]), str(c.u["A"])}               # Quản lý: «Sếp + quản lý» và «Mọi thành viên»
    assert (await c.mgr.get(f"/inbox/{c.u['O']}")).status_code == 404
    assert (await c.mgr.get(f"/inbox/{c.u['M']}")).status_code == 200


async def test_changing_scope_takes_effect_at_once(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    assert (await c.op.get(f"/inbox/{c.u['O']}")).status_code == 404
    r = await w["api"].send("PATCH", f"/groups/{c.g['O']}", {"view_scope": "all_members"})
    assert r.status_code == 200, r.text
    assert (await c.op.get(f"/inbox/{c.u['O']}")).status_code == 200
    r = await w["api"].send("PATCH", f"/groups/{c.g['O']}", {"view_scope": "owner"})
    assert r.status_code == 200, r.text
    assert (await c.op.get(f"/inbox/{c.u['O']}")).status_code == 404


# ─── Kho thô / Kho sạch / sổ tay ──────────────────────────────────────────────

async def test_raw_store_follows_group_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]
    ro = await owner.get("/raw?since=all")
    assert {str(r) for r in c.raw.values()} <= _ids(ro)
    r = await c.aud.get("/raw?since=all")
    assert _ids(r) & {str(x) for x in c.raw.values()} == {str(c.raw["A"])}
    assert r.json()["total"] == ro.json()["total"] - 2                      # tổng không tính tin nhóm bị ẩn
    for k, code in (("O", 404), ("M", 404), ("A", 200)):
        assert (await c.aud.get(f"/raw/{c.raw[k]}")).status_code == code
    assert (await owner.get(f"/raw/{c.raw['O']}")).status_code == 200
    r = await c.aud.get(f"/raw?since=all&group_id={c.g['O']}")      # lọc thẳng theo nhóm cũng không lòi
    assert r.json()["items"] == []
    names = {x["group"]["id"] for x in (await c.aud.get("/raw/by-group?since=all")).json()}
    assert names == {str(c.g["A"])}                                          # thống kê theo nhóm không lộ tên nhóm ẩn
    every = {x["group"]["id"] for x in (await owner.get("/raw/by-group?since=all")).json()}
    assert {str(c.g[k]) for k in _SCOPES} <= every


async def test_clean_store_and_notebook_follow_group_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    r = await c.aud.get("/clean?since=all")
    assert _ids(r) & {str(u) for u in c.u.values()} == {str(c.u["A"])}
    assert (await c.aud.get(f"/clean/{c.u['O']}/evidence")).status_code == 404
    assert (await c.aud.get(f"/clean/{c.u['A']}/evidence")).status_code == 200
    assert (await w["api"].get(f"/clean/{c.u['O']}/evidence")).status_code == 200
    assert (await c.aud.get(f"/notebooks/group/{c.g['O']}")).status_code == 404
    assert (await c.aud.get(f"/notebooks/group/{c.g['O']}/compactions")).status_code == 404
    assert (await c.aud.get(f"/notebooks/group/{c.g['A']}")).status_code == 200
    assert (await w["api"].get(f"/notebooks/group/{c.g['O']}")).status_code == 200


# ─── Khách & Nhóm, hồ sơ, tìm kiếm ────────────────────────────────────────────

async def test_group_directory_and_profile_follow_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    mine = {str(g) for g in c.g.values()}
    assert mine <= _ids(await w["api"].get("/directory/groups"))
    assert _ids(await c.op.get("/directory/groups")) & mine == {str(c.g["A"])}
    assert (await c.op.get(f"/notebook/group/{c.g['O']}")).status_code in (403, 404)
    await _give_manager_everything(db, c, w["org"])
    assert _ids(await c.mgr.get("/directory/groups")) & mine == {str(c.g["M"]), str(c.g["A"])}

    def tl(r: Any) -> set[str]:
        assert r.status_code == 200, r.text
        return {t["id"] for t in r.json()["timeline"]}

    assert str(c.u["O"]) in tl(await w["api"].get(f"/profile/{c.p['O']}"))
    r = await c.op.get(f"/profile/{c.p['O']}")                  # người vẫn xem được, nhưng không có tin nhóm ẩn
    assert tl(r) == set() and r.json()["summary"] == []
    assert str(c.u["A"]) in tl(await c.op.get(f"/profile/{c.p['A']}"))


async def test_search_follows_group_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)

    def people(r: Any) -> set[str]:
        assert r.status_code == 200, r.text
        return {i["person"]["id"] for i in r.json()["items"]}

    mine = {str(p) for p in c.p.values()}
    assert mine <= people(await w["api"].get("/search?q=container"))
    assert people(await c.op.get("/search?q=container")) & mine == {str(c.p["A"])}
    assert people(await c.op.get("/search")) & mine == {str(c.p["A"])}


async def test_explain_follows_group_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    assert (await w["api"].get(f"/explain/meaning_unit/{c.u['O']}")).status_code == 200
    assert (await c.op.get(f"/explain/meaning_unit/{c.u['O']}")).status_code == 404
    assert (await c.op.get(f"/explain/meaning_unit/{c.u['A']}")).status_code == 200
    assert (await c.op.get(f"/explain/raw/{c.raw['O']}")).status_code == 404
    assert (await c.op.get(f"/explain/raw/{c.raw['A']}")).status_code == 200
    alert = (await db.execute(text("""
        INSERT INTO biz.alerts (org_id, code, alert_type, title, summary, priority, status, evidence)
        VALUES (:o, core.next_code('ALT'), 'rule', 'Cảnh báo thử', 'tóm tắt', 'P2', 'open', CAST(:ev AS jsonb))
        RETURNING id"""), {"o": w["org"], "ev": f'[{{"type": "meaning_unit", "id": "{c.u["O"]}"}}, '
                                                f'{{"type": "meaning_unit", "id": "{c.u["A"]}"}}]'})).scalar_one()
    await db.commit()
    assert (await c.op.get(f"/explain/alert/{alert}")).status_code == 404   # chứng cứ ở nhóm ẩn ⇒ ẩn cả cảnh báo
    assert (await w["api"].get(f"/explain/alert/{alert}")).status_code == 200
    only_a = (await db.execute(text("""
        INSERT INTO biz.alerts (org_id, code, alert_type, title, summary, priority, status, evidence)
        VALUES (:o, core.next_code('ALT'), 'rule', 'Cảnh báo A', 'tóm tắt', 'P2', 'open', CAST(:ev AS jsonb))
        RETURNING id"""), {"o": w["org"], "ev": f'[{{"type": "meaning_unit", "id": "{c.u["A"]}"}}]'})).scalar_one()
    await db.commit()
    got = (await c.op.get(f"/explain/alert/{only_a}")).json()["units"]
    assert {u["id"] for u in got} == {str(c.u["A"])}


# ─── Nhật ký lắng nghe, kênh, Việc, Cung–Cầu, Gen nhân viên ───────────────────

async def test_listen_log_and_channel_groups_follow_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    owner: Any = w["api"]
    mine = {str(g) for g in c.g.values()}
    st = (await c.aud.get("/listen-log/stats")).json()
    assert {g["id"] for g in st["groups"]} & mine == {str(c.g["A"])}
    assert mine <= {g["id"] for g in (await owner.get("/listen-log/stats")).json()["groups"]}
    items = (await c.aud.get("/listen-log?since=all&kind=all")).json()["items"]
    seen = {(i.get("group") or {}).get("id") for i in items} & mine
    assert seen <= {str(c.g["A"])}
    groups = (await c.aud.get("/channels/zalo/groups")).json()
    assert {g["id"] for g in groups} & mine == {str(c.g["A"])}
    assert mine <= {g["id"] for g in (await owner.get("/channels/zalo/groups")).json()}


async def test_tasks_and_signals_follow_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    org = w["org"]
    for k in _SCOPES:
        await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, priority, status, subject_type, subject_id)
                                 VALUES (:o, core.next_code('TSK'), :t, 'P2', 'todo', 'group', :g)"""),
                         {"o": org, "t": f"Việc nhóm {k}", "g": c.g[k]})
        await db.execute(text("""INSERT INTO biz.market_signals (org_id, side, item, status, group_id)
                                 VALUES (:o, 'demand', :t, 'open', :g)"""),
                         {"o": org, "t": f"Cần hàng {k}", "g": c.g[k]})
    await db.commit()
    titles = {i["title"] for i in (await c.op.get("/tasks")).json()["items"]}
    assert titles & {f"Việc nhóm {k}" for k in _SCOPES} == {"Việc nhóm A"}
    assert {f"Việc nhóm {k}" for k in _SCOPES} <= {i["title"] for i in (await w["api"].get("/tasks")).json()["items"]}
    items = {i["item"] for i in (await c.op.get("/supply")).json()["items"]}
    assert items & {f"Cần hàng {k}" for k in _SCOPES} == {"Cần hàng A"}
    assert {f"Cần hàng {k}" for k in _SCOPES} <= {i["item"] for i in (await w["api"].get("/supply")).json()["items"]}


async def test_gen_of_staff_reads_groups_only_through_the_same_filtered_api(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    token = c.op.c.cookies.get(service.SESSION_COOKIE)
    op_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'operator@example.vn'"))).scalar_one()
    me = service.CurrentUser(id=op_id, org_id=w["org"], email="", display_name="", role_code="operator", role_name="",
                             role_id=uuid.uuid4(), team_id=None, session_id=uuid.uuid4(), pin_verified_until=None,
                             addressing={}, permissions=dict(rbac.DEFAULT_MATRIX["operator"]))
    run = ToolRunner(w["api"].c._transport.app, me, token)  # type: ignore[attr-defined]
    inbox = await run.run("queue.list", {"tab": "all"})
    assert inbox.ok and str(c.u["A"]) in inbox.text
    assert str(c.u["O"]) not in inbox.text and str(c.u["M"]) not in inbox.text
    found = await run.run("profile.search", {"q": "container"})
    assert found.ok and str(c.p["A"]) in found.text and str(c.p["O"]) not in found.text


# ─── WebSocket ────────────────────────────────────────────────────────────────

class _Ws:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, t: str) -> None:
        self.sent.append(t)


async def test_ws_raw_new_of_group_follows_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    hub = realtime.Hub(None)  # type: ignore[arg-type]
    org = w["org"]

    def user(role: str) -> service.CurrentUser:
        return service.CurrentUser(id=uuid.uuid4(), org_id=org, email="", display_name="", role_code=role,
                                   role_name="", role_id=uuid.uuid4(), team_id=None, session_id=uuid.uuid4(),
                                   pin_verified_until=None, addressing={}, permissions={"data.read": "all"})

    ow, mg, au = _Ws(), _Ws(), _Ws()
    hub.clients = {ow: user("owner"), mg: user("manager"), au: user("auditor")}  # type: ignore[dict-item]
    for k, (n_ow, n_mg, n_au) in {"O": (1, 0, 0), "M": (2, 1, 0), "A": (3, 2, 1)}.items():
        await hub.dispatch({"type": "raw.new", "org_id": str(org), "to_user": None,
                            "data": {"id": k, "group": {"id": str(c.g[k])}, "text": "tin nhóm"}})
        assert (len(ow.sent), len(mg.sent), len(au.sent)) == (n_ow, n_mg, n_au), k


async def test_agent_decisions_follow_group_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    from tests.test_p3_duty import make_agent

    c = await _ctx(w, db, redis, client)
    aid = await make_agent(db, w)
    for k in _SCOPES:
        await db.execute(text("""INSERT INTO agent.decisions (org_id, agent_id, trigger_ref, trigger_unit_id, decision,
                                                              outcome, rationale)
                                 VALUES (:o, :a, CAST(:r AS jsonb), :u, 'note', 'noted', :t)"""),
                         {"o": w["org"], "a": aid, "r": f'{{"type":"meaning_unit","id":"{c.u[k]}"}}', "u": c.u[k],
                          "t": f"Lý do nhóm {k}"})
    await db.commit()
    got = {i["rationale"] for i in (await c.aud.get("/agents/decisions")).json()["items"]}
    assert got & {f"Lý do nhóm {k}" for k in _SCOPES} == {"Lý do nhóm A"}
    got = {i["rationale"] for i in (await w["api"].get("/agents/decisions")).json()["items"]}
    assert {f"Lý do nhóm {k}" for k in _SCOPES} <= got


# ─── cảnh báo gắn vào NGƯỜI mà chứng cứ ở nhóm bị ẩn, tài liệu, nháp WS, việc giao đích danh ──────────────

async def test_person_alert_with_evidence_in_hidden_group_is_hidden(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    from gh.biz.queue import jobs as queue_jobs
    from tests.test_p3_queue import _unit

    c = await _ctx(w, db, redis, client)
    for k in ("O", "A"):
        await _unit(db, w["org"], group_id=c.g[k], person_id=c.p[k], event_type="MentionsCompetitor",
                    conclusion=f"Nhắc bên ABC rẻ hơn ({k})")
    await db.commit()
    await queue_jobs.early_warning_scan({"redis_bus": redis})
    ids = {k: (await db.execute(text("SELECT id FROM biz.alerts WHERE summary LIKE :s"),
                                {"s": f"%({k})%"})).scalar_one() for k in ("O", "A")}
    owner: Any = w["api"]
    assert {str(i) for i in ids.values()} <= _ids(await owner.get("/inbox"))
    got = _ids(await c.op.get("/inbox"))
    assert str(ids["A"]) in got and str(ids["O"]) not in got
    assert (await c.op.get(f"/inbox/{ids['O']}")).status_code == 404
    assert (await c.op.get(f"/explain/alert/{ids['O']}")).status_code == 404
    assert (await c.op.get(f"/explain/alert/{ids['A']}")).status_code == 200


async def test_documents_of_hidden_group_are_hidden(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    for k in ("O", "A"):
        await db.execute(text("""INSERT INTO biz.documents (org_id, title, storage_key, mime, bytes, created_by,
                                                              owner_group_id)
                                 VALUES (:o, :t, :k, 'application/pdf', 10, 'test', :g)"""),
                         {"o": w["org"], "t": f"Hợp đồng nhóm {k}", "k": f"k/{k}", "g": c.g[k]})
    await db.commit()
    titles = {d["title"] for d in (await c.op.get("/documents")).json()["items"]}
    assert titles & {"Hợp đồng nhóm O", "Hợp đồng nhóm A"} == {"Hợp đồng nhóm A"}
    owner_titles = {d["title"] for d in (await w["api"].get("/documents")).json()["items"]}
    assert {"Hợp đồng nhóm O", "Hợp đồng nhóm A"} <= owner_titles


async def test_ws_draft_events_follow_group_view_scope(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    org = w["org"]
    drafts: dict[str, uuid.UUID] = {}
    for k in _SCOPES:
        drafts[k] = (await db.execute(text("""
            INSERT INTO biz.action_drafts (org_id, code, kind, body, autonomy_level, subject_type, subject_id, group_id)
            VALUES (:o, core.next_code('DRF'), 'message', '{}', 1, 'group', :g, :g) RETURNING id"""),
                                       {"o": org, "g": c.g[k]})).scalar_one()
    await db.commit()
    hub = realtime.Hub(None)  # type: ignore[arg-type]

    def user(role: str) -> service.CurrentUser:
        return service.CurrentUser(id=uuid.uuid4(), org_id=org, email="", display_name="", role_code=role,
                                   role_name="", role_id=uuid.uuid4(), team_id=None, session_id=uuid.uuid4(),
                                   pin_verified_until=None, addressing={}, permissions={"action.approve": "all"})

    ow, mg, op = _Ws(), _Ws(), _Ws()
    hub.clients = {ow: user("owner"), mg: user("manager"), op: user("operator")}  # type: ignore[dict-item]
    for k, (n_ow, n_mg, n_op) in {"O": (1, 0, 0), "M": (2, 1, 0), "A": (3, 2, 1)}.items():
        await hub.dispatch({"type": "draft.new", "org_id": str(org), "to_user": None, "data": {"id": str(drafts[k])}})
        assert (len(ow.sent), len(mg.sent), len(op.sent)) == (n_ow, n_mg, n_op), k


async def test_assigned_task_of_hidden_group_is_hidden(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    c = await _ctx(w, db, redis, client)
    for k in ("O", "A"):
        await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, priority, status, subject_type, subject_id,
                                                         assignee_user_id)
                                 VALUES (:o, core.next_code('TSK'), :t, 'P2', 'todo', 'group', :g, :u)"""),
                         {"o": w["org"], "t": f"Việc giao nhóm {k}", "g": c.g[k], "u": c.mgr_id})
    await db.commit()
    titles = {i["title"] for i in (await c.mgr.get("/tasks")).json()["items"]}
    assert titles & {"Việc giao nhóm O", "Việc giao nhóm A"} == {"Việc giao nhóm A"}
