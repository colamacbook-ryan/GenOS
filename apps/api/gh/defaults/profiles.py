"""Hồ sơ model tiêu chuẩn theo VAI (v0.1.55, G1 — "Chế độ tiêu chuẩn").

Cài xong chưa gán model nào mà Gen vẫn trả lời được: bộ định tuyến (`gh.providers.router.ModelRouter._chain`) tự
chọn model cho từng VAI theo bảng A1 dưới đây — dữ liệu theo (vai × loại nguồn), KHÔNG ghi cứng tên model: nguồn nào
đang bật thì chọn trong `agent.models` đã bật của nguồn đó theo TẦNG (`gh.providers.catalog.tier_of`: fast / balanced
/ strong). Thiếu tầng thì lấy tầng gần nhất; KHÔNG tự thêm dòng model nào. Dòng `agent.bindings` Owner đã đổi luôn
thắng hồ sơ; nguồn Owner đã chốt model ("Dùng model này" = `is_default`) giữ model đó (v0.1.66 — hiển thị "Chuẩn: …"
và chuỗi chạy thật `router._chain` dùng CHUNG hàm `resolve` này, không còn hai bộ chọn).

Luật cứng còn lại (bảng A1 chỉ chọn trong phạm vi đã được phép):
- việc nền (sàng lọc tin `core.refinery`, trực việc `agent:*`, Bản tin `core.briefing`) chỉ chạy Claude Code CLI khi
  Owner đã cho phép việc nền (F-86 / QD-12 — điều khoản gói Claude); thiếu nguồn thì giữ hành vi cũ (hết chuỗi →
  cảnh báo), KHÔNG tự chuyển sang Claude Code CLI.

Antigravity CLI (agy) — Owner mở cho các vai ngày 16/10/2026 sau khi được báo rủi ro (agy có công cụ đọc tệp / chạy
lệnh; luật F-22 "agy chỉ cho Gen của Owner" của v0.1.38 đã gỡ). Gói G (v0.1.62) PHÂN LỚP, gói H2 (v0.1.63) cho mỗi lớp
một chỗ chạy: core (core.gen của Owner, core.briefing, agent nội bộ) chạy agy TRONG TIẾN TRÌNH; lớp ngoại giao
(core.reply, agent gắn kênh / nhóm / người), sàng lọc tin người lạ (core.refinery) và Gen của nhân viên chạy agy QUA
HỘP RIÊNG (`gh.agybox`, không có khoá hệ thống). Hộp chưa cài / tắt thì các vai này dùng nguồn AI kế, KHÔNG rơi về agy
trong tiến trình. Quyết định nằm ở MỘT điểm `gh.providers.agy_policy.decide` (luật chỗ chạy: `gh.providers.placement`);
`resolve` / `missing_reason` / `choice_options` nhận kết quả đó (`agy=`) và loại nguồn Antigravity CLI khỏi hồ sơ tiêu
chuẩn của khoá chưa dùng được agy.

`resolve()` THUẦN (không DB) để test ma trận; phần truy vấn DB nằm ở `load_rows` / `standard_for` / `choice_options`.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.providers import agy_policy, catalog

KIND_CLAUDE = "claude_code_cli"
KIND_AGY = "antigravity_cli"
KIND_API = "api"
API_KINDS = ("gemini", "deepseek", "openai_compat")
CLI_KINDS = (KIND_AGY, KIND_CLAUDE)

TIER_ORDER = catalog.TIERS                   # ("fast", "balanced", "strong")
TIER_LABEL = {"fast": "Nhanh", "balanced": "Cân bằng", "strong": "Kỹ hơn"}
#: Tầng của khung chat (TurnIn.model_choice.tier): 'deep' ↔ 'strong'; 'auto' = hồ sơ tiêu chuẩn.
CHOICE_TIERS = ("auto", "fast", "balanced", "deep")
CHOICE_EFFORTS = ("low", "medium", "high")   # chat chỉ cho chọn ba mức này (Thấp/Vừa/Cao)

ROLE_GEN, ROLE_BRIEFING, ROLE_REFINERY, ROLE_REPLY, ROLE_AGENT = (
    "core.gen", "core.briefing", "core.refinery", "core.reply", "agent:*")
#: Vai chạy nền (không có Sếp ngồi trước màn hình): luật F-86 (Claude Code CLI) áp bất kể `purpose` bên gọi truyền.
#: v0.1.72: Sếp tag bot trong nhóm và Chế độ hỗ trợ chạy khi không có Sếp ngồi trước màn hình ⇒ cũng là việc nền.
ROLE_OWNER_REPLY, ROLE_SUPPORT = "core.owner_reply", "core.support"
BACKGROUND_ROLES = frozenset({ROLE_BRIEFING, ROLE_REFINERY, ROLE_AGENT, ROLE_OWNER_REPLY, ROLE_SUPPORT})


@dataclass(frozen=True)
class Profile:
    tier: str
    effort: str | None
    temperature: float
    context_tokens: int


# Bảng A1 — vai × loại nguồn → hồ sơ. Vắng mục = vai đó KHÔNG dùng loại nguồn ấy.
PROFILES: dict[str, dict[str, Profile]] = {
    # Gen: CLI cân bằng/vừa; agy cân bằng (flash)/vừa; khoá API cân bằng.
    ROLE_GEN: {KIND_CLAUDE: Profile("balanced", "medium", 0.3, 6000),
               KIND_AGY: Profile("balanced", "medium", 0.3, 6000),
               KIND_API: Profile("balanced", None, 0.3, 6000)},
    # Bản tin Gen: khoá API nhanh; Claude CLI nhanh CHỈ khi Owner cho phép việc nền; agy nhanh/thấp (v0.1.62).
    ROLE_BRIEFING: {KIND_API: Profile("fast", None, 0.2, 6000),
                    KIND_CLAUDE: Profile("fast", None, 0.2, 6000),
                    KIND_AGY: Profile("fast", "low", 0.2, 6000)},
    # Sàng lọc: khoá API nhanh t=0,2 (JSON sai hai lần: runner hiện có tự lên balanced — ở đây chỉ cấp tầng); agy
    # nhanh/thấp (v0.1.62). agy không có model tầng "nhanh" ⇒ `pick_model` lấy tầng gần nhất (flash).
    ROLE_REFINERY: {KIND_API: Profile("fast", None, 0.2, 6000),
                    KIND_CLAUDE: Profile("fast", None, 0.2, 6000),
                    KIND_AGY: Profile("fast", "low", 0.2, 6000)},
    # Soạn lại / dịch nháp: CLI cân bằng/thấp (agy cũng vậy, v0.1.62); khoá API cân bằng.
    ROLE_REPLY: {KIND_CLAUDE: Profile("balanced", "low", 0.3, 6000),
                 KIND_AGY: Profile("balanced", "low", 0.3, 6000),
                 KIND_API: Profile("balanced", None, 0.3, 6000)},
    # Agent trực việc: khoá API cân bằng t=0,3 ngữ cảnh 6000; Claude CLI chỉ khi cho phép việc nền; agy cân bằng/thấp
    # (v0.1.62 — gửi mức rõ ràng để agy khỏi từ chối rồi thử lại).
    ROLE_AGENT: {KIND_API: Profile("balanced", None, 0.3, 6000),
                 KIND_CLAUDE: Profile("balanced", None, 0.3, 6000),
                 KIND_AGY: Profile("balanced", "low", 0.3, 6000)},
}
# v0.1.72: hai vai mới dùng hồ sơ của «Soạn lại / dịch nháp» (cân bằng) — cùng lớp «người ngoài», chạy qua hộp riêng.
PROFILES[ROLE_OWNER_REPLY] = PROFILES[ROLE_REPLY]
PROFILES[ROLE_SUPPORT] = PROFILES[ROLE_REPLY]


def role_of(agent_key: str) -> str | None:
    """Khoá agent → vai của bảng A1; khoá lạ (core.embedding…) không có hồ sơ ⇒ None (chuỗi chỉ theo hạng như cũ)."""
    if agent_key in PROFILES:
        return agent_key
    if agent_key.startswith("agent:"):
        return ROLE_AGENT
    return None


def kind_class(kind: str) -> str | None:
    if kind in (KIND_CLAUDE, KIND_AGY):
        return kind
    if kind in API_KINDS:
        return KIND_API
    return None


def normalize_tier(tier: str | None) -> str | None:
    """'deep' (chat) → 'strong'; 'auto'/None/giá trị lạ → None (hồ sơ tiêu chuẩn tự chọn)."""
    if tier == "deep":
        return "strong"
    return tier if tier in TIER_ORDER else None


def _g(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def allowed_efforts(kind: str, model_name: str) -> tuple[str, ...]:
    """Mức suy nghĩ model cho phép: theo nguồn ĐÃ xác minh (`known_efforts`), không biết thì theo CLI (`valid_efforts`).
    Nguồn khoá API không có mức suy nghĩ ⇒ rỗng; haiku ⇒ rỗng."""
    if kind not in CLI_KINDS:
        return ()
    base = catalog.split_variant(kind, model_name.split("[")[0])[0]
    known = catalog.known_efforts(kind, base)
    if known is None and kind == KIND_AGY:
        # Danh mục agy (biến thể trong tệp chạy `agy`): gemini-3.1-pro chỉ nhận low/high — gửi "medium" là CLI từ chối.
        for m in catalog.AGY_FALLBACK:
            if m["id"] == base.lower():
                known = tuple(m["efforts"])
    return tuple(known) if known is not None else catalog.valid_efforts(kind)


def effort_for(kind: str, model_name: str, effort: str | None) -> str | None:
    """`effort` nếu model hỗ trợ đúng mức đó, ngược lại None (không bao giờ gửi mức model không có)."""
    return effort if effort and effort in allowed_efforts(kind, model_name) else None


def _model_ok(m: Any) -> bool:
    name = str(_g(m, "model_name", ""))
    return bool(_g(m, "is_enabled", True)) and "embedding" not in name.lower()


def pick_model(kind: str, models: Sequence[Any], wanted: str) -> tuple[Any, str] | None:
    """Model của MỘT nguồn theo tầng `wanted`: đúng tầng (giữ thứ tự đầu vào: mặc định trước), thiếu tầng thì tầng
    gần nhất (hoà thì tầng rẻ hơn). Chỉ chọn trong model đã bật; không có model ⇒ None. Trả (dòng model, tầng thực)."""
    best: tuple[int, int, int, Any, str] | None = None
    want = TIER_ORDER.index(wanted)
    for i, m in enumerate(models):
        if not _model_ok(m):
            continue
        t = catalog.tier_of(kind, str(_g(m, "model_name", "")))
        if t is None:
            continue
        ti = TIER_ORDER.index(t)
        key = (abs(ti - want), ti, i)
        if best is None or key < best[:3]:
            best = (*key, m, t)
    return None if best is None else (best[3], best[4])


def _rank_key(p: Any) -> tuple[int, int]:
    r = _g(p, "failover_rank")
    return (1, 0) if r is None else (0, int(r))


def provider_allowed(kind: str, *, bg: bool, bg_cli_allowed: set[str] | frozenset[str],
                     agy_ok: bool = True) -> bool:
    """Luật cứng F-86 cho MỘT loại nguồn (hồ sơ tiêu chuẩn chỉ chọn trong phạm vi này). Antigravity CLI không cần Owner
    cho phép việc nền (Claude Code CLI thì có); `agy_ok=False` (quyết định `agy_policy.decide` cho khoá này là KHÔNG:
    vai phải qua hộp mà hộp chưa cài / tắt) ⇒ loại Antigravity CLI."""
    if kind == KIND_AGY:
        return agy_ok
    if kind == KIND_CLAUDE:
        return (not bg) or kind in bg_cli_allowed
    return kind in API_KINDS


def resolve(providers_rows: Sequence[Any], models_rows: Sequence[Any], agent_key: str, *, background: bool,
            bg_cli_allowed: set[str] | frozenset[str], tier_override: str | None = None,
            agy: agy_policy.AgyVerdict | None = None) -> list[dict[str, Any]]:
    """Hồ sơ tiêu chuẩn của MỘT vai → danh sách ứng viên (nguồn + model), THỨ TỰ theo hạng nguồn (failover_rank).

    - `providers_rows`: id, kind, name, is_enabled (mặc định True), failover_rank, has_key (chỉ nguồn khoá API; vắng =
      coi như có). `models_rows`: id, provider_id, model_name, is_enabled, effort… (thứ tự vào = ưu tiên trong tầng).
    - `background`: purpose của lượt gọi là việc nền; các VAI nền (sàng lọc, trực việc, bản tin) luôn bị coi là việc
      nền.
    - `bg_cli_allowed`: CLI Owner đã cho chạy việc nền (chỉ `claude_code_cli`; Antigravity CLI không cần cho phép).
    - `tier_override`: ép tầng (chat chọn Nhanh / Cân bằng / Kỹ hơn) — vẫn tôn trọng mọi luật trên; ép tầng thì model
      Owner đã chốt ("Dùng model này") không giữ chỗ đầu (lựa chọn của lượt này thắng).
    - `agy`: quyết định `agy_policy.decide` cho khoá này. Bỏ trống ⇒ tính tĩnh từ khoá với hộp coi như CHƯA CÀI (đóng
      an toàn: vai phải qua hộp bị loại); bên gọi thật truyền `verdict_for` (cần DB: agent ngoại giao + trạng thái
      hộp).
    Hàm thuần: không DB, không Redis. Thiếu tầng ⇒ tầng gần nhất; KHÔNG tự thêm dòng model."""
    role = role_of(agent_key)
    if role is None:
        return []
    agy_ok = (agy or agy_policy.decide(agent_key)).allowed
    forced = normalize_tier(tier_override)
    bg = background or role in BACKGROUND_ROLES
    by_provider: dict[str, list[Any]] = {}
    for m in models_rows:
        by_provider.setdefault(str(_g(m, "provider_id")), []).append(m)
    out: list[dict[str, Any]] = []
    for p in sorted(providers_rows, key=_rank_key):
        kind = str(_g(p, "kind", ""))
        kc = kind_class(kind)
        prof = PROFILES[role].get(kc) if kc else None
        if prof is None or not _g(p, "is_enabled", True):
            continue
        if kc == KIND_API and _g(p, "has_key", True) is False:
            continue
        if not provider_allowed(kind, bg=bg, bg_cli_allowed=bg_cli_allowed, agy_ok=agy_ok):
            continue
        wanted = forced or prof.tier
        mine = by_provider.get(str(_g(p, "id")), [])
        pinned = None if forced else owner_default(mine)
        if pinned is not None:
            # Owner đã chốt model của nguồn này: giữ nguyên model + mức Owner đã đặt (hồ sơ không ghi đè), như
            # `ModelRouter._effort` (nguồn "default" chỉ xét `models.effort`).
            m, source = pinned, "default"
            actual = catalog.tier_of(kind, str(_g(m, "model_name", ""))) or prof.tier
            effort = effort_for(kind, str(_g(m, "model_name")), _g(m, "effort"))
        else:
            picked = pick_model(kind, mine, wanted)
            if picked is None:
                continue
            (m, actual), source = picked, "profile"
            effort = effort_for(kind, str(_g(m, "model_name")), prof.effort)
        out.append({"provider_id": _g(p, "id"), "provider_kind": kind, "provider_name": _g(p, "name"),
                    "model_id": _g(m, "id"), "model_name": str(_g(m, "model_name")), "tier": actual,
                    "wanted_tier": wanted, "effort": effort,
                    "temperature": prof.temperature, "context_tokens": prof.context_tokens, "role": role,
                    "source": source})
    return out


def owner_default(models: Sequence[Any]) -> Any | None:
    """Model Owner đã chốt ("Dùng model này" = `is_default`) của MỘT nguồn: model đã bật đầu tiên (thứ tự vào: mặc định
    trước) nếu nó mang cờ `is_default`; không thì None. Cùng luật với phần đầu chuỗi của `ModelRouter._chain`."""
    for m in models:
        if _model_ok(m):
            return m if _g(m, "is_default", False) else None
    return None


REASON_NEED_API = "cần khoá API hoặc đăng nhập Antigravity CLI"
# core.reply, khi chưa có nguồn agy nào để nêu lý do hộp
REASON_NEED_API_OR_CLAUDE = "cần khoá API hoặc Claude Code CLI"
REASON_NO_SOURCE = "chưa có nguồn phù hợp"


def missing_reason(providers_rows: Sequence[Any], models_rows: Sequence[Any], agent_key: str,
                   bg_cli_allowed: set[str] | frozenset[str] = frozenset(),
                   agy: agy_policy.AgyVerdict | None = None) -> str:
    """v0.1.58 — vì sao `resolve()` không ra ứng viên cho vai này (hàm thuần, KHÔNG đổi `resolve` / `pick_model`).

    Gọi khi `resolve` rỗng; câu trả về nối sau "Chuẩn: " ở web. Thứ tự:
    1. có nguồn ĐƯỢC PHÉP dùng cho vai này (đúng luật F-86) nhưng 0 model ⇒ "chưa có model — bấm Kiểm tra kết nối
       ở <nguồn>" (đường tự sửa của Sếp);
    2. vai nền không có nguồn dùng được ⇒ "cần khoá API hoặc đăng nhập Antigravity CLI" (v0.1.62: Antigravity CLI
       chạy được việc nền; Claude Code CLI chỉ khi Owner cho phép nên không gợi ý ở đây);
    3. khoá chưa dùng được agy mà có nguồn Antigravity CLI đang bật ⇒ nêu đúng lý do (`agy.reason`: "Hộp riêng
       Antigravity chưa chạy / chưa được cài" cho vai phải qua hộp) thay vì gợi ý đăng nhập lại Antigravity;
    4. trả lời lại / soạn nháp (core.reply) ⇒ "cần khoá API hoặc Claude Code CLI" (chưa có nguồn Antigravity nào);
    5. còn lại ⇒ "chưa có nguồn phù hợp"."""
    role = role_of(agent_key)
    if role is None:
        return REASON_NO_SOURCE
    verdict = agy or agy_policy.decide(agent_key)
    bg = role_background(agent_key)
    agy_blocked_here = False
    has_model = {str(_g(m, "provider_id")) for m in models_rows if _model_ok(m)}
    for p in sorted(providers_rows, key=_rank_key):
        kind = str(_g(p, "kind", ""))
        kc = kind_class(kind)
        if kc is None or PROFILES[role].get(kc) is None or not _g(p, "is_enabled", True):
            continue
        if kc == KIND_API and _g(p, "has_key", True) is False:
            continue
        if not provider_allowed(kind, bg=bg, bg_cli_allowed=bg_cli_allowed, agy_ok=verdict.allowed):
            agy_blocked_here = agy_blocked_here or (kind == KIND_AGY and not verdict.allowed)
            continue
        if str(_g(p, "id")) not in has_model:
            if kc == KIND_API:   # adopt chỉ cho CLI ⇒ nguồn khoá API không có nút tự thêm model
                return f"chưa chọn model ở {_g(p, 'name') or kind}"
            return f"chưa có model — bấm Kiểm tra kết nối ở {_g(p, 'name') or kind}"
    if agy_blocked_here and verdict.reason:
        return verdict.reason
    if bg:
        return REASON_NEED_API
    if role == ROLE_REPLY:
        return REASON_NEED_API_OR_CLAUDE
    return REASON_NO_SOURCE


def profile_effort(agent_key: str, kind: str, model_name: str) -> str | None:
    """Mức suy nghĩ của hồ sơ cho (vai, loại nguồn, model); None khi vai/nguồn không có hoặc model không hỗ trợ."""
    role = role_of(agent_key)
    kc = kind_class(kind)
    prof = PROFILES[role].get(kc) if role and kc else None
    return effort_for(kind, model_name, prof.effort) if prof else None


# ─── truy vấn DB ──────────────────────────────────────────────────────────────

async def load_rows(db: AsyncSession, org_id: uuid.UUID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Nguồn sinh văn bản đang bật + model đã bật của tổ chức, đúng thứ tự chuỗi (hạng nguồn, model mặc định trước)."""
    prov = (await db.execute(text("""
        SELECT p.id, p.kind, p.name, p.is_enabled, p.failover_rank, p.auth_state, p.created_at,
               EXISTS (SELECT 1 FROM agent.provider_keys k WHERE k.provider_id = p.id AND k.is_enabled) AS has_key
        FROM agent.providers p
        WHERE p.org_id = :o AND p.is_enabled AND p.kind NOT IN ('embedding', 'system_one')
        ORDER BY p.failover_rank NULLS LAST, p.created_at"""), {"o": org_id})).all()
    mods = (await db.execute(text("""
        SELECT m.id, m.provider_id, m.model_name, m.effort, m.is_default, m.is_enabled
        FROM agent.models m JOIN agent.providers p ON p.id = m.provider_id
        WHERE p.org_id = :o AND m.is_enabled AND m.model_name NOT ILIKE '%embedding%'
        ORDER BY m.is_default DESC, m.id"""), {"o": org_id})).all()
    providers = [{"id": r.id, "kind": r.kind, "name": r.name, "is_enabled": r.is_enabled,
                  "failover_rank": r.failover_rank, "auth_state": r.auth_state,
                  "has_key": bool(r.has_key) if r.kind in API_KINDS else True} for r in prov]
    models = [{"id": r.id, "provider_id": r.provider_id, "model_name": r.model_name, "effort": r.effort,
               "is_default": r.is_default, "is_enabled": r.is_enabled} for r in mods]
    return providers, models


def role_background(agent_key: str) -> bool:
    """Vai này chạy nền khi hiển thị "Chuẩn: …" (sàng lọc, trực việc, Bản tin) — chỉ ảnh hưởng Claude Code CLI
    (F-86)."""
    return role_of(agent_key) in BACKGROUND_ROLES


async def standard_with_reasons(
        db: AsyncSession, org_id: uuid.UUID, agent_keys: Sequence[str],
) -> tuple[dict[str, dict[str, Any] | None], dict[str, str | None]]:
    """Như `standard_for` + (v0.1.58) LÝ DO khi một vai chưa có model chuẩn (`missing_reason`; vai đã có ⇒ None).
    Mỗi khoá được hỏi `agy_policy.verdicts_for` (agent ngoại giao tra DB một lần, trạng thái hộp đọc một lần) — khoá
    phải qua hộp mà hộp chưa chạy không nhận Antigravity CLI làm model chuẩn. Hiển thị "Chuẩn: …" là mặt nhìn của Owner
    nên `owner=True`."""
    from gh.providers.router import background_cli_allowed

    providers, models = await load_rows(db, org_id)
    bg_cli = await background_cli_allowed(db, org_id)
    verdicts = await agy_policy.verdicts_for(db, org_id, list(agent_keys))
    std: dict[str, dict[str, Any] | None] = {}
    why: dict[str, str | None] = {}
    for key in agent_keys:
        cands = resolve(providers, models, key, background=role_background(key), bg_cli_allowed=bg_cli,
                        agy=verdicts[key])
        std[key] = cands[0] if cands else None
        why[key] = None if cands else missing_reason(providers, models, key, bg_cli, verdicts[key])
    return std, why


async def standard_for(db: AsyncSession, org_id: uuid.UUID,
                       agent_keys: Sequence[str]) -> dict[str, dict[str, Any] | None]:
    """Model hồ sơ tiêu chuẩn đang phủ từng khoá agent (ứng viên đầu tiên) — dùng cho "Chuẩn: <model> (tự chọn)" ở
    Bộ não AI / API & Model và mục Về mặc định. Khoá không có nguồn phù hợp ⇒ None."""
    return (await standard_with_reasons(db, org_id, agent_keys))[0]


def role_temperature(agent_key: str) -> float | None:
    """Nhiệt độ của hồ sơ vai (bảng A1; mọi loại nguồn của một vai cùng nhiệt độ). Khoá lạ ⇒ None (bên gọi tự chọn)."""
    role = role_of(agent_key)
    temps = {p.temperature for p in PROFILES[role].values()} if role else set()
    return temps.pop() if len(temps) == 1 else None


async def owner_pinned(db: AsyncSession, org_id: uuid.UUID, agent_key: str) -> bool:
    """Sếp đã GHIM model cho khoá này? — dòng `agent.bindings`, hoặc nguồn chạy đầu tiên đang giữ model Sếp đã chốt
    ("Dùng model này"). Khi đã ghim, bên gọi KHÔNG tự ép tầng (J3 của Gen) vì ép tầng đẩy model của Sếp xuống cuối chuỗi
    (v0.1.66, F006)."""
    if (await db.execute(text("SELECT 1 FROM agent.bindings WHERE org_id = :o AND agent_key = :k"),
                         {"o": org_id, "k": agent_key})).first() is not None:
        return True
    std = (await standard_with_reasons(db, org_id, [agent_key]))[0].get(agent_key)
    return std is not None and std["source"] == "default"


def standard_public(c: dict[str, Any] | None) -> dict[str, Any] | None:
    """Ứng viên → khối JSON cho web (chỉ chuỗi/số/null — web không render object)."""
    if c is None:
        return None
    return {"model_name": str(c["model_name"]), "provider_name": str(c["provider_name"]), "tier": str(c["tier"]),
            "tier_label": TIER_LABEL[str(c["tier"])], "effort": c["effort"], "temperature": float(c["temperature"]),
            "context_tokens": int(c["context_tokens"])}


def standard_text(c: dict[str, Any] | None, reason: str | None = None) -> str:
    """"Chuẩn: <model> (tự chọn)" hoặc "Chuẩn: <lý do>" (v0.1.58: lý do cụ thể từ `missing_reason`)."""
    return f"Chuẩn: {reason or REASON_NO_SOURCE}" if c is None else f"Chuẩn: {c['model_name']} (tự chọn)"


async def choice_options(db: AsyncSession, org_id: uuid.UUID, *, owner: bool = True) -> dict[str, Any]:
    """Tầng + mức suy nghĩ mà khung chat (G3) được phép mời người hỏi chọn — hợp đồng G1 → G3.

    `{'tiers': [{'tier': 'auto'|'fast'|'balanced'|'deep', 'available': bool, 'efforts': [...]}]}`; 'deep' ↔ tầng
    'strong'. Một tầng `available` khi có nguồn DÙNG ĐƯỢC (đang bật; khoá API có khoá; CLI chưa hết hạn đăng nhập) có
    model đã bật ở đúng tầng đó. v0.1.62: Antigravity CLI không phụ thuộc nội dung bên ngoài (không còn `tainted`).
    `owner=False` (người hỏi là nhân viên) ⇒ Gen của nhân viên chạy agy qua hộp (`agy_policy.verdict_for`): hộp không
    chạy thì Antigravity CLI bị loại và mọi tầng chỉ tính nguồn khác. 'auto' (Tự động, chuẩn) khả dụng khi có bất kỳ
    nguồn nào. `efforts` ⊆ low/medium/high mà các model CLI của tầng ấy hỗ trợ (khoá API: rỗng)."""
    providers, models = await load_rows(db, org_id)
    agy_ok = (await agy_policy.verdict_for(db, org_id, ROLE_GEN, owner=owner)).allowed
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for m in models:
        by_provider.setdefault(str(m["provider_id"]), []).append(m)
    avail: dict[str, bool] = dict.fromkeys(TIER_ORDER, False)
    efforts: dict[str, set[str]] = {t: set() for t in TIER_ORDER}
    any_source = False
    for p in providers:
        kind = str(p["kind"])
        if kind == KIND_AGY and not agy_ok:
            continue
        if kind in API_KINDS and not p["has_key"]:
            continue
        if kind in CLI_KINDS and p.get("auth_state") == "expired":
            continue
        for m in by_provider.get(str(p["id"]), []):
            t = catalog.tier_of(kind, str(m["model_name"]))
            if t is None:
                continue
            any_source = True
            avail[t] = True
            efforts[t] |= set(allowed_efforts(kind, str(m["model_name"]))) & set(CHOICE_EFFORTS)
    rows: list[dict[str, Any]] = [{"tier": "auto", "available": any_source, "efforts": []}]
    for name, tier in (("fast", "fast"), ("balanced", "balanced"), ("deep", "strong")):
        rows.append({"tier": name, "available": avail[tier], "efforts": catalog.sort_efforts(efforts[tier])})
    return {"tiers": rows}
