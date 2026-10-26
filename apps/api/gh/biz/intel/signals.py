# ruff: noqa: E501
"""Tín hiệu (spec r6.6.1 §4.1–§4.3, §4.2.1 (e)) — MODULE DUY NHẤT đổi `level`/`lane`/`status` của `biz.alerts` (rào AST trong test).

Hợp đồng T1 (gói khác chỉ đọc):
- `upsert(db, org, UpsertIn, now) -> Signal`: TỪ CHỐI trích dẫn chưa kiểm (`UnverifiedQuote`); khoá chống trùng `(case_key, kind)` trên vụ
  đang mở; không có khoá trùng thì vẫn gộp vào tín hiệu mở cùng `kind` có chung TIN GỐC (`raw_event_ids`) — một sự việc ra MỘT tín hiệu
  dù đến từ đường nhanh (luật) hay đường model. Bằng chứng mới được GẮN THÊM. Sau đó gọi `relane`.
- `relane(db, org, signal_id, now) -> Signal`: khoá tư vấn theo (org, ngày-trần) → đọc `DeliveryState` → `levels.compute` → ghi mức, ngăn,
  phân phối, trạng thái TRONG CÙNG transaction (giữ chỗ trần nguyên tử, CNS-126). "Một vụ một 🔴": `case_state` lưu thẻ đã đẩy, lần sau
  không leo thang ⇒ `merge`.
- `transition`, `resolve_by_owner` ("Đã xử lý" ⇒ ⚪ `none`), `feedback` (Sếp "đúng" thắng "máy bác"), `set_outcome`, `precision`.
- `reevaluate_due(db, now)`: đường tính lại THEO ĐỒNG HỒ — tín hiệu mở có `next_eval_at ≤ now` được `relane` lại (hạn tới gần ⇒ leo cổng
  4/6; quá 24 giờ ⇒ 7a′ theo Q15…), không cần tin mới; commit theo từng dòng, `SKIP LOCKED`, dòng lỗi không chặn dòng khác. Job
  `intel_reeval` (jobs.py) gọi mỗi phút.
- THỨ TỰ KHOÁ (chống deadlock): khoá tư vấn trần `cap_lock` (org, ngày-trần) → khoá dòng `biz.alerts` → `core.next_code`.
- `project_disputes(db, org, now)`: chiếu `mem.disputes` (có `dispute_signal_kind`) sang tín hiệu, ghi `projected_signal_id`.
- `crosscheck_plan` / `apply_crosscheck` / `run_crosscheck`: gọi (d1) `crosscheck.verify` / (d2) `crosscheck.verify_strong` của L3 và gộp
  vào độ chắc hai trục (§4D.4, §4.2.1 (b)0).
Không lưu chữ thô chưa che vào `biz.alerts` (title/summary/facts/evidence): trích dẫn chỉ ở dạng `quote_display` (đã che, ≤ 80 ký tự).
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import asks, levels, mutes
from gh.biz.mem import contexts, profile_read
from gh.chassis import actionlog
from gh.data.common import mask_text
from gh.refinery import clock

log = logging.getLogger("gh.intel.signals")
ERROR_BACKOFF = timedelta(minutes=15)                       # dòng lỗi của job đồng hồ được thử lại sau chừng này
TERMINAL = ("resolved", "broken", "expired", "dismissed")
OPEN = ("open", "acknowledged")
INTEL_KINDS = levels.KINDS
NON_INTEL_KINDS = ("system", "legacy")
SOURCE_RELIABILITY = {"owner": 1.0, "rule": 1.0, "insider": 0.90, "member": 0.75, "outsider": 0.50, "forwarded": 0.40}
PRIORITY_OF = {"red": "P1", "amber": "P2", "grey": "P3", "off": "P3"}
MAX_QUOTE_DISPLAY = 80
DEFAULT_TTL = {"nghia_vu_loi_hua": 7, "viec_han": 7, "cau_hoi_cho_xu_ly": 3, "su_kien_dip": 14, "suc_khoe_an_toan": 14,
               "xung_dot_khieu_nai": 7, "quan_he_nong_nguoi": 14, "co_hoi_ket_noi": 7, "rui_ro_lua_dao": 14, "thi_truong_thong_tin": 7}


class SignalError(ValueError):
    pass


class UnverifiedQuote(SignalError):
    """Bằng chứng có trích dẫn chưa `verified` — không bao giờ thành tín hiệu (§8.1)."""


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["meaning_unit", "raw_event", "fact"]
    id: uuid.UUID
    quote_status: Literal["verified", "absent", "unverifiable"] | None = None
    raw_event_id: uuid.UUID | None = None            # tin gốc của đơn vị/sự thật (để gộp đường nhanh với đường model)
    received_at: datetime | None = None
    source_field: Literal["body", "transcript", "ocr", "caption"] | None = None
    span_start: int | None = None
    span_end: int | None = None
    quote: str | None = Field(default=None, max_length=2000)   # chữ trích (CHỈ dùng tính quote_display đã che; không lưu nguyên)


class UpsertIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=1000)
    suggested_action: str | None = Field(default=None, max_length=500)
    action_executable: bool = False                  # có hành động thực thi được (giao việc/nhắc/nháp) ⇒ tạo ask
    detector: str = Field(min_length=1, max_length=64)
    detector_version: str | None = None
    level_input: levels.SignalIn
    evidence: list[EvidenceIn] = Field(default_factory=list)
    case_key: str = Field(min_length=1, max_length=300)
    thread_key: str | None = None
    group_id: uuid.UUID | None = None
    subject_type: Literal["person", "group"] | None = None
    subject_id: uuid.UUID | None = None
    person_ids: list[uuid.UUID] = Field(default_factory=list)   # bên liên quan (mute theo người)
    fact_ids: list[uuid.UUID] = Field(default_factory=list)
    owner_fact_ids: list[uuid.UUID] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    due_at: datetime | None = None
    expires_at: datetime | None = None
    alert_type: str = "intel"


@dataclass(frozen=True)
class Signal:
    id: uuid.UUID
    code: str
    kind: str
    level: str
    gate: str | None
    candidate: str | None
    delivery: str
    status: str
    lane: str
    case_key: str | None
    reasons: tuple[str, ...]
    pushed_at: datetime | None
    would_push_at: datetime | None
    capped_at: datetime | None
    next_eval_at: datetime | None
    ask_id: uuid.UUID | None
    created: bool = False


_COLS = ("id, code, kind, level, gate, candidate, delivery, status, lane, case_key, reasons, pushed_at, would_push_at, capped_at, "
         "next_eval_at, ask_id")


def _sig(r: Any, created: bool = False) -> Signal:
    return Signal(r.id, r.code, r.kind, r.level, r.gate, r.candidate, r.delivery, r.status, r.lane, r.case_key, tuple(r.reasons or ()),
                  r.pushed_at, r.would_push_at, r.capped_at, r.next_eval_at, r.ask_id, created)


async def get(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID) -> Signal | None:
    r = (await db.execute(text(f"SELECT {_COLS} FROM biz.alerts WHERE id = :i AND org_id = :o"), {"i": signal_id, "o": org_id})).one_or_none()  # noqa: S608
    return _sig(r) if r else None


# ─── DeliveryState (đọc trong cùng transaction giữ chỗ trần) ──────────────────

def _aware(d: datetime) -> datetime:
    return d if d.tzinfo is not None else d.replace(tzinfo=UTC)


def cap_day(now: datetime, quiet: levels.QuietHours) -> date:
    """Ngày-trần hiện hành = ngày VN của now; riêng khoảng từ giờ bắt đầu yên lặng buổi tối tới 24:00 tính là ngày VN kế tiếp (§4.2.1 (e))."""
    t = _aware(now).astimezone(levels.VN)
    start = levels._minutes(quiet.start)
    end = levels._minutes(quiet.end)
    m = t.hour * 60 + t.minute
    if start > end and m >= start:
        return t.date() + timedelta(days=1)
    return t.date()


async def intel_settings(db: AsyncSession, org_id: uuid.UUID) -> tuple[dict[str, Any], datetime]:
    r = (await db.execute(text("SELECT settings -> 'intel' AS s, created_at FROM core.organizations WHERE id = :o"), {"o": org_id})).one()
    s = r.s if isinstance(r.s, dict) else {}
    installed = profile_read.parse_when(s.get("installed_at")) or r.created_at
    return s, installed


_HHMM = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def _hhmm(s: dict[str, Any], key: str, default: str, org_id: uuid.UUID) -> str:
    """Giờ dạng HH:MM; sai định dạng ⇒ mặc định + log (không ghi giá trị sai) — một tổ chức cấu hình sai không làm hỏng job đồng hồ."""
    v = s.get(key)
    if v is None or v == "":
        return default
    if isinstance(v, str) and _HHMM.fullmatch(v):
        return v
    log.warning("intel settings: %s sai định dạng HH:MM ở tổ chức %s, dùng mặc định", key, org_id)
    return default


def _int_setting(s: dict[str, Any], key: str, default: int, org_id: uuid.UUID) -> int:
    """Số nguyên ≥ 0; thiếu ⇒ mặc định (0 là giá trị hợp lệ, không bị `or` nuốt); sai ⇒ mặc định + log."""
    v = s.get(key)
    if v is None:
        return default
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v
    log.warning("intel settings: %s không hợp lệ ở tổ chức %s, dùng mặc định", key, org_id)
    return default


async def _quiet(db: AsyncSession, org_id: uuid.UUID) -> levels.QuietHours:
    s, _ = await intel_settings(db, org_id)
    return _quiet_of(s, org_id)


def _quiet_of(s: dict[str, Any], org_id: uuid.UUID) -> levels.QuietHours:
    return levels.QuietHours(start=_hhmm(s, "quiet_start", "22:00", org_id), end=_hhmm(s, "quiet_end", "07:00", org_id))


async def cap_lock(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, wait: bool = True) -> date | None:
    """Khoá tư vấn giữ chỗ trần theo (org, ngày-trần), giữ tới hết transaction. THỨ TỰ KHOÁ (chống deadlock): mọi đường ghi tín hiệu
    (`upsert`/`transition`/`feedback`/`relane`/`reevaluate_due`) lấy khoá này TRƯỚC mọi khoá dòng `biz.alerts` và trước `core.next_code`.
    Lấy lại trong cùng transaction là no-op (khoá tư vấn tái nhập). `wait=False` (job đồng hồ): không lấy được ngay ⇒ trả None, không chờ."""
    day = cap_day(now, await _quiet(db, org_id))
    key = {"o": str(org_id), "d": day.isoformat()}
    if wait:
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('intel_cap:' || :o || ':' || :d))"), key)
        return day
    got = (await db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext('intel_cap:' || :o || ':' || :d))"), key)).scalar_one()
    return day if got else None


async def delivery_state(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, exclude: uuid.UUID | None = None) -> levels.DeliveryState:
    s, installed = await intel_settings(db, org_id)
    quiet = _quiet_of(s, org_id)
    day = cap_day(now, quiet)
    reds = (await db.execute(text("""SELECT count(*) FROM biz.alerts WHERE org_id = :o AND cap_day = :d
                                     AND (CAST(:x AS uuid) IS NULL OR id <> :x)"""), {"o": org_id, "d": day, "x": exclude})).scalar_one()

    def gates(key: str) -> list[str]:
        v = s.get(key)
        return [g for g in v if g in levels.GATES] if isinstance(v, list) else []

    return levels.DeliveryState(
        intel_mode="shadow" if s.get("mode") == "shadow" else "live",
        red_cap_per_day=_int_setting(s, "red_cap_per_day", 3, org_id), reds_pushed_today=int(reds),
        days_since_install=max(0.0, (_aware(now) - _aware(installed)).total_seconds() / 86400.0),
        ramp_early_enable=s.get("ramp_gates") == "push",
        m1_passed_gates=gates("m1_passed_gates"), low_precision_gates=gates("low_precision_gates"), quiet_hours=quiet,
        money_small_vnd=int(s.get("money_small_vnd") or 5_000_000), money_big_vnd=int(s.get("money_big_vnd") or 50_000_000))


# ─── bằng chứng ───────────────────────────────────────────────────────────────

async def _check_evidence(db: AsyncSession, org_id: uuid.UUID, evidence: list[EvidenceIn]) -> list[dict[str, Any]]:
    """Mọi phần tử phải `verified`: meaning_unit ⇒ `clean.evidence.quote_status`; fact ⇒ `mem.fact_evidence`; raw_event ⇒ do CODE cắt
    span nên `verified` theo cấu tạo, bắt buộc có `source_field/span_start/span_end` (CNS-070). Sai ⇒ `UnverifiedQuote`."""
    out: list[dict[str, Any]] = []
    for ev in evidence:
        if ev.quote_status is not None and ev.quote_status != "verified":
            raise UnverifiedQuote(f"trích dẫn chưa kiểm ({ev.type})")
        if ev.type == "raw_event":
            if ev.source_field is None or ev.span_start is None or ev.span_end is None or ev.span_end <= ev.span_start:
                raise UnverifiedQuote("bằng chứng tin thô thiếu span do code cắt")
            ok = (await db.execute(text("SELECT 1 FROM raw.events WHERE id = :i AND org_id = :o LIMIT 1"),
                                   {"i": ev.id, "o": org_id})).first() is not None
        elif ev.type == "meaning_unit":
            ok = (await db.execute(text("""
                SELECT bool_and(e.quote_status = 'verified') FROM clean.evidence e JOIN clean.meaning_units m
                  ON m.id = e.meaning_unit_id AND m.observed_at = e.meaning_observed_at
                WHERE e.meaning_unit_id = :i AND m.org_id = :o"""), {"i": ev.id, "o": org_id})).scalar_one() is True
        else:
            ok = (await db.execute(text("""SELECT bool_or(quote_status = 'verified') FROM mem.fact_evidence
                                           WHERE fact_id = :i AND org_id = :o"""), {"i": ev.id, "o": org_id})).scalar_one() is True
        if not ok:
            raise UnverifiedQuote(f"trích dẫn chưa kiểm hoặc không thuộc tổ chức ({ev.type})")
        d: dict[str, Any] = {"type": ev.type, "id": str(ev.id), "quote_status": "verified"}
        if ev.raw_event_id or ev.type == "raw_event":
            d["raw_event_id"] = str(ev.raw_event_id or ev.id)
        if ev.received_at:
            d["received_at"] = _aware(ev.received_at).isoformat()
        if ev.source_field:
            d |= {"source_field": ev.source_field, "span_start": ev.span_start, "span_end": ev.span_end}
        if ev.quote:
            d["quote_display"] = (mask_text(ev.quote, False) or "")[:MAX_QUOTE_DISPLAY]
        out.append(d)
    return out


def _merge_evidence(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {(e.get("type"), e.get("id")) for e in old}
    return old + [e for e in new if (e.get("type"), e.get("id")) not in seen]


def _raw_ids(evidence: list[dict[str, Any]], extra: uuid.UUID | None) -> list[str]:
    ids = {str(e["raw_event_id"]) for e in evidence if e.get("raw_event_id")}
    if extra:
        ids.add(str(extra))
    return sorted(ids)


TXN_ORDER = ("none", "pending", "just_done", "done_cannot_stop")


def _union_people(a: list[levels.PersonRel], b: list[levels.PersonRel]) -> list[levels.PersonRel]:
    out: dict[str, levels.PersonRel] = {}
    for p in [*a, *b]:
        if p.ref in out:
            q = out[p.ref]
            out[p.ref] = levels.PersonRel(ref=p.ref, **{k: getattr(q, k) or getattr(p, k) for k in levels.PersonRel.model_fields if k != "ref"})
        else:
            out[p.ref] = p
    return list(out.values())


def merge_level_input(old: levels.SignalIn, new: levels.SignalIn) -> levels.SignalIn:
    """Gộp tin mới vào vụ đang mở: HỢP với bản cũ, không thay thế — tin yếu hơn tới sau (cùng khoá / cùng tin gốc, OCR tới muộn) không
    bao giờ hạ được 🔴 lừa đảo/an toàn (fail-high). Tập (`hard_rule_hit`, victims, impersonated, payee_refs) hợp; cờ OR; `txn_state`
    mạnh nhất; độ chắc lấy max; (d2) "đồng ý" ở bất kỳ tin nào thắng; mốc thời gian mới nhất. Dữ kiện ĐÓNG VIỆC (status, closure,
    remediation, hạn, promise/opportunity/work) theo tin mới — đó là đường hạ hợp lệ (A16, đã kiểm người gửi ở bên gọi); `privacy` dính."""
    f_old, f_new = old.flags, new.flags
    flags = levels.Flags.model_validate({k: (max(getattr(f_old, k) or 0, getattr(f_new, k) or 0) or None) if k == "repeat_n"
                                         else bool(getattr(f_old, k) or getattr(f_new, k)) for k in levels.Flags.model_fields})
    t_old, t_new = TXN_ORDER.index(old.txn_state), TXN_ORDER.index(new.txn_state)
    txn_state, txn_at = (new.txn_state, new.txn_at or old.txn_at) if t_new >= t_old else (old.txn_state, old.txn_at or new.txn_at)
    agree = old.d2.verdict == "agree" or new.d2.verdict == "agree"
    d2 = levels.D2(verdict="agree", error=False, flags_confirmed=sorted(set(old.d2.flags_confirmed) | set(new.d2.flags_confirmed)),
                   harm_what=new.d2.harm_what or old.d2.harm_what) if agree else (new.d2 if (new.d2.verdict or new.d2.error) else old.d2)
    money_new = (new.money_vnd or 0) >= (old.money_vnd or 0)

    def latest(a: datetime | None, b: datetime | None) -> datetime | None:
        return max((x for x in (a, b) if x is not None), default=None, key=_aware)

    return new.model_copy(update={
        "privacy": "console_pin" if "console_pin" in (old.privacy, new.privacy) else "chat_ok",
        "gate_candidate": new.gate_candidate or old.gate_candidate,
        "msg_at": latest(old.msg_at, new.msg_at), "event_at": latest(old.event_at, new.event_at),
        "deadline_at": new.deadline_at or old.deadline_at,
        "ongoing": old.ongoing or new.ongoing, "flags": flags,
        "hard_rule_hit": list(dict.fromkeys([*old.hard_rule_hit, *new.hard_rule_hit])), "d2": d2,
        "harm_what": new.harm_what.strip() or old.harm_what,
        "victims": _union_people(old.victims, new.victims), "impersonated": _union_people(old.impersonated, new.impersonated),
        "delivery_form": "direct" if "direct" in (old.delivery_form, new.delivery_form) else new.delivery_form,
        "customer_of_owner": old.customer_of_owner or new.customer_of_owner,
        "case_history": new.case_history or old.case_history, "case_anchor_at": new.case_anchor_at or old.case_anchor_at,
        "txn_state": txn_state, "txn_at": txn_at,
        "owner_resolved": old.owner_resolved or new.owner_resolved,
        "closure": new.closure if new.closure != "none" else old.closure,
        "remediation": new.remediation if new.remediation != "none" else old.remediation,
        "impact_base": max(old.impact_base, new.impact_base),
        "money_vnd": new.money_vnd if money_new else old.money_vnd, "money_basis": new.money_basis if money_new else old.money_basis,
        "irreversible": old.irreversible or new.irreversible,
        "non_forwarded_sources_n": max(old.non_forwarded_sources_n, new.non_forwarded_sources_n),
        "source_class": "rule" if "rule" in (old.source_class, new.source_class) else new.source_class,
        "confidence_exist": max(old.confidence_exist, new.confidence_exist),
        "confidence_content": max(old.confidence_content, new.confidence_content),
        "payee_refs": list(dict.fromkeys([*old.payee_refs, *new.payee_refs])),
        "owner_unanswered_h": max(old.owner_unanswered_h, new.owner_unanswered_h),
        "subject_entity_id": new.subject_entity_id or old.subject_entity_id,
        "goal_priority1_match_valid": old.goal_priority1_match_valid or new.goal_priority1_match_valid,
        "goods_risk": old.goods_risk or new.goods_risk,
        "mute": None, "case_prior": None})


# ─── upsert ───────────────────────────────────────────────────────────────────

def _weak_key(ck: str | None) -> bool:
    """Khoá dự phòng của `levels.case_key` (không đủ dữ kiện ⇒ theo tin gốc `raw|` hoặc băm `anon|`)."""
    if not ck or ":" not in ck:
        return False
    return ck.split(":", 1)[1].startswith(("raw|", "anon|"))


def _merge_row_input(row: Any, li: levels.SignalIn) -> levels.SignalIn:
    """Hợp đầu vào mức của dòng đã có (fail-high) và giữ riêng tư DÍNH."""
    if row is not None and row.level_input is not None:
        li = merge_level_input(levels.SignalIn.model_validate(row.level_input), li)   # HỢP, không thay thế (fail-high)
    if row is not None and row.privacy == "console_pin":
        li = li.model_copy(update={"privacy": "console_pin"})   # riêng tư DÍNH: chặt nhất thắng, gộp không bao giờ hạ (§8.1)
    return li


async def _open_weak_rows(db: AsyncSession, org_id: uuid.UUID, kind: str, keep_id: uuid.UUID, raw_ids: list[str]) -> list[Any]:
    """Dòng đang mở mang khoá DỰ PHÒNG (raw|/anon|) có tin gốc trùng `raw_ids`, trừ dòng chính."""
    if not raw_ids:
        return []
    rows = (await db.execute(text("""
        SELECT id, case_key, evidence, fact_ids, raw_event_ids, privacy, level_input, case_state FROM biz.alerts
        WHERE org_id = :o AND kind = :k AND status IN ('open', 'acknowledged') AND id <> :i
          AND raw_event_ids && CAST(:raw AS uuid[]) ORDER BY created_at, id FOR UPDATE"""),
        {"o": org_id, "k": kind, "i": keep_id, "raw": raw_ids})).all()
    return [r for r in rows if _weak_key(r.case_key)]


async def _absorb_weak(db: AsyncSession, w: Any, into: uuid.UUID, now: datetime) -> None:
    """Đóng dòng raw|/anon| đã gộp vào dòng mạnh; thẻ đã đẩy (`case_state`) chuyển sang dòng mạnh để KHÔNG leo thang 🔴 lần hai."""
    if w.case_state:
        await db.execute(text("""UPDATE biz.alerts SET case_state = CAST(:w AS jsonb) || COALESCE(case_state, '{}'::jsonb)
                                 WHERE id = :i"""), {"w": orjson.dumps(w.case_state).decode(), "i": into})
    await db.execute(text("""UPDATE biz.alerts SET status = 'dismissed', delivery = 'none', next_eval_at = NULL, resolved_at = :now,
                                    updated_at = now() WHERE id = :i"""), {"now": now, "i": w.id})


async def _update_row(db: AsyncSession, row_id: uuid.UUID, common: dict[str, Any], merged: list[dict[str, Any]], li: levels.SignalIn,
                      inp: UpsertIn, raw_ids: list[str], new_ck: str | None) -> None:
    await db.execute(text("""
        UPDATE biz.alerts SET case_key = COALESCE(:nck, case_key),
               evidence = CAST(:ev AS jsonb), evidence_n = :n, summary = COALESCE(:su, summary),
               suggested_action = COALESCE(:sa, suggested_action), level_input = CAST(:li AS jsonb),
               fact_ids = ARRAY(SELECT DISTINCT unnest(fact_ids || CAST(:fi AS uuid[]))),
               raw_event_ids = ARRAY(SELECT DISTINCT unnest(raw_event_ids || CAST(:raw AS uuid[]))),
               owner_fact_ids = CAST(:of AS uuid[]), facts = facts || CAST(:fx AS jsonb), due_at = COALESCE(:due, due_at),
               privacy = CASE WHEN privacy = 'console_pin' OR :pv = 'console_pin' THEN 'console_pin' ELSE 'chat_ok' END,
               personnel_related = personnel_related OR :pv = 'console_pin',
               expires_at = GREATEST(expires_at, :ex), detector_version = COALESCE(:dv, detector_version), updated_at = now()
        WHERE id = :i"""), {**common, "ev": orjson.dumps(merged).decode(), "n": len(merged), "fi": inp.fact_ids, "raw": raw_ids,
                             "pv": li.privacy, "i": row_id, "nck": new_ck})


CASE3_KINDS = ("xung_dot_khieu_nai", "quan_he_nong_nguoi")


async def effective_case_key(db: AsyncSession, org_id: uuid.UUID, kind: str, case_key: str, at: datetime) -> str:
    """Khoá vụ hiệu lực theo cửa sổ TRƯỢT (T12, gỡ Nợ #83): khoá không gắn ngày/bucket; vụ đang mở cùng khoá gốc (kể cả `|#n`) có tin mới
    nhất cách `at` < `levels.case_window` ⇒ dùng khoá của vụ đó; không có vụ nào trong cửa sổ ⇒ khoá gốc nếu chưa vụ mở nào giữ nó, ngược lại
    hậu tố tuần tự kế tiếp (vụ cũ quá cửa sổ VẪN mở — Sếp chưa xử lý — chỉ không nhận tin mới). Bên gọi giữ khoá trần (`cap_lock`)."""
    base = levels.base_case_key(case_key)
    win = levels.case_window(base)
    if win is None:
        return base
    # Khoá vụ cổng 3 (`case3:`) dùng CHUNG cho xung_dot_khieu_nai và quan_he_nong_nguoi của cùng lời doạ bỏ ⇒ tính trên cả hai loại, để hai
    # bản cùng ra một khoá (`close_case` đóng đúng cả hai), không bản nào rơi vào vụ cũ đã quá cửa sổ.
    kinds = list(CASE3_KINDS) if base.startswith("case3:") else [kind]
    rows = (await db.execute(text("""
        SELECT case_key, COALESCE(level_input ->> 'msg_at', '') AS seen, created_at FROM biz.alerts
        WHERE org_id = :o AND kind = ANY(:k) AND status IN ('open', 'acknowledged')
          AND (case_key = :b OR left(case_key, length(:b) + 2) = :b || '|#')"""), {"o": org_id, "k": kinds, "b": base})).all()
    if not rows:
        return base
    at = _aware(at)

    def seen(r: Any) -> datetime:
        try:
            return _aware(datetime.fromisoformat(r.seen)) if r.seen else _aware(r.created_at)
        except ValueError:
            return _aware(r.created_at)

    live = [r for r in rows if abs(at - seen(r)) < win]
    if live:
        return str(max(live, key=seen).case_key)
    taken = {str(r.case_key) for r in rows}
    if base not in taken:
        return base
    n = 2
    while f"{base}|#{n}" in taken:
        n += 1
    return f"{base}|#{n}"


async def upsert(db: AsyncSession, org_id: uuid.UUID, inp: UpsertIn, now: datetime) -> Signal:
    if inp.kind not in INTEL_KINDS + NON_INTEL_KINDS:
        raise SignalError(f"loại tín hiệu không hợp lệ: {inp.kind}")
    if inp.level_input.kind not in (inp.kind, None):
        raise SignalError("kind của level_input khác kind của tín hiệu")
    ev = await _check_evidence(db, org_id, inp.evidence)
    if inp.kind in INTEL_KINDS and not ev:
        raise UnverifiedQuote("tín hiệu tình báo cần ≥ 1 bằng chứng đã kiểm")
    raw_ids = _raw_ids(ev, None)
    prof = await profile_read.snapshot(db, org_id)
    owner_fact_ids = [f for f in dict.fromkeys(inp.owner_fact_ids) if prof.has(f)]      # sai org / chưa xác nhận ⇒ bỏ (§4C)
    li = inp.level_input.model_copy(update={"kind": inp.kind})
    await cap_lock(db, org_id, now)                        # khoá trần TRƯỚC khoá dòng / next_code (F1)
    ck = await effective_case_key(db, org_id, inp.kind, inp.case_key, li.msg_at)      # cửa sổ trượt (không bucket cố định)
    inp = inp.model_copy(update={"case_key": ck})
    row = (await db.execute(text("""
        SELECT id, case_key, evidence, fact_ids, raw_event_ids, privacy, level_input, case_state FROM biz.alerts
        WHERE org_id = :o AND kind = :k AND status IN ('open', 'acknowledged')
          AND (case_key = :ck OR (cardinality(CAST(:raw AS uuid[])) > 0 AND raw_event_ids && CAST(:raw AS uuid[])))
        ORDER BY (case_key = :ck) DESC, COALESCE(case_state ->> 'red_pushed', '') = 'true' DESC, created_at LIMIT 1 FOR UPDATE"""),
        {"o": org_id, "k": inp.kind, "ck": inp.case_key, "raw": raw_ids})).one_or_none()
    # Nhiều dòng khớp theo tin gốc ⇒ ưu tiên dòng ĐÃ ĐẨY 🔴 (sau khớp khoá): giữ thẻ đã đẩy ở dòng chính, dòng kia gộp vào (G-KB Đợt 2 P1).
    absorbed: list[Any] = []                               # dòng raw|/anon| cùng tin gốc bị gộp vào dòng chính (một vụ một 🔴)
    rekey = row is not None and row.case_key != inp.case_key and _weak_key(row.case_key) and not _weak_key(inp.case_key)
    if row is not None and (not _weak_key(row.case_key) or rekey):
        # Kể cả khi dòng chính đang mang khoá yếu và sắp được NÂNG lên khoá mạnh: dòng yếu khác cùng tin gốc (vd raw|actor: của luật
        # fraud đã đẩy) phải gộp theo, nếu không vụ nâng khoá không thấy thẻ đã đẩy ⇒ 🔴 lần hai.
        absorbed = await _open_weak_rows(db, org_id, inp.kind, row.id, raw_ids)
    for w in absorbed:
        ev = _merge_evidence(list(w.evidence or []), ev)
        raw_ids = list(dict.fromkeys([*raw_ids, *(str(x) for x in w.raw_event_ids)]))
        li = _merge_row_input(w, li)
    li = _merge_row_input(row, li)
    level_input = li.model_dump(mode="json")
    expires = inp.expires_at or (now + timedelta(days=DEFAULT_TTL.get(inp.kind, 7)))
    common = {"su": inp.summary, "sa": inp.suggested_action, "li": orjson.dumps(level_input).decode(),
              "of": owner_fact_ids, "fx": orjson.dumps({**inp.facts, "action_executable": inp.action_executable}).decode(),
              "due": inp.due_at, "ex": expires, "dv": inp.detector_version}
    created = False
    if row is not None:
        merged = _merge_evidence(list(row.evidence or []), ev)
        # Vụ đang mang khoá DỰ PHÒNG (raw|/anon| — đường nhanh) mà tin mới có khoá mạnh (STK, mạo danh…) ⇒ nâng khoá: tin sau cùng vụ
        # tìm thấy vụ theo khoá mạnh, không đẻ tín hiệu thứ hai/🔴 thứ hai (F2). Đã khoá trần ⇒ không ai chen INSERT cùng khoá mạnh.
        new_ck = inp.case_key if rekey else None
        sid = row.id
        try:
            async with db.begin_nested():                  # savepoint: va chạm khoá mạnh không làm hỏng cả transaction
                await _update_row(db, row.id, common, merged, li, inp, raw_ids, new_ck)
        except IntegrityError:
            # Lượt khác (ngày-trần khác ⇒ khoá trần khác) vừa nâng một dòng khác lên đúng khoá mạnh này ⇒ gộp vào dòng mạnh đó, dòng
            # raw| của ta bị gộp theo (không đẻ vụ thứ hai).
            strong = (await db.execute(text("""
                SELECT id, case_key, evidence, fact_ids, raw_event_ids, privacy, level_input, case_state FROM biz.alerts
                WHERE org_id = :o AND kind = :k AND case_key = :ck AND status IN ('open', 'acknowledged') FOR UPDATE"""),
                {"o": org_id, "k": inp.kind, "ck": inp.case_key})).one_or_none()
            if strong is None:
                raise
            absorbed.append(row)
            li = _merge_row_input(strong, li)
            merged = _merge_evidence(list(strong.evidence or []), merged)
            raw_ids = list(dict.fromkeys([*raw_ids, *(str(x) for x in strong.raw_event_ids)]))
            sid = strong.id
            common = {**common, "li": orjson.dumps(li.model_dump(mode="json")).decode()}
            await _update_row(db, strong.id, common, merged, li, inp, raw_ids, None)
        for w in absorbed:
            if w.id != sid:
                await _absorb_weak(db, w, sid, now)
    else:
        code = (await db.execute(text("SELECT core.next_code('ALR')"))).scalar_one()
        owner = (await db.execute(text("""
            SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id JOIN core.roles r ON r.id = ur.role_id AND r.code = 'owner'
            WHERE u.org_id = :o AND u.is_active ORDER BY u.created_at LIMIT 1"""), {"o": org_id})).scalar_one_or_none()
        sid = (await db.execute(text("""
            INSERT INTO biz.alerts (org_id, code, alert_type, priority, recipient_user_id, subject_type, subject_id, title, summary,
                                    suggested_action, evidence, evidence_n, personnel_related, kind, privacy, case_key, dedupe_key,
                                    raw_event_id, raw_event_ids, thread_key, group_id, detector, detector_version, fact_ids,
                                    owner_fact_ids, facts, level_input, due_at, expires_at)
            VALUES (:o, :c, :at, 'P3', :u, :st, :si, :ti, :su, :sa, CAST(:ev AS jsonb), :n, :pr, :k, :pv, :ck, :ck, :re,
                    CAST(:raw AS uuid[]), :tk, :g, :d, :dv, CAST(:fi AS uuid[]), CAST(:of AS uuid[]), CAST(:fx AS jsonb),
                    CAST(:li AS jsonb), :due, :ex)
            RETURNING id"""),
            {**common, "o": org_id, "c": code, "at": inp.alert_type, "u": owner, "st": inp.subject_type, "si": inp.subject_id,
             "ti": inp.title, "ev": orjson.dumps(ev).decode(), "n": len(ev), "pr": li.privacy == "console_pin", "k": inp.kind,
             "pv": li.privacy, "ck": inp.case_key, "re": raw_ids[0] if raw_ids else None, "raw": raw_ids, "tk": inp.thread_key,
             "g": inp.group_id, "d": inp.detector, "fi": inp.fact_ids})).scalar_one()
        created = True
    if inp.person_ids:
        await db.execute(text("UPDATE biz.alerts SET facts = facts || jsonb_build_object('person_ids', CAST(:p AS jsonb)) WHERE id = :i"),
                         {"p": orjson.dumps([str(p) for p in inp.person_ids]).decode(), "i": sid})
    sig = await relane(db, org_id, sid, now)
    return Signal(**{**sig.__dict__, "created": created})


# ─── relane: một nơi tính mức + ngăn + phân phối ─────────────────────────────

def _lane(status: str, level: str, has_task: bool, has_open_ask: bool, prev_lane: str) -> str:
    """Luật ngăn r4 §4.3: kết thúc ⇒ giữ ngăn cuối; giao việc ⇒ delegated; acknowledged ⇒ handling; có ask ⇒ decide;
    open ≥ 🟠 ⇒ decide; còn lại ⇒ fyi."""
    if status in TERMINAL:
        return prev_lane
    if has_task:
        return "delegated"
    if status == "acknowledged":
        return "handling"
    if has_open_ask or level in ("red", "amber"):
        return "decide"
    return "fyi"


def _system_summary(kind: str, level: str, gate: str | None) -> str:
    lab = {"red": "Báo ngay", "amber": "Cần Sếp xem", "grey": "Để biết"}.get(level, "Để biết")
    return f"{lab}: {kind.replace('_', ' ')}" + (f" ({gate.replace('_', ' ')})" if gate else "")


def _merge_case_prior(states: list[dict[str, Any]]) -> levels.CasePrior | None:
    """Hợp `case_state` của mọi tín hiệu còn mở cùng `case_key` (khác loại, vd khiếu nại + quan hệ nóng của cùng lời doạ bỏ — F6)."""
    states = [x for x in states if isinstance(x, dict) and x]
    if not states:
        return None
    pushed = [x for x in states if x.get("red_pushed")]
    return levels.CasePrior(
        red_pushed=bool(pushed), gate=next((x.get("gate") for x in pushed if x.get("gate")), None),
        branch_rank=max(int(x.get("branch_rank") or 0) for x in states),
        victim_refs=sorted({str(v) for x in states for v in x.get("victim_refs") or []}),
        payee_refs=sorted({str(v) for x in states for v in x.get("payee_refs") or []}),
        emergency=any(bool(x.get("emergency")) for x in states), said_will_leave=any(bool(x.get("said_will_leave")) for x in states),
        txn_reminded=any(bool(x.get("txn_reminded")) for x in states))


LEVEL_RANK = {"off": 0, "grey": 1, "amber": 2, "red": 3}
TOP_DELIVERY = ("digest_top", "digest_morning")


async def relane(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID, now: datetime) -> Signal:
    sig = await _relane(db, org_id, signal_id, now, skip_locked=False)
    if sig is None:                                        # không xảy ra khi skip_locked=False
        raise SignalError("không tìm thấy tín hiệu")
    return sig


async def _relane(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID, now: datetime, *, skip_locked: bool) -> Signal | None:
    """`skip_locked` (job đồng hồ): dòng đang bị lượt khác khoá ⇒ bỏ qua (trả None), lượt đó tự relane khi xong."""
    day = await cap_lock(db, org_id, now, wait=not skip_locked)   # khoá trần TRƯỚC khoá dòng (F1); job đồng hồ không chờ
    if day is None:
        return None                                        # trần đang bị lượt khác giữ ⇒ để lượt sau
    r = (await db.execute(text(f"""
        SELECT id, kind, status, lane, level, gate, delivery, case_key, thread_key, group_id, detector, level_input, case_state, pushed_at,
               raw_event_ids,
               would_push_at, capped_at, cap_day, suggested_action, facts, expires_at, ask_id
        FROM biz.alerts WHERE id = :i AND org_id = :o FOR UPDATE{" SKIP LOCKED" if skip_locked else ""}"""),  # noqa: S608
        {"i": signal_id, "o": org_id})).one_or_none()
    if r is None:
        if skip_locked:
            return None
        raise SignalError("không tìm thấy tín hiệu")
    if r.status in TERMINAL or r.level_input is None or r.kind not in INTEL_KINDS:
        return await _finish_lane(db, org_id, r, r.level, r.status)
    s = levels.SignalIn.model_validate(r.level_input)
    facts = r.facts if isinstance(r.facts, dict) else {}
    mute = await mutes.active(db, org_id, kind=r.kind, group_id=r.group_id, person_ids=facts.get("person_ids") or [], detector=r.detector,
                              now=now)
    upd: dict[str, Any] = {}
    if mute and (s.mute is None or mute == "pin"):
        upd["mute"] = mute
    states = [r.case_state] if r.case_state else []
    # Hợp thẻ vụ của dòng còn mở cùng khoá (khác loại — F6) và — phòng thủ (G-KB Đợt 2 P1) — dòng còn mở CÙNG LOẠI chung tin gốc: hai khoá
    # khác nhau cho cùng một sự việc (vd stk| và imp|) không được đẩy 🔴 hai lần.
    states += list((await db.execute(text("""
        SELECT case_state FROM biz.alerts WHERE org_id = :o AND id <> :i AND status IN ('open', 'acknowledged') AND case_state IS NOT NULL
          AND (case_key = :ck OR (kind = :k AND cardinality(CAST(:raw AS uuid[])) > 0 AND raw_event_ids && CAST(:raw AS uuid[])))"""),
        {"o": org_id, "ck": r.case_key, "i": r.id, "k": r.kind, "raw": [str(x) for x in r.raw_event_ids or []]})).scalars().all())
    prior = _merge_case_prior(states)
    if prior is not None:
        upd["case_prior"] = prior
    s = s.model_copy(update=upd)
    st = await delivery_state(db, org_id, now, exclude=r.id)
    pol = await contexts.policy(db, org_id, r.thread_key or "")
    prof = await profile_read.snapshot(db, org_id)
    lv = levels.compute(s, prof, pol, st, now)
    imp, urg = levels.axes(s, prof, st, now)
    axis = s.confidence_exist if (r.kind in ("rui_ro_lua_dao", "suc_khoe_an_toan") or lv.gate in levels.BYPASS_GATES
                                  or lv.candidate in levels.BYPASS_GATES) else s.confidence_content
    status = r.status
    if lv.delivery == "none" and "closed" in lv.reasons:
        status = "resolved"                                # "Đã xử lý" / tin hoàn thành đã kiểm (A16) ⇒ đóng
    elif s.owner_resolved:
        status = "resolved"                                # Sếp bấm "Đã xử lý" thắng mute (F3)
    elif "muted" in lv.reasons:
        # mute chỉ là không báo, không giữ vụ mở mãi: tin hoàn thành đã kiểm vẫn đóng vụ (cùng luật bước 5, không qua mute)
        lv0 = levels.compute(s.model_copy(update={"mute": None}), prof, pol, st, now)
        if lv0.delivery == "none" and "closed" in lv0.reasons:
            status = "resolved"
    esc = "escalation" in lv.reasons or "txn_day_reminder" in lv.reasons    # nhắc lại sáng ngày hẹn chuyển = một lần đẩy mới (D1)
    pushed_at = now if lv.delivery == "push_now" and (r.pushed_at is None or esc) else r.pushed_at
    would_push_at = r.would_push_at or (now if lv.would_push else None)
    capped_at = r.capped_at or (now if lv.capped else None)
    new_cap_day = r.cap_day
    if (lv.level == "red" and lv.gate in levels.REGULAR_GATES and lv.delivery in ("push_now", "digest_morning")
            and (r.cap_day is None or esc)):
        new_cap_day = day
    case_state = dict(r.case_state or {})
    if lv.level == "red":
        f = s.flags
        refs = sorted(set(case_state.get("victim_refs") or []) | {p.ref for p in s.victims})
        payees = sorted(set(case_state.get("payee_refs") or []) | set(s.payee_refs))
        case_state |= {"payee_refs": payees}
        if lv.delivery in ("push_now", "digest_morning", "digest_top"):
            case_state |= {"red_pushed": True, "gate": lv.gate, "victim_refs": refs,
                           "branch_rank": max(int(case_state.get("branch_rank") or 0), lv.branch_rank),
                           "emergency": bool(case_state.get("emergency")) or f.emergency or "emergency_115_chay_cap_cuu" in levels.hard_rules(s),
                           "said_will_leave": bool(case_state.get("said_will_leave")) or f.said_will_leave}
        rem = levels.txn_remind_at(s)
        if (lv.gate == levels.G1 and lv.delivery == "push_now" and rem is not None
                and _aware(now).astimezone(levels.VN).date() >= rem.astimezone(levels.VN).date()):
            # Lần đẩy này đã rơi vào (hoặc chính là) sáng ngày hẹn chuyển ⇒ không nhắc thêm lần nữa trong vụ (D1).
            case_state["txn_reminded"] = True
    next_eval = levels.next_change_at(s, now) if status in OPEN else None
    # Mục đã lên bản tin (digest_at) mà nay leo lên nhóm đầu bản tin hoặc tăng mức ⇒ lên lại bản tin kế (F4)
    redigest = ((lv.delivery in TOP_DELIVERY and r.delivery not in TOP_DELIVERY)
                or LEVEL_RANK.get(lv.level, 0) > LEVEL_RANK.get(r.level or "off", 0))
    await db.execute(text("""
        UPDATE biz.alerts SET digest_at = CASE WHEN :rdg THEN NULL ELSE digest_at END, level = :lv, gate = :g, candidate = :c, delivery = :d, demoted = :dm, reasons = CAST(:rs AS text[]),
               capped_at = :ca, would_push_at = :wp, pushed_at = :pa, cap_day = :cd, case_state = CAST(:cs AS jsonb), impact = :im,
               urgency = :ur, confidence_exist = :ce, confidence_content = :cc, confidence_level = :cl, priority = :pr,
               level_at = :now, next_eval_at = :ne, status = :stt, resolved_at = CASE WHEN :stt = 'resolved' THEN :now ELSE resolved_at END,
               privacy = CASE WHEN privacy = 'console_pin' OR :pv = 'console_pin' THEN 'console_pin' ELSE 'chat_ok' END,
               updated_at = now()
        WHERE id = :i"""),
        {"lv": lv.level, "g": lv.gate, "c": lv.candidate, "d": lv.delivery, "dm": lv.demoted, "rs": lv.reasons, "ca": capped_at,
         "wp": would_push_at, "pa": pushed_at, "cd": new_cap_day, "cs": orjson.dumps(case_state).decode() if case_state else None,
         "im": imp, "ur": urg, "ce": round(min(1.0, max(0.0, s.confidence_exist)), 3),
         "cc": round(min(1.0, max(0.0, s.confidence_content)), 3), "cl": levels.conf_level(axis), "pr": PRIORITY_OF[lv.level],
         "now": now, "ne": next_eval, "stt": status, "pv": s.privacy, "i": r.id, "rdg": redigest})
    if (lv.level, status) != (r.level, r.status):
        await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:intel", action=f"intel.signal.{status if status != r.status else 'level'}",
                               target_type="signal", target_id=str(r.id), result="ok",
                               detail={"level": lv.level, "gate": lv.gate, "candidate": lv.candidate, "delivery": lv.delivery,
                                       "reasons": lv.reasons})
    return await _finish_lane(db, org_id, r, lv.level, status)


async def _finish_lane(db: AsyncSession, org_id: uuid.UUID, r: Any, level: str, status: str) -> Signal:
    facts = r.facts if isinstance(r.facts, dict) else {}
    ask_id = r.ask_id
    if status in TERMINAL:
        await asks.close_for(db, org_id, "signal", r.id)   # tín hiệu đóng ⇒ ask hết hạn cùng transaction
    elif level in ("red", "amber") and r.suggested_action and facts.get("action_executable") and r.kind in INTEL_KINDS:
        a = await asks.create(db, org_id, source="signal", ref_id=r.id, signal_id=r.id, decision_kind="internal",
                              summary=_system_summary(r.kind, level, None), expires_at=r.expires_at)
        ask_id = a.id
    has_task = (await db.execute(text("SELECT 1 FROM biz.signal_links WHERE signal_id = :i AND ref_type = 'task' LIMIT 1"),
                                 {"i": r.id})).first() is not None
    has_ask = ask_id is not None and (await db.execute(text("SELECT 1 FROM ops.asks WHERE id = :a AND state = 'open'"),
                                                       {"a": ask_id})).first() is not None
    lane = _lane(status, level, has_task, has_ask, r.lane)
    out = (await db.execute(text(f"UPDATE biz.alerts SET lane = :l, ask_id = :a WHERE id = :i RETURNING {_COLS}"),  # noqa: S608
                            {"l": lane, "a": ask_id, "i": r.id})).one()
    return _sig(out)


# ─── vòng đời ─────────────────────────────────────────────────────────────────

ALLOWED = {"open": ("acknowledged", "resolved", "broken", "expired", "dismissed"),
           "acknowledged": ("resolved", "broken", "expired", "dismissed")}


async def transition(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID, to: str, *, now: datetime, actor_type: str = "user",
                     actor_id: str = "system:intel", by_owner: bool = False) -> Signal:
    """Đổi trạng thái (r4 §4.3). Sếp bấm "Đã xử lý" (`resolved`, `by_owner`) ⇒ `owner_resolved` vào đầu vào mức ⇒ ⚪ `none` (mọi loại,
    kể cả lừa đảo/an toàn — một trong hai đường xuống ⚪ của nguyên lý fail-high)."""
    await cap_lock(db, org_id, now)                        # khoá trần TRƯỚC khoá dòng (F1)
    r = (await db.execute(text("SELECT id, status, level_input FROM biz.alerts WHERE id = :i AND org_id = :o FOR UPDATE"),
                          {"i": signal_id, "o": org_id})).one_or_none()
    if r is None:
        raise SignalError("không tìm thấy tín hiệu")
    if to not in ALLOWED.get(r.status, ()):
        raise SignalError(f"không chuyển được từ {r.status} sang {to}")
    if to == "resolved" and by_owner and r.level_input is not None:
        li = dict(r.level_input)
        li["owner_resolved"] = True
        await db.execute(text("UPDATE biz.alerts SET level_input = CAST(:li AS jsonb) WHERE id = :i"),
                         {"li": orjson.dumps(li).decode(), "i": r.id})
        sig = await relane(db, org_id, r.id, now)
    else:
        await db.execute(text("""UPDATE biz.alerts SET status = :t, updated_at = now(), next_eval_at = CASE WHEN :t = 'acknowledged'
                                 THEN next_eval_at ELSE NULL END, resolved_at = CASE WHEN :t IN ('resolved', 'broken', 'expired',
                                 'dismissed') THEN :now ELSE resolved_at END WHERE id = :i"""), {"t": to, "i": r.id, "now": now})
        sig = await relane(db, org_id, r.id, now)
    await actionlog.record(db, org_id=org_id, actor_type=actor_type, actor_id=actor_id, action=f"intel.signal.{to}",
                           target_type="signal", target_id=str(r.id), result="ok")
    return sig


async def resolve_by_owner(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID, *, now: datetime, actor_id: str) -> Signal:
    return await transition(db, org_id, signal_id, "resolved", now=now, actor_id=actor_id, by_owner=True)


async def feedback(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID, *, verdict: str, verdict_by: str, now: datetime,
                   user_id: uuid.UUID | None = None, note: str | None = None) -> Signal:
    """Đúng/Sai (chỉ-thêm ở `biz.signal_feedback`). "Sai" ⇒ `dismissed`, TRỪ khi máy bác (`system`) một tín hiệu Sếp đã bấm "đúng"."""
    if verdict not in ("correct", "wrong", "unimportant", "missed"):
        raise SignalError("verdict không hợp lệ")
    if verdict_by not in ("owner", "system"):
        raise SignalError("verdict_by không hợp lệ")
    await cap_lock(db, org_id, now)                        # khoá trần TRƯỚC khoá dòng (F1)
    r = (await db.execute(text("""SELECT id, status, gate, candidate, would_push_at, feedback_verdict, verdict_by FROM biz.alerts
                                  WHERE id = :i AND org_id = :o FOR UPDATE"""), {"i": signal_id, "o": org_id})).one_or_none()
    if r is None:
        raise SignalError("không tìm thấy tín hiệu")
    owner_said_correct = r.feedback_verdict == "correct" and r.verdict_by == "owner"
    await db.execute(text("""INSERT INTO biz.signal_feedback (org_id, signal_id, verdict, verdict_by, user_id, gate, shadow, note, at)
                             VALUES (:o, :s, :v, :b, :u, :g, :sh, :n, :at)"""),
                     {"o": org_id, "s": r.id, "v": verdict, "b": verdict_by, "u": user_id, "g": r.gate or r.candidate,
                      "sh": r.would_push_at is not None, "n": (note or None) and note[:500], "at": now})
    if verdict_by == "system" and owner_said_correct:
        return await relane(db, org_id, r.id, now)        # Sếp "đúng" thắng "máy bác" (CNS-060)
    await db.execute(text("UPDATE biz.alerts SET feedback_verdict = :v, verdict_by = :b, updated_at = now() WHERE id = :i"),
                     {"v": verdict, "b": verdict_by, "i": r.id})
    if verdict == "wrong" and r.status in OPEN:
        return await transition(db, org_id, r.id, "dismissed", now=now, actor_type="user" if verdict_by == "owner" else "system",
                                actor_id=str(user_id) if user_id else "system:intel")
    return await relane(db, org_id, r.id, now)


async def set_outcome(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID, *, outcome: str, result: str, by: str,
                      value_vnd: int | None = None, note: str | None = None) -> bool:
    if outcome not in ("acted", "ignored") or result not in ("saved", "lost", "neutral", "unknown") or by not in ("auto", "owner", "staff"):
        raise SignalError("kết quả không hợp lệ")
    r = await db.execute(text("""UPDATE biz.alerts SET outcome = :oc, outcome_result = :rs, outcome_by = :by, outcome_value_vnd = :v,
                                 outcome_note = :n, outcome_at = now(), updated_at = now() WHERE id = :i AND org_id = :o"""),
                         {"oc": outcome, "rs": result, "by": by, "v": value_vnd, "n": note, "i": signal_id, "o": org_id})
    return bool(r.rowcount)  # type: ignore[attr-defined]


def wilson_low(k: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = k / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    rad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - rad) / den


async def precision(db: AsyncSession, org_id: uuid.UUID, gate: str, *, days: int = 30, shadow_only: bool = False) -> dict[str, Any]:
    """Precision theo cổng trên Đúng/Sai của Sếp (bản mới nhất mỗi tín hiệu). M1: n ≥ 10, điểm ≥ 80%, cận dưới Wilson ≥ 60%."""
    row = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE verdict = 'correct') AS k, count(*) AS n FROM (
          SELECT DISTINCT ON (signal_id) verdict FROM biz.signal_feedback
          WHERE org_id = :o AND gate = :g AND verdict_by = 'owner' AND verdict IN ('correct', 'wrong')
            AND at >= now() - make_interval(days => :d) AND (NOT :sh OR shadow)
          ORDER BY signal_id, at DESC) t"""), {"o": org_id, "g": gate, "d": days, "sh": shadow_only})).one()
    k, n = int(row.k), int(row.n)
    lo = wilson_low(k, n)
    return {"gate": gate, "n": n, "correct": k, "precision": (k / n) if n else None, "wilson_low": lo,
            "m1_passed": n >= 10 and k / n >= 0.8 and lo >= 0.6}


# ─── đồng hồ ──────────────────────────────────────────────────────────────────

async def reevaluate_due(db: AsyncSession, now: datetime, *, org_id: uuid.UUID | None = None, limit: int = 200) -> list[Signal]:
    """Tín hiệu mở tới mốc `next_eval_at` ⇒ `relane` lại theo đồng hồ (§4.2.1 (a): tuổi/hạn đo bằng code). Trả các tín hiệu ĐỔI mức hoặc
    phân phối. COMMIT THEO TỪNG DÒNG (nhả khoá trần + khoá dòng ngay, không giữ khoá của tổ chức này khi sang tổ chức khác — F1); dòng đang
    bị lượt khác khoá ⇒ bỏ qua (`SKIP LOCKED`); một dòng lỗi ⇒ rollback dòng đó, ghi log, dòng/tổ chức khác vẫn chạy (F5). Bên gọi
    KHÔNG được để việc chưa commit trong `db` trước khi gọi."""
    rows = (await db.execute(text("""
        SELECT id, org_id, level, delivery FROM biz.alerts
        WHERE next_eval_at IS NOT NULL AND next_eval_at <= :now AND status IN ('open', 'acknowledged')
          AND (CAST(:o AS uuid) IS NULL OR org_id = :o)
        ORDER BY next_eval_at LIMIT :n"""), {"now": now, "o": org_id, "n": limit})).all()
    await db.commit()
    changed: list[Signal] = []
    for r in rows:
        try:
            sig = await _relane(db, r.org_id, r.id, now, skip_locked=True)
            await db.commit()
        except Exception as e:                              # noqa: BLE001 — một dòng lỗi không chặn dòng/tổ chức khác
            await db.rollback()
            log.error("reevaluate_due: bỏ qua tín hiệu %s (org %s): %s", r.id, r.org_id, type(e).__name__)
            # dời mốc (transaction riêng) để dòng hỏng không chiếm đầu hàng `limit` mỗi phút
            await db.execute(text("""UPDATE biz.alerts SET next_eval_at = :t WHERE next_eval_at <= :now
                                AND id IN (SELECT id FROM biz.alerts WHERE id = :i FOR UPDATE SKIP LOCKED)"""),
                             {"t": now + ERROR_BACKOFF, "i": r.id, "now": now})
            await db.commit()
            continue
        if sig is not None and (sig.level, sig.delivery) != (r.level, r.delivery):
            changed.append(sig)
    return changed


# ─── chiếu mem.disputes ───────────────────────────────────────────────────────

async def project_disputes(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> list[Signal]:
    """Tranh chấp đang mở có `dispute_signal_kind` (vd `payment_account` mâu thuẫn ⇒ `rui_ro_lua_dao`) ⇒ một tín hiệu, `needs_owner`
    (chỉ Sếp đóng), bằng chứng = sự thật đã kiểm của tranh chấp. Ghi `projected_signal_id` (chạy lại không tạo trùng)."""
    rows = (await db.execute(text("""
        SELECT d.id, d.subject_id, d.predicate, d.fact_ids, d.dispute_signal_kind, d.opened_at,
               (SELECT f.object_key FROM mem.facts f WHERE f.id = ANY(d.fact_ids) ORDER BY f.recorded_at DESC LIMIT 1) AS key
        FROM mem.disputes d WHERE d.org_id = :o AND d.resolved_at IS NULL AND d.projected_signal_id IS NULL
          AND d.dispute_signal_kind IS NOT NULL AND d.dispute_signal_kind NOT IN ('system', 'legacy')"""), {"o": org_id})).all()
    out: list[Signal] = []
    for d in rows:
        ev_rows = (await db.execute(text("""SELECT DISTINCT fact_id, raw_event_id FROM mem.fact_evidence
                                            WHERE org_id = :o AND fact_id = ANY(:f) AND quote_status = 'verified'"""),
                                    {"o": org_id, "f": list(d.fact_ids)})).all()
        if not ev_rows:
            continue                                       # chưa có bằng chứng đã kiểm ⇒ không thành tín hiệu
        kind = d.dispute_signal_kind
        evidence = [EvidenceIn(type="fact", id=e.fact_id, raw_event_id=e.raw_event_id) for e in ev_rows]
        ck = (levels.case_key(kind, at=d.opened_at, payee_digest=d.key) if kind == "rui_ro_lua_dao" and d.key
              else f"{kind}:dispute|{d.id}")
        li = levels.SignalIn(kind=kind, msg_at=d.opened_at, event_at=d.opened_at, impact_base=2, source_class="rule",
                             confidence_exist=1.0, confidence_content=0.85, harm_what="thông tin mâu thuẫn, cần Sếp xác nhận bản đúng",
                             flags=levels.Flags(needs_owner=True), subject_entity_id=d.subject_id)
        sig = await upsert(db, org_id, UpsertIn(kind=kind, title="Thông tin mâu thuẫn cần Sếp xác nhận", detector="mem_disputes",
                                                level_input=li, evidence=evidence, case_key=ck, fact_ids=list(d.fact_ids),
                                                facts={"dispute_id": str(d.id), "predicate": d.predicate}), now)
        await db.execute(text("UPDATE mem.disputes SET projected_signal_id = :s WHERE id = :d"), {"s": sig.id, "d": d.id})
        await db.execute(text("""INSERT INTO biz.signal_links (org_id, signal_id, ref_type, ref_id) VALUES (:o, :s, 'dispute', :d)
                                 ON CONFLICT DO NOTHING"""), {"o": org_id, "s": sig.id, "d": d.id})
        out.append(sig)
    return out


# ─── xác minh độc lập (d1)/(d2) — gọi L3 crosscheck ──────────────────────────

def crosscheck_plan(s: levels.SignalIn, profile: profile_read.ProfileSnapshot, policy: contexts.Policy, state: levels.DeliveryState,
                    now: datetime) -> Literal["d2", "d1"] | None:
    """§4D.2 (d): (d2) mạnh cho mọi ứng viên 🔴/🟠 lừa đảo/an toàn và mọi tin có cờ luật `credentials_entered`/`money_request`/`emergency`;
    (d1) cho ứng viên 🔴 của 4 cổng thường và ứng viên 🟠 (tính thử với độ chắc giả định medium)."""
    f = s.flags
    if s.kind in ("rui_ro_lua_dao", "suc_khoe_an_toan") or f.credentials_entered or f.money_request or f.emergency or s.hard_rule_hit:
        trial = levels.compute(s.model_copy(update={"confidence_exist": 0.8, "d2": levels.D2()}), profile, policy, state, now)
        if trial.level in ("red", "amber") or s.hard_rule_hit or f.credentials_entered or f.money_request or f.emergency:
            return "d2"
    trial = levels.compute(s.model_copy(update={"confidence_content": 0.75}), profile, policy, state, now)
    if trial.level in ("red", "amber"):
        return "d1"
    return None


def apply_crosscheck(s: levels.SignalIn, *, d1: dict[str, Any] | None = None, d2: dict[str, Any] | None = None) -> tuple[levels.SignalIn, bool]:
    """Gộp kết quả (d) vào đầu vào mức (hàm thuần). Trả (SignalIn mới, `dismissed_by_system`). (d1) 0/2 ⇒ dismissed (`verdict_by=system`).
    (d1) không chạy đủ 2 lượt (`agreement=None`) ⇒ giữ "chưa qua (d)". (d2) lỗi ⇒ giữ nguyên (fail-high); luật cứng ⇒ exist 1,00."""
    upd: dict[str, Any] = {}
    dismissed = False
    rel = SOURCE_RELIABILITY.get(s.source_class, 0.75)
    if d1 is not None and d1.get("agreement") is not None:
        ag = float(d1["agreement"])
        upd["confidence_content"] = round(ag * rel, 3)
        if s.source_class != "rule":
            upd["confidence_exist"] = ag
        dismissed = ag == 0.0
    if d2 is not None:
        verdict = d2.get("verdict")
        err = verdict not in ("agree", "disagree") or bool(d2.get("error"))
        upd["d2"] = levels.D2(verdict=None if err else verdict, error=err,
                              flags_confirmed=[] if err else [x for x in (d2.get("flags_confirmed") or []) if x in levels.Flags.model_fields],
                              harm_what=None if err else d2.get("harm_what"))
    return s.model_copy(update=upd), dismissed


async def run_crosscheck(s: levels.SignalIn, unit: Any, raw_ctx: Any, *, call: Any, profile: profile_read.ProfileSnapshot,
                         policy: contexts.Policy, state: levels.DeliveryState, now: datetime,
                         secrets: tuple[str, ...] = ()) -> tuple[levels.SignalIn, bool]:
    from gh.refinery import crosscheck

    plan = crosscheck_plan(s, profile, policy, state, now)
    if plan == "d2":
        res = await crosscheck.verify_strong(unit, s.kind or "rui_ro_lua_dao", raw_ctx, call=call, secrets=secrets)
        return apply_crosscheck(s, d2=dict(res))
    if plan == "d1":
        res1 = await crosscheck.verify(unit, s.kind or "", raw_ctx, call=call, secrets=secrets)
        return apply_crosscheck(s, d1=res1)
    return s, False


# ─── bên gọi `raise_alert` cũ (bảng alert_type §4B.2) ─────────────────────────

LEGACY_MAP: dict[str, tuple[str, str, int | None, int | None, str | None]] = {
    "customer_cooling": ("quan_he_nong_nguoi", "chat_ok", 2, 1, "medium"),
    "slow_response": ("cau_hoi_cho_xu_ly", "chat_ok", 2, 2, "high"),
    "unclaimed_opportunity": ("co_hoi_ket_noi", "chat_ok", 2, 2, "medium"),
    "opportunity_signal": ("co_hoi_ket_noi", "chat_ok", 2, 1, "medium"),
    "competitor": ("thi_truong_thong_tin", "chat_ok", 1, 1, "medium"),
    "forgotten_deadline": ("viec_han", "chat_ok", 2, 3, "medium"),
    "repeated_complaint": ("xung_dot_khieu_nai", "chat_ok", 2, 2, "medium"),
    "people_signal": ("legacy", "console_pin", 1, 1, "medium"),
    "rule_alert": ("legacy", "chat_ok", 1, 1, "low"),
    "data_conflict": ("system", "chat_ok", None, None, None),
    "model_quota_low": ("system", "chat_ok", None, None, None),
    "model_chain_exhausted": ("system", "chat_ok", None, None, None),
    "model_chain_agy_only": ("system", "chat_ok", None, None, None),
}


async def record_legacy(db: AsyncSession, org_id: uuid.UUID, *, alert_type: str, priority: str, title: str, summary: str | None,
                        suggested: str | None = None, subject_type: str | None = None, subject_id: Any = None,
                        evidence: list[dict[str, Any]] | None = None, personnel_related: bool = False,
                        recipient_user_id: uuid.UUID | None = None, now: datetime | None = None) -> uuid.UUID:
    """Ghi một cảnh báo của bên gọi CŨ (chỉ có `alert_type` + `priority`): loại/riêng tư/trục theo bảng §4B.2 (loại lạ ⇒ legacy,
    console_pin, low). Loại tình báo mà bằng chứng chưa `verified` ⇒ `legacy` (Console thấy, không lên Zalo — §8.1). Ngoài 6 cổng nên
    tối đa 🟠: 🟠 khi impact ≥ 2 và urgency ≥ 2, còn lại ⚪. Một nơi ghi mức/ngăn vẫn là module này."""
    kind, privacy, imp, urg, conf = LEGACY_MAP.get(alert_type, ("legacy", "console_pin", 1, 1, "low"))
    ev = list(evidence or [])
    if personnel_related:
        privacy = "console_pin"
    if kind in INTEL_KINDS:
        mu_ids = [str(e.get("id")) for e in ev if isinstance(e, dict) and e.get("type") == "meaning_unit" and e.get("id")]
        verified = bool(mu_ids) and (await db.execute(text("""
            SELECT bool_and(e.quote_status = 'verified') FROM clean.evidence e JOIN clean.meaning_units m
              ON m.id = e.meaning_unit_id AND m.observed_at = e.meaning_observed_at
            WHERE m.org_id = :o AND e.meaning_unit_id::text = ANY(:ids)"""), {"o": org_id, "ids": mu_ids})).scalar_one() is True
        if not verified:
            kind = "legacy"
    if kind in NON_INTEL_KINDS:
        level, delivery = ("grey", "none")
    else:
        level, delivery = ("amber", "digest") if (imp or 0) >= 2 and (urg or 0) >= 2 else ("grey", "digest_low")
    await cap_lock(db, org_id, now or clock.now())         # khoá trần TRƯỚC core.next_code (cùng thứ tự với upsert)
    code = (await db.execute(text("SELECT core.next_code('ALR')"))).scalar_one()
    return (await db.execute(text("""
        INSERT INTO biz.alerts (org_id, code, alert_type, priority, recipient_user_id, subject_type, subject_id, title, summary,
                                suggested_action, evidence, evidence_n, personnel_related, kind, privacy, impact, urgency, confidence_level,
                                level, delivery, lane, detector, dedupe_key)
        VALUES (:o, :c, :t, :p, :u, :st, :si, :ti, :su, :sa, CAST(:ev AS jsonb), :n, :pr, :k, :pv, :im, :ur, :cl, :lv, :dl, :ln,
                'legacy_' || :t, NULL)
        RETURNING id"""),
        {"o": org_id, "c": code, "t": alert_type, "p": priority, "u": recipient_user_id, "st": subject_type, "si": subject_id,
         "ti": title, "su": summary, "sa": suggested, "ev": orjson.dumps(ev).decode(), "n": len(ev), "pr": personnel_related,
         "k": kind, "pv": privacy, "im": imp, "ur": urg, "cl": conf, "lv": level, "dl": delivery,
         "ln": _lane("open", level, False, False, "fyi")})).scalar_one()  # type: ignore[no-any-return]
