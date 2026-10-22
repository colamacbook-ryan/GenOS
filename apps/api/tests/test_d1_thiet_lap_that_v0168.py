"""v0.1.68 (gói D1) — thiết lập nói thật: F003, F026, F038, F044, F079 (+ định nghĩa "xong" của việc 13/14, phần C2
giao D1).

- F003: bước 4 KHÔNG tự bật lại nguồn Sếp đã tắt; sau Hoàn tất mà ĐỔI hạng chuỗi thì cần phiên PIN `ai.route_change`.
- F026: "Để sau" bước 4 (đã có model thật) không chặn bước 8 / 9.
- F038: "Mời người trong đội" chỉ xong khi có người mới thật đang hoạt động (cùng định nghĩa `staff.active` của Gen).
- F044: bước 8 làm lại thì CẬP NHẬT agent của bước 8, không tạo thêm agent trùng.
- F079: agent bước 8 nghe sẵn mọi kênh đã nối.
- 13 Facebook: `needs_login` không tính là xong. 14 Gen-hub: theo bản kiểm `hub` mới nhất (cùng "Việc Sếp cần làm")."""

import json
from typing import Any

from sqlalchemy import text

from gh.boss_checks import service as boss
from tests.conftest import Api, verify_pin
from tests.phase2 import org_id
from tests.test_ux_v0128 import _provider


async def _followup(api: Api) -> dict[int, dict[str, Any]]:
    return {i["n"]: i for i in (await api.get("/setup/follow-up")).json()}


async def _finish(api: Api) -> None:
    """Bấm "Để sau" bước 4 rồi Hoàn tất (như test_pin_barriers_v0145) ⇒ sau Hoàn tất, bước 4 ghi 'skipped'."""
    assert (await api.send("POST", "/setup/steps/4/skip")).status_code == 200
    r = await api.send("PUT", "/setup/steps/12")
    assert r.status_code == 200 and r.json()["finished"] is True, r.text


async def _drop_pin(db: Any) -> None:
    await db.execute(text("UPDATE core.sessions SET pin_verified_until = NULL"))
    await db.commit()


async def _enabled(db: Any, pid: str) -> bool:
    await db.rollback()
    row = await db.execute(text("SELECT is_enabled FROM agent.providers WHERE id = :i"), {"i": pid})
    return bool(row.scalar_one())


async def _ranks(db: Any) -> dict[str, int]:
    await db.rollback()
    rows = (await db.execute(text("SELECT name, failover_rank FROM agent.providers"))).all()
    return {r.name: r.failover_rank for r in rows}


# ─── F003 ───────────────────────────────────────────────────────────────────

async def test_step4_does_not_reenable_a_source_the_boss_turned_off(owner_api: Api, db: Any) -> None:
    api = owner_api
    alpha = await _provider(api, db, "alpha", ok=True, tested=["m-a"])
    beta = await _provider(api, db, "beta", ok=True, tested=["m-b"])
    assert (await api.send("PATCH", f"/providers/{beta}", {"enabled": False})).status_code == 200
    await _finish(api)
    await _drop_pin(db)
    before = await _ranks(db)
    # Gửi nguyên thứ tự hiện có (cả nguồn đã tắt): không đổi gì ⇒ không đòi PIN, nguồn tắt vẫn tắt, hạng giữ nguyên.
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [alpha, beta]})
    assert r.status_code == 200, r.text
    assert await _enabled(db, beta) is False and await _enabled(db, alpha) is True
    assert await _ranks(db) == before


async def test_step4_after_finish_changing_order_needs_pin(owner_api: Api, db: Any) -> None:
    api = owner_api
    alpha = await _provider(api, db, "alpha", ok=True, tested=["m-a"])
    beta = await _provider(api, db, "beta", ok=True, tested=["m-b"])
    await _finish(api)
    await _drop_pin(db)
    before = await _ranks(db)
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [beta, alpha]})        # đổi hạng, chưa có PIN
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED", r.text
    assert await _ranks(db) == before
    await verify_pin(api)
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [beta, alpha]})
    assert r.status_code == 200, r.text
    ranks = await _ranks(db)
    assert ranks["beta"] < ranks["alpha"]


async def test_step4_disabled_source_is_not_ready(owner_api: Api, db: Any) -> None:
    api = owner_api
    solo = await _provider(api, db, "solo", ok=True, tested=["m-s"])
    assert (await api.send("PATCH", f"/providers/{solo}", {"enabled": False})).status_code == 200
    await _finish(api)
    await _drop_pin(db)
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [solo]})
    assert r.status_code == 409 and r.json()["code"] == "STEP_INCOMPLETE", r.text
    assert await _enabled(db, solo) is False


async def test_step4_all_sources_off_409_says_disabled_not_untested(owner_api: Api, db: Any) -> None:
    """Review v0.1.68 P2-1: mọi nguồn TẮT ⇒ câu 409 nói đúng "đã tắt" + chỉ chỗ bật (API & Model)."""
    api = owner_api
    solo = await _provider(api, db, "solo", ok=True, tested=["m-s"])
    assert (await api.send("PATCH", f"/providers/{solo}", {"enabled": False})).status_code == 200
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [solo]})
    assert r.status_code == 409 and r.json()["code"] == "STEP_INCOMPLETE", r.text
    msg = r.json()["title"]
    assert "đã tắt" in msg and "API & Model" in msg, msg
    # Còn nguồn bật nhưng CHƯA kiểm tra (nguồn tắt gửi kèm): câu cũ vẫn đúng — không nhắc nhầm "đã tắt".
    fresh = await _provider(api, db, "fresh", ok=False, tested=[])
    r = await api.send("PUT", "/setup/steps/4", {"provider_ids": [fresh]})
    assert r.status_code == 409, r.text
    assert "đã tắt" not in r.json()["title"], r.json()["title"]


# ─── F026 ───────────────────────────────────────────────────────────────────

async def test_skip_step4_does_not_block_step8_and_step9(owner_api: Api, db: Any) -> None:
    api = owner_api
    await _provider(api, db, "tốt", ok=True, tested=["m-t"])
    assert (await api.send("POST", "/setup/steps/4/skip")).status_code == 200      # "Để sau" bước 4 ⇒ 'skipped'
    assert (await api.send("PUT", "/setup/steps/8", {"template": "secretary"})).status_code == 200
    r = await api.send("PUT", "/setup/steps/9", {"ack_boundaries": True})
    assert r.status_code == 200, r.text


# ─── F038 ───────────────────────────────────────────────────────────────────

async def test_invite_team_done_only_with_a_real_active_member(owner_api: Api, db: Any) -> None:
    api = owner_api
    r = await api.send("PUT", "/setup/steps/10", {"invites": []})          # danh sách rỗng KHÔNG đánh dấu xong
    assert r.status_code == 409 and r.json()["code"] == "STEP_INCOMPLETE", r.text
    assert (await _followup(api))[10]["done"] is False
    await verify_pin(api)
    inv = {"display_name": "Lan", "email": "lan@example.vn", "role": "operator"}
    assert (await api.send("PUT", "/setup/steps/10", {"invites": [inv]})).status_code == 200
    assert (await _followup(api))[10]["done"] is True
    # Người đã khoá không tính (giống `staff.active` của Gen).
    await db.execute(text("UPDATE core.users SET is_active = false WHERE email = 'lan@example.vn'"))
    await db.commit()
    assert (await _followup(api))[10]["done"] is False
    # Bấm "Để sau" rồi có người dùng thật ⇒ vẫn theo dữ liệu thật; "Để sau" một mình không tính xong.
    assert (await api.send("POST", "/setup/steps/10/skip")).status_code == 200
    assert (await _followup(api))[10]["done"] is False


# ─── F044 ───────────────────────────────────────────────────────────────────

async def test_step8_again_updates_the_setup_agent_instead_of_creating_a_duplicate(owner_api: Api, db: Any) -> None:
    api = owner_api
    r = await api.send("PUT", "/setup/steps/8", {"name": "Mai", "role_desc": "Chăm khách", "template": "secretary"})
    assert r.status_code == 200, r.text
    first = r.json()["agent"]["id"]
    r = await api.send("PUT", "/setup/steps/8", {"name": "Mai 2", "role_desc": "Chăm khách VIP"})
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["id"] == first
    await db.rollback()
    rows = (await db.execute(text("SELECT id, name, role_desc FROM agent.identities"))).all()
    assert len(rows) == 1 and str(rows[0].id) == first and rows[0].name == "Mai 2"
    # Agent đã bị xoá ⇒ lần sau tạo mới (không treo vào id cũ).
    await db.execute(text("DELETE FROM agent.identities"))
    await db.commit()
    r = await api.send("PUT", "/setup/steps/8", {"name": "Mới", "role_desc": "Làm lại"})
    assert r.status_code == 200 and r.json()["agent"]["id"] != first


# ─── F079 ───────────────────────────────────────────────────────────────────

async def _active_channel(db: Any, org: Any, ctype: str) -> Any:
    cid = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                            {"o": org, "t": ctype})).scalar_one()
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at)
                             VALUES (:c, :o, 'acc', 'active', now())"""), {"c": cid, "o": org})
    await db.commit()
    return cid


async def test_step8_agent_listens_to_every_connected_channel(owner_api: Api, db: Any) -> None:
    api = owner_api
    org = await org_id(db)
    zalo = await _active_channel(db, org, "zalo")
    r = await api.send("PUT", "/setup/steps/8", {"template": "secretary"})
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["listening_channels"] == 1
    await db.rollback()
    scopes = (await db.execute(text("SELECT channel_id, group_id FROM agent.channel_scopes"))).all()
    assert [(s.channel_id, s.group_id) for s in scopes] == [(zalo, None)]


async def test_step8_without_connected_channel_has_no_scope_and_says_so(owner_api: Api, db: Any) -> None:
    r = await owner_api.send("PUT", "/setup/steps/8", {"template": "secretary"})
    assert r.status_code == 200, r.text
    assert r.json()["agent"]["listening_channels"] == 0
    await db.rollback()
    assert (await db.execute(text("SELECT count(*) FROM agent.channel_scopes"))).scalar_one() == 0


# ─── việc 13 / 14 ───────────────────────────────────────────────────────────

async def test_followup_facebook_needs_login_is_not_done(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    await db.execute(text("""INSERT INTO core.social_accounts (org_id, platform, label, status)
                             VALUES (:o, 'facebook_personal', 'FB', 'needs_login')"""), {"o": org})
    await db.commit()
    assert (await _followup(owner_api))[13]["done"] is False
    await db.execute(text("UPDATE core.social_accounts SET status = 'active'"))
    await db.commit()
    assert (await _followup(owner_api))[13]["done"] is True


async def test_followup_hub_follows_the_latest_boss_check(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    await db.execute(text("INSERT INTO agent.hub_links (org_id, last_ok_at) VALUES (:o, now())"), {"o": org})
    await db.commit()
    # chưa có bản kiểm nào: theo last_ok_at như cũ
    assert (await _followup(owner_api))[14]["done"] is True
    await boss.record(db, org, "hub", "fail", error_code="HUB_TOKEN_REJECTED", message="401")
    await db.commit()
    # bản kiểm mới nhất lỗi ⇒ khớp "Việc Sếp cần làm"
    assert (await _followup(owner_api))[14]["done"] is False
    await boss.record(db, org, "hub", "pass", detail={"exposed_tools": 5})
    await db.commit()
    assert (await _followup(owner_api))[14]["done"] is True
    json.dumps(await _followup(owner_api))
