"""v0.1.61 (gói A2) — danh mục lệch giữa web và máy chủ.

(a) F107: tên vai trò tiếng Việt ngay ở nguồn (`rbac.ROLES`, `core.roles.name`) — API ma trận quyền và danh sách vai trò
    khi mời trả cùng một tên; migration 0036 đổi tên ở máy đã cài, chạy lại an toàn, không đụng tên Owner tự sửa;
(b) F070: khoá mẫu agent ở bước 8 của web trùng `TEMPLATES` của máy chủ; migration 0036 sửa agent cũ (sales→commercial,
    ops→admin) để bấm Lưu ở Danh tính Agent không còn 422 "Mẫu không tồn tại";
(c) không có mã nguồn nào so sánh theo tên vai trò (đổi tên không làm đổi hành vi).
"""

import re
from pathlib import Path
from typing import Any

import psycopg
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from gh.agents_api.routes import TEMPLATES
from gh.auth import rbac
from gh.setup.routes import Step8In
from tests.conftest import PG, Api
from tests.phase2 import org_id

ROOT = Path(__file__).resolve().parents[3]
GH_DIR = ROOT / "apps" / "api" / "gh"
SQL_0036 = ROOT / "db" / "sql" / "0036_v016x_danh_muc.sql"
STEP8 = ROOT / "apps" / "web" / "src" / "setup" / "Step8Agent.tsx"

VI_NAMES = {"manager": "Quản lý", "operator": "Vận hành", "agent_staff": "Nhân viên phụ trách", "auditor": "Kiểm soát"}
OLD_NAMES = {"manager": "Manager", "operator": "Operator", "agent_staff": "Agent nhân viên", "auditor": "Auditor"}


# ─── (a) tên vai trò ──────────────────────────────────────────────────────────

def test_rbac_role_names_are_vietnamese() -> None:
    names = {r.code: r.name for r in rbac.ROLES}
    assert names["owner"] == "Owner — Sếp"
    for code, vi in VI_NAMES.items():
        assert names[code] == vi
    assert not {"Manager", "Operator", "Auditor"} & set(names.values())


async def test_api_surfaces_return_the_same_role_names(owner_api: Api) -> None:
    perms = (await owner_api.get("/permissions")).json()["roles"]
    assert {r["code"]: r["name"] for r in perms if r["code"] in VI_NAMES} == VI_NAMES
    users = await owner_api.get("/users")
    assert users.status_code == 200, users.text
    assert {r["code"]: r["name"] for r in users.json()["roles"] if r["code"] in VI_NAMES} == VI_NAMES


async def test_new_org_gets_vietnamese_role_names_in_db(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    rows = (await db.execute(text("SELECT code, name FROM core.roles WHERE org_id = :o"), {"o": org})).all()
    assert {r.code: r.name for r in rows if r.code in VI_NAMES} == VI_NAMES


async def test_migration_0036_renames_old_roles_only_and_is_rerunnable(owner_api: Api, db: Any, fresh_db: str) -> None:
    org = await org_id(db)
    await db.rollback()
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        for code, old in OLD_NAMES.items():
            c.execute("UPDATE core.roles SET name = %s WHERE org_id = %s AND code = %s", (old, org, code))
        sql = SQL_0036.read_text(encoding="utf-8")

        def names() -> dict[str, str]:
            rows = c.execute("SELECT code, name FROM core.roles WHERE org_id = %s", (org,)).fetchall()
            return {code: name for code, name in rows}

        assert {k: v for k, v in names().items() if k in OLD_NAMES} == OLD_NAMES   # trước: máy cũ còn tên Anh
        c.execute(sql)  # type: ignore[call-overload]
        first = names()
        assert {k: v for k, v in first.items() if k in VI_NAMES} == VI_NAMES
        assert first["owner"] == "Owner — Sếp"
        c.execute(sql)  # type: ignore[call-overload]
        assert names() == first                                    # chạy lại lần hai không đổi gì
        # Owner tự đặt tên khác ⇒ KHÔNG bị ghi đè.
        c.execute("UPDATE core.roles SET name = 'Trưởng ca' WHERE org_id = %s AND code = 'manager'", (org,))
        c.execute(sql)  # type: ignore[call-overload]
        assert names()["manager"] == "Trưởng ca"


def test_no_code_compares_by_role_name() -> None:
    """Đổi tên vai trò không được đổi hành vi: mọi so sánh vai trò đi theo `code`, không theo `name`."""
    bad: list[str] = []
    pat = re.compile(
        r"role_name\s*(==|!=|\bin\b)|\.role\.name\s*(==|!=)|\br\.name\s*(==|!=)\s*['\"](Manager|Operator|Auditor|Owner)"
    )
    for path in GH_DIR.rglob("*.py"):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{no}: {line.strip()}")
    assert bad == []


# ─── (b) mẫu agent bước 8 ─────────────────────────────────────────────────────

def test_step8_template_keys_exist_in_server_templates() -> None:
    src = STEP8.read_text(encoding="utf-8")
    keys = re.findall(r"\{ value: '([^']*)', label:", src)
    assert len(keys) >= 5
    unknown = [k for k in keys if k and k not in TEMPLATES]
    assert unknown == [], f"mẫu ở bước 8 không có trong máy chủ: {unknown}"
    default = re.search(r"DEFAULT_TEMPLATE = '([^']+)'", src)
    assert default is not None and default.group(1) in TEMPLATES


async def test_legacy_step8_agent_cannot_save_until_migrated_then_can(owner_api: Api, db: Any, fresh_db: str) -> None:
    org = await org_id(db)
    ids: dict[str, Any] = {}
    for tpl in ("sales", "ops", "cs"):
        ids[tpl] = (await db.execute(text("""
            INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when,
                                          autonomy_level)
            VALUES (:o, :n, 'Báo giá', :t, '{}'::jsonb, 'lễ phép', 'khi được hỏi', 3) RETURNING id"""),
            {"o": org, "n": f"Trợ lý {tpl}", "t": tpl})).scalar_one()
    await db.commit()
    # Trước: web luôn gửi lại template của agent ⇒ agent cũ 'sales' bị 422 "Mẫu không tồn tại".
    r = await owner_api.send("PATCH", f"/agents/{ids['sales']}", {"name": "Đổi tên", "template": "sales"})
    assert r.status_code == 422, r.text
    sql = SQL_0036.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]
        first = c.execute("SELECT name, template FROM agent.identities ORDER BY name").fetchall()
        c.execute(sql)  # type: ignore[call-overload]
        assert c.execute("SELECT name, template FROM agent.identities ORDER BY name").fetchall() == first
    assert dict(first) == {"Trợ lý sales": "commercial", "Trợ lý ops": "admin", "Trợ lý cs": "cs"}
    # Sau: agent đã chuyển mẫu sửa/lưu được như agent tạo mới.
    for tpl, now in (("sales", "commercial"), ("ops", "admin")):
        r = await owner_api.send("PATCH", f"/agents/{ids[tpl]}", {"name": f"Đổi tên {tpl}", "template": now})
        assert r.status_code == 200, r.text
        assert r.json()["template"] == now


def test_step8_server_accepts_only_real_template_keys_and_maps_legacy_ones() -> None:
    """Bản web cũ còn trong bộ nhớ đệm gửi 'sales'/'ops': máy chủ đổi sang khoá thật, không lưu khoá sai (F070)."""
    assert Step8In(template="sales").template == "commercial"
    assert Step8In(template="ops").template == "admin"
    assert Step8In(template="").template is None and Step8In().template is None
    for key in TEMPLATES:
        assert Step8In(template=key).template == key
    with pytest.raises(ValidationError, match="Mẫu không tồn tại"):
        Step8In(template="khong-co-mau-nay")


async def test_step8_api_rejects_unknown_template_with_422(owner_api: Api) -> None:
    r = await owner_api.send("PUT", "/setup/steps/8", {"template": "khong-co-mau-nay"})
    assert r.status_code == 422, r.text
