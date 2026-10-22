"""PLAN §5.6 — Backup/restore.

Hai lớp kiểm:
1. `select_retained`/`is_due`: thuật toán THUẦN (không đụng DB/đĩa) — kiểm vòng đời GFS 7 ngày/4 tuần/12 tháng
   với lịch sử tổng hợp nhiều tháng, không cần dựng dữ liệu thật kéo dài cả năm.
2. Round-trip THẬT trên Postgres 16 thật: `pg_dump` → mã hoá (`gh.crypto`) → `ObjectStore` đĩa cục bộ → xoá
   sạch (khôi phục vào CSDL đích trống, khác CSDL nguồn — không đụng CSDL đang dùng của bộ test khác chạy
   song song) → `pg_restore` → đối chiếu KHÔNG chỉ tổng số dòng mà cả giá trị cụ thể (mã cơ hội, số tiền, tên
   người) khớp `docs/design/seed-data.json` qua `gh.seed_demo`.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import text

from gh import crypto
from gh.backup import BackupEntry, _backup_key, is_due, list_backups, prune, restore_backup, run_backup, select_retained
from gh.chassis.objects import LocalObjectStore
from gh.config import get_settings
from gh.db import sessionmaker
from gh.seed_demo import seed_demo
from tests.conftest import PG


def _set_backup_key(monkeypatch, hex_key: str) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_BACKUP_KEY", hex_key)
    get_settings.cache_clear()

# ═══ vòng đời GFS (thuần) ═══════════════════════════════════════════════════════

def _entry(day_offset: int, key: str | None = None, *, hour: int = 2) -> BackupEntry:
    ts = datetime(2026, 1, 1, hour, tzinfo=UTC) - timedelta(days=day_offset)
    return BackupEntry(key=key or f"d{day_offset}", taken_at=ts, database="gh", size_bytes=1, sha256="x")


def test_select_retained_empty() -> None:
    assert select_retained([]) == set()


def test_select_retained_fewer_than_daily_cap_keeps_all() -> None:
    entries = [_entry(i) for i in range(5)]
    assert select_retained(entries) == {e.key for e in entries}


def test_select_retained_one_year_of_daily_backups_keeps_exactly_23() -> None:
    """400 ngày liên tục có backup hằng ngày → đúng 7 (ngày) + 4 (tuần) + 12 (tháng) = 23 bản còn giữ, 7 ngày
    gần nhất chắc chắn nằm trong đó, phần còn lại (377 bản) bị dọn."""
    entries = [_entry(i) for i in range(400)]
    keep = select_retained(entries)
    assert len(keep) == 7 + 4 + 12
    assert {f"d{i}" for i in range(7)} <= keep
    kept_offsets = sorted(int(k[1:]) for k in keep)
    assert kept_offsets[0] == 0  # bản mới nhất luôn được giữ
    assert kept_offsets == sorted(set(kept_offsets))  # không trùng


def test_select_retained_multiple_same_day_keeps_only_latest() -> None:
    ts = datetime(2026, 1, 1, 12, tzinfo=UTC)
    older = BackupEntry(key="sang", taken_at=ts, database="gh", size_bytes=1, sha256="x")
    newer = BackupEntry(key="toi", taken_at=ts + timedelta(hours=6), database="gh", size_bytes=1, sha256="x")
    assert select_retained([older, newer]) == {"toi"}


def test_select_retained_eight_consecutive_days_one_falls_to_weekly() -> None:
    """8 ngày liên tục, ít hơn 23 tổng → không có gì bị dọn: 7 gần nhất theo hạng ngày, ngày thứ 8 (cũ nhất)
    không lọt hạng ngày nhưng vẫn có chỗ ở hạng tuần (4 tuần còn trống) nên vẫn được giữ."""
    entries = [_entry(i) for i in range(8)]
    keep = select_retained(entries)
    assert keep == {e.key for e in entries}


def test_select_retained_beyond_all_buckets_are_pruned() -> None:
    """> 23 bản rải xa nhau (mỗi 20 ngày, hơn 2 năm) → chỉ 23 bản tồn tại lâu nhất trong 3 vòng được giữ,
    phần vượt vòng tháng (> tháng thứ 12 kể từ vòng tuần) bị dọn hẳn."""
    entries = [_entry(i) for i in range(0, 900, 20)]  # 45 bản, mỗi bản một ngày riêng biệt
    keep = select_retained(entries)
    assert len(keep) <= 7 + 4 + 12
    assert len(keep) < len(entries)  # có dọn thật, không giữ hết
    # 7 bản gần nhất (theo ngày) luôn còn
    newest_seven = sorted(entries, key=lambda e: e.taken_at, reverse=True)[:7]
    assert {e.key for e in newest_seven} <= keep


# ═══ lịch chạy theo cấu hình bước 11 ════════════════════════════════════════════

NOW = datetime(2026, 6, 15, 2, 3, tzinfo=UTC)  # 02:03 UTC — trong cửa sổ 15' quanh "02:00"


def test_is_due_daily_first_run_in_window() -> None:
    cfg = {"frequency": "daily", "time_of_day": "02:00"}
    assert is_due(cfg, last_at=None, now=NOW) is True


def test_is_due_daily_outside_window() -> None:
    cfg = {"frequency": "daily", "time_of_day": "02:00"}
    outside = NOW.replace(hour=10)
    assert is_due(cfg, last_at=None, now=outside) is False


def test_is_due_daily_already_ran_today() -> None:
    cfg = {"frequency": "daily", "time_of_day": "02:00"}
    assert is_due(cfg, last_at=NOW.replace(hour=1), now=NOW) is False


def test_is_due_daily_ran_yesterday() -> None:
    cfg = {"frequency": "daily", "time_of_day": "02:00"}
    assert is_due(cfg, last_at=NOW - timedelta(days=1), now=NOW) is True


def test_is_due_weekly_same_iso_week_not_due() -> None:
    cfg = {"frequency": "weekly", "time_of_day": "02:00"}
    monday = NOW - timedelta(days=NOW.weekday())
    assert is_due(cfg, last_at=monday, now=NOW) is False


def test_is_due_weekly_previous_week_due() -> None:
    cfg = {"frequency": "weekly", "time_of_day": "02:00"}
    assert is_due(cfg, last_at=NOW - timedelta(days=8), now=NOW) is True


def test_is_due_monthly_same_month_not_due() -> None:
    cfg = {"frequency": "monthly", "time_of_day": "02:00"}
    assert is_due(cfg, last_at=NOW.replace(day=1), now=NOW) is False


def test_is_due_monthly_previous_month_due() -> None:
    cfg = {"frequency": "monthly", "time_of_day": "02:00"}
    assert is_due(cfg, last_at=NOW.replace(month=5), now=NOW) is True


# ═══ khoá backup riêng (`GH_BACKUP_KEY`, HANDOFF-v0.1.2 mục 1) ═════════════════════

def test_backup_key_unset_returns_none() -> None:
    get_settings.cache_clear()
    assert _backup_key() is None


def test_backup_key_valid_hex_returns_32_bytes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    hex_key = os.urandom(32).hex()
    _set_backup_key(monkeypatch, hex_key)
    key = _backup_key()
    assert key == bytes.fromhex(hex_key)
    assert len(key) == 32  # type: ignore[arg-type]


def test_backup_key_wrong_length_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _set_backup_key(monkeypatch, os.urandom(16).hex())  # 16 byte, không phải 32
    with pytest.raises(ValueError, match="32 byte"):
        _backup_key()


def test_backup_key_not_hex_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _set_backup_key(monkeypatch, "khong-phai-hex" * 5)
    with pytest.raises(ValueError, match="hex"):
        _backup_key()


def test_backup_entry_from_json_missing_key_id_defaults_master() -> None:
    """Bản backup CŨ (ghi trước khi có trường `key_id`) không có khoá này trong JSON — tương thích ngược:
    mặc định "master" (mọi bản cũ đều mã hoá bằng khoá master, vì chưa có khoá backup riêng lúc đó)."""
    d = {"key": "backups/old.pgcustom.enc", "taken_at": "2026-01-01T02:00:00+00:00", "database": "gh",
         "size_bytes": 10, "sha256": "x"}
    entry = BackupEntry.from_json(d)
    assert entry.key_id == "master"


def test_backup_entry_to_json_roundtrip_keeps_key_id() -> None:
    entry = BackupEntry(key="k", taken_at=datetime(2026, 1, 1, tzinfo=UTC), database="gh", size_bytes=1,
                        sha256="x", key_id="backup")
    assert BackupEntry.from_json(entry.to_json()).key_id == "backup"


async def test_run_backup_without_backup_key_uses_master(tmp_path, scratch_db) -> None:  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    store = LocalObjectStore(root=str(tmp_path / "objects"))
    entry = await run_backup(database_url=f"{PG}/{scratch_db}", store=store)
    assert entry.key_id == "master"
    enc = await store.get(entry.key)
    # Giải mã trực tiếp bằng khoá master hiện hành (không key= riêng) phải thành công — đúng hành vi cũ.
    assert crypto.decrypt(enc, associated=b"gh-backup-v1")[:4] == b"PGDM"


async def test_run_backup_with_backup_key_uses_backup_key_and_restores(  # type: ignore[no-untyped-def]
    tmp_path, monkeypatch, scratch_db
) -> None:
    """Có cấu hình `GH_BACKUP_KEY`: bản backup MỚI mã hoá bằng khoá đó (`key_id == "backup"`), giải mã bằng
    khoá master hiện hành phải THẤT BẠI (đã đổi khoá thật), và `restore_backup` (tự tra `key_id` từ manifest)
    vẫn khôi phục đúng."""
    hex_key = os.urandom(32).hex()
    _set_backup_key(monkeypatch, hex_key)
    store = LocalObjectStore(root=str(tmp_path / "objects"))

    with psycopg.connect(f"{PG}/{scratch_db}", autocommit=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS t_bkey (id serial primary key, v text)")
        c.execute("DELETE FROM t_bkey")
        c.execute("INSERT INTO t_bkey (v) VALUES ('khoa-rieng')")

    entry = await run_backup(database_url=f"{PG}/{scratch_db}", store=store)
    assert entry.key_id == "backup"

    enc = await store.get(entry.key)
    with pytest.raises(InvalidTag):
        crypto.decrypt(enc, associated=b"gh-backup-v1")  # khoá master hiện hành KHÔNG mở được bản này

    target = f"gh_backup_bkey_target_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
        c.execute(f"CREATE DATABASE {target}")
    try:
        await restore_backup(entry.key, database_url=f"{PG}/{scratch_db}", target_database=target, store=store)
        with psycopg.connect(f"{PG}/{target}", autocommit=True) as c:
            rows = c.execute("SELECT v FROM t_bkey").fetchall()
        assert [r[0] for r in rows] == ["khoa-rieng"]
    finally:
        with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {target} WITH (FORCE)")


async def test_restore_old_entry_without_key_id_uses_master(tmp_path, scratch_db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Mô phỏng bản backup ghi TRƯỚC v0.1.2 (không có `GH_BACKUP_KEY`, manifest không có `key_id`) — restore
    vẫn phải dùng khoá master, không đòi hỏi `GH_BACKUP_KEY` dù biến này đang có cấu hình ở máy hiện tại."""
    get_settings.cache_clear()  # đảm bảo không có GH_BACKUP_KEY lúc tạo bản backup "cũ" giả lập
    store = LocalObjectStore(root=str(tmp_path / "objects"))
    with psycopg.connect(f"{PG}/{scratch_db}", autocommit=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS t_old (id serial primary key, v text)")
        c.execute("DELETE FROM t_old")
        c.execute("INSERT INTO t_old (v) VALUES ('cu-truoc-v012')")
    entry = await run_backup(database_url=f"{PG}/{scratch_db}", store=store)
    assert entry.key_id == "master"

    # Giờ máy có cấu hình GH_BACKUP_KEY (nâng cấp lên v0.1.2) — bản CŨ vẫn phải giải mã đúng bằng khoá master.
    _set_backup_key(monkeypatch, os.urandom(32).hex())
    target = f"gh_backup_old_target_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
        c.execute(f"CREATE DATABASE {target}")
    try:
        await restore_backup(entry.key, database_url=f"{PG}/{scratch_db}", target_database=target, store=store)
        with psycopg.connect(f"{PG}/{target}", autocommit=True) as c:
            rows = c.execute("SELECT v FROM t_old").fetchall()
        assert [r[0] for r in rows] == ["cu-truoc-v012"]
    finally:
        with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {target} WITH (FORCE)")


# ═══ round-trip THẬT: pg_dump + mã hoá + ObjectStore + pg_restore ═══════════════

def _admin(sql: str) -> None:
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
        c.execute(sql)


def _connect(dbname: str) -> psycopg.Connection:
    return psycopg.connect(f"{PG}/{dbname}", autocommit=True)


@pytest.fixture
def scratch_db():  # type: ignore[no-untyped-def]
    name = f"gh_backup_scratch_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {name}")
    try:
        yield name
    finally:
        _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


async def test_backup_restore_direct_with_clean_overwrite_and_real_encryption(tmp_path, scratch_db) -> None:  # type: ignore[no-untyped-def]
    """`pg_restore --clean --if-exists` phải XOÁ dữ liệu cũ ở CSDL đích trước khi khôi phục (không chỉ cộng
    thêm); và bản lưu trong `ObjectStore` phải là bản MÃ HOÁ thật (không phải bytes `pg_dump` gốc)."""
    with _connect(scratch_db) as c:
        c.execute("CREATE TABLE t (id serial primary key, v text)")
        c.execute("INSERT INTO t (v) VALUES ('a'), ('b'), ('c')")

    store = LocalObjectStore(root=str(tmp_path / "objects"))
    src_url = f"{PG}/{scratch_db}"
    entry = await run_backup(database_url=src_url, store=store)
    assert entry.database == scratch_db and entry.size_bytes > 0
    assert [e.key for e in await list_backups(store=store)] == [entry.key]

    raw_blob = await store.get(entry.key)
    assert raw_blob[:3] == b"GH1"          # phong bì gh.crypto, không phải header pg_dump ("PGDMP")
    assert b"PGDMP" not in raw_blob[:200]

    target = f"gh_backup_scratch_target_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {target}")
    try:
        with _connect(target) as c:  # dữ liệu KHÁC đã có sẵn — mô phỏng CSDL đích cần "xoá sạch"
            c.execute("CREATE TABLE t (id serial primary key, v text)")
            c.execute("INSERT INTO t (v) VALUES ('rac-cu-phai-mat')")

        await restore_backup(entry.key, database_url=src_url, target_database=target, store=store)

        with _connect(target) as c:
            rows = c.execute("SELECT v FROM t ORDER BY id").fetchall()
        assert [r[0] for r in rows] == ["a", "b", "c"]
    finally:
        _admin(f"DROP DATABASE IF EXISTS {target} WITH (FORCE)")


def _counts(conn: psycopg.Connection, org: str) -> dict[str, int]:
    def n(sql: str) -> int:
        with conn.cursor() as cur:
            cur.execute(sql, (org,))
            return cur.fetchone()[0]  # type: ignore[no-any-return]

    return {
        "raw": n("SELECT count(*) FROM raw.events e JOIN core.channels c ON c.id = e.channel_id WHERE c.org_id = %s"),
        "units": n("SELECT count(*) FROM clean.meaning_units WHERE org_id = %s"),
        "opps": n("SELECT count(*) FROM biz.opportunities WHERE org_id = %s"),
        "signals": n("SELECT count(*) FROM biz.market_signals WHERE org_id = %s"),
        "alerts": n("SELECT count(*) FROM biz.alerts WHERE org_id = %s"),
        "agents": n("SELECT count(*) FROM agent.identities WHERE org_id = %s"),
    }


async def test_backup_restore_round_trip_preserves_seed_demo_data(app, db, redis, fresh_db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Backup CSDL có dữ liệu thật (`make seed-demo`) → khôi phục vào một CSDL MỚI HOÀN TOÀN TRỐNG (tương
    đương "xoá sạch rồi khôi phục" — tránh đụng pool kết nối SQLAlchemy đang mở của chính bộ test song song
    trong môi trường dùng chung) → đối chiếu số dòng CÁC BẢNG CHÍNH và vài giá trị cụ thể, không chỉ tổng số."""
    sm = sessionmaker()
    seeded = await seed_demo(sm, redis)
    org = seeded["org_id"]

    before_opp = (await db.execute(text("""
        SELECT o.code, o.value_vnd, p.display_name FROM biz.opportunities o JOIN core.persons p ON p.id = o.person_id
        WHERE o.org_id = :o AND p.display_name = 'Nguyễn Văn Mẫu'"""), {"o": org})).one()
    before_alert = (await db.execute(text("""
        SELECT a.priority, a.alert_type, p.display_name FROM biz.alerts a JOIN core.persons p ON p.id = a.subject_id
        WHERE a.org_id = :o AND a.alert_type = 'repeated_complaint'"""), {"o": org})).one()
    before_counts = {k: v for k, v in (await db.execute(text("""
        SELECT 'raw', (SELECT count(*) FROM raw.events e JOIN core.channels c ON c.id = e.channel_id
                       WHERE c.org_id = :o)
        UNION ALL SELECT 'units', (SELECT count(*) FROM clean.meaning_units WHERE org_id = :o)
        UNION ALL SELECT 'opps', (SELECT count(*) FROM biz.opportunities WHERE org_id = :o)
        UNION ALL SELECT 'signals', (SELECT count(*) FROM biz.market_signals WHERE org_id = :o)
        UNION ALL SELECT 'alerts', (SELECT count(*) FROM biz.alerts WHERE org_id = :o)
        UNION ALL SELECT 'agents', (SELECT count(*) FROM agent.identities WHERE org_id = :o)"""),
        {"o": org})).all()}
    assert before_counts["units"] > 0 and before_counts["opps"] > 0 and before_counts["alerts"] > 0

    store = LocalObjectStore(root=str(tmp_path / "objects"))
    src_url = f"{PG}/{fresh_db}"
    entry = await run_backup(database_url=src_url, store=store)

    target = f"gh_backup_restore_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {target}")  # CSDL mới hoàn toàn trống — chưa có schema/dữ liệu gì
    try:
        await restore_backup(entry.key, database_url=src_url, target_database=target, store=store)

        with _connect(target) as conn:
            after_counts = _counts(conn, org)
            with conn.cursor() as cur:
                cur.execute("""SELECT o.code, o.value_vnd, p.display_name FROM biz.opportunities o
                               JOIN core.persons p ON p.id = o.person_id
                               WHERE o.org_id = %s AND p.display_name = 'Nguyễn Văn Mẫu'""", (org,))
                after_opp = cur.fetchone()
                cur.execute("""SELECT a.priority, a.alert_type, p.display_name FROM biz.alerts a
                               JOIN core.persons p ON p.id = a.subject_id
                               WHERE a.org_id = %s AND a.alert_type = 'repeated_complaint'""", (org,))
                after_alert = cur.fetchone()

        assert after_counts == before_counts  # nguyên vẹn tổng số ở mọi bảng chính, không chỉ một bảng
        assert after_opp == (before_opp.code, before_opp.value_vnd, before_opp.display_name)
        assert after_opp[1] == 1_200_000_000  # giá trị cụ thể khớp docs/design/seed-data.json (OPP-1842)
        assert after_alert == (before_alert.priority, before_alert.alert_type, before_alert.display_name)
        assert after_alert[0] == "P1" and after_alert[2] == "Đặng Văn Mẫu Bảy"
    finally:
        _admin(f"DROP DATABASE IF EXISTS {target} WITH (FORCE)")


async def test_restore_overwrite_partitioned_tables_in_place(tmp_path, scratch_db) -> None:  # type: ignore[no-untyped-def]
    """Hồi quy e2e cài thật: phục hồi ĐÈ lên chính CSDL đang có bảng phân vùng (như `genh import` / rollback của
    `genh update`). `pg_restore --clean` trên CSDL đó lỗi hàng loạt "cannot drop inherited constraint" — nay
    `restore_backup` tạo lại CSDL rỗng trước (gh.backup.recreate_database)."""
    with _connect(scratch_db) as c:
        c.execute("CREATE TABLE ev (id bigint, at date NOT NULL, v text, PRIMARY KEY (id, at)) PARTITION BY RANGE (at)")
        c.execute("CREATE TABLE ev_2026_09 PARTITION OF ev FOR VALUES FROM ('2026-09-01') TO ('2026-10-01')")
        c.execute("CREATE TABLE ev_default PARTITION OF ev DEFAULT")
        c.execute("INSERT INTO ev VALUES (1, '2026-09-15', 'truoc-backup'), (2, '2027-01-01', 'mac-dinh')")

    store = LocalObjectStore(root=str(tmp_path / "objects"))
    src_url = f"{PG}/{scratch_db}"
    entry = await run_backup(database_url=src_url, store=store)

    with _connect(scratch_db) as c:  # thay đổi SAU backup — phải biến mất sau khi phục hồi
        c.execute("INSERT INTO ev VALUES (3, '2026-09-20', 'sau-backup')")

    await restore_backup(entry.key, database_url=src_url, store=store)

    with _connect(scratch_db) as c:
        rows = c.execute("SELECT v FROM ev ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["truoc-backup", "mac-dinh"]


# ═══ prune không xoá bản Owner chọn khôi phục ═══════════════════════════════════════════════════════════

async def _seed_old_entries(store: LocalObjectStore) -> tuple[list[BackupEntry], str]:
    from gh.backup import _write_manifest
    # 3 bản cùng một ngày rất xa: GFS chỉ giữ bản mới nhất trong ngày, bản cũ nhất sẽ bị prune xoá nếu không được ghim.
    # Neo 12:00 UTC: nếu lấy giờ hiện tại + i giờ thì chạy sau 22:00 UTC các bản rơi sang ngày hôm sau (test chập chờn).
    day = (datetime.now(UTC) - timedelta(days=2000)).replace(hour=12, minute=0, second=0, microsecond=0)
    entries = [BackupEntry(key=f"backups/e{i}.enc", taken_at=day + timedelta(hours=i), database="gh",
                           size_bytes=1, sha256="x") for i in range(3)]
    for e in entries:
        await store.put(e.key, b"x")
    await _write_manifest(store, entries)
    return entries, entries[0].key


async def test_prune_never_deletes_key_in_gh_backup_keep(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = LocalObjectStore(root=str(tmp_path / "objects"))
    entries, pinned = await _seed_old_entries(store)
    monkeypatch.setenv("GH_BACKUP_KEEP", f"backups/other.enc, {pinned}")
    removed = await prune(store=store, entries=entries)
    assert pinned not in removed
    monkeypatch.delenv("GH_BACKUP_KEEP")
    assert pinned in await prune(store=store, entries=entries)  # đối chứng: không ghim thì bị xoá


async def test_pre_restore_trigger_skips_prune(tmp_path, scratch_db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = LocalObjectStore(root=str(tmp_path / "objects"))
    _, old = await _seed_old_entries(store)
    monkeypatch.delenv("GH_BACKUP_KEEP", raising=False)
    await run_backup(database_url=f"{PG}/{scratch_db}", store=store, trigger="pre-restore")
    assert old in [e.key for e in await list_backups(store=store)]
    await run_backup(database_url=f"{PG}/{scratch_db}", store=store, trigger="manual")
    assert old not in [e.key for e in await list_backups(store=store)]  # đối chứng: trigger khác vẫn prune
