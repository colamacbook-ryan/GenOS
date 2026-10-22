"""Kết quả kiểm thật từng dòng "Việc Sếp cần làm" (v0.1.39, bảng `ops.boss_checks` — migration 0025).

- Mỗi lần Sếp bấm Kiểm tra / Gọi thử / Đổi tài khoản (hoặc luồng đăng nhập CLI kết thúc) = MỘT bản ghi
  {check_key, status pass|fail|pending, error_code, message thân thiện, detail}. Giữ 50 bản mới nhất mỗi (org, key).
- Bí mật: KHÔNG lưu token/mật khẩu/cookie/giá trị mã đăng nhập. `detail` chỉ nhận khoá trong `DETAIL_KEYS`, `message`
  luôn qua `redact` (che token, email…). Email chỉ ở dạng che `b***@tên-miền`; email đầy đủ chỉ trả trong phản hồi
  API cho Owner. Mã đăng nhập chỉ lưu DẠNG (`code_shape`: độ dài, lớp ký tự, ký hiệu, có khoảng trắng).
- Không commit — bên gọi commit.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CHECK_KEYS = ("hub", "facebook", "agy_login", "agy_call", "agy_switch", "claude_login", "claude_call", "jev",
              "telegram", "remote_access", "facebook_reply", "kho_write", "ai_source")
RUNNABLE = ("hub", "facebook", "agy_call", "agy_switch", "claude_call", "jev", "telegram", "remote_access",
            "ai_source")
STATUSES = ("pass", "fail", "pending")
KEEP_PER_KEY = 50
MESSAGE_MAX = 300

#: v0.1.55 (G2, Boss "gọn"): CHỈ dòng 0 "nguồn AI" là bắt buộc — mọi kết nối khác (Gen-hub, Facebook, Google, Claude,
#: Telegram, từ xa, Jev…) là tuỳ chọn. Số dòng giữ nguyên 1–9 như trước (nhãn Gen, e2e); dòng "ai" đứng đầu mang số 0.
ROWS: tuple[dict[str, Any], ...] = (
    # Đạt khi có lượt gọi model thật thành công trong 30 ngày, HOẶC Claude/Google đã gọi thử đạt, HOẶC bấm Kiểm tra
    # (gọi thử nguồn đầu chuỗi) — xem `ai_source_evidence` / `overview`.
    {"row": 0, "key": "ai", "title": "Có ít nhất 1 nguồn AI chạy được", "optional": False, "checks": ["ai_source"]},
    {"row": 1, "key": "hub", "title": "Nối Gen-hub", "optional": True, "checks": ["hub"]},
    {"row": 2, "key": "facebook", "title": "Facebook", "optional": True, "checks": ["facebook"]},
    # v0.1.55: bỏ `agy_switch` khỏi dòng (bài kiểm của nhà phát triển, đổi qua lại hai tài khoản); việc đổi tài khoản
    # vẫn dùng được ở Kết nối.
    {"row": 3, "key": "agy", "title": "Google / Antigravity", "optional": True,
     "checks": ["agy_login", "agy_call"]},
    {"row": 4, "key": "claude", "title": "Claude Code CLI", "optional": True,
     "checks": ["claude_login", "claude_call"]},
    {"row": 5, "key": "jev", "title": "Jev", "optional": True, "checks": ["jev"]},
    # v0.1.44 (F-8c): kênh "Báo động & bản tin" — đạt khi lần Gửi thử gần nhất tới được Telegram của Sếp.
    {"row": 6, "key": "telegram", "title": "Telegram (báo động & bản tin)", "optional": True,
     "checks": ["telegram"]},
    # v0.1.46 (F-21): Console mở được từ máy khác (điện thoại) bằng địa chỉ từ xa — kiểm theo Origin của lần bấm.
    # v0.1.55: chỉ nhắc khi đã có người dùng khác Owner đang hoạt động (Gen hướng dẫn: signals `staff.active`).
    {"row": 7, "key": "remote", "title": "Truy cập từ xa", "optional": True, "checks": ["remote_access"]},
    # v0.1.47 (F-79): Facebook trả lời — đạt khi một lượt trả lời bình luận thật đã gửi xong (không bắt buộc; không có
    # nút "Kiểm tra" riêng vì mỗi lần gửi phải do chính Sếp xác nhận + nhập PIN).
    {"row": 8, "key": "facebook_reply", "title": "Facebook trả lời", "optional": True, "checks": ["facebook_reply"]},
    # v0.1.50 (F-81): Gen ghi Kho — đạt khi một lần ghi Kho THẬT (Gen đề xuất → Sếp Xác nhận + PIN) đã xong; không có
    # nút "Kiểm tra" riêng vì mỗi lần ghi phải do chính Sếp xác nhận (máy chủ tự ghi 'pass' lần đầu: write_kho).
    {"row": 9, "key": "kho_write", "title": "Gen ghi Kho", "optional": True, "checks": ["kho_write"]},
)
REQUIRED_TOTAL = sum(1 for r in ROWS if not r["optional"])

DETAIL_KEYS = frozenset({"latency_ms", "probe_model", "models_count", "models_source", "account_masked",
                         "expected_masked", "account_match", "code_shape", "credentials_file", "job_status",
                         "exposed_tools", "missing_tools", "target_profile", "from_profile",
                         "login_source", "bot_username", "chat_masked", "opened_from", "access_mode",
                         "read_scopes", "read_missing", "write_scopes", "write_missing", "provider_id"})

SOCIAL_FAILED_MSG = "Lượt đọc Facebook chưa thành công — mở trang Mạng xã hội xem lý do rồi bấm Đọc ngay lần nữa"
SOCIAL_HALTED_MSG = ("Đọc mạng xã hội đang bị dừng (Dừng tất cả) — bật lại ở trang Tài khoản mạng xã hội rồi bấm "
                     "Đọc ngay")
SOCIAL_CANCELLED_MSG = "Lượt đọc Facebook đã bị huỷ — bấm Đọc ngay lần nữa"
SOCIAL_TIMEOUT_MSG = ("Lượt đọc Facebook chạy quá lâu nên đã dừng (trình duyệt nền không phản hồi) — mở trang "
                      "Tài khoản mạng xã hội xem rồi bấm Đọc ngay lần nữa")
SOCIAL_MISSING_MSG = "Không còn thấy lượt đọc Facebook này (có thể tài khoản đã bị gỡ) — bấm Đọc ngay lần nữa"


def mask_email(email: str | None) -> str | None:
    """'binh@example.test' → 'b***@example.test'. Không phải email → None (không bao giờ trả chuỗi gốc)."""
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().rpartition("@")
    if not local or not domain:
        return None
    return f"{local[0]}***@{domain}"


def code_shape(code: str) -> dict[str, Any]:
    """DẠNG của mã đăng nhập (không bao giờ lưu giá trị): độ dài, lớp ký tự, ký hiệu, có khoảng trắng hay không."""
    classes: set[str] = set()
    symbols: set[str] = set()
    has_space = False
    for ch in code:
        if ch.isspace():
            has_space = True
        elif ch.isdigit():
            classes.add("digit")
        elif ch.isalpha():
            classes.add("upper" if ch.isupper() else "lower")
        else:
            classes.add("symbol")
            symbols.add(ch)
    return {"length": len(code), "classes": sorted(classes), "symbols": "".join(sorted(symbols)),
            "has_space": has_space}


def clean_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Chỉ giữ khoá trong danh sách cho phép (khoá lạ có thể mang bí mật/email đầy đủ)."""
    return {k: v for k, v in (detail or {}).items() if k in DETAIL_KEYS}


def clean_message(message: str | None) -> str | None:
    if message is None:
        return None
    from gh.providers.clients import redact

    return redact(str(message), MESSAGE_MAX)[:MESSAGE_MAX]


def _iso(v: datetime | None) -> str | None:
    return v.isoformat().replace("+00:00", "Z") if v else None


def _ts(iso: str | None) -> datetime:
    """Ngược của `_iso` để so sánh thời điểm; thiếu/hỏng ⇒ mốc rất xa (coi là cũ nhất)."""
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


async def record(db: AsyncSession, org_id: uuid.UUID, key: str, status: str, *, error_code: str | None = None,
                 message: str | None = None, detail: dict[str, Any] | None = None, user_id: uuid.UUID | None = None,
                 ref_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Ghi một bản ghi kiểm + dọn bản cũ ngoài 50 bản mới nhất của (org, key). Không commit."""
    if key not in CHECK_KEYS:
        raise ValueError(f"check_key lạ: {key}")
    if status not in STATUSES:
        raise ValueError(f"status lạ: {status}")
    msg = clean_message(message)
    det = clean_detail(detail)
    row = (await db.execute(text("""
        INSERT INTO ops.boss_checks (org_id, check_key, status, error_code, message, detail, ref_id, checked_by)
        VALUES (:o, :k, :s, :c, :m, CAST(:d AS jsonb), :r, :u) RETURNING id, checked_at"""),
        {"o": org_id, "k": key, "s": status, "c": error_code, "m": msg, "d": orjson.dumps(det).decode(),
         "r": ref_id, "u": user_id})).one()
    await db.execute(text("""
        DELETE FROM ops.boss_checks WHERE org_id = :o AND check_key = :k AND id NOT IN (
          SELECT id FROM ops.boss_checks WHERE org_id = :o AND check_key = :k
          ORDER BY checked_at DESC, id DESC LIMIT :n)"""), {"o": org_id, "k": key, "n": KEEP_PER_KEY})
    runs = int((await db.execute(text("SELECT count(*) FROM ops.boss_checks WHERE org_id = :o AND check_key = :k"),
                                 {"o": org_id, "k": key})).scalar_one())
    return {"id": str(row.id), "key": key, "status": status, "error_code": error_code, "message": msg,
            "detail": det, "checked_at": _iso(row.checked_at), "runs": runs}


async def resolve_pending(db: AsyncSession, org_id: uuid.UUID) -> int:
    """Bản ghi facebook 'pending' (ref_id = việc đọc) → đọc agent.browser_jobs: done → pass; failed → fail
    (WORKER_TIMEOUT riêng); halted (Dừng tất cả) → SOCIAL_READ_HALTED; cancelled → SOCIAL_READ_CANCELLED; việc không
    còn → fail SOCIAL_JOB_MISSING. Cập nhật tại chỗ, trả số bản ghi đã chốt. Không commit.

    Việc còn 'queued'/'running' quá `social.service.STALE_AFTER` (worker trình duyệt chết/chưa chạy) được ĐÓNG ngay tại
    đây giống `social.active_job` (failed + WORKER_TIMEOUT) — nếu không, ô Facebook kẹt "Đang chạy…" mãi vì nút Đọc ngay
    bị tắt khi đang chờ và `active_job` chỉ chạy khi có lượt đọc mới.
    Không chép `job.error` thô (có thể mang dữ liệu trang) — chỉ câu thân thiện + mã."""
    from gh.social.service import STALE_AFTER

    await db.execute(text("""
        UPDATE agent.browser_jobs j SET status = 'failed', error = 'WORKER_TIMEOUT', finished_at = now()
        FROM ops.boss_checks b
        WHERE b.org_id = :o AND b.check_key = 'facebook' AND b.status = 'pending' AND j.id = b.ref_id
          AND j.org_id = b.org_id AND j.status IN ('queued', 'running')
          AND j.created_at < now() - make_interval(secs => :s)"""),
        {"o": org_id, "s": STALE_AFTER.total_seconds()})
    rows = (await db.execute(text("""
        SELECT b.id, b.detail, j.status AS job_status, j.error AS job_error, (j.id IS NOT NULL) AS has_job
        FROM ops.boss_checks b LEFT JOIN agent.browser_jobs j ON j.id = b.ref_id AND j.org_id = b.org_id
        WHERE b.org_id = :o AND b.check_key = 'facebook' AND b.status = 'pending' AND b.ref_id IS NOT NULL"""),
        {"o": org_id})).all()
    n = 0
    for r in rows:
        detail = clean_detail(dict(r.detail or {}))
        if r.has_job:
            detail["job_status"] = r.job_status
        if not r.has_job:
            status, code, msg = "fail", "SOCIAL_JOB_MISSING", SOCIAL_MISSING_MSG
        elif r.job_status == "done":
            status, code, msg = "pass", None, None
        elif r.job_status == "failed" and r.job_error == "WORKER_TIMEOUT":
            status, code, msg = "fail", "WORKER_TIMEOUT", SOCIAL_TIMEOUT_MSG
        elif r.job_status == "failed":
            status, code, msg = "fail", "SOCIAL_READ_FAILED", SOCIAL_FAILED_MSG
        elif r.job_status == "halted":
            status, code, msg = "fail", "SOCIAL_READ_HALTED", SOCIAL_HALTED_MSG
        elif r.job_status == "cancelled":
            status, code, msg = "fail", "SOCIAL_READ_CANCELLED", SOCIAL_CANCELLED_MSG
        else:
            # Còn đang xếp hàng/chạy: chỉ cập nhật trạng thái việc để web hiện "Đang chạy…".
            await db.execute(text("UPDATE ops.boss_checks SET detail = CAST(:d AS jsonb) WHERE id = :i"),
                             {"d": orjson.dumps(detail).decode(), "i": r.id})
            continue
        await db.execute(text("""UPDATE ops.boss_checks SET status = :s, error_code = :c, message = :m,
                                 detail = CAST(:d AS jsonb) WHERE id = :i"""),
                         {"s": status, "c": code, "m": msg, "d": orjson.dumps(detail).decode(), "i": r.id})
        n += 1
    return n


async def latest(db: AsyncSession, org_id: uuid.UUID) -> dict[str, dict[str, Any] | None]:
    """{check_key: bản ghi mới nhất (kèm `runs` = số bản ghi đang giữ) | None}."""
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (check_key) check_key, status, error_code, message, detail, checked_at,
               count(*) OVER (PARTITION BY check_key) AS runs
        FROM ops.boss_checks WHERE org_id = :o
        ORDER BY check_key, checked_at DESC, id DESC"""), {"o": org_id})).all()
    out: dict[str, dict[str, Any] | None] = dict.fromkeys(CHECK_KEYS)
    for r in rows:
        if r.check_key in out:
            out[r.check_key] = {"key": r.check_key, "status": r.status, "error_code": r.error_code,
                                "message": r.message, "detail": dict(r.detail or {}),
                                "checked_at": _iso(r.checked_at), "runs": int(r.runs)}
    return out


async def pass_count(db: AsyncSession, org_id: uuid.UUID, key: str) -> int:
    return int((await db.execute(text("""SELECT count(*) FROM ops.boss_checks WHERE org_id = :o AND check_key = :k
                                         AND status = 'pass'"""), {"o": org_id, "k": key})).scalar_one())


#: Lỗi TẠM (bận / hạn mức) — KHÔNG ghi thành bản kiểm (thay kết quả đã lưu bằng một lỗi chỉ vì bấm quá nhanh là sai).
TRANSIENT_CODES = frozenset({"SOCIAL_BUSY", "SOCIAL_RATE_LIMIT", "PROBE_RATE_LIMITED", "HUB_RATE_LIMITED",
                             "CLI_LOGIN_IN_PROGRESS", "TELEGRAM_RATE_LIMITED"})


async def record_hub_result(db: AsyncSession, org_id: uuid.UUID, r: dict[str, Any],
                            user_id: uuid.UUID | None = None) -> dict[str, Any] | None:
    """Ghi bản kiểm `hub` từ kết quả `hub_link.service.test_link` (v0.1.68, F028) — DÙNG CHUNG cho nút Kiểm tra ở
    "Việc Sếp cần làm" (`/boss-checks/hub/run`) và nút "Lưu & kiểm tra" ở Kết nối › Gen-hub (`/hub/link/test`),
    để hai nơi
    không bao giờ lệch (Hướng dẫn "Đã xong" mà Việc Sếp cần làm "Chưa kiểm", Gen vẫn nhắc "Nối Gen-hub").
    Lỗi TẠM → None (không ghi). Không commit."""
    ok = bool(r.get("ok"))
    if not ok and r.get("error_code") in TRANSIENT_CODES:
        return None
    detail: dict[str, Any] = {"latency_ms": r.get("latency_ms"), "exposed_tools": len(r.get("exposed_tools") or []),
                              "missing_tools": list(r.get("missing_tools") or [])}
    # v0.1.49 (QD-16): quyền ĐỌC thêm (lịch/mail/việc/Drive) chỉ để hiển thị — KHÔNG quyết định Đạt/Lỗi. Chỉ lưu khi
    # Kiểm tra XANH: lượt đỏ (mạng, token bị từ chối…) trả quyền cũ trong CSDL (toàn False nếu chưa từng xanh) — hiện
    # "Lịch ✗ … tick thêm quyền" cạnh "Lỗi …" là chỉ sai cách sửa.
    scopes = r.get("read_scopes")
    if ok and isinstance(scopes, dict):
        detail["read_scopes"] = {str(k): bool(v) for k, v in scopes.items()}
        detail["read_missing"] = [str(x) for x in (r.get("read_missing") or [])]
    # v0.1.50 (F-81): quyền GHI Kho (kho_create, kho_update) — cũng chỉ để hiển thị, cùng quy tắc "chỉ khi kiểm xanh".
    wscopes = r.get("write_scopes")
    if ok and isinstance(wscopes, dict):
        detail["write_scopes"] = {str(k): bool(v) for k, v in wscopes.items()}
        detail["write_missing"] = [str(x) for x in (r.get("write_missing") or [])]
    return await record(db, org_id, "hub", "pass" if ok else "fail",
                        error_code=None if ok else (r.get("error_code") or "HUB_ERROR"),
                        message=None if ok else r.get("error"), detail=detail, user_id=user_id)


def _passed(results: dict[str, dict[str, Any] | None], key: str) -> bool:
    r = results.get(key)
    return r is not None and r["status"] == "pass"


#: Số ngày nhìn lại tìm lượt gọi model thành công để coi "đã có nguồn AI chạy được".
AI_SOURCE_DAYS = 30

#: Mã lỗi của dòng "nguồn AI" theo `readiness.reason_code` (giữ các mã cũ AI_NO_SOURCE / AI_KEY_MISSING /
# *_NOT_LOGGED_IN
#: mà web, test và Gen hướng dẫn đã biết).
AI_ERROR_CODES = {"no_source": "AI_NO_SOURCE", "no_key": "AI_KEY_MISSING", "cli_expired": "AI_CLI_EXPIRED",
                  "disabled": "AI_DISABLED", "no_model": "AI_NO_MODEL", "error": "AI_ERROR",
                  "box_down": "AI_BOX_DOWN"}
CLI_NOT_LOGGED_IN = {"antigravity_cli": "AGY_NOT_LOGGED_IN", "claude_code_cli": "CLAUDE_NOT_LOGGED_IN"}
#: Kiểm tra gọi thử CLI đạt (`agy_call` / `claude_call`) chỉ là bằng chứng khi nguồn CLI đó còn dùng được.
CALL_CHECK_KIND = {"agy_call": "antigravity_cli", "claude_call": "claude_code_cli"}


def ai_error_code(verdict: dict[str, Any]) -> str:
    """Mã lỗi của `readiness.explain(...)` (chưa ok) cho bản ghi / kết quả `ai_source`."""
    src = verdict.get("source")
    if verdict["reason_code"] == "no_key" and src is not None and src["kind"] in CLI_NOT_LOGGED_IN:
        return CLI_NOT_LOGGED_IN[src["kind"]]
    return AI_ERROR_CODES[str(verdict["reason_code"])]


async def ai_source_evidence(db: AsyncSession, org_id: uuid.UUID,
                             usable_ids: list[str] | None = None) -> dict[str, Any] | None:
    """Bằng chứng có nguồn AI chạy được từ lượt gọi THẬT: `agent.model_calls` status 'ok' trong 30 ngày (không tính
    embedding / Jev — không sinh văn bản). Trả bản ghi giả dạng kết quả kiểm (status 'pass', `runs` 0) hoặc None.
    v0.1.63 (F014): CHỈ tính lượt gọi của nguồn còn DÙNG ĐƯỢC (`usable_ids` = id nguồn của `readiness.usable_sources`;
    bỏ trống = tự tra) — lượt gọi thành công của nguồn đã tắt / gỡ khoá / hết phiên không chứng minh Gen trả lời được
    hôm nay. Chỉ cần LƯỢT MỚI NHẤT (ORDER BY at DESC LIMIT 1 trên chỉ mục (org_id, at DESC)) — không đếm cả 30 ngày mỗi
    lần `overview` chạy (Hôm nay, Việc Sếp cần làm, coach cùng gọi)."""
    from gh.providers import readiness

    if usable_ids is None:
        usable_ids = [s["provider_id"] for s in await readiness.usable_sources(db, org_id, readiness.READY_ROLE)]
    if not usable_ids:
        return None
    r = (await db.execute(text("""
        SELECT c.at
        FROM agent.model_calls c
        JOIN agent.models m ON m.id = c.model_id
        JOIN agent.providers p ON p.id = m.provider_id
        WHERE c.org_id = :o AND c.status = 'ok' AND c.at > now() - make_interval(days => :d)
          AND c.purpose <> 'embedding' AND p.kind NOT IN ('system_one', 'embedding')
          AND p.id = ANY(CAST(:ids AS uuid[]))
        ORDER BY c.at DESC LIMIT 1"""), {"o": org_id, "d": AI_SOURCE_DAYS, "ids": usable_ids})).one_or_none()
    if r is None:
        return None
    return {"key": "ai_source", "status": "pass", "error_code": None, "message": None,
            "detail": {"via": "model_calls"}, "checked_at": _iso(r.at), "runs": 0}


def _derived_ai(results: dict[str, dict[str, Any] | None], usable_kinds: set[str]) -> dict[str, Any] | None:
    """Claude / Google đã gọi thử ĐẠT (lượt gọi thật tới CLI) VÀ nguồn CLI đó còn dùng được ⇒ coi như có nguồn AI chạy
    được."""
    for key in ("claude_call", "agy_call"):
        r = results.get(key)
        if r is not None and r["status"] == "pass" and CALL_CHECK_KIND[key] in usable_kinds:
            return {"key": "ai_source", "status": "pass", "error_code": None, "message": None,
                    "detail": {"via": key}, "checked_at": r["checked_at"], "runs": 0}
    return None


async def _resolve_ai(db: AsyncSession, org_id: uuid.UUID,
                      results: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
    """Kết quả cuối của dòng 0 "nguồn AI" (v0.1.63, F014): bằng chứng chỉ có giá trị khi Gen trả lời được BÂY GIỜ.

    - `readiness.explain` không ok (không có nguồn nào dùng được) ⇒ dù trước đó có lượt gọi / lần Kiểm tra
    đạt, kết quả là
      'fail' kèm đúng lý do hiện tại (nguồn tắt, gỡ khoá, hết phiên, hết model…) — dòng bắt buộc duy nhất
      phải nói sự thật;
    - ok ⇒ một lần Kiểm tra 'pass' đã ghi nguồn (`detail.provider_id`) mà nguồn đó không còn dùng được thì KHÔNG tính;
      bằng chứng ngầm (lượt gọi model thật, Claude / Google gọi thử đạt) chỉ tính của nguồn còn dùng được, và chỉ thay
      kết quả ĐÃ GHI khi nó MỚI HƠN (Sếp vừa bấm Kiểm tra mà lỗi thì lượt gọi thành công của 20 ngày trước
      không che nó)."""
    from gh.providers import readiness

    states = await readiness.source_states(db, org_id, readiness.READY_ROLE)
    verdict = readiness.explain(states)
    ai = results.get("ai_source")
    if not verdict["ok"]:
        if verdict["reason_code"] == "no_source" and (ai is None or ai["status"] != "pass"):
            return ai          # máy mới chưa thêm nguồn nào: chưa kiểm lần nào (hoặc lần kiểm lỗi đã nói đúng)
        return {"key": "ai_source", "status": "fail", "error_code": ai_error_code(verdict),
                "message": verdict["reason_vi"], "detail": {"via": "ai_ready", "fix_to": verdict["fix_to"]},
                "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "runs": ai["runs"] if ai else 0}
    usable = [s for s in states if s["usable"]]
    usable_ids = {s["provider_id"] for s in usable}
    if ai is not None and ai["status"] == "pass":
        recorded = ai["detail"].get("provider_id")
        if recorded is not None and str(recorded) not in usable_ids:
            ai = None                      # nguồn đã đạt không còn dùng được
    if ai is None or ai["status"] != "pass":
        found = (await ai_source_evidence(db, org_id, sorted(usable_ids)),
                 _derived_ai(results, {s["kind"] for s in usable}))
        best = max((e for e in found if e is not None), key=lambda e: _ts(e["checked_at"]), default=None)
        if best is not None and (ai is None or _ts(best["checked_at"]) > _ts(ai["checked_at"])):
            return best
    return ai


async def overview(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Quy tắc 'done' (v0.1.55): `ai` = có bằng chứng nguồn AI chạy được (lượt gọi model thật trong 30 ngày, hoặc
    Claude / Google gọi thử đạt, hoặc bấm Kiểm tra đạt — `ai_source`); agy = agy_call đạt (không còn đòi đổi qua lại
    hai tài khoản); claude = claude_login đạt VÀ claude_call đạt; còn lại = kiểm tương ứng đạt. Chỉ dòng `ai` bắt buộc.

    v0.1.68 (F040): bỏ `switch_passes` (web không còn đọc; mỗi lần `overview` thừa một truy vấn).
    `results.agy_switch.runs`
    đếm MỌI bản ghi, cả lỗi."""
    results = await latest(db, org_id)
    ai = await _resolve_ai(db, org_id, results)
    results["ai_source"] = ai
    rows: list[dict[str, Any]] = []
    for row in ROWS:
        if row["key"] == "agy":
            done = _passed(results, "agy_call")
        elif row["key"] == "claude":
            done = _passed(results, "claude_login") and _passed(results, "claude_call")
        else:
            done = _passed(results, row["checks"][0])
        rows.append({**row, "checks": list(row["checks"]), "done": done})
    required_done = sum(1 for r in rows if r["done"] and not r["optional"])
    return {"rows": rows, "results": results, "required_done": required_done, "required_total": REQUIRED_TOTAL}
