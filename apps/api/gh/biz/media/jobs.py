# ruff: noqa: E501
"""Việc nền của cụm `media`: pipeline voice/ảnh → `clean.media_texts` (spec §4E).

Hàng đợi = chính `raw.events` (kind voice/image có `payload.media_ref`) trừ những tin đã có kết quả chung cuộc trong
`clean.media_texts` — bền (worker chết không mất việc), idempotent, không cần bảng/ DDL mới. `media_sweep` chạy mỗi phút:
nhặt tối đa `SWEEP_LIMIT` tin chưa xong trong 24 giờ và xử lý. Chống chạy trùng bằng khoá Redis theo tin.

Một tin: kiểm khai báo (too_large/MIME, chưa chạm mạng) → ngân sách (L2, điểm cắm `budget_ok`) → tải qua bridge
(`media.fetch`) hoặc đọc lại file đã lưu → lưu `objects` + `raw.attachments` → STT/OCR/mô tả → `clean.media_texts` → luật (a)
`prefilter.scan_text` trên chữ lấy ra (có ứng viên ⇒ tin chạy đường nhanh). Lỗi tạm thử lại tối đa `MAX_ATTEMPTS` lần
rồi chốt `gave_up`; lỗi cố định (quá lớn, MIME lạ…) chốt ngay. Không có kết quả nào bị bỏ im lặng: mọi tin có một hàng trạng thái.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.biz.hooks import CronJob, Hook
from gh.biz.media import fetch as mfetch
from gh.biz.media import prefilter_port
from gh.biz.media.ocr import read_image
from gh.biz.media.providers import (
    EngineError,
    EngineText,
    SttEngine,
    VisionEngine,
    provider_name,
    stt_engine,
    vision_engine,
)
from gh.biz.media.transcribe import transcribe
from gh.chassis.bus import EventBus
from gh.chassis.objects import ObjectNotFound, ObjectStore, get_object_store, new_key
from gh.data.common import org_settings

log = logging.getLogger("gh.biz.media")

SWEEP_LIMIT = 10
SWEEP_CONCURRENCY = 3
SWEEP_BUDGET_S = 200.0
SINCE_HOURS = mfetch.RETRY_WINDOW_H
UNAVAILABLE_COOLDOWN_S = 900     # engine chưa bật: thử lại chậm (15 phút/lần), không tính lượt thử
MAX_ATTEMPTS = 3
CLAIM_TTL_S = 600
PLACEHOLDER_PREFIX = "none:"       # storage_key của hàng `raw.attachments` giữ chỗ (chưa có file) — kho không có khoá này
NA_VERSION = "n/a"
PRIMARY_KIND = {"voice": "transcript", "image": "ocr"}


def claim_key(event_id: uuid.UUID) -> str:
    return f"gh:media:claim:{event_id}"


def attempts_key(event_id: uuid.UUID) -> str:
    return f"gh:media:try:{event_id}"


def cooldown_key(event_id: uuid.UUID) -> str:
    return f"gh:media:cool:{event_id}"


@dataclass
class PendingEvent:
    id: uuid.UUID
    received_at: datetime
    org_id: uuid.UUID
    kind: str
    media_ref: str
    media: dict[str, Any]
    group_id: uuid.UUID | None = None
    bind: dict[str, Any] = field(default_factory=dict)   # {chat, sender} bridge đã ràng buộc với media_ref


BudgetOk = Callable[[AsyncSession, uuid.UUID], Awaitable[bool]]
Fetcher = Callable[..., Awaitable[mfetch.Fetched]]
Scanner = Callable[[str, str], list[Any]]


async def _budget_ok(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """ĐIỂM CẮM TRẦN CHI PHÍ (L2, CNS-100): `gh/refinery/budget.py` chưa merge. Khi có, thay thân hàm này bằng lời gọi của
    L2 (chữ ký giữ nguyên). Trả False ⇒ tin ghi `skipped_budget` và được thử lại sau, không mất."""
    return True


@dataclass
class Deps:
    sm: async_sessionmaker[AsyncSession]
    redis: Any
    bus: EventBus
    router: Any = None
    stt: SttEngine | None = None             # ghi đè engine (test, S5); mặc định theo cài đặt tổ chức
    vision: VisionEngine | None = None
    store: ObjectStore | None = None
    budget_ok: BudgetOk = _budget_ok
    fetcher: Fetcher = mfetch.fetch
    scan: Scanner = prefilter_port.scan_text
    fetch_timeout_s: float = mfetch.FETCH_TIMEOUT_S
    max_attempts: int = MAX_ATTEMPTS
    unavailable_cooldown_s: int = UNAVAILABLE_COOLDOWN_S
    stats: dict[str, int] = field(default_factory=dict)

    def bump(self, k: str) -> None:
        self.stats[k] = self.stats.get(k, 0) + 1


# ─── hàng đợi ──────────────────────────────────────────────────────────────────────────────────────────────────────

async def pending_events(db: AsyncSession, org_id: uuid.UUID, *, limit: int = SWEEP_LIMIT,
                         since_hours: int = SINCE_HOURS) -> list[PendingEvent]:
    """Tin media chưa có kết quả CHUNG CUỘC: chữ chính `ok`, hoặc `too_large`, hoặc `failed` với mã cố định."""
    rows = (await db.execute(text("""
        SELECT e.id, e.received_at, e.org_id, e.kind, e.payload->>'media_ref' AS media_ref,
               coalesce(e.payload->'media', '{}'::jsonb) AS media, e.group_id,
               coalesce(e.payload->'media_bind', '{}'::jsonb) AS bind
          FROM raw.events e
         WHERE e.org_id = :o AND e.kind IN ('voice', 'image') AND e.payload->>'media_ref' IS NOT NULL
           AND e.received_at > now() - make_interval(hours => :h)
           AND NOT EXISTS (
                 SELECT 1 FROM clean.media_texts m WHERE m.raw_event_id = e.id AND (
                      (m.kind IN ('transcript', 'ocr') AND m.status = 'ok')
                   OR m.status = 'too_large'
                   OR (m.status = 'failed' AND m.error_code = ANY(:final))))
         ORDER BY e.received_at LIMIT :n"""),
        {"o": org_id, "h": since_hours, "n": limit, "final": sorted(mfetch.FINAL_ERRORS)})).mappings().all()
    return [PendingEvent(id=r["id"], received_at=r["received_at"], org_id=r["org_id"], kind=r["kind"],
                         media_ref=r["media_ref"], media=r["media"] if isinstance(r["media"], dict) else {},
                         group_id=r["group_id"], bind=r["bind"] if isinstance(r["bind"], dict) else {})
            for r in rows]


# ─── ghi kết quả ───────────────────────────────────────────────────────────────────────────────────────────────────

async def _set_org(db: AsyncSession, org_id: uuid.UUID) -> None:
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})


async def _attachment(db: AsyncSession, ev: PendingEvent) -> Any:
    """Hàng `raw.attachments` đã có của tin (ưu tiên hàng thật, chưa có thì None)."""
    return (await db.execute(text("""
        SELECT id, storage_key, mime, bytes, sha256 FROM raw.attachments
         WHERE event_id = :e AND event_received_at = :r
         ORDER BY (storage_key LIKE :ph) ASC, id LIMIT 1"""),
        {"e": ev.id, "r": ev.received_at, "ph": PLACEHOLDER_PREFIX + "%"})).first()


async def _insert_attachment(db: AsyncSession, ev: PendingEvent, *, key: str, mime: str, size: int, sha: bytes) -> uuid.UUID:
    return (await db.execute(text("""
        INSERT INTO raw.attachments (event_id, event_received_at, storage_key, mime, bytes, sha256)
        VALUES (:e, :r, :k, :m, :b, :s) RETURNING id"""),
        {"e": ev.id, "r": ev.received_at, "k": key, "m": mime, "b": size, "s": sha})).scalar_one()


async def _placeholder(db: AsyncSession, ev: PendingEvent, code: str) -> uuid.UUID:
    """Giữ chỗ cho tin chưa có file (quá lớn, tải lỗi…) để vẫn có hàng trạng thái (`media_texts.attachment_id` NOT NULL)."""
    row = await _attachment(db, ev)
    if row is not None:
        return row.id
    declared = mfetch.base_mime(ev.media.get("mime")) or "application/octet-stream"
    return await _insert_attachment(db, ev, key=f"{PLACEHOLDER_PREFIX}{code}", mime=declared, size=0,
                                    sha=hashlib.sha256(b"").digest())


async def _write_text(db: AsyncSession, ev: PendingEvent, attachment_id: uuid.UUID, *, kind: str, status: str,
                      text_: str | None = None, lang: str | None = None, engine: str = "media",
                      version: str = NA_VERSION, confidence: float | None = None, error: str | None = None) -> None:
    await db.execute(text("""
        INSERT INTO clean.media_texts (org_id, raw_event_id, raw_received_at, attachment_id, kind, text, lang, engine,
                                       engine_version, confidence, status, error_code)
        VALUES (:o, :e, :r, :a, :k, :t, :l, :en, :v, :c, :s, :err)
        ON CONFLICT (attachment_id, kind, engine_version) DO UPDATE SET
          text = EXCLUDED.text, lang = EXCLUDED.lang, engine = EXCLUDED.engine, confidence = EXCLUDED.confidence,
          status = EXCLUDED.status, error_code = EXCLUDED.error_code, created_at = now()"""),
        {"o": ev.org_id, "e": ev.id, "r": ev.received_at, "a": attachment_id, "k": kind, "t": text_, "l": lang,
         "en": engine, "v": version, "c": confidence, "s": status, "err": error})


def _status_for(code: str) -> str:
    return "too_large" if code == "too_large" else "skipped_budget" if code == "skipped_budget" else "failed"


async def _record_failure(d: Deps, ev: PendingEvent, code: str) -> str:
    async with d.sm() as db:
        await _set_org(db, ev.org_id)
        att = await _placeholder(db, ev, code)
        await _write_text(db, ev, att, kind=PRIMARY_KIND[ev.kind], status=_status_for(code), error=code)
        await db.commit()
    d.bump(code)
    return _status_for(code)


async def _escalate_fast(db: AsyncSession, ev: PendingEvent) -> None:
    """Luật (a) trúng trên chữ OCR/transcript ⇒ tin chạy đường nhanh (cùng cơ chế `ingest_message`): đặt `fast` và báo
    `raw_ingested`. Lỗi ở đây không được làm mất kết quả đã ghi."""
    try:
        async with db.begin_nested():
            n = (await db.execute(text("""
                UPDATE refinery.event_state SET fast = true, updated_at = now()
                 WHERE event_id = :e AND event_received_at = :r AND state = 'pending' AND fast IS NOT TRUE
                RETURNING 1"""), {"e": ev.id, "r": ev.received_at})).first()
            if n is not None:
                await db.execute(text("SELECT pg_notify('raw_ingested', :p)"), {"p": f"{ev.org_id}|fast|{ev.id}"})
    except Exception as e:  # noqa: BLE001
        log.warning("Không nâng được tin media lên đường nhanh: %s", type(e).__name__)


# ─── một tin ───────────────────────────────────────────────────────────────────────────────────────────────────────

async def process_event(d: Deps, ev: PendingEvent) -> str:
    """Xử lý một tin; trả trạng thái: ok | failed | too_large | skipped_budget | claimed (đang có worker khác làm)."""
    if await d.redis.exists(cooldown_key(ev.id)):
        return "cooldown"            # engine chưa bật: chờ hết 15 phút mới thử lại (không tốn tải/ngân sách)
    if not await d.redis.set(claim_key(ev.id), b"1", nx=True, ex=CLAIM_TTL_S):
        return "claimed"
    try:
        return await _process(d, ev)
    finally:
        try:
            await d.redis.delete(claim_key(ev.id))
        except Exception:  # noqa: BLE001 — khoá tự hết hạn
            pass


async def _process(d: Deps, ev: PendingEvent) -> str:
    bound = isinstance(ev.bind.get("chat"), str) and isinstance(ev.bind.get("sender"), str) and bool(ev.bind.get("sender"))
    problem = mfetch.declared_problem(ev.kind, ev.media) if mfetch.valid_ref(ev.media_ref) and bound else "bad_request"
    if problem:                      # quá lớn / MIME lạ / ref sai: chốt ngay, KHÔNG chạm mạng
        return await _record_failure(d, ev, problem)

    async with d.sm() as db:
        await _set_org(db, ev.org_id)
        if not await d.budget_ok(db, ev.org_id):
            settings = None
        else:
            settings = await org_settings(db, ev.org_id)
        existing = await _attachment(db, ev) if settings is not None else None
    if settings is None:
        return await _record_failure(d, ev, "skipped_budget")

    n = await d.redis.incr(attempts_key(ev.id))
    await d.redis.expire(attempts_key(ev.id), 86400)
    if n > d.max_attempts:
        return await _record_failure(d, ev, "gave_up")

    store = d.store or get_object_store()
    data: bytes | None = None
    mime = ""
    att_id: uuid.UUID | None = None
    if existing is not None and not str(existing.storage_key).startswith(PLACEHOLDER_PREFIX):
        # Đã lưu file ở lần trước (engine lỗi): đọc lại từ kho, không cần bridge còn giữ ref.
        try:
            data = await store.get(existing.storage_key)
            if hashlib.sha256(data).digest() != bytes(existing.sha256):
                data = None
            else:
                mime, att_id = existing.mime, existing.id
        except ObjectNotFound:
            data = None
    if data is None:
        try:
            got = await d.fetcher(d.bus, ev.org_id, kind=ev.kind, media_ref=ev.media_ref, chat=ev.bind["chat"],
                                  sender=ev.bind["sender"], timeout=d.fetch_timeout_s)
        except mfetch.MediaFetchError as e:
            if e.code == "not_found" and n >= mfetch.NOT_FOUND_ATTEMPTS:
                return await _record_failure(d, ev, "gone")      # hai lần liền không còn ref ⇒ chốt
            return await _record_failure(d, ev, e.code)
        data, mime = got.data, got.mime
        key = new_key(ev.org_id, mfetch.safe_filename(None, mime))
        await store.put(key, data)
        async with d.sm() as db:
            await _set_org(db, ev.org_id)
            att_id = await _insert_attachment(db, ev, key=key, mime=mime, size=len(data), sha=bytes.fromhex(got.sha256))
            await db.commit()
    assert att_id is not None

    try:
        results = await _run_engines(d, ev, settings, data, mime)
    except EngineError as e:
        if e.code == "engine_unavailable":
            # Chưa có công cụ đọc (vd. router chưa có đường âm thanh/ảnh): KHÔNG tính lượt thử, không chốt gave_up; giữ file
            # đã lưu và thử lại chậm — khi công cụ được bật thì tin tự được đọc.
            await d.redis.decr(attempts_key(ev.id))
            if d.unavailable_cooldown_s > 0:
                await d.redis.set(cooldown_key(ev.id), b"1", ex=d.unavailable_cooldown_s)
        async with d.sm() as db:
            await _set_org(db, ev.org_id)
            await _write_text(db, ev, att_id, kind=PRIMARY_KIND[ev.kind], status="failed", error=e.code)
            await db.commit()
        d.bump(e.code)
        return "failed"

    hits = 0
    async with d.sm() as db:
        await _set_org(db, ev.org_id)
        for kind, res, err in results:
            if res is None:
                await _write_text(db, ev, att_id, kind=kind, status="failed", error=err or "engine_error")
                continue
            await _write_text(db, ev, att_id, kind=kind, status="ok", text_=res.text, lang=res.lang, engine=res.engine,
                              version=res.engine_version, confidence=res.confidence)
            if res.text:
                hits += len(d.scan(res.text, prefilter_port.source_field_for(kind)) or [])
        if hits:
            await _escalate_fast(db, ev)
        await db.commit()
    d.bump("ok")
    return "ok"


async def _run_engines(d: Deps, ev: PendingEvent, settings: dict[str, Any], data: bytes,
                       mime: str) -> list[tuple[str, EngineText | None, str | None]]:
    """[(kind media_texts, kết quả|None, mã lỗi)]. Ném EngineError khi KHÔNG có kết quả chính nào."""
    if ev.kind == "voice":
        engine = d.stt or stt_engine(provider_name(settings), router=d.router, org_id=ev.org_id)
        return [("transcript", await transcribe(engine, data, mime, lang_hint="vi"), None)]
    vision = d.vision or vision_engine(router=d.router, org_id=ev.org_id)
    reading = await read_image(vision, data, mime)
    out: list[tuple[str, EngineText | None, str | None]] = []
    out.append(("ocr", reading.ocr, reading.errors.get("ocr")))
    out.append(("description", reading.description, reading.errors.get("description")))
    return out      # OCR chưa đọc được ⇒ hàng `ocr` failed ⇒ tin còn trong hàng đợi, lần sau đọc lại từ file đã lưu


# ─── việc định kỳ ──────────────────────────────────────────────────────────────────────────────────────────────────

async def sweep_org(d: Deps, org_id: uuid.UUID, *, limit: int = SWEEP_LIMIT, deadline: float | None = None) -> dict[str, int]:
    async with d.sm() as db:
        await _set_org(db, org_id)
        evs = await pending_events(db, org_id, limit=limit)
    sem = asyncio.Semaphore(SWEEP_CONCURRENCY)
    counts: dict[str, int] = {}

    async def one(ev: PendingEvent) -> None:
        async with sem:
            if deadline is not None and time.monotonic() > deadline:
                return
            try:
                st = await process_event(d, ev)
            except Exception as e:  # noqa: BLE001 — một tin lỗi không chặn tin khác; lần sau nhặt lại
                # Chỉ tên lỗi: traceback của SQLAlchemy kèm tham số (có thể là chữ OCR/transcript) — không được vào log.
                log.error("Xử lý media lỗi (tin %s): %s", ev.id, type(e).__name__)
                st = "error"
            counts[st] = counts.get(st, 0) + 1

    await asyncio.gather(*(one(e) for e in evs))
    return counts


async def media_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    redis = ctx["redis_bus"]
    d = Deps(sm=sm, redis=redis, bus=EventBus(redis), router=ctx.get("model_router"))
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    total: dict[str, int] = {}
    deadline = time.monotonic() + SWEEP_BUDGET_S
    for org in orgs:
        for k, v in (await sweep_org(d, org, deadline=deadline)).items():
            total[k] = total.get(k, 0) + v
    return total


HOOKS: list[Hook] = []
JOBS: list[CronJob] = [(media_sweep, {"minute": set(range(60)), "timeout": 300})]
