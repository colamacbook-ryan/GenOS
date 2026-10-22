"""Envelope có kiểu mỗi vòng model trả về (docs/design/gen-v1.md §3.3) — khớp `packages/contracts/src/gen.ts`.

Model trả MỘT khối JSON `{"steps": [GenStep, …]}`. Sai schema (`extra="forbid"`) → hỏi lại model một lần, vẫn sai →
"Gen chưa hiểu" và không thực thi gì.
"""

import json
import re
from datetime import datetime
from typing import Annotated, Any, Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from gh.hub_link import kho_write

DATA_TOOL_NAMES = ("overview.summary", "queue.list", "draft.list", "draft.get", "profile.search", "profile.get",
                   "opportunity.list", "people.care", "audit.list", "system.health", "guide.list", "screens.list",
                   "task.list", "staff.list", "refinery.summary", "hub.kho_summary", "hub.kho_search", "hub.kho_get",
                   "social.accounts", "social.read",
                   # v0.1.49 (QD-16): Tài liệu/Deal/Vụ việc nội bộ + lịch/việc/mail/Drive Google qua Gen-hub (chỉ Owner)
                   "document.list", "document.get", "deal.list", "deal.get", "case.list", "case.get", "hub.calendar",
                   "hub.tasks", "hub.mail_search", "hub.mail_read", "hub.drive_search",
                   # v0.1.54 (g1-api): Gen hướng dẫn — việc vận hành Sếp cần làm, tiến độ x/N, bài học (chỉ Owner)
                   "coach.status",
                   # v0.1.74: danh bạ nhóm / người + tin nhóm gần đây (chỉ đọc, theo «Quyền xem»)
                   "group.list", "group.recent", "person.list")
DataToolName = Literal["overview.summary", "queue.list", "draft.list", "draft.get", "profile.search", "profile.get",
                       "opportunity.list", "people.care", "audit.list", "system.health", "guide.list", "screens.list",
                       "task.list", "staff.list", "refinery.summary", "hub.kho_summary", "hub.kho_search",
                       "hub.kho_get", "social.accounts", "social.read", "document.list", "document.get", "deal.list",
                       "deal.get", "case.list", "case.get", "hub.calendar", "hub.tasks", "hub.mail_search",
                       "hub.mail_read", "hub.drive_search", "coach.status", "group.list", "group.recent",
                       "person.list"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Navigate(_M):
    type: Literal["navigate"]
    screen: str = Field(min_length=1, max_length=40)
    params: dict[str, str] | None = None


class Highlight(_M):
    type: Literal["highlight"]
    target: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=400)
    waitFor: Literal["click", "none"] | None = None  # noqa: N815 — tên trường theo hợp đồng TS


class TourStep(_M):
    screen: str | None = Field(default=None, max_length=40)
    target: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=400)


class Tour(_M):
    type: Literal["tour"]
    steps: list[TourStep] = Field(min_length=1, max_length=8)


UiAction = Annotated[Navigate | Highlight | Tour, Field(discriminator="type")]


class Suggestion(_M):
    label: str = Field(min_length=1, max_length=60)
    action: UiAction


class Say(_M):
    kind: Literal["say"]
    text: str = Field(min_length=1, max_length=4000)


class ToolCall(_M):
    kind: Literal["tool"]
    name: DataToolName
    args: dict[str, Any] = Field(default_factory=dict)


class Ui(_M):
    kind: Literal["ui"]
    action: UiAction


class Suggest(_M):
    kind: Literal["suggest"]
    items: list[Suggestion] = Field(min_length=1, max_length=3)


class Done(_M):
    kind: Literal["done"]


# ── Gen v2 (A4): đề xuất thao tác có xác nhận. Model chỉ ĐỀ XUẤT (điền sẵn form); server kiểm + làm giàu thành bước
# `{"kind": "proposal", "proposal": {...}}` gửi xuống web; chỉ khi người dùng bấm Xác nhận, web mới gọi
# `POST /gen/proposals/{id}/confirm` và server thực hiện NHÂN DANH người đó qua endpoint sẵn có (gh.gen.proposals).

class SubjectRef(_M):
    type: Literal["person", "group"]
    id: str = Field(min_length=1, max_length=64)


class DraftMessageFields(_M):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    subject: SubjectRef | None = None


class ReminderFields(_M):
    title: str = Field(min_length=1, max_length=200)
    remind_at: datetime
    due_at: datetime | None = None
    priority: Literal["P1", "P2", "P3"] = "P3"
    assignee_user_id: str | None = Field(default=None, max_length=64)
    subject: SubjectRef | None = None


class AssignFields(_M):
    item_type: Literal["task", "inbox"]
    item_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)


class ProposeDraft(_M):
    type: Literal["draft_message"]
    fields: DraftMessageFields


class ProposeReminder(_M):
    type: Literal["reminder"]
    fields: ReminderFields


class ProposeAssign(_M):
    type: Literal["assign"]
    fields: AssignFields


class SocialWriteFields(_M):
    """Trả lời bình luận / nhắn tin Facebook: đích PHẢI là link của mục vừa đọc được (gh.gen.proposals kiểm)."""
    account_id: str = Field(min_length=1, max_length=64)
    target_url: str = Field(min_length=12, max_length=300)
    text: str = Field(min_length=1, max_length=2000)


class ProposeSocialReply(_M):
    type: Literal["social_reply"]
    fields: SocialWriteFields


class ProposeSocialDm(_M):
    type: Literal["social_dm"]
    fields: SocialWriteFields


class MemoryNoteFields(_M):
    """v0.1.50 (QD-18): Gen nhớ — một quy ước / sở thích ổn định của Sếp (≤ 280 ký tự) + lý do (≤ 200)."""
    text: str = Field(min_length=1, max_length=280)
    reason: str = Field(min_length=1, max_length=200)


class ProposeMemoryNote(_M):
    type: Literal["memory_note"]
    fields: MemoryNoteFields


class KhoCreateFields(_M):
    """v0.1.50 (F-81): tạo bản ghi Phiên / Việc ở Kho dữ liệu — trường hợp lệ: gh.hub_link.kho_write (server kiểm
    lại)."""
    bang: Literal["Phiên", "Việc"]
    record: dict[str, str] = Field(max_length=8)


class KhoUpdateFields(_M):
    """v0.1.50 (F-81): sửa bản ghi Kho — mã PHIEN-n / VIEC-n PHẢI có trong kết quả hub.kho_* của lượt này."""
    ma: str = Field(pattern=kho_write.MA_RE, max_length=16)
    record: dict[str, str] = Field(min_length=1, max_length=8)


class ProposeKhoCreate(_M):
    type: Literal["kho_create"]
    fields: KhoCreateFields


class ProposeKhoUpdate(_M):
    type: Literal["kho_update"]
    fields: KhoUpdateFields


ProposalIn = Annotated[
    ProposeDraft | ProposeReminder | ProposeAssign | ProposeSocialReply | ProposeSocialDm | ProposeMemoryNote
    | ProposeKhoCreate | ProposeKhoUpdate,
    Field(discriminator="type")]
PROPOSAL_FIELDS: dict[str, type[_M]] = {"draft_message": DraftMessageFields, "reminder": ReminderFields,
                                        "assign": AssignFields, "social_reply": SocialWriteFields,
                                        "social_dm": SocialWriteFields, "memory_note": MemoryNoteFields,
                                        "kho_create": KhoCreateFields, "kho_update": KhoUpdateFields}


class Propose(_M):
    kind: Literal["propose"]
    proposal: ProposalIn


GenStep = Annotated[Say | ToolCall | Ui | Suggest | Propose | Done, Field(discriminator="kind")]


class Envelope(_M):
    steps: list[GenStep] = Field(min_length=1, max_length=12)


_STEP: TypeAdapter[Any] = TypeAdapter(GenStep)


class EnvelopeError(ValueError):
    pass


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n?```", re.S)
EMBED_SCAN_MAX = 20000
EMBED_MIN_SHARE = 0.5


def _embedded_json(text: str) -> Any:
    """JSON envelope lẫn trong văn bản (Antigravity CLI hay kèm lời dẫn). Chỉ nhận khi (a) nằm trong một khối ```…```,
    hoặc (b) là PHẦN CHÍNH của văn bản (≥ 50% độ dài) — review bảo mật v0.1.74 P3-1: không nhặt một payload
    `{"steps": …}` model chép lại từ tin nhóm nằm giữa đoạn văn xuôi. Không có ⇒ None."""
    for m in _FENCE.finditer(text[:EMBED_SCAN_MAX]):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        if _is_env(obj):
            return obj
    body = text.strip()[:EMBED_SCAN_MAX]
    dec = json.JSONDecoder()
    for i, ch in enumerate(body):
        if ch not in "{[":
            continue
        try:
            obj, end = dec.raw_decode(body, i)
        except ValueError:
            continue
        if _is_env(obj) and (end - i) >= EMBED_MIN_SHARE * len(body):
            return obj
        if i > (1 - EMBED_MIN_SHARE) * len(body):
            break                      # khối bắt đầu muộn hơn thế thì không thể chiếm ≥ 50% — dừng sớm (tránh O(n²))
    return None


def _is_env(obj: Any) -> bool:
    if isinstance(obj, dict):
        return "steps" in obj or "kind" in obj
    return isinstance(obj, list) and bool(obj) and all(isinstance(x, dict) and "kind" in x for x in obj)


def parse(text: str) -> Envelope:
    """Đọc envelope; chấp nhận `{"steps": […]}`, một bước lẻ `{"kind": …}` hoặc mảng bước."""
    try:
        data = orjson.loads(_strip_fence(text))
    except orjson.JSONDecodeError as e:
        data = _embedded_json(text)      # v0.1.74: JSON nằm lẫn trong văn xuôi (Antigravity CLI hay kèm lời dẫn)
        if data is None:
            raise EnvelopeError(f"không phải JSON: {e}") from e
    if isinstance(data, list):
        data = {"steps": data}
    elif isinstance(data, dict) and "kind" in data:
        data = {"steps": [data]}
    try:
        return Envelope.model_validate(data)
    except ValidationError as e:
        raise EnvelopeError(e.errors(include_url=False, include_input=False).__repr__()[:600]) from e


def dump_step(step: Any) -> dict[str, Any]:
    out: dict[str, Any] = _STEP.dump_python(step, mode="json", exclude_none=True)
    return out
