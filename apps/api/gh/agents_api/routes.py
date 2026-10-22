"""Danh tính Agent (PLAN 4.1) + phần còn thiếu của API & Model (PLAN 4.2, ARCHITECTURE §11).

Đã có sẵn từ giai đoạn 1/2 (KHÔNG viết lại ở đây): `/providers`, khoá (`/providers/{pid}/keys`, chỉ lộ `last4`),
model + hạn mức/giới hạn tốc độ hiển thị-chỉnh được (`/providers/{pid}/models`), kiểm kết nối (`/providers/{pid}/test`),
thẻ "Khoá & phiên" (`/providers/credentials`), hồ sơ Antigravity CLI (`gh.system_api.routes`); "agent đã nói gì,
nhân danh gì" (`agent.decisions`, `GET /agents/decisions` ở `gh.biz.core.routes` — tái dùng nguyên).

Module này thêm:
- CRUD `agent.identities` (tạo/sửa/liệt kê/nhân bản/tắt) + gắn phạm vi nghe `agent.channel_scopes`.
- Mẫu có sẵn (spec E13) — hardcode trong code, không phải bảng DB, chỉ là gợi ý khi tạo mới; không có mặc định
  bắt buộc (Bé Heo tắt sẵn, Owner phải tự bật).
- Gán model theo agent/mục đích + tham số core (`agent.bindings`: model, temperature, context_tokens, rule_codes).

Không thuộc `gh/biz/*` (không phải màn kinh doanh, không cần `ScopeFilter` theo người/nhóm) — quyền theo vai trò
thường `system.read` / `system.manage`, cùng cách `gh.plugins_api.routes` / `gh.system_api.routes` dùng.

Thứ tự route trong file: các đường literal (`/templates`, `/bindings`…) đặt TRƯỚC `/{agent_id}` — Starlette so
khớp theo thứ tự đăng ký, `/{agent_id}` (một đoạn biến) sẽ nuốt mọi đường một đoạn nếu đứng trước.
"""

import uuid
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require, require_pin
from gh.biz.duty.context import DEFAULT_CONTEXT_TOKENS
from gh.biz.duty.engine import DEFAULT_LIMITS
from gh.chassis import actionlog
from gh.chassis.policy import DEFAULT_AUTONOMY
from gh.data.common import iso
from gh.db import DB
from gh.defaults import profiles
from gh.errors import field_errors, not_found, pin_required
from gh.gen import diplomat
from gh.gen.coach import cache as coach_cache
from gh.providers import agy_policy, catalog

router = APIRouter(prefix="/agents", tags=["agents"])
READ = require("system.read", rbac.ALL)
MANAGE = require("system.manage", rbac.ALL)

LIMIT_KEYS = set(DEFAULT_LIMITS)   # {"decisions_per_min", "drafts_per_hour"} — gh.biz.duty.engine.limits_of


# ─── mẫu có sẵn (spec E13 "Mẫu có sẵn (optional templates), không phải bản sắc hệ thống") ──────────────────────

TEMPLATES: dict[str, dict[str, Any]] = {
    "commercial": {
        "name": "Trợ lý thương mại",
        "role_desc": "Theo dõi cơ hội, nhắc việc quá hạn, soạn nháp trả lời khách hàng trong nhóm kinh doanh.",
        "voice": "Thân thiện, chuyên nghiệp, xưng hô lịch sự",
        "speak_when": "Khi được hỏi trực tiếp, có cơ hội mới, hoặc việc sắp quá hạn",
        "forbidden": ["Cam kết giá hoặc chiết khấu ngoài bảng giá đã duyệt", "Tự ý huỷ đơn hàng"],
        "default_enabled": True,
    },
    "key_account": {
        "name": "Chăm sóc khách lớn",
        "role_desc": "Hỗ trợ chăm sóc khách hàng lớn, theo dõi lời hứa và deal đang mở.",
        "voice": "Trang trọng, đúng hẹn, nhấn mạnh cam kết",
        "speak_when": "Khi khách VIP nhắn tin hoặc deal sắp tới hạn",
        "forbidden": ["Đàm phán giá cuối cùng thay Account Manager", "Hứa thời gian giao hàng chưa xác nhận với kho"],
        "default_enabled": True,
    },
    "admin": {
        "name": "Admin hậu cần",
        "role_desc": "Ghi nhận yêu cầu hậu cần, nhắc lịch, tổng hợp việc trong nhóm nội bộ.",
        "voice": "Gọn gàng, trung lập, đúng việc",
        "speak_when": "Khi có yêu cầu hậu cần mới hoặc việc tới hạn",
        "forbidden": ["Duyệt chi phí", "Thay đổi quyền truy cập hệ thống"],
        "default_enabled": True,
    },
    "cs": {
        "name": "CSKH",
        "role_desc": "Trả lời câu hỏi thường gặp, ghi nhận khiếu nại, theo dõi thời gian phản hồi khách hàng.",
        "voice": "Ấm áp, kiên nhẫn, xin lỗi đúng mực khi khách phàn nàn",
        "speak_when": "Khi khách hỏi hoặc phàn nàn trong nhóm/kênh chăm sóc",
        "forbidden": ["Hứa hoàn tiền hoặc đền bù", "Tiết lộ thông tin khách hàng khác"],
        "default_enabled": True,
    },
    "recruiter": {
        "name": "Tuyển dụng",
        "role_desc": "Sàng lọc ứng viên, nhắc lịch phỏng vấn, tổng hợp hồ sơ.",
        "voice": "Chuyên nghiệp, tôn trọng ứng viên",
        "speak_when": "Khi có hồ sơ ứng viên mới hoặc lịch phỏng vấn sắp tới",
        "forbidden": ["Đưa ra kết quả tuyển dụng cuối cùng", "Nhận xét đánh giá cá nhân ứng viên"],
        "default_enabled": True,
    },
    "secretary": {
        "name": "Thư ký cá nhân",
        "role_desc": "Nhắc việc, tổng hợp tin nhắn quan trọng, quản lý lịch cho Owner.",
        "voice": "Riêng tư, ngắn gọn, đúng giờ",
        "speak_when": "Khi có việc cần nhắc hoặc tin nhắn quan trọng gửi tới Owner",
        "forbidden": ["Trả lời thay Owner trong các quyết định cá nhân", "Chia sẻ lịch trình ra ngoài nhóm riêng"],
        "default_enabled": True,
    },
    "mascot": {
        "name": "Bé Heo",
        "role_desc": "Mẫu trò chuyện phiếm, vui vẻ — không đảm nhiệm việc kinh doanh. Tắt sẵn, Sếp tự bật khi cần.",
        "voice": "Dí dỏm, thân mật",
        "speak_when": "Chỉ khi được gọi trực tiếp",
        "forbidden": ["Tự ý tham gia nghiệp vụ kinh doanh", "Gửi tin ra ngoài khi chưa được bật"],
        "default_enabled": False,   # spec E13: mẫu phiếm, tắt mặc định (web gửi is_enabled theo cờ này — F082)
    },
}


@router.get("/templates")
async def list_templates(_: service.CurrentUser = Depends(READ)) -> list[dict[str, Any]]:
    return [{"code": k, **v} for k, v in TEMPLATES.items()]


def _check_template(v: str | None) -> str | None:
    if v is not None and v not in TEMPLATES:
        raise ValueError(f"Mẫu không tồn tại: {v}")
    return v


def _check_limits(v: dict[str, int]) -> dict[str, int]:
    for k, n in v.items():
        if k not in LIMIT_KEYS:
            raise ValueError(f"Khoá giới hạn không hợp lệ: {k} (chỉ nhận {', '.join(sorted(LIMIT_KEYS))})")
        if n <= 0:
            raise ValueError(f"{k} phải > 0")
    return v


# ─── gán model theo agent/mục đích (agent.bindings) ─────────────────────────────

# Mục đích dùng chung — v0.1.43 (F-25): chỉ còn ba khoá và cả ba đều được ModelRouter dùng thật:
#   core.refinery — gh.refinery.runner.AGENT_KEY (sàng lọc & suy luận chính);
#   core.reply    — gh.biz.core.routes._agent_key (soạn lại / dịch nháp không gắn agent);
#   core.gen      — gh.gen.engine.AGENT_KEY (Gen — trợ lý quản trị trong Console).
# v0.1.55 (G1): thêm core.briefing — gh.gen.briefing.AGENT_KEY (Bản tin Gen, việc nền; trước đây dưới khoá core.gen).
# Khoá lõi KHÔNG có dòng gán thì dùng hồ sơ tiêu chuẩn theo vai (gh.defaults.profiles): "Chuẩn: <model> (tự chọn)".
# Ngoài ra mỗi agent identity có khoá riêng `agent:<id>` (gh.biz.duty.engine.agent_key) — danh sách động, ghép ở
# list_bindings(). Bỏ core.intent/core.scoring/core.indexing (không nơi nào gọi model bằng các khoá đó); hàng
# agent.bindings cũ của chúng do migration 0028 dọn — list_bindings vẫn tự ẩn nếu còn, PUT/DELETE trả 422.
CORE_AGENT_KEYS: dict[str, str] = {
    "core.refinery": "Sàng lọc & suy luận chính",
    "core.reply": "Soạn lại / dịch nháp",
    "core.gen": "Gen — trợ lý quản trị",
    "core.briefing": "Bản tin Gen",
    "core.owner_reply": "Sếp gọi bot ở nhóm",
    "core.support": "Chế độ hỗ trợ nhóm",
}


async def _agent_key_label(db: AsyncSession, org_id: uuid.UUID, agent_key: str) -> str:
    if agent_key in CORE_AGENT_KEYS:
        return CORE_AGENT_KEYS[agent_key]
    if agent_key.startswith("agent:"):
        try:
            aid = uuid.UUID(agent_key[len("agent:"):])
        except ValueError as e:
            raise not_found("Agent") from e
        name = (await db.execute(text("SELECT name FROM agent.identities WHERE id = :i AND org_id = :o "
                                      "AND archived_at IS NULL"), {"i": aid, "o": org_id})).scalar_one_or_none()
        if name is None:
            raise not_found("Agent")
        return str(name)
    raise field_errors({"agent_key": "agent_key phải là mục dùng chung (core.*) hoặc agent:<id>"})


REASON_SOURCE_OFF = "Nguồn đã tắt — agent đang dùng Chuẩn"
REASON_MODEL_OFF = "Model đã tắt — agent đang dùng Chuẩn"


def _inactive_reason(r: Any, agy: agy_policy.AgyVerdict | None = None) -> str | None:
    """v0.1.67 (F016): dòng gán còn đó nhưng nguồn / model của nó đã bị Sếp tắt ⇒ `ModelRouter._chain` bỏ dòng gán
    âm thầm và agent chạy bằng hồ sơ Chuẩn — chuỗi này nói thẳng điều đó (null = dòng gán đang có hiệu lực)."""
    if getattr(r, "provider_enabled", True) is False:
        return REASON_SOURCE_OFF
    if getattr(r, "model_enabled", True) is False:
        return REASON_MODEL_OFF
    # Review v0.1.67 P3: dòng gán Antigravity CLI của khoá bị `agy_policy` chặn (agent ngoại giao, core.reply…) —
    # `ModelRouter._chain` gỡ dòng này, khoá chạy bằng Chuẩn; thẻ không được ghi "· Antigravity" như đang dùng thật.
    if agy is not None and not agy.allowed and agy.reason and getattr(r, "provider_kind", None) == agy_policy.KIND_AGY:
        return f"{agy.reason} — đang dùng Chuẩn"
    return None


def _binding_out(r: Any, agy: agy_policy.AgyVerdict | None = None) -> dict[str, Any]:
    # Antigravity CLI gán được cho mọi khoá (luật F-22 owner-only đã gỡ ở v0.1.62 ⇒ không còn `blocked_reason` hay 409
    # AGY_OWNER_GEN_ONLY). v0.1.63 (H2): khoá của lớp ngoại giao / người ngoài (core.reply, core.refinery, agent gắn
    # kênh / nhóm / người, Gen của nhân viên) chạy agy QUA HỘP RIÊNG — hộp chưa chạy / chưa cài thì xem
    # `agy_blocked_reason` ở từng mục danh sách và PUT trả 422 khi gán model agy cho khoá đó (một điểm quyết định:
    # `gh.providers.agy_policy.decide`, luật chỗ chạy ở `gh.providers.placement`).
    return {"model_id": str(r.model_id), "model_name": r.model_name, "provider_name": r.provider_name,
            "temperature": float(r.temperature), "context_tokens": r.context_tokens,
            "rule_codes": list(r.rule_codes or []), "effort": getattr(r, "effort", None),
            "inactive_reason": _inactive_reason(r, agy)}


@router.get("/bindings")
async def list_bindings(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    """Mỗi mục: `binding` (dòng Owner đã gán, kèm `effort`) hoặc null; `source` = 'custom' (có dòng gán — Owner đã đổi)
    | 'standard' (chưa có dòng — dùng hồ sơ tiêu chuẩn); `standard` = model hồ sơ đang phủ khi chưa có dòng (null nếu
    chưa có nguồn phù hợp); `standard_reason` (v0.1.58) = vì sao `standard` null ("cần khoá API…", "chưa có model —
    bấm Kiểm tra kết nối ở …"; chuỗi hoặc null)."""
    agents = (await db.execute(text("SELECT id, name FROM agent.identities WHERE org_id = :o AND archived_at IS NULL "
                                    "ORDER BY created_at"), {"o": user.org_id})).all()
    keys = [*CORE_AGENT_KEYS.items(), *((f"agent:{a.id}", a.name) for a in agents)]
    rows = (await db.execute(text("""
        SELECT b.agent_key, b.model_id, m.model_name, p.name AS provider_name, p.kind AS provider_kind,
               p.is_enabled AS provider_enabled, m.is_enabled AS model_enabled,
               b.temperature, b.context_tokens, b.rule_codes, b.effort
        FROM agent.bindings b JOIN agent.models m ON m.id = b.model_id JOIN agent.providers p ON p.id = m.provider_id
        WHERE b.org_id = :o"""), {"o": user.org_id})).all()
    by_key = {r.agent_key: r for r in rows}
    unbound = [k for k, _ in keys if k not in by_key]
    verdicts = await agy_policy.verdicts_for(db, user.org_id, [k for k, _ in keys])
    standard, reasons = await profiles.standard_with_reasons(db, user.org_id, unbound) if unbound else ({}, {})
    items = [{"agent_key": k, "label": label, "binding": _binding_out(by_key[k], verdicts[k]) if k in by_key else None,
              "source": "custom" if k in by_key else "standard",
              "standard": None if k in by_key else profiles.standard_public(standard.get(k)),
              "standard_reason": None if k in by_key else reasons.get(k),
              # Lý do Antigravity CLI chưa dùng được cho khoá này (chuỗi: hộp riêng chưa chạy / chưa cài) hoặc null —
              # web làm mờ model agy ở ô chọn; dòng gán agy cũ của khoá chưa dùng được vẫn hiện để Sếp gỡ / đổi.
              "agy_blocked_reason": verdicts[k].reason}
             for k, label in keys]
    models = (await db.execute(text("""
        SELECT m.id, m.model_name, p.name AS provider_name, p.kind AS provider_kind, m.is_enabled
        FROM agent.models m JOIN agent.providers p ON p.id = m.provider_id
        WHERE p.org_id = :o ORDER BY p.name, m.model_name"""),
        {"o": user.org_id})).all()
    return {"items": items,
            "models": [{"id": str(m.id), "model_name": m.model_name, "provider_name": m.provider_name,
                       "kind": m.provider_kind, "enabled": m.is_enabled} for m in models]}


class BindingIn(BaseModel):
    model_id: uuid.UUID
    temperature: float = Field(default=0.3, ge=0, le=2)
    context_tokens: int = Field(default=DEFAULT_CONTEXT_TOKENS, ge=256, le=200_000)
    rule_codes: list[str] = Field(default_factory=list, max_length=20)
    #: v0.1.55 (G1): mức suy nghĩ riêng của vai này (chỉ model CLI có mức); null = theo hồ sơ tiêu chuẩn / model.
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


def _check_effort(kind: str, model_name: str, effort: str | None) -> None:
    """422 thân thiện khi mức suy nghĩ không hợp với model (theo `catalog.known_efforts` / `valid_efforts`)."""
    if effort is None:
        return
    allowed = profiles.allowed_efforts(kind, model_name)
    if effort in allowed:
        return
    if not allowed:
        raise field_errors({"effort": f"Model “{model_name}” không chỉnh được mức suy nghĩ — bỏ trống ô này"})
    names = ", ".join(catalog.EFFORT_LABEL[e] for e in catalog.sort_efforts(allowed))
    raise field_errors({"effort": f"Model “{model_name}” chỉ nhận mức suy nghĩ: {names}"})


@router.put("/bindings/{agent_key}")
async def set_binding(agent_key: str, body: BindingIn, request: Request,
                      user: service.CurrentUser = Depends(MANAGE), db: AsyncSession = DB) -> dict[str, Any]:
    label = await _agent_key_label(db, user.org_id, agent_key)
    m = (await db.execute(text("""SELECT m.id, m.model_name, p.kind FROM agent.models m
                                  JOIN agent.providers p ON p.id = m.provider_id
                                  WHERE m.id = :m AND p.org_id = :o"""),
                          {"m": body.model_id, "o": user.org_id})).one_or_none()
    if m is None:
        raise not_found("Model")
    _check_effort(m.kind, m.model_name, body.effort)
    if m.kind == agy_policy.KIND_AGY:
        # H2: khoá phải qua hộp riêng mà hộp chưa chạy / chưa cài ⇒ chưa nhận dòng gán agy (lượt gọi sẽ không dùng được
        # agy và KHÔNG rơi về agy trong tiến trình); lý do là câu trung thực của `agy_policy`.
        verdict = await agy_policy.verdict_for(db, user.org_id, agent_key)
        if not verdict.allowed:
            raise field_errors({"model_id": verdict.reason or agy_policy.REASON_BOX_DOWN})
    await db.execute(text("""
        INSERT INTO agent.bindings (org_id, agent_key, model_id, temperature, context_tokens, rule_codes, effort)
        VALUES (:o, :k, :m, :t, :ct, :rc, :e)
        ON CONFLICT (org_id, agent_key) DO UPDATE
          SET model_id = :m, temperature = :t, context_tokens = :ct, rule_codes = :rc, effort = :e"""),
        {"o": user.org_id, "k": agent_key, "m": body.model_id, "t": body.temperature, "ct": body.context_tokens,
         "rc": body.rule_codes, "e": body.effort})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="agent.bound",
                           target_type="binding", target_id=agent_key, target_label=label,
                           detail=body.model_dump(mode="json"), ip=user.ip)
    r = (await db.execute(text("""
        SELECT b.model_id, m.model_name, p.name AS provider_name, p.kind AS provider_kind,
               p.is_enabled AS provider_enabled, m.is_enabled AS model_enabled, b.temperature,
               b.context_tokens, b.rule_codes, b.effort
        FROM agent.bindings b JOIN agent.models m ON m.id = b.model_id JOIN agent.providers p ON p.id = m.provider_id
        WHERE b.org_id = :o AND b.agent_key = :k"""), {"o": user.org_id, "k": agent_key})).one()
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)   # v0.1.59: thẻ đọc lại ngay
    return {"agent_key": agent_key, "label": label, "binding": _binding_out(r), "source": "custom",
            "standard": None}


@router.delete("/bindings/{agent_key}", status_code=204)
async def delete_binding(agent_key: str, request: Request, user: service.CurrentUser = Depends(MANAGE),
                         db: AsyncSession = DB) -> Response:
    # F-25: khoá lõi đã bỏ (core.intent/scoring/indexing) → 422 như PUT; hàng cũ do migration 0028 dọn.
    # agent:<id> không tra agent.identities ở đây — gỡ được gán model của agent đã xoá.
    if agent_key not in CORE_AGENT_KEYS and not agent_key.startswith("agent:"):
        raise field_errors({"agent_key": "agent_key phải là mục dùng chung (core.*) hoặc agent:<id>"})
    row = (await db.execute(text("DELETE FROM agent.bindings WHERE org_id = :o AND agent_key = :k "
                                 "RETURNING agent_key"), {"o": user.org_id, "k": agent_key})).one_or_none()
    if row is None:
        raise not_found("Gán model")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="agent.unbound",
                           target_type="binding", target_id=agent_key, ip=user.ip)
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)   # v0.1.59: thẻ đọc lại ngay
    return Response(status_code=204)


# ─── danh tính agent (CRUD, phạm vi nghe) ────────────────────────────────────────

AGENT_SELECT = """
SELECT id, org_id, name, role_desc, template, addressing, voice, speak_when, forbidden, autonomy_level,
       is_enabled, limits, created_at, updated_at, archived_at
FROM agent.identities
"""


class ScopeIn(BaseModel):
    channel_id: uuid.UUID
    group_id: uuid.UUID | None = None   # NULL = cả kênh (mọi nhóm đang nghe + tin 1-1)


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role_desc: str = Field(min_length=1, max_length=500)
    voice: str = Field(min_length=1, max_length=200)
    speak_when: str = Field(min_length=1, max_length=500)
    template: str | None = Field(default=None, max_length=40)
    addressing: dict[str, Any] = Field(default_factory=dict)
    forbidden: list[str] = Field(default_factory=list, max_length=50)
    autonomy_level: int = Field(default=DEFAULT_AUTONOMY, ge=0, le=6)
    limits: dict[str, int] = Field(default_factory=dict)
    is_enabled: bool = True
    channel_scopes: list[ScopeIn] = Field(default_factory=list, max_length=100)

    _v_template = field_validator("template")(_check_template)
    _v_limits = field_validator("limits")(_check_limits)


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role_desc: str | None = Field(default=None, min_length=1, max_length=500)
    voice: str | None = Field(default=None, min_length=1, max_length=200)
    speak_when: str | None = Field(default=None, min_length=1, max_length=500)
    template: str | None = Field(default=None, max_length=40)
    addressing: dict[str, Any] | None = None
    forbidden: list[str] | None = Field(default=None, max_length=50)
    autonomy_level: int | None = Field(default=None, ge=0, le=6)
    limits: dict[str, int] | None = None
    channel_scopes: list[ScopeIn] | None = None   # None = giữ nguyên phạm vi hiện có, [] = xoá hết

    _v_template = field_validator("template")(_check_template)

    @field_validator("limits")
    @classmethod
    def _v_limits(cls, v: dict[str, int] | None) -> dict[str, int] | None:
        return _check_limits(v) if v is not None else v


class CloneIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    copy_channel_scopes: bool = True


class DisableIn(BaseModel):
    enabled: bool = False


async def _validate_scopes(db: AsyncSession, org_id: uuid.UUID, scopes: list[ScopeIn]) -> None:
    if not scopes:
        return
    chan_ids = {s.channel_id for s in scopes}
    found = set((await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND id = ANY(CAST(:ids AS "
                                       "uuid[]))"), {"o": org_id, "ids": [str(c) for c in chan_ids]})
                ).scalars().all())
    missing = chan_ids - found
    if missing:
        raise field_errors({"channel_scopes": f"Kênh không tồn tại: {', '.join(str(m) for m in missing)}"})
    group_ids = {s.group_id for s in scopes if s.group_id is not None}
    if group_ids:
        found_g = set((await db.execute(text("SELECT id FROM core.groups WHERE org_id = :o AND id = ANY(CAST(:ids "
                                              "AS uuid[]))"), {"o": org_id, "ids": [str(g) for g in group_ids]})
                      ).scalars().all())
        missing_g = group_ids - found_g
        if missing_g:
            raise field_errors({"channel_scopes": f"Nhóm không tồn tại: {', '.join(str(m) for m in missing_g)}"})


async def _replace_scopes(db: AsyncSession, agent_id: uuid.UUID, scopes: list[ScopeIn]) -> None:
    await db.execute(text("DELETE FROM agent.channel_scopes WHERE agent_id = :a"), {"a": agent_id})
    for s in scopes:
        await db.execute(text("""INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id)
                                 VALUES (:a, :c, :g) ON CONFLICT DO NOTHING"""),
                         {"a": agent_id, "c": s.channel_id, "g": s.group_id})


async def _scopes_of(db: AsyncSession, agent_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT s.channel_id, c.type AS channel_type, s.group_id, g.name AS group_name
        FROM agent.channel_scopes s JOIN core.channels c ON c.id = s.channel_id
        LEFT JOIN core.groups g ON g.id = s.group_id
        WHERE s.agent_id = :a ORDER BY c.type, g.name NULLS FIRST"""), {"a": agent_id})).all()
    return [{"channel_id": str(r.channel_id), "channel_type": r.channel_type,
             "group_id": str(r.group_id) if r.group_id else None, "group_name": r.group_name} for r in rows]


async def _binding_of(db: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID) -> dict[str, Any] | None:
    r = (await db.execute(text("""
        SELECT b.model_id, m.model_name, p.name AS provider_name, p.kind AS provider_kind,
               p.is_enabled AS provider_enabled, m.is_enabled AS model_enabled, b.temperature,
               b.context_tokens, b.rule_codes, b.effort
        FROM agent.bindings b JOIN agent.models m ON m.id = b.model_id JOIN agent.providers p ON p.id = m.provider_id
        WHERE b.org_id = :o AND b.agent_key = :k"""), {"o": org_id, "k": f"agent:{agent_id}"})).one_or_none()
    if r is None:
        return None
    agy = (await agy_policy.verdict_for(db, org_id, f"agent:{agent_id}")
           if r.provider_kind == agy_policy.KIND_AGY else None)
    return _binding_out(r, agy)


def _agent_out(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "name": r.name, "role_desc": r.role_desc, "template": r.template,
            "addressing": r.addressing or {}, "voice": r.voice, "speak_when": r.speak_when,
            "forbidden": list(r.forbidden or []), "autonomy_level": r.autonomy_level, "is_enabled": r.is_enabled,
            "limits": {**DEFAULT_LIMITS, **(r.limits or {})}, "created_at": iso(r.created_at),
            "updated_at": iso(r.updated_at), "archived": r.archived_at is not None}


#: Quyết định tính là "đã lên tiếng" (F083): im lặng và ghi chú vào sổ tay KHÔNG phải lên tiếng với con người.
SPOKE_DECISIONS = ("suggest", "draft", "send")


async def _attach_model_and_spoke(db: AsyncSession, org_id: uuid.UUID, items: list[dict[str, Any]]) -> None:
    """Gắn vào từng agent: `model` (F022: dùng model nào, chạy được không — một dòng chữ, không object lồng) và
    `last_spoke_at` / `last_spoke_decision` (F083: lần lên tiếng gần nhất THEO TỪNG AGENT, không phải trong 8 quyết
    định chung)."""
    if not items:
        return
    need_std = [f"agent:{i['id']}" for i in items if i["binding"] is None or i["binding"].get("inactive_reason")]
    std, why = await profiles.standard_with_reasons(db, org_id, need_std) if need_std else ({}, {})
    for i in items:
        key, b = f"agent:{i['id']}", i["binding"]
        if b is not None and not b.get("inactive_reason"):
            i["model"] = {"custom": True, "ok": True, "label": f"{b['model_name']} · {b['provider_name']}"}
        elif b is not None:
            cand = std.get(key)
            tail = f": {cand['model_name']}" if cand else " — chưa có model khác chạy được"
            i["model"] = {"custom": True, "ok": cand is not None, "label": f"{b['inactive_reason']}{tail}"}
        else:
            i["model"] = {"custom": False, "ok": std.get(key) is not None,
                          "label": profiles.standard_text(std.get(key), why.get(key))}
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (agent_id) agent_id, decision, at FROM agent.decisions
        WHERE org_id = :o AND decision = ANY(CAST(:d AS text[])) AND agent_id = ANY(CAST(:a AS uuid[]))
        ORDER BY agent_id, at DESC"""),
                             {"o": org_id, "d": list(SPOKE_DECISIONS), "a": [i["id"] for i in items]})).all()
    spoke = {str(r.agent_id): r for r in rows}
    for i in items:
        r = spoke.get(i["id"])
        i["last_spoke_at"] = iso(r.at) if r else None
        i["last_spoke_decision"] = r.decision if r else None


async def _full(db: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID) -> dict[str, Any]:
    r = (await db.execute(text(AGENT_SELECT + " WHERE id = :i AND org_id = :o"),
                          {"i": agent_id, "o": org_id})).one_or_none()
    if r is None:
        raise not_found("Agent")
    out = _agent_out(r)
    out["channel_scopes"] = await _scopes_of(db, agent_id)
    out["binding"] = await _binding_of(db, org_id, agent_id)
    await _attach_model_and_spoke(db, org_id, [out])
    return out


class DiplomatIn(BaseModel):
    agent_id: uuid.UUID | None = None


@router.get("/diplomat")
async def get_diplomat(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    """v0.1.73 — «Agent ngoại giao»: agent nói thay Gen ở MỌI kênh ngoài Console (kênh quản trị Zalo/WhatsApp,
    Telegram, trả lời trong nhóm, Chế độ hỗ trợ). Chưa chọn ⇒ `agent_id` null và bot xưng «trợ lý»."""
    p = await diplomat.load(db, user.org_id)
    return {**diplomat.public(p), "neutral_name": diplomat.NEUTRAL}


@router.put("/diplomat")
async def put_diplomat(body: DiplomatIn, user: service.CurrentUser = Depends(MANAGE),
                       _pin: Any = Depends(require_pin("agent.manage")), db: AsyncSession = DB) -> dict[str, Any]:
    """Đặt / bỏ agent ngoại giao (Owner + mã PIN như đổi cấu hình agent). Chỉ nhận agent đang bật, chưa lưu trữ."""
    if body.agent_id is not None and not await diplomat.valid_agent(db, user.org_id, body.agent_id):
        raise field_errors({"agent_id": "Agent không tồn tại, đã tắt hoặc đã lưu trữ"})
    await diplomat.save(db, user.org_id, body.agent_id)
    p = await diplomat.load(db, user.org_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="agent.diplomat_set",
                           target_type="agent", target_id=str(body.agent_id) if body.agent_id else None,
                           target_label=p.name if p.configured else None, ip=user.ip)
    return {**diplomat.public(p), "neutral_name": diplomat.NEUTRAL}


@router.get("")
async def list_agents(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> list[dict[str, Any]]:
    """Agent đang dùng (không gồm agent đã Lưu trữ — F085)."""
    rows = (await db.execute(text(AGENT_SELECT + " WHERE org_id = :o AND archived_at IS NULL ORDER BY created_at"),
                             {"o": user.org_id})).all()
    out = []
    for r in rows:
        item = _agent_out(r)
        item["channel_scopes"] = await _scopes_of(db, r.id)
        item["binding"] = await _binding_of(db, user.org_id, r.id)
        out.append(item)
    await _attach_model_and_spoke(db, user.org_id, out)
    return out


async def _check_name_free(db: AsyncSession, org_id: uuid.UUID, name: str, except_id: uuid.UUID | None = None) -> None:
    """F085: hai agent trùng tên (không phân biệt hoa thường / khoảng trắng hai đầu) làm Sếp không biết ai là ai ở
    thẻ, bảng quyết định, Gán model — chặn khi tạo / sửa tên / nhân bản. Agent đã Lưu trữ không tính."""
    want = name.strip().casefold()
    rows = (await db.execute(text("SELECT id, name FROM agent.identities WHERE org_id = :o AND archived_at IS NULL"),
                             {"o": org_id})).all()
    if any(r.name.strip().casefold() == want and r.id != except_id for r in rows):
        raise field_errors({"name": f"Đã có agent tên “{name.strip()}” — đặt tên khác để khỏi nhầm."})


@router.post("", status_code=201)
async def create_agent(body: AgentIn, user: service.CurrentUser = Depends(MANAGE),
                       _pin: Any = Depends(require_pin("agent.manage")), db: AsyncSession = DB) -> dict[str, Any]:
    """Không có mặc định bắt buộc (spec E13) — Owner tự đặt mọi trường, mẫu chỉ là gợi ý prefill ở Console
    (`GET /agents/templates`), không tự áp khi thiếu. PIN vì đây là thao tác "tạo agent"."""
    await _validate_scopes(db, user.org_id, body.channel_scopes)
    await _check_name_free(db, user.org_id, body.name)
    aid = (await db.execute(text("""
        INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when, forbidden,
                                      autonomy_level, is_enabled, limits)
        VALUES (:o, :n, :rd, :tpl, CAST(:addr AS jsonb), :v, :sw, :fb, :al, :en, CAST(:lim AS jsonb))
        RETURNING id"""),
        {"o": user.org_id, "n": body.name.strip(), "rd": body.role_desc.strip(), "tpl": body.template,
         "addr": orjson.dumps(body.addressing).decode(), "v": body.voice.strip(), "sw": body.speak_when.strip(),
         "fb": body.forbidden, "al": body.autonomy_level, "en": body.is_enabled,
         "lim": orjson.dumps(body.limits).decode()})).scalar_one()
    await _replace_scopes(db, aid, body.channel_scopes)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="agent.created",
                           target_type="agent", target_id=str(aid), target_label=body.name,
                           detail={"template": body.template, "autonomy_level": body.autonomy_level,
                                   "is_enabled": body.is_enabled, "scopes": len(body.channel_scopes)}, ip=user.ip)
    return await _full(db, user.org_id, aid)


@router.get("/{agent_id}")
async def get_agent(agent_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                    db: AsyncSession = DB) -> dict[str, Any]:
    return await _full(db, user.org_id, agent_id)


_PATCH_COLS = ("name", "role_desc", "voice", "speak_when", "template", "autonomy_level", "addressing", "forbidden",
              "limits")

# v0.1.45 (F-20): các trường "rào chắn" của agent — đổi giá trị (hạ hoặc nâng rào) cần phiên PIN `policy.change`.
GUARDED_FIELDS = ("autonomy_level", "forbidden", "limits", "channel_scopes")


def _norm_forbidden(items: list[str] | None) -> list[str]:
    """Chuẩn hoá danh sách điều cấm: bỏ khoảng trắng hai đầu, bỏ dòng rỗng (dùng cả khi so lẫn khi lưu)."""
    return [s.strip() for s in (items or []) if s and s.strip()]


async def _barrier_changed(db: AsyncSession, cur: Any, body: "AgentPatch") -> bool:
    """Có trường rào chắn nào được gửi (khác None) VÀ khác giá trị đang lưu không. Gửi lại đúng giá trị cũ ⇒ False."""
    sent = body.model_dump(exclude_unset=True)
    if sent.get("autonomy_level") is not None and int(sent["autonomy_level"]) != int(cur.autonomy_level):
        return True
    if body.forbidden is not None and _norm_forbidden(body.forbidden) != _norm_forbidden(list(cur.forbidden or [])):
        return True
    if body.limits is not None and {**DEFAULT_LIMITS, **(cur.limits or {})} != {**DEFAULT_LIMITS, **body.limits}:
        return True
    if body.channel_scopes is not None:
        have = {(s["channel_id"], s["group_id"]) for s in await _scopes_of(db, cur.id)}
        want = {(str(s.channel_id), str(s.group_id) if s.group_id else None) for s in body.channel_scopes}
        if have != want:
            return True
    return False


@router.patch("/{agent_id}")
async def patch_agent(agent_id: uuid.UUID, body: AgentPatch, user: service.CurrentUser = Depends(MANAGE),
                      db: AsyncSession = DB) -> dict[str, Any]:
    """Sửa agent. v0.1.45 (F-20): PIN policy.change chỉ khi đổi autonomy_level/forbidden/limits/channel_scopes
    (khác giá trị đang lưu) — đổi tên, mô tả, giọng, lúc lên tiếng, mẫu, xưng hô không bao giờ đòi PIN; gửi lại đúng
    giá trị cũ của trường rào chắn cũng không đòi PIN (không hạ rào). Thứ tự: 403 → 404 → 422 → 423."""
    cur = (await db.execute(text(AGENT_SELECT + " WHERE id = :i AND org_id = :o AND archived_at IS NULL"),
                            {"i": agent_id, "o": user.org_id})).one_or_none()
    if cur is None:
        raise not_found("Agent")
    if body.channel_scopes is not None:
        await _validate_scopes(db, user.org_id, body.channel_scopes)
    if body.name is not None and body.name.strip().casefold() != cur.name.strip().casefold():
        await _check_name_free(db, user.org_id, body.name, except_id=agent_id)
    if not user.pin_active() and await _barrier_changed(db, cur, body):
        raise pin_required()
    fields = body.model_dump(exclude_unset=True, exclude={"channel_scopes"})
    if fields.get("forbidden") is not None:
        fields["forbidden"] = _norm_forbidden(fields["forbidden"])
    changes: dict[str, Any] = {}
    for k in _PATCH_COLS:
        if k not in fields or fields[k] is None:
            continue
        v = fields[k]
        cur_v = getattr(cur, k)
        if k == "forbidden":
            cur_v = list(cur_v or [])
        elif k in ("addressing", "limits"):
            cur_v = cur_v or {}
        if v != cur_v:
            changes[k] = v
    if changes:
        sets: list[str] = []
        params: dict[str, Any] = {"i": agent_id}
        for k, v in changes.items():
            if k in ("addressing", "limits"):
                sets.append(f"{k} = CAST(:{k} AS jsonb)")
                params[k] = orjson.dumps(v).decode()
            else:
                sets.append(f"{k} = :{k}")
                params[k] = v.strip() if isinstance(v, str) else v
        sets.append("updated_at = now()")
        await db.execute(text(f"UPDATE agent.identities SET {', '.join(sets)} WHERE id = :i"), params)  # noqa: S608
    scope_changed = False
    if body.channel_scopes is not None:
        await _replace_scopes(db, agent_id, body.channel_scopes)
        scope_changed = True
    if changes or scope_changed:
        detail: dict[str, Any] = {"changed": list(changes)}
        if scope_changed:
            detail["channel_scopes"] = len(body.channel_scopes or [])
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="agent.updated", target_type="agent", target_id=str(agent_id),
                               target_label=cur.name, detail=detail, ip=user.ip)
    return await _full(db, user.org_id, agent_id)


async def _copy_binding(db: AsyncSession, org_id: uuid.UUID, src_id: uuid.UUID, new_id: uuid.UUID) -> bool:
    """F091: bản sao chạy đúng model riêng của agent nguồn (không âm thầm rơi về Chuẩn). Antigravity CLI chỉ chép khi
    luật agy (`agy_policy`) vẫn cho agent mới — phạm vi nghe đã chép xong trước khi gọi hàm này."""
    b = (await db.execute(text("""
        SELECT b.model_id, b.temperature, b.context_tokens, b.rule_codes, b.effort, p.kind
        FROM agent.bindings b JOIN agent.models m ON m.id = b.model_id JOIN agent.providers p ON p.id = m.provider_id
        WHERE b.org_id = :o AND b.agent_key = :k"""), {"o": org_id, "k": f"agent:{src_id}"})).one_or_none()
    if b is None:
        return False
    if b.kind == agy_policy.KIND_AGY and not (await agy_policy.verdict_for(db, org_id, f"agent:{new_id}")).allowed:
        return False
    await db.execute(text("""
        INSERT INTO agent.bindings (org_id, agent_key, model_id, temperature, context_tokens, rule_codes, effort)
        VALUES (:o, :k, :m, :t, :ct, :rc, :e)"""),
                     {"o": org_id, "k": f"agent:{new_id}", "m": b.model_id, "t": b.temperature,
                      "ct": b.context_tokens, "rc": list(b.rule_codes or []), "e": b.effort})
    return True


@router.post("/{agent_id}/clone", status_code=201)
async def clone_agent(agent_id: uuid.UUID, body: CloneIn, user: service.CurrentUser = Depends(MANAGE),
                      _pin: Any = Depends(require_pin("agent.manage")), db: AsyncSession = DB) -> dict[str, Any]:
    """Nhân bản: sao mọi trường (kể cả phạm vi nghe nếu chọn và model riêng — v0.1.67 F091) từ agent nguồn, NHƯNG
    tạo ở trạng thái tắt (`is_enabled=false`) — tránh hai agent cùng nghe/trả lời trùng nhau trước khi Owner rà
    lại bản sao. Quyết định tự đưa ra (PLAN không nói rõ trạng thái ban đầu của bản nhân bản)."""
    src = (await db.execute(text(AGENT_SELECT + " WHERE id = :i AND org_id = :o AND archived_at IS NULL"),
                            {"i": agent_id, "o": user.org_id})).one_or_none()
    if src is None:
        raise not_found("Agent")
    await _check_name_free(db, user.org_id, body.name)
    new_id = (await db.execute(text("""
        INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when, forbidden,
                                      autonomy_level, is_enabled, limits)
        VALUES (:o, :n, :rd, :tpl, CAST(:addr AS jsonb), :v, :sw, :fb, :al, false, CAST(:lim AS jsonb))
        RETURNING id"""),
        {"o": user.org_id, "n": body.name.strip(), "rd": src.role_desc, "tpl": src.template,
         "addr": orjson.dumps(src.addressing or {}).decode(), "v": src.voice, "sw": src.speak_when,
         "fb": list(src.forbidden or []), "al": src.autonomy_level,
         "lim": orjson.dumps(src.limits or {}).decode()})).scalar_one()
    if body.copy_channel_scopes:
        await db.execute(text("""INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id)
                                 SELECT :n, channel_id, group_id FROM agent.channel_scopes WHERE agent_id = :s"""),
                         {"n": new_id, "s": agent_id})
    binding_copied = await _copy_binding(db, user.org_id, agent_id, new_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="agent.cloned",
                           target_type="agent", target_id=str(new_id), target_label=body.name,
                           detail={"source_id": str(agent_id), "source_name": src.name,
                                   "copy_channel_scopes": body.copy_channel_scopes,
                                   "binding_copied": binding_copied}, ip=user.ip)
    return await _full(db, user.org_id, new_id)


@router.patch("/{agent_id}/disable")
async def disable_agent(agent_id: uuid.UUID, body: DisableIn, user: service.CurrentUser = Depends(MANAGE),
                        db: AsyncSession = DB) -> dict[str, Any]:
    """Tắt/bật một danh tính agent (cùng dạng toggle với `gh.plugins_api.routes.toggle`). Tắt KHÔNG xoá cấu hình
    hay phạm vi nghe — chỉ dừng agent nhận đơn vị mới (`agent.identities a JOIN ... WHERE a.is_enabled` ở
    `gh.biz.duty.context`). v0.1.67 (F089): TẮT không cần PIN (agent đang nói bậy mà PIN bị khoá thì Sếp vẫn dừng
    được ngay, như nút dừng khẩn Facebook); BẬT lại cần PIN `agent.manage`."""
    if body.enabled and not user.pin_active():
        raise pin_required()
    row = (await db.execute(text("""UPDATE agent.identities SET is_enabled = :e, updated_at = now()
                                    WHERE id = :i AND org_id = :o AND archived_at IS NULL
                                    RETURNING name, is_enabled"""),
                            {"e": body.enabled, "i": agent_id, "o": user.org_id})).one_or_none()
    if row is None:
        raise not_found("Agent")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="agent.enabled" if body.enabled else "agent.disabled", target_type="agent",
                           target_id=str(agent_id), target_label=row.name, ip=user.ip)
    return await _full(db, user.org_id, agent_id)


@router.post("/{agent_id}/archive")
async def archive_agent(agent_id: uuid.UUID, request: Request, user: service.CurrentUser = Depends(MANAGE),
                        _pin: Any = Depends(require_pin("agent.manage")), db: AsyncSession = DB) -> dict[str, Any]:
    """F085 — Lưu trữ agent: tắt hẳn, ẩn khỏi danh sách / bảng Gán model / ô chọn, gỡ model riêng. KHÔNG xoá dòng:
    lịch sử quyết định (`agent.decisions`) và nháp cũ vẫn đọc được tên agent. Tên đã lưu trữ đặt lại được."""
    row = (await db.execute(text("""UPDATE agent.identities
                                    SET is_enabled = false, archived_at = now(), updated_at = now()
                                    WHERE id = :i AND org_id = :o AND archived_at IS NULL RETURNING name"""),
                            {"i": agent_id, "o": user.org_id})).one_or_none()
    if row is None:
        raise not_found("Agent")
    had_binding = (await db.execute(text("DELETE FROM agent.bindings WHERE org_id = :o AND agent_key = :k "
                                         "RETURNING 1"), {"o": user.org_id, "k": f"agent:{agent_id}"})
                   ).one_or_none() is not None
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="agent.archived",
                           target_type="agent", target_id=str(agent_id), target_label=row.name,
                           detail={"binding_removed": had_binding}, ip=user.ip)
    await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)
    return await _full(db, user.org_id, agent_id)


__all__ = ["router", "TEMPLATES", "CORE_AGENT_KEYS"]
