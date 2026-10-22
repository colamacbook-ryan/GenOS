"""Giai đoạn 4.4 (Plugin & Tiện ích): nạp từ tệp (chữ ký + PIN + trạng thái chờ duyệt quyền, KHÔNG tự chạy),
reset breaker thủ công, nhật ký LIVE bền vững (`ops.plugin_logs`), thứ tự nạp đã đúng từ giai đoạn 1."""

import base64
import hashlib
import os

import orjson
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import text

from gh.config import get_settings
from tests.conftest import OWNER, Api

MANIFEST = {"package": "@ext/demo-tool", "name": "Demo Tool", "version": "1.0.0", "layer": "extension",
           "description": "Plugin thử nạp từ tệp", "permissions": ["contacts.read", "crm.write"]}


def _signed_body(manifest: dict, priv: Ed25519PrivateKey, *, code: bytes = b"print('hello')") -> dict:
    code_sha256 = hashlib.sha256(code).hexdigest()
    payload = orjson.dumps(manifest, option=orjson.OPT_SORT_KEYS) + b"|" + code_sha256.encode()
    sig = base64.b64encode(priv.sign(payload)).decode()
    return {"manifest": manifest, "code_sha256": code_sha256, "signature": sig}


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


# ─── thứ tự nạp (đã đúng từ giai đoạn 1 — kiểm không phá) ───────────────────

async def test_load_order_reflects_registration(owner_api) -> None:  # type: ignore[no-untyped-def]
    items = (await owner_api.get("/plugins")).json()
    core = [p for p in items if p["origin"] == "core"]
    orders = [p["load_order"] for p in core]
    assert orders == sorted(orders)   # kernel → bus → store → policy/auth → intel-core → kênh → ui, không xáo trộn
    assert core[0]["package"] == "@gen/chassis-kernel"


# ─── reset breaker ───────────────────────────────────────────────────────────

async def test_reset_breaker_closes_open_circuit(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    pm = app.state.plugins
    lp = pm.get("@gen/channel-zalo")
    for _ in range(10):
        lp.breaker.record_failure("giả lập lỗi")
    assert lp.breaker.state == "open"
    r = await api.send("POST", "/plugins/@gen/channel-zalo/breaker/reset", {})
    assert r.status_code == 200, r.text
    assert r.json()["breaker"]["state"] == "closed"
    assert lp.breaker.state == "closed"
    row = (await db.execute(text("SELECT action FROM ops.action_log WHERE action = 'plugin.breaker_reset'"))).all()
    assert len(row) == 1


async def test_reset_breaker_unknown_plugin_404(owner_api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", "/plugins/@gen/khong-co/breaker/reset", {})
    assert r.status_code == 404


# ─── nhật ký LIVE bền vững ───────────────────────────────────────────────────

async def test_plugin_logs_persisted_and_listable(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    pm = app.state.plugins
    await pm.log_sink("@gen/channel-zalo", "WARN", "thử một dòng log")
    r = await api.get("/plugins/@gen/channel-zalo/logs")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(x["level"] == "WARN" and x["message"] == "thử một dòng log" for x in items)


async def test_plugin_logs_unknown_plugin_404(owner_api) -> None:  # type: ignore[no-untyped-def]
    assert (await owner_api.get("/plugins/@gen/khong-co/logs")).status_code == 404


# ─── nạp từ tệp (local_file) ─────────────────────────────────────────────────

async def test_local_install_requires_pin(owner_api) -> None:  # type: ignore[no-untyped-def]
    body = _signed_body(MANIFEST, Ed25519PrivateKey.generate())
    r = await owner_api.send("POST", "/plugins/local", body)
    assert r.status_code == 423


async def test_local_install_rejects_untrusted_signature(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await _pin(api)
    os.environ["GH_PLUGIN_TRUSTED_SIGNING_KEYS"] = ""
    get_settings.cache_clear()
    try:
        body = _signed_body(MANIFEST, Ed25519PrivateKey.generate())   # không khoá nào tin cậy
        r = await api.send("POST", "/plugins/local", body)
        assert r.status_code == 409 and r.json()["code"] == "SIGNATURE_INVALID"
    finally:
        os.environ.pop("GH_PLUGIN_TRUSTED_SIGNING_KEYS", None)
        get_settings.cache_clear()
    n = (await db.execute(text("SELECT count(*) FROM ops.plugins WHERE package = :p"),
                          {"p": MANIFEST["package"]})).scalar()
    assert n == 0
    log = (await db.execute(text("SELECT result FROM ops.action_log WHERE action = 'plugin.install_rejected'"))
          ).all()
    assert len(log) == 1 and log[0].result == "blocked"


async def test_local_install_with_trusted_signature_is_pending_and_never_autoruns(
        owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await _pin(api)
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    os.environ["GH_PLUGIN_TRUSTED_SIGNING_KEYS"] = pub_b64
    get_settings.cache_clear()
    try:
        body = _signed_body(MANIFEST, priv)
        r = await api.send("POST", "/plugins/local", body)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["permissions_status"] == "pending" and out["is_enabled"] is False and out["signature_ok"] is True
    finally:
        os.environ.pop("GH_PLUGIN_TRUSTED_SIGNING_KEYS", None)
        get_settings.cache_clear()
    row = (await db.execute(text("SELECT origin, is_enabled, permissions_status, code_sha256 FROM ops.plugins "
                                 "WHERE package = :p"), {"p": MANIFEST["package"]})).one()
    assert row.origin == "local_file" and row.is_enabled is False and row.permissions_status == "pending"
    assert row.code_sha256 is not None
    # KHÔNG được nạp vào PluginManager đang chạy (mã chưa được duyệt/kiểm soát) — không đăng ký, toggle → 404.
    assert MANIFEST["package"] not in app.state.plugins.plugins
    await _pin(api)
    r = await api.send("PATCH", f"/plugins/{MANIFEST['package']}/toggle", {"enabled": True})
    assert r.status_code == 404
    items = (await api.get("/plugins")).json()
    mine = next(p for p in items if p["package"] == MANIFEST["package"])
    assert mine["permissions_status"] == "pending" and mine["enabled"] is False


async def test_local_install_duplicate_package_conflict(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await _pin(api)
    body = _signed_body({**MANIFEST, "package": "@gen/chassis-kernel"}, Ed25519PrivateKey.generate())
    r = await api.send("POST", "/plugins/local", body)
    assert r.status_code == 409 and r.json()["code"] == "PLUGIN_EXISTS"
