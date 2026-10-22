"""v0.1.47 — Facebook GHI (trả lời bình luận / nhắn tin) có xác nhận: migration 0031, PIN, permit ký, Dừng tất cả,
trần lượt/ngày, cổng F-85 (sandbox hoặc đồng ý rủi ro), đích phải là mục vừa đọc, ảnh chụp bằng chứng mã hoá, không
lộ bí mật.

Worker GIẢ như tests/test_social.py: đọc `gh:browser:jobs` (kiểm chữ ký), trả kết quả ký bằng khoá browser.
"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

import orjson
import psycopg
import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from gh import crypto
from gh.chassis.objects import ObjectNotFound, get_object_store
from gh.db import admin_sessionmaker
from gh.social import permit, platforms, protocol, service
from tests.conftest import PG, Api
from tests.phase2 import org_id
from tests.test_social import STATE, _add, _db_text, _deliver, _jobs, _login, _pin, _result

SQL = Path(__file__).resolve().parents[3] / "db" / "sql" / "0031_v0147_social_write.sql"
COMMENT_URL = "https://www.facebook.com/permalink.php?story_fbid=123&comment_id=456"
DM_URL = "https://www.facebook.com/messages/t/777"
READ_ITEMS = [
    {"kind": "notification", "who": "Chị Lan", "text": "Chị Lan đã bình luận: \"Giá bao nhiêu?\"", "time": "5 phút",
     "unread": True, "link": COMMENT_URL},
    {"kind": "inbox", "who": "Anh Tuấn", "text": "Anh còn hàng không em?", "time": "1 giờ", "unread": True,
     "link": DM_URL},
]
REPLY = "Cảm ơn chị Lan, giá 250k ạ!"
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(200)) + b"HINH-ANH-GIA-LAP"


async def _clear_pin(api: Api) -> None:
    me = (await api.get("/auth/me")).json()
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.sessions SET pin_verified_until = NULL WHERE user_id = :u"), {"u": me["id"]})
        await db.commit()


async def _heartbeat(redis: Redis, sandbox: dict[str, Any] | None) -> None:
    body: dict[str, Any] = {"version": "t", "at": "x", "running": 0}
    if sandbox is not None:
        body["sandbox"] = sandbox
    await redis.set(protocol.HEARTBEAT_KEY, orjson.dumps(body), ex=45)


async def _read_done(api: Api, redis: Redis, acc_id: str, items: list[dict[str, Any]] | None = None) -> None:
    r = await api.send("POST", f"/social/accounts/{acc_id}/read", {})
    assert r.status_code == 200, r.text
    job = (await _jobs(redis))[-1]
    await _deliver(redis, _result(job, "done", {"items": items or READ_ITEMS, "pages": 1}, state=STATE))


async def _ready(api: Api, redis: Redis, *, gate: bool = True) -> str:
    """Tài khoản đã đăng nhập + một lượt đọc xong (có link bình luận/hội thoại) + cổng ghi mở (sandbox bật)."""
    acc = await _add(api)
    await _login(api, redis, acc["id"])
    await _read_done(api, redis, acc["id"])
    if gate:
        await _heartbeat(redis, {"enabled": True, "mode": "auto", "reason": None, "checked_at": "2026-10-03T01:00:00Z"})
    await _pin(api)
    return str(acc["id"])


def _body(**over: Any) -> dict[str, Any]:
    return {"action": "reply_comment", "target_url": COMMENT_URL, "text": REPLY} | over


async def _write(api: Api, acc_id: str, **over: Any) -> Any:
    return await api.send("POST", f"/social/accounts/{acc_id}/write", _body(**over))


def _write_done(job: dict[str, Any], *, proof: bool = True, confirmed: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {"action": job["payload"]["action"], "sent": True, "confirmed": confirmed,
                            "trace": [{"step": "open", "ms": 1200, "ok": True}, {"step": "send", "ms": 3100,
                                                                                "ok": True}],
                            "cost": {"ms": 9000}}
    if proof:
        aad = f"{job['org_id']}:{job['account_id']}:proof:{job['id']}"
        data |= {"proof": protocol.seal(crypto.browser_key(), JPEG, aad),
                 "proof_sha256": hashlib.sha256(JPEG).hexdigest()}
    return _result(job, "done", data, state=STATE)


async def _write_jobs(redis: Redis) -> list[dict[str, Any]]:
    return [j for j in await _jobs(redis) if j["kind"] == "write"]


# ─── 1. migration 0031 ─────────────────────────────────────────────────────────

async def test_migration_0031_is_rerunnable_and_constrains_action(owner_api: Api, fresh_db: str, db: Any) -> None:
    acc = await _add(owner_api)
    sql = SQL.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)           # lần 2 (migration đã chạy một lần khi dựng CSDL mẫu) — không lỗi
        c.execute(sql)
    ins = """INSERT INTO agent.browser_jobs (org_id, account_id, kind, action)
             SELECT org_id, id, 'write', :a FROM core.social_accounts WHERE id = :i"""
    await db.execute(text(ins), {"a": "reply_comment", "i": acc["id"]})
    await db.commit()
    with pytest.raises(IntegrityError):
        await db.execute(text(ins), {"a": "dang_bai", "i": acc["id"]})
    await db.rollback()
    default = (await db.execute(text("SELECT daily_write_limit FROM core.social_accounts WHERE id = :i"),
                                {"i": acc["id"]})).scalar_one()
    assert default == 10
    with pytest.raises(IntegrityError):
        await db.execute(text("UPDATE core.social_accounts SET daily_write_limit = 21 WHERE id = :i"),
                         {"i": acc["id"]})
    await db.rollback()


# ─── 2. thiếu PIN ──────────────────────────────────────────────────────────────

async def test_write_without_pin_is_refused_and_queues_nothing(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    await _clear_pin(owner_api)
    before = await redis.xlen(protocol.JOBS_STREAM)
    r = await _write(owner_api, acc_id)
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    assert (await db.execute(text("SELECT count(*) FROM agent.browser_jobs WHERE kind = 'write'"))).scalar_one() == 0
    assert await redis.xlen(protocol.JOBS_STREAM) == before


# ─── 3. permit ─────────────────────────────────────────────────────────────────

async def test_permit_issue_verify_and_payload_in_stream(owner_api: Api, redis: Redis) -> None:
    acc_id = await _ready(owner_api, redis)
    r = await _write(owner_api, acc_id)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["kind"] == "write" and out["action"] == "reply_comment" and out["has_proof"] is False
    assert out["via"] == "user" and "proof_key" not in out
    job = (await _write_jobs(redis))[-1]
    pl = job["payload"]
    assert pl["action"] == "reply_comment" and pl["target_url"] == COMMENT_URL and pl["text"] == REPLY
    assert pl["timeout_s"] == 180 and pl["state"]
    p = protocol.verify(crypto.browser_key(), protocol.P_PERMIT, pl["permit"])
    assert p is not None
    me = (await owner_api.get("/auth/me")).json()
    assert p["v"] == 1 and p["job_id"] == job["id"] and p["org_id"] == job["org_id"]
    assert p["account_id"] == acc_id and p["action"] == "reply_comment" and p["confirmed_by"] == me["id"]
    assert p["target_url_sha256"] == hashlib.sha256(COMMENT_URL.encode()).hexdigest()
    assert p["body_sha256"] == hashlib.sha256(REPLY.encode()).hexdigest()
    assert p["exp"] - p["iat"] == 300 and len(p["nonce"]) == 32
    args = {"job_id": job["id"], "org_id": job["org_id"], "account_id": acc_id, "action": "reply_comment",
            "target_url": COMMENT_URL, "text": REPLY}
    assert permit.verify(pl["permit"], **args, now=p["iat"] + 10) is None
    assert permit.verify(pl["permit"], **args, now=p["exp"] + 1) == "PERMIT_EXPIRED"
    assert permit.verify(pl["permit"], **(args | {"text": REPLY[:-1] + "?"}), now=p["iat"]) == "PERMIT_MISMATCH"
    assert permit.verify(pl["permit"], **(args | {"job_id": "khac"}), now=p["iat"]) == "PERMIT_MISMATCH"
    # Đổi ký tự đầu thành ký tự KHÁC: sig vốn bắt đầu bằng "A" (xác suất 1/64) thì "A"+sig[1:] trùng chữ ký gốc
    # ⇒ test chập chờn (CI đỏ ở v0.1.50).
    sig0 = pl["permit"]["sig"]
    bad = {**pl["permit"], "sig": ("B" if sig0[0] == "A" else "A") + sig0[1:]}
    assert permit.verify(bad, **args, now=p["iat"]) == "PERMIT_BAD_SIG"
    assert permit.verify(None, **args) == "PERMIT_MISSING"
    # Ghi nhật ký: chỉ sha256, không nguyên văn nội dung.
    log = await _db_text("SELECT detail FROM ops.action_log WHERE action = 'social.write_requested'")
    assert hashlib.sha256(REPLY.encode()).hexdigest() in log and "Cảm ơn chị Lan" not in log


# ─── 4. Dừng tất cả ────────────────────────────────────────────────────────────

async def test_kill_switch_blocks_writes_and_accepts_late_done(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    assert (await _write(owner_api, acc_id)).status_code == 201
    job = (await _write_jobs(redis))[-1]
    await _deliver(redis, _result(job, "started"))
    assert (await owner_api.send("POST", "/social/halt", {})).json()["halted"] is True
    await _pin(owner_api)
    assert (await db.execute(text("SELECT status FROM agent.browser_jobs WHERE id = :i"),
                             {"i": job["id"]})).scalar_one() == "halted"
    r = await _write(owner_api, acc_id, text="Câu khác")
    assert r.status_code == 409 and r.json()["code"] == "SOCIAL_HALTED"
    assert len(await _write_jobs(redis)) == 1
    # Worker đã bấm gửi trước khi kịp dừng → kết quả 'done' đến muộn vẫn được ghi.
    await db.rollback()
    assert await _deliver(redis, _write_done(job)) == "ok"
    await db.rollback()
    got = (await owner_api.get(f"/social/jobs/{job['id']}")).json()
    assert got["status"] == "done" and got["error"] is None and got["has_proof"] is True
    assert got["result"]["after_halt"] is True and got["result"]["sent"] is True
    assert "social.write" in await _db_text("SELECT action FROM ops.action_log")
    async with admin_sessionmaker()() as adb:
        detail = (await adb.execute(text("SELECT detail FROM ops.action_log WHERE action = 'social.write'"))
                  ).scalar_one()
    assert detail["after_halt"] is True and detail["confirmed"] is True and len(detail["text_sha256"]) == 64


# ─── 5. trần lượt/ngày ─────────────────────────────────────────────────────────

async def test_daily_write_limit_enforced_and_lowered_only(owner_api: Api, redis: Redis) -> None:
    acc_id = await _ready(owner_api, redis)
    r = await owner_api.send("PATCH", f"/social/accounts/{acc_id}", {"daily_write_limit": 2})
    assert r.status_code == 200 and r.json()["daily_write_limit"] == 2
    for _ in range(2):
        assert (await _write(owner_api, acc_id)).status_code == 201
        await _deliver(redis, _write_done((await _write_jobs(redis))[-1]))
    r = await _write(owner_api, acc_id)
    assert r.status_code == 429 and r.json()["code"] == "SOCIAL_WRITE_LIMIT" and "2 lượt" in r.json()["title"]
    assert (await owner_api.get(f"/social/accounts/{acc_id}")).json()["writes_today"] == 2
    assert (await owner_api.send("PATCH", f"/social/accounts/{acc_id}", {"daily_write_limit": 21})).status_code == 422
    assert (await owner_api.send("PATCH", f"/social/accounts/{acc_id}", {"daily_write_limit": 0})).status_code == 422
    st = (await owner_api.get("/social/status")).json()["limits"]
    assert st["writes_per_day_max"] == 20 and st["write_delay_s"] == 3


# ─── 6. cổng F-85 ──────────────────────────────────────────────────────────────

async def test_write_gate_sandbox_or_consent(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis, gate=False)
    gate = (await owner_api.get("/social/write-gate")).json()
    assert gate["open"] is False and gate["worker_online"] is False and gate["consent"] is None
    assert gate["sandbox"] == {"enabled": None, "reason": None, "checked_at": None}
    assert gate["version"] == platforms.WRITE_RISK_VERSION and 3 <= len(gate["risk"]) <= 5
    # Nhịp tim báo KHÔNG có sandbox + chưa đồng ý → khoá.
    await _heartbeat(redis, {"enabled": False, "mode": "auto", "reason": "x" * 300, "checked_at": None})
    gate = (await owner_api.get("/social/write-gate")).json()
    assert gate["open"] is False and gate["worker_online"] is True and len(gate["sandbox"]["reason"]) <= 120
    r = await _write(owner_api, acc_id)
    assert r.status_code == 409 and r.json()["code"] == "SOCIAL_WRITE_LOCKED"
    assert len(await _write_jobs(redis)) == 0
    # Đồng ý rủi ro: cần PIN; sai phiên bản → 409.
    await _clear_pin(owner_api)
    body = {"version": platforms.WRITE_RISK_VERSION}
    assert (await owner_api.send("POST", "/social/write-consent", body)).status_code == 423
    await _pin(owner_api)
    r = await owner_api.send("POST", "/social/write-consent", {"version": "cu"})
    assert r.status_code == 409 and r.json()["code"] == "SOCIAL_CONSENT_VERSION"
    r = await owner_api.send("POST", "/social/write-consent", body)
    assert r.status_code == 200 and r.json()["open"] is True
    consent = r.json()["consent"]
    assert consent["accepted_at"] and consent["accepted_by_name"] == "Anh Cơ" and consent["version"] == body["version"]
    assert (await _write(owner_api, acc_id)).status_code == 201
    # Rút lại: KHÔNG cần PIN, khoá lại ngay.
    await _clear_pin(owner_api)
    r = await owner_api.send("DELETE", "/social/write-consent")
    assert r.status_code == 200 and r.json()["open"] is False and r.json()["consent"] is None
    acts = await _db_text("SELECT action FROM ops.action_log WHERE action LIKE 'social.write_risk%'")
    assert "social.write_risk_accepted" in acts and "social.write_risk_revoked" in acts
    # Sandbox bật → mở, không cần đồng ý.
    await _heartbeat(redis, {"enabled": True, "mode": "on", "reason": None, "checked_at": "2026-10-03T01:00:00Z"})
    gate = (await owner_api.get("/social/write-gate")).json()
    assert gate["open"] is True and gate["consent"] is None and gate["sandbox"]["enabled"] is True
    # Đồng ý của phiên bản cảnh báo cũ không còn hiệu lực.
    await db.execute(text("UPDATE ops.risk_consents SET version = 'cu', revoked_at = NULL"))
    await db.commit()
    await _heartbeat(redis, None)
    assert (await owner_api.get("/social/write-gate")).json()["open"] is False


# ─── 7. đích + nội dung ────────────────────────────────────────────────────────

async def test_target_must_be_a_recently_read_item_and_text_validated(owner_api: Api, redis: Redis,
                                                                     db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    for url in ("https://www.facebook.com/permalink/999", "https://evil.example.com/permalink/123",
                "http://www.facebook.com/permalink/123", DM_URL):      # DM_URL là mục hộp thư, không phải bình luận
        r = await _write(owner_api, acc_id, target_url=url)
        assert r.status_code == 422 and "target_url" in r.json()["errors"], url
    assert (await _write(owner_api, acc_id, action="send_message", target_url=COMMENT_URL)).status_code == 422
    for bad in ("", "   \n ", "x" * 2001):
        r = await _write(owner_api, acc_id, text=bad)
        assert r.status_code == 422, bad
    r = await _write(owner_api, acc_id, text="  \x07 ")
    assert r.status_code == 422 and "text" in r.json()["errors"]
    assert (await owner_api.send("POST", f"/social/accounts/{acc_id}/write",
                                 _body(action="dang_bai"))).status_code == 422
    # Lượt đọc quá 7 ngày không còn tính.
    await db.execute(text("UPDATE agent.browser_jobs SET finished_at = now() - interval '8 days' WHERE kind = 'read'"))
    await db.commit()
    r = await _write(owner_api, acc_id)
    assert r.status_code == 422 and "target_url" in r.json()["errors"]
    assert len(await _write_jobs(redis)) == 0
    # Nhắn tin: đúng mục hộp thư thì được.
    await db.execute(text("UPDATE agent.browser_jobs SET finished_at = now() WHERE kind = 'read'"))
    await db.commit()
    r = await _write(owner_api, acc_id, action="send_message", target_url=DM_URL, text="Dạ còn hàng ạ")
    assert r.status_code == 201 and r.json()["action"] == "send_message"


# ─── 8. kết quả có ảnh chụp ────────────────────────────────────────────────────

async def test_done_with_proof_is_encrypted_served_and_logged_safely(owner_api: Api, redis: Redis, db: Any,
                                                                    caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    acc_id = await _ready(owner_api, redis)
    jid = (await _write(owner_api, acc_id)).json()["id"]
    job = (await _write_jobs(redis))[-1]
    await _deliver(redis, _result(job, "started"))
    assert await _deliver(redis, _write_done(job)) == "ok"
    await db.rollback()
    got = (await owner_api.get(f"/social/jobs/{jid}")).json()
    assert got["status"] == "done" and got["has_proof"] is True and "proof_key" not in got
    res = got["result"]
    assert res["sent"] is True and res["confirmed"] is True and res["text"] == REPLY
    assert res["target_url"] == COMMENT_URL
    assert res["trace"] == [{"step": "open", "ms": 1200, "ok": True}, {"step": "send", "ms": 3100, "ok": True}]
    assert "proof_error" not in res and "after_halt" not in res
    r = await owner_api.get(f"/social/jobs/{jid}/proof")
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg" and r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    # Trên đĩa chỉ có bản mã hoá (không chứa byte gốc).
    key = (await db.execute(text("SELECT proof_key FROM agent.browser_jobs WHERE id = :i"), {"i": jid})).scalar_one()
    blob = await get_object_store().get(key)
    assert JPEG not in blob and b"HINH-ANH-GIA-LAP" not in blob and blob[:3] == b"GH1"
    assert crypto.decrypt(blob, f"social_proof:{await org_id(db)}:{jid}".encode()) == JPEG
    # Lịch sử gửi + chuông + boss check.
    items = (await owner_api.get(f"/social/writes?account_id={acc_id}")).json()["items"]
    assert len(items) == 1 and items[0]["job_id"] == jid and items[0]["has_proof"] is True
    assert items[0]["text"] == REPLY and items[0]["confirmed"] is True and items[0]["after_halt"] is False
    assert items[0]["account_label"] == "Facebook của Sếp" and items[0]["status"] == "done"
    notes = (await owner_api.get("/notifications")).json()["items"]
    assert any(n["kind"] == "social.write" and n["title"].endswith("đã gửi trả lời") for n in notes)
    ov = (await owner_api.get("/boss-checks")).json()
    assert ov["results"]["facebook_reply"]["status"] == "pass"
    assert next(r_ for r_ in ov["rows"] if r_["key"] == "facebook_reply")["done"] is True
    assert ov["required_total"] == 1          # v0.1.55 (G2): chỉ dòng "nguồn AI" bắt buộc; dòng 8 tuỳ chọn
    # Action Log: có sha256, KHÔNG có nguyên văn / cookie / permit.
    async with admin_sessionmaker()() as adb:
        detail = (await adb.execute(text("SELECT detail FROM ops.action_log WHERE action = 'social.write'"))
                  ).scalar_one()
    assert detail["text_sha256"] == hashlib.sha256(REPLY.encode()).hexdigest()
    assert detail["proof_sha256"] == hashlib.sha256(JPEG).hexdigest()
    everything = await _db_text("SELECT * FROM ops.action_log") + caplog.text
    assert "Cảm ơn chị Lan" not in everything and "SIEU-BI-MAT" not in everything
    assert "SIEU-BI-MAT" not in await _db_text("SELECT * FROM agent.browser_jobs")
    assert job["payload"]["permit"]["sig"] not in everything
    # Ảnh hỏng / thiếu → vẫn ghi nhận đã gửi nhưng báo thiếu bằng chứng.
    await db.execute(text("UPDATE agent.browser_jobs SET created_at = now() - interval '2 days' WHERE id = :i"),
                     {"i": jid})
    await db.commit()
    assert (await _write(owner_api, acc_id)).status_code == 201
    job2 = (await _write_jobs(redis))[-1]
    data = _write_done(job2)
    data["data"]["proof"] = protocol.seal(crypto.browser_key(), b"khong-phai-jpeg", "sai:aad")
    assert await _deliver(redis, protocol.sign(crypto.browser_key(), protocol.P_RESULT, data)) == "ok"
    await db.rollback()
    got2 = (await owner_api.get(f"/social/jobs/{job2['id']}")).json()
    assert got2["status"] == "done" and got2["has_proof"] is False and got2["result"]["proof_error"] == "PROOF_MISSING"
    assert (await owner_api.get(f"/social/jobs/{job2['id']}/proof")).status_code == 404


async def test_proof_unreadable_after_key_change(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    jid = (await _write(owner_api, acc_id)).json()["id"]
    await _deliver(redis, _write_done((await _write_jobs(redis))[-1]))
    await db.rollback()
    key = (await db.execute(text("SELECT proof_key FROM agent.browser_jobs WHERE id = :i"), {"i": jid})).scalar_one()
    await get_object_store().put(key, b"GH1" + os.urandom(120))            # như đổi khoá / chuyển máy
    r = await owner_api.get(f"/social/jobs/{jid}/proof")
    assert r.status_code == 409 and r.json()["code"] == "SOCIAL_PROOF_UNREADABLE"


# ─── 9. lỗi của việc gửi không làm hỏng tài khoản ───────────────────────────────

async def test_write_errors_do_not_count_as_account_failures(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    await owner_api.send("PATCH", f"/social/accounts/{acc_id}", {"daily_write_limit": 20})
    for code in ("PERMIT_INVALID", "TARGET_NOT_FOUND", "BLOCKED_URL"):
        assert (await _write(owner_api, acc_id)).status_code == 201
        job = (await _write_jobs(redis))[-1]
        assert await _deliver(redis, _result(job, "failed", {"code": code})) == "ok"
        await db.rollback()
        row = (await db.execute(text("SELECT status, fail_streak FROM core.social_accounts WHERE id = :i"),
                                {"i": acc_id})).one()
        assert row.status == "active" and row.fail_streak == 0, code
        got = (await owner_api.get(f"/social/jobs/{job['id']}")).json()
        assert got["status"] == "failed" and got["error"] == code and got["error_text"]
        # Thất bại KHÔNG xoá đích + nội dung lưu lúc xếp việc (trang /social cần để hiện "Lần gửi gần đây").
        assert got["result"]["target_url"] == COMMENT_URL and got["result"]["text"] == REPLY, code
    writes = (await owner_api.get("/social/writes")).json()["items"]
    assert "Giấy phép gửi không hợp lệ" in str(writes) and "Hỏi Gen soạn lại" in str(writes)
    assert all(w["target_url"] == COMMENT_URL and w["text"] == REPLY for w in writes)
    # CHECKPOINT thì vẫn dừng tài khoản như cũ.
    assert (await _write(owner_api, acc_id)).status_code == 201
    await _deliver(redis, _result((await _write_jobs(redis))[-1], "failed", {"code": "CHECKPOINT"}))
    assert (await owner_api.get(f"/social/accounts/{acc_id}")).json()["status"] == "paused"


# ─── 10. gỡ tài khoản xoá ảnh chụp ─────────────────────────────────────────────

async def test_revoke_wipes_proof_images(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    jid = (await _write(owner_api, acc_id)).json()["id"]
    await _deliver(redis, _write_done((await _write_jobs(redis))[-1]))
    await db.rollback()
    key = (await db.execute(text("SELECT proof_key FROM agent.browser_jobs WHERE id = :i"), {"i": jid})).scalar_one()
    assert await get_object_store().get(key)
    await _pin(owner_api)
    assert (await owner_api.send("DELETE", f"/social/accounts/{acc_id}")).status_code == 204
    await db.rollback()
    row = (await db.execute(text("SELECT proof_key, proof_sha256, result FROM agent.browser_jobs WHERE id = :i"),
                            {"i": jid})).one()
    assert row.proof_key is None and row.proof_sha256 is None and row.result is None
    with pytest.raises(ObjectNotFound):
        await get_object_store().get(key)
    assert (await owner_api.get("/social/writes")).json()["items"] == []


# ─── Owner-only + hạn lưu ảnh chụp ──────────────────────────────────────────────

async def test_retention_drops_old_proofs(owner_api: Api, redis: Redis, db: Any) -> None:
    from gh import retention

    acc_id = await _ready(owner_api, redis)
    jid = (await _write(owner_api, acc_id)).json()["id"]
    await _deliver(redis, _write_done((await _write_jobs(redis))[-1]))
    await db.rollback()
    key = (await db.execute(text("SELECT proof_key FROM agent.browser_jobs WHERE id = :i"), {"i": jid})).scalar_one()
    assert await retention.purge_browser_proofs(db, asyncio.get_running_loop().time() + 60) == 0   # còn mới
    await db.execute(text("UPDATE agent.browser_jobs SET created_at = now() - interval '91 days' WHERE id = :i"),
                     {"i": jid})
    await db.commit()
    import time
    assert await retention.purge_browser_proofs(db, time.monotonic() + 60) == 1
    row = (await db.execute(text("SELECT proof_key, status FROM agent.browser_jobs WHERE id = :i"),
                            {"i": jid})).one()
    assert row.proof_key is None and row.status == "done"
    with pytest.raises(ObjectNotFound):
        await get_object_store().get(key)


# ─── review v0.1.47: đích bình luận, kết quả muộn, lỗi sau khi gửi, hạn lưu ──────────────────────────────────────

async def test_reply_to_notification_without_comment_id_is_refused(owner_api: Api, redis: Redis, db: Any) -> None:
    """Thông báo thích/sinh nhật/bài viết (không có comment_id) không phải đích trả lời — không xếp việc nào."""
    like_url = "https://www.facebook.com/photo/?fbid=42"
    acc = await _add(owner_api)
    acc_id = str(acc["id"])
    await _login(owner_api, redis, acc_id)
    await _read_done(owner_api, redis, acc_id, [*READ_ITEMS, {
        "kind": "notification", "who": None, "text": "Minh đã thích ảnh của bạn", "time": "1 giờ", "unread": True,
        "link": like_url}])
    await _heartbeat(redis, {"enabled": True, "mode": "auto", "reason": None, "checked_at": "2026-10-03T01:00:00Z"})
    await _pin(owner_api)
    r = await _write(owner_api, acc_id, target_url=like_url)
    assert r.status_code == 422 and "bình luận" in r.json()["errors"]["target_url"], r.text
    assert len(await _write_jobs(redis)) == 0
    assert (await _write(owner_api, acc_id)).status_code == 201          # thông báo có comment_id vẫn trả lời được


@pytest.mark.parametrize("closer", ["pause", "timeout"])
async def test_late_done_after_pause_or_timeout_is_logged_and_counted(owner_api: Api, redis: Redis, db: Any,
                                                                      closer: str) -> None:
    acc_id = await _ready(owner_api, redis)
    assert (await _write(owner_api, acc_id)).status_code == 201
    job = (await _write_jobs(redis))[-1]
    await _deliver(redis, _result(job, "started"))
    if closer == "pause":
        assert (await owner_api.send("POST", f"/social/accounts/{acc_id}/pause", {})).status_code == 200
        assert await redis.exists(protocol.CANCELLED_PREFIX + job["id"])      # worker kiểm trước khi gửi
        want = "cancelled"
    else:
        await db.execute(text("""UPDATE agent.browser_jobs SET status = 'failed', error = 'WORKER_TIMEOUT',
                                        finished_at = now() WHERE id = :i"""), {"i": job["id"]})
        await db.commit()
        want = "failed"
        # Đã chạy rồi mất liên lạc: tin CÓ THỂ đã đi — không bảo "thử lại" (Owner gửi lại thành hai lần).
        got = (await owner_api.get(f"/social/jobs/{job['id']}")).json()
        assert got["error_text"] == service.WRITE_UNKNOWN_TEXT and "thử lại" not in got["error_text"]
        item = (await owner_api.get("/social/writes")).json()["items"][0]
        assert item["error_text"] == service.WRITE_UNKNOWN_TEXT and item["started_at"]
    await db.rollback()
    assert (await db.execute(text("SELECT status FROM agent.browser_jobs WHERE id = :i"),
                             {"i": job["id"]})).scalar_one() == want
    assert await _deliver(redis, _write_done(job)) == "ok"          # tin ĐÃ đi: phải ghi nhận
    await db.rollback()
    got = (await owner_api.get(f"/social/jobs/{job['id']}")).json()
    assert got["status"] == "done" and got["error"] is None and got["has_proof"] is True
    assert got["result"]["after_cancel"] is True and got["result"]["text"] == REPLY
    async with admin_sessionmaker()() as adb:
        detail = (await adb.execute(text("SELECT detail FROM ops.action_log WHERE action = 'social.write'"))
                  ).scalar_one()
    assert detail["after_cancel"] is True and detail["sent"] is True
    assert (await owner_api.get(f"/social/accounts/{acc_id}")).json()["writes_today"] == 1
    item = (await owner_api.get("/social/writes")).json()["items"][0]
    assert item["after_cancel"] is True and item["status"] == "done"


async def test_late_done_after_revoke_logs_without_storing(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    assert (await _write(owner_api, acc_id)).status_code == 201
    job = (await _write_jobs(redis))[-1]
    await _deliver(redis, _result(job, "started"))
    await _pin(owner_api)
    assert (await owner_api.send("DELETE", f"/social/accounts/{acc_id}")).status_code == 204
    assert await _deliver(redis, _write_done(job)) == "ok"
    await db.rollback()
    row = (await db.execute(text("SELECT status, proof_key, result FROM agent.browser_jobs WHERE id = :i"),
                            {"i": job["id"]})).one()
    assert row.status == "done" and row.proof_key is None and row.result is None       # không lưu lại gì đã xoá
    assert (await db.execute(text("SELECT state_enc FROM core.social_accounts WHERE id = :i"),
                             {"i": acc_id})).scalar_one() is None
    async with admin_sessionmaker()() as adb:
        detail = (await adb.execute(text("SELECT detail FROM ops.action_log WHERE action = 'social.write'"))
                  ).scalar_one()
    assert detail["account_revoked"] is True and detail["sent"] is True
    # thẻ đề xuất đã dừng ở "Đã huỷ" → phải có chuông báo tin THỰC SỰ đã đi
    notes = (await owner_api.get("/notifications")).json()["items"]
    assert any(n["kind"] == "social.write" and "trước khi kịp gỡ" in n["title"] for n in notes), notes


async def test_send_error_after_submit_is_done_with_warning(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    assert (await _write(owner_api, acc_id)).status_code == 201
    job = (await _write_jobs(redis))[-1]
    res = _write_done(job, confirmed=False)
    res["data"]["send_error"] = True
    assert await _deliver(redis, protocol.sign(crypto.browser_key(), protocol.P_RESULT, res)) == "ok"
    await db.rollback()
    got = (await owner_api.get(f"/social/jobs/{job['id']}")).json()
    assert got["status"] == "done" and got["result"]["send_error"] is True and got["result"]["confirmed"] is False
    assert (await owner_api.get("/social/writes")).json()["items"][0]["send_error"] is True
    notes = (await owner_api.get("/notifications")).json()["items"]
    assert any(n["kind"] == "social.write" and "trước khi gửi lại" in (n["body"] or "") for n in notes)


async def test_writes_list_survives_purged_result(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis)
    jid = (await _write(owner_api, acc_id)).json()["id"]
    await _deliver(redis, _write_done((await _write_jobs(redis))[-1]))
    await db.execute(text("UPDATE agent.browser_jobs SET result = NULL WHERE id = :i"), {"i": jid})   # như hạn lưu
    await db.commit()
    r = await owner_api.get("/social/writes")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["target_url"] is None and item["text"] is None and item["status"] == "done"


async def test_withdrawing_consent_cancels_queued_writes(owner_api: Api, redis: Redis, db: Any) -> None:
    acc_id = await _ready(owner_api, redis, gate=False)
    await _heartbeat(redis, {"enabled": False, "mode": "auto", "reason": None, "checked_at": None})
    r = await owner_api.send("POST", "/social/write-consent", {"version": platforms.WRITE_RISK_VERSION})
    assert r.status_code == 200 and r.json()["open"] is True
    assert (await _write(owner_api, acc_id)).status_code == 201
    job = (await _write_jobs(redis))[-1]
    assert (await owner_api.send("DELETE", "/social/write-consent")).json()["open"] is False
    await db.rollback()
    assert (await db.execute(text("SELECT status FROM agent.browser_jobs WHERE id = :i"),
                             {"i": job["id"]})).scalar_one() == "cancelled"
    async with admin_sessionmaker()() as adb:
        detail = (await adb.execute(text("""SELECT detail FROM ops.action_log
                                            WHERE action = 'social.write_risk_revoked'"""))).scalar_one()
    assert detail["writes_cancelled"] == 1
    # Việc còn trong hàng đợi (worker bận) không nhận được pub/sub `cancel` → api phải đặt khoá huỷ để worker nhận
    # việc sau đó KHÔNG gửi (permit còn hạn tới 5 phút). TTL ≥ hạn việc trong hàng đợi.
    from gh.social.service import JOB_TTL_S
    assert await redis.get(protocol.CANCELLED_PREFIX + job["id"]) == b"1"
    assert await redis.ttl(protocol.CANCELLED_PREFIX + job["id"]) >= JOB_TTL_S


async def test_worker_timeout_before_start_is_safe_to_retry(owner_api: Api, redis: Redis, db: Any) -> None:
    """Việc GỬI chưa từng chạy (không có 'started') rồi quá giờ: chắc chắn chưa gửi — giữ câu "thử lại" thường."""
    acc_id = await _ready(owner_api, redis)
    jid = (await _write(owner_api, acc_id)).json()["id"]
    await db.execute(text("""UPDATE agent.browser_jobs SET status = 'failed', error = 'WORKER_TIMEOUT',
                                    finished_at = now() WHERE id = :i"""), {"i": jid})
    await db.commit()
    got = (await owner_api.get(f"/social/jobs/{jid}")).json()
    assert got["started_at"] is None and got["error_text"] == service.ERROR_TEXT["WORKER_TIMEOUT"]
    item = (await owner_api.get("/social/writes")).json()["items"][0]
    assert item["started_at"] is None and item["error_text"] == service.ERROR_TEXT["WORKER_TIMEOUT"]


async def test_writes_list_proof_error_not_inferred_from_purged_proof(owner_api: Api, redis: Redis, db: Any) -> None:
    """has_proof=false có hai nghĩa: không chụp được (proof_error PROOF_MISSING) hoặc ảnh đã xoá theo hạn lưu 90 ngày
    (proof_error null) — web chỉ cảnh báo "không chụp được ảnh" ở trường hợp đầu."""
    acc_id = await _ready(owner_api, redis)
    j1 = (await _write(owner_api, acc_id)).json()["id"]
    await _deliver(redis, _write_done((await _write_jobs(redis))[-1], proof=False))
    await _pin(owner_api)
    j2 = (await _write(owner_api, acc_id)).json()["id"]
    await _deliver(redis, _write_done((await _write_jobs(redis))[-1]))
    await db.execute(text("UPDATE agent.browser_jobs SET proof_key = NULL, proof_sha256 = NULL WHERE id = :i"),
                     {"i": j2})                                     # như retention.purge_browser_proofs
    await db.commit()
    items = {i["job_id"]: i for i in (await owner_api.get("/social/writes")).json()["items"]}
    assert items[j1]["has_proof"] is False and items[j1]["proof_error"] == "PROOF_MISSING"
    assert items[j2]["has_proof"] is False and items[j2]["proof_error"] is None
