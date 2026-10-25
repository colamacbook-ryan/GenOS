# ruff: noqa: E501
"""v0.1.76 (gói T9) — tự suy kết quả (`pipes/outcomes.py`) + `POST /signals/{id}/outcome`: không đè kết quả Sếp/nhân viên đã ghi,
chạy lại an toàn, lọc org, `console_pin` cần PIN, ghi chỉ Owner."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel.pipes import outcomes
from gh.db import sessionmaker
from tests.conftest import Api, verify_pin
from tests.mem_helpers import mk_org
from tests.phase2 import org_id
from tests.test_intel_digest_rawtail_v0176 import _sig
from tests.test_rbac_api import login_as

SM = sessionmaker
NOW = datetime.now(UTC)


async def _s(db: AsyncSession, org: uuid.UUID, ck: str, **upd: Any) -> uuid.UUID:
    s = await _sig(db, org, "viec_han", ck)
    if upd:
        await db.execute(text("UPDATE biz.alerts SET " + ", ".join(f"{k} = :{k}" for k in upd) + " WHERE id = :i"), {**upd, "i": s.id})  # noqa: S608
    await db.commit()
    return s.id


async def _row(db: AsyncSession, sid: uuid.UUID) -> Any:
    return (await db.execute(text("SELECT outcome, outcome_result, outcome_by FROM biz.alerts WHERE id = :i"), {"i": sid})).one()


async def _run(org: uuid.UUID) -> dict[str, int]:
    async with SM()() as s:
        out = await outcomes.infer(s, org, NOW)
        await s.commit()
    return out


async def _promise(db: AsyncSession, org: uuid.UUID, sid: uuid.UUID, *, kept: bool = False, broken: bool = False) -> None:
    pid = (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name) VALUES (:o, core.next_code('PER'), 'A') RETURNING id"), {"o": org})).scalar_one()
    await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, text, due_at, kept_at, broken, signal_id)
                             VALUES (:o, :p, 'hứa', now(), :k, :b, :s)"""), {"o": org, "p": pid, "k": NOW if kept else None, "b": broken or None, "s": sid})
    await db.commit()


async def test_outcomes_infer_rules(db: AsyncSession) -> None:
    org = await mk_org(db)
    kept = await _s(db, org, "kept")
    await _promise(db, org, kept, kept=True)
    broken = await _s(db, org, "broken")
    await _promise(db, org, broken, broken=True)
    acked = await _s(db, org, "acked", status="acknowledged")
    resolved = await _s(db, org, "res", status="resolved")
    expired = await _s(db, org, "exp", status="expired")
    dismissed = await _s(db, org, "dis", status="dismissed")
    untouched = await _s(db, org, "open")
    asked = await _s(db, org, "ask")
    await db.execute(text("""INSERT INTO ops.asks (org_id, code, source, ref_id, decision_kind, summary, signal_id, state)
                             VALUES (:o, 'AB3CD', 'signal', core.uuid_v7(), 'self', 'x', :s, 'confirmed')"""), {"o": org, "s": asked})
    await db.commit()
    out = await _run(org)
    assert out["set"] == 6
    assert tuple(await _row(db, kept)) == ("acted", "saved", "auto")
    assert tuple(await _row(db, broken)) == ("ignored", "lost", "auto")
    assert tuple(await _row(db, acked)) == ("acted", "unknown", "auto")
    assert tuple(await _row(db, resolved)) == ("acted", "unknown", "auto")
    assert tuple(await _row(db, asked)) == ("acted", "unknown", "auto")
    assert tuple(await _row(db, expired)) == ("ignored", "unknown", "auto")
    assert (await _row(db, dismissed)).outcome is None and (await _row(db, untouched)).outcome is None      # Sếp bấm Sai ≠ kết quả


async def test_outcomes_never_overwrite_owner_and_idempotent(db: AsyncSession) -> None:
    org = await mk_org(db)
    sid = await _s(db, org, "mine", status="resolved", outcome="acted", outcome_result="lost", outcome_by="owner")
    kept = await _s(db, org, "k2", status="acknowledged")
    await _promise(db, org, kept, kept=True)
    assert (await _run(org))["set"] == 1
    assert tuple(await _row(db, sid)) == ("acted", "lost", "owner")
    assert (await _run(org))["set"] == 0                                                  # chạy lại không đổi gì


async def test_outcomes_org_isolation_and_skips_legacy(db: AsyncSession) -> None:
    a, b = await mk_org(db), await mk_org(db, "B")
    other = await _s(db, b, "ob", status="resolved")
    legacy = await _s(db, a, "lg", status="resolved", kind="legacy")
    assert (await _run(a))["set"] == 0
    assert (await _row(db, other)).outcome is None and (await _row(db, legacy)).outcome is None


async def test_outcome_route_owner_sets_and_overrides_auto(owner_api: Api, db: AsyncSession) -> None:
    org = await org_id(db)
    sid = await _s(db, org, "r1", status="resolved", outcome="acted", outcome_result="unknown", outcome_by="auto")
    r = await owner_api.send("POST", f"/signals/{sid}/outcome", {"result": "saved", "value_vnd": 5_000_000, "note": "giữ được khách"})
    assert r.status_code == 200, r.text
    row = (await db.execute(text("SELECT outcome, outcome_result, outcome_by, outcome_value_vnd FROM biz.alerts WHERE id = :i"), {"i": sid})).one()
    assert tuple(row) == ("acted", "saved", "owner", 5_000_000)
    assert (await owner_api.send("POST", f"/signals/{sid}/outcome", {"result": "bậy"})).status_code == 422
    assert (await owner_api.send("POST", f"/signals/{sid}/outcome", {"result": "lost", "value_vnd": -1})).status_code == 422


async def test_outcome_route_security(owner_api: Api, client: Any, db: AsyncSession) -> None:
    org = await org_id(db)
    pub = await _s(db, org, "pub")
    priv = await _s(db, org, "priv", privacy="console_pin")
    mgr = await login_as(client, db, "manager")
    other = await _s(db, await mk_org(db, "B"), "ob")
    assert (await owner_api.send("POST", f"/signals/{priv}/outcome", {"result": "saved"})).status_code == 423
    assert (await owner_api.send("POST", f"/signals/{other}/outcome", {"result": "saved"})).status_code == 404
    assert (await owner_api.send("POST", f"/signals/{uuid.uuid4()}/outcome", {"result": "saved"})).status_code == 404
    await verify_pin(owner_api)
    assert (await owner_api.send("POST", f"/signals/{priv}/outcome", {"result": "saved"})).status_code == 200
    assert (await mgr.send("POST", f"/signals/{pub}/outcome", {"result": "saved"})).status_code == 403
    assert (await _row(db, other)).outcome is None


async def test_outcomes_kept_promise_or_confirmed_ask_ignored_when_dismissed(db: AsyncSession) -> None:
    org = await mk_org(db)
    k = await _s(db, org, "dk", status="dismissed")
    await _promise(db, org, k, kept=True)
    a = await _s(db, org, "da", status="dismissed")
    await db.execute(text("""INSERT INTO ops.asks (org_id, code, source, ref_id, decision_kind, summary, signal_id, state)
                             VALUES (:o, 'AB3CD', 'signal', core.uuid_v7(), 'self', 'x', :s, 'confirmed')"""), {"o": org, "s": a})
    await db.commit()
    assert (await _run(org))["set"] == 0
    assert (await _row(db, k)).outcome is None and (await _row(db, a)).outcome is None


async def test_outcome_route_records_exact_choice_not_forced_acted(owner_api: Api, db: AsyncSession) -> None:
    org = await org_id(db)
    sid = await _s(db, org, "ig", status="open")
    r = await owner_api.send("POST", f"/signals/{sid}/outcome", {"outcome": "ignored", "result": "lost"})
    assert r.status_code == 200 and r.json()["outcome"] == "ignored"
    assert tuple(await _row(db, sid)) == ("ignored", "lost", "owner")
    r = await owner_api.send("POST", f"/signals/{sid}/outcome", {"result": "neutral"})            # mặc định acted
    assert tuple(await _row(db, sid)) == ("acted", "neutral", "owner")
    assert (await owner_api.send("POST", f"/signals/{sid}/outcome", {"outcome": "bậy", "result": "lost"})).status_code == 422
