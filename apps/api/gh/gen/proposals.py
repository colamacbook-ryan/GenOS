"""Gen v2 — A4: đề xuất thao tác có xác nhận (docs/design/gen-v1.md §10).

Ưu tiên đã chốt (§9.5): nháp tin gửi đi → nhắc việc → gán người phụ trách. Nguyên tắc:

1. Gen KHÔNG tự ghi gì. Model trả bước `propose` (envelope có kiểu); server kiểm (quyền, mục tiêu registry, id phải
   vừa thấy trong kết quả tool của chính lượt đó) rồi làm giàu thành bước `proposal` (tóm tắt do HỆ THỐNG viết, nhãn
   tên người/việc, cần PIN hay không) và lưu tạm ở Redis (`PROPOSAL_TTL_S`).
2. Chỉ khi người dùng bấm **Xác nhận** trên thẻ, web gọi `POST /gen/proposals/{id}/confirm` (kèm các trường đã sửa).
   Server kiểm lại mọi thứ, rồi gọi NỘI BỘ đúng endpoint sẵn có (`POST /drafts`, `POST /tasks`, `PATCH /tasks/{id}`,
   `POST /inbox/{id}/assign`) bằng chính phiên + CSRF của người đó → endpoint tự kiểm quyền/phạm vi như khi bấm tay.
3. Mục tiêu registry nhạy cảm (`sensitive`) → cần phiên PIN (423 PIN_REQUIRED, web tự hỏi PIN rồi gửi lại).
4. Mỗi lần xác nhận/huỷ ghi Action Log: actor_type="user" (người bấm), detail.via="gen".
5. v0.1.50 (QD-18): thêm 3 loại CHỈ Owner — `memory_note` (Gen nhớ, không PIN), `kho_create` / `kho_update` (ghi Kho
   dữ liệu, bảng Phiên và Việc; PIN `hub.write`). Ghi Kho đi MỘT đường: `confirm_proposal` (Owner + PIN) phát permit ký
   ngay trong `plan_call` (không bao giờ ở bước dựng thẻ) → `POST /hub/kho/write` → `hub_link.service.write_kho`.
   Đề xuất do job `gh.gen.kho_release` dựng (`build_release`) mang thêm khoá meta `release_version` (không thuộc
   `fields`, không sửa được) để mỗi (tổ chức, phiên bản) chỉ ghi vào Kho một lần.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import orjson
from pydantic import BaseModel, Field, ValidationError
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.data.common import mask_text
from gh.errors import ApiError
from gh.gen import envelope, memory_notes, registry
from gh.hub_link import KHO_LABEL, kho_write, load_kho_label, relabel
from gh.hub_link import permit as hub_permit

PROPOSAL_TTL_S = 24 * 3600
CLAIM_TTL_S = 60
# Nhắc việc lùi quá khứ quá mức này thì từ chối (cho phép lệch đồng hồ nhỏ).
PAST_SLACK = timedelta(minutes=2)

TYPE_LABELS = {"draft_message": "Soạn nháp tin gửi đi", "reminder": "Tạo nhắc việc", "assign": "Giao người phụ trách",
               "social_reply": "Trả lời bình luận Facebook", "social_dm": "Nhắn tin Facebook",
               "memory_note": "Ghi nhớ", "kho_create": f"Ghi vào {KHO_LABEL} (tạo mới)",
               "kho_update": f"Ghi vào {KHO_LABEL} (cập nhật)"}
SOCIAL_TYPES = ("social_reply", "social_dm")
SOCIAL_TARGET_PREFIX = "social.write:"
MEMORY_TYPE = "memory_note"
MEMORY_TARGET = "gen.memory"
KHO_TYPES = ("kho_create", "kho_update")
KHO_TARGET_PREFIX = "hub.kho_write:"
OWNER_ONLY_TYPES = SOCIAL_TYPES + (MEMORY_TYPE,) + KHO_TYPES
OWNER_ONLY_MSG = {"social_reply": "chỉ Owner được gửi trả lời / tin nhắn mạng xã hội",
                  "social_dm": "chỉ Owner được gửi trả lời / tin nhắn mạng xã hội",
                  "memory_note": "chỉ Owner được lưu ghi chú Gen nhớ",
                  "kho_create": f"chỉ Owner được ghi vào {KHO_LABEL}",
                  "kho_update": f"chỉ Owner được ghi vào {KHO_LABEL}"}
VALUE_SNIPPET = 400


@dataclass(frozen=True)
class Spec:
    permission: str
    # Trường người dùng được sửa trên thẻ; còn lại (id đối tượng/việc) giữ nguyên như lúc đề xuất.
    editable: tuple[str, ...]


SPECS: dict[str, Spec] = {
    "draft_message": Spec("action.draft", ("title", "text")),
    "reminder": Spec("queue.act", ("title", "remind_at", "due_at", "priority", "assignee_user_id")),
    "assign": Spec("queue.act", ("user_id",)),
    # Chỉ Owner (kiểm riêng ở permission_error); chỉ nội dung trả lời sửa được — tài khoản + đích giữ như lúc đề xuất.
    "social_reply": Spec("system.manage", ("text",)),
    "social_dm": Spec("system.manage", ("text",)),
    # v0.1.50 (QD-18): chỉ Owner (permission_error); ghi chú sửa cả hai trường, Kho chỉ sửa `record` (bảng / mã giữ).
    "memory_note": Spec("system.manage", ("text", "reason")),
    "kho_create": Spec("system.manage", ("record",)),
    "kho_update": Spec("system.manage", ("record",)),
}


def key(pid: Any) -> str:
    return f"gh:gen:proposal:{pid}"


def claim_key(pid: Any) -> str:
    return f"gh:gen:proposal:{pid}:claim"


def target_of(ptype: str, fields: dict[str, Any]) -> str:
    """Mục tiêu registry mà đề xuất gắn vào (quyền + cờ nhạy cảm lấy từ đây)."""
    if ptype == "draft_message":
        return "workbench.drafts"
    if ptype in SOCIAL_TYPES:
        return f"{SOCIAL_TARGET_PREFIX}{fields.get('account_id')}"
    if ptype == MEMORY_TYPE:
        return MEMORY_TARGET
    if ptype in KHO_TYPES:
        return f"{KHO_TARGET_PREFIX}{fields.get('bang') if ptype == 'kho_create' else fields.get('ma')}"
    if ptype == "reminder":
        return "tasks.new"
    return f"{'tasks.row' if fields.get('item_type') == 'task' else 'inbox.row'}:{fields.get('item_id')}"


def requires_pin(target_id: str) -> bool:
    if target_id.startswith(SOCIAL_TARGET_PREFIX):
        return True            # gửi lên mạng xã hội: luôn cần PIN (cả ở bước xác nhận lẫn endpoint /social/.../write)
    if target_id.startswith(KHO_TARGET_PREFIX):
        return True            # ghi Kho dữ liệu: luôn cần PIN (cả ở bước xác nhận lẫn endpoint /hub/kho/write)
    t = registry.resolve_target(target_id)
    return bool(t and t.sensitive)


def _has(permissions: dict[str, str], perm: str | None) -> bool:
    return perm is None or permissions.get(perm, rbac.NONE) != rbac.NONE


async def kho_for(db: AsyncSession, org_id: uuid.UUID, ptype: str) -> str:
    """Tên Kho hiệu lực (v0.1.57, Nợ #30) cho đề xuất loại `ptype`: chỉ loại kho_* đọc tổ chức; loại khác ⇒ mặc định."""
    return await load_kho_label(db, org_id) if ptype in KHO_TYPES else KHO_LABEL


def permission_error(permissions: dict[str, str], ptype: str, target_id: str,
                     role_code: str | None = None, kho: str = KHO_LABEL) -> str | None:
    """Quyền của CHÍNH người hỏi: quyền của loại đề xuất + màn + quyền riêng của mục tiêu registry. Loại social_*,
    memory_note, kho_* KHÔNG tra registry: chỉ Owner (vai trò tuỳ biến có system.manage vẫn bị chặn)."""
    spec = SPECS[ptype]
    if not _has(permissions, spec.permission):
        return f"người hỏi không có quyền '{spec.permission}'"
    if ptype in OWNER_ONLY_TYPES:
        return None if role_code == rbac.OWNER else relabel(OWNER_ONLY_MSG[ptype], kho)
    t = registry.resolve_target(target_id)
    if t is None:
        return f"mục tiêu '{target_id}' không có trong registry"
    if not registry.can_see(permissions, t.screen):
        return f"người hỏi không được xem màn '{t.screen}'"
    if not _has(permissions, t.permission):
        return f"người hỏi không có quyền '{t.permission}'"
    return None


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        return None


def _aware(dt: datetime | None, tz: ZoneInfo) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


async def org_tz(db: AsyncSession, org_id: uuid.UUID) -> ZoneInfo:
    name = (await db.execute(text("SELECT timezone FROM core.organizations WHERE id = :o"),
                             {"o": org_id})).scalar_one_or_none()
    try:
        return ZoneInfo(name or "Asia/Ho_Chi_Minh")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


class _MemoryConfirmFields(envelope.MemoryNoteFields):
    """Ghi nhớ lúc Sếp XÁC NHẬN: lý do không bắt buộc (cùng quy tắc ô "Lý do (không bắt buộc)" ở Cài đặt › Gen nhớ).
    Gen thì vẫn PHẢI nêu lý do khi đề xuất (`envelope.MemoryNoteFields`)."""
    reason: str = Field(default="", max_length=200)


def normalize(ptype: str, fields: dict[str, Any], tz: ZoneInfo, *, confirming: bool = False) -> dict[str, Any]:
    """Kiểm schema loại đề xuất + chuẩn hoá (giờ không múi → múi giờ tổ chức, UUID dạng chuẩn). Lỗi → ValueError.
    `confirming=True` (Sếp bấm Xác nhận, đã có thể sửa trường): Ghi nhớ được để trống lý do."""
    model: type[BaseModel] = (_MemoryConfirmFields if confirming and ptype == MEMORY_TYPE
                              else envelope.PROPOSAL_FIELDS[ptype])
    try:
        m = model.model_validate(fields)
    except ValidationError as e:
        bad = ", ".join(".".join(str(x) for x in err["loc"]) for err in e.errors())
        raise ValueError(f"trường không hợp lệ: {bad}") from e
    out: dict[str, Any] = m.model_dump(mode="python")
    for k in ("remind_at", "due_at"):
        if k in out:
            v = _aware(out[k], tz)
            out[k] = v.astimezone(UTC).isoformat().replace("+00:00", "Z") if v else None
    for k in ("assignee_user_id", "user_id", "item_id", "account_id"):
        if out.get(k) is not None:
            u = _uuid(out[k])
            if u is None and k == "assignee_user_id":
                # v0.1.74 (bản thật 19/10): model hay điền TÊN («Sếp», «anh Tuấn») vào người được giao ⇒ bỏ trường
                # (mặc định là chính người hỏi) thay vì chặn cả lời nhắc của Sếp.
                out.pop(k)
                continue
            if u is None:
                raise ValueError(f"{k} phải là UUID")
            out[k] = str(u)
    subj = out.get("subject")
    if subj is not None:
        u = _uuid(subj.get("id"))
        if u is None:
            raise ValueError("subject.id phải là UUID")
        subj["id"] = str(u)
    if ptype in SOCIAL_TYPES:
        from gh.social.service import clean_write_text

        out["text"] = clean_write_text(out["text"])
        if not out["text"]:
            raise ValueError("nội dung trả lời không được trống")
    if ptype == MEMORY_TYPE:
        out["text"] = memory_notes.clean_text(out["text"])
        reason = memory_notes.clean_reason(out["reason"])
        if not reason and not confirming:
            raise ValueError("lý do ghi nhớ không được trống")
        out["reason"] = reason or ""
    if ptype == "kho_create":
        record = kho_write.validate_record(out["bang"], out["record"], create=True)
        out["record"] = kho_write.fill_defaults(out["bang"], record)
    if ptype == "kho_update":
        bang = kho_write.bang_of_ma(out["ma"])
        if bang is None:
            raise ValueError("ma phải dạng PHIEN-12 hoặc VIEC-12")
        out["record"] = kho_write.validate_record(bang, out["record"], create=False)
    if ptype == "reminder":
        remind = datetime.fromisoformat(out["remind_at"].replace("Z", "+00:00"))
        if remind < datetime.now(UTC) - PAST_SLACK:
            raise ValueError("giờ nhắc đã qua")
    return out


def id_errors(ptype: str, fields: dict[str, Any], seen_ids: set[str], self_id: str) -> str | None:
    """Chống bịa id: id đối tượng / việc / người phải vừa xuất hiện trong kết quả tool của lượt này."""
    ids: list[str] = []
    if fields.get("subject"):
        ids.append(fields["subject"]["id"])
    if ptype == "assign":
        ids += [fields["item_id"], fields["user_id"]]
    if ptype == "reminder" and fields.get("assignee_user_id"):
        ids.append(fields["assignee_user_id"])
    if ptype == "kho_update" and fields["ma"] not in seen_ids:
        return f"mã '{fields['ma']}' không có trong kết quả hub.kho_* của lượt này — đọc bản ghi trước rồi mới đề xuất"
    for i in ids:
        if i != self_id and i not in seen_ids:
            return f"id '{i}' không có trong kết quả tool của lượt này"
    return None


def _snippet(v: Any, n: int) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


async def social_labels(db: AsyncSession, user: service.CurrentUser, ptype: str, fields: dict[str, Any],
                        redis: Redis | None) -> dict[str, str]:
    """Nhãn thẻ đề xuất gửi mạng xã hội: tài khoản (thuộc tổ chức, chưa gỡ), đích LẤY TỪ mục đọc khớp target_url trong 7
    ngày (Gen không được bịa link), trạng thái cổng F-85, cờ đáng ngờ."""
    from gh.social import permit
    from gh.social import service as social

    label = (await db.execute(text("""SELECT label FROM core.social_accounts
                                      WHERE id = :i AND org_id = :o AND status <> 'revoked'"""),
                              {"i": fields["account_id"], "o": user.org_id})).scalar_one_or_none()
    if label is None:
        raise ValueError("tài khoản mạng xã hội không tồn tại")
    if not social.write_target_ok(permit.PROPOSAL_ACTION[ptype], fields["target_url"]):
        raise ValueError("target_url không trỏ tới một bình luận cụ thể (không có comment_id) — chỉ trả lời được vào "
                         "thông báo về bình luận")
    item = await social.find_read_item(db, user.org_id, uuid.UUID(fields["account_id"]),
                                       permit.PROPOSAL_ACTION[ptype], fields["target_url"])
    if item is None:
        raise ValueError("target_url không phải mục Gen vừa đọc từ tài khoản này trong 7 ngày qua")
    if ptype == "social_dm":
        prefix = f"Hội thoại với {_snippet(item.get('who') or 'người gửi', 40)}: “"
    else:
        prefix = "Bình luận/Thông báo: “"
    out = {"account": label, "target": prefix + _snippet(item.get("text"), 120 - len(prefix) - 1) + "”",
           "write_gate": "locked"}
    if redis is not None and (await social.write_gate(db, redis, user.org_id))["open"]:
        out["write_gate"] = "open"
    if item.get("suspicious"):
        out["suspicious"] = "1"
    return out


def _record_fields(data: Any, bang: str, depth: int = 0) -> dict[str, Any] | None:
    """Các trường của bản ghi Kho trong kết quả `kho_find_by_id` (đã che): dict có ít nhất một tên trường của bảng, hoặc
    nằm trong `fields` / `structuredContent` / JSON ở `content[].text`. Không thấy ⇒ None."""
    if depth > 5:
        return None
    names = kho_write.KHO_FIELDS[bang]
    if isinstance(data, dict):
        if any(k in data for k in names):
            return {k: data[k] for k in names if k in data}
        for v in data.values():
            found = _record_fields(v, bang, depth + 1)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _record_fields(v, bang, depth + 1)
            if found is not None:
                return found
    elif isinstance(data, str) and data.strip()[:1] in ("{", "["):
        try:
            return _record_fields(orjson.loads(data), bang, depth + 1)
        except orjson.JSONDecodeError:
            return None
    return None


async def kho_labels(db: AsyncSession, user: service.CurrentUser, ptype: str, fields: dict[str, Any],
                     redis: Redis | None, app: Any) -> dict[str, str]:
    """Nhãn thẻ ghi Kho: bảng, đích ('Tạo mới ở bảng Phiên' | 'VIEC-12 · <tiêu đề hiện tại>'), quyền ghi ('ok' |
    'missing') và với kho_update `cur:<tên trường>` = giá trị HIỆN TẠI của trường sẽ sửa (đọc qua Gen-hub, đã che, đi
    qua đệm 5 phút). Đọc lỗi, Kho báo lỗi (`isError`) hoặc kết quả không nhận ra được các trường của bảng (chữ thường,
    Markdown, hình dạng lạ) ⇒ ValueError, KHÔNG có đề xuất — Sếp không được xác nhận một thay đổi mà thẻ không cho thấy
    giá trị cũ (thẻ trống cột Hiện tại sẽ đọc thành "đang trống")."""
    from gh.hub_link import service as hub

    link = await hub.load(db, user.org_id)
    # Quyền theo ĐÚNG tool của đề xuất (thiếu kho_update không khoá thẻ tạo mới) — như `write_kho` kiểm khi ghi.
    ok = (link is not None and link.server_id is not None and bool(link.enabled)
          and (await hub.write_scopes(db, user.org_id))[ptype])
    out = {"write_scope": "ok" if ok else "missing"}
    if ptype == "kho_create":
        return {"bang": fields["bang"], "target": f"Tạo mới ở bảng {fields['bang']}", **out}
    ma = str(fields["ma"])
    bang = kho_write.bang_of_ma(ma) or ""
    transport = getattr(getattr(app, "state", None), "mcp_transport", None)
    try:
        res = await hub.call_hub(db, redis, hub.client_for(transport), user=user, suffix="kho_find_by_id",
                                 args={"id": ma})
    except ApiError as e:
        raise ValueError("chưa đọc được bản ghi Kho") from e
    data = res.get("data")
    if isinstance(data, dict) and data.get("isError") is True:
        raise ValueError(f"Kho báo lỗi khi đọc {ma} — chưa đọc được giá trị hiện tại")
    cur = _record_fields(data, bang) if bang else None
    if cur is None:
        raise ValueError(f"chưa đọc được giá trị hiện tại của {ma} (Kho trả kết quả không nhận ra các trường)")
    title = _snippet(cur.get(kho_write.REQUIRED[bang]), 80)
    labs = {"bang": bang, "target": f"{ma} · {title}" if title else ma, **out}
    for k in fields["record"]:
        labs[f"cur:{k}"] = _snippet(cur.get(k, ""), VALUE_SNIPPET)
    return labs


async def memory_error(db: AsyncSession, org_id: uuid.UUID, fields: dict[str, Any]) -> str | None:
    """Lý do (cho model biết) không thể đề xuất ghi nhớ: đã đủ 30 ghi chú hoặc trùng một ghi chú có sẵn."""
    if await memory_notes.count(db, org_id) >= memory_notes.MAX_NOTES:
        return memory_notes.FULL_MSG
    if await memory_notes.duplicate_of(db, org_id, fields["text"]):
        return memory_notes.DUPLICATE_MSG
    return None


async def labels(db: AsyncSession, user: service.CurrentUser, ptype: str, fields: dict[str, Any],
                 redis: Redis | None = None, app: Any = None) -> dict[str, str]:
    """Nhãn hiển thị cho thẻ (tên người, việc, đối tượng). Người được giao phải là người dùng còn hoạt động."""
    if ptype in SOCIAL_TYPES:
        return await social_labels(db, user, ptype, fields, redis)
    if ptype == MEMORY_TYPE:
        return {"count": f"{await memory_notes.count(db, user.org_id)}/{memory_notes.MAX_NOTES}"}
    if ptype in KHO_TYPES:
        return await kho_labels(db, user, ptype, fields, redis, app)
    owner = user.role_code == rbac.OWNER
    out: dict[str, str] = {}
    uid = fields.get("user_id") if ptype == "assign" else fields.get("assignee_user_id")
    if uid:
        name = (await db.execute(text("""SELECT display_name FROM core.users
                                         WHERE id = :u AND org_id = :o AND is_active AND deleted_at IS NULL"""),
                                 {"u": uid, "o": user.org_id})).scalar_one_or_none()
        if name is None:
            raise ValueError("người được giao không tồn tại hoặc đã bị khoá")
        out["user"] = name
    subj = fields.get("subject")
    if subj:
        if subj["type"] == "person":
            n = (await db.execute(text("SELECT display_name FROM core.persons WHERE id = :i AND org_id = :o"),
                                  {"i": subj["id"], "o": user.org_id})).scalar_one_or_none()
        else:
            n = (await db.execute(text("SELECT name FROM core.groups WHERE id = :i AND org_id = :o"),
                                  {"i": subj["id"], "o": user.org_id})).scalar_one_or_none()
        if n is None:
            raise ValueError("đối tượng không tồn tại")
        out["subject"] = mask_text(n, owner) or ""
    if ptype == "assign" and fields["item_type"] == "task":
        r = (await db.execute(text("SELECT code, title FROM biz.tasks WHERE id = :i AND org_id = :o"),
                              {"i": fields["item_id"], "o": user.org_id})).one_or_none()
        if r is None:
            raise ValueError("việc không tồn tại")
        out["item"] = f"{r.code} · {mask_text(r.title, owner)}"
    elif ptype == "assign":
        # Hiện ĐÚNG mục sẽ bị giao (mã + tiêu đề, theo phạm vi queue.read của người hỏi) — không để thẻ mơ hồ.
        from gh.biz.core.scope import scope_for
        from gh.biz.queue.routes import _item_payload, _load_item
        from gh.errors import ApiError

        try:
            r = await _load_item(db, user, await scope_for(db, user, "queue.read"),
                                 uuid.UUID(fields["item_id"]))
        except ApiError as e:
            raise ValueError("mục trong Hộp thư không tồn tại hoặc ngoài phạm vi") from e
        item = _item_payload(r, owner=owner)
        out["item"] = f"{item['code']} · {item['title']}" if item.get("code") else str(item["title"] or "")
    return out


def _fmt_time(iso: str | None, tz: ZoneInfo) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz).strftime("%H:%M %d/%m/%Y")


def summary(ptype: str, fields: dict[str, Any], lab: dict[str, str], tz: ZoneInfo, kho: str = KHO_LABEL) -> str:
    """Tóm tắt do HỆ THỐNG viết từ các trường đã kiểm — không dùng lời model (chống prompt injection). `kho` = tên
    Kho hiệu lực của tổ chức (v0.1.57)."""
    if ptype == "draft_message":
        to = f" cho {lab['subject']}" if lab.get("subject") else ""
        return (f"Soạn bản nháp tin “{fields['title']}”{to}. Bản nháp vào Bàn làm việc chờ duyệt — "
                "chưa gửi đi.")
    if ptype == "reminder":
        who = f", giao cho {lab['user']}" if lab.get("user") else ""
        due = f", hạn {_fmt_time(fields.get('due_at'), tz)}" if fields.get("due_at") else ""
        return (f"Tạo nhắc việc “{fields['title']}” ({fields['priority']}), nhắc lúc "
                f"{_fmt_time(fields['remind_at'], tz)}{due}{who}.")
    if ptype == MEMORY_TYPE:
        why = f" (lý do: {fields['reason']})" if fields.get("reason") else ""
        return (f"Ghi nhớ: “{fields['text']}”{why}. Gen dùng ghi chú này khi trả lời và khi "
                "soạn Bản tin; sửa/xoá ở Cài đặt › Bộ não AI › Gen nhớ.")
    if ptype in KHO_TYPES:
        listing = "; ".join(f"{k} = “{_snippet(v, VALUE_SNIPPET)}”" for k, v in fields["record"].items())
        if ptype == "kho_create":
            head = f"Tạo bản ghi mới ở bảng {fields['bang']} của {kho}: {listing}."
        else:
            head = f"Cập nhật {fields['ma']} (bảng {lab.get('bang', '')}) ở {kho}: {listing}."
        return f"{head} Chỉ ghi khi Sếp bấm Xác nhận và nhập mã PIN (qua Gen-hub)."
    if ptype in SOCIAL_TYPES:
        verb = "Trả lời trên Facebook" if ptype == "social_reply" else "Nhắn tin trên Facebook"
        return (f"{verb} ({lab.get('account', '')}) vào {lab.get('target', '')}: “{fields['text']}”. "
                "Gửi NGAY khi Sếp bấm Xác nhận và nhập mã PIN; có ảnh chụp làm bằng chứng.")
    return f"Giao {lab.get('item', 'mục này')} cho {lab.get('user', 'người được chọn')}."


def public(p: dict[str, Any]) -> dict[str, Any]:
    """Phần gửi xuống web (không kèm user_id/org_id)."""
    return {k: p[k] for k in ("id", "type", "fields", "summary", "labels", "target", "requires_pin", "status")
            if k in p} | ({"result": p["result"]} if p.get("result") else {})


async def build(db: AsyncSession, user: service.CurrentUser, prop: Any, seen_ids: set[str], tz: ZoneInfo,
                *, turn_id: uuid.UUID, conversation_id: uuid.UUID, redis: Redis | None = None,
                app: Any = None) -> tuple[dict[str, Any] | None, str | None]:
    """Kiểm + làm giàu một đề xuất của model. Trả (đề xuất đầy đủ, None) hoặc (None, lý do chặn)."""
    ptype: str = prop.type
    try:
        fields = normalize(ptype, prop.fields.model_dump(mode="python"), tz)
    except ValueError as e:
        return None, str(e)
    if ptype == "reminder" and not fields.get("assignee_user_id"):
        fields["assignee_user_id"] = str(user.id)  # mặc định nhắc chính người hỏi
    target = target_of(ptype, fields)
    kho = await kho_for(db, user.org_id, ptype)
    err = permission_error(user.permissions, ptype, target, user.role_code, kho) \
        or id_errors(ptype, fields, seen_ids, str(user.id))
    if err is None and ptype == MEMORY_TYPE:
        err = await memory_error(db, user.org_id, fields)
    if err:
        return None, err
    try:
        lab = await labels(db, user, ptype, fields, redis, app)
    except ValueError as e:
        return None, str(e)
    if kho != KHO_LABEL:
        lab["kho"] = kho           # v0.1.57: thẻ web dùng tên Kho Owner tự đặt (mặc định ⇒ không thêm nhãn, thẻ như cũ)
    pid = uuid.uuid4()
    return {"id": str(pid), "type": ptype, "fields": fields, "labels": lab, "target": target,
            "summary": summary(ptype, fields, lab, tz, kho), "requires_pin": requires_pin(target), "status": "pending",
            "user_id": str(user.id), "org_id": str(user.org_id), "turn_id": str(turn_id),
            "conversation_id": str(conversation_id)}, None


async def save(redis: Redis, p: dict[str, Any], ttl: int = PROPOSAL_TTL_S) -> None:
    await redis.set(key(p["id"]), orjson.dumps(p), ex=ttl)


async def load(redis: Redis, pid: Any) -> dict[str, Any] | None:
    raw = await redis.get(key(pid))
    return orjson.loads(raw) if raw is not None else None


async def load_many(redis: Redis, pids: Sequence[Any]) -> list[tuple[dict[str, Any] | None, int]]:
    """Đọc NHIỀU đề xuất trong MỘT lượt Redis (pipeline GET + TTL, không giao dịch): [(đề xuất | None, TTL giây)] đúng
    thứ tự `pids`. TTL ≤ 0 = không còn hạn / không có khoá. Dùng cho `GET /owner/today|proposals` (trước đây 2 lượt
    tuần tự cho MỖI đề xuất)."""
    if not pids:
        return []
    pipe = redis.pipeline(transaction=False)
    for pid in pids:
        pipe.get(key(pid))
        pipe.ttl(key(pid))
    got = await pipe.execute()
    out: list[tuple[dict[str, Any] | None, int]] = []
    for i in range(len(pids)):
        raw, ttl = got[2 * i], got[2 * i + 1]
        out.append((orjson.loads(raw) if raw is not None else None, int(ttl or 0)))
    return out


# ── Thực hiện sau khi người dùng xác nhận ──

@dataclass
class Call:
    method: str
    path: str
    body: dict[str, Any]
    result_type: str


async def plan_call(db: AsyncSession, user: service.CurrentUser, ptype: str, f: dict[str, Any],
                    pid: str | None = None) -> Call:
    """Endpoint SẴN CÓ tương ứng loại đề xuất (không có đường ghi riêng cho Gen)."""
    if ptype == MEMORY_TYPE:
        return Call("POST", "/gen/memory", {"text": f["text"], "reason": f["reason"] or None, "proposal_id": pid},
                    "memory_note")
    if ptype in KHO_TYPES:
        # Permit ký PHÁT Ở ĐÂY — hàm này chỉ được `confirm_proposal` gọi, SAU khi qua kiểm quyền + PIN (không bao giờ ở
        # bước dựng thẻ). Gắn đúng đề xuất + tool + sha256 tham số; `write_kho` kiểm lại, nonce dùng một lần.
        if pid is None:
            raise ValueError("thiếu mã đề xuất")
        args = kho_write.tool_args(ptype, f["bang"] if ptype == "kho_create" else f["ma"], f["record"])
        permit_ = hub_permit.issue(user.org_id, user.id, pid, ptype, args)
        return Call("POST", "/hub/kho/write", {"proposal_id": pid, "tool": ptype, "args": args, "permit": permit_},
                    "kho_record")
    if ptype in SOCIAL_TYPES:
        from gh.social import permit

        return Call("POST", f"/social/accounts/{f['account_id']}/write",
                    {"action": permit.PROPOSAL_ACTION[ptype], "target_url": f["target_url"], "text": f["text"],
                     "proposal_id": pid}, "social_write")
    if ptype == "draft_message":
        body: dict[str, Any] = {"kind": "message", "title": f["title"], "text": f["text"],
                                "sources": [{"label": "Gen đề xuất, người dùng xác nhận"}]}
        subj = f.get("subject")
        if subj:
            body["subject"] = subj
            if subj["type"] == "group":
                ch = (await db.execute(text("""SELECT c.type FROM core.groups g JOIN core.channels c
                                               ON c.id = g.channel_id WHERE g.id = :g AND g.org_id = :o"""),
                                       {"g": subj["id"], "o": user.org_id})).scalar_one_or_none()
                if ch:
                    body["target"] = {"channel": ch, "thread_type": "group", "group_id": subj["id"]}
        return Call("POST", "/drafts", body, "draft")
    if ptype == "reminder":
        body = {"title": f["title"], "priority": f["priority"], "remind_at": f["remind_at"],
                "due_at": f.get("due_at"), "assignee_user_id": f.get("assignee_user_id")}
        if f.get("subject"):
            body["subject"] = f["subject"]
        return Call("POST", "/tasks", body, "task")
    if f["item_type"] == "task":
        return Call("PATCH", f"/tasks/{f['item_id']}", {"assignee_user_id": f["user_id"]}, "task")
    return Call("POST", f"/inbox/{f['item_id']}/assign", {"user_id": f["user_id"]}, "inbox_item")


async def call_as_user(app: Any, cookies: dict[str, str], csrf: str, call: Call,
                       ip: str | None = None) -> tuple[int, Any]:
    """Gọi NỘI BỘ (ASGI) endpoint sẵn có bằng phiên + CSRF của chính người bấm — tái dùng nguyên RBAC/phạm vi.
    Người gọi PHẢI đã đóng transaction của request ngoài (xem routes.confirm_proposal)."""
    headers = {"x-csrf-token": csrf} | ({"x-forwarded-for": ip} if ip else {})  # Action Log giữ IP thật
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gen.internal", cookies=cookies,
                                 timeout=30.0) as c:
        r = await c.request(call.method, f"/api/v1{call.path}", json=call.body,
                            headers=headers)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def result_of(call: Call, fields: dict[str, Any], body: Any) -> dict[str, Any]:
    b = body if isinstance(body, dict) else {}
    if call.result_type == "social_write":
        return {"type": "social_write", "id": str(b.get("id")) if b.get("id") else None, "code": None,
                "screen": "social", "status": b.get("status")}
    if call.result_type == "memory_note":
        return {"type": "memory_note", "id": str(b.get("id")) if b.get("id") else None, "code": None,
                "screen": "system"}
    if call.result_type == "kho_record":
        ma = b.get("ma") if isinstance(b.get("ma"), str) else None
        bang = b.get("bang") or fields.get("bang") or (kho_write.bang_of_ma(ma) if ma else None) \
            or kho_write.bang_of_ma(str(fields.get("ma") or ""))
        return {"type": "kho_record", "id": None, "code": ma, "screen": None, "bang": bang}
    if call.result_type == "inbox_item":
        return {"type": "inbox_item", "id": fields["item_id"], "screen": "inbox"}
    rid = b.get("id")
    out = {"type": call.result_type, "id": str(rid) if rid else None, "code": b.get("code"),
           "screen": "workbench" if call.result_type == "draft" else "tasks"}
    if call.result_type == "draft":
        # v0.1.43 (F-24): nháp chỉ GỬI ĐƯỢC khi có nơi gửi (hiện chỉ đối tượng là NHÓM mới gắn target — plan_call).
        # Không có target thì duyệt ở Bàn làm việc sẽ NO_TARGET ⇒ web không được hứa "Duyệt & gửi".
        out["sendable"] = bool(call.body.get("target"))
    return out


# ── v0.1.50 (F-87): đề xuất ghi Phiên vào Kho cho MỖI bản mới (job gh.gen.kho_release) ──

def release_record(version: str, today: str, repo: str) -> dict[str, str]:
    return {"Chủ đề": f"Gen-Harness lên bản {version}", "Ngày": today,
            "Đã chốt": f"Máy chủ Gen-Harness đã nâng lên {version}. Ghi chú phát hành: "
                       f"https://github.com/{repo}/releases/tag/{version}"}


def build_release(org_id: uuid.UUID, owner_id: uuid.UUID, version: str, *, turn_id: uuid.UUID,
                  conversation_id: uuid.UUID, tz: ZoneInfo, repo: str, now: datetime | None = None,
                  kho: str = KHO_LABEL) -> dict[str, Any]:
    """Đề xuất `kho_create` bảng Phiên cho Owner `owner_id` — tóm tắt / nhãn do HỆ THỐNG viết, cùng khuôn với đề xuất
    của model. `release_version` là khoá meta (không thuộc `fields`, không sửa được): `confirm_proposal` dựa vào đó để
    chỉ cho MỘT Owner ghi mỗi (tổ chức, phiên bản). Quyền ghi đã được job kiểm trước khi dựng (nhãn 'ok')."""
    today = kho_write.vn_today(now).isoformat()
    fields = normalize("kho_create", {"bang": "Phiên", "record": release_record(version, today, repo)}, tz)
    # `release` = phiên bản: thẻ nói rõ mỗi bản ghi MỘT lần cho cả tổ chức (Huỷ = huỷ cho mọi Owner).
    lab = {"bang": "Phiên", "target": "Tạo mới ở bảng Phiên", "write_scope": "ok", "release": version}
    if kho != KHO_LABEL:
        lab["kho"] = kho
    target = target_of("kho_create", fields)
    pid = uuid.uuid4()
    return {"id": str(pid), "type": "kho_create", "fields": fields, "labels": lab, "target": target,
            "summary": summary("kho_create", fields, lab, tz, kho), "requires_pin": requires_pin(target),
            "status": "pending", "user_id": str(owner_id), "org_id": str(org_id), "turn_id": str(turn_id),
            "conversation_id": str(conversation_id), "release_version": version}
