"""Sao lưu/khôi phục CSDL Postgres (PLAN §5.6).

- `pg_dump` thật, định dạng tuỳ biến nén sẵn (`-Fc`) ra tệp tạm.
- Mã hoá bằng đúng cơ chế phong bì AES-256-GCM của `gh.crypto` (giai đoạn 1 dùng cho bí mật) — không tự chế
  thuật toán mã hoá mới, chỉ đổi `associated data` để tách bối cảnh backup khỏi các bí mật khác.
- Khoá mã hoá backup (HANDOFF-v0.1.2 mục 1): dùng `GH_BACKUP_KEY` (hex 64 ký tự = 32 byte) riêng cho backup
  nếu có cấu hình — tách khỏi `GH_MASTER_KEY` (bí mật ứng dụng) để mất một khoá không kéo theo mất khoá kia.
  Trống ⇒ giữ hành vi cũ (mã hoá bằng khoá master). Mỗi bản ghi rõ đã dùng khoá nào (`BackupEntry.key_id`:
  `"backup"`|`"master"`) để giải mã đúng khoá lúc restore — không thử/sai. Xem `_backup_key()`.
- Lưu qua `gh.chassis.objects.ObjectStore` (điểm nối MinIO thật thay sau — xem docstring của module đó): tái
  dùng đúng abstraction dựng ở giai đoạn 3 cho Tài liệu, không viết client MinIO riêng cho backup.
- Vòng đời GFS (grandfather-father-son): giữ 7 bản gần nhất theo NGÀY + 4 bản theo TUẦN (ISO) + 12 bản theo
  THÁNG dương lịch — thuật toán thuần (`select_retained`, không đụng DB/đĩa) nên test được đầy đủ không cần
  dựng nhiều tháng dữ liệu thật. Một danh mục (`MANIFEST_KEY`) tự quản trong chính `ObjectStore` ghi lại
  từng bản: khoá, thời điểm, CSDL nguồn, kích thước, sha256 — vì `ObjectStore` chỉ có put/get/delete (không có
  list), không mở rộng giao diện đó chỉ để phục vụ backup.

Restore: giải mã, `pg_restore --clean --if-exists` thật vào CSDL đích — mặc định CSDL đang cấu hình
(`GH_DATABASE_URL`), cho phép chỉ định CSDL khác (`target_database`) để test không đụng CSDL đang dùng.

Lịch chạy: nối vào cấu hình lưu ở bước 11 trình thiết lập (`core.organizations.settings->'backup'` —
`gh/setup/routes.py::step11`). Vì `pg_dump` sao lưu TOÀN CỤM CSDL dùng chung giữa mọi tổ chức (multi-tenant qua
RLS, không phải một CSDL riêng mỗi tổ chức), lịch dùng chung là của tổ chức ĐẦU TIÊN có cấu hình (đúng mô hình
triển khai một tổ chức một bản cài của `genh`, giai đoạn 6) — quyết định tự đưa ra, ghi rõ ở đây để không mơ hồ
nếu sau này cần cấu hình lịch backup tách khỏi tổ chức. `retention_count` ở bước 11 KHÔNG dùng cho vòng đời
GFS này (spec 5.6 khoá cứng 7/4/12) — chỉ còn ý nghĩa hiển thị/tương thích ngược, ghi rõ trong báo cáo.

CLI: `python -m gh.backup run|list|prune|restore` — `Makefile` bọc `make backup` / `make restore BACKUP=<khoá>`.

v0.1.20 (Console › Dữ liệu & lưu trữ): mỗi bản ghi thêm `trigger` (nguồn: `manual` | `scheduled` | `pre-update` |
`pre-restore` | `pre-import`; bản cũ không có ⇒ None). CLI `run` đọc nguồn từ biến môi trường `GH_BACKUP_TRIGGER`
(genh truyền `-e`), KHÔNG qua cờ — `genh update` chạy backup trong container api CŨ, bản cũ gặp cờ lạ sẽ thoát lỗi.
Nút "Sao lưu ngay" chạy qua worker arq (`backup_now`), tiến trình ghi ở Redis `JOB_KEY`; mọi lần chạy (kể cả lịch)
giữ khoá Redis `LOCK_KEY` để hai bản không ghi đè danh mục của nhau. Vòng đời GFS luôn giữ thêm mọi bản trong
`RECENT_KEEP_HOURS` giờ qua — không thì bấm "Sao lưu ngay" (hoặc bản an toàn trước khi khôi phục) sẽ xoá bản
cùng ngày Owner vừa định khôi phục.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import orjson
import psycopg
from arq.worker import func
from psycopg import sql
from sqlalchemy import text

from gh import crypto
from gh.biz.hooks import CronJob, Hook
from gh.chassis.objects import ObjectNotFound, ObjectStore, content_hash, get_object_store
from gh.config import get_settings

log = logging.getLogger("gh.backup")

MANIFEST_KEY = "backups/manifest.json"
BACKUP_AAD = b"gh-backup-v1"

DAILY_KEEP = 7
WEEKLY_KEEP = 4
MONTHLY_KEEP = 12

DUE_WINDOW_MIN = 15  # dung sai quanh `time_of_day` cấu hình — job quét mỗi 15 phút (xem JOBS bên dưới)
RECENT_KEEP_HOURS = 24  # bản trong 24 giờ qua luôn giữ (ngoài GFS) — xem docstring module

TRIGGERS = ("manual", "scheduled", "pre-update", "pre-restore", "pre-import")
TRIGGER_ENV = "GH_BACKUP_TRIGGER"
KEEP_ENV = "GH_BACKUP_KEEP"  # khoá (phẩy phân cách) prune không bao giờ xoá — genh restore ghim bản đang khôi phục
LOCK_KEY = "gh:backup:lock"
LOCK_SECONDS = 3600
JOB_KEY = "gh:backup:job"


@dataclass(frozen=True)
class BackupEntry:
    key: str
    taken_at: datetime          # UTC, có tzinfo
    database: str
    size_bytes: int
    sha256: str
    # Khoá nào đã mã hoá bản này: "backup" (GH_BACKUP_KEY, xem `_backup_key()`) hoặc "master" (GH_MASTER_KEY —
    # hành vi cũ). Bản cũ ghi TRƯỚC khi có trường này không có khoá `key_id` trong JSON → `from_json` mặc định
    # "master" (tương thích ngược: mọi bản đã tồn tại đều mã hoá bằng khoá master lúc chưa có khoá backup riêng).
    key_id: str = "master"
    # Nguồn tạo bản (TRIGGERS); bản ghi trước v0.1.20 không có ⇒ None.
    trigger: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key, "taken_at": self.taken_at.isoformat(), "database": self.database,
                "size_bytes": self.size_bytes, "sha256": self.sha256, "key_id": self.key_id, "trigger": self.trigger}

    @staticmethod
    def from_json(d: dict[str, Any]) -> BackupEntry:
        return BackupEntry(key=d["key"], taken_at=datetime.fromisoformat(d["taken_at"]), database=d["database"],
                           size_bytes=d["size_bytes"], sha256=d["sha256"], key_id=d.get("key_id", "master"),
                           trigger=d.get("trigger"))


# ─── DSN: `database_url` là SQLAlchemy async (`postgresql+asyncpg://…`), pg_dump/pg_restore cần libpq thường ──

def libpq_url(database_url: str, *, database: str | None = None) -> str:
    parts: SplitResult = urlsplit(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    if database is not None:
        parts = parts._replace(path=f"/{database}")
    return urlunsplit(parts)


def libpq_conn(database_url: str, *, database: str | None = None) -> tuple[str, dict[str, str]]:
    """v0.1.45 (F-54): (URL libpq KHÔNG có mật khẩu, biến môi trường {"PGPASSWORD": …}) cho `pg_dump`/`pg_restore`.

    Mật khẩu superuser không được nằm trên argv (ai trên máy/trong container cũng đọc được qua `ps`/`/proc/*/cmdline`)
    ⇒ bỏ khỏi netloc (giữ user/host/port/db và query) và cả tham số `?password=` nếu có, chuyển sang PGPASSWORD (đã
    unquote, vd `%40` → `@`). Không có mật khẩu ⇒ env rỗng."""
    parts: SplitResult = urlsplit(libpq_url(database_url, database=database))
    password: str | None = None
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, hostport = netloc.rsplit("@", 1)
        user, sep, raw_pw = userinfo.partition(":")
        if sep:
            password = unquote(raw_pw)
        netloc = f"{user}@{hostport}" if user else hostport
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        kept = [(k, v) for k, v in pairs if k != "password"]
        if len(kept) != len(pairs):
            password = password if password is not None else next(v for k, v in pairs if k == "password")
            query = urlencode(kept, quote_via=quote)  # khoảng trắng → %20 (libpq không giải '+')
    env = {"PGPASSWORD": password} if password else {}
    return urlunsplit(parts._replace(netloc=netloc, query=query)), env


_URL_PASSWORD = re.compile(r"(?P<pre>[a-z][a-z0-9+.-]*://[^:/@\s]*:)[^@\s]+@", re.IGNORECASE)


def redact_secrets(message: str, secrets: Iterable[str] = ()) -> str:
    """Che mật khẩu trong thông báo lỗi pg_* trước khi ném/log: mọi giá trị trong `secrets` (cả dạng URL-encode) và
    phần mật khẩu của mọi URL `scheme://user:mật-khẩu@…` ⇒ `***`."""
    out = message
    for s in sorted({x for x in secrets if x}, key=len, reverse=True):
        out = out.replace(s, "***")
        enc = quote(s, safe="")
        if enc != s:
            out = out.replace(enc, "***")
    return _URL_PASSWORD.sub(lambda m: m.group("pre") + "***@", out)


def database_name(database_url: str) -> str:
    return urlsplit(libpq_url(database_url)).path.lstrip("/")


def recreate_database(pg_url: str) -> None:
    """Xoá hẳn CSDL đích (ngắt mọi kết nối — `WITH (FORCE)`, Postgres 13+) rồi tạo lại RỖNG, trước `pg_restore`.

    Vì sao không dùng `pg_restore --clean` trên CSDL đang có dữ liệu: các bảng phân vùng (pg_partman — raw.events,
    ops.action_log, agent.model_calls…) làm `--clean` thử `DROP CONSTRAINT` trên từng phân vùng con trước bảng cha
    → hàng chục lỗi "cannot drop inherited constraint" và pg_restore thoát ≠ 0 (phát hiện ở e2e cài thật:
    `genh import`, và cả rollback của `genh update` vì dùng chung `restore_backup`). CSDL không có cấu hình cấp
    database riêng (không `ALTER DATABASE … SET` trong migration) nên xoá/tạo lại không mất gì ngoài dữ liệu sẽ được
    nạp lại; quyền của role `gh_app` trên schema/bảng nằm trong bản dump (GRANT) nên được khôi phục cùng.
    """
    parts = urlsplit(pg_url)
    db_name = parts.path.lstrip("/")
    maintenance_url = urlunsplit(parts._replace(path="/postgres"))
    with psycopg.connect(maintenance_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    log.info("Đã tạo lại CSDL rỗng %s trước khi phục hồi", db_name)


# ─── khoá riêng cho backup (`GH_BACKUP_KEY`, hex 64 ký tự = 32 byte) — HANDOFF-v0.1.2 mục 1 ─────────────────
# Tách khỏi `GH_MASTER_KEY` (bí mật ứng dụng, base64) để mất một khoá không kéo theo mất khoá kia. Rỗng ⇒ giữ
# hành vi cũ: backup mã hoá bằng khoá master (xem `Settings.backup_key`, `crypto.encrypt`/`decrypt` tham số
# `key=None` ⇒ dùng `crypto.master_key()`).

def _backup_key() -> bytes | None:
    raw = get_settings().backup_key
    if not raw:
        return None
    try:
        key = bytes.fromhex(raw)
    except ValueError as e:
        raise ValueError("GH_BACKUP_KEY phải là chuỗi hex (64 ký tự = 32 byte)") from e
    if len(key) != 32:
        raise ValueError("GH_BACKUP_KEY phải là 32 byte (64 ký tự hex)")
    return key


# ─── vòng đời GFS: thuật toán thuần, không đụng DB/đĩa ─────────────────────────────────────────────────────

def select_retained(entries: Iterable[BackupEntry], *, now: datetime | None = None, daily: int = DAILY_KEEP,
                    weekly: int = WEEKLY_KEEP, monthly: int = MONTHLY_KEEP) -> set[str]:
    """Trả về tập `key` các bản backup CẦN GIỮ theo vòng đời 7 ngày/4 tuần/12 tháng.

    Thuật toán (mỗi bản chỉ thuộc đúng MỘT hạng, ưu tiên hạng gần nhất trước):
    1. **Hàng ngày**: với mỗi ngày dương lịch có ít nhất một bản, giữ bản MỚI NHẤT của ngày đó; lấy `daily`
       ngày gần nay nhất theo cách đó.
    2. **Hàng tuần**: trong các bản KHÔNG thuộc ngày đã giữ ở (1), nhóm theo tuần ISO (năm, số tuần), giữ bản
       mới nhất mỗi tuần; lấy `weekly` tuần gần nay nhất.
    3. **Hàng tháng**: trong các bản không thuộc ngày (1) VÀ không thuộc tuần (2), nhóm theo (năm, tháng
       dương lịch), giữ bản mới nhất mỗi tháng; lấy `monthly` tháng gần nay nhất.

    Mọi bản còn lại (cũ hơn 12 tháng, hoặc rơi vào ngày/tuần/tháng không lọt vào 3 vòng trên) bị dọn. `now`
    chỉ dùng để test (mặc định giờ hệ thống) — bản thân thuật toán chỉ so sánh các bản với NHAU, không so với
    `now`, nên không cần dữ liệu thật kéo dài nhiều tháng để kiểm.
    """
    items = sorted(entries, key=lambda e: e.taken_at, reverse=True)
    keep: set[str] = set()

    day_latest: dict[Any, BackupEntry] = {}
    for e in items:
        day_latest.setdefault(e.taken_at.date(), e)
    daily_days = sorted(day_latest, reverse=True)[:daily]
    keep.update(day_latest[d].key for d in daily_days)
    daily_days_set = set(daily_days)

    week_latest: dict[Any, BackupEntry] = {}
    for e in items:
        d = e.taken_at.date()
        if d in daily_days_set:
            continue
        week_latest.setdefault(d.isocalendar()[:2], e)
    weekly_weeks = sorted(week_latest, reverse=True)[:weekly]
    keep.update(week_latest[w].key for w in weekly_weeks)
    weekly_weeks_set = set(weekly_weeks)

    month_latest: dict[Any, BackupEntry] = {}
    for e in items:
        d = e.taken_at.date()
        if d in daily_days_set or d.isocalendar()[:2] in weekly_weeks_set:
            continue
        month_latest.setdefault((d.year, d.month), e)
    monthly_months = sorted(month_latest, reverse=True)[:monthly]
    keep.update(month_latest[m].key for m in monthly_months)

    return keep


# ─── danh mục (manifest) trong chính ObjectStore ───────────────────────────────────────────────────────────

async def _read_manifest(store: ObjectStore) -> list[BackupEntry]:
    try:
        raw = await store.get(MANIFEST_KEY)
    except ObjectNotFound:
        return []
    return [BackupEntry.from_json(d) for d in orjson.loads(raw)]


async def _write_manifest(store: ObjectStore, entries: list[BackupEntry]) -> None:
    await store.put(MANIFEST_KEY, orjson.dumps([e.to_json() for e in entries]))


async def list_backups(*, store: ObjectStore | None = None) -> list[BackupEntry]:
    store = store or get_object_store()
    return sorted(await _read_manifest(store), key=lambda e: e.taken_at, reverse=True)


# ─── chạy tiến trình con thật ───────────────────────────────────────────────────────────────────────────────

#: Biến môi trường tối thiểu tiến trình con pg_dump/pg_restore cần (ngoài PGPASSWORD do `libpq_conn` cấp).
_PG_CHILD_ENV_KEEP = ("PATH", "LC_ALL", "LANG")


def pg_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Môi trường TỐI THIỂU cho pg_dump/pg_restore (review lần 2 v0.1.62): PATH (tìm tệp chạy) + LC_ALL/LANG nếu có +
    `extra` (PGPASSWORD). KHÔNG thừa hưởng `os.environ` của api/worker — nó mang GH_ADMIN_DATABASE_URL, khoá master…
    mà tiến trình con (và mọi thứ cùng uid đọc được `/proc/<pid>/environ` của nó) không cần biết."""
    env = {k: os.environ[k] for k in _PG_CHILD_ENV_KEEP if os.environ.get(k)}
    env.setdefault("PATH", os.defpath)
    env.update(extra or {})
    return env


async def _run(cmd: list[str], *, env_extra: dict[str, str] | None = None) -> None:
    """Chạy `pg_dump`/`pg_restore`. v0.1.36 (F-3): job bị huỷ (quá giờ arq, worker tắt) ⇒ giết tiến trình con rồi mới
    ném lại `CancelledError` — không để pg_dump/pg_restore mồ côi chạy tiếp, giữ khoá CSDL/ghi đĩa không ai đợi.

    v0.1.45 (F-54): mật khẩu đi qua `env_extra` (PGPASSWORD — xem `libpq_conn`), KHÔNG trên argv; thông báo lỗi đã
    che mật khẩu trước khi ném.

    Tiến trình con chỉ nhận môi trường tối thiểu (`pg_child_env`), không thừa hưởng environ của api/worker."""
    env = pg_child_env(env_extra)
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                env=env)
    try:
        _out, err = await proc.communicate()
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5)
        raise
    if proc.returncode != 0:
        tail = redact_secrets(err.decode(errors="replace"), (env_extra or {}).values())[-4000:]
        raise RuntimeError(f"{cmd[0]} thất bại (mã {proc.returncode}): {tail}")


# ─── backup / prune / restore ──────────────────────────────────────────────────────────────────────────────

async def run_backup(*, database_url: str | None = None, store: ObjectStore | None = None,
                     trigger: str = "manual") -> BackupEntry:
    """`pg_dump -Fc` CSDL thật → mã hoá → lưu qua `ObjectStore` → dọn theo vòng đời GFS."""
    if trigger not in TRIGGERS:
        trigger = "manual"
    # Superuser (GH_ADMIN_DATABASE_URL): role ứng dụng gh_app không chắc SELECT được mọi bảng hệ thống mà
    # pg_dump cần đọc (vd. large object, một số catalog) — dump/restore luôn qua vai trò quản trị.
    database_url = database_url or get_settings().effective_admin_database_url
    store = store or get_object_store()
    src, pg_env = libpq_conn(database_url)
    db_name = database_name(database_url)
    taken_at = datetime.now(UTC)

    with tempfile.TemporaryDirectory(prefix="gh-backup-") as tmp:
        dump_path = Path(tmp) / "dump.pgcustom"
        await _run(["pg_dump", "--format=custom", "--no-owner", "--file", str(dump_path), src], env_extra=pg_env)
        raw = dump_path.read_bytes()

    bkey = _backup_key()
    key_id = "backup" if bkey is not None else "master"
    enc = crypto.encrypt(raw, associated=BACKUP_AAD, key=bkey)
    key = f"backups/{taken_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}.pgcustom.enc"
    await store.put(key, enc)
    entry = BackupEntry(key=key, taken_at=taken_at, database=db_name, size_bytes=len(raw),
                        sha256=content_hash(raw), key_id=key_id, trigger=trigger)

    entries = [*await _read_manifest(store), entry]
    await _write_manifest(store, entries)
    if trigger != "pre-restore":  # bản an toàn trước khôi phục không được dọn mất bản Owner vừa chọn
        await prune(store=store, entries=entries)
    log.info("Backup mới: %s (%d byte, CSDL %s)", key, len(raw), db_name)
    return entry


async def prune(*, store: ObjectStore | None = None, entries: list[BackupEntry] | None = None,
                now: datetime | None = None) -> list[str]:
    """Áp vòng đời GFS lên danh mục hiện có, xoá phần thừa khỏi `ObjectStore`. Trả về các `key` đã xoá."""
    store = store or get_object_store()
    entries = entries if entries is not None else await _read_manifest(store)
    keep_keys = select_retained(entries, now=now)
    recent = (now or datetime.now(UTC)) - timedelta(hours=RECENT_KEEP_HOURS)
    keep_keys |= {e.key for e in entries if e.taken_at >= recent}
    keep_keys |= {k.strip() for k in os.environ.get(KEEP_ENV, "").split(",") if k.strip()}
    kept, removed = [], []
    for e in entries:
        if e.key in keep_keys:
            kept.append(e)
        else:
            await store.delete(e.key)
            removed.append(e.key)
    if removed:
        await _write_manifest(store, kept)
        log.info("Đã dọn %d bản backup quá hạn vòng đời", len(removed))
    return removed


async def restore_backup(key: str, *, database_url: str | None = None, target_database: str | None = None,
                         store: ObjectStore | None = None) -> None:
    """Giải mã bản backup `key`, tạo lại CSDL đích rỗng (`recreate_database`) rồi `pg_restore` thật vào đó.

    `target_database`: tên CSDL khác CSDL đang cấu hình — dùng khi test round-trip để không đụng CSDL đang
    dùng (spec 5.6 yêu cầu rõ điều này).
    """
    # Xoá/tạo lại CSDL + pg_restore cần quyền superuser → luôn qua URL quản trị.
    database_url = database_url or get_settings().effective_admin_database_url
    store = store or get_object_store()
    enc = await store.get(key)
    # Bản nào mã hoá bằng khoá nào ghi trong manifest (`key_id`) — bản cũ không có trường này ⇒ "master" (xem
    # `BackupEntry.from_json`). Không tự đoán qua thử/sai (thử cả hai khoá) — đọc rõ ràng từ manifest.
    entry = next((e for e in await _read_manifest(store) if e.key == key), None)
    dkey = _backup_key() if (entry is not None and entry.key_id == "backup") else None
    raw = crypto.decrypt(enc, associated=BACKUP_AAD, key=dkey)
    # recreate_database chạy psycopg TRONG tiến trình ⇒ dùng URL đầy đủ; pg_restore (tiến trình con) ⇒ PGPASSWORD.
    dest = libpq_url(database_url, database=target_database) if target_database else libpq_url(database_url)
    dest_nopw, pg_env = libpq_conn(database_url, database=target_database)

    with tempfile.TemporaryDirectory(prefix="gh-restore-") as tmp:
        dump_path = Path(tmp) / "dump.pgcustom"
        dump_path.write_bytes(raw)
        await asyncio.to_thread(recreate_database, dest)
        await _run(["pg_restore", "--no-owner", "--dbname", dest_nopw, str(dump_path)], env_extra=pg_env)
    log.info("Đã khôi phục %s vào %s", key, target_database or database_name(database_url))


# ─── lịch chạy định kỳ theo cấu hình bước 11 (worker cron — xem JOBS bên dưới) ─────────────────────────────

def is_due(cfg: dict[str, Any], *, last_at: datetime | None, now: datetime) -> bool:
    """`cfg` là `settings->'backup'` (bước 11: `frequency`, `time_of_day`). Đến giờ khi: giờ hiện tại nằm
    trong cửa sổ `DUE_WINDOW_MIN` phút quanh `time_of_day` cấu hình, VÀ chưa có bản backup nào trong chu kỳ
    hiện tại (ngày/tuần ISO/tháng dương lịch tuỳ `frequency`)."""
    try:
        hh, mm = (int(x) for x in str(cfg.get("time_of_day", "02:00")).split(":"))
    except ValueError:
        hh, mm = 2, 0
    scheduled = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if abs((now - scheduled).total_seconds()) > DUE_WINDOW_MIN * 60:
        return False
    if last_at is None:
        return True
    freq = cfg.get("frequency", "daily")
    if freq == "weekly":
        return last_at.isocalendar()[:2] < now.isocalendar()[:2]
    if freq == "monthly":
        return (last_at.year, last_at.month) < (now.year, now.month)
    return last_at.date() < now.date()  # "daily" và giá trị lạ khác → mặc định hằng ngày


async def _acquire_lock(redis: Any) -> bool:
    return bool(await redis.set(LOCK_KEY, b"1", nx=True, ex=LOCK_SECONDS))


async def scheduled_backup_scan(ctx: dict[str, Any]) -> dict[str, Any]:
    """Quét mỗi `DUE_WINDOW_MIN` phút (đăng ký ở `JOBS`): đọc `settings->'backup'` của tổ chức đầu tiên đã
    cấu hình (xem lý do ở docstring đầu module), chạy `run_backup()` thật nếu đến giờ. Giờ chạy (`time_of_day`)
    tính theo múi giờ của tổ chức (bước 3), không phải UTC.

    v0.1.36 (F-3): job có `timeout` 3600 giây (JOBS — mặc định arq 300 giây giết pg_dump giữa chừng); bị huỷ (quá
    giờ, bộ xử lý nền khởi động lại) ⇒ chuông "Sao lưu thất bại" rồi mới ném lại — không chết âm thầm. Kiểm "bản
    mới nhất quá 36 giờ" KHÔNG nằm ở đây (job này có thể không chạy chính vì worker chết): dùng chung
    `gh.health.evaluate` (vòng theo dõi trong api, chuông `backup.stale`)."""
    from zoneinfo import ZoneInfo

    from gh.db import sessionmaker

    sm = sessionmaker()
    async with sm() as db:
        row = (await db.execute(text("""
            SELECT settings -> 'backup' AS cfg, timezone FROM core.organizations
            WHERE settings ? 'backup' ORDER BY created_at LIMIT 1"""))).first()
    if row is None or not row.cfg:
        return {"skipped": "no_config"}
    try:
        tz: Any = ZoneInfo(row.timezone or "UTC")
    except (ValueError, KeyError):
        tz = UTC

    store = get_object_store()
    entries = await list_backups(store=store)
    last_at = entries[0].taken_at.astimezone(tz) if entries else None
    now = datetime.now(tz)
    if not is_due(row.cfg, last_at=last_at, now=now):
        return {"skipped": "not_due"}
    redis = ctx.get("redis")
    if redis is not None and not await _acquire_lock(redis):
        return {"skipped": "locked"}
    try:
        entry = await run_backup(store=store, trigger="scheduled")
    except asyncio.CancelledError:
        await _notify_cancelled(redis, CANCELLED_SCHEDULED_MESSAGE)
        raise
    except Exception as exc:
        # F106: chuông chỉ mang câu thân thiện; stderr của pg_dump chỉ vào nhật ký máy chủ (đã che mật khẩu).
        log.error("Sao lưu theo lịch thất bại: %s", _technical(exc))
        await _notify_owners(redis, ok=False, message=SCHEDULED_FAILED_MESSAGE)
        raise
    finally:
        if redis is not None:
            await redis.delete(LOCK_KEY)
    return {"ran": entry.key}


# F106: Sếp chỉ thấy câu cố định này; lỗi gốc (stderr pg_dump/pg_restore) đi riêng sang `technical`
# → "Chi tiết kỹ thuật".
BACKUP_FAILED_MESSAGE = "Chưa sao lưu được — thử lại; nếu vẫn lỗi gửi gói chẩn đoán."
SCHEDULED_FAILED_MESSAGE = ("Bản sao lưu theo lịch chưa thành công — bấm Sao lưu ngay để thử lại; "
                            "nếu vẫn lỗi gửi gói chẩn đoán.")


def _technical(exc: BaseException) -> str:
    """Lỗi gốc cho "Chi tiết kỹ thuật": đã che mật khẩu (kể cả lỗi không đi qua `_run`), giữ 500 ký tự cuối."""
    return redact_secrets(str(exc))[-500:]


async def _set_job(redis: Any, **fields: Any) -> None:
    raw = await redis.get(JOB_KEY)
    cur = orjson.loads(raw) if raw else {}
    await redis.set(JOB_KEY, orjson.dumps({**cur, **fields}), ex=7 * 24 * 3600)


async def _notify_owners(redis: Any, *, ok: bool, message: str) -> None:
    """v0.1.23 (B6): báo kết quả sao lưu lên chuông thông báo của các Owner. Lỗi ở đây không được làm hỏng
    kết quả sao lưu — chỉ ghi log."""
    from gh import notifications
    from gh.db import sessionmaker

    try:
        async with sessionmaker()() as db:
            orgs = (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at"))).scalars().all()
            for org in orgs:
                await notifications.notify(
                    db, org, await notifications.owner_ids(db, org),
                    kind="backup.done" if ok else "backup.failed",
                    title="Sao lưu đã xong" if ok else "Sao lưu thất bại",
                    body=message, link="/system?tab=storage", redis=redis)
            await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Không ghi được thông báo sao lưu")


CANCELLED_SCHEDULED_MESSAGE = ("Sao lưu theo lịch bị dừng giữa chừng (quá 60 phút hoặc bộ xử lý nền khởi động lại) — "
                               "bấm Sao lưu ngay để thử lại.")
CANCELLED_NOW_MESSAGE = "Sao lưu bị dừng giữa chừng"


async def _notify_cancelled(redis: Any, message: str) -> None:
    """v0.1.36 (F-3): job đang bị huỷ — vẫn cố gửi chuông (shield: lệnh huỷ không cắt ngang việc ghi; tối đa 10 giây)
    rồi để bên gọi ném lại `CancelledError`. Lỗi gửi chỉ ghi log."""
    with contextlib.suppress(Exception):
        await asyncio.wait_for(asyncio.shield(_notify_owners(redis, ok=False, message=message)), 10)


async def backup_now(ctx: dict[str, Any], trigger: str = "manual") -> dict[str, Any]:
    """Nút "Sao lưu ngay" (gh/system_api/backups.py xếp hàng qua arq): chạy `run_backup()` và ghi tiến trình vào
    Redis `JOB_KEY` (queued → running → done/failed) cho Console hỏi lại. v0.1.36 (F-3): bị huỷ giữa chừng ⇒ trạng
    thái 'failed' + chuông, không treo mãi ở 'running'."""
    redis = ctx["redis"]
    if not await _acquire_lock(redis):
        await _set_job(redis, state="failed", finished_at=datetime.now(UTC).isoformat(),
                       message="Đang có một bản sao lưu khác chạy — thử lại sau ít phút", technical=None)
        return {"skipped": "locked"}
    await _set_job(redis, state="running", started_at=datetime.now(UTC).isoformat(), message=None,
                   technical=None)
    try:
        entry = await run_backup(trigger=trigger)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(_set_job(redis, state="failed",
                                                           finished_at=datetime.now(UTC).isoformat(),
                                                           message=CANCELLED_NOW_MESSAGE, technical=None)), 10)
        await _notify_cancelled(redis, f"{CANCELLED_NOW_MESSAGE} — bấm Sao lưu ngay để thử lại.")
        raise
    except Exception as exc:  # noqa: BLE001 — báo lỗi cho Console, không nuốt im lặng
        log.exception("Sao lưu ngay thất bại")
        await _set_job(redis, state="failed", finished_at=datetime.now(UTC).isoformat(),
                       message=BACKUP_FAILED_MESSAGE, technical=_technical(exc))
        await _notify_owners(redis, ok=False, message=BACKUP_FAILED_MESSAGE)
        return {"failed": BACKUP_FAILED_MESSAGE}
    finally:
        await redis.delete(LOCK_KEY)
    await _set_job(redis, state="done", finished_at=datetime.now(UTC).isoformat(), key=entry.key)
    # v0.1.28 (UX L4): câu cho người dùng — không đưa đường dẫn tệp trong kho lên chuông.
    size_mb = max(entry.size_bytes, 0) / 1_048_576
    size = f"{size_mb:.1f}".replace(".", ",")
    await _notify_owners(redis, ok=True,
                         message=f"Đã sao lưu ({size} MB) — tải về hoặc khôi phục ở Cài đặt › Sao lưu & cập nhật.")
    return {"ran": entry.key}


HOOKS: list[Hook] = []
# v0.1.36 (F-3): timeout 3600 giây — mặc định arq (300 giây) huỷ pg_dump của CSDL lớn giữa chừng.
JOBS: list[CronJob] = [(scheduled_backup_scan, {"minute": set(range(0, 60, DUE_WINDOW_MIN)), "timeout": 3600})]
# Job xếp hàng theo yêu cầu (không theo lịch) — gh/worker.py đăng ký. v0.1.36 (F-3): max_tries=1 — arq mặc định coi
# CancelledError (worker tắt khi `genh update`/khởi động lại) là "sẽ chạy lại", trong khi `backup_now` đã ghi 'failed' +
# nhả khoá + chuông "bấm Sao lưu ngay để thử lại". Chạy lại ngầm ⇒ Owner bấm theo chuông là có HAI pg_dump cùng lúc.
FUNCTIONS = [func(backup_now, timeout=3600, max_tries=1)]


# ─── CLI: `python -m gh.backup …` (Makefile bọc `make backup` / `make restore BACKUP=<khoá>`) ──────────────

async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Sao lưu/khôi phục CSDL Gen-Harness (PLAN §5.6)")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("run", help="Chạy pg_dump + mã hoá + lưu + dọn vòng đời ngay")
    sub.add_parser("prune", help="Chỉ áp vòng đời GFS lên danh mục hiện có")
    sub.add_parser("list", help="Liệt kê các bản backup còn giữ")
    p_restore = sub.add_parser("restore", help="Giải mã + pg_restore một bản backup")
    p_restore.add_argument("--key", required=True, help="Khoá bản backup (xem `list`)")
    p_restore.add_argument("--target-database", default=None, help="CSDL đích khác CSDL đang cấu hình")
    args = parser.parse_args()

    if args.action == "run":
        entry = await run_backup(trigger=os.environ.get(TRIGGER_ENV) or "manual")
        log.info("OK: %s", entry.to_json())
    elif args.action == "prune":
        removed = await prune()
        log.info("Đã xoá %d bản: %s", len(removed), removed)
    elif args.action == "list":
        for e in await list_backups():
            log.info("%s  %s  %d byte  CSDL=%s", e.taken_at.isoformat(), e.key, e.size_bytes, e.database)
    elif args.action == "restore":
        await restore_backup(args.key, target_database=args.target_database)
        log.info("Đã khôi phục %s", args.key)


if __name__ == "__main__":
    asyncio.run(_main())
