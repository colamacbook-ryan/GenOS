# ruff: noqa: E501
"""Công cụ dữ liệu CHỈ ĐỌC của Gen (docs/design/gen-v1.md §3.4; v2 thêm task.list, staff.list;
v0.1.25 thêm refinery.summary; v0.1.26 thêm hub.kho_* — đọc Kho dữ liệu qua Gen-hub, chỉ Owner, đã che trước khi vào
model; v0.1.49 (QD-16) thêm document.*/deal.*/case.* — Tài liệu, Deal, Vụ việc nội bộ, và hub.calendar/tasks/
mail_search/mail_read/drive_search — lịch, việc, mail, Drive Google qua Gen-hub; tất cả CHỈ ĐỌC, chỉ Owner, nội dung
đã che trước khi vào model và coi là dữ liệu không tin cậy).

Mỗi tool là lớp bọc mỏng quanh một endpoint GET đã có, gọi NỘI BỘ (ASGI, không qua mạng) bằng chính cookie phiên
của người đang hỏi → tái dùng nguyên RBAC, phạm vi dữ liệu và lớp che của endpoint; Gen không có quyền riêng, không
có truy vấn SQL riêng. Kết quả được cắt gọn (≤ 4 KB, ≤ 20 dòng) trước khi đưa model; mọi id xuất hiện trong kết quả
được ghi lại để validator chỉ cho phép làm sáng dòng có thật (chống model bịa id).
"""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import orjson

from gh.auth import rbac, service
from gh.chassis.masking import mask_for_prompt
from gh.gen import registry
from gh.hub_link import KHO_LABEL

MAX_BYTES = 4000
MAX_ROWS = 20
# v0.1.50 (F-81): "Mã ID" là khoá mã bản ghi Kho (PHIEN-12, VIEC-3) trong kết quả hub.kho_* — đề xuất kho_update chỉ
# được dùng mã đã xuất hiện ở đây (proposals.id_errors).
ID_KEYS = ("id", "code", "n", "person_id", "draft_id", "item_id", "subject_id", "Mã ID")
# Mã Phiên / Việc nằm trong CHỮ của kết quả Kho (Kho trả `content[].text`): cũng được coi là mã đã thấy.
KHO_CODE_RE = re.compile(r"\b(?:PHIEN|VIEC)-\d{1,6}\b")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # Cần ít nhất một trong các quyền này (kiểm trước; endpoint còn tự kiểm lại phạm vi).
    permissions: tuple[str, ...]
    path: str | None = None
    # Tham số query cho phép: tên → danh sách giá trị hợp lệ (None = chuỗi tự do ≤ 120 ký tự).
    query: dict[str, tuple[str, ...] | None] = field(default_factory=dict)
    # Tham số nằm trong đường dẫn (vd {id}) — phải là UUID.
    path_args: tuple[str, ...] = ()
    default_query: dict[str, str] = field(default_factory=dict)
    # Tham số đường dẫn KHÔNG phải UUID: tên → regex phải khớp (vd mã Kho VIEC-12).
    path_patterns: dict[str, str] = field(default_factory=dict)
    # Chỉ vai trò Owner (quyết định Boss: Gen đọc Kho chỉ cho Owner) — endpoint còn tự kiểm lại.
    owner_only: bool = False


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("overview.summary", "Số liệu Hôm nay + hàng đợi cần xử lý (cơ hội, cảnh báo, chờ duyệt, đến hạn)",
         ("overview.read",), "/overview"),
    Tool("queue.list", "Hộp thư; args.tab ∈ all|opportunity|alert|approval|reply|candidate",
         ("queue.read",), "/inbox",
         {"tab": ("all", "opportunity", "alert", "approval", "reply", "candidate"), "intent": None},
         default_query={"limit": "20"}),
    Tool("draft.list", "Bản nháp; args.status ∈ pending|decided|all (pending = đang chờ duyệt, kèm lý do giữ)",
         ("action.draft", "action.approve"), "/drafts", {"status": ("pending", "decided", "all"), "kind": None},
         default_query={"limit": "20"}),
    Tool("draft.get", "Chi tiết một bản nháp theo args.id (lý do bị giữ, người soạn, nội dung)",
         ("action.draft", "action.approve"), "/drafts/{id}", path_args=("id",)),
    Tool("profile.search", "Tìm hội thoại/khách theo ngôn ngữ tự nhiên; args.q", ("opportunity.read",), "/search",
         {"q": None}, default_query={"limit": "20"}),
    Tool("profile.get", "Hồ sơ sống của một người theo args.id", ("profile.read",), "/profile/{id}",
         path_args=("id",)),
    # v0.1.74 (Nợ #68): danh bạ nhóm / người — để Gen tra id người nhận trước khi đề xuất draft_message (id lấy từ kết quả
    # tool mới hợp lệ). Bọc endpoint danh bạ sẵn có nên áp đúng phạm vi `profile.read` của người hỏi.
    Tool("group.list", "Danh bạ NHÓM chat (id, tên, kênh, số thành viên); args.q = một phần tên nhóm — dùng để lấy id nhóm "
         "trước khi đề xuất soạn tin gửi nhóm", ("profile.read",), "/gen/sources/groups", {"q": None},
         default_query={"limit": "10"}),
    Tool("group.recent", "Nhóm nào có tin mới: số tin, giờ tin cuối và vài đoạn trích mới nhất mỗi nhóm; args.hours "
         "1..168 (mặc định 24), args.group_id (tuỳ chọn, UUID từ group.list: chỉ nhóm đó, nhiều tin hơn). Chỉ nhóm Sếp "
         "được xem", ("profile.read",), "/gen/sources/groups/recent",
         {"hours": tuple(str(i) for i in range(1, 169)), "group_id": None}, default_query={"limit": "10"}),
    Tool("person.list", "Danh bạ NGƯỜI (id, tên, kênh, quan hệ); args.q = một phần tên — dùng để lấy id người nhận trước khi "
         "đề xuất soạn tin", ("profile.read",), "/gen/sources/people", {"q": None}, default_query={"limit": "10"}),
    Tool("opportunity.list", "Cơ hội; args.stage, args.confidence ∈ high|medium|low", ("opportunity.read",),
         "/opportunities", {"stage": None, "confidence": ("high", "medium", "low")}, default_query={"limit": "20"}),
    Tool("people.care", "Thời gian phản hồi khách theo từng nhân viên", ("care.read",), "/care/response-times"),
    Tool("audit.list", "Nhật ký hành động gần nhất; args.action (tiền tố, vd gen.)", ("audit.read",), "/audit",
         {"action": None}, default_query={"limit": "20"}),
    Tool("system.health", "Tình trạng CSDL, Redis, kho tệp, bridge kênh", ("system.read",), "/ready"),
    Tool("guide.list", "Các việc thiết lập tuỳ chọn 5–11, 13, 14: vì sao cần, các bước, đã xong chưa",
         ("system.manage",)),
    # v0.1.54 (g1-api): Gen hướng dẫn — gộp /gen/coach/today (không đánh dấu đã xem) + /gen/coach/curriculum. Chỉ Owner.
    Tool("coach.status", "Việc vận hành Sếp cần làm ngay, tiến độ x/N, bài học hôm nay và danh sách bài",
         ("system.manage",), owner_only=True),
    Tool("screens.list", "Các màn Sếp được xem (khoá + tên) — dùng khi cần mở màn", ()),
    Tool("task.list", "Việc & nhắc hẹn; args.status ∈ todo|doing|done|cancelled (id việc dùng cho đề xuất gán người)",
         ("queue.read",), "/tasks", {"status": ("todo", "doing", "done", "cancelled")}, default_query={"limit": "20"}),
    Tool("staff.list", "Người trong tổ chức có thể giao việc (id, tên, vai trò) — dùng trước khi đề xuất gán người",
         ("queue.act",), "/gen/assignees"),
    Tool("refinery.summary", "Lọc tin Hộp thư (Jev/quy tắc): số mục đã lọc, trùng, rác, điểm thấp, chờ lọc, độ trễ và "
         "độ khớp của Jev; args.days ∈ 1|7|30", ("queue.read",), "/refinery/triage/summary",
         {"days": ("1", "7", "30")}),
    Tool("hub.kho_summary", f"{KHO_LABEL} qua Gen-hub (chỉ đọc): tóm tắt đầu phiên — Phiên gần nhất, Việc đang mở, "
         "Quyết định hiệu lực, Dự án trọng tâm; args.so_phien ∈ 1..5. Trích dẫn bằng mã (VIEC-/QD-/PHIEN-)",
         ("system.manage",), "/hub/kho/summary", {"so_phien": ("1", "2", "3", "4", "5")}, owner_only=True),
    Tool("hub.kho_search", f"Tìm trong {KHO_LABEL} theo từ khoá; args.q, args.bang (tuỳ chọn: Việc, Dự án, Phiên, "
         "Quyết định, Bài học, Tri thức — Tri thức chỉ là bản sao từ GitHub)", ("system.manage",), "/hub/kho/search",
         {"q": None, "bang": None}, owner_only=True),
    Tool("hub.kho_get", f"Một bản ghi {KHO_LABEL} theo mã; args.ma (vd VIEC-12, QD-3, PHIEN-1)", ("system.manage",),
         "/hub/kho/records/{ma}", path_args=("ma",), path_patterns={"ma": r"^[A-Za-z]{2,6}-\d{1,6}$"},
         owner_only=True),
    # v0.1.49 (QD-16): Gen đọc lịch, việc, mail, Drive Google qua Gen-hub — CHỈ ĐỌC, chỉ Owner; endpoint (gh.hub_link)
    # tự che nội dung và chặn mọi tool ghi. Mã mail phân biệt hoa/thường nên `hub.mail_read` đi bằng QUERY
    # (path_patterns sẽ viết HOA giá trị).
    Tool("hub.calendar", "Lịch Google của Sếp qua Gen-hub (chỉ đọc); args.day ∈ today|tomorrow",
         ("system.manage",), "/hub/google/calendar", {"day": ("today", "tomorrow")}, owner_only=True),
    Tool("hub.tasks", "Việc đang mở (Google Tasks) qua Gen-hub (chỉ đọc)", ("system.manage",),
         "/hub/google/tasks", owner_only=True),
    Tool("hub.mail_search", "Tìm mail (cú pháp Gmail, vd is:unread from:x) qua Gen-hub, chỉ đọc; trả id/tiêu đề/người "
         "gửi/đoạn trích đã che; args.q", ("system.manage",), "/hub/google/mail/search", {"q": None}, owner_only=True),
    Tool("hub.mail_read", "Đọc một mail theo args.id (lấy từ hub.mail_search), đã che", ("system.manage",),
         "/hub/google/mail/message", {"id": None}, owner_only=True),
    Tool("hub.drive_search", "Tìm tệp Google Drive theo tên (chỉ đọc, không đọc/sửa nội dung); args.q",
         ("system.manage",), "/hub/google/drive/search", {"q": None}, owner_only=True),
    # v0.1.49 (QD-16): Tài liệu, Deal, Vụ việc nội bộ — chỉ Owner; endpoint /gen/sources/* tái dùng phạm vi/ACL của
    # cụm Quan hệ & Thị trường rồi che email/SĐT/số dài. Tài liệu chỉ siêu dữ liệu, không đọc nội dung tệp.
    Tool("document.list", "Tài liệu nội bộ (báo giá, hợp đồng, tệp): tiêu đề, mô tả, người/nhóm gắn, nguồn; "
         "args.source ∈ channel|agent|tay", ("profile.read",), "/gen/sources/documents",
         {"source": ("channel", "agent", "tay")}, default_query={"limit": "20"}, owner_only=True),
    Tool("document.get", "Siêu dữ liệu một tài liệu theo args.id (không đọc nội dung tệp)", ("profile.read",),
         "/gen/sources/documents/{id}", path_args=("id",), owner_only=True),
    Tool("deal.list", "Deal (thương vụ): mã, khách, số tiền, trạng thái; args.status ∈ open|won|lost",
         ("opportunity.read",), "/gen/sources/deals", {"status": ("open", "won", "lost")},
         default_query={"limit": "20"}, owner_only=True),
    Tool("deal.get", "Một deal theo args.id", ("opportunity.read",), "/gen/sources/deals/{id}", path_args=("id",),
         owner_only=True),
    Tool("case.list", "Vụ việc (khiếu nại): mã, tiêu đề, mức ưu tiên, người xử lý; args.status, "
         "args.priority ∈ P1|P2|P3", ("opportunity.read",), "/gen/sources/cases",
         {"status": None, "priority": ("P1", "P2", "P3")}, default_query={"limit": "20"}, owner_only=True),
    Tool("case.get", "Một vụ việc theo args.id", ("opportunity.read",), "/gen/sources/cases/{id}", path_args=("id",),
         owner_only=True),
    # Mạng xã hội — chỉ Owner. social.read xếp một lượt đọc trình duyệt (tính vào giới hạn 6 lượt/ngày; dùng lại lượt
    # vừa đọc trong 10 phút) rồi chờ ngắn; nội dung đã làm sạch + gắn cờ đáng ngờ. Muốn trả lời/nhắn thì Gen ĐỀ XUẤT
    # (social_reply/social_dm) — Sếp xác nhận + nhập PIN mới gửi.
    Tool("social.accounts", "Tài khoản mạng xã hội đã kết nối (id, tên, nền tảng, trạng thái, lần đọc gần nhất)",
         ("system.manage",), "/social/accounts", owner_only=True),
    Tool("social.read", "Đọc thông báo + danh sách hội thoại (xem trước tin mới nhất) của một tài khoản mạng xã hội; "
         "args.account_id (tuỳ chọn — bỏ trống = tài khoản đang hoạt động đầu tiên). Tốn 1 lượt (tối đa 6 lượt/ngày); "
         "muốn trả lời/nhắn thì đề xuất social_reply/social_dm",
         ("system.manage",), owner_only=True),
)}


def allowed(user: service.CurrentUser, tool: Tool) -> bool:
    if tool.owner_only and user.role_code != rbac.OWNER:
        return False
    return not tool.permissions or any(user.permissions.get(p, rbac.NONE) != rbac.NONE for p in tool.permissions)


def tools_for(user: service.CurrentUser) -> list[Tool]:
    return [t for t in TOOLS.values() if allowed(user, t)]


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ToolResult:
    ok: bool
    data: Any
    text: str
    ids: set[str]
    error: str | None = None


def collect_ids(data: Any, out: set[str], parse_text: bool = False, depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if k in ID_KEYS and isinstance(v, str | int) and not isinstance(v, bool):
                out.add(str(v))
            collect_ids(v, out, parse_text, depth + 1)
    elif isinstance(data, list):
        for v in data:
            collect_ids(v, out, parse_text, depth + 1)
    elif parse_text and isinstance(data, str) and data[:1] in ("{", "[") and len(data) <= 20000:
        # Chỉ kết quả Kho (hub.kho_*): Kho gói JSON trong `content[].text` — đọc vào để thấy mã bản ghi. Các tool khác
        # KHÔNG làm vậy (chữ do khách viết không được tự khai id).
        try:
            collect_ids(orjson.loads(data), out, parse_text, depth + 1)
        except orjson.JSONDecodeError:
            return


def _shrink(data: Any, rows: int, text_len: int) -> Any:
    if isinstance(data, dict):
        return {k: _shrink(v, rows, text_len) for k, v in data.items()}
    if isinstance(data, list):
        return [_shrink(v, rows, text_len) for v in data[:rows]]
    if isinstance(data, str) and len(data) > text_len:
        return data[:text_len] + "…"
    return data


GUIDE_STEPS_MAX = 240


def compact(data: Any) -> tuple[Any, str]:
    """Cắt gọn ≤ MAX_BYTES / MAX_ROWS — giảm dần số dòng và độ dài chuỗi tới khi vừa."""
    for rows, text_len in ((MAX_ROWS, 300), (10, 200), (5, 120), (3, 80)):
        small = _shrink(data, rows, text_len)
        raw = orjson.dumps(small, default=str).decode()
        if len(raw.encode()) <= MAX_BYTES:
            return small, raw
    raw = orjson.dumps(_shrink(data, 2, 60), default=str).decode()
    cut = raw.encode()[:MAX_BYTES].decode(errors="ignore")
    return None, cut + "…(đã cắt)"


def _build_request(tool: Tool, args: dict[str, Any]) -> tuple[str, dict[str, str]]:
    assert tool.path is not None
    path = tool.path
    for a in tool.path_args:
        v = str(args.get(a) or "")
        if a in tool.path_patterns:
            if not re.fullmatch(tool.path_patterns[a], v):
                raise ToolError("BAD_ARGS", f"{a} không đúng dạng")
            v = v.upper()
        else:
            try:
                uuid.UUID(v)
            except ValueError as e:
                raise ToolError("BAD_ARGS", f"{a} phải là UUID") from e
        path = path.replace("{" + a + "}", v)
    query = dict(tool.default_query)
    for k, allowed_values in tool.query.items():
        if k not in args or args[k] in (None, ""):
            continue
        v = str(args[k])[:120]
        if allowed_values is not None and v not in allowed_values:
            raise ToolError("BAD_ARGS", f"{k} phải thuộc {', '.join(allowed_values)}")
        query[k] = v
    return path, query


GetJson = Callable[[str, dict[str, str]], Any]

#: Tool trả dữ liệu tĩnh / do hệ thống tự dựng (danh mục màn, việc thiết lập, trạng thái hướng dẫn) — không có chữ của
#: người ngoài nên không che (che sẽ làm hỏng câu hướng dẫn dài và địa chỉ).
STATIC_TOOLS = frozenset({"screens.list", "guide.list", "coach.status"})


class ToolRunner:
    """Chạy tool cho MỘT người trong MỘT lượt; gom id đã thấy cho validator."""

    def __init__(self, app: Any, user: service.CurrentUser, session_token: str):
        self.app, self.user, self.token = app, user, session_token
        self.seen_ids: set[str] = set()

    async def _get(self, path: str, query: dict[str, str]) -> tuple[int, Any]:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gen.internal",
                                     cookies={service.SESSION_COOKIE: self.token}, timeout=20.0) as c:
            r = await c.get(f"/api/v1{path}", params=query)
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body

    async def run(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = TOOLS.get(name)
        if tool is None:
            return self._fail("UNKNOWN_TOOL", f"Không có tool {name}")
        if not allowed(self.user, tool):
            return self._fail("FORBIDDEN", "Vai trò của người hỏi không có quyền dữ liệu này")
        try:
            if tool.name == "screens.list":
                data: Any = registry.visible_screens(self.user.permissions)
            elif tool.name == "social.read":
                data = await self._social_read(args)
            elif tool.name == "coach.status":
                data = await self._coach_status()
            elif tool.name == "guide.list":
                status, follow = await self._get("/setup/follow-up", {})
                done = {i.get("n"): i.get("done") for i in follow} if status == 200 and isinstance(follow, list) \
                    else {}
                # Gọn cho đủ mọi việc (9 việc ở v0.1.39) trong 4 KB không bị cắt: việc đã xong bỏ phần bước, việc còn
                # lại nối các bước thành một dòng, cắt GUIDE_STEPS_MAX ký tự (test_gen đo kích thước có dư địa).
                data = [{"n": g["n"], "title": g["title"], "done": bool(done.get(g["n"]))}
                        | ({} if done.get(g["n"]) else {"steps": " / ".join(g["steps"])[:GUIDE_STEPS_MAX]})
                        for g in registry.load().guide]
            else:
                path, query = _build_request(tool, args)
                status, data = await self._get(path, query)
                if status == 403:
                    return self._fail("FORBIDDEN", "Vai trò của người hỏi không có quyền dữ liệu này")
                if status == 404:
                    return self._fail("NOT_FOUND", "Không tìm thấy")
                if status >= 400:
                    # 409 có mã rõ (vd HUB_LINK_OFF, HUB_UNAVAILABLE) → báo đúng lý do cho model, không đoán.
                    if isinstance(data, dict) and isinstance(data.get("code"), str) and status == 409:
                        return self._fail(data["code"], str(data.get("title") or f"Lỗi {status}")[:200])
                    return self._fail("ERROR", f"Lỗi {status}")
        except ToolError as e:
            return self._fail(e.code, str(e))
        # v0.1.71 (P2-3): kết quả tool (Hộp thư, hồ sơ, tìm kiếm… — chữ do người ngoài viết) sang model là bản đã che
        # OTP / mật khẩu / SĐT / số tài khoản; UUID, mã đối tượng, số tiền, ngày giờ giữ nguyên (validator id vẫn khớp).
        if tool.name not in STATIC_TOOLS and not tool.name.startswith("hub."):    # hub.* đã che ở `hub_link` (giữ id)
            data = mask_for_prompt(data)
        small, raw = compact(data)
        ids: set[str] = set()
        kho = tool.name.startswith("hub.kho_")
        collect_ids(small if small is not None else data, ids, parse_text=kho)
        if kho:
            ids.update(KHO_CODE_RE.findall(raw))
        self.seen_ids |= ids
        return ToolResult(ok=True, data=small, text=raw, ids=ids)

    async def _coach_status(self) -> Any:
        """Gộp thẻ hôm nay (KHÔNG `mark_shown` — hỏi Gen không tính là Sếp đã xem thẻ) và lộ trình bài học thành một
        kết quả gọn: việc cần làm, tiến độ, bài hôm nay, danh sách bài (id, tiêu đề, trạng thái). Chỉ khoá / tiêu đề
        tĩnh / số đếm — endpoint gốc không bao giờ trả chi tiết sự cố, thông điệp, email hay token."""
        status, today = await self._get("/gen/coach/today", {})
        if status == 403:
            raise ToolError("FORBIDDEN", "Vai trò của người hỏi không có quyền dữ liệu này")
        if status != 200 or not isinstance(today, dict):
            raise ToolError("ERROR", f"Lỗi {status}")
        status, curr = await self._get("/gen/coach/curriculum", {})
        lessons = curr.get("lessons") if status == 200 and isinstance(curr, dict) else []
        todos = []
        for t in today.get("todos") or []:
            row = {k: t[k] for k in ("key", "level", "title", "why", "target", "link") if t.get(k)}
            followup = str(t.get("target") or "").removeprefix("guide.item.do:")
            if followup.isdigit():
                row["n"] = int(followup)   # id dòng cho `guide.item.do:<n>` — validator chỉ cho làm sáng id đã thấy
            todos.append(row)
        lesson = today.get("lesson")
        return {"enabled": bool(today.get("enabled")), "todos": todos, "progress": today.get("progress"),
                "lesson": None if not isinstance(lesson, dict) else {
                    k: lesson[k] for k in ("id", "k", "total", "title", "status") if k in lesson},
                "lessons": [{"id": x.get("id"), "title": x.get("title"), "status": x.get("status")}
                            for x in lessons or [] if isinstance(x, dict)]}

    async def _social_read(self, args: dict[str, Any]) -> Any:
        from gh.db import sessionmaker
        from gh.social import service as social

        account_id = str(args.get("account_id") or "") or None
        if account_id is not None:
            try:
                uuid.UUID(account_id)
            except ValueError as e:
                raise ToolError("BAD_ARGS", "account_id phải là UUID") from e
        async with sessionmaker()() as db:
            out = await social.gen_read(db, self.app.state.redis, self.user, account_id)
            await db.commit()
        return out

    @staticmethod
    def _fail(code: str, message: str) -> ToolResult:
        return ToolResult(ok=False, data=None, text=orjson.dumps({"error": code, "message": message}).decode(),
                          ids=set(), error=code)
