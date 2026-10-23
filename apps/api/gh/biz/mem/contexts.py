# ruff: noqa: E501
"""Bối cảnh nhóm + chính sách theo bối cảnh (spec §4B.3) — MỘT nguồn: `mem.context_types.policy`.

Luật chung (code, không đổi theo bối cảnh): ngoài 6 cổng INT-19, mọi tín hiệu TỐI ĐA 🟠 (`MAX_LEVEL_OUTSIDE_GATE`); chính sách
chỉ HẠ hoặc TẮT, không bao giờ tự nâng; `lua_dao_dang_dien_ra` và `an_toan` luôn là cổng (không bối cảnh nào tắt được);
cổng thực thể (`ENTITY_GATES`) áp ở MỌI bối cảnh khi điều kiện thực thể đúng (CNS-097).

Nhóm chưa xác nhận (chỉ có nhãn `suggested` hoặc chưa có nhãn) và chat 1-1 dùng hàng "chưa xác nhận" (`UNCONFIRMED_POLICY`,
mặc định `khac`, đủ 6 cổng, không chấm điểm người, không "cơ hội" ngoài cổng). Hàng này nằm trong CODE vì bảng
`mem.context_types` chỉ seed đúng 8 bối cảnh theo spec — lệch nhỏ so với "một nguồn", đã ghi vào báo cáo L1.
Nhóm nhiều nhãn đã xác nhận: áp chính sách NGHIÊM NHẤT trong các nhãn (giao các cổng, hợp các loại tắt, trần thấp nhất).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

GATES = ("lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo",
         "co_hoi_lon_sap_troi", "su_co_cong_viec_gap")
ALWAYS_GATES = ("lua_dao_dang_dien_ra", "an_toan")
ENTITY_GATES = ("khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "su_co_cong_viec_gap")
CONTEXT_CODES = ("kinh_doanh", "cong_viec", "hoc_tap", "gia_dinh", "ban_be", "nhan_vien", "giao_thuong", "khac")
LEVELS = ("red", "amber", "grey")                 # cao → thấp
MAX_LEVEL_OUTSIDE_GATE = "amber"
LEVEL_VI = {"red": "đỏ", "amber": "cam", "grey": "xám"}
SOURCES = ("owner", "suggested")

#: Hàng "chưa xác nhận" (§4B.3): = khac, đủ 6 cổng (INT-19); co_hoi_lon_sap_troi chỉ khi khớp Hồ sơ owner_confirmed;
#: co_hoi_ket_noi ngoài cổng tắt; thi_truong_thong_tin, quan_he_nong_nguoi tắt; không console_pin; không chấm điểm người.
UNCONFIRMED_POLICY: dict[str, Any] = {
    "gates": list(GATES),
    "kinds_off": ["thi_truong_thong_tin", "quan_he_nong_nguoi"],
    "kinds_off_outside_gate": ["co_hoi_ket_noi"],
    "kind_caps": {},
    "key_relation_only_kinds": [],
    "profile_match_required_kinds": [],
    "gate_requires_profile_match": ["co_hoi_lon_sap_troi"],
    "customer_gates_only_for_owner_customer": False,
    "score_people": False, "console_pin_allowed": False, "trust_graph": False, "people_eval_privacy": None,
    "name_on_chat": True, "customer_word": False, "vocab": {}, "lunar_dates": False, "address_by_role": False,
    "large_group": None,
    "notes_vi": "Nhóm chưa được Sếp xác nhận bối cảnh: dùng mức thận trọng, không chấm điểm người; hệ thống sẽ hỏi Sếp.",
}


def _lvl(x: str) -> int:
    return LEVELS.index(x)


@dataclass(frozen=True)
class Policy:
    thread_key: str
    confirmed: bool
    contexts: tuple[str, ...]                    # nhãn đã xác nhận đang áp (rỗng = chưa xác nhận)
    gates: tuple[str, ...]
    kinds_off: frozenset[str]
    kinds_off_outside_gate: frozenset[str]
    kind_caps: dict[str, str]
    key_relation_only_kinds: frozenset[str]
    profile_match_required_kinds: frozenset[str]
    gate_requires_profile_match: frozenset[str]
    customer_gates_only_for_owner_customer: bool
    score_people: bool
    console_pin_allowed: bool
    trust_graph: bool
    people_eval_privacy: str | None
    name_on_chat: bool
    customer_word: bool
    vocab: dict[str, str]
    lunar_dates: bool
    address_by_role: bool
    large_group: dict[str, Any] | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def effective_context(self) -> str:
        return self.contexts[0] if self.contexts else "khac"

    def gate_allowed(self, gate: str) -> bool:
        return gate in self.gates or gate in ALWAYS_GATES

    def kind_enabled(self, kind: str, *, via_gate: bool = False) -> bool:
        if kind in self.kinds_off:
            return False
        return not (kind in self.kinds_off_outside_gate and not via_gate)

    def level_cap(self, kind: str, *, via_gate: bool = False) -> str:
        """Mức cao nhất cho phép: qua cổng ⇒ đỏ; ngoài cổng ⇒ tối đa cam, và thấp hơn nếu bối cảnh hạ trần loại đó."""
        if via_gate:
            return "red"
        cap = MAX_LEVEL_OUTSIDE_GATE
        c = self.kind_caps.get(kind)
        return c if c is not None and _lvl(c) > _lvl(cap) else cap


def _build(thread_key: str, contexts: tuple[str, ...], p: dict[str, Any]) -> Policy:
    gates = tuple(g for g in GATES if g in p.get("gates", GATES))
    return Policy(
        thread_key=thread_key, confirmed=bool(contexts), contexts=contexts, gates=gates,
        kinds_off=frozenset(p.get("kinds_off", [])), kinds_off_outside_gate=frozenset(p.get("kinds_off_outside_gate", [])),
        kind_caps={str(k): str(v) for k, v in (p.get("kind_caps") or {}).items()},
        key_relation_only_kinds=frozenset(p.get("key_relation_only_kinds", [])),
        profile_match_required_kinds=frozenset(p.get("profile_match_required_kinds", [])),
        gate_requires_profile_match=frozenset(p.get("gate_requires_profile_match", [])),
        customer_gates_only_for_owner_customer=bool(p.get("customer_gates_only_for_owner_customer", False)),
        score_people=bool(p.get("score_people", False)), console_pin_allowed=bool(p.get("console_pin_allowed", False)),
        trust_graph=bool(p.get("trust_graph", False)), people_eval_privacy=p.get("people_eval_privacy"),
        name_on_chat=bool(p.get("name_on_chat", True)), customer_word=bool(p.get("customer_word", False)),
        vocab={str(k): str(v) for k, v in (p.get("vocab") or {}).items()}, lunar_dates=bool(p.get("lunar_dates", False)),
        address_by_role=bool(p.get("address_by_role", False)), large_group=p.get("large_group"),
        notes=tuple(n for n in [p.get("notes_vi")] if n))


def merge_policies(thread_key: str, labelled: list[tuple[str, dict[str, Any]]]) -> Policy:
    """Nhiều nhãn đã xác nhận ⇒ NGHIÊM NHẤT: giao các cổng (luôn giữ 2 cổng bắt buộc), hợp các loại tắt, trần thấp nhất,
    mọi cờ "được phép" phải đúng ở TẤT CẢ nhãn."""
    codes = tuple(c for c, _ in labelled)
    ps = [p for _, p in labelled]
    if len(ps) == 1:
        return _build(thread_key, codes, ps[0])
    gates = [g for g in GATES if g in ALWAYS_GATES or all(g in p.get("gates", GATES) for p in ps)]
    caps: dict[str, str] = {}
    for p in ps:
        for k, v in (p.get("kind_caps") or {}).items():
            caps[k] = v if k not in caps or _lvl(v) > _lvl(caps[k]) else caps[k]

    def union(key: str) -> list[str]:
        return sorted({x for p in ps for x in p.get(key, [])})

    merged: dict[str, Any] = {
        "gates": gates, "kinds_off": union("kinds_off"), "kinds_off_outside_gate": union("kinds_off_outside_gate"),
        "kind_caps": caps, "key_relation_only_kinds": union("key_relation_only_kinds"),
        "profile_match_required_kinds": union("profile_match_required_kinds"),
        "gate_requires_profile_match": union("gate_requires_profile_match"),
        "customer_gates_only_for_owner_customer": any(p.get("customer_gates_only_for_owner_customer") for p in ps),
        "score_people": all(p.get("score_people") for p in ps), "console_pin_allowed": all(p.get("console_pin_allowed") for p in ps),
        "trust_graph": all(p.get("trust_graph") for p in ps),
        "people_eval_privacy": next((p["people_eval_privacy"] for p in ps if p.get("people_eval_privacy")), None),
        "name_on_chat": all(p.get("name_on_chat", True) for p in ps), "customer_word": all(p.get("customer_word") for p in ps),
        "vocab": {k: v for p in ps for k, v in (p.get("vocab") or {}).items()},
        "lunar_dates": any(p.get("lunar_dates") for p in ps), "address_by_role": any(p.get("address_by_role") for p in ps),
        "large_group": next((p["large_group"] for p in ps if p.get("large_group")), None),
        "notes_vi": " ".join(dict.fromkeys(p["notes_vi"] for p in ps if p.get("notes_vi"))),
    }
    return _build(thread_key, codes, merged)


def unconfirmed(thread_key: str) -> Policy:
    return _build(thread_key, (), UNCONFIRMED_POLICY)


def group_of(thread_key: str) -> uuid.UUID | None:
    """`g:<group_id>` ⇒ group_id; `dm:…` hoặc khác ⇒ None."""
    if thread_key.startswith("g:"):
        try:
            return uuid.UUID(thread_key[2:])
        except ValueError:
            return None
    return None


async def policy(db: AsyncSession, org_id: uuid.UUID, thread_key: str) -> Policy:
    """Chính sách của một luồng: nhóm có nhãn do Sếp xác nhận ⇒ chính sách (nghiêm nhất) của các nhãn đó; còn lại
    (nhãn mới gợi ý, chưa có nhãn, chat 1-1) ⇒ hàng "chưa xác nhận"."""
    gid = group_of(thread_key)
    if gid is None:
        return unconfirmed(thread_key)
    rows = (await db.execute(text("""
        SELECT g.context, t.policy FROM mem.group_contexts g JOIN mem.context_types t ON t.code = g.context
        WHERE g.org_id = :o AND g.group_id = :g AND g.source = 'owner'
        ORDER BY g.is_primary DESC, g.context"""), {"o": org_id, "g": gid})).all()
    if not rows:
        return unconfirmed(thread_key)
    return merge_policies(thread_key, [(r.context, dict(r.policy)) for r in rows])


def render_for_prompt(p: Policy) -> str:
    """Khối "Chính sách bối cảnh" trong prompt — do CODE render từ `policy`; kỹ năng v2.1 không chứa bảng trần riêng (CNS-068).
    Xác định (cùng đầu vào ⇒ cùng chữ)."""
    head = ("Chính sách bối cảnh (nhóm đã xác nhận: " + ", ".join(p.contexts) + ")") if p.confirmed else \
        "Chính sách bối cảnh (CHƯA xác nhận — dùng mức thận trọng, mặc định khac)"
    lines = [head,
             f"- Cổng đỏ được dùng: {', '.join(p.gates)}.",
             f"- Ngoài các cổng đó mọi tín hiệu tối đa mức {LEVEL_VI[MAX_LEVEL_OUTSIDE_GATE]}."]
    off = [g for g in GATES if g not in p.gates]
    if off:
        lines.append(f"- Cổng đỏ KHÔNG dùng ở bối cảnh này: {', '.join(off)}.")
    if p.gate_requires_profile_match:
        lines.append(f"- Cổng chỉ bật khi khớp Hồ sơ Sếp đã xác nhận: {', '.join(sorted(p.gate_requires_profile_match))}.")
    if p.customer_gates_only_for_owner_customer:
        lines.append("- Hai cổng liên quan khách (khach_che_cong_khai_doa_bo, loi_hua_khach_sap_vo) chỉ khi người đó là khách của Sếp.")
    if p.kinds_off:
        lines.append(f"- Loại tín hiệu TẮT: {', '.join(sorted(p.kinds_off))}.")
    if p.kinds_off_outside_gate:
        lines.append(f"- Loại tắt khi không qua cổng: {', '.join(sorted(p.kinds_off_outside_gate))}.")
    if p.profile_match_required_kinds:
        lines.append(f"- Loại chỉ khi khớp Hồ sơ Sếp đã xác nhận: {', '.join(sorted(p.profile_match_required_kinds))}.")
    if p.key_relation_only_kinds:
        lines.append(f"- Loại chỉ cho người trong quan hệ quan trọng của Sếp: {', '.join(sorted(p.key_relation_only_kinds))}.")
    if p.kind_caps:
        lines.append("- Trần mức theo loại: " + ", ".join(f"{k} ≤ {LEVEL_VI[v]}" for k, v in sorted(p.kind_caps.items())) + ".")
    if not p.score_people:
        lines.append("- KHÔNG chấm điểm người, KHÔNG đồ thị độ tin cậy, KHÔNG đánh giá người ở mức console_pin.")
    elif p.people_eval_privacy:
        lines.append(f"- Mọi đánh giá người: quyền riêng tư {p.people_eval_privacy}.")
    if not p.name_on_chat:
        lines.append("- Không nêu tên cá nhân trên kênh chat.")
    if p.vocab:
        lines.append("- Cách gọi: " + ", ".join(f"{k} → \"{v}\"" for k, v in sorted(p.vocab.items())) + ".")
    if p.lunar_dates:
        lines.append("- Dịp tính theo ngày (có âm lịch).")
    if p.address_by_role:
        lines.append("- Xưng hô theo vai.")
    if p.large_group:
        lines.append(f"- Nhóm trên {p.large_group.get('threshold')} người: link/quảng cáo chung chỉ ở mức "
                     f"{LEVEL_VI.get(str(p.large_group.get('link_ads_cap')), 'xám')} trừ khi nhắm tới Sếp hoặc khách của Sếp.")
    lines.extend(f"- {n}" for n in p.notes)
    return "\n".join(lines)


# ─── nhãn bối cảnh của nhóm (A6) ────────────────────────────────────────────

@dataclass(frozen=True)
class GroupContext:
    group_id: uuid.UUID
    context: str
    is_primary: bool
    source: str
    confidence: float | None
    confirmed_at: datetime | None


async def list_group_contexts(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID) -> list[GroupContext]:
    rows = (await db.execute(text("""
        SELECT group_id, context, is_primary, source, confidence, confirmed_at FROM mem.group_contexts
        WHERE org_id = :o AND group_id = :g ORDER BY is_primary DESC, source, context"""),
                             {"o": org_id, "g": group_id})).all()
    return [GroupContext(r.group_id, r.context, r.is_primary, r.source, None if r.confidence is None else float(r.confidence),
                         r.confirmed_at) for r in rows]


async def set_group_contexts(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID, labels: list[dict[str, Any]],
                             *, user_id: uuid.UUID) -> list[GroupContext]:
    """Sếp xác nhận nhãn (đa nhãn được; đúng một nhãn chính — không đặt thì lấy nhãn đầu). Thay toàn bộ nhãn `owner`
    của nhóm; nhãn `suggested` cùng mã bị thay bằng nhãn xác nhận, nhãn gợi ý khác giữ nguyên."""
    codes = [str(x["context"]) for x in labels]
    bad = [c for c in codes if c not in CONTEXT_CODES]
    if bad or not codes or len(set(codes)) != len(codes):
        raise ValueError(f"nhãn bối cảnh không hợp lệ: {bad or codes}")
    primary = next((str(x["context"]) for x in labels if x.get("is_primary")), codes[0])
    await db.execute(text("DELETE FROM mem.group_contexts WHERE org_id = :o AND group_id = :g AND source = 'owner'"),
                     {"o": org_id, "g": group_id})
    await db.execute(text("DELETE FROM mem.group_contexts WHERE org_id = :o AND group_id = :g AND context = ANY(:c)"),
                     {"o": org_id, "g": group_id, "c": codes})
    for c in codes:
        await db.execute(text("""INSERT INTO mem.group_contexts (org_id, group_id, context, is_primary, source, confidence,
                                   confirmed_by, confirmed_at, evidence)
                                 VALUES (:o, :g, :c, :p, 'owner', 1.0, :u, now(), '{}')"""),
                         {"o": org_id, "g": group_id, "c": c, "p": c == primary, "u": user_id})
    return await list_group_contexts(db, org_id, group_id)


async def suggest(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID, context: str, *, confidence: float,
                  evidence: dict[str, Any] | None = None) -> bool:
    """Hệ thống GỢI Ý nhãn (chưa xác nhận ⇒ vẫn dùng chính sách "chưa xác nhận"). Không đè nhãn Sếp đã đặt."""
    if context not in CONTEXT_CODES:
        raise ValueError(f"nhãn bối cảnh không hợp lệ: {context}")
    r = await db.execute(text("""
        INSERT INTO mem.group_contexts (org_id, group_id, context, is_primary, source, confidence, evidence)
        VALUES (:o, :g, :c, false, 'suggested', :cf, CAST(:e AS jsonb))
        ON CONFLICT (org_id, group_id, context) DO UPDATE SET confidence = EXCLUDED.confidence, evidence = EXCLUDED.evidence
        WHERE mem.group_contexts.source = 'suggested'"""),
        {"o": org_id, "g": group_id, "c": context, "cf": confidence, "e": orjson.dumps(evidence or {}).decode()})
    return bool(r.rowcount)  # type: ignore[attr-defined]
