"""Kết nối (kênh & đăng nhập); nhà cung cấp model, khoá; hồ sơ Antigravity CLI (docs/api/phase-2.md)."""

import csv
import io
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto, health, realtime, retention
from gh.audit.routes import export_rows, query_log
from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.biz.core.scope import group_row_view_sql
from gh.boss_checks import service as boss_service
from gh.chassis import actionlog
from gh.chassis.bus import BRIDGE_CONTROL
from gh.chassis.mcp_client import McpBlockedNetwork, McpError, pin_endpoint
from gh.data import listen_events
from gh.data.common import (
    ALL_LISTEN_MODES,
    CHANNEL_NAME,
    DIRECT_MODES,
    LISTENING_MODES,
    NEW_GROUP_MODES,
    NEW_SILENT_KEY,
    direct_mode,
    drop_channel_counter,
    iso,
    new_group_mode,
    org_settings,
    set_channel_setting,
)
from gh.data.ingest import close_sessions, publish_resume, sync_listen_sets, uptime_pct
from gh.db import DB, sessionmaker
from gh.errors import ApiError, conflict, field_errors, forbidden, not_found, pin_required
from gh.gen import jev
from gh.gen.coach import cache as coach_cache
from gh.hub_link import KHO_LABEL, load_kho_label, relabel
from gh.providers import catalog
from gh.providers import cli as climod
from gh.providers import router as mrouter
from gh.providers.clients import AGY_MODEL_RE
from gh.providers.router import KEY_AAD, cooldown_key, quota_key
from gh.shell.routes import publish_header

log = logging.getLogger("gh.system")
router = APIRouter(tags=["system"])
READ = require("system.read")
MANAGE = require("system.manage", rbac.ALL)   # F-58: system.manage luôn cần phạm vi ALL (xem deps.ALL_ONLY)
ROLES_MANAGE = require("roles.manage")
AUDIT_READ = require("audit.read")
DATA_MANAGE = require("data.manage")

REFRESH_GAP_S = 30   # giãn cách giữa hai lần "Cập nhật danh sách nhóm" của một kênh
CHANNEL_TYPES = ("zalo", "whatsapp", "telegram", "linkedin")
QR_CHANNELS = ("zalo", "whatsapp")
#: v0.1.65: một nguồn ở `gh.data.common` (trước đây mỗi nơi viết một kiểu — F066). Giữ tên cũ làm bí danh.
LISTEN_MODES = ALL_LISTEN_MODES
VIEW_SCOPES = ("owner", "manager", "all_members")
GROUP_KINDS = ("internal", "market", "partner", "customer", "private")


# ─── Kênh ───────────────────────────────────────────────────────────────────

async def _latest_session(db: AsyncSession, channel_id: uuid.UUID) -> Any:
    return (await db.execute(text("""
        SELECT id, state, account_label, started_at, ended_at, last_heartbeat_at, meta, qr_issued_at,
               (credential_enc IS NOT NULL) AS has_credential
        FROM core.channel_sessions WHERE channel_id = :c
        ORDER BY (ended_at IS NULL) DESC, COALESCE(started_at, qr_issued_at) DESC NULLS LAST, id DESC LIMIT 1"""),
        {"c": channel_id})).one_or_none()


async def channel_card(db: AsyncSession, redis: Any, org_id: uuid.UUID, type_: str) -> dict[str, Any]:
    ch = (await db.execute(text("SELECT id, name, capabilities FROM core.channels WHERE org_id = :o AND type = :t"),
                           {"o": org_id, "t": type_})).one_or_none()
    base: dict[str, Any] = {"type": type_, "name": CHANNEL_NAME[type_], "installed": ch is not None,
                            "id": str(ch.id) if ch else None, "account_label": None, "started_at": None,
                            "groups_listening": 0, "groups_pending": 0, "outbound_queued": 0,
                            "last_heartbeat_at": None, "qr": None,
                            "resumable": False, "bridge_online": None,
                            "stats": {"msgs_24h": None, "tagged_24h": None, "latency_ms": None, "uptime_pct": None}}
    if ch is None:
        return {**base, "state": "not_installed"}
    if "identity_only" in (ch.capabilities or []):
        n = (await db.execute(text("""
            SELECT count(*) AS ids,
                   count(*) FILTER (WHERE (SELECT count(*) FROM core.person_identities j
                                           WHERE j.person_id = i.person_id) > 1) AS merged
            FROM core.person_identities i WHERE i.channel_id = :c"""), {"c": ch.id})).one()
        return {**base, "state": "identity_only", "stats": {**base["stats"], "identities": n.ids, "merged": n.merged}}
    s = await _latest_session(db, ch.id)
    counts = (await db.execute(text("""
        SELECT count(*) AS msgs, count(*) FILTER (WHERE mentions_agent) AS tagged FROM raw.events
        WHERE channel_id = :c AND received_at > now() - interval '24 hours'"""), {"c": ch.id})).one()
    # v0.1.65 (F066): "đang nghe" = LISTENING_MODES và chưa rời nhóm; "chờ chọn" = pending và chưa rời.
    listening, pending = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE listen_mode = ANY(:m)), count(*) FILTER (WHERE listen_mode = 'pending')
        FROM core.groups WHERE channel_id = :c AND left_at IS NULL"""),
                                           {"c": ch.id, "m": list(LISTENING_MODES)})).one()
    state = s.state if s is not None else "logged_out"
    settings = await org_settings(db, org_id)
    dmode = direct_mode(settings, type_)
    meta = (s.meta or {}) if s is not None else {}
    qr = None
    if s is not None and state == "pending_qr":
        raw = await redis.get(f"gh:channel:qr:{s.id}")
        if raw:
            q = orjson.loads(raw)
            qr = {"session_id": str(s.id), "image": q.get("image"), "expires_at": q.get("expires_at"),
                  "scanned": bool(q.get("scanned"))}
    # v0.1.62 (F068): "Thời gian trực" đo 24 giờ THỰC (chỉ cắt bớt
    # khi kênh mới nối lần đầu trong vòng 24 giờ), không tính lại
    # từ lần nối gần nhất — nối lại sau một đêm mất mạng không được hiện 100%.
    first_up = None
    if s is not None and state == "active":
        first_up = (await db.execute(text("SELECT min(started_at) FROM core.channel_sessions WHERE channel_id = :c"),
                                     {"c": ch.id})).scalar_one()
    return {**base, "state": state, "session_id": str(s.id) if s else None,
            "account_label": s.account_label if s else None, "started_at": iso(s.started_at) if s else None,
            "ended_at": iso(s.ended_at) if s else None, "groups_listening": listening,
            "groups_pending": pending,
            "outbound_queued": int(meta.get("queued") or 0),
            "last_heartbeat_at": iso(s.last_heartbeat_at) if s else None, "qr": qr, "error": meta.get("error"),
            # v0.1.62 (F046): phiên 'error' còn credential ⇒ nối lại được bằng phiên đã lưu (không cần quét QR);
            # `bridge_online` = bộ nhận tin còn nhịp tim (khoá TTL 45 giây) — false ⇒ thẻ hiện "Mất liên lạc".
            "resumable": bool(s is not None and state == "error" and s.has_credential),
            "bridge_online": bool(await redis.get("gh:bridge:heartbeat")) if type_ in QR_CHANNELS else None,
            # v0.1.65: tin 1-1 ba mức (off | silent | reply), mặc định silent; `listen_direct` cho web cũ (khác off).
            "direct_mode": dmode, "listen_direct": dmode != "off",
            # F1d: nhóm MỚI sau này (ask | silent | off, MẶC ĐỊNH silent theo QD 17/10); `new_group_asked` = Sếp đã
            # chọn ở hộp hỏi hoặc thẻ kênh kể từ lần nối kênh gần nhất (chỉ còn để tương thích — ô «Áp dụng cho các
            # nhóm mới sau này» không còn bật sẵn). `new_groups_silent` = số nhóm mới tự vào Lắng nghe im lặng mà Sếp
            # chưa bấm «Đã biết».
            "new_group_mode": new_group_mode(settings, type_),
            "new_group_asked": _asked(settings, type_),
            "new_groups_silent": _count(settings, NEW_SILENT_KEY, type_),
            "stats": {"msgs_24h": counts.msgs, "tagged_24h": counts.tagged, "latency_ms": meta.get("latency_ms"),
                      "uptime_pct": await uptime_pct(redis, type_, first_up) if first_up is not None else None}}


def _count(settings: dict[str, Any], key: str, type_: str) -> int:
    block = settings.get(key)
    v = block.get(type_) if isinstance(block, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def _asked(settings: dict[str, Any], type_: str) -> bool:
    block = settings.get("new_group_asked")
    return bool(isinstance(block, dict) and block.get(type_) is True)


@router.get("/channels")
async def channels(request: Request, user: service.CurrentUser = Depends(READ),
                   db: AsyncSession = DB) -> list[dict[str, Any]]:
    return [await channel_card(db, request.app.state.redis, user.org_id, t) for t in CHANNEL_TYPES]


class LoginIn(BaseModel):
    account_label: str | None = Field(default=None, max_length=80)
    accept_risk: bool = False


async def _qr_channel(db: AsyncSession, org_id: uuid.UUID, type_: str) -> Any:
    if type_ not in QR_CHANNELS:
        raise not_found("Kênh")
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                           {"o": org_id, "t": type_})).one_or_none()
    if ch is None:
        raise not_found("Kênh")
    return ch


@router.post("/channels/{type_}/login", status_code=202)
async def channel_login(type_: str, body: LoginIn, request: Request, user: service.CurrentUser = Depends(MANAGE),
                        _pin: Any = Depends(require_pin("channel.login")),
                        db: AsyncSession = DB) -> dict[str, Any]:
    ch = await _qr_channel(db, user.org_id, type_)
    if not body.accept_risk:
        raise field_errors({"accept_risk": "Cần xác nhận đã đọc cảnh báo rủi ro tài khoản cá nhân"})
    redis = request.app.state.redis
    if not await redis.get("gh:bridge:heartbeat"):
        raise _bridge_offline()
    # Phiên đang chờ QR cũ bị huỷ; phiên đang hoạt động giữ tới khi phiên mới thành công.
    cancelled = (await db.execute(text("""UPDATE core.channel_sessions SET state = 'expired', ended_at = now()
                                          WHERE channel_id = :c AND state = 'pending_qr' AND ended_at IS NULL
                                          RETURNING id"""), {"c": ch.id})).scalars().all()
    sid = (await db.execute(text("""
        INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, qr_issued_at, risk_accepted_by)
        VALUES (:c, :o, :l, 'pending_qr', now(), :u) RETURNING id"""),
        {"c": ch.id, "o": user.org_id, "l": body.account_label or "", "u": user.id})).scalar_one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="channel.login",
                           target_type="channel", target_id=type_, target_label=body.account_label,
                           detail={"session_id": str(sid), "accept_risk": True}, ip=user.ip)
    # F1d: nối kênh = bắt đầu lại — hộp hỏi nhóm mới lần đầu sau đó bật sẵn «Áp dụng cho các nhóm mới sau này».
    await set_channel_setting(db, user.org_id, "new_group_asked", type_, False)
    await db.commit()
    # v0.1.62 (F067): phiên chờ QR cũ bị huỷ trong CSDL thì bridge cũng phải bỏ vòng xin QR của nó.
    await close_sessions(request.app.state.bus, user.org_id, type_, list(cancelled), actor=user.actor_id)
    await request.app.state.bus.publish(BRIDGE_CONTROL, "session.login",
                                        {"channel": type_, "session_id": str(sid), "credential": None},
                                        actor=user.actor_id, org_id=user.org_id)
    await realtime.publish(redis, "channel.status", {"type": type_, "state": "pending_qr", "session_id": str(sid),
                                                     "account_label": body.account_label, "scanned": False,
                                                     "error": None}, org_id=user.org_id)
    return {"session_id": str(sid)}


def _bridge_offline() -> ApiError:
    """503 khi bộ nhận tin (bridge) không có nhịp tim: câu cho Sếp là tiếng Việt thường; mã BRIDGE_OFFLINE nằm ở
    "Chi tiết kỹ thuật" của Console (v0.1.62, F069)."""
    return ApiError(503, "BRIDGE_OFFLINE", "Bộ nhận tin Zalo/WhatsApp đang tắt — thử lại sau ít phút; "
                    "nếu vẫn lỗi, gửi Gói chẩn đoán cho người hỗ trợ")


@router.post("/channels/{type_}/resume", status_code=202)
async def channel_resume(type_: str, request: Request, user: service.CurrentUser = Depends(MANAGE),
                         db: AsyncSession = DB) -> dict[str, Any]:
    """v0.1.62 (F046): nối lại bằng PHIÊN ĐÃ LƯU (không quét QR) khi phiên 'Lỗi kết nối' còn credential. Không đổi
    quyền truy cập nào so với lần đăng nhập đã chấp nhận rủi ro trước đó, nên không đòi mã PIN lần nữa."""
    ch = await _qr_channel(db, user.org_id, type_)
    redis = request.app.state.redis
    if not await redis.get("gh:bridge:heartbeat"):
        raise _bridge_offline()
    s = (await db.execute(text("""
        SELECT s.id, s.credential_enc, s.account_label FROM core.channel_sessions s
        WHERE s.channel_id = :c AND s.state = 'error' AND s.credential_enc IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM core.channel_sessions n WHERE n.channel_id = s.channel_id AND n.id <> s.id
                            AND COALESCE(n.started_at, n.qr_issued_at) > COALESCE(s.started_at, s.qr_issued_at))
        ORDER BY COALESCE(started_at, qr_issued_at) DESC NULLS LAST, id DESC LIMIT 1"""), {"c": ch.id})).one_or_none()
    if s is None:
        raise conflict("NO_SAVED_SESSION", "Chưa có phiên đã lưu để nối lại — hãy tạo mã QR để đăng nhập lại")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="channel.resume",
                           target_type="channel", target_id=type_, target_label=s.account_label,
                           detail={"session_id": str(s.id)}, ip=user.ip)
    await db.commit()
    await publish_resume(request.app.state.bus, user.org_id, type_, s.id, s.credential_enc, actor=user.actor_id)
    return {"session_id": str(s.id)}


@router.post("/channels/{type_}/logout", status_code=204)
async def channel_logout(type_: str, request: Request, user: service.CurrentUser = Depends(MANAGE),
                         _pin: Any = Depends(require_pin("channel.logout")),
                         db: AsyncSession = DB) -> Response:
    ch = await _qr_channel(db, user.org_id, type_)
    # Gói K (P1-1): phiên 'error' (vd. CONNECTION_REPLACED) đã có ended_at nhưng còn credential — đăng xuất phải
    # xoá cả nó, nếu không bridge khởi động lại vẫn tự nối lại được.
    rows = (await db.execute(text("""
        UPDATE core.channel_sessions SET state = 'logged_out', ended_at = COALESCE(ended_at, now()),
                                         credential_enc = NULL
        WHERE channel_id = :c AND (ended_at IS NULL OR credential_enc IS NOT NULL)
        RETURNING id, account_label"""), {"c": ch.id})).all()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="channel.logout",
                           target_type="channel", target_id=type_,
                           target_label=rows[0].account_label if rows else None,
                           detail={"sessions": [str(r.id) for r in rows]}, ip=user.ip)
    # v0.1.36 (F-6a): Sếp chủ động đăng xuất
    await health.clear(db, user.org_id, f"channel.down:{type_}", redis=request.app.state.redis)
    if rows:   # v0.1.68: dòng "Kênh … đã đăng xuất" cho Nhật ký lắng nghe
        await listen_events.record(db, user.org_id, "channel_ended", channel_id=ch.id, channel_type=type_,
                                   detail={"reason": "logged_out", "by": "owner"})
    await db.commit()
    await listen_events.publish_pending(sessionmaker(), request.app.state.redis, user.org_id,
                                        listen_events.take_pending(db))
    for r in rows:
        await request.app.state.bus.publish(BRIDGE_CONTROL, "session.logout",
                                            {"channel": type_, "session_id": str(r.id)}, actor=user.actor_id,
                                            org_id=user.org_id)
    await realtime.publish(request.app.state.redis, "channel.status",
                           {"type": type_, "state": "logged_out", "session_id": str(rows[0].id) if rows else None,
                            "account_label": rows[0].account_label if rows else None, "scanned": False,
                            "error": None}, org_id=user.org_id)
    return Response(status_code=204)


class ChannelPatch(BaseModel):
    """Tin riêng 1-1 của kênh: `direct_mode` (off | silent | reply). `listen_direct` là khoá cũ của web ≤ 0.1.64:
    true ⇒ reply (như trước: agent trực được trả lời), false ⇒ off. F1d: `new_group_mode` (ask | silent | off; mặc định
    silent — QD 17/10) = chế độ cho nhóm MỚI phát hiện sau này (không đổi nhóm đã có). Đổi gì cũng cần mã PIN
    (`policy.change`)."""

    listen_direct: bool | None = None
    direct_mode: Literal["off", "silent", "reply"] | None = None
    new_group_mode: Literal["ask", "silent", "off"] | None = None


@router.patch("/channels/{type_}")
async def channel_patch(type_: str, body: ChannelPatch, request: Request, user: service.CurrentUser = Depends(MANAGE),
                        _pin: Any = Depends(require_pin("policy.change")),
                        db: AsyncSession = DB) -> dict[str, Any]:
    await _qr_channel(db, user.org_id, type_)
    if body.direct_mode is None and body.listen_direct is None and body.new_group_mode is None:
        raise field_errors({"direct_mode": "Chọn một trong: off, silent, reply",
                            "new_group_mode": "Hoặc chọn nhóm mới: ask, silent, off"})
    if body.direct_mode is not None or body.listen_direct is not None:
        mode = body.direct_mode or ("reply" if body.listen_direct else "off")
        assert mode in DIRECT_MODES
        before = direct_mode(await org_settings(db, user.org_id), type_)
        await set_channel_setting(db, user.org_id, "direct_mode", type_, mode)
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="channel.listen_direct_changed", target_type="channel", target_id=type_,
                               detail={"direct_mode": mode, "from": before, "listen_direct": mode != "off"},
                               ip=user.ip)
    if body.new_group_mode is not None:
        await _save_new_group_mode(db, user, type_, body.new_group_mode)
    await sync_listen_sets(db, request.app.state.redis, user.org_id)
    return await channel_card(db, request.app.state.redis, user.org_id, type_)


async def _save_new_group_mode(db: AsyncSession, user: service.CurrentUser, type_: str, mode: str) -> None:
    """Lưu `new_group_mode` của kênh (+ đánh dấu «Sếp đã chọn kể từ lần nối kênh này») và ghi Nhật ký hành động."""
    assert mode in NEW_GROUP_MODES
    before = new_group_mode(await org_settings(db, user.org_id), type_)
    await set_channel_setting(db, user.org_id, "new_group_mode", type_, mode)
    await set_channel_setting(db, user.org_id, "new_group_asked", type_, True)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="channel.new_group_mode_changed", target_type="channel", target_id=type_,
                           detail={"new_group_mode": mode, "from": before}, ip=user.ip)


def _group_out(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "name": r.name, "members": r.member_count, "kind": r.kind,
            "listen_mode": r.listen_mode, "view_scope": r.view_scope, "msgs_24h": r.msgs,
            "channel": r.channel_type, "left": r.left_at is not None}


GROUP_SELECT = """
SELECT g.id, g.code, g.name, g.member_count, g.kind, g.listen_mode, g.view_scope, g.left_at, c.type AS channel_type,
       (SELECT count(*) FROM raw.events e
         WHERE e.group_id = g.id AND e.received_at > now() - interval '24 hours') AS msgs
FROM core.groups g JOIN core.channels c ON c.id = g.channel_id
"""


@router.get("/channels/{type_}/groups")
async def channel_groups(type_: str, pending: int = Query(default=0, ge=0, le=1),
                         user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> list[dict[str, Any]]:
    """Nhóm của kênh (nhóm đã rời xếp cuối, có `left: true`). `?pending=1` = chỉ nhóm mới đang CHỜ Sếp chọn."""
    only = " AND g.listen_mode = 'pending' AND g.left_at IS NULL" if pending else ""
    only += f" AND {group_row_view_sql(user.role_code, 'g')}"      # «Quyền xem» của nhóm (v0.1.71)
    rows = (await db.execute(text(GROUP_SELECT + " WHERE c.org_id = :o AND c.type = :t" + only
                                  + " ORDER BY (g.left_at IS NOT NULL), g.name"),  # noqa: S608 — `only` là hằng
                             {"o": user.org_id, "t": type_})).all()
    return [_group_out(r) for r in rows]


@router.post("/channels/{type_}/groups/refresh", status_code=202)
async def channel_groups_refresh(type_: str, request: Request, user: service.CurrentUser = Depends(MANAGE),
                                 db: AsyncSession = DB) -> dict[str, Any]:
    """v0.1.65 (F056) "Cập nhật danh sách nhóm": bảo bridge quét lại danh bạ ngay (`directory.sync`) thay vì chờ
    chu kỳ 2 giờ. Giãn cách 30 giây mỗi kênh để không dồn dập lên tài khoản cá nhân của Sếp. Không đổi quyền truy cập
    nên không đòi PIN."""
    ch = await _qr_channel(db, user.org_id, type_)
    redis = request.app.state.redis
    if not await redis.get("gh:bridge:heartbeat"):
        raise _bridge_offline()
    sid = (await db.execute(text("""SELECT id FROM core.channel_sessions WHERE channel_id = :c AND state = 'active'
                                    AND ended_at IS NULL ORDER BY started_at DESC NULLS LAST LIMIT 1"""),
                            {"c": ch.id})).scalar_one_or_none()
    if sid is None:
        raise conflict("CHANNEL_NOT_ACTIVE", "Kênh chưa đăng nhập — đăng nhập xong mới cập nhật được danh sách nhóm")
    if not await redis.set(f"gh:groups:refresh:{user.org_id}:{type_}", "1", nx=True, ex=REFRESH_GAP_S):
        raise ApiError(429, "REFRESH_TOO_SOON", "Vừa cập nhật danh sách nhóm xong — chờ vài giây rồi thử lại")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="channel.groups_refresh", target_type="channel", target_id=type_,
                           detail={"session_id": str(sid)}, ip=user.ip)
    await db.commit()
    await request.app.state.bus.publish(BRIDGE_CONTROL, "directory.sync", {"channel": type_, "session_id": str(sid)},
                                        actor=user.actor_id, org_id=user.org_id)
    return {"session_id": str(sid), "requested": True}


@router.post("/channels/{type_}/groups/seen")
async def channel_groups_seen(type_: str, request: Request, user: service.CurrentUser = Depends(MANAGE),
                              db: AsyncSession = DB) -> dict[str, Any]:
    """QD 17/10: Sếp bấm «Đã biết» ở khối «N nhóm mới đã vào Lắng nghe im lặng» ⇒ đặt lại bộ đếm của kênh về 0.
    Không đổi chế độ nhóm nào, không đổi quyền truy cập nên không đòi PIN. Trả thẻ kênh."""
    await _qr_channel(db, user.org_id, type_)
    await set_channel_setting(db, user.org_id, NEW_SILENT_KEY, type_, 0)
    return await channel_card(db, request.app.state.redis, user.org_id, type_)


class GroupModeIn(BaseModel):
    mode: Literal["off", "tagged_only", "silent", "proactive", "paused"]
    # F1d — ô «Áp dụng cho các nhóm mới sau này» của hộp hỏi: chỉ với Im lặng / Không nghe, lưu `new_group_mode`
    # của kênh cùng lúc. Đổi chính sách cho nhóm tương lai nên cần mã PIN như đổi ở thẻ kênh. None/False = chỉ nhóm này.
    apply_to_new: bool = False


@router.patch("/channels/{type_}/groups/{gid}/mode")
async def channel_group_mode(type_: str, gid: uuid.UUID, body: GroupModeIn, request: Request,
                             user: service.CurrentUser = Depends(MANAGE), db: AsyncSession = DB) -> dict[str, Any]:
    """v0.1.65: Sếp chọn chế độ cho một nhóm (hộp «Nhóm mới»). `pending` không phải lựa chọn. `apply_to_new` (F1d):
    đồng thời đặt chế độ cho nhóm mới sau này (`silent`/`off`; 422 với chế độ khác, 423 PIN_REQUIRED khi chưa có PIN —
    không đổi gì cả khi lỗi). Có chọn ở hộp hỏi (dù có tick hay không) ⇒ `new_group_asked` = true."""
    ok = (await db.execute(text("""SELECT 1 FROM core.groups g JOIN core.channels c ON c.id = g.channel_id
                                   WHERE g.id = :i AND c.type = :t AND c.org_id = :o"""),
                           {"i": gid, "t": type_, "o": user.org_id})).first()
    if ok is None:
        raise not_found("Nhóm")
    if body.apply_to_new:
        if body.mode not in ("silent", "off"):
            raise field_errors({"apply_to_new": "Chỉ áp dụng cho nhóm mới được với Im lặng hoặc Không nghe"})
        if not user.pin_active():
            raise pin_required()
        await _save_new_group_mode(db, user, type_, body.mode)
        # cờ «nhóm mới» của bridge, làm mới cả khi nhóm này không đổi chế độ
        await sync_listen_sets(db, request.app.state.redis, user.org_id)
    else:
        await set_channel_setting(db, user.org_id, "new_group_asked", type_, True)
    return await update_group(db, request.app.state.redis, user, gid, GroupPatch(listen_mode=body.mode))


class GroupPatch(BaseModel):
    # "pending" chỉ được NHẬN LẠI nguyên trạng (web cũ gửi lại chế độ hiện tại của mọi nhóm ở bước 6); đổi một nhóm đã
    # chọn về "pending" bị từ chối trong `update_group`. Khớp `ALL_LISTEN_MODES` (có test).
    listen_mode: Literal["pending", "off", "tagged_only", "silent", "proactive", "paused"] | None = None
    view_scope: Literal["owner", "manager", "all_members"] | None = None
    kind: Literal["internal", "market", "partner", "customer", "private"] | None = None


async def update_group(db: AsyncSession, redis: Any, user: service.CurrentUser, gid: uuid.UUID,
                       body: GroupPatch) -> dict[str, Any]:
    cur = (await db.execute(text(GROUP_SELECT + " WHERE g.id = :i AND g.org_id = :o"),
                            {"i": gid, "o": user.org_id})).one_or_none()
    if cur is None:
        raise not_found("Nhóm")
    changes = {k: v for k, v in body.model_dump().items() if v is not None and getattr(cur, k) != v}
    if changes.get("listen_mode") == "pending":
        raise field_errors({"listen_mode": "Không đặt lại nhóm về «Chờ Sếp chọn» — hãy chọn Lắng nghe im lặng, "
                                           "Không nghe hoặc chế độ khác"})
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        await db.execute(text(f"UPDATE core.groups SET {sets} WHERE id = :i"), {**changes, "i": gid})  # noqa: S608
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="group.updated", target_type="group", target_id=cur.code, target_label=cur.name,
                               detail={"from": {k: getattr(cur, k) for k in changes}, "to": changes}, ip=user.ip)
        if "listen_mode" in changes:
            if cur.listen_mode == "silent":
                # Sếp đã xử lý một nhóm đang «Lắng nghe im lặng» ⇒ thẻ kênh bớt một nhóm «mới» (v0.1.71).
                await drop_channel_counter(db, user.org_id, NEW_SILENT_KEY, cur.channel_type)
            await sync_listen_sets(db, redis, user.org_id)
            await publish_header(db, redis, user.org_id)
    r = (await db.execute(text(GROUP_SELECT + " WHERE g.id = :i"), {"i": gid})).one()
    return _group_out(r)


@router.patch("/groups/{gid}")
async def patch_group(gid: uuid.UUID, body: GroupPatch, request: Request, user: service.CurrentUser = Depends(MANAGE),
                      db: AsyncSession = DB) -> dict[str, Any]:
    return await update_group(db, request.app.state.redis, user, gid, body)


# ─── Nhà cung cấp & khoá ───────────────────────────────────────────────────

# `system_one` (v0.1.21) = Jev của TypeSafe — bộ quyết định nhanh cho Gen (gh.gen.jev), KHÔNG vào chuỗi sinh chữ.
# `claude_code_cli` (v0.1.31) = Claude Code CLI bằng gói Claude của Owner (QD-12: Owner tự quyết rủi ro điều khoản).
PROVIDER_KINDS = ("antigravity_cli", "claude_code_cli", "gemini", "deepseek", "openai_compat", "system_one")
CLI_KINDS = climod.CLI_KINDS
CliKind = Literal["antigravity_cli", "claude_code_cli"]
KEY_PREFIX = {"gemini": "GEM", "deepseek": "DS", "openai_compat": "API", "system_one": "JEV"}


class ProviderIn(BaseModel):
    kind: Literal["antigravity_cli", "claude_code_cli", "gemini", "deepseek", "openai_compat", "system_one"]
    name: str = Field(min_length=1, max_length=80)
    endpoint: str | None = Field(default=None, max_length=300)
    keys: list[str] = Field(default_factory=list, max_length=20)
    models: list[str] = Field(default_factory=list, max_length=20)


class KeyIn(BaseModel):
    secret: str = Field(min_length=8, max_length=500)


class ProviderPatch(BaseModel):
    enabled: bool | None = None
    failover_rank: int | None = Field(default=None, ge=1, le=99)


class ModelIn(BaseModel):
    model_name: str = Field(min_length=1, max_length=120)
    daily_quota: int | None = Field(default=None, ge=1)
    rate_limit_per_min: int | None = Field(default=None, ge=1)
    # v0.1.31: "Dùng model này" → model mặc định của nguồn. Nguồn CLI: model MỚI được gọi thử thật trước khi lưu.
    make_default: bool = False
    # v0.1.32: mức suy nghĩ tách khỏi tên model (agy `--effort low|medium|high`; claude thêm xhigh, max).
    # None = để CLI tự chọn (không gửi --effort).
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    # Review v0.1.66 (P2-2): "Tắt model" / "Bật model" — ngừng dùng mà giữ lịch sử chi phí.
    # Không gửi ⇒ giữ trạng thái đang có.
    enabled: bool | None = None


async def _add_key(db: AsyncSession, provider_id: uuid.UUID, kind: str, secret: str) -> None:
    n = (await db.execute(text("SELECT count(*) FROM agent.provider_keys WHERE provider_id = :p"),
                          {"p": provider_id})).scalar_one()
    label = f"{KEY_PREFIX.get(kind, 'KEY')}-KEY-{n + 1:02d}"
    await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4, rotation_order)
                             VALUES (:p, :l, :s, :f, :r)"""),
                     {"p": provider_id, "l": label, "s": crypto.encrypt(secret.strip().encode(), KEY_AAD),
                      "f": secret.strip()[-4:], "r": n})


async def provider_payloads(db: AsyncSession, redis: Any, org_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""SELECT * FROM agent.providers WHERE org_id = :o
                                     ORDER BY failover_rank NULLS LAST, created_at"""), {"o": org_id})).all()
    out = []
    for p in rows:
        keys = (await db.execute(text("""SELECT id, label, last4, is_enabled FROM agent.provider_keys
                                         WHERE provider_id = :p ORDER BY rotation_order"""), {"p": p.id})).all()
        models = (await db.execute(text("""SELECT id, model_name, effort, daily_quota, rate_limit_per_min,
                                                  is_enabled, is_default
                                           FROM agent.models WHERE provider_id = :p ORDER BY id"""),
                                   {"p": p.id})).all()
        key_out = []
        for k in keys:
            ttl = await redis.ttl(cooldown_key(k.id))
            key_out.append({"id": str(k.id), "label": k.label, "last4": k.last4, "enabled": k.is_enabled,
                            "cooldown_until": iso(datetime.fromtimestamp(datetime.now(UTC).timestamp() + ttl, UTC))
                            if ttl and ttl > 0 else None, "quota_left_pct": None})
        model_out = []
        for m in models:
            used = int(await redis.get(quota_key(m.id)) or 0)
            model_out.append({"id": str(m.id), "model_name": m.model_name, "effort": m.effort,
                              "daily_quota": m.daily_quota,
                              "rate_limit_per_min": m.rate_limit_per_min, "enabled": m.is_enabled,
                              "is_default": m.is_default,
                              "used_today": used,
                              "left_pct": (round(max(0, 100 - used * 100 / m.daily_quota), 1)
                                           if m.daily_quota else None)})
        out.append({"id": str(p.id), "kind": p.kind, "name": p.name, "endpoint": p.endpoint,
                    "failover_rank": p.failover_rank, "enabled": p.is_enabled, "auth_state": p.auth_state,
                    "account_label": p.account_label, "last_test": p.last_test, "keys": key_out, "models": model_out})
    return out


async def _check_provider_endpoint(endpoint: str | None, *, has_key: bool) -> None:
    """v0.1.45 (F-49): kiểm địa chỉ nhà cung cấp AI lúc GHI cấu hình. Rỗng ⇒ dùng mặc định https của
    gemini/deepseek. Phân giải tên một lần qua `mcp_client.pin_endpoint` (cho phép mạng công cộng) với CÙNG quy tắc
    như máy chủ MCP / Gen-hub: có khoá + `http://` tới IP công cộng ⇒ 422 (khoá đi rõ trên Internet); `http://` trong
    mạng nội bộ / cùng máy (Ollama, LM Studio, vLLM ở 192.168.x, 10.x, localhost) vẫn được. Vùng cấm (169.254.x
    siêu dữ liệu đám mây, 0.0.0.0, dịch vụ nội bộ) ⇒ 422; không phân giải được lúc này ⇒ cho qua (kiểm lại lúc gọi)."""
    if not endpoint or not endpoint.strip():
        return
    endpoint = endpoint.strip()
    scheme = endpoint.split("://", 1)[0].lower() if "://" in endpoint else ""
    if scheme not in ("http", "https"):
        raise field_errors({"endpoint": "Địa chỉ phải bắt đầu bằng https:// (hoặc http:// với máy trong mạng nội bộ)"})
    try:
        await pin_endpoint(endpoint, True, has_token=has_key)
    except McpBlockedNetwork as e:
        if "không hợp lệ" in str(e):
            raise field_errors({"endpoint": "Địa chỉ không hợp lệ — dạng https://<máy chủ>/v1 (hoặc http:// với máy "
                                            "trong mạng nội bộ)"}) from e
        if "https://" in str(e):
            raise field_errors({"endpoint": "Có khoá API mà máy chủ ở mạng công cộng thì địa chỉ phải là https:// "
                                            "(http:// chỉ dùng được với máy trong mạng nội bộ)"}) from e
        raise field_errors({"endpoint": "Địa chỉ trỏ vào vùng mạng bị cấm (siêu dữ liệu đám mây 169.254.x, 0.0.0.0, "
                                        "dịch vụ nội bộ)"}) from e
    except McpError:
        return


async def _provider(db: AsyncSession, org_id: uuid.UUID, pid: uuid.UUID) -> Any:
    r = (await db.execute(text("SELECT * FROM agent.providers WHERE id = :i AND org_id = :o"),
                          {"i": pid, "o": org_id})).one_or_none()
    if r is None:
        raise not_found("Nhà cung cấp")
    return r


async def _one_provider(db: AsyncSession, redis: Any, org_id: uuid.UUID, pid: uuid.UUID) -> dict[str, Any]:
    return next(p for p in await provider_payloads(db, redis, org_id) if p["id"] == str(pid))


@router.get("/providers")
async def providers(request: Request, user: service.CurrentUser = Depends(READ),
                    db: AsyncSession = DB) -> list[dict[str, Any]]:
    return await provider_payloads(db, request.app.state.redis, user.org_id)


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderIn, request: Request, user: service.CurrentUser = Depends(MANAGE),
                          _pin: Any = Depends(require_pin("ai.route_change")),
                          db: AsyncSession = DB) -> dict[str, Any]:
    """v0.1.35 (F-20): thêm / đổi tên nhà cung cấp AI (kể cả nhánh CLI) cần phiên PIN `ai.route_change` — kiểm SAU
    quyền (vai trò thiếu quyền nhận 403 trước 423). Phạm vi PIN đợt này chỉ gồm 4 route ghi chuỗi chuyển hướng:
    POST /providers, PATCH /providers/chain, PATCH /providers/{id}, POST /providers/{id}/keys. KHÔNG đòi PIN: GET,
    DELETE nhà cung cấp/khoá (chỉ thu hẹp đường đi), /test, /diagnose, /models.
    v0.1.45 (F-49): endpoint nhà cung cấp (không phải CLI) được kiểm lúc GHI — xem `_check_provider_endpoint`."""
    if body.kind == "openai_compat" and not body.endpoint:
        raise field_errors({"endpoint": "Cần endpoint cho API tương thích OpenAI"})
    if body.kind not in CLI_KINDS and not body.keys:
        raise field_errors({"keys": "Cần ít nhất một khoá API"})
    if body.kind == "system_one":
        body.endpoint = (body.endpoint or jev.DEFAULT_BASE_URL).rstrip("/")
        if not body.endpoint.startswith("https://"):
            raise field_errors({"endpoint": "Địa chỉ Jev phải bắt đầu bằng https://"})
        body.models = body.models or [jev.DEFAULT_MODEL]
    if body.kind not in CLI_KINDS:
        await _check_provider_endpoint(body.endpoint, has_key=bool(body.keys))
    if body.kind in CLI_KINDS:
        pid = await climod.cli_provider_id(db, user.org_id, body.kind)
        await db.execute(text("UPDATE agent.providers SET name = :n WHERE id = :i"), {"n": body.name, "i": pid})
    else:
        rank = (await db.execute(text("""SELECT COALESCE(max(failover_rank), 0) + 1 FROM agent.providers
                                         WHERE org_id = :o"""), {"o": user.org_id})).scalar_one()
        pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                        VALUES (:o, :k, :n, :e, :r) RETURNING id"""),
                                {"o": user.org_id, "k": body.kind, "n": body.name, "e": body.endpoint,
                                 "r": rank})).scalar_one()
        for k in body.keys:
            await _add_key(db, pid, body.kind, k)
    for m in body.models:
        await db.execute(text("""INSERT INTO agent.models (provider_id, model_name) VALUES (:p, :m)
                                 ON CONFLICT DO NOTHING"""), {"p": pid, "m": m.strip()})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.created", target_type="provider", target_id=str(pid),
                           target_label=body.name, detail={"kind": body.kind, "keys": len(body.keys),
                                                           "models": body.models}, ip=user.ip)
    out = await _one_provider(db, request.app.state.redis, user.org_id, pid)
    # v0.1.59: nguồn mới có thể kèm khoá + model ⇒ thẻ "Hôm nay của Sếp" (Gen chưa có model…) đọc lại ngay.
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)
    return out


# ─── v0.1.41 (F-86): Nguồn AI cho việc nền ───────────────────────────────────
# ĐĂNG KÝ TRƯỚC các route `/providers/{pid}` (xem chú thích `patch_chain`).

async def _background_payload(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Mọi giá trị là chuỗi/bool/null hoặc danh sách của chúng (web không render object)."""
    ai = (await org_settings(db, org_id)).get("ai") or {}
    allow = sorted(await mrouter.background_cli_allowed(db, org_id))
    accepted = ai.get("background_cli_accepted_at") if allow else None
    return {"allow_cli": allow, "accepted_at": str(accepted) if accepted else None,
            "risk_text": mrouter.BACKGROUND_CLI_RISK, "purposes": list(mrouter.BACKGROUND_PURPOSE_LABELS),
            "has_api_source": await mrouter.has_api_source(db, org_id),
            "has_agy_source": await mrouter.has_agy_source(db, org_id),
            "sources": await mrouter.background_sources(db, org_id)}


@router.get("/providers/background")
async def get_background(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    """Nguồn AI cho việc nền (sàng lọc tin, trực việc, Bản tin Gen): khoá API và Antigravity CLI; Claude Code CLI chỉ
    khi Owner cho phép."""
    return await _background_payload(db, user.org_id)


class BackgroundIn(BaseModel):
    allow_cli: list[Literal["claude_code_cli", "antigravity_cli"]] = Field(default_factory=list, max_length=2)
    accept_risk: bool = False


@router.put("/providers/background")
async def put_background(body: BackgroundIn, user: service.CurrentUser = Depends(require_owner),
                         db: AsyncSession = DB) -> dict[str, Any]:
    """Cho / thôi cho Claude Code CLI chạy việc nền — quyền và rủi ro của Owner (QD-12, điều khoản gói Claude).

    Thứ tự kiểm: Owner (403) → agy không cần cho phép (422 errors.allow_cli — v0.1.62: Antigravity CLI luôn chạy được
    việc nền, nên không có công tắc) → THÊM CLI cần phiên PIN `ai.background_cli` (423, kiểm trong hàm — bỏ CLI không
    cần PIN) rồi tích xác nhận (422 errors.accept_risk). Thu hẹp (bỏ CLI) không cần PIN/xác nhận."""
    if "antigravity_cli" in body.allow_cli:
        raise field_errors({"allow_cli": "Antigravity CLI luôn chạy được việc nền — không cần cho phép (chỉ Claude "
                                         "Code CLI cần Sếp cho phép)"})
    new = sorted(set(body.allow_cli))
    current = await mrouter.background_cli_allowed(db, user.org_id)
    adding = set(new) - current
    if adding:
        if not user.pin_active():
            raise pin_required()
        if not body.accept_risk:
            raise field_errors({"accept_risk": "Cần tích “Tôi đã đọc cảnh báo và tự chịu rủi ro”"})
    patch: dict[str, Any] = {"background_cli": new}
    if adding:
        patch |= {"background_cli_accepted_at": datetime.now(UTC).isoformat(),
                  "background_cli_accepted_by": str(user.id)}
    await db.execute(text("""
        UPDATE core.organizations
        SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{ai}',
                                 COALESCE(settings->'ai', '{}'::jsonb) || CAST(:p AS jsonb), true)
        WHERE id = :o"""), {"o": user.org_id, "p": orjson.dumps(patch).decode()})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="ai.background_cli_changed", target_type="organization",
                           target_id=str(user.org_id),
                           detail={"allow_cli": new, "accept_risk": body.accept_risk}, ip=user.ip)
    return await _background_payload(db, user.org_id)


class ChainIn(BaseModel):
    provider_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


@router.patch("/providers/chain")
async def patch_chain(body: ChainIn, request: Request, user: service.CurrentUser = Depends(MANAGE),
                      _pin: Any = Depends(require_pin("ai.route_change")),
                      db: AsyncSession = DB) -> list[dict[str, Any]]:
    """Kéo-thả sắp lại toàn bộ chuỗi chuyển hướng một lượt (thiết kế `[providers]`) — khác `PATCH /providers/{id}`
    vốn chỉ đổi một ô; ở đây backend chỉ nhận thứ tự mới và ghi lại `failover_rank` theo đúng thứ tự đó.

    ĐĂNG KÝ TRƯỚC `PATCH /providers/{pid}` bên dưới — nếu không, Starlette so khớp `/providers/chain` vào mẫu
    `/providers/{pid}` (nhánh có sẵn từ giai đoạn 1/2) và "chain" bị parse nhầm thành UUID (422)."""
    ids = list(dict.fromkeys(body.provider_ids))
    have = set((await db.execute(text("SELECT id FROM agent.providers WHERE org_id = :o"),
                                 {"o": user.org_id})).scalars().all())
    if set(ids) != have:
        raise field_errors({"provider_ids": "Cần đúng và đủ danh sách nhà cung cấp hiện có, không thiếu không thừa"})
    for rank, pid in enumerate(ids, start=1):
        await db.execute(text("UPDATE agent.providers SET failover_rank = :r WHERE id = :i"), {"r": rank, "i": pid})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.chain_reordered", target_type="provider",
                           detail={"order": [str(i) for i in ids]}, ip=user.ip)
    return await provider_payloads(db, request.app.state.redis, user.org_id)


@router.patch("/providers/{pid}")
async def patch_provider(pid: uuid.UUID, body: ProviderPatch, request: Request,
                         user: service.CurrentUser = Depends(MANAGE),
                         _pin: Any = Depends(require_pin("ai.route_change")),
                         db: AsyncSession = DB) -> dict[str, Any]:
    """Bật/tắt hoặc đổi `failover_rank` — cần PIN `ai.route_change` (v0.1.35, F-20; xem `create_provider`)."""
    p = await _provider(db, user.org_id, pid)
    if body.enabled is not None:
        await db.execute(text("UPDATE agent.providers SET is_enabled = :e WHERE id = :i"),
                         {"e": body.enabled, "i": pid})
    if body.failover_rank is not None and body.failover_rank != p.failover_rank:
        # Đổi chỗ trong chuỗi: đẩy các nhà cung cấp khác lùi một bậc.
        await db.execute(text("""UPDATE agent.providers SET failover_rank = failover_rank + 1
                                 WHERE org_id = :o AND id <> :i AND failover_rank >= :r"""),
                         {"o": user.org_id, "i": pid, "r": body.failover_rank})
        await db.execute(text("UPDATE agent.providers SET failover_rank = :r WHERE id = :i"),
                         {"r": body.failover_rank, "i": pid})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.updated", target_type="provider", target_id=str(pid), target_label=p.name,
                           detail=body.model_dump(exclude_none=True), ip=user.ip)
    out = await _one_provider(db, request.app.state.redis, user.org_id, pid)
    # v0.1.59: bật / tắt nguồn đổi "có nguồn AI chạy được không" ⇒ thẻ "Hôm nay của Sếp" đọc lại ngay.
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)
    return out


@router.delete("/providers/{pid}", status_code=204)
async def delete_provider(pid: uuid.UUID, request: Request, user: service.CurrentUser = Depends(MANAGE),
                          _pin: Any = Depends(require_pin("ai.route_change")),
                          db: AsyncSession = DB) -> Response:
    """v0.1.28 (UX N1): xoá một nguồn model nhập nhầm / gọi thử lỗi (bước 4 và màn API & Model). Gỡ luôn gán model của
    các agent đang trỏ vào model của nguồn này (agent đó quay về dùng chuỗi chung). Antigravity CLI dùng phiên đăng
    nhập — gỡ ở thẻ tài khoản CLI, không xoá ở đây.

    Review v0.1.68 (P3): cần PIN `ai.route_change` như thêm / sửa nhà cung cấp — gỡ nguồn của Sếp cũng đổi chuỗi nguồn
    AI (nguồn lạ lên đứng đầu, agent bị gỡ gán model rơi về chuỗi chung). Thứ tự kiểm: quyền (403) → PIN (423) → 404."""
    p = await _provider(db, user.org_id, pid)
    if p.kind in CLI_KINDS:
        name = climod.spec(p.kind).name
        raise conflict("CLI_PROVIDER", f"{name} gỡ bằng cách xoá tài khoản ở thẻ Tài khoản {name}")
    await db.execute(text("""DELETE FROM agent.bindings WHERE org_id = :o
                             AND model_id IN (SELECT id FROM agent.models WHERE provider_id = :p)"""),
                     {"o": user.org_id, "p": pid})
    await db.execute(text("DELETE FROM agent.models WHERE provider_id = :p"), {"p": pid})
    await db.execute(text("DELETE FROM agent.providers WHERE id = :p"), {"p": pid})
    await health.clear(db, user.org_id, f"model.auth_expired:{pid}", redis=request.app.state.redis)  # v0.1.36 (F-6b)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.deleted", target_type="provider", target_id=str(pid),
                           target_label=p.name, detail={"kind": p.kind}, ip=user.ip)
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)   # v0.1.59: thẻ đọc lại ngay
    return Response(status_code=204)


@router.post("/providers/{pid}/keys", status_code=201)
async def add_key(pid: uuid.UUID, body: KeyIn, request: Request, user: service.CurrentUser = Depends(MANAGE),
                  _pin: Any = Depends(require_pin("ai.route_change")),
                  db: AsyncSession = DB) -> dict[str, Any]:
    """Thêm khoá API — cần PIN `ai.route_change` (v0.1.35, F-20; xem `create_provider`)."""
    p = await _provider(db, user.org_id, pid)
    if p.kind in CLI_KINDS:
        raise conflict("CLI_NO_KEYS", f"{climod.spec(p.kind).name} dùng phiên đăng nhập, không dùng khoá API")
    await _check_provider_endpoint(p.endpoint, has_key=True)   # F-49: khoá chỉ gắn vào địa chỉ https hợp lệ
    await _add_key(db, pid, p.kind, body.secret)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.key_added",
                           target_type="provider", target_id=str(pid), target_label=p.name,
                           detail={"last4": body.secret.strip()[-4:]}, ip=user.ip)
    out = await _one_provider(db, request.app.state.redis, user.org_id, pid)
    # v0.1.59: khoá API đầu tiên biến nguồn thành "chạy được" ⇒ thẻ "Hôm nay của Sếp" đọc lại ngay.
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)
    return out


@router.delete("/providers/{pid}/keys/{kid}", status_code=204)
async def delete_key(pid: uuid.UUID, kid: uuid.UUID, request: Request, user: service.CurrentUser = Depends(MANAGE),
                     _pin: Any = Depends(require_pin("ai.route_change")),
                     db: AsyncSession = DB) -> Response:
    """Xoá khoá API của một nguồn — cần PIN `ai.route_change` (review v0.1.68 P3; xem `delete_provider`)."""
    p = await _provider(db, user.org_id, pid)
    row = (await db.execute(text("DELETE FROM agent.provider_keys WHERE id = :k AND provider_id = :p RETURNING label"),
                            {"k": kid, "p": pid})).one_or_none()
    if row is None:
        raise not_found("Khoá")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.key_deleted", target_type="provider", target_id=str(pid),
                           target_label=p.name, detail={"key": row.label}, ip=user.ip)
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)   # v0.1.59: thẻ đọc lại ngay
    return Response(status_code=204)


# Review v0.1.31: gọi thử model CLI là lượt gọi THẬT (tốn hạn mức gói) → giới hạn số lượt / tổ chức; tên model CLI chỉ
# gồm ký tự an toàn (không bắt đầu bằng "-" — không bao giờ bị CLI hiểu thành một cờ).
PROBE_LIMIT, PROBE_WINDOW_S = 12, 600
CLI_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,119}")


async def _probe_budget(redis: Any, org_id: uuid.UUID) -> None:
    k = f"gh:cli-probe:{org_id}"
    n = int(await redis.incr(k))
    if n == 1:
        await redis.expire(k, PROBE_WINDOW_S)
    if n > PROBE_LIMIT:
        raise ApiError(429, "PROBE_RATE_LIMITED", "Gọi thử quá nhiều lần — đợi vài phút rồi thử lại",
                       "Mỗi lần gọi thử dùng hạn mức thật của gói CLI")


@router.post("/providers/{pid}/models", status_code=201)
async def add_model(pid: uuid.UUID, body: ModelIn, request: Request, user: service.CurrentUser = Depends(MANAGE),
                    _pin: Any = Depends(require_pin("ai.route_change")),
                    db: AsyncSession = DB) -> dict[str, Any]:
    """Thêm / sửa / tắt-bật / chọn mặc định một model của nguồn — cần PIN `ai.route_change` (dọn sót v0.1.70, như
    `delete_provider`): "Dùng model này" và Tắt model đổi model nào Gen gọi, tức đổi chuỗi nguồn AI. Thứ tự kiểm:
    quyền (403) → PIN (423) → 404 / 422."""
    p = await _provider(db, user.org_id, pid)
    # v0.1.32: tên biến thể cũ của agy ("gemini-3.8-flash-high") → model gốc + mức suy nghĩ.
    name, var_effort = catalog.split_variant(p.kind, body.model_name.strip())
    prev = (await db.execute(text("""SELECT effort, daily_quota, rate_limit_per_min FROM agent.models
                                     WHERE provider_id = :p AND model_name = :m"""),
                             {"p": pid, "m": name})).one_or_none()
    # Không gửi `effort` (vd chỉ sửa hạn mức) → giữ mức đã lưu.
    effort = body.effort if "effort" in body.model_fields_set else (var_effort or (prev.effort if prev else None))
    effort = effort or var_effort
    # v0.1.66 (F009): hạn mức nay sửa được trên giao diện ⇒ KHÔNG gửi trường nào (vd "Dùng model này") thì giữ hạn
    # mức đã lưu; gửi null = bỏ hạn mức.
    daily_quota = (body.daily_quota if "daily_quota" in body.model_fields_set
                   else (prev.daily_quota if prev else None))
    rate_limit = (body.rate_limit_per_min if "rate_limit_per_min" in body.model_fields_set
                  else (prev.rate_limit_per_min if prev else None))
    if p.kind == "antigravity_cli" and not AGY_MODEL_RE.fullmatch(name):
        # F-22: đúng regex AgyClient dùng cho `--model=<tên>` (không bao giờ thành một cờ).
        raise field_errors({"model_name": "Tên model chỉ gồm chữ, số và . _ : - (tối đa 80 ký tự)"})
    if p.kind in CLI_KINDS and not CLI_MODEL_RE.fullmatch(name):
        raise field_errors({"model_name": "Tên model chỉ gồm chữ, số và . _ - : / [ ]"})
    if effort and effort not in catalog.valid_efforts(p.kind):
        raise field_errors({"effort": f"{p.name} không có mức suy nghĩ “{effort}”"})
    # Review v0.1.32: theo TỪNG model khi nguồn chính thức nói rõ (Claude Code: haiku không có mức suy nghĩ — claude
    # bỏ qua `--effort`, lưu vào chỉ gây hiểu nhầm). Model lạ (CLI liệt kê lúc chạy) → để lượt gọi thử quyết định.
    known = catalog.known_efforts(p.kind, name)
    if effort and known is not None and effort not in known:
        raise field_errors({"effort": f"Model “{name}” không chỉnh được mức suy nghĩ “{effort}”"
                                      + (f" (chỉ: {', '.join(known)})" if known else "")})
    if p.kind in CLI_KINDS and (prev is None or (prev.effort or None) != effort):
        await _probe_budget(request.app.state.redis, user.org_id)
        # v0.1.31: danh sách model của CLI có thể là danh mục dự phòng → gọi thử THẬT (đúng cờ --model/--effort) trước
        # khi lưu; CLI từ chối thì không lưu (không bao giờ ghi một mã model mà CLI không nhận).
        await db.commit()
        probe = await request.app.state.model_router.probe_model(pid, name, effort)
        if not probe["ok"]:
            await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                                   action="provider.model_rejected", target_type="provider", target_id=str(pid),
                                   target_label=p.name, result="failed",
                                   detail={"model": name, "effort": effort, "error": probe["error"]}, ip=user.ip)
            await db.commit()
            raise ApiError(422, "VALIDATION", "Dữ liệu chưa hợp lệ",
                           errors={"model_name": probe["error"]}, technical=probe.get("error_detail"),
                           available=probe.get("available") or [])
    await db.execute(text("""
        INSERT INTO agent.models (provider_id, model_name, effort, daily_quota, rate_limit_per_min, is_enabled)
        VALUES (:p, :m, :e, :q, :r, COALESCE(CAST(:en AS boolean), true))
        ON CONFLICT (provider_id, model_name) DO UPDATE
          SET effort = :e, daily_quota = :q, rate_limit_per_min = :r,
              is_enabled = COALESCE(CAST(:en AS boolean), agent.models.is_enabled)"""),
        {"p": pid, "m": name, "e": effort, "q": daily_quota, "r": rate_limit, "en": body.enabled})
    if body.enabled is False:
        # Model đã tắt không thể là model Sếp đã chốt ("Dùng model này") — chuỗi bỏ qua model tắt.
        await db.execute(text("UPDATE agent.models SET is_default = false WHERE provider_id = :p AND model_name = :m"),
                         {"p": pid, "m": name})
    if body.make_default:
        await db.execute(text("UPDATE agent.models SET is_default = false WHERE provider_id = :p AND is_default"),
                         {"p": pid})
        await db.execute(text("""UPDATE agent.models SET is_default = true, is_enabled = true
                                 WHERE provider_id = :p AND model_name = :m"""), {"p": pid, "m": name})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.model_set", target_type="provider", target_id=str(pid),
                           target_label=p.name, detail=body.model_dump(), ip=user.ip)
    out = await _one_provider(db, request.app.state.redis, user.org_id, pid)
    # v0.1.59: có model mới ⇒ thẻ "Hôm nay của Sếp" đọc lại ngay (không chờ cache 60 giây).
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)
    return out


@router.delete("/providers/{pid}/models/{mid}", status_code=204)
async def delete_model(pid: uuid.UUID, mid: uuid.UUID, request: Request, user: service.CurrentUser = Depends(MANAGE),
                       _pin: Any = Depends(require_pin("ai.route_change")),
                       db: AsyncSession = DB) -> Response:
    """v0.1.66 (F008): gỡ một model đã thêm nhầm. Cần PIN `ai.route_change` (dọn sót v0.1.70, như `delete_provider`).
    Gỡ luôn dòng gán model trỏ vào nó (vai đó quay về model chuẩn).
    Model còn lượt gọi trong 30 ngày ⇒ 409 `MODEL_HAS_CALLS` (gỡ sẽ xoá dây chuyền bảng giá, chi phí lịch sử tụt về 0) —
    dùng "Tắt model" (`POST /providers/{id}/models` với `enabled: false`)."""
    p = await _provider(db, user.org_id, pid)
    row = (await db.execute(text("""SELECT model_name FROM agent.models WHERE id = :m AND provider_id = :p"""),
                            {"m": mid, "p": pid})).one_or_none()
    if row is None:
        raise not_found("Model")
    # Review v0.1.66 (P2-2): xoá model kéo theo `agent.model_prices` (ON DELETE CASCADE) mà chi phí tính lúc đọc từ
    # bảng giá ⇒ chi phí hôm nay / 7 ngày tụt về 0, trần chi phí không kêu, không đảo ngược. Model còn lượt gọi trong
    # 30 ngày ⇒ không cho gỡ (chỉ "Tắt model": ngừng dùng, giữ lịch sử).
    left = (await db.execute(text("SELECT count(*) FROM agent.models WHERE provider_id = :p"), {"p": pid})).scalar_one()
    if left <= 1 and p.kind == "system_one":
        # Jev không có danh sách model để chọn lại — gỡ model cuối là mất nguồn (muốn ngừng dùng ⇒ tắt nguồn Jev).
        raise conflict("LAST_MODEL", f"{p.name} chỉ có một model và không có danh sách để chọn lại — không gỡ được. "
                                     "Muốn ngừng dùng Jev, hãy tắt nguồn này.")
    if left <= 1 and p.kind in CLI_KINDS:
        # `_heal_agy` tự thêm lại model khi nguồn CLI không còn dòng nào ⇒ gỡ hết chỉ là vô ích và gây khó hiểu.
        raise conflict("LAST_MODEL", f"{p.name} cần ít nhất một model — gỡ hết thì hệ thống tự thêm lại. "
                                     "Muốn ngừng dùng model này, bấm “Tắt model” hoặc chọn model khác bằng "
                                     "“Dùng model này”.")
    used = (await db.execute(text("""SELECT EXISTS (SELECT 1 FROM agent.model_calls
                                     WHERE org_id = :o AND model_id = :m AND at >= now() - interval '30 days')"""),
                             {"o": user.org_id, "m": mid})).scalar_one()
    if used:
        raise conflict("MODEL_HAS_CALLS",
                       f"Model “{row.model_name}” đã có lượt gọi trong 30 ngày qua — gỡ đi sẽ mất lịch sử chi phí. "
                       "Bấm “Tắt model” để ngừng dùng mà vẫn giữ lịch sử.")
    unbound = list((await db.execute(text("""DELETE FROM agent.bindings WHERE org_id = :o AND model_id = :m
                                             RETURNING agent_key"""), {"o": user.org_id, "m": mid})).scalars().all())
    await db.execute(text("DELETE FROM agent.models WHERE id = :m"), {"m": mid})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.model_deleted", target_type="provider", target_id=str(pid),
                           target_label=p.name, detail={"model_name": row.model_name, "unbound": sorted(unbound)},
                           ip=user.ip)
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)   # thẻ "Hôm nay" đọc lại ngay
    return Response(status_code=204)


@router.post("/providers/{pid}/test")
async def test_provider(pid: uuid.UUID, request: Request, user: service.CurrentUser = Depends(MANAGE),
                        db: AsyncSession = DB) -> dict[str, Any]:
    p = await _provider(db, user.org_id, pid)
    if p.kind in CLI_KINDS:
        await _probe_budget(request.app.state.redis, user.org_id)
    await db.commit()
    result: dict[str, Any] = await request.app.state.model_router.test_provider(pid)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="provider.tested",
                           target_type="provider", target_id=str(pid), target_label=p.name,
                           result="ok" if result["ok"] else "failed", detail={"error": result["error"]}, ip=user.ip)
    if p.kind in CLI_KINDS:
        # v0.1.58: nguồn CLI gọi thử thật xanh mà chưa có dòng model nào (mất dòng model → Gen không trả lời, mọi vai
        # "Chuẩn: chưa có nguồn phù hợp") ⇒ lưu cặp (model, mức) vừa chạy được, KHÔNG đặt mặc định; đã có model thì
        # không đụng.
        await mrouter.adopt_probed_model(db, user.org_id, pid, p.name, result, actor_type="user",
                                         actor_id=user.actor_id, ip=user.ip)
        if result["ok"] and user.role_code == rbac.OWNER:
            # "Việc Sếp cần làm" là danh sách của Owner (chạy kiểm bằng POST /boss-checks/{key}/run đòi require_owner):
            # người quản lý khác được quyền gọi thử nguồn nhưng không được tích hộ Sếp.
            await _record_cli_probe_evidence(db, user, p, result)
    # v0.1.59: commit TRƯỚC khi xoá cache — thẻ "Hôm nay của Sếp" đọc lại ngay (kể cả lượt gọi thử đỏ / nguồn khoá API:
    # trạng thái nguồn đã đổi), không chờ cache 60 giây rồi dựng lại từ dữ liệu cũ.
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)
    return result


#: Nguồn CLI → khoá kiểm "Việc Sếp cần làm" của đúng lượt gọi thử đó (Kết nối › dòng Google / Claude Code).
CLI_CALL_CHECK = {climod.AGY: "agy_call", climod.CLAUDE: "claude_call"}


async def _record_cli_probe_evidence(db: AsyncSession, user: service.CurrentUser, p: Any,
                                     result: dict[str, Any]) -> None:
    """v0.1.59 — "Kiểm tra kết nối" của nguồn CLI XANH là một lượt gọi model THẬT (`ModelRouter._cli_probe` sinh
    văn bản) ⇒ ghi đúng bằng chứng đó vào "Việc Sếp cần làm": `agy_call` / `claude_call` (dòng Kết nối tương ứng)
    và `ai_source` (dòng bắt buộc "nguồn AI") — Sếp không phải bấm Kiểm tra lần nữa ở chỗ khác.

    `ai_source` chỉ ghi khi nguồn đang BẬT, đúng như `_run_ai_source` (chỉ xét nguồn `is_enabled`): nguồn đang tắt
    chạy được nhưng chuỗi của Gen không dùng, ghi 'pass' sẽ che một lần 'fail' có thật. Chỉ ghi khi XANH: lượt gọi
    thử lỗi KHÔNG ghi 'fail' ở đây (bản 'fail' chỉ do Sếp bấm Kiểm tra ở Việc Sếp cần làm / Kết nối). Nguồn khoá
    API KHÔNG gọi hàm này: test của chúng chỉ liệt kê model, không phải lượt sinh văn bản."""
    from gh.boss_checks import routes as boss_routes

    detail = boss_routes._call_detail(result)
    if p.is_enabled and p.kind not in ("embedding", "system_one"):
        await boss_service.record(db, user.org_id, "ai_source", "pass", detail=detail, user_id=user.id)
    await boss_service.record(db, user.org_id, CLI_CALL_CHECK[p.kind], "pass", detail=detail, user_id=user.id)
    if p.kind == climod.CLAUDE:
        # Phiên Claude Code có sẵn từ trước (không qua luồng đăng nhập) — như bấm Kiểm tra ở Việc Sếp cần làm.
        await boss_routes._adopt_existing_claude_login(db, user, result)


@router.post("/providers/{pid}/diagnose")
async def diagnose_provider(pid: uuid.UUID, request: Request, user: service.CurrentUser = Depends(require_owner),
                            db: AsyncSession = DB) -> dict[str, Any]:
    """v0.1.32 — "Chẩn đoán" (chỉ Owner) cho nguồn CLI: phiên bản, liệt kê model, một lượt gọi rất ngắn; trả đầu ra
    thô (stdout/stderr/mã thoát) đã che token & email để Boss chép gửi khi còn lỗi."""
    p = await _provider(db, user.org_id, pid)
    if p.kind not in CLI_KINDS:
        raise conflict("NOT_CLI", "Chỉ chẩn đoán được nguồn CLI")
    await _probe_budget(request.app.state.redis, user.org_id)
    await db.commit()
    result: dict[str, Any] = await request.app.state.model_router.diagnose(pid)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="provider.diagnosed", target_type="provider", target_id=str(pid),
                           target_label=p.name,
                           detail={"exit_codes": [s["exit_code"] for s in result["steps"]]}, ip=user.ip)
    return result


@router.get("/providers/credentials")
async def credentials(request: Request, user: service.CurrentUser = Depends(READ),
                      db: AsyncSession = DB) -> list[dict[str, Any]]:
    """Dòng thẻ "Khoá & phiên" (thiết kế `creds`): khoá API theo nhà cung cấp + phiên đăng nhập CLI.

    v0.1.66 (F134): KHÔNG còn dòng "khoá phiên QR" của Zalo / WhatsApp — kênh có thẻ riêng ở Kết nối, hiện lại trong thẻ
    tài khoản Google làm Sếp tưởng có hai lỗi."""
    out: list[dict[str, Any]] = []
    for p in await provider_payloads(db, request.app.state.redis, user.org_id):
        if p["kind"] in CLI_KINDS:
            # v0.1.66 (F015): CÙNG nhãn với `providerStatus` của web — lần Kiểm tra lỗi (vd model bị từ chối) là
            # "Lỗi kết nối" chứ không bảo đăng nhập lại; chỉ phiên hết hạn / chưa đăng nhập mới nói chuyện đăng nhập.
            state, label = {"ok": ("ok", "Hoạt động"), "expiring": ("warn", "Sắp hết hạn"),
                            "error": ("bad", "Lỗi kết nối"), "expired": ("bad", "Hết hạn")
                            }.get(p["auth_state"], ("bad", "Chưa đăng nhập"))
            out.append({"icon": "terminal-window", "name": f"{p['name']} — phiên CLI",
                        "meta": p["account_label"] or "chưa đăng nhập", "state": state, "state_label": label})
            continue
        n = len(p["keys"])
        low = [m for m in p["models"] if m["left_pct"] is not None and m["left_pct"] < 20]
        cooling = [k for k in p["keys"] if k["cooldown_until"]]
        name = f"{p['name']} — {n} khoá xoay vòng" if n > 1 else p["name"]
        labels = " … ".join(dict.fromkeys([p["keys"][0]["label"], p["keys"][-1]["label"]])) if n else "chưa có khoá"
        meta = labels + (f" · còn {low[0]['left_pct']:.0f}% hạn mức".replace(".", ",") if low else "")
        # v0.1.28 (UX N1): cùng nhãn với mọi màn khác (web `providerStatus`) — gọi thử lỗi là "Lỗi kết nối", không
        # còn "Hoạt động" chỉ vì có khoá.
        bad_auth = p["auth_state"] in ("expired", "error")
        state = "bad" if not n or len(cooling) == n or bad_auth else "warn" if low or cooling \
            or p["auth_state"] == "unconfigured" else "ok"
        label = ("Hết hạn" if p["auth_state"] == "expired" else "Lỗi kết nối" if p["auth_state"] == "error"
                 else "Không dùng được") if state == "bad" else \
            ("Sắp cạn" if low else "Đang nghỉ" if cooling else "Chưa kiểm tra") if state == "warn" else "Hoạt động"
        out.append({"icon": "key", "name": name, "meta": meta, "state": state, "state_label": label})
    return out


# ─── CLI: Antigravity (Google) và Claude Code (v0.1.31) ─────────────────────

async def _profile_kind(db: AsyncSession, org_id: uuid.UUID, profile_id: uuid.UUID) -> str | None:
    return (await db.execute(text("""SELECT p.kind FROM agent.cli_profiles c JOIN agent.providers p
                                     ON p.id = c.provider_id WHERE c.id = :i AND c.org_id = :o"""),
                             {"i": profile_id, "o": org_id})).scalar_one_or_none()


class CodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=520)

    @field_validator("code")
    @classmethod
    def _v_code(cls, v: str) -> str:
        """v0.1.45 (F-56): bỏ khoảng trắng hai đầu (dán kèm xuống dòng) rồi khớp đúng `CLI_CODE_RE`."""
        v = v.strip()
        if not climod.CLI_CODE_RE.fullmatch(v):
            raise PydanticCustomError("cli_code", "Mã đăng nhập chỉ gồm chữ, số và các ký hiệu . _ ~ # / + = - "
                                                  "(không có khoảng trắng hay ký tự lạ)")
        return v


@router.get("/cli/profiles")
async def cli_profiles(kind: CliKind = "antigravity_cli", user: service.CurrentUser = Depends(READ),
                       db: AsyncSession = DB) -> list[dict[str, Any]]:
    return await climod.profiles(db, user.org_id, kind)


@router.post("/cli/login", status_code=202)
async def cli_login(request: Request, kind: CliKind = "antigravity_cli", user: service.CurrentUser = Depends(MANAGE),
                    _pin: Any = Depends(require_pin("cli.switch_account")),
                    db: AsyncSession = DB) -> dict[str, Any]:
    """Thêm tài khoản CLI. v0.1.45 (F-20): cần phiên PIN `cli.switch_account` — kiểm SAU quyền (403 trước 423),
    trước khi ghi nhật ký/khởi động CLI. GET trạng thái, /code, /cancel của CÙNG phiên không đòi PIN lại."""
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="cli.login_started", target_type="cli", detail={"kind": kind}, ip=user.ip)
    await db.commit()
    s = await request.app.state.cli_logins.start(user.org_id, user.id, kind)
    return {"login_id": s.id, "kind": kind}


@router.get("/cli/login/{login_id}")
async def cli_login_status(login_id: str, request: Request,
                           user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """Trạng thái phiên đăng nhập (link, bước) — dự phòng khi WebSocket `cli.login` không tới được trình duyệt."""
    s = request.app.state.cli_logins.get(login_id, user.org_id)
    if s is None:
        raise not_found("Phiên đăng nhập")
    return s.public()  # type: ignore[no-any-return]


@router.post("/cli/login/{login_id}/code", status_code=202)
async def cli_code(login_id: str, body: CodeIn, request: Request,
                   user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    s = request.app.state.cli_logins.get(login_id, user.org_id)
    if s is None or s.status != "waiting_code":
        raise conflict("CLI_LOGIN_NOT_WAITING", "Phiên đăng nhập không ở bước chờ mã")
    await request.app.state.cli_logins.submit(s, body.code)
    return {}


@router.post("/cli/login/{login_id}/cancel", status_code=204)
async def cli_cancel(login_id: str, request: Request, user: service.CurrentUser = Depends(MANAGE)) -> Response:
    if request.app.state.cli_logins.get(login_id, user.org_id) is None:
        raise not_found("Phiên đăng nhập")
    request.app.state.cli_logins.cancel(login_id)
    return Response(status_code=204)


@router.post("/cli/profiles/{profile_id}/activate")
async def cli_activate(profile_id: uuid.UUID, request: Request, user: service.CurrentUser = Depends(MANAGE),
                       _pin: Any = Depends(require_pin("cli.switch_account")),
                       db: AsyncSession = DB) -> dict[str, Any]:
    if request.app.state.cli_logins.busy(user.org_id, await _profile_kind(db, user.org_id, profile_id)):
        # Đang thêm tài khoản: tệp phiên đang để trống cho CLI — ghi tệp lúc này sẽ bị nhận nhầm là tài khoản mới.
        raise conflict("CLI_LOGIN_IN_PROGRESS",
                       "Đang đăng nhập thêm một tài khoản — hoàn tất hoặc huỷ bước đó rồi đổi tài khoản")
    out = await climod.activate(db, user.org_id, profile_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="cli.account_switched", target_type="cli_profile", target_id=out["id"],
                           target_label=out["email"], ip=user.ip)
    return next(p for p in await climod.profiles(db, user.org_id, out["kind"]) if p["id"] == out["id"])


@router.delete("/cli/profiles/{profile_id}", status_code=204)
async def cli_delete(profile_id: uuid.UUID, request: Request, user: service.CurrentUser = Depends(MANAGE),
                     _pin: Any = Depends(require_pin("cli.switch_account")),
                     db: AsyncSession = DB) -> Response:
    if request.app.state.cli_logins.busy(user.org_id, await _profile_kind(db, user.org_id, profile_id)):
        raise conflict("CLI_LOGIN_IN_PROGRESS",
                       "Đang đăng nhập thêm một tài khoản — hoàn tất hoặc huỷ bước đó rồi xoá tài khoản")
    out = await climod.delete_profile(db, user.org_id, profile_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="cli.profile_deleted", target_type="cli_profile", target_id=str(profile_id),
                           target_label=out["email"], detail={"was_active": out["was_active"]}, ip=user.ip)
    return Response(status_code=204)


# ─── Bộ não AI: chuỗi chuyển hướng + quy tắc ────────────────────────────────

# Quy tắc chuyển hướng (ARCHITECTURE §11, thiết kế `failoverRules`) — cố định trong `gh.providers.router`, không
# có tham số nào Owner chỉnh được ở đây nên chỉ có GET (đọc để hiển thị, không PATCH).
FAILOVER_RULES: tuple[dict[str, str], ...] = (
    {"key": "hết hạn mức", "value": "chuyển xuống nhà cung cấp kế tiếp trong chuỗi (hạn mức Sếp đặt ở từng model)"},
    {"key": "ngắt mạch", "value": "giữ nguyên hội thoại, thử lại sau 60 giây"},
    # v0.1.70 (F029 sót): hai luật việc nền — câu chữ khớp `gh.providers.placement` / `agy_policy.decide` / `router`
    # (test_failover_rules_v0170 đối chiếu từng vai và từng loại việc).
    {"key": "hộp Antigravity",
     "value": "vai chạy Antigravity qua hộp riêng (soạn lại / dịch nháp tin khách, sàng lọc tin, Bản tin Gen, agent "
              "ngoại giao, Gen của nhân viên) mà hộp chưa chạy thì bỏ qua Antigravity và chuyển nguồn kế tiếp — không "
              "bao giờ chạy thay trong tiến trình; chỉ Gen của Sếp và agent nội bộ không cần hộp"},
    {"key": "việc nền",
     "value": "việc nền (sàng lọc tin, trực việc, Bản tin Gen) bỏ qua Claude Code CLI trừ khi Sếp đã cho phép ở Bộ não "
              "AI; Antigravity không cần cho phép này. Chỉ còn Claude Code CLI chưa được phép thì hết chuỗi và báo "
              "Sếp"},
    {"key": "hết chuỗi", "value": "xếp hàng và báo Sếp qua hàng đợi cần xử lý"},
    {"key": "ngưỡng cảnh báo", "value": "còn dưới 20% hạn mức ngày trên model nào Sếp đã đặt hạn mức"},
)


@router.get("/failover-rules")
async def failover_rules(user: service.CurrentUser = Depends(READ)) -> list[dict[str, str]]:
    return list(FAILOVER_RULES)


# ─── Quyền hạn: ma trận, nhóm lắng nghe, ranh giới ──────────────────────────

# 7 cột của ma trận thiết kế (`permCols`) → các quyền (`core.permissions`) thuộc cột đó. `data.*`/`system.*`/
# `roles.manage` nằm NGOÀI ma trận thiết kế (rbac.py) nên không sửa được qua đây — chỉ Owner có, không cấu hình.
PERMISSION_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("overview", "Hôm nay", ("overview.read",)),
    ("queue", "Hàng đợi", ("queue.read", "queue.act")),
    ("profile", "Hồ sơ khách", ("profile.read", "profile.write")),
    ("people_review", "Đánh giá nhân sự", ("people_review.read", "people_review.write", "care.read")),
    ("opportunity", "Cơ hội", ("opportunity.read", "opportunity.write")),
    ("action", "Hành động", ("action.draft", "action.approve")),
    ("audit", "Nhật ký", ("audit.read",)),
)
EDITABLE_PERMISSIONS = frozenset(c for _, _, codes in PERMISSION_COLUMNS for c in codes)


@router.get("/permissions")
async def get_permissions(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    rows = (await db.execute(text("""
        SELECT r.code AS role_code, rp.permission_code, rp.scope FROM core.role_permissions rp
        JOIN core.roles r ON r.id = rp.role_id WHERE r.org_id = :o"""), {"o": user.org_id})).all()
    by_role: dict[str, dict[str, str]] = {}
    for r in rows:
        by_role.setdefault(r.role_code, {})[r.permission_code] = r.scope
    roles_out = [{"code": rd.code, "name": rd.name, "meta": rd.meta,
                 "permissions": {c: by_role.get(rd.code, {}).get(c, rbac.NONE) for c in EDITABLE_PERMISSIONS}}
                for rd in rbac.ROLES]
    return {"columns": [{"key": k, "label": lbl, "permissions": list(codes)} for k, lbl, codes in PERMISSION_COLUMNS],
            "roles": roles_out}


class PermissionPatch(BaseModel):
    role: Literal["owner", "manager", "operator", "agent_staff", "auditor"]
    permission: str = Field(max_length=60)
    scope: Literal["all", "team", "assigned", "none"]


@router.patch("/permissions")
async def patch_permissions(body: PermissionPatch, user: service.CurrentUser = Depends(ROLES_MANAGE),
                            _pin: Any = Depends(require_pin("roles.change")),
                            db: AsyncSession = DB) -> dict[str, Any]:
    """Owner sửa từng ô của ma trận (PIN + log, ARCHITECTURE §7.4/§8.3). Hai bất biến không sửa được qua API —
    `gh.bootstrap._permissions` đặt lại nếu có ai chỉnh thẳng DB, ở đây từ chối thẳng (422):
    Owner luôn `all` mọi cột; Auditor không bao giờ có quyền ghi (`rbac.WRITE_PERMISSIONS`)."""
    if body.permission not in EDITABLE_PERMISSIONS:
        raise field_errors({"permission": "Quyền này không nằm trong ma trận sửa được ở Quyền hạn"})
    if body.role == rbac.OWNER and body.scope != rbac.ALL:
        raise field_errors({"scope": "Owner luôn toàn quyền ở mọi cột — khoá cứng, không sửa được"})
    if body.role == rbac.AUDITOR and body.permission in rbac.WRITE_PERMISSIONS and body.scope != rbac.NONE:
        raise field_errors({"scope": "Vai Kiểm soát không bao giờ có quyền ghi — khoá cứng, không sửa được"})
    row = (await db.execute(text("""
        SELECT rp.scope, r.id AS role_id FROM core.role_permissions rp JOIN core.roles r ON r.id = rp.role_id
        WHERE r.org_id = :o AND r.code = :role AND rp.permission_code = :perm"""),
        {"o": user.org_id, "role": body.role, "perm": body.permission})).one_or_none()
    if row is None:
        raise not_found("Ô ma trận")
    await db.execute(text("""UPDATE core.role_permissions SET scope = :s
                             WHERE role_id = :rid AND permission_code = :perm"""),
                     {"s": body.scope, "rid": row.role_id, "perm": body.permission})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="permission.changed", target_type="role", target_id=body.role,
                           target_label=body.permission, detail={"permission": body.permission,
                                                                  "from": row.scope, "to": body.scope}, ip=user.ip)
    return await get_permissions(user, db)


@router.get("/listening-groups")
async def listening_groups(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> list[dict[str, Any]]:
    """Nhóm đang lắng nghe (khoá cứng #1 `listen_authorized_only` — chỉ nhóm Sếp đã chọn nghe mới vào đây): chế độ thuộc
    LISTENING_MODES (không gồm off / paused / pending) và Sếp chưa rời nhóm."""
    rows = (await db.execute(text(GROUP_SELECT + " WHERE c.org_id = :o AND g.listen_mode = ANY(:m) "
                                  f"AND g.left_at IS NULL AND {group_row_view_sql(user.role_code, 'g')} "
                                  "ORDER BY g.name"),
                             {"o": user.org_id, "m": list(LISTENING_MODES)})).all()
    return [_group_out(r) for r in rows]


# Nhãn tiếng Việt cho `ops.policy_boundaries.code` (ARCHITECTURE §7.4 + PLAN Q "Mặc định giới hạn"); 6/8 khoá cứng
# có mặt ở đây dưới dạng `is_locked=true` — 2 khoá còn lại (#5 kho thô/nhật ký chỉ-INSERT, #6 PIN+mã hoá bí mật)
# là bất biến ở tầng DB/code, không phải công tắc nên không có dòng `ops.policy_boundaries` tương ứng.
BOUNDARY_LABELS: dict[str, str] = {
    "listen_authorized_only": "Chỉ lưu tin của nhóm Sếp đã chọn nghe",
    "disclose_staff_observation": "Công khai nội bộ khi dùng để đánh giá nhân sự",
    "hide_sensitive_below_owner": "Ẩn dữ liệu nhạy cảm khỏi vai trò dưới Owner",
    "personnel_alert_requires_evidence": "Điểm số, cảnh báo nhân sự phải có chứng cứ",
    "observe_external_market": "Quan sát nhóm thị trường bên ngoài",
    "auto_personnel_decisions": "Hệ thống tự ra quyết định nhân sự",
    "approval_gate": "Gửi ra ngoài / vượt ngưỡng tiền / liên quan nhân sự luôn chờ duyệt",
    "mcp_write_requires_approval": ("Tool MCP loại ghi qua duyệt trước khi chạy (ngoại lệ duy nhất: Ghi vào "
                                    + KHO_LABEL + " sau khi Sếp Xác nhận + nhập mã PIN)"),
}


@router.get("/boundaries")
async def get_boundaries(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""SELECT code, is_enabled, is_locked, params FROM ops.policy_boundaries
                                     WHERE org_id = :o ORDER BY code"""), {"o": user.org_id})).all()
    kho = await load_kho_label(db, user.org_id)       # v0.1.57: tên Kho Owner tự đặt (Nợ #30)
    return [{"code": r.code, "label": relabel(BOUNDARY_LABELS.get(r.code, r.code), kho), "enabled": r.is_enabled,
             "locked": r.is_locked, "params": r.params} for r in rows]


class BoundaryPatch(BaseModel):
    enabled: bool | None = None
    params: dict[str, Any] | None = None


@router.patch("/boundaries/{code}")
async def patch_boundary(code: str, body: BoundaryPatch, user: service.CurrentUser = Depends(MANAGE),
                         _pin: Any = Depends(require_pin("policy.change")), db: AsyncSession = DB
                         ) -> dict[str, Any]:
    """Ranh giới có trách nhiệm (PIN + log). Khoá cứng (`is_locked`) không tắt/bật được (422) dù có PIN đúng —
    `params` (ví dụ `approval_threshold_vnd`) sửa được ngay cả khi ranh giới đó bị khoá bật, vì bản thân việc
    CÓ chờ duyệt là bất biến, còn NGƯỠNG là do Owner đặt (ARCHITECTURE §7.2)."""
    row = (await db.execute(text("""SELECT is_enabled, is_locked, params FROM ops.policy_boundaries
                                    WHERE org_id = :o AND code = :c"""), {"o": user.org_id, "c": code})).one_or_none()
    if row is None:
        raise not_found("Ranh giới")
    if body.enabled is not None and body.enabled != row.is_enabled and row.is_locked:
        raise field_errors({"enabled": "Ranh giới này là khoá cứng — không tắt/bật được (ARCHITECTURE §7.4)"})
    if code == "approval_gate" and body.params and "approval_threshold_vnd" in body.params:
        v = body.params["approval_threshold_vnd"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise field_errors({"params.approval_threshold_vnd": "Ngưỡng tiền phải là số nguyên không âm"})
    sets: list[str] = []
    params: dict[str, Any] = {"o": user.org_id, "c": code}
    if body.enabled is not None:
        sets.append("is_enabled = :e")
        params["e"] = body.enabled
    if body.params is not None:
        sets.append("params = params || CAST(:p AS jsonb)")
        params["p"] = orjson.dumps(body.params).decode()
    if sets:
        await db.execute(text(f"UPDATE ops.policy_boundaries SET {', '.join(sets)} WHERE org_id = :o AND code = :c"),
                         params)  # noqa: S608 — sets chỉ gồm hằng cố định ở trên
    new = (await db.execute(text("""SELECT is_enabled, is_locked, params FROM ops.policy_boundaries
                                    WHERE org_id = :o AND code = :c"""), {"o": user.org_id, "c": code})).one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="boundary.changed", target_type="boundary", target_id=code,
                           target_label=BOUNDARY_LABELS.get(code, code),
                           detail={"from": {"enabled": row.is_enabled, "params": row.params},
                                   "to": {"enabled": new.is_enabled, "params": new.params}}, ip=user.ip)
    return {"code": code, "label": relabel(BOUNDARY_LABELS.get(code, code), await load_kho_label(db, user.org_id)),
            "enabled": new.is_enabled,
            "locked": new.is_locked, "params": new.params}


# ─── Nhật ký ─────────────────────────────────────────────────────────────────

@router.get("/audit-log")
async def system_audit_log(cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                           actor_type: str | None = None, action: str | None = None,
                           target_type: str | None = None, target_id: str | None = None,
                           since: datetime | None = None, until: datetime | None = None,
                           user: service.CurrentUser = Depends(AUDIT_READ), db: AsyncSession = DB
                           ) -> dict[str, Any]:
    """Cùng đường đọc với `/audit` (`gh.audit.routes.query_log`) — chỉ thêm chỗ vào tab Nhật ký của Điều khiển
    hệ thống, thêm lọc theo đối tượng/thời gian mà `[auditLog]` cần."""
    return await query_log(db, user, cursor=cursor, limit=limit, actor_type=actor_type, action=action,
                           target_type=target_type, target_id=target_id, since=since, until=until)


@router.get("/audit-log/export")
async def system_audit_log_export(actor_type: str | None = None, action: str | None = None,
                                  target_type: str | None = None, target_id: str | None = None,
                                  since: datetime | None = None, until: datetime | None = None,
                                  user: service.CurrentUser = Depends(DATA_MANAGE),
                                  _pin: Any = Depends(require_pin("data.export")), db: AsyncSession = DB
                                  ) -> Response:
    """Xuất CSV nhật ký — cùng khuôn với `POST /raw/export` (`gh.data_api.routes.raw_export`): quyền quản lý dữ
    liệu + PIN, ghi lại chính lượt xuất vào Action Log."""
    rows = await export_rows(db, user, actor_type=actor_type, action=action, target_type=target_type,
                             target_id=target_id, since=since, until=until)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["at", "actor_type", "actor_id", "actor_label", "action", "target_type", "target_id", "target_label",
                "autonomy_level", "result"])
    for it in rows:
        w.writerow([it["at"], it["actor_type"], it["actor_id"], it["actor_label"], it["action"], it["target_type"],
                    it["target_id"], it["target_label"], it["autonomy_level"], it["result"]])
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="audit_log.exported", target_type="audit_log",
                           detail={"rows": len(rows), "filters": {"actor_type": actor_type, "action": action,
                                                                  "target_type": target_type, "target_id": target_id}},
                           ip=user.ip)
    name = f"nhat-ky-{datetime.now(UTC):%Y%m%d-%H%M}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{name}"'})


# ─── Dữ liệu & lưu trữ (spec I) ──────────────────────────────────────────────

# Tập dữ liệu Owner đặt được hạn lưu (`ops.retention_policies`, thiết kế 01-ui-screens §system bổ sung).
# v0.1.40 (F-2): nguồn sự thật là gh/retention.py (chế độ dọn, ghi chú, sync partman).
RETENTION_DATASETS = tuple(retention.DATASETS)


@router.get("/retention-policies")
async def get_retention(request: Request, user: service.CurrentUser = Depends(READ),
                        db: AsyncSession = DB) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""SELECT dataset, keep_days, anonymize_after_days, confirmed_at
                                     FROM ops.retention_policies WHERE org_id = :o"""), {"o": user.org_id})).all()
    by_ds = {r.dataset: r for r in rows}
    last = await retention.read_last(getattr(request.app.state, "redis", None)) or {}
    last_ds = last.get("datasets") if isinstance(last.get("datasets"), dict) else {}
    out: list[dict[str, Any]] = []
    for d, meta in [*retention.DATASETS.items(), *retention.FIXED.items()]:
        mode = str(meta["mode"])
        if d in retention.FIXED:
            keep, anon = meta["keep_days"], None
        else:
            keep = by_ds[d].keep_days if d in by_ds else None
            anon = by_ds[d].anonymize_after_days if d in by_ds else None
        if mode == "not_applicable":
            keep = None
        run = last_ds.get(d) if isinstance(last_ds, dict) else None
        # Hạn đặt trước v0.1.40 (confirmed_at NULL) chưa được thi hành — web hiện "Chưa áp dụng — cần xác nhận lại".
        needs_confirm = (d in retention.ENFORCED and d not in retention.FIXED and keep is not None
                         and by_ds[d].confirmed_at is None)
        out.append({"dataset": d, "keep_days": keep, "anonymize_after_days": anon, "mode": mode,
                    "editable": bool(meta["editable"]), "note": retention.note_for(d),
                    "needs_confirm": needs_confirm,
                    "last_run_at": last.get("at") if isinstance(run, dict) else None,
                    "last_deleted": run.get("deleted") if isinstance(run, dict) else None,
                    # Lượt dọn của tập này lỗi (`ok: false`) ⇒ web báo lỗi, không hiện "đã xoá 0" như thành công.
                    "last_ok": (run.get("ok") is not False) if isinstance(run, dict) else None})
    return out


class RetentionIn(BaseModel):
    dataset: Literal["raw.events", "clean.meaning_units", "ops.action_log", "memory.entries", "agent.model_calls"]
    keep_days: int | None = Field(default=None, ge=1, le=3650)
    anonymize_after_days: int | None = Field(default=None, ge=1, le=3650)
    #: v0.1.40 (F-2): đặt hạn cho tập dữ liệu bị xoá thật ⇒ bên gọi PHẢI xác nhận đã biết dữ liệu quá hạn bị XOÁ VĨNH
    #: VIỄN ở lượt dọn kế tiếp (web hỏi lại trước khi gửi). Thiếu ⇒ 422 RETENTION_CONFIRM_REQUIRED.
    confirm_delete: bool = False


def _confirm_text(dataset: str, keep: int) -> str:
    what = "cả tháng dữ liệu cũ hơn" if retention.DATASETS[dataset]["mode"] == "partition" else "dữ liệu cũ hơn"
    return (f"Cần xác nhận: {what} {keep} ngày sẽ bị XOÁ VĨNH VIỄN ở lượt dọn kế tiếp (05:00 hằng ngày) — "
            "chỉ lấy lại được từ bản sao lưu")


@router.patch("/retention-policies")
async def patch_retention(body: RetentionIn, request: Request, user: service.CurrentUser = Depends(MANAGE),
                          _pin: Any = Depends(require_pin("policy.change")), db: AsyncSession = DB
                          ) -> list[dict[str, Any]]:
    if retention.DATASETS[body.dataset]["mode"] == "not_applicable" and body.keep_days is not None:
        raise ApiError(422, "RETENTION_NOT_APPLICABLE", "Dữ liệu chưa hợp lệ",
                       errors={"keep_days": retention.NOT_APPLICABLE_ERROR})
    # Bảng phân vùng: xoá cả tháng của raw.events/clean.meaning_units/agent.model_calls cho MỌI tổ chức trên máy ⇒ chỉ
    # Owner (Manager có system.manage vẫn đổi được hạn sổ tay).
    if body.dataset in retention.PARTITIONED and user.role_code != rbac.OWNER:
        raise forbidden("Chỉ Owner đổi được hạn lưu của dữ liệu xoá theo tháng")
    deletes = body.dataset in retention.ENFORCED and body.keep_days is not None
    if deletes and not body.confirm_delete:
        raise ApiError(422, "RETENTION_CONFIRM_REQUIRED", "Cần xác nhận xoá vĩnh viễn",
                       errors={"keep_days": _confirm_text(body.dataset, int(body.keep_days or 0))})
    await db.execute(text("""
        INSERT INTO ops.retention_policies (org_id, dataset, keep_days, anonymize_after_days, confirmed_at)
        VALUES (:o, :d, :k, :a, CASE WHEN CAST(:c AS boolean) THEN now() END)
        ON CONFLICT (org_id, dataset) DO UPDATE SET keep_days = :k, anonymize_after_days = :a,
          confirmed_at = CASE WHEN CAST(:c AS boolean) THEN now() END"""),
        {"o": user.org_id, "d": body.dataset, "k": body.keep_days, "a": body.anonymize_after_days, "c": deletes})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="retention_policy.changed", target_type="retention_policy",
                           target_id=body.dataset, detail=body.model_dump(), ip=user.ip)
    # Bảng phân vùng: KHÔNG đẩy hạn sang partman ở đây — chỉ lượt dọn 05:00 (`retention_sweep`) đặt retention, xoá
    # phân vùng quá hạn và đếm, đúng giờ câu xác nhận đã hứa (`partition_maintenance` 23:20/04:20 không xoá gì).
    return await get_retention(request, user, db)


async def _person_row(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID) -> Any:
    r = (await db.execute(text("SELECT id, code, display_name, attrs FROM core.persons WHERE id = :i AND org_id = :o"),
                          {"i": person_id, "o": org_id})).one_or_none()
    if r is None:
        raise not_found("Người")
    return r


async def _export_person_bundle(db: AsyncSession, person: Any) -> dict[str, Any]:
    identities = (await db.execute(text("""
        SELECT c.type AS channel, ci.external_id, ci.handle, ci.phone_e164, ci.first_seen_at
        FROM core.person_identities ci JOIN core.channels c ON c.id = ci.channel_id
        WHERE ci.person_id = :p"""), {"p": person.id})).all()
    scores = (await db.execute(text("""SELECT dimension, value, trend, updated_at FROM clean.current_scores
                                       WHERE subject_type = 'person' AND subject_id = :p"""),
                               {"p": person.id})).all()
    notes = (await db.execute(text("""
        SELECT e.section, e.body, e.author, e.created_at FROM memory.entries e
        JOIN memory.notebooks n ON n.id = e.notebook_id
        WHERE n.subject_type = 'person' AND n.subject_id = :p AND e.archived_at IS NULL
        ORDER BY e.created_at"""), {"p": person.id})).all()
    return {"person": {"id": str(person.id), "code": person.code, "display_name": person.display_name,
                       "attrs": person.attrs},
            "identities": [{"channel": i.channel, "external_id": i.external_id, "handle": i.handle,
                            "phone_e164": i.phone_e164, "first_seen_at": iso(i.first_seen_at)} for i in identities],
            "scores": [{"dimension": s.dimension, "value": float(s.value), "trend": s.trend,
                       "updated_at": iso(s.updated_at)} for s in scores],
            "notebook": [{"section": n.section, "body": n.body, "author": n.author, "created_at": iso(n.created_at)}
                        for n in notes]}


class DataRequestIn(BaseModel):
    kind: Literal["export", "erase", "restrict"]


@router.post("/persons/{person_id}/data-requests", status_code=201)
async def create_data_request(person_id: uuid.UUID, body: DataRequestIn, user: service.CurrentUser = Depends(MANAGE),
                              _pin: Any = Depends(require_pin("data.export_delete")), db: AsyncSession = DB
                              ) -> dict[str, Any]:
    """Yêu cầu xuất / xoá / giới hạn dữ liệu một người (spec I, `ops.data_requests`). `erase` xoá/ẩn danh dữ liệu
    suy ra (điểm số, sổ tay, số điện thoại/handle) — KHÔNG đụng `raw.events` (khoá cứng #5, chỉ-INSERT); tin thô
    vẫn còn nhưng người đã ẩn danh, không còn định danh được ngược từ Console."""
    person = await _person_row(db, user.org_id, person_id)
    req_id = (await db.execute(text("""INSERT INTO ops.data_requests (org_id, person_id, kind, status)
                                       VALUES (:o, :p, :k, 'open') RETURNING id"""),
                               {"o": user.org_id, "p": person_id, "k": body.kind})).scalar_one()
    result: dict[str, Any]
    if body.kind == "export":
        result = await _export_person_bundle(db, person)
    elif body.kind == "erase":
        await db.execute(text("""UPDATE core.persons SET display_name = 'Người dùng đã xoá', attrs = '{}'::jsonb,
                                 deleted_at = now() WHERE id = :i"""), {"i": person_id})
        await db.execute(text("""UPDATE core.person_identities SET handle = NULL, phone_e164 = NULL
                                 WHERE person_id = :i"""), {"i": person_id})
        await db.execute(text("DELETE FROM clean.current_scores WHERE subject_type = 'person' AND subject_id = :i"),
                         {"i": person_id})
        await db.execute(text("""DELETE FROM memory.entries WHERE notebook_id IN
                                 (SELECT id FROM memory.notebooks
                                   WHERE subject_type = 'person' AND subject_id = :i)"""), {"i": person_id})
        result = {"erased": True}
    else:
        await db.execute(text("""UPDATE core.persons SET attrs = attrs || '{"data_restricted": true}'::jsonb
                                 WHERE id = :i"""), {"i": person_id})
        result = {"restricted": True}
    await db.execute(text("UPDATE ops.data_requests SET status = 'completed', completed_at = now() WHERE id = :i"),
                     {"i": req_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action=f"person_data.{body.kind}", target_type="person", target_id=person.code,
                           target_label=person.display_name, detail={"request_id": str(req_id)}, ip=user.ip)
    return {"id": str(req_id), "kind": body.kind, "status": "completed", "result": result}


@router.get("/persons/{person_id}/data-requests")
async def list_data_requests(person_id: uuid.UUID, user: service.CurrentUser = Depends(READ), db: AsyncSession = DB
                             ) -> list[dict[str, Any]]:
    await _person_row(db, user.org_id, person_id)
    rows = (await db.execute(text("""SELECT id, kind, status, requested_at, completed_at FROM ops.data_requests
                                     WHERE org_id = :o AND person_id = :p ORDER BY requested_at DESC"""),
                             {"o": user.org_id, "p": person_id})).all()
    return [{"id": str(r.id), "kind": r.kind, "status": r.status, "requested_at": iso(r.requested_at),
             "completed_at": iso(r.completed_at)} for r in rows]


__all__ = ["router", "channel_card", "update_group", "GroupPatch", "provider_payloads", "LISTEN_MODES", "VIEW_SCOPES",
           "GROUP_KINDS", "PROVIDER_KINDS"]
