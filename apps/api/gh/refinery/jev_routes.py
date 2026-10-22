"""/jev — "Bật Jev 1 chạm", thử 12 câu mẫu, số đo giá trị lọc (v0.1.55, G4). Chỉ Owner.

- `POST /jev/enable` — tạo/bật nguồn `system_one` theo `jev.PRESET` (OpenRouter + `typesafe/jev-1.13`). Giữ NGUYÊN rào
  PIN của việc tạo nguồn (`ai.route_change`, 423 nếu chưa nhập mã PIN): "1 chạm" = điền sẵn + một nút, không bớt rào.
  Khoá lấy từ (a) `use_existing_openrouter`: CHÉP khoá ĐÃ MÃ HÓA của nguồn OpenRouter đang có, hoàn toàn phía máy chủ —
  không bao giờ giải mã ra trình duyệt, không có trong phản hồi/Action Log; hoặc (b) `key` Sếp dán. Action Log
  `jev.enable` chỉ ghi nguồn khoá + endpoint/model, KHÔNG ghi khoá (kể cả 4 ký tự cuối).
- `POST /jev/benchmark` — chạy bộ 12 câu cố định (`gh.gen.jev_bench`) tuần tự qua `JevDecider` (trần 1,5 s/câu). Chưa có
  nguồn Jev kèm khoá ⇒ 409 `JEV_KEY_MISSING`.
- `GET /jev/value-summary` — số đo giá trị (`triage.value_summary`): chỉ đếm lần, không quy ra tiền.
"""

import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto
from gh.auth import service
from gh.auth.deps import require_owner, require_pin
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, field_errors
from gh.gen import decider as decmod
from gh.gen import jev, jev_bench
from gh.providers.router import KEY_AAD
from gh.refinery import triage

router = APIRouter(prefix="/jev", tags=["jev"])

JEV_NAME = "Jev (System One)"


def key_missing() -> ApiError:
    """409 JEV_KEY_MISSING — `title` là chuỗi thân thiện (web hiện thẳng); lý do kỹ thuật nằm ở `reasons` (chuỗi) để web
    đặt vào "Chi tiết kỹ thuật" (cùng khuôn MODEL_UNAVAILABLE), `detail` để trống."""
    return ApiError(409, "JEV_KEY_MISSING", "Chưa có khoá OpenRouter cho Jev", None,
                    reasons=["Không có nguồn model kind=system_one đang bật kèm khoá (agent.providers / "
                             "agent.provider_keys). Bật Jev bằng POST /api/v1/jev/enable (dùng khoá OpenRouter đang "
                             "có hoặc dán khoá)."])


def _transport(request: Request) -> Any:
    """Transport HTTP của bộ định tuyến model (None ở production; test tiêm Jev giả)."""
    return getattr(getattr(request.app.state, "model_router", None), "transport", None)


class EnableIn(BaseModel):
    use_existing_openrouter: bool = False
    key: str | None = Field(default=None, max_length=500)


def _is_openrouter(endpoint: str | None) -> bool:
    host = (urlparse(endpoint or "").hostname or "").lower()
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


async def _openrouter_secret(db: AsyncSession, org_id: uuid.UUID) -> bytes | None:
    """Khoá (đã mã hóa, nguyên văn) của nguồn OpenRouter đang có — CHỈ phía máy chủ."""
    rows = (await db.execute(text("""
        SELECT p.endpoint, k.secret_enc FROM agent.providers p
        JOIN agent.provider_keys k ON k.provider_id = p.id AND k.is_enabled
        WHERE p.org_id = :o AND p.kind NOT IN ('system_one', 'antigravity_cli', 'claude_code_cli')
        ORDER BY p.failover_rank NULLS LAST, p.created_at, k.rotation_order"""), {"o": org_id})).all()
    return next((bytes(r.secret_enc) for r in rows if _is_openrouter(r.endpoint)), None)


async def _has_key(db: AsyncSession, provider_id: uuid.UUID, *, enc: bytes | None = None,
                   plain: str | None = None) -> bool:
    """Nguồn này đã có đúng khoá đó chưa (bấm nút hai lần không sinh khoá trùng)."""
    rows = (await db.execute(text("SELECT secret_enc FROM agent.provider_keys WHERE provider_id = :p"),
                             {"p": provider_id})).all()
    for r in rows:
        cur = bytes(r.secret_enc)
        if enc is not None and cur == enc:
            return True
        if plain is not None:
            try:
                if crypto.decrypt(cur, KEY_AAD).decode() == plain:
                    return True
            except Exception:  # noqa: BLE001 — khoá master đổi: coi như khác
                continue
    return False


async def _add_key(db: AsyncSession, provider_id: uuid.UUID, enc: bytes, last4: str) -> None:
    n = (await db.execute(text("SELECT count(*) FROM agent.provider_keys WHERE provider_id = :p"),
                          {"p": provider_id})).scalar_one()
    await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4, rotation_order)
                             VALUES (:p, :l, :s, :f, :r)"""),
                     {"p": provider_id, "l": f"JEV-KEY-{n + 1:02d}", "s": enc, "f": last4, "r": n})


@router.post("/enable")
async def enable_jev(body: EnableIn, user: service.CurrentUser = Depends(require_owner),
                     _pin: Any = Depends(require_pin("ai.route_change")), db: AsyncSession = DB) -> dict[str, Any]:
    """Tạo/bật nguồn Jev theo `jev.PRESET`. Idempotent: gọi lại chỉ bật lại + bổ sung khoá còn thiếu."""
    pasted = (body.key or "").strip()
    if body.use_existing_openrouter and pasted:
        raise field_errors({"key": "Chọn một: dùng khoá OpenRouter đang có hoặc dán khoá mới, không cả hai"})
    if pasted and len(pasted) < 8:
        raise field_errors({"key": "Khoá quá ngắn — dán đủ khoá OpenRouter (bắt đầu bằng sk-or-…)"})
    existing = (await db.execute(text("""
        SELECT id FROM agent.providers WHERE org_id = :o AND kind = 'system_one'
        ORDER BY failover_rank NULLS LAST, created_at LIMIT 1"""), {"o": user.org_id})).one_or_none()
    enc: bytes | None = None
    last4 = ""
    source = "kept"
    if body.use_existing_openrouter:
        enc = await _openrouter_secret(db, user.org_id)
        if enc is None:
            raise key_missing()
        source = "existing_openrouter"
        last4 = (await db.execute(text("""
            SELECT k.last4 FROM agent.provider_keys k WHERE k.secret_enc = :s LIMIT 1"""), {"s": enc})).scalar_one()
    elif pasted:
        enc = crypto.encrypt(pasted.encode(), KEY_AAD)
        last4 = pasted[-4:]
        source = "pasted"
    created = existing is None
    if existing is None:
        if enc is None:
            raise key_missing()
        rank = (await db.execute(text("""SELECT COALESCE(max(failover_rank), 0) + 1 FROM agent.providers
                                         WHERE org_id = :o"""), {"o": user.org_id})).scalar_one()
        pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                        VALUES (:o, 'system_one', :n, :e, :r) RETURNING id"""),
                                {"o": user.org_id, "n": JEV_NAME, "e": jev.PRESET["endpoint"], "r": rank})
                ).scalar_one()
    else:
        pid = existing.id
        await db.execute(text("UPDATE agent.providers SET is_enabled = true WHERE id = :i"), {"i": pid})
        has_key = (await db.execute(text("""SELECT 1 FROM agent.provider_keys WHERE provider_id = :p AND is_enabled
                                            LIMIT 1"""), {"p": pid})).first()
        if enc is None and has_key is None:
            raise key_missing()
    await db.execute(text("""INSERT INTO agent.models (provider_id, model_name) VALUES (:p, :m)
                             ON CONFLICT DO NOTHING"""), {"p": pid, "m": jev.PRESET["model"]})
    if enc is not None:
        already = (await _has_key(db, pid, enc=enc) if source == "existing_openrouter"
                   else await _has_key(db, pid, plain=pasted))
        if not already:
            await _add_key(db, pid, enc, last4)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="jev.enable", target_type="provider", target_id=str(pid), target_label=JEV_NAME,
                           detail={"created": created, "key_source": source, "endpoint": jev.PRESET["endpoint"],
                                   "model": jev.PRESET["model"]}, ip=user.ip)
    return {"provider_id": str(pid), "created": created, "key_source": source,
            "endpoint": jev.PRESET["endpoint"], "model": jev.PRESET["model"]}


@router.post("/benchmark")
async def benchmark(request: Request, user: service.CurrentUser = Depends(require_owner),
                    db: AsyncSession = DB) -> dict[str, Any]:
    """Bộ 12 câu mẫu cố định, chạy tuần tự qua `JevDecider` (trần 1,5 s/câu)."""
    dec = await decmod.load_decider(db, user.org_id, transport=_transport(request))
    if not isinstance(dec, decmod.JevDecider):
        raise key_missing()
    result: dict[str, Any] = await jev_bench.run(dec, filter_options=triage.JEV_OPTIONS,
                                                 filter_context=triage.jev_context("tin nhắn mẫu"))
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="jev.benchmark", target_type="provider", target_id="jev",
                           detail={"total": result["total"], "correct": result["correct"],
                                   "avg_latency_ms": result["avg_latency_ms"]}, ip=user.ip)
    return result


@router.get("/value-summary")
async def value_summary(days: int = Query(7, ge=1, le=90), user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> dict[str, Any]:
    return await triage.value_summary(db, user.org_id, days)
