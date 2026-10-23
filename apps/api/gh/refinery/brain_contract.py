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

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CONTRACT_VERSION = "brain-out/1"

KINDS = ("nghia_vu_loi_hua", "viec_han", "cau_hoi_cho_xu_ly", "su_kien_dip", "suc_khoe_an_toan", "xung_dot_khieu_nai",
         "quan_he_nong_nguoi", "co_hoi_ket_noi", "rui_ro_lua_dao", "thi_truong_thong_tin")
GATES = ("lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo", "co_hoi_lon_sap_troi",
         "su_co_cong_viec_gap")
QUOTE_FIELDS = ("body", "transcript", "ocr", "caption")
ROLES = ("nguoi_hua", "nguoi_duoc_hua", "nguoi_hoi", "nguoi_bi_anh_huong", "nguoi_gui", "nguoi_bi_mao_danh", "nguoi_nhan_tien",
         "khac")
STATUS_HINTS = ("dang_mo", "sap_han", "qua_han", "da_xu_ly", "da_giu", "theo_doi")
MODALITIES = ("text", "voice_transcript", "ocr", "caption", "forward", "forward_suspected")
RELEVANCE_HOW = ("goal", "resource", "limit_conflict", "key_relation", "rhythm")
DECISION_KINDS = ("self", "internal", "outbound", "console")        # đúng 4 giá trị của ops.asks (r4 §4.4)
SOURCE_HINTS = ("insider", "member", "outsider", "forwarded")
MEMORY_UPDATE_TYPES = ("promise_kept", "owner_profile_suggestion", "alias", "channel_health")
MAX_QUOTE_CHARS = 120
MAX_OWNER_QUESTION_CHARS = 400

ThreadRef = Annotated[str, StringConstraints(pattern=r"^(g:[^:\s]+|dm:[^:\s]+:[^:\s]+)$")]
MessageId = Annotated[str, StringConstraints(pattern=r"^m_\S+$", max_length=128)]
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
    # `nguoi_bi_mao_danh` hiểu rộng (§4F.1 #5): người bị mạo danh VÀ chủ tài khoản bị chiếm.
    role: Literal["nguoi_hua", "nguoi_duoc_hua", "nguoi_hoi", "nguoi_bi_anh_huong", "nguoi_gui", "nguoi_bi_mao_danh",
                  "nguoi_nhan_tien", "khac"] = Field(
        default="khac", description="nguoi_bi_mao_danh gồm người bị mạo danh VÀ chủ tài khoản bị chiếm; "
                                    "nguoi_nhan_tien = chủ STK / người nhận tiền (§4F.1 #12)")


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
    ongoing: bool = False           # cờ thứ 14 (§4F.1 #1): sự việc còn tiếp diễn; đi cùng `Unit.ongoing_message_ids`


class Harm(_Strict):
    what: Text
    amount_text: Text | None = None
    horizon_text: Text | None = None
    irreversible: bool = False                    # §4F.1 #11: không cần kiểm thêm; code cộng luật số (tiền đã chuyển ≥ 24 giờ)


class Txn(_Strict):
    """Giao dịch tiền (§4F.1 #10). `message_id` phải nằm trong `quotes`; code suy `pending`/`just_done`/`done_cannot_stop`."""

    state_hint: Literal["requested", "transferred"] | None = None
    message_id: MessageId | None = None


class Deadline(_Strict):
    text: Text | None = None
    anchor_message_id: MessageId | None = None
    lunar: bool = False
    ambiguous: bool = False


class Condition(_Strict):
    text: Text | None = None
    met: Literal["yes", "no", "unknown"] = "unknown"
    met_message_id: MessageId | None = None


class EventTime(_Strict):
    """Thời điểm sự việc xảy ra (§4F.1 #3). `text` là chuỗi con NGUYÊN VĂN của tin `anchor_message_id` (L3 kiểm với thân tin,
    xem `check_event_time_text`); code tính `event_at` bằng `vi_time.parse_past`, null ⇒ `event_at = msg_at`."""

    text: Text | None = None
    anchor_message_id: MessageId | None = None


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
    status_message_id: MessageId | None = None           # §4F.1 #4: tin chứng minh status_hint (phải nằm trong quotes)
    ongoing_message_ids: list[MessageId] = Field(default_factory=list)   # §4F.1 #2: ⊆ quotes; rỗng ⇒ flags.ongoing = False
    event_time: EventTime | None = None                  # §4F.1 #3
    delivery_form_hint: Literal["direct", "warning_story"] = "direct"   # §4F.1 #9: code (chuyển tiếp/simhash) thắng ⇒ forwarded
    txn: Txn | None = None                               # §4F.1 #10
    # Do CODE ghi khi chuẩn hoá đầu ra của model (không phải model khai); model có gửi thì giữ và nối thêm.
    normalization_notes: list[Annotated[str, StringConstraints(max_length=200)]] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)
    owner_relevance: list[OwnerRelevance] = Field(default_factory=list)
    owner_basis: Literal["co_ho_so", "thieu_ho_so"] = "thieu_ho_so"
    gate_candidate: Literal["lua_dao_dang_dien_ra", "an_toan", "khach_che_cong_khai_doa_bo", "loi_hua_khach_sap_vo",
                            "co_hoi_lon_sap_troi", "su_co_cong_viec_gap"] | None = None
    level_hint: Literal["red", "amber", "grey", "off"] | None = None         # chỉ để đo (khớp Level.level); levels.py không đọc
    competing_hypothesis: Text | None = None                               # bắt buộc với người / lừa đảo / quan hệ
    action: Action | None = None

    @model_validator(mode="after")
    def _normalize_message_refs(self) -> Unit:
        """§4F.1: id tham chiếu tin phải nằm trong `quotes` của CHÍNH đơn vị — CHUẨN HOÁ, không làm hỏng cả lượt: id lạ bị lọc,
        `ongoing` rỗng id thì ép false, `status_message_id`/`txn.message_id` không hợp lệ thì đặt None. Mỗi lần chuẩn hoá ghi
        một dòng vào `normalization_notes` (G-KB đếm). Chỉ sai schema cơ bản (kiểu/enum) mới raise. (Việc
        `sent_at ≥ now − 48h`, trích dẫn `verified`, `text` ⊂ tin anchor là của L3/`verify.py`.)"""
        quoted = {q.message_id for q in self.quotes}
        notes: list[str] = []
        self.normalization_notes = []         # ghi chú chỉ do CODE ghi: giá trị model gửi bị bỏ
        stray = [i for i in self.ongoing_message_ids if i not in quoted]
        if stray:
            self.ongoing_message_ids = [i for i in self.ongoing_message_ids if i in quoted]
            notes.extend(f"ongoing_message_id_dropped:{i}" for i in stray)
        if self.status_message_id is not None and self.status_message_id not in quoted:
            notes.append(f"status_message_id_dropped:{self.status_message_id}")
            self.status_message_id = None
        if self.txn is not None and self.txn.message_id is not None and self.txn.message_id not in quoted:
            notes.append(f"txn_message_id_dropped:{self.txn.message_id}")
            self.txn = self.txn.model_copy(update={"message_id": None})
        if self.flags.ongoing and not self.ongoing_message_ids:
            notes.append("ongoing_forced_false")
            self.flags = self.flags.model_copy(update={"ongoing": False})
        if notes:
            self.normalization_notes = [n[:200] for n in notes]
        return self


def check_event_time_text(unit: Unit, message_text_by_id: dict[str, str]) -> bool:
    """`event_time.text` phải là chuỗi con nguyên văn của tin `anchor_message_id` (L3 gọi với thân tin thật). Không có
    `event_time`/`text` ⇒ đúng (code dùng `msg_at`)."""
    et = unit.event_time
    if et is None or et.text is None:
        return True
    return et.anchor_message_id is not None and et.text in message_text_by_id.get(et.anchor_message_id, "")


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
