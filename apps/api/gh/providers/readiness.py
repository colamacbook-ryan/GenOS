"""MỘT hàm "nguồn AI dùng được" (v0.1.63, gói B2) — nguồn sự thật chung cho mọi nơi hỏi "Gen có trả lời được không?".

Trước B2 mỗi nơi tự hiểu một kiểu: bước 4 "Việc thiết lập tiếp" chỉ đếm dòng model (không xét khoá / phiên đăng nhập),
dòng "nguồn AI" của "Việc Sếp cần làm" tin lượt gọi cũ dù nguồn đã tắt / bị gỡ khoá, nút Kiểm tra chỉ thử `ready[0]`.
Từ nay tất cả hỏi module này (chỉ ĐỌC DB, không gọi nhà cung cấp, không tốn hạn mức):

    usable_sources(db, org_id, role=None) -> list[dict]
        Các nguồn sinh văn bản DÙNG ĐƯỢC ngay, theo đúng thứ tự chuỗi (`failover_rank`, rồi `created_at`). Mỗi phần tử
        {provider_id, name, kind, rank, model_id, model_name, models} — `model_*` là model đầu của nguồn (mặc định
        trước). `role` = khoá agent (`core.gen`, `core.reply`, `core.refinery`, `agent:<uuid>`…): có thì áp
        thêm luật của
        vai đó (agy theo `agy_policy.decide`, Claude Code CLI theo luật việc nền F-86, và hộp riêng — xem
        dưới); bỏ trống
        = "có nguồn nào sinh văn bản được không", không loại nguồn theo vai.
    ai_ready(db, org_id) -> {ok, reason_code, reason_vi, fix_to}
        `ok` khi có ít nhất một nguồn dùng được cho Gen của Sếp (`core.gen`, Owner). Không ok thì `reason_code` là MỘT
        trong `REASON_CODES`, `reason_vi` là câu tiếng Việt có dấu nói đúng cái thiếu, `fix_to` là đường dẫn web để sửa.
    source_states(db, org_id, role=None) -> list[dict]
        Trạng thái TỪNG nguồn (kể cả nguồn không dùng được): {…như trên, usable, blocker, cli,
        ready_to_probe}. `blocker`
        ∈ REASON_CODES hoặc "policy" (vai này không được dùng loại nguồn đó) hoặc None.

Mã lý do (`reason_code`): no_source (chưa có nguồn nào) · disabled (có nhưng tắt hết) · no_key (nguồn khoá API chưa có
khoá bật, hoặc nguồn CLI chưa đăng nhập) · cli_expired (phiên CLI hết hạn) · no_model (có khoá / phiên nhưng 0 model
bật) · error (lần kiểm tra thật gần nhất của nguồn CLI báo lỗi, hoặc không đọc được trạng thái) · box_down (nguồn
Antigravity bị hộp riêng chặn vì hộp chưa chạy).

Khi nhiều nguồn cùng hỏng, báo lý do GẦN dùng được nhất (ít việc phải làm nhất): box_down < no_model < error <
cli_expired < no_key < disabled < no_source — để Sếp thấy bước sửa ngắn nhất.

─── hộp riêng Antigravity (`box_down`) — nối với chính sách CUỐI của H2 ───────────────────────────────────────────
Chỗ chạy agy theo vai nằm ở `gh.providers.placement` (luật thuần), quyết định cuối ở `agy_policy.decide` — cùng điểm
mà bộ định tuyến dùng. Readiness hỏi `BoxGate`:
    required(role, agent_key, *, owner=True, purpose=None) -> bool
        vai này có BẮT BUỘC chạy agy qua hộp không (đúng chữ ký `placement.box_required`)
    await state() -> str      "ok" | "down" | "not_installed"   (`agybox.client.box_status()["state"]`)
Mặc định `SoftBoxGate` gọi thẳng hai hàm đó. Vai bắt buộc hộp mà hộp không `ok` ⇒ nguồn Antigravity bị chặn bằng
`agy_policy.decide(..., box=state)` (lý do tiếng Việt của `decide`, mã `box_down`; "chưa cài" cũng là `box_down` nhưng
câu nói đúng là chưa cài); vai không cần hộp ⇒ không bao giờ bị hộp chặn — nên kết luận ở đây LUÔN khớp với việc bộ
định tuyến sẽ làm. `agent:<id>` ngoại giao được tra từ DB (`agy_policy.diplomatic_agent_ids`) rồi truyền cho cổng bằng
`placement.AgentRef`. Test muốn tiêm riêng: truyền `box=` (kwarg) vào ba hàm trên, hoặc `set_box_gate(gate)` cho cả
tiến trình. Chỉ nguồn Antigravity CLI bị hộp chặn; nguồn khác không bao giờ.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.providers import agy_policy, placement

log = logging.getLogger("gh.providers.readiness")

KIND_AGY = agy_policy.KIND_AGY
KIND_CLAUDE = "claude_code_cli"
CLI_KINDS = (KIND_AGY, KIND_CLAUDE)
EXCLUDED_KINDS = ("embedding", "system_one")        # không sinh văn bản (Jev, embedding)

NO_SOURCE, NO_KEY, CLI_EXPIRED, DISABLED, NO_MODEL, ERROR, BOX_DOWN = (
    "no_source", "no_key", "cli_expired", "disabled", "no_model", "error", "box_down")
REASON_CODES = (NO_SOURCE, NO_KEY, CLI_EXPIRED, DISABLED, NO_MODEL, ERROR, BOX_DOWN)
POLICY = "policy"                                    # blocker nội bộ: vai này không được dùng loại nguồn đó

#: Vai mặc định của `ai_ready`: Gen của Sếp (Owner) — việc cốt lõi mà "có nguồn AI" phải bảo đảm.
READY_ROLE = "core.gen"

#: v0.1.68 (F029): MỘT đích duy nhất để thêm / sửa nguồn AI — màn Thiết lập nguồn AI (dán khoá, đăng nhập Google /
#: Claude Code, gọi thử, chọn model đều làm trọn ở đó). Trước đây có nơi dẫn "Kết nối › Bộ não AI" (không có chỗ nhập
#: khoá) và nơi dẫn "Cài đặt › Bộ não AI" (chỉ đọc). Riêng nguồn đã TẮT thì bật lại ở API & Model (công tắc từng nguồn).
FIX_AI = "/setup?buoc=nguon-ai"
FIX_API = "/api"
FIX_TO = {NO_SOURCE: FIX_AI, DISABLED: FIX_API, NO_KEY: FIX_AI, CLI_EXPIRED: FIX_AI,
          NO_MODEL: FIX_AI, ERROR: FIX_AI, BOX_DOWN: FIX_AI}

#: Càng nhỏ càng gần dùng được (xem docstring module).
_CLOSENESS = {BOX_DOWN: 0, NO_MODEL: 1, ERROR: 2, CLI_EXPIRED: 3, NO_KEY: 4, DISABLED: 5}

CLI_LABEL = {KIND_AGY: "Google (Antigravity CLI)", KIND_CLAUDE: "Claude Code"}

REASON_NO_SOURCE = ("Chưa có nguồn AI nào — thêm khoá API hoặc đăng nhập Google / Claude Code ở màn Thiết lập nguồn AI "
                    "rồi bấm Kiểm tra")
REASON_DISABLED = "Mọi nguồn AI đang tắt — bật lại một nguồn ở API & Model rồi bấm Kiểm tra"
REASON_BOX_DOWN = ("Hộp riêng Antigravity chưa chạy — nguồn Google (Antigravity) tạm không dùng được cho việc này; "
                   "bật lại hộp hoặc thêm khoá API ở màn Thiết lập nguồn AI")


class BoxGate(Protocol):
    """Giao diện hộp riêng Antigravity cho readiness / test (xem docstring module)."""

    def required(self, role: str | None, agent_key: Any, *, owner: bool = True, purpose: str | None = None) -> bool: ...

    async def state(self) -> str: ...


class SoftBoxGate:
    """Mặc định: luật thật — `placement.box_required` và `agybox.client.box_status` (lỗi ⇒ đóng an toàn)."""

    def required(self, role: str | None, agent_key: Any, *, owner: bool = True, purpose: str | None = None) -> bool:
        try:
            if role is None:
                # Review v0.1.63 (P3): khoá lạ (không có hồ sơ vai) ⇒ bộ định tuyến xếp QUA HỘP (`placement.UNKNOWN_
                # PLACEMENT`, đóng an toàn) ⇒ readiness cũng coi là cần hộp, để kết luận khớp việc bộ định tuyến làm.
                key = agent_key if isinstance(agent_key, str) else ""
                return placement.box_required(key, None, owner=owner, purpose=purpose)
            return placement.box_required(role, agent_key, owner=owner, purpose=purpose)
        except Exception:  # noqa: BLE001 — hỏi hộp là phần thêm; lỗi không được làm sập câu trả lời "dùng được không"
            log.warning("placement.box_required lỗi (%s) — coi như không bắt buộc hộp", role, exc_info=True)
            return False

    async def state(self) -> str:
        return await agy_policy.current_box_state()


_box_gate: BoxGate = SoftBoxGate()


def set_box_gate(gate: BoxGate | None) -> None:
    """Đặt cổng hộp cho cả tiến trình (H2 gọi khi khởi động nếu muốn thay mặc định); None = về `SoftBoxGate`."""
    global _box_gate
    _box_gate = gate if gate is not None else SoftBoxGate()


_SQL_PROVIDERS = """
    SELECT p.id, p.kind, p.name, p.is_enabled, p.failover_rank, p.auth_state, p.created_at,
           EXISTS (SELECT 1 FROM agent.provider_keys k WHERE k.provider_id = p.id AND k.is_enabled) AS has_key,
           EXISTS (SELECT 1 FROM agent.cli_profiles c WHERE c.provider_id = p.id AND c.is_active
                   AND c.token_enc IS NOT NULL) AS has_session,
           (SELECT c.expires_at FROM agent.cli_profiles c WHERE c.provider_id = p.id AND c.is_active
            AND c.token_enc IS NOT NULL LIMIT 1) AS session_expires_at
    FROM agent.providers p
    WHERE p.org_id = :o AND p.kind NOT IN ('embedding', 'system_one')
    ORDER BY p.failover_rank NULLS LAST, p.created_at, p.id"""

_SQL_MODELS = """
    SELECT m.id, m.provider_id, m.model_name
    FROM agent.models m JOIN agent.providers p ON p.id = m.provider_id
    WHERE p.org_id = :o AND m.is_enabled AND m.model_name NOT ILIKE '%embedding%'
    ORDER BY m.is_default DESC, m.id"""


async def _session_expired(db: AsyncSession, org_id: uuid.UUID, kind: str, auth_state: str,
                           expires_at: datetime | None) -> bool:
    """Phiên CLI đã hết hạn thật: lượt gọi thật gần nhất báo lỗi xác thực, hoặc token quá hạn mà không làm mới được
    (cùng quy tắc `cli.profile_state` mà thẻ tài khoản dùng — một sự thật). Chỉ giải mã token khi `expires_at` đã
    qua."""
    if auth_state == "expired":
        return True
    if expires_at is None or expires_at > datetime.now(UTC):
        return False
    from gh.providers import cli as climod

    try:
        return any(p["active"] and p["state"] == "expired" for p in await climod.profiles(db, org_id, kind))
    except Exception:  # noqa: BLE001 — không giải mã được thì không kết luận "hết hạn"
        log.warning("Không đọc được hồ sơ CLI %s để xét hạn phiên", kind, exc_info=True)
        return False


async def source_states(db: AsyncSession, org_id: uuid.UUID, role: str | None = None, *, owner: bool = True,
                        box: BoxGate | None = None) -> list[dict[str, Any]]:
    """Trạng thái từng nguồn sinh văn bản của tổ chức theo thứ tự chuỗi (xem docstring module). `ready_to_probe`: có
    thể bấm "Kiểm tra" để chữa tiếp — nguồn đang bật, đúng luật vai, đã có khoá / phiên chưa hết hạn, hộp không chặn
    (kể cả khi còn thiếu model: gọi thử nguồn CLI sẽ tự lưu model chạy được — `router.adopt_probed_model`)."""
    from gh.defaults import profiles
    from gh.providers import router

    provs = (await db.execute(text(_SQL_PROVIDERS), {"o": org_id})).all()
    models: dict[Any, list[Any]] = {}
    for m in (await db.execute(text(_SQL_MODELS), {"o": org_id})).all():
        models.setdefault(m.provider_id, []).append(m)

    gate = box or _box_gate
    verdict = agy_policy.ALLOWED                 # chính sách CUỐI cho nguồn Antigravity (agy_policy.decide)
    if role:
        aid = agy_policy.agent_id_of(role)
        dip = aid is not None and aid in await agy_policy.diplomatic_agent_ids(db, org_id, only=aid)
        ref: Any = placement.AgentRef(diplomatic=dip) if aid is not None else role
        if any(p.kind == KIND_AGY for p in provs) and gate.required(profiles.role_of(role), ref, owner=owner):
            verdict = agy_policy.decide(role, owner=owner, diplomatic=dip, box=await gate.state())
    bg = bool(role) and profiles.role_background(role or "")
    bg_cli = await router.background_cli_allowed(db, org_id) if bg else set()
    box_blocked = not verdict.allowed and verdict.code in (agy_policy.CODE_BOX_DOWN, agy_policy.CODE_BOX_NOT_INSTALLED)

    out: list[dict[str, Any]] = []
    for p in provs:
        cli = p.kind in CLI_KINDS
        ms = models.get(p.id, [])
        blocker: str | None = None
        policy_reason: str | None = None
        if not p.is_enabled:
            blocker = DISABLED
        elif p.kind == KIND_AGY and not verdict.allowed and not box_blocked:
            blocker, policy_reason = POLICY, verdict.reason
        elif p.kind == KIND_CLAUDE and bg and p.kind not in bg_cli:
            blocker, policy_reason = POLICY, router.BACKGROUND_CLI_REASON
        elif (p.has_session if cli else p.has_key) is False:
            blocker = NO_KEY
        elif cli and await _session_expired(db, org_id, p.kind, p.auth_state, p.session_expires_at):
            blocker = CLI_EXPIRED
        elif cli and p.auth_state == "error":
            blocker = ERROR      # nguồn CLI: lượt gọi thật tự đưa `auth_state` về 'ok' nên 'error' là sự thật hiện tại
        elif not ms:
            blocker = NO_MODEL
        elif p.kind == KIND_AGY and box_blocked:
            blocker, policy_reason = BOX_DOWN, verdict.reason
        # NO_MODEL chỉ chữa được bằng gọi thử khi là nguồn CLI (tự lưu model chạy được); nguồn khoá API thiếu model thì
        # phải chọn model, gọi thử không sửa gì.
        probe_ok = blocker in (None, ERROR) or (blocker == NO_MODEL and cli)
        out.append({"provider_id": str(p.id), "name": str(p.name), "kind": str(p.kind), "cli": cli,
                    "rank": None if p.failover_rank is None else int(p.failover_rank),
                    "model_id": str(ms[0].id) if ms else None, "model_name": str(ms[0].model_name) if ms else None,
                    "models": len(ms), "usable": blocker is None, "blocker": blocker, "policy_reason": policy_reason,
                    "ready_to_probe": probe_ok})
    return out


async def usable_sources(db: AsyncSession, org_id: uuid.UUID, role: str | None = None, *, owner: bool = True,
                         box: BoxGate | None = None) -> list[dict[str, Any]]:
    """Các nguồn dùng được ngay (xem docstring module), thứ tự chuỗi."""
    return [{k: s[k] for k in ("provider_id", "name", "kind", "rank", "model_id", "model_name", "models")}
            for s in await source_states(db, org_id, role, owner=owner, box=box) if s["usable"]]


def _reason_vi(code: str, s: dict[str, Any] | None) -> str:
    if code == NO_SOURCE:
        return REASON_NO_SOURCE
    if code == DISABLED:
        return REASON_DISABLED
    if code == BOX_DOWN:
        return (s or {}).get("policy_reason") or REASON_BOX_DOWN
    assert s is not None
    label = CLI_LABEL.get(s["kind"], "")
    name = s["name"]
    if code == NO_KEY:
        if s["cli"]:
            return f"Chưa đăng nhập {label} — bấm Đăng nhập trước ở màn Thiết lập nguồn AI"
        return f"Nguồn AI «{name}» chưa có khoá API đang bật — thêm khoá ở màn Thiết lập nguồn AI rồi bấm Kiểm tra"
    if code == CLI_EXPIRED:
        return f"Phiên đăng nhập {label} đã hết hạn — đăng nhập lại ở màn Thiết lập nguồn AI"
    if code == NO_MODEL:
        return (f"Nguồn AI «{name}» chưa có model nào được bật — bấm Kiểm tra kết nối để Gen tự lấy model, "
                "hoặc chọn model ở Thiết lập nguồn AI")
    return f"Lần kiểm tra gần nhất của nguồn AI «{name}» báo lỗi — bấm Kiểm tra để xem lỗi chi tiết"


def explain(states: list[dict[str, Any]]) -> dict[str, Any]:
    """Từ trạng thái các nguồn → {ok, reason_code, reason_vi, fix_to, source} (hàm thuần; `source` = nguồn bị chọn để
    giải thích, None khi ok / không có nguồn)."""
    if any(s["usable"] for s in states):
        return {"ok": True, "reason_code": None, "reason_vi": None, "fix_to": None, "source": None}
    if not states:
        return {"ok": False, "reason_code": NO_SOURCE, "reason_vi": REASON_NO_SOURCE, "fix_to": FIX_TO[NO_SOURCE],
                "source": None}
    blocked = [s for s in states if s["blocker"] not in (POLICY, DISABLED)]
    if blocked:
        best = min(blocked, key=lambda s: _CLOSENESS[s["blocker"]])        # min giữ nguồn đứng trước (chuỗi) khi hoà
        code = best["blocker"]
        return {"ok": False, "reason_code": code, "reason_vi": _reason_vi(code, best), "fix_to": FIX_TO[code],
                "source": best}
    if any(s["blocker"] == DISABLED for s in states):
        return {"ok": False, "reason_code": DISABLED, "reason_vi": REASON_DISABLED, "fix_to": FIX_TO[DISABLED],
                "source": None}
    # Chỉ còn nguồn bị luật vai chặn (vd agy với core.reply): nói đúng luật đó, không gợi ý đăng nhập lại.
    why = next((s["policy_reason"] for s in states if s["policy_reason"]), None)
    return {"ok": False, "reason_code": NO_SOURCE, "fix_to": FIX_TO[NO_SOURCE],
            "reason_vi": f"{why}. Thêm nguồn khác ở màn Thiết lập nguồn AI" if why else REASON_NO_SOURCE,
            "source": None}


async def ai_ready(db: AsyncSession, org_id: uuid.UUID, role: str = READY_ROLE, *, owner: bool = True,
                   box: BoxGate | None = None) -> dict[str, Any]:
    """Gen (mặc định `core.gen` của Owner) có ít nhất một nguồn AI dùng được không? → {ok, reason_code, reason_vi,
    fix_to}. Lỗi đọc trạng thái KHÔNG ném: trả `ok False` + `reason_code='error'` (nói thật là chưa kiểm tra được)."""
    try:
        # Review v0.1.68 (P3): savepoint — lỗi SQL bị nuốt ở dưới KHÔNG được làm hỏng giao dịch của người gọi (Postgres
        # đánh dấu cả giao dịch hỏng nếu không có savepoint; `owner/brief.ai_ready` đã làm đúng như vậy).
        async with db.begin_nested():
            states = await source_states(db, org_id, role, owner=owner, box=box)
        verdict = explain(states)
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được trạng thái nguồn AI (%s)", org_id, exc_info=True)
        return {"ok": False, "reason_code": ERROR, "fix_to": FIX_AI,
                "reason_vi": "Chưa kiểm tra được nguồn AI lúc này — thử lại sau ít phút hoặc bấm Kiểm tra"}
    return {k: verdict[k] for k in ("ok", "reason_code", "reason_vi", "fix_to")}


class FakeBox:
    """Cổng hộp giả cho test / dev: `FakeBox(required=True, state="down")`."""

    def __init__(self, required: bool | Callable[[str | None, Any], bool] = True, state: str = "ok") -> None:
        self._required, self._state = required, state

    def required(self, role: str | None, agent_key: Any, *, owner: bool = True, purpose: str | None = None) -> bool:
        return self._required(role, agent_key) if callable(self._required) else bool(self._required)

    async def state(self) -> str:
        return self._state


__all__ = ["REASON_CODES", "READY_ROLE", "BoxGate", "SoftBoxGate", "FakeBox", "set_box_gate", "source_states",
           "usable_sources", "ai_ready", "explain"]
