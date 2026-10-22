"""v0.1.40 (F-2): hạn lưu dữ liệu THẬT (gh/retention.py).

- Bảng phân vùng: hạn hiệu lực = MAX qua mọi tổ chức; tổ chức chưa đặt ⇒ giữ mãi. PATCH KHÔNG đẩy sang partman;
  chỉ lượt dọn 05:00 đặt `partman.part_config.retention` trong giao dịch riêng, xoá tháng quá hạn, đếm rồi trả về NULL
  ⇒ bảo trì phân vùng 23:20/04:20 không xoá sớm hơn giờ câu xác nhận đã hứa.
- ops.action_log: "Không áp dụng" — PATCH keep_days ⇒ 422 tiếng Việt; GET luôn keep_days null.
- Xoá theo lô: memory.entries (đã nén, quá hạn, không ghim), browser_jobs.result 14 ngày, tệp đính kèm mồ côi; gom
  hội thoại Gen + chuông vào `retention_sweep`; ghi `gh:retention:last`; một phần lỗi không chặn phần khác.
"""

from collections import Counter
from pathlib import Path
from typing import Any

import orjson
import psycopg
import pytest
from redis.asyncio import Redis
from sqlalchemy import event, text

from gh import retention
from gh.chassis import objects
from gh.db import admin_sessionmaker, get_engine
from gh.memory import notebook
from tests.conftest import PG, Api, verify_pin
from tests.phase2 import org_id

SQL_0026 = Path(__file__).resolve().parents[3] / "db" / "sql" / "0026_v0140_retention_jobs.sql"


async def test_migration_0026_is_rerunnable(fresh_db: str) -> None:
    """Migration 0026 chạy lại (lần 2, lần 3 sau khi alembic đã chạy lần 1) không lỗi, không nhân đôi policy/chỉ mục."""
    sql = SQL_0026.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        pol = c.execute("""SELECT count(*) FROM pg_policies
                           WHERE schemaname = 'ops' AND tablename = 'job_watermarks'""").fetchone()
        idx = c.execute("""SELECT count(*) FROM pg_indexes WHERE indexname IN (
                           'browser_jobs_finished_result_idx', 'memory_entries_archived_idx',
                           'attachments_event_received_idx')""").fetchone()
        lower_idx = c.execute("""SELECT count(*) FROM pg_indexes
                                 WHERE indexname = 'persons_lower_name_trgm_idx'""").fetchone()
        cols = c.execute("""SELECT count(*) FROM information_schema.columns
                            WHERE (table_schema, table_name, column_name) IN
                              (('ops', 'retention_policies', 'confirmed_at'), ('ops', 'job_watermarks', 'full_at'))"""
                         ).fetchone()
    assert pol is not None and pol[0] == 1
    assert idx is not None and idx[0] == 3
    assert lower_idx is not None and lower_idx[0] == 0                # chỉ mục lower() không dùng được dưới RLS — bỏ
    assert cols is not None and cols[0] == 2


async def _part_config(table: str) -> Any:
    async with admin_sessionmaker()() as adb:
        return (await adb.execute(text("""SELECT retention, retention_keep_table FROM partman.part_config
                                          WHERE parent_table = :t"""), {"t": table})).one()


async def _patch(api: Api, dataset: str, keep: int | None, confirm: bool = True) -> Any:
    return await api.send("PATCH", "/retention-policies", {"dataset": dataset, "keep_days": keep,
                                                            "confirm_delete": confirm})


async def _partitions(table: str) -> int:
    async with admin_sessionmaker()() as adb:
        return int((await adb.execute(text("SELECT count(*) FROM partman.show_partitions(:t)"), {"t": table})
                    ).scalar_one())


@pytest.mark.parametrize("dataset", ["raw.events", "clean.meaning_units", "agent.model_calls"])
async def test_patch_sets_effective_keep_without_touching_partman(owner_api: Api, db, dataset: str) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await verify_pin(api)
    r = await _patch(api, dataset, 30)
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["dataset"] == dataset)
    assert row["keep_days"] == 30 and row["mode"] == "partition" and "partman_synced" not in row
    assert await retention.effective_keep_days(db, dataset) == 30
    assert (await _part_config(dataset)).retention is None            # chỉ lượt dọn 05:00 đặt (rồi trả về NULL)

    r = await _patch(api, dataset, None)
    assert r.status_code == 200, r.text
    assert await retention.effective_keep_days(db, dataset) is None

    # Hai tổ chức 30 và 90 ⇒ hạn hiệu lực 90 (MAX); tổ chức thứ hai chưa đặt ⇒ giữ mãi.
    org2 = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('Tổ chức 2') RETURNING id"))
            ).scalar_one()
    await db.commit()
    r = await _patch(api, dataset, 30)
    assert r.status_code == 200
    assert await retention.effective_keep_days(db, dataset) is None
    await db.execute(text("""INSERT INTO ops.retention_policies (org_id, dataset, keep_days, confirmed_at)
                             VALUES (:o, :d, 90, now())"""), {"o": org2, "d": dataset})
    await db.commit()
    assert await retention.effective_keep_days(db, dataset) == 90


async def test_partition_maintenance_never_drops_only_sweep_does(owner_api: Api, db, redis: Redis) -> None:  # type: ignore[no-untyped-def]
    """Owner xác nhận lúc 22:00 ⇒ bảo trì phân vùng 23:20/04:20 KHÔNG xoá tháng nào (kể cả retention cũ còn sót trong
    part_config); chỉ lượt dọn 05:00 xoá và ghi đúng số tháng đã xoá cho dòng hạn lưu."""
    from gh import worker

    api = owner_api
    await verify_pin(api)
    assert (await _patch(api, "raw.events", 30)).status_code == 200
    async with admin_sessionmaker()() as adb:  # giá trị sót từ bản dev v0.1.40 trước (PATCH từng đẩy ngay)
        await adb.execute(text("UPDATE partman.part_config SET retention = '30 days', retention_keep_table = false "
                               "WHERE parent_table = 'raw.events'"))
        await adb.commit()
    before = await _partitions("raw.events")
    await worker.partition_maintenance({})
    assert await _partitions("raw.events") == before
    assert (await _part_config("raw.events")).retention is None

    out = await retention.retention_sweep({"redis": redis})
    after = await _partitions("raw.events")
    assert after < before                                               # tháng cũ hơn 30 ngày đã bị xoá ở 05:00
    assert out["datasets"]["raw.events"] == {"mode": "partition", "deleted": before - after, "ok": True}
    assert (await _part_config("raw.events")).retention is None        # trả về NULL sau lượt dọn
    got = {x["dataset"]: x for x in (await api.get("/retention-policies")).json()}
    assert got["raw.events"]["last_deleted"] == before - after and got["raw.events"]["last_ok"] is True
    await worker.partition_maintenance({})
    assert await _partitions("raw.events") >= after                     # bảo trì chỉ tạo thêm, không xoá


async def test_patch_requires_delete_confirmation(owner_api: Api) -> None:
    """Đặt hạn cho tập dữ liệu bị xoá thật mà không xác nhận ⇒ 422 tiếng Việt, không ghi gì, partman giữ nguyên."""
    api = owner_api
    await verify_pin(api)
    for ds in ("raw.events", "memory.entries"):
        r = await _patch(api, ds, 30, confirm=False)
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["code"] == "RETENTION_CONFIRM_REQUIRED"
        assert "XOÁ VĨNH VIỄN" in body["errors"]["keep_days"] and "30 ngày" in body["errors"]["keep_days"]
    assert (await _part_config("raw.events")).retention is None
    got = {x["dataset"]: x for x in (await api.get("/retention-policies")).json()}
    assert got["raw.events"]["keep_days"] is None and got["raw.events"]["needs_confirm"] is False
    # Bỏ hạn (giữ mãi) không xoá gì ⇒ không cần xác nhận.
    assert (await _patch(api, "raw.events", None, confirm=False)).status_code == 200


async def test_preexisting_unconfirmed_policy_not_enforced(owner_api: Api, db, redis: Redis) -> None:  # type: ignore[no-untyped-def]
    """Hạn lưu đặt TRƯỚC v0.1.40 (chỉ hiển thị, confirmed_at NULL) KHÔNG được đẩy sang partman/xoá theo lô; Owner nhận
    chuông nhắc xác nhận (một lần trong 30 ngày); lưu lại có xác nhận ⇒ mới thi hành."""
    api = owner_api
    org = await org_id(db)
    await db.execute(text("""INSERT INTO ops.retention_policies (org_id, dataset, keep_days) VALUES
                             (:o, 'raw.events', 30), (:o, 'memory.entries', 30), (:o, 'ops.action_log', 30)"""),
                     {"o": org})
    await db.commit()
    assert await retention.effective_keep_days(db, "raw.events") is None
    assert {r.org_id: r.datasets for r in await retention.unconfirmed_orgs(db)} == {
        org: ["memory.entries", "raw.events"]}
    assert await retention.partman_keeps(db) == {d: None for d in retention.PARTITIONED}
    got = {x["dataset"]: x for x in (await api.get("/retention-policies")).json()}
    assert got["raw.events"]["keep_days"] == 30 and got["raw.events"]["needs_confirm"] is True
    assert got["memory.entries"]["needs_confirm"] is True
    assert got["ops.action_log"]["needs_confirm"] is False

    out = await retention.retention_sweep({"redis": redis})
    assert out["datasets"]["raw.events"]["ok"] is True
    assert (await _part_config("raw.events")).retention is None
    rows = (await db.execute(text("""SELECT title, link FROM core.notifications
                                     WHERE org_id = :o AND kind = :k"""), {"o": org, "k": retention.CONFIRM_KIND})
            ).all()
    assert len(rows) == 1 and rows[0].title == "Hạn lưu dữ liệu cần xác nhận lại"
    assert rows[0].link == "/system?tab=storage"
    await retention.retention_sweep({"redis": redis})              # lượt sau trong 30 ngày: không nhắc lại
    assert (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = :k"),
                             {"k": retention.CONFIRM_KIND})).scalar_one() == 1

    await verify_pin(api)
    r = await _patch(api, "raw.events", 30)
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["dataset"] == "raw.events")
    assert row["needs_confirm"] is False
    assert await retention.effective_keep_days(db, "raw.events") == 30


async def test_manager_cannot_set_partitioned_retention(client, db, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    """Bảng phân vùng (xoá cả tháng cho mọi tổ chức) chỉ Owner đổi được; Manager có system.manage vẫn đổi hạn sổ tay."""
    from tests.test_rbac_api import login_as

    await db.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                             SELECT id, 'system.manage', 'all' FROM core.roles WHERE code = 'manager'
                             ON CONFLICT (role_id, permission_code) DO UPDATE SET scope = 'all'"""))
    await db.commit()
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    r = await _patch(mgr, "raw.events", 30)
    assert r.status_code == 403, r.text
    assert await retention.effective_keep_days(db, "raw.events") is None
    assert (await _patch(mgr, "memory.entries", 30)).status_code == 200


async def test_ensure_leakproof_restores_attribute(app) -> None:  # type: ignore[no-untyped-def]
    """pg_restore không giữ LEAKPROOF của similarity_op ⇒ partition_maintenance đặt lại."""
    async with admin_sessionmaker()() as adb:
        await adb.execute(text("ALTER FUNCTION public.similarity_op(text, text) NOT LEAKPROOF"))
        await adb.commit()
        assert await retention.ensure_leakproof(adb) is True
        await adb.commit()
        assert await retention.ensure_leakproof(adb) is False
        leak = (await adb.execute(text(
            "SELECT proleakproof FROM pg_proc WHERE oid = 'similarity_op(text,text)'::regprocedure"))).scalar_one()
    assert leak is True


async def test_action_log_not_applicable(owner_api: Api) -> None:
    api = owner_api
    await verify_pin(api)
    r = await _patch(api, "ops.action_log", 10)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "RETENTION_NOT_APPLICABLE"
    assert body["errors"]["keep_days"] == "Nhật ký hành động chưa áp dụng hạn lưu (chuỗi chống sửa)"
    r = await _patch(api, "ops.action_log", None)
    assert r.status_code == 200
    got = {x["dataset"]: x for x in (await api.get("/retention-policies")).json()}
    al = got["ops.action_log"]
    assert al["mode"] == "not_applicable" and al["keep_days"] is None and al["editable"] is False
    assert al["note"] == "Không áp dụng — nhật ký chống sửa được giữ nguyên"
    pc = await _part_config("ops.action_log")
    assert pc.retention is None


async def test_get_has_mode_note_and_browser_row(owner_api: Api, redis: Redis) -> None:
    api = owner_api
    rows = (await api.get("/retention-policies")).json()
    got = {x["dataset"]: x for x in rows}
    assert list(got)[:5] == ["raw.events", "clean.meaning_units", "ops.action_log", "memory.entries",
                             "agent.model_calls"]
    for x in rows:
        assert {"dataset", "keep_days", "anonymize_after_days", "mode", "editable", "note", "last_run_at",
                "last_deleted"} <= set(x)
        assert isinstance(x["note"], str) and x["note"]
    assert got["raw.events"]["note"] == "Xoá theo tháng: cả tháng quá hạn mới bị xoá"
    assert got["memory.entries"]["mode"] == "batch"
    assert got["memory.entries"]["note"] == "Chỉ xoá mục sổ tay đã nén; mục ghim giữ mãi"
    br = got["agent.browser_jobs.result"]
    assert br["keep_days"] == 14 and br["editable"] is False and br["mode"] == "batch"
    assert got["raw.events"]["last_run_at"] is None

    await redis.set(retention.LAST_KEY, orjson.dumps({"at": "2026-10-01T22:00:00Z", "datasets": {
        "memory.entries": {"mode": "batch", "deleted": 12}}}))
    got = {x["dataset"]: x for x in (await api.get("/retention-policies")).json()}
    assert got["memory.entries"]["last_run_at"] == "2026-10-01T22:00:00Z"
    assert got["memory.entries"]["last_deleted"] == 12 and got["memory.entries"]["last_ok"] is True
    assert got["raw.events"]["last_deleted"] is None and got["raw.events"]["last_ok"] is None

    # Lượt dọn của tập lỗi ⇒ last_ok false (web báo lỗi, không hiện "đã xoá 0" như thành công).
    await redis.set(retention.LAST_KEY, orjson.dumps({"at": "2026-10-01T22:00:00Z", "datasets": {
        "memory.entries": {"mode": "batch", "deleted": 0, "ok": False}}}))
    got = {x["dataset"]: x for x in (await api.get("/retention-policies")).json()}
    assert got["memory.entries"]["last_ok"] is False and got["memory.entries"]["last_deleted"] == 0


# ─── lượt dọn hằng ngày ─────────────────────────────────────────────────────────────────────────────────────

class _FakeStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes) -> None:
        return None

    async def get(self, key: str) -> bytes:
        return b""

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


@pytest.fixture
async def swept(owner_api: Api, db, redis: Redis, monkeypatch):  # type: ignore[no-untyped-def]
    org = await org_id(db)
    uid = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one()
    pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                                    VALUES (:o, 'PER-R1', 'Chị Mai') RETURNING id"""), {"o": org})).scalar_one()
    nb = await notebook.ensure(db, org, "person", pid)
    await db.execute(text("""INSERT INTO ops.retention_policies (org_id, dataset, keep_days, confirmed_at)
                             VALUES (:o, 'memory.entries', 30, now()), (:o, 'raw.events', 30, now())"""), {"o": org})
    await db.execute(text("""
        INSERT INTO memory.entries (notebook_id, section, body, author, is_pinned, archived_at)
        SELECT :nb, 'rolling_context', 'cũ ' || g, 'agent:test', false, now() - interval '40 days'
        FROM generate_series(1, 12005) g"""), {"nb": nb.id})
    await db.execute(text("""
        INSERT INTO memory.entries (notebook_id, section, body, author, is_pinned, archived_at) VALUES
          (:nb, 'rolling_context', 'ghim cũ', 'user:x', true, now() - interval '400 days'),
          (:nb, 'rolling_context', 'nén gần đây', 'agent:test', false, now() - interval '5 days'),
          (:nb, 'rolling_context', 'đang dùng', 'agent:test', false, NULL)"""), {"nb": nb.id})
    acc = (await db.execute(text("""INSERT INTO core.social_accounts (org_id, platform, label)
                                    VALUES (:o, 'facebook_personal', 'FB') RETURNING id"""), {"o": org})).scalar_one()
    jobs = {}
    for days in (15, 13):
        jobs[days] = (await db.execute(text("""
            INSERT INTO agent.browser_jobs (org_id, account_id, kind, status, result, finished_at)
            VALUES (:o, :a, 'read', 'done', '{"posts": ["x"]}', now() - make_interval(days => :d)) RETURNING id"""),
            {"o": org, "a": acc, "d": days})).scalar_one()
    gen_old = (await db.execute(text("""INSERT INTO agent.gen_conversations (org_id, user_id, last_at)
                                        VALUES (:o, :u, now() - interval '200 days') RETURNING id"""),
                                {"o": org, "u": uid})).scalar_one()
    await db.execute(text("""INSERT INTO core.notifications (org_id, user_id, kind, title, created_at)
                             VALUES (:o, :u, 'test.old', 'cũ', now() - interval '100 days')"""), {"o": org, "u": uid})
    await db.execute(text("""
        INSERT INTO raw.attachments (event_id, event_received_at, storage_key, mime, bytes, sha256) VALUES
          (core.uuid_v7(), now() - interval '60 days', 'att/old.bin', 'image/png', 1, '\\x00'),
          (core.uuid_v7(), now() - interval '2 days', 'att/new.bin', 'image/png', 1, '\\x00')"""))
    await db.commit()
    store = _FakeStore()
    monkeypatch.setattr(objects, "_store", store)
    return {"org": org, "nb": nb.id, "jobs": jobs, "gen_old": gen_old, "store": store}


async def test_retention_sweep_batches_and_summary(swept, db, redis: Redis) -> None:  # type: ignore[no-untyped-def]
    seen: Counter[str] = Counter()

    def on_exec(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
        if "DELETE FROM memory.entries" in statement:
            seen["memory"] += 1

    sync_engine = get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", on_exec)
    try:
        out = await retention.retention_sweep({"redis": redis})
    finally:
        event.remove(sync_engine, "before_cursor_execute", on_exec)

    ds = out["datasets"]
    assert ds["memory.entries"] == {"mode": "batch", "deleted": 12005, "ok": True}
    assert seen["memory"] >= 3                                          # 5000 + 5000 + 2005
    # Sắp ở Python (theo mã ký tự), không ORDER BY trong SQL: thứ tự chuỗi tiếng Việt phụ thuộc
    # collation của CSDL (C.UTF-8 đặt "đ" sau "n", en_US.utf8 của image CI đặt "đ" cạnh "d").
    left = sorted((await db.execute(text("SELECT body FROM memory.entries WHERE notebook_id = :n"),
                                    {"n": swept["nb"]})).scalars().all())
    assert left == ["ghim cũ", "nén gần đây", "đang dùng"]

    res = dict((await db.execute(text("SELECT id, result FROM agent.browser_jobs"))).all())
    assert res[swept["jobs"][15]] is None and res[swept["jobs"][13]] == {"posts": ["x"]}
    assert (await db.execute(text("SELECT count(*) FROM agent.browser_jobs"))).scalar_one() == 2  # giữ dòng việc
    assert ds["agent.browser_jobs.result"]["deleted"] == 1

    assert (await db.execute(text("SELECT count(*) FROM agent.gen_conversations WHERE id = :i"),
                             {"i": swept["gen_old"]})).scalar_one() == 0
    assert (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'test.old'"))
            ).scalar_one() == 0
    assert ds["agent.gen_conversations"]["deleted"] >= 1 and ds["core.notifications"]["deleted"] >= 1

    keys = (await db.execute(text("SELECT storage_key FROM raw.attachments"))).scalars().all()
    assert keys == ["att/new.bin"] and swept["store"].deleted == ["att/old.bin"]
    assert ds["raw.attachments"]["deleted"] == 1

    assert ds["ops.action_log"]["mode"] == "not_applicable"
    assert ds["raw.events"]["mode"] == "partition" and ds["raw.events"]["ok"] is True
    pc = await _part_config("raw.events")
    assert pc.retention is None and pc.retention_keep_table is False        # sweep đặt rồi trả về NULL

    saved = orjson.loads(await redis.get(retention.LAST_KEY))
    assert saved["at"].endswith("Z") and saved["datasets"]["memory.entries"]["deleted"] == 12005
    assert 0 < await redis.ttl(retention.LAST_KEY) <= 14 * 86400


async def test_one_failing_part_does_not_block_others(swept, db, redis: Redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def boom(*a: Any, **kw: Any) -> int:
        raise RuntimeError("hỏng thử")

    monkeypatch.setattr(retention, "purge_memory_entries", boom)
    monkeypatch.setattr(retention, "partman_keeps", boom)
    out = await retention.retention_sweep({"redis": redis})
    ds = out["datasets"]
    assert ds["memory.entries"]["ok"] is False and ds["raw.events"]["ok"] is False
    assert ds["agent.browser_jobs.result"] == {"mode": "batch", "deleted": 1, "ok": True}
    assert ds["agent.gen_conversations"]["ok"] is True and ds["core.notifications"]["ok"] is True
    assert ds["raw.attachments"]["deleted"] == 1                         # hạn raw.events đọc lại từ bảng chính sách
    assert (await db.execute(text("SELECT count(*) FROM memory.entries"))).scalar_one() == 12008
    assert orjson.loads(await redis.get(retention.LAST_KEY))["datasets"]["memory.entries"]["ok"] is False


async def test_worker_jobs_route_through_retention(swept, db, redis: Redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from gh import worker

    calls: list[str] = []
    real_gen, real_notif, real_sess = retention.purge_gen, retention.purge_notifications, retention.purge_sessions

    async def spy_gen(d: Any) -> int:
        calls.append("gen")
        return await real_gen(d)

    async def spy_notif(d: Any) -> int:
        calls.append("notif")
        return await real_notif(d)

    async def spy_sess(d: Any) -> int:
        calls.append("sess")
        return await real_sess(d)

    monkeypatch.setattr(retention, "purge_gen", spy_gen)
    monkeypatch.setattr(retention, "purge_notifications", spy_notif)
    monkeypatch.setattr(retention, "purge_sessions", spy_sess)
    assert await worker.purge_gen_conversations({}) >= 1
    assert await worker.purge_notifications({}) >= 1
    assert await worker.expire_sessions({}) >= 0
    assert calls == ["gen", "notif", "sess"]
    summary = await worker.retention_sweep({"redis": redis})
    assert summary["datasets"]["memory.entries"]["deleted"] == 12005
