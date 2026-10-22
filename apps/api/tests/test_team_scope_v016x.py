"""v0.1.69 (D4; F096, Q4=2 của Sếp 16/10): vai Quản lý KHÔNG hứa "thấy team mình" khi hệ thống chưa có cách tạo team.

Quyết định Q4 = 2: bỏ lời hứa — Quản lý thấy việc được giao cho mình (và khách được giao); tính năng Nhóm (tạo nhóm, xếp
người) ghi vào Nợ ROADMAP và làm khi đội đông. Mô tả vai ở máy chủ, nhãn ở web và hành vi phải KHỚP nhau.
"""

import re
import uuid
from pathlib import Path
from typing import Any

from gh.auth import rbac
from tests.conftest import Api
from tests.test_audit_search import _add, _org
from tests.test_rbac_api import login_as

ROOT = Path(__file__).resolve().parents[3]
GH_DIR = ROOT / "apps" / "api" / "gh"


def test_manager_role_meta_promises_assigned_work_not_a_team() -> None:
    meta = {rd.code: rd.meta for rd in rbac.ROLES}
    assert meta["manager"] == "thấy việc được giao"
    assert all("team" not in rd.meta.lower() for rd in rbac.ROLES)


async def test_roles_payload_does_not_mention_team(owner_api: Api) -> None:
    roles = (await owner_api.get("/users")).json()["roles"]
    manager = next(r for r in roles if r["code"] == "manager")
    assert manager["meta"] == "thấy việc được giao"
    assert not [r for r in roles if "team" in r["meta"].lower()]


def test_nothing_in_the_app_creates_teams_so_the_promise_cannot_be_kept() -> None:
    """Lý do của Q4 = 2: không có đường ghi `core.teams` / `team_id` ⇒ phạm vi team luôn rỗng. Khi một gói Nhóm thêm
    đường ghi, test này đỏ ⇒ phải đổi lại nhãn vai Quản lý và `TEAMS_AVAILABLE` ở web (systemModel.ts) cùng lúc."""
    pat = re.compile(r"INTO\s+core\.teams|SET\s+team_id|team_id\s*=\s*:", re.IGNORECASE)
    hits = [f"{p.relative_to(ROOT)}:{n}" for p in GH_DIR.rglob("*.py")
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if pat.search(line) and "SELECT" not in line.upper()]
    assert hits == []
    model = (ROOT / "apps" / "web" / "src" / "screens" / "system" / "systemModel.ts").read_text(encoding="utf-8")
    assert re.search(r"export const TEAMS_AVAILABLE = false", model)


async def test_manager_without_team_sees_only_own_audit_rows(owner_api: Api, client: Any, db: Any) -> None:
    """Hành vi thật khớp lời đã sửa: Quản lý chưa thuộc team nào chỉ thấy nhật ký của chính mình."""
    org = await _org(db)
    await _add(db, org, "rule.updated", actor_type="user", actor_id="user:" + str(uuid.uuid4()),
               target_label="Của người khác")
    await db.commit()
    manager = await login_as(client, db, "manager")
    items = (await manager.get("/audit?limit=50")).json()["items"]
    assert items, "Quản lý phải thấy ít nhất dòng của chính mình (đăng nhập, đặt PIN)"
    actors = {i["actor_id"] for i in items}
    assert len(actors) == 1 and next(iter(actors)).startswith("user:")
    assert not [i for i in items if i["target_label"] == "Của người khác"]
