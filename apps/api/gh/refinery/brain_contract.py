# ruff: noqa: E501
"""Hợp đồng đầu ra bộ não `brain-out/1` (spec §4F) — MỘT nguồn sự thật cho kỹ năng v2.1, `extract.py`, `verify.py` (L3) và bộ chấm
G-KB (L6). Đổi hợp đồng = tăng `CONTRACT_VERSION` + chạy lại G-KB.

Phân vai: model thấy sự việc, tách đơn vị, chọn `kind`, trích nguyên văn, nêu cờ, `harm`, đề cử `gate_candidate`, nối Hồ sơ bằng
`fact_id`, đề xuất sự thật — KHÔNG trả mức cuối, độ chắc, tự tính ngày/tiền, áp trần bối cảnh. Code (`verify.py`, `vi_time`,
§4D.4) kiểm schema + trích dẫn, tính `due_at`/`value_vnd`, nối thực thể, độ chắc hai trục. `levels.py` + `mem.context_types.policy`
quyết mức cuối; `level_hint` chỉ để đo độ lệch (levels.py KHÔNG đọc), `source_hint`/`source_rating` không đi vào độ chắc (CNS-104).

Dùng: `BrainOut.model_validate_json(raw)` (sai ⇒ `pydantic.ValidationError`: gọi lại một lần kèm lỗi, vẫn sai ⇒ `contract_error`);
`json_schema()` cho kỹ năng/bộ chấm; `stub(thread_ref)` sinh một đầu ra hợp lệ tối thiểu (L2 dùng khi chờ L3).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CONTRACT_VERSION = "brain-out/1"

KINDS = ("nghia_vu_loi_hua", "viec_han", "cau_hoi_cho_xu_ly", "su_kien_dip", "suc_khoe_an_toan", "xung_dot_khieu_nai",
         "quan_he_nong_nguoi", "co_hoi_ket_noi", "rui_ro_lua_dao", "thi_truong_thong_tin")
GATES = ("lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "co_hoi_lon_sap_troi",
         "su_co_cong_viec_gap")
QUOTE_FIELDS = ("body", "transcript", "ocr", "caption")
ROLES = ("nguoi_hua", "nguoi_duoc_hua", "nguoi_hoi", "nguoi_bi_anh_huong", "nguoi_gui", "nguoi_bi_mao_danh", "khac")
STATUS_HINTS = ("dang_mo", "sap_han", "qua_han", "da_xu_ly", "da_giu", "theo_doi")
MODALITIES = ("text", "voice_transcript", "ocr", "caption", "forward", "forward_suspected")
RELEVANCE_HOW = ("goal", "resource", "limit_conflict", "key_relation", "rhythm")
DECISION_KINDS = ("self", "internal", "outbound", "console")        # đúng 4 giá trị của ops.asks (r4 §4.4)
SOURCE_HINTS = ("insider", "member", "outsider", "forwarded")
MEMORY_UPDATE_TYPES = ("promise_kept", "owner_profile_suggestion", "alias", "channel_health")
MAX_QUOTE_CHARS = 120
MAX_OWNER_QUESTION_CHARS = 400

ThreadRef = Annotated[str, StringConstraints(pattern=r"^(g:[^:\s]+|dm:[^:\s]+:[^:\s]+)$")]
MessageId = Annotated[str, StringConstraints(pattern=r"^m_\S+$")]
PersonRef = Annotated[str, StringConstraints(pattern=r"^u_\S+$")]
FactRef = Annotated[str, StringConstraints(pattern=r"^f_\S+$")]
ShortKey = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Text = Annotated[str, StringConstraints(max_length=2000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quote(_Strict):
    message_id: MessageId
    field: Literal["body", "transcript", "ocr", "caption"]
    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_QUOTE_CHARS)]      # nguyên văn, ≤ 120 ký tự


class PersonMention(_Strict):
    surface: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    person_ref: PersonRef | None = None
    role: Literal["nguoi_hua", "nguoi_duoc_hua", "nguoi_hoi", "nguoi_bi_anh_huong", "nguoi_gui", "nguoi_bi_mao_danh",
                  "khac"] = "khac"


class Flags(_Strict):
    public: bool = False
    nudged: bool = False
    repeat_n: int | None = Field(default=None, ge=0, le=1000)
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


class Harm(_Strict):
    what: Text
    amount_text: Text | None = None
    horizon_text: Text | None = None


class Deadline(_Strict):
    text: Text | None = None
    anchor_message_id: MessageId | None = None
    lunar: bool = False
    ambiguous: bool = False


class Condition(_Strict):
    text: Text | None = None
    met: Literal["yes", "no", "unknown"] = "unknown"
    met_message_id: MessageId | None = None


class Conflict(_Strict):
    fact_id: FactRef | None = None
    text: Text


class Evidence(_Strict):
    sources_n: int = Field(default=1, ge=0, le=10_000)
    source_rating: Annotated[str, StringConstraints(max_length=200)] | None = None       # nhãn ngắn — chỉ để đo
    conflicts: list[Conflict] = Field(default_factory=list)
    modality: Literal["text", "voice_transcript", "ocr", "caption", "forward", "forward_suspected"] = "text"


class OwnerRelevance(_Strict):
    fact_id: FactRef                                   # chỉ id có trong khối Hồ sơ của prompt (code kiểm)
    how: Literal["goal", "resource", "limit_conflict", "key_relation", "rhythm"]
    note: Text | None = None


class Action(_Strict):
    who: Annotated[str, StringConstraints(pattern=r"^(owner|u_\S+)$")]
    what: Text
    by_when_text: Text | None = None
    decision_kind: Literal["self", "internal", "outbound", "console"]
    draft: Text | None = None


class Unit(_Strict):
    unit_ref: ShortKey
    event_ref: ShortKey
    kind: Literal["nghia_vu_loi_hua", "viec_han", "cau_hoi_cho_xu_ly", "su_kien_dip", "suc_khoe_an_toan",
                  "xung_dot_khieu_nai", "quan_he_nong_nguoi", "co_hoi_ket_noi", "rui_ro_lua_dao",
                  "thi_truong_thong_tin"] | None = None
    quotes: Annotated[list[Quote], Field(min_length=1, max_length=10)]
    people: list[PersonMention] = Field(default_factory=list)
    summary: Text
    flags: Flags = Field(default_factory=Flags)
    harm: Harm | None = None
    deadline: Deadline = Field(default_factory=Deadline)
    amount_text: Text | None = None
    condition: Condition = Field(default_factory=Condition)
    status_hint: Literal["dang_mo", "sap_han", "qua_han", "da_xu_ly", "da_giu", "theo_doi"] = "dang_mo"
    evidence: Evidence = Field(default_factory=Evidence)
    owner_relevance: list[OwnerRelevance] = Field(default_factory=list)
    owner_basis: Literal["co_ho_so", "thieu_ho_so"] = "thieu_ho_so"
    gate_candidate: Literal["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo",
                            "co_hoi_lon_sap_troi", "su_co_cong_viec_gap"] | None = None
    level_hint: Literal["red", "amber", "grey"] | None = None                # chỉ để đo; levels.py không đọc
    competing_hypothesis: Text | None = None                               # bắt buộc với người / lừa đảo / quan hệ
    action: Action | None = None


class FactObject(_Strict):
    surface: Text | None = None
    person_ref: PersonRef | None = None
    value: Any = None


class FactSubject(_Strict):
    surface: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    person_ref: PersonRef | None = None


class FactProposed(_Strict):
    unit_ref: ShortKey
    subject: FactSubject
    predicate: Annotated[str, StringConstraints(min_length=1, max_length=64)]       # mã trong mem.predicates (verify.py kiểm)
    object: FactObject = Field(default_factory=FactObject)
    valid_from_text: Text | None = None
    quote_refs: Annotated[list[MessageId], Field(min_length=1, max_length=10)]
    source_hint: Literal["insider", "member", "outsider", "forwarded"] | None = None     # chỉ để đo


class MemoryUpdate(_Strict):
    type: Literal["promise_kept", "owner_profile_suggestion", "alias", "channel_health"]
    text: Text
    message_ids: list[MessageId] = Field(default_factory=list)


class Unreadable(_Strict):
    voice: int = Field(default=0, ge=0)
    image: int = Field(default=0, ge=0)
    file: int = Field(default=0, ge=0)


class BrainOut(_Strict):
    contract_version: Literal["brain-out/1"] = CONTRACT_VERSION     # type: ignore[assignment]
    thread_ref: ThreadRef
    units: list[Unit] = Field(default_factory=list)
    facts_proposed: list[FactProposed] = Field(default_factory=list)
    memory_updates: list[MemoryUpdate] = Field(default_factory=list)
    unreadable: Unreadable = Field(default_factory=Unreadable)
    owner_question: Annotated[str, StringConstraints(max_length=MAX_OWNER_QUESTION_CHARS)] | None = None


def json_schema() -> dict[str, Any]:
    """JSON Schema của `brain-out/1` (cho kỹ năng v2.1 và bộ chấm G-KB đọc đúng một khuôn)."""
    s = BrainOut.model_json_schema()
    s["$id"] = f"urn:genos:{CONTRACT_VERSION}"
    s["title"] = "BrainOut"
    s["description"] = "Hợp đồng đầu ra bộ não brain-out/1 — gh/refinery/brain_contract.py (spec Bước 1 r6.1 §4F)"
    return s


def stub(thread_ref: str, *, message_id: str = "m_stub", quote: str = "(không có sự việc)") -> BrainOut:
    """Đầu ra HỢP LỆ tối thiểu cho thử nghiệm/chờ L3: không đơn vị nào (lượt không thấy sự việc) hoặc một đơn vị trống kind."""
    return BrainOut(thread_ref=thread_ref, units=[Unit(unit_ref="u1", event_ref="e1", kind=None,
                                                       quotes=[Quote(message_id=message_id, field="body", text=quote)],
                                                       summary="(stub)")])


def validate_json(raw: str | bytes) -> BrainOut:
    return BrainOut.model_validate_json(raw)
