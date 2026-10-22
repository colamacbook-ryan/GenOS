"""API Console của bot tài khoản riêng (v0.1.72). CHỈ Owner (+ `system.manage`); mọi thao tác đổi trạng thái cần PIN.

- `GET/PUT /owner-channel/{ch}` — trạng thái kênh quản trị + cài đặt báo cáo chủ động (chỉ sự kiện đặc biệt).
- `POST /owner-channel/{ch}/pair/start` (PIN) — cấp mã 6 số (trả MỘT lần) · `DELETE /owner-channel/{ch}/pair` (PIN).
- `PUT /channels/{ch}/sessions/{sid}/account-kind` (PIN) — «tài khoản riêng của bot» ⇄ «tài khoản cá nhân của Sếp».
- `GET/PUT /groups/{gid}/support` (PIN khi đổi) — Chế độ hỗ trợ của một nhóm.
- `GET /auto-sends` — sổ tin bot tự gửi (không có nội dung tin: chỉ băm + độ dài + mã luật).
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.chassis import actionlog
from gh.data import listen_events as lev
from gh.data.common import LISTENING_MODES, iso, org_settings, since_cutoff
from gh.data.ingest import sync_listen_sets
from gh.db import DB
from gh.errors import ApiError, field_errors
from gh.owner_channel import inbound as oin
from gh.owner_channel import pairing, reports
from gh.owner_channel.autosend import support_limits
from gh.owner_channel.identity import CHANNELS, active_session, owner_ids, sync_owner_sets

router = APIRouter(tags=["owner-channel"])
MANAGE = require("system.manage", rbac.ALL)
BASIS_LABEL = {"owner_dm": "Kênh quản trị", "owner_tag": "Sếp gọi", "support": "Hỗ trợ"}


def _ch(ch: str) -> str:
    if ch not in CHANNELS:
        raise ApiError(404, "CHANNEL_NOT_FOUND", "Kênh này chưa có kênh quản trị (chỉ Zalo và WhatsApp)")
    return ch


class PrefsIn(BaseModel):
    quiet_start: int | None = Field(None, ge=0, le=23)
    quiet_end: int | None = Field(None, ge=0, le=23)
    max_per_hour: int | None = Field(None, ge=1, le=60)
    min_severity: Literal["info", "important", "urgent"] | None = None


class OwnerChannelIn(BaseModel):
    enabled: bool | None = None
    report_prefs: PrefsIn | None = None


class AccountKindIn(BaseModel):
    account_kind: Literal["bot", "owner_self"]


class SupportIn(BaseModel):
    enabled: bool
    until_hours: int | None = Field(None, ge=1, le=24 * 30)
    limits: dict[str, int] | None = None


async def _view(db: AsyncSession, org_id: uuid.UUID, ch: str) -> dict[str, Any]:
    sess = await active_session(db, org_id, ch)
    row = (await db.execute(text("""SELECT * FROM ops.owner_channels WHERE org_id = :o AND channel_type = :c"""),
                            {"o": org_id, "c": ch})).one_or_none()
    oi = await owner_ids(db, org_id, ch)
    now = datetime.now(UTC)
    expires = row.pair_expires_at if row is not None else None
    pending = bool(row is not None and row.pair_code_hash is not None and expires and expires > now)
    n_support = len(await oin_support_groups(db, org_id, ch))
    prefs = reports.prefs_of(await org_settings(db, org_id), row.report_prefs if row is not None else None)
    ext = (row.owner_external_id or "") if row is not None else ""
    return {
        "channel": ch,
        "session": {"id": str(sess.id), "account_kind": sess.account_kind or "owner_self"} if sess else None,
        "bot_account": bool(sess and sess.account_kind == "bot"),
        "paired": oi.paired, "enabled": oi.enabled,
        "owner_masked": ("•••" + ext[-4:]) if ext else None,
        "owner_display": row.owner_display if row is not None else None,
        "paired_at": iso(row.paired_at) if row is not None else None,
        "pair_pending": pending, "pair_expires_at": iso(expires) if pending else None,
        "report_prefs": prefs, "support_groups": n_support,
    }


async def oin_support_groups(db: AsyncSession, org_id: uuid.UUID, ch: str) -> list[str]:
    from gh.owner_channel.identity import support_active_groups

    return await support_active_groups(db, org_id, ch)


@router.get("/owner-channel/{ch}")
async def get_owner_channel(ch: str, _m: service.CurrentUser = Depends(MANAGE),
                            user: service.CurrentUser = Depends(require_owner),
                            db: AsyncSession = DB) -> dict[str, Any]:
    return await _view(db, user.org_id, _ch(ch))


@router.put("/owner-channel/{ch}")
async def put_owner_channel(ch: str, body: OwnerChannelIn, request: Request,
                            _m: service.CurrentUser = Depends(MANAGE),
                            user: service.CurrentUser = Depends(require_owner),
                            _p: service.CurrentUser = Depends(require_pin("notify.change")),
                            db: AsyncSession = DB) -> dict[str, Any]:
    """Bật/tắt kênh quản trị (bật cần đã ghép + tài khoản bot) và chỉnh cài đặt báo cáo chủ động."""
    ch = _ch(ch)
    oi = await owner_ids(db, user.org_id, ch)
    if body.enabled is True and not oi.paired:
        raise ApiError(409, "OWNER_NOT_PAIRED", "Chưa ghép kênh quản trị — bấm Ghép và nhắn mã cho bot trước")
    if body.enabled is True:
        sess = await active_session(db, user.org_id, ch)
        if sess is None or sess.account_kind != "bot":
            raise ApiError(409, "NOT_BOT_ACCOUNT", "Kênh đang chạy bằng tài khoản cá nhân — chưa bật được")
    if body.enabled is not None:
        await db.execute(text("""UPDATE ops.owner_channels SET enabled = :e, updated_at = now(), updated_by = :u
                                 WHERE org_id = :o AND channel_type = :c"""),
                         {"e": body.enabled, "u": user.id, "o": user.org_id, "c": ch})
    if body.report_prefs is not None:
        patch = {k: v for k, v in body.report_prefs.model_dump().items() if v is not None}
        await db.execute(text("""
            INSERT INTO ops.owner_channels (org_id, channel_type, report_prefs, updated_by)
            VALUES (:o, :c, CAST(:p AS jsonb), :u)
            ON CONFLICT (org_id, channel_type) DO UPDATE
              SET report_prefs = ops.owner_channels.report_prefs || CAST(:p AS jsonb), updated_at = now(),
                  updated_by = :u"""),
                         {"o": user.org_id, "c": ch, "p": json.dumps(patch), "u": user.id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="owner_channel.update", target_type="owner_channel", target_id=ch,
                           detail={"enabled": body.enabled,
                                   "report_prefs": body.report_prefs.model_dump(exclude_none=True)
                                   if body.report_prefs else None}, ip=user.ip)
    await sync_owner_sets(db, request.app.state.redis, user.org_id)
    await db.commit()
    return await _view(db, user.org_id, ch)


@router.post("/owner-channel/{ch}/pair/start")
async def pair_start(ch: str, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                     user: service.CurrentUser = Depends(require_owner),
                     _p: service.CurrentUser = Depends(require_pin("notify.change")),
                     db: AsyncSession = DB) -> dict[str, Any]:
    """Cấp mã ghép 6 số (sống 10 phút). Mã chỉ có trong phản hồi này — DB chỉ lưu băm, Action Log không có mã."""
    ch = _ch(ch)
    code, expires = await pairing.start_pairing(db, request.app.state.redis, user.org_id, ch, user.id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action=pairing.ACTION_PAIR_START, target_type="owner_channel", target_id=ch,
                           detail={"expires_at": iso(expires)}, ip=user.ip)
    await db.commit()
    return {**await _view(db, user.org_id, ch), "pair": {"code": code, "expires_at": iso(expires)}}


@router.delete("/owner-channel/{ch}/pair")
async def pair_delete(ch: str, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                      user: service.CurrentUser = Depends(require_owner),
                      _p: service.CurrentUser = Depends(require_pin("notify.change")),
                      db: AsyncSession = DB) -> dict[str, Any]:
    """Huỷ ghép: xoá ID Owner, tắt kênh quản trị và mọi chế độ hỗ trợ của kênh."""
    ch = _ch(ch)
    await pairing.unpair(db, request.app.state.redis, user.org_id, ch)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action=pairing.ACTION_UNPAIRED, target_type="owner_channel", target_id=ch, detail={},
                           ip=user.ip)
    await db.commit()
    return await _view(db, user.org_id, ch)


@router.put("/channels/{ch}/sessions/{sid}/account-kind")
async def put_account_kind(ch: str, sid: uuid.UUID, body: AccountKindIn, request: Request,
                           _m: service.CurrentUser = Depends(MANAGE),
                           user: service.CurrentUser = Depends(require_owner),
                           _p: service.CurrentUser = Depends(require_pin("policy.change")),
                           db: AsyncSession = DB) -> dict[str, Any]:
    """Đánh dấu phiên kênh là «tài khoản riêng của bot» (`bot`) hay «tài khoản cá nhân của Sếp» (`owner_self`).
    Đổi khỏi `bot`
    ⇒ huỷ ghép kênh quản trị và tắt mọi chế độ hỗ trợ của kênh (bot không còn được tự gửi)."""
    ch = _ch(ch)
    s = (await db.execute(text("""
        SELECT s.id, s.account_kind FROM core.channel_sessions s JOIN core.channels c ON c.id = s.channel_id
        WHERE s.id = :i AND c.org_id = :o AND c.type = :t"""), {"i": sid, "o": user.org_id, "t": ch})).one_or_none()
    if s is None:
        raise ApiError(404, "SESSION_NOT_FOUND", "Không tìm thấy phiên kênh này")
    before = s.account_kind or "owner_self"
    await db.execute(text("UPDATE core.channel_sessions SET account_kind = :k WHERE id = :i"),
                     {"k": body.account_kind, "i": sid})
    if before == "bot" and body.account_kind != "bot":
        await oin.cancel_pairing(db, user.org_id, ch, reason="account_kind_changed")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="channel.account_kind", target_type="channel_session", target_id=str(sid),
                           detail={"channel": ch, "from": before, "to": body.account_kind}, ip=user.ip)
    await sync_listen_sets(db, request.app.state.redis, user.org_id)
    await db.commit()
    return await _view(db, user.org_id, ch)


async def _group_row(db: AsyncSession, org_id: uuid.UUID, gid: uuid.UUID) -> Any:
    g = (await db.execute(text("""
        SELECT g.id, g.code, g.name, g.listen_mode, g.left_at, g.added_by_kind, c.type AS channel, c.id AS channel_id
        FROM core.groups g JOIN core.channels c ON c.id = g.channel_id WHERE g.id = :g AND g.org_id = :o"""),
                          {"g": gid, "o": org_id})).one_or_none()
    if g is None:
        raise ApiError(404, "GROUP_NOT_FOUND", "Không tìm thấy nhóm này")
    return g


async def _support_view(db: AsyncSession, org_id: uuid.UUID, g: Any) -> dict[str, Any]:
    cur = (await db.execute(text("""
        SELECT id, enabled_at, enabled_via, until_at, limits FROM ops.group_support
        WHERE group_id = :g AND disabled_at IS NULL AND (until_at IS NULL OR until_at > now())"""),
                            {"g": g.id})).one_or_none()
    hist = (await db.execute(text("""
        SELECT enabled_at, enabled_via, disabled_at, disabled_reason FROM ops.group_support
        WHERE group_id = :g AND disabled_at IS NOT NULL ORDER BY enabled_at DESC LIMIT 5"""), {"g": g.id})).all()
    sess = await active_session(db, org_id, g.channel)
    reasons: list[str] = []
    if sess is None or sess.account_kind != "bot":
        reasons.append("Kênh chưa chạy bằng tài khoản riêng của bot")
    if g.left_at is not None:
        reasons.append("Bot không còn trong nhóm")
    if g.listen_mode not in LISTENING_MODES:
        reasons.append("Nhóm chưa ở chế độ nghe")
    elif g.added_by_kind != "owner":
        approved = (await db.execute(text("""SELECT 1 FROM ops.owner_questions WHERE subject_id = :g AND answer = 'nghe'
                                             LIMIT 1"""), {"g": g.id})).first()
        if not approved:
            reasons.append("Nhóm không do Sếp thêm bot và Sếp chưa duyệt nghe")
    return {
        "group": {"id": str(g.id), "code": g.code, "name": g.name, "channel": g.channel, "listen_mode": g.listen_mode,
                  "added_by_kind": g.added_by_kind},
        "enabled": cur is not None,
        "since": iso(cur.enabled_at) if cur else None, "via": cur.enabled_via if cur else None,
        "until_at": iso(cur.until_at) if cur and cur.until_at else None,
        "limits": await support_limits(db, org_id, cur.limits if cur and isinstance(cur.limits, dict) else None),
        "can_enable": not reasons, "blocked_reasons": reasons,
        "history": [{"enabled_at": iso(h.enabled_at), "via": h.enabled_via, "disabled_at": iso(h.disabled_at),
                     "reason": h.disabled_reason} for h in hist],
    }


@router.get("/groups/{gid}/support")
async def get_support(gid: uuid.UUID, _m: service.CurrentUser = Depends(MANAGE),
                      user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await _support_view(db, user.org_id, await _group_row(db, user.org_id, gid))


@router.put("/groups/{gid}/support")
async def put_support(gid: uuid.UUID, body: SupportIn, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                      user: service.CurrentUser = Depends(require_owner),
                      _p: service.CurrentUser = Depends(require_pin("policy.change")),
                      db: AsyncSession = DB) -> dict[str, Any]:
    """Bật/tắt Chế độ hỗ trợ của nhóm. Bật đòi: tài khoản bot, bot còn trong nhóm, nhóm đang nghe, nhóm do Sếp thêm hoặc
    Sếp đã duyệt nghe. Tắt luôn được."""
    g = await _group_row(db, user.org_id, gid)
    if body.enabled:
        view = await _support_view(db, user.org_id, g)
        if not view["can_enable"]:
            raise ApiError(409, "SUPPORT_NOT_ALLOWED", "Chưa bật được chế độ hỗ trợ cho nhóm này",
                           detail="; ".join(view["blocked_reasons"]))
        if view["enabled"]:
            raise ApiError(409, "SUPPORT_ALREADY_ON", "Nhóm này đã bật chế độ hỗ trợ")
        base = await support_limits(db, user.org_id, None)
        limits = body.limits or {}
        if set(limits) - set(base) or any(v < 0 for v in limits.values()):
            raise field_errors({"limits": "Giới hạn không hợp lệ (khoá lạ hoặc số âm)"})
        until = datetime.now(UTC) + timedelta(hours=body.until_hours) if body.until_hours else None
        await oin.expire_stale_support(db, g.id)
        await db.execute(text("""
            INSERT INTO ops.group_support (org_id, group_id, enabled_by, enabled_via, until_at, limits)
            VALUES (:o, :g, :u, 'console', :t, CAST(:l AS jsonb))"""),
                         {"o": user.org_id, "g": g.id, "u": user.id, "t": until,
                          "l": json.dumps(limits)})
        await lev.record(db, user.org_id, "support_on", channel_id=g.channel_id, channel_type=g.channel,
                         group_id=g.id, detail={"via": "console", "hours": body.until_hours})
    else:
        await oin.disable_support(db, user.org_id, g.channel, reason="owner", group_id=g.id)
        await db.execute(text("""UPDATE ops.group_support SET disabled_by = :u
                                 WHERE group_id = :g AND disabled_reason = 'owner' AND disabled_by IS NULL
                                   AND disabled_at > now() - interval '1 minute'"""),
                         {"u": user.id, "g": g.id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="owner_channel.support_on" if body.enabled else "owner_channel.support_off",
                           target_type="group", target_id=str(g.id), target_label=g.name,
                           detail={"via": "console", "hours": body.until_hours}, ip=user.ip)
    await sync_listen_sets(db, request.app.state.redis, user.org_id)
    pending = lev.take_pending(db)
    await db.commit()
    await lev.publish_pending(_sm(), request.app.state.redis, user.org_id, pending)
    return await _support_view(db, user.org_id, g)


def _sm() -> Any:
    from gh.db import sessionmaker

    return sessionmaker()


@router.get("/auto-sends")
async def list_auto_sends(since: str = "24h", limit: int = 100, _m: service.CurrentUser = Depends(MANAGE),
                          user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Sổ tin bot tự gửi (mới nhất trước). KHÔNG có nội dung tin."""
    cutoff = since_cutoff(since)
    rows = (await db.execute(text("""
        SELECT a.id, a.created_at, a.basis, a.channel_type, a.thread_type, a.status, a.error, a.body_len, a.guard,
               a.group_id, g.name AS group_name, g.code AS group_code
        FROM ops.auto_sends a LEFT JOIN core.groups g ON g.id = a.group_id
        WHERE a.org_id = :o AND (CAST(:c AS timestamptz) IS NULL OR a.created_at >= :c)
        ORDER BY a.created_at DESC LIMIT :n"""), {"o": user.org_id, "c": cutoff, "n": max(1, min(limit, 500))})).all()
    today = (await db.execute(text("""
        SELECT basis, count(*) AS n FROM ops.auto_sends WHERE org_id = :o AND created_at >= date_trunc('day', now())
        GROUP BY basis"""), {"o": user.org_id})).all()
    counts = {r.basis: int(r.n) for r in today}
    escalated = (await db.execute(text("""
        SELECT count(*) FROM ops.listen_events WHERE org_id = :o AND kind = 'support_escalated'
          AND at >= date_trunc('day', now())"""), {"o": user.org_id})).scalar_one()
    return {
        "items": [{"id": str(r.id), "at": iso(r.created_at), "basis": r.basis, "basis_label": BASIS_LABEL[r.basis],
                   "channel": r.channel_type, "thread_type": r.thread_type, "status": r.status, "error": r.error,
                   "body_len": r.body_len, "rules": (r.guard or {}).get("rules", []),
                   "group": {"id": str(r.group_id), "code": r.group_code, "name": r.group_name} if r.group_id else None}
                  for r in rows],
        "today": {"owner_dm": counts.get("owner_dm", 0), "owner_tag": counts.get("owner_tag", 0),
                  "support": counts.get("support", 0), "escalated": int(escalated), "total": sum(counts.values())},
    }
