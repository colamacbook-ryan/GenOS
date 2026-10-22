"""Phạm vi dữ liệu (ScopeFilter) cho mọi màn kinh doanh — tầng service, không chỉ route (ARCHITECTURE §8.3).

Quyền = năng lực × phạm vi (`all | team | assigned | none`). Màn gọi `scope_for(db, user, "profile.read")` một lần,
rồi ghép biểu thức SQL do `Scope` sinh vào truy vấn của mình:

    sc = await scope_for(db, user, "profile.read")
    where, params = sc.person_sql("p")
    rows = await db.execute(text(f"SELECT … FROM core.persons p WHERE p.org_id = :o AND {where}"), {"o": …, **params})

Đối tượng ngoài phạm vi phải trả 404 (không lộ là có tồn tại): dùng `ensure_person` / `ensure_group` / `not_found`.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.errors import ApiError, forbidden


def not_found(what: str = "Đối tượng") -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what} không tồn tại hoặc ngoài phạm vi của bạn")


async def ensure_active_user(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    """UUID người được gán (người xử lý/người phụ trách) phải là người dùng đang hoạt động, chưa xoá mềm, cùng org
    — khớp /pickers/users; không để khoá ngoại nổ thành 500 (F-1). Dùng chung cho vụ việc, hồ sơ."""
    if user_id is None:
        return
    ok = (await db.execute(text("SELECT 1 FROM core.users WHERE id = :u AND org_id = :o AND is_active "
                                "AND deleted_at IS NULL"),
                           {"u": user_id, "o": org_id})).first()
    if ok is None:
        raise not_found("Người dùng")


# ─── «Quyền xem» của nhóm (`core.groups.view_scope`, v0.1.71) ─────────────────────────────────────────────────────────
# Sếp đặt cho từng nhóm chat: «Chỉ Sếp» (`owner`) · «Sếp + quản lý» (`manager`) · «Mọi thành viên»
# (`all_members`). Trước v0.1.71 chỉ lưu và hiển thị, không API đọc nào lọc theo nó. Nay MỘT chỗ này quyết
# định, mọi API đọc tin / đơn vị / nhóm ghép vào (`Scope.group_sql`, `Scope.group_id_sql`,
# `direct_visible_sql`): Owner thấy hết; vai Quản lý thấy nhóm `manager` + `all_members`; mọi vai khác (Vận
# hành, Nhân viên phụ trách, Kiểm soát) chỉ thấy nhóm `all_members`. Quyền theo ma trận vẫn áp thêm (phạm vi
# gán, v.v.): `view_scope` chỉ THU HẸP, không mở rộng.

def view_scopes_for(role_code: str) -> tuple[str, ...] | None:
    """Các giá trị `view_scope` mà vai này được đọc; None = đọc mọi nhóm (Owner)."""
    if role_code == rbac.OWNER:
        return None
    if role_code == rbac.MANAGER:
        return ("manager", "all_members")
    return ("all_members",)


def group_view_sql(role_code: str, group_col: str) -> str:
    """Điều kiện SQL «vai này được đọc nhóm có id ở cột `group_col`» (cột NULL ⇒ sai: không có nhóm để xét)."""
    allowed = view_scopes_for(role_code)
    if allowed is None:
        return "TRUE"
    lst = ", ".join(f"'{v}'" for v in allowed)       # hằng nội bộ, không nhận từ người dùng
    # `IN (truy vấn con không tương quan)` ⇒ Postgres băm một lần rồi tra, không dò từng dòng tin (bảng tin rất lớn).
    return f"{group_col} IN (SELECT vg.id FROM core.groups vg WHERE vg.view_scope IN ({lst}))"


def group_row_view_sql(role_code: str, alias: str) -> str:
    """Như `group_view_sql` nhưng trên một dòng `core.groups` có bí danh `alias` (không cần truy vấn con)."""
    allowed = view_scopes_for(role_code)
    if allowed is None:
        return "TRUE"
    lst = ", ".join(f"'{v}'" for v in allowed)
    return f"{alias}.view_scope IN ({lst})"


@dataclass(frozen=True)
class Scope:
    org_id: uuid.UUID
    user_id: uuid.UUID
    level: str                                   # all | team | assigned | none
    users: tuple[uuid.UUID, ...] = field(default=())   # người dùng mà phạm vi bao phủ (mình, hoặc cả team)
    role: str = ""                               # vai người xem (rỗng = hẹp nhất) — để xét `view_scope`

    @property
    def is_all(self) -> bool:
        return self.level == rbac.ALL

    def _params(self) -> dict[str, Any]:
        return {"scope_users": list(self.users)}

    def person_sql(self, alias: str = "p") -> tuple[str, dict[str, Any]]:
        """Biểu thức boolean trên một dòng `core.persons` có bí danh `alias`."""
        if self.is_all:
            return "TRUE", {}
        if self.level == rbac.NONE:
            return "FALSE", {}
        return (f"({alias}.owner_user_id = ANY(:scope_users) OR EXISTS ("
                f"SELECT 1 FROM core.assignments sa WHERE sa.subject_type = 'person' AND sa.subject_id = {alias}.id"
                f" AND sa.active_to IS NULL AND sa.user_id = ANY(:scope_users)))"), self._params()

    def person_id_sql(self, column: str) -> tuple[str, dict[str, Any]]:
        """Biểu thức trên một cột chứa id người (vd `o.person_id`); NULL chỉ thấy khi phạm vi `all`."""
        if self.is_all:
            return "TRUE", {}
        where, params = self.person_sql("sp")
        return f"EXISTS (SELECT 1 FROM core.persons sp WHERE sp.id = {column} AND {where})", params

    def group_sql(self, alias: str = "g") -> tuple[str, dict[str, Any]]:
        """Biểu thức boolean trên một dòng `core.groups` có bí danh `alias` (phạm vi gán VÀ «Quyền xem» của nhóm)."""
        view = group_row_view_sql(self.role, alias)
        if self.is_all:
            return view, {}
        if self.level == rbac.NONE:
            return "FALSE", {}
        return (f"(EXISTS (SELECT 1 FROM core.assignments sa WHERE sa.subject_type = 'group'"
                f" AND sa.subject_id = {alias}.id AND sa.active_to IS NULL AND sa.user_id = ANY(:scope_users))"
                f" AND {view})"), self._params()

    def group_id_sql(self, column: str) -> tuple[str, dict[str, Any]]:
        """Biểu thức trên một cột chứa id nhóm. Cột NULL (tin 1-1) chỉ qua khi phạm vi `all` — tin 1-1 có quy tắc riêng
        ở `direct_visible_sql`."""
        if self.is_all:
            view = group_view_sql(self.role, column)
            return ("TRUE", {}) if view == "TRUE" else (f"({column} IS NULL OR {view})", {})
        where, params = self.group_sql("sg")
        return f"EXISTS (SELECT 1 FROM core.groups sg WHERE sg.id = {column} AND {where})", params

    def user_sql(self, column: str) -> tuple[str, dict[str, Any]]:
        """Biểu thức trên một cột chứa id người dùng (người phụ trách, người được giao, người tạo)."""
        if self.is_all:
            return "TRUE", {}
        if self.level == rbac.NONE:
            return "FALSE", {}
        return f"{column} = ANY(:scope_users)", self._params()

    def subject_sql(self, type_col: str, id_col: str) -> tuple[str, dict[str, Any]]:
        """Biểu thức trên cặp cột (subject_type, subject_id) kiểu person | group | khác (khác → chỉ `all`)."""
        if self.is_all:
            view = group_view_sql(self.role, id_col)       # đối tượng là nhóm ⇒ theo «Quyền xem» của nhóm
            return ("TRUE", {}) if view == "TRUE" else (f"({type_col} IS DISTINCT FROM 'group' OR {view})", {})
        pw, pp = self.person_id_sql(id_col)
        gw, gp = self.group_id_sql(id_col)
        return f"(({type_col} = 'person' AND {pw}) OR ({type_col} = 'group' AND {gw}))", {**pp, **gp}


# ─── Tin 1-1: chỉ Owner (hoặc người được giao ĐÚNG người đó) ─────────────────────────────────────────────────────────
# Tin riêng 1-1 của khách (`group_id IS NULL`) là chuyện riêng giữa khách và Sếp. Migration 0038 bật lưu 1-1 cho bản cũ
# nên Vận hành (queue.read/profile.read ALL) hay Kiểm soát (data.read ALL) — vốn "thấy hết" — không được đọc nguyên văn.
# Quy tắc MỘT chỗ, mọi API đọc tin/đơn vị ghép vào (review v0.1.65 P2-2):
#   · Owner: thấy hết.
#   · Vai khác: dòng thuộc nhóm (`group_id` không NULL) theo phạm vi thường; dòng 1-1 chỉ khi người đó (`person_id`) do
#     chính mình phụ trách (`core.persons.owner_user_id`) hoặc được giao thẳng cho mình (`core.assignments` đang hiệu
#     lực). Giao cho team không đủ: phải đúng người dùng này. Dòng 1-1 không rõ người ⇒ chỉ Owner.

_DM_PERSON_SQL = ("EXISTS (SELECT 1 FROM core.persons dmp WHERE dmp.id = {person} AND (dmp.owner_user_id = :dm_user"
                  " OR EXISTS (SELECT 1 FROM core.assignments dma WHERE dma.subject_type = 'person'"
                  " AND dma.subject_id = dmp.id AND dma.active_to IS NULL AND dma.user_id = :dm_user)))")


def direct_visible_sql(user: service.CurrentUser, group_col: str, person_col: str) -> tuple[str, dict[str, Any]]:
    """Điều kiện SQL "dòng này người xem được phép đọc nguyên văn" trên cặp cột (nhóm, người) của một tin/đơn vị."""
    if user.role_code == rbac.OWNER:
        return "TRUE", {}
    # Dòng thuộc nhóm: theo «Quyền xem» của nhóm (v0.1.71); dòng 1-1: chỉ người phụ trách đúng người đó.
    return (f"(({group_col} IS NOT NULL AND {group_view_sql(user.role_code, group_col)})"
            f" OR ({group_col} IS NULL AND {_DM_PERSON_SQL.format(person=person_col)}))"), {"dm_user": user.id}


async def direct_visible(db: AsyncSession, user: service.CurrentUser, group_id: Any, person_id: Any) -> bool:
    """Bản một dòng của `direct_visible_sql` (đã biết `group_id`, `person_id` của dòng)."""
    if user.role_code == rbac.OWNER:
        return True
    if group_id is not None:
        return await group_visible(db, user, group_id)
    if person_id is None:
        return False
    where, params = direct_visible_sql(user, "CAST(:dm_g AS uuid)", ":dm_p")
    return bool((await db.execute(text(f"SELECT {where}"),  # noqa: S608 — biểu thức nội bộ
                                  {"dm_g": None, "dm_p": uuid.UUID(str(person_id)), **params})).scalar())


async def group_visible(db: AsyncSession, user: service.CurrentUser, group_id: Any) -> bool:
    """«Quyền xem» của nhóm có cho vai của người xem đọc nội dung nhóm này không (Owner luôn được)."""
    if user.role_code == rbac.OWNER:
        return True
    where = group_view_sql(user.role_code, "CAST(:vg AS uuid)")
    return bool((await db.execute(text(f"SELECT {where}"),  # noqa: S608 — biểu thức nội bộ
                                  {"vg": uuid.UUID(str(group_id))})).scalar())


async def team_users(db: AsyncSession, team_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    """Thành viên của team và các team con."""
    rows = (await db.execute(text("""
        WITH RECURSIVE t AS (SELECT id FROM core.teams WHERE id = :t
                             UNION SELECT c.id FROM core.teams c JOIN t ON c.parent_id = t.id)
        SELECT DISTINCT ur.user_id FROM core.user_roles ur JOIN t ON ur.team_id = t.id"""), {"t": team_id})).all()
    return tuple(r.user_id for r in rows)


async def scope_for(db: AsyncSession, user: service.CurrentUser, permission: str) -> Scope:
    """Phạm vi của người gọi cho `permission`. Không có quyền → 403."""
    level = user.permissions.get(permission, rbac.NONE)
    if level == rbac.NONE:
        raise forbidden(permission)
    users: tuple[uuid.UUID, ...] = (user.id,)
    if level == rbac.TEAM:
        members = await team_users(db, user.team_id) if user.team_id else ()
        users = tuple(dict.fromkeys((user.id, *members)))
    return Scope(org_id=user.org_id, user_id=user.id, level=level, users=users, role=user.role_code)


async def ensure_person(db: AsyncSession, sc: Scope, person_id: uuid.UUID) -> None:
    where, params = sc.person_sql("p")
    ok = (await db.execute(text(f"SELECT 1 FROM core.persons p WHERE p.id = :i AND p.org_id = :o AND {where}"),
                           {"i": person_id, "o": sc.org_id, **params})).first()  # noqa: S608 — biểu thức nội bộ
    if ok is None:
        raise not_found("Người")


async def ensure_group(db: AsyncSession, sc: Scope, group_id: uuid.UUID) -> None:
    where, params = sc.group_sql("g")
    ok = (await db.execute(text(f"SELECT 1 FROM core.groups g WHERE g.id = :i AND g.org_id = :o AND {where}"),
                           {"i": group_id, "o": sc.org_id, **params})).first()  # noqa: S608 — biểu thức nội bộ
    if ok is None:
        raise not_found("Nhóm")


async def ensure_subject(db: AsyncSession, sc: Scope, subject_type: str | None, subject_id: uuid.UUID | None) -> None:
    if sc.is_all:
        return
    if subject_type == "person" and subject_id:
        await ensure_person(db, sc, subject_id)
    elif subject_type == "group" and subject_id:
        await ensure_group(db, sc, subject_id)
    else:
        raise not_found()
