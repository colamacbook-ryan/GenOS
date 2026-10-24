# ruff: noqa: E501
"""Các công cụ phụ của bộ đo G-KB (L6): kiểm rò rỉ n-gram, phiếu rà tay, đo token mỗi lượt (S7), so pipeline với lô 40 (red team B), chạy bộ ≥ 3 lần + lần chưa xác nhận,
engine media giả, seed qua API công khai (MockTransport), chế độ thời gian thật làm tham chiếu, khóa GH_ENV=eval."""

import json
from pathlib import Path
from typing import Any

import compare_b
import gkb_score as gs
import httpx
import leakcheck
import manual_sheet
import pytest
import replay
import tokens

from tests.scenarios import intel_helpers as h

# ─── leakcheck ───────────────────────────────────────────────────────────────────────────────────────────────────────────

DATA = {"kb1:m1": "Chị Tư ơi tuần này chị lấy 20kg Blend Honey như cũ nha em lên đơn liền"}


def test_leakcheck_blocks_skill_example_copied_from_data_ngram_8() -> None:
    skill = {"skill.md": "Ví dụ: «chị tư ơi, tuần này chị lấy 20kg Blend Honey như cũ nha» là một lời hứa"}
    hits = leakcheck.check(skill, DATA, 8)
    assert hits and hits[0]["source"] == "kb1:m1"
    assert leakcheck.check({"s": "Ví dụ khác hoàn toàn: khách hỏi giá hạt rang sỉ"}, DATA, 8) == []
    # 7 từ trùng thì chưa chặn (ngưỡng là ≥ 8)
    assert leakcheck.check({"s": "chị tư ơi tuần này chị lấy"}, DATA, 8) == []
    assert leakcheck.check({"s": "chị tư ơi tuần này chị lấy"}, DATA, 7)


def test_leakcheck_ignores_diacritics_case_punctuation() -> None:
    assert leakcheck.check({"s": "CHI TU OI!!! tuan nay chi lay 20kg, Blend-Honey nhu cu"}, DATA, 8)


def test_leakcheck_cli_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ds = h.synth_dataset()
    d = h.write_ds(ds, tmp_path / "kb")
    leaky = tmp_path / "skill.md"
    leaky.write_text(f"ví dụ {h.PLANTED['otp'][2]} hết")
    clean = tmp_path / "clean.md"
    clean.write_text("một đoạn kỹ năng không liên quan gì tới dữ liệu kiểm cả")
    monkeypatch.setattr("sys.argv", ["leakcheck", "--skill", str(leaky), "--data", str(d)])
    with pytest.raises(SystemExit) as e:
        leakcheck.main()
    assert e.value.code == 1
    monkeypatch.setattr("sys.argv", ["leakcheck", "--skill", str(clean), "--data", str(d)])
    with pytest.raises(SystemExit) as e2:
        leakcheck.main()
    assert e2.value.code == 0


def test_leakcheck_extra_text_covers_answer_key_outside_repo() -> None:
    key_strings = {"key#0": "đã nhập mã OTP vào trang giả của ngân hàng giả mạo lúc chiều"}
    assert leakcheck.check({"s": "ví dụ đã nhập mã OTP vào trang giả của ngân hàng giả mạo lúc chiều ok"}, key_strings, 8)


# ─── replay: bộ chạy, tham chiếu, khoá env, media giả, seed ─────────────────────────────────────────────────────────────

async def test_suite_runs_three_compressed_plus_unconfirmed_plus_realtime_reference(tmp_path: Path) -> None:
    ds = h.synth_dataset()
    out = await replay.run_suite(ds, tmp_path, repeat=3, unconfirmed_extra=True, realtime_ref=True)
    labels = [r["label"] for r in out]
    assert labels == ["r1", "r2", "r3", "u1", "ref"]
    by = {r["label"]: r for r in out}
    for k in ("r1", "r2", "r3", "u1"):
        assert by[k]["mode"] == "compressed" and by[k]["distribution_check"]["ok"], by[k]["distribution_check"]
        assert by[k]["distribution"]["lively"]["median"] >= 10 and by[k]["distribution"]["overflow_turns"] >= 1
    assert by["u1"]["confirmed"] is False and by["ref"]["mode"] == "realtime"
    assert by["ref"]["distribution"]["overall"]["median"] < by["r1"]["distribution"]["overall"]["median"]       # thời gian thật: lượt nhỏ
    assert json.loads((tmp_path / "suite.json").read_text())["runs"][4]["label"] == "ref"
    for f in ("plan.json", "turns.jsonl", "outputs.jsonl", "tokens.jsonl", "idmap.json", "media_table.json", "summary.json"):
        assert (tmp_path / "r1" / f).exists()
    # ba lần nén giống hệt nhau về kế hoạch (tất định) ⇒ so sánh lần với lần có nghĩa
    assert (tmp_path / "r1" / "turns.jsonl").read_text() == (tmp_path / "r2" / "turns.jsonl").read_text()


async def test_replay_orders_events_before_each_turn_and_stub_output_is_valid(tmp_path: Path) -> None:
    ds = h.synth_dataset()
    emitted: list[tuple[str, str, dict[str, Any]]] = []

    class Sink:
        async def emit(self, stream: str, type: str, payload: dict[str, Any]) -> None:
            emitted.append((stream, type, payload))

        async def close(self) -> None:
            return None

    s = await replay.run_replay(ds, tmp_path / "r", sink=Sink())
    assert s["analyzer"] == "stub" and s["emitted"] == len(ds.msgs) == len(emitted)
    ids = [p["external_msg_id"] for _, _, p in emitted]
    assert ids == [m.id for m in ds.msgs]                                           # giữ thứ tự toàn cục
    by_ext = {p["external_msg_id"]: p for _, _, p in emitted}
    first_reply = next(m for m in ds.msgs if m.reply_to)
    assert by_ext[first_reply.id]["payload"]["reply_to"] == first_reply.reply_to    # chuỗi reply_to đi theo phong bì
    assert all(p["occurred_at"] == m.ts.isoformat() for (_, _, p), m in zip(emitted, ds.msgs, strict=True))     # giờ GỐC, không phải giờ nén
    outs = [json.loads(x) for x in (tmp_path / "r" / "outputs.jsonl").read_text().splitlines()]
    assert outs and all(o["error"] is None for o in outs)
    assert all(p["mentions_self"] is False for _, _, p in emitted)


def test_voice_and_image_use_media_ref_and_fake_engine_returns_exact_text() -> None:
    ds = h.synth_dataset()
    voice = next(m for m in ds.msgs if m.kind == "voice_transcript")
    image = next(m for m in ds.msgs if m.kind == "image_caption")
    ev_v, ev_i = common_ev(voice), common_ev(image)
    fb = h.common.fake_bridge()
    (_, _, pv), = fb.compile_script_event(ev_v)
    (_, _, pi), = fb.compile_script_event(ev_i)
    assert pv["kind"] == "voice" and pv["body_text"] is None and pv["payload"]["media_ref"].startswith("fake://voice/")
    assert pi["kind"] == "image" and pi["payload"]["media_ref"].startswith("fake://image/")
    eng = replay.FakeMediaEngine()
    eng.register(pv["payload"]["media_ref"], pv["payload"]["fake_media_text"])
    eng.register(pi["payload"]["media_ref"], pi["payload"]["fake_media_text"])
    assert eng.process(pv["payload"]["media_ref"]) == {"status": "ok", "text": voice.text, "engine": "fake"}
    assert eng.process(pi["payload"]["media_ref"])["text"] == h.common.strip_media_prefix(image.text)
    assert eng.process("fake://voice/khong-co")["status"] == "failed"
    assert eng.coverage() == {"processed": 2, "failed": 1, "read_rate": pytest.approx(2 / 3)}
    eng2 = replay.FakeMediaEngine(fail_refs={pv["payload"]["media_ref"]}, table=eng.table)
    assert eng2.process(pv["payload"]["media_ref"])["status"] == "failed"


def common_ev(m: Any) -> dict[str, Any]:
    return h.common.to_script_event(m)


async def test_media_text_reaches_analyzer_via_fake_engine(tmp_path: Path) -> None:
    ds = h.synth_dataset()
    seen: list[dict[str, Any]] = []

    class Spy(replay.StubAnalyzer):
        async def analyze(self, inp: replay.TurnInput) -> replay.AnalysisResult:
            seen.append(inp.media)
            return await super().analyze(inp)

    s = await replay.run_replay(ds, tmp_path / "r", analyzer=Spy())
    flat = {k: v for d in seen for k, v in d.items()}
    voices = [m for m in ds.msgs if m.kind == "voice_transcript"]
    assert flat and all(flat[m.id]["status"] == "ok" and flat[m.id]["text"] == m.text for m in voices if m.id in flat)
    assert s["media_coverage"]["failed"] == 0 and s["media_coverage"]["processed"] >= 1


async def test_bus_sink_requires_gh_env_key_in_target_redis(redis, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Review L6 🟡7: kiểm PHÍA ĐÍCH — Redis phải có gh:env = eval, không chỉ tin biến môi trường của người chạy."""
    from tests.conftest import REDIS_URL

    monkeypatch.setenv("GH_ENV", "eval")
    await redis.delete("gh:env", "gh.bridge.inbound")
    sink = replay.BusSink(REDIS_URL, "org-x")
    with pytest.raises(SystemExit, match="gh:env"):
        await sink.emit("gh.bridge.inbound", "message", {"x": 1})
    assert await redis.xlen("gh.bridge.inbound") == 0
    await redis.set("gh:env", "production")
    with pytest.raises(SystemExit):
        await sink.emit("gh.bridge.inbound", "message", {"x": 1})
    await redis.set("gh:env", "eval")
    await sink.emit("gh.bridge.inbound", "message", {"x": 1})
    assert await redis.xlen("gh.bridge.inbound") == 1
    await redis.delete("gh:env", "gh.bridge.inbound")
    await sink.close()


async def test_seeder_logs_in_with_csrf_header_like_e2e() -> None:
    """Review L6 🟡9: Seeder đăng nhập thật (cookie phiên + gh_csrf → X-CSRF-Token), không dùng client trần."""
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/auth/login":
            return httpx.Response(200, json={"ok": True}, headers={"set-cookie": "gh_csrf=tok123; Path=/"})
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.test")
    s = replay.Seeder(client)
    await s.login("sep@example.com", "mat-khau")
    await s.group_ids_by_name()
    assert json.loads(seen[0].content) == {"email": "sep@example.com", "password": "mat-khau"}
    assert seen[1].headers["x-csrf-token"] == "tok123"
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)), base_url="http://api.test")
    with pytest.raises(SystemExit, match="đăng nhập thất bại"):
        await replay.Seeder(bad).login("a@b.c", "x")
    await client.aclose()
    await bad.aclose()


def test_gh_env_eval_is_mandatory_for_bus_and_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_ENV", "development")
    with pytest.raises(SystemExit, match="GH_ENV"):
        replay.require_eval_env()
    with pytest.raises(SystemExit):
        replay.BusSink("redis://localhost:6379/15", "org")
    monkeypatch.setenv("GH_ENV", "eval")
    replay.require_eval_env()


async def test_seeder_uses_public_api_only(tmp_path: Path) -> None:
    ds = h.synth_dataset()
    calls: list[tuple[str, str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        if req.method == "GET" and req.url.path == "/channels/zalo/groups":
            return httpx.Response(200, json=[{"id": "uuid-g1", "name": "Nhóm G1"}, {"id": "uuid-g2", "name": "Nhóm G2"}])
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.test")
    seeder = replay.Seeder(client)
    s = await replay.run_replay(ds, tmp_path / "r", seeder=seeder, confirmed=True)
    puts = [(p, b) for m, p, b in calls if m == "PUT"]
    assert ("/groups/uuid-g1/contexts", {"labels": [{"context": "kinh_doanh", "is_primary": True}]}) in puts
    assert ("/groups/uuid-g2/contexts", {"labels": [{"context": "gia_dinh", "is_primary": True}]}) in puts
    posts = [b for m, p, b in calls if m == "POST" and p == "/me/profile/items"]
    assert {"predicate": "owner_goal", "text": "Mở quán thứ hai trước Tết"} in posts and {"predicate": "owner_value_limit", "text": "Không vay mượn bạn bè"} in posts
    assert s["seeded"] == {"contexts": 2, "profile": 2}
    # lần chưa xác nhận: Hồ sơ có, nhãn nhóm KHÔNG
    calls.clear()
    s2 = await replay.run_replay(ds, tmp_path / "r2", seeder=replay.Seeder(client), confirmed=False)
    assert not [1 for m, p, b in calls if m == "PUT"] and s2["seeded"]["contexts"] == 0 and s2["seeded"]["profile"] == 2
    await client.aclose()


def test_context_parse_on_kb4_style_table_and_profile() -> None:
    md = ("## 2. Bảy nhóm\n| Nhóm | Tên | Bối cảnh | Thành viên chính |\n|---|---|---|---|\n| G1 | Gia đình nhỏ | gia đình | a |\n| G3 | Bạn thân | bạn bè | b |\n| G6 | Quán | nhân viên | c |\n\n"
          "## 4. Hồ sơ Sếp (owner model)\n\n**Mục tiêu và ưu tiên quý 4/2026 (theo thứ tự)**\n1. Gia đình: sức khoẻ ba má\n2. Hoàn thành MBA\n\n**Nguồn lực**\n- Tiền: 600 triệu\n\n"
          "**Giá trị và giới hạn**\n- Không vay\n\n**Sở thích cách báo**\n- Bản tin 07:30 tối đa 10 dòng\n")
    c = h.common.parse_context(md)
    assert c["groups"] == {"G1": "gia_dinh", "G3": "ban_be", "G6": "nhan_vien"}
    assert c["profile"]["owner_goal"] == ["Gia đình: sức khoẻ ba má", "Hoàn thành MBA"]
    assert c["profile"]["owner_resource"] == ["Tiền: 600 triệu"] and c["profile"]["owner_value_limit"] == ["Không vay"]
    assert c["profile"]["owner_report_pref"] == ["Bản tin 07:30 tối đa 10 dòng"]


def test_load_chats_rejects_malformed_line(tmp_path: Path) -> None:
    p = tmp_path / "chats.jsonl"
    p.write_text('{"id": "m1", "group": "G1"}\n')
    with pytest.raises(ValueError, match="sai khuôn"):
        h.common.load_chats(p)


# ─── phiếu rà tay ───────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_manual_sheet_lists_only_unmatched_units_with_context_and_no_answer(tmp_path: Path) -> None:
    ds = h.synth_dataset()
    key = h.tiny_key(ds)
    odd = next(m for m in ds.msgs if m.group == "G1" and m.text.startswith("tin thường") and m.reply_to is None)

    def extra(inp: Any, n: int) -> list[dict[str, Any]]:
        return [{"unit_ref": f"q{n}", "event_ref": "q", "kind": "viec_han", "quotes": [{"message_id": m["message_id"], "field": "body", "text": m["text"][:80]}],
                 "summary": "mục không ghép được", "level_hint": "amber"} for m in inp.new if m["id"] == odd.id]

    await replay.run_replay(ds, tmp_path / "r1", analyzer=h.Oracle(key, extra=extra))
    md, template = manual_sheet.build_sheet(ds, gs.Run(tmp_path / "r1", ds), key)
    assert len(template) == 1 and odd.text in md and "mục không ghép được" in md and "→" in md
    assert "rui_ro_lua_dao" not in md and "E1" not in md                # không lộ đáp án ra phiếu
    md2, _ = manual_sheet.build_sheet(ds, gs.Run(tmp_path / "r1", ds), key, {k: "ok" for k in template})
    assert "chưa rà: 0" in md2


# ─── token / S7 ──────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_s7_token_report_both_modes_and_costs(tmp_path: Path) -> None:
    ds = h.synth_dataset()
    key = h.tiny_key(ds)
    await replay.run_replay(ds, tmp_path / "r1", analyzer=h.Oracle(key, tokens=9000), mode="compressed")
    await replay.run_replay(ds, tmp_path / "ref", analyzer=h.Oracle(key, tokens=9000), mode="realtime")
    rep = tokens.report([str(tmp_path / "r1"), str(tmp_path / "ref")])
    assert set(rep["by_mode"]) == {"compressed", "realtime"} and rep["note"] is None
    r1 = rep["runs"][0]
    assert r1["tokens_per_turn"]["input"]["median"] == 9000 and r1["cost_vnd_total"] is None            # không bịa giá
    assert rep["by_mode"]["realtime"]["turns"] > rep["by_mode"]["compressed"]["turns"]               # thời gian thật: nhiều lượt nhỏ hơn
    priced = tokens.report([str(tmp_path / "r1")], {"input_vnd_per_m": 100_000, "output_vnd_per_m": 500_000, "cache_read_vnd_per_m": 10_000})
    t = priced["runs"][0]
    assert t["cost_vnd_total"] == pytest.approx(t["turns"] * (9000 * 0.1 + 100 * 0.5 + 500 * 0.01))
    assert r1["vs_spec_assumption"]["input"]["spec"] == 8900 and r1["turns_per_day_max"] >= 1
    free = tokens.report([str(tmp_path / "r1")], {"input_vnd_per_m": 0, "output_vnd_per_m": 0})
    assert free["runs"][0]["cost_vnd_total"] == 0                                                      # gói thuê bao CLI = 0 ₫


async def test_s7_notes_when_analyzer_is_stub(tmp_path: Path) -> None:
    await replay.run_replay(h.synth_dataset(), tmp_path / "r1")
    assert "StubAnalyzer" in tokens.report([str(tmp_path / "r1")])["note"]


# ─── red team B ──────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_compare_b_batch40_vs_thread(tmp_path: Path) -> None:
    ds = h.synth_dataset()
    key = h.tiny_key(ds)
    await replay.run_replay(ds, tmp_path / "batch", analyzer=h.Oracle(key), plan_kind="batch40")
    await replay.run_replay(ds, tmp_path / "t1", analyzer=h.Oracle(key))

    class Weak(h.Oracle):                 # lượt theo luồng sót một sự việc ⇒ kém lô 40
        async def analyze(self, inp: Any) -> Any:
            r = await super().analyze(inp)
            r.output["units"] = [u for u in r.output["units"] if u["event_ref"] != "E4"]
            r.output["facts_proposed"] = [f for f in r.output["facts_proposed"] if f["unit_ref"] in {u["unit_ref"] for u in r.output["units"]}]
            return r

    await replay.run_replay(ds, tmp_path / "t2", analyzer=Weak(key))
    ok = compare_b.compare(ds, key, [str(tmp_path / "batch")], [str(tmp_path / "t1")])
    assert ok["thread_not_worse"] and ok["delta_recall_event"] == 0
    bad = compare_b.compare(ds, key, [str(tmp_path / "batch")], [str(tmp_path / "t2")])
    assert not bad["thread_not_worse"] and bad["delta_recall_event"] == pytest.approx(-1 / 6)
    s = json.loads((tmp_path / "batch" / "summary.json").read_text())
    assert s["mode"] == "batch40" and all(t["thread"] == "mixed" for t in map(json.loads, (tmp_path / "batch" / "turns.jsonl").read_text().splitlines()))
