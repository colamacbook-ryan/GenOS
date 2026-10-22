"""Sức khoẻ hệ thống + chuông tự khử trùng lặp (v0.1.36 — F-6, F-3, F-4).

Mỗi "sự cố đang mở" là MỘT dòng `ops.health_alerts` theo (org_id, key) — migration 0024. Đây là nguồn sự thật cho
cả chuông (core.notifications) lẫn dải "Cần Sếp xử lý" ở Tổng quan:

- `raise_once` chỉ gửi chuông khi dòng MỚI mở (chưa có / đã đóng) hoặc `fingerprint` đổi (vd. một lần cập nhật lỗi
  KHÁC) — sự kiện lặp lại (bridge gửi `session.ended` nhiều lần, model 401 liên tục…) KHÔNG sinh chuông thứ hai.
- `clear` đóng dòng khi hết sự cố; lần sau sự cố quay lại mới có chuông mới.
- `collect` dựng khuôn `GET /api/v1/system/health` (đọc thuần, không gửi chuông). KHÔNG đụng `/ready`: genh dùng
  `/ready` để quyết rollback (apps/genh/internal/ops/update.go) — bộ xử lý nền im không được làm hỏng một bản cập
  nhật tốt.
- `evaluate` + `watch_loop` (chạy trong api, `Settings.health_watch_seconds`): mỗi phút tính lại các sự cố theo dõi
  định kỳ (cập nhật lỗi trong 26 giờ, quá hạn sao lưu theo tần suất bước 11, bộ xử lý nền im, ổ đĩa sắp đầy, model
  đang hết đăng nhập, bản sao ngoài máy cũ/lỗi — v0.1.40, chi phí AI hôm nay vượt trần — v0.1.41, lịch tự cập nhật
  đêm im quá 36 giờ — v0.1.53, cầu nối tin nhắn im quá 2 phút và hàng lỗi tin khách vào > 0 — v0.1.62) và dọn dòng sự
  kiện cũ.

Hợp đồng Redis với worker (gh/worker.py ghi): `gh:cron:last:<tên hàm>` = JSON {"at": ISO UTC 'Z', "ok": bool,
"ms": int} + tên hàm trong tập `gh:cron:names`; `gh:worker:heartbeat` = ISO UTC. Hàng lỗi: tập `gh:dlq:streams`
(gh/chassis/bus.py ghi). Không SCAN keyspace mỗi lần đọc — chỉ quét bù tối đa một lần mỗi ngày (`_discover`) cho
khoá có từ trước khi có hai tập này. Mọi chuỗi hiện cho người dùng là tiếng Việt thân thiện, không đường dẫn
tệp, không bí mật.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("gh.health")

#: Quá hạn sao lưu theo `settings->'backup'->>'frequency'` (bước 11 / PUT /backups/schedule): một chu kỳ + 12 giờ
#: dư (hằng ngày giữ mốc 36 giờ cũ). Giá trị lạ ⇒ như hằng ngày (khớp `gh.backup.is_due`).
BACKUP_STALE_HOURS = 36
BACKUP_STALE_LIMITS: dict[str, tuple[timedelta, str]] = {
    "daily": (timedelta(hours=BACKUP_STALE_HOURS), f"{BACKUP_STALE_HOURS} giờ"),
    "weekly": (timedelta(days=7, hours=12), "một tuần"),
    "monthly": (timedelta(days=31, hours=12), "một tháng"),
}
#: Cập nhật lỗi chỉ còn là sự cố trong 26 giờ sau khi xong — khớp thẻ cập nhật ở web (updateModel.ts RECENT_MS):
#: quá hạn thì thẻ không còn lỗi để xem/thử lại, dải "Cần Sếp xử lý" cũng thôi báo. 26 giờ (không phải 24): lịch đêm
#: chạy 03:00 + trễ ngẫu nhiên tới 30 phút (genh autoupdate RandomizedDelaySec=1800) nên hai lần thử liền nhau cách tới
#: 24 giờ 30 phút — hạn 24 giờ sẽ đóng sự cố giữa hai lần rồi mở lại (chuông lặp mỗi đêm).
UPDATE_FAILED_RECENT = timedelta(hours=26)
#: F103: tổ chức chưa đặt lịch sao lưu quá thời gian này (kể từ lúc tạo) ⇒ sự cố backup.stale "chưa cấu hình".
BACKUP_UNCONFIGURED_GRACE = timedelta(hours=24)
WORKER_SILENT_MINUTES = 10
HEARTBEAT_KEY = "gh:worker:heartbeat"
#: Nhịp tim cầu nối tin nhắn (apps/bridge ghi mỗi 15 giây, TTL 45 giây). Khoá HẾT HẠN sau 45 giây nên "mất khoá" không
#: đủ để biết im bao lâu ⇒ lấy thêm nhịp từng phiên kênh (`core.channel_sessions.last_heartbeat_at`, cũng 15 giây/lần).
BRIDGE_HEARTBEAT_KEY = "gh:bridge:heartbeat"
#: Cầu nối im quá N phút (mà còn kênh đang "kết nối") ⇒ sự cố `bridge.silent`. Dư gấp nhiều lần chu kỳ nhịp để bridge
#: khởi động lại / Redis chớp không đẻ chuông giả.
BRIDGE_SILENT_MINUTES = 2
#: Hàng lỗi của tin khách vào (`gh.bridge.inbound`): tin đã thử nhập kho 5 lần mà vẫn lỗi. Chỉ hàng này phát lại được
#: (nhập kho tin thô idempotent theo mã tin ngoài); mọi hàng lỗi khác chỉ xem rồi xoá — phát lại có thể lặp tác dụng.
DLQ_INBOUND = "gh.bridge.inbound.dlq"
DLQ_REPLAYABLE = frozenset({"gh.bridge.inbound"})
#: Một lần bấm "Phát lại" xử lý tối đa N tin (còn dư thì bấm tiếp) — không giữ yêu cầu HTTP quá lâu.
DLQ_REPLAY_BATCH = 500
CRON_LAST_PREFIX = "gh:cron:last:"
CRON_NAMES_KEY = "gh:cron:names"
DISCOVERED_KEY = "gh:health:discovered"
DISCOVER_TTL = 86400
WATCH_LOCK_KEY = "gh:health:tick"
#: browser-worker ghi nhịp mỗi 15 giây (TTL 45 giây — apps/browser/ghb/worker.py) — quá 40 giây coi như im. Ngưỡng
#: phải DƯỚI TTL: quá TTL khoá biến mất và trạng thái thành 'off' (không phân biệt được với chưa bật).
BROWSER_SILENT_SECONDS = 40
#: v0.1.63 (H1): hộp riêng Antigravity phải tắt liên tục chừng này giây mới mở sự cố `agy_box.down` (hộp khởi động lại
#: khi `genh update` tắt vài chục giây — không nhá chuông).
AGY_BOX_DOWN_GRACE_SECONDS = 120
AGY_BOX_DOWN_SINCE_KEY = "gh:health:agy_box:down_since"

STORAGE_LINK = "/system?tab=storage"
#: Đích của job.timeout ("Xem sức khoẻ"): thẻ "Sức khoẻ hệ thống" đầu Dữ liệu & lưu trữ (HealthCard đọc `focus=health`).
HEALTH_LINK = "/system?tab=storage&focus=health"
#: Đích của backup.stale: Dữ liệu & lưu trữ, cuộn tới mục Sao lưu (BackupPanel đọc `focus=backup`).
BACKUP_LINK = "/system?tab=storage&focus=backup"
#: v0.1.40 (F-12): đích của offsite.stale/offsite.failed — mục "Bản sao ngoài máy" ở Dữ liệu & lưu trữ.
OFFSITE_LINK = "/system?tab=storage&focus=offsite"
#: Bản sao ngoài máy quá N ngày chưa có lần thành công ⇒ cũ (lịch tuần + bù vài ngày lỡ); quá 30 ngày ⇒ 'bad'.
OFFSITE_STALE_DAYS = 7
#: Ngưỡng "cũ" thật = lịch tuần + 12 giờ ân hạn: `last_success_at` là giờ BẮT ĐẦU lượt tuần trước, timer systemd trễ
#: ngẫu nhiên tới 30 phút và xuất+kiểm mất vài phút ⇒ đúng 7 ngày thì lượt tuần này thường chưa xong — không được coi
#: là cũ (tránh chuông giả mỗi tuần). Dùng chung cho chuông (health) và GET /system/offsite (offsite.read_status).
OFFSITE_STALE_AFTER = timedelta(days=OFFSITE_STALE_DAYS, hours=12)
OFFSITE_BAD_DAYS = 30
#: v0.1.53 (F-99): lịch tự cập nhật đêm (~03:00) im quá N giờ kể từ mốc = max(lần chạy cuối, lúc bật) ⇒ cảnh báo
#: `host.nightly`. Một đêm trượt + ân hạn 12 giờ (timer trễ ngẫu nhiên, máy ngủ đến sáng); `genh auto-update status`
#: cũng dùng 36 giờ.
NIGHTLY_STALE_HOURS = 36

#: Nhãn nút hành động theo kind (web hiện trên dải "Cần Sếp xử lý").
ACTIONS = {
    "channel.down": "Đăng nhập lại",
    "model.auth_expired": "Đăng nhập lại model",
    # Khoá API bị từ chối dùng cùng `kind` model.auth_expired nhưng link `/api` ⇒ nhãn nút riêng (`_ACTION_BY_LINK`).
    "update.failed": "Xem & thử lại",
    "backup.stale": "Mở mục Sao lưu",
    "worker.silent": "Xem sức khoẻ",
    # F051: cầu nối tin nhắn (Zalo/WhatsApp) im — tin khách không vào được.
    "bridge.silent": "Xem sức khoẻ",
    # F064/F102: tin khách nằm trong hàng lỗi — đích là nút Phát lại ở thẻ Sức khoẻ.
    "dlq.inbound": "Xem & phát lại",
    "disk.low": "Xem cách giải phóng",
    "host.autostart": "Xem cách bật",
    # v0.1.53 (F-99): lịch tự cập nhật đêm không chạy (tắt / linger tắt / trình nhận yêu cầu lỗi) — `_eval_nightly`.
    "host.nightly": "Xem cách bật lại",
    "offsite.stale": "Chọn nơi lưu / sao lưu ngay",
    "offsite.failed": "Xem bản sao ngoài máy",
    # Do worker mở/đóng (key "job.timeout:<tên hàm>") — chỉ khai nhãn ở đây.
    "job.timeout": "Xem sức khoẻ",
    # v0.1.41 (F-84): chi phí AI hôm nay vượt trần (`_eval_budget`).
    "ai.budget_exceeded": "Xem chi phí AI",
    # v0.1.41: việc nền không còn nguồn AI dùng được — do gói bản tin/nguồn nền mở/đóng, chỉ khai nhãn ở đây.
    "ai.background_no_source": "Mở Bộ não AI",
    # v0.1.44 (F-8c): hộp thư đi Telegram hỏng vì cấu hình — do gh.telegram.service mở/đóng.
    "telegram.failed": "Mở cấu hình Telegram",
    # v0.1.46 (F-21): cổng đang mở cho cả mạng (bản cài cũ) — `_eval_network`.
    "network.open_lan": "Chọn cách truy cập",
    # v0.1.47 (F-83): phiên Facebook đã hết / bị yêu cầu xác minh — gh.social.session_watch mở/đóng.
    "social.session_expired": "Đăng nhập lại",
    # v0.1.49 (F-83): Gen-hub không trả lời hơn 15 phút — gh.hub_link.service.breaker_watch mở/đóng.
    # Nút cuộn tới + làm sáng thẻ Gen-hub ở Kết nối (không mở trang Gen-hub bên ngoài) — cùng nhãn nút của Bản tin.
    "hub.unreachable": "Mở thẻ Gen-hub",
}
#: Khoá API bị từ chối (F007): đích `/api` (API & Model — nơi dán khoá mới) ⇒ nhãn nút theo ĐÍCH, không theo `kind`
#: (CLI vẫn "Đăng nhập lại model" → Kết nối › Bộ não AI). Khoá = (kind, link).
MODEL_API_LINK = "/api"
MODEL_CLI_LINK = "/connections#brain"
_ACTION_BY_LINK = {("model.auth_expired", MODEL_API_LINK): "Thêm khoá API mới"}
#: Nhãn cho người KHÔNG phải Owner khi nút ở nhãn gốc chỉ Owner có (vd "Chọn nơi lưu" — Manager không có nút đó).
NON_OWNER_ACTIONS = {
    "offsite.stale": "Xem bản sao ngoài máy",
    "telegram.failed": "Nhờ Owner xử lý",
    "network.open_lan": "Nhờ Owner xử lý",
    "social.session_expired": "Nhờ Owner xử lý",
    "hub.unreachable": "Nhờ Owner xử lý",
}
#: Sự cố mà đích nút chỉ Owner mở được (thẻ Telegram chỉ dựng cho Owner) ⇒ người khác không nhận link (không nút chết).
#: (trang /social chỉ Owner mở được ⇒ social.session_expired cũng không có link cho người khác.)
NON_OWNER_NO_LINK = frozenset({"telegram.failed", "social.session_expired"})
_TELEGRAM_NON_OWNER = "Kênh Telegram của Owner đang lỗi — nhờ Owner mở Kết nối › Telegram"
#: Thân sự cố cho người KHÔNG phải Owner khi thân gốc bảo bấm nút chỉ Owner có ("Chọn nơi lưu…"). Khoá = (kind, mã) —
#: mã là fingerprint (offsite.stale) hoặc mã genh ở cuối fingerprint (offsite.failed: "<lần thử>|<mã>").
NON_OWNER_BODIES = {
    ("offsite.stale", "not_configured"): "Hỏng ổ đĩa là mất hết dữ liệu. Nhờ Owner cắm ổ USB/NAS và chọn nơi lưu bản "
                                         "sao ngoài máy",
    ("offsite.failed", "GH-EB00"): "Chưa chọn nơi lưu — nhờ Owner cắm ổ USB/NAS và chọn nơi lưu bản sao ngoài máy",
    ("offsite.failed", "GH-EB07"): "Nơi lưu không hợp lệ — nhờ Owner chọn lại thư mục trên ổ USB/NAS",
    # v0.1.44 (F-8c): fingerprint = mã lỗi Telegram (gh/telegram/client.py).
    ("telegram.failed", "TELEGRAM_TOKEN_REJECTED"): f"{_TELEGRAM_NON_OWNER} và dán lại token bot.",
    ("telegram.failed", "TELEGRAM_CHAT_NOT_FOUND"): f"{_TELEGRAM_NON_OWNER} và chọn lại chat_id.",
    ("telegram.failed", "TELEGRAM_BOT_BLOCKED"): f"{_TELEGRAM_NON_OWNER}; Owner mở bot trên Telegram, bấm Bắt đầu "
                                                 "(Start) rồi bấm Gửi thử.",
    # v0.1.46 (F-21): fingerprint cố định "lan_legacy"; người không phải Owner không chạy được `genh remote`.
    ("network.open_lan", "lan_legacy"): "Cổng Console đang mở cho cả mạng — nhờ Owner chọn cách truy cập từ xa.",
}
#: Thân cho người KHÔNG phải Owner theo `kind` (mọi fingerprint) — dùng khi không có mục riêng (kind, mã) ở trên.
#: v0.1.47 (F-83): sự cố phiên Facebook có nhiều fingerprint (needs_login, key_changed, checkpoint, captcha…) — mọi
#: trường hợp chỉ Owner tự đăng nhập lại được ở trang /social.
NON_OWNER_KIND_BODIES = {
    "social.session_expired": "Phiên Facebook của Owner đã hết — nhờ Owner mở Tài khoản mạng xã hội và đăng nhập lại.",
    # v0.1.49 (F-83): thẻ Gen-hub (Kết nối › Gen-hub) chỉ Owner thao tác.
    "hub.unreachable": "Gen-hub của Owner tạm không trả lời — nhờ Owner kiểm tra ở Kết nối › Gen-hub.",
}


def _viewer_body(kind: str, fingerprint: str | None, body: str, is_owner: bool) -> str:
    """Thân sự cố theo vai trò người xem: không phải Owner thì không bảo bấm nút chỉ Owner có (`NON_OWNER_BODIES`)."""
    if is_owner:
        return body
    fp = fingerprint or ""
    code = fp.rpartition("|")[2] if kind == "offsite.failed" else fp
    return NON_OWNER_BODIES.get((kind, code)) or NON_OWNER_KIND_BODIES.get(kind, body)

#: v0.1.37 (F-73): `run/autostart-status.json` (genh ghi) — chỉ nhận giá trị trong các tập này, còn lại 'unknown'.
AUTOSTART_YES_NO = ("yes", "no", "unknown", "not_applicable")
AUTOSTART_DOCKER_MODES = ("system", "rootless", "desktop", "unknown")
#: Lệnh sửa do API tự ghép từ chuỗi cố định — KHÔNG lấy lệnh/chữ từ tệp (run/ là 0777, không tin cậy).
AUTOSTART_FIX = {
    "docker_system": "Docker chưa bật tự chạy khi mở máy — chạy một lần trên máy chủ: sudo systemctl enable docker",
    "docker_rootless": "Docker chưa bật tự chạy khi mở máy — chạy một lần trên máy chủ: systemctl --user enable docker",
    "linger": "Lịch tự cập nhật và nút Cập nhật ngay chỉ chạy khi có người đăng nhập — chạy một lần: "
              "sudo loginctl enable-linger $USER",
    # Docker rootless chạy dưới user manager của người dùng ⇒ thiếu linger thì CẢ Docker cũng không tự lên khi bật máy.
    "linger_rootless": "Docker rootless, lịch tự cập nhật và nút Cập nhật ngay chỉ chạy khi có người đăng nhập — chạy "
                       "một lần: sudo loginctl enable-linger $USER",
}
#: Câu cuối của thân cảnh báo: genh chỉ ghi lại `run/autostart-status.json` khi chạy `genh status`/`genh doctor` hoặc
#: lần cập nhật kế tiếp (lịch đêm) — chạy xong lệnh sửa mà không biết điều này, Sếp sẽ tưởng lệnh không có tác dụng.
#: KHÔNG hứa "đợi tới đêm": thiếu linger thì lịch đêm không chạy, tắt tự cập nhật thì không có lần chạy đêm nào.
AUTOSTART_DONE = "Chạy xong thì chạy genh status để cảnh báo tự hết"
#: Tiêu đề cảnh báo phòng trước (máy VẪN đang chạy — chỉ là khi bật lại sẽ không tự lên); cùng câu ở tài liệu/web/test.
AUTOSTART_TITLE = "Máy chủ có thể không tự chạy lại Gen-Harness khi bật lại máy"
#: Mỗi câu kết thúc bằng lệnh — KHÔNG thêm dấu chấm sau lệnh (Sếp chép nguyên dòng: "docker." / "$USER." chạy sẽ lỗi).
AUTOSTART_SEP = " · "


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _s(value: Any) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


# ─── dòng sự cố: mở một lần / đóng ───────────────────────────────────────────────────────────────────────────

async def raise_once(db: AsyncSession, org_id: uuid.UUID, *, key: str, kind: str, severity: str, title: str,
                     body: str, link: str | None, fingerprint: str = "", redis: Any = None) -> bool:
    """Mở sự cố `key`; gửi chuông cho các Owner CHỈ khi dòng mới mở hoặc `fingerprint` đổi. Trả True nếu đã gửi.

    Sự cố đang mở với cùng fingerprint ⇒ không chuông thứ hai (chỉ làm mới tiêu đề/nội dung cho dải "Cần Sếp xử lý",
    vd. số phút bộ xử lý nền đã ngừng). Bên gọi commit (chuông đẩy WebSocket sau commit — gh.notifications)."""
    from gh import notifications

    params = {"o": org_id, "k": key, "kind": kind, "sev": severity, "t": title, "b": body, "l": link,
              "f": fingerprint}
    fresh = (await db.execute(text("""
        INSERT INTO ops.health_alerts (org_id, key, kind, severity, fingerprint, title, body, link)
        VALUES (:o, :k, :kind, :sev, :f, :t, :b, :l)
        ON CONFLICT (org_id, key) DO UPDATE SET kind = EXCLUDED.kind, severity = EXCLUDED.severity,
               title = EXCLUDED.title, body = EXCLUDED.body, link = EXCLUDED.link,
               fingerprint = EXCLUDED.fingerprint, raised_at = now(), cleared_at = NULL
        WHERE ops.health_alerts.cleared_at IS NOT NULL
           OR ops.health_alerts.fingerprint IS DISTINCT FROM EXCLUDED.fingerprint
        RETURNING key"""), params)).scalar_one_or_none()
    if fresh is None:
        await db.execute(text("""
            UPDATE ops.health_alerts SET title = :t, body = :b, link = :l
            WHERE org_id = :o AND key = :k AND cleared_at IS NULL
              AND (title, body, link) IS DISTINCT FROM (:t, :b, :l)"""),
            {"o": org_id, "k": key, "t": title, "b": body, "l": link})
        return False
    await notifications.notify(db, org_id, await notifications.owner_ids(db, org_id), kind=kind, title=title,
                               body=body, link=link, redis=redis, incident_key=key)
    return True


async def clear(db: AsyncSession, org_id: uuid.UUID, key: str, *, label: bool = True, redis: Any = None) -> bool:
    """Đóng sự cố `key` đang mở (hết sự cố). Trả True nếu có dòng được đóng. Bên gọi commit.

    v0.1.69 (F098): đóng sự cố thì chuông của nó cũng tắt — đã đọc + "Đã xử lý" (`notifications.resolve_incident`).
    `label=False` (gộp sự cố này vào sự cố khác, vd. offsite.failed → offsite.stale) chỉ đánh dấu đã đọc, không nói
    "Đã xử lý" vì vấn đề vẫn còn. Có `redis` thì chuông đang mở đổi ngay (WebSocket `notification.resolved`, sau
    commit)."""
    from gh import notifications

    row = (await db.execute(text("""UPDATE ops.health_alerts SET cleared_at = now()
                                    WHERE org_id = :o AND key = :k AND cleared_at IS NULL
                                    RETURNING kind, title"""), {"o": org_id, "k": key})).first()
    if row is None:
        return False
    # Còn sự cố MỞ khác cùng `kind` (job.timeout:<tên>, model.auth_expired:<id>) ⇒ chỉ chuông cùng tiêu đề; không thì
    # mọi chuông của kind (tiêu đề từng đổi theo số phút/ngày giữa các lần mở).
    others = (await db.execute(text("""SELECT EXISTS (SELECT 1 FROM ops.health_alerts
                                       WHERE org_id = :o AND kind = :kind AND key <> :k AND cleared_at IS NULL)"""),
                               {"o": org_id, "kind": row.kind, "k": key})).scalar_one()
    await notifications.resolve_incident(db, org_id, kind=row.kind, title=row.title if others else None, label=label,
                                         key=key, redis=redis)
    return True


async def active_issues(db: AsyncSession, org_id: uuid.UUID, *, is_owner: bool = True) -> list[dict[str, Any]]:
    """Sự cố đang mở: 'bad' trước, rồi mới nhất trước. Mọi trường là chuỗi (web không render object). Nhãn nút theo
    vai trò người xem: không phải Owner thì không hứa nút chỉ Owner có (`NON_OWNER_ACTIONS`, `NON_OWNER_BODIES`)."""
    labels = ACTIONS if is_owner else {**ACTIONS, **NON_OWNER_ACTIONS}
    rows = (await db.execute(text("""
        SELECT key, kind, severity, title, body, link, raised_at, fingerprint FROM ops.health_alerts
        WHERE org_id = :o AND cleared_at IS NULL
        ORDER BY (severity = 'bad') DESC, raised_at DESC"""), {"o": org_id})).all()
    return [{"key": r.key, "kind": r.kind, "severity": r.severity, "title": r.title,
             "body": _viewer_body(r.kind, r.fingerprint, r.body, is_owner),
             "link": None if not is_owner and r.kind in NON_OWNER_NO_LINK else r.link,
             "action": _ACTION_BY_LINK.get((r.kind, r.link)) or labels.get(r.kind, "Xem chi tiết"),
             "raised_at": _iso(r.raised_at)}
            for r in rows]


# ─── đọc nguồn (mọi lỗi ⇒ None/'unknown', không ném) ─────────────────────────────────────────────────────────

async def _discover(redis: Any) -> None:
    """Quét bù (SCAN) tối đa một lần mỗi `DISCOVER_TTL`: dấu cron / hàng lỗi ghi trước khi worker/bus đăng ký tên
    vào tập. Lượt đọc thường chỉ SMEMBERS + MGET/XLEN — không quét cả keyspace mỗi lần GET /system/health."""
    from gh.chassis.bus import DLQ_STREAMS_KEY

    if not await redis.set(DISCOVERED_KEY, b"1", nx=True, ex=DISCOVER_TTL):
        return
    names = [_s(k)[len(CRON_LAST_PREFIX):] async for k in redis.scan_iter(match=f"{CRON_LAST_PREFIX}*", count=500)]
    if names:
        await redis.sadd(CRON_NAMES_KEY, *names)
    dlqs = [_s(k) async for k in redis.scan_iter(match="*.dlq", count=500, _type="stream")]
    if dlqs:
        await redis.sadd(DLQ_STREAMS_KEY, *dlqs)


async def _dlq_queues(redis: Any) -> list[dict[str, Any]]:
    from gh.chassis.bus import DLQ_STREAMS_KEY

    await _discover(redis)
    queues: list[dict[str, Any]] = []
    for name in sorted(_s(k) for k in await redis.smembers(DLQ_STREAMS_KEY)):
        if not await redis.exists(name):  # stream đã bị xoá ⇒ bỏ khỏi tập
            await redis.srem(DLQ_STREAMS_KEY, name)
            continue
        queues.append({"stream": name[: -len(".dlq")], "dlq": int(await redis.xlen(name))})
    return queues


# ─── cầu nối tin nhắn (bridge): nhịp tim ─────────────────────────────────────────────────────────────────────

async def _bridge_state(db: AsyncSession, redis: Any, org_id: uuid.UUID, now: datetime) -> dict[str, Any] | None:
    """Cầu nối tin nhắn (apps/bridge). Trả {"state": 'ok'|'silent', "last_seen_at", "silent_minutes"} hoặc None khi
    không có gì để báo (không có nhịp VÀ không kênh nào đang "kết nối" ⇒ chưa dùng cầu nối, không phải sự cố).

    - Khoá nhịp còn ⇒ 'ok' (khoá sống 45 giây, bridge ghi lại mỗi 15 giây).
    - Hết khoá: dựa vào nhịp gần nhất của các phiên kênh còn "kết nối" (`state='active'`, chưa kết thúc) — bridge
      chết/treo không gửi `session.ended` nên phiên vẫn 'active' mà nhịp đứng yên; im quá `BRIDGE_SILENT_MINUTES` ⇒
      'silent'. Phiên chưa có nhịp nào lấy giờ bắt đầu làm mốc."""
    raw = await redis.get(BRIDGE_HEARTBEAT_KEY)
    if raw:
        return {"state": "ok", "last_seen_at": _iso(_parse_ts(raw) or now), "silent_minutes": None}
    seen = (await db.execute(text("""
        SELECT max(COALESCE(last_heartbeat_at, started_at, qr_issued_at)) FROM core.channel_sessions
        WHERE org_id = :o AND state = 'active' AND ended_at IS NULL"""), {"o": org_id})).scalar_one_or_none()
    if seen is None:
        return None
    gap = now - seen
    if gap <= timedelta(minutes=BRIDGE_SILENT_MINUTES):
        return {"state": "ok", "last_seen_at": _iso(seen), "silent_minutes": None}
    return {"state": "silent", "last_seen_at": _iso(seen), "silent_minutes": int(gap.total_seconds() // 60)}


async def _cron_runs(redis: Any) -> list[dict[str, Any]]:
    await _discover(redis)
    out: list[dict[str, Any]] = []
    names = sorted(_s(k) for k in await redis.smembers(CRON_NAMES_KEY))
    raws = await redis.mget([f"{CRON_LAST_PREFIX}{n}" for n in names]) if names else []
    for name, raw in zip(names, raws, strict=True):
        if raw is None:  # dấu đã hết hạn (job bị gỡ) ⇒ bỏ khỏi tập, như SCAN không còn thấy khoá
            await redis.srem(CRON_NAMES_KEY, name)
            continue
        data: dict[str, Any] = {}
        with contextlib.suppress(ValueError, TypeError):
            loaded = orjson.loads(raw) if raw else {}
            data = loaded if isinstance(loaded, dict) else {}
        at = _parse_ts(data.get("at"))
        ok = data.get("ok")
        out.append({"name": name, "last_at": _iso(at), "ok": ok if isinstance(ok, bool) else None, "_at": at})
    out.sort(key=lambda c: c["name"])
    return out


async def _worker_last_seen(redis: Any, crons: list[dict[str, Any]] | None = None) -> datetime | None:
    seen = [_parse_ts(await redis.get(HEARTBEAT_KEY))]
    seen += [c["_at"] for c in (crons if crons is not None else await _cron_runs(redis))]
    times = [t for t in seen if t is not None]
    return max(times) if times else None


def _worker_state(last_seen: datetime | None, *, now: datetime, started_at: datetime | None) -> tuple[str, int | None]:
    """('ok'|'silent'|'unknown', số phút im). Chưa từng thấy: im khi api đã chạy quá 10 phút (đủ thời gian để
    worker khởi động và ghi nhịp đầu tiên), còn không ⇒ 'unknown'."""
    limit = timedelta(minutes=WORKER_SILENT_MINUTES)
    if last_seen is not None:
        gap = now - last_seen
        return ("silent", int(gap.total_seconds() // 60)) if gap > limit else ("ok", None)
    if started_at is not None and now - started_at > limit:
        return "silent", int((now - started_at).total_seconds() // 60)
    return "unknown", None


def _host_dir() -> Any:
    from gh.system_api import update

    return update._dir()


def _disk_status() -> dict[str, Any] | None:
    from gh.system_api import update

    d = _host_dir()
    if not d.is_dir():
        return None
    return update._read_json(d / "disk-status.json")


def _autostart_status() -> dict[str, Any]:
    """Khối `autostart` của /system/health từ `run/autostart-status.json`: mọi giá trị ngoài tập cho phép ⇒ 'unknown'.
    `state`: 'warn' khi (linger_required và linger 'no') hoặc docker_enabled 'no'; 'ok' khi không vấn đề, docker_enabled
    yes/not_applicable và linger yes/not_applicable/không cần; còn lại (kể cả tệp thiếu/hỏng) ⇒ 'unknown'."""
    from gh.system_api import update

    d = _host_dir()
    raw = update._read_json(d / "autostart-status.json") if d.is_dir() else None
    if raw is None:
        return {"state": "unknown", "linger": "unknown", "linger_required": None, "docker_enabled": "unknown",
                "docker_mode": "unknown", "checked_at": None}
    linger = _pick(raw.get("linger"), AUTOSTART_YES_NO)
    required = raw.get("linger_required") if isinstance(raw.get("linger_required"), bool) else None
    docker = _pick(raw.get("docker_enabled"), AUTOSTART_YES_NO)
    mode = _pick(raw.get("docker_mode"), AUTOSTART_DOCKER_MODES)
    checked = _parse_ts(raw.get("checked_at"))
    problems = _autostart_problems(linger, required, docker, mode)
    # 'ok' chỉ khi Docker CHẮC tự chạy (yes/not_applicable) và linger ổn (có / không cần) — linger 'yes' một mình
    # không kéo lên 'ok' khi Docker còn 'unknown' (vd macOS Colima, docker info lỗi). Cùng điều kiện đóng sự cố.
    good = docker in ("yes", "not_applicable") and (linger in ("yes", "not_applicable") or required is False)
    state = "warn" if problems else ("ok" if good else "unknown")
    return {"state": state, "linger": linger, "linger_required": required, "docker_enabled": docker,
            "docker_mode": mode, "checked_at": _iso(checked)}


def _pick(value: Any, allowed: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _autostart_problems(linger: str, required: bool | None, docker: str, mode: str) -> list[str]:
    """Khoá của AUTOSTART_FIX cho từng vấn đề (đã lọc giá trị)."""
    problems: list[str] = []
    if docker == "no":
        problems.append("docker_rootless" if mode == "rootless" else "docker_system")
    if required is True and linger == "no":
        problems.append("linger_rootless" if mode == "rootless" else "linger")
    return problems


#: v0.1.53 (F-99): thân sự cố `host.nightly` — chỉ ghép từ chuỗi cố định (run/ là 0777, không tin cậy; không lệnh/chữ
#: lấy từ tệp). Mỗi câu kết thúc bằng lệnh: KHÔNG thêm dấu chấm sau lệnh (Sếp chép nguyên dòng).
NIGHTLY_BODY = "Máy chủ không tự lên bản mới. Trên máy chủ chạy: genh auto-update status"
NIGHTLY_LINGER = ("Tiến trình nền chỉ chạy khi có người đăng nhập — chạy một lần: "
                  "sudo loginctl enable-linger $USER")
NIGHTLY_ENABLE = "Sau đó bật lại lịch: genh auto-update enable"
#: Sếp đã chủ động tắt (`genh auto-update disable` / `install --no-auto-update`) mà lịch vẫn bật (tắt hụt, hoặc bật
#: lại tay bằng systemctl/crontab): máy chủ VẪN tự lên bản mới ~03:00 — nói thật + hai lựa chọn, Sếp tự quyết.
NIGHTLY_OPTED_OUT_RUNNING_TITLE = "Sếp đã tắt tự cập nhật đêm nhưng lịch vẫn bật"
NIGHTLY_OPTED_OUT_RUNNING_BODY = ("Máy chủ vẫn tự lên bản mới khoảng 03:00. Trên máy chủ chạy: genh auto-update status"
                                  " · Muốn tắt hẳn: genh auto-update disable"
                                  " · Muốn giữ tự cập nhật: genh auto-update enable")
#: Cơ chế lịch đêm mà linger (systemd --user) KHÔNG chi phối: crontab/LaunchAgent/Task Scheduler chạy cả khi không ai
#: đăng nhập ⇒ khối `nightly` báo linger 'not_applicable', không nhắc `enable-linger` (lệnh đó không chữa được gì).
NIGHTLY_NO_LINGER_MECHANISMS = ("cron", "launchd", "schtasks")


def _nightly_eval(now: datetime) -> dict[str, Any]:
    """Khối `nightly` của /system/health từ `run/nightly-status.json` (genh ghi; đã lọc kiểu/tập giá trị
    ở `update.read_nightly`). Thêm `watcher` {state, reason, hint} CHỈ khi người gác yêu cầu (.path) không ok
    (v0.1.54: dự phòng quét mỗi phút / chưa có người nhận). `state`:
    - 'other': lịch đêm dùng chung của máy thuộc một bản cài KHÁC còn sống (owned_by_other) — bản cài này không tự lên
      bản mới theo lịch đó, mà `genh auto-update enable` ở đây cũng bị từ chối ⇒ chỉ báo (xám), không mở sự cố;
    - 'off': Sếp đã chủ động tắt (opted_out) VÀ lịch không còn bật;
    - 'warn': lịch đang tắt (`reason` 'disabled'), đã quá `NIGHTLY_STALE_HOURS` kể từ mốc = max(lần chạy cuối, lúc bật)
      ('stale'), hoặc Sếp đã tắt mà lịch VẪN bật ('opted_out_running' — máy vẫn tự lên bản mới, nói thật);
    - 'ok'; 'unknown' (tệp thiếu/hỏng = genh cũ, hoặc không đủ dữ liệu để kết luận).
    `days_since` = số ngày tròn kể từ mốc (36–48 giờ ⇒ 1). Linger: giá trị genh ghi trong nightly-status.json; chưa
    rõ thì lấy từ autostart-status.json; cơ chế không phải systemd --user (crontab/launchd/schtasks) ⇒
    'not_applicable'."""
    from gh.system_api import update

    d = _host_dir()
    raw = update.read_nightly(d)
    linger = "unknown"
    if raw is not None and raw["linger"] != "unknown":
        linger = raw["linger"]
    else:
        auto = update._read_json(d / "autostart-status.json")
        if auto is not None:
            linger = _pick(auto.get("linger"), AUTOSTART_YES_NO)
    if raw is not None and raw["mechanism"] in NIGHTLY_NO_LINGER_MECHANISMS:
        linger = "not_applicable"
    if raw is None:
        return {"state": "unknown", "reason": None, "last_run_at": None, "next_run_at": None, "days_since": None,
                "opted_out": None, "linger": linger, "checked_at": None}
    enabled, opted_out = raw["enabled"], raw["opted_out"]
    times = [t for t in (_parse_ts(raw["last_run_at"]), _parse_ts(raw["since"])) if t is not None]
    anchor = max(times) if times else None
    days = max(0, int((now - anchor).total_seconds() // 86400)) if anchor is not None else None
    stale = enabled is True and anchor is not None and now - anchor > timedelta(hours=NIGHTLY_STALE_HOURS)
    reason: str | None = None
    if opted_out is True and enabled is not True:
        state, days = "off", None
    elif raw["owned_by_other"] is True and enabled is not True:
        state, days = "other", None
    elif opted_out is True:
        # Dấu "Sếp đã tắt" có mà lịch vẫn bật: KHÔNG được nói "Tắt" (máy vẫn tự cập nhật lúc 03:00).
        state, reason = "warn", "opted_out_running"
    elif enabled is False:
        state, reason = "warn", "disabled"
    elif stale:
        state, reason = "warn", "stale"
    elif enabled is True and anchor is not None:
        state = "ok"
    else:
        state, days = "unknown", None
    block: dict[str, Any] = {
        "state": state, "reason": reason, "last_run_at": raw["last_run_at"], "next_run_at": raw["next_run_at"],
        "days_since": days, "opted_out": opted_out, "linger": linger, "checked_at": raw["checked_at"]}
    w = raw["watcher"]
    if w["state"] != "ok":
        # v0.1.54: người gác yêu cầu (.path) lỗi — đang chạy dự phòng quét mỗi phút ('fallback') hoặc chưa có người nhận
        # ('failed'). Chỉ có khối này khi không ok; câu gợi ý là chuỗi cố định của API (không lấy chữ từ tệp).
        block["watcher"] = {"state": w["state"], "reason": w["reason"], "hint": update.WATCHER_HINTS[w["reason"]]}
    return block


def _nightly_status(now: datetime | None = None) -> dict[str, Any]:
    """Khối `nightly` của /system/health — xem `_nightly_eval`."""
    return _nightly_eval(now or datetime.now(UTC))


def _gb(n: Any) -> str:
    try:
        v = float(n) / (1 << 30)
    except (TypeError, ValueError):
        return "?"
    # Giữ ",0" — cùng khuôn `fmtGb` của web (thẻ Sức khoẻ: "còn 3,0 GB"), một luồng một cách viết số.
    return f"{v:.1f}".replace(".", ",")


async def _org_backup_cfg(db: AsyncSession, org_id: uuid.UUID) -> Any:
    return (await db.execute(text("""SELECT settings ? 'backup' AS configured, created_at, timezone,
                                            settings->'backup'->>'frequency' AS frequency
                                     FROM core.organizations WHERE id = :o"""), {"o": org_id})).one_or_none()


async def _latest_backup() -> datetime | None:
    from gh import backup

    entries = await backup.list_backups()  # mới nhất trước — TÍNH CẢ bản pre-update/pre-restore
    return entries[0].taken_at if entries else None


def backup_stale_limit(frequency: str | None) -> tuple[timedelta, str]:
    """(hạn, chữ hiện cho Sếp) theo tần suất sao lưu đã cấu hình — giá trị lạ/thiếu ⇒ hằng ngày."""
    return BACKUP_STALE_LIMITS.get(frequency or "daily", BACKUP_STALE_LIMITS["daily"])


def _backup_stale(configured: bool, latest: datetime | None, org_created: datetime | None, now: datetime,
                  frequency: str | None = None) -> bool:
    limit, _ = backup_stale_limit(frequency)
    if not configured:
        return False
    if latest is not None:
        return now - latest > limit
    return org_created is not None and now - org_created > limit


# ─── GET /system/health ───────────────────────────────────────────────────────────────────────────────────────

async def collect(db: AsyncSession, redis: Any, org_id: uuid.UUID, *, now: datetime | None = None,
                  started_at: datetime | None = None, is_owner: bool = True) -> dict[str, Any]:
    """Khuôn trả về của `GET /api/v1/system/health` — mọi trường là chuỗi/số/bool/null. Đọc một nguồn lỗi ⇒ phần
    đó 'unknown', KHÔNG ném 500."""
    now = now or datetime.now(UTC)

    worker: dict[str, Any] = {"state": "unknown", "alive": False, "last_seen_at": None, "silent_minutes": None}
    crons: list[dict[str, Any]] = []
    try:
        from arq.constants import default_queue_name, health_check_key_suffix

        crons = await _cron_runs(redis)
        last_seen = await _worker_last_seen(redis, crons)
        state, minutes = _worker_state(last_seen, now=now, started_at=started_at)
        alive = bool(await redis.exists(f"{default_queue_name}{health_check_key_suffix}"))
        worker = {"state": state, "alive": alive, "last_seen_at": _iso(last_seen), "silent_minutes": minutes}
    except Exception:  # noqa: BLE001 — Redis lỗi: phần này 'unknown'
        log.warning("Không đọc được trạng thái bộ xử lý nền", exc_info=True)

    browser: dict[str, Any] = {"state": "off", "last_heartbeat_at": None}
    try:
        from gh.social import service as social_service

        ws = await social_service.worker_state(redis)
        at = _parse_ts((ws or {}).get("at"))
        if ws is not None:
            fresh = at is not None and (now - at).total_seconds() <= BROWSER_SILENT_SECONDS
            browser = {"state": "ok" if fresh else "silent", "last_heartbeat_at": _iso(at)}
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được nhịp trình duyệt nền", exc_info=True)

    # v0.1.63 (H1): hộp riêng Antigravity — CHỈ có khối khi hộp đã được cấu hình (ok/down); chưa cài (bản cài cũ trước
    # `genh update`, dev) giữ nguyên khuôn cũ như 'bridge'/'autostart'.
    agy_box: dict[str, Any] = {"state": "not_installed", "checked_at": None, "agy_version": None}
    try:
        from gh.agybox.client import box_status

        st = await box_status()
        agy_box = {"state": st["state"], "checked_at": st["checked_at"], "agy_version": st["agy_version"]}
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được trạng thái hộp riêng Antigravity", exc_info=True)

    # F051: cầu nối tin nhắn — CHỈ có khối khi có nhịp hoặc có kênh đang "kết nối" (giữ khuôn cũ khi chưa dùng cầu nối).
    bridge: dict[str, Any] | None = None
    try:
        bridge = await _bridge_state(db, redis, org_id, now)
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được nhịp cầu nối tin nhắn", exc_info=True)

    queues: list[dict[str, Any]] = []
    try:
        queues = await _dlq_queues(redis)
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được hàng đợi lỗi (DLQ)", exc_info=True)

    backup: dict[str, Any] = {"configured": False, "latest_at": None, "age_hours": None, "stale": False,
                              "frequency": None, "stale_after": None}
    try:
        cfg = await _org_backup_cfg(db, org_id)
        configured = bool(cfg and cfg.configured)
        frequency = cfg.frequency if cfg and cfg.frequency in BACKUP_STALE_LIMITS else ("daily" if configured else None)
        latest = await _latest_backup()
        backup = {"configured": configured, "latest_at": _iso(latest),
                  "age_hours": round((now - latest).total_seconds() / 3600, 1) if latest else None,
                  "stale": _backup_stale(configured, latest, cfg.created_at if cfg else None, now, frequency),
                  "frequency": frequency, "stale_after": backup_stale_limit(frequency)[1] if configured else None}
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được danh mục sao lưu", exc_info=True)

    update: dict[str, Any] = {"state": "unknown", "stalled_reason": None, "failed": False, "interrupted": None,
                              "blocked_version": None, "finished_at": None}
    disk: dict[str, Any] = {"state": "unknown", "free_bytes": None, "min_bytes": None, "checked_at": None}
    try:
        from gh.system_api import update as upd

        if _host_dir().is_dir():
            st = upd._state()
            update = {"state": str(st.get("state") or "unknown"), "stalled_reason": st.get("stalled_reason"),
                      "failed": _update_failed_recent(st, now),
                      # GH-E94B dừng gọn (không dở dang): thẻ Sức khoẻ hiện "bị dừng giữa chừng" (vàng), không đỏ.
                      "interrupted": st.get("interrupted") if _update_failed_recent(st, now) else None,
                      "blocked_version": st.get("blocked_version"), "finished_at": st.get("finished_at")}
            ds = _disk_status() or {}
            disk_state = str(ds.get("state")) if ds.get("state") in ("ok", "low") else "unknown"
            disk = {"state": disk_state,
                    "free_bytes": ds.get("free_bytes") if isinstance(ds.get("free_bytes"), int) else None,
                    "min_bytes": ds.get("min_bytes") if isinstance(ds.get("min_bytes"), int) else None,
                    "checked_at": ds.get("checked_at") if isinstance(ds.get("checked_at"), str) else None}
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được hộp thư với genh", exc_info=True)

    autostart: dict[str, Any] | None = None
    try:
        if _host_dir().is_dir():  # bản phát triển không có hộp thư với genh ⇒ không có khối này
            autostart = _autostart_status()
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được trạng thái tự chạy lại khi bật máy", exc_info=True)

    # v0.1.53 (F-99): lịch tự cập nhật đêm — CHỈ khi có hộp thư với genh (khuôn cũ giữ nguyên khi không có).
    nightly: dict[str, Any] | None = None
    try:
        if _host_dir().is_dir():
            nightly = _nightly_status(now)
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được trạng thái lịch tự cập nhật đêm", exc_info=True)

    # v0.1.40 (F-12): bản sao ngoài máy — CHỈ khi có hộp thư với genh (khuôn cũ giữ nguyên khi không có).
    offsite: dict[str, Any] | None = None
    try:
        if _host_dir().is_dir():
            from gh.system_api import offsite as offsite_api

            ost = offsite_api.read_status(_host_dir(), now=now)
            cfg_o = await _org_backup_cfg(db, org_id)
            offsite = {"state": ost["state"], "configured": ost["configured"],
                       "last_success_at": ost["last_success_at"], "age_days": ost["age_days"],
                       "stale": _offsite_stale(ost, cfg_o.created_at if cfg_o else None, now),
                       "error_code": ost["error_code"], "schedule": ost["schedule"]}
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được trạng thái bản sao ngoài máy", exc_info=True)

    issues: list[dict[str, Any]] = []
    try:
        issues = await active_issues(db, org_id, is_owner=is_owner)
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được sự cố đang mở", exc_info=True)

    # v0.1.39: kết quả kiểm thật "Việc Sếp cần làm" (mới nhất mỗi mục) — chỉ để xem, KHÔNG tính vào 'overall'.
    # Đọc thuần (không chốt việc đọc Facebook đang chờ — việc đó ở GET /boss-checks). SAVEPOINT: lỗi đọc không làm
    # hỏng transaction của request. KHÔNG kèm `detail` (email đã che, dạng mã, công cụ thiếu…): /system/health mở cho
    # mọi vai trò có system.read (cả Auditor) trong khi "Việc Sếp cần làm" chỉ cho Owner.
    boss_checks: list[dict[str, Any]] = []
    try:
        from gh.boss_checks import service as boss_service

        async with db.begin_nested():
            checks = await boss_service.latest(db, org_id)
        boss_checks = [{"key": k, "status": v["status"], "error_code": v["error_code"],
                        "checked_at": v["checked_at"]}
                       for k, v in checks.items() if v is not None]
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được kết quả kiểm Việc Sếp cần làm", exc_info=True)

    bad = (any(i["severity"] == "bad" for i in issues) or worker["state"] == "silent"
           or (bridge is not None and bridge["state"] == "silent")
           or (update["failed"] and not update["interrupted"]) or disk["state"] == "low" or backup["stale"])
    warn = (any(i["severity"] == "warn" for i in issues) or browser["state"] == "silent" or update["failed"]
            or agy_box["state"] == "down"
            or any(q["dlq"] > 0 for q in queues) or any(c["ok"] is False for c in crons)
            or (autostart is not None and autostart["state"] == "warn")
            or (nightly is not None and nightly["state"] == "warn")
            or (offsite is not None and offsite["stale"]))
    out: dict[str, Any] = {
        "checked_at": _iso(now),
        "overall": "bad" if bad else ("warn" if warn else "ok"),
        "worker": worker,
        "browser": browser,
        "queues": queues,
        "crons": [{"name": c["name"], "last_at": c["last_at"], "ok": c["ok"]} for c in crons],
        "backup": backup,
        "update": update,
        "disk": disk,
        "issues": issues,
    }
    if bridge is not None:
        out["bridge"] = bridge
    if agy_box["state"] != "not_installed":
        out["agy_box"] = agy_box
    if boss_checks:
        # Chỉ khi đã có ít nhất một lần kiểm (như 'autostart': khuôn cũ giữ nguyên khi chưa có gì để xem).
        out["boss_checks"] = boss_checks
    if autostart is not None:
        out["autostart"] = autostart
    if nightly is not None:
        out["nightly"] = nightly
    if offsite is not None:
        out["offsite"] = offsite
    return out


# ─── vòng theo dõi: mở/đóng sự cố định kỳ ────────────────────────────────────────────────────────────────────

def _update_failed_recent(st: dict[str, Any], now: datetime) -> bool:
    """Lần cập nhật lỗi còn là sự cố: state 'failed' VÀ `finished_at` trong 26 giờ — cùng điều kiện với thẻ cập nhật
    ở web (updateModel.ts `recent`): thiếu/hỏng `finished_at` ⇒ không tính."""
    if st.get("state") != "failed":
        return False
    finished = _parse_ts(st.get("finished_at"))
    return finished is not None and now - finished < UPDATE_FAILED_RECENT


_GH_CODE = re.compile(r"GH-E[0-9A-F]{3}")


def _update_fingerprint(st: dict[str, Any]) -> str:
    """Dấu vân của sự cố update.failed: bản đích + mã lỗi genh (GH-Exxx cuối thông điệp) + mức độ — KHÔNG gồm giờ kết
    thúc (F104: lịch đêm thử lại cùng một lỗi mỗi đêm từng sinh thêm một chuông đỏ mỗi sáng). Lỗi khác mã hoặc kết cục
    nặng hơn (quay về bản cũ cũng lỗi) thì dấu vân đổi ⇒ vẫn có chuông mới."""
    codes = _GH_CODE.findall(st.get("message") or "") if isinstance(st.get("message"), str) else []
    if st.get("interrupted"):
        level = f"interrupted-{st['interrupted']}"
    elif st.get("blocked_rollback_failed") is True:
        level = "rollback_failed"
    elif st.get("blocked_version"):
        level = "rolled_back"
    else:
        level = "failed"
    return f"{st.get('to') or ''}|{codes[-1] if codes else 'no_code'}|{level}"


async def _eval_update(db: AsyncSession, org_id: uuid.UUID, redis: Any, now: datetime) -> None:
    from gh.system_api import update as upd

    if not _host_dir().is_dir():
        return
    st = upd._state()
    state = st.get("state")
    if _update_failed_recent(st, now):
        target = st.get("to") or "bản mới"
        interrupted = st.get("interrupted")
        fingerprint = _update_fingerprint(st)
        if interrupted:
            # GH-E94B (máy tắt/khởi động lại/bị dừng tay) mà không dở dang: không phải bản mới hỏng ⇒ 'warn', không
            # gióng chuông đỏ "chưa thành công" trái với thẻ cập nhật ("Đây không phải lỗi của bản mới").
            if interrupted == "resume":
                body = "Máy tắt giữa lúc cập nhật — cần chạy lại để hoàn tất. Bấm để thử lại ngay."
            elif st.get("auto_update_enabled") is True:
                body = "Bản đang dùng vẫn chạy bình thường — lịch đêm sẽ tự thử lại, hoặc bấm để thử lại ngay."
            else:
                body = "Bản đang dùng vẫn chạy bình thường — bấm để thử lại."
            await raise_once(db, org_id, key="update.failed", kind="update.failed", severity="warn",
                             title=f"Cập nhật lên {target} bị dừng giữa chừng", body=body, link=STORAGE_LINK,
                             fingerprint=fingerprint, redis=redis)
            return
        if st.get("blocked_rollback_failed") is True:
            body = "Tự quay về bản cũ cũng lỗi — cần hỗ trợ ngay."
        elif st.get("blocked_version"):
            body = "Hệ thống đã tự quay về bản cũ, dữ liệu an toàn. Bấm để xem và thử lại."
        else:
            body = "Bấm để xem chi tiết và thử lại."
        await raise_once(db, org_id, key="update.failed", kind="update.failed", severity="bad",
                         title=f"Cập nhật lên {target} chưa thành công", body=body, link=STORAGE_LINK,
                         fingerprint=fingerprint, redis=redis)
    elif state in ("done", "idle", "failed"):  # 'failed' quá hạn: thẻ cập nhật đã thôi báo ⇒ đóng sự cố
        # Chỉ 'done' là đã xử lý thật; 'idle'/'failed' quá hạn là tự hết hạn, chưa chắc đã hết vấn đề ⇒ không dán nhãn.
        await clear(db, org_id, "update.failed", label=(state == "done"), redis=redis)


async def _eval_backup(db: AsyncSession, org_id: uuid.UUID, redis: Any, now: datetime) -> None:
    from zoneinfo import ZoneInfo

    cfg = await _org_backup_cfg(db, org_id)
    if cfg is None:
        await clear(db, org_id, "backup.stale", redis=redis)
        return
    if not cfg.configured:
        # F103: chưa có lịch sao lưu (Sếp rời thiết lập trước bước 11) thì KHÔNG có bản sao lưu tự động nào — trước đây
        # chỉ có dòng vàng "Chưa cấu hình" nằm sâu trong Cài đặt. Quá `BACKUP_UNCONFIGURED_GRACE` kể từ lúc tạo tổ
        # chức ⇒ sự cố backup.stale có chuông (tổ chức mới cài chưa kịp đi tới bước 11 thì chưa báo); có lịch ⇒ tự đóng.
        if cfg.created_at is not None and now - cfg.created_at > BACKUP_UNCONFIGURED_GRACE:
            await raise_once(db, org_id, key="backup.stale", kind="backup.stale", severity="warn",
                             title="Chưa đặt lịch sao lưu tự động",
                             body="Máy chưa tự sao lưu — hỏng ổ đĩa là mất dữ liệu. Mở mục Sao lưu, chọn tần suất và "
                                  "giờ chạy rồi bấm lưu.",
                             link=BACKUP_LINK, fingerprint="not_configured", redis=redis)
        else:
            await clear(db, org_id, "backup.stale", redis=redis)
        return
    latest = await _latest_backup()
    limit_text = backup_stale_limit(cfg.frequency)[1]
    if not _backup_stale(True, latest, cfg.created_at, now, cfg.frequency):
        await clear(db, org_id, "backup.stale", redis=redis)
        return
    if latest is not None:
        try:
            tz: Any = ZoneInfo(cfg.timezone or "UTC")
        except (ValueError, KeyError):
            tz = UTC
        last = f"Bản gần nhất lúc {latest.astimezone(tz):%d/%m %H:%M}."
    else:
        last = "Chưa có bản nào."
    await raise_once(db, org_id, key="backup.stale", kind="backup.stale", severity="bad",
                     title=f"Đã hơn {limit_text} chưa có bản sao lưu mới",
                     body=f"{last} Mở mục Sao lưu và bấm Sao lưu ngay để giữ an toàn dữ liệu.", link=BACKUP_LINK,
                     redis=redis)


async def _eval_worker(db: AsyncSession, org_id: uuid.UUID, redis: Any, now: datetime,
                       started_at: datetime | None) -> None:
    state, minutes = _worker_state(await _worker_last_seen(redis), now=now, started_at=started_at)
    if state == "silent":
        await raise_once(db, org_id, key="worker.silent", kind="worker.silent", severity="bad",
                         title=f"Bộ xử lý nền đã ngừng {minutes} phút",
                         body="Sàng lọc tin, nhắc việc và sao lưu theo lịch đang dừng. "
                              "Bấm để xem cách khởi động lại.", link=STORAGE_LINK, redis=redis)
    elif state == "ok":
        await clear(db, org_id, "worker.silent", redis=redis)


async def _eval_bridge(db: AsyncSession, org_id: uuid.UUID, redis: Any, now: datetime) -> None:
    """F051: cầu nối tin nhắn im quá `BRIDGE_SILENT_MINUTES` mà còn kênh đang "kết nối" ⇒ sự cố `bridge.silent`
    (chuông + dải "Cần Sếp xử lý"). Có nhịp lại / không còn kênh nào cần cầu nối ⇒ đóng."""
    st = await _bridge_state(db, redis, org_id, now)
    if st is not None and st["state"] == "silent":
        await raise_once(db, org_id, key="bridge.silent", kind="bridge.silent", severity="bad",
                         title=f"Bộ nhận tin Zalo/WhatsApp đã ngừng {st['silent_minutes']} phút",
                         body="Tin khách đang KHÔNG vào được Gen-Harness và bản nháp đã duyệt có thể hết hạn trước khi "
                              "gửi, dù thẻ kênh vẫn ghi \"Đang kết nối\". Bấm để xem cách khởi động lại.",
                         link=HEALTH_LINK, redis=redis)
    else:
        await clear(db, org_id, "bridge.silent", redis=redis)


async def sync_dlq_alert(db: AsyncSession, org_id: uuid.UUID, redis: Any) -> None:
    """F064/F102: hàng lỗi tin khách vào (`DLQ_INBOUND`) > 0 ⇒ sự cố `dlq.inbound` (một chuông tới khi hết; số tin đổi
    thì chỉ làm mới chữ). Hết sạch (phát lại / xoá) ⇒ đóng ngay. Bên gọi commit."""
    n = int(await redis.xlen(DLQ_INBOUND)) if await redis.exists(DLQ_INBOUND) else 0
    if n > 0:
        await raise_once(db, org_id, key="dlq.inbound", kind="dlq.inbound", severity="warn",
                         title=f"{n} tin khách chưa nhập kho được",
                         body="Mỗi tin đã thử nhập 5 lần mà vẫn lỗi nên nằm trong hàng lỗi — chưa mất. Bấm để xem và "
                              "phát lại; phát lại vẫn lỗi thì gửi kèm khi báo lỗi (Trợ giúp › Báo lỗi).",
                         link=HEALTH_LINK, redis=redis)
    else:
        await clear(db, org_id, "dlq.inbound", redis=redis)


class DlqError(Exception):
    """Phát lại/xoá hàng lỗi không làm được; `code` ∈ {'unknown', 'not_replayable'} (route đổi sang lỗi HTTP)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def _dlq_name(redis: Any, stream: str) -> str:
    """Tên hàng lỗi của `stream` nếu là hàng đã ghi nhận (tập `gh:dlq:streams`) — không nhận tên tuỳ ý từ URL."""
    from gh.chassis.bus import DLQ_STREAMS_KEY

    await _discover(redis)
    name = f"{stream}.dlq"
    if not stream or not await redis.sismember(DLQ_STREAMS_KEY, name) or not await redis.exists(name):
        raise DlqError("unknown")
    return name


async def dlq_replay(redis: Any, stream: str) -> dict[str, int]:
    """Phát lại tối đa `DLQ_REPLAY_BATCH` việc trong hàng lỗi của `stream` (chỉ `DLQ_REPLAYABLE`): chép sang stream gốc
    (bỏ `error`/`group`) rồi xoá khỏi hàng lỗi — chép TRƯỚC xoá SAU nên không mất tin; lỡ dở thì tin nhập kho lại bị bỏ
    qua vì trùng mã tin ngoài (`ingest_message` idempotent). Trả {"replayed", "remaining"}."""
    from gh.chassis.bus import DLQ_STREAMS_KEY

    name = await _dlq_name(redis, stream)
    if stream not in DLQ_REPLAYABLE:
        raise DlqError("not_replayable")
    replayed = 0
    for entry_id, fields in await redis.xrange(name, min="-", max="+", count=DLQ_REPLAY_BATCH):
        keep = {k: v for k, v in fields.items() if _s(k) not in ("error", "group")}
        if keep:
            await redis.xadd(stream, keep, maxlen=100_000, approximate=True)
        await redis.xdel(name, entry_id)
        replayed += 1
    remaining = int(await redis.xlen(name))
    if remaining == 0:
        await redis.srem(DLQ_STREAMS_KEY, name)
    return {"replayed": replayed, "remaining": remaining}


async def dlq_clear(redis: Any, stream: str) -> dict[str, int]:
    """"Đã xem, xoá": XTRIM hàng lỗi của `stream` tới mục mới nhất TẠI LÚC BẤM (MINID = mã đó + 1) — việc lỗi mới tới
    sau đó vẫn còn. Trả {"cleared", "remaining"}."""
    from gh.chassis.bus import DLQ_STREAMS_KEY

    name = await _dlq_name(redis, stream)
    last = await redis.xrevrange(name, count=1)
    if not last:
        return {"cleared": 0, "remaining": 0}
    ms, _, seq = _s(last[0][0]).partition("-")
    before = int(await redis.xlen(name))
    await redis.xtrim(name, minid=f"{ms}-{int(seq or 0) + 1}", approximate=False)
    remaining = int(await redis.xlen(name))
    if remaining == 0:
        await redis.srem(DLQ_STREAMS_KEY, name)
    return {"cleared": before - remaining, "remaining": remaining}


async def _eval_disk(db: AsyncSession, org_id: uuid.UUID, redis: Any) -> None:
    ds = _disk_status()
    if ds is None:
        return
    if ds.get("state") == "low":
        await raise_once(db, org_id, key="disk.low", kind="disk.low", severity="bad", title="Ổ đĩa sắp hết chỗ",
                         body=f"Còn {_gb(ds.get('free_bytes'))} GB trống, cần tối thiểu {_gb(ds.get('min_bytes'))} GB"
                              " — cập nhật tự động đang tạm dừng.", link=STORAGE_LINK, redis=redis)
    elif ds.get("state") == "ok":
        await clear(db, org_id, "disk.low", redis=redis)


async def _eval_autostart(db: AsyncSession, org_id: uuid.UUID, redis: Any) -> None:
    """F-73: máy chủ chưa tự chạy lại Gen-Harness khi bật máy (Docker chưa enable / thiếu linger). Thân thông báo chỉ
    ghép từ chuỗi cố định AUTOSTART_FIX; fingerprint = tập vấn đề (đổi vấn đề ⇒ chuông mới). 'unknown' ⇒ để nguyên."""
    st = _autostart_status()
    problems = _autostart_problems(st["linger"], st["linger_required"], st["docker_enabled"], st["docker_mode"])
    if problems:
        # Đích: thẻ "Sức khoẻ hệ thống" (Dữ liệu & lưu trữ) — hướng dẫn từng bước, lệnh dạng mã chép được (thân chuông
        # bị cắt còn 2 dòng, không đủ chỗ cho lệnh). Câu cuối nói cách làm cảnh báo tự hết.
        body = AUTOSTART_SEP.join([*(AUTOSTART_FIX[p] for p in problems), AUTOSTART_DONE])
        await raise_once(db, org_id, key="host.autostart", kind="host.autostart", severity="warn",
                         title=AUTOSTART_TITLE, body=body, link=STORAGE_LINK,
                         fingerprint="|".join(sorted(problems)), redis=redis)
        return
    good = ("yes", "not_applicable")
    linger_ok = st["linger"] in good or st["linger_required"] is False
    if st["docker_enabled"] in good and linger_ok:  # đã biết chắc không còn vấn đề; còn 'unknown' ⇒ để nguyên
        await clear(db, org_id, "host.autostart", redis=redis)


async def _eval_nightly(db: AsyncSession, org_id: uuid.UUID, redis: Any, now: datetime) -> None:
    """F-99: lịch tự cập nhật đêm không chạy — đang tắt (không do Sếp chủ động tắt) hoặc im quá `NIGHTLY_STALE_HOURS`;
    hoặc Sếp đã tắt mà lịch vẫn bật (máy vẫn tự lên bản mới). Thân chỉ ghép từ chuỗi cố định; fingerprint = tình trạng
    (disabled | linger | stale | opted_out_running) ⇒ số ngày tăng dần không sinh chuông mới, đổi nguyên nhân mới có
    chuông mới. 'off' (Sếp đã tắt) / 'other' (lịch của bản cài khác) / 'ok' ⇒ đóng; 'unknown' (genh cũ chưa ghi tệp)
    ⇒ để nguyên. Chỉ khi có hộp thư."""
    if not _host_dir().is_dir():
        return
    st = _nightly_eval(now)
    if st["state"] == "warn":
        days = st["days_since"] or 0
        if st["reason"] == "opted_out_running":
            title, fingerprint, body = (NIGHTLY_OPTED_OUT_RUNNING_TITLE, "opted_out_running",
                                        NIGHTLY_OPTED_OUT_RUNNING_BODY)
        else:
            if st["reason"] == "disabled":
                title, fingerprint = "Lịch tự cập nhật đêm đang tắt", "disabled"
            else:
                title = ("Lịch tự cập nhật đêm đã hơn 1 ngày chưa chạy" if days < 2
                         else f"Lịch tự cập nhật đêm chưa chạy {days} ngày")
                fingerprint = "linger" if st["linger"] == "no" else "stale"
            parts = [NIGHTLY_BODY]
            if st["linger"] == "no":  # 'not_applicable' khi lịch là crontab/launchd/schtasks (xem `_nightly_eval`)
                parts.append(NIGHTLY_LINGER)
            parts.append(NIGHTLY_ENABLE)
            body = AUTOSTART_SEP.join(parts)
        await raise_once(db, org_id, key="host.nightly", kind="host.nightly", severity="warn", title=title,
                         body=body, link=HEALTH_LINK, fingerprint=fingerprint, redis=redis)
    elif st["state"] in ("ok", "off", "other"):
        await clear(db, org_id, "host.nightly", redis=redis)


ACCESS_LINK = "/system?tab=storage&focus=access"
OPEN_LAN_TITLE = "Cổng đang mở cho cả mạng"
# Thẻ đích chỉ có LỆNH chạy trên máy chủ (không có nút chọn một chạm) ⇒ "Bấm để xem lệnh…", không hứa "Bấm để chọn".
OPEN_LAN_BODY = ("Mọi máy cùng mạng (Wi-Fi văn phòng, khách…) đều thấy trang đăng nhập Gen-Harness. Bấm để xem lệnh "
                 "chọn cách truy cập (chạy trên máy chủ): Tailscale (khuyên dùng), chỉ máy này, hoặc giữ mở cho mạng "
                 "nội bộ.")


async def _eval_network(db: AsyncSession, org_id: uuid.UUID, redis: Any) -> None:
    """v0.1.46 (F-21): bản cài cũ chưa chọn cách truy cập (`run/network-status.json` mode lan_legacy + bind 0.0.0.0) ⇒
    MỘT chuông 'Cổng đang mở cho cả mạng' (fingerprint cố định ⇒ không lặp). Tệp hợp lệ báo chế độ khác (kể cả 'lan' do
    Owner tự chọn) ⇒ đóng; tệp thiếu/hỏng/chế độ lạ ⇒ để nguyên. Không có nút 'bỏ qua': Owner chấp nhận bằng
    `genh remote --lan`."""
    from gh.system_api import access

    st = access.network_status()
    if st is None or st["mode"] == "unknown":
        return
    if st["mode"] == "lan_legacy" and st["bind_addr"] == "0.0.0.0":
        await raise_once(db, org_id, key="network.open_lan", kind="network.open_lan", severity="warn",
                         title=OPEN_LAN_TITLE, body=OPEN_LAN_BODY, link=ACCESS_LINK, fingerprint="lan_legacy",
                         redis=redis)
    else:
        await clear(db, org_id, "network.open_lan", redis=redis)


def _offsite_stale(st: dict[str, Any], org_created: datetime | None, now: datetime) -> bool:
    """Bản sao ngoài máy đáng nhắc: chưa có lần thành công / cũ hơn `OFFSITE_STALE_AFTER` (7 ngày + ân hạn) — nhưng tổ
    chức mới tạo chưa có lần nào thì chưa nhắc trong cùng khoảng đó (vừa cài xong, chưa kịp cắm ổ)."""
    last = _parse_ts(st.get("last_success_at"))
    limit = OFFSITE_STALE_AFTER
    if last is not None:
        return now - last > limit
    return org_created is not None and now - org_created > limit


#: Mã lỗi offsite cùng nguyên nhân với offsite.stale (chưa chọn nơi lưu / chưa thấy ổ) — F104: khi bản sao đã cũ thì
#: không mở thêm offsite.failed, cách chữa của hai sự cố là một.
OFFSITE_SAME_CAUSE = frozenset({"GH-EB00", "GH-EB01"})

#: Thân chuông offsite.failed theo mã genh — chuỗi cố định (không lấy chữ từ run/).
OFFSITE_FAILED_BODY = {
    "GH-EB00": "Chưa chọn nơi lưu — bấm 'Chọn nơi lưu bản sao ngoài máy'",
    "GH-EB01": "Chưa thấy ổ USB/NAS — cắm lại ổ rồi bấm 'Sao lưu ra ổ ngoài ngay'",
    "GH-EB02": "Không xuất được gói dữ liệu — bấm 'Sao lưu ra ổ ngoài ngay' để thử lại",
    "GH-EB03": "Bản sao vừa tạo không đọc lại được — chưa có bản sao ngoài máy. Bấm 'Sao lưu ra ổ ngoài ngay' để "
               "thử lại",
    "GH-EB04": "Không ghi được vào ổ ngoài — kiểm tra ổ còn chỗ trống và cho phép ghi",
    "GH-EB05": "Máy chủ đang cập nhật/khôi phục nên lần sao lưu ra ổ ngoài bị bỏ qua — sẽ thử lại sau",
    "GH-EB06": "Dịch vụ Gen-Harness chưa chạy nên chưa sao lưu ra ổ ngoài được",
    "GH-EB07": "Nơi lưu không hợp lệ — chọn lại thư mục trên ổ USB/NAS",
    # v0.1.53 (F-97): cùng câu với gh/system_api/offsite.py ERROR_MESSAGES — sửa quyền, không phải cắm ổ/thử lại.
    "GH-E94C": "Máy chủ không xoá được tệp yêu cầu — chưa làm gì. Nhờ người quản trị kiểm quyền thư mục run/request "
               "trong thư mục cài đặt rồi thử lại",
}
OFFSITE_FAILED_GENERIC = "Lần sao lưu ra ổ ngoài gần nhất chưa thành công — bấm để xem chi tiết và thử lại"


async def _eval_offsite(db: AsyncSession, org_id: uuid.UUID, redis: Any, now: datetime) -> None:
    """F-12: (a) chưa chọn nơi lưu sau 7 ngày, (b) bản sao ngoài máy cũ > 7 ngày ('bad' khi > 30 ngày) ⇒ offsite.stale;
    (c) lần thử cuối lỗi (failed/not_mounted, sau lần thành công gần nhất) ⇒ offsite.failed. Chỉ khi có hộp thư."""
    from gh.system_api import offsite as offsite_api

    d = _host_dir()
    if not d.is_dir():
        return
    st = offsite_api.read_status(d, now=now)
    cfg = await _org_backup_cfg(db, org_id)
    created = cfg.created_at if cfg else None

    stale = _offsite_stale(st, created, now)
    if stale:
        last = _parse_ts(st["last_success_at"])
        if not st["configured"]:
            await raise_once(db, org_id, key="offsite.stale", kind="offsite.stale", severity="warn",
                             title="Chưa có bản sao ngoài máy",
                             body="Hỏng ổ đĩa là mất hết dữ liệu. Cắm ổ USB hoặc chọn thư mục NAS rồi bấm "
                                  "'Chọn nơi lưu bản sao ngoài máy'",
                             link=OFFSITE_LINK, fingerprint="not_configured", redis=redis)
        elif last is None:
            await raise_once(db, org_id, key="offsite.stale", kind="offsite.stale", severity="warn",
                             title="Chưa có bản sao ngoài máy",
                             body="Đã chọn nơi lưu nhưng chưa có lần nào thành công — cắm ổ rồi bấm "
                                  "'Sao lưu ra ổ ngoài ngay'",
                             link=OFFSITE_LINK, fingerprint="never", redis=redis)
        else:
            days = int((now - last).total_seconds() // 86400)
            severity = "bad" if days > OFFSITE_BAD_DAYS else "warn"
            await raise_once(db, org_id, key="offsite.stale", kind="offsite.stale", severity=severity,
                             title=f"Bản sao ngoài máy đã cũ {days} ngày",
                             body="Cắm ổ USB/NAS rồi bấm 'Sao lưu ra ổ ngoài ngay' để có bản sao mới ngoài máy chủ",
                             link=OFFSITE_LINK, fingerprint=severity, redis=redis)
    else:
        await clear(db, org_id, "offsite.stale", redis=redis)

    attempt = _parse_ts(st["last_attempt_at"])
    success = _parse_ts(st["last_success_at"])
    failed = (st["state"] in ("failed", "not_mounted") and attempt is not None
              and (success is None or attempt > success))
    code = st["error_code"] or ("GH-EB01" if st["state"] == "not_mounted" else "")
    if failed and stale and code in OFFSITE_SAME_CAUSE:
        # F104: ổ chưa cắm/chưa chọn nơi lưu VÀ bản sao đã cũ là MỘT nguyên nhân, MỘT cách chữa ⇒ một sự cố (stale),
        # không hai dòng + hai chuông. Dòng failed đang mở (mở lúc bản sao chưa đủ cũ) đóng gộp vào stale.
        await clear(db, org_id, "offsite.failed", label=False, redis=redis)
    elif failed:
        await raise_once(db, org_id, key="offsite.failed", kind="offsite.failed", severity="warn",
                         title="Sao lưu ra ổ ngoài chưa thành công",
                         body=OFFSITE_FAILED_BODY.get(code, OFFSITE_FAILED_GENERIC), link=OFFSITE_LINK,
                         fingerprint=f"{st['last_attempt_at'] or ''}|{st['error_code'] or ''}", redis=redis)
    elif st["state"] != "running":  # đang chạy ⇒ chưa biết kết quả, để nguyên
        await clear(db, org_id, "offsite.failed", redis=redis)


async def raise_model_expired(db: AsyncSession, org_id: uuid.UUID, provider_id: uuid.UUID, name: str, *,
                              redis: Any = None) -> bool:
    """Sự cố "model hết xác thực" — một khoá mỗi nhà cung cấp (`model.auth_expired:<uuid>`). F007: câu chữ + đích bấm
    theo LOẠI nguồn — CLI (đăng nhập tài khoản) ⇒ Kết nối › Bộ não AI; khoá API (dán khoá) ⇒ API & Model, vì khoá API
    không có chuyện "đăng nhập" và trang Bộ não AI chỉ đọc."""
    from gh.providers.router import CLI_KINDS

    kind = (await db.execute(text("SELECT kind FROM agent.providers WHERE id = :i"),
                             {"i": provider_id})).scalar_one_or_none()
    if kind in CLI_KINDS:
        title = f"Model {name} cần đăng nhập lại"
        body = "Gen và sàng lọc tin có thể dừng nếu không còn model khác. Bấm để đăng nhập lại."
        link = MODEL_CLI_LINK
    else:
        title = f"Khoá API của {name} bị từ chối — thêm khoá mới"
        body = ("Khoá bị sai, hết hạn hoặc chưa có nên nhà cung cấp không nhận. Gen và sàng lọc tin có thể dừng nếu "
                "không còn model khác. Bấm để dán khoá mới ở API & Model.")
        link = MODEL_API_LINK
    return await raise_once(db, org_id, key=f"model.auth_expired:{provider_id}", kind="model.auth_expired",
                            severity="warn", title=title, body=body, link=link, redis=redis)


async def _eval_models(db: AsyncSession, org_id: uuid.UUID, redis: Any) -> None:
    """Model đang bật mà 'expired' nhưng chưa có sự cố — vd. đã hết hạn TRƯỚC khi lên v0.1.36, hoặc nút "Gọi thử"
    đổi sang 'expired' (lần 401 sau đó không đổi trạng thái nên `_set_auth_state` không mở). Khoá khử trùng lặp ⇒
    sự cố đang mở thì không chuông thêm."""
    rows = (await db.execute(text("""SELECT id, name FROM agent.providers
                                     WHERE org_id = :o AND is_enabled AND auth_state = 'expired'"""),
                             {"o": org_id})).all()
    for r in rows:
        await raise_model_expired(db, org_id, r.id, r.name, redis=redis)


#: v0.1.41 (F-84): đích chuông "Chi phí AI hôm nay vượt trần" — thẻ chi phí AI ở Hôm nay (dưới Sức khoẻ hệ thống).
AI_COST_LINK = "/overview?focus=ai-cost"


async def _eval_budget(db: AsyncSession, org_id: uuid.UUID, redis: Any, now: datetime) -> None:
    """Trần chi phí AI mỗi ngày (gh/ai_cost.py). Có trần và tổng hôm nay (giờ VN) > trần ⇒ mở sự cố `ai.budget`;
    fingerprint = ngày VN ⇒ mỗi ngày tối đa MỘT chuông (ngày hôm sau vẫn vượt ⇒ chuông mới). ≤ trần / bỏ trần ⇒ đóng."""
    from gh import ai_cost

    budget = await ai_cost.get_budget(db, org_id)
    day = ai_cost.today_vn(now)
    # Đóng sự cố do Sếp nâng/bỏ trần ngay trong ngày vượt ⇒ "Đã xử lý"; sự cố của NGÀY CŨ hết vì sang ngày mới (tổng chi
    # về 0) là tự hết, không ai xử lý ⇒ chỉ đánh dấu đã đọc (fingerprint của sự cố = ngày VN lúc mở).
    opened_day = (await db.execute(text("""SELECT fingerprint FROM ops.health_alerts
                                           WHERE org_id = :o AND key = 'ai.budget' AND cleared_at IS NULL"""),
                                   {"o": org_id})).scalar_one_or_none()
    handled = opened_day == day.isoformat()
    if budget is None:
        await clear(db, org_id, "ai.budget", label=handled, redis=redis)
        return
    total = (await ai_cost.day_cost(db, org_id, day))["total_vnd"]
    if total <= budget:
        await clear(db, org_id, "ai.budget", label=handled, redis=redis)
        return
    await raise_once(db, org_id, key="ai.budget", kind="ai.budget_exceeded", severity="warn",
                     fingerprint=day.isoformat(), title=f"Chi phí AI hôm nay vượt trần {ai_cost.fmt_vnd(budget)}",
                     body=f"Đã dùng {ai_cost.fmt_vnd(total)}. Xem agent nào tốn nhiều ở Hôm nay › Chi phí AI hôm nay; "
                          "đổi trần ở Bộ não AI.", link=AI_COST_LINK, redis=redis)


async def _eval_background_source(db: AsyncSession, org_id: uuid.UUID, redis: Any) -> None:
    """v0.1.41 (F-86): sự cố `ai.background_no_source` đang mở mà Sếp đã thêm khoá API (có model), đăng nhập Antigravity
    CLI (có model; v0.1.62 — chạy được việc nền) hoặc đã cho Claude Code CLI chạy việc nền ⇒ đóng ngay, không đợi một
    lượt việc nền chạy được (refinery rảnh / trực việc tắt thì có thể tới bản tin kế tiếp mới có lượt). Chỉ truy vấn
    nguồn khi sự cố đang mở."""
    from gh.providers.router import (
        BG_NO_SOURCE_FLAG,
        BG_NO_SOURCE_KEY,
        BG_NO_SOURCE_TRY,
        background_cli_allowed,
        has_agy_source,
        has_api_source,
    )

    open_ = (await db.execute(text("""SELECT 1 FROM ops.health_alerts
                                      WHERE org_id = :o AND key = :k AND cleared_at IS NULL"""),
                              {"o": org_id, "k": BG_NO_SOURCE_KEY})).first()
    if open_ is None:
        return
    if (await has_api_source(db, org_id) or await has_agy_source(db, org_id)
            or await background_cli_allowed(db, org_id)):
        await clear(db, org_id, BG_NO_SOURCE_KEY, redis=redis)
        await redis.delete(BG_NO_SOURCE_FLAG.format(org_id), BG_NO_SOURCE_TRY.format(org_id))


AGY_BOX_DOWN_TITLE = "Hộp riêng Antigravity chưa chạy"
AGY_BOX_DOWN_BODY = ("Việc có người ngoài (soạn trả lời khách, sàng lọc tin người lạ) tạm không dùng Antigravity và "
                     "chuyển sang nguồn AI khác. Thường tự hết sau khi hộp khởi động lại; nếu kéo dài, chạy "
                     "`genh start` trên máy chủ để dựng lại hộp (xem lỗi bằng `genh logs agy-box`).")


async def _eval_agy_box(db: AsyncSession, org_id: uuid.UUID, redis: Any, now: datetime) -> None:
    """v0.1.63 (H1): sự cố `agy_box.down` (vàng) khi hộp đã cấu hình mà không trả lời `/healthz` liên tục
    `AGY_BOX_DOWN_GRACE_SECONDS`. Hộp chạy lại hoặc chưa cài (`not_installed`) ⇒ đóng và quên mốc."""
    from gh.agybox.client import box_status

    st = await box_status()
    if st["state"] != "down":
        await redis.delete(AGY_BOX_DOWN_SINCE_KEY)
        await clear(db, org_id, "agy_box.down", redis=redis)
        return
    raw = await redis.get(AGY_BOX_DOWN_SINCE_KEY)
    since = _parse_ts(raw)
    if since is None:
        await redis.set(AGY_BOX_DOWN_SINCE_KEY, _iso(now))
        return
    if (now - since).total_seconds() >= AGY_BOX_DOWN_GRACE_SECONDS:
        await raise_once(db, org_id, key="agy_box.down", kind="agy_box.down", severity="warn", title=AGY_BOX_DOWN_TITLE,
                         body=AGY_BOX_DOWN_BODY, link=HEALTH_LINK, redis=redis)


async def _eval_events(db: AsyncSession, org_id: uuid.UUID, redis: Any) -> None:
    """Dọn dòng sự kiện cũ: kênh đã đăng nhập lại (hoặc không còn kênh loại đó dùng được — bị xoá, plugin cầu nối bị
    tắt) / model đã ổn (hoặc bị tắt, bị xoá) ⇒ đóng sự cố."""
    keys = (await db.execute(text("""SELECT key FROM ops.health_alerts WHERE org_id = :o AND cleared_at IS NULL
                                      AND (key LIKE 'channel.down:%' OR key LIKE 'model.auth_expired:%')"""),
                             {"o": org_id})).scalars().all()
    for key in keys:
        kind, _, ref = key.partition(":")
        if kind == "channel.down":
            live = (await db.execute(text("""
                SELECT 1 FROM core.channel_sessions s JOIN core.channels c ON c.id = s.channel_id
                WHERE c.org_id = :o AND c.type = :t AND s.state = 'active' AND s.ended_at IS NULL LIMIT 1"""),
                {"o": org_id, "t": ref})).first()
            usable = live is not None or (await db.execute(text("""
                SELECT 1 FROM core.channels c LEFT JOIN ops.plugins pl ON pl.id = c.plugin_id
                WHERE c.org_id = :o AND c.type = :t AND (c.plugin_id IS NULL OR pl.is_enabled) LIMIT 1"""),
                {"o": org_id, "t": ref})).first() is not None
            if live is not None or not usable:  # không còn kênh để "Đăng nhập lại" ⇒ dòng nút chết, đóng
                await clear(db, org_id, key, redis=redis)
        else:
            try:
                pid = uuid.UUID(ref)
            except ValueError:
                await clear(db, org_id, key, redis=redis)
                continue
            p = (await db.execute(text("SELECT is_enabled, auth_state FROM agent.providers WHERE id = :i"),
                                  {"i": pid})).one_or_none()
            if p is None or not p.is_enabled or p.auth_state != "expired":
                await clear(db, org_id, key, redis=redis)


async def evaluate(db: AsyncSession, redis: Any, org_id: uuid.UUID, *, now: datetime,
                   started_at: datetime | None) -> None:
    """Tính lại các sự cố theo dõi định kỳ và mở/đóng dòng tương ứng (chuông chỉ khi mới mở). Bên gọi commit.

    Mỗi phần chạy trong savepoint riêng: một nguồn lỗi (tệp hỏng, Redis tạm mất) không chặn các phần còn lại."""
    parts = (
        ("update.failed", lambda: _eval_update(db, org_id, redis, now)),
        ("backup.stale", lambda: _eval_backup(db, org_id, redis, now)),
        ("worker.silent", lambda: _eval_worker(db, org_id, redis, now, started_at)),
        ("bridge.silent", lambda: _eval_bridge(db, org_id, redis, now)),
        ("dlq.inbound", lambda: sync_dlq_alert(db, org_id, redis)),
        ("disk.low", lambda: _eval_disk(db, org_id, redis)),
        ("host.autostart", lambda: _eval_autostart(db, org_id, redis)),
        ("host.nightly", lambda: _eval_nightly(db, org_id, redis, now)),
        ("offsite", lambda: _eval_offsite(db, org_id, redis, now)),
        ("models", lambda: _eval_models(db, org_id, redis)),
        ("events", lambda: _eval_events(db, org_id, redis)),
        ("ai.budget", lambda: _eval_budget(db, org_id, redis, now)),
        ("ai.background_source", lambda: _eval_background_source(db, org_id, redis)),
        ("network", lambda: _eval_network(db, org_id, redis)),
        ("agy_box", lambda: _eval_agy_box(db, org_id, redis, now)),
        ("social.session", lambda: session_watch.evaluate_alerts(db, org_id, redis)),
    )
    from gh import notifications
    from gh.social import session_watch  # nạp trễ: session_watch import gh.health

    for name, run in parts:
        mark = notifications.pending_mark(db)
        try:
            async with db.begin_nested():
                await run()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — một phần lỗi không làm hỏng cả lượt
            notifications.pending_reset(db, mark)
            log.exception("Theo dõi sức khoẻ: phần %s lỗi", name)


# ─── ảnh chụp sức khoẻ cho Trực canh máy chủ (v0.1.44, F-6b) ────────────────────────────────────────────────

#: run/api-health.json — genh watchdog đọc (tươi khi written_at ≤ 10 phút) để gửi cảnh báo Telegram cả những sự cố chỉ
#: api thấy (channel.down, model.auth_expired, telegram.failed…). api KHÔNG gửi Telegram cho sự cố (tránh gửi đôi).
API_HEALTH_FILE = "api-health.json"


async def write_host_snapshot(db: AsyncSession, org_id: uuid.UUID, *, now: datetime) -> bool:
    """Ghi nguyên tử run/api-health.json: {schema, written_at, version, public_url, alerts[key, kind, severity, title,
    body, fingerprint, raised_at], latest_backup_at, backup_stale_limit_hours}. Không có hộp thư ⇒ bỏ qua. Lỗi chỉ
    log, không ném."""
    from gh import __version__
    from gh.config import get_settings
    from gh.telegram.service import write_json_atomic

    try:
        d = _host_dir()
        if not d.is_dir():
            return False
        rows = (await db.execute(text("""
            SELECT key, kind, severity, title, body, fingerprint, raised_at FROM ops.health_alerts
            WHERE org_id = :o AND cleared_at IS NULL
            ORDER BY (severity = 'bad') DESC, raised_at DESC"""), {"o": org_id})).all()
        cfg = await _org_backup_cfg(db, org_id)
        latest: datetime | None = None
        try:
            latest = await _latest_backup()
        except Exception:  # noqa: BLE001 — danh mục sao lưu lỗi ⇒ null, vẫn ghi phần còn lại
            log.warning("Ảnh chụp sức khoẻ: không đọc được danh mục sao lưu", exc_info=True)
        limit, _ = backup_stale_limit(cfg.frequency if cfg else None)
        data = {
            "schema": 1,
            "written_at": _iso(now),
            "version": __version__,
            "public_url": get_settings().public_url.rstrip("/"),
            "alerts": [{"key": r.key, "kind": r.kind, "severity": r.severity, "title": r.title, "body": r.body,
                        "fingerprint": r.fingerprint or "", "raised_at": _iso(r.raised_at)} for r in rows],
            "latest_backup_at": _iso(latest),
            "backup_stale_limit_hours": int(limit.total_seconds() // 3600),
        }
        write_json_atomic(d / API_HEALTH_FILE, data)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — ảnh chụp là phụ, không làm hỏng vòng theo dõi
        log.warning("Không ghi được run/%s", API_HEALTH_FILE, exc_info=True)
        return False


async def watch_loop(sm: Any, redis: Any, stop: asyncio.Event, *, interval: float, started_at: datetime) -> None:
    """Vòng theo dõi chạy trong api: chờ `interval` giây đầu rồi mỗi `interval` giây gọi `evaluate` cho từng tổ chức.
    Nhiều tiến trình api ⇒ khoá Redis `WATCH_LOCK_KEY` để chỉ một bản chạy mỗi lượt. Không chết vì một lượt lỗi
    (khuôn `gh.app._permit_sweep_loop`). v0.1.44 (F-6b): sau mỗi lượt (đang giữ khoá) ghi run/api-health.json cho
    tổ chức đầu tiên (`write_host_snapshot`)."""
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            if not await redis.set(WATCH_LOCK_KEY, b"1", nx=True, ex=max(1, int(interval - 5))):
                continue
            async with sm() as db:
                orgs = (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at"))
                        ).scalars().all()
            for org_id in orgs:
                async with sm() as db:
                    await evaluate(db, redis, org_id, now=datetime.now(UTC), started_at=started_at)
                    await db.commit()
            if orgs:
                async with sm() as db:
                    await write_host_snapshot(db, orgs[0], now=datetime.now(UTC))
                    await db.rollback()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — vòng theo dõi không được chết vì một lượt lỗi
            log.exception("Vòng theo dõi sức khoẻ lỗi")
