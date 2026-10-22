"""Đăng nhập, phiên, PIN (ARCHITECTURE §8.1–8.2)."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.chassis import actionlog
from gh.config import get_settings
from gh.crypto import hash_secret, new_token, token_digest, verify_secret
from gh.hub_link import KHO_LABEL

#: Hộp PIN và Tài khoản của tôi dùng đúng câu này khi người dùng (vd Quản lý được mời) chưa đặt mã PIN.
PIN_NOT_SET = "Tài khoản chưa có mã PIN — đặt ở Tài khoản của tôi"

CHAT_SESSION_PREFIXES = ("chat_ok:", "chat_gen:")  # phiên tạm kênh chat — không gia hạn (load_session)
SESSION_COOKIE = "gh_session"
CSRF_COOKIE = "gh_csrf"

# Thao tác cần phiên PIN (một chỗ duy nhất; backend kiểm, UI chỉ bật hộp nhập).
PIN_OPERATIONS: dict[str, str] = {
    "social.write": "Gửi trả lời / tin nhắn mạng xã hội",
    "channel.logout": "Đăng xuất kênh",
    "channel.login": "Đăng nhập kênh (quét QR)",
    "cli.switch_account": "Đổi tài khoản Antigravity CLI",
    "plugin.install": "Cài plugin",
    "plugin.uninstall": "Gỡ plugin",
    "plugin.toggle": "Bật/tắt plugin",
    "roles.change": "Đổi quyền",
    "secret.reveal": "Xem khoá API",
    "identity.merge": "Gộp / tách danh tính",
    "data.export": "Xuất dữ liệu thô",
    "draft.decide": "Duyệt / huỷ bản nháp",
    "mcp.expose": "Mở tool MCP",
    "hub.link": "Nối Gen-hub (địa chỉ, token)",
    # v0.1.50 (F-81, QD-18): Gen đề xuất ghi Kho dữ liệu — Sếp Xác nhận + nhập PIN mới ghi (bảng Phiên, Việc).
    # Nhãn chỉ để khai báo (UI không hiện) ⇒ không cần tên Kho tuỳ chỉnh.
    "hub.write": f"Ghi {KHO_LABEL} qua Gen-hub (Phiên, Việc)",
    "social.manage": "Tài khoản mạng xã hội: thêm, đăng nhập, gỡ, bật lại sau Dừng tất cả",
    "policy.change": "Đổi mức tự trị, ngưỡng tiền, ranh giới",
    "data.export_delete": "Xuất / xoá dữ liệu",
    "people_review.read": "Xem dữ liệu đánh giá nhân sự",
    "agent.manage": "Tạo / tắt / nhân bản danh tính agent",
    "backup.download": "Tải bản sao lưu",
    "backup.restore": "Khôi phục bản sao lưu",
    "user.manage": "Mời / khoá / đặt lại mật khẩu người dùng",
    # v0.1.35 (F-20): phiên Owner bị lấy không được âm thầm chuyển lưu lượng LLM ra máy chủ lạ.
    "ai.route_change": "Thêm / sửa / xoá nhà cung cấp AI, model, khoá API, chuỗi ưu tiên",
    # v0.1.40 (F-12): bản sao ngoài máy — đổi nơi lưu, xem Khoá khôi phục, tải toàn bộ dữ liệu ra trình duyệt.
    "offsite.destination": "Chọn nơi lưu bản sao ngoài máy",
    "offsite.recovery_kit": "Xem Bộ khôi phục",
    "offsite.portable": "Tải gói mang đi",
    # v0.1.41 (F-86): gói Claude Pro/Max cá nhân chạy việc nền tự động là rủi ro điều khoản của Sếp (QD-12).
    "ai.background_cli": "Cho Claude Code CLI chạy việc nền",
    # v0.1.44 (F-8c, F-4b): nơi nhận báo động/bản tin (token bot Telegram) và gói chẩn đoán (log máy chủ).
    "notify.change": "Đổi nơi nhận báo động (Telegram)",
    "diagnostics.download": "Tải gói chẩn đoán",
    # v0.1.55 (G1): "Về mặc định tất cả" — đưa mọi cài đặt có thể reset về mặc định (không đụng khoá API, phiên CLI, mã
    # PIN, mật khẩu, token Gen-hub/Telegram, tài khoản mạng xã hội, danh tính tổ chức, ranh giới cứng).
    "defaults.reset_all": "Về mặc định tất cả cài đặt",
    # v0.1.62 (F064/F102): phát lại tin khách nằm trong hàng lỗi / xoá hàng lỗi đã xem (không hoàn tác được).
    "queue.manage": "Phát lại / xoá việc nền bị lỗi",
}


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CurrentUser:
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    display_name: str
    role_code: str
    role_name: str
    role_id: uuid.UUID
    team_id: uuid.UUID | None
    session_id: uuid.UUID
    pin_verified_until: datetime | None
    addressing: dict[str, Any]
    permissions: dict[str, str] = field(default_factory=dict)
    ip: str | None = None
    session_expires_at: datetime | None = None
    must_change_password: bool = False
    # True khi request này vừa gia hạn phiên (xem load_session) — middleware SessionCookieRenewal đặt lại cookie.
    session_renewed: bool = False

    @property
    def actor_id(self) -> str:
        return f"user:{self.id}"

    def pin_active(self) -> bool:
        return self.pin_verified_until is not None and self.pin_verified_until > now()


@dataclass
class NewSession:
    token: str
    csrf: str
    expires_at: datetime


async def create_session(db: AsyncSession, user_id: uuid.UUID, *, ip: str | None, user_agent: str | None,
                         pin_verified: bool = False) -> NewSession:
    s = get_settings()
    token, csrf = new_token(), new_token(16)
    expires = min(now() + timedelta(hours=s.session_ttl_hours), now() + timedelta(days=s.session_absolute_days))
    await db.execute(text("""
        INSERT INTO core.sessions (user_id, token_hash, csrf_hash, ip, user_agent, expires_at, last_seen_at,
                                   pin_verified_until)
        VALUES (:uid, :th, :ch, CAST(:ip AS inet), :ua, :exp, now(), :pin)"""),
        {"uid": user_id, "th": token_digest(token), "ch": token_digest(csrf), "ip": ip, "ua": user_agent,
         "exp": expires, "pin": now() + timedelta(minutes=s.pin_session_minutes) if pin_verified else None})
    await db.execute(text("UPDATE core.users SET last_login_at = now() WHERE id = :uid"), {"uid": user_id})
    return NewSession(token=token, csrf=csrf, expires_at=expires)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Hash giả (cùng tham số argon2 như hash thật) để email không tồn tại cũng tốn thời gian xác minh như email thật
    — không lộ email nào có tài khoản qua thời gian phản hồi. Sinh một lần mỗi tiến trình."""
    return hash_secret(new_token())


async def login(db: AsyncSession, email: str, password: str) -> dict[str, Any] | None:
    row = (await db.execute(text("""
        SELECT id, org_id, password_hash FROM core.users
        WHERE lower(email) = lower(:e) AND is_active AND deleted_at IS NULL"""),
        {"e": email.strip().lower()})).one_or_none()
    if row is None:
        verify_secret(_dummy_hash(), password)
        return None
    if not verify_secret(row.password_hash, password):
        return None
    return {"id": row.id, "org_id": row.org_id}


async def load_session(db: AsyncSession, token: str, *, renew: bool = True,
                       renew_pin: bool = False) -> CurrentUser | None:
    """Nạp phiên từ token cookie. `renew=False` (v0.1.45, WS nạp lại định kỳ): chỉ ĐỌC — không gia hạn phiên trượt,
    không gia hạn phiên PIN, không cập nhật last_seen_at (tab để mở không được giữ phiên/PIN sống mãi).

    v0.1.61 (F115): phiên PIN "hết sau 30 phút KHÔNG thao tác" chỉ được gia hạn khi `renew_pin=True` — nơi gọi bật
    cờ này CHỈ cho lời gọi ghi do người bấm (POST/PUT/PATCH/DELETE, xem deps.optional_user). Mặc định tắt: các
    GET tự làm mới của giao diện (thanh đầu trang 60 giây, chuông 120 giây…) và WS không được giữ PIN sống mãi."""
    row = (await db.execute(text("""
        SELECT s.id AS sid, s.pin_verified_until, s.expires_at, s.created_at, s.user_agent, u.id, u.org_id, u.email,
               u.display_name,
               u.addressing,
               u.must_change_password,
               r.id AS role_id, r.code AS role_code, r.name AS role_name, ur.team_id
        FROM core.sessions s
        JOIN core.users u ON u.id = s.user_id
        JOIN core.user_roles ur ON ur.user_id = u.id
        JOIN core.roles r ON r.id = ur.role_id
        WHERE s.token_hash = :h AND s.revoked_at IS NULL AND s.expires_at > now()
          AND s.created_at > now() - make_interval(days => :abs)
          AND u.is_active AND u.deleted_at IS NULL
        ORDER BY r.code LIMIT 1"""), {"h": token_digest(token),
                                      "abs": get_settings().session_absolute_days})).one_or_none()
    if row is None:
        return None
    perms = {r.permission_code: r.scope for r in (await db.execute(text(
        "SELECT permission_code, scope FROM core.role_permissions WHERE role_id = :r"), {"r": row.role_id})).all()}
    pin_until = row.pin_verified_until
    # Phiên đăng nhập trượt: còn dưới nửa TTL thì gia hạn thêm đủ một TTL tính từ bây giờ — Owner dùng đều đặn
    # thì không bị đăng xuất; bỏ không quá TTL thì hết hạn như cũ. Phiên PIN tách riêng (bên dưới).
    # v0.1.46: trượt nhưng không quá session_absolute_days kể từ created_at (hết hạn tuyệt đối).
    ttl = timedelta(hours=get_settings().session_ttl_hours)
    expires_at, renewed = row.expires_at, False
    # v0.1.74 (review bảo mật P2-1): phiên tạm kênh chat (`chat_ok:*`, `chat_gen:*` — owner_channel/chat_confirm.py)
    # sống đúng hạn lúc tạo (≤ 5 phút) — KHÔNG trượt; trước đây lời GET đầu tiên đẩy hạn lên đủ một TTL (7 ngày).
    if (row.user_agent or "").startswith(CHAT_SESSION_PREFIXES):
        renew = False
    if renew and expires_at - now() < ttl / 2:
        new_exp = min(now() + ttl, row.created_at + timedelta(days=get_settings().session_absolute_days))
        if new_exp > expires_at:
            expires_at, renewed = new_exp, True
            await db.execute(text("UPDATE core.sessions SET expires_at = :e, last_seen_at = now() WHERE id = :sid"),
                             {"e": expires_at, "sid": row.sid})
    # Phiên PIN trượt: hết hạn sau 30 phút KHÔNG thao tác — "thao tác" là lời gọi ghi của người dùng (F115).
    if renew and renew_pin and pin_until is not None and pin_until > now():
        new_until = now() + timedelta(minutes=get_settings().pin_session_minutes)
        if (new_until - pin_until).total_seconds() > 60:
            await db.execute(text("UPDATE core.sessions SET pin_verified_until = :p, last_seen_at = now() "
                                  "WHERE id = :sid"), {"p": new_until, "sid": row.sid})
            pin_until = new_until
    return CurrentUser(id=row.id, org_id=row.org_id, email=row.email, display_name=row.display_name,
                       role_code=row.role_code, role_name=row.role_name, role_id=row.role_id, team_id=row.team_id,
                       session_id=row.sid, pin_verified_until=pin_until, addressing=row.addressing or {},
                       permissions=perms, session_expires_at=expires_at, session_renewed=renewed,
                       must_change_password=bool(row.must_change_password))


async def csrf_matches(db: AsyncSession, session_id: uuid.UUID, csrf: str) -> bool:
    h = (await db.execute(text("SELECT csrf_hash FROM core.sessions WHERE id = :s"), {"s": session_id})).scalar()
    return h is not None and bytes(h) == token_digest(csrf)


async def revoke_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(text("UPDATE core.sessions SET revoked_at = now() WHERE id = :s"), {"s": session_id})


async def purge_expired_sessions(db: AsyncSession, *, older_than_days: int) -> int:
    """Xoá vĩnh viễn phiên đã HẾT HẠN hoặc BỊ THU HỒI quá `older_than_days` ngày (PLAN §5.6 lỗi 🟡 — dọn
    core.sessions; job hằng giờ đăng ký ở gh/worker.py::expire_sessions). Chỉ xoá theo mốc thời gian đã qua
    từ lâu (không phải mọi phiên hết hạn) để giữ lại vết gần đây phục vụ điều tra sự cố đăng nhập."""
    cutoff = now() - timedelta(days=older_than_days)
    result = await db.execute(text(
        "DELETE FROM core.sessions WHERE (expires_at < :cutoff) OR (revoked_at IS NOT NULL AND revoked_at < :cutoff)"),
        {"cutoff": cutoff})
    return result.rowcount or 0  # type: ignore[attr-defined]


# ─── PIN ────────────────────────────────────────────────────────────────────

def valid_pin(pin: str) -> bool:
    return len(pin) == 6 and pin.isdigit()


@dataclass
class PinResult:
    ok: bool
    attempts_left: int = 0
    locked_until: datetime | None = None
    pin_verified_until: datetime | None = None
    just_locked: bool = False
    #: Tài khoản chưa đặt mã PIN (người được mời): không phải "nhập sai" — không đếm lần sai, không khoá.
    no_pin: bool = False


async def verify_pin(db: AsyncSession, user: CurrentUser, pin: str) -> PinResult:
    s = get_settings()
    row = (await db.execute(text(
        "SELECT pin_hash, pin_failed, pin_locked_until FROM core.users WHERE id = :u FOR UPDATE"),
        {"u": user.id})).one()
    if row.pin_hash is None:
        # F071/F097: người được mời chưa có PIN. Nói thật "chưa có PIN" (không phải "không đúng"), không đếm sai
        # (nếu không, gõ thử 5 lần là bị khoá 15 phút vì một mã không tồn tại).
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="auth.pin_not_set", result="failed", ip=user.ip)
        return PinResult(ok=False, no_pin=True)
    if row.pin_locked_until is not None and row.pin_locked_until > now():
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="auth.pin_attempt_while_locked", result="blocked", ip=user.ip)
        return PinResult(ok=False, locked_until=row.pin_locked_until)
    if verify_secret(row.pin_hash, pin):
        until = now() + timedelta(minutes=s.pin_session_minutes)
        await db.execute(text("UPDATE core.users SET pin_failed = 0, pin_locked_until = NULL WHERE id = :u"),
                         {"u": user.id})
        await db.execute(text("UPDATE core.sessions SET pin_verified_until = :p WHERE id = :s"),
                         {"p": until, "s": user.session_id})
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="auth.pin_verified", ip=user.ip)
        return PinResult(ok=True, pin_verified_until=until)
    failed = row.pin_failed + 1
    if failed >= s.pin_max_attempts:
        locked = now() + timedelta(minutes=s.pin_lock_minutes)
        await db.execute(text("UPDATE core.users SET pin_failed = 0, pin_locked_until = :l WHERE id = :u"),
                         {"l": locked, "u": user.id})
        await db.execute(text("UPDATE core.sessions SET pin_verified_until = NULL WHERE user_id = :u"),
                         {"u": user.id})
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="auth.pin_locked", result="blocked",
                               detail={"locked_until": locked.isoformat()}, ip=user.ip)
        return PinResult(ok=False, locked_until=locked, just_locked=True)
    await db.execute(text("UPDATE core.users SET pin_failed = :f WHERE id = :u"), {"f": failed, "u": user.id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="auth.pin_failed", result="failed",
                           detail={"attempts_left": s.pin_max_attempts - failed}, ip=user.ip)
    return PinResult(ok=False, attempts_left=s.pin_max_attempts - failed)


async def set_pin(db: AsyncSession, user_id: uuid.UUID, pin: str) -> None:
    await db.execute(text("UPDATE core.users SET pin_hash = :h, pin_failed = 0, pin_locked_until = NULL "
                          "WHERE id = :u"), {"h": hash_secret(pin), "u": user_id})
