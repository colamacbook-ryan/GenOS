"""/plugins — danh sách, bật/tắt, gỡ plugin (giai đoạn 1) + PLAN 4.4: nạp từ tệp, reset breaker, log LIVE.

Nạp từ tệp (`local_file`, ARCHITECTURE §6.4 "Cài: kiểm chữ ký, hiện danh sách quyền xin, yêu cầu PIN") — phạm vi
tối thiểu có chủ đích: KHÔNG chạy mã tải lên trong tiến trình api/worker (không có sandbox tiến trình con cho một
gói bất kỳ do người dùng tải lên, khác plugin nền đọc từ đĩa lúc build). `POST /plugins/local` chỉ kiểm chữ ký
(ed25519, khoá tin cậy ở `GH_PLUGIN_TRUSTED_SIGNING_KEYS`) + PIN rồi lưu một dòng `ops.plugins` ở trạng thái
`permissions_status='pending'`, `is_enabled=false` — không đăng ký vào `PluginManager` đang chạy, không tự bật.
Kích hoạt runtime thật cho local_file (chạy trong sandbox tiến trình con) nằm ngoài phạm vi cụm này.
"""

import base64
from datetime import datetime
from typing import Any

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import realtime
from gh.auth import rbac, service
from gh.auth.deps import require, require_pin
from gh.chassis import actionlog
from gh.chassis.plugins import Manifest, PluginError, PluginManager
from gh.config import get_settings
from gh.data.common import iso
from gh.db import DB
from gh.errors import ApiError, conflict, field_errors, not_found

router = APIRouter(prefix="/plugins", tags=["plugins"])
READ = require("system.read", rbac.ALL)
MANAGE = require("system.manage", rbac.ALL)

realtime.register_event("plugin.log", "system.read")


class ToggleIn(BaseModel):
    enabled: bool


def _manager(request: Request) -> PluginManager:
    return request.app.state.plugins


def _plugin_error(e: PluginError) -> ApiError:
    if e.code == "NOT_FOUND":
        return not_found("Plugin")
    return conflict(e.code, str(e))


@router.get("")
async def list_plugins(request: Request, user: service.CurrentUser = Depends(require("system.read", rbac.ALL)),
                       db: AsyncSession = DB) -> list[dict[str, Any]]:
    live = {r["package"]: r for r in _manager(request).reports()}
    rows = (await db.execute(text("""
        SELECT p.package, p.name, p.layer, p.origin, p.version, p.is_enabled, p.load_order, p.sandbox,
               p.permissions, p.signature_ok, p.installed_at, p.manifest, p.permissions_status,
               COALESCE(array_agg(d.depends_on) FILTER (WHERE d.depends_on IS NOT NULL), '{}') AS deps
        FROM ops.plugins p LEFT JOIN ops.plugin_dependencies d ON d.plugin_id = p.id
        GROUP BY p.id ORDER BY p.origin <> 'core', p.load_order NULLS LAST, p.package"""))).all()
    out = []
    for r in rows:
        rep = live.get(r.package, {})
        manifest = r.manifest or {}
        out.append({
            "package": r.package, "name": r.name, "layer": r.layer, "origin": r.origin, "version": r.version,
            "description": manifest.get("description"), "enabled": r.is_enabled, "load_order": r.load_order,
            "removable": r.origin != "core" and bool(manifest.get("removable", True)),
            "can_disable": bool(manifest.get("can_disable", True)),
            "sandbox": r.sandbox, "permissions": r.permissions, "signature_ok": r.signature_ok,
            "permissions_status": r.permissions_status,
            "installed_at": r.installed_at.isoformat(), "dependencies": list(r.deps),
            "health": rep.get("health", "healthy" if r.is_enabled else "disabled"),
            "breaker": rep.get("breaker", {"state": "closed", "total_errors": 0, "last_error": None}),
        })
    return out


@router.patch("/{package:path}/toggle")
async def toggle(package: str, body: ToggleIn, request: Request,
                 _: service.CurrentUser = Depends(require("system.manage", rbac.ALL)),
                 user: service.CurrentUser = Depends(require_pin("plugin.toggle")),
                 db: AsyncSession = DB) -> dict[str, Any]:
    pm = _manager(request)
    try:
        lp = await (pm.enable(package) if body.enabled else pm.disable(package))
    except PluginError as e:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="plugin.enable" if body.enabled else "plugin.disable", target_type="plugin",
                               target_id=package, result="blocked", detail={"code": e.code, "reason": str(e)},
                               ip=user.ip)
        await db.commit()
        raise _plugin_error(e) from e
    await pm.broadcast("enable" if body.enabled else "disable", package, user.actor_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="plugin.enable" if body.enabled else "plugin.disable", target_type="plugin",
                           target_id=package, target_label=lp.manifest.name, ip=user.ip)
    return lp.report()


@router.delete("/{package:path}", status_code=204)
async def uninstall(package: str, request: Request, response: Response,
                    _: service.CurrentUser = Depends(require("system.manage", rbac.ALL)),
                    user: service.CurrentUser = Depends(require_pin("plugin.uninstall")),
                    db: AsyncSession = DB) -> Response:
    pm = _manager(request)
    try:
        name = pm.get(package).manifest.name
        await pm.uninstall(package)
    except PluginError as e:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="plugin.uninstall", target_type="plugin", target_id=package,
                               result="blocked", detail={"code": e.code, "reason": str(e)}, ip=user.ip)
        await db.commit()
        raise _plugin_error(e) from e
    await pm.broadcast("uninstall", package, user.actor_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="plugin.uninstall", target_type="plugin", target_id=package, target_label=name,
                           ip=user.ip)
    response.status_code = 204
    return response


# ─── reset breaker (PLAN 4.4) ──────────────────────────────────────────────────

@router.post("/{package:path}/breaker/reset")
async def reset_breaker(package: str, request: Request, user: service.CurrentUser = Depends(MANAGE),
                        db: AsyncSession = DB) -> dict[str, Any]:
    """Đóng thủ công một breaker đang mở/nửa mở — cần `system.manage`, không cần PIN (không nằm trong danh mục
    `PIN_OPERATIONS`, khác cài/gỡ/bật/tắt)."""
    pm = _manager(request)
    try:
        pm.reset_breaker(package)
    except PluginError as e:
        raise _plugin_error(e) from e
    await pm.broadcast("reset_breaker", package, user.actor_id)
    lp = pm.get(package)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="plugin.breaker_reset", target_type="plugin", target_id=package,
                           target_label=lp.manifest.name, ip=user.ip)
    return lp.report()


# ─── nhật ký LIVE (`ops.plugin_logs`, PLAN 4.4) ────────────────────────────────

@router.get("/{package:path}/logs")
async def plugin_logs(package: str, cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                      _: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    pid = (await db.execute(text("SELECT id FROM ops.plugins WHERE package = :p"), {"p": package})).scalar_one_or_none()
    if pid is None:
        raise not_found("Plugin")
    where = "WHERE plugin_id = :i"
    params: dict[str, Any] = {"i": pid}
    if cursor:
        try:
            ts, cid = cursor.split("|")
            params["cts"], params["cid"] = datetime.fromisoformat(ts), cid
        except ValueError as e:
            raise field_errors({"cursor": "Con trỏ không hợp lệ"}) from e
        where += " AND (at, id) < (:cts, CAST(:cid AS uuid))"
    rows = (await db.execute(text(f"""SELECT id, at, level, message, ctx FROM ops.plugin_logs {where}
                                      ORDER BY at DESC, id DESC LIMIT :lim"""),  # noqa: S608
                             {**params, "lim": limit + 1})).all()
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = f"{rows[-1].at.isoformat()}|{rows[-1].id}" if more else None
    return {"items": [{"id": str(r.id), "at": iso(r.at), "level": r.level, "message": r.message, "ctx": r.ctx}
                      for r in rows], "next_cursor": nxt}


# ─── nạp từ tệp (local_file) ───────────────────────────────────────────────────

def _trusted_signing_keys() -> list[Any]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    out = []
    for b64 in filter(None, (s.strip() for s in get_settings().plugin_trusted_signing_keys.split(","))):
        try:
            out.append(Ed25519PublicKey.from_public_bytes(base64.b64decode(b64)))
        except (ValueError, TypeError):
            continue
    return out


def _verify_signature(payload: bytes, signature_b64: str) -> bool:
    """Chữ ký ed25519: `signature_b64` = base64(Ed25519.sign(khoá riêng nhà phát triển, payload)); chỉ hợp lệ nếu
    khớp một khoá công khai trong `GH_PLUGIN_TRUSTED_SIGNING_KEYS`. Trống ở dev/test → luôn signature_ok=false
    (không tự ý coi một gói chưa ai xác nhận là đáng tin — Owner tự cấu hình khoá tin cậy khi triển khai thật)."""
    from cryptography.exceptions import InvalidSignature

    try:
        sig = base64.b64decode(signature_b64)
    except (ValueError, TypeError):
        return False
    for key in _trusted_signing_keys():
        try:
            key.verify(sig, payload)
            return True
        except InvalidSignature:
            continue
    return False


class LocalInstallIn(BaseModel):
    manifest: dict[str, Any]
    code_sha256: str = Field(min_length=64, max_length=64, description="sha256 (hex) của mã plugin tải lên")
    signature: str = Field(min_length=1, description="base64(Ed25519.sign(khoá riêng, manifest||code_sha256))")


@router.post("/local", status_code=201)
async def install_local(body: LocalInstallIn, _: service.CurrentUser = Depends(MANAGE),
                        user: service.CurrentUser = Depends(require_pin("plugin.install")),
                        db: AsyncSession = DB) -> dict[str, Any]:
    """Nạp plugin từ tệp (ARCHITECTURE §6.4). Phạm vi tối thiểu — xem docstring đầu file: KHÔNG chạy mã tải lên.

    Luồng: kiểm manifest hợp lệ → kiểm chữ ký (từ chối nếu không khớp khoá tin cậy nào, 409 SIGNATURE_INVALID —
    "kiểm chữ ký" chỉ có ý nghĩa nếu chữ ký sai thì không cài được) → PIN đã qua ở dependency → lưu
    `ops.plugins` với `origin='local_file'`, `permissions_status='pending'`, `is_enabled=false`. KHÔNG đăng ký
    vào `PluginManager` đang chạy — plugin nằm ở trạng thái chờ, không tự bật/chạy được qua `/toggle` (404) cho
    tới khi có một tính năng nạp runtime thật riêng cho local_file.
    """
    try:
        manifest = Manifest.model_validate(body.manifest)
    except ValidationError as e:
        raise field_errors({".".join(str(p) for p in err["loc"]): err["msg"] for err in e.errors()}) from e
    try:
        code_hash = bytes.fromhex(body.code_sha256)
    except ValueError as e:
        raise field_errors({"code_sha256": "Phải là chuỗi hex sha256 (64 ký tự)"}) from e
    exists = (await db.execute(text("SELECT 1 FROM ops.plugins WHERE package = :p"),
                               {"p": manifest.package})).scalar_one_or_none()
    if exists:
        raise conflict("PLUGIN_EXISTS", f"Đã có plugin {manifest.package}")
    payload = orjson.dumps(body.manifest, option=orjson.OPT_SORT_KEYS) + b"|" + body.code_sha256.encode()
    signature_ok = _verify_signature(payload, body.signature)
    if not signature_ok:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="plugin.install_rejected", target_type="plugin",
                               target_id=manifest.package, target_label=manifest.name, result="blocked",
                               detail={"reason": "chữ ký không hợp lệ hoặc không rõ nguồn"}, ip=user.ip)
        await db.commit()
        raise conflict("SIGNATURE_INVALID", "Chữ ký không hợp lệ — không cài plugin chưa xác thực được nguồn")
    row = (await db.execute(text("""
        INSERT INTO ops.plugins (package, name, layer, origin, version, is_enabled, load_order, sandbox,
                                 permissions, signature_ok, manifest, permissions_status, code_sha256)
        VALUES (:pkg, :name, :layer, 'local_file', :ver, false, :lo, CAST(:sb AS jsonb), :perms, true,
               CAST(:mf AS jsonb), 'pending', :sha)
        RETURNING id, installed_at"""),
        {"pkg": manifest.package, "name": manifest.name, "layer": manifest.layer, "ver": manifest.version,
         "lo": manifest.load_order, "sb": manifest.sandbox.model_dump_json(), "perms": manifest.permissions,
         "mf": manifest.model_dump_json(), "sha": code_hash})).one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="plugin.install_requested", target_type="plugin", target_id=manifest.package,
                           target_label=manifest.name, detail={"permissions": manifest.permissions,
                           "signature_ok": True}, ip=user.ip)
    return {"id": str(row.id), "package": manifest.package, "name": manifest.name, "version": manifest.version,
           "origin": "local_file", "is_enabled": False, "permissions_status": "pending",
           "signature_ok": True, "permissions": manifest.permissions, "installed_at": iso(row.installed_at)}
