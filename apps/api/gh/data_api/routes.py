"""API tầng dữ liệu (docs/api/phase-2.md): dải pipeline, Kho thô, sàng lọc, quy tắc, Kho sạch, sổ tay, danh tính."""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require, require_pin
from gh.biz.core.scope import direct_visible, direct_visible_sql, group_row_view_sql, group_visible
from gh.chassis import actionlog
from gh.data.common import (
    CHANNEL_NAME,
    LISTENING_MODES,
    RAW_SELECT,
    fetch_raw,
    iso,
    mask_text,
    raw_code,
    raw_item,
    ref,
    since_cutoff,
)
from gh.db import DB
from gh.errors import ApiError, conflict, field_errors, not_found
from gh.identity import service as identity
from gh.memory import notebook
from gh.providers.router import ModelUnavailable
from gh.refinery import extract, scoring
from gh.refinery.presets import DEFAULT_WEIGHTS
from gh.refinery.rules import KINDS, EventCtx, apply_outputs, evaluate, validate
from gh.refinery.runner import AGENT_KEY, event_types, load_rules, load_schedule, run_out
from gh.refinery.scheduler import next_run

router = APIRouter(tags=["data"])
READ = require("data.read")
MANAGE = require("data.manage")

def _mask(text_: str | None, user: service.CurrentUser) -> str | None:
    return mask_text(text_, user.role_code == rbac.OWNER)


def _raw_out(r: Any, user: service.CurrentUser, *, with_payload: bool = False) -> dict[str, Any]:
    item = raw_item(r, with_payload=with_payload and user.role_code == rbac.OWNER)
    item["text"] = _mask(item["text"], user)
    if with_payload and user.role_code != rbac.OWNER:
        item["payload"] = {"hidden": "Chỉ Owner xem được bản gốc từ bridge"}
    return item


def _uuid(v: str | None, field: str) -> uuid.UUID | None:
    if not v:
        return None
    try:
        return uuid.UUID(v)
    except ValueError as e:
        raise field_errors({field: "Mã không hợp lệ"}) from e


# ─── Dải pipeline ───────────────────────────────────────────────────────────

@router.get("/data/pipeline")
async def pipeline(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    o = user.org_id
    r = (await db.execute(text("""
        SELECT
          (SELECT count(DISTINCT c.id) FROM core.channels c JOIN core.channel_sessions s ON s.channel_id = c.id
             WHERE c.org_id = :o AND s.state = 'active' AND s.ended_at IS NULL) AS channels_live,
          (SELECT count(*) FROM core.groups
            WHERE org_id = :o AND left_at IS NULL AND listen_mode = ANY(:modes)) AS groups_listening,
          (SELECT count(*) FROM raw.events WHERE org_id = :o) AS raw_total,
          (SELECT count(*) FROM refinery.event_state WHERE org_id = :o AND state = 'pending') AS raw_pending,
          (SELECT count(*) FROM refinery.event_state WHERE org_id = :o AND state = 'error') AS raw_error,
          (SELECT count(*) FROM clean.meaning_units WHERE org_id = :o AND superseded_by IS NULL) AS clean_total"""),
        {"o": o, "modes": list(LISTENING_MODES)})).one()
    sched = await load_schedule(db, o)
    return {**dict(r._mapping), "interval_seconds": sched.interval_seconds, "count_threshold": sched.count_threshold}


# ─── Kho thô ────────────────────────────────────────────────────────────────

def _raw_filters(user: service.CurrentUser, channel: str | None, group_id: str | None, state: str | None,
                 since: str | None, label: str | None, min_confidence: float | None) -> tuple[str, dict[str, Any]]:
    where: list[str] = ["e.org_id = :o"]
    # Tin 1-1 (không nhóm) chỉ Owner hoặc người được giao đúng người đó xem nguyên văn (review v0.1.65 P2-2).
    dm_sql, params = direct_visible_sql(
        user, "e.group_id",
        "(SELECT pi2.person_id FROM core.person_identities pi2 WHERE pi2.id = e.sender_identity_id)")
    where.append(dm_sql)
    if channel:
        where.append("c.type = :ch")
        params["ch"] = channel
    gid = _uuid(group_id, "group_id")
    if gid:
        where.append("e.group_id = :g")
        params["g"] = gid
    if state:
        if state == "pending":
            where.append("(s.state IS NULL OR s.state = 'pending')")
        else:
            where.append("s.state = :st")
            params["st"] = state
    cut = since_cutoff(since)
    if cut:
        where.append("e.received_at >= :cut")
        params["cut"] = cut
    if label:
        where.append("s.label = :lb")
        params["lb"] = label
    if min_confidence:
        where.append("s.confidence >= :mc")
        params["mc"] = min_confidence
    return " WHERE " + " AND ".join(where), params


@router.get("/raw")
async def raw_list(request: Request, cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                   channel: Literal["zalo", "whatsapp", "telegram"] | None = None, group_id: str | None = None,
                   state: Literal["pending", "processing", "clean", "lowconf", "discarded", "error"] | None = None,
                   since: str | None = "24h", label: str | None = None,
                   min_confidence: float | None = Query(None, ge=0, le=1),
                   user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    where, params = _raw_filters(user, channel, group_id, state, since, label, min_confidence)
    params["o"] = user.org_id
    total = (await db.execute(text(f"""
        SELECT count(*) FROM raw.events e JOIN core.channels c ON c.id = e.channel_id
        LEFT JOIN refinery.event_state s ON s.event_id = e.id AND s.event_received_at = e.received_at
        {where}"""), params)).scalar_one()  # noqa: S608 — where do code dựng từ hằng
    page_where = where
    if cursor:
        if not cursor.isdigit():
            raise field_errors({"cursor": "Con trỏ không hợp lệ"})
        page_where += " AND e.seq < :cur"
        params["cur"] = int(cursor)
    rows = (await db.execute(text(RAW_SELECT + page_where + " ORDER BY e.seq DESC LIMIT :lim"),
                             {**params, "lim": limit + 1})).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [_raw_out(r, user) for r in rows], "next_cursor": str(rows[-1].seq) if more else None,
            "total": total}


@router.get("/raw/by-group")
async def raw_by_group(since: str | None = "24h", limit: int = Query(7, ge=1, le=50),
                       user: service.CurrentUser = Depends(READ),
                       db: AsyncSession = DB) -> list[dict[str, Any]]:
    cut = since_cutoff(since) or datetime(1970, 1, 1, tzinfo=UTC)
    rows = (await db.execute(text(f"""
        SELECT g.id, g.code, g.name, count(*) AS n FROM raw.events e JOIN core.groups g ON g.id = e.group_id
        WHERE e.org_id = :o AND e.received_at >= :cut AND {group_row_view_sql(user.role_code, "g")}
        GROUP BY g.id ORDER BY n DESC LIMIT :lim"""),  # noqa: S608 — biểu thức nội bộ
        {"o": user.org_id, "cut": cut, "lim": limit})).all()
    return [{"group": ref(r.id, r.code, r.name), "n": r.n} for r in rows]


@router.get("/raw/export")
async def raw_export(channel: Literal["zalo", "whatsapp", "telegram"] | None = None, group_id: str | None = None,
                     state: str | None = None, since: str | None = "24h", label: str | None = None,
                     min_confidence: float | None = Query(None, ge=0, le=1),
                     user: service.CurrentUser = Depends(MANAGE), _pin: Any = Depends(require_pin("data.export")),
                     db: AsyncSession = DB) -> Response:
    where, params = _raw_filters(user, channel, group_id, state, since, label, min_confidence)
    rows = (await db.execute(text(RAW_SELECT + where + " ORDER BY e.seq DESC LIMIT 100000"),
                             {**params, "o": user.org_id})).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["code", "received_at", "occurred_at", "channel", "group", "person", "direction", "kind", "text",
                "label", "confidence", "state"])
    for r in rows:
        it = raw_item(r)
        w.writerow([it["code"], it["received_at"], it["occurred_at"], it["channel"]["name"],
                    (it["group"] or {}).get("code"), (it["person"] or {}).get("code"), it["direction"], it["kind"],
                    it["text"], it["label"], it["confidence"], it["state"]])
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="raw.exported",
                           target_type="raw",
                           detail={"rows": len(rows), "filters": {k: str(v) for k, v in params.items()}},
                           ip=user.ip)
    name = f"kho-tho-{datetime.now(UTC):%Y%m%d-%H%M}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{name}"'})


@router.get("/raw/{event_id}")
async def raw_detail(event_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                     db: AsyncSession = DB) -> dict[str, Any]:
    r = await fetch_raw(db, event_id)
    if r is None or (await db.execute(text("SELECT org_id FROM raw.events WHERE id = :i"),
                                      {"i": event_id})).scalar() != user.org_id \
            or not await direct_visible(db, user, r.group_id, r.person_id):
        raise not_found("Bản ghi thô")
    out = _raw_out(r, user, with_payload=True)
    units = (await db.execute(text("""
        SELECT m.id, m.event_type, m.conclusion FROM clean.evidence x
        JOIN clean.meaning_units m ON m.id = x.meaning_unit_id AND m.observed_at = x.meaning_observed_at
        WHERE x.raw_event_id = :e AND m.superseded_by IS NULL"""), {"e": event_id})).all()
    out["meaning_units"] = [{"id": str(u.id), "event_type": u.event_type, "conclusion": u.conclusion} for u in units]
    detail = (await db.execute(text("SELECT detail FROM refinery.event_state WHERE event_id = :e"),
                               {"e": event_id})).scalar()
    out["refinery"] = detail or {}
    return out


# ─── Sàng lọc ───────────────────────────────────────────────────────────────

class ScheduleIn(BaseModel):
    interval_seconds: int = Field(ge=60, le=86400)
    count_threshold: int = Field(ge=1, le=100000)
    batch_size: int = Field(ge=1, le=2000)
    min_confidence: float = Field(ge=0, le=1)


@router.get("/refinery/schedule")
async def get_schedule(request: Request, user: service.CurrentUser = Depends(READ),
                       db: AsyncSession = DB) -> dict[str, Any]:
    return await next_run(db, request.app.state.redis, user.org_id)


@router.put("/refinery/schedule")
async def put_schedule(body: ScheduleIn, request: Request, user: service.CurrentUser = Depends(MANAGE),
                       db: AsyncSession = DB) -> dict[str, Any]:
    await save_schedule(db, user.org_id, body)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="refinery.schedule_changed", target_type="refinery", detail=body.model_dump(),
                           ip=user.ip)
    return await next_run(db, request.app.state.redis, user.org_id)


async def save_schedule(db: AsyncSession, org_id: uuid.UUID, body: ScheduleIn) -> None:
    await db.execute(text("""
        INSERT INTO refinery.schedule (org_id, interval_seconds, count_threshold, batch_size, min_confidence)
        VALUES (:o, :i, :c, :b, :m)
        ON CONFLICT (org_id) DO UPDATE SET interval_seconds = :i, count_threshold = :c, batch_size = :b,
               min_confidence = :m, updated_at = now()"""),
        {"o": org_id, "i": body.interval_seconds, "c": body.count_threshold, "b": body.batch_size,
         "m": body.min_confidence})


@router.post("/refinery/run", status_code=202)
async def run_now(user: service.CurrentUser = Depends(MANAGE), db: AsyncSession = DB) -> dict[str, Any]:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('refinery.manual:' || :o))"),
                     {"o": str(user.org_id)})
    busy = (await db.execute(text("""SELECT 1 FROM refinery.runs WHERE org_id = :o AND trigger = 'manual'
                                     AND status IN ('queued', 'running') LIMIT 1"""), {"o": user.org_id})).scalar()
    if busy:
        raise conflict("REFINERY_BUSY", "Đang có một lượt chạy ngay chưa xong")
    run_id = (await db.execute(text("""INSERT INTO refinery.runs (org_id, trigger, requested_by, status)
                                       VALUES (:o, 'manual', :u, 'queued') RETURNING id"""),
                               {"o": user.org_id, "u": user.id})).scalar_one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="refinery.run_requested", target_type="refinery_run", target_id=str(run_id),
                           ip=user.ip)
    await db.execute(text("SELECT pg_notify('raw_ingested', :p)"), {"p": f"{user.org_id}|manual|{run_id}"})
    return {"run_id": str(run_id)}


_run_out = run_out


@router.get("/refinery/runs")
async def runs(limit: int = Query(5, ge=1, le=100), user: service.CurrentUser = Depends(READ),
               db: AsyncSession = DB) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""SELECT * FROM refinery.runs WHERE org_id = :o
                                     ORDER BY started_at DESC LIMIT :n"""), {"o": user.org_id, "n": limit})).all()
    return [_run_out(r) for r in rows]


# ─── Quy tắc ────────────────────────────────────────────────────────────────

class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(max_length=20)
    conditions: list[dict[str, Any]] = Field(max_length=20)
    outputs: list[dict[str, Any]] = Field(max_length=20)
    threshold: float = Field(ge=0, le=1)
    prompt_hint: str | None = Field(default=None, max_length=1000)


class RulePatch(BaseModel):
    enabled: bool


async def _kind_labels(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(text("SELECT code, label_vi FROM core.lookup WHERE kind = 'rule_kind'"))).all()
    return {r.code: r.label_vi for r in rows}


async def rule_payloads(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    labels = await _kind_labels(db)
    rows = (await db.execute(text("""
        SELECT r.id, r.code, r.name, r.kind, r.is_enabled, r.current_version, r.updated_at, v.threshold,
               v.conditions, v.outputs, v.prompt_hint,
               COALESCE((SELECT sum(h.hits) FROM refinery.rule_hits_hourly h
                         WHERE h.rule_id = r.id AND h.hour > now() - interval '24 hours'), 0) AS hits
        FROM refinery.rules r JOIN refinery.rule_versions v ON v.rule_id = r.id AND v.version = r.current_version
        WHERE r.org_id = :o ORDER BY r.code"""), {"o": org_id})).all()
    return [_rule_out(r, labels) for r in rows]


def _rule_out(r: Any, labels: dict[str, str]) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "name": r.name, "kind": r.kind, "kind_label": labels.get(r.kind, r.kind),
            "enabled": r.is_enabled, "version": r.current_version, "threshold": float(r.threshold),
            "hits_24h": int(r.hits), "conditions": r.conditions, "outputs": r.outputs, "prompt_hint": r.prompt_hint,
            "updated_at": iso(r.updated_at)}


async def _one_rule(db: AsyncSession, org_id: uuid.UUID, rule_id: uuid.UUID) -> dict[str, Any]:
    for r in await rule_payloads(db, org_id):
        if r["id"] == str(rule_id):
            return r
    raise not_found("Quy tắc")


def _check_rule(body: RuleIn) -> None:
    errors = validate(body.conditions, body.outputs)
    if body.kind not in KINDS:
        errors["kind"] = f"Loại phải thuộc {', '.join(KINDS)}"
    if errors:
        raise field_errors(errors)


async def create_rule(db: AsyncSession, org_id: uuid.UUID, body: RuleIn, user_id: uuid.UUID | None,
                      code: str | None = None, enabled: bool = True) -> uuid.UUID:
    if code is None:
        last = (await db.execute(text("""SELECT max(substring(code from 'R-(\\d+)')::int) FROM refinery.rules
                                         WHERE org_id = :o"""), {"o": org_id})).scalar()
        code = f"R-{(last or 0) + 1:02d}"
    rule_id = (await db.execute(text("""INSERT INTO refinery.rules (org_id, code, name, kind, is_enabled)
                                        VALUES (:o, :c, :n, :k, :e) RETURNING id"""),
                                {"o": org_id, "c": code, "n": body.name, "k": body.kind, "e": enabled})).scalar_one()
    await _version(db, rule_id, 1, body, user_id)
    return rule_id  # type: ignore[no-any-return]


async def _version(db: AsyncSession, rule_id: uuid.UUID, version: int, body: RuleIn, user_id: uuid.UUID | None) -> None:
    await db.execute(text("""
        INSERT INTO refinery.rule_versions (rule_id, version, conditions, outputs, threshold, prompt_hint, created_by)
        VALUES (:r, :v, CAST(:c AS jsonb), CAST(:o AS jsonb), :t, :h, :u)"""),
        {"r": rule_id, "v": version, "c": orjson.dumps(body.conditions).decode(),
         "o": orjson.dumps(body.outputs).decode(), "t": body.threshold, "h": body.prompt_hint, "u": user_id})


@router.get("/rules")
async def list_rules(user: service.CurrentUser = Depends(READ),
                     db: AsyncSession = DB) -> list[dict[str, Any]]:
    return await rule_payloads(db, user.org_id)


@router.post("/rules", status_code=201)
async def post_rule(body: RuleIn, user: service.CurrentUser = Depends(MANAGE),
                    db: AsyncSession = DB) -> dict[str, Any]:
    _check_rule(body)
    rule_id = await create_rule(db, user.org_id, body, user.id)
    out = await _one_rule(db, user.org_id, rule_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="rule.created",
                           target_type="rule", target_id=out["code"], target_label=body.name, ip=user.ip)
    return out


@router.put("/rules/weights")
async def put_weights(body: list[dict[str, Any]], user: service.CurrentUser = Depends(MANAGE),
                      db: AsyncSession = DB) -> list[dict[str, Any]]:
    await save_weights(db, user.org_id, body)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="rule.weights_changed", target_type="scoring_weights",
                           detail={"weights": body}, ip=user.ip)
    return await weights_payload(db, user.org_id)


async def save_weights(db: AsyncSession, org_id: uuid.UUID, body: list[dict[str, Any]]) -> None:
    values: dict[str, int] = {}
    for w in body:
        d, v = w.get("dimension"), w.get("value")
        if d not in scoring.DIMENSIONS or not isinstance(v, int) or not 0 <= v <= 100:
            raise field_errors({"_": "Mỗi trọng số cần dimension hợp lệ và value nguyên 0–100"})
        values[d] = v
    if set(values) != set(scoring.DIMENSIONS) or sum(values.values()) != 100:
        raise ApiError(422, "VALIDATION", "Dữ liệu chưa hợp lệ", errors={"_": "Tổng trọng số phải bằng 100%"})
    now = datetime.now(UTC)
    for d, v in values.items():
        await db.execute(text("""INSERT INTO refinery.scoring_weights (org_id, dimension, weight, valid_from)
                                 VALUES (:o, :d, :w, :t)"""), {"o": org_id, "d": d, "w": v / 100, "t": now})


async def weights_payload(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    w = await scoring.weights(db, org_id)
    labels = {r.code: r.label_vi for r in (await db.execute(text(
        "SELECT code, label_vi FROM core.lookup WHERE kind = 'score_dimension'"))).all()}
    defaults = dict(DEFAULT_WEIGHTS)
    return [{"dimension": d, "label": labels.get(d, d), "value": round(w[d] * 100) if d in w else defaults[d]}
            for d in scoring.DIMENSIONS]


@router.get("/rules/weights")
async def get_weights(user: service.CurrentUser = Depends(READ),
                      db: AsyncSession = DB) -> list[dict[str, Any]]:
    return await weights_payload(db, user.org_id)


class RuleTestIn(BaseModel):
    raw_event_id: uuid.UUID | None = None
    text: str | None = Field(default=None, max_length=4000)
    group_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None


@router.post("/rules/test")
async def test_rule(body: RuleTestIn, request: Request, user: service.CurrentUser = Depends(READ),
                    db: AsyncSession = DB) -> dict[str, Any]:
    """Thử quy tắc trên một tin — không ghi gì vào kho (chỉ lượt gọi model được tính hạn mức)."""
    if body.raw_event_id:
        r = await fetch_raw(db, body.raw_event_id)
        if r is None or not await direct_visible(db, user, r.group_id, r.person_id):
            raise not_found("Bản ghi thô")
        msg, kind, code = r.body_text or "", r.kind, raw_code(r.seq)
        group = ref(r.group_id, r.group_code, r.group_name)
        person = ref(r.person_id, r.person_code, r.person_name)
    elif body.text:
        msg, kind, code = body.text, "text", None
        group = await _named(db, "core.groups", body.group_id, "name")
        person = await _named(db, "core.persons", body.person_id, "display_name")
    else:
        raise field_errors({"text": "Cần raw_event_id hoặc text"})
    rules = await load_rules(db, user.org_id)
    sched = await load_schedule(db, user.org_id)
    outcome = evaluate(rules, EventCtx(msg, kind))
    output: list[dict[str, str]] = []
    result: dict[str, Any] = {"input": {"code": code, "text": _mask(msg, user)}, "matched_rules": outcome.codes,
                              "discarded_by": outcome.discarded_by, "confidence": None, "would_write": "discarded",
                              "model": None}
    if outcome.discarded_by:
        output.append({"key": "label", "value": f"Noise — loại bởi {outcome.discarded_by}"})
        result["output"] = output
        return result
    ev = extract.EventIn("E1", "test", (group or {}).get("code"), (person or {}).get("code"),
                         datetime.now(UTC).isoformat(), kind, msg,
                         [f"{h.rule.code} ({h.score:.2f})" for h in outcome.hits + outcome.near if h.score > 0])
    router_ = request.app.state.model_router
    try:
        routed = await router_.generate(user.org_id, agent_key=AGENT_KEY, purpose="rule_test",
                                        messages=extract.build_messages([ev], rules, await event_types(db)))
        ex = extract.validate(routed.text, {"E1"}, {r.code for r in rules})
        result["model"] = routed.model
    except (ModelUnavailable, ValueError) as e:
        out = apply_outputs(outcome.hits)
        output.append({"key": "rules", "value": ", ".join(outcome.codes) or "không quy tắc nào khớp"})
        if out["sets"]:
            output.append({"key": "intent", "value": " · ".join(f"{k} = {v}" for k, v in out["sets"].items())})
        output.append({"key": "model", "value": f"không khả dụng — {str(e)[:160]}"})
        result.update(output=output, would_write="lowconf" if outcome.hits else "pending")
        return result
    if not ex.units:
        output.append({"key": "label", "value": "Noise — model không thấy sự kiện có nghĩa"})
        result.update(output=output, would_write="discarded")
        return result
    u = max(ex.units, key=lambda x: x.confidence)
    side = {"demand": "CẦU", "supply": "CUNG"}.get(u.side or "", None)
    output.append({"key": "intent", "value": u.event_type + (f" · side = {side}" if side else "")})
    if person:
        output.append({"key": "person_id", "value": f"{person['code']} · {person['name']}"})
    if group:
        output.append({"key": "group_id", "value": f"{group['code']} · {group['name']}"})
    if u.entities:
        output.append({"key": "entities", "value": " · ".join(str(v) for v in u.entities.values() if v)})
    applied = [h for h in outcome.hits if not h.rule.discards]
    adds = apply_outputs(applied)["adds"]
    scores = scoring.unit_scores(u.signals, adds, u.confidence, 0)
    output.append({"key": "scores", "value": f"độ nóng {scores['heat']:.0f} · tiềm năng {scores['potential']:.0f}"
                                              f" · rủi ro {scores['churn_risk']:.0f}"})
    ok = u.confidence >= sched.min_confidence
    conf_s = f"{u.confidence:.2f}".replace(".", ",")
    thr_s = f"{sched.min_confidence:.2f}".replace(".", ",")
    output.append({"key": "confidence", "value": f"{conf_s} — {'trên' if ok else 'dưới'} ngưỡng {thr_s}, "
                                                  f"{'được ghi vào kho sạch' if ok else 'giữ lại chờ Sếp xem'}"})
    output.append({"key": "conclusion", "value": u.conclusion})
    result.update(output=output, confidence=u.confidence, would_write="clean" if ok else "lowconf",
                  matched_rules=sorted(set(outcome.codes) | {c for c, v in u.rules.items()
                                                              if any(r.code == c and v >= r.threshold for r in rules)}))
    return result


async def _named(db: AsyncSession, table: str, id_: uuid.UUID | None, col: str) -> dict[str, Any] | None:
    if id_ is None:
        return None
    r = (await db.execute(text(f"SELECT id, code, {col} AS name FROM {table} WHERE id = :i"),  # noqa: S608
                          {"i": id_})).one_or_none()
    return ref(r.id, r.code, r.name) if r else None


class TestBatchIn(BaseModel):
    n: int = Field(default=100, ge=1, le=1000)


@router.post("/rules/test-batch")
async def test_batch(body: TestBatchIn, user: service.CurrentUser = Depends(READ),
                     db: AsyncSession = DB) -> dict[str, Any]:
    """Chạy thử bộ quy tắc hiện tại trên n tin gần nhất — chỉ bước tất định, không gọi model, không ghi."""
    rules = await load_rules(db, user.org_id)
    rows = (await db.execute(text("""SELECT body_text, kind FROM raw.events WHERE org_id = :o
                                     ORDER BY seq DESC LIMIT :n"""), {"o": user.org_id, "n": body.n})).all()
    clean = lowconf = discarded = 0
    by_rule: dict[str, int] = {}
    for r in rows:
        o = evaluate(rules, EventCtx(r.body_text or "", r.kind))
        for c in o.codes:
            by_rule[c] = by_rule.get(c, 0) + 1
        if o.discarded_by:
            discarded += 1
        elif o.hits:
            clean += 1
        else:
            lowconf += 1
    return {"n": len(rows), "clean": clean, "lowconf": lowconf, "discarded": discarded, "mode": "rules_only",
            "by_rule": [{"code": c, "hits": n} for c, n in sorted(by_rule.items())]}


@router.get("/rules/{rule_id}/versions")
async def rule_versions(rule_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                        db: AsyncSession = DB) -> list[dict[str, Any]]:
    await _one_rule(db, user.org_id, rule_id)
    rows = (await db.execute(text("""
        SELECT v.version, v.conditions, v.outputs, v.threshold, v.prompt_hint, v.created_at, u.display_name
        FROM refinery.rule_versions v LEFT JOIN core.users u ON u.id = v.created_by
        WHERE v.rule_id = :r ORDER BY v.version DESC"""), {"r": rule_id})).all()
    return [{"version": r.version, "conditions": r.conditions, "outputs": r.outputs, "threshold": float(r.threshold),
             "prompt_hint": r.prompt_hint, "created_at": iso(r.created_at), "created_by": r.display_name or "Hệ thống"}
            for r in rows]


@router.put("/rules/{rule_id}")
async def put_rule(rule_id: uuid.UUID, body: RuleIn, user: service.CurrentUser = Depends(MANAGE),
                   db: AsyncSession = DB) -> dict[str, Any]:
    _check_rule(body)
    cur = (await db.execute(text("""SELECT current_version FROM refinery.rules WHERE id = :i AND org_id = :o
                                    FOR UPDATE"""), {"i": rule_id, "o": user.org_id})).scalar_one_or_none()
    if cur is None:
        raise not_found("Quy tắc")
    await _version(db, rule_id, cur + 1, body, user.id)
    await db.execute(text("""UPDATE refinery.rules SET name = :n, kind = :k, current_version = :v, updated_at = now()
                             WHERE id = :i"""), {"n": body.name, "k": body.kind, "v": cur + 1, "i": rule_id})
    out = await _one_rule(db, user.org_id, rule_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="rule.updated",
                           target_type="rule", target_id=out["code"], target_label=body.name,
                           detail={"version": cur + 1}, ip=user.ip)
    return out


@router.patch("/rules/{rule_id}")
async def patch_rule(rule_id: uuid.UUID, body: RulePatch, user: service.CurrentUser = Depends(MANAGE),
                     db: AsyncSession = DB) -> dict[str, Any]:
    n = (await db.execute(text("""UPDATE refinery.rules SET is_enabled = :e, updated_at = now()
                                  WHERE id = :i AND org_id = :o"""),
                          {"e": body.enabled, "i": rule_id, "o": user.org_id})).rowcount  # type: ignore[attr-defined]
    if not n:
        raise not_found("Quy tắc")
    out = await _one_rule(db, user.org_id, rule_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="rule.enabled" if body.enabled else "rule.disabled", target_type="rule",
                           target_id=out["code"], target_label=out["name"], ip=user.ip)
    return out


# ─── Kho sạch ───────────────────────────────────────────────────────────────

CLEAN_SELECT = """
SELECT m.id, m.observed_at, m.event_type, m.conclusion, m.score, m.confidence, m.created_at,
       g.id AS group_id, g.code AS group_code, g.name AS group_name,
       p.id AS person_id, p.code AS person_code, p.display_name AS person_name,
       r.started_at AS cycle_at,
       ARRAY(SELECT x.raw_event_id FROM clean.evidence x WHERE x.meaning_unit_id = m.id) AS raw_ids
FROM clean.meaning_units m
LEFT JOIN core.groups g ON g.id = m.group_id
LEFT JOIN core.persons p ON p.id = m.person_id
LEFT JOIN refinery.runs r ON r.id = m.run_id
"""


@router.get("/clean")
async def clean_list(cursor: str | None = None, limit: int = Query(50, ge=1, le=200), group_id: str | None = None,
                     person_id: str | None = None, since: str | None = "7d",
                     user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    dm_sql, dm_params = direct_visible_sql(user, "m.group_id", "m.person_id")   # tin 1-1: chỉ Owner/người được giao
    where = ["m.org_id = :o", "m.superseded_by IS NULL", dm_sql]
    params: dict[str, Any] = {"o": user.org_id, **dm_params}
    if (gid := _uuid(group_id, "group_id")) is not None:
        where.append("m.group_id = :g")
        params["g"] = gid
    if (pid := _uuid(person_id, "person_id")) is not None:
        where.append("m.person_id = :p")
        params["p"] = pid
    if (cut := since_cutoff(since)) is not None:
        where.append("m.observed_at >= :cut")
        params["cut"] = cut
    w = " WHERE " + " AND ".join(where)
    total = (await db.execute(text("SELECT count(*) FROM clean.meaning_units m" + w), params)).scalar_one()
    if cursor:
        try:
            ts, cid = cursor.split("|")
            params["cts"], params["cid"] = datetime.fromisoformat(ts), uuid.UUID(cid)
        except ValueError as e:
            raise field_errors({"cursor": "Con trỏ không hợp lệ"}) from e
        w += " AND (m.observed_at, m.id) < (:cts, :cid)"
    rows = (await db.execute(text(CLEAN_SELECT + w + " ORDER BY m.observed_at DESC, m.id DESC LIMIT :lim"),
                             {**params, "lim": limit + 1})).all()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [{"id": str(r.id), "observed_at": iso(r.observed_at), "group": ref(r.group_id, r.group_code, r.group_name),
              "person": ref(r.person_id, r.person_code, r.person_name), "event_type": r.event_type,
              "conclusion": _mask(r.conclusion, user), "score": r.score, "confidence": float(r.confidence),
              "cycle_at": iso(r.cycle_at), "raw_event_ids": [str(x) for x in r.raw_ids]} for r in rows]
    nxt = f"{rows[-1].observed_at.isoformat()}|{rows[-1].id}" if more else None
    return {"items": items, "next_cursor": nxt, "total": total}


@router.get("/clean/agent-params")
async def agent_params(group_id: str | None = None, person_id: str | None = None,
                       user: service.CurrentUser = Depends(READ), db: AsyncSession = DB
                       ) -> list[dict[str, Any]]:
    """Tham số agent đọc khi trực (thiết kế `agentParams`) — số thật từ kho sạch và sổ tay."""
    gid, pid = _uuid(group_id, "group_id"), _uuid(person_id, "person_id")
    group = await _named(db, "core.groups", gid, "name")
    person = await _named(db, "core.persons", pid, "display_name")
    n_units = (await db.execute(text("""
        SELECT count(*) FROM clean.meaning_units WHERE org_id = :o AND superseded_by IS NULL
          AND observed_at > now() - interval '180 days'
          AND (CAST(:g AS uuid) IS NULL OR group_id = :g) AND (CAST(:p AS uuid) IS NULL OR person_id = :p)"""),
        {"o": user.org_id, "g": gid, "p": pid})).scalar_one()
    tokens = 0
    for t, i in (("person", pid), ("group", gid)):
        if i is not None:
            tokens += (await db.execute(text("""SELECT COALESCE(max(token_used), 0) FROM memory.notebooks
                                                WHERE org_id = :o AND subject_type = :t AND subject_id = :i"""),
                                        {"o": user.org_id, "t": t, "i": i})).scalar_one()
    threads = (await db.execute(text("""
        SELECT count(DISTINCT m.group_id) FROM clean.meaning_units m
        WHERE m.org_id = :o AND m.superseded_by IS NULL AND CAST(:p AS uuid) IS NOT NULL AND m.person_id = :p"""),
        {"o": user.org_id, "p": pid})).scalar_one()
    fmt = lambda n: f"{n:,}".replace(",", ".")  # noqa: E731
    return [
        {"key": "group", "label": "ID nhóm đang trực", "value": (group or {}).get("code") or "—",
         "icon": "users-three"},
        {"key": "person", "label": "ID người đang nói", "value": (person or {}).get("code") or "—", "icon": "user"},
        {"key": "clean_read", "label": "Bản ghi sạch được đọc", "value": f"{fmt(n_units)} sự kiện",
         "icon": "database"},
        {"key": "window", "label": "Cửa sổ thời gian", "value": "180 ngày", "icon": "clock"},
        {"key": "memory", "label": "Trí nhớ tạm", "value": f"{fmt(tokens)} token", "icon": "brain"},
        {"key": "history", "label": "Lịch sử nội dung tương quan", "value": f"{threads} luồng liên quan",
         "icon": "git-branch"},
    ]


@router.get("/clean/{unit_id}/evidence")
async def clean_evidence(unit_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                         db: AsyncSession = DB) -> list[dict[str, Any]]:
    dm_sql, dm_params = direct_visible_sql(user, "m.group_id", "m.person_id")
    rows = (await db.execute(text(f"""
        SELECT x.raw_event_id, x.quote FROM clean.evidence x JOIN clean.meaning_units m ON m.id = x.meaning_unit_id
        WHERE x.meaning_unit_id = :u AND m.org_id = :o AND {dm_sql}"""),  # noqa: S608 — biểu thức nội bộ
        {"u": unit_id, "o": user.org_id, **dm_params})).all()
    if not rows:
        raise not_found("Đơn vị ý nghĩa")
    out = []
    for r in rows:
        raw = await fetch_raw(db, r.raw_event_id)
        if raw is not None and await direct_visible(db, user, raw.group_id, raw.person_id):
            out.append({"raw": _raw_out(raw, user), "quote": _mask(r.quote, user)})
    return out


# ─── Sổ tay (lõi) ───────────────────────────────────────────────────────────

class EntryIn(BaseModel):
    section: str
    body: str = Field(min_length=1, max_length=2000)
    pinned: bool = False


class EntryPatch(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=2000)
    pinned: bool | None = None


async def _subject(db: AsyncSession, org_id: uuid.UUID, type_: str, sid: uuid.UUID) -> dict[str, Any]:
    table, col = ("core.persons", "display_name") if type_ == "person" else ("core.groups", "name")
    r = (await db.execute(text(f"SELECT id, code, {col} AS name FROM {table} WHERE id = :i AND org_id = :o"),  # noqa: S608
                          {"i": sid, "o": org_id})).one_or_none()
    if r is None:
        raise not_found("Hồ sơ" if type_ == "person" else "Nhóm")
    return {"type": type_, "id": str(r.id), "code": r.code, "name": r.name}


async def notebook_payload(db: AsyncSession, org_id: uuid.UUID, type_: str, sid: uuid.UUID) -> dict[str, Any]:
    subject = await _subject(db, org_id, type_, sid)
    nb = await notebook.ensure(db, org_id, type_, sid)
    rows = (await db.execute(text("""
        SELECT e.id, e.section, e.body, e.refs, e.author, e.is_pinned, e.created_at, u.display_name AS user_name
        FROM memory.entries e
        LEFT JOIN core.users u ON e.author LIKE 'user:%' AND u.id::text = substring(e.author from 6)
        WHERE e.notebook_id = :n AND e.archived_at IS NULL ORDER BY e.is_pinned DESC, e.created_at DESC"""),
        {"n": nb.id})).all()
    sections = []
    for key, title in notebook.SECTIONS.items():
        entries = [r for r in rows if r.section == key]
        sections.append({"key": key, "title": title, "updated_at": iso(max((e.created_at for e in entries),
                                                                             default=None)),
                         "entries": [{"id": str(e.id), "body": e.body, "refs": e.refs, "pinned": e.is_pinned,
                                      "author": {"type": "user" if e.author.startswith("user:") else "agent",
                                                 "label": e.user_name or ("Core agent" if "refinery" in e.author
                                                                          else "Hệ thống")},
                                      "created_at": iso(e.created_at)} for e in entries]})
    return {"id": str(nb.id), "subject": subject, "token_used": nb.token_used, "token_budget": nb.token_budget,
            "compaction_no": nb.compaction_no, "last_compacted_at": iso(nb.last_compacted_at), "sections": sections}


# F-15: /notebooks chỉ màn Kho sạch dùng và KHÔNG lọc theo phạm vi → khoá theo quyền Kho (data.read /
# data.manage) như phần còn lại của router. Sổ tay có phạm vi cho từng người/nhóm là /notebook của relations.
NB_READ = READ
NB_WRITE = MANAGE


async def _ensure_group_view(db: AsyncSession, user: service.CurrentUser, type_: str, sid: uuid.UUID) -> None:
    """Sổ tay của NHÓM theo «Quyền xem» của nhóm (v0.1.71): ngoài quyền ⇒ 404 như nhóm không tồn tại."""
    if type_ == "group" and not await group_visible(db, user, sid):
        raise not_found("Nhóm")


@router.get("/notebooks/{type_}/{sid}")
async def get_notebook(type_: Literal["person", "group"], sid: uuid.UUID, user: service.CurrentUser = Depends(NB_READ),
                       db: AsyncSession = DB) -> dict[str, Any]:
    await _ensure_group_view(db, user, type_, sid)
    return await notebook_payload(db, user.org_id, type_, sid)


@router.post("/notebooks/{type_}/{sid}/entries", status_code=201)
async def add_entry(type_: Literal["person", "group"], sid: uuid.UUID, body: EntryIn,
                    user: service.CurrentUser = Depends(NB_WRITE), db: AsyncSession = DB
                    ) -> dict[str, Any]:
    if body.section not in notebook.SECTIONS:
        raise field_errors({"section": "Mục không hợp lệ"})
    subject = await _subject(db, user.org_id, type_, sid)
    eid = await notebook.append(db, user.org_id, type_, sid, body.section, body.body, [], f"user:{user.id}",
                                body.pinned)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.entry_added", target_type=type_, target_id=subject["code"],
                           detail={"entry_id": str(eid), "section": body.section, "pinned": body.pinned}, ip=user.ip)
    return {"id": str(eid)}


async def _entry(db: AsyncSession, org_id: uuid.UUID, type_: str, sid: uuid.UUID, eid: uuid.UUID) -> Any:
    r = (await db.execute(text("""
        SELECT e.* FROM memory.entries e JOIN memory.notebooks n ON n.id = e.notebook_id
        WHERE e.id = :e AND n.org_id = :o AND n.subject_type = :t AND n.subject_id = :s AND e.archived_at IS NULL"""),
        {"e": eid, "o": org_id, "t": type_, "s": sid})).one_or_none()
    if r is None:
        raise not_found("Mục sổ tay")
    return r


@router.patch("/notebooks/{type_}/{sid}/entries/{eid}")
async def patch_entry(type_: Literal["person", "group"], sid: uuid.UUID, eid: uuid.UUID, body: EntryPatch,
                      user: service.CurrentUser = Depends(NB_WRITE), db: AsyncSession = DB
                      ) -> dict[str, Any]:
    e = await _entry(db, user.org_id, type_, sid, eid)
    new_id = eid
    if body.body is not None and body.body != e.body:
        # Sửa giữ bản cũ: bản mới thay chỗ, bản cũ lưu trữ và trỏ sang bản mới.
        new_id = (await db.execute(text("""
            INSERT INTO memory.entries (notebook_id, section, body, refs, author, is_pinned, tokens, created_at)
            VALUES (:n, :s, :b, CAST(:r AS jsonb), :a, :p, :t, now()) RETURNING id"""),
            {"n": e.notebook_id, "s": e.section, "b": body.body, "r": orjson.dumps(e.refs).decode(),
             "a": f"user:{user.id}", "p": body.pinned if body.pinned is not None else e.is_pinned,
             "t": notebook.estimate_tokens(body.body)})).scalar_one()
        await db.execute(text("UPDATE memory.entries SET archived_at = now(), replaced_by = :n WHERE id = :e"),
                         {"n": new_id, "e": eid})
    elif body.pinned is not None:
        await db.execute(text("UPDATE memory.entries SET is_pinned = :p WHERE id = :e"), {"p": body.pinned, "e": eid})
    await notebook.recount(db, e.notebook_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.entry_edited", target_type=type_, target_id=str(sid),
                           detail={"entry_id": str(eid), "new_entry_id": str(new_id), "pinned": body.pinned},
                           ip=user.ip)
    return {"id": str(new_id)}


@router.delete("/notebooks/{type_}/{sid}/entries/{eid}", status_code=204)
async def delete_entry(type_: Literal["person", "group"], sid: uuid.UUID, eid: uuid.UUID,
                       user: service.CurrentUser = Depends(NB_WRITE), db: AsyncSession = DB) -> Response:
    e = await _entry(db, user.org_id, type_, sid, eid)
    await db.execute(text("UPDATE memory.entries SET archived_at = now() WHERE id = :e"), {"e": eid})
    await notebook.recount(db, e.notebook_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.entry_archived", target_type=type_, target_id=str(sid),
                           detail={"entry_id": str(eid)}, ip=user.ip)
    return Response(status_code=204)


@router.post("/notebooks/{type_}/{sid}/compact")
async def compact_now(type_: Literal["person", "group"], sid: uuid.UUID, user: service.CurrentUser = Depends(NB_WRITE),
                      db: AsyncSession = DB) -> dict[str, Any]:
    await _subject(db, user.org_id, type_, sid)
    nb = await notebook.ensure(db, user.org_id, type_, sid)
    info = await notebook.compact(db, nb.id, reason="manual")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.compacted", target_type=type_, target_id=str(sid), detail=info or {},
                           ip=user.ip)
    return await notebook_payload(db, user.org_id, type_, sid)


@router.get("/notebooks/{type_}/{sid}/compactions")
async def compactions(type_: Literal["person", "group"], sid: uuid.UUID, user: service.CurrentUser = Depends(NB_READ),
                      db: AsyncSession = DB) -> list[dict[str, Any]]:
    await _subject(db, user.org_id, type_, sid)
    await _ensure_group_view(db, user, type_, sid)
    rows = (await db.execute(text("""
        SELECT c.compaction_no, c.at, c.tokens_before, c.tokens_after, cardinality(c.archived_entries) AS n, c.summary
        FROM memory.compactions c JOIN memory.notebooks n ON n.id = c.notebook_id
        WHERE n.org_id = :o AND n.subject_type = :t AND n.subject_id = :s ORDER BY c.compaction_no DESC"""),
        {"o": user.org_id, "t": type_, "s": sid})).all()
    return [{"compaction_no": r.compaction_no, "at": iso(r.at), "tokens_before": r.tokens_before,
             "tokens_after": r.tokens_after, "archived": r.n, "summary": r.summary} for r in rows]


# ─── Hợp nhất danh tính ────────────────────────────────────────────────────

class SplitIn(BaseModel):
    person_id: uuid.UUID
    identity_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


@router.get("/identity/stats")
async def identity_stats(user: service.CurrentUser = Depends(READ),
                         db: AsyncSession = DB) -> dict[str, int]:
    return await identity.stats(db, user.org_id)


@router.get("/identity/candidates")
async def identity_candidates(status: Literal["pending", "merged", "rejected"] = "pending",
                              user: service.CurrentUser = Depends(READ),
                              db: AsyncSession = DB) -> list[dict[str, Any]]:
    return await identity.candidates(db, user.org_id, status)


@router.get("/identity/candidates/{cid}/evidence")
async def identity_evidence(cid: uuid.UUID, user: service.CurrentUser = Depends(READ),
                            db: AsyncSession = DB) -> list[dict[str, Any]]:
    out = []
    for item in await identity.evidence(db, user.org_id, cid):
        raw = item["raw"]
        if not await direct_visible(db, user, (raw["group"] or {}).get("id"), (raw["person"] or {}).get("id")):
            continue   # tin 1-1: chỉ Owner/người được giao đúng người đó (review v0.1.65 P2-2)
        raw["text"] = _mask(raw["text"], user)
        out.append(item)
    return out


@router.post("/identity/candidates/{cid}/merge")
async def identity_merge(cid: uuid.UUID, user: service.CurrentUser = Depends(MANAGE),
                         _pin: Any = Depends(require_pin("identity.merge")),
                         db: AsyncSession = DB) -> dict[str, Any]:
    out = await identity.merge(db, user.org_id, cid, user.id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="identity.merged", target_type="person", target_id=out["person"]["code"],
                           target_label=out["person"]["name"],
                           detail={"merged": out["merged"]["code"], "candidate_id": str(cid), "log_id": out["log_id"]},
                           ip=user.ip)
    return out


@router.post("/identity/candidates/{cid}/reject")
async def identity_reject(cid: uuid.UUID, user: service.CurrentUser = Depends(MANAGE),
                          db: AsyncSession = DB) -> dict[str, Any]:
    n = await identity.reject(db, user.org_id, cid, user.id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="identity.rejected", target_type="identity_candidate", target_id=str(cid),
                           detail={"pairs": n}, ip=user.ip)
    return {"id": str(cid), "status": "rejected"}


@router.post("/identity/split")
async def identity_split(body: SplitIn, user: service.CurrentUser = Depends(MANAGE),
                         _pin: Any = Depends(require_pin("identity.merge")),
                         db: AsyncSession = DB) -> dict[str, Any]:
    out = await identity.split(db, user.org_id, body.person_id, body.identity_ids, user.id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="identity.split", target_type="person", target_id=out["from"]["code"],
                           target_label=out["from"]["name"],
                           detail={"new_person": out["person"]["code"], "identities": len(body.identity_ids),
                                   "log_id": out["log_id"]}, ip=user.ip)
    return out


@router.get("/identity/history")
async def identity_history(user: service.CurrentUser = Depends(READ),
                           db: AsyncSession = DB) -> list[dict[str, Any]]:
    return await identity.history(db, user.org_id)


@router.post("/identity/history/{log_id}/revert")
async def identity_revert(log_id: uuid.UUID, user: service.CurrentUser = Depends(MANAGE),
                          _pin: Any = Depends(require_pin("identity.merge")),
                          db: AsyncSession = DB) -> dict[str, Any]:
    out = await identity.revert(db, user.org_id, log_id, user.id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="identity.reverted", target_type="identity_log", target_id=str(log_id),
                           detail={"op": out["op"], "person": out["person"]["code"]}, ip=user.ip)
    return out


# ─── Nhật ký lắng nghe (v0.1.68): dòng thời gian gộp tin + sự kiện kênh ──────────────────────────────────────────────
from gh.data_api import listen_log  # noqa: E402

router.include_router(listen_log.router)


__all__ = ["router", "save_schedule", "save_weights", "create_rule", "RuleIn", "ScheduleIn", "rule_payloads",
           "timedelta", "CHANNEL_NAME"]
