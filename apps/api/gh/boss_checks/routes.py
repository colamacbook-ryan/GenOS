"""/boss-checks — kiểm thật từng dòng trang "Việc Sếp cần làm" (v0.1.39). CHỈ Owner (+ `system.manage`).

- `GET /boss-checks` — chốt các lượt đọc Facebook đang chờ rồi trả {rows, results, required_done, required_total}.
- `POST /boss-checks/{key}/run` — chạy một mục kiểm (`hub`, `facebook`, `agy_call`, `agy_switch`, `claude_call`, `jev`,
  `telegram` — v0.1.44, Gửi thử như POST /notify/telegram/test; `remote_access` — v0.1.46, không cần PIN: quyết theo
  hostname của header Origin (trình duyệt luôn gửi Origin với POST; không có thì Host — KHÔNG tin Host trước vì proxy
  ngoài có thể đổi Host) và Settings.public_url: chưa chọn cách truy cập từ xa → REMOTE_NOT_CONFIGURED, đang mở trên
  chính máy chủ → REMOTE_OPENED_ON_SERVER, còn lại Đạt)
  và GHI kết quả. Lỗi nghiệp vụ (chưa cấu hình, 409/429 từ dịch vụ, gọi thử lỗi) vẫn 200 với `status: 'fail'` + mã lỗi
  thống nhất; chỉ 401/403/422/423 mới ném. `hub` và `agy_switch` cần phiên PIN (423 → web hỏi PIN rồi gửi lại).
- v0.1.49 (QD-16): `hub` Đạt thì lưu thêm `read_scopes` (lịch/mail/việc/Drive → bool) và `read_missing` (nhãn quyền
  còn thiếu) vào `detail` — chỉ để hiển thị "Quyền đọc thêm (không bắt buộc)"; Đạt/Lỗi KHÔNG phụ thuộc các quyền này.
  Lỗi thì KHÔNG lưu (quyền trong CSDL lúc đó là của lần kiểm cũ / toàn False — gợi ý tick quyền sẽ chỉ sai cách sửa).
- v0.1.50 (F-81): `hub` Đạt thì lưu thêm `write_scopes` ({kho, kho_create, kho_update: bool}) và `write_missing` —
  cũng chỉ để hiển thị. Dòng 9 "Gen ghi Kho" (`kho_write`) KHÔNG chạy được từ đây (không nằm trong RUNNABLE): máy
  chủ tự ghi 'pass' sau lần ghi Kho thật đầu tiên (gh.hub_link.service.write_kho).
- v0.1.55 (G2): `ai_source` (dòng 0 "nguồn AI", duy nhất bắt buộc) chạy được từ đây — gọi thử nguồn đầu chuỗi sẵn sàng
  (`ModelRouter.test_provider`, như nút Kiểm tra ở Bộ não AI). Không cần PIN; không ghi khoá vào log / kết quả.
- Phản hồi cho Owner được kèm email ĐẦY ĐỦ (`account`); CSDL chỉ lưu email đã che.
- Lỗi TẠM (bận/hạn mức: `TRANSIENT_CODES`) KHÔNG ghi thành bản kiểm: trả `{transient: true, status: 'fail', …}` để web
  báo ngay cạnh nút, còn kết quả đã lưu (Đạt / Đang chạy…) giữ nguyên — bấm lại khi đang chạy không biến "Xong" thành
  "Lỗi", lượt đọc Facebook đang chạy không bị một bản 'fail' mới hơn che mất.
"""

import contextlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require, require_owner
from gh.boss_checks import service as boss
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, field_errors, not_found, pin_required
from gh.gen.coach import cache as coach_cache
from gh.hub_link import service as hub
from gh.providers import cli as climod
from gh.providers import readiness
from gh.providers import router as mrouter
from gh.social import service as social_service

router = APIRouter(prefix="/boss-checks", tags=["boss-checks"])
MANAGE = require("system.manage", rbac.ALL)
PIN_KEYS = ("hub", "agy_switch")
RAISE_STATUSES = (401, 403, 422, 423)
CALL_KINDS = {"agy_call": "antigravity_cli", "claude_call": "claude_code_cli", "jev": "system_one"}
NOT_READY = {
    "agy_call": ("AGY_NOT_LOGGED_IN", "Chưa đăng nhập Google (Antigravity CLI) — bấm Đăng nhập Google trước"),
    "claude_call": ("CLAUDE_NOT_LOGGED_IN", "Chưa đăng nhập Claude Code — bấm Đăng nhập Claude Code trước"),
    "jev": ("JEV_NOT_CONFIGURED",
            "Chưa thêm Jev — mục này không bắt buộc; thêm khoá Jev ở Cài đặt › Bộ não AI nếu Sếp cần"),
}
TRANSIENT_CODES = boss.TRANSIENT_CODES
SOCIAL_NO_ACCOUNT_MSG = "Chưa có tài khoản Facebook — mở trang Tài khoản mạng xã hội để thêm và đăng nhập"


class BossRunIn(BaseModel):
    profile_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None


@router.get("")
async def get_checks(_m: service.CurrentUser = Depends(MANAGE), user: service.CurrentUser = Depends(require_owner),
                     db: AsyncSession = DB) -> dict[str, Any]:
    await boss.resolve_pending(db, user.org_id)
    await db.commit()
    return await boss.overview(db, user.org_id)


@router.post("/{key}/run")
async def run_check(key: str, request: Request, body: BossRunIn | None = None,
                    _m: service.CurrentUser = Depends(MANAGE), user: service.CurrentUser = Depends(require_owner),
                    db: AsyncSession = DB) -> dict[str, Any]:
    if key not in boss.RUNNABLE:
        raise not_found("Mục kiểm")
    if key in PIN_KEYS and not user.pin_active():
        raise pin_required()
    body = body or BossRunIn()
    if key == "agy_switch" and body.profile_id is None:
        raise field_errors({"profile_id": "Chọn tài khoản Google cần đổi sang"})
    out = await _dispatch(key, request, body, user, db)
    # v0.1.59: kết quả kiểm vừa ghi quyết định dòng "Việc Sếp cần làm" ⇒ commit rồi làm mới thẻ Hôm nay của Sếp ngay.
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)
    return out


async def _dispatch(key: str, request: Request, body: BossRunIn, user: service.CurrentUser,
                    db: AsyncSession) -> dict[str, Any]:
    try:
        if key == "hub":
            return await _run_hub(request, db, user)
        if key == "facebook":
            return await _run_facebook(request, db, user, body.account_id)
        if key == "agy_switch":
            assert body.profile_id is not None
            return await _run_switch(request, db, user, body.profile_id)
        if key == "remote_access":
            return await _run_remote(request, db, user)
        if key == "ai_source":
            return await _run_ai_source(request, db, user)
        if key == "telegram":
            # v0.1.44 (F-8c): cùng hàm với POST /notify/telegram/test (ghi bản kiểm + yêu cầu genh gửi thử).
            from gh.telegram.routes import run_test as telegram_test

            return await telegram_test(request, db, user)
        return await _run_call(request, db, user, key)
    except ApiError as e:
        if e.status in RAISE_STATUSES:
            raise
        # Lỗi nghiệp vụ (409/429/404…): câu của dịch vụ đã thân thiện → ghi thành một lần kiểm "Lỗi".
        await db.rollback()
        if e.code in TRANSIENT_CODES:
            return transient(key, e.code, e.title)
        return await boss.record(db, user.org_id, key, "fail", error_code=e.code, message=e.title,
                                 user_id=user.id)


def transient(key: str, code: str, message: str | None) -> dict[str, Any]:
    """Kết quả lỗi TẠM (bận/hạn mức) — KHÔNG ghi CSDL, không thay kết quả đã lưu (xem docstring module)."""
    return {"id": None, "key": key, "status": "fail", "error_code": code, "message": boss.clean_message(message),
            "detail": {}, "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "runs": 0,
            "transient": True}


# ─── từng mục ───────────────────────────────────────────────────────────────

async def _run_hub(request: Request, db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    client = hub.client_for(getattr(request.app.state, "mcp_transport", None))
    r = await hub.test_link(db, request.app.state.redis, client, user=user)
    out = await boss.record_hub_result(db, user.org_id, r, user.id)
    if out is None:
        return transient("hub", str(r["error_code"]), r.get("error"))
    return out


async def _run_facebook(request: Request, db: AsyncSession, user: service.CurrentUser,
                        account_id: uuid.UUID | None) -> dict[str, Any]:
    if account_id is None:
        account_id = (await db.execute(text("""
            SELECT id FROM core.social_accounts WHERE org_id = :o AND platform LIKE 'facebook%'
            ORDER BY (status = 'active') DESC, created_at LIMIT 1"""), {"o": user.org_id})).scalar_one_or_none()
    if account_id is None:
        return await boss.record(db, user.org_id, "facebook", "fail", error_code="SOCIAL_NO_ACCOUNT",
                                 message=SOCIAL_NO_ACCOUNT_MSG, user_id=user.id)
    job = await social_service.request_read(db, request.app.state.redis, org_id=user.org_id, account_id=account_id,
                                            via="user", user=user)
    return await boss.record(db, user.org_id, "facebook", "pending", detail={"job_status": job["status"]},
                             ref_id=uuid.UUID(job["id"]), user_id=user.id)


REMOTE_NOT_CONFIGURED_MSG = ("Chưa chọn cách truy cập từ xa — trên máy chủ chạy genh remote tailscale (khuyên dùng) "
                             "hoặc genh remote --lan")
REMOTE_ON_SERVER_MSG = ("Đang mở trên chính máy chủ — mở Console trên điện thoại bằng địa chỉ ở Cài đặt › Sao lưu & "
                        "cập nhật › Truy cập từ xa rồi bấm Kiểm tra từ đó")


def _opened_host(request: Request) -> str:
    """Hostname trình duyệt đã dùng: ưu tiên Origin (POST luôn có), 'null'/thiếu → Host."""
    from urllib.parse import urlparse

    origin = request.headers.get("origin", "").strip()
    if origin and origin != "null":
        with contextlib.suppress(ValueError):
            host = urlparse(origin).hostname
            if host:
                return host
    return (request.headers.get("host", "") or "").rsplit(":", 1)[0].strip("[]")


async def _run_remote(request: Request, db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    from gh.config import get_settings
    from gh.system_api import access

    if access.is_local_url(get_settings().public_url):
        return await boss.record(db, user.org_id, "remote_access", "fail", error_code="REMOTE_NOT_CONFIGURED",
                                 message=REMOTE_NOT_CONFIGURED_MSG, user_id=user.id)
    host = _opened_host(request)
    if access.is_local_url(host):
        return await boss.record(db, user.org_id, "remote_access", "fail", error_code="REMOTE_OPENED_ON_SERVER",
                                 message=REMOTE_ON_SERVER_MSG, user_id=user.id)
    net = access.network_status()
    return await boss.record(db, user.org_id, "remote_access", "pass", message=None,
                             detail={"opened_from": host, "access_mode": (net or {}).get("mode", "unknown")},
                             user_id=user.id)


async def _provider(db: AsyncSession, org_id: uuid.UUID, kind: str) -> Any:
    return (await db.execute(text("""
        SELECT p.id, p.name, EXISTS (SELECT 1 FROM agent.cli_profiles c WHERE c.provider_id = p.id AND c.is_active
                                     AND c.token_enc IS NOT NULL) AS has_session
        FROM agent.providers p WHERE p.org_id = :o AND p.kind = :k ORDER BY p.created_at LIMIT 1"""),
        {"o": org_id, "k": kind})).one_or_none()


async def _probe(request: Request, db: AsyncSession, user: service.CurrentUser, p: Any, kind: str) -> dict[str, Any]:
    """Gọi thử thật (như POST /providers/{id}/test): hạn mức gọi thử cho CLI, commit trước lượt gọi dài."""
    from gh.system_api.routes import _probe_budget

    if kind in climod.CLI_KINDS:
        await _probe_budget(request.app.state.redis, user.org_id)
    await db.commit()
    result: dict[str, Any] = await request.app.state.model_router.test_provider(p.id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="provider.tested",
                           target_type="provider", target_id=str(p.id), target_label=p.name,
                           result="ok" if result["ok"] else "failed",
                           detail={"error": result["error"], "via": "boss_checks"}, ip=user.ip)
    if kind in climod.CLI_KINDS:
        # v0.1.63 (F002): nguồn CLI gọi thử THẬT xanh mà chưa có dòng model nào ⇒ lưu cặp (model, mức) vừa chạy được —
        # cùng việc với POST /providers/{id}/test (nếu không, Kiểm tra xanh mà Gen vẫn "chưa có model").
        await mrouter.adopt_probed_model(db, user.org_id, p.id, p.name, result, actor_type="user",
                                         actor_id=user.actor_id, ip=user.ip)
    return result


def _call_detail(result: dict[str, Any], provider_id: Any = None) -> dict[str, Any]:
    out = _call_detail_base(result)
    if provider_id is not None:
        out["provider_id"] = str(provider_id)     # để `overview` biết bằng chứng này của nguồn nào (F014)
    return out


def _call_detail_base(result: dict[str, Any]) -> dict[str, Any]:
    return {"latency_ms": result.get("latency_ms"), "probe_model": result.get("probe_model"),
            "models_count": len(result.get("models") or []), "models_source": result.get("models_source"),
            "account_masked": boss.mask_email(result.get("account"))}


async def _run_call(request: Request, db: AsyncSession, user: service.CurrentUser, key: str) -> dict[str, Any]:
    kind = CALL_KINDS[key]
    p = await _provider(db, user.org_id, kind)
    if p is None or (kind in climod.CLI_KINDS and not p.has_session):
        code, msg = NOT_READY[key]
        return await boss.record(db, user.org_id, key, "fail", error_code=code, message=msg, user_id=user.id)
    result = await _probe(request, db, user, p, kind)
    ok = bool(result["ok"])
    if not ok and result.get("error_code") in TRANSIENT_CODES:
        return transient(key, str(result["error_code"]), result.get("error"))
    out = await boss.record(db, user.org_id, key, "pass" if ok else "fail",
                            error_code=None if ok else (result.get("error_code") or "PROVIDER_ERROR"),
                            message=None if ok else result.get("error"), detail=_call_detail(result),
                            user_id=user.id)
    if kind in climod.CLI_KINDS:
        out["account"] = result.get("account")
    if key == "claude_call" and ok:
        await _adopt_existing_claude_login(db, user, result)
    return out


#: Số nguồn tối đa được gọi thử trong MỘT lần bấm Kiểm tra (mỗi lượt CLI dùng hạn mức thật của gói).
AI_MAX_PROBES = 3


async def _run_ai_source(request: Request, db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    """Gọi thử nguồn AI như `POST /providers/{id}/test` (v0.1.55), chọn nguồn theo `readiness` (v0.1.63, F035).

    Ứng viên = nguồn `ready_to_probe` theo thứ tự chuỗi của Gen Owner (`core.gen`): đang bật, đã có khoá /
    phiên chưa hết
    hạn, không bị luật vai hay hộp riêng chặn; nguồn CLI chưa có model vẫn được thử để tự lưu model (F002).
    Nguồn đầu hỏng
    thì thử nguồn kế (tối đa `AI_MAX_PROBES`) — Gen dùng chuỗi, nên một nguồn chạy được là đủ; mọi nguồn
    hỏng thì báo lỗi
    của nguồn ĐẦU. Không ứng viên nào ⇒ ghi 'fail' kèm đúng lý do của `readiness` (không có nguồn, khoá,
    phiên, model…)."""
    states = await readiness.source_states(db, user.org_id, readiness.READY_ROLE)
    cands = [st for st in states if st["ready_to_probe"]]
    if not cands:
        verdict = readiness.explain(states)
        return await boss.record(db, user.org_id, "ai_source", "fail", error_code=boss.ai_error_code(verdict),
                                 message=verdict["reason_vi"], user_id=user.id)
    first_fail: tuple[dict[str, Any], dict[str, Any]] | None = None
    for st in cands[:AI_MAX_PROBES]:
        p = SimpleNamespace(id=uuid.UUID(st["provider_id"]), name=st["name"])
        try:
            result = await _probe(request, db, user, p, st["kind"])
        except ApiError:
            if first_fail is None:
                raise                       # hạn mức gọi thử ngay từ nguồn đầu: như trước (429 → báo "tạm")
            break                           # đã có lỗi thật của nguồn trước: giữ nó, đừng che bằng lỗi hạn mức
        if result["ok"]:
            detail = _call_detail(result, st["provider_id"])
            return await boss.record(db, user.org_id, "ai_source", "pass", detail=detail, user_id=user.id)
        if result.get("error_code") in TRANSIENT_CODES:
            return transient("ai_source", str(result["error_code"]), result.get("error"))
        if first_fail is None:
            first_fail = (st, result)
    assert first_fail is not None
    st, result = first_fail
    code = result.get("error_code") or "PROVIDER_ERROR"
    return await boss.record(db, user.org_id, "ai_source", "fail", error_code=code, message=result.get("error"),
                             detail=_call_detail(result, st["provider_id"]), user_id=user.id)


async def _adopt_existing_claude_login(db: AsyncSession, user: service.CurrentUser, result: dict[str, Any]) -> None:
    """Phiên Claude Code có từ TRƯỚC v0.1.39 (tự chuyển khi cập nhật) không đi qua luồng đăng nhập nên không có bản
    `claude_login`; lượt gọi thử vừa ĐẠT chứng minh phiên đang dùng được → ghi `claude_login` 'pass'
    (`login_source: existing_session`) để dòng 4 thành "Xong" mà Sếp không phải đăng nhập lại. Chỉ ghi khi bản
    `claude_login` mới nhất chưa đạt (chưa có, hoặc một lượt đăng nhập lại hỏng trong khi phiên cũ vẫn chạy)."""
    last = (await boss.latest(db, user.org_id)).get("claude_login")
    if last is not None and last["status"] == "pass":
        return
    detail: dict[str, Any] = {"login_source": "existing_session",
                              "account_masked": boss.mask_email(result.get("account"))}
    with contextlib.suppress(OSError):
        detail["credentials_file"] = climod.token_path(climod.CLAUDE).exists()
    await boss.record(db, user.org_id, "claude_login", "pass", detail=detail, user_id=user.id)


async def _run_switch(request: Request, db: AsyncSession, user: service.CurrentUser,
                      profile_id: uuid.UUID) -> dict[str, Any]:
    row = (await db.execute(text("""SELECT c.email, p.kind FROM agent.cli_profiles c
                                    JOIN agent.providers p ON p.id = c.provider_id
                                    WHERE c.id = :i AND c.org_id = :o"""),
                            {"i": profile_id, "o": user.org_id})).one_or_none()
    if row is None:
        raise not_found("Tài khoản Google")
    if row.kind != climod.AGY:
        raise field_errors({"profile_id": "Đây không phải tài khoản Google / Antigravity"})
    if request.app.state.cli_logins.busy(user.org_id, climod.AGY):
        return transient("agy_switch", "CLI_LOGIN_IN_PROGRESS",
                         "Đang đăng nhập thêm một tài khoản — hoàn tất hoặc huỷ bước đó rồi đổi tài khoản")
    from gh.system_api.routes import _probe_budget

    # Đổi tài khoản rồi gọi thử THẬT — kiểm hạn mức trước để không đổi mà không kiểm được.
    await _probe_budget(request.app.state.redis, user.org_id)
    # Hồ sơ đang hoạt động TRƯỚC khi đổi (`from_profile`): "đổi" sang chính nó không phải lần đổi thật (F-76).
    before = (await db.execute(text("""SELECT c.id FROM agent.cli_profiles c
                                       JOIN agent.providers p ON p.id = c.provider_id
                                       WHERE c.org_id = :o AND p.kind = :k AND c.is_active LIMIT 1"""),
                               {"o": user.org_id, "k": climod.AGY})).scalar_one_or_none()
    out = await climod.activate(db, user.org_id, profile_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="cli.account_switched", target_type="cli_profile", target_id=out["id"],
                           target_label=out["email"], detail={"via": "boss_checks"}, ip=user.ip)
    await db.commit()
    p = await _provider(db, user.org_id, climod.AGY)
    result: dict[str, Any] = await request.app.state.model_router.test_provider(p.id)
    expected, actual = out["email"], result.get("account")
    if result["ok"] and not actual:
        # Tệp phiên agy có thể không có id_token → hỏi userinfo (như lúc đăng nhập) trước khi kết luận.
        actual = await _session_account(request)
    # Chỉ kết luận "lệch" khi có ĐỦ hai email và chúng khác nhau; thiếu một bên = không so được (None), không phải lệch.
    match: bool | None = (str(expected).lower() == str(actual).lower()) if expected and actual else None
    detail = {"expected_masked": boss.mask_email(expected), "account_masked": boss.mask_email(actual),
              "account_match": match, "latency_ms": result.get("latency_ms"), "target_profile": str(profile_id),
              "from_profile": str(before) if before else None}
    if not result["ok"]:
        status, code, msg = "fail", result.get("error_code") or "PROVIDER_ERROR", result.get("error")
    elif match is not False:
        status, code, msg = "pass", None, None
    else:
        status, code = "fail", "AGY_ACCOUNT_MISMATCH"
        msg = (f"Đã đổi sang {boss.mask_email(expected) or 'tài khoản này'} nhưng lượt gọi thử vẫn chạy bằng tài khoản "
               "khác — bấm Đăng nhập lại tài khoản này")
    rec = await boss.record(db, user.org_id, "agy_switch", status, error_code=code, message=msg, detail=detail,
                            user_id=user.id)
    rec["account"] = actual
    return rec


async def _session_account(request: Request) -> str | None:
    """Email của tệp phiên agy đang dùng qua `token_identity` (id_token, không có thì userinfo của Google)."""
    raw = None
    with contextlib.suppress(OSError):
        raw = climod.read_session(climod.AGY)
    if not raw:
        return None
    with contextlib.suppress(Exception):
        ident = await climod.token_identity(raw, getattr(request.app.state.cli_logins, "transport", None))
        email = ident.get("email")
        return email if isinstance(email, str) and email else None
    return None


__all__ = ["router"]
