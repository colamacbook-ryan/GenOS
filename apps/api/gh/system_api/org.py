"""Điều khiển hệ thống › Tổ chức (v0.1.22, Đợt B2) + Trợ giúp › Giới thiệu (Đợt B3).

- `GET /system/org` (`system.read`) / `PATCH /system/org` (chỉ Owner): tên tổ chức, múi giờ, tiền tệ, "Sếp tự
  xưng là", "Agent gọi Sếp là" — sửa được sau khi thiết lập, kiểm bằng ĐÚNG hàm của bước 3
  (`gh.setup.routes.validate_org`). Xưng hô là của Owner đang đăng nhập (`core.users.addressing`). Ghi Action Log
  `org.updated` kèm các trường đã đổi (giá trị cũ → mới).
- `GET /system/about` (mọi người đã đăng nhập): phiên bản đang chạy, tên tổ chức, múi giờ — cho trang /help và
  "Báo lỗi". v0.1.36 (F-46) tách hai nguồn phiên bản:
  - `image_version`: phiên bản của chính ảnh api/worker (`gh.__version__` — `GH_VERSION` từ build-arg `VERSION`, hoặc
    tệp VERSION của repo khi phát triển; không có ⇒ "dev");
  - `genh_version`: bản genh đã cài (genh.json trong hộp thư chung — cùng nguồn với "Cập nhật ngay"; bản phát triển
    không có ⇒ null);
  - `version` = `genh_version` nếu có, không thì `image_version` (giữ khoá cũ cho web cũ).
"""

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import gh
from gh.auth import service
from gh.auth.deps import current_user, require, require_owner
from gh.chassis import actionlog
from gh.db import DB
from gh.setup.routes import CURRENCIES, Step3In, validate_org
from gh.system_api import update

router = APIRouter(tags=["system"])
READ = require("system.read")


async def org_payload(db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    org = (await db.execute(text("SELECT name, timezone, currency FROM core.organizations WHERE id = :o"),
                            {"o": user.org_id})).one()
    addressing = (await db.execute(text("SELECT addressing FROM core.users WHERE id = :u"),
                                   {"u": user.id})).scalar() or {}
    return {"org_name": org.name, "timezone": org.timezone, "currency": org.currency.strip(),
            "self_name": addressing.get("self") or "", "bot_calls_me": addressing.get("bot_calls_me") or "",
            "currencies": list(CURRENCIES), "can_edit": user.role_code == "owner"}


@router.get("/system/org")
async def get_org(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    return await org_payload(db, user)


@router.patch("/system/org")
async def patch_org(body: Step3In, user: service.CurrentUser = Depends(require_owner),
                    db: AsyncSession = DB) -> dict[str, Any]:
    org_name, tz, currency, self_name, bot_calls = validate_org(body)
    before = await org_payload(db, user)
    after = {"org_name": org_name, "timezone": tz, "currency": currency, "self_name": self_name,
             "bot_calls_me": bot_calls}
    changes = {k: {"from": before[k], "to": v} for k, v in after.items() if before[k] != v}
    if not changes:
        return before
    await db.execute(text("UPDATE core.organizations SET name = :n, timezone = :tz, currency = :c WHERE id = :o"),
                     {"n": org_name, "tz": tz, "c": currency, "o": user.org_id})
    await db.execute(text("UPDATE core.users SET addressing = addressing || CAST(:a AS jsonb), updated_at = now() "
                          "WHERE id = :u"), {"a": json.dumps({"self": self_name, "bot_calls_me": bot_calls}),
                                             "u": user.id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="org.updated", target_type="organization", target_id=str(user.org_id),
                           target_label=org_name, detail={"fields": sorted(changes), "changes": changes},
                           ip=user.ip)
    user.addressing = {**user.addressing, "self": self_name, "bot_calls_me": bot_calls}
    return await org_payload(db, user)


@router.get("/system/about")
async def about(user: service.CurrentUser = Depends(current_user), db: AsyncSession = DB) -> dict[str, Any]:
    org = (await db.execute(text("SELECT name, timezone FROM core.organizations WHERE id = :o"),
                            {"o": user.org_id})).one()
    genh_version = update.running_version()
    image_version = gh.__version__
    # Bản phát triển (không build-arg, không tệp VERSION ⇒ "dev"): `version` null để web hiện "bản phát triển".
    legacy = genh_version or (image_version if image_version != "dev" else None)
    return {"version": legacy, "image_version": image_version,
            "genh_version": genh_version, "org_name": org.name,
            "timezone": org.timezone, "role": {"code": user.role_code, "name": user.role_name}}
