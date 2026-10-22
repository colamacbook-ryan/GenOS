"""Sổ mặc định (v0.1.55, G1 — "Về mặc định" / "Chế độ tiêu chuẩn").

Một sổ DUY NHẤT trong mã liệt kê mọi cài đặt có "mặc định tiêu chuẩn" mà Owner có thể đổi và muốn quay về. Mỗi mục:
`{key, label, scope 'org'|'user', group, resettable, read(db, org, user) → State, reset(db, org, user)}`.

- `customized` được TÍNH RA từ dữ liệu thật, không lưu cờ riêng: cài đặt có khoá và khác mặc định; dòng gán model có
  tồn tại; tuỳ chọn Gen hướng dẫn có dòng khác mặc định. Cài xong chưa đụng gì ⇒ không mục nào "Đã đổi".
- `read` chỉ trả CHỮ cho người đọc (`default_text`, `current_text`) — web không bao giờ nhận giá trị thô dạng object.
- `reset` KHÔNG BAO GIỜ chạm: `agent.provider_keys`, `agent.providers`, `core.users` (mã PIN, mật khẩu),
  `agent.hub_links`, `ops.notify_channels`, `core.social_accounts`, ranh giới cứng, danh tính tổ chức.
  Từng mục có quyết định riêng: gen giữ nguyên công tắc `enabled` (chỉ vai trò + số ngày giữ); backup GHI LẠI mặc
  định (xoá khoá = tắt sao lưu); ai_cost chỉ xoá trần chi phí và GIỮ bảng giá Owner đã nhập; coach xoá dòng của CHÍNH
  người gọi; jev.preset chỉ hiển thị (đổi nguồn model cần PIN `ai.route_change` ở thẻ Jev); kho_label (v0.1.57) chỉ
  xoá khoá tên Kho tuỳ chỉnh (không đụng địa chỉ/token Gen-hub).
- Mọi lần ghi ở `gh.defaults.routes` chỉ ghi vào Action Log khoá (key | 'all' | 'apply_standard'), không ghi giá trị
  cài đặt.
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.defaults import profiles

#: Dòng gán lõi mà "Áp model chuẩn theo vai" xoá (khoá/nguồn giữ nguyên; agent:* giữ nguyên).
CORE_BINDING_KEYS = ("core.gen", "core.briefing", "core.refinery", "core.reply", "core.owner_reply", "core.support")
#: Khoá mục "Mức tự trị của tổ chức": nâng mức tự trị là thay đổi liên quan an toàn ⇒ Về mặc định MỘT mục này cũng đòi
#: phiên mã PIN (như tất cả); mục hiện trong sổ để hộp Xác nhận nói thẳng, đếm vào "Đã đổi N mục".
AUTONOMY_KEY = "autonomy"
APPLY_STANDARD_TO = "/system?tab=brain#chuan"
BRAIN_TAB = "/system?tab=brain"

GEN_DEFAULTS: dict[str, Any] = {"roles": ["owner"], "retention_days": 90}
SCHEDULE_DEFAULTS: dict[str, Any] = {"interval_seconds": 900, "count_threshold": 500, "batch_size": 250,
                                     "min_confidence": 0.6}
AI_COST_RESET_KEYS = ("daily_budget_vnd",)
PREF_FIELDS = ("enabled", "bell", "lessons_per_day", "quiet_start", "quiet_end", "snooze_until")


@dataclass(frozen=True)
class State:
    default_text: str
    current_text: str
    customized: bool


Reader = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[State]]
Resetter = Callable[[AsyncSession, uuid.UUID, uuid.UUID], Awaitable[None]]


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    scope: str          # 'org' | 'user'
    group: str
    resettable: bool
    read: Reader
    reset: Resetter


# ─── chữ hiển thị ─────────────────────────────────────────────────────────────

def _vn_number(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _onoff(v: Any) -> str:
    return "bật" if v else "tắt"


def _gen_text(cfg: dict[str, Any]) -> str:
    roles = [str(r) for r in (cfg.get("roles") or [])]
    who = "chỉ Owner" if roles == ["owner"] else ("vai trò: " + ", ".join(roles) if roles else "chưa có vai trò nào")
    return f"Gen dùng được cho {who} · giữ hội thoại {cfg.get('retention_days')} ngày"


def _triage_text(cfg: dict[str, Any]) -> str:
    return (f"Lọc tin {_onoff(cfg.get('enabled'))} · ngưỡng điểm {cfg.get('min_score')} · "
            f"{'có' if cfg.get('use_jev') else 'không'} dùng Jev")


_FREQ = {"daily": "hằng ngày", "weekly": "hằng tuần", "monthly": "hằng tháng"}


def _backup_text(cfg: dict[str, Any] | None) -> str:
    if not cfg:
        return "Chưa đặt lịch sao lưu tự động"
    from gh import backup  # nhập muộn: gh.backup kéo arq/psycopg, không cần cho mọi lần nạp registry

    where = "trên máy chủ này" if cfg.get("destination") == "local" else str(cfg.get("destination"))
    # Vòng đời thật là GFS; `retention_count` của cấu hình KHÔNG dùng nên không in ra (F108).
    keep = (f"giữ {backup.DAILY_KEEP} bản theo ngày, {backup.WEEKLY_KEEP} theo tuần, "
            f"{backup.MONTHLY_KEEP} theo tháng")
    return (f"Sao lưu {_FREQ.get(str(cfg.get('frequency')), str(cfg.get('frequency')))} lúc {cfg.get('time_of_day')} · "
            f"{keep} · lưu {where}")


def _ai_cost_text(budget: int | None) -> str:
    return "Không đặt trần chi phí AI" if budget is None else f"Trần chi phí AI {_vn_number(budget)} ₫ mỗi ngày"


def _coach_text(c: dict[str, Any]) -> str:
    out = (f"Gen hướng dẫn {_onoff(c['enabled'])} · chuông {_onoff(c['bell'])} · {c['lessons_per_day']} bài mỗi ngày · "
           f"yên lặng {c['quiet_start']}h–{c['quiet_end']}h")
    return out + (" · đang hoãn" if c.get("snooze_until") else "")


def _schedule_text(c: dict[str, Any]) -> str:
    mins = int(c["interval_seconds"]) // 60
    conf = f"{float(c['min_confidence']):g}".replace(".", ",")
    return (f"Sàng lọc mỗi {mins} phút hoặc khi đủ {c['count_threshold']} tin · mỗi lô {c['batch_size']} tin · "
            f"độ tin cậy tối thiểu {conf}")


def _differs(cur: dict[str, Any], default: dict[str, Any]) -> bool:
    return any(cur.get(k) != v for k, v in default.items())


# ─── từng mục: gen ────────────────────────────────────────────────────────────

async def _read_gen(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> State:
    from gh.gen import store

    cur = await store.get_settings(db, org)
    return State(_gen_text(GEN_DEFAULTS), _gen_text(cur), _differs(cur, GEN_DEFAULTS))


async def _reset_gen(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> None:
    # KHÔNG đụng `enabled` (công tắc Gen của Sếp): chỉ vai trò + số ngày giữ hội thoại; chưa có khoá `gen` ⇒ không tạo.
    await db.execute(text("""
        UPDATE core.organizations
        SET settings = jsonb_set(settings, '{gen}', (settings->'gen') || CAST(:d AS jsonb), true)
        WHERE id = :o AND jsonb_typeof(settings->'gen') = 'object'"""),
                     {"o": org, "d": json.dumps(GEN_DEFAULTS)})


# ─── triage ───────────────────────────────────────────────────────────────────

def _triage_defaults() -> dict[str, Any]:
    from gh.refinery import triage

    return dict(triage.DEFAULTS)   # nguồn duy nhất — gói Jev/lọc tin (G4) đổi mặc định ở đó


async def _read_triage(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> State:
    from gh.refinery import triage

    default = _triage_defaults()
    cur = await triage.get_settings(db, org)
    return State(_triage_text(default), _triage_text(cur), _differs(cur, default))


async def _reset_triage(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> None:
    await db.execute(text("UPDATE core.organizations SET settings = settings - 'triage' WHERE id = :o"), {"o": org})


# ─── backup ───────────────────────────────────────────────────────────────────

def _backup_defaults() -> dict[str, Any]:
    from gh.setup.routes import DEFAULT_BACKUP

    return dict(DEFAULT_BACKUP)


async def _backup_cfg(db: AsyncSession, org: uuid.UUID) -> dict[str, Any] | None:
    raw = (await db.execute(text("SELECT settings->'backup' FROM core.organizations WHERE id = :o"),
                            {"o": org})).scalar_one_or_none()
    return raw if isinstance(raw, dict) else None


async def _read_backup(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> State:
    default = _backup_defaults()
    cur = await _backup_cfg(db, org)
    # Chưa có khoá = chưa từng đặt lịch (không phải "Đã đổi"); có khoá mà khác mặc định = Đã đổi.
    return State(_backup_text(default), _backup_text(cur), cur is not None and _differs(cur, default))


async def _reset_backup(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> None:
    # KHÔNG xoá khoá: xoá `settings->'backup'` = TẮT sao lưu (gh.health / gh.backup coi "có khoá" là đã cấu hình).
    # Ghi lại mặc định (hằng ngày 02:00, giữ 7 bản, đích máy chủ này).
    await db.execute(text("""
        UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{backup}',
                                                          CAST(:d AS jsonb), true) WHERE id = :o"""),
                     {"o": org, "d": json.dumps(_backup_defaults())})


# ─── ai_cost ──────────────────────────────────────────────────────────────────

async def _read_ai_cost(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> State:
    from gh import ai_cost

    budget = await ai_cost.get_budget(db, org)
    return State(_ai_cost_text(None), _ai_cost_text(budget), budget is not None)


async def _reset_ai_cost(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> None:
    # Chỉ xoá trần chi phí/cảnh báo; bảng giá Owner đã nhập (agent.model_prices) GIỮ NGUYÊN, không đặt giá mặc định.
    for k in AI_COST_RESET_KEYS:
        await db.execute(text("""
            UPDATE core.organizations
            SET settings = jsonb_set(settings, '{ai_cost}', (settings->'ai_cost') - CAST(:k AS text), true)
            WHERE id = :o AND jsonb_typeof(settings->'ai_cost') = 'object'"""), {"o": org, "k": k})


# ─── coach (theo người gọi) ───────────────────────────────────────────────────

async def _coach_row(db: AsyncSession, user: uuid.UUID) -> dict[str, Any]:
    from gh.gen.coach import store

    p = await store.get_prefs(db, user)     # chưa có dòng ⇒ mặc định, KHÔNG ghi
    return {k: getattr(p, k) for k in PREF_FIELDS}


async def _read_coach(db: AsyncSession, _org: uuid.UUID, user: uuid.UUID) -> State:
    from gh.gen.coach.engine import Prefs

    default = {k: getattr(Prefs(), k) for k in PREF_FIELDS}
    cur = await _coach_row(db, user)
    return State(_coach_text(default), _coach_text(cur), cur != default)


async def _reset_coach(db: AsyncSession, org: uuid.UUID, user: uuid.UUID) -> None:
    # Xoá dòng tuỳ chọn của CHÍNH người gọi (không ai khác); `ensure_prefs` tạo lại dòng mặc định khi cần.
    await db.execute(text("DELETE FROM agent.gen_coach_prefs WHERE user_id = :u AND org_id = :o"),
                     {"u": user, "o": org})


# ─── refinery.schedule ────────────────────────────────────────────────────────

async def _read_schedule(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> State:
    r = (await db.execute(text("""SELECT interval_seconds, count_threshold, batch_size, min_confidence
                                  FROM refinery.schedule WHERE org_id = :o"""), {"o": org})).one_or_none()
    cur = dict(SCHEDULE_DEFAULTS) if r is None else {
        "interval_seconds": int(r.interval_seconds), "count_threshold": int(r.count_threshold),
        "batch_size": int(r.batch_size), "min_confidence": float(r.min_confidence)}
    return State(_schedule_text(SCHEDULE_DEFAULTS), _schedule_text(cur), _differs(cur, SCHEDULE_DEFAULTS))


async def _reset_schedule(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> None:
    from gh.data_api.routes import ScheduleIn, save_schedule

    await save_schedule(db, org, ScheduleIn(**SCHEDULE_DEFAULTS))


# ─── jev.preset (chỉ hiển thị) ────────────────────────────────────────────────

def _jev_defaults() -> tuple[str, str]:
    from urllib.parse import urlparse

    from gh.gen import jev

    return (str(getattr(jev, "DEFAULT_MODEL", "typesafe/jev-1.13")),
            urlparse(str(getattr(jev, "DEFAULT_BASE_URL", "https://openrouter.ai/api/v1"))).hostname or "openrouter.ai")


async def _read_jev(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> State:
    from urllib.parse import urlparse

    d_model, d_host = _jev_defaults()
    default = f"Jev dùng model {d_model} qua {d_host}"
    r = (await db.execute(text("""
        SELECT p.endpoint, (SELECT m.model_name FROM agent.models m WHERE m.provider_id = p.id AND m.is_enabled
                            ORDER BY m.id LIMIT 1) AS model_name
        FROM agent.providers p WHERE p.org_id = :o AND p.kind = 'system_one' AND p.is_enabled
        ORDER BY p.created_at LIMIT 1"""), {"o": org})).one_or_none()
    if r is None:
        return State(default, "Chưa dùng Jev", False)
    host = urlparse(r.endpoint or "").hostname or d_host    # chỉ tên máy, không bao giờ cả địa chỉ
    model = r.model_name or d_model
    return State(default, f"Jev dùng model {model} qua {host}", (model, host) != (d_model, d_host))


async def _noop(_db: AsyncSession, _org: uuid.UUID, _user: uuid.UUID) -> None:
    return None


# ─── tên Kho (v0.1.57, Nợ #30) ────────────────────────────────────────────────

KHO_LABEL_KEY = "kho_label"


def _kho_text(label: str) -> str:
    return f"Kho nối qua Gen-hub gọi là “{label}”"


async def _read_kho_label(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> State:
    from gh.hub_link import KHO_LABEL, load_kho_label

    label = await load_kho_label(db, org)
    return State(_kho_text(KHO_LABEL), _kho_text(label), label != KHO_LABEL)


async def _reset_kho_label(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> None:
    # Chỉ xoá khoá tên Kho; địa chỉ/token Gen-hub (agent.hub_links, agent.mcp_servers.auth_enc) KHÔNG đụng. Ghi chú
    # máy chủ do hệ thống tự đặt đổi theo tên mặc định (ghi chú Owner sửa tay giữ nguyên).
    from gh.hub_link import KHO_LABEL, load_kho_label
    from gh.hub_link import service as hub

    old = await load_kho_label(db, org)
    await db.execute(text("UPDATE core.organizations SET settings = settings - 'kho_label' WHERE id = :o"), {"o": org})
    if old != KHO_LABEL:
        await hub.refresh_server_note(db, org, old, KHO_LABEL)


# ─── mức tự trị của tổ chức ───────────────────────────────────────────────────

def _autonomy_text(level: int) -> str:
    from gh.chassis import policy

    return f"{policy.LEVELS.get(level, 'mức ' + str(level))} (mức {level})"


async def _org_autonomy(db: AsyncSession, org: uuid.UUID) -> int | None:
    """Mức tự trị đã LƯU của tổ chức (khoá `autonomy_level`); chưa có khoá ⇒ None."""
    raw = (await db.execute(text("SELECT settings->>'autonomy_level' FROM core.organizations WHERE id = :o"),
                            {"o": org})).scalar_one_or_none()
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


async def _read_autonomy(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> State:
    from gh.chassis import policy

    cur = await _org_autonomy(db, org)
    level = policy.DEFAULT_AUTONOMY if cur is None else cur
    return State(_autonomy_text(policy.DEFAULT_AUTONOMY), _autonomy_text(level), level != policy.DEFAULT_AUTONOMY)


async def _reset_autonomy(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> None:
    await reset_autonomy(db, org)


# ─── dòng gán model ───────────────────────────────────────────────────────────

def _effort_label(effort: str | None) -> str:
    from gh.providers.catalog import EFFORT_LABEL

    return f" · mức suy nghĩ {EFFORT_LABEL.get(effort or '', effort)}" if effort else ""


def _binding_item(key: str, label: str, row: Any, std: dict[str, Any] | None, why: str | None = None) -> Item:
    default_text = profiles.standard_text(std, why) + (
        "" if std is None or not std.get("effort") else _effort_label(str(std["effort"])))
    if row is None:
        current = default_text
    else:
        current = f"{row.model_name} ({row.provider_name}){_effort_label(row.effort)} — Sếp đã chọn"

    async def read(_db: AsyncSession, _org: uuid.UUID, _user: uuid.UUID) -> State:
        return State(default_text, current, row is not None)

    async def reset(db: AsyncSession, org: uuid.UUID, _user: uuid.UUID) -> None:
        await db.execute(text("DELETE FROM agent.bindings WHERE org_id = :o AND agent_key = :k"),
                         {"o": org, "k": key})

    return Item(f"binding:{key}", f"Model cho {label}", "org", "Gán model", True, read, reset)


async def _binding_items(db: AsyncSession, org: uuid.UUID) -> list[Item]:
    from gh.agents_api.routes import CORE_AGENT_KEYS

    rows = (await db.execute(text("""
        SELECT b.agent_key, m.model_name, p.name AS provider_name, b.effort
        FROM agent.bindings b JOIN agent.models m ON m.id = b.model_id JOIN agent.providers p ON p.id = m.provider_id
        WHERE b.org_id = :o"""), {"o": org})).all()
    bound = {r.agent_key: r for r in rows}
    agents = (await db.execute(text("SELECT id, name, archived_at FROM agent.identities WHERE org_id = :o "
                                    "ORDER BY created_at"), {"o": org})).all()
    keys: list[tuple[str, str]] = [(k, v) for k, v in CORE_AGENT_KEYS.items()]
    # Agent đã Lưu trữ (archived_at) ẩn khỏi bảng Gán model như mọi nơi khác — kể cả dòng gán còn sót (lưu trữ đã gỡ nó)
    # cũng không hiện lại thành "Agent đã xoá".
    known = {f"agent:{a.id}" for a in agents}
    keys += [(f"agent:{a.id}", str(a.name)) for a in agents if a.archived_at is None]
    # Dòng gán của agent đã bị xoá (không còn trong agent.identities) vẫn nằm trong sổ để "Về mặc định tất cả" dọn được.
    keys += [(k, "Agent đã xoá") for k in sorted(bound) if k.startswith("agent:") and k not in known]
    std, why = await profiles.standard_with_reasons(db, org, [k for k, _ in keys])
    return [_binding_item(k, label, bound.get(k), std.get(k), why.get(k)) for k, label in keys]


# ─── sổ ───────────────────────────────────────────────────────────────────────

_STATIC: tuple[Item, ...] = (
    Item("gen", "Gen — vai trò dùng và số ngày giữ hội thoại", "org", "Gen", True, _read_gen, _reset_gen),
    Item("coach", "Gen hướng dẫn — tuỳ chọn của Sếp", "user", "Gen", True, _read_coach, _reset_coach),
    Item("triage", "Lọc tin", "org", "Bộ não AI", True, _read_triage, _reset_triage),
    Item("refinery.schedule", "Lịch sàng lọc tin", "org", "Bộ não AI", True, _read_schedule, _reset_schedule),
    Item("jev.preset", "Jev — nguồn model", "org", "Bộ não AI", False, _read_jev, _noop),
    Item("ai_cost", "Trần chi phí AI", "org", "Chi phí AI", True, _read_ai_cost, _reset_ai_cost),
    Item("backup", "Lịch sao lưu", "org", "Sao lưu", True, _read_backup, _reset_backup),
    Item(AUTONOMY_KEY, "Mức tự trị của tổ chức", "org", "Gen", True, _read_autonomy, _reset_autonomy),
    Item(KHO_LABEL_KEY, "Tên Kho nối qua Gen-hub", "org", "Kết nối", True, _read_kho_label, _reset_kho_label),
)


async def items(db: AsyncSession, org_id: uuid.UUID) -> list[Item]:
    """Toàn bộ sổ: mục cố định + dòng gán model (lõi và agent:<id>)."""
    return [*_STATIC, *await _binding_items(db, org_id)]


async def find(db: AsyncSession, org_id: uuid.UUID, key: str) -> Item | None:
    for it in await items(db, org_id):
        if it.key == key:
            return it
    return None


async def describe(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID,
                   its: list[Item] | None = None) -> list[dict[str, Any]]:
    """Mục sổ → JSON cho web: chỉ chuỗi/bool (không object thô)."""
    out: list[dict[str, Any]] = []
    for it in its if its is not None else await items(db, org_id):
        st = await it.read(db, org_id, user_id)
        out.append({"key": it.key, "label": it.label, "scope": it.scope, "group": it.group,
                    "default_text": st.default_text, "current_text": st.current_text, "customized": st.customized,
                    "resettable": it.resettable})
    return out


async def reset_autonomy(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Mức tự trị của TỔ CHỨC về mặc định 4 (`policy.DEFAULT_AUTONOMY`) nếu khoá đang có và khác. Là mục sổ
    `autonomy` (hiện trong `GET /defaults`, đếm vào "Đã đổi", nói trong hộp Xác nhận) và chỉ chạy sau phiên PIN
    (`defaults.reset_all`, hoặc PIN cho Về mặc định riêng mục này); không đụng mức tự trị từng agent (đổi cần PIN
    `policy.change`) và ranh giới cứng."""
    from gh.chassis import policy

    await db.execute(text("""
        UPDATE core.organizations
        SET settings = jsonb_set(settings, '{autonomy_level}', to_jsonb(CAST(:n AS int)), true)
        WHERE id = :o AND settings ? 'autonomy_level'
          AND (settings->>'autonomy_level') IS DISTINCT FROM CAST(:s AS text)"""),
                     {"o": org_id, "n": policy.DEFAULT_AUTONOMY, "s": str(policy.DEFAULT_AUTONOMY)})


# ─── hợp đồng với G5: gợi ý trên màn Hôm nay ──────────────────────────────────

#: MỘT bản chữ cho gợi ý thiếu khoá nền — dùng chung với Mặt tiền Owner (`gh.owner.service.BG_SUGGESTION`, khi sự cố
#: `ai.background_no_source` mở mà registry chưa nêu) để Sếp không gặp hai tiêu đề cho cùng một việc (F024).
BACKGROUND_KEY_SUGGESTION: dict[str, str] = {
    "key": "background_key_missing",
    "title": "Cần 1 khoá API để chạy lọc tin và bản tin",
    "body": ("Lọc tin, trực việc và Bản tin Gen chạy nền. Sếp dán 1 khoá OpenRouter hoặc Gemini, "
             "hoặc đăng nhập Antigravity CLI ở Bộ não AI là đủ."),
    "to": BRAIN_TAB,
}

async def suggestions(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    """`[{key, title, body, to}]` — Gen chỉ ĐỀ XUẤT, không tự làm.

    - 'apply_standard': từ 2 dòng gán lõi trở lên cùng MỘT model (bản cài cũ gán một model cho mọi việc).
    - 'background_key_missing': không có nguồn khoá API bật, không có Antigravity CLI chạy được (v0.1.62: agy chạy được
      việc nền) và Owner chưa cho Claude Code CLI chạy việc nền."""
    from gh.providers.router import background_cli_allowed, has_agy_source, has_api_source

    out: list[dict[str, Any]] = []
    rows = (await db.execute(text("""
        SELECT b.model_id, count(*) AS n FROM agent.bindings b
        WHERE b.org_id = :o AND b.agent_key = ANY(:k) GROUP BY b.model_id"""),
                             {"o": org_id, "k": list(CORE_BINDING_KEYS)})).all()
    if any(int(r.n) >= 2 for r in rows):
        out.append({"key": "apply_standard",
                    "title": "Áp model chuẩn theo vai? (đang dùng 1 model cho mọi việc)",
                    "body": ("Em thấy Gen, lọc tin và soạn nháp đang dùng chung một model. Áp chuẩn thì em tự chọn "
                             "model hợp từng việc (việc nhanh dùng model nhanh, rẻ). Sếp đổi lại được bất cứ lúc nào."),
                    "to": APPLY_STANDARD_TO})
    if (not await has_api_source(db, org_id) and not await has_agy_source(db, org_id)
            and not await background_cli_allowed(db, org_id)):
        out.append(dict(BACKGROUND_KEY_SUGGESTION))
    return out
