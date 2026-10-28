"""v0.1.42 (F-7, F-41, F-65) → v0.1.78 (G0): cây danh mục MỘT shell — 6 mục "Việc hằng ngày" + "Quản trị" (7 trang con);
màn cũ là con ẩn của trang mới; Đội ngũ chỉ hiện khi đã có nhân viên (core.persons person_type='staff' còn hiệu lực)."""

import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from gh.auth import rbac
from gh.shell import navigation
from tests.conftest import Api
from tests.test_rbac_api import login_as

# Màn cũ nay là con ẩn của trang mới (nhúng thành tab) — luôn ẩn khỏi thanh bên.
LEGACY = {"overview", "inbox", "workbench", "tasks", "directory", "opportunity", "deals", "documents", "search",
          "profile", "graph", "notebook", "supply", "people", "care", "raw", "listenlog", "rules", "clean",
          "identity", "agents", "api", "mcp", "plugins", "system"}
DAILY = ["intel", "decide", "work", "panorama", "me", "team"]
ADMIN = ["start", "connections", "access", "backup", "org", "log", "advanced"]
STAFF_ONLY = {"team"}


def walk(nav: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def go(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            out.append(n)
            go(n["children"])

    for d in nav:
        go(d["groups"])
    return out


def by_key(nav: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {n["key"]: n for n in walk(nav) if n["key"]}


async def add_staff(db, code: str, *, deleted: bool = False, merged: bool = False) -> None:  # type: ignore[no-untyped-def]
    org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
    target = None
    if merged:
        target = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
            VALUES (:o, :c, 'Gốc') RETURNING id"""), {"o": org, "c": code + "-ROOT"})).scalar_one()
    await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name, person_type, deleted_at,
                                                       merged_into_id)
        VALUES (:o, :c, 'Nhân viên', 'staff', CASE WHEN :d THEN now() END, :m)"""),
        {"o": org, "c": code, "d": deleted, "m": target})
    await db.commit()


async def test_owner_tree_without_staff(owner_api: Api) -> None:
    nav = (await owner_api.get("/navigation")).json()
    assert [d["domain"] for d in nav] == ["daily", "admin"]
    assert [d["label"] for d in nav] == ["Việc hằng ngày", "Quản trị"]
    assert [d["crumb"] for d in nav] == ["HẰNG NGÀY", "QUẢN TRỊ"]
    assert [d["collapsed"] for d in nav] == [False, True]
    assert [n["key"] for n in nav[0]["groups"]] == DAILY
    assert [n["key"] for n in nav[1]["groups"]] == ADMIN
    keys = by_key(nav)
    assert keys["team"].get("hidden") is True                         # chưa có nhân viên ⇒ Đội ngũ ẩn
    for k in LEGACY:
        assert keys[k].get("hidden") is True, k                       # màn cũ luôn ẩn, chỉ còn là tab của trang mới
    assert all("hidden" not in n for n in walk(nav) if n["key"] not in LEGACY | STAFF_ONLY)
    assert [d["count"] for d in nav] == [5, 7]
    assert [c["key"] for c in keys["team"]["children"]] == ["people", "care"]
    assert [c["key"] for c in keys["intel"]["children"]] == ["overview", "inbox"]
    assert [c["key"] for c in keys["panorama"]["children"]] == [
        "directory", "opportunity", "deals", "documents", "search", "profile", "graph", "notebook", "supply"]
    assert [c["key"] for c in keys["advanced"]["children"]] == [
        "raw", "listenlog", "rules", "clean", "identity", "agents", "api", "mcp", "plugins", "system"]


async def test_staff_reveals_team(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await add_staff(db, "NV-DEL", deleted=True)
    await add_staff(db, "NV-MRG", merged=True)
    nav = (await owner_api.get("/navigation")).json()
    assert by_key(nav)["team"].get("hidden") is True                  # đã xoá / đã gộp ⇒ không tính
    assert [d["count"] for d in nav] == [5, 7]
    await add_staff(db, "NV-001")
    nav = (await owner_api.get("/navigation")).json()
    keys = by_key(nav)
    assert "hidden" not in keys["team"]
    # people/care là tab của Đội ngũ, không phải mục thanh bên
    assert keys["people"]["hidden"] is True and keys["care"]["hidden"] is True
    assert keys["plugins"]["hidden"] is True
    assert [d["count"] for d in nav] == [6, 7]


async def test_names_and_icons(owner_api: Api) -> None:
    keys = by_key((await owner_api.get("/navigation")).json())
    assert {k: keys[k]["name"] for k in ("intel", "decide", "work", "panorama", "me", "team", "start", "connections",
                                         "access", "backup", "org", "log", "advanced", "inbox", "directory")} == {
        "intel": "Tình báo", "decide": "Cần quyết", "work": "Việc", "panorama": "Toàn cảnh", "me": "Tôi",
        "team": "Đội ngũ", "start": "Bắt đầu", "connections": "Kết nối", "access": "Người & quyền",
        "backup": "Sao lưu & cập nhật", "org": "Tổ chức", "log": "Nhật ký", "advanced": "Nâng cao",
        "inbox": "Hộp thư", "directory": "Khách & Nhóm"}
    assert keys["intel"]["icon"] == "ph ph-sun"
    assert keys["system"]["icon"] == "ph ph-gear-six"
    assert keys["mcp"]["name"] == "MCP Hub"


async def test_agent_staff_first_visible_is_intel(owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    api = await login_as(client, db, "agent_staff")
    nav = (await api.get("/navigation")).json()
    first = next(n["key"] for n in walk(nav) if n["key"] and not n.get("hidden"))
    assert first == "intel"  # Nhân viên: trang chủ là Tình báo (Hôm nay theo vai)
    assert [n["key"] for n in nav[0]["groups"] if n["key"]] == ["intel", "decide", "work", "panorama"]
    assert len(nav) == 1                                              # không có Quản trị


async def test_manager_sees_log_only_in_admin(owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    keys = by_key((await (await login_as(client, db, "manager")).get("/navigation")).json())
    assert {"intel", "decide", "work", "panorama", "log"} <= set(keys)
    assert not ({"connections", "access", "backup", "org", "start", "advanced", "me", "team"} & set(keys))


async def test_auditor_sees_connections_not_start_or_decide(owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    keys = by_key((await (await login_as(client, db, "auditor")).get("/navigation")).json())
    assert {"connections", "backup", "org", "log", "access", "advanced"} <= set(keys)
    assert not ({"start", "decide", "me", "team"} & set(keys))


def test_hidden_screens_never_have_badges() -> None:
    owner = dict(rbac.DEFAULT_MATRIX["owner"])
    badges: dict[str, int | None] = {k: 7 for k in navigation.all_screen_keys()}
    nav = navigation.build(owner, badges, has_staff=False)
    hidden = [n for n in walk(nav) if n["key"] and n.get("hidden")]
    assert {n["key"] for n in hidden} == LEGACY | STAFF_ONLY
    assert all(n["badge"] is None for n in hidden)
    assert set(navigation.BADGE_SCREENS) <= set(DAILY)
    assert {"connections", "team", "intel", "advanced"} <= set(navigation.all_screen_keys())
    shown = by_key(navigation.build(owner, badges, has_staff=True))
    assert shown["team"]["badge"] == {"value": "7", "tone": "ok"}
    assert shown["people"]["badge"] is None and shown["plugins"]["badge"] is None


def test_every_screen_key_has_a_permission() -> None:
    # Khoá màn mới không có quyền trong rbac.SCREEN_PERMISSION ⇒ không ai thấy (kể cả Owner): chặn quên khi thêm mục.
    assert set(navigation.all_screen_keys()) <= set(rbac.SCREEN_PERMISSION)
    assert len(navigation.all_screen_keys()) == len(set(navigation.all_screen_keys()))


# ── Cây Python (navigation.NAV) phải trùng packages/contracts/src/screens.ts (nit review v0.1.42) ──────────────────
SCREENS_TS = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "src" / "screens.ts"


def _str_field(block: str, field: str) -> str | None:
    m = re.search(rf"\b{field}: (?:'((?:[^'\\]|\\.)*)'|null)", block)
    return m.group(1) if m else None


def _ts_tree() -> dict[str, Any]:
    src = SCREENS_TS.read_text("utf-8")
    body = src.split("export const SCREENS: ScreenMeta[] = [", 1)[1].split("\n];", 1)[0]
    screens: dict[str, dict[str, Any]] = {}
    for block in re.findall(r"\{(.*?)\n  \}", body, flags=re.S):
        key = _str_field(block, "key")
        assert key, block
        screens[key] = {"domain": _str_field(block, "domain"), "parent": _str_field(block, "parent"),
                        "icon": _str_field(block, "icon"), "name": _str_field(block, "name"),
                        "en": _str_field(block, "en"), "hidden": "navHidden: true" in block,
                        "needs_staff": "needsStaff: true" in block}
    order_src = src.split("export const NAV_ORDER", 1)[1].split("};", 1)[0]
    order = {m.group(1): re.findall(r"'([^']+)'", m.group(2))
             for m in re.finditer(r"^\s*(\w+): \[(.*)\],?$", order_src, flags=re.M)}
    collapsed = {d: c == "true" for d, c in re.findall(r"(\w+): \{ id: '\w+'.*?collapsedByDefault: (\w+)", src)}
    group_icons = dict(re.findall(r"'([^']+)': '(ph [^']+)'", src.split("GROUP_ICONS", 1)[1].split("};", 1)[0]))
    return {"screens": screens, "order": order, "collapsed": collapsed, "group_icons": group_icons}


def test_python_nav_matches_web_screens_ts() -> None:
    ts = _ts_tree()
    assert len(ts["screens"]) >= 38 and set(ts["order"]) == {"daily", "admin"}
    py_screens: dict[str, dict[str, Any]] = {}

    def collect(nodes: list[dict[str, Any]], parent: str | None, domain: str) -> None:
        for n in nodes:
            if n["key"] is None:
                assert n["icon"] == ts["group_icons"][n["name"]], n["name"]
            else:
                py_screens[n["key"]] = {"domain": domain, "parent": parent, "icon": n["icon"], "name": n["name"],
                                        "en": n["en"], "hidden": n["hidden"], "needs_staff": n["needs_staff"]}
            collect(n["children"], n["name"], domain)

    for dom in navigation.NAV:
        assert dom["collapsed"] == ts["collapsed"][dom["domain"]], dom["domain"]
        assert [n["key"] or n["name"] for n in dom["groups"]] == ts["order"][dom["domain"]], dom["domain"]
        collect(dom["groups"], None, dom["domain"])
    assert py_screens == ts["screens"]
