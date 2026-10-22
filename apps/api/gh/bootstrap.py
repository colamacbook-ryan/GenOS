"""Khởi tạo hệ thống khi khởi động (idempotent).

Một bản cài = một tổ chức. Lần đầu: tạo tổ chức, vai trò, quyền theo ma trận mặc định, ranh giới có trách nhiệm,
dòng plugin nền, trạng thái thiết lập kèm mã thiết lập một lần. Chạy lại không ghi đè chỉnh sửa của Owner,
trừ các khoá cứng (ARCHITECTURE §7.4) luôn được đặt lại đúng giá trị.
"""

import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac
from gh.chassis import policy
from gh.chassis.plugins import Manifest, read_manifests
from gh.config import get_settings
from gh.crypto import new_token, token_digest
from gh.hostlink_io import run_root, write_request

log = logging.getLogger("gh.bootstrap")

# F128: mã thiết lập Owner tự sinh (cài tay/dev) KHÔNG vào log — ghi vào tệp quyền 0600 trong hộp thư dữ liệu `run/`
# (Settings.host_link_dir, genh đọc); log chỉ ghi đường dẫn. Mã bị vô hiệu ⇒ tệp bị xoá.
SETUP_CODE_FILE = "setup-code.json"

DEFAULT_ORG_NAME = "Gen-Harness"


@dataclass(frozen=True)
class Boundary:
    code: str
    enabled: bool
    locked: bool
    params: dict[str, object] | None = None


# Ranh giới có trách nhiệm (thiết kế: boundaries) + các khoá cứng không có công tắc trên màn hình.
BOUNDARIES = (
    Boundary("listen_authorized_only", True, True),
    Boundary("disclose_staff_observation", True, False),
    Boundary("hide_sensitive_below_owner", True, True),
    Boundary("personnel_alert_requires_evidence", True, True),
    Boundary("observe_external_market", True, False),
    Boundary("auto_personnel_decisions", False, True),
    Boundary("approval_gate", True, True, {"approval_threshold_vnd": policy.DEFAULT_APPROVAL_THRESHOLD_VND}),
    # Khoá cứng #4. Ngoại lệ duy nhất (v0.1.50, QD-18): ghi Kho dữ liệu qua `hub_link.service.write_kho` — bước duyệt là
    # Xác nhận + mã PIN của Owner + permit ký (invoke_tool(approved_write=True)); test bất biến khoá chỗ gọi này.
    Boundary("mcp_write_requires_approval", True, True),
)


@dataclass
class BootstrapResult:
    org_id: uuid.UUID
    setup_token: str | None       # chỉ có khi vừa sinh mới (ghi vào tệp 0600, KHÔNG vào log)
    setup_finished: bool


def plugins_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "plugins").is_dir() and (parent / "plugins" / "chassis-kernel").is_dir():
            return parent / "plugins"
    return Path("/app/plugins")


async def bootstrap(db: AsyncSession, *, manifests: list[Manifest] | None = None,
                    mint_setup_code: bool = True) -> BootstrapResult:
    """`mint_setup_code=False` (worker): KHÔNG sinh / ghi đè mã thiết lập — chỉ api sinh và giao mã (P2-2, A10). Worker
    khởi động sau api mà sinh mã mới sẽ ghi đè hash ⇒ mã trong `run/setup-code.json` sai. Mọi phần khác của bootstrap
    (quyền, ranh giới, plugin, tầng dữ liệu, vô hiệu mã khi Owner đã tạo) vẫn chạy ở cả hai tiến trình."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('gh.bootstrap'))"))
    org_id = (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at LIMIT 1"))).scalar()
    if org_id is None:
        org_id = (await db.execute(text(
            "INSERT INTO core.organizations (name, settings) VALUES (:n, CAST(:s AS jsonb)) RETURNING id"),
            {"n": DEFAULT_ORG_NAME, "s": json.dumps({"autonomy_level": policy.DEFAULT_AUTONOMY})})).scalar_one()
        log.info("Đã tạo tổ chức %s", org_id)

    await _permissions(db, org_id)
    await _boundaries(db, org_id)
    await _core_plugins(db, manifests if manifests is not None else read_manifests(plugins_root()))
    await _data_layer(db, org_id)
    token, finished = await _setup_state(db, org_id, mint=mint_setup_code)
    return BootstrapResult(org_id=org_id, setup_token=token, setup_finished=finished)


async def _permissions(db: AsyncSession, org_id: uuid.UUID) -> None:
    for code, label in rbac.PERMISSIONS.items():
        await db.execute(text("""INSERT INTO core.permissions (code, label_vi) VALUES (:c, :l)
                                 ON CONFLICT (code) DO UPDATE SET label_vi = EXCLUDED.label_vi"""),
                         {"c": code, "l": label})
    for role in rbac.ROLES:
        role_id = (await db.execute(text("""
            INSERT INTO core.roles (org_id, code, name, is_system) VALUES (:o, :c, :n, true)
            ON CONFLICT (org_id, code) DO UPDATE SET name = EXCLUDED.name RETURNING id"""),
            {"o": org_id, "c": role.code, "n": role.name})).scalar_one()
        for perm, scope in rbac.DEFAULT_MATRIX[role.code].items():
            # Không ghi đè ô Owner đã chỉnh.
            await db.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                                     VALUES (:r, :p, :s) ON CONFLICT DO NOTHING"""),
                             {"r": role_id, "p": perm, "s": scope})
    # Khoá cứng: Owner luôn giữ toàn quyền; Auditor không bao giờ có quyền ghi.
    await db.execute(text("""
        UPDATE core.role_permissions rp SET scope = 'all' FROM core.roles r
        WHERE rp.role_id = r.id AND r.org_id = :o AND r.code = 'owner' AND rp.scope <> 'all'"""), {"o": org_id})
    await db.execute(text("""
        UPDATE core.role_permissions rp SET scope = 'none' FROM core.roles r
        WHERE rp.role_id = r.id AND r.org_id = :o AND r.code = 'auditor' AND rp.scope <> 'none'
          AND rp.permission_code = ANY(:w)"""), {"o": org_id, "w": list(rbac.WRITE_PERMISSIONS)})


async def _boundaries(db: AsyncSession, org_id: uuid.UUID) -> None:
    for b in BOUNDARIES:
        await db.execute(text("""
            INSERT INTO ops.policy_boundaries (org_id, code, is_enabled, is_locked, params)
            VALUES (:o, :c, :e, :l, CAST(:p AS jsonb)) ON CONFLICT (org_id, code) DO NOTHING"""),
            {"o": org_id, "c": b.code, "e": b.enabled, "l": b.locked, "p": json.dumps(b.params or {})})
        if b.locked:
            await db.execute(text("""UPDATE ops.policy_boundaries SET is_enabled = :e, is_locked = true
                                     WHERE org_id = :o AND code = :c"""), {"o": org_id, "c": b.code, "e": b.enabled})


async def _core_plugins(db: AsyncSession, manifests: list[Manifest]) -> None:
    """Plugin dựng sẵn trong repo (origin=core). Plugin không cho tắt luôn bật; plugin tắt được giữ lựa chọn Owner."""
    for m in manifests:
        await db.execute(text("""
            INSERT INTO ops.plugins (package, name, layer, origin, version, is_enabled, load_order, sandbox,
                                     permissions, signature_ok, manifest)
            VALUES (:pkg, :name, :layer, 'core', :ver, true, :lo, CAST(:sb AS jsonb), :perms, true,
                    CAST(:mf AS jsonb))
            ON CONFLICT (package) DO UPDATE SET name = EXCLUDED.name, version = EXCLUDED.version,
                origin = 'core', is_enabled = CASE WHEN :cd THEN ops.plugins.is_enabled ELSE true END,
                load_order = EXCLUDED.load_order, sandbox = EXCLUDED.sandbox,
                manifest = EXCLUDED.manifest"""),
            {"pkg": m.package, "name": m.name, "layer": m.layer, "ver": m.version, "lo": m.load_order,
             "sb": m.sandbox.model_dump_json(), "perms": m.permissions, "mf": m.model_dump_json(),
             "cd": m.can_disable})
        pid = (await db.execute(text("SELECT id FROM ops.plugins WHERE package = :p"), {"p": m.package})).scalar_one()
        await db.execute(text("DELETE FROM ops.plugin_dependencies WHERE plugin_id = :i"), {"i": pid})
        for dep, spec in m.dependencies.items():
            await db.execute(text("""INSERT INTO ops.plugin_dependencies (plugin_id, depends_on, version_range)
                                     VALUES (:i, :d, :s)"""), {"i": pid, "d": dep, "s": spec})


# Kênh có sẵn (thiết kế: 4 thẻ). Telegram là plugin trong chợ — chưa cài thì không có dòng kênh.
CHANNELS = (("zalo", "Zalo", ["receive", "send"]), ("whatsapp", "WhatsApp", ["receive", "send"]),
            ("linkedin", "LinkedIn", ["identity_only"]))


async def _data_layer(db: AsyncSession, org_id: uuid.UUID) -> None:
    from gh.refinery.presets import DEFAULT_WEIGHTS

    for type_, name, caps in CHANNELS:
        await db.execute(text("""INSERT INTO core.channels (org_id, type, name, capabilities) VALUES (:o, :t, :n, :c)
                                 ON CONFLICT (org_id, type, name) DO NOTHING"""),
                         {"o": org_id, "t": type_, "n": name, "c": caps})
    await db.execute(text("INSERT INTO refinery.schedule (org_id) VALUES (:o) ON CONFLICT DO NOTHING"), {"o": org_id})
    has_weights = (await db.execute(text("SELECT 1 FROM refinery.scoring_weights WHERE org_id = :o LIMIT 1"),
                                    {"o": org_id})).scalar()
    if not has_weights:
        for dim, pct in DEFAULT_WEIGHTS:
            await db.execute(text("""INSERT INTO refinery.scoring_weights (org_id, dimension, weight, valid_from)
                                     VALUES (:o, :d, :w, '2000-01-01')"""), {"o": org_id, "d": dim, "w": pct / 100})


async def _setup_state(db: AsyncSession, org_id: uuid.UUID, *, mint: bool = True) -> tuple[str | None, bool]:
    row = (await db.execute(text("SELECT finished_at, completed FROM ops.setup_state WHERE org_id = :o"),
                            {"o": org_id})).one_or_none()
    if row is None:
        await db.execute(text("INSERT INTO ops.setup_state (org_id, step, completed) VALUES (:o, 1, '{}')"),
                         {"o": org_id})
        finished, owner_created = False, False
    else:
        finished = row.finished_at is not None
        owner_created = (row.completed or {}).get("steps", {}).get("2") == "done"
    if finished or owner_created:
        # Mã thiết lập chỉ dùng tới khi có tài khoản Owner; sau đó vô hiệu (xoá cả tệp mã nếu còn).
        await db.execute(text("UPDATE ops.setup_state SET setup_token_hash = NULL WHERE org_id = :o"), {"o": org_id})
        with contextlib.suppress(OSError):
            (run_root() / SETUP_CODE_FILE).unlink(missing_ok=True)
        return None, finished
    if not mint:
        return None, False        # worker: mã (nếu có) do api đã sinh / giao — không đụng hash, không ghi tệp
    configured = get_settings().setup_token
    token = configured or new_token(9)
    await db.execute(text("UPDATE ops.setup_state SET setup_token_hash = :h WHERE org_id = :o"),
                     {"h": token_digest(token), "o": org_id})
    if not configured:
        _deliver_setup_code(token)
        return token, False
    return None, False


def _deliver_setup_code(token: str) -> None:
    """Giao mã thiết lập tự sinh cho trình cài/Owner qua tệp 0600 (không qua log — luật cứng: bí mật không vào log)."""
    root = run_root()
    path = root / SETUP_CODE_FILE
    try:
        if not root.is_dir():
            raise OSError("không có thư mục hộp thư")
        write_request(root, SETUP_CODE_FILE, {"setup_token": token,
                                              "setup_url": f"{get_settings().public_url}/setup?token={token}"},
                      mode=0o600)
    except OSError:
        log.warning("Đã sinh mã thiết lập Owner nhưng không ghi được tệp %s. Đặt GH_SETUP_TOKEN trong cấu hình "
                    "hoặc chạy `genh reset-setup` để lấy mã.", path)
        return
    log.warning("Mã thiết lập Owner (dùng một lần) nằm trong tệp %s (quyền 0600) — không ghi vào log.", path)
