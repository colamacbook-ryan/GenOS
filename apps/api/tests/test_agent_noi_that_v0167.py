"""v0.1.67 gói C5 — agent nói thật: lỗi model đúng loại (F011), "Gợi ý" đọc được (F075), nhãn quyết định đúng với
nháp (F076), "Soạn lại" theo giọng agent (F081)."""

import uuid
from typing import Any

import orjson
from sqlalchemy import text

from tests.conftest import OWNER, Api
from tests.phase2 import FakeRouter, org_id
from tests.test_rbac_api import PIN, login_as

DRAFT = {"kind": "message", "title": "Trả lời chị Lan", "text": "Dạ em báo giá 3 container thép cuộn ạ."}


async def _agent(db, org, name: str = "Trợ lý thương mại") -> uuid.UUID:  # type: ignore[no-untyped-def]
    aid = (await db.execute(text("""
        INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when, forbidden,
                                      autonomy_level, is_enabled)
        VALUES (:o, :n, 'Báo giá, follow khách', 'commercial', '{"self": "em", "customer": "anh/chị"}'::jsonb,
                'lễ phép, ngắn gọn', 'khi khách hỏi giá', ARRAY['không tự cam kết giá', 'không hứa đền bù'], 4, true)
        RETURNING id"""), {"o": org, "n": name})).scalar_one()
    await db.commit()
    return aid  # type: ignore[no-any-return]


# ─── F011: hết hạn mức / mạng ≠ chưa có model ────────────────────────────────────────────────────────────────

async def test_model_down_is_not_reported_as_no_model(owner_api: Api, app) -> None:  # type: ignore[no-untyped-def]
    d = (await owner_api.send("POST", "/drafts", DRAFT)).json()
    for path, payload in ((f"/drafts/{d['id']}/translate", {"lang": "en"}), (f"/drafts/{d['id']}/regenerate", {})):
        app.state.model_router = FakeRouter(down=True, no_chain=False)        # có model, lượt gọi lỗi
        r = await owner_api.send("POST", path, payload)
        j = r.json()
        assert r.status_code == 503 and j["code"] == "MODEL_UNAVAILABLE" and j["model_down"] is True, (path, j)
        assert "hết hạn mức" in j["title"] and "Chưa có model" not in j["title"]
        assert "Chưa có model AI nào hoạt động" not in (j["detail"] or "") and isinstance(j["detail"], str)
        assert j["reasons"]                                                   # lý do kỹ thuật vẫn cho Owner
        for chain in (True, None):                                            # chưa có model nào: câu cũ, đúng
            app.state.model_router = FakeRouter(down=True, no_chain=chain)
            j = (await owner_api.send("POST", path, payload)).json()
            assert j["model_down"] is False and "Chưa có model nào chạy được" in j["title"], (path, chain, j)
            assert "Chưa có model AI nào hoạt động" in j["detail"]


# ─── F081: Soạn lại theo giọng + điều cấm của agent, từ chối đầu ra hỏng ─────────────────────────────────────

async def test_regenerate_uses_agent_voice_forbidden_and_instruction(owner_api: Api, db, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    aid = await _agent(db, org)
    d = (await owner_api.send("POST", "/drafts", DRAFT)).json()
    await db.execute(text("UPDATE biz.action_drafts SET agent_id = :a WHERE id = :i"), {"a": aid, "i": d["id"]})
    await db.commit()
    router = FakeRouter(lambda m: "Dạ em gửi anh báo giá nhẹ nhàng hơn ạ.")
    app.state.model_router = router
    r = await owner_api.send("POST", f"/drafts/{d['id']}/regenerate", {"instruction": "mềm hơn, ngắn hơn"})
    assert r.status_code == 200, r.text
    system = router.calls[0][0].content
    user = router.calls[0][1].content
    assert "lễ phép, ngắn gọn" in system and "không tự cam kết giá" in system and "không hứa đền bù" in system
    assert "Trợ lý thương mại" in system and "em" in system
    assert "mềm hơn, ngắn hơn" in user and DRAFT["text"] in user
    assert r.json()["text"] == "Dạ em gửi anh báo giá nhẹ nhàng hơn ạ."
    assert [v["text"] for v in r.json()["versions"]] == [DRAFT["text"]]


async def test_regenerate_rejects_empty_or_invented_output_and_keeps_draft(owner_api: Api, db, app) -> None:  # type: ignore[no-untyped-def]
    d = (await owner_api.send("POST", "/drafts", DRAFT)).json()
    for bad in ("   ", "Dạ theo hợp đồng OPP-9999 em báo giá ạ."):        # rỗng; mã không có trong nguồn
        app.state.model_router = FakeRouter(lambda m, bad=bad: bad)
        r = await owner_api.send("POST", f"/drafts/{d['id']}/regenerate", {})
        assert r.status_code == 502 and r.json()["code"] == "MODEL_BAD_OUTPUT", r.text
        assert isinstance(r.json()["detail"], str)
        cur = (await owner_api.get(f"/drafts/{d['id']}")).json()
        assert cur["text"] == DRAFT["text"] and cur["versions"] == []      # nháp nguyên vẹn, không có bản mới
    # mã có sẵn trong bản nháp thì được phép nhắc lại
    own = {**DRAFT, "text": "Dạ theo hợp đồng OPP-1842 em báo giá ạ."}
    keep = (await owner_api.send("POST", "/drafts", own)).json()
    app.state.model_router = FakeRouter(lambda m: "Theo OPP-1842, em gửi báo giá ạ.")
    assert (await owner_api.send("POST", f"/drafts/{keep['id']}/regenerate", {})).status_code == 200


# ─── F075 + F076: /agents/decisions trả outcome + proposal_text (che) + trạng thái nháp ─────────────────────

async def _decision(db, org, aid, decision: str, outcome: str, *, proposal: dict[str, Any] | None = None,  # type: ignore[no-untyped-def]
                    draft: str | None = None) -> None:
    await db.execute(text("""
        INSERT INTO agent.decisions (org_id, agent_id, trigger_ref, decision, outcome, proposal, draft_id)
        VALUES (:o, :a, '{"type":"meaning_unit","id":"x"}'::jsonb, :d, :oc, CAST(:p AS jsonb), :dr)"""),
                     {"o": org, "a": aid, "d": decision, "oc": outcome, "dr": draft,
                      "p": orjson.dumps(proposal).decode() if proposal else None})
    await db.commit()


async def test_decisions_expose_outcome_proposal_text_and_draft_status(owner_api: Api, db, client) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    aid = await _agent(db, org)
    pending = (await owner_api.send("POST", "/drafts", DRAFT)).json()
    sent = (await owner_api.send("POST", "/drafts", {**DRAFT, "title": "Đã gửi"})).json()
    await db.execute(text("UPDATE biz.action_drafts SET status = 'sent' WHERE id = :i"), {"i": sent["id"]})
    await db.commit()
    secret = "Gợi ý: gọi khách theo số 0912345678 và báo giá 3 container."
    await _decision(db, org, aid, "suggest", "suggested", proposal={"text": secret})
    await _decision(db, org, aid, "send", "held", proposal={"text": DRAFT["text"]}, draft=pending["id"])
    await _decision(db, org, aid, "send", "held", proposal={"text": DRAFT["text"]}, draft=sent["id"])
    await _decision(db, org, aid, "silent", "none")

    items = (await owner_api.get("/agents/decisions")).json()["items"]
    by = {(i["decision"], (i["draft"] or {}).get("status")): i for i in items}
    assert by[("suggest", None)]["outcome"] == "suggested" and by[("suggest", None)]["proposal_text"] == secret
    assert by[("send", "pending")]["outcome"] == "held" and by[("send", "pending")]["draft"]["status"] == "pending"
    assert by[("send", "sent")]["draft"]["status"] == "sent"
    assert by[("silent", None)]["proposal_text"] is None
    auditor = await login_as(client, db, "auditor")                         # vai dưới Owner: số điện thoại bị che
    masked = next(i for i in (await auditor.get("/agents/decisions")).json()["items"] if i["decision"] == "suggest")
    assert "0912345678" not in masked["proposal_text"] and masked["proposal_text"].endswith("báo giá 3 container.")
    assert "678" in masked["proposal_text"]


async def test_decisions_hide_proposal_text_of_drafts_outside_scope(owner_api: Api, db, client) -> None:  # type: ignore[no-untyped-def]
    """Review v0.1.67 P2-1 — Quản lý (action.approve mức TEAM) không đọc toàn văn nháp ngoài phạm vi của mình."""
    org = await org_id(db)
    aid = await _agent(db, org)
    theirs = (await owner_api.send("POST", "/drafts", {**DRAFT, "title": "Nháp của Sếp"})).json()   # created_by = Owner
    manager = await login_as(client, db, "manager")
    mine = (await manager.send("POST", "/drafts", {**DRAFT, "title": "Nháp của Quản lý"})).json()
    await _decision(db, org, aid, "send", "held", proposal={"text": "TOÀN VĂN NHÁP CỦA SẾP"}, draft=theirs["id"])
    await _decision(db, org, aid, "send", "held", proposal={"text": "TOÀN VĂN NHÁP CỦA QUẢN LÝ"}, draft=mine["id"])
    await _decision(db, org, aid, "suggest", "suggested", proposal={"text": "Gợi ý chung, không gắn nháp"})

    items = (await manager.get("/agents/decisions")).json()["items"]
    text_by = {(i["draft"] or {}).get("id"): i["proposal_text"] for i in items}
    assert text_by[mine["id"]] == "TOÀN VĂN NHÁP CỦA QUẢN LÝ"
    assert text_by[theirs["id"]] is None                                   # ngoài phạm vi ⇒ không lộ toàn văn
    assert text_by[None] == "Gợi ý chung, không gắn nháp"
    owner_items = (await owner_api.get("/agents/decisions")).json()["items"]
    owner_by = {(i["draft"] or {}).get("id"): i["proposal_text"] for i in owner_items}
    assert owner_by[theirs["id"]] == "TOÀN VĂN NHÁP CỦA SẾP"


async def test_non_owner_cannot_send_text_with_masked_numbers(owner_api: Api, db, client) -> None:  # type: ignore[no-untyped-def]
    """Review v0.1.67 P3: vai dưới Owner thấy số ≥ 8 chữ số bị che (•••••678) — "Khôi phục bản trước" / "Sửa rồi gửi" /
    dùng bản dịch đưa chuỗi đã che vào ô sửa; máy chủ không được gửi đi chuỗi che đó cho khách."""
    manager = await login_as(client, db, "manager")
    d = (await manager.send("POST", "/drafts", {**DRAFT, "text": "Dạ chị chuyển khoản vào STK 0912345678 ạ."})).json()
    shown = (await manager.get(f"/drafts/{d['id']}")).json()["text"]
    assert "0912345678" not in shown and "•••" in shown
    pin = await manager.send("POST", "/auth/pin/verify", {"pin": PIN})
    assert pin.status_code == 200, pin.text
    r = await manager.send("POST", f"/drafts/{d['id']}/edit-send", {"text": shown})
    assert r.status_code == 422 and r.json()["code"] == "MASKED_TEXT", r.text
    assert (await manager.get(f"/drafts/{d['id']}")).json()["status"] == "pending"       # chưa quyết gì
    r = await manager.send("POST", f"/drafts/{d['id']}/edit-send", {"text": "Dạ em báo giá lại cho chị ạ."})
    assert r.status_code == 200, r.text
    # Owner thấy nguyên văn nên không bị chặn
    d2 = (await owner_api.send("POST", "/drafts", {**DRAFT, "text": "STK •••••678 là ký hiệu của em"})).json()
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    ok = await owner_api.send("POST", f"/drafts/{d2['id']}/edit-send", {"text": "STK •••••678 là ký hiệu của em"})
    assert ok.status_code == 200, ok.text
