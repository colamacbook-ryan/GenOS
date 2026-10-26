# ruff: noqa: E501
"""Chọn mức tín hiệu — NƠI DUY NHẤT ra `level`/`gate`/`delivery` (spec r6.6.1 §4.2 + §4.2.1, INT-19, Q13–Q16).

`compute(SignalIn, ProfileSnapshot, Policy, DeliveryState, now) -> Level` là hàm THUẦN (không đọc DB, không đọc đồng hồ): mọi số đếm
và cờ chế độ lúc chạy nằm ở `DeliveryState` (bên gọi `signals.relane` đọc trong cùng transaction giữ chỗ trần). `Policy` là kiểu L1
(`mem.contexts.policy`), `ProfileSnapshot` là kiểu L1 (`mem.profile_read.snapshot`, chỉ `owner_confirmed`) — không sửa hai kiểu đó.

Thứ tự áp đúng §4.2.1 (b): 0 chuẩn hoá → 1 trục → 2 sáu cổng (theo CỜ, không theo `kind`) + `m_nw` → 3 bối cảnh → 4 mute →
5 đóng/khắc phục → 6 🔴 + phân phối (e) → 7 không 🔴 (a, a′, a″, b, c, d, e).

Các hàm thuần đi kèm, một nơi: `owner_side` ("phía Sếp"), `case_anchor` (mốc vụ cổng 3), `case_key` (khoá vụ 10 loại),
`impact_base` (cơ sở impact theo loại, §4.2.1 (f)), `axes` (impact/urgency), `next_change_at` (mốc thời gian kế tiếp mà mức có thể
đổi — nền cho đường tính lại theo đồng hồ của `signals.reevaluate_due`).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from gh.biz.mem.contexts import Policy
from gh.biz.mem.profile_read import ProfileSnapshot

VN = timezone(timedelta(hours=7))

G1, G2, G3, G4, G5, G6 = ("lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo",
                          "co_hoi_lon_sap_troi", "su_co_cong_viec_gap")
GATES = (G1, G2, G3, G4, G5, G6)
BYPASS_GATES = (G1, G2)                      # `bypass_all`: không trần, không precision, bỏ giờ yên lặng, đẩy thật từ ngày cài
REGULAR_GATES = (G3, G4, G5, G6)
#: Cờ PHIÊN BẢN (Ryan quyết 2026-10-26, v0.1.76 "phát hành phần chắc"): False ⇒ bốn cổng thường KHÔNG bao giờ đẩy 🔴 ngay — chỉ lên đầu bản
#: tin như chạy bóng ("đáng lẽ báo ngay …"), kể cả qua 14 ngày / đạt M1 / Sếp chọn `ramp_gates=push`. Áp ở tầng `DeliveryState`
#: (`signals.delivery_state`), KHÔNG trong `compute` — hàm thuần và bộ vector giữ nguyên. Công thức ưu tiên chung thay ở v0.1.77.
REGULAR_PUSH_ENABLED = False
FR, SK = "rui_ro_lua_dao", "suc_khoe_an_toan"
KINDS = ("nghia_vu_loi_hua", "viec_han", "cau_hoi_cho_xu_ly", "su_kien_dip", SK, "xung_dot_khieu_nai", "quan_he_nong_nguoi",
         "co_hoi_ket_noi", FR, "thi_truong_thong_tin")
LEVELS = ("red", "amber", "grey", "off")
DELIVERIES = ("push_now", "digest_top", "digest_morning", "digest", "digest_low", "merge", "none")
REASONS = ("window_expired", "done_cannot_stop", "remediated", "remediation_ignored_hard_rule", "closed", "closed_by_staff",
           "low_confidence", "no_harm", "harm_template", "d2_disagree", "gate_not_met", "ctx_off", "muted", "mute_chat_ignored",
           "shadow", "capped", "precision", "quiet", "escalation", "merged", "agent_probe", "event_time_unparsed",
           "safety_wide_circle", "fraud_wide_circle", "impersonation_dm_agent", "impersonation_big_group", "name_match_unverified",
           "txn_day_reminder", "txn_pending", "big_opportunity_no_catalog")

#: Luật cứng (regex luật (a) trên span `verified`) ⇒ cờ kéo theo (§4.2.1 (b)0).
HARD_RULE_FLAGS: dict[str, tuple[str, ...]] = {
    "credentials_entered": ("credentials_entered",),
    "emergency_115_chay_cap_cuu": ("emergency",),
    "stk_khac_lich_su+money_request": ("money_request",),
}
#: Mã luật cứng mà `prefilter` (L2) phát ⇒ mã chuẩn của spec/bộ vector (một nghĩa, hai cách viết).
HARD_RULE_ALIASES = {"emergency": "emergency_115_chay_cap_cuu", "stk_changed_money_request": "stk_khac_lich_su+money_request"}


def hard_rules(s: SignalIn) -> list[str]:
    return [HARD_RULE_ALIASES.get(h, h) for h in s.hard_rule_hit]


HARM_TEMPLATE = {G1: "có thể mất tiền trong tài khoản (luật cứng: {h})", G2: "nguy hiểm tính mạng/tài sản (luật cứng: {h})"}
BRANCH_RANK = {"request": 1, "goods": 1, "just_done": 2, "credentials_entered": 3, "account_takeover": 4}
#: Giờ bản tin sáng (giờ VN) — mốc nhắc lại 🔴 một lần vào sáng ngày hẹn chuyển tiền (Ryan chốt D1, 2026-10-25).
TXN_REMIND_HHMM = "07:30"

H_SAN = 336.0            # chân trời sàn lừa đảo/an toàn: 14 ngày từ event_at (CNS-129)
N_NEAR_SMALL = 30        # nhóm gần theo cỡ
N_NEAR_CTX = 50          # nhóm gần theo nhãn gia_dinh/ban_be/nhan_vien (CNS-146)
N_KD_CUSTOMER = 50       # khách tại cơ sở (lõi cổng 2) ở nhóm kinh_doanh ≤ 50 (CNS-147)
N_WIDE_MAX = 200         # > 200 người: vòng rộng không có cổng
NEAR_CTX = ("gia_dinh", "ban_be", "nhan_vien")
CONF_HIGH, CONF_MEDIUM = 0.85, 0.70


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─── hợp đồng §4.2.1 (c) ──────────────────────────────────────────────────────

class Flags(_M):
    """13 cờ (§4F), không gồm `ongoing` (đã kiểm riêng ở `SignalIn.ongoing`)."""
    public: bool = False
    nudged: bool = False
    repeat_n: int | None = None
    explicit_urgent: bool = False
    needs_owner: bool = False
    blocks_work: bool = False
    is_template: bool = False
    conditional: bool = False
    said_will_leave: bool = False
    money_request: bool = False
    credentials_entered: bool = False
    account_takeover: bool = False
    emergency: bool = False


class D2(_M):
    verdict: Literal["agree", "disagree"] | None = None
    error: bool = False
    flags_confirmed: list[str] = Field(default_factory=list)
    harm_what: str | None = None


class Thread(_M):
    type: Literal["group", "dm_owner", "dm_agent"] = "group"
    size: int = 0
    #: False ⇒ cầu nối không báo cỡ nhóm (`core.groups.member_count` NULL): `size` là 0 giả. Gen quyết D-T3 (2026-10-26, fail-high cho lừa
    #: đảo/an toàn — thay P7 của T13): `owner_side` coi nhóm không rõ cỡ là nhóm gần và ≤ 50 khách lõi.
    size_known: bool = True
    owner_in_thread: bool = False
    agent_in_thread: bool = False
    customer_audience_n: int | None = Field(default=None, ge=0)  # r7 (c′): None = không đếm được


class PersonRel(_M):
    """Dữ kiện TRA CỨU theo người (không phán đoán). "Phía Sếp" chỉ tính ở `owner_side`."""
    ref: str
    is_owner: bool = False
    is_agent: bool = False
    owner_key_relation: bool = False
    customer: bool = False
    team: bool = False
    thread_member: bool = False
    kin_of_member: bool = False
    present_in_thread: bool = False


class CaseEvent(_M):
    at: datetime
    evt: Literal["complaint", "closure"]
    public: bool = False
    said_will_leave: bool = False


class CasePrior(_M):
    red_pushed: bool = False
    gate: str | None = None
    branch_rank: int = 0
    victim_refs: list[str] = Field(default_factory=list)
    payee_refs: list[str] = Field(default_factory=list)
    emergency: bool = False
    said_will_leave: bool = False
    #: Đã nhắc lại 🔴 sáng ngày hẹn chuyển tiền (hoặc lần đẩy đầu đã rơi vào sáng/trong ngày đó) — một vụ chỉ nhắc một lần (D1).
    txn_reminded: bool = False


class Promise(_M):
    to_customer: bool = False
    completed: bool = False
    obstacle_reported: bool = False
    condition_met: Literal["yes", "no", "none"] = "none"
    nudged_by_customer: bool = False                 # r7 (c′)
    fixed_occasion: bool = False                     # r7 (c′)


class Opportunity(_M):
    catalog_score: float | None = None
    resource_owner_confirmed: bool = False
    touches_value_limit: bool = False
    need_fields_n: int | None = Field(default=None, ge=0, le=4)  # r7 (c′): None = không có nguồn ⇒ không trần


class Work(_M):
    type: Literal["none", "deadline_today", "system_incident", "submission_today", "cutover_at_risk"] = "none"
    owner_work: bool = False
    done: bool = False
    late_signs: bool = False


class SignalIn(_M):
    """Đầu vào `compute` — bên gọi dựng từ tín hiệu đã kiểm + `mem` (bảng nguồn §4.2.1 (c)). Mặc định = trung tính."""
    kind: str | None = None                          # None = đường chỉ có luật mà luật (a) không gán loại (CNS-131)
    privacy: Literal["chat_ok", "console_pin"] = "chat_ok"
    gate_candidate: str | None = None
    msg_at: datetime
    event_at: datetime | None = None                 # None ⇒ = msg_at
    deadline_at: datetime | None = None
    ongoing: bool = False
    status: str = "dang_mo"
    flags: Flags = Field(default_factory=Flags)
    hard_rule_hit: list[str] = Field(default_factory=list)
    d2: D2 = Field(default_factory=D2)
    harm_what: str = ""
    thread: Thread = Field(default_factory=Thread)
    victims: list[PersonRel] = Field(default_factory=list)
    impersonated: list[PersonRel] = Field(default_factory=list)
    delivery_form: Literal["direct", "forwarded", "warning_story"] = "direct"
    customer_of_owner: bool = False
    event_time_unparsed: bool = False
    case_anchor_at: datetime | None = None
    case_history: list[CaseEvent] | None = None
    txn_at: datetime | None = None
    owner_resolved: bool = False
    closure: Literal["none", "staff", "customer", "owner"] = "none"
    remediation: Literal["none", "victim_or_owner_side", "other"] = "none"
    txn_state: Literal["none", "pending", "just_done", "done_cannot_stop"] = "none"
    promise: Promise = Field(default_factory=Promise)
    opportunity: Opportunity = Field(default_factory=Opportunity)
    work: Work = Field(default_factory=Work)
    impact_base: int = Field(default=1, ge=1, le=3)
    money_vnd: int | None = None
    money_basis: Literal["quoted", "db", "estimated"] | None = None
    irreversible: bool = False
    non_forwarded_sources_n: int = 1
    source_class: Literal["owner", "rule", "insider", "member", "outsider", "forwarded"] = "member"
    confidence_exist: float = 0.8
    confidence_content: float = 0.0
    mute: Literal["chat", "pin"] | None = None
    payee_refs: list[str] = Field(default_factory=list)
    case_prior: CasePrior | None = None
    owner_unanswered_h: float = 0
    in_work_hours: bool = True
    subject_entity_id: uuid.UUID | None = None       # đối tượng chính (so `owner_key_relation` của Hồ sơ)
    goal_priority1_match_valid: bool = False         # verify.py: `owner_relevance` khớp `owner_goal` ưu tiên 1 (kiểm được)
    #: Đặt hàng ghi nợ/công nợ hoặc đổi địa chỉ giao/người nhận hàng — CODE kiểm trên chữ đã kiểm (`fraud.goods_redirect`), không phải lời
    #: model. Cùng người bị mạo danh đã biết ⇒ nhánh "mạo danh đặt hàng / đổi địa chỉ giao" của cổng 1 (Ryan chốt D2, 2026-10-25).
    goods_risk: bool = False
    # ─── r7 (c′): chỉ thêm, mọi trường có mặc định; KHÔNG BAO GIỜ đọc ở Bước 0 (B2) ───
    complaint_stage: Literal["vent", "voice", "ultimatum", "exit_pending", "exit_done", "third_party"] | None = None
    asks_for_owner: bool = False
    public_venue: Literal["group", "social", "authority"] | None = None
    prior_threat_no_exit_n: int = Field(default=0, ge=0)
    staff_owned: bool = False
    staff_ref: str | None = None                     # chỉ để hiển thị "NV X đang lo"
    deadline_granularity: Literal["minute", "hour", "part_of_day", "day", "week"] | None = None
    same_customer_day_red: bool = False              # r7.1: chỉ đọc ở N10 D-T5 (G4)


class QuietHours(_M):
    start: str = "22:00"
    end: str = "07:00"


class WorkHours(_M):
    """r7 (c′): settings intel.work_start / intel.work_end."""
    start: str = "08:00"
    end: str = "17:30"


class DeliveryState(_M):
    """Kiểu của T1: số đếm + cờ chế độ lúc chạy. `relane` đọc trong cùng transaction giữ chỗ trần (§4.2.1 (e))."""
    intel_mode: Literal["live", "shadow"] = "live"
    red_cap_per_day: int = 3
    reds_pushed_today: int = 0
    days_since_install: float = 0.0
    ramp_early_enable: bool = False
    m1_passed_gates: list[str] = Field(default_factory=list)
    low_precision_gates: list[str] = Field(default_factory=list)
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    money_small_vnd: int = 5_000_000                 # settings intel.money_small_vnd (bảng trục §4.2)
    money_big_vnd: int = 50_000_000                  # settings intel.money_big_vnd
    work_hours: WorkHours = Field(default_factory=WorkHours)  # r7 (c′)


class Level(_M):
    level: Literal["red", "amber", "grey", "off"]
    gate: str | None = None
    candidate: str | None = None
    delivery: Literal["push_now", "digest_top", "digest_morning", "digest", "digest_low", "merge", "none"]
    would_push: bool = False
    capped: bool = False
    demoted: Literal["precision", "cap"] | None = None
    reasons: list[str] = Field(default_factory=list)
    #: Ngoài hợp đồng (không serialize): nhánh cổng 1 đã thoả (1 yêu cầu · 2 vừa chuyển · 3 lộ OTP · 4 chiếm tài khoản) — `relane`
    #: lưu vào `case_prior.branch_rank` để biết lần sau có LEO THANG không (§4.2.1 (e)0).
    _branch_rank: int = PrivateAttr(default=0)

    @property
    def branch_rank(self) -> int:
        return self._branch_rank


# ─── tiện ích thời gian ───────────────────────────────────────────────────────

def _aware(d: datetime) -> datetime:
    return d if d.tzinfo is not None else d.replace(tzinfo=UTC)


def _hrs(a: datetime, b: datetime) -> float:
    return (_aware(a) - _aware(b)).total_seconds() / 3600.0


def _age(now: datetime, t: datetime) -> float:
    """Tuổi = max(0, now − t): đồng hồ nền tảng lệch không làm tuổi âm (CNS-118)."""
    return max(0.0, _hrs(now, t))


def conf_level(x: float) -> str:
    return "high" if x >= CONF_HIGH else ("medium" if x >= CONF_MEDIUM else "low")


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def in_quiet(now: datetime, q: QuietHours) -> bool:
    t = _aware(now).astimezone(VN)
    m = t.hour * 60 + t.minute
    a, b = _minutes(q.start), _minutes(q.end)
    return (m >= a or m < b) if a > b else (a <= m < b)


def _vn_day_end(t: datetime) -> datetime:
    d = _aware(t).astimezone(VN)
    return d.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def txn_due_at(s: SignalIn) -> datetime | None:
    """Ngày hẹn chuyển tiền / hạn giao dịch của vụ lừa đảo còn chờ (D1): `deadline_at` hoặc `txn_at` mà tin nói tới một thời điểm SAU lúc
    gửi tin (`txn_at` = giờ tin là cách model điền "chưa rõ" ⇒ bỏ). Không chờ giao dịch (`txn_state` ≠ pending) ⇒ None. Lấy mốc xa nhất."""
    if s.txn_state != "pending":
        return None
    msg = _aware(s.msg_at)
    c = [_aware(x) for x in (s.deadline_at, s.txn_at) if x is not None and _aware(x) > msg]
    return max(c) if c else None


def txn_hold_until(s: SignalIn) -> datetime | None:
    """Giữ cửa sổ (i) của cổng 1 tới HẾT ngày hẹn chuyển (giờ VN), trần chân trời sàn `H_SAN` tính từ event_at (D1)."""
    due = txn_due_at(s)
    if due is None:
        return None
    return min(_vn_day_end(due), _aware(s.event_at or s.msg_at) + timedelta(hours=H_SAN))


def txn_remind_at(s: SignalIn) -> datetime | None:
    """Giờ bản tin sáng của ngày hẹn chuyển (giờ VN) — mốc nhắc lại 🔴 một lần (D1). Hẹn rơi trước giờ đó trong ngày ⇒ vẫn mốc giờ bản tin
    sáng (nhắc ngay khi tới bản tin, còn trong ngày giao dịch). Ngoài cửa sổ giữ ⇒ None."""
    due, hold = txn_due_at(s), txn_hold_until(s)
    if due is None or hold is None:
        return None
    d = due.astimezone(VN)
    at = d.replace(hour=_minutes(TXN_REMIND_HHMM) // 60, minute=_minutes(TXN_REMIND_HHMM) % 60, second=0, microsecond=0)
    return at if at < hold else None


def is_today_or_overnight(deadline_at: datetime | None, now: datetime) -> bool:
    """"Hôm nay" của cổng 4 và cổng 6 (§4.2.1 (a)): cùng ngày lịch VN với now (KỂ CẢ đã qua), hoặc 0 ≤ hạn − now < 12 giờ."""
    if deadline_at is None:
        return False
    dh = _hrs(deadline_at, now)
    return _aware(deadline_at).astimezone(VN).date() == _aware(now).astimezone(VN).date() or 0 <= dh < 12


# ─── mốc vụ cổng 3, phía Sếp ──────────────────────────────────────────────────

def case_anchor(history: list[CaseEvent] | None, event_at: datetime) -> datetime:
    """Mốc vụ cổng 3 (CNS-141, CNS-145): duyệt theo thời gian; tin đóng vụ xoá mốc; tin `public`/`said_will_leave` đầu tiên sau đó
    ⇒ mốc; tin LEO THANG (doạ bỏ tường minh đầu tiên sau lời chê chưa doạ bỏ) ⇒ mốc mới; chê lại không làm mới mốc; không có ⇒ event_at."""
    if not history:
        return event_at
    anchor: datetime | None = None
    seen_leave = False
    for h in sorted(history, key=lambda x: _aware(x.at)):
        if h.evt == "closure":
            anchor, seen_leave = None, False
        elif h.public or h.said_will_leave:
            if anchor is None:
                anchor, seen_leave = h.at, h.said_will_leave
            elif h.said_will_leave and not seen_leave:
                anchor, seen_leave = h.at, True
    return anchor or event_at


class OwnerSide(_M):
    fraud: bool                 # lừa đảo nhắm phía Sếp (🔴 được)
    fraud_wide: bool            # vòng rộng lừa đảo (nhóm ≤ 200 không gần) ⇒ 7a″
    safety: bool                # đối tượng an toàn thuộc lõi / vòng rộng ở nhóm gần
    safety_wide: bool           # vòng rộng an toàn ⇒ 7a″
    agent_probe: bool           # chỉ agent là nạn nhân / DM của agent
    imp_present: bool           # mạo danh Sếp/agent có mặt trong nhóm, không nạn nhân phía Sếp
    imp_unverified: bool        # trùng tên Sếp/agent, người thật không có trong luồng
    esc_refs: list[str]         # người tính cho leo thang (lõi hoặc thành viên nhóm gần)


def _is_core(p: PersonRel) -> bool:
    return p.is_owner or p.owner_key_relation or p.customer or p.team


def owner_side(s: SignalIn, policy: Policy) -> OwnerSide:
    """MỘT nơi tính "phía Sếp" (§4.2.1 (c)–(d); CNS-112/130/132/139/140/142/146–148). Lõi = Sếp, người thân Sếp, đội, khách
    (lừa đảo: mọi cỡ nhóm). Vòng rộng = thành viên / người nhà thành viên: 🔴 chỉ ở nhóm gần; nhóm ≤ 200 khác ⇒ 🟠 digest_top;
    > 200 ⇒ không cổng. Nhãn bối cảnh chỉ tính khi Sếp đã xác nhận (`Policy.confirmed`)."""
    th = s.thread
    contexts = policy.contexts if policy.confirmed else ()
    in_thread = th.owner_in_thread or th.agent_in_thread
    grp = th.type == "group" and in_thread
    small200 = grp and th.size <= N_WIDE_MAX
    # Gen quyết D-T3 (2026-10-26, fail-high): `owner_side` chỉ phục vụ cổng lừa đảo/an toàn ⇒ nhóm KHÔNG RÕ CỠ coi như nhóm gần / ≤ 50 khách
    # (thay P7 của T13). Cỡ đã biết vẫn theo ngưỡng.
    near = (th.type == "dm_owner" or (grp and (not th.size_known or th.size <= N_NEAR_SMALL))
            or (grp and th.size <= N_NEAR_CTX and any(c in NEAR_CTX for c in contexts)))
    victim_core = any(_is_core(p) for p in s.victims)
    member_victim = any(p.thread_member and not p.is_agent for p in s.victims)
    # Tin kể chuyện chỉ "nhắm" khi nêu đích danh; nhãn mất hiệu lực khi có luật cứng hoặc nạn nhân lõi (CNS-142).
    story = s.delivery_form == "warning_story" and not s.hard_rule_hit and not victim_core
    direct = s.delivery_form == "direct" or (s.delivery_form == "warning_story" and not story)
    reach = (direct and th.type == "group") or member_victim
    fraud = victim_core or (th.type == "dm_owner" and direct) or (near and reach)
    fraud_wide = (not fraud) and small200 and not near and reach
    imp_owner = [p for p in s.impersonated if p.is_owner or p.is_agent or _is_core(p)]
    others = not fraud and not fraud_wide and th.type == "group"
    imp_present = others and any(p.present_in_thread for p in imp_owner)
    imp_unverified = others and bool(imp_owner) and not imp_present
    agent_only = (bool(s.victims) and all(p.is_agent for p in s.victims)) or th.type == "dm_agent"
    kin_ok = s.source_class not in ("outsider", "forwarded")      # vai thân tộc không áp cho người lạ/chuyển tiếp (L-87)
    customer_core = (th.type == "dm_owner"
                     or (grp and "kinh_doanh" in contexts and (not th.size_known or th.size <= N_KD_CUSTOMER))
                     or (grp and "nhan_vien" in contexts and th.size <= N_WIDE_MAX))     # r6.6.1
    core_s = (any(p.is_owner or p.owner_key_relation or p.team for p in s.victims)
              or (customer_core and any(p.customer for p in s.victims)))
    wide_p = any(p.customer or p.thread_member or (p.kin_of_member and kin_ok) for p in s.victims)
    safety = core_s or (wide_p and near)
    safety_wide = (not safety) and wide_p and (small200 or th.type == "dm_agent")
    esc = sorted({p.ref for p in s.victims if _is_core(p) or (p.thread_member and near)})
    return OwnerSide(fraud=fraud, fraud_wide=fraud_wide, safety=safety, safety_wide=safety_wide,
                     agent_probe=agent_only and not fraud, imp_present=imp_present, imp_unverified=imp_unverified, esc_refs=esc)


# ─── trục ─────────────────────────────────────────────────────────────────────

def _flags(s: SignalIn) -> Flags:
    """Cờ cuối = model ∪ luật (a) (đã gộp ở bên gọi) ∪ cờ do `hard_rule_hit` ∪ `d2.flags_confirmed` khi (d2) không lỗi."""
    f = s.flags.model_copy()
    for h in hard_rules(s):
        for name in HARD_RULE_FLAGS.get(h, ()):
            setattr(f, name, True)
    if not s.d2.error:
        for name in s.d2.flags_confirmed:
            if name in Flags.model_fields and name != "repeat_n":
                setattr(f, name, True)
    return f


def _key_relation(s: SignalIn, profile: ProfileSnapshot) -> bool:
    return s.subject_entity_id is not None and s.subject_entity_id in profile.key_relation_entity_ids()


def axes(s: SignalIn, profile: ProfileSnapshot, state: DeliveryState, now: datetime, f: Flags | None = None) -> tuple[int, int]:
    """(impact, urgency) theo bảng trục §4.2: mỗi cờ cộng đúng một trục, +1, trần 3. Không còn đường ra 🔴 bằng tổng điểm."""
    f = f or _flags(s)
    imp = s.impact_base
    if s.money_vnd is not None:
        if s.money_basis in ("quoted", "db"):
            if s.money_vnd >= state.money_big_vnd:
                imp = max(imp, 3)
            elif s.money_vnd >= state.money_small_vnd:
                imp = max(imp, 2)
        elif s.money_basis == "estimated" and s.money_vnd >= state.money_small_vnd:
            imp = max(imp, 2)                                       # tiền ước tính tối đa 2
    cs = s.opportunity.catalog_score
    if cs is not None and cs >= 75:
        imp = max(imp, 3)
    # `irreversible` chỉ tính khi loại mất mát thuộc danh sách (CNS-143); không bao giờ cho co_hoi_ket_noi.
    irr_ok = (s.kind == SK or (s.kind == FR and s.txn_state in ("just_done", "done_cannot_stop"))
              or (s.kind in ("xung_dot_khieu_nai", "quan_he_nong_nguoi") and s.flags.said_will_leave))
    if s.irreversible and irr_ok:
        imp = 3
    imp += sum([f.public, (f.repeat_n or 0) >= 3, _key_relation(s, profile), s.goal_priority1_match_valid])
    imp = min(3, imp)
    if s.kind == "co_hoi_ket_noi" and s.opportunity.touches_value_limit:
        imp = min(imp, 1)
    if s.ongoing:
        urg = 3
    elif s.deadline_at is None:
        urg = 1
    else:
        dh = _hrs(s.deadline_at, now)
        urg = 3 if dh < 24 else (2 if dh <= 72 else 1)           # đã qua mà chưa xong ⇒ 3; "24–72" đóng hai đầu
    urg += sum([f.nudged, f.explicit_urgent, f.blocks_work and s.owner_unanswered_h > 2 and s.in_work_hours])
    return imp, min(3, urg)


def impact_base(kind: str, *, owner_side_targeted: bool = False, money_or_credentials: bool = False, emergency: bool = False,
                subject_owner_side: bool = False, important: bool = False) -> int:
    """Cơ sở impact theo loại (§4.2.1 (f)) — detector gọi; `compute` nhận giá trị đã tính ở `SignalIn.impact_base`.
    `important` = điều kiện cột "2" của loại đó (khách / owner_key_relation / việc của Sếp / khớp Nguồn lực …)."""
    if kind == FR:
        return 3 if owner_side_targeted and money_or_credentials else (2 if owner_side_targeted else 1)
    if kind == SK:
        return 3 if emergency else (2 if subject_owner_side else 1)
    return 2 if important else 1


# ─── compute ──────────────────────────────────────────────────────────────────

def _off_by_context(s: SignalIn, policy: Policy, key_rel: bool, profile_match: bool) -> bool:
    """Loại tắt theo bối cảnh, xét bằng điều kiện code (§4B.3, A5/A18/A19). Chỉ gọi khi KHÔNG thoả cổng và không phải vụ fail-high."""
    k = s.kind
    if k in policy.kinds_off:
        return True
    if k in policy.key_relation_only_kinds and not key_rel:
        return True
    if k in policy.profile_match_required_kinds and not profile_match:
        return True
    if k in policy.kinds_off_outside_gate:
        # Hàng "chưa xác nhận" (§4B.3): co_hoi_ket_noi KHỚP Hồ sơ owner_confirmed ⇒ không tắt (ngoài cổng ≤ 🟠).
        return policy.confirmed or not profile_match
    return False


def compute(signal: SignalIn, profile: ProfileSnapshot, policy: Policy, state: DeliveryState, now: datetime) -> Level:
    s, st = signal, state
    now = _aware(now)
    kind = s.kind
    reasons: list[str] = []

    def L(level: str, gate: str | None = None, delivery: str | None = None, **kw: Any) -> Level:
        d = delivery or {"red": "push_now", "amber": "digest", "grey": "digest_low", "off": "none"}[level]
        out = Level(level=level, gate=gate, delivery=d, reasons=list(dict.fromkeys(reasons)), **kw)  # type: ignore[arg-type]
        out._branch_rank = rank_box[0]
        return out

    rank_box = [0]

    # ---- 0. chuẩn hoá (CNS-110, 111, 135) ----
    hard = hard_rules(s)
    f = _flags(s)
    exist = s.confidence_exist
    if hard:
        exist = 1.0                                          # luật cứng: 1,00 bất kể (d2); (d2) không đồng ý chỉ ghi lại
        if s.d2.verdict == "disagree" and not s.d2.error:
            reasons.append("d2_disagree")
    elif s.d2.error:
        pass                                                 # fail-high: giữ giá trị bên gọi = max(luật, model chính)
    elif s.d2.verdict == "agree":
        exist = 1.0
    elif s.d2.verdict == "disagree":
        exist = 0.5
        reasons.append("d2_disagree")
    msg_at = _aware(s.msg_at)
    event_at = _aware(s.event_at or s.msg_at)
    dl = _aware(s.deadline_at) if s.deadline_at else None
    age = _age(now, msg_at)
    ev_age = _age(now, event_at)
    dh = _hrs(dl, now) if dl else None
    today = is_today_or_overnight(dl, now)
    pm, op, wk = s.promise, s.opportunity, s.work
    os_ = owner_side(s, policy)

    # ---- 1. trục ----
    imp, _urg = axes(s, profile, st, now, f)
    urg = _urg
    key_rel = _key_relation(s, profile)
    profile_match = op.resource_owner_confirmed or s.goal_priority1_match_valid
    cs = op.catalog_score

    # ---- 2. sáu cổng — theo CỜ (CNS-111); window=False ⇒ "mọi điều kiện trừ cửa sổ" cho m_nw ----
    txn_age = min(ev_age, _age(now, _aware(s.txn_at))) if s.txn_at else ev_age            # CNS-142
    anchor = s.case_anchor_at or (case_anchor(s.case_history, event_at) if s.case_history else None)
    anchor_age = _age(now, _aware(anchor)) if anchor else ev_age
    hold = txn_hold_until(s)
    held = hold is not None and now < hold                   # D1: hẹn chuyển tiền còn chưa qua hết ngày ⇒ cửa sổ (i) chưa hết
    # D2: người bị mạo danh là người ĐÃ BIẾT (khách/đối tác…), không phải Sếp/agent — mạo danh Sếp/agent đi đường (iii)/7e như cũ.
    imp_known = any(not (p.is_owner or p.is_agent) for p in s.impersonated)

    def gates(window: bool = True, wide: bool = False) -> tuple[str | None, int | None]:
        if not window and ev_age >= H_SAN:
            return None, None
        consider1 = kind == FR or f.credentials_entered or f.account_takeover or "stk_khac_lich_su+money_request" in hard
        if consider1 and (os_.fraud_wide if wide else os_.fraud):
            fresh48 = age < 48 or s.ongoing or not window
            fresh168 = age < 168 or s.ongoing or not window
            moved = s.txn_state in ("just_done", "done_cannot_stop")
            just_done = moved and (txn_age < 24 or not window)
            br: list[str] = []
            if f.money_request and s.txn_state == "pending" and (fresh48 or held):
                br.append("request")
            if s.goods_risk and imp_known and fresh48:
                br.append("goods")
            if f.money_request and just_done and fresh48:
                br.append("just_done")
            if f.credentials_entered and fresh168:
                br.append("credentials_entered")
            imp_owner = (any((p.is_owner or p.is_agent) and p.present_in_thread for p in s.impersonated)
                         or bool(s.impersonated and os_.fraud))
            if f.account_takeover and fresh48 and (f.money_request or bool(hard) or imp_owner):     # chống nhiễu "nick mới" (L-87)
                br.append("account_takeover")
            if br:
                return G1, max(BRANCH_RANK[b] for b in br)
        consider2 = kind == SK or f.emergency
        subject = os_.safety_wide if wide else os_.safety
        if (consider2 and f.emergency and (s.ongoing or ev_age < 24 or not window) and subject
                and s.non_forwarded_sources_n >= 1):
            return G2, None
        if (kind in ("xung_dot_khieu_nai", "quan_he_nong_nguoi") and s.customer_of_owner and (f.public or f.said_will_leave)
                and (anchor_age < 24 or not window)):
            return G3, None
        if (kind == "nghia_vu_loi_hua" and pm.to_customer and not pm.completed
                and (not f.conditional or pm.condition_met == "yes") and today
                and (pm.obstacle_reported or (dh is not None and dh <= 4))):
            return G4, None
        if (kind == "co_hoi_ket_noi" and (op.resource_owner_confirmed or (cs is not None and cs >= 75))
                and not op.touches_value_limit and imp == 3
                and ((dh is not None and 0 <= dh < 48) or (f.explicit_urgent and (dh is None or dh >= 0)))):
            return G5, None
        g6_kind = (kind in ("viec_han", "cau_hoi_cho_xu_ly") or (kind == "nghia_vu_loi_hua" and not pm.to_customer)
                   or wk.type == "system_incident")
        within24 = dh is not None and 0 <= dh < 24
        if g6_kind and wk.owner_work and not wk.done and (
                (wk.type == "deadline_today" and wk.late_signs and today) or wk.type == "system_incident"
                or (wk.type == "submission_today" and today)
                or (wk.type == "cutover_at_risk" and wk.late_signs and within24)) and (f.blocks_work or today or within24):
            return G6, None
        return None, None

    m, rank = gates(True)
    rank_box[0] = rank or 0
    m_nw, _ = gates(False)
    m_wide = gates(True, wide=True)[0] if (not m and (os_.safety_wide or os_.fraud_wide)) else None
    if m_wide not in BYPASS_GATES:
        m_wide = None
    if m in (G1, G2, G3):
        m_nw = m                                             # CNS-133
    fail_high = m_nw in BYPASS_GATES                         # "vụ fail-high" — một định nghĩa (nguyên lý mục 5)
    if s.event_time_unparsed:
        reasons.append("event_time_unparsed")
    harm = s.harm_what.strip() or (s.d2.harm_what or "").strip()
    if not harm and hard and (m in BYPASS_GATES or m_nw in BYPASS_GATES):
        harm = HARM_TEMPLATE[m if m in BYPASS_GATES else m_nw].format(h=hard[0])  # type: ignore[index]
        reasons.append("harm_template")

    # ---- 3. bối cảnh cho cổng (chỉ co_hoi_lon_sap_troi bị tắt) ----
    ctx_ok = True
    if m is not None and not policy.gate_allowed(m):
        ctx_ok = False
    if m is not None and m in policy.gate_requires_profile_match and not profile_match:
        ctx_ok = False
    mc = m if ctx_ok else None
    axis = exist if (kind in (FR, SK) or m in BYPASS_GATES or m_nw in BYPASS_GATES) else s.confidence_content
    low = conf_level(axis) == "low"

    # ---- 4. mute (CNS-123) ----
    if s.mute:
        if fail_high and s.mute == "chat":
            reasons.append("mute_chat_ignored")
        elif fail_high:
            reasons.append("muted")
            return L("grey", delivery="digest_low")          # mute qua Console + PIN: sàn ⚪ dòng cụm
        else:
            reasons.append("muted")
            return L("grey", delivery="none")

    # ---- 5. đóng việc / khắc phục (A3, A16; CNS-109, CNS-120) ----
    if s.owner_resolved:
        reasons.append("closed")
        return L("grey", delivery="none")
    if fail_high or kind in (FR, SK):
        # status_hint của model bị bỏ qua; chỉ tin khắc phục của nạn nhân / người phía Sếp (code kiểm người gửi) được tính.
        if fail_high and s.remediation == "victim_or_owner_side":
            if hard:
                reasons.append("remediation_ignored_hard_rule")
            else:
                reasons.append("remediated")
                return L("amber", candidate=m_nw, delivery="digest_top")
    else:
        if s.status in ("da_xu_ly", "da_giu"):
            reasons.append("closed")
            return L("grey", delivery="none")
        if kind in ("xung_dot_khieu_nai", "quan_he_nong_nguoi"):
            if s.closure in ("owner", "customer"):
                reasons.append("closed")
                return L("grey", delivery="none")
            if s.closure == "staff":
                reasons.append("closed_by_staff")
                return L("grey", delivery="digest_low")

    # ---- 6. 🔴 + phân phối (e) ----
    if mc and not low and harm:
        g = mc
        cp = s.case_prior
        if cp is not None and cp.red_pushed:
            new_refs = bool(set(os_.esc_refs) - set(cp.victim_refs))
            if g == G1:
                esc = (rank or 0) > cp.branch_rank or new_refs        # STK/domain mới một mình KHÔNG đẩy lại (L-87)
                rem = txn_remind_at(s)
                if not esc and not cp.txn_reminded and rem is not None and rem <= now and held:
                    # D1: nhắc lại 🔴 MỘT lần sáng ngày hẹn chuyển (bypass như mọi 🔴 cổng 1; một vụ một lần ⇒ không vượt trần vô hạn).
                    reasons.append("txn_day_reminder")
                    return L("red", g, "push_now")
            elif g == G2:
                esc = (f.emergency and not cp.emergency) or new_refs
            elif g == G3:
                esc = f.said_will_leave and not cp.said_will_leave
            else:
                esc = False
            if not esc:
                reasons.append("merged")
                return L("red", g, "merge")
            reasons.append("escalation")
        if g in BYPASS_GATES:
            return L("red", g, "push_now")                   # mọi intel_mode (Q13)
        live = st.intel_mode == "live" and (st.ramp_early_enable or (g in st.m1_passed_gates and st.days_since_install >= 14))
        if not live:
            reasons.append("shadow")
            return L("red", g, "digest_top", would_push=True)
        if g in st.low_precision_gates:
            reasons.append("precision")
            return L("amber", g, "digest_top", demoted="precision")
        if st.reds_pushed_today >= st.red_cap_per_day:
            reasons.append("capped")
            return L("amber", g, "digest_top", capped=True, demoted="cap")
        if in_quiet(now, st.quiet_hours):
            reasons.append("quiet")
            return L("red", g, "digest_morning")
        return L("red", g, "push_now")

    # ---- 7. không 🔴: dòng đầu tiên khớp ----
    if mc:                                                   # 7a
        reasons.append("low_confidence" if low else "no_harm")
        return L("amber", candidate=mc, delivery="digest_top" if mc in (G1, G2, G3) else "digest")
    if m_wide:                                               # 7a″
        reasons.append("safety_wide_circle" if m_wide == G2 else "fraud_wide_circle")
        return L("amber", candidate=m_wide, delivery="digest_top")
    if m_nw in (G1, G2, G3):                                 # 7a′ (Q15 cho cổng 3)
        reasons.append("window_expired")
        if m_nw == G1 and s.txn_state in ("just_done", "done_cannot_stop") and f.money_request:
            reasons.append("done_cannot_stop")
        if m_nw == G1 and s.txn_state == "pending" and f.money_request and txn_due_at(s) is None:
            reasons.append("txn_pending")                    # D1: không biết ngày chuyển ⇒ bản tin không ghi "đã quá thời điểm"
        return L("amber", candidate=m_nw, delivery="digest_top")
    # D5 (Ryan chốt 2026-10-25): cơ hội tiền lớn có hạn gần / gấp tường minh mà chưa khớp Nguồn lực ở nhóm CHƯA xác nhận ⇒ không tắt
    # hẳn, tối thiểu 🟠 (bản tin). Nhóm Sếp đã xác nhận bối cảnh tắt loại này (vd gia_dinh) vẫn tắt; 🔴 cổng 5 vẫn cần catalog như cũ.
    # "Lớn" ở đây = TIỀN lớn theo ngưỡng cổng 5 (`money_big_vnd`, tiền báo/đã có trong dữ liệu — tiền ước tính một mình không đủ), không phải
    # impact 3 do điểm catalog (catalog chưa xác nhận ở nhóm chưa xác nhận vẫn không đủ — A19).
    big_money = s.money_vnd is not None and s.money_basis in ("quoted", "db") and s.money_vnd >= st.money_big_vnd
    big_opp = (kind == "co_hoi_ket_noi" and not policy.confirmed and big_money and not op.touches_value_limit
               and ((dh is not None and 0 <= dh < 48) or (f.explicit_urgent and (dh is None or dh >= 0))))
    if big_opp and not low:
        reasons.append("big_opportunity_no_catalog")
        return L("amber", candidate=G5)
    if _off_by_context(s, policy, key_rel, profile_match):   # 7b
        reasons.append("ctx_off")
        return L("off")
    cand = m or s.gate_candidate
    if low and kind not in (FR, SK):                         # 7c
        reasons.append("low_confidence")
        return L("grey")
    if cand:                                                 # 7d
        reasons.append("gate_not_met")
        return L("amber", candidate=cand)
    amber = (imp >= 2 and urg >= 2) or f.needs_owner         # 7e
    if kind == FR and (os_.fraud or os_.fraud_wide):
        amber = True
    if kind == FR and os_.agent_probe:
        reasons.append("agent_probe")
        if s.impersonated:
            reasons.append("impersonation_dm_agent")
            return L("amber", delivery="digest_top")
        amber = True
    if kind == FR and os_.imp_present:
        reasons.append("impersonation_big_group")
        return L("amber", delivery="digest_top")
    if kind == FR and os_.imp_unverified:
        reasons.append("name_match_unverified")
        amber = True
    if kind == "xung_dot_khieu_nai" and (f.public or (f.repeat_n or 0) >= 3):
        amber = True
    level = "amber" if amber else "grey"
    if level == "amber" and kind is not None and policy.level_cap(kind) == "grey":
        level = "grey"                                       # trần bối cảnh chỉ hạ (vd hoc_tap: thi_truong_thong_tin ≤ ⚪)
    if fail_high and level in ("grey", "off"):
        raise AssertionError("vi phạm sàn lừa đảo/an toàn")  # bất biến test_levels_floor_* — không bao giờ xảy ra
    return L(level)


# ─── khoá vụ (§4.2 bảng case_key 10 loại) ─────────────────────────────────────

def _day(t: datetime) -> str:
    return _aware(t).astimezone(VN).date().isoformat()


#: Cửa sổ TRƯỢT của vụ theo loại khoá (T12, gỡ Nợ #83): khoá vụ KHÔNG gắn ngày/bucket cố định (hai tin của cùng kẻ lừa vắt qua nửa đêm
#: hoặc qua biên bucket không còn bị cắt đôi). `signals.upsert` kiểm cửa sổ bằng mốc tin mới nhất của vụ đang mở; quá cửa sổ ⇒ vụ mới
#: với hậu tố tuần tự `|#n`. None = không giới hạn thời gian (một tin gốc, một lời hứa, một nhu cầu, một chuỗi trả lời…).
CASE_WINDOWS_H = {"stk": 7 * 24, "imp": 24, "domain": 24, "actor": 24, "simhash": 7 * 24, "sk": 24, "case3": 72, "quan_he": 14 * 24}
_SEQ_SUFFIX = "|#"


def base_case_key(ck: str) -> str:
    """Bỏ hậu tố tuần tự `|#n` (vụ thứ n của cùng khoá)."""
    head, sep, tail = ck.rpartition(_SEQ_SUFFIX)
    return head if sep and tail.isdigit() else ck


def case_window(ck: str | None) -> timedelta | None:
    """Cửa sổ trượt của một khoá vụ (từ cấu trúc khoá do `case_key` sinh — một nơi)."""
    if not ck or ":" not in ck:
        return None
    kind, part = base_case_key(ck).split(":", 1)
    tok = part.split("|")
    if kind == "case3":
        h = CASE_WINDOWS_H["case3"] if len(tok) == 1 else None          # có chuỗi trả lời ⇒ vụ theo chuỗi, không theo thời gian
    elif kind == FR and tok[0] in ("stk", "imp", "domain"):
        h = CASE_WINDOWS_H[tok[0]]
    elif kind == FR and tok[0] == "raw" and len(tok) > 1 and tok[1].startswith("actor:"):
        h = CASE_WINDOWS_H["actor"]
    elif kind == "thi_truong_thong_tin" and tok[0] == "simhash":
        h = CASE_WINDOWS_H["simhash"]
    elif kind == SK and len(tok) == 1 and tok[0] not in ("raw", "anon"):
        h = CASE_WINDOWS_H["sk"]
    elif kind == "quan_he_nong_nguoi" and len(tok) == 1 and tok[0] not in ("raw", "anon"):
        h = CASE_WINDOWS_H["quan_he"]
    else:
        h = None
    return timedelta(hours=h) if h else None


def case_key(kind: str, *, at: datetime, thread_key: str | None = None, raw_event_id: str | None = None,
             promise_id: str | None = None, deadline_fact_id: str | None = None, task_title_folded: str | None = None,
             deadline_at: datetime | None = None, entity_id: str | None = None, event_date: str | None = None,
             customer_entity_id: str | None = None, reply_chain_id: str | None = None, demand_id: str | None = None,
             payee_digest: str | None = None, group_id: str | None = None, impersonated_entity_id: str | None = None,
             domain: str | None = None, simhash: str | None = None) -> str:
    """Khoá vụ — NƠI DUY NHẤT (khoá chống trùng `(case_key, kind)`, "một vụ một 🔴"). `event_ref` của model chỉ là gợi ý; code quyết.
    Trả chuỗi `<kind>:<phần khoá>`; không đủ dữ kiện ⇒ rơi về `raw_event_id` gốc (một sự việc, một tín hiệu). Khoá KHÔNG chứa thời gian
    (trừ ngày của chính sự việc: hạn việc, ngày dịp); cửa sổ vụ là cửa sổ TRƯỢT — `case_window`, kiểm ở `signals.upsert`."""
    def k(*parts: object) -> str:
        return kind + ":" + "|".join(str(p) for p in parts)

    if kind == "nghia_vu_loi_hua" and promise_id:
        return k("promise", promise_id)
    if kind == "viec_han":
        if deadline_fact_id:
            return k("fact", deadline_fact_id)
        if thread_key and task_title_folded and deadline_at:
            return k(thread_key, task_title_folded, _day(deadline_at))
    if kind == "su_kien_dip" and entity_id and event_date:
        return k(entity_id, event_date)
    if kind == SK and entity_id:
        return k(entity_id)
    if kind in ("xung_dot_khieu_nai", "quan_he_nong_nguoi") and customer_entity_id:
        # Vụ cổng 3 (CNS-122): (khách, chuỗi trả lời | cửa sổ trượt 72 giờ — `case_window`). Bản quan_he_nong_nguoi của cùng lời doạ bỏ
        # dùng CHUNG khoá vụ.
        return "case3:" + "|".join((customer_entity_id, *([reply_chain_id] if reply_chain_id else [])))
    if kind == "quan_he_nong_nguoi" and entity_id:
        return k(entity_id)
    if kind == "co_hoi_ket_noi" and demand_id:
        return k("demand", demand_id)
    if kind == FR:
        if payee_digest:
            return k("stk", payee_digest, group_id or "-")
        if impersonated_entity_id:
            return k("imp", impersonated_entity_id)
        if domain:
            return k("domain", domain.lower())
    if kind == "thi_truong_thong_tin" and simhash:
        return k("simhash", simhash)
    if raw_event_id:
        return k("raw", raw_event_id)
    # Không có dữ kiện nào: băm theo thời điểm + luồng (không bao giờ gộp hai sự việc khác luồng).
    return k("anon", hashlib.sha256(f"{thread_key}|{_aware(at).isoformat()}".encode()).hexdigest()[:16])


# ─── đồng hồ: mốc kế tiếp mức có thể đổi ──────────────────────────────────────

def next_change_at(s: SignalIn, now: datetime) -> datetime | None:
    """Thời điểm SAU `now` gần nhất mà kết quả `compute` có thể đổi chỉ vì đồng hồ trôi (không có tin mới): hạn tới gần (trục khẩn
    72/24 giờ, cổng 5 48 giờ, cổng 4 "≤ 4 giờ", "qua đêm < 12 giờ", sang ngày VN), cửa sổ tuổi (24 giờ cổng 2/3 — Q15, 48 giờ /
    7 ngày cổng 1, chân trời sàn 14 ngày). `signals` lưu giá trị này vào `next_eval_at`; job `intel_reeval` tính lại khi tới mốc."""
    now = _aware(now)
    cands: list[datetime] = []
    ev = _aware(s.event_at or s.msg_at)
    msg = _aware(s.msg_at)
    for base, hours in ((msg, 48), (msg, 168), (ev, 24), (ev, H_SAN)):
        cands.append(base + timedelta(hours=hours))
    if s.txn_at:
        cands.append(_aware(s.txn_at) + timedelta(hours=24))
    hold, rem = txn_hold_until(s), txn_remind_at(s)
    cands.extend(x for x in (hold, rem) if x is not None)
    anchor = s.case_anchor_at or (case_anchor(s.case_history, ev) if s.case_history else None)
    if anchor:
        cands.append(_aware(anchor) + timedelta(hours=24))
    if s.deadline_at:
        dl = _aware(s.deadline_at)
        for h in (72, 48, 24, 12, 4, 0):
            cands.append(dl - timedelta(hours=h) + timedelta(seconds=1))   # biên nửa mở/đóng: tính lại ngay SAU biên
        d = dl.astimezone(VN)
        start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        cands.extend([start, start + timedelta(days=1)])             # "hôm nay" bắt đầu / hết
    fut = [c for c in cands if c > now]
    return min(fut) if fut else None
