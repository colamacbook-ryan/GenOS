# ruff: noqa: E501
"""Định tuyến lượt gọi model: gán theo vai trò, xoay vòng khoá, hạn mức, ngắt mạch, chuỗi chuyển hướng.

Theo `failoverRules` của thiết kế (ARCHITECTURE §11):
- 429 → khoá đó nghỉ (cooldown), sang khoá kế; hết khoá → nhà cung cấp kế.
- hết hạn mức ngày của model → nhà cung cấp kế tiếp trong chuỗi.
- lỗi tạm thời liên tiếp → ngắt mạch nhà cung cấp 60 giây (giữ nguyên việc, lượt sau thử lại).
- hết chuỗi → ném ModelUnavailable (việc nằm chờ) và báo Sếp qua hàng đợi (tối đa 1 lần / giờ).
- còn < 20% hạn mức ở bất kỳ model nào → cảnh báo (1 lần / ngày / model).
Mọi lượt gọi (thành công hay không) ghi `agent.model_calls`.

v0.1.55 (G1) — thứ tự chuỗi: (1) dòng `agent.bindings` Owner đã đổi (kèm `bindings.effort`) → (2) hồ sơ tiêu chuẩn theo
vai (`gh.defaults.profiles.resolve`, chọn model theo TẦNG trong các nguồn đang bật) → (3) hạng nhà cung cấp như cũ
(`failover_rank`, model mặc định) làm phần đuôi. `generate(tier=…)` ép tầng của hồ sơ (chat chọn Nhanh / Cân bằng / Kỹ
hơn), `effort=…` ép mức suy nghĩ — vẫn tôn trọng luật F-22/F-86 bên dưới.

v0.1.41 (F-86) — việc nền (`BACKGROUND_PURPOSES`: sàng lọc tin, trực việc, Bản tin Gen): Claude Code CLI (gói Pro/Max
cá nhân của Sếp) bị bỏ qua trừ khi Owner cho phép (cảnh báo + xác nhận + PIN, lưu ở
`core.organizations.settings->'ai'->'background_cli'`) — điều khoản gói Claude, QD-12, GIỮ NGUYÊN. Việc nền hết chuỗi
vì chỉ còn Claude Code CLI chưa được phép ⇒ sự cố sức khoẻ `ai.background_no_source` (một chuông), tự đóng khi việc nền
chạy lại được. 'Đọc theo lịch' mạng xã hội (gh.social) không gọi model — không thuộc diện này.

Antigravity CLI (`agy`) — Owner mở cho các vai ngày 16/10/2026 sau khi được báo rủi ro (agy có công cụ đọc tệp / chạy
lệnh: nội dung bên ngoài có thể lừa nó đọc tệp trong container nó chạy). Luật v0.1.38 (F-22) "agy chỉ cho Gen của
Owner" đã gỡ ở v0.1.62; lớp cô lập mỗi lượt của `AgyClient` (cwd rỗng 0700, env sạch, prompt qua stdin, regex tên model)
vẫn giữ.

v0.1.63 (gói H2) — MỖI VAI MỘT CHỖ CHẠY agy (quyết định của Sếp 16/10/2026; luật ở `gh.providers.placement`, MỘT điểm
quyết định `gh.providers.agy_policy.decide` gộp luật đó với trạng thái hộp):
- core (core.gen của Owner, agent nội bộ không gắn kênh / nhóm / người) ⇒ agy TRONG TIẾN TRÌNH (core.briefing qua hộp
  từ review v0.1.63 P2-3 — prompt có tên hiển thị khách)
  (`cli_factory` → `AgyClient`);
- lớp ngoại giao / người ngoài (core.reply, agent ngoại giao, core.refinery sàng lọc tin người lạ, Gen của nhân viên,
  lượt thử agent) ⇒ agy QUA HỘP RIÊNG (`box_factory` → `AgyBoxClient`, container không có khoá / DB / Redis chính);
- hộp chưa cài / tắt ⇒ các vai qua hộp KHÔNG rơi về agy trong tiến trình: Antigravity bị gỡ khỏi chuỗi của lượt đó,
  nguồn AI kế đứng ra, `reasons` ghi câu tiếng Việt trung thực ("Hộp riêng Antigravity chưa chạy / chưa được cài").
Lỗi của hộp (`BoxUnavailable`, `BoxUnauthorized`…) có ngắt mạch RIÊNG và không đánh dấu phiên đăng nhập agy hết hạn —
agy trong tiến trình của Sếp không bị kéo theo. Chỗ duy nhất tạo client agy trong tiến trình cho một lượt gọi model là
`_client(..., where=placement.INPROC)`; `where` bắt buộc truyền (test quét `test_agy_placement_v0163`).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh import crypto
from gh.agybox.client import AgyBoxClient, BoxUnavailable
from gh.config import get_settings
from gh.defaults import profiles
from gh.providers import agy_policy, catalog, placement
from gh.providers.clients import (
    AgyClient,
    AuthFailed,
    BadRequest,
    ClaudeCodeClient,
    Completion,
    GeminiClient,
    Message,
    ModelRejected,
    OpenAICompatClient,
    ProviderError,
    QuotaExhausted,
    RateLimited,
)

log = logging.getLogger("gh.providers")

KEY_COOLDOWN_S = 60
AUTH_COOLDOWN_S = 3600
BREAKER_OPEN_S = 60
BREAKER_FAILS = 3
LOW_QUOTA = 0.20
KEY_AAD = b"provider_key"
CLI_AAD = b"cli_token"
CLI_KINDS = ("antigravity_cli", "claude_code_cli")
PROBE_PROMPT = "Trả lời đúng một chữ: OK"
# Lịch sử: v0.1.38 (F-22) từng giới hạn agy chỉ cho lượt Gen của Owner (`allow_agy`, `AGY_OWNER_ONLY_REASON`,
# `agy_only`…). v0.1.62 gỡ toàn bộ; v0.1.63 (H2) thay bằng chỗ chạy theo vai (xem docstring module).
#: Gói G (P3): trần thời gian MỘT lượt agy của việc nền (giây). Việc nền chạy trong job worker (`gh.worker.JOB_TIMEOUT`
#: = 300s) và gọi agy TUẦN TỰ (lô sàng lọc: mỗi phần lô một lượt, JSON sai thì gọi lại một lượt nữa) — AgyClient mặc
#: định 300s bằng đúng JOB_TIMEOUT nên một lượt treo ăn trọn job và job bị arq giết giữa chừng (bản ghi kẹt "đang xử
#: lý"). 90s × 3 lượt < 300s: lượt quá hạn ném ProviderError sớm, bộ định tuyến chuyển nguồn / trả ModelUnavailable,
#: runner kịp nhả bản ghi và job kết thúc có kiểm soát. Lượt Gen của Sếp (không nền, chạy ở api) giữ 300s.
AGY_BACKGROUND_TIMEOUT_S = 90.0
PROBE_TIMEOUT_S = 90.0
#: Số lượt gọi CLI tối đa của một lần "Kiểm tra kết nối" (kể cả lượt thử lại khi CLI đòi / không nhận mức suy nghĩ).
PROBE_MAX_CALLS = 4
#: Tự lành (v0.1.58): nguồn Antigravity CLI mất dòng model ⇒ lượt gọi tự kiểm tra kết nối — tối đa một lần / 10 phút /
#: tổ chức. Chỉ vai chạy agy trong tiến trình (core.gen của Owner, agent nội bộ) kích hoạt được khi chuỗi rỗng.
AGY_HEAL_KEY = "gh:agy_heal:{}"
AGY_HEAL_TTL = 600
#: Trần thời gian của lần kiểm tra kết nối tự lành (giây): treo quá hạn ⇒ lượt Gen đi tiếp đường cũ.
AGY_HEAL_TIMEOUT = 60.0

# ─── v0.1.41 (F-86): việc nền — Claude Code CLI chỉ khi Owner cho phép ─────────

#: Việc tự động không có Sếp ngồi trước màn hình (khớp gh/refinery/runner.py purpose="refinery",
#: gh/biz/duty/engine.py PURPOSE="duty_decide", gh/gen/briefing.py). So khớp bằng nhau hoặc tiền tố "<p>.".
BACKGROUND_PURPOSES = ("refinery", "duty_decide", "gen.briefing")
#: CLI Owner được phép cho chạy việc nền (QD-12 — điều khoản gói Claude). Antigravity CLI không cần cho phép (v0.1.62:
#: luôn chạy được việc nền) nên không có trong danh sách; giá trị lạ trong cài đặt bị bỏ.
BACKGROUND_CLI_KINDS_ALLOWED = ("claude_code_cli",)
BACKGROUND_CLI_REASON = ("Claude Code CLI (gói Pro/Max của Sếp) mặc định chỉ dùng khi Sếp hỏi Gen trực tiếp — việc nền "
                         "dùng khoá API hoặc Antigravity CLI")
#: Nguồn sự thật DUY NHẤT cho câu cảnh báo trên UI (GET /providers/background → risk_text).
BACKGROUND_CLI_RISK = ("Claude Code CLI dùng gói Claude Pro/Max cá nhân của Sếp. Cho nó chạy việc nền tự động "
                       "(sàng lọc tin, trực việc, bản tin) có thể trái điều khoản gói và tài khoản có thể bị hạn chế "
                       "hoặc khoá. Đây là quyết định và rủi ro của Sếp (QD-12). Cách an toàn: dán khoá API OpenRouter "
                       "hoặc Gemini.")
BACKGROUND_PURPOSE_LABELS = ("Sàng lọc tin", "Trực việc (agent soạn nháp)", "Bản tin Gen")
BG_NO_SOURCE_KEY = "ai.background_no_source"
BG_NO_SOURCE_FLAG = "gh:bg_nosrc:{}"
BG_NO_SOURCE_FLAG_TTL = 30 * 86400
#: Ngưỡng ghi DB của `_background_no_source`: mỗi lượt việc nền hết chuỗi (mỗi lô sàng lọc) KHÔNG mở phiên DB — tối đa
#: một lần / 10 phút / tổ chức (SET NX). Xoá cùng cờ khi sự cố đóng ⇒ lần thiếu nguồn kế tiếp báo ngay.
BG_NO_SOURCE_TRY = "gh:bg_nosrc_try:{}"
BG_NO_SOURCE_TRY_TTL = 600
API_KINDS = ("gemini", "deepseek", "openai_compat")


def is_background(purpose: str) -> bool:
    return any(purpose == p or purpose.startswith(p + ".") for p in BACKGROUND_PURPOSES)


def background_cli_only(reasons: list[str]) -> bool:
    """Việc nền hết chuỗi chỉ vì luật Claude Code CLI (F-86) — chuỗi chỉ còn CLI đó, chưa có khoá API / Antigravity
    CLI dùng được."""
    return BACKGROUND_CLI_REASON in reasons and all(r == BACKGROUND_CLI_REASON for r in reasons)


async def background_cli_allowed(db: AsyncSession, org_id: uuid.UUID) -> set[str]:
    """CLI Owner đã cho chạy việc nền. Thiếu khoá ⇒ rỗng = mặc định chỉ khoá API. Giá trị lạ (kể cả agy) bị bỏ."""
    raw = (await db.execute(text("SELECT settings->'ai'->'background_cli' FROM core.organizations WHERE id = :o"),
                            {"o": org_id})).scalar_one_or_none()
    if not isinstance(raw, list):
        return set()
    return {str(k) for k in raw if k in BACKGROUND_CLI_KINDS_ALLOWED}


async def has_api_source(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """Có ít nhất một nguồn khoá API đang bật, có khoá bật và có model (không phải embedding) bật."""
    return bool((await db.execute(text("""
        SELECT EXISTS (
          SELECT 1 FROM agent.providers p
          WHERE p.org_id = :o AND p.is_enabled AND p.kind = ANY(:k)
            AND EXISTS (SELECT 1 FROM agent.provider_keys k WHERE k.provider_id = p.id AND k.is_enabled)
            AND EXISTS (SELECT 1 FROM agent.models m WHERE m.provider_id = p.id AND m.is_enabled
                        AND m.model_name NOT ILIKE '%embedding%'))"""),
        {"o": org_id, "k": list(API_KINDS)})).scalar_one())


async def has_agy_source(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """Có nguồn Antigravity CLI đang bật, có model (không phải embedding) bật: dùng được cho việc nền mà không cần Owner
    cho phép như Claude Code CLI (vai nào chạy trong tiến trình hay qua hộp: `gh.providers.placement`)."""
    return bool((await db.execute(text("""
        SELECT EXISTS (
          SELECT 1 FROM agent.providers p
          WHERE p.org_id = :o AND p.is_enabled AND p.kind = 'antigravity_cli'
            AND EXISTS (SELECT 1 FROM agent.models m WHERE m.provider_id = p.id AND m.is_enabled
                        AND m.model_name NOT ILIKE '%embedding%'))"""), {"o": org_id})).scalar_one())


async def background_sources(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    """Các nguồn theo thứ tự chuỗi, kèm việc nền có dùng được không và vì sao (mọi giá trị là chuỗi/bool/null — web
    không render object). Jev (`system_one`) và nguồn embedding không sinh văn bản ⇒ bỏ khỏi danh sách."""
    allowed = await background_cli_allowed(db, org_id)
    rows = (await db.execute(text("""
        SELECT p.id, p.name, p.kind, p.is_enabled,
               EXISTS (SELECT 1 FROM agent.provider_keys k WHERE k.provider_id = p.id AND k.is_enabled) AS has_key,
               EXISTS (SELECT 1 FROM agent.models m WHERE m.provider_id = p.id AND m.is_enabled
                       AND m.model_name NOT ILIKE '%embedding%') AS has_model
        FROM agent.providers p WHERE p.org_id = :o AND p.kind NOT IN ('embedding', 'system_one')
        ORDER BY p.failover_rank NULLS LAST, p.created_at"""), {"o": org_id})).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        reason: str | None = None
        if not r.is_enabled:
            reason = "Nguồn đang tắt"
        elif r.kind == "claude_code_cli" and r.kind not in allowed:
            reason = BACKGROUND_CLI_REASON
        elif r.kind not in CLI_KINDS and not r.has_key:
            reason = "Chưa có khoá API"
        elif not r.has_model:
            reason = "Chưa chọn model"
        out.append({"provider_id": str(r.id), "name": str(r.name), "kind": str(r.kind), "used": reason is None,
                    "reason": reason})
    return out


def error_detail(e: BaseException | None) -> str | None:
    """Lỗi gốc (đã che bí mật) cho mục "Chi tiết kỹ thuật" của Console."""
    if e is None:
        return None
    from gh.providers.clients import redact

    raw = getattr(e, "raw", None) or str(e)
    return redact(f"{type(e).__name__}: {raw}", 2000)


def effort_from_rejection(kind: str, e: ModelRejected, *, tried: Collection[str | None] = ()) -> str | None:
    """v0.1.58 — CLI từ chối vì CHƯA gửi mức suy nghĩ (agy: `--model "X" --effort ""` → `Invalid model "X"
    (available: low, medium, high)`): danh sách CLI nêu toàn là mức suy nghĩ hợp lệ của nguồn ⇒ trả mức nên thử
    ('medium' nếu có trong danh sách, không thì mức đầu tiên chưa thử). Không phải trường hợp này ⇒ None."""
    valid = catalog.valid_efforts(kind)
    avail = [a.lower() for a in e.available]
    if not avail or not valid or not set(avail) <= set(valid):
        return None
    rest = [a for a in avail if a not in tried]
    if not rest:
        return None
    return "medium" if "medium" in rest else rest[0]


def friendly_probe_error(e: Exception, model: str, effort: str | None = None) -> str:
    """Câu báo lỗi ngắn cho Console khi gọi thử một model (v0.1.31; v0.1.32: tách model / mức suy nghĩ).

    v0.1.58: không bao giờ in model rỗng (`“”`); CLI nêu danh sách toàn mức suy nghĩ ⇒ câu riêng "cần chọn mức"."""
    if isinstance(e, ModelRejected):
        from gh.providers.catalog import EFFORT_LABEL

        name = f"model “{model}”" if model else "model đã chọn"
        if e.what == "effort" and effort:
            return (f"CLI không nhận mức suy nghĩ “{EFFORT_LABEL.get(effort, effort)}” cho {name} — "
                    "chọn mức khác")
        avail = [a.lower() for a in e.available]
        if avail and all(a in EFFORT_LABEL for a in avail):
            labels = ", ".join(EFFORT_LABEL[a] for a in avail[:8])
            if effort:   # đã gửi mức mà CLI vẫn nêu danh sách mức ⇒ không phải "thiếu mức"
                return f"CLI không nhận {name} với mức “{EFFORT_LABEL.get(effort, effort)}” (CLI nêu: {labels})"
            return f"CLI cần chọn mức suy nghĩ{f' cho model “{model}”' if model else ''} (nhận: {labels})"
        tail = f" (CLI nhận: {', '.join(e.available[:8])})" if e.available else ""
        return f"CLI không nhận {name} — chọn model khác trong danh sách{tail}"
    if isinstance(e, AuthFailed):
        return "Phiên đăng nhập đã hết hiệu lực — bấm “Đăng nhập lại” ở thẻ tài khoản"
    if isinstance(e, RateLimited | QuotaExhausted):
        return "Tài khoản đang hết lượt dùng (giới hạn của gói) — thử lại sau hoặc chọn model nhẹ hơn"
    if isinstance(e, TimeoutError):
        return "Gọi thử quá lâu không có trả lời — thử lại sau"
    return f"Gọi thử chưa được: {str(e)[:160]}"


def probe_error_code(e: BaseException) -> str:
    """Mã lỗi thống nhất của "Gọi thử" (v0.1.39) cho trang "Việc Sếp cần làm"."""
    from gh.gen.jev import JevError

    if isinstance(e, FileNotFoundError) or isinstance(e.__cause__, FileNotFoundError):
        return "CLI_MISSING"   # AgyClient/ClaudeCodeClient bọc "chưa cài CLI" thành AuthFailed
    if isinstance(e, AuthFailed):
        return "AUTH_EXPIRED"
    if isinstance(e, ModelRejected):
        return "MODEL_REJECTED"
    if isinstance(e, TimeoutError | asyncio.TimeoutError):
        return "TIMEOUT"
    if isinstance(e, JevError) or isinstance(e.__cause__, JevError):
        return "JEV_ERROR"
    return "PROVIDER_ERROR"


async def adopt_probed_model(db: AsyncSession, org_id: uuid.UUID, provider_id: uuid.UUID, provider_name: str,
                             result: dict[str, Any], *, actor_type: str = "system", actor_id: str = "system:router",
                             ip: str | None = None) -> bool:
    """v0.1.58 — nguồn CLI gọi thử THẬT thành công mà CHƯA có dòng model nào ⇒ lưu cặp (model, mức) vừa chạy được.

    Chỉ khi `result.ok` + có `probe_model` + nguồn có 0 dòng `agent.models` (không đụng lựa chọn của Sếp). KHÔNG đặt
    `is_default` (Sếp chưa chốt — hồ sơ tiêu chuẩn tự phủ). Ghi Action Log `provider.model_auto`. Trả True khi thêm.

    v0.1.66 (F132): với Antigravity CLI, lưu SẴN cả các model mà `agy models` liệt kê thật (`source == "cli"` trong
    `model_groups` — mục dự phòng chưa xác minh KHÔNG thêm) để các tầng Cân bằng / Kỹ hơn của khung Gen có nguồn phục vụ
    ngay sau Kiểm tra xanh. Model thăm dò đứng đầu (giữ mức đã chạy được). Chỉ làm lần đầu (nguồn 0 model): Sếp xoá
    bớt model rồi Kiểm tra lại thì KHÔNG bị thêm lại."""
    from gh.chassis import actionlog

    model = str(result.get("probe_model") or "").strip()
    if not (result.get("ok") and model):
        return False
    n = (await db.execute(text("SELECT count(*) FROM agent.models WHERE provider_id = :p"),
                          {"p": provider_id})).scalar_one()
    if n:
        return False
    effort = result.get("probe_effort")
    effort = effort if isinstance(effort, str) and effort in catalog.EFFORT_LABEL else None
    kind = (await db.execute(text("SELECT kind FROM agent.providers WHERE id = :p"), {"p": provider_id})).scalar_one()
    wanted: list[tuple[str, str | None]] = [(model, effort)]
    if kind == "antigravity_cli":
        for g in result.get("model_groups") or []:
            for m in (g.get("models") or []) if isinstance(g, dict) else []:
                mid = str(m.get("id") or "").strip() if isinstance(m, dict) else ""
                if not mid or m.get("source") != "cli" or any(mid == w[0] for w in wanted):
                    continue
                eff = m.get("default_effort")
                wanted.append((mid, eff if isinstance(eff, str) and eff in catalog.EFFORT_LABEL else None))
    added_models: list[str] = []
    for i, (name, eff) in enumerate(wanted):
        if i:
            # `agent.models.id` = core.uuid_v7(): hai dòng chèn cùng một mili-giây có thứ tự id NGẪU NHIÊN, mà chuỗi /
            # hồ sơ chọn model theo thứ tự id ⇒ chờ để model thăm dò (chèn trước) luôn đứng trước.
            await db.execute(text("SELECT pg_sleep(0.003)"))
        row = (await db.execute(text("""INSERT INTO agent.models (provider_id, model_name, effort)
                                        VALUES (:p, :m, :e) ON CONFLICT (provider_id, model_name) DO NOTHING
                                        RETURNING id"""),
                                {"p": provider_id, "m": name, "e": eff})).first()
        if row is not None:
            added_models.append(name)
    if not added_models:
        return False
    await actionlog.record(db, org_id=org_id, actor_type=actor_type, actor_id=actor_id, action="provider.model_auto",
                           target_type="provider", target_id=str(provider_id), target_label=provider_name,
                           detail={"model_name": model, "effort": effort, "make_default": False,
                                   "models": added_models}, ip=ip)
    return True


#: Lý do một nguồn khoá API bị bỏ qua vì không còn khoá nào dùng được (đã xoá / tắt / chưa giải mã được). Gen đọc nó
#: để nói "thêm khoá" thay vì "thử lại sau" (v0.1.66, F010).
REASON_NO_KEY = "không còn khoá khả dụng"


class ModelUnavailable(Exception):  # noqa: N818
    def __init__(self, reasons: list[str], *, no_chain: bool | None = None):
        super().__init__("; ".join(reasons) or "Chưa cấu hình model")
        self.reasons = reasons
        # v0.1.28 (UX C1): True = chưa có nguồn/model nào trong chuỗi; False = có model nhưng lượt gọi đều lỗi
        # (mạng, hạn mức…) — hai tình huống cần câu trả lời khác nhau cho người dùng. None = không rõ (coi như chưa có).
        self.no_chain = no_chain


@dataclass
class Routed:
    text: str
    provider: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    attempts: list[str] = field(default_factory=list)


def _today() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def quota_key(model_id: Any) -> str:
    return f"gh:quota:{model_id}:{_today()}"


def cooldown_key(key_id: Any) -> str:
    return f"gh:cooldown:key:{key_id}"


def breaker_key(provider_id: Any, where: str = placement.INPROC) -> str:
    """Ngắt mạch của nhà cung cấp; agy qua hộp (`box`) có khoá RIÊNG, hộp lỗi không ngắt agy chạy trong tiến trình."""
    return f"gh:breaker:provider:{provider_id}" + (":box" if where == placement.BOX else "")


def fail_key(provider_id: Any, where: str = placement.INPROC) -> str:
    return f"gh:pfail:{provider_id}" + (":box" if where == placement.BOX else "")


async def owner_user_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID | None:
    return (await db.execute(text("""
        SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
        JOIN core.roles r ON r.id = ur.role_id AND r.code = 'owner'
        WHERE u.org_id = :o AND u.is_active ORDER BY u.created_at LIMIT 1"""), {"o": org_id})).scalar_one_or_none()


async def raise_alert(db: AsyncSession, org_id: uuid.UUID, *, alert_type: str, priority: str, title: str,
                      summary: str, suggested: str | None = None, subject_type: str | None = None,
                      subject_id: Any = None, evidence: list[dict[str, Any]] | None = None,
                      personnel_related: bool = False, redis: Any = None) -> None:
    """Ghi `biz.alerts`. Cảnh báo P1 (phàn nàn nặng, cơ hội nóng chưa ai nhận, hết chuỗi model…) còn rung CHUÔNG của mọi
    Owner (v0.1.71): trước đây P1 chỉ nằm trong Hộp thư nên Sếp không ở màn đó sẽ không biết. Chuông chỉ mang tiêu đề
    và việc cần làm, không mang nội dung tin của khách; `redis` có thì đẩy WebSocket ngay sau khi commit."""
    import orjson

    from gh import notifications

    code = (await db.execute(text("SELECT core.next_code('ALR')"))).scalar_one()
    await db.execute(text("""
        INSERT INTO biz.alerts (org_id, code, alert_type, priority, recipient_user_id, subject_type, subject_id,
                                title, summary, suggested_action, evidence, personnel_related)
        VALUES (:o, :c, :t, :p, :u, :st, :si, :ti, :su, :sa, CAST(:ev AS jsonb), :pr)"""),
        {"o": org_id, "c": code, "t": alert_type, "p": priority, "u": await owner_user_id(db, org_id),
         "st": subject_type, "si": subject_id, "ti": title, "su": summary, "sa": suggested,
         "ev": orjson.dumps(evidence or []).decode(), "pr": personnel_related})
    if priority == "P1":
        await notifications.notify(db, org_id, await notifications.owner_ids(db, org_id), kind="alert.p1",
                                   title=f"Cảnh báo khẩn: {title}",
                                   body=(suggested or "Mở Hộp thư để xem và xử lý.")[:200],
                                   link="/inbox?tab=alert", redis=redis)


class ModelRouter:
    def __init__(self, sm: async_sessionmaker[AsyncSession], redis: Redis, *,
                 transport: httpx.AsyncBaseTransport | None = None, cli_factory: Any = None,
                 claude_factory: Any = None, box_factory: Any = None):
        self.sm, self.redis, self.transport = sm, redis, transport
        s = get_settings()
        #: agy TRONG tiến trình — chỉ cho vai `placement.INPROC` và việc của Sếp (đăng nhập, gọi thử, chẩn đoán).
        self.cli_factory = cli_factory or (lambda: AgyClient(s.cli_binary, s.cli_home))
        #: agy QUA HỘP RIÊNG (`AgyBoxClient`, v0.1.63 H1); trả None khi hộp chưa được cài.
        self.box_factory = box_factory or (lambda: AgyBoxClient.from_env(cli_home=get_settings().cli_home))
        self.claude_factory = claude_factory or (lambda: ClaudeCodeClient(get_settings().claude_binary,
                                                                          get_settings().claude_home))

    # ─── chuỗi ───────────────────────────────────────────────────────────────

    async def _chain(self, db: AsyncSession, org_id: uuid.UUID, agent_key: str, *,
                     tier: str | None = None, agy: agy_policy.AgyVerdict | None = None,
                     owner: bool = True, purpose: str | None = None) -> list[dict[str, Any]]:
        """Chuỗi nguồn + model cho `agent_key` (v0.1.55: binding → hồ sơ tiêu chuẩn → hạng nhà cung cấp):

        1. dòng `agent.bindings` Owner đã đổi — luôn đứng đầu (kèm `bindings.effort`); lượt gọi ép `tier` (chat chọn
           tầng) thì dòng gán xuống sau các nguồn đã chọn theo tầng;
        2. mỗi nguồn đang bật theo `failover_rank` với model do HỒ SƠ TIÊU CHUẨN chọn theo tầng của vai
           (`profiles.resolve` — cũng là hàm quyết định "Chuẩn: …" Sếp thấy); nguồn Owner đã chốt model ("Dùng model
           này" = `is_default`) giữ model đó, trừ khi lượt gọi ép tầng;
        3. phần đuôi như cũ: model mặc định theo hạng của các nguồn còn lại/chưa dùng (để lỗi tầng đã chọn vẫn còn đường
           lui).
        `agy` (quyết định `agy_policy.decide` của khoá này; bỏ trống ⇒ tự tra theo `owner` / `purpose`) không cho phép
        (vai phải qua hộp mà hộp chưa chạy / chưa cài) ⇒ nguồn Antigravity CLI bị gỡ KHỎI chuỗi (cả dòng gán agy cũ, cả
        phần đuôi) — không bao giờ để agy chạy trong tiến trình thay cho hộp.
        Luật F-86 KHÔNG lọc ở đây: `generate` bỏ qua Claude Code CLI khi việc nền chưa được phép và ghi LÝ DO vào
        `reasons`/hết chuỗi.

        Mỗi phần tử: {"provider", "model", "keys", "source": binding|default|profile|rank, "binding_effort"}."""
        bound = (await db.execute(text("""SELECT b.model_id, b.effort FROM agent.bindings b
                                          WHERE b.org_id = :o AND b.agent_key = :k"""),
                                  {"o": org_id, "k": agent_key})).one_or_none()
        providers = (await db.execute(text("""
            SELECT id, kind, name, endpoint, auth_state, failover_rank FROM agent.providers
            WHERE org_id = :o AND is_enabled AND kind NOT IN ('embedding', 'system_one')
            ORDER BY failover_rank NULLS LAST, created_at"""), {"o": org_id})).all()
        verdict = agy if agy is not None else await agy_policy.verdict_for(db, org_id, agent_key, owner=owner,
                                                                           purpose=purpose)
        if not verdict.allowed:
            providers = [p for p in providers if p.kind != agy_policy.KIND_AGY]
        models = (await db.execute(text("""
            SELECT m.id, m.provider_id, m.model_name, m.effort, m.daily_quota, m.rate_limit_per_min, m.is_default,
                   m.is_enabled
            FROM agent.models m JOIN agent.providers p ON p.id = m.provider_id
            WHERE p.org_id = :o AND m.is_enabled AND m.model_name NOT ILIKE '%embedding%'
            ORDER BY m.is_default DESC, m.id"""), {"o": org_id})).all()
        by_provider: dict[Any, list[Any]] = {}
        for m in models:
            by_provider.setdefault(m.provider_id, []).append(m)
        prov_by_id = {p.id: p for p in providers}
        model_by_id = {m.id: m for m in models}
        forced = profiles.normalize_tier(tier)
        # Chọn model theo hồ sơ cho MỌI nguồn (luật F-86 của Claude Code CLI do `generate` áp — nên cho hồ sơ xét cả CLI
        # ở đây).
        picked = {c["provider_id"]: c for c in profiles.resolve(
            providers, models, agent_key, background=False, bg_cli_allowed={"claude_code_cli"}, tier_override=tier,
            agy=verdict)}
        keys_cache: dict[Any, list[Any]] = {}

        async def keys_of(pid: Any) -> list[Any]:
            if pid not in keys_cache:
                keys_cache[pid] = list((await db.execute(text("""
                    SELECT id, label, secret_enc FROM agent.provider_keys
                    WHERE provider_id = :p AND is_enabled ORDER BY rotation_order, created_at"""),
                                                          {"p": pid})).all())
            return keys_cache[pid]

        seen: set[tuple[Any, Any]] = set()

        async def link(p: Any, m: Any, source: str, binding_effort: str | None = None) -> dict[str, Any] | None:
            if (p.id, m.id) in seen:
                return None
            seen.add((p.id, m.id))
            return {"provider": p, "model": m, "keys": await keys_of(p.id), "source": source,
                    "binding_effort": binding_effort}

        bound_link = None
        if bound is not None and bound.model_id in model_by_id:
            bm = model_by_id[bound.model_id]
            if bm.provider_id in prov_by_id:
                bound_link = await link(prov_by_id[bm.provider_id], bm, "binding", bound.effort)
        chain: list[dict[str, Any]] = [bound_link] if bound_link is not None and not forced else []
        for p in providers:
            ms = by_provider.get(p.id)
            if not ms:
                continue
            if p.id in picked:
                # `profiles.resolve` là MỘT bộ chọn cho cả "Chuẩn: …" hiển thị lẫn chuỗi chạy: nguồn Owner đã chốt model
                # giữ model đó (source "default"), còn lại chọn theo tầng (source "profile").
                lk = await link(p, model_by_id[picked[p.id]["model_id"]], picked[p.id]["source"])
            elif ms[0].is_default and not forced:
                lk = await link(p, ms[0], "default")          # nguồn hồ sơ không phủ (vd khoá API chưa có khoá)
            else:
                lk = await link(p, ms[0], "rank")
            if lk is not None:
                chain.append(lk)
        if bound_link is not None and forced:
            chain.append(bound_link)
        for p in providers:           # phần đuôi: model mặc định theo hạng của nguồn chưa có đường lui
            ms = by_provider.get(p.id)
            lk = await link(p, ms[0], "rank") if ms else None
            if lk is not None:
                chain.append(lk)
        return chain

    def _client(self, p: Any, secret: str | None, *, where: str, background: bool = False) -> Any:
        """Client cho một nguồn. `where` (BẮT BUỘC, `placement.INPROC` | `placement.BOX`) chỉ có nghĩa với Antigravity
        CLI: BOX ⇒ `AgyBoxClient`, không bao giờ rơi về `AgyClient` trong tiến trình
        (hộp chưa cài ⇒ `BoxUnavailable`)."""
        if p.kind == "antigravity_cli":
            if where == placement.BOX:
                client = self.box_factory()
                if client is None:
                    raise BoxUnavailable("Hộp riêng Antigravity chưa được cài")
            elif where == placement.INPROC:
                client = self.cli_factory()
            else:
                raise ValueError(f"Chỗ chạy agy không hợp lệ: {where!r}")
            if background and hasattr(client, "timeout"):
                # Gói G (P3): việc nền chạy trong job worker có JOB_TIMEOUT 300s — một lượt agy treo 300s ăn trọn job.
                client.timeout = min(float(client.timeout), AGY_BACKGROUND_TIMEOUT_S)
            return client
        if p.kind == "claude_code_cli":
            return self.claude_factory()
        if p.kind == "gemini":
            return GeminiClient(p.endpoint, secret or "", transport=self.transport)
        return OpenAICompatClient(p.endpoint or ("https://api.deepseek.com/v1" if p.kind == "deepseek" else None),
                                  secret or "", transport=self.transport)

    async def _record(self, org_id: uuid.UUID, model_id: Any, key_id: Any, agent_key: str, purpose: str,
                      status: str, started: float, c: Completion | None) -> None:
        async with self.sm() as db:
            await db.execute(text("""
                INSERT INTO agent.model_calls (org_id, model_id, key_id, agent_key, purpose, tokens_in, tokens_out,
                                               latency_ms, status)
                VALUES (:o, :m, :k, :a, :p, :ti, :to, :l, :s)"""),
                {"o": org_id, "m": model_id, "k": key_id, "a": agent_key, "p": purpose,
                 "ti": c.tokens_in if c else None, "to": c.tokens_out if c else None,
                 "l": int((time.monotonic() - started) * 1000), "s": status})
            await db.commit()

    async def _quota_ok(self, org_id: uuid.UUID, m: Any) -> bool:
        if m.daily_quota is None:
            return True
        used = int(await self.redis.get(quota_key(m.id)) or 0)
        return used < int(m.daily_quota)

    async def _count_use(self, org_id: uuid.UUID, p: Any, m: Any) -> None:
        k = quota_key(m.id)
        used = await self.redis.incr(k)
        if used == 1:
            await self.redis.expire(k, 2 * 86400)
        if m.daily_quota and used >= (1 - LOW_QUOTA) * int(m.daily_quota):
            if await self.redis.set(f"gh:alert:quota_low:{m.id}:{_today()}", "1", nx=True, ex=86400):
                left = max(0, int(m.daily_quota) - used)
                async with self.sm() as db:
                    await raise_alert(db, org_id, alert_type="model_quota_low", priority="P2",
                                      title=f"{m.model_name} còn dưới 20% hạn mức ngày",
                                      summary=f"{p.name} · còn {left} / {m.daily_quota} lượt hôm nay",
                                      suggested="Thêm khoá hoặc đổi thứ tự chuỗi chuyển hướng",
                                      subject_type="model", subject_id=m.id)
                    await db.commit()

    async def _fail(self, p: Any, where: str = placement.INPROC) -> None:
        k = fail_key(p.id, where)
        n = await self.redis.incr(k)
        await self.redis.expire(k, 300)
        if n >= BREAKER_FAILS:
            await self.redis.set(breaker_key(p.id, where), "open", ex=BREAKER_OPEN_S)
            await self.redis.delete(k)

    async def _set_auth_state(self, p: Any, state: str) -> None:
        """Đổi `auth_state` khi khác giá trị cũ. v0.1.36 (F-6b): sang 'expired' ⇒ mở sự cố + chuông MỘT lần
        (gh.health.raise_once — model 401 liên tục không dội chuông); về 'ok' ⇒ đóng sự cố. Cùng transaction, nhưng
        phần sự cố/chuông chạy trong savepoint riêng: lỗi ở đó chỉ ghi log — định tuyến model (chuyển sang nhà cung
        cấp kế tiếp, hoặc kết quả đã gọi xong) không bao giờ hỏng vì một cái chuông;
        `_eval_models` mở bù sau ≤60 giây."""
        from gh import health, notifications

        async with self.sm() as db:
            row = (await db.execute(text("""UPDATE agent.providers SET auth_state = :s
                                            WHERE id = :i AND auth_state <> :s RETURNING org_id, name"""),
                                    {"s": state, "i": p.id})).one_or_none()
            if row is not None and state in ("expired", "ok"):
                mark = notifications.pending_mark(db)
                try:
                    async with db.begin_nested():
                        if state == "expired":
                            await health.raise_model_expired(db, row.org_id, p.id, row.name, redis=self.redis)
                        else:
                            await health.clear(db, row.org_id, f"model.auth_expired:{p.id}", redis=self.redis)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — chuông lỗi không được làm hỏng lượt gọi model
                    notifications.pending_reset(db, mark)
                    log.warning("Không cập nhật được sự cố model %s (%s)", p.id, state, exc_info=True)
            await db.commit()

    # ─── gọi ─────────────────────────────────────────────────────────────────

    async def generate(self, org_id: uuid.UUID, *, agent_key: str, purpose: str, messages: list[Message],
                       json_mode: bool = True, temperature: float = 0.2,
                       tier: str | None = None, effort: str | None = None, owner: bool = True) -> Routed:
        """`owner` = người hỏi là Owner (mặc định True: việc nền / hệ thống không có người hỏi). Chỉ `core.gen` phân
        biệt. v0.1.63 (H2): chỗ chạy Antigravity CLI theo VAI — Gen của Owner, agent nội bộ chạy agy trong tiến
        trình; Gen của nhân viên (`owner=False`), `core.reply`, `core.refinery`, `core.briefing`, agent ngoại giao và
        lượt thử agent
        (`purpose="setup_agent_try"`) chạy agy QUA HỘP RIÊNG (`box_factory`). Hộp chưa cài / tắt ⇒ các lượt đó bỏ
        Antigravity (kể cả dòng gán agy cũ), dùng nguồn AI kế và ghi lý do vào `reasons` — KHÔNG BAO GIỜ chạy agy trong
        tiến trình thay cho hộp (`agy_policy.decide`, luật ở `gh.providers.placement`). Xem docstring module.
        Soạn lại / Dịch nháp (`draft_*`) luôn qua hộp bất kể khoá; thử quy tắc (`purpose="rule_test"`) KHÔNG dùng agy
        ở đâu cả — văn bản tuỳ ý của vai chỉ đọc (review F2, v0.1.62).

        v0.1.41 (F-86): việc nền (`is_background(purpose)`) bỏ qua Claude Code CLI trừ khi Owner đã cho phép
        (`background_cli_allowed`) — điều khoản gói Claude, GIỮ NGUYÊN.

        v0.1.55 (G1): `tier` ('fast' | 'balanced' | 'strong'; chat gọi 'deep' = 'strong') ép tầng model của hồ sơ
        tiêu chuẩn cho lượt này — vẫn tôn trọng F-86 (Claude Code CLI chỉ khi được phép ở việc nền). `effort` ép mức
        suy nghĩ; ưu tiên: tham số → `bindings.effort` → hồ sơ → `models.effort`, chỉ gửi khi model hỗ trợ mức đó. Thiếu
        nguồn cho việc nền ⇒ giữ hành vi cũ (hết chuỗi → ModelUnavailable / bản tin để trống), KHÔNG tự chuyển sang
        Claude Code CLI."""
        background = is_background(purpose)
        reasons: list[str] = []
        async with self.sm() as db:
            verdict = await agy_policy.verdict_for(db, org_id, agent_key, owner=owner, purpose=purpose)
            chain = await self._chain(db, org_id, agent_key, tier=tier, agy=verdict)
            bg_cli = await background_cli_allowed(db, org_id) if background else set()
            if not verdict.allowed and verdict.reason and await self._agy_enabled(db, org_id):
                reasons.append(verdict.reason)          # Antigravity có trong cấu hình nhưng khoá này chưa dùng được
        where = verdict.where                              # chỗ chạy agy của lượt này: "inproc" | "box"
        if not chain and verdict.allowed and where == placement.INPROC and await self._heal_agy(org_id):
            async with self.sm() as db:
                chain = await self._chain(db, org_id, agent_key, tier=tier, agy=verdict)
        # v0.1.55 (F-R3): nguồn đã lỗi Ở CẤP NGUỒN (mạng / 5xx / quá giờ, hoặc CLI hết phiên đăng nhập) trong lượt này
        # ⇒ bỏ mọi mắt xích sau của CÙNG nguồn (phần đuôi `_chain` có thể trỏ model khác của nguồn đó): gọi lại chỉ
        # nhân đôi thời gian chờ, dòng `model_calls` lỗi và số đếm ngắt mạch. Lỗi cấp MODEL (BadRequest/ModelRejected,
        # hết hạn mức một model) thì đuôi vẫn là đường lui hợp lệ.
        provider_down: set[Any] = set()
        for link in chain:
            p, m = link["provider"], link["model"]
            via = where if p.kind == agy_policy.KIND_AGY else placement.INPROC   # chỗ chạy áp cho nguồn này
            down_id = (p.id, via)
            if down_id in provider_down:
                reason = f"{p.name}: đã lỗi ở lượt này — không thử lại model khác của cùng nguồn"
                if reason not in reasons:
                    reasons.append(reason)
                continue
            if background and p.kind == "claude_code_cli" and p.kind not in bg_cli:
                if BACKGROUND_CLI_REASON not in reasons:
                    reasons.append(BACKGROUND_CLI_REASON)
                continue
            if await self.redis.exists(breaker_key(p.id, via)):
                reasons.append(f"{p.name}: đang ngắt mạch")
                continue
            if not await self._quota_ok(org_id, m):
                reasons.append(f"{p.name}: {m.model_name} hết hạn mức ngày")
                continue
            if m.rate_limit_per_min:
                rk = f"gh:rate:{m.id}:{int(time.time() // 60)}"
                n = await self.redis.incr(rk)
                await self.redis.expire(rk, 120)
                if n > int(m.rate_limit_per_min):
                    reasons.append(f"{p.name}: vượt {m.rate_limit_per_min} lượt/phút")
                    continue
            slots: list[tuple[Any, str | None]] = [(None, None)] if p.kind in CLI_KINDS else []
            for k in link["keys"]:
                if await self.redis.exists(cooldown_key(k.id)):
                    continue
                try:
                    slots.append((k, crypto.decrypt(bytes(k.secret_enc), KEY_AAD).decode()))
                except Exception:  # noqa: BLE001 — khoá master đổi / dữ liệu hỏng: bỏ khoá này
                    log.error("Không giải mã được khoá %s", k.label)
            if not slots:
                reasons.append(f"{p.name}: {REASON_NO_KEY}")
                continue
            for key, secret in slots:
                started = time.monotonic()
                kid = key.id if key else None
                try:
                    eff, implicit = self._effort(agent_key, p, m, link, effort)
                    c = await self._complete(p, secret, m, messages, json_mode=json_mode, temperature=temperature,
                                             effort=eff, implicit=implicit, background=background, where=via)
                except RateLimited as e:
                    await self._record(org_id, m.id, kid, agent_key, purpose, "rate_limited", started, None)
                    if key is not None:
                        await self.redis.set(cooldown_key(key.id), "429", ex=int(e.retry_after or KEY_COOLDOWN_S))
                    reasons.append(f"{p.name}{' ' + key.label if key else ''}: 429")
                    continue
                except QuotaExhausted:
                    await self._record(org_id, m.id, kid, agent_key, purpose, "quota", started, None)
                    await self.redis.set(quota_key(m.id), str(m.daily_quota or 10**9), ex=86400)
                    reasons.append(f"{p.name}: nhà cung cấp báo hết hạn mức")
                    break
                except AuthFailed as e:
                    await self._record(org_id, m.id, kid, agent_key, purpose, "auth", started, None)
                    if key is not None:
                        await self.redis.set(cooldown_key(key.id), "auth", ex=AUTH_COOLDOWN_S)
                    else:
                        # phiên đăng nhập agy trong HỘP là chuyện của hộp, không đổi nhãn nguồn
                        if via == placement.INPROC:
                            await self._set_auth_state(p, "expired")
                        provider_down.add(down_id)        # CLI hết phiên: model khác của cùng CLI cũng hỏng
                    reasons.append(f"{p.name}: xác thực lỗi ({str(e)[:80]})")
                    continue
                except BadRequest as e:
                    await self._record(org_id, m.id, kid, agent_key, purpose, "error", started, None)
                    reasons.append(f"{p.name}: {str(e)[:120]}")
                    break
                except (ProviderError, TimeoutError) as e:
                    await self._record(org_id, m.id, kid, agent_key, purpose, "error", started, None)
                    await self._fail(p, via)
                    provider_down.add(down_id)
                    reasons.append(f"{p.name}: {str(e)[:120]}")
                    break
                if not str(c.text or "").strip():
                    # v0.1.74: nguồn báo thành công nhưng chữ RỖNG (Antigravity CLI từng trả 0 ký tự dù tốn ~3000 token) ⇒ coi
                    # là lỗi nhà cung cấp, thử model / nguồn kế tiếp trong chuỗi thay vì đưa chữ rỗng cho bên gọi.
                    await self._record(org_id, m.id, kid, agent_key, purpose, "error", started, None)
                    reasons.append(f"{p.name}: {m.model_name} trả về chữ rỗng")
                    break
                await self._record(org_id, m.id, kid, agent_key, purpose, "ok", started, c)
                await self.redis.delete(fail_key(p.id, via))
                if key is None and p.auth_state != "ok" and via == placement.INPROC:
                    # CLI vừa gọi được thật (token đã tự làm mới) → bỏ nhãn "Hết hạn" cũ (một sự thật, v0.1.31).
                    await self._set_auth_state(p, "ok")
                await self._count_use(org_id, p, m)
                if background:
                    await self._background_ok(org_id)
                return Routed(c.text, p.name, m.model_name, c.tokens_in, c.tokens_out, reasons)
        await self._chain_exhausted(org_id, reasons, purpose=purpose)
        raise ModelUnavailable(reasons, no_chain=not chain)

    @staticmethod
    async def _agy_enabled(db: AsyncSession, org_id: uuid.UUID) -> bool:
        return bool((await db.execute(text("""SELECT EXISTS (SELECT 1 FROM agent.providers
                                               WHERE org_id = :o AND is_enabled AND kind = 'antigravity_cli')"""),
                                      {"o": org_id})).scalar_one())

    async def _heal_agy(self, org_id: uuid.UUID) -> bool:
        """v0.1.58 — tự lành khi chuỗi rỗng; chỉ cho vai chạy agy TRONG TIẾN TRÌNH (v0.1.63: vai qua hộp không kích hoạt
        gọi thử trong tiến trình). Nguồn Antigravity CLI
        đang bật, có hồ sơ CLI đang dùng mà 0 dòng model (nên chuỗi không có agy) ⇒ "Kiểm tra kết nối" đúng MỘT lần
        (khoá Redis 10 phút) rồi lưu model vừa chạy được.
        Trả True khi đã thêm model (bên gọi dựng lại chuỗi). Lỗi chỉ ghi log — lượt gọi đi tiếp như cũ."""
        try:
            async with self.sm() as db:
                row = (await db.execute(text("""
                    SELECT p.id, p.name FROM agent.providers p
                    WHERE p.org_id = :o AND p.kind = 'antigravity_cli' AND p.is_enabled
                      AND EXISTS (SELECT 1 FROM agent.cli_profiles c WHERE c.provider_id = p.id AND c.is_active)
                      AND NOT EXISTS (SELECT 1 FROM agent.models m WHERE m.provider_id = p.id)
                    ORDER BY p.failover_rank NULLS LAST, p.created_at LIMIT 1"""), {"o": org_id})).one_or_none()
            if row is None or not await self.redis.set(AGY_HEAL_KEY.format(org_id), "1", nx=True, ex=AGY_HEAL_TTL):
                return False
            result = await asyncio.wait_for(self.test_provider(row.id), AGY_HEAL_TIMEOUT)
            if not result.get("ok"):
                return False
            async with self.sm() as db:
                added = await adopt_probed_model(db, org_id, row.id, row.name, result)
                await db.commit()
            if added:
                # v0.1.59: vừa có dòng model ⇒ thẻ "Hôm nay của Sếp" đọc lại ngay (xoá cache 60 giây).
                from gh.gen.coach import cache as coach_cache

                await coach_cache.invalidate(self.redis, org_id)
            return added
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — tự lành là phần thưởng thêm; lỗi không được làm hỏng lượt Gen
            log.warning("Tự lành nguồn Antigravity CLI không xong (%s)", org_id, exc_info=True)
            return False

    @staticmethod
    def _effort(agent_key: str, p: Any, m: Any, link: dict[str, Any], param: str | None) -> tuple[str | None, bool]:
        """(mức suy nghĩ gửi cho CLI, do hồ sơ tự thêm?). Ưu tiên: tham số → `bindings.effort` (chỉ khi model do dòng
        gán chọn) → hồ sơ (chỉ khi model do hồ sơ chọn) → `models.effort`; lấy mức ĐẦU TIÊN model hỗ trợ
        (`profiles.effort_for`). Nguồn khoá API không có mức suy nghĩ ⇒ None. Model Owner đã chốt (dòng gán, "Dùng model
        này") giữ nguyên mức Owner đã đặt — hồ sơ không ghi đè lựa chọn của Sếp."""
        if p.kind not in CLI_KINDS:
            return None, False
        src = link.get("source")
        layers: list[tuple[str, str | None]] = [("param", param)]
        if src == "binding":
            layers.append(("binding", link.get("binding_effort")))
        if src == "profile":
            layers.append(("profile", profiles.profile_effort(agent_key, p.kind, m.model_name)))
        layers.append(("model", m.effort))
        for name, cand in layers:
            eff = profiles.effort_for(p.kind, m.model_name, cand)
            if eff:
                return eff, name == "profile"
        return None, False

    async def _complete(self, p: Any, secret: str | None, m: Any, messages: list[Message], *, json_mode: bool,
                        temperature: float, effort: str | None, implicit: bool, where: str,
                        background: bool = False) -> Completion:
        """Một lượt gọi model. Mức suy nghĩ do HỒ SƠ tự thêm (`implicit`) mà CLI không nhận cho model này thì gọi lại
        một lần KHÔNG gửi mức — hồ sơ tiêu chuẩn không bao giờ làm hỏng lượt gọi chỉ vì mức suy nghĩ. v0.1.58: ngược
        lại, CHƯA gửi mức mà CLI đòi mức (agy: `available: low, medium, high`) thì gọi lại một lần với mức hợp lệ."""
        client = self._client(p, secret, where=where, background=background)
        extra = {"effort": effort} if effort else {}
        try:
            c: Completion = await client.generate(m.model_name, messages, json_mode=json_mode,
                                                  temperature=temperature, **extra)
            return c
        except ModelRejected as e:
            if implicit and effort and e.what == "effort":
                log.warning("CLI không nhận mức %s cho %s — gọi lại không gửi mức", effort, m.model_name)
                c = await client.generate(m.model_name, messages, json_mode=json_mode, temperature=temperature)
                return c
            need = effort_from_rejection(p.kind, e) if not effort else None
            if need is None:
                raise
            log.warning("CLI đòi mức suy nghĩ cho %s — gọi lại với mức %s", m.model_name, need)
            c = await client.generate(m.model_name, messages, json_mode=json_mode, temperature=temperature,
                                      effort=need)
            return c

    async def _background_no_source(self, org_id: uuid.UUID) -> None:
        """v0.1.41 (F-86): việc nền chỉ còn Claude Code CLI chưa được phép ⇒ sự cố `ai.background_no_source` + MỘT
        chuông (raise_once không dội chuông lần hai khi sự cố đang mở). Savepoint riêng như `_set_auth_state`: lỗi
        chuông chỉ ghi log."""
        from gh import health, notifications

        async with self.sm() as db:
            mark = notifications.pending_mark(db)
            try:
                async with db.begin_nested():
                    await health.raise_once(
                        db, org_id, key=BG_NO_SOURCE_KEY, kind=BG_NO_SOURCE_KEY, severity="warn",
                        title="Việc nền (sàng lọc, trực việc, bản tin) chưa có nguồn AI",
                        body=("Dán khoá OpenRouter hoặc Gemini ở API & Model (Thêm nhà cung cấp), đăng nhập "
                              "Antigravity CLI, hoặc cho phép Claude Code CLI chạy việc nền ở Bộ não AI (có cảnh báo "
                              "điều khoản)."),
                        link="/system?tab=brain", redis=self.redis)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — chuông lỗi không được làm hỏng định tuyến
                notifications.pending_reset(db, mark)
                log.warning("Không mở được sự cố việc nền thiếu khoá API (%s)", org_id, exc_info=True)
            await db.commit()
        await self.redis.set(BG_NO_SOURCE_FLAG.format(org_id), "1", ex=BG_NO_SOURCE_FLAG_TTL)

    async def _background_ok(self, org_id: uuid.UUID) -> None:
        """Việc nền vừa chạy được ⇒ đóng sự cố thiếu nguồn — chỉ khi cờ Redis còn (không UPDATE mỗi lượt)."""
        from gh import health

        flag = BG_NO_SOURCE_FLAG.format(org_id)
        try:
            if not await self.redis.exists(flag):
                return
            async with self.sm() as db:
                await health.clear(db, org_id, BG_NO_SOURCE_KEY, redis=self.redis)
                await db.commit()
            await self.redis.delete(flag, BG_NO_SOURCE_TRY.format(org_id))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — kết quả model đã có; đóng sự cố lỗi thì lượt sau thử lại
            log.warning("Không đóng được sự cố việc nền thiếu khoá API (%s)", org_id, exc_info=True)

    async def _chain_exhausted(self, org_id: uuid.UUID, reasons: list[str], *, purpose: str = "") -> None:
        if is_background(purpose) and background_cli_only(reasons):
            # v0.1.41 (F-86): sự cố sức khoẻ (dải "Cần Sếp xử lý" + chuông), KHÔNG dùng biz.alerts. Có ngưỡng: lô
            # sàng lọc / trực việc kế tiếp trong 10 phút không mở phiên DB thêm lần nữa.
            if await self.redis.set(BG_NO_SOURCE_TRY.format(org_id), "1", nx=True, ex=BG_NO_SOURCE_TRY_TTL):
                await self._background_no_source(org_id)
            return
        if not await self.redis.set(f"gh:alert:chain:{org_id}", "1", nx=True, ex=3600):
            return
        async with self.sm() as db:
            await raise_alert(db, org_id, alert_type="model_chain_exhausted", priority="P1",
                              title="Hết chuỗi model — việc AI đang xếp hàng chờ",
                              summary="; ".join(reasons)[:500] or "Chưa cấu hình nhà cung cấp nào",
                              suggested="Kiểm tra khoá, hạn mức và trạng thái đăng nhập các nguồn ở màn API & Model")
            await db.commit()

    async def embed(self, org_id: uuid.UUID, texts: list[str]) -> list[list[float]] | None:
        """Embedding 768 chiều; không có model embedding hoặc lỗi → None (không chặn sàng lọc)."""
        if not texts:
            return []
        async with self.sm() as db:
            rows = (await db.execute(text("""
                SELECT p.id, p.kind, p.name, p.endpoint, m.id AS model_id, m.model_name
                FROM agent.providers p JOIN agent.models m ON m.provider_id = p.id
                WHERE p.org_id = :o AND p.is_enabled AND m.is_enabled AND m.model_name ILIKE '%embedding%'
                  AND p.kind IN ('gemini', 'openai_compat', 'deepseek', 'embedding')
                ORDER BY p.failover_rank NULLS LAST"""), {"o": org_id})).all()
            for r in rows:
                keys = (await db.execute(text("""SELECT id, secret_enc FROM agent.provider_keys
                                                 WHERE provider_id = :p AND is_enabled ORDER BY rotation_order"""),
                                         {"p": r.id})).all()
                for k in keys:
                    if await self.redis.exists(cooldown_key(k.id)):
                        continue
                    started = time.monotonic()
                    try:
                        secret = crypto.decrypt(bytes(k.secret_enc), KEY_AAD).decode()
                        client = (GeminiClient(r.endpoint, secret, transport=self.transport)
                                  if r.kind in ("gemini", "embedding")
                                  else OpenAICompatClient(r.endpoint, secret, transport=self.transport))
                        vecs = await client.embed(r.model_name, texts)
                    except RateLimited:
                        await self.redis.set(cooldown_key(k.id), "429", ex=KEY_COOLDOWN_S)
                        continue
                    except Exception as e:  # noqa: BLE001
                        log.warning("embedding lỗi (%s): %s", r.name, e)
                        await self._record(org_id, r.model_id, k.id, "core.embedding", "embedding", "error",
                                           started, None)
                        break
                    await self._record(org_id, r.model_id, k.id, "core.embedding", "embedding", "ok", started, None)
                    if len(vecs) == len(texts) and all(len(v) == 768 for v in vecs):
                        return vecs
                    return None
        return None

    async def _jev_ping(self, *, db_provider_id: uuid.UUID, endpoint: str | None, secret: str) -> str:
        from gh.gen import jev

        async with self.sm() as db:
            model = (await db.execute(text("""SELECT model_name FROM agent.models WHERE provider_id = :p
                                              AND is_enabled ORDER BY id LIMIT 1"""),
                                      {"p": db_provider_id})).scalar_one_or_none()
        client = jev.JevClient(endpoint, secret, model, transport=self.transport, timeout=10.0)
        try:
            await client.ping()
        except jev.JevError as e:
            raise ProviderError(f"Jev: {e}") from e
        return str(client.model)

    async def _cli_probe(self, client: Any, candidates: list[tuple[str, str | None]],
                         kind: str = "antigravity_cli") -> tuple[str, str | None, Completion]:
        """Một lượt gọi thật rất ngắn; (model, mức suy nghĩ) bị CLI từ chối thì thử cặp kế (tối đa 3 model).

        v0.1.58 (hotfix): agy của Boss không đánh dấu model "current" nên lượt gọi đầu KHÔNG có `--effort`, agy từ
        chối và nêu `available: low, medium, high` ⇒ thử lại CÙNG model với một mức trong danh sách đó ('medium' nếu có)
        thay vì bỏ sang model kế (cũng trượt). Ngược lại CLI không nhận mức đã gửi (`what == 'effort'`) ⇒ thử lại không
        gửi mức. Tổng số lượt gọi CLI tối đa `PROBE_MAX_CALLS`; ném lỗi thì gắn `e.model` / `e.effort` của lượt cuối để
        câu báo lỗi nêu đúng thứ đã gửi."""
        last: ModelRejected | None = None
        calls = 0
        for model, effort in [c for c in dict.fromkeys(candidates) if c[0]][:3]:
            tried: set[str | None] = set()
            cur = effort
            while calls < PROBE_MAX_CALLS:
                tried.add(cur)
                calls += 1
                try:
                    return model, cur, await asyncio.wait_for(
                        client.generate(model, [Message("user", PROBE_PROMPT)], json_mode=False, temperature=0,
                                        effort=cur), PROBE_TIMEOUT_S)
                except ModelRejected as e:
                    e.model, e.effort = model, cur
                    last = e
                    if cur is None:
                        nxt = effort_from_rejection(kind, e, tried=tried)
                        if nxt is None:
                            break
                    elif e.what == "effort" and None not in tried:
                        nxt = None
                    else:
                        break
                    cur = nxt
        raise last or BadRequest("Chưa có model nào để gọi thử")

    async def probe_model(self, provider_id: uuid.UUID, model: str, effort: str | None = None) -> dict[str, Any]:
        """Gọi thử ĐÚNG model + mức suy nghĩ này trước khi lưu (v0.1.31/32) — CLI từ chối thì không lưu."""
        async with self.sm() as db:
            p = (await db.execute(text("SELECT id, kind, name, endpoint, auth_state FROM agent.providers "
                                       "WHERE id = :i"), {"i": provider_id})).one()
        started = time.monotonic()
        try:
            await asyncio.wait_for(self._client(p, None, where=placement.INPROC).generate(
                model, [Message("user", PROBE_PROMPT)], json_mode=False, temperature=0, effort=effort),
                PROBE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — trả lỗi cho Console
            if isinstance(e, AuthFailed):
                await self._set_auth_state(p, "expired")
            return {"ok": False, "error": friendly_probe_error(e, model, effort),
                    "rejected": isinstance(e, ModelRejected), "error_detail": error_detail(e),
                    "available": getattr(e, "available", []),
                    "latency_ms": int((time.monotonic() - started) * 1000)}
        if p.auth_state != "ok":
            await self._set_auth_state(p, "ok")
        return {"ok": True, "error": None, "rejected": False, "latency_ms": int((time.monotonic() - started) * 1000)}

    async def _test_cli(self, p: Any, result: dict[str, Any]) -> None:
        """Nguồn CLI: (1) liệt kê model (agy models / phiên Claude), (2) gọi thật một lượt ngắn. "Gọi thử OK" CHỈ khi
        lượt gọi thật thành công — trước đây chỉ liệt kê model nên phiên hết hạn vẫn báo OK (Boss 01/10)."""
        client = self._client(p, None, where=placement.INPROC)
        async with self.sm() as db:
            saved = [(r.model_name, r.effort) for r in (await db.execute(text(
                """SELECT model_name, effort FROM agent.models WHERE provider_id = :p AND is_enabled
                   ORDER BY is_default DESC, id"""), {"p": p.id})).all()]
        try:
            discovered = await client.discover_models()
        finally:
            if getattr(client, "last_models_raw", None):
                result["models_raw"] = client.last_models_raw
        built = catalog.build(p.kind, discovered, saved)
        result.update(built)
        chosen: list[tuple[str, str | None]] = []
        if saved:
            base, var_effort = catalog.split_variant(p.kind, saved[0][0])
            chosen.append((base, saved[0][1] or var_effort))
        current = [(d["id"], d.get("current_effort")) for d in discovered if isinstance(d, dict) and d.get("current")]
        offered = [(m["id"], m.get("default_effort")) for g in built["model_groups"] for m in g["models"]]
        probe_model, probe_effort, _c = await self._cli_probe(client, [*chosen, *current, *offered], p.kind)
        result["probe_model"], result["probe_effort"] = probe_model, probe_effort
        # CLI có thể vừa làm mới token → lưu lại vào hồ sơ đang dùng (hạn mới hiện đúng trên thẻ tài khoản).
        from gh.providers import cli as climod

        async with self.sm() as db:
            org = (await db.execute(text("SELECT org_id FROM agent.providers WHERE id = :i"),
                                    {"i": p.id})).scalar_one()
            with contextlib.suppress(Exception):
                await climod.save_current_back(db, org, p.kind)
                await db.commit()

    async def test_provider(self, provider_id: uuid.UUID) -> dict[str, Any]:
        async with self.sm() as db:
            p = (await db.execute(text("SELECT id, kind, name, endpoint, auth_state FROM agent.providers "
                                       "WHERE id = :i"), {"i": provider_id})).one()
            key = (await db.execute(text("""SELECT secret_enc FROM agent.provider_keys WHERE provider_id = :p
                                            AND is_enabled ORDER BY rotation_order LIMIT 1"""),
                                    {"p": provider_id})).scalar_one_or_none()
        started = time.monotonic()
        result: dict[str, Any] = {"ok": False, "latency_ms": None, "models": [], "error": None}
        auth_bad = False
        try:
            if p.kind in CLI_KINDS:
                await self._test_cli(p, result)
            else:
                if key is None:
                    raise AuthFailed("Chưa có khoá API")
                secret = crypto.decrypt(bytes(key), KEY_AAD).decode()
                if p.kind == "system_one":
                    # Jev (gh.gen.jev): thử một lượt quyết định nhỏ thay vì liệt kê model.
                    result["models"] = [await self._jev_ping(db_provider_id=p.id, endpoint=p.endpoint,
                                                             secret=secret or "")]
                else:
                    names = (await self._client(p, secret, where=placement.INPROC).list_models())[:50]
                    result.update(catalog.build(p.kind, names))
                    result["models"] = names
            result["ok"] = True
        except Exception as e:  # noqa: BLE001 — trả lỗi cho Console, không ném
            auth_bad = isinstance(e, AuthFailed)
            # v0.1.58: nêu model / mức của lượt gọi thử CUỐI (gắn vào lỗi bởi `_cli_probe`), không dùng model "".
            result["error"] = (friendly_probe_error(e, getattr(e, "model", None) or result.get("probe_model") or "",
                                                    getattr(e, "effort", None))
                               if p.kind in CLI_KINDS else str(e)[:300])
            result["error_detail"] = error_detail(e)
            result["error_code"] = probe_error_code(e)
            if p.kind in CLI_KINDS and not result.get("model_groups"):
                # Vẫn cho Console thấy danh sách (dự phòng) để Owner biết sẽ chọn được gì sau khi đăng nhập lại.
                result.update(catalog.build(p.kind, None))
        result.setdefault("error_code", None)
        if p.kind in CLI_KINDS:
            # v0.1.39 (F-76): email của tệp phiên CLI VỪA dùng cho lượt gọi thật (None nếu chưa đăng nhập) — để kiểm
            # "đổi tài khoản" thật sự đổi (không chỉ cờ trong CSDL). Không bí mật → lưu cả vào last_test.
            from gh.providers import cli as climod

            raw = None
            with contextlib.suppress(OSError):
                raw = climod.read_session(p.kind)
            result["account"] = climod.session_email(p.kind, raw) if raw else None
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["at"] = datetime.now(UTC).isoformat()   # Console hiện giờ của lần gọi thật gần nhất (v0.1.32)
        err = result["error"] or ""
        state = "ok" if result["ok"] else ("expired" if auth_bad or "xác thực" in err or "401" in err else "error")
        async with self.sm() as db:
            import orjson

            org_id = (await db.execute(text("""UPDATE agent.providers SET auth_state = :s,
                                               last_test = CAST(:t AS jsonb) WHERE id = :i RETURNING org_id"""),
                                       {"s": state, "i": provider_id,
                                        "t": orjson.dumps(result).decode()})).scalar_one_or_none()
            if state == "ok" and org_id is not None:
                # v0.1.36 (F-6b): gọi thử OK ⇒ đóng sự cố. Gọi thử lỗi 'expired' thì ở đây KHÔNG chuông (Sếp đang
                # nhìn kết quả), nhưng vòng theo dõi (`health._eval_models`) vẫn mở sự cố + MỘT chuông trong ≤60 giây —
                # cố ý: dải "Cần Sếp xử lý" phải nhắc tiếp nếu Sếp rời trang mà chưa đăng nhập lại.
                from gh import health

                await health.clear(db, org_id, f"model.auth_expired:{provider_id}", redis=self.redis)
            await db.commit()
        return result

    async def diagnose(self, provider_id: uuid.UUID) -> dict[str, Any]:
        """Chẩn đoán nguồn CLI (v0.1.32, chỉ Owner): phiên bản, liệt kê model, một lượt gọi rất ngắn với đúng model +
        mức suy nghĩ đang dùng. Đầu ra thô đã che token/email để Boss chép gửi khi còn lỗi."""
        async with self.sm() as db:
            p = (await db.execute(text("SELECT id, kind, name FROM agent.providers WHERE id = :i"),
                                  {"i": provider_id})).one()
            row = (await db.execute(text("""SELECT model_name, effort FROM agent.models WHERE provider_id = :p
                                            AND is_enabled ORDER BY is_default DESC, id LIMIT 1"""),
                                    {"p": provider_id})).one_or_none()
        model, effort = (catalog.split_variant(p.kind, row.model_name) if row else (None, None))
        effort = (row.effort if row else None) or effort
        if model is None:
            first = catalog.fallback(p.kind)[:1]
            model = first[0]["id"] if first else None
        steps = await self._client(p, None, where=placement.INPROC).diagnose(model, effort, PROBE_PROMPT)
        return {"provider": p.name, "kind": p.kind, "model": model, "effort": effort, "steps": steps,
                "at": datetime.now(UTC).isoformat()}
