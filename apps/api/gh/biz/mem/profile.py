# ruff: noqa: E501
"""Hồ sơ Sếp (owner model, spec §4C): ghi, sửa, xoá, đề xuất và xác nhận. L1 (`profile_read.snapshot`) chỉ ĐỌC.

Nguyên tắc cứng (đều kiểm bằng code, không do model tự khai):
- MỌI lần ghi đi qua `facts.assert_fact` (thêm) hoặc `facts.correct` (sửa/xoá/xác nhận). Module này không có câu INSERT nào vào `mem.facts`.
- Chỉ Sếp tạo ra `owner_confirmed` (cần `user_id` của Sếp + bằng chứng `owner_edit` trỏ Nhật ký). Đề xuất của trợ lý/job luôn `proposed`,
  `confirmed_by` rỗng; không có đường nào trong `propose()` để một đề xuất tự thành `owner_confirmed`.
- Đề xuất chỉ được sinh từ tin do CHÍNH Sếp viết (kiểm bằng định danh người gửi trong `raw.events`, không theo tên), không phải tin
  chuyển tiếp/trích lời — chữ của người ngoài không bao giờ thành mục Hồ sơ hay chỉ thị.
- Ngoại lệ phòng thủ (CNS-073/106): `owner_report_pref` / `owner_rhythm` chạm lừa đảo hoặc sức khoẻ-an toàn không ghi được từ chat
  (`via='chat'` ⇒ `console_required`); từ Console cần PIN (route kiểm).
- Chữ của một mục được rửa (NFC, bỏ ký tự điều khiển, gộp khoảng trắng, ≤ 280) và không được chứa mã giả `[f_<id>]` để không giả mạo dòng
  Hồ sơ trong prompt (`ProfileSnapshot.render_for_prompt`).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import entities, facts, predicates, profile_read
from gh.textnorm import strip_accents

SECTION_PREDICATE: dict[str, str] = {
    "goals": "owner_goal", "resources": "owner_resource", "key_relations": "owner_key_relation",
    "values_limits": "owner_value_limit", "rhythm": "owner_rhythm", "report_prefs": "owner_report_pref"}
PREDICATE_SECTION = {v: k for k, v in SECTION_PREDICATE.items()}
SECTIONS = profile_read.SECTIONS
MAX_TEXT = 280
PROPOSAL_EXTRACTOR = "profile-lexicon/1"
FWD_KEYS = ("forwarded", "is_forwarded", "forward")
_FACT_ID_FORGERY = re.compile(r"\[?\s*f_[0-9a-f]{8}-?[0-9a-f]{4}", re.IGNORECASE)
_CTRL = re.compile(r"[\u0000-\u001f\u007f-\u009f  ​-‏‪-‮⁦-⁩]")

#: Chạm lừa đảo / sức khoẻ-an toàn (khớp trên chữ bỏ dấu, chữ thường) — chỉ để quyết định đường ghi (Console + PIN), không để chấm điểm.
DEFENSIVE_LEXICON = (
    "lua dao", "scam", "gia mao", "chiem doat", "chuyen khoan", "doi stk", "doi so tai khoan", "otp", "mat khau",
    "suc khoe", "cap cuu", "benh vien", "tai nan", "an toan", "nguy hiem", "dot quy", "ung thu", "cong an")
QUIET_LEXICON = ("yen lang", "im lang", "khong lam phien", "dung bao", "khong bao", "tat thong bao", "ngu")


class ProfileError(Exception):
    """Lỗi nghiệp vụ Hồ sơ: `code` máy đọc, `message` tiếng Việt thân thiện, `detail` = "Chi tiết kỹ thuật"."""

    def __init__(self, code: str, message: str, *, status: int = 422, detail: str | None = None,
                 decision_kind: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.detail, self.decision_kind = code, message, status, detail, decision_kind


@dataclass(frozen=True)
class ItemResult:
    fact: facts.Fact
    outcome: str                 # added | existing | confirmed | edited | removed | rejected | proposed
    replaced_fact_id: uuid.UUID | None = None


# ─── chữ & phân loại ────────────────────────────────────────────────────────

def clean_text(raw: str | None) -> str:
    s = unicodedata.normalize("NFC", raw or "")
    s = _CTRL.sub(" ", s)
    s = " ".join(s.split())
    if not s:
        raise ProfileError("empty_text", "Nội dung mục Hồ sơ đang trống", detail="text rỗng sau khi làm sạch")
    if len(s) > MAX_TEXT:
        raise ProfileError("text_too_long", f"Mục Hồ sơ dài quá {MAX_TEXT} ký tự — rút gọn lại giúp em", detail=f"len={len(s)}")
    if _FACT_ID_FORGERY.search(s):
        raise ProfileError("text_forbidden", "Nội dung có chuỗi mã nội bộ không được phép", detail="mẫu [f_<id>] bị chặn")
    return s


def _fold(s: str) -> str:
    return strip_accents(" ".join(s.split())).lower()


def is_defensive(section: str, value_text: str) -> bool:
    """Mục `report_prefs`/`rhythm` có nội dung chạm lừa đảo hoặc sức khoẻ-an toàn ⇒ chỉ ghi qua Console + PIN."""
    if section not in ("report_prefs", "rhythm"):
        return False
    f = _fold(value_text)
    return any(w in f for w in DEFENSIVE_LEXICON)


def is_quiet_hours(section: str, value_text: str) -> bool:
    """Đổi giờ yên lặng (mục `rhythm` nhắc yên lặng/đừng báo) ⇒ báo kênh phụ (T10 móc vào cờ này, spec §4C)."""
    if section != "rhythm":
        return False
    f = _fold(value_text)
    return any(w in f for w in QUIET_LEXICON)


def needs_pin(section: str, value_text: str, predicate_sensitivity: str = "normal") -> bool:
    return predicate_sensitivity != "normal" or is_defensive(section, value_text)


# ─── đọc cho mặt "Tôi" ──────────────────────────────────────────────────────

async def _evidence(db: AsyncSession, fact_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    if not fact_ids:
        return {}
    rows = (await db.execute(text("""SELECT id, fact_id, kind, raw_event_id, quote_status, span, created_at
                                     FROM mem.fact_evidence WHERE fact_id = ANY(:f) ORDER BY created_at, id"""), {"f": fact_ids})).all()
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r.fact_id, []).append({"id": str(r.id), "kind": r.kind,
                                              "raw_event_id": None if r.raw_event_id is None else str(r.raw_event_id),
                                              "quote_status": r.quote_status, "span": r.span})
    return out


async def read(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """`GET /me/profile`: đủ 6 mục; mỗi mục có item `owner_confirmed` ("Sếp nói") và `proposed` ("em đoán") tách bạch."""
    owner = await entities.owner(db, org_id)
    ver = (await db.execute(text("SELECT version FROM mem.owner_profile WHERE org_id = :o"), {"o": org_id})).scalar_one_or_none()
    rows = (await db.execute(text("""
        SELECT v.fact_id, v.owner_section, v.predicate, v.status, v.object_entity_id, v.object_value, v.valid_from, v.evidence_n,
               e.display_name AS obj_name, f.created_by, f.source_class
        FROM mem.v_owner_profile v JOIN mem.facts f ON f.id = v.fact_id
        LEFT JOIN mem.entities e ON e.id = v.object_entity_id
        WHERE v.org_id = :o
        ORDER BY v.owner_section, (v.status = 'owner_confirmed') DESC, v.valid_from, v.fact_id"""), {"o": org_id})).all()
    ev = await _evidence(db, [r.fact_id for r in rows])
    sections: dict[str, list[dict[str, Any]]] = {s: [] for s in SECTIONS}
    proposals = 0
    for r in rows:
        confirmed = r.status == "owner_confirmed"
        proposals += 0 if confirmed else 1
        txt = r.obj_name if r.object_entity_id is not None else profile_read.value_text(r.object_value)
        sections.setdefault(r.owner_section, []).append({
            "fact_id": str(r.fact_id), "fact_ids": [str(r.fact_id)], "text": txt, "predicate": r.predicate,
            "object_entity_id": None if r.object_entity_id is None else str(r.object_entity_id),
            "status": r.status, "source": "owner" if confirmed else "assistant",
            "source_label": "Sếp nói" if confirmed else "em đoán", "evidence": ev.get(r.fact_id, []),
            "evidence_ids": [e["id"] for e in ev.get(r.fact_id, [])], "valid_from": r.valid_from, "editable": True,
            "needs_pin": needs_pin(r.owner_section, txt)})
    return {"breadcrumb": [{"tier": "me", "id": str(owner.id), "label": "Tôi"}], "version": int(ver or 0),
            "owner_entity_id": str(owner.id), "proposals_n": proposals,
            "sections": [{"code": s, "label": profile_read.SECTION_LABEL_VI[s], "items": sections.get(s, [])} for s in SECTIONS]}


# ─── ghi ────────────────────────────────────────────────────────────────────

def _check_section(section: str) -> str:
    if section not in SECTION_PREDICATE:
        raise ProfileError("unknown_section", "Mục Hồ sơ này không có", detail=f"section={section}")
    return SECTION_PREDICATE[section]


async def _profile_fact(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID) -> Any:
    """Dòng sự thật CỦA Hồ sơ Sếp (đúng org, chủ thể = thực thể Sếp, vị từ `owner_*`, còn hiệu lực). Không phải ⇒ 404 — route này
    không bao giờ được dùng để sửa sự thật khác (chống đụng sự thật người khác qua id đoán)."""
    r = (await db.execute(text("""
        SELECT f.id, f.org_id, f.predicate, f.status, f.object_entity_id, f.object_value, f.object_key, f.source_class,
               f.created_by, f.subject_id, f.valid_from, p.owner_section, p.sensitivity
        FROM mem.facts f JOIN mem.predicates p ON p.code = f.predicate AND p.owner_section IS NOT NULL
        JOIN mem.entities s ON s.id = f.subject_id
        JOIN mem.entities c ON c.id = coalesce(s.merged_into, s.id) AND c.is_owner
        WHERE f.id = :i AND f.org_id = :o AND f.status IN ('proposed', 'asserted', 'owner_confirmed', 'disputed')"""),
                          {"i": fact_id, "o": org_id})).one_or_none()
    if r is None:
        raise ProfileError("not_found", "Mục Hồ sơ không tồn tại hoặc nằm ngoài phạm vi của Sếp", status=404,
                           detail=f"fact_id={fact_id}")
    return r


def _row_text(r: Any) -> str:
    return profile_read.value_text(r.object_value) if r.object_entity_id is None else ""


async def _check_object_entity(db: AsyncSession, org_id: uuid.UUID, owner_id: uuid.UUID, object_id: uuid.UUID) -> uuid.UUID:
    try:
        canon = await entities.canonical(db, object_id)
        ent = await entities.require(db, org_id, canon, allow_forgotten=False)
    except entities.EntityError as e:
        raise ProfileError("bad_entity", "Người/đối tượng này không có trong hệ thống", status=404, detail=str(e)) from e
    if ent.is_owner or canon == owner_id:
        raise ProfileError("bad_entity", "Không thể chọn chính Sếp làm đối tượng của mục này", detail="object = owner")
    return canon


def _wrap(e: facts.FactWriteError) -> ProfileError:
    status = 409 if e.code in (facts.ERR_CONFLICT, facts.ERR_DEADLOCK) else 422
    return ProfileError(e.code, "Không ghi được mục Hồ sơ", status=status, detail=e.detail or e.code)


async def _copy_evidence(db: AsyncSession, old_id: uuid.UUID, new_id: uuid.UUID) -> None:
    """Bằng chứng gốc (tin của Sếp) theo mục sang bản `owner_confirmed` mới — mặt Bằng chứng vẫn dẫn về tin gốc."""
    await db.execute(text("""
        INSERT INTO mem.fact_evidence (org_id, fact_id, kind, raw_event_id, raw_received_at, meaning_unit_id, action_log_id, quote_status, span)
        SELECT org_id, :n, kind, raw_event_id, raw_received_at, meaning_unit_id, action_log_id, quote_status, span
        FROM mem.fact_evidence WHERE fact_id = :o AND kind <> 'owner_edit'
        ON CONFLICT (fact_id, kind, coalesce(raw_event_id, meaning_unit_id, action_log_id)) DO NOTHING"""), {"o": old_id, "n": new_id})


async def _promote(db: AsyncSession, org_id: uuid.UUID, old: Any, *, user_id: uuid.UUID, action_log_id: uuid.UUID) -> facts.Fact:
    """Xác nhận một mục (`proposed`/`asserted`/`disputed` → `owner_confirmed`) bằng `facts.correct` cùng giá trị: bản cũ
    `superseded`, bản mới `owner_confirmed` + bằng chứng `owner_edit` (+ chép bằng chứng gốc nếu nguồn không phải người ngoài)."""
    try:
        new = await facts.correct(db, org_id, old.id, actor_user_id=user_id, new_value=None if old.object_entity_id is not None else old.object_value,
                                  new_object_id=old.object_entity_id, action_log_id=action_log_id)
    except facts.FactWriteError as e:
        raise _wrap(e) from e
    if old.source_class not in facts.EXTERNAL_CLASSES:
        await _copy_evidence(db, old.id, new.id)
    return new


async def add_item(db: AsyncSession, org_id: uuid.UUID, *, user_id: uuid.UUID, section: str, text_value: str | None = None,
                   object_entity_id: uuid.UUID | None = None, action_log_id: uuid.UUID, via: str = "console",
                   now: datetime | None = None) -> ItemResult:
    """Sếp thêm một mục ⇒ `owner_confirmed` (bằng chứng `owner_edit`). Mục trùng một đề xuất/sự thật đang hiệu lực ⇒ chính nó được xác nhận
    (không tạo dòng thứ hai). `via='chat'` (W8): mục phòng thủ ⇒ `console_required`."""
    pcode = _check_section(section)
    pred = await predicates.get(db, pcode)
    owner = await entities.owner(db, org_id)
    obj: uuid.UUID | None = None
    value: str | None = None
    if pred.object_kind == "entity":
        if object_entity_id is None:
            raise ProfileError("need_entity", "Mục này cần chọn một người/đối tượng", detail="object_entity_id bắt buộc")
        obj = await _check_object_entity(db, org_id, owner.id, object_entity_id)
        key = facts.entity_key(obj)
        shown = ""
    else:
        if text_value is None or object_entity_id is not None:
            raise ProfileError("need_text", "Mục này cần nội dung chữ", detail="text bắt buộc, không dùng object_entity_id")
        value = clean_text(text_value)
        key = facts.value_key(value)
        shown = value
        if via == "chat" and is_defensive(section, value):
            raise ProfileError("console_required", "Việc này cần mở Console (có PIN) mới ghi được", status=423,
                               detail=f"{pcode}: nội dung chạm lừa đảo/sức khoẻ-an toàn", decision_kind="console")
    existing = (await db.execute(text("""
        SELECT f.id, f.org_id, f.predicate, f.status, f.object_entity_id, f.object_value, f.object_key, f.source_class,
               f.created_by, f.subject_id, f.valid_from, p.owner_section, p.sensitivity
        FROM mem.facts f JOIN mem.predicates p ON p.code = f.predicate
        WHERE f.org_id = :o AND f.subject_id = ANY(:m) AND f.predicate = :p AND f.object_key = :k
          AND f.status IN ('proposed', 'asserted', 'owner_confirmed', 'disputed')
          AND (f.valid_to IS NULL OR f.valid_to > now())
        ORDER BY (f.status = 'owner_confirmed') DESC, f.recorded_at LIMIT 1"""),
                                {"o": org_id, "m": await entities.members(db, owner.id), "p": pcode, "k": key})).one_or_none()
    if existing is not None:
        if existing.status == "owner_confirmed":
            return ItemResult(await facts.get(db, org_id, existing.id) or _missing(), "existing")
        return ItemResult(await _promote(db, org_id, existing, user_id=user_id, action_log_id=action_log_id), "confirmed", existing.id)
    fin = facts.FactIn(org_id=org_id, subject_id=owner.id, predicate=pcode, valid_from=now or await _db_now(db), object_entity_id=obj,
                       object_value=value, source_class="owner", speaker_entity_id=owner.id, confidence_exist=1.0, confidence_content=1.0,
                       created_by="owner", confirmed_by=user_id, evidence=[facts.EvidenceIn("owner_edit", action_log_id=action_log_id)])
    try:
        fact = await facts.assert_fact(db, fin)
    except facts.FactWriteError as e:
        raise _wrap(e) from e
    del shown
    return ItemResult(fact, "added")


async def _db_now(db: AsyncSession) -> datetime:
    """Giờ của DB trong giao dịch (cùng đồng hồ với `now()` trong view `mem.v_owner_profile`): mục vừa ghi phải thấy ngay, không bị
    lệch vài mili-giây giữa đồng hồ máy chủ ứng dụng và DB."""
    return (await db.execute(text("SELECT now()"))).scalar_one()  # type: ignore[no-any-return]


def _missing() -> facts.Fact:
    raise ProfileError("not_found", "Mục Hồ sơ không còn", status=404, detail="fact biến mất giữa chừng")


async def edit_item(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID, *, user_id: uuid.UUID, text_value: str | None = None,
                    object_entity_id: uuid.UUID | None = None, action_log_id: uuid.UUID) -> ItemResult:
    """Sửa = đóng bản cũ (`superseded`) + ghi bản mới `owner_confirmed` (cũng là cách Sếp "sửa rồi xác nhận" một đề xuất)."""
    old = await _profile_fact(db, org_id, fact_id)
    owner = await entities.owner(db, org_id)
    obj: uuid.UUID | None = None
    value: str | None = None
    if old.object_entity_id is not None:
        if object_entity_id is None or text_value is not None:
            raise ProfileError("need_entity", "Mục này cần chọn một người/đối tượng", detail="object_entity_id bắt buộc")
        obj = await _check_object_entity(db, org_id, owner.id, object_entity_id)
    else:
        if text_value is None or object_entity_id is not None:
            raise ProfileError("need_text", "Mục này cần nội dung chữ", detail="text bắt buộc")
        value = clean_text(text_value)
    try:
        new = await facts.correct(db, org_id, fact_id, actor_user_id=user_id, new_value=value, new_object_id=obj, action_log_id=action_log_id)
    except facts.FactWriteError as e:
        raise _wrap(e) from e
    return ItemResult(new, "edited", fact_id)


async def remove_item(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID, *, user_id: uuid.UUID, action_log_id: uuid.UUID) -> ItemResult:
    """Xoá = `retracted` (đóng hiệu lực, không xoá dòng)."""
    await _profile_fact(db, org_id, fact_id)
    try:
        new = await facts.correct(db, org_id, fact_id, actor_user_id=user_id, retract=True, action_log_id=action_log_id)
    except facts.FactWriteError as e:
        raise _wrap(e) from e
    return ItemResult(new, "removed")


async def get_for_decision(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID) -> Any:
    """Đề xuất `proposed` của Hồ sơ Sếp (đúng org) để route quyết định có cần PIN không."""
    r = await _profile_fact(db, org_id, fact_id)
    if r.status != "proposed":
        raise ProfileError("not_proposal", "Mục này không phải đề xuất đang chờ Sếp quyết", status=409,
                           detail=f"status={r.status}")
    return r


def decision_needs_pin(r: Any) -> bool:
    return needs_pin(r.owner_section, _row_text(r), r.sensitivity)


async def decide(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID, decision: str, *, user_id: uuid.UUID,
                 action_log_id: uuid.UUID) -> ItemResult:
    """`confirm` ⇒ `owner_confirmed`; `reject` ⇒ `retracted` (job đêm không đề xuất lại đúng mục đó, xem `was_rejected`)."""
    if decision not in ("confirm", "reject"):
        raise ProfileError("bad_decision", "Quyết định chỉ có thể là xác nhận hoặc từ chối", detail=f"decision={decision}")
    old = await get_for_decision(db, org_id, fact_id)
    if decision == "reject":
        try:
            new = await facts.correct(db, org_id, fact_id, actor_user_id=user_id, retract=True, action_log_id=action_log_id)
        except facts.FactWriteError as e:
            raise _wrap(e) from e
        return ItemResult(new, "rejected")
    return ItemResult(await _promote(db, org_id, old, user_id=user_id, action_log_id=action_log_id), "confirmed", fact_id)


# ─── đề xuất (trợ lý / job đêm) ─────────────────────────────────────────────

async def owner_person_ids(db: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    """Mọi hồ sơ người thuộc Sếp: người của thực thể Sếp + người đã gộp vào (theo `core.persons.merged_into_id`, cả chuỗi)."""
    owner = await entities.owner(db, org_id)
    seeds = (await db.execute(text("SELECT person_id FROM mem.entities WHERE person_id IS NOT NULL AND (id = :c OR merged_into = :c)"),
                              {"c": owner.id})).scalars().all()
    if not seeds:
        return []
    rows = (await db.execute(text("""
        WITH RECURSIVE down(id, depth) AS (
          SELECT id, 0 FROM core.persons WHERE id = ANY(:s) AND org_id = :o
          UNION SELECT p.id, d.depth + 1 FROM core.persons p JOIN down d ON p.merged_into_id = d.id WHERE d.depth < 10)
        SELECT DISTINCT id FROM down"""), {"s": list(seeds), "o": org_id})).scalars().all()
    return list(rows)


def _forwarded(payload: Any) -> bool:
    return isinstance(payload, dict) and any(payload.get(k) not in (None, "", False) for k in FWD_KEYS)


async def owner_authored(db: AsyncSession, org_id: uuid.UUID, raw_event_id: uuid.UUID) -> tuple[bool, Any]:
    """Tin `raw_event_id` do CHÍNH Sếp viết (người gửi là hồ sơ người của Sếp — theo định danh, không theo tên), là chữ thường,
    không phải tin chuyển tiếp. Trả (đúng?, dòng tin hoặc None)."""
    people = await owner_person_ids(db, org_id)
    if not people:
        return False, None
    r = (await db.execute(text("""
        SELECT e.id, e.received_at, e.occurred_at, e.kind, e.body_text, e.payload, e.group_id
        FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
        WHERE e.org_id = :o AND e.id = :i AND pi.person_id = ANY(:p) AND e.direction = 'inbound'"""),
                          {"o": org_id, "i": raw_event_id, "p": people})).one_or_none()
    if r is None or r.kind != "text" or not r.body_text or _forwarded(r.payload):
        return False, r
    return True, r


async def was_rejected(db: AsyncSession, org_id: uuid.UUID, owner_id: uuid.UUID, predicate: str, object_key: str) -> bool:
    """Sếp đã TỪ CHỐI đúng đề xuất này ⇒ đừng đề xuất lại (chống vòng tự củng cố / làm phiền)."""
    n = (await db.execute(text("""
        SELECT count(*) FROM mem.facts f JOIN mem.fact_status_events s ON s.fact_id = f.id AND s.to_status = 'retracted' AND s.by = 'owner'
        WHERE f.org_id = :o AND f.subject_id = ANY(:m) AND f.predicate = :p AND f.object_key = :k AND f.created_by <> 'owner'"""),
                          {"o": org_id, "m": await entities.members(db, owner_id), "p": predicate, "k": object_key})).scalar_one()
    return int(n) > 0


async def propose(db: AsyncSession, org_id: uuid.UUID, *, section: str, text_value: str, raw_event_id: uuid.UUID,
                  quote: str | None = None, extractor_version: str = PROPOSAL_EXTRACTOR, created_by: str = "consolidator") -> ItemResult | None:
    """Đề xuất một mục Hồ sơ (`proposed`, "em đoán") từ MỘT tin của chính Sếp. Luôn `proposed`, không `confirmed_by`. Trả None khi
    Sếp từng từ chối đúng mục này; ném `ProfileError(not_owner_message)` khi tin không do Sếp viết (chữ người ngoài không thành Hồ sơ).
    `quote` (mặc định cả nội dung) phải là chuỗi con NGUYÊN VĂN của thân tin — span do code cắt."""
    pcode = _check_section(section)
    pred = await predicates.get(db, pcode)
    if pred.object_kind != "value":
        raise ProfileError("need_text", "Đề xuất tự động chỉ áp cho mục dạng chữ", detail=pcode)
    value = clean_text(text_value)
    ok, row = await owner_authored(db, org_id, raw_event_id)
    if not ok or row is None:
        raise ProfileError("not_owner_message", "Chỉ tin do chính Sếp viết mới thành đề xuất Hồ sơ", status=403,
                           detail="tin không do Sếp gửi / không phải chữ thường / là tin chuyển tiếp")
    body: str = row.body_text
    q = quote if quote is not None else value
    start = body.find(q)
    if start < 0 or not q.strip():
        raise ProfileError("quote_not_found", "Trích dẫn không khớp nguyên văn tin gốc", detail="quote không là chuỗi con của body_text")
    owner = await entities.owner(db, org_id)
    if await was_rejected(db, org_id, owner.id, pcode, facts.value_key(value)):
        return None
    ev = facts.EvidenceIn("raw_event", raw_event_id=raw_event_id, raw_received_at=row.received_at,
                          span={"source_field": "body", "span_start": start, "span_end": start + len(q)})
    fin = facts.FactIn(org_id=org_id, subject_id=owner.id, predicate=pcode, valid_from=row.occurred_at, object_value=value,
                       source_class="owner", speaker_entity_id=owner.id, source_raw_event_id=raw_event_id,
                       source_received_at=row.received_at, confidence_exist=0.5, confidence_content=0.5,
                       extractor_version=extractor_version, created_by=created_by, proposed=True, confirmed_by=None, evidence=[ev])
    try:
        fact = await facts.assert_fact(db, fin)
    except facts.FactWriteError as e:
        raise _wrap(e) from e
    return ItemResult(fact, "proposed" if fact.outcome != "duplicate" else "existing")


# ─── từ vựng đề xuất (xác định, không gọi model) ────────────────────────────

#: (mục, mẫu trên chữ bỏ dấu). Neo ngôi thứ nhất của Sếp (anh/em/tôi/mình/minh) để hạn chế bắt nhầm lời người khác.
_SELF = r"(?:\banh\b|\btoi\b|\bminh\b|\bem\b)"
PROPOSAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("goals", re.compile(rf"(?:muc tieu (?:cua )?{_SELF}|{_SELF}[^.!?\n]{{0,25}}\b(?:uu tien|muon mo|dinh mo|se mo|du dinh)\b|\bquy nay\b[^.!?\n]{{0,40}}\b(?:mo|dat|ra mat)\b)")),
    ("values_limits", re.compile(rf"(?:{_SELF}[^.!?\n]{{0,20}}\b(?:khong nhan|khong lam|khong ban|khong hop tac|khong ban toi)\b|\btuyet doi khong\b)")),
    ("rhythm", re.compile(rf"(?:\b(?:toi|sang|trua|chieu) (?:thu [2-7]|chu nhat)\b[^.!?\n]{{0,40}}\b(?:danh cho|nghi|ban)\b|{_SELF}[^.!?\n]{{0,15}}\b(?:khong lam viec|nghi) (?:cuoi tuan|chu nhat))")),
    ("report_prefs", re.compile(rf"(?:\b(?:dung|khong can|dung co) bao\b|\bbao (?:ngay|cho {_SELF}|giup {_SELF})\b)")),
)
MIN_SENTENCE = 12
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def candidates_from_message(body: str) -> list[tuple[str, str]]:
    """[(mục, câu nguyên văn)] — mỗi câu tối đa MỘT mục (mục đầu tiên khớp theo thứ tự `PROPOSAL_PATTERNS`)."""
    out: list[tuple[str, str]] = []
    for sent in _SPLIT.split(body):
        s = sent.strip()
        if not (MIN_SENTENCE <= len(s) <= MAX_TEXT) or _FACT_ID_FORGERY.search(s):
            continue
        f = _fold(s)
        for section, pat in PROPOSAL_PATTERNS:
            if pat.search(f):
                out.append((section, s))
                break
    return out
