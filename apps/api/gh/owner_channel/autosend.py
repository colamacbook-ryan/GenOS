"""CỬA DUY NHẤT để bot tự gửi tin, không qua bản nháp + PIN (CLAUDE.md › Luật cứng, Sếp duyệt 18/10/2026).

Đúng ba cơ sở (`Basis`): `owner_dm` (kênh quản trị 1-1 với Sếp), `owner_tag` (Sếp tag bot trong nhóm),
`support` (nhóm Sếp
đã bật Chế độ hỗ trợ). `send_auto` KHÔNG tin người gọi: trong một transaction nó tự đọc lại từ DB/Redis mọi
điều kiện, chạy
bộ lọc an toàn (`guard.check`) rồi mới ghi sổ `ops.auto_sends` + Nhật ký hành động, COMMIT, và chỉ khi đó mới ký permit
(không có `draft_id`, có `basis`) và đẩy lệnh gửi sang bridge. Bridge kiểm lại `basis` (lớp hai).

Mọi nhánh từ chối trả `AutoSendResult(ok=False, code=…)` và KHÔNG phát gì lên luồng gửi. Module này và
`gh.biz.core.drafts` là HAI nơi duy nhất được phát lên `gh.bridge.outbound` / ký permit (test AST).
"""

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.core.drafts import bridge_permit
from gh.chassis import actionlog, policy
from gh.chassis.bus import BRIDGE_OUTBOUND, EventBus
from gh.data.common import LISTENING_MODES, org_settings
from gh.owner_channel import guard
from gh.owner_channel.basis import Basis, Trigger
from gh.owner_channel.identity import CHANNELS, active_session, is_owner, owner_ids

log = logging.getLogger("gh.owner_channel.autosend")

OWNER_TRIGGER_MAX_AGE = timedelta(minutes=10)
DEFAULT_SUPPORT_LIMITS: dict[str, int] = {"per_group_hour": 20, "per_member_hour": 5, "min_gap_s": 20,
                                          "per_channel_day": 150, "max_len": 600, "max_age_s": 300}
ACTOR = "system:autosend"


@dataclass(frozen=True)
class AutoSendResult:
    ok: bool
    code: str | None = None
    auto_send_id: uuid.UUID | None = None
    rules: tuple[str, ...] = ()


@dataclass
class _Plan:
    thread_id: str
    thread_type: str
    session_id: uuid.UUID
    group_id: uuid.UUID | None = None
    ctx: guard.GuardCtx | None = None


class _Refuse(Exception):
    def __init__(self, code: str, rules: tuple[str, ...] = ()):
        super().__init__(code)
        self.code, self.rules = code, rules


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def support_limits(db: AsyncSession, org_id: uuid.UUID, override: dict[str, Any] | None) -> dict[str, int]:
    out = dict(DEFAULT_SUPPORT_LIMITS)
    for src in ((await org_settings(db, org_id)).get("support_limits"), override):
        if isinstance(src, dict):
            for k in out:
                v = src.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    out[k] = v
    return out


async def _group(db: AsyncSession, org_id: uuid.UUID, channel: str, group_id: uuid.UUID | None) -> Any:
    if group_id is None:
        raise _Refuse("GROUP_REQUIRED")
    g = (await db.execute(sql("""
        SELECT g.id, g.external_id, g.listen_mode, g.left_at
        FROM core.groups g JOIN core.channels c ON c.id = g.channel_id
        WHERE g.id = :g AND g.org_id = :o AND c.type = :t"""),
                            {"g": group_id, "o": org_id, "t": channel})).one_or_none()
    if g is None:
        raise _Refuse("GROUP_NOT_FOUND")
    if g.left_at is not None:
        raise _Refuse("GROUP_LEFT")
    if g.listen_mode not in LISTENING_MODES:
        raise _Refuse("GROUP_NOT_LISTENING")
    return g


async def _event(db: AsyncSession, org_id: uuid.UUID, trigger: Trigger, group: Any, now: datetime,
                 max_age: timedelta) -> Any:
    if trigger.kind != "raw_event":
        raise _Refuse("BAD_TRIGGER")
    eid = _uuid(trigger.ref)
    if eid is None:
        raise _Refuse("BAD_TRIGGER")
    e = (await db.execute(sql("""
        SELECT e.id, e.direction, e.mentions_agent, e.group_id, e.received_at, e.sender_identity_id, e.payload,
               i.external_id AS sender_ext, i.person_id
        FROM raw.events e LEFT JOIN core.person_identities i ON i.id = e.sender_identity_id
        WHERE e.id = :i AND e.org_id = :o"""), {"i": eid, "o": org_id})).one_or_none()
    if e is None:
        raise _Refuse("TRIGGER_NOT_FOUND")
    if e.direction != "inbound" or e.group_id != group.id:
        raise _Refuse("TRIGGER_MISMATCH")
    if now - e.received_at > max_age:
        raise _Refuse("TRIGGER_TOO_OLD")
    return e


async def _guard_ctx(db: AsyncSession, org_id: uuid.UUID, basis: Basis, group: Any, asker_person: uuid.UUID | None,
                     canary: str, max_len: int | None) -> guard.GuardCtx:
    """Ngữ cảnh bộ lọc: tên/số điện thoại của người KHÔNG thuộc nhóm này (đối chiếu hồ sơ trong Kho)."""
    rows = (await db.execute(sql("""
        SELECT p.display_name, array_remove(array_agg(DISTINCT i.phone_e164), NULL) AS phones
        FROM core.persons p LEFT JOIN core.person_identities i ON i.person_id = p.id
        WHERE p.org_id = :o AND p.merged_into_id IS NULL AND p.id NOT IN (
            SELECT person_id FROM core.group_members WHERE group_id = :g AND left_at IS NULL)
        GROUP BY p.id, p.display_name LIMIT 5000"""), {"o": org_id, "g": group.id})).all()
    names: set[str] = set()
    phones: set[str] = set()
    for r in rows:
        n = guard._norm(r.display_name or "")
        if len(n.split()) >= 2:
            names.add(n)
        for ph in r.phones or []:
            d = "".join(ch for ch in ph if ch.isdigit())
            if len(d) >= 9:
                phones.add(d)
                if d.startswith("84"):
                    phones.add("0" + d[2:])
    allowed: set[str] = set()
    if asker_person is not None:
        dn = (await db.execute(sql("SELECT display_name FROM core.persons WHERE id = :p"),
                               {"p": asker_person})).scalar_one_or_none()
        allowed = {t for t in guard._norm(dn or "").split() if t} | ({guard._norm(dn)} if dn else set())
    return guard.GuardCtx(basis=basis, known_other_names=frozenset(names), known_other_phones=frozenset(phones),
                          allowed_mentions=frozenset(allowed), canary=canary, max_len=max_len)


async def _plan_owner_dm(db: AsyncSession, redis: Redis, org_id: uuid.UUID, channel: str, oi: Any, sess: Any,
                         trigger: Trigger, group_id: uuid.UUID | None, now: datetime) -> _Plan:
    if group_id is not None:
        raise _Refuse("GROUP_NOT_ALLOWED")
    if not oi.enabled or not oi.thread_id or oi.session_id != sess.id:
        raise _Refuse("OWNER_CHANNEL_OFF")
    if trigger.kind == "owner_message":
        if not trigger.ref.startswith(f"{channel}:"):
            raise _Refuse("BAD_TRIGGER")
        stamp = await redis.get(f"gh:owner:dm:{trigger.ref}")
        if stamp is None:
            raise _Refuse("TRIGGER_NOT_FOUND")
        try:
            sent_at = datetime.fromtimestamp(int(stamp), UTC)
        except ValueError:
            raise _Refuse("TRIGGER_NOT_FOUND") from None
        if now - sent_at > OWNER_TRIGGER_MAX_AGE:
            raise _Refuse("TRIGGER_TOO_OLD")
    elif trigger.kind == "report":
        oid = _uuid(trigger.ref)
        ok = oid is not None and (await db.execute(sql("""
            SELECT 1 FROM ops.owner_outbox WHERE id = :i AND org_id = :o AND channel_type = :c
              AND sent_at IS NULL AND failed_code IS NULL"""), {"i": oid, "o": org_id, "c": channel})).first()
        if not ok:
            raise _Refuse("TRIGGER_NOT_FOUND")
    else:
        raise _Refuse("BAD_TRIGGER")
    return _Plan(oi.thread_id, "user", sess.id)


async def _plan_owner_tag(db: AsyncSession, org_id: uuid.UUID, channel: str, oi: Any, sess: Any, trigger: Trigger,
                          group_id: uuid.UUID | None, now: datetime, canary: str) -> _Plan:
    if not oi.enabled or oi.session_id != sess.id:
        raise _Refuse("OWNER_CHANNEL_OFF")
    g = await _group(db, org_id, channel, group_id)
    e = await _event(db, org_id, trigger, g, now, OWNER_TRIGGER_MAX_AGE)
    if not e.mentions_agent:
        raise _Refuse("NOT_TAGGED")
    if not is_owner(oi, e.sender_ext):
        raise _Refuse("SENDER_NOT_OWNER")
    return _Plan(g.external_id, "group", sess.id, g.id,
                 await _guard_ctx(db, org_id, Basis.OWNER_TAG, g, None, canary, None))


async def _plan_support(db: AsyncSession, org_id: uuid.UUID, channel: str, oi: Any, sess: Any, trigger: Trigger,
                        group_id: uuid.UUID | None, now: datetime, canary: str) -> tuple[_Plan, dict[str, int], Any]:
    g = await _group(db, org_id, channel, group_id)
    sup = (await db.execute(sql("""
        SELECT id, limits FROM ops.group_support WHERE group_id = :g AND org_id = :o AND disabled_at IS NULL
          AND (until_at IS NULL OR until_at > :now)"""), {"g": g.id, "o": org_id, "now": now})).one_or_none()
    if sup is None:
        raise _Refuse("SUPPORT_OFF")
    lim = await support_limits(db, org_id, sup.limits if isinstance(sup.limits, dict) else None)
    e = await _event(db, org_id, trigger, g, now, timedelta(seconds=lim["max_age_s"]))
    if is_owner(oi, e.sender_ext):
        raise _Refuse("SENDER_IS_OWNER")
    replies = isinstance(e.payload, dict) and e.payload.get("replies_to_self") is True
    if not (e.mentions_agent or replies):
        raise _Refuse("NOT_ADDRESSED")
    ctx = await _guard_ctx(db, org_id, Basis.SUPPORT, g, e.person_id, canary, lim["max_len"])
    return _Plan(g.external_id, "group", sess.id, g.id, ctx), lim, e


async def _check_limits(db: AsyncSession, org_id: uuid.UUID, channel: str, plan: _Plan, lim: dict[str, int], e: Any,
                        now: datetime) -> None:
    hour, day = now - timedelta(hours=1), now - timedelta(hours=24)
    last = (await db.execute(sql("""
        SELECT max(created_at) FROM ops.auto_sends WHERE org_id = :o AND basis = 'support' AND group_id = :g"""),
                             {"o": org_id, "g": plan.group_id})).scalar_one()
    if last is not None and lim["min_gap_s"] and (now - last).total_seconds() < lim["min_gap_s"]:
        raise _Refuse("RATE_GAP")
    member = (await db.execute(sql("""
        SELECT count(*) FROM ops.auto_sends a JOIN raw.events r ON r.id::text = a.trigger_ref AND r.org_id = a.org_id
        WHERE a.org_id = :o AND a.basis = 'support' AND a.trigger_kind = 'raw_event' AND a.created_at > :h
          AND r.sender_identity_id = :s"""), {"o": org_id, "h": hour, "s": e.sender_identity_id})).scalar_one()
    if int(member) >= lim["per_member_hour"]:
        raise _Refuse("RATE_MEMBER")
    group = (await db.execute(sql("""
        SELECT count(*) FROM ops.auto_sends WHERE org_id = :o AND basis = 'support' AND group_id = :g
          AND created_at > :h"""), {"o": org_id, "g": plan.group_id, "h": hour})).scalar_one()
    if int(group) >= lim["per_group_hour"]:
        raise _Refuse("RATE_GROUP")
    chan = (await db.execute(sql("""
        SELECT count(*) FROM ops.auto_sends WHERE org_id = :o AND basis = 'support' AND channel_type = :c
          AND created_at > :d"""), {"o": org_id, "c": channel, "d": day})).scalar_one()
    if int(chan) >= lim["per_channel_day"]:
        raise _Refuse("RATE_CHANNEL_DAY")


async def _refused(db: AsyncSession, org_id: uuid.UUID, channel: str, basis: str, trigger: Trigger,
                   err: _Refuse) -> AutoSendResult:
    """Ghi Nhật ký hành động (mã từ chối + mã luật, KHÔNG nội dung) rồi trả kết quả từ chối. Không đụng sổ tự gửi."""
    try:
        async with db.begin_nested():
            await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=ACTOR, action="autosend.refused",
                                   target_type="auto_send", target_id=trigger.ref[:80], result="blocked",
                                   detail={"basis": basis, "channel": channel, "code": err.code,
                                           "rules": list(err.rules), "trigger_kind": trigger.kind})
        await db.commit()
    except Exception:  # noqa: BLE001 — ghi nhật ký từ chối là phụ; từ chối vẫn có hiệu lực
        log.warning("không ghi được autosend.refused", exc_info=True)
        await db.rollback()
    return AutoSendResult(False, err.code, None, err.rules)


async def send_auto(db: AsyncSession, bus: EventBus | Any, redis: Redis, *, org_id: uuid.UUID, channel: str,
                    basis: Basis, trigger: Trigger, text: str, group_id: uuid.UUID | None = None, canary: str = "",
                    now: datetime | None = None) -> AutoSendResult:
    """Tự gửi MỘT tin theo một trong ba cơ sở. Hàm COMMIT `db` trước khi phát (sổ phải có trước khi tin ra).
    Xem docstring
    module. `text` là bản ĐÃ CHỐT — hàm không sửa; bộ lọc chỉ cho qua hoặc chặn."""
    now = now or datetime.now(UTC)
    try:
        basis = Basis(basis)
    except ValueError:
        return AutoSendResult(False, "INVALID_BASIS")
    if channel not in CHANNELS:
        return AutoSendResult(False, "CHANNEL_NOT_SUPPORTED")
    try:
        await db.execute(sql("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"autosend:{org_id}:{channel}"})
        sess = await active_session(db, org_id, channel)
        if sess is None:
            raise _Refuse("SESSION_NOT_ACTIVE")
        if sess.account_kind != "bot":
            raise _Refuse("NOT_BOT_ACCOUNT")
        oi = await owner_ids(db, org_id, channel)
        lim: dict[str, int] = {}
        ev: Any = None
        if basis == Basis.OWNER_DM:
            plan = await _plan_owner_dm(db, redis, org_id, channel, oi, sess, trigger, group_id, now)
            ctx = guard.GuardCtx(basis=basis, canary=canary)
        elif basis == Basis.OWNER_TAG:
            plan = await _plan_owner_tag(db, org_id, channel, oi, sess, trigger, group_id, now, canary)
            assert plan.ctx is not None
            ctx = plan.ctx
        else:
            plan, lim, ev = await _plan_support(db, org_id, channel, oi, sess, trigger, group_id, now, canary)
            assert plan.ctx is not None
            ctx = plan.ctx
        verdict = guard.check(text, ctx)
        if not verdict.ok:
            raise _Refuse("GUARD_BLOCKED", verdict.codes)
        if basis == Basis.SUPPORT:
            await _check_limits(db, org_id, channel, plan, lim, ev, now)
    except _Refuse as err:
        return await _refused(db, org_id, channel, basis.value, trigger, err)

    sha = hashlib.sha256(text.encode()).hexdigest()
    row = (await db.execute(sql("""
        INSERT INTO ops.auto_sends (org_id, basis, channel_type, session_id, thread_type, thread_id, group_id,
                                    trigger_kind, trigger_ref, body_sha256, body_len, guard)
        VALUES (:o, :b, :c, :s, :tt, :ti, :g, :tk, :tr, :sha, :n, CAST(:gd AS jsonb))
        ON CONFLICT (org_id, basis, trigger_ref) DO NOTHING RETURNING id"""),
        {"o": org_id, "b": basis.value, "c": channel, "s": plan.session_id, "tt": plan.thread_type, "ti":
        plan.thread_id,
         "g": plan.group_id, "tk": trigger.kind, "tr": trigger.ref, "sha": sha, "n": len(text),
         "gd": '{"checked": true, "rules": []}'})).scalar_one_or_none()
    if row is None:
        return await _refused(db, org_id, channel, basis.value, trigger, _Refuse("DUPLICATE_TRIGGER"))
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=ACTOR, action="autosend.issued",
                           target_type="auto_send", target_id=str(row), result="ok",
                           detail={"basis": basis.value, "channel": channel, "thread_type": plan.thread_type,
                                   "group_id": str(plan.group_id) if plan.group_id else None,
                                   "trigger_kind": trigger.kind, "body_len": len(text), "body_sha256": sha})
    await db.commit()                       # sổ + Nhật ký có TRƯỚC khi tin ra bridge
    claims = {"nonce": uuid.uuid4().hex, "auto_send_id": str(row), "basis": basis.value, "channel": channel,
              "thread_id": plan.thread_id, "thread_type": plan.thread_type, "body_sha256": sha,
              "exp": int(time.time()) + policy.PERMIT_TTL_S}
    try:
        await bus.publish(BRIDGE_OUTBOUND, "message.send",
                          {"channel": channel, "session_id": str(plan.session_id), "thread_id": plan.thread_id,
                           "thread_type": plan.thread_type, "text": text, "permit": bridge_permit(claims),
                           "auto_send_id": str(row)},
                          actor=ACTOR, org_id=org_id, correlation_id=str(row))
    except Exception as exc:  # noqa: BLE001
        log.error("autosend: không đẩy được lệnh gửi (%s)", type(exc).__name__)
        await db.execute(sql("UPDATE ops.auto_sends SET status = 'failed', error = 'PUBLISH_FAILED' WHERE id = :i"),
                         {"i": row})
        await db.commit()
        return AutoSendResult(False, "PUBLISH_FAILED", row)
    return AutoSendResult(True, None, row)


async def on_send_result(db: AsyncSession, org_id: uuid.UUID, payload: dict[str, Any]) -> bool:
    """`send.result` của bridge mang `auto_send_id` ⇒ cập nhật sổ (chỉ lần đầu có tác dụng). Trả True nếu đã
    cập nhật."""
    aid = _uuid(payload.get("auto_send_id"))
    if aid is None:
        return False
    ok = bool(payload.get("ok"))
    row = (await db.execute(sql("""
        UPDATE ops.auto_sends SET status = :s, error = :e, external_msg_id = :x, sent_at = CASE WHEN :ok THEN now() END
        WHERE id = :i AND org_id = :o AND status IN ('issued', 'expired') RETURNING basis"""),
        {"s": "sent" if ok else "failed", "e": None if ok else str(payload.get("error") or "SEND_FAILED")[:80],
         "x": str(payload.get("external_msg_id") or "")[:200] or None, "ok": ok, "i": aid, "o": org_id})).one_or_none()
    if row is None:
        return False
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=ACTOR,
                           action="autosend.sent" if ok else "autosend.failed", target_type="auto_send",
                           target_id=str(aid), result="ok" if ok else "failed",
                           detail={"basis": row.basis, "error": None if ok else str(payload.get("error") or "")[:80]})
    return True


async def expire_stale(db: AsyncSession, now: datetime | None = None) -> int:
    """Dòng sổ còn `issued` quá hạn permit mà bridge không báo kết quả ⇒ `expired` (không rõ đã gửi hay chưa)."""
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=policy.PERMIT_TTL_S + 30)
    res = await db.execute(sql("""UPDATE ops.auto_sends SET status = 'expired', error = 'PERMIT_EXPIRED'
                                   WHERE status = 'issued' AND created_at < :c RETURNING id"""), {"c": cutoff})
    return len(res.all())
