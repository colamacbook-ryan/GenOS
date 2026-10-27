# ruff: noqa: E501
"""Thẻ "Tình báo" trên Console (v0.1.76, gói T11, spec r6 §8.1 + §11 T11, CNS-077): các thao tác PIN của Tình báo.

Mọi route: CHỈ Owner (`require_owner`), lọc `org_id` của người gọi (id lạ/khác tổ chức ⇒ 404, không lộ có/không), và — trừ "khoá trợ lý"
(lệnh khẩn, không bắt PIN để không cản lúc nguy cấp) — cần phiên PIN (`intel.console`, 423 khi thiếu). Không route nào trả mã/PIN/băm/STK
đầy đủ; Nhật ký chỉ ghi nhãn, không ghi số. Việc "hạ phòng thủ" đi qua `strong_confirm.require(via="console")` (Nhật ký + báo kênh phụ).
Tái dùng, không nhân đôi: mute (`POST/DELETE /intel/mutes`, T1), trần `intel_*` (`PUT /system/ai-cost/intel`, L2), ghép kênh phụ
(`/notify/telegram*`, T10), nhãn bối cảnh một nhóm (`PUT /groups/{id}/contexts`, L1). Ở đây chỉ thêm phần còn thiếu.
"""

from __future__ import annotations

import math
import uuid
from datetime import timedelta
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require_owner, require_pin
from gh.biz.intel import access, levels, signals, strong_confirm
from gh.biz.intel.detectors import fraud
from gh.biz.intel.pipes import metrics
from gh.biz.mem import contexts
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, not_found
from gh.refinery import clock
from gh.telegram import backup

router = APIRouter(tags=["intel"])
PIN_OP = "intel.console"
MODES = ("live", "shadow")
RAMPS = ("digest_top", "push")
MAX_BULK_GROUPS = 100


async def _intel(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    v = (await db.execute(text("SELECT settings -> 'intel' FROM core.organizations WHERE id = :o"), {"o": org_id})).scalar_one_or_none()
    return v if isinstance(v, dict) else {}


async def _set(db: AsyncSession, org_id: uuid.UUID, patch: dict[str, Any]) -> None:
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || CAST(:p AS jsonb)) WHERE id = :o"""),
                     {"o": org_id, "p": orjson.dumps(patch).decode()})


async def _backup_paired(db: AsyncSession, org_id: uuid.UUID) -> bool:
    return await strong_confirm._backup_paired(db, org_id)


@router.get("/intel/console")
async def console_state(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Trạng thái cho thẻ "Tình báo": cách chạy, khoá trợ lý, mở khoá hôm nay, kênh phụ, mute đang bật, STK chờ xác minh, nhóm + nhãn."""
    org = user.org_id
    s = await _intel(db, org)
    now = clock.now()
    until = await strong_confirm.outbound_locked_until(db, org, now)
    mutes = (await db.execute(text("""SELECT id, scope_type, scope_value, source, until FROM biz.signal_mutes
                                      WHERE org_id = :o AND revoked_at IS NULL AND (until IS NULL OR until > now())
                                        AND scope_type = 'kind' ORDER BY created_at DESC LIMIT 50"""), {"o": org})).all()
    payees = (await db.execute(text(f"""SELECT a.id, a.title, a.facts ->> 'payee_last3' AS last3, a.facts ->> 'payee_bank' AS bank, a.created_at
                                        FROM biz.alerts a
                                        WHERE a.org_id = :o AND a.kind = 'rui_ro_lua_dao' AND a.status IN ('open', 'acknowledged')
                                          AND length(coalesce(a.facts ->> 'payee_digest', '')) = 64
                                          AND NOT EXISTS (SELECT 1 FROM biz.payment_identifiers p WHERE p.org_id = a.org_id
                                                          AND p.digest = a.facts ->> 'payee_digest' AND p.verified_at IS NOT NULL)
                                          AND {access.alert_visible_sql(user, 'a')}
                                        ORDER BY a.created_at DESC LIMIT 20"""), {"o": org})).all()  # noqa: S608
    groups = (await db.execute(text("SELECT id, name FROM core.groups WHERE org_id = :o ORDER BY name LIMIT 300"), {"o": org})).all()
    labels = (await db.execute(text("""SELECT group_id, context, is_primary, source FROM mem.group_contexts
                                       WHERE org_id = :o AND source = 'owner' ORDER BY is_primary DESC, context"""), {"o": org})).all()
    by_group: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for lb in labels:
        by_group.setdefault(lb.group_id, []).append({"context": lb.context, "is_primary": lb.is_primary})
    mode = "shadow" if s.get("mode") == "shadow" else "live"
    _s, installed = await signals.intel_settings(db, org)
    ramp = await ramp_state(db, org, s, installed, now)
    shadow_items = await shadow_state(db, user)
    return {
        "pin_active": user.pin_active(),
        "mode": mode,
        "ramp_gates": "push" if s.get("ramp_gates") == "push" and levels.REGULAR_PUSH_ENABLED else "digest_top",
        "regular_push_enabled": levels.REGULAR_PUSH_ENABLED,
        "assistant": {"locked": await backup.is_locked(db, org), "locked_at": s.get("assistant_locked_at"),
                      "locked_via": s.get("assistant_locked_via")},
        "outbound": {"unlocked_today": await strong_confirm.unlocked_today(db, org, now),
                     "locked_until": until.isoformat() if until else None},
        "backup_channel": {"paired": await _backup_paired(db, org), "kind": "telegram" if await _backup_paired(db, org) else "none"},
        "mutes": [{"id": str(m.id), "scope_value": m.scope_value, "source": m.source,
                   "until": m.until.isoformat() if m.until else None} for m in mutes],
        "payees": [{"signal_id": str(p.id), "title": p.title, "last3": p.last3, "bank": p.bank} for p in payees],
        "groups": [{"id": str(g.id), "name": g.name, "labels": by_group.get(g.id, [])} for g in groups],
        "contexts": list(contexts.CONTEXT_CODES),
        "ramp": ramp,
        "shadow_items": shadow_items,
    }


# ─── chạy thử 14 ngày (R4) + dòng bóng chờ Sếp chấm Đúng/Sai ──────────────────

RAMP_DAYS = 14
SHADOW_LIST_MAX = 12


async def ramp_state(db: AsyncSession, org_id: uuid.UUID, s: dict[str, Any], installed: Any, now: Any) -> dict[str, Any]:
    """Banner chạy thử (R4): bốn cổng thường chạy bóng 14 ngày kể từ mốc hiệu lực (cùng logic `signals.ramp_start/ramp_days`, có fallback ngày cài);
    M1 gộp n ≥ 10. `in_trial` = chế độ chạy thử (`mode=shadow`) HOẶC (Sếp chưa chọn nhắn ngay sớm VÀ (chưa đủ 14 ngày HOẶC chưa cổng nào qua M1)).
    `renewed` = chạy thử LẠI (mốc ramp muộn hơn ngày cài ≥ 1 ngày) — tổ chức cài mới thì chỉ là "chạy thử". Chỉ số + mã, không chữ tin."""
    start = signals.ramp_start(s, installed, org_id)
    m = await metrics.m1_ramp(db, org_id)
    passed = [g for g in s.get("m1_passed_gates") or [] if g in levels.REGULAR_GATES] if isinstance(s.get("m1_passed_gates"), list) else []
    days = signals.ramp_days(s, installed, now, org_id)
    early = levels.REGULAR_PUSH_ENABLED and s.get("ramp_gates") == "push"
    in_trial = s.get("mode") == "shadow" or (not early and (days < RAMP_DAYS or not passed))
    first = signals._aware(installed)
    renewed = start is not None and start - first >= timedelta(days=1)
    return {"started_at": start.isoformat() if start else None, "days": RAMP_DAYS,
            "ends_at": (start + timedelta(days=RAMP_DAYS)).isoformat() if start else None,
            "days_left": max(0, math.ceil(RAMP_DAYS - days)), "red_cap_per_day": signals._int_setting(s, "red_cap_per_day", 3, org_id), "in_trial": in_trial, "renewed": renewed, "early_push": early, "passed_gates": passed,
            "m1": {"n": m["pooled"]["n"], "k": m["pooled"]["k"], "p": m["pooled"]["p"], "lo95": m["pooled"]["lo95"], "ok": m["pooled"]["m1_ok"],
                   "min_n": metrics.M1_MIN_N, "wrong_by_reason": m["wrong_by_reason"], "low_precision_gates": m["low_precision_gates"]}}


async def shadow_state(db: AsyncSession, user: service.CurrentUser) -> list[dict[str, Any]]:
    """Dòng bóng ("đáng lẽ báo ngay") của bốn cổng thường đang mở — Sếp chấm Đúng/Sai (Sai kèm 1 trong 3 lý do). Cùng cổng đọc với `/signals`
    (`access.alert_visible_sql`: `console_pin`/nhóm ẩn không lộ) và lọc `org_id` của người gọi."""
    rows = (await db.execute(text(f"""SELECT a.id, a.kind, a.gate, a.title, a.reasons, a.would_push_at, a.feedback_verdict
                                      FROM biz.alerts a
                                      WHERE a.org_id = :o AND a.status IN ('open', 'acknowledged') AND a.would_push_at IS NOT NULL
                                        AND a.pushed_at IS NULL
                                        AND a.level = 'red' AND a.gate = ANY(:g) AND a.kind NOT IN ('system', 'legacy')
                                        AND {access.alert_visible_sql(user, 'a')}
                                      ORDER BY a.would_push_at DESC LIMIT :n"""), {"o": user.org_id, "g": list(levels.REGULAR_GATES),  # noqa: S608
                                                                                   "n": SHADOW_LIST_MAX})).all()
    return [{"id": str(r.id), "kind": r.kind, "gate": r.gate, "title": r.title, "reasons": list(r.reasons or []),
             "would_push_at": r.would_push_at.isoformat() if r.would_push_at else None, "verdict": r.feedback_verdict} for r in rows]


# ─── khoá / mở khoá trợ lý ───────────────────────────────────────────────────

@router.post("/intel/lock-assistant")
async def lock_assistant(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Lệnh khẩn của Sếp — KHÔNG bắt PIN (khoá là chiều an toàn; mở khoá mới cần PIN). Chạy lại khi đã khoá ⇒ `already: true`."""
    res = await backup.lock_assistant(db, user.org_id, via="console", user_id=user.id)
    if not res.ok:
        raise ApiError(403, "NOT_OWNER", "Chỉ Sếp mới khoá được trợ lý")
    return {"ok": True, "locked": True, "already": res.already}


@router.post("/intel/unlock-assistant")
async def unlock_assistant(user: service.CurrentUser = Depends(require_owner), _p: service.CurrentUser = Depends(require_pin(PIN_OP)),
                           db: AsyncSession = DB) -> dict[str, Any]:
    """Mở khoá trợ lý (Console + PIN). Chỉ xoá cờ khoá — KHÔNG tự bật lại kênh quản trị / hỗ trợ nhóm (Sếp bật lại có chủ ý).
    Idempotent: không khoá ⇒ `already: true`, không ghi thêm. Báo kênh phụ mỗi lần mở khoá thật."""
    res = await backup.unlock_assistant(db, user.org_id, user_id=user.id)      # kiểm-và-xoá trong cùng khoá tổ chức
    if not res.ok:
        raise ApiError(403, "NOT_OWNER", "Chỉ Sếp mới mở khoá được trợ lý")
    if res.already:
        return {"ok": True, "locked": False, "already": True}
    await backup.notify(db, user.org_id, "Sếp vừa MỞ KHOÁ trợ lý trên Console (đã nhập mã PIN). Kênh quản trị và chế độ hỗ trợ nhóm vẫn tắt "
                                         "cho tới khi Sếp bật lại. Nếu không phải Sếp: «khoá trợ lý» ngay.",
                        kind="security", dedupe_key=f"assistant.unlock:{uuid.uuid4().hex}")
    return {"ok": True, "locked": False, "already": False}


@router.post("/intel/unlock-today")
async def unlock_today(user: service.CurrentUser = Depends(require_owner), _p: service.CurrentUser = Depends(require_pin(PIN_OP)),
                       db: AsyncSession = DB) -> dict[str, Any]:
    """«Mở khoá hôm nay» (Console + PIN): trong ngày VN này «OK #MÃ» gửi tin cho khách không cần mã kép. Không gỡ khoá bất thường 1 giờ.
    Idempotent: đã mở hôm nay ⇒ `already: true`, không ghi/báo thêm."""
    await strong_confirm._serialize(db, user.org_id)         # cùng khoá với khoá/mở khoá trợ lý
    if await backup.is_locked(db, user.org_id):
        raise ApiError(409, "ASSISTANT_LOCKED", "Trợ lý đang bị khoá — mở khoá trợ lý trước")
    if await strong_confirm.unlocked_today(db, user.org_id):
        return {"ok": True, "day": strong_confirm.vn_day(), "already": True}
    if not await strong_confirm.unlock_today(db, user.org_id, user_id=user.id, pin_verified=True):
        raise ApiError(403, "NOT_OWNER", "Chỉ Sếp mới mở khoá được")
    return {"ok": True, "day": strong_confirm.vn_day(), "already": False}


# ─── cách chạy tình báo ──────────────────────────────────────────────────────

class SettingsIn(BaseModel):
    mode: Literal["live", "shadow"] | None = None
    ramp_gates: Literal["digest_top", "push"] | None = None


@router.put("/intel/settings")
async def put_settings(body: SettingsIn, user: service.CurrentUser = Depends(require_owner),
                       _p: service.CurrentUser = Depends(require_pin(PIN_OP)), db: AsyncSession = DB) -> dict[str, Any]:
    """Đổi `intel.mode` (live | shadow) và/hoặc `intel.ramp_gates` (digest_top | push). Mọi chiều đều Console + PIN; `live → shadow` là hạ phòng thủ
    nên đi qua `strong_confirm` (Nhật ký + báo kênh phụ). Chỉ khoá gửi lên mới đổi."""
    if body.mode is None and body.ramp_gates is None:
        raise ApiError(422, "VALIDATION", "Chưa chọn gì để đổi")
    if body.ramp_gates == "push" and not levels.REGULAR_PUSH_ENABLED:
        raise ApiError(422, "VALIDATION", "Bốn cổng thường đang chỉ lên đầu bản tin — chưa bật được «Báo ngay»",
                       detail="ramp_gates=push đang bị khoá (levels.REGULAR_PUSH_ENABLED=False); lừa đảo và an toàn vẫn báo ngay")
    cur = await _intel(db, user.org_id)
    patch: dict[str, Any] = {}
    if body.mode is not None and body.mode != ("shadow" if cur.get("mode") == "shadow" else "live"):
        if body.mode == "shadow":
            ok = await strong_confirm.require(db, user.org_id, "intel_mode_shadow", via="console", pin_verified=True)
            if not isinstance(ok, strong_confirm.Allowed):
                raise ApiError(403, "STRONG_CONFIRM", "Chưa được phép đổi sang chạy thử")
        patch["mode"] = body.mode
    if body.ramp_gates is not None and body.ramp_gates != ("push" if cur.get("ramp_gates") == "push" else "digest_top"):
        patch["ramp_gates"] = body.ramp_gates
    if patch:
        await _set(db, user.org_id, patch)
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="intel.settings.changed",
                               target_type="settings", target_id="intel", result="ok", detail=patch, ip=user.ip)
    return {"ok": True, "changed": sorted(patch), "mode": patch.get("mode", "shadow" if cur.get("mode") == "shadow" else "live"),
            "ramp_gates": patch.get("ramp_gates", "push" if cur.get("ramp_gates") == "push" else "digest_top")}


# ─── xác minh STK ────────────────────────────────────────────────────────────

async def _verified(db: AsyncSession, user: service.CurrentUser, signal_id: uuid.UUID | None, identifier_id: uuid.UUID | None) -> dict[str, Any]:
    try:
        if signal_id is not None:
            closed = await fraud.verify_signal_payee(db, user.org_id, signal_id, user_id=user.id, pin_verified=True, now=clock.now())
        else:
            assert identifier_id is not None
            closed = await fraud.verify_identifier(db, user.org_id, identifier_id, user_id=user.id, pin_verified=True, now=clock.now())
    except fraud.FraudError:
        raise not_found("Số tài khoản") from None
    await strong_confirm.require(db, user.org_id, "payment_identifier_verify", via="console", pin_verified=True)     # báo kênh phụ
    return {"ok": True, "closed": len(closed)}


@router.post("/intel/signals/{signal_id}/verify-payee")
async def verify_signal_payee(signal_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner),
                              _p: service.CurrentUser = Depends(require_pin(PIN_OP)), db: AsyncSession = DB) -> dict[str, Any]:
    """Sếp xác minh STK trong MỘT tín hiệu lừa đảo (T7 `verify_signal_payee`): đóng các tín hiệu cùng STK. Tín hiệu lạ/khác tổ chức ⇒ 404."""
    await access.require_alert(db, user, signal_id)
    return await _verified(db, user, signal_id, None)


@router.post("/intel/payment-identifiers/{identifier_id}/verify")
async def verify_identifier(identifier_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner),
                            _p: service.CurrentUser = Depends(require_pin(PIN_OP)), db: AsyncSession = DB) -> dict[str, Any]:
    """Sếp xác minh một STK đã có trong lịch sử (T7 `verify_identifier`). Id lạ/khác tổ chức ⇒ 404."""
    return await _verified(db, user, None, identifier_id)


# ─── gán bối cảnh nhóm hàng loạt ─────────────────────────────────────────────

class LabelIn(BaseModel):
    context: str
    is_primary: bool = False


class BulkContextsIn(BaseModel):
    group_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_BULK_GROUPS)
    labels: list[LabelIn] = Field(min_length=1, max_length=len(contexts.CONTEXT_CODES))


@router.post("/intel/group-contexts/bulk")
async def bulk_group_contexts(body: BulkContextsIn, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Gán CÙNG bộ nhãn bối cảnh cho nhiều nhóm (như `PUT /groups/{id}/contexts` nhưng một lần). Cả lô hoặc không gì: có nhóm lạ/khác
    tổ chức ⇒ 404 và không nhóm nào đổi. Không cần PIN (như đặt nhãn từng nhóm); mỗi nhóm một dòng Nhật ký."""
    ids = list(dict.fromkeys(body.group_ids))
    found = (await db.execute(text("SELECT id, name FROM core.groups WHERE org_id = :o AND id = ANY(:i)"), {"o": user.org_id, "i": ids})).all()
    if len(found) != len(ids):
        raise not_found("Nhóm")
    labels = [x.model_dump() for x in body.labels]
    try:
        for g in found:
            await contexts.set_group_contexts(db, user.org_id, g.id, labels, user_id=user.id)
    except ValueError as e:
        raise ApiError(422, "VALIDATION", "Nhãn bối cảnh chưa hợp lệ", detail=str(e)) from e
    for g in found:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="mem.group_contexts_set",
                               target_type="group", target_id=str(g.id), target_label=g.name, detail={"labels": [x.context for x in body.labels], "bulk": True},
                               ip=user.ip)
    return {"ok": True, "updated": len(found)}


# ─── Thiết lập Tình báo (Ryan chốt D3, 2026-10-25: "hỏi lúc cài") ──────────────

MAX_SETUP_GROUPS = 300
MAX_SETUP_PEOPLE = 200
#: Loại cần biết khách / nhân sự của Sếp: chỉ chạy đầy đủ khi nhóm có nhãn bối cảnh và Sếp đã khai nhân viên. Lừa đảo, an toàn luôn chạy.
SETUP_GATED_VI = ("lời hứa với khách", "câu hỏi chờ trả lời", "khách chê / doạ bỏ", "cơ hội", "thông tin thị trường", "quan hệ cần để ý")


@router.get("/intel/setup")
async def setup_state(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Ba bước "Thiết lập Tình báo": (3, v0.1.77 R2) khách chủ chốt do Sếp khai (`key_customers`; trợ lý chỉ gợi ý tên); (1) nhóm còn chưa có nhãn bối cảnh do Sếp gán — nhãn mở các loại theo bối cảnh nhóm (khởi động lạnh
    T12/T13 dùng nhãn kinh_doanh/nhan_vien); (2) khai nhân viên (`PATCH /profile/{id}` `person_type=staff`, Owner + PIN) rồi bấm "Đã khai xong"
    (`POST /intel/setup/staff-confirmed`) — danh sách nhân viên giúp phân biệt khách/nhân viên chính xác hơn; cờ "đã khai xong" chỉ để nhắc,
    không bật/tắt loại nào. Không chặn app; lừa đảo/an toàn luôn chạy. Chỉ đọc, không cần PIN."""
    org = user.org_id
    s = await _intel(db, org)
    groups = (await db.execute(text("""
        SELECT g.id, g.name, g.member_count
        FROM core.groups g WHERE g.org_id = :o AND g.left_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM mem.group_contexts c WHERE c.org_id = g.org_id AND c.group_id = g.id AND c.source = 'owner')
        ORDER BY g.name, g.id LIMIT :n"""), {"o": org, "n": MAX_SETUP_GROUPS})).all()
    unlabeled = list(groups)                                  # tối đa MAX_SETUP_GROUPS dòng để gán nhanh
    # Đếm KHÔNG qua LIMIT (review D3-b); nhóm Sếp đã rời (`left_at`) không tính.
    cnt = (await db.execute(text("""
        SELECT count(*) AS total, count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM mem.group_contexts c WHERE c.org_id = g.org_id
                                                   AND c.group_id = g.id AND c.source = 'owner')) AS unlabeled
        FROM core.groups g WHERE g.org_id = :o AND g.left_at IS NULL"""), {"o": org})).one()
    people = (await db.execute(text("""
        SELECT p.id, p.display_name, p.person_type, p.attrs -> 'before_staff' ->> 'type' AS prev_type,
               (p.person_type = 'staff' AND COALESCE(p.attrs ->> 'staff_marked_at', '') = '') AS needs_reconfirm FROM core.persons p
        WHERE p.org_id = :o AND p.deleted_at IS NULL AND p.merged_into_id IS NULL
          AND COALESCE(p.attrs ->> 'is_owner', '') <> 'true'
          AND NOT EXISTS (SELECT 1 FROM core.users u WHERE u.org_id = p.org_id AND u.person_id = p.id)
          AND (p.person_type = 'staff' OR EXISTS (SELECT 1 FROM core.group_members gm JOIN core.groups g ON g.id = gm.group_id
                                                    WHERE gm.person_id = p.id AND gm.left_at IS NULL AND g.org_id = p.org_id))
        ORDER BY p.display_name, p.id LIMIT :n"""), {"o": org, "n": MAX_SETUP_PEOPLE})).all()   # thứ tự ổn định: bật/tắt không làm dòng nhảy
    staff_n = (await db.execute(text("""SELECT count(*) FROM core.persons WHERE org_id = :o AND person_type = 'staff'
                                        AND deleted_at IS NULL AND merged_into_id IS NULL"""), {"o": org})).scalar_one()
    confirmed_at = s.get("staff_confirmed_at") if isinstance(s.get("staff_confirmed_at"), str) else None
    groups_done = int(cnt.unlabeled) == 0
    staff_done = confirmed_at is not None
    key = await key_customers_state(db, org, s)
    return {
        "ready": groups_done and staff_done and key["done"],
        "groups": {"done": groups_done, "total": int(cnt.total), "unlabeled_count": int(cnt.unlabeled),
                   "unlabeled": [{"id": str(g.id), "name": g.name, "member_count": g.member_count} for g in unlabeled]},
        "staff": {"done": staff_done, "confirmed_at": confirmed_at, "staff_count": int(staff_n),
                  "people": [{"id": str(p.id), "name": p.display_name, "person_type": p.person_type or "unknown",
                              "prev_type": p.prev_type, "needs_reconfirm": bool(p.needs_reconfirm)} for p in people]},
        "key_customers": key,
        "contexts": list(contexts.CONTEXT_CODES),
        "gated_kinds": list(SETUP_GATED_VI),
    }


MAX_SETUP_CUSTOMERS = 200


async def key_customers_state(db: AsyncSession, org_id: uuid.UUID, s: dict[str, Any]) -> dict[str, Any]:
    """Bước 3 "Khách chủ chốt" (R2): Sếp KHAI, trợ lý chỉ GỢI Ý tên (`owner_key_relation` `proposed` ⇒ `suggested`, KHÔNG dùng chấm mức). Danh
    sách = khách (`person_type = customer`) của tổ chức, kèm `entity_id` để ghi qua API Hồ sơ hiện có (`POST /me/profile/items`, Owner + mã PIN) và
    `fact_id` của mục đã xác nhận để bỏ đánh dấu (`DELETE /me/profile/items/{id}`). Chỉ đọc."""
    rows = (await db.execute(text("""
        SELECT p.id, p.display_name, e.id AS entity_id,
               (SELECT v.fact_id FROM mem.v_owner_profile v WHERE v.org_id = p.org_id AND v.predicate = 'owner_key_relation'
                  AND v.object_entity_id = e.id AND v.status = 'owner_confirmed' ORDER BY v.valid_from DESC LIMIT 1) AS key_fact_id,
               EXISTS (SELECT 1 FROM mem.v_owner_profile v WHERE v.org_id = p.org_id AND v.predicate = 'owner_key_relation'
                  AND v.object_entity_id = e.id AND v.status = 'proposed') AS suggested
        FROM core.persons p JOIN mem.entities e ON e.person_id = p.id AND e.org_id = p.org_id AND e.merged_into IS NULL AND e.forgotten_at IS NULL
        WHERE p.org_id = :o AND p.person_type = 'customer' AND p.deleted_at IS NULL AND p.merged_into_id IS NULL
        ORDER BY (SELECT count(*) FROM mem.v_owner_profile v WHERE v.org_id = p.org_id AND v.predicate = 'owner_key_relation'
                    AND v.object_entity_id = e.id AND v.status = 'owner_confirmed') DESC, suggested DESC, p.display_name, p.id LIMIT :n"""),
                              {"o": org_id, "n": MAX_SETUP_CUSTOMERS})).all()
    confirmed_at = s.get("key_customers_confirmed_at") if isinstance(s.get("key_customers_confirmed_at"), str) else None
    key_n = sum(1 for r in rows if r.key_fact_id is not None)
    return {"done": confirmed_at is not None, "confirmed_at": confirmed_at, "key_count": key_n,
            "customers": [{"id": str(r.id), "entity_id": str(r.entity_id), "name": r.display_name, "key_fact_id": str(r.key_fact_id) if r.key_fact_id else None,
                           "suggested": bool(r.suggested)} for r in rows]}


@router.post("/intel/setup/staff-confirmed")
async def setup_staff_confirmed(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Sếp xác nhận "đã khai xong nhân viên" (kể cả không có nhân viên nào). Ghi Nhật ký (số người đã đánh dấu, không tên). Không cần PIN —
    việc này chỉ MỞ thêm suy luận thận trọng, không hạ phòng thủ."""
    org = user.org_id
    now = clock.now()
    n = (await db.execute(text("""SELECT count(*) FROM core.persons WHERE org_id = :o AND person_type = 'staff'
                                  AND deleted_at IS NULL AND merged_into_id IS NULL"""), {"o": org})).scalar_one()
    await _set(db, org, {"staff_confirmed_at": now.isoformat()})
    await actionlog.record(db, org_id=org, actor_type="user", actor_id=user.actor_id, action="intel.setup.staff_confirmed",
                           target_type="org", target_id=str(org), result="ok", detail={"staff_count": int(n)}, ip=user.ip)
    return {"ok": True, "confirmed_at": now.isoformat(), "staff_count": int(n)}


@router.post("/intel/setup/key-customers-confirmed")
async def setup_key_customers_confirmed(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Sếp xác nhận "đã khai xong khách chủ chốt" (kể cả không có ai). Việc ĐÁNH DẤU từng khách đi qua API Hồ sơ (Owner + PIN); nút này chỉ ghi cờ
    "đã khai" để nhắc, không bật/tắt loại nào. Nhật ký chỉ ghi số lượng, không ghi tên."""
    org = user.org_id
    now = clock.now()
    n = (await key_customers_state(db, org, await _intel(db, org)))["key_count"]
    await _set(db, org, {"key_customers_confirmed_at": now.isoformat()})
    await actionlog.record(db, org_id=org, actor_type="user", actor_id=user.actor_id, action="intel.setup.key_customers_confirmed",
                           target_type="org", target_id=str(org), result="ok", detail={"key_count": n}, ip=user.ip)
    return {"ok": True, "confirmed_at": now.isoformat(), "key_count": n}
