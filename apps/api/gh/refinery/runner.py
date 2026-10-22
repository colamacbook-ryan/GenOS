"""Một lượt sàng lọc: Kho thô → Kho sạch (ARCHITECTURE §4.3).

1. Nhận lô: `FOR UPDATE SKIP LOCKED` trên refinery.event_state (nhiều worker song song không trùng, không sót).
2. Quy tắc tất định → nhiễu bị loại kèm mã quy tắc (giải thích được).
3. LLM trích xuất theo từng phần lô, kiểm chứng cứ; model chết → tin trả về `pending`, lượt sau thử lại.
4. Độ tin ≥ ngưỡng → clean.meaning_units + clean.evidence; dưới ngưỡng → `lowconf` chờ Sếp xem.
5. Embedding, chấm điểm người/nhóm, ghi sổ tay, cảnh báo theo quy tắc, phát gh.clean.ready + WebSocket.

v0.1.55 (G4, J2): giữa bước 2 và 3, nếu `triage.prefilter` bật (mặc định), `gh.refinery.prefilter` bỏ qua tin trùng hẳn
và tin rác chắc chắn (quy tắc + Jev, hoặc quy tắc khi chưa có Jev) — KHÔNG gửi model; tin vẫn nằm trong Kho thô với
trạng thái `discarded` (`detail.discarded_by = 'prefilter'`) để xem lại ở "Tin đã bỏ qua". Mọi lỗi của bước này ⇒ chạy
như cũ (không mất tin).
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import orjson
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh import realtime
from gh.chassis import actionlog
from gh.chassis.bus import CLEAN_READY, EventBus
from gh.data.common import iso, live_person, outbound_to_sql, raw_code
from gh.gen import decider as decmod
from gh.memory import notebook
from gh.providers.router import AGY_BACKGROUND_TIMEOUT_S, ModelRouter, ModelUnavailable, raise_alert
from gh.refinery import extract, prefilter, scoring, triage
from gh.refinery.rules import EventCtx, Outcome, RuleDef, RuleHit, apply_outputs, evaluate

log = logging.getLogger("gh.refinery")

AGENT_KEY = "core.refinery"
MAX_ATTEMPTS = 3
#: Review P3 v0.1.64: cả lượt sập (hoặc lượt bị giết giữa chừng) hoàn lại `attempts` nên một tin "độc" làm sập lượt thì
#: được nhận lại vô hạn. Mỗi lần sập cộng `detail.crashes`; đủ chừng này lần ⇒ `error` (bộ lập lịch vẫn nhận lại có
#: giới hạn `MAX_ERROR_RETRIES`, dải pipeline hiện số tin lỗi).
MAX_CRASHES = 3
#: Phần SET dùng chung cho UPDATE `refinery.event_state s` của tin đang `processing` mà lượt xử lý đã sập/chết:
#: hoàn lại lần thử, cộng bộ đếm sập, quá hạn ⇒ `error`. Tham số: :maxc, :why.
CRASH_SET = """state = CASE WHEN COALESCE((s.detail ->> 'crashes')::int, 0) + 1 >= :maxc
                            THEN 'error' ELSE 'pending' END,
               attempts = GREATEST(s.attempts - 1, 0), updated_at = now(),
               detail = s.detail || jsonb_build_object('crashes', COALESCE((s.detail ->> 'crashes')::int, 0) + 1,
                                                       'last_error', CAST(:why AS text))"""
#: J2: tổng thời gian tối đa hỏi Jev cho CẢ lô trước khi trích xuất (giây). Quá hạn ⇒ coi như Jev lỗi (thận trọng:
#: chỉ bỏ tin trùng hẳn) — bước tối ưu chi phí không được ăn hết `JOB_TIMEOUT` của job sàng lọc.
PREFILTER_JEV_BUDGET_S = 20.0
#: Review lần 2 v0.1.62: ngân sách thời gian của MỘT lượt sàng lọc (giây) — nhỏ hơn `gh.worker.JOB_TIMEOUT` (300) để
#: arq không giết job giữa chừng. Mỗi phần lô gọi model tối đa hai lượt (lượt gọi lại khi JSON sai), mỗi lượt tới
#: `AGY_BACKGROUND_TIMEOUT_S`. Trước mỗi phần kế tiếp, còn lại ít hơn `PART_WORST_S` (trường hợp xấu nhất của một phần)
#: ⇒ dừng sớm, trả các bản ghi chưa xử lý về `pending` cho lượt sau. Phần đầu luôn chạy (lượt nào cũng tiến được).
RUN_TIME_BUDGET_S = 270.0
PART_WORST_S = 2 * AGY_BACKGROUND_TIMEOUT_S
ATTENTION_TYPES = {"Complained", "MentionsCompetitor", "WentSilent"}
OPEN_THREAD_TYPES = {"PromisedDelivery", "ScheduledMeeting", "AskedStatus", "RequestedSample", "SentQuotation"}
#: Giá trị `core.persons.person_type` quy tắc sàng lọc được phép đặt (khớp bảng lookup / hợp đồng PersonType).
PERSON_TYPES = frozenset({"customer", "partner", "staff", "candidate", "learner", "supplier"})
RULE_KIND_ALERT = {"risk": "repeated_complaint", "competition": "competitor", "hr": "people_signal",
                   "intent": "opportunity_signal", "custom": "rule_alert", "hygiene": "rule_alert"}


@dataclass
class Schedule:
    interval_seconds: int
    count_threshold: int
    batch_size: int
    min_confidence: float


@dataclass
class Ev:
    event_id: uuid.UUID
    received_at: datetime
    seq: int
    occurred_at: datetime
    kind: str
    text: str
    direction: str
    group_id: uuid.UUID | None
    group_code: str | None
    person_id: uuid.UUID | None
    person_code: str | None
    ref: str = ""
    group_name: str | None = None
    person_name: str | None = None
    outcome: Outcome | None = None
    sets: dict[str, Any] = field(default_factory=dict)
    mentions: bool = False          # tin tag trực tiếp Gen/agent — lọc trước (J2) không bao giờ bỏ qua


def alert_title(label: str, group_name: str | None, person_name: str | None, personnel: bool) -> str:
    """Tiêu đề cảnh báo do quy tắc sinh: tên sự kiện tiếng Việt + nơi xảy ra, không lộ mã nội bộ (`Complained`).
    Tin riêng: kèm tên người, trừ tín hiệu nhân sự (không đưa tên người vào tiêu đề)."""
    if group_name:
        return f"{label} · nhóm {group_name}"
    if person_name and not personnel:
        return f"{label} · tin riêng của {person_name}"
    return f"{label} · tin riêng"


async def event_labels(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(text("SELECT code, label_vi FROM core.lookup WHERE kind = 'event_type'"))).all()
    return {r.code: r.label_vi for r in rows}


def run_out(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "trigger": r.trigger, "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
            "input_count": r.input_count, "clean_count": r.clean_count, "lowconf_count": r.lowconf_count,
            "noise_count": r.noise_count, "error_count": r.error_count, "status": r.status, "error": r.error}


@dataclass
class RunStats:
    run_id: uuid.UUID
    trigger: str
    total: int = 0
    processed: int = 0
    clean: int = 0
    lowconf: int = 0
    noise: int = 0
    errors: int = 0
    held: int = 0
    prefilter_skipped: int = 0     # v0.1.55 (J2): tin bỏ qua trước khi trích xuất (đã tính cả vào noise/processed)
    deferred: int = 0              # hết ngân sách thời gian lượt: tin trả về pending cho lượt sau (không phải lỗi)
    status: str = "running"
    error: str | None = None
    unit_ids: list[str] = field(default_factory=list)

    def progress(self) -> dict[str, Any]:
        return {"run_id": str(self.run_id), "trigger": self.trigger, "processed": self.processed,
                "total": self.total, "clean": self.clean, "lowconf": self.lowconf, "noise": self.noise,
                "errors": self.errors, "status": self.status}


async def load_schedule(db: AsyncSession, org_id: uuid.UUID) -> Schedule:
    await db.execute(text("INSERT INTO refinery.schedule (org_id) VALUES (:o) ON CONFLICT DO NOTHING"),
                     {"o": org_id})
    r = (await db.execute(text("""SELECT interval_seconds, count_threshold, batch_size, min_confidence
                                  FROM refinery.schedule WHERE org_id = :o"""), {"o": org_id})).one()
    return Schedule(r.interval_seconds, r.count_threshold, r.batch_size, float(r.min_confidence))


async def load_rules(db: AsyncSession, org_id: uuid.UUID, *, only_enabled: bool = True) -> list[RuleDef]:
    rows = (await db.execute(text("""
        SELECT r.id, r.code, r.name, r.kind, r.is_enabled, v.version, v.threshold, v.conditions, v.outputs,
               v.prompt_hint
        FROM refinery.rules r JOIN refinery.rule_versions v ON v.rule_id = r.id AND v.version = r.current_version
        WHERE r.org_id = :o ORDER BY r.code"""), {"o": org_id})).all()
    return [RuleDef(str(r.id), r.code, r.name, r.kind, r.version, float(r.threshold), r.conditions, r.outputs,
                    r.prompt_hint, r.is_enabled) for r in rows if r.is_enabled or not only_enabled]


async def event_types(db: AsyncSession) -> list[str]:
    return list((await db.execute(text("""SELECT code FROM core.lookup WHERE kind = 'event_type' AND is_active
                                          ORDER BY sort_order"""))).scalars().all())


async def unanswered(db: AsyncSession, ev: Ev, sender_identity: uuid.UUID | None) -> int:
    """Số tin liên tiếp của cùng người gửi (tính cả tin này) chưa có ai khác trả lời, trong 48 giờ."""
    if sender_identity is None:
        return 0
    # v0.1.67 (F050): tin 1-1 — chỉ tin ĐI gửi cho ĐÚNG người này mới cắt chuỗi (trả lời khách khác không tính).
    # Định danh của người đó trên kênh (kể cả hồ sơ gộp) lấy một lần; `outbound_to_sql` ghép tin đi theo người nhận.
    keys = (await db.execute(text("""
        SELECT array_agg(x.id) AS ids, array_agg(NULLIF(split_part(x.external_id, '@', 1), '')) AS ext
        FROM core.person_identities me
        JOIN core.person_identities x ON x.person_id = me.person_id AND x.channel_id = me.channel_id
        WHERE me.id = :s"""), {"s": sender_identity})).one()
    rows = (await db.execute(text(f"""
        SELECT e.sender_identity_id FROM raw.events e
        WHERE e.org_id = (SELECT org_id FROM raw.events WHERE id = :e AND received_at = :r)
          AND e.channel_id = (SELECT channel_id FROM raw.events WHERE id = :e AND received_at = :r)
          AND e.group_id IS NOT DISTINCT FROM CAST(:g AS uuid)
          AND e.occurred_at <= :t AND e.occurred_at > CAST(:t AS timestamptz) - interval '48 hours'
          AND (CAST(:g AS uuid) IS NOT NULL OR e.sender_identity_id = :s
               OR {outbound_to_sql("e", "CAST(:ids AS uuid[])", "CAST(:ext AS text[])")})
        ORDER BY e.occurred_at DESC LIMIT 12"""),  # noqa: S608 — outbound_to_sql là hằng nội bộ
        {"e": ev.event_id, "r": ev.received_at, "g": ev.group_id, "t": ev.occurred_at, "s": sender_identity,
         "ids": list(keys.ids or [sender_identity]), "ext": [x for x in (keys.ext or []) if x]})).all()
    n = 0
    for r in rows:
        if r.sender_identity_id != sender_identity:
            break
        n += 1
    return n


class Refinery:
    def __init__(self, sm: async_sessionmaker[AsyncSession], redis: Redis, router: ModelRouter,
                 bus: EventBus | None = None, *, jev_transport: httpx.AsyncBaseTransport | None = None,
                 time_budget_s: float = RUN_TIME_BUDGET_S, part_worst_s: float = PART_WORST_S):
        self.sm, self.redis, self.router, self.bus = sm, redis, router, bus
        self.time_budget_s, self.part_worst_s = time_budget_s, part_worst_s   # chỉ test chỉnh
        self.jev_transport = jev_transport      # chỉ test tiêm (Jev giả); production = None

    async def _progress(self, org_id: uuid.UUID, st: RunStats, final: bool = False) -> None:
        await realtime.publish(self.redis, "refinery.progress", st.progress(), org_id=org_id)
        if final:
            from gh.shell.routes import header_payload  # tránh import vòng khi nạp worker

            # Bản ghi lượt chạy (cùng hình dạng GET /refinery/runs) cho danh sách lượt gần nhất.
            async with self.sm() as db:
                row = (await db.execute(text("SELECT * FROM refinery.runs WHERE id = :i"), {"i": st.run_id})).one()
                header = await header_payload(db, org_id)
            await realtime.publish(self.redis, "refinery.run", run_out(row), org_id=org_id)
            # Độ tin cậy dữ liệu hôm nay trên header đổi sau mỗi lượt.
            await realtime.publish(self.redis, "header", header, org_id=org_id)

    # ─── nhận lô ─────────────────────────────────────────────────────────────

    async def _claim(self, db: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID, limit: int,
                     event_ids: list[uuid.UUID] | None) -> list[Any]:
        filt = "AND s.event_id = ANY(:ids)" if event_ids else ""
        return list((await db.execute(text(f"""
            UPDATE refinery.event_state s SET state = 'processing', run_id = :run, updated_at = now(),
                   attempts = s.attempts + 1
            FROM (SELECT event_id, event_received_at FROM refinery.event_state s
                  WHERE s.org_id = :o AND s.state = 'pending' {filt}
                  ORDER BY s.fast DESC, s.event_received_at LIMIT :n FOR UPDATE SKIP LOCKED) pick
            WHERE s.event_id = pick.event_id AND s.event_received_at = pick.event_received_at
            RETURNING s.event_id, s.event_received_at"""),  # noqa: S608 — filt là hằng
            {"o": org_id, "run": run_id, "n": limit, "ids": event_ids or []})).all())

    async def _load(self, db: AsyncSession, claimed: list[Any]) -> tuple[list[Ev], dict[uuid.UUID, Any]]:
        ids = [c.event_id for c in claimed]
        rows = (await db.execute(text("""
            SELECT e.id, e.received_at, e.seq, e.occurred_at, e.kind, e.body_text, e.direction, e.group_id,
                   e.sender_identity_id, e.mentions_agent, g.code AS group_code, g.name AS group_name,
                   p.id AS person_id, p.code AS person_code, p.display_name AS person_name
            FROM raw.events e LEFT JOIN core.groups g ON g.id = e.group_id
            LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            LEFT JOIN core.persons p ON p.id = pi.person_id
            WHERE e.id = ANY(:ids) ORDER BY e.occurred_at"""), {"ids": ids})).all()
        evs, senders = [], {}
        for r in rows:
            pid = r.person_id
            if pid is not None:
                live = await live_person(db, pid)
                if live != pid:
                    pid = live
                    lp = (await db.execute(text("SELECT code, display_name FROM core.persons WHERE id = :i"),
                                           {"i": live})).one()
                    r_code, r_name = lp.code, lp.display_name
                else:
                    r_code, r_name = r.person_code, r.person_name
            else:
                r_code, r_name = None, None
            ev = Ev(r.id, r.received_at, r.seq, r.occurred_at, r.kind, r.body_text or "", r.direction,
                    r.group_id, r.group_code, pid, r_code, group_name=r.group_name, person_name=r_name,
                    mentions=bool(r.mentions_agent))
            evs.append(ev)
            senders[r.id] = r.sender_identity_id
        return evs, senders

    async def _set_state(self, db: AsyncSession, ev: Ev, state: str, run_id: uuid.UUID, *, label: str | None,
                         confidence: float | None, detail: dict[str, Any]) -> None:
        await db.execute(text("""
            UPDATE refinery.event_state SET state = :s, run_id = :run, label = :l, confidence = :c,
                   detail = detail || CAST(:d AS jsonb), updated_at = now()
            WHERE event_id = :e AND event_received_at = :r"""),
            {"s": state, "run": run_id, "l": label, "c": confidence, "d": orjson.dumps(detail, default=str).decode(),
             "e": ev.event_id, "r": ev.received_at})

    # ─── lượt chạy ───────────────────────────────────────────────────────────

    async def start_run(self, org_id: uuid.UUID, trigger: str, requested_by: uuid.UUID | None = None,
                        status: str = "running") -> uuid.UUID:
        async with self.sm() as db:
            run_id = (await db.execute(text("""
                INSERT INTO refinery.runs (org_id, trigger, requested_by, status) VALUES (:o, :t, :u, :s)
                RETURNING id"""), {"o": org_id, "t": trigger, "u": requested_by, "s": status})).scalar_one()
            await db.commit()
        return run_id  # type: ignore[no-any-return]

    async def run(self, org_id: uuid.UUID, trigger: str, *, run_id: uuid.UUID | None = None,
                  event_ids: list[uuid.UUID] | None = None, limit: int | None = None) -> RunStats:
        if run_id is None:
            run_id = await self.start_run(org_id, trigger)
        else:
            async with self.sm() as db:
                await db.execute(text("UPDATE refinery.runs SET status = 'running', started_at = now() WHERE id = :i"),
                                 {"i": run_id})
                await db.commit()
        st = RunStats(run_id, trigger)
        try:
            await self._run(org_id, st, event_ids, limit)
        except Exception as exc:  # noqa: BLE001 — lượt lỗi không làm chết worker; tin đang xử lý trả về pending
            log.exception("Lượt sàng lọc %s lỗi", run_id)
            st.status, st.error = "failed", str(exc)[:500]
            async with self.sm() as db:
                # Lỗi hạ tầng của cả lượt: hoàn lại lần thử đã tính lúc nhận (chỉ JSON sai định dạng mới tính — F055),
                # nhưng ĐẾM số lần sập: đủ MAX_CRASHES ⇒ `error`, không thử lại vô hạn.
                crashed = (await db.execute(text(f"""UPDATE refinery.event_state s SET {CRASH_SET}
                                                    WHERE s.run_id = :r AND s.state = 'processing'
                                                    RETURNING s.state"""),  # noqa: S608 — CRASH_SET là hằng
                                            {"r": run_id, "maxc": MAX_CRASHES, "why": str(exc)[:300]})).scalars().all()
                n_err = sum(1 for x in crashed if x == "error")
                st.errors += n_err
                st.processed += n_err
                await db.commit()
        async with self.sm() as db:
            await db.execute(text("""
                UPDATE refinery.runs SET finished_at = now(), input_count = :i, clean_count = :c, lowconf_count = :l,
                       noise_count = :n, error_count = :e, processed = :p, status = :s, error = COALESCE(:err, error)
                WHERE id = :id"""),
                {"i": st.total, "c": st.clean, "l": st.lowconf, "n": st.noise, "e": st.errors, "p": st.processed,
                 "s": st.status if st.status != "running" else "done", "err": st.error, "id": run_id})
            await db.commit()
        if st.status == "running":
            st.status = "done"
        await self._progress(org_id, st, final=True)
        if st.unit_ids and self.bus is not None:
            await self.bus.publish(CLEAN_READY, "meaning_units", {"ids": st.unit_ids, "run_id": str(run_id)},
                                   actor="agent:core.refinery", org_id=org_id)
        return st

    async def _run(self, org_id: uuid.UUID, st: RunStats, event_ids: list[uuid.UUID] | None,
                   limit: int | None) -> None:
        deadline = time.monotonic() + self.time_budget_s
        async with self.sm() as db:
            sched = await load_schedule(db, org_id)
            claimed = await self._claim(db, org_id, st.run_id, limit or sched.batch_size, event_ids)
            await db.commit()
        st.total = len(claimed)
        await self._progress(org_id, st)
        if not claimed:
            return
        async with self.sm() as db:
            rules = await load_rules(db, org_id)
            etypes = await event_types(db)
            evs, senders = await self._load(db, claimed)
            for i, ev in enumerate(evs, 1):
                ev.ref = f"E{i}"
                ev.outcome = evaluate(rules, EventCtx(ev.text, ev.kind, await unanswered(db, ev, senders[ev.event_id])))
            # Bước 1: nhiễu theo quy tắc.
            to_model: list[Ev] = []
            for ev in evs:
                assert ev.outcome is not None
                if ev.outcome.discarded_by:
                    label = apply_outputs(ev.outcome.hits)["sets"].get("label", "Noise")
                    await self._set_state(db, ev, "discarded", st.run_id, label=label, confidence=None,
                                          detail={"rules": ev.outcome.codes, "discarded_by": ev.outcome.discarded_by})
                    st.noise += 1
                    st.processed += 1
                    await self._raw_state(org_id, ev, "discarded", label, None)
                else:
                    to_model.append(ev)
            await self._rule_hits(db, evs, rules)
            await db.commit()
        to_model = await self._prefilter(org_id, st, to_model)
        await self._progress(org_id, st)
        # Bước 2–4: model theo từng phần lô.
        parts = extract.chunks([self._event_in(ev) for ev in to_model])
        for idx, part in enumerate(parts):
            if idx > 0 and deadline - time.monotonic() < self.part_worst_s:
                left = {p.ref for rest in parts[idx:] for p in rest}
                await self._defer([e for e in to_model if e.ref in left], st)
                break
            by_ref = {e.ref: e for e in to_model if e.ref in {p.ref for p in part}}
            try:
                routed = await self.router.generate(org_id, agent_key=AGENT_KEY, purpose="refinery",
                                                    messages=extract.build_messages(part, rules, etypes))
                try:
                    ex = extract.validate(routed.text, set(by_ref), {r.code for r in rules})
                except ValueError:
                    routed = await self.router.generate(org_id, agent_key=AGENT_KEY, purpose="refinery",
                                                        messages=extract.build_messages(part, rules, etypes))
                    ex = extract.validate(routed.text, set(by_ref), {r.code for r in rules})
            except ModelUnavailable as e:
                await self._release(list(by_ref.values()), st, f"model không khả dụng: {e}")
                st.status, st.error = "failed", f"Model không khả dụng — {len(by_ref)} bản ghi giữ chờ"
                continue
            except ValueError as e:
                await self._release(list(by_ref.values()), st, str(e), count_error=True)
                continue
            await self._apply(org_id, st, sched, rules, by_ref, ex, routed.model)
            await self._progress(org_id, st)

    async def _prefilter(self, org_id: uuid.UUID, st: RunStats, to_model: list[Ev]) -> list[Ev]:
        """J2 (v0.1.55): bỏ qua tin trùng hẳn / rác chắc chắn trước khi trích xuất, xếp tin "Jev một mình chấm rác" cuối
        lô. LỖI BẤT KỲ (Jev, DB) ⇒ trả nguyên `to_model` — tin vẫn đi đường cũ, không bao giờ mất tin vì bước này."""
        if not to_model:
            return to_model
        try:
            kept, skipped = await self._prefilter_apply(org_id, st, to_model)
        except Exception:  # noqa: BLE001 — lọc trước là tối ưu chi phí, không được làm hỏng lượt sàng lọc
            log.exception("Lọc trước (J2) lỗi — trích xuất như cũ")
            return to_model
        for ev, _reason in skipped:
            try:
                await self._raw_state(org_id, ev, "discarded", "Noise", None)
            except Exception:  # noqa: BLE001 — chỉ là sự kiện thời gian thực
                log.warning("Không phát được raw.state cho tin lọc trước", exc_info=True)
        return kept

    async def _prefilter_apply(self, org_id: uuid.UUID, st: RunStats,
                               to_model: list[Ev]) -> tuple[list[Ev], list[tuple[Ev, str]]]:
        async with self.sm() as db:
            cfg = await triage.get_settings(db, org_id)
            if cfg.get("prefilter") is False:
                return to_model, []
            dec: decmod.Decider = (await decmod.load_decider(db, org_id, transport=self.jev_transport)
                                   if cfg["use_jev"] else decmod.LlmDecider())
            items = [prefilter.PrefilterItem(ev.text, ev.person_id, ev.group_id, ev.mentions, ev.occurred_at)
                     for ev in to_model]
            hashes = {k[0] for it in items if (k := prefilter.exact_key(it)) is not None}
            known = await prefilter.dup_index(db, org_id, hashes, [ev.event_id for ev in to_model])
        n = len(items)
        has_jev = dec.name != "llm"
        labels: list[str | None] | None = None
        if has_jev:
            # Chỉ hỏi Jev về tin chưa chắc chắn trùng hẳn và không phải tin tag (đỡ lượt gọi vô ích).
            first = prefilter.decide(items, rules_spam=[False] * n, jev_labels=None, dup_index=known)
            ask = [not r.skip and not it.tagged for r, it in zip(first, items, strict=True)]
            jobs = [(ev.text, ev.kind) for ev, a in zip(to_model, ask, strict=True) if a]
            try:
                asked = await asyncio.wait_for(triage.ask_jev_batch(dec, jobs), timeout=PREFILTER_JEV_BUDGET_S)
            except TimeoutError:
                log.warning("Lọc trước (J2): hỏi Jev quá %.0f giây — coi như Jev lỗi cho lô này",
                            PREFILTER_JEV_BUDGET_S)
                asked = [None] * len(jobs)
            answers = iter(asked)
            labels = []
            for a in ask:
                d = next(answers) if a else None
                labels.append(d.value if d is not None else None)
        rules = [prefilter.rule_spam(it.text, jev_present=has_jev) for it in items]
        results = prefilter.decide(items, rules_spam=rules, jev_labels=labels, dup_index=known)
        skipped: list[tuple[Ev, str]] = [(ev, r.reason or "") for ev, r in zip(to_model, results, strict=True)
                                         if r.skip]
        if skipped:
            async with self.sm() as db:
                for ev, reason in skipped:
                    await self._set_state(db, ev, "discarded", st.run_id, label="Noise", confidence=None,
                                          detail={"discarded_by": "prefilter", "reason": reason})
                await db.commit()
            st.noise += len(skipped)
            st.processed += len(skipped)
            st.prefilter_skipped += len(skipped)
        return prefilter.lower_last(to_model, results), skipped

    def _event_in(self, ev: Ev) -> extract.EventIn:
        assert ev.outcome is not None
        hints = [f"{h.rule.code} ({h.score:.2f})" for h in ev.outcome.hits + ev.outcome.near if h.score > 0]
        return extract.EventIn(ev.ref, str(ev.event_id), ev.group_code, ev.person_code,
                               ev.occurred_at.isoformat(), ev.kind, ev.text, hints)

    async def _defer(self, evs: list[Ev], st: RunStats) -> None:
        """Hết ngân sách thời gian của lượt: trả tin chưa xử lý về `pending` và HOÀN lại lần thử đã tính lúc nhận lô
        (hoãn không phải lỗi — không được dồn tới `MAX_ATTEMPTS`)."""
        note = orjson.dumps({"deferred": "hết ngân sách thời gian của lượt — để lượt sau"}).decode()
        async with self.sm() as db:
            for ev in evs:
                await db.execute(text("""
                    UPDATE refinery.event_state SET state = 'pending', attempts = GREATEST(attempts - 1, 0),
                           updated_at = now(), detail = detail || CAST(:d AS jsonb)
                    WHERE event_id = :e AND event_received_at = :r"""),
                    {"e": ev.event_id, "r": ev.received_at, "d": note})
            await db.commit()
        st.deferred += len(evs)
        log.info("Lượt sàng lọc %s hết ngân sách thời gian — hoãn %d tin cho lượt sau", st.run_id, len(evs))

    async def _release(self, evs: list[Ev], st: RunStats, reason: str, count_error: bool = False) -> None:
        """Trả tin về pending (model chết / trả sai); quá số lần thử → `error`.

        F055: chỉ lần model trả SAI ĐỊNH DẠNG (`count_error`) mới tính vào `attempts`; model không khả dụng là lỗi hạ
        tầng — hoàn lại lần thử đã cộng lúc nhận (`_claim`), để vài lượt model chết không đẩy tin vào `error`.
        Tin `error` được bộ lập lịch tự nhận lại sau một lúc (`scheduler.requeue_errors`)."""
        async with self.sm() as db:
            for ev in evs:
                attempts = (await db.execute(text("""SELECT attempts FROM refinery.event_state
                                                     WHERE event_id = :e AND event_received_at = :r"""),
                                             {"e": ev.event_id, "r": ev.received_at})).scalar_one()
                final = count_error and attempts >= MAX_ATTEMPTS
                await db.execute(text("""
                    UPDATE refinery.event_state SET state = :s, updated_at = now(),
                           attempts = CASE WHEN :refund THEN GREATEST(attempts - 1, 0) ELSE attempts END,
                           detail = detail || CAST(:d AS jsonb)
                    WHERE event_id = :e AND event_received_at = :r"""),
                    {"s": "error" if final else "pending", "e": ev.event_id, "r": ev.received_at,
                     "refund": not count_error,
                     "d": orjson.dumps({"last_error": reason[:300]}).decode()})
                if final:
                    st.errors += 1
                    st.processed += 1
                    await self._raw_state(None, ev, "error", None, None)
            await db.commit()

    async def _raw_state(self, org_id: uuid.UUID | None, ev: Ev, state: str, label: str | None,
                         confidence: float | None) -> None:
        await realtime.publish(self.redis, "raw.state", {"id": str(ev.event_id), "code": raw_code(ev.seq),
                                                         "state": state, "label": label, "confidence": confidence},
                               org_id=org_id)

    async def _rule_hits(self, db: AsyncSession, evs: list[Ev], rules: list[RuleDef]) -> None:
        await self._count_rule_hits(db, [h.rule.id for ev in evs for h in (ev.outcome.hits if ev.outcome else [])])

    async def _count_rule_hits(self, db: AsyncSession, rule_ids: list[str]) -> None:
        counts: dict[str, int] = {}
        for rid in rule_ids:
            counts[rid] = counts.get(rid, 0) + 1
        hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        for rid, n in counts.items():
            await db.execute(text("""INSERT INTO refinery.rule_hits_hourly (rule_id, hour, hits) VALUES (:r, :h, :n)
                                     ON CONFLICT (rule_id, hour) DO UPDATE SET hits = rule_hits_hourly.hits + :n"""),
                             {"r": uuid.UUID(rid), "h": hour, "n": n})

    async def _apply(self, org_id: uuid.UUID, st: RunStats, sched: Schedule, rules: list[RuleDef],
                     by_ref: dict[str, Ev], ex: extract.Extraction, model: str) -> None:
        rule_by_code = {r.code: r for r in rules}
        best: dict[str, tuple[str, float, str]] = {}      # ref → (state, confidence, event_type)
        lowconf_detail: dict[str, list[dict[str, Any]]] = {}
        new_units: list[tuple[uuid.UUID, datetime, str]] = []
        touched: set[tuple[str, uuid.UUID]] = set()
        async with self.sm() as db:
            w = await scoring.weights(db, org_id)
            labels = await event_labels(db)
            for u in ex.units:
                evs = [by_ref[r] for r in u.evidence]
                first = evs[0]
                # Quy tắc áp cho đơn vị: khớp tất định trên tin chứng cứ, hoặc model tin ≥ ngưỡng quy tắc.
                hits = {h.rule.code: h for ev in evs for h in (ev.outcome.hits if ev.outcome else [])
                        if not h.rule.discards}
                applied = list(hits.values())
                model_confirmed: list[RuleHit] = []
                for code, conf in u.rules.items():
                    r = rule_by_code.get(code)
                    if r and code not in hits and not r.discards and conf >= r.threshold:
                        applied.append(RuleHit(r, conf, ["model"]))
                        model_confirmed.append(applied[-1])
                out = apply_outputs(applied)
                event_type = u.event_type
                side = u.side or {"demand": "demand", "supply": "supply"}.get(str(out["sets"].get("side")), None)
                inbound_person_id = next((e.person_id for e in evs if e.direction == "inbound" and e.person_id), None)
                person_id = inbound_person_id or first.person_id
                group_id = first.group_id
                eng = await scoring.engagement(db, person_id)
                scores = scoring.unit_scores(u.signals, out["adds"], u.confidence, eng)
                score = scoring.total(scores, w)
                if u.confidence < sched.min_confidence:
                    for ev in evs:
                        lowconf_detail.setdefault(ev.ref, []).append(
                            {"event_type": event_type, "conclusion": u.conclusion, "confidence": u.confidence,
                             "rules": [h.rule.code for h in applied]})
                        if best.get(ev.ref, ("", -1.0, ""))[0] != "clean" and u.confidence > best.get(
                                ev.ref, ("", -1.0, ""))[1]:
                            best[ev.ref] = ("lowconf", u.confidence, event_type)
                    continue
                # «Lượt/24h» của quy tắc gồm cả khớp do MODEL xác nhận (v0.1.71); khớp tất định đã đếm ở `_rule_hits`.
                await self._count_rule_hits(db, [h.rule.id for h in model_confirmed])
                entities = dict(u.entities)
                set_type = out["sets"].get("person_type")
                if set_type:
                    entities["person_type"] = set_type
                unit_id, observed_at = await self._insert_unit(
                    db, org_id, st.run_id, evs, first, person_id, group_id, event_type, side, u, entities, scores,
                    score, applied)
                new_units.append((unit_id, observed_at, u.conclusion))
                st.unit_ids.append(str(unit_id))
                # Loại người chỉ ghi cho người ĐẾN TỪ TIN NHẬN VÀO: bằng chứng toàn tin gửi đi là của tài khoản mình
                # (Sếp/nhân viên) — model bị dẫn dắt cũng không được gán loại cho họ.
                if inbound_person_id and set_type:
                    await self._write_person_type(db, org_id, inbound_person_id, str(set_type), unit_id)
                if person_id:
                    touched.add(("person", person_id))
                if group_id:
                    touched.add(("group", group_id))
                for ev in evs:
                    if best.get(ev.ref, ("", -1.0, ""))[0] != "clean" or u.confidence > best[ev.ref][1]:
                        best[ev.ref] = ("clean", u.confidence, event_type)
                await self._notebooks(db, org_id, unit_id, event_type, u.conclusion, first, person_id, group_id)
                if out["alert"]:
                    kinds = {h.rule.kind for h in applied if any("alert" in o for o in h.rule.outputs)}
                    kind = next(iter(kinds), "custom")
                    await raise_alert(db, org_id, alert_type=RULE_KIND_ALERT.get(kind, "rule_alert"),
                                      priority=out["alert"],
                                      title=alert_title(labels.get(event_type, event_type), first.group_name,
                                                        first.person_name, kind == "hr"),
                                      summary=u.conclusion, subject_type="person" if person_id else "group",
                                      subject_id=person_id or group_id,
                                      evidence=[{"type": "meaning_unit", "id": str(unit_id)}] +
                                               [{"type": "raw", "id": str(e.event_id)} for e in evs],
                                      personnel_related=kind == "hr", redis=self.redis)
            # Trạng thái từng tin của phần lô.
            for ref, ev in by_ref.items():
                if ref in best:
                    state, conf, et = best[ref]
                    detail: dict[str, Any] = {"rules": ev.outcome.codes if ev.outcome else [], "model": model}
                    if ref in lowconf_detail:
                        detail["lowconf"] = lowconf_detail[ref][:5]
                    await self._set_state(db, ev, state, st.run_id, label=et, confidence=round(conf, 3), detail=detail)
                elif ref in ex.noise:
                    state, conf, et = "discarded", 0.0, "Noise"
                    await self._set_state(db, ev, "discarded", st.run_id, label="Noise", confidence=None,
                                          detail={"discarded_by": "model", "model": model})
                else:
                    state, conf, et = "lowconf", 0.0, ""
                    await self._set_state(db, ev, "lowconf", st.run_id, label=None, confidence=None,
                                          detail={"reason": "model_skipped", "model": model})
                st.processed += 1
                if state == "clean":
                    st.clean += 1
                elif state == "lowconf":
                    st.lowconf += 1
                else:
                    st.noise += 1
            if ex.rejected:
                await db.execute(text("""UPDATE refinery.runs SET error = COALESCE(error || '; ', '') || :e
                                         WHERE id = :i"""),
                                 {"e": f"loại {ex.rejected} kết luận: {'; '.join(ex.reject_reasons[:3])}"[:300],
                                  "i": st.run_id})
            for subject_type, sid in touched:
                await scoring.refresh_subject(db, org_id, subject_type, sid, w)
            await db.commit()
        for ref, ev in by_ref.items():
            state, conf, et = best.get(ref, ("discarded" if ref in ex.noise else "lowconf", 0.0, ""))
            await self._raw_state(org_id, ev, state, et or ("Noise" if state == "discarded" else None),
                                  round(conf, 3) if conf else None)
        await self._embed(org_id, new_units)

    async def _write_person_type(self, db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, ptype: str,
                                 unit_id: uuid.UUID) -> None:
        """F045: kết quả quy tắc "set person_type" ghi ngược vào hồ sơ người. CHỈ nâng từ 'unknown' và không chạm hồ sơ
        Sếp đã tự đặt loại (`attrs.person_type_locked`) — giá trị Sếp chọn luôn thắng quy tắc."""
        if ptype not in PERSON_TYPES:
            return
        changed = (await db.execute(text("""
            UPDATE core.persons SET person_type = :t, updated_at = now()
            WHERE id = :p AND person_type = 'unknown' AND deleted_at IS NULL AND merged_into_id IS NULL
              AND COALESCE(attrs ->> 'person_type_locked', '') <> 'true'
            RETURNING id"""), {"t": ptype, "p": person_id})).scalar()
        if changed:
            await actionlog.record(db, org_id=org_id, actor_type="agent", actor_id=AGENT_KEY,
                                   action="person.type_inferred", target_type="person", target_id=str(person_id),
                                   detail={"person_type": ptype, "meaning_unit": str(unit_id)})

    async def _insert_unit(self, db: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID, evs: list[Ev], first: Ev,
                           person_id: uuid.UUID | None, group_id: uuid.UUID | None, event_type: str,
                           side: str | None, u: extract.Unit, entities: dict[str, Any], scores: dict[str, float],
                           score: int, applied: list[Any]) -> tuple[uuid.UUID, datetime]:
        main_rule = next((h.rule for h in sorted(applied, key=lambda h: -h.score)), None)
        observed_at = min(e.occurred_at for e in evs)
        unit_id = (await db.execute(text("""
            INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, side, conclusion,
                                             entities, confidence, run_id, rule_id, rule_version, score, scores,
                                             rule_codes)
            VALUES (:o, :obs, :g, :p, :et, :side, :c, CAST(:ent AS jsonb), :conf, :run, :rid, :rv, :score,
                    CAST(:scores AS jsonb), :codes)
            RETURNING id"""),
            {"o": org_id, "obs": observed_at, "g": group_id, "p": person_id, "et": event_type, "side": side,
             "c": u.conclusion, "ent": orjson.dumps(entities, default=str).decode(), "conf": round(u.confidence, 3),
             "run": run_id, "rid": uuid.UUID(main_rule.id) if main_rule else None,
             "rv": main_rule.version if main_rule else None, "score": score,
             "scores": orjson.dumps(scores).decode(), "codes": sorted({h.rule.code for h in applied})})).scalar_one()
        for ev in evs:
            # Chạy lại trên cùng tin: bản cũ giữ nguyên, trỏ sang bản mới.
            await db.execute(text("""
                UPDATE clean.meaning_units m SET superseded_by = :new
                FROM clean.evidence x
                WHERE x.raw_event_id = :e AND x.meaning_unit_id = m.id AND x.meaning_observed_at = m.observed_at
                  AND m.superseded_by IS NULL AND m.id <> :new AND m.event_type = :et"""),
                {"new": unit_id, "e": ev.event_id, "et": event_type})
            await db.execute(text("""
                INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote)
                VALUES (:m, :mo, :e, :r, :q) ON CONFLICT DO NOTHING"""),
                {"m": unit_id, "mo": observed_at, "e": ev.event_id, "r": ev.received_at, "q": ev.text[:280]})
        return unit_id, observed_at

    async def _notebooks(self, db: AsyncSession, org_id: uuid.UUID, unit_id: uuid.UUID, event_type: str,
                         conclusion: str, first: Ev, person_id: uuid.UUID | None, group_id: uuid.UUID | None) -> None:
        section = "attention_now" if event_type in ATTENTION_TYPES else (
            "open_threads" if event_type in OPEN_THREAD_TYPES else "rolling_context")
        refs = [{"type": "meaning_unit", "id": str(unit_id)}, {"type": "raw", "id": str(first.event_id),
                                                                "code": raw_code(first.seq)}]
        if person_id:
            await notebook.append(db, org_id, "person", person_id, section, conclusion, refs + (
                [{"type": "group", "id": str(group_id), "code": first.group_code}] if group_id else []),
                "agent:core.refinery")
        if group_id:
            who = f"{first.person_code}: " if first.person_code else ""
            await notebook.append(db, org_id, "group", group_id, section, f"{who}{conclusion}", refs + (
                [{"type": "person", "id": str(person_id), "code": first.person_code}] if person_id else []),
                "agent:core.refinery")

    async def _embed(self, org_id: uuid.UUID, units: list[tuple[uuid.UUID, datetime, str]]) -> None:
        if not units:
            return
        try:
            vecs = await self.router.embed(org_id, [c for _, _, c in units])
        except Exception as exc:  # noqa: BLE001 — embedding không được làm hỏng lượt sàng lọc
            log.warning("embedding lỗi: %s", exc)
            return
        if not vecs:
            return
        async with self.sm() as db:
            for (uid, obs, _), v in zip(units, vecs, strict=True):
                await db.execute(text("""UPDATE clean.meaning_units SET embedding = CAST(:v AS vector)
                                         WHERE id = :i AND observed_at = :o"""),
                                 {"v": "[" + ",".join(f"{x:.6f}" for x in v) + "]", "i": uid, "o": obs})
            await db.commit()
