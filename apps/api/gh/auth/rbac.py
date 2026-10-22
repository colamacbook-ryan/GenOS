"""Phân quyền 5 vai trò theo ma trận của thiết kế (ARCHITECTURE §8.3, PLAN "Định nghĩa có giới hạn").

Quyền = năng lực × phạm vi. Phạm vi: all | team | assigned | none (khớp ✓ / – / ✕ của ma trận).
Ma trận dưới đây là giá trị khởi tạo; Owner sửa được từng ô trong Cài đặt › Quyền hạn.
"""

from dataclasses import dataclass

ALL, TEAM, ASSIGNED, NONE = "all", "team", "assigned", "none"
SCOPES = (ALL, TEAM, ASSIGNED, NONE)
_RANK = {NONE: 0, ASSIGNED: 1, TEAM: 2, ALL: 3}

OWNER, MANAGER, OPERATOR, AGENT_STAFF, AUDITOR = "owner", "manager", "operator", "agent_staff", "auditor"


@dataclass(frozen=True)
class RoleDef:
    code: str
    name: str
    meta: str


# v0.1.61 (F107): tên vai trò tiếng Việt ngay ở nguồn — chuông, toast, chân trang, hộp mời đều lấy `core.roles.name` nên
# mọi màn gọi một tên (migration 0036 đổi tên ở máy đã cài). Mã vai trò (`code`) mới là khoá so sánh; không so theo tên.
ROLES = (
    RoleDef(OWNER, "Owner — Sếp", "thấy toàn cảnh"),
    # Q4=2 (16/10): chưa có tính năng Nhóm (tạo nhóm, xếp người) ⇒ không hứa "thấy team mình".
    RoleDef(MANAGER, "Quản lý", "thấy việc được giao"),
    RoleDef(OPERATOR, "Vận hành", "thấy hàng đợi việc"),
    RoleDef(AGENT_STAFF, "Nhân viên phụ trách", "chỉ khách được phân"),
    RoleDef(AUDITOR, "Kiểm soát", "xem, không hành động"),
)

# Cột ma trận của thiết kế → các quyền thuộc cột đó.
PERMISSIONS: dict[str, str] = {
    # Tổng quan
    "overview.read": "Xem Hôm nay (Tổng quan)",
    # Hàng đợi
    "queue.read": "Xem hàng đợi / Hộp thư",
    "queue.act": "Xử lý item trong hàng đợi",
    # Hồ sơ khách
    "profile.read": "Xem nhóm, con người, hồ sơ sống, sổ tay",
    "profile.write": "Sửa hồ sơ, ghi chú, gán phụ trách, sổ tay",
    # Đánh giá nhân sự
    "people_review.read": "Xem đánh giá con người",
    "people_review.write": "Sửa điểm tay, xử lý phản biện",
    "care.read": "Xem chất lượng chăm sóc theo nhân viên",
    # Cơ hội
    "opportunity.read": "Xem cơ hội, cung cầu, kho hội thoại",
    "opportunity.write": "Đổi giai đoạn, ghép cung cầu",
    # Hành động
    "action.draft": "Soạn bản nháp, chạy việc trong mức tự trị",
    "action.approve": "Duyệt / huỷ bản nháp bị giữ",
    # Nhật ký
    "audit.read": "Xem nhật ký hành động",
    # Kỹ thuật (ngoài ma trận thiết kế — mặc định chỉ Owner, Auditor xem)
    "data.read": "Xem tầng dữ liệu (kho thô, quy tắc, kho sạch, danh tính)",
    "data.manage": "Sửa quy tắc, chạy sàng lọc, gộp/tách danh tính",
    "system.read": "Xem agent, model, MCP, plugin, điều khiển hệ thống",
    "system.manage": "Cấu hình agent, model, MCP, plugin, kênh, chính sách",
    "roles.manage": "Đổi ma trận quyền, mời người dùng",
}

_M = {
    #                  Owner  Manager  Operator  AgentNV  Auditor
    "overview.read":  (ALL,  TEAM,    ASSIGNED, NONE,     ALL),
    "queue.read":     (ALL,  TEAM,    ALL,      ASSIGNED, ALL),
    "queue.act":      (ALL,  TEAM,    ALL,      ASSIGNED, NONE),
    "profile.read":   (ALL,  TEAM,    ALL,      ASSIGNED, ALL),
    "profile.write":  (ALL,  TEAM,    ALL,      ASSIGNED, NONE),
    "people_review.read":  (ALL, NONE, NONE, NONE, NONE),   # Q4: chỉ Owner; Auditor thấy nhật ký truy cập
    "people_review.write": (ALL, NONE, NONE, NONE, NONE),
    "care.read":      (ALL,  NONE,    NONE,     NONE,     NONE),
    "opportunity.read":  (ALL, TEAM,  ALL,      ASSIGNED, ALL),
    "opportunity.write": (ALL, TEAM,  ALL,      ASSIGNED, NONE),
    "action.draft":   (ALL,  TEAM,    ASSIGNED, ASSIGNED, NONE),
    "action.approve": (ALL,  TEAM,    NONE,     NONE,     NONE),
    "audit.read":     (ALL,  TEAM,    NONE,     NONE,     ALL),
    "data.read":      (ALL,  NONE,    NONE,     NONE,     ALL),
    "data.manage":    (ALL,  NONE,    NONE,     NONE,     NONE),
    "system.read":    (ALL,  NONE,    NONE,     NONE,     ALL),
    "system.manage":  (ALL,  NONE,    NONE,     NONE,     NONE),
    "roles.manage":   (ALL,  NONE,    NONE,     NONE,     NONE),
}
# Quyền ghi — Auditor không bao giờ có (khoá cứng, bootstrap đặt lại nếu bị sửa).
WRITE_PERMISSIONS = ("queue.act", "profile.write", "people_review.write", "opportunity.write", "action.draft",
                     "action.approve", "data.manage", "system.manage", "roles.manage")

_ORDER = (OWNER, MANAGER, OPERATOR, AGENT_STAFF, AUDITOR)
DEFAULT_MATRIX: dict[str, dict[str, str]] = {
    role: {perm: scopes[i] for perm, scopes in _M.items()} for i, role in enumerate(_ORDER)
}

# Màn hình (khoá trong docs/design/screens.json) → quyền cần để thấy màn trong danh mục.
SCREEN_PERMISSION: dict[str, tuple[str, ...]] = {
    "overview": ("overview.read",),
    "inbox": ("queue.read",),
    "workbench": ("action.draft", "action.approve"),
    "tasks": ("queue.read",),
    "directory": ("profile.read",),
    "documents": ("profile.read",),
    "graph": ("profile.read",),
    "profile": ("profile.read",),
    "notebook": ("profile.read",),
    "opportunity": ("opportunity.read",),
    "supply": ("opportunity.read",),
    "search": ("opportunity.read",),
    "deals": ("opportunity.read",),
    "people": ("people_review.read",),
    "care": ("care.read",),
    "listenlog": ("data.read",),         # v0.1.68: Nhật ký lắng nghe (cùng quyền với Kho thô)
    "raw": ("data.read",),
    "rules": ("data.read",),
    "clean": ("data.read",),
    "identity": ("data.read",),
    "agents": ("system.read",),
    "api": ("system.read",),
    "mcp": ("system.read",),
    "plugins": ("system.read",),
    "system": ("system.read", "audit.read"),
    "connections": ("system.read",),     # v0.1.42: Kết nối (tách khỏi Cài đặt)
    "team": ("roles.manage",),           # v0.1.42: Đội ngũ (người dùng, đánh giá, chăm sóc)
}


def at_least(have: str, need: str) -> bool:
    return _RANK[have] >= _RANK[need]


def can_see_screen(permissions: dict[str, str], screen: str) -> bool:
    return any(permissions.get(p, NONE) != NONE for p in SCREEN_PERMISSION.get(screen, ()))
