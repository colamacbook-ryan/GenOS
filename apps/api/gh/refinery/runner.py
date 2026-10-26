# ruff: noqa: E501
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

v0.1.76 (L2, spec Bước 1 r6.6.1 §4D): thêm đường THEO LUỒNG (`GroupTurns`, cuối tệp) — một lượt = MỘT luồng (`g:<nhóm>` hoặc `dm:…`):
tin mới (≤ 60 tin / 12.000 ký tự, không cắt giữa chuỗi trả lời) + cửa sổ gần (30 tin / 24 giờ) + `retrieve.pack` + Hồ sơ Sếp + chính
sách bối cảnh → `analyze_group` (L3, hiện là stub `_l3_stub`) → `verify.check` → MỘT giao dịch ngắn ghi `refinery.analyses`,
`clean.meaning_units` + bằng chứng đã kiểm, mention, `facts.assert_fact`, sổ tay (chỉ câu "theo lời <người>" kèm tham chiếu). Nhịp, trần
và giãn nhịp ở `gh.refinery.budget`; bộ kích hoạt ở `gh.refinery.scheduler`. Đường cũ (lô 40 tin) giữ nguyên khi tổ chức chưa bật đường theo
luồng (`settings.intel.group_pipeline = 'on'`, hoặc mặc định bật khi L3 thật đã cắm).
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import orjson
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh import ai_cost, realtime
from gh.biz.mem import facts as facts_mod
from gh.chassis import actionlog
from gh.chassis.bus import CLEAN_READY, EventBus
from gh.data.common import iso, live_person, outbound_to_sql, raw_code
from gh.gen import decider as decmod
from gh.memory import notebook
from gh.providers.clients import Message
from gh.providers.router import AGY_BACKGROUND_TIMEOUT_S, ModelRouter, ModelUnavailable, raise_alert
from gh.refinery import (
    _l3_stub as l3,  # L3-PLUG: ĐIỂM CẮM L3 duy nhất (xem docstring `_l3_stub`); sau khi L3 merge đổi nguồn ở đây
)
from gh.refinery import budget, clock, extract, prefilter, scoring, triage
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
#: KHÔNG có "staff" (v0.1.76, security review T14): vai nhân viên chỉ do Sếp đặt qua `PATCH /profile` (Owner + PIN + strong_confirm).
PERSON_TYPES = frozenset({"customer", "partner", "candidate", "learner", "supplier"})
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
    facts_written: int = 0         # v0.1.76 (L2): sự thật `mem.facts` ghi trong lượt theo luồng
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


def attributed(speaker: str | None, conclusion: str) -> str:
    """"theo lời <người>: <kết luận>" — nhãn nguồn BẮT BUỘC của mọi câu vào sổ tay (§4D.6). `speaker` là MÃ người do hệ thống cấp
    (`PER-…`), không phải tên hiển thị (chữ của người ngoài — sổ tay có thể vào prompt về sau); dù vậy vẫn làm phẳng một dòng, cắt gọn,
    bỏ ký tự điều khiển."""
    who = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", speaker or "").split())[:60] or "một thành viên"
    return f"theo lời {who}: {conclusion}"


async def notebook_note(db: AsyncSession, org_id: uuid.UUID, section: str, unit_id: uuid.UUID, conclusion: str, first: Ev,
                        person_id: uuid.UUID | None, group_id: uuid.UUID | None) -> None:
    body = attributed(first.person_code, conclusion)
    refs = [{"type": "meaning_unit", "id": str(unit_id)}, {"type": "raw", "id": str(first.event_id), "code": raw_code(first.seq)}]
    if person_id:
        await notebook.append(db, org_id, "person", person_id, section, body, refs + (
            [{"type": "group", "id": str(group_id), "code": first.group_code}] if group_id else []), "agent:core.refinery")
    if group_id:
        await notebook.append(db, org_id, "group", group_id, section, body, refs + (
            [{"type": "person", "id": str(person_id), "code": first.person_code}] if person_id else []), "agent:core.refinery")


class Refinery:
    def __init__(self, sm: async_sessionmaker[AsyncSession], redis: Redis, router: ModelRouter,
                 bus: EventBus | None = None, *, jev_transport: httpx.AsyncBaseTransport | None = None,
                 time_budget_s: float = RUN_TIME_BUDGET_S, part_worst_s: float = PART_WORST_S,
                 analyzer: Callable[..., Awaitable[Any]] | None = None, verifier: Callable[..., Any] | None = None):
        self.sm, self.redis, self.router, self.bus = sm, redis, router, bus
        self.time_budget_s, self.part_worst_s = time_budget_s, part_worst_s   # chỉ test chỉnh
        self.jev_transport = jev_transport      # chỉ test tiêm (Jev giả); production = None
        #: Đường theo luồng gọi `l3.analyze_group_detailed` / `l3.check`; test (hoặc L6) tiêm bản giả/khác ở đây.
        self.groups = GroupTurns(self, analyzer or l3.analyze_group_detailed, verifier or l3.check)
        self._injected = analyzer is not None

    async def group_mode(self, org_id: uuid.UUID) -> bool:
        """Tổ chức này chạy đường THEO LUỒNG không? `settings.intel.group_pipeline` = 'on'/'off' thắng; không đặt ⇒ bật khi L3 thật
        đã cắm (`l3.IS_STUB` False), tắt khi còn stub (stub không trích gì — bật sẽ làm mất việc trích xuất của đường cũ)."""
        async with self.sm() as db:
            v = (await db.execute(text("SELECT settings->'intel'->>'group_pipeline' FROM core.organizations WHERE id = :o"),
                                  {"o": org_id})).scalar_one_or_none()
        if v == "on":
            if l3.IS_STUB and not self._injected:
                log.warning("settings.intel.group_pipeline='on' bị bỏ qua: L3 vẫn là stub (không trích gì) — giữ đường cũ")
                return False
            return True
        if v == "off":
            return False
        return not l3.IS_STUB

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

    async def _set_state(self, db: AsyncSession, ev: Any, state: str, run_id: uuid.UUID, *, label: str | None,
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
                  event_ids: list[uuid.UUID] | None = None, limit: int | None = None,
                  threads: list[str] | None = None, legacy: bool = False) -> RunStats:
        if run_id is None:
            run_id = await self.start_run(org_id, trigger)
        else:
            async with self.sm() as db:
                await db.execute(text("UPDATE refinery.runs SET status = 'running', started_at = now() WHERE id = :i"),
                                 {"i": run_id})
                await db.commit()
        st = RunStats(run_id, trigger)
        try:
            if not legacy and await self.group_mode(org_id):
                await self.groups.run(org_id, st, trigger=trigger, threads=threads)
            else:
                await self._run(org_id, st, event_ids, limit)
        except Exception as exc:  # noqa: BLE001 — lượt lỗi không làm chết worker; tin đang xử lý trả về pending
            # KHÔNG traceback/thông điệp: lỗi Postgres mang giá trị dữ liệu (vd `invalid input syntax for type uuid: "<giá trị>"`)
            log.error("run: lượt sàng lọc %s (tổ chức %s) lỗi: %s", run_id, org_id, err_code(exc))
            st.status, st.error = "failed", f"Lượt sàng lọc lỗi ({err_code(exc)})"
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
        except Exception as exc:  # noqa: BLE001 — lọc trước là tối ưu chi phí, không được làm hỏng lượt sàng lọc
            log.error("_prefilter: lọc trước (J2) lượt %s lỗi — trích xuất như cũ: %s", st.run_id, err_code(exc))
            return to_model
        for ev, _reason in skipped:
            try:
                await self._raw_state(org_id, ev, "discarded", "Noise", None)
            except Exception as exc:  # noqa: BLE001 — chỉ là sự kiện thời gian thực
                log.warning("_prefilter: không phát được raw.state cho tin %s: %s", ev.event_id, err_code(exc))
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

    async def _raw_state(self, org_id: uuid.UUID | None, ev: Any, state: str, label: str | None,
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
                    routed = None
                    if kind == "risk":      # v0.1.76 (T5): đường nhanh khiếu nại → cổng khach_che_cong_khai_doa_bo; không dựng được ⇒ raise_alert cũ
                        from gh.biz.intel.detectors import conflicts
                        from gh.refinery import clock
                        try:
                            async with db.begin_nested():
                                routed = await conflicts.from_rule(db, org_id, event_ids=[e.event_id for e in evs], person_id=inbound_person_id,
                                                                   group_id=group_id, now=clock.now())
                        except Exception:  # noqa: BLE001 — lỗi đường nhanh không được làm mất cảnh báo: quay về đường cũ
                            log.exception("conflicts.from_rule lỗi, dùng raise_alert cũ")
                    if routed is None:
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
        """Sổ tay KHÔNG phải nguồn sự thật (spec §4D.6, red team 5.1): chỉ ghi câu có NGUỒN — mở đầu "theo lời <người>" — kèm `refs`
        trỏ `meaning_unit` (và tin gốc). Câu kết luận của model không bao giờ vào sổ như một sự thật trần; Gen đọc sự thật ở `mem.facts`."""
        section = "attention_now" if event_type in ATTENTION_TYPES else (
            "open_threads" if event_type in OPEN_THREAD_TYPES else "rolling_context")
        await notebook_note(db, org_id, section, unit_id, conclusion, first, person_id, group_id)

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


# ═══ Đường THEO LUỒNG (v0.1.76, L2) — spec §4D.1, §4D.5, §4D.6 ═══════════════════════════════════════════════════════════
MAX_NEW_EVENTS = 60
MAX_NEW_CHARS = 12_000
CHAIN_SECONDS = 120                      # cùng người gửi trong 2 phút = một chuỗi (không cắt giữa chuỗi)
BURST_MESSAGES = 30                      # một người > 30 tin / 10 phút ⇒ gộp thành một khối
BURST_WINDOW = timedelta(minutes=10)
CONTEXT_EVENTS = 30
CONTEXT_WINDOW = timedelta(hours=24)
RETRY_BACKOFF = timedelta(minutes=5)
HISTORY_EVIDENCE_FIRST_KINDS = ("nghia_vu_loi_hua", "viec_han")        # đơn vị lời hứa/việc có hạn: bằng chứng chọn trước khi áp trần
HISTORY_EVIDENCE_MAX = 10          # tin bằng chứng của khối Lịch sử thêm vào tin ngữ cảnh (để trích được)
HISTORY_EVIDENCE_CHARS = 3000
ACK_LOOKBACK = timedelta(days=1)    # biên lệch giờ của cận dưới updated_at/received_at lời xác nhận (index org/state/updated_at + partition)
BACKLOG_DONE_MAX = 500              # trần đếm "tin sau cửa sổ đã phân tích" (truy vấn có cận; dòng prompt ghi "N" tối đa giá trị này)
ACK_MAX = 30                        # lời xác nhận ngắn (đã loại khỏi lượt) đưa lại vào cửa sổ làm bằng chứng "đã xử lý"
FREE_MODE_RETRY = timedelta(minutes=15)
TEMPLATE_WINDOW = timedelta(days=7)
UNVERIFIED_CONFIDENCE = 0.8              # §4D.4: đơn vị model có trích dẫn verified, chưa qua (d) — T1 tính lại độ chắc


def pick_history_evidence(history: Sequence[Any], have: set[uuid.UUID], *, limit: int = HISTORY_EVIDENCE_MAX) -> dict[uuid.UUID, datetime]:
    """Chọn tin bằng chứng của khối "Lịch sử" (`pack.history`) để kéo vào tin ngữ cảnh, tối đa `limit`. Hai lượt, mỗi lượt mới nhất trước
    (trần cắt tin cũ hơn): đơn vị `kind_hint ∈ HISTORY_EVIDENCE_FIRST_KINDS` (lời hứa, việc có hạn — cần mã tin hứa để đóng sổ) rồi các đơn
    vị còn lại. Tin đã có (`have`) không lặp lại."""
    want: dict[uuid.UUID, datetime] = {}
    ordered = list(reversed(history))
    first = [it for it in ordered if it.meta.get("kind_hint") in HISTORY_EVIDENCE_FIRST_KINDS]
    rest = [it for it in ordered if it.meta.get("kind_hint") not in HISTORY_EVIDENCE_FIRST_KINDS]
    for it in (*first, *rest):
        for rid, rr in it.meta.get("evidence", ()):
            if rid not in have and rid not in want and len(want) < limit:
                want[rid] = rr
    return want


def verified_fields(vu: Any, u: Any, people: list[dict[str, Any]]) -> dict[str, Any]:
    """Phần ĐÃ KIỂM của một đơn vị (§4F.1, `verify.VerifiedUnit`) để lưu ở `clean.meaning_units.entities.v` (T12). Không chứa chữ tin
    ngoài `surface` người (đã có ở `mem.mentions`); thời điểm dạng ISO."""
    def _t(d: datetime | None) -> str | None:
        return d.isoformat() if d is not None else None

    out: dict[str, Any] = {"people": people, "status_hint": getattr(u, "status_hint", None),
                           "condition_met": u.condition.met if getattr(u, "condition", None) is not None else None}
    if vu is None:
        return out
    return out | {"msg_at": _t(vu.msg_at), "event_at": _t(vu.event_at), "event_time_unparsed": bool(vu.event_time_unparsed),
                  "deadline_at": _t(vu.deadline_at), "deadline_unparsed": bool(vu.deadline_unparsed), "ongoing": bool(vu.ongoing),
                  "money_vnd": vu.money_vnd, "status_by_person_id": str(vu.status_by_person_id) if vu.status_by_person_id else None,
                  "status_at": _t(vu.status_at), "txn_state": vu.txn_state, "txn_at": _t(vu.txn_at), "delivery_form": vu.delivery_form}
SCAN_BATCH = 500
GROUP_LOCK_S = 290
MAX_PASSES = 4
FACT_CONTENT_FACTOR = 0.85
REPLY_KEYS = ("reply_to_msg_id", "reply_to", "quoted_msg_id", "replyTo")
OBLIGATION_PREDICATES = frozenset({"promised_to", "owes", "deadline", "scheduled_event"})
FORWARD_KEYS = ("forwarded", "is_forwarded", "forward")


@dataclass(frozen=True)
class Src:
    """Nguồn của một tin được trích trong lượt (tin mới HOẶC tin ngữ cảnh): đủ để ghi bằng chứng/sự thật đúng khoá raw.events."""

    received_at: datetime | None
    group_id: uuid.UUID | None
    person_id: uuid.UUID | None
    forwarded: bool = False


_NO_SRC = Src(None, None, None)
SAFETY_KINDS = frozenset({"rui_ro_lua_dao", "suc_khoe_an_toan"})
SAFETY_GATES = frozenset({"lua_dao_dang_dien_ra", "an_toan"})


def err_code(exc: BaseException) -> str:
    """Mã lỗi an toàn để log: CHỈ tên lớp (+ lớp lỗi DBAPI gốc). Thông điệp/traceback của SQLAlchemy/asyncpg chứa tham số câu lệnh
    (trích dẫn, summary — có thể có OTP/STK) nên không bao giờ được ghi ra log."""
    parts = [type(exc).__name__]
    orig = getattr(exc, "orig", None)
    root = getattr(orig, "__cause__", None) or orig                 # adapter asyncpg của SQLAlchemy bọc lỗi asyncpg thật trong __cause__
    if root is not None:
        parts.append(type(root).__name__)
    code = getattr(root, "sqlstate", None) or getattr(orig, "sqlstate", None)
    if isinstance(code, str) and code.isalnum():
        parts.append(code)
    return "/".join(parts)


def is_safety_unit(u: Any) -> bool:
    """Đơn vị lừa đảo/an toàn (loại hoặc cổng): ghi lỗi thì phải để lại dấu vết + giữ tin chờ (fail-high), không biến mất im lặng."""
    return getattr(u, "kind", None) in SAFETY_KINDS or getattr(u, "gate_candidate", None) in SAFETY_GATES


@dataclass
class GEv:
    """Một tin của lượt theo luồng. `members` = các tin đi theo tin đầu trong MỘT khối gộp xả tin (§4D.5)."""

    event_id: uuid.UUID
    received_at: datetime
    occurred_at: datetime
    seq: int
    kind: str
    body: str
    direction: str
    group_id: uuid.UUID | None
    channel_id: uuid.UUID | None
    sender_identity_id: uuid.UUID | None
    person_id: uuid.UUID | None
    person_name: str | None
    person_code: str | None = None
    ext_id: str = ""
    reply_ext: str | None = None
    forwarded: bool = False
    attempts: int = 0
    gate_class: str | None = None                   # 'safety' | 'gate' | 'hard' | None (luật (a), từ event_state.detail)
    members: list["GEv"] = field(default_factory=list)

    @property
    def message_id(self) -> str:
        return f"m_{self.event_id}"

    @property
    def sender_key(self) -> Any:
        return self.person_id or self.sender_identity_id

    @property
    def all_events(self) -> list["GEv"]:
        return [self, *self.members]


def _chained(prev: GEv, e: GEv) -> bool:
    """`e` nối tiếp `prev` thành một chuỗi: trả lời đúng tin trước, hoặc cùng người gửi trong `CHAIN_SECONDS`."""
    if e.reply_ext and e.reply_ext == prev.ext_id:
        return True
    return (e.sender_key is not None and e.sender_key == prev.sender_key
            and 0 <= (e.occurred_at - prev.occurred_at).total_seconds() <= CHAIN_SECONDS)


def take_window(evs: list[GEv], *, max_n: int = MAX_NEW_EVENTS, max_chars: int = MAX_NEW_CHARS) -> list[GEv]:
    """Phần tin mới của MỘT lượt: tối đa `max_n` tin / `max_chars` ký tự, nhưng KHÔNG cắt giữa một chuỗi trả lời — nếu giới hạn rơi
    giữa chuỗi thì lùi tới ranh giới chuỗi gần nhất (phần còn lại chạy lượt kế, nối tiếp cùng luồng). Luôn lấy ít nhất một tin."""
    out: list[GEv] = []
    chars = 0
    cut: int | None = None
    for i, e in enumerate(evs):
        c = len(e.body)
        if out and (len(out) >= max_n or chars + c > max_chars):
            cut = i
            break
        out.append(e)
        chars += c
    if cut is not None:
        trial, c = list(out), cut
        while len(trial) > 1 and _chained(trial[-1], evs[c]):
            c -= 1
            trial.pop()
        if len(trial) >= 2:                          # lùi về rỗng/1 tin (một người độc thoại liên tục) ⇒ cắt đúng ở giới hạn
            out = trial
    return out


def burst_flags(items: list[tuple[Any, datetime]]) -> list[bool]:
    """Với danh sách (người gửi, giờ) theo thứ tự thời gian: tin nào nằm trong một cửa sổ 10 phút mà người đó gửi > 30 tin."""
    flags = [False] * len(items)
    by: dict[Any, list[int]] = {}
    for i, (who, _t) in enumerate(items):
        if who is not None:
            by.setdefault(who, []).append(i)
    for idxs in by.values():
        lo = 0
        for hi, i in enumerate(idxs):
            while items[i][1] - items[idxs[lo]][1] > BURST_WINDOW:
                lo += 1
            if hi - lo + 1 > BURST_MESSAGES:
                for j in idxs[lo:hi + 1]:
                    flags[j] = True
    return flags


def effective_count(items: list[tuple[Any, datetime]]) -> int:
    """Số tin MỚI tính cho ngưỡng 40 tin: mỗi người xả tin tính MỘT khối, không làm tăng số lượt (§4D.5)."""
    flags = burst_flags(items)
    flooders = {who for (who, _t), f in zip(items, flags, strict=True) if f}
    return sum(1 for f in flags if not f) + len(flooders)


def merge_bursts(evs: list[GEv]) -> list[GEv]:
    """Gộp tin của người xả tin thành MỘT khối (tại vị trí tin đầu của người đó): `body` nối bằng xuống dòng, `members` giữ các tin
    còn lại để mọi tin đều được đánh dấu đã xử lý. Mã tin của khối = mã tin đầu; trích dẫn được kiểm với thân đã nối."""
    flags = burst_flags([(e.sender_key, e.occurred_at) for e in evs])
    blocks: dict[Any, GEv] = {}
    out: list[GEv] = []
    for e, f in zip(evs, flags, strict=True):
        if not f:
            out.append(e)
        elif e.sender_key not in blocks:
            blocks[e.sender_key] = e
            out.append(e)
        else:
            head = blocks[e.sender_key]
            head.members.append(e)
            head.body = f"{head.body}\n{e.body}" if e.body else head.body
    return out


def thread_group(thread_key: str) -> uuid.UUID | None:
    if thread_key.startswith("g:"):
        try:
            return uuid.UUID(thread_key[2:])
        except ValueError:
            return None
    return None


def thread_dm(thread_key: str) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """`dm:<người>:<kênh>` ⇒ (person_id | None, channel_id | None)."""
    parts = thread_key.split(":")
    if len(parts) != 3 or parts[0] != "dm":
        return None, None

    def u(x: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(x)
        except ValueError:
            return None

    return u(parts[1]), u(parts[2])


def _bare(v: Any) -> str | None:
    s = str(v).split("@", 1)[0] if v else ""
    return s or None


def _payload_get(payload: Any, *keys: str) -> Any:
    if not isinstance(payload, dict):
        return None
    for k in keys:
        if payload.get(k) not in (None, "", False):
            return payload[k]
    return None


async def system_secrets(db: AsyncSession, org_id: uuid.UUID) -> tuple[str, ...]:
    """Bí mật HỆ THỐNG cần thay `[đã che]` trong prompt (Q9: chữ chat đi nguyên văn, chỉ bí mật hệ thống bị che): khoá/token của GenOS
    (cấu hình) và khoá API nhà cung cấp AI của tổ chức (giải mã tại chỗ, không ghi ra đâu). Chỉ lấy chuỗi ≥ 8 ký tự."""
    from gh import crypto
    from gh.config import get_settings
    from gh.providers.router import KEY_AAD

    st = get_settings()
    out = {getattr(st, n, "") for n in ("master_key", "setup_token", "bridge_key", "browser_key", "backup_key")}
    for r in (await db.execute(text("""SELECT k.secret_enc FROM agent.provider_keys k JOIN agent.providers p ON p.id = k.provider_id
                                       WHERE p.org_id = :o"""), {"o": org_id})).all():
        try:
            out.add(crypto.decrypt(bytes(r.secret_enc), KEY_AAD).decode())
        except Exception:  # noqa: BLE001 — khoá hỏng/khoá master đổi: bỏ khoá đó
            continue
    return tuple(sorted(x for x in out if isinstance(x, str) and len(x) >= 8))


_UNLOCK_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_RENEW_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"


class GroupTurns:
    """Lượt phân tích theo luồng. Một lượt = MỘT luồng; các lượt của một tổ chức chạy TUẦN TỰ (khoá Redis) nên cửa sổ thời gian +
    `purpose` riêng đủ để gắn thẻ `thread_key`/`analysis_id` cho `agent.model_calls` (`ai_cost.tag_calls`)."""

    def __init__(self, refinery: "Refinery", analyzer: Callable[..., Awaitable[Any]], verifier: Callable[..., Any]):
        self.r = refinery
        self.analyze, self.verify = analyzer, verifier

    # ─── quét luật (a) lúc tin chờ: thread_key + ứng viên + tin không cần model ────────────────────────────────────

    async def ensure_scanned(self, org_id: uuid.UUID, limit: int = SCAN_BATCH) -> int:
        """Với tin `pending` chưa quét: tính `thread_key`, chạy luật (a) (`prefilter.scan_text` + `enrich`), lưu vào
        `event_state.detail` ({scanned, thread_key, gate_class, cands}). Tin của Sếp/nhân viên (gửi đi) không quét ứng viên. Ứng viên
        của 6 cổng ⇒ bật `fast`. Tin KHÔNG cần model (sticker/reaction/"dạ ok"/tin mẫu lặp) ⇒ `discarded` ngay (vẫn đếm ở độ phủ).
        Trả số tin đã quét. Lỗi từng tin ⇒ bỏ qua tin đó (lượt sau thử lại), không làm hỏng cả lô."""
        async with self.r.sm() as db:
            rows = (await db.execute(text("""
                SELECT s.event_id, s.event_received_at, e.occurred_at, e.kind, e.body_text, e.direction, e.group_id,
                       e.channel_id, e.payload, e.sender_identity_id, pi.person_id, p.display_name
                FROM refinery.event_state s JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
                LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                LEFT JOIN core.persons p ON p.id = pi.person_id
                WHERE s.org_id = :o AND s.state = 'pending' AND s.detail ->> 'scanned' IS NULL
                ORDER BY s.event_received_at LIMIT :n"""), {"o": org_id, "n": limit})).all()
        done = 0
        for r in rows:
            try:
                await self._scan_one(org_id, r)
                done += 1
            except Exception as exc:  # noqa: BLE001 — một tin lỗi không được chặn các tin khác
                log.warning("ensure_scanned: quét luật (a) tin %s lỗi — bỏ qua, lượt sau thử lại: %s", r.event_id, err_code(exc))
        return done

    async def _scan_one(self, org_id: uuid.UUID, r: Any) -> None:
        async with self.r.sm() as db:
            person = await live_person(db, r.person_id) if r.person_id else None
            tk = await self._thread_key(db, r, person)
            body = r.body_text or ""
            cands: list[prefilter.Candidate] = []
            if r.direction == "inbound" and body and r.kind not in prefilter.NO_MODEL_KINDS:
                cands = prefilter.scan_text(body, "body")
                if any(c.flag == "money_request" or c.kind for c in cands):
                    cands = prefilter.enrich(cands, await self._sender_ctx(db, org_id, r, person, tk))
                else:
                    cands = prefilter.enrich(cands, prefilter.SenderCtx(sender_ref=f"u_{person}" if person else None))
            gclass = prefilter.gate_class(cands)
            template_n = 0
            risky = any(c.flag in prefilter.RISK_FLAGS for c in cands)
            if len(triage.normalize(body)) >= triage.BROADCAST_MIN_LEN and r.sender_identity_id is not None:
                # Chỉ đếm trong CÙNG luồng và CÙNG người gửi (kẻ gian không "làm nóng" câu lừa đảo ở nhóm khác để tin ở nhóm Sếp bị bỏ).
                # 7 ngày theo GIỜ GỬI (occurred_at), không theo giờ vào kho: tin nhập lịch sử/replay vào kho cùng lúc thì received_at dồn
                # lại một chỗ và đơn hàng tuần nào cũng thành "mẫu lặp" (lỗi kb1 #4).
                template_n = 1 + int((await db.execute(text("""
                    SELECT count(*) FROM raw.events WHERE org_id = :o AND body_text = :b AND occurred_at < :t AND received_at <= :r
                      AND occurred_at >= CAST(:t AS timestamptz) - interval '7 days'
                      AND sender_identity_id = :s AND group_id IS NOT DISTINCT FROM CAST(:g AS uuid)
                      AND (CAST(:g AS uuid) IS NOT NULL OR channel_id = :c)"""),
                                                         {"o": org_id, "b": body, "t": r.occurred_at, "r": r.event_received_at,
                                                          "s": r.sender_identity_id,
                                                          "g": r.group_id, "c": r.channel_id})).scalar_one())
            # KHÔNG BAO GIỜ bỏ tin có ứng viên rủi ro (từ khoá lừa đảo, link, yêu cầu chuyển tiền, STK, SĐT, đã nhập OTP, cấp cứu…).
            skip = None if (gclass or risky) else prefilter.no_model_reason(r.kind, body, template_n=template_n)
            detail: dict[str, Any] = {"scanned": True, "thread_key": tk, "gate_class": gclass,
                                      "cands": [c.to_json() for c in cands]}
            if risky and not gclass and template_n >= prefilter.TEMPLATE_MIN_N:
                detail["low_priority"] = True               # mẫu lặp có ứng viên rủi ro: chỉ hạ ưu tiên, vẫn phân tích
            if skip is not None:
                detail |= {"discarded_by": "no_model", "reason": skip}
            await db.execute(text("""
                UPDATE refinery.event_state SET detail = detail || CAST(:d AS jsonb), fast = fast OR :f,
                       state = CASE WHEN :skip THEN 'discarded' ELSE state END, label = CASE WHEN :skip THEN 'Noise' ELSE label END,
                       updated_at = now()
                WHERE event_id = :e AND event_received_at = :r AND state = 'pending'"""),
                             {"d": orjson.dumps(detail).decode(), "f": gclass is not None, "skip": skip is not None,
                              "e": r.event_id, "r": r.event_received_at})
            await db.commit()
        if skip is not None:
            await self.r._raw_state(org_id, GEv(r.event_id, r.event_received_at, r.occurred_at, 0, r.kind, body, r.direction,
                                                r.group_id, r.channel_id, r.sender_identity_id, person, r.display_name),
                                    "discarded", "Noise", None)

    async def _thread_key(self, db: AsyncSession, r: Any, person: uuid.UUID | None) -> str:
        if r.group_id is not None:
            return f"g:{r.group_id}"
        who: Any = person
        if r.direction == "outbound":
            who = await self._outbound_dm_person(db, r)
        if who is None:
            who = f"i{r.sender_identity_id.hex}" if r.sender_identity_id else "unknown"
        return f"dm:{who}:{r.channel_id}"

    async def _outbound_dm_person(self, db: AsyncSession, r: Any) -> uuid.UUID | None:
        """Tin ĐI 1-1: người nhận theo payload (Zalo `threadId`; WhatsApp `key.remoteJid`/`remoteJidAlt`; `recipient`), không có thì tin
        ĐẾN 1-1 gần nhất trước nó trên cùng kênh (cùng luật `outbound_to_sql`)."""
        p = r.payload if isinstance(r.payload, dict) else {}
        raw_key = p.get("key")
        key: dict[str, Any] = raw_key if isinstance(raw_key, dict) else {}
        keys = [k for k in (_bare(p.get("threadId")), _bare(key.get("remoteJid")), _bare(key.get("remoteJidAlt")),
                            _bare(p.get("recipient"))) if k]
        if keys:
            pid = (await db.execute(text("""
                SELECT person_id FROM core.person_identities
                WHERE channel_id = :c AND NULLIF(split_part(external_id, '@', 1), '') = ANY(:k) LIMIT 1"""),
                                    {"c": r.channel_id, "k": keys})).scalar_one_or_none()
            if pid is not None:
                return await live_person(db, pid)
        pid = (await db.execute(text("""
            SELECT pi.person_id FROM raw.events i JOIN core.person_identities pi ON pi.id = i.sender_identity_id
            WHERE i.channel_id = :c AND i.group_id IS NULL AND i.direction = 'inbound' AND i.occurred_at <= :t
              AND i.occurred_at > CAST(:t AS timestamptz) - interval '7 days'
            ORDER BY i.occurred_at DESC LIMIT 1"""), {"c": r.channel_id, "t": r.occurred_at})).scalar_one_or_none()
        return await live_person(db, pid) if pid is not None else None

    async def _sender_ctx(self, db: AsyncSession, org_id: uuid.UUID, r: Any, person: uuid.UUID | None,
                          tk: str) -> prefilter.SenderCtx:
        """Dữ kiện THÔ cho `prefilter.enrich` (chỉ tra cứu, không phán đoán): ngày vào nhóm (`core.group_members.joined_at`), người khác
        trùng tên hiển thị trong nhóm, STK đã biết của người này (digest có khoá trong `mem.facts`)."""
        if person is None:
            return prefilter.SenderCtx()
        joined_days: float | None = None
        collide: str | None = None
        if r.group_id is not None:
            jr = (await db.execute(text("""SELECT gm.joined_at, g.created_at FROM core.group_members gm JOIN core.groups g ON g.id = gm.group_id
                                           WHERE gm.group_id = :g AND gm.person_id = :p"""), {"g": r.group_id, "p": person})).one_or_none()
            # `joined_at` chỉ đáng tin khi người đó vào SAU lúc hệ thống bắt đầu nghe nhóm (+1 ngày dung sai): thành viên có sẵn lúc
            # phát hiện nhóm có `joined_at` = lúc thấy lần đầu, không phải ngày vào thật ⇒ coi là "chưa biết" (None), không phải "mới vào".
            if jr is not None and jr.joined_at is not None and jr.created_at is not None and jr.joined_at > jr.created_at + timedelta(days=1):
                joined_days = max(0.0, (clock.now() - jr.joined_at).total_seconds() / 86400)
            other = (await db.execute(text("""
                SELECT p2.id FROM core.group_members gm JOIN core.persons p2 ON p2.id = gm.person_id
                WHERE gm.group_id = :g AND p2.id <> :p AND p2.deleted_at IS NULL AND p2.merged_into_id IS NULL
                  AND mem.fold(p2.display_name) = mem.fold(:n) LIMIT 1"""),
                                       {"g": r.group_id, "p": person, "n": r.display_name or ""})).scalar_one_or_none() \
                if r.display_name else None
            collide = f"u_{other}" if other is not None else None
        known: frozenset[str] = frozenset()
        try:
            rows = (await db.execute(text("""
                SELECT f.object_value ->> 'digest' FROM mem.facts f JOIN mem.entities e ON e.id = f.subject_id
                WHERE e.org_id = :o AND e.person_id = :p AND f.predicate = 'payment_account'
                  AND f.status IN ('asserted', 'owner_confirmed', 'disputed')"""), {"o": org_id, "p": person})).scalars().all()
            known = frozenset(x for x in rows if x)
        except Exception:  # noqa: BLE001 — thiếu bảng/khoá không được chặn quét luật
            known = frozenset()
        first = (await db.execute(text("""
            SELECT min(e.occurred_at) FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            WHERE e.org_id = :o AND pi.person_id = :p"""), {"o": org_id, "p": person})).scalar_one_or_none()
        known_entity = first is not None and first <= r.occurred_at - timedelta(days=7)      # có lịch sử tin ≥ 7 ngày trước tin này
        return prefilter.SenderCtx(sender_ref=f"u_{person}", name_collision_with=collide, joined_days=joined_days,
                                   known_stk_digests=known, known_entity=known_entity)

    # ─── lập kế hoạch: luồng nào đến hạn ───────────────────────────────────────────────────────────────────────────

    async def pending_threads(self, db: AsyncSession, org_id: uuid.UUID) -> list[budget.ThreadState]:
        rows = (await db.execute(text("""
            SELECT s.detail ->> 'thread_key' AS tk, s.detail ->> 'gate_class' AS gc, s.fast, s.event_received_at,
                   s.detail ->> 'next_try_at' AS nt, s.detail ->> 'hold_until' AS hu, e.sender_identity_id, e.occurred_at, e.group_id, pi.person_id
            FROM refinery.event_state s JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
            LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            WHERE s.org_id = :o AND s.state = 'pending' AND s.detail ->> 'scanned' = 'true'
              AND s.detail ->> 'thread_key' IS NOT NULL
            ORDER BY e.occurred_at"""), {"o": org_id})).all()
        by: dict[str, list[Any]] = {}
        for r in rows:
            by.setdefault(r.tk, []).append(r)
        last = {x.thread_key: x.last_analysis_at for x in (await db.execute(text("""
            SELECT thread_key, last_analysis_at FROM refinery.group_watermarks WHERE org_id = :o"""), {"o": org_id})).all()}
        out: list[budget.ThreadState] = []
        for tk, rs in by.items():
            n = effective_count([(x.person_id or x.sender_identity_id, x.occurred_at) for x in rs])
            gc = "safety" if any(x.gc == "safety" for x in rs) else ("gate" if any(x.gc == "gate" for x in rs) else None)
            eff = [max((datetime.fromisoformat(v) for v in (x.nt, x.hu) if v), default=None) for x in rs]
            held = [t for t in eff if t is not None]
            out.append(budget.ThreadState(
                tk, n, min(x.event_received_at for x in rs), next((x.group_id for x in rs if x.group_id), None),
                any(x.fast for x in rs), gc, max(held) if held and len(held) == len(rs) else None, last.get(tk)))
        return out

    async def cadence_min(self, db: AsyncSession, org_id: uuid.UUID) -> int:
        v = (await db.execute(text("SELECT settings->'intel'->>'cadence_min' FROM core.organizations WHERE id = :o"),
                              {"o": org_id})).scalar_one_or_none()
        try:
            n = int(v) if v is not None else budget.DEFAULT_CADENCE_MIN
        except ValueError:
            n = budget.DEFAULT_CADENCE_MIN
        return n if 1 <= n <= 24 * 60 else budget.DEFAULT_CADENCE_MIN

    async def plan(self, org_id: uuid.UUID, *, manual: bool = False, only: list[str] | None = None,
                   now: datetime | None = None) -> list[tuple[budget.ThreadState, str]]:
        """Các luồng đến hạn (đã xếp round-robin). Cũng ghi trạng thái giãn nhịp/trần của từng luồng đang chờ (`group_watermarks`).
        `manual` (Sếp bấm chạy) bỏ qua nhịp: mọi luồng có tin chờ đều đến hạn, vẫn qua trần."""
        now = now or clock.now()
        async with self.r.sm() as db:
            states = await self.pending_threads(db, org_id)
            cadence = await self.cadence_min(db, org_id)
            caps, usage = await budget.load_caps(db, org_id), await budget.load_usage(db, org_id, now)
            paid = caps.budget_vnd <= 0 and await budget.paid_route_possible(db, org_id)
            due: list[tuple[budget.ThreadState, str]] = []
            notes: list[budget.Decision] = []
            for ts in states:
                if only is not None and ts.thread_key not in only:
                    continue
                d = budget.decide(budget.KIND_NORMAL, ts.thread_key, caps, usage, paid_route=paid)
                await budget.sync_thread_state(db, org_id, ts.thread_key, d, group_id=ts.group_id, now=now)
                reason = budget.due_reason(ts, now, cadence_min=cadence, stretched=d.stretched)
                if reason is None and (manual or only is not None) and ts.n > 0:
                    reason = ts.gate_class or "manual"
                if reason is None:
                    continue
                dd = budget.decide(budget.kind_for(reason), ts.thread_key, caps, usage, paid_route=paid)
                if not dd.allow and dd.mode in (budget.MODE_RULES_ONLY, budget.MODE_FREE):
                    # Không có lượt nào được phép ⇒ KHÔNG tạo run rỗng: giữ tin tới 00:00 giờ VN (hết trần) / 15 phút (chế độ miễn phí).
                    await self._hold(db, org_id, ts.thread_key, dd, now=now)
                    notes.append(dd)
                    continue
                due.append((ts, reason))
            await db.commit()
        for dd in notes[:1]:
            await self.notify(org_id, dd, caps)
        return budget.order_threads(due)

    async def _hold(self, db: AsyncSession, org_id: uuid.UUID, tk: str, dec: budget.Decision, *, now: datetime | None = None) -> None:
        """Giữ tin chờ của luồng vì trần/chế độ miễn phí: `detail.hold_until` (khác `next_try_at`: Sếp bấm chạy tay vẫn xét lại)."""
        now = now or clock.now()
        until = budget.next_vn_midnight(now) if dec.mode == budget.MODE_RULES_ONLY else now + FREE_MODE_RETRY
        await db.execute(text("""UPDATE refinery.event_state SET detail = detail || jsonb_build_object('hold_until', CAST(:u AS text))
                                 WHERE org_id = :o AND state = 'pending' AND detail ->> 'thread_key' = :t"""),
                         {"o": org_id, "t": tk, "u": until.isoformat()})

    async def run(self, org_id: uuid.UUID, st: RunStats, *, trigger: str, threads: list[str] | None = None) -> None:
        """Một lượt chạy: quét luật (a) cho tin mới, rồi mỗi luồng đến hạn MỘT lượt phân tích (tuần tự, round-robin)."""
        deadline = time.monotonic() + self.r.time_budget_s
        lock = f"gh:refinery:groups:{org_id}"
        token = uuid.uuid4().hex                     # khoá có chủ: chỉ chủ mới gia hạn/xoá (lượt chạy quá TTL không xoá nhầm khoá của lượt khác)
        if not await self.r.redis.set(lock, token, nx=True, ex=GROUP_LOCK_S):
            return                                   # đang có lượt theo luồng chạy cho tổ chức này
        try:
            await self.ensure_scanned(org_id)
            plan = await self.plan(org_id, manual=trigger == "manual", only=threads)
            for _pass in range(MAX_PASSES):
                for ts, reason in plan:
                    if deadline - time.monotonic() < self.r.part_worst_s:
                        return
                    await self._renew(lock, token)
                    await self.turn(org_id, st, ts, reason)
                    await self.r._progress(org_id, st)
                # Ứng viên cổng/an toàn/đường nhanh đến TRONG lúc đang chạy không phải đợi lượt sau (đường nhanh ≤ 5 phút).
                await self.ensure_scanned(org_id)
                plan = [(ts, r) for ts, r in await self.plan(org_id) if r in ("safety", "gate", "fast")]
                if not plan:
                    break
        finally:
            await self.r.redis.eval(_UNLOCK_LUA, 1, lock, token)  # type: ignore[misc]

    async def _renew(self, lock: str, token: str) -> None:
        await self.r.redis.eval(_RENEW_LUA, 1, lock, token, str(GROUP_LOCK_S))  # type: ignore[misc]

    # ─── một lượt ──────────────────────────────────────────────────────────────────────────────────────────────────

    async def alert_once(self, org_id: uuid.UUID, key: str, *, alert_type: str, priority: str, title: str, summary: str,
                         suggested: str) -> None:
        start, end = budget.day_start()
        async with self.r.sm() as db:
            # Một lần/ngày VN: nguồn sự thật là chính `biz.alerts` (không phụ thuộc Redis dùng chung).
            if (await db.execute(text("""SELECT 1 FROM biz.alerts WHERE org_id = :o AND alert_type = :t AND created_at >= :s
                                         AND created_at < :e LIMIT 1"""), {"o": org_id, "t": alert_type, "s": start, "e": end})).first():
                return
            await raise_alert(db, org_id, alert_type=alert_type, priority=priority, title=title, summary=summary,
                              suggested=suggested, redis=self.r.redis)
            await db.commit()

    async def notify(self, org_id: uuid.UUID, dec: budget.Decision, caps: budget.Caps) -> None:
        """Báo Ryan (một lần/ngày mỗi loại) khi trần chạm: chế độ miễn phí (P2), lượt an toàn vượt trần/chống lạm dụng (P1)."""
        if dec.mode == budget.MODE_FREE:
            await self.alert_once(org_id, "free_mode", alert_type="intel_free_mode", priority="P2",
                                  title="Tình báo đang ở chế độ miễn phí",
                                  summary="Có thể rơi vào nguồn AI tính tiền theo token trong khi trần VND = 0; chỉ quét luật.",
                                  suggested="Kiểm tra nguồn AI của core.refinery: chỉ dùng gói thuê bao (CLI).")
        abuse = [r for r in dec.reasons if r in ("sender_cap", "thread_safety_cap", "safety_hard_cap", "safety_absolute_cap")]
        if dec.kind == budget.KIND_SAFETY and abuse:
            await self.alert_once(org_id, "safety_hard_cap", alert_type="intel_safety_abuse", priority="P1",
                                  title="Lượt an toàn vượt trần chống lạm dụng",
                                  summary=f"Dồn tin an toàn/lừa đảo ({', '.join(abuse)}); người gửi vượt trần chỉ chạy luật cứng.",
                                  suggested="Xem luồng đó trong Hộp thư; có thể là kẻ spam từ khoá để đốt lượt.")
        elif dec.over_safety_cap:
            await self.alert_once(org_id, "safety_cap", alert_type="intel_safety_cap", priority="P1",
                                  title="Vượt trần lượt an toàn trong ngày",
                                  summary=f"Lượt lừa đảo/an toàn hôm nay đã vượt {caps.safety_call_cap}. Vẫn chạy, không cắt.",
                                  suggested="Xem Hệ thống › Chi phí AI; chỉnh intel_safety_call_cap nếu cần.")

    async def _safety_gate_senders(self, db: AsyncSession, org_id: uuid.UUID, tk: str, usage_hard: bool) -> tuple[list[str], list[str]]:
        """(được phép, bị chặn) trong số NGƯỜI GỬI tin ứng viên an toàn đang chờ của luồng [Gen chọn, chống lạm dụng]. Bị chặn khi: (a) đã
        có ≥ `SAFETY_PER_SENDER_HOUR` lượt an toàn trong giờ qua (đếm từ dấu `safety_turns` đóng lúc nhận tin, KỂ CẢ lượt lỗi); hoặc
        (b) đã chạm trần cứng 3 × `intel_safety_call_cap` (`usage_hard`) VÀ là người lạ (chưa có lịch sử tin ≥ 7 ngày)."""
        rows = (await db.execute(text("""
            SELECT DISTINCT COALESCE(pi.person_id::text, e.sender_identity_id::text, 'x') AS who
            FROM refinery.event_state s JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
            LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            WHERE s.org_id = :o AND s.state = 'pending' AND s.detail ->> 'thread_key' = :t AND s.detail ->> 'gate_class' = 'safety'"""),
                                  {"o": org_id, "t": tk})).scalars().all()
        since = clock.now() - timedelta(hours=1)
        ok: list[str] = []
        blocked: list[str] = []
        for who in rows:
            n = (await db.execute(text("""
                SELECT count(DISTINCT elem ->> 'a')
                FROM refinery.event_state s JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
                LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                CROSS JOIN LATERAL jsonb_array_elements(COALESCE(s.detail -> 'safety_turns', '[]'::jsonb)) elem
                WHERE s.org_id = :o AND COALESCE(pi.person_id::text, e.sender_identity_id::text, 'x') = :w
                  AND CAST(elem ->> 't' AS timestamptz) >= :since"""), {"o": org_id, "w": who, "since": since})).scalar_one()
            over = int(n) >= budget.SAFETY_PER_SENDER_HOUR
            if usage_hard and not over:
                first = (await db.execute(text("""
                    SELECT min(e.occurred_at) FROM raw.events e LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                    WHERE e.org_id = :o AND COALESCE(pi.person_id::text, e.sender_identity_id::text, 'x') = :w"""),
                                        {"o": org_id, "w": who})).scalar_one()
                over = first is None or first > clock.now() - timedelta(days=7)
            (blocked if over else ok).append(who)
        return ok, blocked

    async def turn(self, org_id: uuid.UUID, st: RunStats, ts: budget.ThreadState, reason: str) -> None:
        tk = ts.thread_key
        async with self.r.sm() as db:
            caps, usage = await budget.load_caps(db, org_id), await budget.load_usage(db, org_id)
            paid = caps.budget_vnd <= 0 and await budget.paid_route_possible(db, org_id)
            kind0 = budget.kind_for(reason)
            blocked: list[str] = []
            sender_over = False
            if kind0 == budget.KIND_SAFETY:
                hard = usage.calls.get(budget.KIND_SAFETY, 0) >= budget.SAFETY_HARD_FACTOR * max(caps.safety_call_cap, 1)
                ok, blocked = await self._safety_gate_senders(db, org_id, tk, hard)
                sender_over = bool(blocked) and not ok
                if usage.calls.get(budget.KIND_SAFETY, 0) >= budget.SAFETY_ABSOLUTE_FACTOR * max(caps.safety_call_cap, 1):
                    ok, blocked = [], ok + blocked                       # chốt chặn tuyệt đối: hạ MỌI người gửi (luật cứng vẫn tạo ứng viên)
            dec = budget.decide(kind0, tk, caps, usage, sender_over=sender_over, paid_route=paid)
            await budget.sync_thread_state(db, org_id, tk, dec, group_id=ts.group_id)
            if blocked:
                # Hạ CHỈ tin của những người gửi đã vượt trần (đường luật cứng: ứng viên vẫn ở event_state.detail.cands, T1 báo theo luật).
                await db.execute(text("""UPDATE refinery.event_state s SET fast = false, detail = s.detail || '{"gate_class": "hard"}'::jsonb
                                         FROM raw.events e LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                         WHERE s.org_id = :o AND s.state = 'pending' AND s.detail ->> 'thread_key' = :t
                                           AND s.detail ->> 'gate_class' = 'safety' AND e.id = s.event_id AND e.received_at = s.event_received_at
                                           AND COALESCE(pi.person_id::text, e.sender_identity_id::text, 'x') = ANY(:w)"""),
                                 {"o": org_id, "t": tk, "w": blocked})
            if not dec.allow and dec.mode in (budget.MODE_RULES_ONLY, budget.MODE_FREE):
                await self._hold(db, org_id, tk, dec)
            await db.commit()
        await self.notify(org_id, replace(dec, reasons=(*dec.reasons, "sender_cap")) if blocked and dec.allow else dec, caps)
        if not dec.allow:
            return                                   # chỉ luật (a); tin nằm chờ (hết trần ⇒ đợi tới 00:00 giờ VN)
        kind = dec.kind
        aid = uuid.uuid4()
        claimed = await self._claim(org_id, st.run_id, tk, kind, aid)
        if not claimed:
            return
        evs = [m for b in claimed for m in b.all_events]
        st.total += len(evs)
        try:
            async with self.r.sm() as db:
                window, pack, profile = await self._window(db, org_id, tk, claimed)
        except Exception as exc:  # noqa: BLE001 — dựng ngữ cảnh lỗi: trả tin về pending, không mất tin
            log.error("turn: dựng cửa sổ luồng %s lỗi: %s", tk, err_code(exc))
            await self._release(evs, st, f"dựng ngữ cảnh lỗi: {type(exc).__name__}", backoff=True)
            return
        since = datetime.now(UTC) - timedelta(seconds=2)
        async with self.r.sm() as db:
            secrets = await system_secrets(db, org_id)
        try:
            analysis = await asyncio.wait_for(
                self.analyze(window, pack, profile, pack.policy, call=self._call(org_id, kind), secrets=secrets),
                timeout=max(30.0, 2 * AGY_BACKGROUND_TIMEOUT_S + 30))
        except (ModelUnavailable, TimeoutError) as exc:
            # Lừa đảo/an toàn: nếu gói thuê bao từ chối thì ứng viên luật cứng đã nằm ở event_state.detail.cands (T1 đọc); tin giữ chờ, lùi nhịp.
            await self._release(evs, st, f"model không khả dụng: {exc}"[:300], backoff=True)
            await self._record_failed(org_id, tk, aid, window, "model_unavailable", st, kind=kind, since=since)
            st.status, st.error = "failed", f"Model không khả dụng — {len(evs)} bản ghi giữ chờ"
            return
        except l3.ContractError as exc:
            await self._release(evs, st, "contract_error: " + "; ".join(exc.errors[:3]), count_error=True, backoff=True)
            await self._record_failed(org_id, tk, aid, window, "contract_error", st, errors=exc.errors, model=exc.model,
                                      kind=kind, since=since)
            return
        try:
            await self._write(org_id, st, tk, aid, kind, claimed, window, pack, profile, analysis, since, secrets)
        except Exception as exc:  # noqa: BLE001 — lỗi ngoài từng đơn vị ⇒ rollback cả giao dịch, tin về pending (tính lần thử, lùi nhịp)
            log.error("turn: ghi lượt phân tích %s (luồng %s) lỗi: %s", aid, tk, err_code(exc))
            await self._release(evs, st, f"ghi lượt lỗi: {type(exc).__name__}", count_error=True, backoff=True)
            await self._record_failed(org_id, tk, aid, window, "write_failed", st, kind=kind, since=since)

    def _call(self, org_id: uuid.UUID, kind: str) -> Callable[[list[Message]], Awaitable[Any]]:
        async def call(messages: list[Message]) -> Any:
            routed = await self.r.router.generate(org_id, agent_key=AGENT_KEY, purpose=budget.PURPOSE[kind], messages=messages,
                                                  json_mode=True, temperature=0.0)
            return l3.ModelReply(routed.text, routed.model, routed.tokens_in, routed.tokens_out)
        return call

    # ─── nhận tin của luồng ────────────────────────────────────────────────────────────────────────────────────────

    async def _claim(self, org_id: uuid.UUID, run_id: uuid.UUID, tk: str, kind: str = budget.KIND_NORMAL,
                     aid: uuid.UUID | None = None) -> list[GEv]:
        """Nhận tối đa một lượt tin MỚI của `tk` (`FOR UPDATE SKIP LOCKED`): theo thời gian, ≤ 60 tin / 12.000 ký tự, không cắt giữa chuỗi
        trả lời; tin của người xả tin gộp thành một khối. Lượt `safety`/`gate` nhận tin ỨNG VIÊN trước (kèm 2 tin ngữ cảnh trước/sau và
        chuỗi trả lời của nó) — tin tồn đọng thường không chen vào và không tiêu trần an toàn; chúng thành lượt thường. Lượt an toàn
        đóng dấu `safety_turns` lên tin ứng viên (đếm theo người gửi, kể cả lượt lỗi). Trả các khối (đầu khối = tin đại diện)."""
        want = {budget.KIND_SAFETY: "safety", budget.KIND_GATE: "gate"}.get(kind)
        async with self.r.sm() as db:
            rows = (await db.execute(text("""
                SELECT s.event_id, s.event_received_at, s.attempts, e.seq, e.occurred_at, e.body_text, e.kind, e.direction,
                       e.group_id, e.channel_id, e.sender_identity_id, e.external_msg_id, e.payload, pi.person_id,
                       p.display_name, p.code AS person_code, s.detail ->> 'gate_class' AS gc
                FROM refinery.event_state s JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
                LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                LEFT JOIN core.persons p ON p.id = pi.person_id
                WHERE s.org_id = :o AND s.state = 'pending' AND s.detail ->> 'thread_key' = :t
                  AND (s.detail ->> 'next_try_at' IS NULL OR CAST(s.detail ->> 'next_try_at' AS timestamptz) <= :now)
                ORDER BY COALESCE(s.detail ->> 'gate_class' = CAST(:want AS text), false) DESC, e.occurred_at, e.seq
                LIMIT 500 FOR UPDATE OF s SKIP LOCKED"""),
                                     {"o": org_id, "t": tk, "now": clock.now(), "want": want})).all()
            evs: list[GEv] = []
            for r in rows:
                pid, name, code = r.person_id, r.display_name, r.person_code
                if pid is not None:
                    live = await live_person(db, pid)
                    if live != pid:
                        pid = live
                        lp = (await db.execute(text("SELECT display_name, code FROM core.persons WHERE id = :i"), {"i": live})).one()
                        name, code = lp.display_name, lp.code
                pl = r.payload
                evs.append(GEv(r.event_id, r.event_received_at, r.occurred_at, r.seq, r.kind, r.body_text or "", r.direction,
                               r.group_id, r.channel_id, r.sender_identity_id, pid, name, code, r.external_msg_id or "",
                               str(_payload_get(pl, *REPLY_KEYS)) if _payload_get(pl, *REPLY_KEYS) else None,
                               bool(_payload_get(pl, *FORWARD_KEYS)), r.attempts, r.gc))
            evs.sort(key=lambda e: (e.occurred_at, e.seq))
            cand_ids: list[uuid.UUID] = []
            if want is not None and any(e.gate_class == want for e in evs):
                keep: set[int] = set()
                by_ext = {e.ext_id: i for i, e in enumerate(evs) if e.ext_id}
                for i, e in enumerate(evs):
                    if e.gate_class != want:
                        continue
                    cand_ids.append(e.event_id)
                    keep.update(j for j in range(max(0, i - 2), min(len(evs), i + 3)) if evs[j].gate_class != "hard")
                    if e.reply_ext and e.reply_ext in by_ext:
                        keep.add(by_ext[e.reply_ext])
                chosen = take_window([evs[i] for i in sorted(keep)])
                if not any(e.gate_class == want for e in chosen):       # ứng viên không bao giờ bị cắt khỏi lượt của chính nó
                    chosen = [e for e in evs if e.gate_class == want][:MAX_NEW_EVENTS]
            else:
                chosen = take_window(evs)
            if not chosen:
                return []
            await db.execute(text("""
                UPDATE refinery.event_state s SET state = 'processing', run_id = :run, updated_at = now(),
                       attempts = s.attempts + 1
                FROM unnest(CAST(:ids AS uuid[]), CAST(:rs AS timestamptz[])) AS u(id, r)
                WHERE s.event_id = u.id AND s.event_received_at = u.r"""),
                             {"run": run_id, "ids": [e.event_id for e in chosen], "rs": [e.received_at for e in chosen]})
            if kind == budget.KIND_SAFETY and aid is not None:
                stamp = orjson.dumps([{"a": str(aid), "t": clock.now().isoformat()}]).decode()
                await db.execute(text("""
                    UPDATE refinery.event_state SET detail = detail || jsonb_build_object(
                        'safety_turns', COALESCE(detail -> 'safety_turns', '[]'::jsonb) || CAST(:st AS jsonb))
                    WHERE org_id = :o AND event_id = ANY(:ids) AND detail ->> 'gate_class' = 'safety'"""),
                                 {"o": org_id, "st": stamp, "ids": [e.event_id for e in chosen if e.gate_class == "safety"]})
            await db.commit()
        return merge_bursts(chosen)

    async def _release(self, evs: list[GEv], st: RunStats, reason: str, *, count_error: bool = False,
                       backoff: bool = False) -> None:
        """Trả tin về `pending` (hoàn lần thử, trừ khi `count_error`); sai hợp đồng đủ `MAX_ATTEMPTS` lần ⇒ `error`. `backoff` lùi
        `next_try_at` để không dội lại model mỗi 5 giây."""
        nxt = (clock.now() + RETRY_BACKOFF).isoformat() if backoff else None
        async with self.r.sm() as db:
            for ev in evs:
                attempts = (await db.execute(text("SELECT attempts FROM refinery.event_state WHERE event_id = :e AND event_received_at = :r"),
                                             {"e": ev.event_id, "r": ev.received_at})).scalar_one()
                final = count_error and attempts >= MAX_ATTEMPTS
                d: dict[str, Any] = {"last_error": reason[:300]}
                if nxt:
                    d["next_try_at"] = nxt
                await db.execute(text("""
                    UPDATE refinery.event_state SET state = :s, updated_at = now(),
                           attempts = CASE WHEN :refund THEN GREATEST(attempts - 1, 0) ELSE attempts END,
                           detail = detail || CAST(:d AS jsonb)
                    WHERE event_id = :e AND event_received_at = :r"""),
                                 {"s": "error" if final else "pending", "e": ev.event_id, "r": ev.received_at,
                                  "refund": not count_error, "d": orjson.dumps(d).decode()})
                if final:
                    st.errors += 1
                    st.processed += 1
            await db.commit()

    async def _record_failed(self, org_id: uuid.UUID, tk: str, aid: uuid.UUID, window: Any, code: str, st: RunStats, *,
                             kind: str, since: datetime, errors: list[str] | None = None, model: str | None = None) -> None:
        """Dòng `refinery.analyses` cho lượt lỗi (CHỈ mã lỗi, không nội dung tin) + gắn thẻ luồng cho các dòng sổ chi phí của lượt đó
        (lượt lỗi hợp đồng vẫn tốn lượt gọi: phải tính vào trần của luồng)."""
        try:
            async with self.r.sm() as db:
                await db.execute(text("""
                    INSERT INTO refinery.analyses (id, org_id, thread_key, run_id, window_from, window_to, new_n, context_n, model,
                                                   contract_version, stage, status, errors, finished_at)
                    VALUES (:id, :o, :t, :run, :wf, :wt, :nn, :cn, :m, 'brain-out/1', 'extract', :s, CAST(:er AS jsonb), now())"""),
                                 {"id": aid, "o": org_id, "t": tk, "run": st.run_id,
                                  "wf": window.new_events[0].sent_at if window.new_events else None,
                                  "wt": window.new_events[-1].sent_at if window.new_events else None,
                                  "nn": len(window.new_events), "cn": len(window.context_events), "m": model,
                                  "s": "contract_error" if code == "contract_error" else "failed",
                                  "er": orjson.dumps([{"code": code}, *({"code": e} for e in (errors or [])[:12])]).decode()})
                await ai_cost.tag_calls(db, org_id, thread_key=tk, analysis_id=aid, purpose=budget.PURPOSE[kind], since=since)
                await db.commit()
        except Exception as exc:  # noqa: BLE001 — sổ phân tích là phụ; không che lỗi gốc
            log.warning("_record_failed: không ghi được refinery.analyses cho lượt %s (luồng %s): %s", aid, tk, err_code(exc))

    # ─── dựng cửa sổ ───────────────────────────────────────────────────────────────────────────────────────────────

    def _gevent(self, e: GEv, *, context: bool = False) -> Any:
        return l3.GroupEvent(message_id=e.message_id, sent_at=e.occurred_at, body=e.body or None, sender_person_id=e.person_id,
                             sender_name=e.person_name, kind=e.kind or "text", reply_to=None, forwarded=e.forwarded,
                             raw_event_id=e.event_id, received_at=e.received_at, context=context)

    async def _window(self, db: AsyncSession, org_id: uuid.UUID, tk: str, blocks: list[GEv]) -> tuple[Any, Any, Any]:
        from gh.biz.mem import predicates, profile_read, retrieve

        new_events = [self._gevent(b) for b in blocks]
        by_ext = {b.ext_id: b.message_id for b in blocks if b.ext_id}
        for ge, b in zip(new_events, blocks, strict=True):
            if b.reply_ext and b.reply_ext in by_ext and by_ext[b.reply_ext] != b.message_id:
                ge.reply_to = by_ext[b.reply_ext]
        first = min(b.occurred_at for b in blocks)
        backlog, next_unseen = await self._backlog(db, org_id, tk, blocks)
        acks = await self._acks(db, org_id, tk, blocks, await self._ack_from(db, org_id, tk, first), until=next_unseen)
        if acks:
            new_events = sorted([*new_events, *acks], key=lambda e: e.sent_at)
        ack_ids = {a.raw_event_id for a in acks}
        ctx_events = [c for c in await self._context(db, org_id, tk, blocks, first) if c.raw_event_id not in ack_ids]
        pack = await retrieve.pack(db, org_id, tk, [{"sender_person_id": b.person_id, "raw_event_id": b.event_id, "text": b.body}
                                                    for b in blocks], now=clock.now())
        ctx_events = await self._history_evidence(db, org_id, tk, blocks, ctx_events, pack)
        all_ids = [m.event_id for b in blocks for m in b.all_events] + [c.raw_event_id for c in ctx_events if c.raw_event_id]
        media: dict[str, list[Any]] = {}
        for r in (await db.execute(text("""
            SELECT raw_event_id, kind, text FROM clean.media_texts
            WHERE org_id = :o AND raw_event_id = ANY(:ids) AND status = 'ok' AND text IS NOT NULL ORDER BY created_at"""),
                                  {"o": org_id, "ids": all_ids})).all():
            media.setdefault(f"m_{r.raw_event_id}", []).append(l3.MediaText(f"m_{r.raw_event_id}", r.kind, r.text))
        unreadable = {"voice": 0, "image": 0, "file": 0}
        for r in (await db.execute(text("""
            SELECT a.event_id, a.mime FROM raw.attachments a
            WHERE a.org_id = :o AND a.event_id = ANY(:ids)"""), {"o": org_id, "ids": [m.event_id for b in blocks for m in b.all_events]})).all():
            if f"m_{r.event_id}" in media:
                continue
            unreadable["voice" if str(r.mime).startswith("audio/") else "image" if str(r.mime).startswith("image/") else "file"] += 1
        gname = None
        if (gid := thread_group(tk)) is not None:
            gname = (await db.execute(text("SELECT name FROM core.groups WHERE id = :g"), {"g": gid})).scalar_one_or_none()
        profile = await profile_read.snapshot(db, org_id)
        preds = [(p.code, p.description_vi) for p in await predicates.all_predicates(db)]
        window = l3.GroupWindow(thread_ref=tk, new_events=new_events, context_events=ctx_events, media_texts=media,
                                unreadable=unreadable, now=clock.now(), group_name=gname, predicates=preds, backlog=backlog)
        return window, pack, profile

    async def _backlog(self, db: AsyncSession, org_id: uuid.UUID, tk: str, blocks: list[GEv]) -> tuple[Any, datetime | None]:
        """Phần luồng lượt này KHÔNG thấy (chỉ đếm + giờ gửi, không đọc nội dung):
        (1) tin CHỜ (`pending`/`processing` của lượt khác) ngoài lượt: dư sau trần 60 tin/12.000 ký tự, tin tồn cũ hơn mà lượt an toàn/cổng
        bỏ qua để nhận ứng viên trước, tin nhập lịch sử…; (2) tin gửi SAU tin cuối của lượt nhưng đã phân tích ở lượt trước (lượt an toàn/cổng
        chạy trước, tin cũ tới trễ) — trừ tin không cần model. Trả (`l3.Backlog` hoặc None, giờ gửi sớm nhất của tin (1)/(2) sau tin cuối của
        lượt — cận trên cho lời xác nhận ngắn)."""
        mine = [m.event_id for b in blocks for m in b.all_events]
        last = max(m.occurred_at for b in blocks for m in b.all_events)
        p = (await db.execute(text("""
            SELECT count(*) AS n, min(e.occurred_at) AS lo, max(e.occurred_at) AS hi,
                   min(e.occurred_at) FILTER (WHERE e.occurred_at > :last) AS nxt
            FROM refinery.event_state s JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
            WHERE s.org_id = :o AND s.state IN ('pending', 'processing') AND s.detail ->> 'thread_key' = :t
              AND NOT (s.event_id = ANY(:mine))"""), {"o": org_id, "t": tk, "mine": mine, "last": last})).one()
        d_n, d_at = 0, None
        scope = await self._thread_scope(db, tk)
        if scope is not None:
            where, sp = scope
            # chỉ tin MODEL đã phân tích (có analysis_id) — tin bị bộ lọc/luật loại không phải "đã được phân tích"; trần BACKLOG_DONE_MAX dòng
            d = (await db.execute(text(f"""
                SELECT count(*) AS n, min(x.occurred_at) AS lo FROM (
                    SELECT e.occurred_at
                    FROM raw.events e JOIN refinery.event_state s ON s.event_id = e.id AND s.event_received_at = e.received_at
                    WHERE e.org_id = :o AND {where} AND e.occurred_at > :last AND NOT (e.id = ANY(:mine))
                      AND s.state NOT IN ('pending', 'processing', 'error') AND s.detail ->> 'thread_key' = :t AND s.detail ? 'analysis_id'
                    ORDER BY e.occurred_at LIMIT :cap) x"""),  # noqa: S608 — `where` là hằng + outbound_to_sql nội bộ
                                  {"o": org_id, "t": tk, "mine": mine, "last": last, "cap": BACKLOG_DONE_MAX, **sp})).one()
            d_n, d_at = int(d.n), d.lo
        if not p.n and not d_n:
            return None, None
        until = min((x for x in (p.nxt, d_at) if x is not None), default=None)
        return l3.Backlog(int(p.n), p.lo, p.hi, d_n, d_at), until

    async def _history_evidence(self, db: AsyncSession, org_id: uuid.UUID, tk: str, blocks: list[GEv], ctx_events: list[Any],
                                pack: Any) -> list[Any]:
        """Tin bằng chứng của các đơn vị trong khối "Lịch sử" (`retrieve.pack`, cùng luồng) được thêm vào tin ngữ cảnh — để model
        TRÍCH được việc trước đó (lời hứa giữ giá, đợt thanh toán…) và code kiểm được trích dẫn. Trần `HISTORY_EVIDENCE_MAX` tin /
        `HISTORY_EVIDENCE_CHARS` ký tự (một phần trần lịch sử ≈ 2.400 token); tin đã có trong cửa sổ không lặp lại. Tin bằng chứng của đơn vị
        `nghia_vu_loi_hua`/`viec_han` được chọn TRƯỚC khi áp trần (`pick_history_evidence`): lời hứa cũ thiếu mã tin hứa thì không đóng được."""
        have = {m.event_id for b in blocks for m in b.all_events} | {c.raw_event_id for c in ctx_events if c.raw_event_id}
        want = pick_history_evidence(pack.history, have)
        scope = await self._thread_scope(db, tk)
        if not want or scope is None:
            return ctx_events
        where, sp = scope
        # phòng thủ nhiều lớp (CNS-086): chỉ tin CÙNG luồng (nhóm; 1-1 ⇒ đúng người + kênh) dù bằng chứng trỏ đi đâu
        rows = (await db.execute(text(f"""
            SELECT e.id, e.received_at, e.occurred_at, e.kind, e.body_text, e.payload, e.external_msg_id, e.sender_identity_id,
                   pi.person_id, p.display_name
            FROM unnest(CAST(:wids AS uuid[]), CAST(:wrs AS timestamptz[])) AS u(id, r)
            JOIN raw.events e ON e.id = u.id AND e.received_at = u.r
            LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            LEFT JOIN core.persons p ON p.id = pi.person_id
            WHERE e.org_id = :o AND {where}
            ORDER BY e.occurred_at DESC"""), {"o": org_id, "wids": list(want), "wrs": list(want.values()), **sp})).all()  # noqa: S608 — `where` là hằng nội bộ
        extra: list[Any] = []
        used = 0
        for r in rows:
            n = len(r.body_text or "")
            if used + n > HISTORY_EVIDENCE_CHARS:
                continue
            used += n
            extra.append(self._ctx_event(r))
        return sorted([*ctx_events, *extra], key=lambda c: c.sent_at)

    async def _ack_from(self, db: AsyncSession, org_id: uuid.UUID, tk: str, first: datetime) -> datetime:
        """Cận dưới lời xác nhận ngắn của lượt: ngay SAU tin đã phân tích gần nhất trước cửa sổ (tối đa `CONTEXT_WINDOW` trước tin mới
        đầu). Lời xác nhận đến giữa hai lượt ("Dạ chị nhận được rồi nha" trước tin kích hoạt) không tự kích hoạt lượt nào; trước đây nó chỉ
        hiện ở tin ngữ cảnh ("đã xử lý") nên không bao giờ là tin mới của lượt nào (review fix-gkb2 M2)."""
        lo = first - CONTEXT_WINDOW
        scope = await self._thread_scope(db, tk)
        if scope is None:
            return first
        where, sp = scope
        prev = (await db.execute(text(f"""
            SELECT e.occurred_at FROM raw.events e JOIN refinery.event_state s ON s.event_id = e.id AND s.event_received_at = e.received_at
            WHERE e.org_id = :o AND {where} AND e.occurred_at < :first AND e.occurred_at >= :lo AND s.detail ? 'analysis_id'
            ORDER BY e.occurred_at DESC LIMIT 1"""), {"o": org_id, "first": first, "lo": lo, **sp})).scalar_one_or_none()  # noqa: S608
        return (prev + timedelta(microseconds=1)) if prev is not None else lo

    async def _acks(self, db: AsyncSession, org_id: uuid.UUID, tk: str, blocks: list[GEv], first: datetime, *,
                    until: datetime | None = None) -> list[Any]:
        """Lời xác nhận ngắn ("Dạ e ghi nhận ạ", "ok ạ") của CÙNG luồng, từ `first` (= `_ack_from`: ngay sau tin đã phân tích gần nhất) trở đi (cận dưới updated_at/received_at = mốc cũ hơn
        giữa giờ gửi và giờ vào kho của tin mới đầu, trừ `ACK_LOOKBACK` — truy vấn có cận theo cột có index, không cắt ack thuộc cửa sổ). Chúng không kích hoạt lượt, không
        tính trần/đếm tin (vẫn `discarded`, lý do `ack`) nhưng là BẰNG CHỨNG "đã xử lý" — thiếu chúng model báo nhầm "đơn chưa được xác
        nhận" (lỗi kb1 #4). Đưa vào cửa sổ đúng chỗ theo thời gian, tối đa `ACK_MAX` tin (mỗi tin ≤ 24 ký tự). `until` = giờ gửi tin chờ đầu
        tiên SAU cửa sổ (luồng còn tồn đọng): ack từ mốc đó trở đi thuộc lượt sau — đưa vào lượt này thì nó thành "hoạt động mới nhất" của
        nhóm, che mất phần tồn đọng (lỗi kb1-r2: sticker 05–07/10 trong lượt tới 26/09)."""
        have = {m.event_id for b in blocks for m in b.all_events}
        rows = (await db.execute(text("""
            SELECT e.id, e.received_at, e.occurred_at, e.kind, e.body_text, e.payload, e.external_msg_id, e.sender_identity_id,
                   pi.person_id, p.display_name
            FROM refinery.event_state s JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
            LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            LEFT JOIN core.persons p ON p.id = pi.person_id
            WHERE s.org_id = :o AND s.state = 'discarded' AND s.updated_at >= :lo AND s.event_received_at >= :lo AND e.received_at >= :lo
              AND s.detail ->> 'thread_key' = :t AND s.detail ->> 'reason' = 'ack'
              AND e.occurred_at >= :first AND e.occurred_at <= :now AND (CAST(:until AS timestamptz) IS NULL OR e.occurred_at < :until)
            ORDER BY e.occurred_at LIMIT :n"""),
                                 {"o": org_id, "t": tk, "first": first, "now": clock.now(), "n": ACK_MAX, "until": until,
                                  # mốc CŨ HƠN giữa giờ gửi tin mới đầu và giờ vào kho của tin mới, trừ biên lệch giờ: ack có
                                  # occurred_at ≥ first thì vào kho/được quét ≥ first − biên ⇒ không bao giờ bị cắt, truy vấn vẫn có cận
                                  "lo": min(first, min(b.received_at for b in blocks)) - ACK_LOOKBACK})).all()
        out = []
        for r in rows:
            if r.id in have:
                continue
            ge = self._ctx_event(r)
            ge.context = False
            out.append(ge)
        return out

    @staticmethod
    def _ctx_event(r: Any) -> Any:
        return l3.GroupEvent(message_id=f"m_{r.id}", sent_at=r.occurred_at, body=r.body_text or None, sender_person_id=r.person_id,
                             sender_name=r.display_name, kind=r.kind or "text", forwarded=bool(_payload_get(r.payload, *FORWARD_KEYS)),
                             raw_event_id=r.id, received_at=r.received_at, context=True)

    async def _thread_scope(self, db: AsyncSession, tk: str) -> tuple[str, dict[str, Any]] | None:
        """Điều kiện SQL (alias `e` = raw.events) "tin thuộc luồng `tk`": nhóm ⇒ `group_id`; 1-1 ⇒ không nhóm + đúng kênh + người
        đó gửi HOẶC gửi tới người đó. None = luồng không xác định được (không lấy tin nào)."""
        gid = thread_group(tk)
        if gid is not None:
            return "e.group_id = :g", {"g": gid}
        pid, chan = thread_dm(tk)
        if chan is None or pid is None:
            return None
        ids: list[uuid.UUID] = []
        ext: list[str] = []
        for r in (await db.execute(text("SELECT id, external_id FROM core.person_identities WHERE person_id = :p AND channel_id = :c"),
                                   {"p": pid, "c": chan})).all():
            ids.append(r.id)
            if (b := _bare(r.external_id)):
                ext.append(b)
        if not ids:
            return None
        return (f"e.group_id IS NULL AND e.channel_id = :c AND (e.sender_identity_id = ANY(:ids) OR "
                f"{outbound_to_sql('e', 'CAST(:ids AS uuid[])', 'CAST(:ext AS text[])')})", {"c": chan, "ids": ids, "ext": ext})

    async def _context(self, db: AsyncSession, org_id: uuid.UUID, tk: str, blocks: list[GEv], first: datetime) -> list[Any]:
        """Cửa sổ gần: tối đa 30 tin VÀ trong 24 giờ trước tin mới đầu tiên, đã xử lý (không `pending`/`processing`), CÙNG luồng."""
        scope = await self._thread_scope(db, tk)
        if scope is None:
            return []
        where, sp = scope
        params: dict[str, Any] = {"o": org_id, "first": first, "lo": first - CONTEXT_WINDOW, "n": CONTEXT_EVENTS, **sp}
        rows = (await db.execute(text(f"""
            SELECT e.id, e.received_at, e.occurred_at, e.kind, e.body_text, e.payload, e.external_msg_id, e.sender_identity_id,
                   pi.person_id, p.display_name
            FROM raw.events e JOIN refinery.event_state s ON s.event_id = e.id AND s.event_received_at = e.received_at
            LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            LEFT JOIN core.persons p ON p.id = pi.person_id
            WHERE e.org_id = :o AND {where} AND e.occurred_at < :first AND e.occurred_at >= :lo
              AND s.state NOT IN ('pending', 'processing')
            ORDER BY e.occurred_at DESC LIMIT :n"""), params)).all()  # noqa: S608 — `where` là hằng + outbound_to_sql nội bộ
        out = [self._ctx_event(r) for r in rows]
        out.reverse()
        return out

    # ─── ghi: MỘT giao dịch ngắn ───────────────────────────────────────────────────────────────────────────────────

    async def _write(self, org_id: uuid.UUID, st: RunStats, tk: str, aid: uuid.UUID, kind: str, blocks: list[GEv], window: Any,
                     pack: Any, profile: Any, analysis: Any, since: datetime, secrets: tuple[str, ...] = ()) -> None:
        from gh.biz.mem import predicates
        from gh.biz.mem import profile as profile_mod

        evs = [m for b in blocks for m in b.all_events]
        persons = {b.person_id for b in blocks if b.person_id} | {c.sender_person_id for c in window.context_events if c.sender_person_id}
        async with self.r.sm() as db:
            known_preds = await predicates.codes(db)
            if not (persons & set(await profile_mod.owner_person_ids(db, org_id))):
                known_preds = {p for p in known_preds if not p.startswith("owner_")}      # không có lời Sếp trong cửa sổ ⇒ không có vị từ Hồ sơ
        verified = self.verify(analysis.out, window.all_events, window.media_texts, now=clock.now(), tz=window.tz, thread_ref=tk,
                               profile_fact_ids=profile.fact_ids(), known_person_refs={f"u_{p}" for p in persons},
                               known_predicates=known_preds, secrets=secrets)
        block_of = {m.event_id: b for b in blocks for m in b.all_events}
        cited: dict[uuid.UUID, str] = {}                          # event_id → nhãn (kind) của đơn vị đầu tiên trích tin đó
        unit_rows: list[tuple[uuid.UUID, datetime, str]] = []
        errors: list[dict[str, Any]] = [{"code": e} for e in verified.errors[:12]]
        # nguồn của MỌI tin model được trích (tin mới + tin ngữ cảnh đã xử lý): received_at là một nửa khoá của raw.events
        srcs: dict[uuid.UUID, Src] = {c.raw_event_id: Src(c.received_at, thread_group(tk), c.sender_person_id, c.forwarded)
                                      for c in window.all_events if c.raw_event_id is not None and c.received_at is not None}
        srcs |= {e.event_id: Src(e.received_at, e.group_id, e.person_id, e.forwarded) for e in evs}
        ctx_gev = await self._ctx_gevs(org_id, [(c.raw_event_id, c.received_at) for c in window.all_events
                                                if c.raw_event_id is not None and c.received_at is not None and c.raw_event_id not in block_of])
        failed_safety: list[str] = []                             # unit_ref của đơn vị lừa đảo/an toàn ghi lỗi (fail-high)
        hold: set[uuid.UUID] = set()                              # tin của các đơn vị đó: giữ chờ để thử lại, không bỏ
        async with self.r.sm() as db:
            await db.execute(text("""
                INSERT INTO refinery.analyses (id, org_id, thread_key, run_id, window_from, window_to, new_n, context_n, facts_in_n,
                                               profile_version, contract_version, model, tokens_in, tokens_out, stage, status, errors,
                                               started_at, finished_at)
                VALUES (:id, :o, :t, :run, :wf, :wt, :nn, :cn, :fn, :pv, 'brain-out/1', :m, :ti, :to, 'extract', 'ok',
                        CAST(:er AS jsonb), :st, now())"""),
                             {"id": aid, "o": org_id, "t": tk, "run": st.run_id, "wf": window.new_events[0].sent_at,
                              "wt": window.new_events[-1].sent_at, "nn": len(evs), "cn": len(window.context_events),
                              "fn": len(pack.facts) + len(pack.related), "pv": getattr(pack, "profile_version", None),
                              "m": analysis.model, "ti": analysis.tokens_in, "to": analysis.tokens_out, "er": "[]", "st": since})
            fact_ins: list[Any] = []
            unit_ids: dict[str, tuple[uuid.UUID, datetime]] = {}
            for vu in verified.units:
                u = vu.unit
                if vu.status != "ok":
                    errors.append({"code": "unit_absent", "unit_ref": u.unit_ref})
                    continue
                vqs = vu.verified_quotes
                first_q = vqs[0]
                # nguồn = tin trích ĐẦU TIÊN (tin mới hoặc tin ngữ cảnh/bằng chứng): người/nhóm của đơn vị, mention và "theo lời" của sổ tay
                first_ev = next((e for e in evs if e.event_id == first_q.raw_event_id), None) or ctx_gev.get(first_q.raw_event_id) or evs[0]
                src0 = srcs.get(first_q.raw_event_id) if first_q.raw_event_id is not None else None
                observed = vu.event_at or vu.msg_at or first_q.sent_at or first_ev.occurred_at
                try:
                    # Cách ly TỪNG đơn vị (savepoint): một đơn vị/bằng chứng ghi lỗi chỉ loại chính nó, không huỷ cả lượt.
                    async with db.begin_nested():
                        uid = await self._write_unit(db, org_id, st, aid, u, vqs, observed, first_ev, src0, srcs, persons, vu=vu)
                except Exception as exc:  # noqa: BLE001 — lỗi ghi một đơn vị: ghi mã lỗi (không nội dung tin), đơn vị khác vẫn ghi
                    log.error("Ghi đơn vị %s của lượt %s lỗi: %s", u.unit_ref, aid, err_code(exc))
                    safety = is_safety_unit(u)
                    errors.append({"code": "unit_write_failed_safety" if safety else "unit_write_failed", "unit_ref": u.unit_ref,
                                   "kind": u.kind, "gate": u.gate_candidate, "error": type(exc).__name__})
                    if safety:
                        failed_safety.append(u.unit_ref)
                        mine = {q.raw_event_id for q in vqs if q.raw_event_id is not None} & set(block_of)
                        # chỉ trích tin ngữ cảnh/tin bằng chứng (không thuộc lượt) ⇒ giữ MỌI tin mới của lượt để thử lại cả lượt
                        hold.update(mine or set(block_of))
                    continue
                unit_ids[u.unit_ref] = (uid, observed)
                unit_rows.append((uid, observed, u.summary[:400]))
                st.unit_ids.append(str(uid))
                for q in vqs:
                    if q.raw_event_id is not None and q.raw_event_id not in cited:
                        cited[q.raw_event_id] = u.kind or "GroupUnit"
            fact_ins = await self._facts(db, org_id, verified, unit_ids, evs, persons, srcs=srcs, thread_gid=thread_group(tk))
            if fact_ins:
                try:
                    async with db.begin_nested():
                        res = await facts_mod.assert_facts(db, fact_ins, analysis_id=aid)
                    st.facts_written += len(res.written)
                    errors.extend(res.errors)
                except Exception as exc:  # noqa: BLE001 — sự thật đề xuất lỗi ghi: đơn vị đã ghi vẫn giữ
                    log.error("Ghi sự thật của lượt %s lỗi: %s", aid, err_code(exc))
                    errors.append({"code": "facts_write_failed", "n": len(fact_ins), "error": type(exc).__name__})
            await self._promises_kept(db, org_id, aid, verified, window, evs, tk, errors)
            # trạng thái từng tin: có đơn vị trích ⇒ clean; còn lại ⇒ discarded (không có sự việc)
            held: set[uuid.UUID] = set()
            for e in evs:
                head = block_of[e.event_id]
                is_cited = any(x.event_id in cited for x in head.all_events)
                label = next((cited[x.event_id] for x in head.all_events if x.event_id in cited), None)
                if any(x.event_id in hold for x in head.all_events):
                    # Đơn vị lừa đảo/an toàn của tin này ghi lỗi ⇒ KHÔNG bỏ/đóng tin (fail-high), kể cả khi đơn vị thường khác đã trích nó: giữ chờ, tính một lần thử, lùi nhịp; đủ
                    # MAX_ATTEMPTS ⇒ `error` (bộ hẹn giờ F055 nhận lại). Ứng viên luật (a) vẫn ở detail.cands cho T1.
                    await db.execute(text("""
                        UPDATE refinery.event_state SET state = CASE WHEN attempts >= :mx THEN 'error' ELSE 'pending' END, updated_at = now(),
                               detail = detail || CAST(:d AS jsonb)
                        WHERE event_id = :e AND event_received_at = :r"""),
                                     {"mx": MAX_ATTEMPTS, "e": e.event_id, "r": e.received_at,
                                      "d": orjson.dumps({"last_error": "unit_write_failed_safety", "analysis_id": str(aid),
                                                         "next_try_at": (clock.now() + RETRY_BACKOFF).isoformat()}).decode()})
                    held.add(e.event_id)
                    continue
                await self.r._set_state(db, e, "clean" if is_cited else "discarded", st.run_id, label=label if is_cited else "Noise",
                                        confidence=UNVERIFIED_CONFIDENCE if is_cited else None,
                                        detail={"analysis_id": str(aid), "model": analysis.model, "thread_key": tk,
                                                **({} if is_cited else {"discarded_by": "group_no_unit"})})
                st.processed += 1
                if is_cited:
                    st.clean += 1
                else:
                    st.noise += 1
            errors.extend({"code": k, "n": n} for k, n in sorted(verified.counters.items()))        # bộ đếm lỗi kiểm (mã + số, không nội dung)
            if errors:
                await db.execute(text("UPDATE refinery.analyses SET errors = CAST(:e AS jsonb) WHERE id = :i"),
                                 {"e": orjson.dumps(errors).decode(), "i": aid})
            await db.execute(text("""
                INSERT INTO refinery.group_watermarks (org_id, thread_key, group_id, last_received_at, last_event_id, last_analysis_at,
                                                       pending_n)
                VALUES (:o, :t, :g, :lr, :le, :now, (SELECT count(*) FROM refinery.event_state WHERE org_id = :o AND state = 'pending'
                                                      AND detail ->> 'thread_key' = :t))
                ON CONFLICT (org_id, thread_key) DO UPDATE SET last_received_at = EXCLUDED.last_received_at,
                       last_event_id = EXCLUDED.last_event_id, last_analysis_at = EXCLUDED.last_analysis_at,
                       pending_n = EXCLUDED.pending_n, group_id = COALESCE(refinery.group_watermarks.group_id, EXCLUDED.group_id)"""),
                             {"o": org_id, "t": tk, "g": thread_group(tk), "lr": max(e.received_at for e in evs),
                              "le": evs[-1].event_id, "now": clock.now()})
            await ai_cost.tag_calls(db, org_id, thread_key=tk, analysis_id=aid, purpose=budget.PURPOSE[kind], since=since)
            await db.commit()
        if failed_safety:
            await self.alert_once(org_id, "intel_write_failed_safety", alert_type="intel_write_failed_safety", priority="P1",
                                  title="Cảnh báo lừa đảo/an toàn chưa ghi được",
                                  summary=f"Lượt phân tích {aid} có {len(failed_safety)} đơn vị lừa đảo/an toàn ghi lỗi; tin liên quan được giữ chờ "
                                          "để phân tích lại.",
                                  suggested="Xem Hệ thống › Nhật ký (refinery.analyses.errors) và luồng đó trong Hộp thư.")
        for e in evs:
            if e.event_id in held:
                continue
            head = block_of[e.event_id]
            ok = any(x.event_id in cited for x in head.all_events)
            await self.r._raw_state(org_id, e, "clean" if ok else "discarded", cited.get(e.event_id) or ("Noise" if not ok else None),
                                    UNVERIFIED_CONFIDENCE if ok else None)
        await self.r._embed(org_id, unit_rows)

    async def _promises_kept(self, db: AsyncSession, org_id: uuid.UUID, aid: uuid.UUID, verified: Any, window: Any, evs: list[GEv],
                             tk: str, errors: list[dict[str, Any]]) -> None:
        """`memory_updates.promise_kept` ⇒ đóng hiệu lực lời hứa trong sổ qua cổng tin cậy của `facts.close_kept` (kỹ năng v2.1 §9.3:
        [tin hứa, tin đã giữ]). Cờ của từng tin được trích (tin mới của lượt? chuyển tiếp? có chữ? luồng, trả lời tin nào) lấy từ kho thô,
        không từ model. Đếm `promise_kept_closed` / `promise_kept_untrusted` / `promise_kept_unmatched` (mã + số, không nội dung)."""
        by_mid = {e.message_id: e for e in window.all_events if e.raw_event_id is not None}
        mine = {e.event_id: e for e in evs}
        for mu in verified.memory_updates:
            if mu.type != "promise_kept":
                continue
            cited: list[Any] = []
            for i in dict.fromkeys(mu.message_ids):
                ge = by_mid.get(i)
                if ge is None:
                    continue
                g = mine.get(ge.raw_event_id)
                # tin mới của lượt = tin được nhận vào lượt + lời xác nhận ngắn đưa vào cửa sổ (`_acks`, context=False, cùng luồng)
                fresh = g is not None or not ge.context
                reply_raw = None
                if g is not None and g.reply_ext:
                    reply_raw = (await db.execute(text("""SELECT id FROM raw.events WHERE org_id = :o AND channel_id = :c AND external_msg_id = :x
                                                          LIMIT 1"""), {"o": org_id, "c": g.channel_id, "x": g.reply_ext})).scalar_one_or_none()
                cited.append(facts_mod.KeptMsg(
                    ge.raw_event_id, ge.sent_at, g.person_id if g is not None else ge.sender_person_id, new=fresh,
                    forwarded=bool(g.forwarded if g is not None else ge.forwarded),
                    has_text=bool(((g.body if g is not None else ge.body) or "").strip()), thread_key=tk if fresh else None,
                    reply_to_raw=reply_raw, ack=g is None and not ge.context))
            try:
                async with db.begin_nested():
                    res = await facts_mod.close_kept(db, org_id, cited)
            except Exception as exc:  # noqa: BLE001 — đóng lời hứa lỗi: lượt vẫn ghi, mã lỗi vào analyses
                log.error("Đóng lời hứa đã giữ của lượt %s lỗi: %s", aid, err_code(exc))
                errors.append({"code": "promise_kept_failed", "error": type(exc).__name__})
                continue
            if res.closed:
                verified.bump("promise_kept_closed", len(res.closed))
            for code, n in (("promise_kept_untrusted", res.untrusted), ("promise_kept_ambiguous", res.ambiguous),
                            ("promise_kept_self_report", res.self_report)):
                if n:
                    verified.bump(code, n)
            if not (res.closed or res.untrusted or res.ambiguous or res.self_report):
                verified.bump("promise_kept_unmatched")

    async def _ctx_gevs(self, org_id: uuid.UUID, ids: list[tuple[uuid.UUID, datetime]]) -> dict[uuid.UUID, GEv]:
        """`GEv` đầy đủ (người gửi + mã người, nhóm, hướng) cho tin ngữ cảnh/bằng chứng của cửa sổ — để đơn vị trích chúng được ghi
        đúng người nói, không mượn người gửi tin mới đầu tiên."""
        if not ids:
            return {}
        async with self.r.sm() as db:
            rows = (await db.execute(text("""
                SELECT e.id, e.received_at, e.occurred_at, e.seq, e.kind, e.body_text, e.direction, e.group_id, e.channel_id,
                       e.sender_identity_id, pi.person_id, p.display_name, p.code
                FROM unnest(CAST(:ids AS uuid[]), CAST(:rs AS timestamptz[])) AS u(id, r)
                JOIN raw.events e ON e.id = u.id AND e.received_at = u.r
                LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                LEFT JOIN core.persons p ON p.id = pi.person_id
                WHERE e.org_id = :o"""), {"o": org_id, "ids": [i for i, _ in ids], "rs": [r for _, r in ids]})).all()
        return {r.id: GEv(r.id, r.received_at, r.occurred_at, r.seq or 0, r.kind or "text", r.body_text or "", r.direction, r.group_id,
                          r.channel_id, r.sender_identity_id, r.person_id, r.display_name, r.code) for r in rows}

    async def _write_unit(self, db: AsyncSession, org_id: uuid.UUID, st: RunStats, aid: uuid.UUID, u: Any, vqs: list[Any],
                          observed: datetime, first_ev: GEv, src0: "Src | None", srcs: dict[uuid.UUID, "Src"],
                          persons: set[uuid.UUID], *, vu: Any = None) -> uuid.UUID:
        """Ghi MỘT đơn vị + bằng chứng + mention + sổ tay (bên gọi bọc savepoint). Tin được trích có thể là tin ngữ cảnh (đã xử lý ở
        lượt trước): `received_at` lấy từ `srcs` (đủ cả tin mới lẫn tin ngữ cảnh) — thiếu ⇒ ném lỗi rõ ràng thay vì chèn NULL.
        v0.1.76 T12: giá trị ĐÃ KIỂM của `verify` (mốc, hạn, trạng thái, giao dịch, hình thức, vai người) lưu ở `entities.v` để bộ chiếu
        đơn vị (`gh.biz.intel.detectors.units`) dựng `SignalIn` đúng bảng nguồn §4.2.1 (c) — không đọc lại lời model thô."""
        uid = (await db.execute(text("""
            INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, side, conclusion, entities,
                                             confidence, run_id, extractor_version, kind_hint, analysis_id, flags, gate_candidate,
                                             level_hint, harm)
            VALUES (:o, :obs, :g, :p, 'GroupUnit', NULL, :c, CAST(:ent AS jsonb), :conf, :run, :ev, :kh, :a, CAST(:fl AS jsonb),
                    :gc, :lh, CAST(:hm AS jsonb))
            RETURNING id"""),
                                 {"o": org_id, "obs": observed, "g": first_ev.group_id if src0 is None else (src0.group_id or first_ev.group_id),
                                  "p": first_ev.person_id if src0 is None else src0.person_id,
                                  "c": u.summary[:400], "ent": orjson.dumps({"unit_ref": u.unit_ref, "event_ref": u.event_ref,
                                                                            "kind": u.kind}).decode(),
                                  "conf": UNVERIFIED_CONFIDENCE, "run": st.run_id, "ev": l3.EXTRACTOR_VERSION, "kh": u.kind,
                                  "a": aid, "fl": orjson.dumps(u.flags.model_dump()).decode(), "gc": u.gate_candidate,
                                  "lh": u.level_hint if u.level_hint in ("red", "amber", "grey") else None,
                                  "hm": orjson.dumps(u.harm.model_dump()).decode() if u.harm is not None else None})).scalar_one()
        for q in vqs:
            src = srcs.get(q.raw_event_id) if q.raw_event_id is not None else None
            if q.raw_event_id is not None and src is None:
                raise LookupError("evidence_source_unknown")       # trích tin ngoài cửa sổ: không đoán received_at
            await db.execute(text("""
                INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, quote_status,
                                            source_field, span_start, span_end, verified_at, extractor_version)
                VALUES (:m, :mo, :e, :r, :q, 'verified', :sf, :ss, :se, now(), :ev) ON CONFLICT DO NOTHING"""),
                             {"m": uid, "mo": observed, "e": q.raw_event_id, "r": src.received_at if src else None,
                              "q": q.text[:280], "sf": q.field, "ss": q.span_start, "se": q.span_end, "ev": l3.EXTRACTOR_VERSION})
        people: list[dict[str, Any]] = []
        for pm in u.people:
            ent = await self._mention(db, org_id, uid, observed, first_ev, pm, persons)
            people.append({"role": pm.role, "entity_id": str(ent) if ent else None, "surface": pm.surface[:120]})
        await db.execute(text("UPDATE clean.meaning_units SET entities = entities || CAST(:v AS jsonb) WHERE id = :i AND observed_at = :t"),
                         {"v": orjson.dumps({"v": verified_fields(vu, u, people)}).decode(), "i": uid, "t": observed})
        await self._notebook(db, org_id, uid, u, first_ev)
        return uid

    async def _notebook(self, db: AsyncSession, org_id: uuid.UUID, uid: uuid.UUID, u: Any, first_ev: GEv) -> None:
        ev = Ev(first_ev.event_id, first_ev.received_at, first_ev.seq, first_ev.occurred_at, first_ev.kind, first_ev.body,
                first_ev.direction, first_ev.group_id, None, first_ev.person_id, first_ev.person_code, group_name=None,
                person_name=first_ev.person_name)
        et = "Complained" if u.kind == "xung_dot_khieu_nai" else "GroupUnit"
        await notebook_note(db, org_id, "attention_now" if et in ATTENTION_TYPES else "rolling_context", uid, u.summary[:400], ev,
                            first_ev.person_id, first_ev.group_id)

    async def _mention(self, db: AsyncSession, org_id: uuid.UUID, uid: uuid.UUID, observed: datetime, first_ev: GEv, pm: Any,
                       persons: set[uuid.UUID]) -> uuid.UUID | None:
        from gh.biz.mem import entities, mentions

        ent: uuid.UUID | None = None
        if pm.person_ref and pm.person_ref.startswith("u_"):
            try:
                pid = uuid.UUID(pm.person_ref[2:])
            except ValueError:
                pid = None
            if pid in persons:                                   # chỉ người CÓ mặt trong cửa sổ — mã lạ của model bị bỏ
                ent = (await entities.ensure_person(db, org_id, pid)).id
        m = await mentions.link(db, org_id, meaning_unit_id=uid, meaning_observed_at=observed, surface=pm.surface,
                                raw_event_id=first_ev.event_id, group_id=first_ev.group_id, entity_id=ent, method="llm" if ent else None)
        return m.entity_id if m.status == "linked" else None            # chỉ người nối CHẮC (không ứng viên gần đúng) mới mang vai

    async def _resolve(self, db: AsyncSession, org_id: uuid.UUID, person_ref: str | None, surface: str | None, group_id: uuid.UUID | None,
                       persons: set[uuid.UUID]) -> uuid.UUID | None:
        from gh.biz.mem import aliases, entities

        if person_ref and person_ref.startswith("u_"):
            try:
                pid = uuid.UUID(person_ref[2:])
            except ValueError:
                return None
            return (await entities.ensure_person(db, org_id, pid)).id if pid in persons else None
        if surface:
            m = await aliases.resolve(db, org_id, surface, group_id)
            if m and m[0].method == "alias_exact" and len([x for x in m if x.method == "alias_exact"]) == 1:
                return m[0].entity_id
        return None

    async def _facts(self, db: AsyncSession, org_id: uuid.UUID, verified: Any, unit_ids: dict[str, tuple[uuid.UUID, datetime]],
                     evs: list[GEv], persons: set[uuid.UUID], *, srcs: dict[uuid.UUID, "Src"] | None = None,
                     thread_gid: uuid.UUID | None = None) -> list[Any]:
        """`verified.facts` → `FactIn`. Chủ thể/đối tượng chỉ nối được khi `person_ref` là người CÓ mặt trong cửa sổ hoặc cách gọi khớp
        MỘT thực thể chính xác (không đoán). `payment_account` KHÔNG ghi từ model (STK chỉ qua luật (a) + bảng đã xác minh — T7)."""
        from gh.biz.mem import entities, predicates
        from gh.biz.mem import profile as profile_mod

        owner_people = set(await profile_mod.owner_person_ids(db, org_id))
        units_by_ref = {vu.unit.unit_ref: vu for vu in verified.units}
        out: list[Any] = []
        for vf in verified.facts:
            f = vf.fact
            if f.unit_ref not in unit_ids or f.predicate == "payment_account":
                verified.bump("fact_skipped")
                continue
            try:
                pred = await predicates.get(db, f.predicate)
            except predicates.UnknownPredicate:
                verified.bump("unknown_predicate")
                continue
            if f.predicate.startswith("owner_") and (vf.speaker_person_id is None or vf.speaker_person_id not in owner_people):
                verified.bump("fact_owner_predicate_non_owner")             # Hồ sơ Sếp chỉ nhận từ lời CHÍNH Sếp, không từ người khác
                continue
            quotes = vf.evidence
            if f.predicate.startswith("owner_"):
                # MỌI trích dẫn làm bằng chứng cho Hồ sơ Sếp phải là tin do CHÍNH Sếp viết (định danh người gửi), không phải tin chuyển tiếp;
                # `forwarded=null` (Zalo) còn phải qua phép kiểm "không phải bản dán lại lời người khác". Trích dẫn không đạt bị loại.
                quotes = [q for q in quotes if q.raw_event_id is not None and (await profile_mod.owner_authored(db, org_id, q.raw_event_id))[0]]
                if not quotes:
                    verified.bump("fact_owner_predicate_untrusted_evidence")
                    continue
            q0 = quotes[0]
            first_ev = next((e for e in evs if e.event_id == q0.raw_event_id), None)
            src0 = (srcs or {}).get(q0.raw_event_id) if q0.raw_event_id is not None else None      # cả tin ngữ cảnh đã xử lý
            gid = first_ev.group_id if first_ev else (src0.group_id if src0 else thread_gid)
            subj = await self._resolve(db, org_id, f.subject.person_ref, f.subject.surface, gid, persons)
            if subj is None:
                verified.bump("fact_subject_unresolved")
                continue
            obj_ent: uuid.UUID | None = None
            obj_val: Any = None
            if pred.object_kind == "entity":
                obj_ent = await self._resolve(db, org_id, f.object.person_ref, f.object.surface, gid, persons)
                if obj_ent is None:
                    verified.bump("fact_object_unresolved")
                    continue
            else:
                obj_val = f.object.value if f.object.value is not None else f.object.surface
                if obj_val is None:
                    verified.bump("fact_value_missing")
                    continue
                if f.predicate in OBLIGATION_PREDICATES:
                    # hợp đồng đọc của /me/obligations + /me/time (L4): {"text", "due_at", "at", "to_entity_id"}; hạn/giờ do CODE tính (vi_time) ở đơn vị
                    vu = units_by_ref.get(f.unit_ref)
                    to_ent = await self._resolve(db, org_id, f.object.person_ref, None, gid, persons) if f.object.person_ref else None
                    obj_val = {"text": obj_val if isinstance(obj_val, str) else (f.object.surface or str(obj_val)),
                               "due_at": vu.deadline_at.isoformat() if vu is not None and vu.deadline_at is not None else None,
                               "at": vu.event_at.isoformat() if vu is not None and vu.event_at is not None and vu.unit.event_time is not None
                                     and vu.unit.event_time.text else None,          # giờ chỉ khi tin nói rõ (mặc định giờ-tin là nhiễu)
                               "to_entity_id": None if to_ent is None else str(await entities.canonical(db, to_ent))}
            speaker = (await entities.ensure_person(db, org_id, vf.speaker_person_id)).id if vf.speaker_person_id else None
            same = speaker is not None and speaker == await entities.canonical(db, subj)
            fwd = bool(first_ev.forwarded) if first_ev else bool(src0 and src0.forwarded)
            cls = "forwarded" if fwd else "insider" if same else "member"
            rel = facts_mod.RELIABILITY[cls]
            out.append(facts_mod.FactIn(
                org_id=org_id, subject_id=subj, predicate=f.predicate, valid_from=q0.sent_at or (first_ev.occurred_at if first_ev else clock.now()),
                object_entity_id=obj_ent, object_value=obj_val, source_class=cls, speaker_entity_id=speaker,
                attributed_to=None if same else speaker, source_raw_event_id=q0.raw_event_id,
                source_received_at=first_ev.received_at if first_ev else (src0.received_at if src0 else None),
                confidence_exist=UNVERIFIED_CONFIDENCE, confidence_content=round(FACT_CONTENT_FACTOR * rel, 3), context_code=None,
                extractor_version=l3.EXTRACTOR_VERSION, created_by="extractor",
                evidence=[facts_mod.EvidenceIn("raw_event", raw_event_id=q.raw_event_id, raw_received_at=next(
                    (e.received_at for e in evs if e.event_id == q.raw_event_id), (srcs or {}).get(q.raw_event_id, _NO_SRC).received_at),
                    span=q.span) for q in quotes if q.raw_event_id]))
        return out
