"""/defaults — "Chế độ tiêu chuẩn" + "Về mặc định" (v0.1.55, G1). CHỈ Owner (nhân viên → 403 FORBIDDEN).

- `GET /defaults` → `{items: [{key, label, scope, group, default_text, current_text, customized, resettable}],
  customized_count, suggestions}`. Chỉ trả CHỮ (`*_text`) — không bao giờ trả giá trị thô dạng object cho web vẽ.
- `POST /defaults/{key}/reset` {confirm: true} — một mục. Khoá lạ 404 DEFAULTS_KEY_UNKNOWN; mục chỉ hiển thị 409
  DEFAULTS_NOT_RESETTABLE; thiếu Xác nhận 422.
- `POST /defaults/apply-standard` {confirm: true} — "Áp model chuẩn theo vai": xoá đúng các dòng gán core.gen /
  core.briefing / core.refinery / core.reply (agent:* và khoá/nguồn giữ nguyên) ⇒ Gen dùng hồ sơ tiêu chuẩn.
- `POST /defaults/reset-all` {confirm: true} + phiên mã PIN `defaults.reset_all` (423 PIN_REQUIRED) — mọi mục
  resettable, kể cả mục `autonomy` (mức tự trị của tổ chức về mặc định 4; là mục sổ riêng nên hiện trong danh sách,
  đếm vào "Đã đổi" và nói trong hộp Xác nhận). Về mặc định riêng mục `autonomy` cũng đòi phiên PIN.

Action Log 'defaults.reset': target_id = khoá | 'all' | 'apply_standard' (+ `detail.value` giống vậy). KHÔNG ghi giá
trị cài đặt. TUYỆT ĐỐI không chạm khoá API, nguồn AI, mã PIN/mật khẩu, Gen-hub, kênh báo động, tài khoản mạng xã hội,
ranh giới cứng, danh tính tổ chức (xem `gh.defaults.registry`).
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require_owner, require_pin
from gh.chassis import actionlog
from gh.db import DB
from gh.defaults import registry
from gh.errors import ApiError, field_errors, pin_required

router = APIRouter(prefix="/defaults", tags=["defaults"])

CONFIRM_TITLE = "Sếp bấm Xác nhận giúp em trước khi Về mặc định"
ACTION = "defaults.reset"


class ConfirmIn(BaseModel):
    confirm: bool


def _need_confirm(body: ConfirmIn) -> None:
    if body.confirm is not True:
        raise field_errors({"confirm": CONFIRM_TITLE})


async def _log(db: AsyncSession, user: service.CurrentUser, value: str, **extra: Any) -> None:
    """Một dòng Action Log; chỉ khoá (key | 'all' | 'apply_standard') và số đếm — KHÔNG ghi giá trị cài đặt."""
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action=ACTION,
                           target_type="defaults", target_id=value, detail={"value": value, **extra}, ip=user.ip)


@router.get("")
async def list_defaults(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    rows = await registry.describe(db, user.org_id, user.id)
    return {"items": rows,
            "customized_count": sum(1 for r in rows if r["customized"] and r["resettable"]),
            "suggestions": await registry.suggestions(db, user.org_id)}


@router.post("/apply-standard")
async def apply_standard(body: ConfirmIn, user: service.CurrentUser = Depends(require_owner),
                         db: AsyncSession = DB) -> dict[str, Any]:
    _need_confirm(body)
    removed = (await db.execute(text("""DELETE FROM agent.bindings WHERE org_id = :o AND agent_key = ANY(:k)
                                        RETURNING agent_key"""),
                                {"o": user.org_id, "k": list(registry.CORE_BINDING_KEYS)})).all()
    await _log(db, user, "apply_standard", count=len(removed))
    return {"removed": len(removed)}


@router.post("/reset-all")
async def reset_all(body: ConfirmIn, user: service.CurrentUser = Depends(require_owner),
                    _pin: service.CurrentUser = Depends(require_pin("defaults.reset_all")),
                    db: AsyncSession = DB) -> dict[str, Any]:
    _need_confirm(body)
    n = 0
    for it in await registry.items(db, user.org_id):
        if it.resettable:
            await it.reset(db, user.org_id, user.id)
            n += 1
    await _log(db, user, "all", count=n)
    return {"reset": n}


@router.post("/{key}/reset")
async def reset_one(key: str, body: ConfirmIn, user: service.CurrentUser = Depends(require_owner),
                    db: AsyncSession = DB) -> dict[str, Any]:
    it = await registry.find(db, user.org_id, key)
    if it is None:
        raise ApiError(404, "DEFAULTS_KEY_UNKNOWN", "Em không có mục mặc định này — Sếp tải lại trang rồi thử lại")
    if not it.resettable:
        raise ApiError(409, "DEFAULTS_NOT_RESETTABLE",
                       "Mục này chỉ để xem — Sếp đổi ở thẻ của nó (đổi nguồn model cần mã PIN)")
    _need_confirm(body)
    if key == registry.AUTONOMY_KEY and not user.pin_active():
        raise pin_required()     # nâng mức tự trị của tổ chức luôn cần phiên mã PIN, kể cả khi chỉ đổi riêng mục này
    await it.reset(db, user.org_id, user.id)
    await _log(db, user, key)
    st = await it.read(db, user.org_id, user.id)
    return {"key": key, "reset": True, "customized": st.customized, "current_text": st.current_text}
