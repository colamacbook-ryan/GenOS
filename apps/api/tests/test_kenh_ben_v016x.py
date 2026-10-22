"""Gói B4 (đợt sửa sau kiểm kê v0.1.60): KÊNH BỀN.

- F046: mất mạng KHÔNG xoá phiên Zalo/WhatsApp đã lưu — phiên 'error' giữ credential, bridge.hello nối lại cả
  phiên 'error',
  có nút "Nối lại bằng phiên đã lưu" (POST /channels/{type}/resume), chuông nói đúng nguyên nhân.
- F063: ảnh QR trong Redis sống theo `expires_at` của sự kiện (Zalo 100 giây) chứ không cố định 70 giây.
- F067: phiên mới `active` ⇒ lõi ra lệnh `session.close` cho phiên cũ cùng kênh (bridge không giữ kết nối ma).
- F068: nối lại cùng phiên giữ `started_at` gốc; "Thời gian trực" đo 24 giờ thực.
- F069: BRIDGE_OFFLINE nói tiếng người ("Bộ nhận tin Zalo/WhatsApp đang tắt…"), không còn chữ "Bridge".
"""

import json
from datetime import UTC, datetime, timedelta

import orjson
from sqlalchemy import text

from gh import crypto
from gh.chassis.bus import BRIDGE_CONTROL
from gh.data import ingest
from gh.data.ingest import channel_down_text, handle_status, qr_ttl_s
from gh.db import sessionmaker
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_rbac_api import login_as

CRED = {"imei": "imei-1", "cookie": [{"key": "zpw", "value": "v"}], "userAgent": "UA"}
SHOP = {"id": "9", "name": "Cửa hàng"}


async def _channel(db, org, type_="zalo"):  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                             {"o": org, "t": type_})).scalar_one()


async def _session(db, org, *, state="active", ended=False, started_ago_h: float | None = 1.0, cred=True,  # type: ignore[no-untyped-def]
                   type_="zalo", label="Zalo Sếp", qr_ago_h: float = 1.0):
    ch = await _channel(db, org, type_)
    enc = crypto.encrypt(json.dumps(CRED).encode(), b"channel_session") if cred else None
    sid = (await db.execute(text("""
        INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at, ended_at, qr_issued_at,
                                           credential_enc, last_heartbeat_at)
        VALUES (:c, :o, :l, :st, CASE WHEN CAST(:sa AS float) IS NULL THEN NULL
                                      ELSE now() - make_interval(secs => CAST(:sa AS float) * 3600) END,
                CASE WHEN :ended THEN now() - interval '5 minutes' END,
                now() - make_interval(secs => CAST(:qa AS float) * 3600), :enc, now()) RETURNING id"""),
        {"c": ch, "o": org, "l": label, "st": state, "sa": started_ago_h, "ended": ended, "qa": qr_ago_h,
         "enc": enc})).scalar_one()
    await db.commit()
    return sid


async def _row(db, sid):  # type: ignore[no-untyped-def]
    await db.rollback()
    return (await db.execute(text("""SELECT state, ended_at, started_at, credential_enc IS NOT NULL AS has_cred, meta
                                     FROM core.channel_sessions WHERE id = :i"""), {"i": sid})).one()


async def _status(app, redis, org, type_, payload):  # type: ignore[no-untyped-def]
    async with sessionmaker()() as s:
        out = await handle_status(s, redis, app.state.bus, org, type_, payload)
        await s.commit()
    return out


async def _control(redis) -> list[dict]:  # type: ignore[no-untyped-def, type-arg]
    rows = await redis.xrange(BRIDGE_CONTROL)
    return [{"type": f[b"type"].decode(), **orjson.loads(f[b"payload"])} for _, f in rows]


async def _bells(db):  # type: ignore[no-untyped-def]
    await db.rollback()
    return (await db.execute(text("""SELECT title, body FROM core.notifications WHERE kind = 'channel.down'
                                     ORDER BY created_at"""))).all()


# ─── F063: QR sống đủ lâu ─────────────────────────────────────────────────────

def test_qr_ttl_theo_expires_at() -> None:
    now = datetime(2026, 10, 16, 3, 0, 0, tzinfo=UTC)
    assert qr_ttl_s((now + timedelta(seconds=100)).isoformat(), now) == 100 + ingest.QR_TTL_MARGIN_S   # Zalo
    soon = (now + timedelta(seconds=20)).isoformat().replace("+00:00", "Z")
    assert qr_ttl_s(soon, now) == 20 + ingest.QR_TTL_MARGIN_S
    assert qr_ttl_s((now - timedelta(seconds=30)).isoformat(), now) == 5        # đã quá hạn: giữ rất ngắn
    for bad in (None, "", "không-phải-ngày", 123):
        assert qr_ttl_s(bad, now) == ingest.QR_TTL_FALLBACK_S >= 100


async def test_qr_zalo_khong_bien_mat_som(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await _session(db, org, state="pending_qr", started_ago_h=None, cred=False)
    exp = (datetime.now(UTC) + timedelta(seconds=100)).isoformat()
    await _status(app, redis, org, "session.qr",
                  {"session_id": str(sid), "image": "data:image/png;base64,AAAA", "expires_at": exp})
    ttl = await redis.ttl(f"gh:channel:qr:{sid}")
    assert 100 <= ttl <= 110, f"QR Zalo phải sống tới hết 100 giây của bridge, không phải 70 giây (ttl={ttl})"
    await _status(app, redis, org, "session.scanned", {"session_id": str(sid)})
    assert await redis.ttl(f"gh:channel:qr:{sid}") >= 5


# ─── F046: mất mạng không xoá phiên ───────────────────────────────────────────

def test_chuong_noi_dung_nguyen_nhan() -> None:
    cause, todo = channel_down_text("error", "NETWORK_UNAVAILABLE")
    assert "mạng" in cause and "hết hạn" not in cause and "không cần quét lại QR" in todo
    cause, todo = channel_down_text("expired", "CREDENTIAL_REJECTED")
    assert "không còn nhận phiên đã lưu" in cause and "quét mã QR" in todo
    cause, todo = channel_down_text("error", "CONNECTION_REPLACED")
    assert "một nơi khác" in cause and "Nối lại bằng phiên đã lưu" in todo
    assert channel_down_text("logged_out", None)[0] == "đã bị đăng xuất"
    assert channel_down_text("error", None)[0] == "gặp lỗi"


async def test_mat_mang_giu_phien_va_noi_lai_giu_started_at(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await _session(db, org, state="active", started_ago_h=30)
    before = await _row(db, sid)
    lost = {"session_id": str(sid), "error": "NETWORK_UNAVAILABLE"}
    out = await _status(app, redis, org, "session.reconnecting", lost)
    row = await _row(db, sid)
    assert row.state == "error" and row.has_cred and row.ended_at is None
    assert row.meta["error"] == "NETWORK_UNAVAILABLE"
    assert out[0][0] == "channel.status"
    assert (out[0][1]["state"], out[0][1]["error"]) == ("error", "NETWORK_UNAVAILABLE")
    bells = await _bells(db)
    assert len(bells) == 1 and "mất kết nối mạng" in bells[0].body and "hết hạn" not in bells[0].body
    # Lặp lại báo cáo: không chuông thứ hai.
    await _status(app, redis, org, "session.reconnecting", lost)
    assert len(await _bells(db)) == 1

    # Nối lại được: về Đang kết nối, "Phiên mở" giữ mốc gốc (F068).
    await _status(app, redis, org, "session.active", {"session_id": str(sid), "account": SHOP})
    after = await _row(db, sid)
    assert after.state == "active" and after.ended_at is None and after.has_cred
    moved = abs((after.started_at - before.started_at).total_seconds())
    assert moved < 1, "nối lại cùng phiên không được đặt lại started_at"


async def test_chi_bi_tu_choi_that_moi_xoa_phien(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    rejected = await _session(db, org, state="active")
    await _status(app, redis, org, "session.ended", {"session_id": str(rejected), "reason": "expired",
                                                      "error": "CREDENTIAL_REJECTED"})
    row = await _row(db, rejected)
    assert row.state == "expired" and not row.has_cred
    bells = await _bells(db)
    assert "không còn nhận phiên đã lưu" in bells[-1].body and "quét mã QR" in bells[-1].body

    replaced = await _session(db, org, state="active")
    await _status(app, redis, org, "session.ended", {"session_id": str(replaced), "reason": "error",
                                                      "error": "CONNECTION_REPLACED"})
    row = await _row(db, replaced)
    assert row.state == "error" and row.has_cred and row.ended_at is not None
    assert "một nơi khác" in (await _bells(db))[-1].body


async def test_hello_noi_lai_ca_phien_error_con_credential(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    err = await _session(db, org, state="error", ended=True, started_ago_h=3)
    await _status(app, redis, org, "bridge.hello", {"channels": ["zalo", "whatsapp"]})
    ctl = await _control(redis)
    assert [(c["type"], c["session_id"]) for c in ctl] == [("session.login", str(err))]
    assert ctl[0]["credential"], "phải kèm credential mã hoá để bridge đăng nhập lại"
    assert "imei-1" not in str(ctl[0]), "credential chỉ đi dạng mã hoá vận chuyển"


async def test_hello_khong_song_lai_phien_cu_da_bi_thay(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    # Phiên lỗi cũ còn credential…
    await _session(db, org, state="error", ended=True, started_ago_h=48, type_="zalo")
    # …nhưng Sếp đã đăng xuất sau đó.
    await _session(db, org, state="logged_out", ended=True, started_ago_h=2, cred=False, type_="zalo")
    await _status(app, redis, org, "bridge.hello", {"channels": ["zalo"]})
    assert await _control(redis) == []


# ─── nút "Nối lại bằng phiên đã lưu" ─────────────────────────────────────────

async def test_the_kenh_bao_resumable_va_bridge_online(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    api: Api = owner_api
    await _session(db, org, state="error", ended=True)
    card = {c["type"]: c for c in (await api.get("/channels")).json()}["zalo"]
    assert card["state"] == "error" and card["resumable"] is True
    assert card["bridge_online"] is False                       # chưa có nhịp tim của bridge
    await redis.set("gh:bridge:heartbeat", "1")
    card = {c["type"]: c for c in (await api.get("/channels")).json()}["zalo"]
    assert card["bridge_online"] is True
    assert {c["type"]: c for c in (await api.get("/channels")).json()}["linkedin"]["bridge_online"] is None


async def test_resume_gui_lenh_dang_nhap_lai_bang_phien_da_luu(owner_api, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    api: Api = owner_api
    r = await api.send("POST", "/channels/zalo/resume")
    assert r.status_code == 503 and r.json()["code"] == "BRIDGE_OFFLINE"
    await redis.set("gh:bridge:heartbeat", "1")
    r = await api.send("POST", "/channels/zalo/resume")
    assert r.status_code == 409 and r.json()["code"] == "NO_SAVED_SESSION"      # chưa có phiên nào để nối
    sid = await _session(db, org, state="error", ended=True)
    r = await api.send("POST", "/channels/zalo/resume")
    assert r.status_code == 202 and r.json()["session_id"] == str(sid)
    [c] = await _control(redis)
    assert (c["type"], c["channel"], c["session_id"]) == ("session.login", "zalo", str(sid)) and c["credential"]
    assert (await api.send("POST", "/channels/telegram/resume")).status_code == 404
    manager = await login_as(client, db, "manager")
    assert (await manager.send("POST", "/channels/zalo/resume")).status_code == 403


async def test_resume_khong_dung_cho_phien_het_han(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await redis.set("gh:bridge:heartbeat", "1")
    await _session(db, org, state="expired", ended=True, cred=False)
    r = await owner_api.send("POST", "/channels/zalo/resume")
    assert r.status_code == 409 and r.json()["code"] == "NO_SAVED_SESSION"


# ─── F067: phiên mới đóng phiên cũ ────────────────────────────────────────────

async def test_phien_moi_active_dong_phien_cu_tren_bridge(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    old = await _session(db, org, state="active", started_ago_h=10)
    stale = await _session(db, org, state="error", started_ago_h=5)          # đang thử nối lại (ended_at NULL)
    new = await _session(db, org, state="pending_qr", started_ago_h=None, cred=False, qr_ago_h=0.01)
    await _status(app, redis, org, "session.active", {"session_id": str(new), "account": SHOP})
    ctl = await _control(redis)
    closed = sorted(c["session_id"] for c in ctl if c["type"] == "session.close")
    assert closed == sorted([str(old), str(stale)])
    assert all(c["channel"] == "zalo" for c in ctl)
    assert not [c for c in ctl if c["type"] == "session.logout"], "đóng kết nối, KHÔNG đăng xuất nền tảng"
    assert (await _row(db, old)).state == "logged_out" and (await _row(db, new)).state == "active"


async def test_quet_lai_qr_huy_vong_xin_qr_cu_tren_bridge(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    api: Api = owner_api
    await redis.set("gh:bridge:heartbeat", "1")
    pending = await _session(db, org, state="pending_qr", started_ago_h=None, cred=False, qr_ago_h=0.01)
    assert (await api.send("POST", "/auth/pin/verify", {"pin": "246810"})).status_code == 200
    r = await api.send("POST", "/channels/zalo/login", {"accept_risk": True})
    assert r.status_code == 202
    ctl = await _control(redis)
    closes = [(c["type"], c["session_id"]) for c in ctl if c["type"] == "session.close"]
    assert closes == [("session.close", str(pending))]
    assert [c["session_id"] for c in ctl if c["type"] == "session.login"] == [r.json()["session_id"]]


# ─── F068: thời gian trực đo 24 giờ thực ──────────────────────────────────────

async def test_thoi_gian_truc_khong_bi_dat_lai_khi_noi_lai(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    # Phiên đầu nối từ 20 giờ trước rồi mất mạng; phiên hiện tại
    # vừa nối lại 1 phút trước. Chỉ có nhịp tim trong 60 phút qua.
    await _session(db, org, state="logged_out", ended=True, started_ago_h=20, cred=False)
    await _session(db, org, state="active", started_ago_h=1 / 60)
    now = datetime.now(UTC)
    pipe = redis.pipeline()
    for m in range(60):
        t = now - timedelta(minutes=m)
        key = ingest.uptime_minutes_key("zalo", t)
        pipe.setbit(key, t.hour * 60 + t.minute, 1)
    await pipe.execute()
    card = {c["type"]: c for c in (await owner_api.get("/channels")).json()}["zalo"]
    pct = card["stats"]["uptime_pct"]
    assert pct is not None and pct < 15, f"20 giờ qua chỉ có 1 giờ nhịp tim ⇒ không được hiện {pct}%"


# ─── F069: câu chữ ────────────────────────────────────────────────────────────

async def test_bridge_offline_noi_tieng_nguoi(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": "246810"})).status_code == 200
    r = await owner_api.send("POST", "/channels/zalo/login", {"accept_risk": True})
    assert r.status_code == 503 and r.json()["code"] == "BRIDGE_OFFLINE"
    title = r.json()["title"]
    assert title.startswith("Bộ nhận tin Zalo/WhatsApp đang tắt")
    assert "Bridge" not in title and "bridge" not in title
    assert "Gói chẩn đoán" in title



# ─── Gói K (rà soát v0.1.62, P1-1/P2-4): phiên 'error' đăng xuất được; CONNECTION_REPLACED không tự nối lại ──

async def _pin(api: Api) -> None:
    assert (await api.send("POST", "/auth/pin/verify", {"pin": "246810"})).status_code == 200


async def test_dang_xuat_xoa_ca_phien_error_con_credential(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    api: Api = owner_api
    # Phiên bị ngắt vì mở nơi khác: đã có ended_at nhưng vẫn giữ credential (để Sếp bấm "Nối lại").
    sid = await _session(db, org, state="error", ended=True)
    before = await _row(db, sid)
    assert before.has_cred and before.ended_at is not None
    await _pin(api)
    r = await api.send("POST", "/channels/zalo/logout")
    assert r.status_code == 204
    after = await _row(db, sid)
    assert after.state == "logged_out" and not after.has_cred, "đăng xuất phải xoá credential của phiên lỗi"
    assert abs((after.ended_at - before.ended_at).total_seconds()) < 1, "giữ mốc kết thúc gốc"
    card = {c["type"]: c for c in (await api.get("/channels")).json()}["zalo"]
    assert card["state"] == "logged_out" and card["resumable"] is False
    assert [c["session_id"] for c in await _control(redis) if c["type"] == "session.logout"] == [str(sid)]
    # Sau đăng xuất, bridge khởi động lại không còn gì để tự nối.
    await redis.delete(BRIDGE_CONTROL)
    await redis.set("gh:bridge:heartbeat", "1")
    assert (await api.send("POST", "/channels/zalo/resume")).status_code == 409


async def test_hello_khong_tu_noi_lai_phien_bi_mo_noi_khac(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await _session(db, org, state="active")
    await _status(app, redis, org, "session.ended", {"session_id": str(sid), "reason": "error",
                                                      "error": "CONNECTION_REPLACED"})
    assert (await _row(db, sid)).state == "error"
    await _status(app, redis, org, "bridge.hello", {"channels": ["zalo", "whatsapp"]})
    assert await _control(redis) == [], "bridge restart không được đăng nhập lại, đá Zalo Web của Sếp"
    # Lỗi khác (rớt mạng…) vẫn tự nối lại như cũ.
    other = await _session(db, org, state="active", type_="whatsapp")
    await _status(app, redis, org, "session.ended", {"session_id": str(other), "reason": "error",
                                                      "error": "RECONNECT_FAILED"})
    await _status(app, redis, org, "bridge.hello", {"channels": ["zalo", "whatsapp"]})
    assert [(c["type"], c["session_id"]) for c in await _control(redis)] == [("session.login", str(other))]


async def test_nut_noi_lai_van_dung_duoc_voi_phien_bi_mo_noi_khac(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await _session(db, org, state="active")
    await _status(app, redis, org, "session.ended", {"session_id": str(sid), "reason": "error",
                                                      "error": "CONNECTION_REPLACED"})
    await redis.set("gh:bridge:heartbeat", "1")
    card = {c["type"]: c for c in (await owner_api.get("/channels")).json()}["zalo"]
    assert card["state"] == "error" and card["error"] == "CONNECTION_REPLACED" and card["resumable"] is True
    r = await owner_api.send("POST", "/channels/zalo/resume")
    assert r.status_code == 202 and r.json()["session_id"] == str(sid)


async def test_resume_bo_qua_phien_error_da_co_phien_moi_hon(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await redis.set("gh:bridge:heartbeat", "1")
    await _session(db, org, state="error", ended=True, started_ago_h=48)     # phiên lỗi cũ còn credential
    await _session(db, org, state="logged_out", ended=True, started_ago_h=2, cred=False)   # Sếp đã đăng xuất sau đó
    r = await owner_api.send("POST", "/channels/zalo/resume")
    assert r.status_code == 409 and r.json()["code"] == "NO_SAVED_SESSION", "không được sống lại phiên cũ đã bị thay"
    assert await _control(redis) == []


# ─── Review lần 2 v0.1.62: phiên đã đăng xuất không sống lại vì sự kiện muộn của bridge ─────────────────────────────

async def test_session_active_tre_khong_hoi_sinh_phien_da_dang_xuat(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    dead = await _session(db, org, state="logged_out", ended=True, started_ago_h=3, cred=False)
    live = await _session(db, org, state="active", started_ago_h=1)
    out = await _status(app, redis, org, "session.active", {"session_id": str(dead), "account": SHOP})
    assert out == []                                                          # không phát channel.status "active"
    assert (await _row(db, dead)).state == "logged_out" and (await _row(db, dead)).ended_at is not None
    assert (await _row(db, live)).state == "active"                          # phiên đang chạy không bị đóng nhầm
    assert not [c for c in await _control(redis) if c["type"] == "session.close"]
    # Phiên 'error' (mất mạng, đang nối lại) vẫn nối lại được như cũ.
    err = await _session(db, org, state="error", started_ago_h=2)
    await _status(app, redis, org, "session.active", {"session_id": str(err), "account": SHOP})
    assert (await _row(db, err)).state == "active"
