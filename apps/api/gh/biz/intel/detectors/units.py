# ruff: noqa: E501
"""Bộ chiếu đơn vị chung (v0.1.76 T12; spec r6.6.1 §6.2 "đầu vào detector là meaning_units đã kiểm", §4.2.1 (c) bảng nguồn `SignalIn`).

Trước T12, đơn vị model trích ra (`clean.meaning_units`, đường brain-out/1, `event_type = 'GroupUnit'`) chỉ thành tín hiệu cho 2 loại
(`generic.UNIT_KINDS`); mọi loại khác — kể cả lừa đảo có cờ `credentials_entered` mà luật (a) hụt — rơi im lặng. Module này là CHỖ DUY NHẤT
khai báo chủ sở hữu của từng `kind` dạng đơn vị (`OWNERS`; test bất biến đỏ khi thêm loại mà quên chiếu) và tự chiếu các loại mình sở hữu:

- `rui_ro_lua_dao`: `SignalIn` dựng đúng bảng nguồn — `kind` + cờ của model, `victims`/`impersonated`/`payee_refs` từ `people[].role`
  (vai đã nối CHẮC thực thể, lưu ở `entities.v.people` bởi runner), `deadline_at`/`event_at`/`ongoing`/`txn_*`/`delivery_form` là giá trị
  ĐÃ KIỂM của `verify` (`entities.v`), `hard_rule_hit` từ `refinery.event_state.cands` (luật (a)). (d2) qua `signals.run_crosscheck` khi
  kế hoạch (d) nói "d2" và còn trần lượt an toàn: không luật cứng ⇒ (d2) TRƯỚC khi ghi (không 🔴 nào bị hạ sau khi đẩy); có luật cứng ⇒
  ghi ngay rồi (d2) (như `fraud`). Ghi cùng khoá vụ với fraud/raw_tail: người bị mạo danh ⇒ khoá `imp|` (mạnh), còn lại khoá dự phòng
  `raw|<tin gốc>` ⇒ `signals.upsert` GỘP vào vụ mở cùng tin gốc (một vụ một 🔴; T1 nâng khoá raw|/anon| khi có khoá mạnh).
- `xung_dot_khieu_nai` / `quan_he_nong_nguoi`: người chê là khách (dữ liệu, hoặc luật khởi động lạnh `generic.cold_customer`) ⇒ dẫn sang
  CHỦ cổng 3 `conflicts.emit` (cờ `public`/`said_will_leave` của model); không phải khách ⇒ chiếu như tín hiệu thường (không cổng 3).
- `co_hoi_ket_noi`, `su_kien_dip` dạng đơn vị (đơn vị đã có sự thật `scheduled_event` ⇒ để `generic.project_events` làm chủ).
Loại còn lại: chủ sở hữu riêng (`OWNERS`). Không gán mức: mức do `levels.compute` (qua `signals`). Mỗi đơn vị chiếu đúng một lần:
`facts.unit_id` / bằng chứng `meaning_unit` trên tín hiệu, hoặc dấu `intel.units.done` ở Nhật ký (đơn vị đã dẫn sang detector khác / bỏ qua).
Không ghi nội dung tin vào log (chỉ mã đơn vị + tên lỗi).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob
from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import generic as g
from gh.biz.mem import profile_read

log = logging.getLogger("gh.intel.units")

DETECTOR, VERSION = "units", "1"
WINDOW_DAYS = 14
MAX_D2_PER_RUN = 5
MAX_FAILS = 3                         # đơn vị lỗi ≥ 3 lượt ⇒ dấu "đã xử lý" (như fraud), không lấy lại mãi
RUN_BUDGET_S = 30.0                   # trần thời gian một lượt chiếu một tổ chức
FAIL_ACTION = "intel.units.fail"

#: Chủ sở hữu của từng `kind` dạng đơn vị. "units" = module này chiếu. Thêm loại vào `levels.KINDS` mà không khai ở đây ⇒ test đỏ.
OWNERS: dict[str, str] = {
    "suc_khoe_an_toan": "generic.project_units",
    "thi_truong_thong_tin": "generic.project_units",
    "nghia_vu_loi_hua": "promises.project_units",
    "viec_han": "promises.project_units",
    "cau_hoi_cho_xu_ly": "questions.scan_units",
    "rui_ro_lua_dao": "units",
    "xung_dot_khieu_nai": "units",           # khách ⇒ conflicts.emit (chủ cổng 3)
    "quan_he_nong_nguoi": "units",           # khách doạ bỏ ⇒ conflicts.emit; còn lại chiếu thường
    "co_hoi_ket_noi": "units",
    "su_kien_dip": "units",                  # có sự thật scheduled_event ⇒ generic.project_events
}
UNIT_KINDS = tuple(k for k, v in OWNERS.items() if v == "units")
VICTIM_ROLES = ("nguoi_bi_anh_huong", "nguoi_hoi", "nguoi_duoc_hua")
TITLES = {"rui_ro_lua_dao": "Dấu hiệu lừa đảo / rủi ro", "xung_dot_khieu_nai": "Xung đột / khiếu nại", "quan_he_nong_nguoi": "Quan hệ cần để ý",
          "co_hoi_ket_noi": "Cơ hội / kết nối", "su_kien_dip": "Sắp tới dịp / sự kiện"}
STATUS_CLOSED = ("da_xu_ly", "da_giu")


def _when(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return levels._aware(datetime.fromisoformat(v))           # dữ liệu cũ/lỗi thiếu múi giờ ⇒ coi là UTC (không làm hỏng phép so)
    except ValueError:
        return None


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


def verified(u: Any) -> dict[str, Any]:
    """`entities.v` do runner lưu (giá trị đã kiểm của `verify`). Đơn vị cũ (trước T12) ⇒ {} ⇒ mặc định trung tính."""
    e = u.entities if isinstance(u.entities, dict) else {}
    v = e.get("v")
    return v if isinstance(v, dict) else {}


# ─── người: vai của model + tra cứu của code ─────────────────────────────────

@dataclass
class People:
    group_id: uuid.UUID | None
    size: int | None
    sender: g.Who | None
    victims: list[levels.PersonRel] = field(default_factory=list)
    victim_who: list[g.Who] = field(default_factory=list)
    impersonated: list[levels.PersonRel] = field(default_factory=list)
    impersonated_who: list[g.Who] = field(default_factory=list)
    payee_refs: list[str] = field(default_factory=list)
    roles: dict[str, list[g.Who]] = field(default_factory=dict)
    forwarded: bool = False
    hard: list[str] = field(default_factory=list)
    outbound: bool = False


async def people(db: AsyncSession, org_id: uuid.UUID, u: Any, sets: g.OrgSets, raws: list[uuid.UUID]) -> People:
    group_id, size = await g.raw_group(db, raws[0])
    forwarded = outbound = False
    for r in (await db.execute(text("SELECT payload, direction FROM raw.events WHERE id = ANY(:r) AND org_id = :o"), {"r": raws, "o": org_id})).all():
        forwarded = forwarded or any(bool((r.payload or {}).get(k)) for k in g.FORWARD_KEYS)
        outbound = outbound or r.direction == "outbound"
    hard = [str(x) for x in (await db.execute(text("""
        SELECT DISTINCT c ->> 'hard' FROM refinery.event_state s, jsonb_array_elements(COALESCE(s.detail -> 'cands', '[]')) c
        WHERE s.org_id = :o AND s.event_id = ANY(:r) AND c ->> 'hard' IS NOT NULL"""), {"o": org_id, "r": raws})).scalars().all()]
    sender = await g.who_person(db, org_id, u.person_id)
    members: set[uuid.UUID] = set()
    if group_id is not None:
        members = set((await db.execute(text("""SELECT e.id FROM core.group_members gm JOIN mem.entities e ON e.person_id = gm.person_id AND e.org_id = :o
                                                WHERE gm.group_id = :g AND gm.left_at IS NULL"""), {"o": org_id, "g": group_id})).scalars().all())

    def in_group(ent: uuid.UUID) -> bool:          # fail-high như generic: nhóm chưa có danh sách thành viên ⇒ coi như thành viên
        return (sender is not None and ent == sender.entity_id) or ent in members or (group_id is not None and not members)

    p = People(group_id, size, sender, forwarded=forwarded, hard=hard, outbound=outbound)
    seen: set[tuple[str, str]] = set()
    listed = verified(u).get("people")
    if isinstance(listed, list):
        for it in listed:
            if not isinstance(it, dict):
                continue
            role = str(it.get("role") or "")
            w = await g.who(db, _uuid(it.get("entity_id")))
            if w is None:
                if role in VICTIM_ROLES and sender is not None and g.KIN.search(g.fold(str(it.get("surface") or ""))):
                    p.victims.append(levels.PersonRel(ref=f"kin_{len(p.victims)}", kin_of_member=True))
                continue
            if (role, w.ref) in seen:
                continue
            seen.add((role, w.ref))
            p.roles.setdefault(role, []).append(w)
            rel = _rel(w, sets, thread_member=in_group(w.entity_id))
            if role in VICTIM_ROLES:
                p.victims.append(rel)
                p.victim_who.append(w)
            elif role == "nguoi_bi_mao_danh":
                p.impersonated.append(rel)
                p.impersonated_who.append(w)
            elif role == "nguoi_nhan_tien":
                p.payee_refs.append(w.ref)
    else:
        # Đơn vị trước T12 (không có vai): như generic — mọi người được nêu là đối tượng.
        for m in (await db.execute(text("SELECT entity_id FROM mem.mentions WHERE meaning_unit_id = :u AND org_id = :o AND entity_id IS NOT NULL"),
                                   {"u": u.id, "o": org_id})).scalars().all():
            w = await g.who(db, m)
            if w is not None and all(x.ref != w.ref for x in p.victims):
                p.victims.append(_rel(w, sets, thread_member=in_group(w.entity_id)))
                p.victim_who.append(w)
    if not p.victims and sender is not None and not _sender_suspect(u, p, sender):
        p.victims.append(_rel(sender, sets, thread_member=True))
        p.victim_who.append(sender)
    return p


def _sender_suspect(u: Any, p: People, sender: g.Who) -> bool:
    """Lừa đảo mà người gửi chính là người bị nghi — đang mạo danh NGƯỜI KHÁC (vai `nguoi_bi_mao_danh` không phải người gửi) hoặc là người
    nhận tiền ⇒ KHÔNG lấy người gửi làm nạn nhân mặc định (G-KB Đợt 2 P5: kẻ lừa thành "nạn nhân", nguồn insider, sai người leo thang).
    Nạn nhân để rỗng ⇒ `levels.owner_side` dùng nhóm gần/độ phủ (tin trực tiếp trong nhóm)."""
    if str(u.kind_hint or "") != levels.FR:
        return False
    if any(w.entity_id != sender.entity_id for w in p.impersonated_who):
        return True
    return any(w.entity_id == sender.entity_id for w in p.roles.get("nguoi_nhan_tien", []))


def _rel(w: g.Who, sets: g.OrgSets, *, thread_member: bool) -> levels.PersonRel:
    return g.who_rel(w, sets, thread_member=thread_member)


# ─── SignalIn (bảng nguồn §4.2.1 (c)) ─────────────────────────────────────────

def signal_input(kind: str, u: Any, p: People, sets: g.OrgSets, *, customer_of_owner: bool = False, status_by_owner_side: bool = False) -> levels.SignalIn:
    """Hàm thuần: đơn vị đã kiểm + tra cứu người ⇒ `SignalIn`. Không quyết mức, không tính "phía Sếp" (việc của `levels`)."""
    v = verified(u)
    fl = dict(u.flags or {})
    f = g.flags_of(fl)
    observed = u.observed_at
    msg_at = _when(v.get("msg_at")) or observed
    event_at = _when(v.get("event_at")) or msg_at
    ongoing = bool(v["ongoing"]) if "ongoing" in v else fl.get("ongoing") is True
    forwarded = p.forwarded or v.get("delivery_form") == "forwarded"
    sender_ref = p.sender.ref if p.sender else None
    insider = sender_ref is not None and any(x.ref == sender_ref for x in p.victims)
    src = "forwarded" if forwarded else ("insider" if insider else "member")
    harm = u.harm if isinstance(u.harm, dict) else {}
    status_hint = str(v.get("status_hint") or "dang_mo")
    money = v.get("money_vnd") if isinstance(v.get("money_vnd"), int) else None
    core = any(x.is_owner or x.owner_key_relation or x.customer or x.team for x in p.victims)
    if kind == levels.FR:
        ib = levels.impact_base(kind, owner_side_targeted=core, money_or_credentials=f.money_request or f.credentials_entered)
    elif kind == levels.SK:
        ib = levels.impact_base(kind, emergency=f.emergency, subject_owner_side=core)
    else:
        ib = levels.impact_base(kind, important=core or customer_of_owner)
    d: dict[str, Any] = dict(
        kind=kind, msg_at=msg_at, event_at=event_at, deadline_at=_when(v.get("deadline_at")), ongoing=ongoing, flags=f,
        hard_rule_hit=list(p.hard), harm_what=str(harm.get("what") or "") if isinstance(harm.get("what"), str) else "",
        irreversible=bool(harm.get("irreversible")), gate_candidate=u.gate_candidate, thread=g.thread_of(p.group_id, p.size),
        victims=list(p.victims), impersonated=list(p.impersonated), payee_refs=list(p.payee_refs),
        delivery_form="forwarded" if forwarded else ("warning_story" if v.get("delivery_form") == "warning_story" else "direct"),
        customer_of_owner=customer_of_owner, event_time_unparsed=bool(v.get("event_time_unparsed")),
        source_class=src, non_forwarded_sources_n=0 if forwarded else 1, confidence_exist=float(u.confidence or 0.8),
        confidence_content=g.conf_content(src), impact_base=ib, money_vnd=money, money_basis="quoted" if money else None,
        subject_entity_id=p.victim_who[0].entity_id if p.victim_who else None)
    txn = v.get("txn_state")
    if txn in ("pending", "just_done", "done_cannot_stop"):
        d["txn_state"] = txn
        d["txn_at"] = _when(v.get("txn_at"))
    by = v.get("status_by_person_id")
    by_ref = f"u_{by}" if by else None
    if kind in (levels.FR, levels.SK):
        # status_hint của model bị bỏ qua ở lừa đảo/an toàn; chỉ khắc phục của nạn nhân / người phía Sếp (code kiểm người gửi) được tính.
        if status_hint in STATUS_CLOSED and by_ref:
            side = status_by_owner_side or any(x.ref == by_ref for x in p.victims)
            d["remediation"] = "victim_or_owner_side" if side else "other"
    else:
        d["status"] = status_hint if status_hint in STATUS_CLOSED else "dang_mo"
    return levels.SignalIn(**d)


# ─── (d2) ─────────────────────────────────────────────────────────────────────

async def raw_ctx(db: AsyncSession, org_id: uuid.UUID, uid: uuid.UUID, observed: datetime, group_id: uuid.UUID | None) -> tuple[Any, Any]:
    """Đơn vị + tin gốc cho `crosscheck`: tin được trích (nguyên văn — Q9) + ≤ 8 tin trước cùng nhóm trong 2 ngày."""
    from gh.refinery.crosscheck import RawContext
    from gh.refinery.extract import GroupEvent

    q = (await db.execute(text("""SELECT ce.raw_event_id, ce.quote, ce.source_field, e.occurred_at, e.body_text, pi.person_id, p.display_name
                                  FROM clean.evidence ce JOIN raw.events e ON e.id = ce.raw_event_id AND e.received_at = ce.raw_received_at
                                  LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id LEFT JOIN core.persons p ON p.id = pi.person_id
                                  WHERE ce.meaning_unit_id = :u AND ce.meaning_observed_at = :t AND ce.quote_status = 'verified'
                                  ORDER BY e.occurred_at"""), {"u": uid, "t": observed})).all()
    cited = list(dict.fromkeys(r.raw_event_id for r in q))
    prev: list[Any] = []
    if group_id is not None and q:
        prev = list((await db.execute(text("""
            SELECT e.id, e.occurred_at, e.body_text, pi.person_id, p.display_name FROM raw.events e
            LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id LEFT JOIN core.persons p ON p.id = pi.person_id
            WHERE e.org_id = :o AND e.group_id = :g AND e.occurred_at <= :at AND e.occurred_at >= :lo AND e.id <> ALL(:c)
            ORDER BY e.occurred_at DESC LIMIT 8"""), {"o": org_id, "g": group_id, "at": q[0].occurred_at, "lo": q[0].occurred_at - timedelta(days=2),
                                                     "c": cited})).all())
    evs: list[Any] = []
    mid: dict[uuid.UUID, str] = {}
    for r in reversed(prev):
        evs.append(GroupEvent(message_id=f"m_{len(evs) + 1}", sent_at=r.occurred_at, body=r.body_text, sender_person_id=r.person_id,
                              sender_name=r.display_name, raw_event_id=r.id, context=True))
    for r in q:
        if r.raw_event_id in mid:
            continue
        mid[r.raw_event_id] = f"m_{len(evs) + 1}"
        evs.append(GroupEvent(message_id=mid[r.raw_event_id], sent_at=r.occurred_at, body=r.body_text, sender_person_id=r.person_id,
                              sender_name=r.display_name, raw_event_id=r.raw_event_id))
    quotes = [SimpleNamespace(message_id=mid[r.raw_event_id], field=r.source_field or "body", text=(r.quote or "")[:2000]) for r in q]
    return SimpleNamespace(quotes=quotes), RawContext(events=evs)


async def _d2(db: AsyncSession, org_id: uuid.UUID, u: Any, li: levels.SignalIn, thread_key: str, now: datetime, call: Any) -> tuple[levels.SignalIn, dict[str, Any] | None]:
    """(d2) theo kế hoạch (d) của `signals` — chỉ khi kế hoạch là "d2" (lừa đảo/an toàn, cờ tiền/OTP/cấp cứu). Lỗi ⇒ giữ nguyên (fail-high)."""
    from gh.biz.intel.detectors import fraud
    from gh.biz.mem import contexts

    prof = await profile_read.snapshot(db, org_id)
    pol = await contexts.policy(db, org_id, thread_key)
    st = await signals.delivery_state(db, org_id, now)
    if signals.crosscheck_plan(li, prof, pol, st, now) != "d2" or not await fraud._d2_allowed(db, org_id, thread_key, now):
        return li, None
    unit, ctx = await raw_ctx(db, org_id, u.id, u.observed_at, u.group_id)
    try:
        out, _dismissed = await signals.run_crosscheck(li, unit, ctx, call=call, profile=prof, policy=pol, state=st, now=now)
    except Exception as e:  # noqa: BLE001 — verify_strong không ném; phòng xa: fail-high
        log.warning("units: (d2) lỗi ở đơn vị %s: %s", u.id, type(e).__name__)
        return li, {"verdict": "error", "error": type(e).__name__}
    return out, {"verdict": out.d2.verdict, "error": out.d2.error}


# ─── chiếu ────────────────────────────────────────────────────────────────────

_UNIT_COLS = "mu.id, mu.observed_at, mu.group_id, mu.person_id, mu.conclusion, mu.kind_hint, mu.flags, mu.gate_candidate, mu.harm, mu.confidence, mu.entities"


def pending_sql(action: str) -> str:
    """Đơn vị loại `:k`, đã có trích dẫn verified, chưa chiếu (facts.unit_id / bằng chứng meaning_unit / dấu Nhật ký `action`)."""
    return f"""
        SELECT {_UNIT_COLS} FROM clean.meaning_units mu
        WHERE mu.org_id = :o AND mu.kind_hint = ANY(:k) AND mu.superseded_by IS NULL AND mu.observed_at >= :since
          AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o
                          AND (a.facts ->> 'unit_id' = mu.id::text
                               OR a.evidence @> jsonb_build_array(jsonb_build_object('type', 'meaning_unit', 'id', mu.id::text))))
          AND EXISTS (SELECT 1 FROM clean.evidence ce WHERE ce.meaning_unit_id = mu.id AND ce.meaning_observed_at = mu.observed_at
                        AND ce.quote_status = 'verified' AND ce.raw_event_id IS NOT NULL)
          AND NOT EXISTS (SELECT 1 FROM ops.action_log al WHERE al.org_id = :o AND al.action = '{action}' AND al.target_id = mu.id::text)
        ORDER BY mu.observed_at DESC LIMIT :n"""  # noqa: S608 — `action` là hằng nội bộ


async def evidence_of(db: AsyncSession, u: Any) -> list[Any]:
    return list((await db.execute(text("""SELECT raw_event_id, raw_received_at, MIN(span_start) AS s0, MAX(span_end) AS s1, MIN(source_field) AS sf
                                          FROM clean.evidence WHERE meaning_unit_id = :u AND meaning_observed_at = :t AND quote_status = 'verified'
                                            AND raw_event_id IS NOT NULL GROUP BY raw_event_id, raw_received_at ORDER BY raw_received_at"""),
                                 {"u": u.id, "t": u.observed_at})).all())


def unit_evidence(u: Any, ev: list[Any]) -> list[signals.EvidenceIn]:
    return [signals.EvidenceIn(type="meaning_unit", id=u.id, raw_event_id=e.raw_event_id, received_at=e.raw_received_at) for e in ev]


async def mark(db: AsyncSession, org_id: uuid.UUID, uid: uuid.UUID, why: str, *, action: str = "intel.units.done") -> None:
    from gh.chassis import actionlog
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=f"system:{DETECTOR}", action=action,
                           target_type="meaning_unit", target_id=str(uid), result="ok", detail={"why": why})


async def _goods_impersonation(db: AsyncSession, org_id: uuid.UUID, u: Any, p: People, sets: g.OrgSets, li: levels.SignalIn,
                               raws: list[uuid.UUID]) -> levels.SignalIn:
    """(D2, Ryan chốt 2026-10-25) Người gửi MẠO DANH một khách/đối tác đã biết (vai `nguoi_bi_mao_danh` là người khác người gửi, không phải
    Sếp/agent) và chữ ĐÃ KIỂM (trích dẫn verified + thân tin được trích của chính người gửi) có "đặt hàng ghi nợ / đổi địa chỉ giao / đổi
    người nhận" ⇒ `goods_risk` (code kiểm bằng `fraud.goods_redirect`, không theo lời model) và Sếp (bên giao hàng) là bên bị nhắm. Khách
    quen thật đổi địa chỉ từ chính định danh cũ ⇒ không có vai mạo danh ⇒ không đổi gì."""
    from gh.biz.intel.detectors import fraud

    s = p.sender
    if s is None or not any(w.entity_id != s.entity_id and not sets.is_owner(w.entity_id) for w in p.impersonated_who):
        return li
    if s.person_id is None:
        return li
    # CHỈ chữ của chính người gửi (review T14 D2-c): trích dẫn của người khác trong cùng đơn vị không làm kẻ trùng tên thành "đặt hàng".
    texts = list((await db.execute(text("""SELECT ce.quote FROM clean.evidence ce JOIN raw.events e ON e.id = ce.raw_event_id AND e.received_at = ce.raw_received_at
                                            JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                            WHERE ce.meaning_unit_id = :u AND ce.meaning_observed_at = :t AND ce.quote_status = 'verified'
                                              AND ce.quote IS NOT NULL AND pi.person_id = :p"""),
                                   {"u": u.id, "t": u.observed_at, "p": s.person_id})).scalars().all())
    texts += list((await db.execute(text("""SELECT e.body_text FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                             WHERE e.id = ANY(:r) AND e.org_id = :o AND pi.person_id = :p AND e.body_text IS NOT NULL"""),
                                     {"r": raws, "o": org_id, "p": s.person_id})).scalars().all())
    if not any(fraud.goods_redirect(t) for t in texts):
        return li
    victims = list(li.victims)
    if sets.owner is not None and not any(v.is_owner for v in victims):
        ow = await g.who(db, sets.owner)
        victims.append(levels.PersonRel(ref=ow.ref if ow else "owner", is_owner=True))
    return li.model_copy(update={"goods_risk": True, "victims": victims})


def _case_key(kind: str, u: Any, p: People, li: levels.SignalIn, raw0: uuid.UUID) -> str:
    if kind == levels.FR and p.impersonated_who:
        return levels.case_key(kind, at=li.msg_at, impersonated_entity_id=str(p.impersonated_who[0].entity_id))
    if kind == "quan_he_nong_nguoi" and p.victim_who:
        return levels.case_key(kind, at=li.msg_at, entity_id=str(p.victim_who[0].entity_id))
    if kind == "su_kien_dip" and li.deadline_at and p.victim_who:
        return levels.case_key(kind, at=li.msg_at, entity_id=str(p.victim_who[0].entity_id),
                               event_date=levels._aware(li.deadline_at).astimezone(levels.VN).date().isoformat(), raw_event_id=str(raw0))
    return levels.case_key(kind, at=li.msg_at, raw_event_id=str(raw0))


async def _route_conflict(db: AsyncSession, org_id: uuid.UUID, u: Any, p: People, sets: g.OrgSets, ev: list[Any], now: datetime) -> list[signals.Signal] | None:
    """Lời chê / doạ bỏ của KHÁCH ⇒ chủ cổng 3 (`conflicts.emit`). Không phải khách / không dựng được ⇒ None (bên gọi chiếu thường)."""
    from gh.biz.intel.detectors import conflicts

    fl = dict(u.flags or {})
    if u.kind_hint == "quan_he_nong_nguoi" and not (fl.get("said_will_leave") is True or fl.get("public") is True):
        return None                                         # nguội đi / im lặng: không phải lời chê ⇒ chiếu thường
    s = p.sender
    if s is None or s.person_id is None or hard_side(s, sets):
        return None
    cold = not g.hard_customer(s, sets) and g.cold_customer(s, sets, biz_group=await g.business_group(db, org_id, p.group_id))
    if not (g.hard_customer(s, sets) or cold):
        return None
    mine = (await db.execute(text("""SELECT e.id FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = ANY(:r) AND e.org_id = :o AND e.direction = 'inbound' AND pi.person_id = :p
                                     ORDER BY e.occurred_at DESC LIMIT 1"""), {"r": [e.raw_event_id for e in ev], "o": org_id, "p": s.person_id})).scalar_one_or_none()
    if mine is None:
        return None
    e0 = next(e for e in ev if e.raw_event_id == mine)
    v = verified(u)
    status = str(v.get("status_hint") or "dang_mo")
    li_at = _when(v.get("msg_at")) or u.observed_at
    c = conflicts.ComplaintIn(
        sent_at=li_at, raw_event_id=mine, customer_person_id=s.person_id, source_field=e0.sf or "body",  # type: ignore[arg-type]
        span_start=int(e0.s0 or 0), span_end=max(int(e0.s1 or 1), int(e0.s0 or 0) + 1),
        public=fl.get("public") is True and await public_place(db, org_id, p, s, sets, [mine]),
        said_will_leave=fl.get("said_will_leave") is True, repeat_claimed=int(fl["repeat_n"]) if isinstance(fl.get("repeat_n"), int) and not isinstance(fl.get("repeat_n"), bool) else 0,
        closure="customer" if status in STATUS_CLOSED and v.get("status_by_person_id") == str(s.person_id) else "none",
        thread=g.thread_of(p.group_id, p.size), thread_key=f"g:{p.group_id}" if p.group_id else None, group_id=p.group_id,
        title="Khách phàn nàn" if not fl.get("said_will_leave") else "Khách phàn nàn, doạ bỏ")
    out = await conflicts.emit(db, org_id, c, now, cold_customer=cold, extra_evidence=unit_evidence(u, ev), facts_extra={"unit_id": str(u.id)})
    return out or None


async def public_place(db: AsyncSession, org_id: uuid.UUID, p: People, s: g.Who, sets: g.OrgSets, raws: list[uuid.UUID]) -> bool:
    """Cờ `public` của model chỉ giữ khi NƠI nói thật sự công khai, do code xác định theo bối cảnh (G-KB Đợt 2 P8): luật (a) thấy (trên tin
    của chính người chê — `raws`) lời nêu nơi công khai ("đăng lên fb", "bóc phốt"…) ⇒ có; chat 1-1 ⇒ không; nhóm Sếp đã xác nhận chỉ nội bộ (nhan_vien/gia_dinh) ⇒ không; nhóm có
    danh sách thành viên ⇒ có khi còn người KHÁC ngoài người chê và phía Sếp; không danh sách ⇒ theo cỡ nhóm (> 2), không rõ cỡ ⇒ giữ lời model."""
    from gh.biz.mem import contexts

    if (await db.execute(text("""SELECT 1 FROM refinery.event_state st, jsonb_array_elements(COALESCE(st.detail -> 'cands', '[]')) c
                                  WHERE st.org_id = :o AND st.event_id = ANY(:r) AND c ->> 'flag' = 'public' LIMIT 1"""),
                         {"o": org_id, "r": raws})).first() is not None:
        return True
    if p.group_id is None:
        return False
    pol = await contexts.policy(db, org_id, f"g:{p.group_id}")
    if pol.confirmed and pol.contexts and all(c in ("nhan_vien", "gia_dinh") for c in pol.contexts):
        return False
    members = (await db.execute(text("""SELECT COALESCE(e.merged_into, e.id) AS ent, p.person_type, p.attrs ->> 'is_owner' AS own
                                         FROM core.group_members gm JOIN core.persons p ON p.id = gm.person_id
                                         LEFT JOIN mem.entities e ON e.person_id = gm.person_id AND e.org_id = :o
                                         WHERE gm.group_id = :g AND gm.left_at IS NULL"""), {"o": org_id, "g": p.group_id})).all()
    if members:
        return any(m.ent != s.entity_id and m.person_type != "staff" and m.own != "true" and not sets.is_owner(m.ent)
                   and m.ent not in sets.staff for m in members)
    return p.size is None or p.size > 2


def hard_side(w: g.Who, sets: g.OrgSets) -> bool:
    return g.hard_owner_side(w, sets)


async def failed(db: AsyncSession, org_id: uuid.UUID, uid: uuid.UUID, err: Exception, *, done_action: str) -> None:
    """Một đơn vị lỗi (ngoài `SignalError`): ghi một lần lỗi (mã đơn vị + tên lỗi, không nội dung); đủ `MAX_FAILS` lần ⇒ dấu "đã xử lý" để
    không lấy lại mãi. Đơn vị khác trong lô vẫn chạy (một đơn vị hỏng không chặn lừa đảo của đơn vị khác)."""
    log.warning("units: đơn vị %s lỗi: %s", uid, type(err).__name__)
    await mark(db, org_id, uid, type(err).__name__, action=FAIL_ACTION)
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE org_id = :o AND action = :a AND target_id = :t"),
                          {"o": org_id, "a": FAIL_ACTION, "t": str(uid)})).scalar_one()
    if int(n) >= MAX_FAILS:
        await mark(db, org_id, uid, "max_fails", action=done_action)


async def project(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, call: Any = None, limit: int = 200,
                  max_d2: int = MAX_D2_PER_RUN, budget_s: float = RUN_BUDGET_S) -> list[signals.Signal]:
    """Chiếu các đơn vị loại `UNIT_KINDS` của một tổ chức. Một lượt mỗi tổ chức (`pg_try_advisory_xact_lock`), trần thời gian `budget_s`
    (phần còn lại để lượt sau — mới nhất trước). Bên gọi commit."""
    got = (await db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext('intel_units:' || :o))"), {"o": str(org_id)})).scalar_one()
    if not got:
        return []                                           # lượt khác đang chạy cho tổ chức này
    rows = (await db.execute(text(pending_sql("intel.units.done")),
                             {"o": org_id, "k": list(UNIT_KINDS), "since": now - timedelta(days=WINDOW_DAYS), "n": limit})).all()
    if not rows:
        return []
    started = time.monotonic()
    sets = await g.org_sets(db, org_id)
    out: list[signals.Signal] = []
    d2_left = [max_d2 if call is not None else 0]
    for k, u in enumerate(rows):
        if time.monotonic() - started > budget_s:
            log.warning("units: hết trần thời gian lượt chiếu, còn %d đơn vị để lượt sau", len(rows) - k)
            break
        ev = await evidence_of(db, u)
        if not ev:
            continue
        try:
            async with db.begin_nested():
                out.extend(await _project_one(db, org_id, u, ev, sets, now, call, d2_left))
        except signals.SignalError as e:
            log.warning("units: bỏ qua đơn vị %s: %s", u.id, type(e).__name__)
            await mark(db, org_id, u.id, type(e).__name__)
        except Exception as e:  # noqa: BLE001 — một đơn vị hỏng không chặn cả lô (savepoint đã hoàn tác phần của nó)
            await failed(db, org_id, u.id, e, done_action="intel.units.done")
    return out


async def _project_one(db: AsyncSession, org_id: uuid.UUID, u: Any, ev: list[Any], sets: g.OrgSets, now: datetime, call: Any,
                       d2_left: list[int]) -> list[signals.Signal]:
    if u.kind_hint == "su_kien_dip" and (await db.execute(text("""
            SELECT 1 FROM mem.fact_evidence fe JOIN mem.facts f ON f.id = fe.fact_id
            WHERE fe.meaning_unit_id = :u AND f.org_id = :o AND f.predicate = 'scheduled_event' LIMIT 1"""),
            {"u": u.id, "o": org_id})).first() is not None:
        await mark(db, org_id, u.id, "scheduled_event_fact")          # sự thật dịp/sự kiện là chủ (generic.project_events)
        return []
    p = await people(db, org_id, u, sets, [e.raw_event_id for e in ev])
    if u.kind_hint in ("xung_dot_khieu_nai", "quan_he_nong_nguoi"):
        routed = await _route_conflict(db, org_id, u, p, sets, ev, now)
        if routed is not None:
            await mark(db, org_id, u.id, "conflicts")
            return routed
    cust = (u.kind_hint in ("xung_dot_khieu_nai", "quan_he_nong_nguoi") and p.sender is not None and g.hard_customer(p.sender, sets))
    by_w = await g.who_person(db, org_id, _uuid(verified(u).get("status_by_person_id")))
    li = signal_input(u.kind_hint, u, p, sets, customer_of_owner=cust, status_by_owner_side=g.hard_owner_side(by_w, sets))
    if u.kind_hint == levels.FR:
        li = await _goods_impersonation(db, org_id, u, p, sets, li, [e.raw_event_id for e in ev])
    tk = f"g:{u.group_id}" if u.group_id else ""
    fx: dict[str, Any] = {"unit_id": str(u.id)}
    up = signals.UpsertIn(
        kind=u.kind_hint, title=TITLES.get(u.kind_hint, "Tín hiệu"), summary=g.safe(u.conclusion, 300) or None,
        detector=DETECTOR, detector_version=VERSION, level_input=li, evidence=unit_evidence(u, ev),
        case_key=_case_key(u.kind_hint, u, p, li, ev[0].raw_event_id), thread_key=tk or None, group_id=u.group_id,
        subject_type="group" if u.group_id else None, subject_id=u.group_id,
        person_ids=[x.person_id for x in [p.sender, *p.victim_who, *p.impersonated_who] if x is not None and x.person_id][:8],
        due_at=li.deadline_at, facts=fx)
    if not (d2_left[0] > 0 and u.kind_hint == levels.FR):
        return [await signals.upsert(db, org_id, up, now)]
    first = await signals.upsert(db, org_id, up, now) if li.hard_rule_hit else None   # luật cứng: đẩy NGAY, (d2) sau chỉ thêm cờ
    li2, res = await _d2(db, org_id, u, li, tk, now, call)                         # không luật cứng: (d2) TRƯỚC khi ghi
    if res is not None:
        d2_left[0] -= 1
        up = up.model_copy(update={"level_input": li2, "facts": {**fx, "d2": res}})
    return [await signals.upsert(db, org_id, up, now) if (first is None or res is not None) else first]


async def intel_units(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import budget, clock
    from gh.refinery.extract import router_caller

    sm = sessionmaker()
    router = ctx.get("model_router")
    n = errors = 0
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        call = router_caller(router, org, purpose=budget.PURPOSE[budget.KIND_SAFETY], tier="strong") if router is not None else None
        try:
            async with sm() as db:
                n += len(await project(db, org, clock.now(), call=call))
                await db.commit()
        except Exception as e:                               # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác; không nội dung tin
            log.error("intel_units lỗi ở một tổ chức: %s", type(e).__name__)
            errors += 1
    return {"projected": n, "errors": errors}


HOOKS: list[Any] = []
JOBS: list[CronJob] = [(intel_units, {"minute": set(range(60))})]
