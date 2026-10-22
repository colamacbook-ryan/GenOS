"""/setup — trình thiết lập Owner 12 bước (docs/handoff/06). Giai đoạn 1: bước 1–3; giai đoạn 2: bước 4–7, 12;
giai đoạn 3: bước 8–9 (agent đầu tiên + thử trò chuyện; tự trị & ranh giới — docs/api/phase-3-people.md);
giai đoạn 4: bước 10–11 (mời đội ngũ — tài khoản + mật khẩu tạm, chưa có SMTP thật; cấu hình LỊCH/ĐÍCH sao lưu —
chạy pg_dump/MinIO thật thuộc GĐ 5 mục 5.6, PLAN.md)."""

import hmac
import json
import re
import uuid
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.agents_api.routes import _check_name_free
from gh.auth import rbac, service
from gh.auth.deps import client_ip, optional_user
from gh.auth.routes import set_session_cookies
from gh.biz.people.routes import try_chat
from gh.chassis import actionlog, policy
from gh.crypto import hash_secret, token_digest
from gh.crypto import temp_password as new_temp_password
from gh.data.common import LISTENING_MODES
from gh.data_api.routes import RuleIn, ScheduleIn, create_rule, save_schedule, save_weights
from gh.db import DB
from gh.errors import ApiError, conflict, field_errors, forbidden, pin_required, unauthenticated
from gh.gen.coach import cache as coach_cache
from gh.refinery import presets
from gh.refinery.runner import load_schedule
from gh.system_api.routes import GroupPatch, update_group

router = APIRouter(prefix="/setup", tags=["setup"])

# (số, khoá, tên, bắt buộc, làm được từ giai đoạn)
STEPS: tuple[tuple[int, str, str, bool, int], ...] = (
    (1, "welcome", "Chào mừng", True, 1),
    (2, "owner", "Tài khoản Owner", True, 1),
    (3, "org", "Tổ chức & xưng hô", True, 1),
    # v0.1.29 (Boss 30/09, UX V2): bước 4 "Để sau" được — web hỏi lại bằng hộp cảnh báo (Gen/sàng lọc không chạy tới
    # khi có model); bước 12 + Tổng quan hiện "Chưa có model" kèm nút sửa (`/guide/4`, lưu được cả sau Hoàn tất).
    (4, "brain", "Bộ não AI", False, 2),
    # Chỉ 1–3 bắt buộc: có Owner + tổ chức là vào được Console. 4–11 bỏ qua được và làm lại sau ở
    # màn tương ứng của Console — bắt quét QR Zalo/WhatsApp hay dựng agent ngay khi cài làm Owner kẹt
    # (bước 8–9 web chưa có form, trước đây khiến bước 12 không bao giờ hoàn tất được).
    (5, "channels", "Kết nối kênh", False, 2),
    (6, "groups", "Chọn nhóm lắng nghe", False, 2),
    (7, "refinery", "Sàng lọc dữ liệu", False, 2),
    (8, "agent", "Agent đầu tiên", False, 3),
    (9, "autonomy", "Tự trị & ranh giới", False, 3),
    (10, "team", "Mời đội ngũ", False, 4),
    (11, "backup", "Sao lưu", False, 4),
    (12, "finish", "Hoàn tất", True, 2),
)
CURRENT_PHASE = 4
# Khoá cứng — ARCHITECTURE §7.4, không tắt được bằng cài đặt. Chỉ hiển thị lại ở bước 9 để Owner xác nhận đã đọc
# (`ack_boundaries`) — trang Quyền hạn đầy đủ để BẬT/TẮT các giới hạn *tuỳ chọn* khác thuộc giai đoạn 4.
HARD_BOUNDARIES = (
    "Chỉ lưu tin của nhóm Sếp đã chọn nghe",
    "Hệ thống không tự ra quyết định nhân sự",
    "Gửi ra ngoài, vượt ngưỡng tiền, liên quan nhân sự → luôn chờ duyệt ở Bàn làm việc",
    "MCP: tool ghi qua duyệt; agent chỉ gọi tool Owner đã mở",
    "Kho thô và Nhật ký hành động chỉ được ghi thêm, không sửa/xoá",
    "PIN cho thao tác nhạy cảm; bí mật được mã hoá",
    "Điểm số và cảnh báo nhân sự phải có chứng cứ",
    "Ẩn dữ liệu nhạy cảm (số tài khoản, sức khoẻ, đời tư) khỏi vai trò dưới Owner",
)
# v0.1.55: xưng hô mặc định ở bước 3 (Sếp tự xưng / agent gọi Sếp) — điền sẵn, sửa được.
DEFAULT_ADDRESSING = "Sếp"
CONSOLE_STEPS = ("1", "2", "3")    # xong 3 bước này thì Console mở (các bước sau làm tiếp được)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CURRENCIES = ("VND", "USD", "EUR", "JPY", "SGD", "THB", "CNY", "KRW")


class Step1In(BaseModel):
    token: str | None = Field(default=None, max_length=200)
    language: Literal["vi", "en"] = "vi"
    mode: Literal["empty", "sample"] = "empty"


class Step2In(BaseModel):
    token: str = Field(max_length=200)
    display_name: str = Field(max_length=120)
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)
    pin: str = Field(max_length=16)
    pin_confirm: str = Field(max_length=16)


class Step3In(BaseModel):
    org_name: str = Field(max_length=160)
    timezone: str = Field(default="Asia/Ho_Chi_Minh", max_length=64)
    currency: str = Field(default="VND", max_length=3)
    # v0.1.55 (G2): hai ô xưng hô mặc định "Sếp" — thiếu trường ⇒ điền mặc định (hình dạng payload giữ nguyên).
    self_name: str = Field(default=DEFAULT_ADDRESSING, max_length=40)
    bot_calls_me: str = Field(default=DEFAULT_ADDRESSING, max_length=60)


async def _row(db: AsyncSession) -> Any:
    row = (await db.execute(text("""SELECT s.org_id, s.step, s.completed, s.setup_token_hash, s.finished_at
                                    FROM ops.setup_state s ORDER BY s.org_id LIMIT 1 FOR UPDATE"""))).one_or_none()
    if row is None:
        raise ApiError(503, "NOT_BOOTSTRAPPED", "Hệ thống đang khởi tạo, thử lại sau ít giây")
    return row


def state_payload(row: Any) -> dict[str, Any]:
    completed = row.completed or {}
    done = completed.get("steps", {})
    steps = []
    for n, key, title, required, phase in STEPS:
        status = done.get(str(n)) or ("doing" if n == row.step else "todo")
        steps.append({"n": n, "key": key, "title": title, "required": required, "status": status,
                      "available": phase <= CURRENT_PHASE})
    return {"finished": row.finished_at is not None, "current_step": row.step, "steps": steps,
            "language": completed.get("language", "vi"), "mode": completed.get("mode"),
            "owner_created": done.get("2") == "done",
            "console_ready": all(done.get(k) == "done" for k in CONSOLE_STEPS)}


async def console_ready(db: AsyncSession) -> bool:
    completed = (await db.execute(text("SELECT completed FROM ops.setup_state ORDER BY org_id LIMIT 1"))).scalar()
    done = (completed or {}).get("steps", {})
    return all(done.get(k) == "done" for k in CONSOLE_STEPS)


async def _save(db: AsyncSession, org_id: uuid.UUID, completed: dict[str, Any], step: int) -> Any:
    await db.execute(text("UPDATE ops.setup_state SET completed = CAST(:c AS jsonb), step = :s WHERE org_id = :o"),
                     {"c": json.dumps(completed), "s": step, "o": org_id})
    return await _row(db)


def _next_open_step(done: dict[str, str], after: int) -> int:
    for n, *_ in STEPS:
        if n > after and done.get(str(n)) not in ("done", "skipped"):
            return n
    return 12


def _check_token(row: Any, token: str | None) -> None:
    if row.setup_token_hash is None or not token or \
            not hmac.compare_digest(bytes(row.setup_token_hash), token_digest(token.strip())):
        raise ApiError(403, "SETUP_TOKEN_INVALID", "Mã thiết lập không đúng hoặc đã hết hiệu lực")


def _not_finished(row: Any) -> None:
    if row.finished_at is not None:
        raise conflict("SETUP_FINISHED", "Hệ thống đã thiết lập xong")


def _owner_of(row: Any, user: service.CurrentUser | None) -> service.CurrentUser:
    if user is None:
        raise unauthenticated()
    if user.org_id != row.org_id or user.role_code != rbac.OWNER:
        raise forbidden("owner")
    return user


@router.get("/state")
async def get_state(db: AsyncSession = DB) -> dict[str, Any]:
    return state_payload(await _row(db))


@router.put("/steps/1")
async def step1(body: Step1In, request: Request, db: AsyncSession = DB,
                user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    row = await _row(db)
    _not_finished(row)
    completed = dict(row.completed or {})
    done = dict(completed.get("steps", {}))
    if done.get("2") == "done":
        actor = _owner_of(row, user)
        actor_type, actor_id = "user", actor.actor_id
    else:
        _check_token(row, body.token)
        actor_type, actor_id = "system", "system:setup"
    done["1"] = "done"
    completed |= {"steps": done, "language": body.language, "mode": body.mode}
    await actionlog.record(db, org_id=row.org_id, actor_type=actor_type, actor_id=actor_id,
                           action="setup.step_saved", target_type="setup_step", target_id="1",
                           target_label="Chào mừng", detail={"language": body.language, "mode": body.mode},
                           ip=client_ip(request))
    return state_payload(await _save(db, row.org_id, completed, row.step if done.get("2") == "done" else 2))


@router.put("/steps/2")
async def step2(body: Step2In, request: Request, response: Response,
                db: AsyncSession = DB) -> dict[str, Any]:
    row = await _row(db)
    _not_finished(row)
    completed = dict(row.completed or {})
    done = dict(completed.get("steps", {}))
    if done.get("2") == "done":
        raise conflict("OWNER_EXISTS", "Tài khoản Owner đã được tạo")
    _check_token(row, body.token)
    if done.get("1") != "done":
        raise conflict("STEP_ORDER", "Cần hoàn thành bước 1 trước")

    errors: dict[str, str] = {}
    name, email = body.display_name.strip(), body.email.strip().lower()
    if not name:
        errors["display_name"] = "Nhập tên hiển thị"
    if not EMAIL_RE.match(email):
        errors["email"] = "Email chưa đúng định dạng"
    if len(body.password) < 12:
        errors["password"] = "Mật khẩu cần ít nhất 12 ký tự"
    if not service.valid_pin(body.pin):
        errors["pin"] = "PIN gồm đúng 6 chữ số"
    elif body.pin != body.pin_confirm:
        errors["pin_confirm"] = "Hai lần nhập mã PIN không khớp"
    if errors:
        raise field_errors(errors)

    user_id = (await db.execute(text("""
        INSERT INTO core.users (org_id, email, display_name, password_hash, pin_hash)
        VALUES (:o, :e, :n, :p, :pin) RETURNING id"""),
        {"o": row.org_id, "e": email, "n": name, "p": hash_secret(body.password),
         "pin": hash_secret(body.pin)})).scalar_one()
    role_id = (await db.execute(text("SELECT id FROM core.roles WHERE org_id = :o AND code = 'owner'"),
                                {"o": row.org_id})).scalar_one()
    await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"),
                     {"u": user_id, "r": role_id})
    ip = client_ip(request)
    new = await service.create_session(db, user_id, ip=ip, user_agent=request.headers.get("user-agent"))
    set_session_cookies(response, new)
    # Mã thiết lập hết hiệu lực ngay khi có Owner.
    await db.execute(text("UPDATE ops.setup_state SET setup_token_hash = NULL WHERE org_id = :o"), {"o": row.org_id})
    await actionlog.record(db, org_id=row.org_id, actor_type="user", actor_id=f"user:{user_id}",
                           action="setup.owner_created", target_type="user", target_id=str(user_id),
                           target_label=name, ip=ip)
    done["2"] = "done"
    completed["steps"] = done
    return state_payload(await _save(db, row.org_id, completed, 3))


def validate_org(body: Step3In) -> tuple[str, str, str, str, str]:
    """Kiểm + chuẩn hoá thông tin tổ chức & xưng hô (bước 3; dùng lại ở Điều khiển hệ thống › Tổ chức — v0.1.22).
    Sai → 422 lỗi theo ô. Trả (tên tổ chức, múi giờ, tiền tệ, Sếp tự xưng, agent gọi Sếp)."""
    errors: dict[str, str] = {}
    org_name, self_name, bot_calls = body.org_name.strip(), body.self_name.strip(), body.bot_calls_me.strip()
    currency, tz = body.currency.strip().upper(), body.timezone.strip()
    if not org_name:
        errors["org_name"] = "Nhập tên tổ chức"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        errors["timezone"] = "Múi giờ không hợp lệ"
    if currency not in CURRENCIES:
        errors["currency"] = "Tiền tệ chưa hỗ trợ"
    if not self_name:
        errors["self_name"] = "Nhập cách Sếp tự xưng"
    if not bot_calls:
        errors["bot_calls_me"] = "Nhập cách agent gọi Sếp"
    if errors:
        raise field_errors(errors)
    return org_name, tz, currency, self_name, bot_calls


@router.put("/steps/3")
async def step3(body: Step3In, request: Request, db: AsyncSession = DB,
                user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    row = await _row(db)
    _not_finished(row)
    owner = _owner_of(row, user)
    org_name, tz, currency, self_name, bot_calls = validate_org(body)
    await db.execute(text("UPDATE core.organizations SET name = :n, timezone = :tz, currency = :c WHERE id = :o"),
                     {"n": org_name, "tz": tz, "c": currency, "o": row.org_id})
    await db.execute(text("UPDATE core.users SET addressing = addressing || CAST(:a AS jsonb) WHERE id = :u"),
                     {"a": json.dumps({"self": self_name, "bot_calls_me": bot_calls}), "u": owner.id})
    await actionlog.record(db, org_id=row.org_id, actor_type="user", actor_id=owner.actor_id,
                           action="setup.step_saved", target_type="setup_step", target_id="3",
                           target_label="Tổ chức & xưng hô",
                           detail={"org_name": org_name, "timezone": tz, "currency": currency},
                           ip=client_ip(request))
    completed = dict(row.completed or {})
    done = dict(completed.get("steps", {}))
    done["3"] = "done"
    completed["steps"] = done
    request.app.state.console_ready = None  # xoá bộ nhớ đệm của middleware
    return state_payload(await _save(db, row.org_id, completed, max(row.step, _next_open_step(done, 3))))


DEFAULT_BACKUP: dict[str, Any] = {"frequency": "daily", "time_of_day": "02:00", "retention_count": 7,
                                  "destination": "local"}


async def write_default_backup(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """Ghi lịch sao lưu mặc định (`DEFAULT_BACKUP`) khi tổ chức CHƯA có khoá `settings.backup`. Trả True nếu đã ghi."""
    res = await db.execute(text("""UPDATE core.organizations SET settings = settings || CAST(:s AS jsonb)
                                   WHERE id = :o AND NOT (settings ? 'backup')"""),
                           {"s": json.dumps({"backup": DEFAULT_BACKUP}), "o": org_id})
    return bool(res.rowcount)    # type: ignore[attr-defined]


async def seed_default_rules(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None) -> int:
    """Nạp bộ quy tắc khởi đầu (`presets.PRESETS`, bật theo mặc định của từng quy tắc) khi tổ chức CHƯA có quy tắc
    nào — không đụng tới quy tắc Owner đã tạo/tắt. Trả số quy tắc đã thêm."""
    if (await db.execute(text("SELECT EXISTS (SELECT 1 FROM refinery.rules WHERE org_id = :o)"),
                         {"o": org_id})).scalar():
        return 0
    for p in presets.PRESETS:
        rin = RuleIn(name=p["name"], kind=p["kind"], conditions=p["conditions"], outputs=p["outputs"],
                     threshold=p["threshold"], prompt_hint=p.get("prompt_hint"))
        await create_rule(db, org_id, rin, user_id, code=p["code"], enabled=bool(p["enabled"]))
    return len(presets.PRESETS)


@router.post("/steps/{n}/skip")
async def skip(n: int, request: Request, db: AsyncSession = DB,
               user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    row = await _row(db)
    _not_finished(row)
    owner = _owner_of(row, user)
    step = next((s for s in STEPS if s[0] == n), None)
    if step is None:
        raise ApiError(404, "NOT_FOUND", "Không có bước này")
    if step[3]:
        raise conflict("STEP_REQUIRED", f"Bước {n} là bắt buộc, không bỏ qua được")
    completed = dict(row.completed or {})
    done = dict(completed.get("steps", {}))
    if done.get(str(n)) != "done":
        first_skip = str(n) not in done
        done[str(n)] = "skipped"
        # v0.1.28 (UX N3/N4): "Để sau" = dùng mặc định, không phải "không có gì". Bước 7 → nạp bộ quy tắc khởi đầu
        # (nếu tổ chức chưa có quy tắc nào); bước 11 → lịch sao lưu hằng ngày 02:00 (nếu chưa có lịch). Chỉ lần
        # "Để sau" ĐẦU TIÊN — bấm lại sau khi Owner đã xoá bộ mặc định thì không nạp lại.
        if first_skip and n == 7:
            await seed_default_rules(db, row.org_id, owner.id)
        elif first_skip and n == 11:
            await write_default_backup(db, row.org_id)
        elif n == 4:
            # "Để sau" bước 4 nhưng đã có nguồn gọi thử OK kèm model → vẫn chép model đã gọi thử vào `agent.models` cho
            # nguồn còn trống (v0.1.55: KHÔNG ghi agent.bindings — hồ sơ tiêu chuẩn tự phủ); không có thì thôi — Tổng
            # quan hiện "Chưa có model".
            await ensure_tested_models(db, row.org_id)
    completed["steps"] = done
    await actionlog.record(db, org_id=row.org_id, actor_type="user", actor_id=owner.actor_id,
                           action="setup.step_skipped", target_type="setup_step", target_id=str(n),
                           target_label=step[2], ip=client_ip(request))
    return state_payload(await _save(db, row.org_id, completed,
                                     _next_open_step(done, n) if row.step == n else row.step))


# ─── Bước 4–7, 12 (giai đoạn 2) ─────────────────────────────────────────────

class Step4In(BaseModel):
    provider_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class Step6Group(GroupPatch):
    id: uuid.UUID


class Step6In(BaseModel):
    groups: list[Step6Group] = Field(default_factory=list, max_length=500)


#: v0.1.55 (G2): mặc định bước 7 — chu kỳ 900 giây, ngưỡng 500 tin, lô 250, tin cậy tối thiểu 0,6 (cùng giá trị cột mặc
#: định của `refinery.schedule`). Thiếu trường ⇒ điền mặc định; `rule_codes` thiếu ⇒ bật bộ quy tắc khởi đầu.
STEP7_DEFAULTS: dict[str, Any] = {"interval_seconds": 900, "count_threshold": 500, "batch_size": 250,
                                  "min_confidence": 0.6}


class Step7In(BaseModel):
    interval_seconds: int = Field(default=STEP7_DEFAULTS["interval_seconds"], ge=60, le=86400)
    count_threshold: int = Field(default=STEP7_DEFAULTS["count_threshold"], ge=1, le=100000)
    min_confidence: float = Field(default=STEP7_DEFAULTS["min_confidence"], ge=0, le=1)
    # None (thiếu) = bộ quy tắc khởi đầu bật theo mặc định của từng quy tắc; danh sách (kể cả rỗng) = đúng Owner chọn.
    rule_codes: list[str] | None = Field(default=None, max_length=50)
    weights: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


def incomplete(msg: str) -> ApiError:
    return conflict("STEP_INCOMPLETE", msg)


async def _mark_done(db: AsyncSession, request: Request, row: Any, owner: service.CurrentUser, n: int,
                     detail: dict[str, Any] | None = None, *, remember: dict[str, Any] | None = None) -> dict[str, Any]:
    completed = dict(row.completed or {})
    completed.update(remember or {})
    done = dict(completed.get("steps", {}))
    done[str(n)] = "done"
    completed["steps"] = done
    title = next(s[2] for s in STEPS if s[0] == n)
    await actionlog.record(db, org_id=row.org_id, actor_type="user", actor_id=owner.actor_id,
                           action="setup.step_saved", target_type="setup_step", target_id=str(n),
                           target_label=title, detail=detail, ip=client_ip(request))
    step = _next_open_step(done, n) if row.step <= n else row.step
    return state_payload(await _save(db, row.org_id, completed, step))


async def _owner_step(db: AsyncSession, user: service.CurrentUser | None, *,
                      after_finish: bool = False) -> tuple[Any, service.CurrentUser]:
    """`after_finish=True` cho bước tuỳ chọn 5–11: Owner "Để sau" rồi làm tiếp từ trang Hướng dẫn kết nối ở
    Console SAU khi đã bấm Hoàn tất — cùng form, cùng kiểm tra, chỉ không bị chặn bởi `SETUP_FINISHED`."""
    row = await _row(db)
    if not after_finish:
        _not_finished(row)
    owner = _owner_of(row, user)
    if not await console_ready(db):
        raise conflict("STEP_ORDER", "Cần hoàn thành bước 1–3 trước")
    return row, owner


@router.put("/steps/4")
async def step4(body: Step4In, request: Request, db: AsyncSession = DB,
                user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Bộ não AI: thứ tự ưu tiên chuyển dự phòng. Cần ít nhất một nhà cung cấp đã gọi thử thành công.

    v0.1.29: lưu được cả sau Hoàn tất (Owner "Để sau" bước 4 rồi chọn model từ `/guide/4`).
    v0.1.55 (G2): chỉ lưu thứ tự nguồn + bảo đảm có dòng model (`agent.models`); KHÔNG gán `agent.bindings`.
    v0.1.68 (F003): KHÔNG tự bật lại nguồn đã tắt; sau Hoàn tất đổi thứ tự cần PIN (xem bên dưới)."""
    row, owner = await _owner_step(db, user, after_finish=True)
    ids = list(dict.fromkeys(body.provider_ids))
    found = (await db.execute(text("""
        SELECT p.id, p.kind, p.auth_state, p.is_enabled, COALESCE((p.last_test->>'ok')::boolean, false) AS tested,
               CASE WHEN jsonb_typeof(p.last_test->'models') = 'array' THEN
                    ARRAY(SELECT jsonb_array_elements_text(p.last_test->'models')) END AS test_models,
               p.last_test->>'probe_model' AS probe_model, p.last_test->>'probe_effort' AS probe_effort,
               EXISTS (SELECT 1 FROM agent.cli_profiles c WHERE c.provider_id = p.id AND c.is_active) AS cli_ok
        FROM agent.providers p WHERE p.org_id = :o AND p.id = ANY(:ids)"""),
        {"o": row.org_id, "ids": ids})).all()
    if len(found) != len(ids):
        raise field_errors({"provider_ids": "Có nhà cung cấp không tồn tại"})
    # v0.1.68 (F003): nguồn Sếp đã TẮT không "sẵn sàng" và không bao giờ được bước này tự bật lại (bật lại =
    # PATCH /providers, cần PIN `ai.route_change`).
    ready = [p for p in found if p.kind != "system_one" and p.is_enabled  # Jev không sinh được văn bản
             and (p.cli_ok if p.kind in CLI_KINDS else p.tested and p.auth_state == "ok")]
    if not ready:
        # Review v0.1.68 (P2-1): mọi nguồn gửi lên đều TẮT ⇒ nói đúng điều đó + chỉ chỗ bật lại.
        if found and not any(p.is_enabled for p in found):
            raise incomplete("Nguồn AI đã tắt — bật lại ở API & Model (hoặc thêm một nguồn mới) rồi quay lại bước này")
        raise incomplete("Cần ít nhất một nhà cung cấp đã gọi thử thành công, hoặc một CLI (Antigravity / Claude Code) "
                         "đã đăng nhập")
    # v0.1.28 (UX C1): nguồn đã gọi thử OK mà Owner chưa bấm "Dùng model này" → tự dùng model đầu tiên nhận được khi
    # gọi thử (bỏ model embedding). Không nguồn nào có model → chưa cho qua bước (trước đây qua được nhưng không agent
    # nào gọi được model: sàng lọc không chạy, Gen báo "chưa có model").
    model_id = None
    ready_ids = {p.id for p in ready}
    for p in (next(f for f in found if f.id == i) for i in ids):
        if p.id not in ready_ids:
            continue
        mid = await _first_model(db, p.id)
        if mid is None:
            choice = _tested_choice(p)
            if choice:
                mid = (await db.execute(text("""
                    INSERT INTO agent.models (provider_id, model_name, effort) VALUES (:p, :m, :e)
                    ON CONFLICT (provider_id, model_name) DO UPDATE SET model_name = EXCLUDED.model_name
                    RETURNING id"""), {"p": p.id, "m": choice[0], "e": choice[1]})).scalar_one()
        model_id = model_id or mid
    if model_id is None:
        raise incomplete("Chưa có model nào để dùng — bấm \"Kiểm tra\" ở một nguồn rồi chọn \"Dùng model này\"")
    # Thứ tự: nguồn Owner chọn (đã sẵn sàng) đứng đầu theo đúng thứ tự gửi lên; nguồn lỗi / chưa kiểm tra xuống cuối
    # (UX N1: nguồn gọi thử lỗi từng đứng ĐẦU chuỗi).
    rest = (await db.execute(text("""SELECT id FROM agent.providers WHERE org_id = :o AND NOT (id = ANY(:ids))
                                     ORDER BY failover_rank NULLS LAST, created_at"""),
                             {"o": row.org_id, "ids": ids})).scalars().all()
    new_order = [*ids, *rest]
    # v0.1.68 (F003): SAU Hoàn tất, ĐỔI hạng chuỗi nguồn AI cần phiên PIN `ai.route_change` (cùng PATCH /providers);
    # gửi lại đúng thứ tự đang có thì không đòi PIN. Đang thiết lập lần đầu (chưa Hoàn tất) không đòi. Thứ tự:
    # 422/409 → 423.
    if row.finished_at is not None and not owner.pin_active():
        current = (await db.execute(text("""SELECT id FROM agent.providers WHERE org_id = :o
                                            ORDER BY failover_rank NULLS LAST, created_at"""),
                                    {"o": row.org_id})).scalars().all()
        if list(current) != new_order:
            raise pin_required()
    for rank, pid in enumerate(new_order, start=1):
        await db.execute(text("UPDATE agent.providers SET failover_rank = :r WHERE id = :i"), {"r": rank, "i": pid})
    # v0.1.55 (G2): KHÔNG ghi agent.bindings nữa — hồ sơ tiêu chuẩn (gh.defaults) tự phủ cho Gen, Sàng lọc, Trả lời…;
    # Owner vẫn gán riêng từng agent ở màn API & Model khi muốn.
    out = await _mark_done(db, request, row, owner, 4, {"provider_ids": [str(i) for i in ids],
                                                         "model_id": str(model_id)})
    # v0.1.59: nguồn được bật + có dòng model ⇒ thẻ "Hôm nay của Sếp" (Gen chưa có model…) đọc lại ngay.
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, row.org_id)
    return out


#: Nguồn đã gọi thử OK (hoặc CLI đã đăng nhập), theo thứ tự chuỗi — dùng cho `ensure_tested_models`.
_TESTED_SOURCES_SQL = """
        SELECT p.id, p.kind, CASE WHEN jsonb_typeof(p.last_test->'models') = 'array' THEN
                    ARRAY(SELECT jsonb_array_elements_text(p.last_test->'models')) END AS test_models,
               p.last_test->>'probe_model' AS probe_model, p.last_test->>'probe_effort' AS probe_effort
        FROM agent.providers p
        WHERE p.org_id = :o AND p.kind <> 'system_one' AND p.is_enabled
          AND ((p.kind IN ('antigravity_cli', 'claude_code_cli') AND EXISTS (SELECT 1 FROM agent.cli_profiles c
                                                      WHERE c.provider_id = p.id AND c.is_active))
               OR (p.kind NOT IN ('antigravity_cli', 'claude_code_cli') AND p.auth_state = 'ok'
                   AND COALESCE((p.last_test->>'ok')::boolean, false)))
        ORDER BY p.failover_rank NULLS LAST, p.created_at"""


async def ensure_tested_models(db: AsyncSession, org_id: uuid.UUID) -> int:
    """v0.1.55 (G2): với mỗi nguồn đã gọi thử OK mà CHƯA có model nào, chép model đã gọi thử vào `agent.models` (giống
    bước 4 khi Owner chưa bấm "Dùng model này"). KHÔNG ghi `agent.bindings`. Trả số model mới thêm. Chạy lại an toàn."""
    added = 0
    for p in (await db.execute(text(_TESTED_SOURCES_SQL), {"o": org_id})).all():
        if await _first_model(db, p.id) is not None:
            continue
        choice = _tested_choice(p)
        if choice is None:
            continue
        await db.execute(text("""
            INSERT INTO agent.models (provider_id, model_name, effort) VALUES (:p, :m, :e)
            ON CONFLICT (provider_id, model_name) DO NOTHING"""), {"p": p.id, "m": choice[0], "e": choice[1]})
        added += 1
    return added


CLI_KINDS = ("antigravity_cli", "claude_code_cli")


def _tested_name(p: Any) -> str | None:
    """Model tự dùng khi Owner chưa bấm "Dùng model này": model ĐÃ gọi thử thật thành công (v0.1.31, nguồn CLI), không
    có thì model đầu tiên nhận được khi gọi thử (bỏ embedding)."""
    if isinstance(getattr(p, "probe_model", None), str) and p.probe_model:
        return str(p.probe_model)[:120]
    name = next((m for m in (p.test_models or []) if isinstance(m, str) and "embed" not in m.lower()), None)
    return name[:120] if name else None


def _tested_effort(p: Any, name: str) -> str | None:
    """Mức suy nghĩ đã gọi thử thành công cùng model đó (v0.1.32) — chỉ khi đúng model vừa gọi thử."""
    eff = getattr(p, "probe_effort", None)
    if name == getattr(p, "probe_model", None) and eff in ("low", "medium", "high", "xhigh", "max"):
        return str(eff)
    return None


def _tested_choice(p: Any) -> tuple[str, str | None] | None:
    """(model gốc, mức suy nghĩ) tự dùng khi Owner chưa chọn. `last_test` từ bản ≤ v0.1.31 có thể còn tên biến thể
    ("gemini-3.8-flash-high") → tách thành model gốc + mức (review v0.1.32), không bao giờ lưu tên biến thể."""
    from gh.providers.catalog import split_variant

    name = _tested_name(p)
    if not name:
        return None
    base, var_effort = split_variant(str(getattr(p, "kind", "") or ""), name)
    return base[:120], _tested_effort(p, name) or var_effort


async def _first_model(db: AsyncSession, provider_id: uuid.UUID) -> uuid.UUID | None:
    return (await db.execute(text("""SELECT id FROM agent.models WHERE provider_id = :p
                                     AND is_enabled AND model_name NOT ILIKE '%embed%'
                                     ORDER BY is_default DESC, id LIMIT 1"""),
                             {"p": provider_id})).scalar_one_or_none()


@router.put("/steps/5")
async def step5(request: Request, db: AsyncSession = DB,
                user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Kết nối kênh: cần ít nhất một kênh đang hoạt động (quét QR xong)."""
    row, owner = await _owner_step(db, user, after_finish=True)
    live = (await db.execute(text("""
        SELECT array_agg(DISTINCT c.type) FROM core.channel_sessions s JOIN core.channels c ON c.id = s.channel_id
        WHERE c.org_id = :o AND s.state = 'active' AND s.ended_at IS NULL"""), {"o": row.org_id})).scalar()
    if not live:
        raise incomplete("Cần kết nối ít nhất một kênh (quét mã QR) trước khi tiếp tục")
    return await _mark_done(db, request, row, owner, 5, {"channels": sorted(live)})


@router.put("/steps/6")
async def step6(body: Step6In, request: Request, db: AsyncSession = DB,
                user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Chọn nhóm lắng nghe: lưu chế độ từng nhóm; cần ít nhất một nhóm khác \"tắt\"."""
    row, owner = await _owner_step(db, user, after_finish=True)
    for g in body.groups:
        await update_group(db, request.app.state.redis, owner, g.id,
                           GroupPatch.model_validate(g.model_dump(exclude={"id"})))
    # v0.1.65 (F066): "đang nghe" = LISTENING_MODES (không tính off, paused, pending) và chưa rời nhóm.
    listening = int((await db.execute(text("""SELECT count(*) FROM core.groups
                                              WHERE org_id = :o AND left_at IS NULL AND listen_mode = ANY(:m)"""),
                                      {"o": row.org_id, "m": list(LISTENING_MODES)})).scalar_one())
    if listening == 0:
        raise incomplete("Cần bật lắng nghe ít nhất một nhóm")
    return await _mark_done(db, request, row, owner, 6, {"listening": listening})


@router.get("/rule-presets")
async def rule_presets(db: AsyncSession = DB,
                       user: service.CurrentUser | None = Depends(optional_user)) -> list[dict[str, Any]]:
    _owner_of(await _row(db), user)
    return [{k: p[k] for k in ("code", "name", "kind", "threshold", "enabled", "conditions", "outputs",
                               "prompt_hint")} for p in presets.PRESETS]


@router.put("/steps/7")
async def step7(body: Step7In, request: Request, db: AsyncSession = DB,
                user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Sàng lọc: lịch chạy (chu kỳ HOẶC ngưỡng), bộ quy tắc khởi đầu, trọng số chấm điểm.

    v0.1.55 (G2): mọi trường có mặc định (900 giây / 500 tin / lô 250 / tin cậy 0,6; quy tắc khởi đầu bật sẵn) — web
    chỉ hỏi "ngành nào" (bộ quy tắc); gửi `{}` cũng được."""
    row, owner = await _owner_step(db, user, after_finish=True)
    known = {p["code"]: p for p in presets.PRESETS}
    rule_codes = [str(p["code"]) for p in presets.PRESETS if p["enabled"]] if body.rule_codes is None \
        else body.rule_codes
    unknown = [c for c in rule_codes if c not in known]
    if unknown:
        raise field_errors({"rule_codes": f"Không có quy tắc {', '.join(unknown)}"})
    current = await load_schedule(db, row.org_id)
    await save_schedule(db, row.org_id, ScheduleIn(interval_seconds=body.interval_seconds,
                                                   count_threshold=body.count_threshold,
                                                   batch_size=current.batch_size, min_confidence=body.min_confidence))
    if body.weights:
        await save_weights(db, row.org_id, body.weights)
    existing = {r.code: r.id for r in (await db.execute(text("SELECT code, id FROM refinery.rules WHERE org_id = :o"),
                                                            {"o": row.org_id})).all()}
    for code, p in known.items():
        on = code in rule_codes
        if code in existing:
            await db.execute(text("UPDATE refinery.rules SET is_enabled = :e, updated_at = now() WHERE id = :i"),
                             {"e": on, "i": existing[code]})
        else:
            rin = RuleIn(name=p["name"], kind=p["kind"], conditions=p["conditions"], outputs=p["outputs"],
                         threshold=p["threshold"], prompt_hint=p.get("prompt_hint"))
            await create_rule(db, row.org_id, rin, owner.id, code=code, enabled=on)
    return await _mark_done(db, request, row, owner, 7,
                            {"interval_seconds": body.interval_seconds, "count_threshold": body.count_threshold,
                             "min_confidence": body.min_confidence, "rule_codes": rule_codes})


# ─── Bước 8–9 (giai đoạn 3) ─────────────────────────────────────────────────

#: v0.1.55 (G2): bước 8 chỉ cần chọn mẫu — thiếu tên / vai trò thì điền mặc định; thiếu `try_message` thì KHÔNG thử trò
#: chuyện (không gọi model, không tốn lượt).
STEP8_DEFAULT_NAME = "Trợ lý"
STEP8_DEFAULT_ROLE = "Theo dõi tin nhắn, báo việc quan trọng và soạn sẵn trả lời chờ Sếp duyệt."
#: Khoá mẫu cũ của web ≤ 0.1.60 → khoá thật của máy chủ (khớp migration 0036).
STEP8_LEGACY_TEMPLATES = {"sales": "commercial", "ops": "admin"}


class Step8In(BaseModel):
    name: str = Field(default=STEP8_DEFAULT_NAME, min_length=1, max_length=120)
    role_desc: str = Field(default=STEP8_DEFAULT_ROLE, min_length=1, max_length=500)
    voice: str = Field(default="Thân thiện, chuyên nghiệp, xưng hô lịch sự", max_length=200)
    speak_when: str = Field(default="Khi được hỏi trực tiếp hoặc có việc cần báo", max_length=500)
    template: str | None = Field(default=None, max_length=40)
    try_message: str | None = Field(default=None, max_length=1000)

    @field_validator("template", mode="before")
    @classmethod
    def _v_template(cls, v: Any) -> Any:
        """F070: chỉ nhận khoá có thật trong `agents_api.TEMPLATES`. Bản web cũ còn trong bộ nhớ đệm gửi 'sales'/'ops'
        thì đổi sang khoá mới (commercial/admin) thay vì lưu khoá sai làm PATCH ở Danh tính Agent báo 422 lần nữa."""
        from gh.agents_api.routes import _check_template

        v = STEP8_LEGACY_TEMPLATES.get(v, v) if isinstance(v, str) else v
        return _check_template(v if v != "" else None)


async def _setup_agent_id(db: AsyncSession, row: Any) -> uuid.UUID | None:
    """Agent đã tạo ở bước 8 (`completed.setup_agent_id`) nếu còn tồn tại — làm lại bước 8 thì cập nhật nó (F044).
    Agent đã Lưu trữ (v0.1.67) coi như không còn: làm lại bước 8 tạo agent mới, không "sửa" agent đã cất đi."""
    aid = (row.completed or {}).get("setup_agent_id")
    if not aid:
        return None
    return (await db.execute(text("SELECT id FROM agent.identities WHERE org_id = :o AND id = CAST(:i AS uuid) "
                                  "AND archived_at IS NULL"),
                             {"o": row.org_id, "i": aid})).scalar_one_or_none()


@router.put("/steps/8")
async def step8(body: Step8In, request: Request, db: AsyncSession = DB,
                user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Agent đầu tiên (tối thiểu để trình thiết lập đi hết được — quản lý Agent đầy đủ là GĐ 4 mục 4.1): tạo
    `agent.identities` (mức tự trị khởi tạo theo mặc định chung, đặt lại chính xác 3 hay 4 ở bước 9) rồi thử trò
    chuyện một lượt qua `ModelRouter` (`gh.biz.people.routes.try_chat`) — KHÔNG lưu vào hội thoại thật, chỉ để
    Owner nghe thử giọng agent. Model chưa gọi được (chưa cấu hình xong ở bước 4, hoặc lỗi tạm thời) không được
    chặn việc tạo agent — trả `try_reply: null` kèm lý do, Owner thử lại ngay ở đây hoặc ở màn Agent Identity."""
    # v0.1.68 (F026): KHÔNG đòi bước 4 `done` — "Để sau" bước 4 (đã có model thật) ghi 'skipped' nhưng vẫn tạo được
    # agent, đúng như
    # docstring này; bước 8 từ v0.1.55 không gọi model trừ khi có `try_message`.
    row, owner = await _owner_step(db, user, after_finish=True)
    name, role_desc, voice, speak_when = (body.name.strip(), body.role_desc.strip(), body.voice.strip(),
                                          body.speak_when.strip())
    # v0.1.68 (F044): làm lại bước 8 ⇒ CẬP NHẬT agent của bước 8 (`completed.setup_agent_id`), không tạo thêm agent
    # trùng.
    agent_id = await _setup_agent_id(db, row)
    # v0.1.67 (F085) × v0.1.68: bước 8 cũng không tạo / đổi sang tên trùng agent đang dùng (Lưu trữ không tính).
    await _check_name_free(db, row.org_id, name, except_id=agent_id)
    if agent_id is not None:
        await db.execute(text("""UPDATE agent.identities SET name = :n, role_desc = :rd, voice = :v, speak_when = :sw,
                                 template = COALESCE(:tpl, template), updated_at = now() WHERE id = :i"""),
                         {"n": name, "rd": role_desc, "v": voice, "sw": speak_when, "tpl": body.template,
                          "i": agent_id})
    else:
        agent_id = (await db.execute(text("""
            INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when,
                                          autonomy_level)
            VALUES (:o, :n, :rd, :tpl, '{}'::jsonb, :v, :sw, :al) RETURNING id"""),
            {"o": row.org_id, "n": name, "rd": role_desc, "tpl": body.template, "v": voice, "sw": speak_when,
             "al": policy.DEFAULT_AUTONOMY})).scalar_one()
        # v0.1.68 (F079): agent mới nghe sẵn MỌI kênh đã nối (cả kênh, chưa thu hẹp theo nhóm) — không thì agent
        # không nghe
        # kênh nào cho tới khi Sếp tự tick (biz/duty/context chỉ chọn agent có phạm vi). Bỏ bớt ở Danh tính Agent.
        await db.execute(text("""
            INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id)
            SELECT :a, c.id, NULL FROM core.channels c
            WHERE c.org_id = :o AND EXISTS (SELECT 1 FROM core.channel_sessions s WHERE s.channel_id = c.id
                                            AND s.state = 'active' AND s.ended_at IS NULL)"""),
                         {"a": agent_id, "o": row.org_id})
    listening = int((await db.execute(text("SELECT count(*) FROM agent.channel_scopes WHERE agent_id = :a"),
                                      {"a": agent_id})).scalar_one())
    try_reply: str | None = None
    try_error: str | None = None
    try_error_code: str | None = None
    try_reasons: list[str] = []
    try_message = (body.try_message or "").strip()
    try:
        if try_message:     # v0.1.55: không có câu thử ⇒ không gọi model
            try_reply = await try_chat(request.app.state, row.org_id, agent_id, name=name, role_desc=role_desc,
                                       voice=voice, message=try_message)
    except ApiError as e:
        # v0.1.30: `try_error` LUÔN là chuỗi (hợp đồng web `string | null`). Trước đây gán thẳng `e.detail`
        # (`{"reasons": […]}`) → web vẽ đối tượng làm React child → màn /guide/8 sập (React error #31).
        try_error = e.detail if isinstance(e.detail, str) and e.detail else e.title
        try_error_code = e.code
        try_reasons = [str(r) for r in e.extra.get("reasons") or []]
    # Nhớ agent của bước 8 — bước 9 đặt mức tự trị cho ĐÚNG agent này (không phải agent mới tạo gần nhất).
    state = await _mark_done(db, request, row, owner, 8, {"agent_id": str(agent_id), "name": name},
                             remember={"setup_agent_id": str(agent_id)})
    state["agent"] = {"id": str(agent_id), "name": name, "try_reply": try_reply, "try_error": try_error,
                      "try_error_code": try_error_code, "try_reasons": try_reasons, "listening_channels": listening}
    return state


class Step9In(BaseModel):
    # None = giữ nguyên mức hiện tại (mở lại sau Hoàn tất chỉ để xác nhận ranh giới, hoặc agent đang ở mức khác 3/4).
    autonomy_level: Literal[3, 4] | None = policy.DEFAULT_AUTONOMY  # type: ignore[assignment]
    # v0.1.55 (G2): web thay ô tích bằng một dòng ghi chú ("tiếp tục = đã đọc ranh giới khoá cứng") và GỬI `true` rõ
    # ràng. API vẫn giữ cổng cũ: thiếu trường hoặc `false` ⇒ 422 (client khác không được coi là đã xác nhận ngầm).
    ack_boundaries: bool = False


async def _setup_agent(db: AsyncSession, row: Any) -> Any:
    """Agent của bước 9: agent tạo ở bước 8 (`completed.setup_agent_id`); bản cài trước khi có trường này (hoặc
    agent đó đã bị xoá) → agent tạo sớm nhất còn lại — agent thiết lập luôn là agent đầu tiên, KHÔNG phải agent mới
    tạo gần nhất ở màn Danh tính Agent."""
    aid = (row.completed or {}).get("setup_agent_id")
    if aid:
        agent = (await db.execute(text("SELECT id, name, autonomy_level FROM agent.identities "
                                       "WHERE org_id = :o AND id = CAST(:i AS uuid) AND archived_at IS NULL"),
                                  {"o": row.org_id, "i": aid})).one_or_none()
        if agent is not None:
            return agent
    return (await db.execute(text("SELECT id, name, autonomy_level FROM agent.identities WHERE org_id = :o "
                                  "AND archived_at IS NULL ORDER BY created_at ASC LIMIT 1"),
                             {"o": row.org_id})).one_or_none()


@router.get("/steps/9")
async def step9_get(db: AsyncSession = DB,
                    user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Agent bước 9 sẽ đặt mức tự trị + mức hiện tại — form điền sẵn, mở lại sau Hoàn tất không đổi nhầm mức."""
    row = await _row(db)
    _owner_of(row, user)
    agent = await _setup_agent(db, row)
    if agent is None:
        return {"agent": None}
    return {"agent": {"id": str(agent.id), "name": agent.name, "autonomy_level": agent.autonomy_level}}


@router.put("/steps/9")
async def step9(body: Step9In, request: Request, db: AsyncSession = DB,
                user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Tự trị & ranh giới: đặt mức tự trị (3 hoặc 4 — spec H1 cho phép cả hai, mặc định 4) cho agent tạo ở bước 8
    (`_setup_agent`), và bắt Owner xác nhận đã đọc danh sách ranh giới khoá cứng (`HARD_BOUNDARIES`,
    ARCHITECTURE §7.4). Các ranh giới đó **không tắt được** ở đây hay bất cứ đâu trong hệ thống — xác nhận chỉ để
    Owner biết trước khi vào Console, không phải một cài đặt.

    v0.1.45 (F-20): SAU Hoàn tất (mở lại từ trang Hướng dẫn) ĐỔI mức tự trị cần phiên PIN `policy.change` (423);
    gửi lại đúng mức đang có (hoặc `autonomy_level = null` — giữ nguyên) không đòi PIN, như PATCH agent. Đang thiết
    lập lần đầu (chưa Hoàn tất) không đòi PIN. Thứ tự: 401/403 → 422 → 423."""
    row, owner = await _owner_step(db, user, after_finish=True)
    if not body.ack_boundaries:
        raise field_errors({"ack_boundaries": "Cần xác nhận đã đọc ranh giới khoá cứng trước khi tiếp tục"})
    agent = await _setup_agent(db, row)
    if agent is None:
        raise incomplete("Chưa có agent nào — hoàn thành bước 8 trước")
    level = agent.autonomy_level if body.autonomy_level is None else body.autonomy_level
    changed = level != agent.autonomy_level
    if changed and row.finished_at is not None and not owner.pin_active():
        raise pin_required()
    if changed:
        await db.execute(text("UPDATE agent.identities SET autonomy_level = :a, updated_at = now() WHERE id = :i"),
                         {"a": level, "i": agent.id})
    state = await _mark_done(db, request, row, owner, 9,
                             {"agent_id": str(agent.id), "autonomy_level": level, "changed": changed,
                              "hard_boundaries": list(HARD_BOUNDARIES)})
    state["agent"] = {"id": str(agent.id), "name": agent.name, "autonomy_level": level}
    state["hard_boundaries"] = list(HARD_BOUNDARIES)
    return state


# ─── Bước 10–11 (giai đoạn 4) ────────────────────────────────────────────────

class Step10Invite(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=320)
    role: Literal["manager", "operator", "agent_staff", "auditor"]


class Step10In(BaseModel):
    invites: list[Step10Invite] = Field(default_factory=list, max_length=50)


@router.put("/steps/10")
async def step10(body: Step10In, request: Request, db: AsyncSession = DB,
                 user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Mời đội ngũ: bước tuỳ chọn, tạo tài khoản + mật khẩu tạm cho từng người (chưa có SMTP thật gửi lời mời —
    mật khẩu tạm trả thẳng về đây để Owner tự gửi qua kênh riêng). v0.1.68 (F038): danh sách rỗng chỉ qua được khi đã
    có người dùng khác Owner đang hoạt động, còn không → 409 STEP_INCOMPLETE (gọi `POST /setup/steps/10/skip` nếu
    muốn để sau).

    v0.1.45 (F-20): SAU Hoàn tất, danh sách mời KHÁC RỖNG (tạo tài khoản + mật khẩu tạm) cần phiên PIN
    `user.manage` (423); danh sách rỗng, hoặc đang thiết lập lần đầu, không đòi PIN."""
    row, owner = await _owner_step(db, user, after_finish=True)
    if body.invites and row.finished_at is not None and not owner.pin_active():
        raise pin_required()
    # v0.1.68 (F038): danh sách RỖNG không phải "đã mời" — chỉ coi là xong khi đã có người mới đang hoạt động (cùng
    # định nghĩa
    # `staff.active` của Gen); chưa có thì nói thật và chỉ lối "Để sau".
    if not body.invites and not bool((await db.execute(text(STAFF_ACTIVE_SQL), {"o": row.org_id})).scalar()):
        raise incomplete("Chưa nhập người nào — thêm ít nhất một người để mời, hoặc bấm \"Để sau\"")
    errors: dict[str, str] = {}
    seen: set[str] = set()
    created: list[dict[str, Any]] = []
    for idx, inv in enumerate(body.invites):
        email, name = inv.email.strip().lower(), inv.display_name.strip()
        if not EMAIL_RE.match(email):
            errors[f"invites.{idx}.email"] = "Email chưa đúng định dạng"
        elif email in seen:
            errors[f"invites.{idx}.email"] = "Email bị lặp trong danh sách"
        elif (await db.execute(text("SELECT 1 FROM core.users WHERE org_id = :o AND email = :e"),
                               {"o": row.org_id, "e": email})).scalar():
            errors[f"invites.{idx}.email"] = "Email đã có tài khoản"
        if not name:
            errors[f"invites.{idx}.display_name"] = "Nhập tên hiển thị"
        if f"invites.{idx}.email" in errors or f"invites.{idx}.display_name" in errors:
            continue
        seen.add(email)
        temp_password = new_temp_password()
        uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash,
                                                                must_change_password)
                                        VALUES (:o, :e, :n, :p, true) RETURNING id"""),
                                {"o": row.org_id, "e": email, "n": name,
                                 "p": hash_secret(temp_password)})).scalar_one()
        role_id = (await db.execute(text("SELECT id FROM core.roles WHERE org_id = :o AND code = :r"),
                                    {"o": row.org_id, "r": inv.role})).scalar_one()
        await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"),
                         {"u": uid, "r": role_id})
        await actionlog.record(db, org_id=row.org_id, actor_type="user", actor_id=owner.actor_id,
                               action="setup.member_invited", target_type="user", target_id=str(uid),
                               target_label=name, detail={"role": inv.role}, ip=client_ip(request))
        created.append({"id": str(uid), "display_name": name, "email": email, "role": inv.role,
                        "temp_password": temp_password})
    if errors:
        raise field_errors(errors)
    state = await _mark_done(db, request, row, owner, 10, {"invited": len(created)})
    state["invited"] = created
    return state


TIME_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Step11In(BaseModel):
    frequency: Literal["daily", "weekly", "monthly"] = "daily"
    time_of_day: str = Field(default="02:00", max_length=5)
    retention_count: int = Field(default=7, ge=1, le=365)
    # v0.1.40 (F-12): chỉ 'local' — chưa có đường sao lưu S3/MinIO thật; bản sao ngoài máy (ổ USB/NAS) chọn ở
    # Cài đặt › Sao lưu & cập nhật › Bản sao ngoài máy (genh chép gói ra ổ ngoài), không qua trường này.
    destination: Literal["local"] = "local"

    @field_validator("destination", mode="before")
    @classmethod
    def _only_local(cls, v: Any) -> Any:
        if v != "local":
            raise PydanticCustomError(
                "destination_local_only",
                "Hiện chỉ sao lưu trên máy chủ này — bản sao ngoài máy (ổ USB/NAS) chọn ở Cài đặt › Sao lưu & cập nhật "
                "sau khi thiết lập xong")
        return v


@router.put("/steps/11")
async def step11(body: Step11In, request: Request, db: AsyncSession = DB,
                 user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Sao lưu: bước tuỳ chọn, chỉ lưu LỊCH và ĐÍCH sao lưu (`core.organizations.settings->'backup'`) — chạy
    `pg_dump` + MinIO thật, mã hoá, vòng 7 ngày/4 tuần/12 tháng là việc của GĐ 5 mục 5.6 (`docs/PLAN.md`),
    không làm ở trình thiết lập."""
    row, owner = await _owner_step(db, user, after_finish=True)
    if not TIME_HHMM_RE.match(body.time_of_day):
        raise field_errors({"time_of_day": "Giờ chạy sao lưu dạng HH:MM (00:00–23:59)"})
    cfg = {"frequency": body.frequency, "time_of_day": body.time_of_day, "retention_count": body.retention_count,
           "destination": body.destination}
    await db.execute(text("UPDATE core.organizations SET settings = settings || CAST(:s AS jsonb) WHERE id = :o"),
                     {"s": json.dumps({"backup": cfg}), "o": row.org_id})
    state = await _mark_done(db, request, row, owner, 11, cfg)
    state["backup"] = cfg
    return state


# Bước tuỳ chọn → màn Console làm tiếp (khớp FOLLOW_UP ở web). "done" suy từ DỮ LIỆU THẬT, không chỉ từ trạng thái
# trình thiết lập: Owner để sau bước 5 rồi quét QR ở màn Kênh thì mục tự biến mất khỏi "Việc thiết lập tiếp".
# v0.1.63 (B2, F010): bước 4 KHÔNG có SQL ở đây — "xong" = `readiness.ai_ready` (có nguồn AI DÙNG ĐƯỢC:
# đang bật, có khoá
# bật / phiên CLI chưa hết hạn, có model bật), không chỉ có dòng model. Xem `follow_up_status`.
AI_STEP = 4
STAFF_ACTIVE_SQL = """SELECT EXISTS (SELECT 1 FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
                                     JOIN core.roles r ON r.id = ur.role_id
                                     WHERE u.org_id = :o AND u.is_active AND u.deleted_at IS NULL
                                       AND r.code <> 'owner')"""
FOLLOW_UP_SQL: dict[int, str] = {
    5: """SELECT EXISTS (SELECT 1 FROM core.channel_sessions s JOIN core.channels c ON c.id = s.channel_id
                         WHERE c.org_id = :o AND s.state = 'active' AND s.ended_at IS NULL)""",
    6: ("SELECT EXISTS (SELECT 1 FROM core.groups WHERE org_id = :o AND left_at IS NULL "
        f"AND listen_mode IN ({', '.join(repr(m) for m in LISTENING_MODES)}))"),   # một nguồn: gh.data.common
    7: "SELECT EXISTS (SELECT 1 FROM refinery.rules WHERE org_id = :o AND is_enabled)",
    8: "SELECT EXISTS (SELECT 1 FROM agent.identities WHERE org_id = :o AND archived_at IS NULL)",
    # Mức tự trị đặt ngay trong form tạo/sửa agent ở Console → có agent là đã có mức tự trị.
    9: "SELECT EXISTS (SELECT 1 FROM agent.identities WHERE org_id = :o AND archived_at IS NULL)",
    # v0.1.68 (F038): có người dùng KHÁC Owner đang hoạt động (không tính tài khoản đã khoá / xoá) — giống
    # `staff.active` của Gen.
    10: STAFF_ACTIVE_SQL,
    11: "SELECT (SELECT settings ? 'backup' FROM core.organizations WHERE id = :o)",
}

# v0.1.39 (F-28): hai việc KHÔNG thuộc trình thiết lập (không có trong STEPS, không chặn bước 12) nhưng nằm trong
# "Hướng dẫn thiết lập" — xong theo dữ liệu thật: Facebook đã từng đăng nhập; Gen-hub đã Kiểm tra xanh ít nhất một lần.
FOLLOW_UP_EXTRA: tuple[tuple[int, str, str, str], ...] = (
    (13, "social", "Kết nối Facebook",
     # v0.1.68 (F028): `needs_login` (phiên hết, phải đăng nhập lại) KHÔNG tính là xong.
     "SELECT EXISTS (SELECT 1 FROM core.social_accounts WHERE org_id = :o AND platform LIKE 'facebook%' "
     "AND status IN ('active', 'paused'))"),
    # v0.1.68 (F028): theo bản kiểm `hub` MỚI NHẤT — cùng nguồn với dòng Gen-hub ở "Việc Sếp cần làm"; chưa có bản
    # kiểm nào
    # (máy nâng cấp từ bản cũ) thì theo `last_ok_at` như trước.
    (14, "hub", "Nối Gen-hub",
     "SELECT COALESCE((SELECT b.status = 'pass' FROM ops.boss_checks b WHERE b.org_id = :o AND b.check_key = 'hub' "
     "AND b.status IN ('pass', 'fail') ORDER BY b.checked_at DESC, b.id DESC LIMIT 1), "
     "EXISTS (SELECT 1 FROM agent.hub_links WHERE org_id = :o AND last_ok_at IS NOT NULL))"),
)


@router.get("/hard-boundaries")
async def hard_boundaries(db: AsyncSession = DB,
                          user: service.CurrentUser | None = Depends(optional_user)) -> list[str]:
    """Danh sách ranh giới khoá cứng để bước 9 hiển thị TRƯỚC khi Owner xác nhận (một nguồn duy nhất với API)."""
    _owner_of(await _row(db), user)
    return list(HARD_BOUNDARIES)


async def follow_up_status(db: AsyncSession, row: Any) -> list[dict[str, Any]]:
    """Trạng thái từng việc thiết lập tuỳ chọn (bước 4–11 theo STEPS + 13 Facebook, 14 Gen-hub) cho tổ chức của
    `row` (cần `.org_id` và `.completed` — dòng `ops.setup_state`). v0.1.54 (g1-api): tách khỏi endpoint để Gen
    hướng dẫn (gh.gen.coach.signals) dùng lại đúng một nguồn sự thật; đầu ra `GET /setup/follow-up` KHÔNG đổi."""
    status = (row.completed or {}).get("steps", {})
    out = []
    for n, key, title, required, _phase in STEPS:
        if required or (n not in FOLLOW_UP_SQL and n != AI_STEP):
            continue
        if n == AI_STEP:
            from gh.providers import readiness

            real = bool((await readiness.ai_ready(db, row.org_id))["ok"])
        else:
            real = bool((await db.execute(text(FOLLOW_UP_SQL[n]), {"o": row.org_id})).scalar())
        # Bước 4: tắt / gỡ khoá / hết phiên nguồn sau khi đã xong bước 4 thì lại "Chưa có model".
        # Bước 4 và 10 chỉ theo dữ liệu thật (10: bấm Tiếp tục với danh sách rỗng không phải "đã mời"; F038).
        done = real if n in (4, 10) else status.get(str(n)) == "done" or real
        out.append({"n": n, "key": key, "title": title, "status": status.get(str(n), "todo"), "done": done})
    for n, key, title, sql in FOLLOW_UP_EXTRA:
        done = bool((await db.execute(text(sql), {"o": row.org_id})).scalar())
        out.append({"n": n, "key": key, "title": title, "status": "todo", "done": done})
    return out


@router.get("/follow-up")
async def follow_up(db: AsyncSession = DB,
                    user: service.CurrentUser | None = Depends(optional_user)) -> list[dict[str, Any]]:
    """Việc thiết lập tiếp (thẻ ở Tổng quan + trang Hướng dẫn thiết lập): MỌI bước tuỳ chọn 5–11 (+ 13 Facebook,
    14 Gen-hub từ v0.1.39), `done` khi đã xong
    trong trình thiết lập HOẶC dữ liệu thật cho thấy đã làm ở Console — không phải bấm tay. Thẻ Tổng quan chỉ hiện
    mục chưa xong; trang Hướng dẫn hiện đủ để thấy tiến độ."""
    row = await _row(db)
    _owner_of(row, user)
    return await follow_up_status(db, row)


@router.get("/first-run")
async def first_run(db: AsyncSession = DB,
                    user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Số liệu lượt sàng lọc đầu tiên cho bước 12 (cập nhật trực tiếp qua WS `refinery.progress`)."""
    row = await _row(db)
    _owner_of(row, user)
    c = (await db.execute(text("""
        SELECT (SELECT count(*) FROM raw.events WHERE org_id = :o) AS raw_collected,
               count(*) FILTER (WHERE state IN ('pending', 'processing')) AS classifying,
               count(*) FILTER (WHERE state = 'clean') AS clean,
               count(*) FILTER (WHERE state = 'lowconf') AS lowconf,
               count(*) FILTER (WHERE state = 'discarded') AS discarded
        FROM refinery.event_state WHERE org_id = :o"""), {"o": row.org_id})).one()
    run = (await db.execute(text("""SELECT id, trigger, status, started_at, finished_at, input_count, clean_count,
                                           lowconf_count, noise_count, error_count
                                    FROM refinery.runs WHERE org_id = :o ORDER BY started_at DESC LIMIT 1"""),
                            {"o": row.org_id})).one_or_none()
    return {"raw_collected": c.raw_collected, "classifying": c.classifying, "clean": c.clean,
            "lowconf": c.lowconf, "discarded": c.discarded,
            "run": None if run is None else {
                "id": str(run.id), "trigger": run.trigger, "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "input_count": run.input_count, "clean_count": run.clean_count,
                "lowconf_count": run.lowconf_count, "noise_count": run.noise_count,
                "error_count": run.error_count}}


@router.put("/steps/12")
async def step12(request: Request, db: AsyncSession = DB,
                 user: service.CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Hoàn tất: chỉ khi mọi bước bắt buộc đã xong. Bước chưa có ở giai đoạn này → báo rõ bước nào còn thiếu."""
    row, owner = await _owner_step(db, user)
    done = (row.completed or {}).get("steps", {})
    missing = [n for n, _k, _t, required, _p in STEPS if required and n != 12 and done.get(str(n)) != "done"]
    if missing:
        raise incomplete("Còn bước bắt buộc chưa xong: " + ", ".join(str(n) for n in missing))
    await db.execute(text("UPDATE ops.setup_state SET finished_at = now() WHERE org_id = :o"), {"o": row.org_id})
    return await _mark_done(db, request, await _row(db), owner, 12)
