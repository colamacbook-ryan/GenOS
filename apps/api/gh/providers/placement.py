"""Vai nào chạy Antigravity CLI (`agy`) Ở ĐÂU — v0.1.63 (gói H2), quyết định của Sếp 16/10/2026.

Hai chỗ chạy:
- `inproc` — agy chạy TRONG tiến trình api/worker (`AgyClient`), nơi có khoá hệ thống. Chỉ cho lớp CORE: việc chỉ có Sếp
  và hệ thống tham gia, không đọc nội dung do người ngoài gõ vào;
- `box` — agy chạy QUA HỘP RIÊNG `agy-box` (`gh.agybox.client.AgyBoxClient`, hợp đồng ở `docs/api/agybox-protocol.md`):
  container không có khoá, DB, Redis chính. Cho mọi việc có người ngoài hoặc người không phải Sếp.

Bảng (`AGY_PLACEMENT` + ba ngoại lệ bên dưới):

| Vai | Nơi chạy |
|---|---|
| `core.gen` của Owner (Gen của Sếp) | inproc |
| `core.briefing` (Bản tin Gen — prompt có tên hiển thị khách, tiêu đề việc, lý do giữ nháp) | box |
| `agent:<id>` nội bộ (không gắn kênh / nhóm / người) | inproc |
| `core.reply` (soạn lại / dịch nháp tin khách) | box |
| `core.refinery` (sàng lọc tin người lạ) | box |
| `core.owner_reply` (Sếp tag bot trong nhóm), `core.support` (Chế độ hỗ trợ) | box |
| `agent:<id>` ngoại giao (`agy_policy.diplomatic_agent_ids`) | box |
| `core.gen` của nhân viên (không phải Owner) | box |
| lượt thử agent, Soạn lại / Dịch nháp tin khách, trực việc (`purpose` ∈ `BOX_PURPOSES`) | box |
| khoá lạ (không có trong bảng) | box — đóng an toàn |

Hộp chưa cài / tắt ⇒ vai `box` KHÔNG BAO GIỜ rơi về agy trong tiến trình: bộ định tuyến gỡ Antigravity khỏi chuỗi của
lượt đó và chuyển nguồn AI kế (`agy_policy.decide` ghi lý do tiếng Việt). Module này THUẦN (không DB, không mạng): trạng
thái hộp và tra "agent ngoại giao" nằm ở `gh.providers.agy_policy` — MỘT điểm quyết định; ở đây chỉ có luật chỗ chạy.

Hợp đồng cho gói B2 (readiness, reason `box_down`): `box_required(role, agent_key) -> bool` — vai này có BẮT BUỘC qua
hộp để dùng agy không. `role` = vai của bảng A1 (`profiles.role_of(key)`: `core.*` hoặc `agent:*`), `agent_key` = khoá
đầy đủ (`agent:<uuid>`) hoặc None. Hàm THUẦN nên không tra được "agent ngoại giao" của một `agent:<id>` (cần DB): bên
gọi đã biết thì truyền `AgentRef(diplomatic=…)` / `bool` ở tham số thứ hai, không thì coi là nội bộ; đường có DB là
`agy_policy.verdict_for`. B2 ghép với `gh.agybox.client.box_status()["state"] != "ok"` ⇒ `box_down`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

INPROC = "inproc"
BOX = "box"

ROLE_GEN = "core.gen"
AGENT_PREFIX = "agent:"
#: Khoá chung cho mọi `agent:<id>` trong bảng (agent nội bộ); agent ngoại giao là ngoại lệ `DIPLOMATIC_AGENT_PLACEMENT`.
ROLE_AGENT = "agent:*"

#: Nguồn sự thật cho chỗ chạy mặc định của từng vai.
AGY_PLACEMENT: Mapping[str, str] = MappingProxyType({
    "core.gen": INPROC,          # Gen của Owner; Gen của nhân viên ⇒ STAFF_GEN_PLACEMENT
    # Review v0.1.63 (P2-3): Bản tin đưa tên hiển thị do khách tự đặt, tiêu đề việc, lý do giữ nháp vào prompt
    # (gh/gen/briefing.py) ⇒ có chữ người ngoài ⇒ qua hộp. Hộp tắt ⇒ nguồn kế / bản tin không có đoạn tóm tắt bằng
    # model.
    "core.briefing": BOX,
    ROLE_AGENT: INPROC,          # agent không gắn kênh / nhóm / người; ngoại giao ⇒ DIPLOMATIC_AGENT_PLACEMENT
    "core.reply": BOX,
    "core.refinery": BOX,
    # v0.1.72: bot tài khoản riêng. Cả hai đọc chữ NGƯỜI NGOÀI trong nhóm và đầu ra cả nhóm đọc ⇒ luôn qua hộp.
    "core.owner_reply": BOX,     # Sếp tag bot trong nhóm ⇒ trả lời tại chỗ
    "core.support": BOX,         # Chế độ hỗ trợ: trả lời thành viên nhóm
})
#: Ngoại lệ: agent ngoại giao (có `channel_scopes` / gán BOT nhóm / gán BOT người) đọc nội dung người ngoài.
DIPLOMATIC_AGENT_PLACEMENT = BOX
#: Ngoại lệ: Gen của người dùng không phải Owner (nhân viên).
STAFF_GEN_PLACEMENT = BOX
#: Ngoại lệ theo MỤC ĐÍCH lượt gọi — luôn qua hộp, bất kể khoá agent là gì và agent còn gắn kênh / nhóm hay không:
#: - `setup_agent_try`: thử agent trong trình thiết lập;
#: - `draft_translate` / `draft_regenerate`: Dịch / Soạn lại nháp tin khách (`biz/core/routes.py`). Nháp có `agent_id`
#:   chạy dưới khoá `agent:<id>` nhưng nội dung là tin của khách — agent đã bị gỡ khỏi nhóm (không còn "ngoại giao"
#:   theo tiêu chí DB) vẫn không được chạy agy trong tiến trình;
#: - `duty_decide` (review v0.1.63, P3): agent trực việc quyết định trên tin khách (`gh/biz/duty/engine.py`) — luôn
#:   qua hộp, kể cả khi agent không (còn) "ngoại giao" theo tiêu chí DB.
#: - `owner_reply` / `support_classify` / `support_reply` (v0.1.72): Sếp gọi bot trong nhóm và Chế độ hỗ trợ —
# chữ của người
#:   ngoài vào lời nhắc, đầu ra đăng vào nhóm ⇒ KHÔNG BAO GIỜ chạy agy trong tiến trình.
BOX_PURPOSES = frozenset({"setup_agent_try", "draft_translate", "draft_regenerate", "duty_decide", "owner_reply",
                          "support_classify", "support_reply"})
#: Khoá lạ (không có trong bảng): đóng an toàn ⇒ hộp.
UNKNOWN_PLACEMENT = BOX


@dataclass(frozen=True)
class AgentRef:
    """Thông tin tối thiểu về agent để xếp chỗ chạy: có phải agent ngoại giao không (tiêu chí ở `agy_policy`)."""

    diplomatic: bool = False


def _diplomatic(agent: "AgentRef | bool | str | None") -> bool:
    """`bool` / `AgentRef` mang cờ ngoại giao; chuỗi (khoá `agent:<id>`) hoặc None không đủ thông tin ⇒ nội bộ."""
    if isinstance(agent, bool):
        return agent
    return bool(getattr(agent, "diplomatic", False))


def placement(role: str, agent: "AgentRef | bool | str | None" = None, *, owner: bool = True,
              purpose: str | None = None) -> str:
    """Chỗ chạy agy của MỘT lượt gọi: "inproc" | "box".

    - `role`: khoá agent của bộ định tuyến (`core.gen`, `core.reply`, `core.refinery`, `core.briefing`, `agent:<id>`);
    - `agent`: với `agent:<id>` — `AgentRef(diplomatic=…)` hoặc chính cờ `bool` (ngoại giao?); chuỗi / None ⇒ nội bộ;
      khoá khác bỏ qua;
    - `owner`: người hỏi là Owner (chỉ ảnh hưởng `core.gen`; mặc định True cho lượt không có người hỏi cụ thể);
    - `purpose`: mục đích lượt gọi (chỉ `BOX_PURPOSES` đổi kết quả: luôn qua hộp)."""
    if purpose in BOX_PURPOSES:
        return BOX
    if role.startswith(AGENT_PREFIX):
        return DIPLOMATIC_AGENT_PLACEMENT if _diplomatic(agent) else AGY_PLACEMENT[ROLE_AGENT]
    if role == ROLE_GEN and not owner:
        return STAFF_GEN_PLACEMENT
    return AGY_PLACEMENT.get(role, UNKNOWN_PLACEMENT)


def box_required(role: str, agent_key: "AgentRef | bool | str | None" = None, *, owner: bool = True,
                 purpose: str | None = None) -> bool:
    """Vai này BẮT BUỘC qua hộp riêng để dùng Antigravity (hộp tắt ⇒ không có agy cho vai này). Hợp đồng cho B2:
    `box_required(role: str, agent_key: str | None) -> bool` (xem docstring module)."""
    return placement(role, agent_key, owner=owner, purpose=purpose) == BOX
