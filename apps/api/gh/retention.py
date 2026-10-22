"""v0.1.40 (F-2): hạn lưu dữ liệu THẬT — nguồn sự thật duy nhất cho mọi việc dọn dữ liệu quá hạn.

Ba kiểu dọn:
- `partition` — bảng phân vùng theo tháng (raw.events, clean.meaning_units, agent.model_calls): chỉ xoá theo CẢ phân
  vùng tháng qua pg_partman (`partman.part_config.retention` + `retention_keep_table = false`; mặc định partman chỉ
  TÁCH bảng con chứ không xoá). Retention CHỈ được đặt bên trong giao dịch của `retention_sweep` (05:00) rồi trả về
  NULL — PATCH không đẩy sang partman, `partition_maintenance` (23:20/04:20) xoá retention trước khi bảo trì ⇒ dữ liệu
  chỉ bị xoá ở lượt dọn 05:00 như câu xác nhận nói. raw.events và ops.action_log có trigger `core.forbid_mutation`
  chặn UPDATE/DELETE theo dòng ⇒ tuyệt đối không DELETE theo dòng ở đây. Hạn hiệu lực = MAX(keep_days) qua mọi tổ
  chức; tổ chức nào chưa đặt (NULL) ⇒ giữ mãi (retention NULL) — một máy cài, các tổ chức dùng chung bảng phân vùng.

CHỈ thi hành dòng ĐÃ XÁC NHẬN (`confirmed_at`, migration 0026): trước v0.1.40 hạn lưu chỉ để hiển thị ("Chưa tự xoá —
sẽ áp dụng ở bản sau") nên giá trị cũ có thể được đặt mà không ai nghĩ tới chuyện xoá thật. Dòng `keep_days` có mà
`confirmed_at` NULL = "chưa xác nhận" ⇒ coi như giữ mãi; lượt dọn gửi chuông nhắc Owner xác nhận lại
(`notify_unconfirmed`). PATCH chỉ đặt `confirmed_at` khi bên gọi gửi `confirm_delete: true` (web hỏi lại trước).
- `batch` — xoá/làm rỗng theo lô (5000 dòng/lô, commit mỗi lô, dừng khi lô < 5000 hoặc hết ngân sách thời gian):
  memory.entries (chỉ mục ĐÃ NÉN quá hạn, không ghim), agent.browser_jobs.result (cố định 14 ngày — giữ dòng việc,
  bỏ nội dung đã đọc), raw.attachments của sự kiện đã bị partman xoá (xoá dòng + object, best-effort).
- `not_applicable` — ops.action_log: nhật ký chống sửa (chuỗi băm) được giữ nguyên.

`retention_sweep` (cron 05:00 giờ VN) gọi lần lượt mọi phần — mỗi phần try/except riêng — gồm cả hội thoại Gen
(`gen_store.purge_expired`) và chuông (`notifications.purge_old`); phiên đăng nhập vẫn dọn mỗi giờ (`purge_sessions`).
Kết quả ghi Redis `gh:retention:last` = {"at", "datasets": {tên: {"mode", "deleted", "ok"}}} (TTL 14 ngày) —
GET /system/retention-policies đọc để hiện "lần dọn gần nhất".
"""

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("gh.retention")

#: Tập dữ liệu Owner đặt được hạn lưu (`ops.retention_policies`) — thứ tự giữ như danh sách cũ của API.
DATASETS: dict[str, dict[str, Any]] = {
    "raw.events": {"mode": "partition", "column": "received_at", "editable": True},
    "clean.meaning_units": {"mode": "partition", "column": "observed_at", "editable": True},
    "ops.action_log": {"mode": "not_applicable", "editable": False},
    "memory.entries": {"mode": "batch", "editable": True},
    "agent.model_calls": {"mode": "partition", "column": "at", "editable": True},
}
PARTITIONED = tuple(d for d, v in DATASETS.items() if v["mode"] == "partition")

#: Mục cố định (không sửa được): nội dung đã đọc của việc trình duyệt giữ 14 ngày.
BROWSER_RESULT = "agent.browser_jobs.result"
BROWSER_RESULT_DAYS = 14
# v0.1.47 (F-79): ảnh chụp bằng chứng của việc GỬI (đã mã hoá ở object store) giữ 90 ngày — lâu hơn nội dung đã đọc.
PROOF_DAYS = 90
FIXED: dict[str, dict[str, Any]] = {BROWSER_RESULT: {"mode": "batch", "keep_days": BROWSER_RESULT_DAYS,
                                                     "editable": False}}

NOTES = {
    "partition": "Xoá theo tháng: cả tháng quá hạn mới bị xoá",
    "memory.entries": "Chỉ xoá mục sổ tay đã nén; mục ghim giữ mãi",
    "ops.action_log": "Không áp dụng — nhật ký chống sửa được giữ nguyên",
    BROWSER_RESULT: "Nội dung đã đọc của việc trình duyệt giữ 14 ngày (ảnh chụp bằng chứng khi gửi: 90 ngày); "
               "dòng việc vẫn giữ",
}
NOT_APPLICABLE_ERROR = "Nhật ký hành động chưa áp dụng hạn lưu (chuỗi chống sửa)"

BATCH = 5000
TIME_BUDGET_S = 600.0
LAST_KEY = "gh:retention:last"
LAST_TTL = 14 * 86400


def note_for(dataset: str) -> str:
    if dataset in NOTES:
        return NOTES[dataset]
    mode = (DATASETS.get(dataset) or FIXED.get(dataset) or {}).get("mode", "")
    return NOTES.get(mode, "")


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ─── partman ───────────────────────────────────────────────────────────────────────────────────────────────

async def effective_keep_days(db: AsyncSession, dataset: str) -> int | None:
    """MAX(keep_days) qua mọi tổ chức; có tổ chức chưa đặt (NULL/thiếu dòng/chưa xác nhận) hoặc chưa có tổ chức ⇒ None
    (giữ mãi)."""
    r = (await db.execute(text("""
        SELECT count(*) AS orgs,
               count(rp.keep_days) FILTER (WHERE rp.confirmed_at IS NOT NULL) AS set_,
               max(rp.keep_days) FILTER (WHERE rp.confirmed_at IS NOT NULL) AS keep
        FROM core.organizations o
        LEFT JOIN ops.retention_policies rp ON rp.org_id = o.id AND rp.dataset = :d"""), {"d": dataset})).one()
    if not r.orgs or r.set_ < r.orgs or r.keep is None:
        return None
    return int(r.keep)


async def partman_keeps(db: AsyncSession) -> dict[str, int | None]:
    """Hạn hiệu lực của 3 bảng phân vùng (None = giữ mãi) — chỉ đọc bảng chính sách."""
    return {ds: await effective_keep_days(db, ds) for ds in PARTITIONED}


async def clear_partman_retention(admin_db: AsyncSession) -> None:
    """Đưa `partman.part_config.retention` của 3 bảng phân vùng về NULL. `partition_maintenance` (23:20/04:20) gọi
    TRƯỚC `run_maintenance()` để bảo trì phân vùng không bao giờ xoá tháng nào — chỉ `retention_sweep` (05:00) xoá, đúng
    giờ câu xác nhận hứa với Owner và đếm đủ số tháng đã xoá. Dọn cả giá trị cũ do bản dev v0.1.40 trước đã ghi."""
    await admin_db.execute(text("""UPDATE partman.part_config SET retention = NULL
                                   WHERE parent_table = ANY(:t) AND retention IS NOT NULL"""),
                           {"t": list(PARTITIONED)})


async def _partition_count(admin_db: AsyncSession, parent: str) -> int:
    return int((await admin_db.execute(text("SELECT count(*) FROM partman.show_partitions(:t)"),
                                       {"t": parent})).scalar_one())


async def drop_expired_partitions(admin_db: AsyncSession, keeps: dict[str, int | None]) -> dict[str, int]:
    """XOÁ phân vùng tháng quá hạn của từng bảng có hạn lưu. PHẢI chạy qua `admin_sessionmaker` (role gh_app không có
    quyền schema partman). Mỗi bảng một giao dịch: đặt `part_config.retention` → `run_maintenance(bảng)` → đếm → trả
    retention về NULL → commit. Phiên khác (bảo trì phân vùng chạy `run_maintenance()` cho mọi bảng) không bao giờ thấy
    retention khác NULL ⇒ không xoá sớm hơn lượt dọn này. Trả số phân vùng đã xoá theo bảng."""
    out: dict[str, int] = {}
    for ds in PARTITIONED:
        keep = keeps.get(ds)
        if keep is None:
            out[ds] = 0
            continue
        try:
            before = await _partition_count(admin_db, ds)
            await admin_db.execute(text("""
                UPDATE partman.part_config
                SET retention = CAST(:k AS int)::text || ' days', retention_keep_table = false
                WHERE parent_table = :t"""), {"k": keep, "t": ds})
            await admin_db.execute(text("SELECT partman.run_maintenance(p_parent_table => :t)"), {"t": ds})
            dropped = max(0, before - await _partition_count(admin_db, ds))
            await admin_db.execute(text("UPDATE partman.part_config SET retention = NULL WHERE parent_table = :t"),
                                   {"t": ds})
            await admin_db.commit()
        except Exception:
            await admin_db.rollback()
            raise
        out[ds] = dropped
    return out


# ─── xoá theo lô ───────────────────────────────────────────────────────────────────────────────────────────

async def _batched(db: AsyncSession, sql: str, params: dict[str, Any], deadline: float) -> int:
    """Lặp một câu xoá/làm rỗng `LIMIT :lim` — commit mỗi lô, dừng khi lô < BATCH hoặc hết ngân sách thời gian."""
    total = 0
    while True:
        n = int((await db.execute(text(sql), {**params, "lim": BATCH})).rowcount or 0)  # type: ignore[attr-defined]
        await db.commit()
        total += n
        if n < BATCH or time.monotonic() >= deadline:
            return total


async def purge_memory_entries(db: AsyncSession, deadline: float) -> int:
    """Mục sổ tay ĐÃ NÉN (archived_at) quá `keep_days` (đã xác nhận) của tổ chức sở hữu sổ — không bao giờ xoá mục
    ghim."""
    return await _batched(db, """
        DELETE FROM memory.entries WHERE id IN (
          SELECT e.id FROM memory.entries e
          JOIN memory.notebooks n ON n.id = e.notebook_id
          JOIN ops.retention_policies rp ON rp.org_id = n.org_id AND rp.dataset = 'memory.entries'
          WHERE rp.keep_days IS NOT NULL AND rp.confirmed_at IS NOT NULL
            AND e.archived_at IS NOT NULL AND NOT e.is_pinned
            AND e.archived_at < now() - make_interval(days => rp.keep_days)
          LIMIT :lim)""", {}, deadline)


async def purge_browser_results(db: AsyncSession, deadline: float) -> int:
    """Bỏ nội dung đã đọc (`result`) của việc trình duyệt xong quá 14 ngày — giữ dòng việc (trạng thái/chi phí)."""
    return await _batched(db, """
        UPDATE agent.browser_jobs SET result = NULL WHERE id IN (
          SELECT id FROM agent.browser_jobs
          WHERE result IS NOT NULL AND finished_at < now() - make_interval(days => :d)
          LIMIT :lim)""", {"d": BROWSER_RESULT_DAYS}, deadline)


async def purge_browser_proofs(db: AsyncSession, deadline: float, store: Any = None) -> int:
    """Ảnh chụp bằng chứng quá `PROOF_DAYS` (theo `created_at` của việc): bỏ cột proof_key/proof_sha256 rồi xoá object
    (best-effort: lỗi chỉ đếm, cột đã bỏ để không quét lại mãi). Kết quả `result` vẫn theo luật 14 ngày."""
    if store is None:
        from gh.chassis.objects import get_object_store

        store = get_object_store()
    total, failed = 0, 0
    while True:
        rows = (await db.execute(text("""
            WITH old AS (
              SELECT id, proof_key FROM agent.browser_jobs
              WHERE proof_key IS NOT NULL AND created_at < now() - make_interval(days => :d)
              LIMIT :lim FOR UPDATE)
            UPDATE agent.browser_jobs j SET proof_key = NULL, proof_sha256 = NULL
            FROM old WHERE j.id = old.id RETURNING old.proof_key"""), {"d": PROOF_DAYS, "lim": BATCH})).scalars().all()
        await db.commit()
        for key in rows:
            try:
                await store.delete(key)
            except Exception:  # noqa: BLE001 — best-effort
                failed += 1
        total += len(rows)
        if len(rows) < BATCH or time.monotonic() >= deadline:
            break
    if total:
        log.info("Dọn ảnh chụp bằng chứng quá hạn: %d ảnh (%d object không xoá được)", total, failed)
    return total


async def purge_browser_data(db: AsyncSession, deadline: float) -> int:
    """Phần `agent.browser_jobs.result` (14 ngày) + ảnh chụp bằng chứng (90 ngày); trả số `result` đã bỏ."""
    n = await purge_browser_results(db, deadline)
    await db.commit()
    await purge_browser_proofs(db, deadline)
    return n


MCP_ARGS_DIGEST = "agent.mcp_calls.args"
# Đã đổi hết dòng cũ trong một lượt trọn (không chạm hạn thời gian) ⇒ bỏ qua phần này tới khi cờ hết hạn — tránh mỗi
# đêm quét lại cả agent.mcp_calls (điều kiện jsonb không có chỉ mục). TTL 7 ngày: quay về bản cũ (ghi nguyên văn) rồi
# nâng lại thì vẫn được đổi trong vòng một tuần.
MCP_ARGS_DONE_KEY = "gh:retention:mcp_args_digested"
MCP_ARGS_DONE_TTL = 7 * 86400


async def digest_mcp_call_args(db: AsyncSession, deadline: float) -> int:
    """v0.1.45 (F-57): dòng `agent.mcp_calls` cũ (trước v0.1.45) lưu NGUYÊN VĂN tham số tool → đổi sang dấu vết
    {sha256, keys, bytes} như `gh.mcp_api.invoke.args_digest` (sha256 tính trên `args::text` của Postgres). Idempotent:
    dòng đã đúng dạng dấu vết (đúng 3 khoá sha256/keys/bytes, `keys` là mảng) bị bỏ qua — chạy lại không đổi gì; tham
    số tool thật tình cờ có khoá `sha256` vẫn được đổi. Không cần migration (bảng phân vùng, chạy theo lô)."""
    return await _batched(db, """
        UPDATE agent.mcp_calls SET args = jsonb_build_object(
            'sha256', encode(sha256(convert_to(args::text, 'UTF8')), 'hex'),
            'keys', (SELECT coalesce(jsonb_agg(k ORDER BY k), '[]'::jsonb)
                     FROM (SELECT k FROM jsonb_object_keys(args) k ORDER BY k LIMIT 20) ks),
            'bytes', length(args::text))
        WHERE (id, at) IN (
          SELECT id, at FROM agent.mcp_calls
          WHERE jsonb_typeof(args) = 'object' AND NOT (
            args ?& array['sha256', 'keys', 'bytes'] AND jsonb_typeof(args->'keys') = 'array'
            AND (SELECT count(*) FROM jsonb_object_keys(args)) = 3)
          LIMIT :lim)""", {}, deadline)


async def digest_mcp_call_args_once(db: AsyncSession, deadline: float, redis: Any = None) -> int:
    """`digest_mcp_call_args` có cờ Redis "đã xong": cờ còn ⇒ 0 (không quét); một lượt chạy trọn trước hạn thời gian
    ⇒ đặt cờ. Không có Redis / Redis lỗi ⇒ vẫn chạy như cũ."""
    if redis is not None:
        try:
            if await redis.get(MCP_ARGS_DONE_KEY):
                return 0
        except Exception as exc:  # noqa: BLE001
            log.warning("Không đọc được %s: %s", MCP_ARGS_DONE_KEY, exc)
    n = await digest_mcp_call_args(db, deadline)
    if redis is not None and time.monotonic() < deadline:
        try:
            await redis.set(MCP_ARGS_DONE_KEY, "1", ex=MCP_ARGS_DONE_TTL)
        except Exception as exc:  # noqa: BLE001
            log.warning("Không ghi được %s: %s", MCP_ARGS_DONE_KEY, exc)
    return n


async def purge_orphan_attachments(db: AsyncSession, keep_days: int | None, deadline: float,
                                   store: Any = None) -> int:
    """Tệp đính kèm có sự kiện thô đã bị partman xoá theo tháng (cũ hơn hạn, sự kiện không còn) ⇒ xoá dòng + object
    (object best-effort: lỗi chỉ log, dòng vẫn xoá để không quét lại mãi)."""
    if keep_days is None:
        return 0
    if store is None:
        from gh.chassis.objects import get_object_store

        store = get_object_store()
    total, failed = 0, 0
    while True:
        rows = (await db.execute(text("""
            DELETE FROM raw.attachments WHERE id IN (
              SELECT a.id FROM raw.attachments a
              WHERE a.event_received_at < now() - make_interval(days => :k)
                AND NOT EXISTS (SELECT 1 FROM raw.events e
                                WHERE e.id = a.event_id AND e.received_at = a.event_received_at)
              LIMIT :lim)
            RETURNING storage_key"""), {"k": keep_days, "lim": BATCH})).scalars().all()
        await db.commit()
        for key in rows:
            try:
                await store.delete(key)
            except Exception:  # noqa: BLE001 — best-effort
                failed += 1
        total += len(rows)
        if len(rows) < BATCH or time.monotonic() >= deadline:
            break
    if total:
        log.info("Dọn tệp đính kèm quá hạn: %d dòng (%d object không xoá được)", total, failed)
    return total


async def purge_sessions(db: AsyncSession) -> int:
    """Phiên đăng nhập hết hạn/thu hồi quá `GH_SESSION_PURGE_AFTER_DAYS` (job hằng giờ `expire_sessions`). Bên gọi
    commit."""
    from gh.auth import service as auth_service
    from gh.config import get_settings

    return await auth_service.purge_expired_sessions(db, older_than_days=get_settings().session_purge_after_days)


async def purge_gen(db: AsyncSession) -> int:
    from gh.gen import store as gen_store

    n = await gen_store.purge_expired(db)
    await db.commit()
    return n


async def purge_notifications(db: AsyncSession) -> int:
    from gh import notifications

    return await notifications.purge_old(db, commit_each=True)


# ─── hạn lưu chưa xác nhận ─────────────────────────────────────────────────────────────────────────────────

CONFIRM_KIND = "retention.confirm_needed"
CONFIRM_LINK = "/system?tab=storage"
#: Nhắc lại tối đa mỗi 30 ngày (Owner có thể cố ý để nguyên — chuông không được thành rác).
CONFIRM_REMIND_DAYS = 30
CONFIRM_TITLE = "Hạn lưu dữ liệu cần xác nhận lại"
CONFIRM_BODY = ("Hạn lưu đặt trước bản v0.1.40 chưa được thi hành. Từ bản này, dữ liệu quá hạn bị XOÁ VĨNH VIỄN — mở "
                "Hạn lưu dữ liệu, kiểm số ngày rồi bấm Lưu để xác nhận (hoặc để trống = giữ mãi).")
#: Tập dữ liệu mà hạn lưu thật sự xoá dữ liệu (không gồm ops.action_log — không áp dụng).
ENFORCED = tuple(d for d, v in DATASETS.items() if v["mode"] != "not_applicable")


async def unconfirmed_orgs(db: AsyncSession) -> list[Any]:
    """Tổ chức có hạn lưu đã đặt mà CHƯA xác nhận (đặt trước v0.1.40) — việc dọn bỏ qua các dòng này."""
    rows = (await db.execute(text("""
        SELECT org_id, array_agg(dataset ORDER BY dataset) AS datasets FROM ops.retention_policies
        WHERE keep_days IS NOT NULL AND confirmed_at IS NULL AND dataset = ANY(:ds)
        GROUP BY org_id"""), {"ds": list(ENFORCED)})).all()
    return list(rows)


async def notify_unconfirmed(db: AsyncSession, redis: Any = None) -> int:
    """Chuông nhắc Owner xác nhận lại hạn lưu chưa xác nhận (tối đa mỗi `CONFIRM_REMIND_DAYS` ngày). Trả số tổ chức
    đã nhắc. Bên gọi commit."""
    from gh import notifications

    n = 0
    for r in await unconfirmed_orgs(db):
        recent = (await db.execute(text("""
            SELECT 1 FROM core.notifications WHERE org_id = :o AND kind = :k
              AND created_at > now() - make_interval(days => :d) LIMIT 1"""),
            {"o": r.org_id, "k": CONFIRM_KIND, "d": CONFIRM_REMIND_DAYS})).first()
        if recent is not None:
            continue
        owners = await notifications.owner_ids(db, r.org_id)
        if not owners:
            continue
        await notifications.notify(db, r.org_id, owners, kind=CONFIRM_KIND, title=CONFIRM_TITLE, body=CONFIRM_BODY,
                                   link=CONFIRM_LINK, redis=redis)
        n += 1
    return n


async def ensure_leakproof(admin_db: AsyncSession) -> bool:
    """Đặt lại LEAKPROOF cho `similarity_op` (migration 0026) — pg_dump/pg_restore không giữ thuộc tính này của hàm
    thuộc extension ⇒ sau `genh import` dò trùng tên âm thầm về đường chậm. Chạy qua admin session (cần superuser).
    Trả True khi vừa phải đặt lại. Bên gọi commit."""
    leak = (await admin_db.execute(text("""
        SELECT proleakproof FROM pg_proc WHERE oid = to_regprocedure('public.similarity_op(text, text)')"""))
            ).scalar_one_or_none()
    if leak is None or leak:
        return False
    await admin_db.execute(text("ALTER FUNCTION public.similarity_op(text, text) LEAKPROOF"))
    log.info("Đã đặt lại LEAKPROOF cho similarity_op (mất sau khi khôi phục dữ liệu)")
    return True


# ─── lượt dọn hằng ngày ────────────────────────────────────────────────────────────────────────────────────

async def read_last(redis: Any) -> dict[str, Any] | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(LAST_KEY)
        data = orjson.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001 — chỉ là thông tin hiển thị
        log.warning("Không đọc được %s: %s", LAST_KEY, exc)
        return None
    return data if isinstance(data, dict) else None


async def retention_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron 05:00 giờ VN: sync partman → xoá phân vùng quá hạn → các lô → hội thoại Gen → chuông. Mỗi phần chạy
    trong session riêng, lỗi một phần chỉ log và không chặn phần khác."""
    from gh.db import admin_sessionmaker, sessionmaker

    deadline = time.monotonic() + TIME_BUDGET_S
    datasets: dict[str, dict[str, Any]] = {"ops.action_log": {"mode": "not_applicable", "deleted": 0, "ok": True}}

    async def part(name: str, mode: str, fn: Callable[[], Awaitable[int]]) -> None:
        try:
            datasets[name] = {"mode": mode, "deleted": int(await fn()), "ok": True}
        except Exception:  # noqa: BLE001 — một phần lỗi không chặn phần khác
            log.exception("Dọn dữ liệu quá hạn lỗi ở phần %s", name)
            datasets[name] = {"mode": mode, "deleted": 0, "ok": False}

    keeps: dict[str, int | None] = {}
    dropped: dict[str, int] = {}

    async def do_partman() -> int:
        async with admin_sessionmaker()() as adb:
            keeps.update(await partman_keeps(adb))
            dropped.update(await drop_expired_partitions(adb, keeps))
        return sum(dropped.values())

    try:
        await do_partman()
        for ds in PARTITIONED:
            datasets[ds] = {"mode": "partition", "deleted": dropped.get(ds, 0), "ok": True}
    except Exception:  # noqa: BLE001
        log.exception("Dọn dữ liệu quá hạn lỗi ở phần partman")
        for ds in PARTITIONED:
            datasets[ds] = {"mode": "partition", "deleted": dropped.get(ds, 0), "ok": False}

    sm = sessionmaker()

    def in_session(fn: Callable[[AsyncSession], Awaitable[int]]) -> Callable[[], Awaitable[int]]:
        async def run() -> int:
            async with sm() as db:
                return await fn(db)
        return run

    await part("memory.entries", "batch", in_session(lambda db: purge_memory_entries(db, deadline)))
    await part(BROWSER_RESULT, "batch", in_session(lambda db: purge_browser_data(db, deadline)))
    if "raw.events" in keeps:
        raw_keep = keeps["raw.events"]
    else:  # partman lỗi — vẫn tính hạn từ bảng chính sách (chỉ đọc)
        async with sm() as db:
            raw_keep = await effective_keep_days(db, "raw.events")
    await part("raw.attachments", "batch", in_session(lambda db: purge_orphan_attachments(db, raw_keep, deadline)))
    await part("agent.gen_conversations", "batch", in_session(purge_gen))
    await part("core.notifications", "batch", in_session(purge_notifications))
    # Đổi dòng nhật ký MCP cũ chạy SAU các phần xoá quá hạn — lần đầu sau nâng cấp (bảng lớn) không ăn hết thời gian
    # của chúng.
    await part(MCP_ARGS_DIGEST, "batch",
               in_session(lambda db: digest_mcp_call_args_once(db, deadline, ctx.get("redis"))))
    try:  # hạn lưu đặt trước v0.1.40 chưa xác nhận ⇒ không xoá, chỉ nhắc Owner
        async with sm() as db:
            await notify_unconfirmed(db, ctx.get("redis"))
            await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Không gửi được chuông nhắc xác nhận hạn lưu")

    summary = {"at": _utc_iso(), "datasets": datasets}
    redis = ctx.get("redis")
    if redis is not None:
        try:
            await redis.set(LAST_KEY, orjson.dumps(summary), ex=LAST_TTL)
        except Exception as exc:  # noqa: BLE001
            log.warning("Không ghi được %s: %s", LAST_KEY, exc)
    return summary


__all__ = ["BATCH", "CONFIRM_KIND", "DATASETS", "ENFORCED", "FIXED", "LAST_KEY", "PARTITIONED", "effective_keep_days",
           "clear_partman_retention", "digest_mcp_call_args", "digest_mcp_call_args_once", "drop_expired_partitions",
           "ensure_leakproof", "notify_unconfirmed", "partman_keeps", "read_last", "retention_sweep",
           "unconfirmed_orgs"]
