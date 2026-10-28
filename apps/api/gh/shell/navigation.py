"""Cây danh mục của Console — cây chuẩn v0.1.78 (giống hệt packages/contracts/src/screens.ts).

Chỉ nhãn, icon, cấu trúc lấy từ thiết kế. Số badge KHÔNG lấy từ thiết kế (đó là số mẫu):
badge được tính từ dữ liệu thật qua `BadgeSource`; chưa có nguồn → `null`.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from gh.auth import rbac

OK, WARN, BAD, ACCENT = "ok", "warn", "bad", "accent"


def _s(key: str, icon: str, name: str, en: str, tone: str | None = None,
       children: list[dict[str, Any]] | None = None, hidden: bool = False,
       needs_staff: bool = False) -> dict[str, Any]:
    return {"key": key, "icon": icon, "name": name, "en": en, "badge_tone": tone, "children": children or [],
            "hidden": hidden, "needs_staff": needs_staff}


def _g(name: str, icon: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"key": None, "icon": icon, "name": name, "en": None, "badge_tone": None, "children": children,
            "hidden": False, "needs_staff": False}


# v0.1.78 (G0, UI-IA-REVIEW §4): MỘT shell, hai miền — `daily` (6 mục) và `admin` (Quản trị, thu gọn mặc định).
# Mỗi mục thanh bên
# là MỘT trang có tab; màn cũ (inbox, workbench, raw…) vẫn là khoá màn (quyền, Gen, góc nhìn đã lưu) nhưng chỉ là con ẩn
# (`hidden=True`: có trong cây, không hiện thanh bên) của trang mới. Phải giống hệt packages/contracts/src/screens.ts.
def _h(key: str, icon: str, name: str, en: str, needs_staff: bool = False) -> dict[str, Any]:
    """Con ẩn: màn cũ được nhúng thành tab của trang cha."""
    return _s(key, icon, name, en, hidden=True, needs_staff=needs_staff)


NAV: list[dict[str, Any]] = [
    {"domain": "daily", "label": "Việc hằng ngày", "crumb": "HẰNG NGÀY", "icon": "ph-fill ph-briefcase",
     "tone": OK, "collapsed": False,
     "groups": [
         _s("intel", "ph ph-sun", "Tình báo", "Sáng nay có gì, tín hiệu nào cần để ý", BAD, children=[
             _h("overview", "ph ph-sun-horizon", "Hôm nay", "Cần Sếp xử lý · 4 số chính"),
             _h("inbox", "ph ph-tray", "Hộp thư", "Tin quan trọng đã được sắp xếp"),
         ]),
         _s("decide", "ph ph-seal-check", "Cần quyết", "Nháp tin và đề xuất chờ Sếp quyết", WARN, children=[
             _h("workbench", "ph ph-pen-nib", "Bàn làm việc", "Soạn & duyệt tin trả lời"),
         ]),
         _s("work", "ph ph-check-square", "Việc", "Việc đến hạn và lời hứa", BAD, children=[
             _h("tasks", "ph ph-check-square", "Việc & Nhắc hẹn", "Việc cần làm và lời nhắc"),
         ]),
         _s("panorama", "ph ph-map-trifold", "Toàn cảnh", "Người, nhóm, quan hệ, cơ hội", children=[
             _h("directory", "ph ph-address-book", "Khách & Nhóm", "Nhóm theo kênh · lọc khách"),
             _h("opportunity", "ph ph-target", "Bảng cơ hội", "Cơ hội bán hàng đang theo"),
             _h("deals", "ph ph-handshake", "Deal & Vụ việc", "Thương vụ và vụ việc"),
             _h("documents", "ph ph-files", "Tài liệu", "Báo giá, hợp đồng, tệp"),
             _h("search", "ph ph-brain", "Kho hội thoại", "Tìm trong mọi hội thoại"),
             # F-65: Hồ sơ sống mở từ danh sách; v0.1.78 có thêm tab riêng ở Toàn cảnh.
             _h("profile", "ph ph-identification-card", "Hồ sơ sống", "Hồ sơ một người — mở từ danh sách"),
             _h("graph", "ph ph-graph", "Bản đồ quan hệ", "Ai quen ai, qua đâu"),
             _h("notebook", "ph ph-notebook", "Sổ tay nhận thức", "Ghi chú trợ lý theo từng người"),
             _h("supply", "ph ph-arrows-left-right", "Cung ↔ Cầu", "Ghép người cần với người có"),
         ]),
         _s("me", "ph ph-user-circle", "Tôi", "Hồ sơ của Sếp, Gen nhớ gì về Sếp"),
         _s("team", "ph ph-users-three", "Đội ngũ", "Đánh giá nhân viên, chất lượng chăm sóc", needs_staff=True,
            children=[
                _h("people", "ph ph-users-three", "Đánh giá con người", "Điểm nhân viên có chứng cứ", needs_staff=True),
                _h("care", "ph ph-heartbeat", "Chất lượng chăm sóc", "Cách nhân viên chăm khách", needs_staff=True),
            ]),
     ]},
    {"domain": "admin", "label": "Quản trị", "crumb": "QUẢN TRỊ", "icon": "ph-fill ph-gear", "tone": ACCENT,
     "collapsed": True,
     "groups": [
         _s("start", "ph ph-rocket-launch", "Bắt đầu", "Việc thiết lập và kiểm tra kết nối"),
         _s("connections", "ph ph-plugs-connected", "Kết nối", "Bộ não AI, Zalo, Facebook, Gen-hub…"),
         _s("access", "ph ph-users", "Người & quyền", "Người dùng Console và quyền hạn"),
         _s("backup", "ph ph-database", "Sao lưu & cập nhật", "Máy có an toàn không, bản mới"),
         _s("org", "ph ph-briefcase", "Tổ chức", "Tên, giờ, xưng hô"),
         _s("log", "ph ph-notebook", "Nhật ký", "Ai đã làm gì"),
         _s("advanced", "ph ph-cpu", "Nâng cao", "Dữ liệu, agent, model, MCP", children=[
             _h("raw", "ph ph-database", "Kho dữ liệu thô", "Tin gốc các kênh gom về"),
             _h("listenlog", "ph ph-ear", "Nhật ký lắng nghe", "Mọi kênh đang nghe, hệ thống đã làm gì"),
             _h("rules", "ph ph-funnel", "Quy tắc sàng lọc", "Lọc, phân loại, chấm điểm"),
             _h("clean", "ph ph-check-circle", "Kho sạch", "Dữ liệu đã lọc & bộ nhớ làm việc"),
             _h("identity", "ph ph-git-merge", "Hợp nhất danh tính", "Gộp một người nhiều tài khoản"),
             _h("agents", "ph ph-user-focus", "Danh tính Agent", "Tên, giọng, phạm vi trợ lý"),
             _h("api", "ph ph-plugs", "API & Model", "Khoá API và model cho trợ lý"),
             _h("mcp", "ph ph-plugs-connected", "MCP Hub", "Máy chủ MCP bên ngoài"),
             # F-41: đóng băng — route và API /plugins giữ nguyên, không hiện thanh bên.
             _h("plugins", "ph ph-puzzle-piece", "Plugin & Tiện ích", "Plugin nền và plugin cài thêm"),
             _h("system", "ph ph-gear-six", "Cài đặt", "Sao lưu, cập nhật, tổ chức, bộ não AI, quyền, nhật ký"),
         ]),
     ]},
]

# Màn có badge trong thiết kế. Mỗi giai đoạn đăng ký nguồn số thật cho màn của mình.
# v0.1.78: số đếm chỉ gắn vào ba mục thanh bên có số (Tình báo 🔴, Cần quyết, Việc) — nguồn số thật do G1/G2
# đăng ký; màn ẩn không bao giờ có badge.
BadgeSource = Callable[[Any], Awaitable[int | None]]
BADGE_SCREENS = ("intel", "decide", "work")


def format_badge(n: int | None) -> str | None:
    if n is None or n <= 0:
        return None
    if n >= 1000:
        k = n / 1000
        return f"{k:.0f}k" if k >= 10 else f"{k:.1f}k".replace(".0k", "k")
    return str(n)


def _is_hidden(node: dict[str, Any], has_staff: bool) -> bool:
    return bool(node["hidden"] or (node["needs_staff"] and not has_staff))


def _filter(node: dict[str, Any], perms: Mapping[str, str], badges: Mapping[str, int | None],
            has_staff: bool) -> dict[str, Any] | None:
    children = [c for c in (_filter(ch, perms, badges, has_staff) for ch in node["children"]) if c is not None]
    key = node["key"]
    visible = key is not None and rbac.can_see_screen(dict(perms), key)
    if not visible and not children:
        return None
    hidden = visible and _is_hidden(node, has_staff)
    value = format_badge(badges.get(key)) if key and not hidden else None
    out = {"key": key if visible else None, "icon": node["icon"], "name": node["name"], "en": node["en"],
           "badge": {"value": value, "tone": node["badge_tone"] or OK} if value else None,
           "children": children}
    if hidden:
        out["hidden"] = True
    return out


def _count(groups: list[dict[str, Any]]) -> int:
    # v0.1.42: số khoá màn KHÔNG ẩn trong domain (mọi cấp).
    def walk(nodes: list[dict[str, Any]]) -> int:
        return sum((1 if n["key"] and not n.get("hidden") else 0) + walk(n["children"]) for n in nodes)

    return walk(groups)


def build(perms: Mapping[str, str], badges: Mapping[str, int | None] | None = None, *,
          has_staff: bool = True) -> list[dict[str, Any]]:
    out = []
    for dm in NAV:
        groups = [g for g in (_filter(n, perms, badges or {}, has_staff) for n in dm["groups"]) if g is not None]
        if groups:
            out.append({k: dm[k] for k in ("domain", "label", "crumb", "icon", "tone", "collapsed")} |
                       {"count": _count(groups), "groups": groups})
    return out


def all_screen_keys() -> list[str]:
    keys: list[str] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            if n["key"]:
                keys.append(n["key"])
            walk(n["children"])

    for dm in NAV:
        walk(dm["groups"])
    return keys
