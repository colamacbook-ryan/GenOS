"""Chi phí AI theo ngày (giờ VN) + trần chi phí mỗi ngày (v0.1.41 — F-84).

Nguồn: `agent.model_calls` (router ghi mỗi lượt gọi) × bảng giá `agent.model_prices` (Owner nhập, VND / 1 triệu
token — migration 0027). Hiện ở Tổng quan › Sức khoẻ ("Chi phí AI hôm nay"), đổi giá/trần ở Bộ não AI.

- Nguồn giá (`price_source`): model của nhà cung cấp CLI (antigravity_cli, claude_code_cli) ⇒ 'subscription' (0 ₫ —
  trả theo gói, không tính theo token); có dòng `agent.model_prices` ⇒ 'owner'; còn lại 'none'. KHÔNG bịa giá mặc
  định: model chưa có giá ⇒ lượt gọi đếm vào `unpriced_calls` để Sếp biết tổng đang thiếu.
- Giá TÍNH LÚC ĐỌC: router không ghi `cost_vnd` lúc gọi ⇒ Owner đổi giá thì giá mới áp lại cho cả lịch sử (kể cả
  các ngày trước). Dòng nào đã có `cost_vnd` (ghi sẵn) thì dùng nguyên giá trị đó.
- Chỉ tính lượt `status = 'ok'` (lượt lỗi/giới hạn không bị tính tiền). Ngày theo giờ VN: [00:00, 00:00 hôm sau).
- Trần chi phí mỗi ngày ở `core.organizations.settings->'ai_cost'->'daily_budget_vnd'` (int | null). Vượt trần ⇒
  sự cố `ai.budget_exceeded` (gh/health.py::_eval_budget), mỗi ngày tối đa một chuông.
- Mọi số trả JSON là int/float (VND làm tròn về số nguyên) — không Decimal/object lạ.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.agents_api.routes import CORE_AGENT_KEYS
from gh.providers.cli import CLI_KINDS

# F-25: khoá cũ đã đổi tên — chi phí đã ghi theo khoá cũ vẫn hiện nhãn tiếng Việt, không hiện mã thô. Khoá cũ
# core.intent/core.scoring/core.indexing (không còn dùng) giữ fallback: hiện chính khoá.
# Nhãn có hậu tố "(cũ)" để ngày nâng cấp không có hai dòng cùng nhãn trong bảng chi phí (day_cost gộp theo agent_key).
# v0.1.55 (G1): `core.briefing` (Bản tin Gen, việc nền riêng) đã nằm trong CORE_AGENT_KEYS; giữ thêm ở đây để nhãn tiếng
# Việt không phụ thuộc thứ tự tra cứu. Lượt gọi cũ của Bản tin (dưới khoá core.gen) vẫn hiện "Gen — trợ lý quản trị".
# KHÔNG đặt giá mặc định cho model nào (xem đầu tệp).
LEGACY_AGENT_LABELS: dict[str, str] = {"core.reply_fast": "Soạn lại / dịch nháp (cũ)", "core.briefing": "Bản tin Gen"}

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TIMEZONE = "Asia/Ho_Chi_Minh"
#: Giới hạn trần/giá nhận từ API (chặn số vô lý gõ nhầm).
MAX_BUDGET_VND = 10_000_000_000
MAX_PRICE_PER_MTOK = 1e9
HISTORY_DAYS = 7

#: Biểu thức chi phí một lượt gọi (VND, numeric): CLI ⇒ 0 (trả theo gói); có giá ⇒ token × giá / 1 triệu; không giá
#: ⇒ NULL (SUM bỏ qua). Một phía giá để trống ⇒ coi là 0 ₫.
_COST_EXPR = """COALESCE(c.cost_vnd,
          CASE WHEN p.kind = ANY(:cli) THEN 0
               WHEN mp.model_id IS NOT NULL THEN
                 (COALESCE(c.tokens_in, 0) * COALESCE(mp.in_vnd_per_mtok, 0)
                  + COALESCE(c.tokens_out, 0) * COALESCE(mp.out_vnd_per_mtok, 0)) / 1000000
          END)"""
_UNPRICED_EXPR = "c.cost_vnd IS NULL AND mp.model_id IS NULL AND (p.kind IS NULL OR NOT (p.kind = ANY(:cli)))"
_CALLS_FROM = """FROM agent.model_calls c
        LEFT JOIN agent.models m ON m.id = c.model_id
        LEFT JOIN agent.providers p ON p.id = m.provider_id
        LEFT JOIN agent.model_prices mp ON mp.model_id = c.model_id"""


def vnd(value: Any) -> int:
    """VND làm tròn về số nguyên (nửa lên). None ⇒ 0."""
    if value is None:
        return 0
    return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fmt_vnd(value: int) -> str:
    """12500 ⇒ "12.500 ₫" (phân cách nghìn bằng dấu chấm)."""
    return f"{int(value):,}".replace(",", ".") + " ₫"


def today_vn(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(VN_TZ).date()


def day_bounds(d: date) -> tuple[datetime, datetime]:
    """[00:00 giờ VN của ngày d, 00:00 giờ VN hôm sau) — dạng có múi giờ."""
    start = datetime.combine(d, time.min, tzinfo=VN_TZ)
    return start, datetime.combine(d + timedelta(days=1), time.min, tzinfo=VN_TZ)


def price_source(provider_kind: str | None, has_price: bool) -> str:
    if provider_kind in CLI_KINDS:
        return "subscription"
    return "owner" if has_price else "none"


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


async def _labels(db: AsyncSession, org_id: uuid.UUID, keys: list[str]) -> dict[str, str]:
    """Nhãn agent: mục dùng chung (CORE_AGENT_KEYS) ⇒ tên tiếng Việt; 'agent:<id>' ⇒ tên agent.identities; khác ⇒
    chính khoá (agent đã xoá vẫn hiện khoá, không lỗi)."""
    ids: dict[uuid.UUID, str] = {}
    for k in keys:
        if k.startswith("agent:"):
            try:
                ids[uuid.UUID(k[len("agent:"):])] = k
            except ValueError:
                continue
    names: dict[str, str] = {}
    if ids:
        rows = (await db.execute(text("SELECT id, name FROM agent.identities WHERE org_id = :o AND id = ANY(:i)"),
                                 {"o": org_id, "i": list(ids)})).all()
        names = {ids[r.id]: str(r.name) for r in rows}
    return {k: CORE_AGENT_KEYS.get(k) or LEGACY_AGENT_LABELS.get(k) or names.get(k) or k for k in keys}


async def day_cost(db: AsyncSession, org_id: uuid.UUID, d: date) -> dict[str, Any]:
    """Chi phí ngày `d` (giờ VN) theo agent — MỘT truy vấn gộp theo agent_key. Trả {total_vnd, unpriced_calls,
    agents: [...]} (agent tốn nhiều nhất trước)."""
    start, end = day_bounds(d)
    rows = (await db.execute(text(f"""
        SELECT c.agent_key, count(*) AS calls, COALESCE(sum(c.tokens_in), 0) AS tokens_in,
               COALESCE(sum(c.tokens_out), 0) AS tokens_out,
               COALESCE(sum({_COST_EXPR}), 0) AS cost,
               count(*) FILTER (WHERE {_UNPRICED_EXPR}) AS unpriced
        {_CALLS_FROM}
        WHERE c.org_id = :o AND c.status = 'ok' AND c.at >= :s AND c.at < :e
        GROUP BY c.agent_key"""), {"o": org_id, "s": start, "e": end, "cli": list(CLI_KINDS)})).all()
    labels = await _labels(db, org_id, [r.agent_key for r in rows])
    agents = [{"agent_key": r.agent_key, "label": labels[r.agent_key], "calls": int(r.calls),
               "tokens_in": int(r.tokens_in), "tokens_out": int(r.tokens_out), "cost_vnd": vnd(r.cost),
               "unpriced_calls": int(r.unpriced)} for r in rows]
    agents.sort(key=lambda a: (-a["cost_vnd"], -a["calls"], a["agent_key"]))
    total = vnd(sum((Decimal(str(r.cost)) for r in rows), Decimal(0)))
    return {"total_vnd": total, "unpriced_calls": sum(a["unpriced_calls"] for a in agents), "agents": agents}


async def daily_totals(db: AsyncSession, org_id: uuid.UUID, last: date, days: int = HISTORY_DAYS
                       ) -> list[dict[str, Any]]:
    """Tổng VND từng ngày (giờ VN) của `days` ngày kết thúc ở `last` (cũ → mới; ngày không có lượt gọi = 0)."""
    first = last - timedelta(days=days - 1)
    start, end = day_bounds(first)[0], day_bounds(last)[1]
    rows = (await db.execute(text(f"""
        SELECT (c.at AT TIME ZONE :tz)::date AS d, COALESCE(sum({_COST_EXPR}), 0) AS cost
        {_CALLS_FROM}
        WHERE c.org_id = :o AND c.status = 'ok' AND c.at >= :s AND c.at < :e
        GROUP BY 1"""), {"o": org_id, "s": start, "e": end, "tz": TIMEZONE, "cli": list(CLI_KINDS)})).all()
    by_day = {r.d: r.cost for r in rows}
    return [{"date": (first + timedelta(days=i)).isoformat(), "total_vnd": vnd(by_day.get(first + timedelta(days=i)))}
            for i in range(days)]


async def models(db: AsyncSession, org_id: uuid.UUID, d: date) -> list[dict[str, Any]]:
    """Mọi model của tổ chức + giá + nguồn giá + số lượt gọi thành công trong ngày `d`."""
    start, end = day_bounds(d)
    rows = (await db.execute(text("""
        SELECT m.id, m.model_name, p.name AS provider_name, p.kind AS provider_kind,
               mp.model_id IS NOT NULL AS has_price, mp.in_vnd_per_mtok, mp.out_vnd_per_mtok,
               COALESCE(cc.n, 0) AS calls_today
        FROM agent.models m
        JOIN agent.providers p ON p.id = m.provider_id
        LEFT JOIN agent.model_prices mp ON mp.model_id = m.id
        LEFT JOIN (SELECT model_id, count(*) AS n FROM agent.model_calls
                   WHERE org_id = :o AND status = 'ok' AND at >= :s AND at < :e GROUP BY model_id) cc
               ON cc.model_id = m.id
        WHERE p.org_id = :o
        ORDER BY p.name, m.model_name"""), {"o": org_id, "s": start, "e": end})).all()
    return [{"model_id": str(r.id), "provider_name": r.provider_name, "provider_kind": r.provider_kind,
             "model_name": r.model_name, "in_vnd_per_mtok": _num(r.in_vnd_per_mtok),
             "out_vnd_per_mtok": _num(r.out_vnd_per_mtok), "price_source": price_source(r.provider_kind, r.has_price),
             "calls_today": int(r.calls_today)} for r in rows]


async def feedback_7d(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    """Đếm đánh giá Hữu ích / Không hữu ích của Gen trong 7 ngày (theo lần chấm gần nhất), tách riêng Bản tin Gen."""
    r = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE rating = 'helpful') AS helpful,
               count(*) FILTER (WHERE rating = 'not_helpful') AS not_helpful,
               count(*) FILTER (WHERE rating = 'helpful' AND kind = 'briefing') AS briefing_helpful,
               count(*) FILTER (WHERE rating = 'not_helpful' AND kind = 'briefing') AS briefing_not_helpful
        FROM agent.gen_feedback WHERE org_id = :o AND updated_at >= now() - interval '7 days'"""),
                           {"o": org_id})).one()
    return {"helpful": int(r.helpful), "not_helpful": int(r.not_helpful),
            "briefing_helpful": int(r.briefing_helpful), "briefing_not_helpful": int(r.briefing_not_helpful)}


async def get_budget(db: AsyncSession, org_id: uuid.UUID) -> int | None:
    raw = (await db.execute(text("SELECT settings->'ai_cost'->'daily_budget_vnd' FROM core.organizations "
                                 "WHERE id = :o"), {"o": org_id})).scalar_one_or_none()
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return int(raw)


async def set_budget(db: AsyncSession, org_id: uuid.UUID, value: int | None) -> None:
    await db.execute(text("""
        UPDATE core.organizations
        SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{ai_cost}',
                                 COALESCE(settings->'ai_cost', '{}'::jsonb)
                                 || jsonb_build_object('daily_budget_vnd', CAST(:b AS jsonb)), true)
        WHERE id = :o"""), {"o": org_id, "b": orjson.dumps(value).decode()})


async def model_in_org(db: AsyncSession, org_id: uuid.UUID, model_id: uuid.UUID) -> bool:
    return (await db.execute(text("""SELECT 1 FROM agent.models m JOIN agent.providers p ON p.id = m.provider_id
                                     WHERE m.id = :m AND p.org_id = :o"""),
                             {"m": model_id, "o": org_id})).first() is not None


async def set_price(db: AsyncSession, org_id: uuid.UUID, model_id: uuid.UUID, in_price: float | None,
                    out_price: float | None, updated_by: uuid.UUID | None) -> None:
    """Đặt giá (VND / 1 triệu token). Cả hai None ⇒ xoá dòng giá (model về 'chưa có giá')."""
    if in_price is None and out_price is None:
        await db.execute(text("DELETE FROM agent.model_prices WHERE model_id = :m AND org_id = :o"),
                         {"m": model_id, "o": org_id})
        return
    await db.execute(text("""
        INSERT INTO agent.model_prices (model_id, org_id, in_vnd_per_mtok, out_vnd_per_mtok, updated_by, updated_at)
        VALUES (:m, :o, :i, :x, :u, now())
        ON CONFLICT (model_id) DO UPDATE SET in_vnd_per_mtok = EXCLUDED.in_vnd_per_mtok,
               out_vnd_per_mtok = EXCLUDED.out_vnd_per_mtok, updated_by = EXCLUDED.updated_by, updated_at = now()"""),
                     {"m": model_id, "o": org_id, "i": in_price, "x": out_price, "u": updated_by})


async def summary(db: AsyncSession, org_id: uuid.UUID, d: date | None = None) -> dict[str, Any]:
    """Khuôn `GET /api/v1/system/ai-cost` (hợp đồng JSON 4 của đợt v0.1.41)."""
    d = d or today_vn()
    cost = await day_cost(db, org_id, d)
    budget = await get_budget(db, org_id)
    return {"date": d.isoformat(), "timezone": TIMEZONE, "total_vnd": cost["total_vnd"], "budget_vnd": budget,
            "over_budget": budget is not None and cost["total_vnd"] > budget,
            "unpriced_calls": cost["unpriced_calls"], "agents": cost["agents"],
            "models": await models(db, org_id, d), "last_7_days": await daily_totals(db, org_id, d),
            "feedback_7d": await feedback_7d(db, org_id)}


# ─── Trần tình báo `settings.ai_cost.intel_*` + gắn thẻ luồng (v0.1.76, gói L2, spec §4D.5, CNS-100) ───────────────
# MỘT nguồn chi phí: không có bảng giá hay sổ riêng. Sổ vẫn là `agent.model_calls`; L2 chỉ gắn thêm
# `thread_key`/`analysis_id` cho các dòng do lượt phân tích theo luồng ghi (cột có từ migration 0043). Trần là khoá
# con của cùng đối tượng `ai_cost`.
#: Mặc định theo spec: chạy bằng gói thuê bao (Q14) nên trần chính là SỐ LƯỢT; các khoá VND giữ 0 (không gọi model
#: tính tiền theo token). Giá trị thiếu trong DB = dùng mặc định. Đây không phải bảng giá model (không bịa giá).
INTEL_DEFAULTS: dict[str, int] = {
    "intel_daily_call_cap": 400,           # trần chính (lượt/ngày VN, toàn tổ chức)
    "intel_safety_call_cap": 60,           # trần lượt an toàn — vượt thì báo Ryan, KHÔNG cắt
    "intel_gate_call_cap": 40,             # trần lượt chạy ngay của 4 cổng còn lại
    "intel_daily_budget_vnd": 0,           # 0 ⇒ không gọi model tính tiền theo token
    "intel_safety_budget_vnd": 0,
    "intel_gate_budget_vnd": 0,
    "intel_media_daily_budget_vnd": 26_000,
}
INTEL_KEYS = tuple(INTEL_DEFAULTS)
#: Giới hạn nhận từ API (chặn số vô lý).
INTEL_MAX_CALLS = 100_000
INTEL_MAX_VND = MAX_BUDGET_VND


async def get_intel(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    """Các khoá `intel_*` đã điền mặc định. Giá trị sai kiểu/âm trong DB bị bỏ (dùng mặc định)."""
    raw = (await db.execute(text("SELECT settings->'ai_cost' FROM core.organizations WHERE id = :o"),
                            {"o": org_id})).scalar_one_or_none()
    cfg = raw if isinstance(raw, dict) else {}
    out = dict(INTEL_DEFAULTS)
    for k in INTEL_KEYS:
        v = cfg.get(k)
        if isinstance(v, int | float) and not isinstance(v, bool) and v >= 0:
            out[k] = int(v)
    return out


def intel_limit(key: str) -> int:
    return INTEL_MAX_CALLS if key.endswith("_call_cap") else INTEL_MAX_VND


async def set_intel(db: AsyncSession, org_id: uuid.UUID, values: dict[str, int]) -> None:
    """Đặt các khoá `intel_*` (chỉ khoá được phép; giá trị đã kiểm ở bên gọi). Giữ nguyên khoá `ai_cost` khác."""
    bad = [k for k in values if k not in INTEL_DEFAULTS]
    if bad:
        raise ValueError(f"khoá intel không hợp lệ: {bad}")
    for k, v in values.items():
        if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > intel_limit(k):
            raise ValueError(f"giá trị {k} ngoài khoảng cho phép")
    await db.execute(text("""
        UPDATE core.organizations
        SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{ai_cost}',
                                 COALESCE(settings->'ai_cost', '{}'::jsonb) || CAST(:v AS jsonb), true)
        WHERE id = :o"""), {"o": org_id, "v": orjson.dumps(values).decode()})


async def tag_calls(db: AsyncSession, org_id: uuid.UUID, *, thread_key: str, analysis_id: uuid.UUID | None,
                    purpose: str, since: datetime, until: datetime | None = None) -> int:
    """Gắn `thread_key`/`analysis_id` cho các dòng `agent.model_calls` MỚI (chưa có thẻ) của `purpose` trong
    [since, until]. Router (ngoài phạm vi L2) ghi dòng; lượt phân tích theo luồng của một tổ chức chạy tuần tự nên
    cửa sổ thời gian + `purpose` riêng của L2 đủ để không gắn nhầm. Trả số dòng đã gắn. Không đụng token/giá —
    chi phí vẫn tính lúc đọc như cũ."""
    r = await db.execute(text("""
        UPDATE agent.model_calls SET thread_key = :t, analysis_id = :a
        WHERE org_id = :o AND thread_key IS NULL AND purpose = :p AND at >= :s
          AND (CAST(:u AS timestamptz) IS NULL OR at <= :u)"""),
                         {"o": org_id, "t": thread_key, "a": analysis_id, "p": purpose, "s": since, "u": until})
    return int(r.rowcount or 0)  # type: ignore[attr-defined]
