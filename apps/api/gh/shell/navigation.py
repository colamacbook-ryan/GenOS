"""Cây danh mục của Console — cây chuẩn v0.1.42 (giống hệt apps/web/src/screens.ts).

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


# v0.1.42 (F-7): cây chuẩn 6 mục "Việc hằng ngày" + "Nâng cao" (mặc định gập). Phải giống hệt
# apps/web/src/screens.ts. `hidden=True` = có trong cây, có route, không hiện ở thanh bên.
NAV: list[dict[str, Any]] = [
    {"domain": "business", "label": "Việc hằng ngày", "crumb": "HẰNG NGÀY", "icon": "ph-fill ph-briefcase",
     "tone": OK, "collapsed": False,
     "groups": [
         _s("overview", "ph ph-sun-horizon", "Hôm nay", "Cần Sếp xử lý · 4 số chính", BAD),
         _g("Hộp thư & Việc", "ph ph-tray", [
             _s("inbox", "ph ph-tray", "Hộp thư", "Tin quan trọng đã được sắp xếp", WARN),
             _s("workbench", "ph ph-pen-nib", "Bàn làm việc", "Soạn & duyệt tin trả lời", WARN),
             _s("tasks", "ph ph-check-square", "Việc & Nhắc hẹn", "Việc cần làm và lời nhắc", BAD),
         ]),
         _g("Khách & Cơ hội", "ph ph-address-book", [
             _s("directory", "ph ph-address-book", "Khách & Nhóm", "Nhóm theo kênh · lọc khách"),
             _s("opportunity", "ph ph-target", "Bảng cơ hội", "Cơ hội bán hàng đang theo", OK),
             _s("deals", "ph ph-handshake", "Deal & Vụ việc", "Thương vụ và vụ việc"),
             _s("documents", "ph ph-files", "Tài liệu", "Báo giá, hợp đồng, tệp"),
             _s("search", "ph ph-brain", "Kho hội thoại", "Tìm trong mọi hội thoại"),
             # F-65: Hồ sơ sống mở từ danh sách, không còn là mục thanh bên.
             _s("profile", "ph ph-identification-card", "Hồ sơ sống", "Hồ sơ một người — mở từ danh sách",
                hidden=True),
         ]),
         _s("connections", "ph ph-plugs-connected", "Kết nối", "Bộ não AI, Zalo, Facebook, Gen-hub…"),
         _s("team", "ph ph-users", "Đội ngũ", "Người dùng, đánh giá, chăm sóc", children=[
             _s("people", "ph ph-users-three", "Đánh giá con người", "Điểm nhân viên có chứng cứ", needs_staff=True),
             _s("care", "ph ph-heartbeat", "Chất lượng chăm sóc", "Cách nhân viên chăm khách", needs_staff=True),
         ]),
         _s("system", "ph ph-gear-six", "Cài đặt", "Sao lưu, cập nhật, tổ chức, bộ não AI, quyền, nhật ký"),
     ]},
    {"domain": "tech", "label": "Nâng cao", "crumb": "NÂNG CAO", "icon": "ph-fill ph-cpu", "tone": ACCENT,
     "collapsed": True,
     "groups": [
         _g("Tầng dữ liệu", "ph ph-database", [
             _s("raw", "ph ph-database", "Kho dữ liệu thô", "Tin gốc các kênh gom về", WARN),
             _s("listenlog", "ph ph-ear", "Nhật ký lắng nghe", "Mọi kênh đang nghe, hệ thống đã làm gì"),
             _s("rules", "ph ph-funnel", "Quy tắc sàng lọc", "Lọc, phân loại, chấm điểm"),
             _s("clean", "ph ph-check-circle", "Kho sạch", "Dữ liệu đã lọc & bộ nhớ làm việc"),
             _s("identity", "ph ph-git-merge", "Hợp nhất danh tính", "Gộp một người nhiều tài khoản", WARN),
         ]),
         _g("Agent & Model", "ph ph-robot", [
             _s("agents", "ph ph-user-focus", "Danh tính Agent", "Tên, giọng, phạm vi trợ lý"),
             _s("api", "ph ph-plugs", "API & Model", "Khoá API và model cho trợ lý"),
             _s("mcp", "ph ph-plugs-connected", "MCP Hub", "Máy chủ MCP bên ngoài", OK),
         ]),
         _s("graph", "ph ph-graph", "Bản đồ quan hệ", "Ai quen ai, qua đâu", children=[
             _s("notebook", "ph ph-notebook", "Sổ tay nhận thức", "Ghi chú trợ lý theo từng người"),
         ]),
         _s("supply", "ph ph-arrows-left-right", "Cung ↔ Cầu", "Ghép người cần với người có", OK),
         # F-41: đóng băng — route và API /plugins giữ nguyên, không hiện thanh bên.
         _s("plugins", "ph ph-puzzle-piece", "Plugin & Tiện ích", "Plugin nền và plugin cài thêm", OK,
            hidden=True),
     ]},
]

# Màn có badge trong thiết kế. Mỗi giai đoạn đăng ký nguồn số thật cho màn của mình.
# v0.1.42 (F-41): bỏ 'plugins' — màn đóng băng, ẩn khỏi thanh bên. Màn ẩn không bao giờ có badge.
BadgeSource = Callable[[Any], Awaitable[int | None]]
BADGE_SCREENS = ("overview", "inbox", "workbench", "tasks", "opportunity", "supply", "raw", "identity", "mcp")


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
