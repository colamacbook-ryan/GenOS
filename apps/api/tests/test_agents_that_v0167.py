"""v0.1.67 gói B7 — Danh tính Agent làm và nói thật (F016 F022 F082 F083 F085 F089 F091 F142).

Mỗi test đỏ trên mã v0.1.62 (xem lịch sử commit): nguồn tắt không cảnh báo, thẻ agent không nói model, "lần lên tiếng"
nhìn 8 quyết định chung, không có Lưu trữ agent / trùng tên, tắt agent đòi PIN, nhân bản rơi model riêng, mẫu còn tên
dự án cũ."""

import uuid

from sqlalchemy import text

from tests.conftest import Api
from tests.test_p4_agents import AGENT_BODY, _channel_group, _pin, _seed_agent, _seed_provider_model


async def _decision(db, org, aid, decision: str, *, minutes_ago: int = 1) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""
        INSERT INTO agent.decisions (org_id, agent_id, trigger_ref, decision, outcome, at)
        VALUES (:o, :a, '{"type":"meaning_unit","id":"x"}'::jsonb, :d, 'none', now() - make_interval(mins => :m))"""),
                     {"o": org, "a": aid, "d": decision, "m": minutes_ago})
    await db.commit()


# ─── F016: nguồn đã tắt ⇒ dòng gán báo thẳng ─────────────────────────────────────────────────────────────────

async def test_binding_reports_when_source_or_model_disabled(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    aid = await _seed_agent(db, org)
    api: Api = owner_api
    pid, mid = await _seed_provider_model(api, db)
    assert (await api.send("PUT", f"/agents/bindings/agent:{aid}", {"model_id": mid})).status_code == 200

    def item(listing: dict) -> dict:  # type: ignore[type-arg]
        return next(i for i in listing["items"] if i["agent_key"] == f"agent:{aid}")

    ok = item((await api.get("/agents/bindings")).json())
    assert ok["binding"]["inactive_reason"] is None

    await db.execute(text("UPDATE agent.models SET is_enabled = false WHERE id = :m"), {"m": mid})
    await db.commit()
    off_model = item((await api.get("/agents/bindings")).json())
    assert "đã tắt" in off_model["binding"]["inactive_reason"] and "Chuẩn" in off_model["binding"]["inactive_reason"]

    await db.execute(text("UPDATE agent.models SET is_enabled = true WHERE id = :m"), {"m": mid})
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE id = :p"), {"p": pid})
    await db.commit()
    off_src = item((await api.get("/agents/bindings")).json())
    assert off_src["binding"]["inactive_reason"].startswith("Nguồn đã tắt")
    # thẻ agent cũng nói thật
    card = next(a for a in (await api.get("/agents")).json() if a["id"] == str(aid))
    assert card["binding"]["inactive_reason"].startswith("Nguồn đã tắt")
    assert card["model"]["ok"] is False and "Nguồn đã tắt" in card["model"]["label"]


# ─── F022: thẻ agent cho biết dùng model nào / có chạy được không ─────────────────────────────────────────────

async def test_agent_card_says_which_model(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    aid = await _seed_agent(db, org)
    api: Api = owner_api
    card = next(a for a in (await api.get("/agents")).json() if a["id"] == str(aid))
    assert card["model"]["ok"] is False and card["model"]["custom"] is False
    assert card["model"]["label"].startswith("Chuẩn: ")
    _pid, mid = await _seed_provider_model(api, db)
    card = next(a for a in (await api.get("/agents")).json() if a["id"] == str(aid))
    assert card["model"]["ok"] is True and card["model"]["label"].startswith("Chuẩn: ")
    assert (await api.send("PUT", f"/agents/bindings/agent:{aid}", {"model_id": mid})).status_code == 200
    card = (await api.get(f"/agents/{aid}")).json()
    assert card["model"] == {"custom": True, "ok": True, "label": "gemini-2.5-flash · Gemini test"}


# ─── F083: lần lên tiếng gần nhất theo từng agent ────────────────────────────────────────────────────────────

async def test_last_spoke_per_agent_counts_only_real_speech(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    quiet = await _seed_agent(db, org, name="Agent ít việc")
    busy = await _seed_agent(db, org, name="Agent nhiều việc")
    never = await _seed_agent(db, org, name="Agent chỉ im lặng")
    await _decision(db, org, quiet, "draft", minutes_ago=3000)       # hôm kia: có soạn nháp
    for _ in range(10):                                              # 10 quyết định mới hơn của agent khác
        await _decision(db, org, busy, "silent", minutes_ago=1)
    await _decision(db, org, busy, "suggest", minutes_ago=5)
    await _decision(db, org, never, "silent", minutes_ago=2)
    await _decision(db, org, never, "note", minutes_ago=2)
    by_id = {a["id"]: a for a in (await owner_api.get("/agents")).json()}
    assert by_id[str(quiet)]["last_spoke_at"] is not None and by_id[str(quiet)]["last_spoke_decision"] == "draft"
    assert by_id[str(busy)]["last_spoke_decision"] == "suggest"
    assert by_id[str(never)]["last_spoke_at"] is None and by_id[str(never)]["last_spoke_decision"] is None


# ─── F085: Lưu trữ agent + không trùng tên ───────────────────────────────────────────────────────────────────

async def test_archive_hides_agent_keeps_history_and_needs_pin(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    aid = await _seed_agent(db, org, name="A.Ly")
    await _decision(db, org, aid, "draft")
    api: Api = owner_api
    assert (await api.send("POST", f"/agents/{aid}/archive", {})).status_code == 423
    await _pin(api)
    r = await api.send("POST", f"/agents/{aid}/archive", {})
    assert r.status_code == 200, r.text
    assert r.json()["is_enabled"] is False and r.json()["archived"] is True
    assert aid not in {uuid.UUID(a["id"]) for a in (await api.get("/agents")).json()}
    keys = {i["agent_key"] for i in (await api.get("/agents/bindings")).json()["items"]}
    assert f"agent:{aid}" not in keys
    # lịch sử quyết định còn nguyên, vẫn đọc được tên
    dec = (await api.get("/agents/decisions")).json()
    assert dec["total"] == 1 and dec["items"][0]["agent"]["name"] == "A.Ly"
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'agent.archived' "
                               "AND target_id = :i"), {"i": str(aid)})).scalar_one()
    assert n == 1
    # tên đã lưu trữ dùng lại được
    r = await api.send("POST", "/agents", {**AGENT_BODY, "name": "a.ly"})
    assert r.status_code == 201, r.text


async def test_duplicate_name_rejected_on_create_patch_clone(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    first = await _seed_agent(db, org, name="A.Ly")
    other = await _seed_agent(db, org, name="Bé Na")
    api: Api = owner_api
    await _pin(api)
    r = await api.send("POST", "/agents", {**AGENT_BODY, "name": "  a.LY "})
    assert r.status_code == 422 and "name" in r.json()["errors"], r.text
    r = await api.send("PATCH", f"/agents/{other}", {"name": "A.ly"})
    assert r.status_code == 422 and "name" in r.json()["errors"], r.text
    r = await api.send("POST", f"/agents/{first}/clone", {"name": "bé na"})
    assert r.status_code == 422 and "name" in r.json()["errors"], r.text
    # giữ tên cũ khi sửa trường khác (kể cả đổi hoa thường của chính nó) vẫn được
    assert (await api.send("PATCH", f"/agents/{first}", {"name": "A.LY", "voice": "mới"})).status_code == 200
    assert (await api.send("POST", f"/agents/{first}/clone", {"name": "A.Ly bản 2"})).status_code == 201


# ─── F089: TẮT agent không cần PIN, BẬT cần PIN ──────────────────────────────────────────────────────────────

async def test_disable_needs_no_pin_enable_does(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    aid = await _seed_agent(db, org)
    api: Api = owner_api
    r = await api.send("PATCH", f"/agents/{aid}/disable", {"enabled": False})
    assert r.status_code == 200 and r.json()["is_enabled"] is False
    r = await api.send("PATCH", f"/agents/{aid}/disable", {"enabled": True})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    await _pin(api)
    r = await api.send("PATCH", f"/agents/{aid}/disable", {"enabled": True})
    assert r.status_code == 200 and r.json()["is_enabled"] is True
    log = (await db.execute(text("SELECT action FROM ops.action_log WHERE action IN ('agent.enabled','agent.disabled') "
                                 "AND target_id = :i ORDER BY at"), {"i": str(aid)})).scalars().all()
    assert log == ["agent.disabled", "agent.enabled"]


# ─── F091: nhân bản chép cả model riêng ──────────────────────────────────────────────────────────────────────

async def test_clone_copies_own_model_binding(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, *_ = await _channel_group(db)
    aid = await _seed_agent(db, org)
    api: Api = owner_api
    _pid, mid = await _seed_provider_model(api, db)
    r = await api.send("PUT", f"/agents/bindings/agent:{aid}",
                       {"model_id": mid, "temperature": 0.7, "context_tokens": 4000, "rule_codes": ["r1"]})
    assert r.status_code == 200, r.text
    r = await api.send("POST", f"/agents/{aid}/clone", {"name": "Bản sao có model"})
    assert r.status_code == 201, r.text
    b = r.json()["binding"]
    assert b is not None and b["model_id"] == mid and b["temperature"] == 0.7 and b["rule_codes"] == ["r1"]
    # agent nguồn không có model riêng ⇒ bản sao cũng không
    plain = await _seed_agent(db, org, name="Trơn")
    r = await api.send("POST", f"/agents/{plain}/clone", {"name": "Trơn 2"})
    assert r.status_code == 201 and r.json()["binding"] is None


# ─── F142 (+F082): mẫu sạch tên dự án cũ và tiếng Anh ────────────────────────────────────────────────────────

async def test_templates_vietnamese_no_old_project_name(owner_api) -> None:  # type: ignore[no-untyped-def]
    items = (await owner_api.get("/agents/templates")).json()
    blob = " ".join(f"{t['name']} {t['role_desc']}" for t in items).lower()
    assert "heo-harness" not in blob and "recruiter" not in blob and "key account" not in blob
    assert next(t for t in items if t["code"] == "mascot")["default_enabled"] is False


# ─── Review v0.1.67 P3: agent đã Lưu trữ không được dùng lại ────────────────────────────────────────────────

async def test_archived_agent_cannot_be_cloned_patched_bound_or_assigned(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org, _ch, gid = await _channel_group(db)
    old = await _seed_agent(db, org, name="Cũ")
    live = await _seed_agent(db, org, name="Còn dùng")
    pid_ = (await db.execute(text("""
        INSERT INTO core.persons (org_id, code, display_name, person_type, relation_to_owner)
        VALUES (:o, 'PT-A1', 'Khách A', 'customer', 'direct') RETURNING id"""), {"o": org})).scalar_one()
    await db.commit()
    api: Api = owner_api
    _pid, mid = await _seed_provider_model(api, db)
    assert (await api.send("POST", f"/agents/{old}/archive", {})).status_code == 200

    gone = str(old)
    for method, path, body in (
            ("POST", f"/agents/{old}/clone", {"name": "Bản sao"}),
            ("PATCH", f"/agents/{old}", {"voice": "giọng mới"}),
            ("PUT", f"/agents/bindings/agent:{old}", {"model_id": mid}),
            ("POST", f"/directory/groups/{gid}/bot", {"agent_id": gone}),
            ("POST", f"/directory/people/{pid_}/bot", {"agent_id": gone}),
            ("POST", "/directory/people/bulk-bot", {"filter": {}, "agent_id": gone})):
        r = await api.send(method, path, body)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
    assert (await db.execute(text("SELECT voice FROM agent.identities WHERE id = :i"), {"i": old})).scalar_one() \
        == "lễ phép"                                                      # PATCH bị chặn, không ghi
    assert (await db.execute(text("SELECT count(*) FROM agent.bindings WHERE agent_key = :k"),
                             {"k": f"agent:{old}"})).scalar_one() == 0
    # agent còn dùng vẫn gán được
    assert (await api.send("PUT", f"/agents/bindings/agent:{live}", {"model_id": mid})).status_code == 200
    assert (await api.send("POST", f"/directory/groups/{gid}/bot", {"agent_id": str(live)})).status_code == 200
    assert (await api.send("POST", f"/directory/people/{pid_}/bot", {"agent_id": str(live)})).status_code == 200
