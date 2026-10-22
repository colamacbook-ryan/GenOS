# ruff: noqa: F811 — fixture `world` nhập từ test_p3_market
"""v0.1.71 — lỗi tìm thấy khi giả lập toàn luồng trên v0.1.70 (ke-hoach/review/giaolap-v0170.md). Mỗi mục một khối,
đỏ trước khi sửa, xanh sau."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import text

from gh.auth import rbac, service
from gh.biz.hooks import HookCtx
from gh.biz.market.jobs import market_signal_capture
from gh.biz.queue import jobs as queue_jobs
from gh.chassis.masking import mask_for_prompt
from gh.db import sessionmaker
from gh.gen import briefing
from gh.gen.tools import ToolRunner
from gh.providers.clients import Message
from gh.refinery.runner import Refinery
from tests.phase2 import FakeRouter, install_presets, listen, msg, org_id, put
from tests.test_p3_market import world  # noqa: F401 — fixture
from tests.test_p3_queue import _person_of, _unit
from tests.test_refinery import by_text

NOW = dt.datetime.now(dt.UTC)


def ts(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


# ─── P2-2 — chứng cứ loại raw của cảnh báo «Phản hồi chậm» ────────────────────────────────────────────────────────

async def test_slow_response_alert_explains_with_its_raw_evidence(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [raw] = await put(sessionmaker(), org, {**msg("Cần 3 container thép, giá bao nhiêu?", sender="u1", name="Chị Lan"),
                                            "occurred_at": ts(4)})
    lan = await _person_of(db, raw)
    unit = await _unit(db, org, person_id=lan, event_type="AskedPrice", conclusion="Hỏi giá", raw_id=raw)
    await db.commit()
    await queue_jobs.early_warning_scan({"redis_bus": redis})
    alert = (await db.execute(text("SELECT id, evidence FROM biz.alerts WHERE alert_type = 'slow_response'"))).one()
    assert alert.evidence == [{"type": "raw", "id": str(raw)}]          # chứng cứ loại raw (không phải meaning_unit)
    r = await owner_api.get(f"/explain/alert/{alert.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [u["id"] for u in body["units"]] == [str(unit)], body          # đơn vị chứa tin bị bỏ quên + trích dẫn
    assert "Chưa có chứng cứ" not in body["statement"]
    assert body["units"][0]["quotes"][0]["raw_id"] == str(raw)
    inbox: Any = await owner_api.get(f"/inbox/{alert.id}")                   # chi tiết trong Hộp thư cũng thấy
    assert inbox.status_code == 200, inbox.text
    assert [u["id"] for u in inbox.json()["units"]] == [str(unit)]


# ─── P2-3 — OTP / mật khẩu / số điện thoại không vào prompt model nguyên văn ────────────────────────────

SECRET_MSG = ("Em cần 3 container thép giá 25.000.000 đồng, OTP của em là 482913, mật khẩu wifi: abc12345xyz, "
              "gọi em số 0912 345 678, STK 1234 5678 9012 3456")


def _no_secret(s: str) -> None:
    for leak in ("482913", "abc12345xyz", "0912 345 678", "0912345678", "1234 5678 9012", "912 345 678"):
        assert leak not in s, leak


def test_mask_for_prompt_hides_secrets_and_phones_but_keeps_money_dates_and_ids() -> None:
    out = mask_for_prompt(SECRET_MSG)
    _no_secret(out)
    assert "25.000.000" in out                              # số tiền để model còn trích value_vnd
    assert "•••••••678" in out                              # SĐT: che phần giữa, giữ 3 số cuối (đủ để hiểu là SĐT)
    uid = str(uuid.uuid4())
    keep = f"{uid} 2026-10-18T10:20:30.123456+07:00 18.10.2026 PER-ZL-0001234 giá 1,200,000,000 và 250000000"
    assert mask_for_prompt(keep) == keep
    nested = mask_for_prompt({"password": "abc", "n": 5, "rows": [{"text": "gọi 0987654321"}]})
    assert nested == {"password": "[đã che]", "n": 5, "rows": [{"text": "gọi •••••••321"}]}


async def test_refinery_prompt_masks_outsider_text(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await install_presets(db, org)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, msg(SECRET_MSG, sender="u9", name="Chị Lan 0988777666"))
    router: FakeRouter = by_text()
    await Refinery(sessionmaker(), redis, router).run(org, "manual")  # type: ignore[arg-type]
    assert router.calls, "phải có lượt gọi model"
    prompt = "\n".join(m.content for m in router.calls[0])
    _no_secret(prompt)
    assert "0988777666" not in prompt and "25.000.000" in prompt


async def test_duty_context_line_masks_outsider_text() -> None:
    from gh.biz.duty.context import Item

    it = Item("C1", "trigger", {"type": "meaning_unit", "id": str(uuid.uuid4())}, "x",
              {"conclusion": SECRET_MSG, "quotes": [SECRET_MSG], "observed_at": dt.datetime.now(dt.UTC)})
    line = it.line()
    _no_secret(line)
    assert "25.000.000" in line and line.startswith("C1 ")


async def test_briefing_summary_prompt_masks_outsider_lines(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    seen: list[list[Message]] = []

    class _R:
        async def generate(self, org_id_: Any, **kw: Any) -> Any:
            seen.append(kw["messages"])
            return type("Routed", (), {"text": "tóm tắt"})()

    out = await briefing._summarize(_R(), org,  # type: ignore[arg-type]
                                    [{"title": "Cơ hội", "count": "1", "lines": [SECRET_MSG]}])
    assert out == "tóm tắt" and seen
    _no_secret("\n".join(m.content for m in seen[0]))
    assert "25.000.000" in seen[0][-1].content


async def test_gen_tool_results_to_model_are_masked_but_ids_survive(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [raw] = await put(sessionmaker(), org, msg("Cần container", sender="u1", name="Chị Lan"))
    lan = await _person_of(db, raw)
    unit = await _unit(db, org, person_id=lan, event_type="AskedPrice", conclusion=SECRET_MSG, raw_id=raw)
    await db.commit()
    token = owner_api.c.cookies.get(service.SESSION_COOKIE)
    me = service.CurrentUser(id=uuid.uuid4(), org_id=org, email="", display_name="", role_code="owner", role_name="",
                             role_id=uuid.uuid4(), team_id=None, session_id=uuid.uuid4(), pin_verified_until=None,
                             addressing={}, permissions=dict(rbac.DEFAULT_MATRIX["owner"]))
    run = ToolRunner(owner_api.c._transport.app, me, token)  # type: ignore[attr-defined]
    res = await run.run("queue.list", {"tab": "all"})
    assert res.ok and str(unit) in res.text and str(unit) in run.seen_ids       # id còn nguyên cho validator
    _no_secret(res.text)


# ─── P3-5 — tin ĐI của nhân viên / tài khoản Sếp không tạo tín hiệu cầu, cơ hội ──────────────────────────────────────

async def test_outbound_messages_do_not_create_market_signals_or_opportunities(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await install_presets(db, org)
    await listen(db, org, "g1")
    sm = sessionmaker()
    await put(sm, org, {**msg("Anh cần 3 container thép cuộn giá bao nhiêu em", sender="own-zalo", name="Sếp"),
                        "direction": "outbound"},
              msg("Cần 3 container thép cuộn, giá bao nhiêu vậy em?", sender="khach1", name="Chị Lan"))
    await Refinery(sm, redis, by_text()).run(org, "manual")  # type: ignore[arg-type]
    units = (await db.execute(text("SELECT count(*) FROM clean.meaning_units WHERE side = 'demand'"))).scalar_one()
    assert units == 2                                                  # cả hai tin đều thành đơn vị (lưu đủ)
    ids = list((await db.execute(text("SELECT id FROM clean.meaning_units"))).scalars().all())
    await market_signal_capture(HookCtx(org_id=org, unit_ids=ids, run_id=None, sm=sm, redis=redis, bus=None,
                                        router=None))
    sig = (await db.execute(text("""SELECT p.display_name FROM biz.market_signals s
                                    JOIN core.persons p ON p.id = s.person_id"""))).scalars().all()
    opp = (await db.execute(text("""SELECT p.display_name FROM biz.opportunities o
                                    JOIN core.persons p ON p.id = o.person_id"""))).scalars().all()
    assert sig == ["Chị Lan"] and opp == ["Chị Lan"], (sig, opp)      # chỉ khách thật, không có «Sếp»


# ─── P3-11 — cảnh báo P1 (phàn nàn nặng) rung chuông Owner ───────────────────────────────────────────────────────────

async def test_p1_complaint_alert_rings_the_owner_bell(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await install_presets(db, org)
    await listen(db, org, "g1")

    def reply(messages: list[Message]) -> dict[str, Any]:
        from tests.phase2 import texts_in

        refs = list(texts_in(messages))
        return {"units": [{"evidence": [refs[0]], "event_type": "Complained", "side": None,
                           "conclusion": "Khách bực vì chưa ai trả lời", "entities": {}, "confidence": 0.95,
                           "rules": {"R-03": 0.95}, "signals": {"churn_risk": 70}}], "noise": refs[1:]}

    sm = sessionmaker()
    await put(sm, org, msg("Không ai trả lời tôi, quá chậm, tôi chuyển sang nhà cung cấp khác 0912345678",
                           sender="u9", name="Chị Lan"))
    await Refinery(sm, redis, FakeRouter(reply)).run(org, "manual")  # type: ignore[arg-type]
    alert = (await db.execute(text("SELECT priority, alert_type FROM biz.alerts"))).one()
    assert alert.priority == "P1" and alert.alert_type == "repeated_complaint"
    rows = (await db.execute(text("""SELECT kind, title, body, link FROM core.notifications
                                  WHERE kind = 'alert.p1'"""))).all()
    assert len(rows) == 1, rows
    assert rows[0].link == "/inbox?tab=alert" and "Cảnh báo khẩn" in rows[0].title
    assert "0912345678" not in rows[0].title + rows[0].body                # chuông không mang số của khách
    got = (await owner_api.get("/notifications")).json()
    assert any(n["kind"] == "alert.p1" for n in got["items"])


async def test_p2_alert_does_not_ring_the_bell(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    from gh.providers.router import raise_alert

    await raise_alert(db, org, alert_type="competitor", priority="P2", title="Đối thủ", summary="x")
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'alert.p1'"))).scalar_one() == 0


# ─── P3-12 — tài khoản kênh của chính Sếp không thành «Người lạ có tín hiệu» ──────────────────────────────────────────

async def test_own_channel_account_is_not_a_stranger(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, msg("Dạ em gửi báo giá", sender="own-zalo", name="Sếp") | {"direction": "outbound"},
              msg("Giá thép bao nhiêu", sender="khach1", name="Chị Lan"))
    rel = dict((await db.execute(text("""SELECT pi.external_id, p.relation_to_owner FROM core.persons p
                                         JOIN core.person_identities pi ON pi.person_id = p.id"""))).all())
    assert rel == {"own-zalo": "staff", "khach1": "stranger"}
    # Sếp đã tự chọn «Liên quan Sếp» thì tin đi sau đó không ghi đè
    await db.execute(text("""UPDATE core.persons SET relation_to_owner = 'direct'
                             WHERE id IN (SELECT person_id FROM core.person_identities
                                          WHERE external_id = 'own-zalo')"""))
    await db.commit()
    await put(sessionmaker(), org, msg("Dạ", sender="own-zalo", name="Sếp") | {"direction": "outbound"})
    got = (await db.execute(text("""SELECT p.relation_to_owner FROM core.persons p
                                    JOIN core.person_identities pi ON pi.person_id = p.id
                                    WHERE pi.external_id = 'own-zalo'"""))).scalar_one()
    assert got == "direct"


# ─── P3-13 — nháp «Giới thiệu nguồn cung» nêu số lượng / giá / khu vực có trong tín hiệu ─────────────────────────────

async def test_introduce_draft_names_quantity_price_and_area(world, owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from gh.biz.market.jobs import recompute_matches_org

    await recompute_matches_org(db, world["org"])
    await db.commit()
    match_id = (await owner_api.get("/matches")).json()["items"][0]["id"]
    body = (await owner_api.send("POST", f"/matches/{match_id}/introduce", {})).json()
    d = (await owner_api.get(f"/drafts/{body['draft']['id']}")).json()
    text_body = d["body"]["text"] if "body" in d else d["text"]
    assert "Thép cuộn" in text_body
    assert "4 container" in text_body and "1.100.000.000 đồng" in text_body and "Hà Nội" in text_body, text_body


# ─── P3-14 — «N nhóm mới đã vào Lắng nghe im lặng» giảm khi Sếp đổi chế độ nhóm ───────────────────────────────────────

async def test_new_silent_counter_drops_when_boss_changes_a_group_mode(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await put(sessionmaker(), org, msg("a", group="g1"), msg("b", group="g2"), msg("c", group="g3"))

    async def count() -> int:
        cards = (await owner_api.get("/channels")).json()
        return int(next(c for c in cards if c["type"] == "zalo")["new_groups_silent"])

    gid = {r.external_id: r.id for r in (await db.execute(text("SELECT id, external_id FROM core.groups"))).all()}
    assert await count() == 3
    r = await owner_api.send("PATCH", f"/groups/{gid['g1']}", {"listen_mode": "tagged_only"})
    assert r.status_code == 200, r.text
    assert await count() == 2                                   # Sếp đã xử lý một nhóm ⇒ không còn «mới»
    await owner_api.send("PATCH", f"/groups/{gid['g1']}", {"listen_mode": "off"})       # nhóm không còn silent
    assert await count() == 2
    await owner_api.send("PATCH", f"/groups/{gid['g2']}", {"view_scope": "all_members"})   # đổi thứ khác: giữ
    assert await count() == 2
    await owner_api.send("PATCH", f"/groups/{gid['g2']}", {"listen_mode": "off"})
    await owner_api.send("PATCH", f"/groups/{gid['g3']}", {"listen_mode": "off"})
    assert await count() == 0
    await owner_api.send("PATCH", f"/groups/{gid['g3']}", {"listen_mode": "silent"})
    await owner_api.send("PATCH", f"/groups/{gid['g3']}", {"listen_mode": "off"})
    assert await count() == 0                                   # không bao giờ âm


# ─── P3-15 — «lượt/24h» của quy tắc gồm cả khớp do model xác nhận ────────────────────────────────────────────────────

async def test_rule_hits_24h_counts_model_confirmed_matches(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await install_presets(db, org)
    await listen(db, org, "g1")

    def reply(messages: list[Message]) -> dict[str, Any]:
        from tests.phase2 import texts_in

        refs = list(texts_in(messages))
        # tin thường (không từ khoá bất mãn) nhưng model thấy đúng quy tắc R-03 với độ tin cao hơn ngưỡng
        return {"units": [{"evidence": [refs[0]], "event_type": "Complained", "side": None,
                           "conclusion": "Khách tỏ ý không hài lòng", "entities": {}, "confidence": 0.9,
                           "rules": {"R-03": 0.95}, "signals": {}}], "noise": refs[1:]}

    sm = sessionmaker()
    await put(sm, org, msg("Hàng về hơi khác mẫu em gửi hôm trước", sender="u9", name="Chị Lan"))
    await Refinery(sm, redis, FakeRouter(reply)).run(org, "manual")  # type: ignore[arg-type]
    rules = {r["code"]: r["hits_24h"] for r in (await owner_api.get("/rules")).json()}
    assert rules["R-03"] == 1, rules


# ─── P3-6 — «Chăm sóc khách»: tin của nhân viên không phải «tin cần trả lời» ──────────────────────────────────────────

async def _care_world(db: Any) -> dict[str, Any]:
    org = await org_id(db)
    await listen(db, org, "g1")
    sm = sessionmaker()
    lan1, lan2, binh, nv = await put(
        sm, org,
        {**msg("Giá thép cuộn?", sender="khach-lan", name="Chị Lan"), "occurred_at": ts(5)},
        {**msg("Có hàng không em?", sender="khach-lan", name="Chị Lan"), "occurred_at": ts(4)},
        {**msg("Báo giá giúp anh", sender="khach-binh", name="Anh Bình"), "occurred_at": ts(4)},
        {**msg("Anh sẽ gọi lại khách", sender="nhan-vien", name="Em Nam"), "occurred_at": ts(3)})
    await db.execute(text("UPDATE core.persons SET person_type = 'staff' WHERE id = :p"),
                     {"p": await _person_of(db, nv)})
    await db.commit()
    return {"org": org, "lan1": lan1, "lan2": lan2, "binh": binh, "nv": nv}


async def test_care_ignores_staff_messages(app, db) -> None:  # type: ignore[no-untyped-def]
    from gh.biz.people.service import PAIR_CTE

    w = await _care_world(db)
    df, dt_ = NOW - dt.timedelta(days=1), NOW + dt.timedelta(hours=1)
    rows = (await db.execute(text(PAIR_CTE + "SELECT id FROM paired"), {"o": w["org"], "df": df, "dt": dt_})).all()
    ids = {r.id for r in rows}
    assert w["nv"] not in ids                                         # tin của nhân viên không phải «tin cần trả lời»
    assert {w["lan1"], w["lan2"], w["binh"]} <= ids


# ─── P3-7 — thẻ «Khách chưa ai trả lời» đếm KHÁCH, không đếm tin ──────────────────────────────────────────────────────

async def test_care_unattended_counts_customers_not_messages(app, db, owner_api) -> None:  # type: ignore[no-untyped-def]
    await _care_world(db)
    body = (await owner_api.get("/care/response-times")).json()
    assert body["unattended"] == 2                    # 2 KHÁCH (Lan 2 tin, Bình 1 tin), không phải 3 tin


# ─── P3-8 — số «Cơ hội» của Hộp thư và Mặt tiền là MỘT con số ─────────────────────────────────────────────────────────

async def test_opportunity_count_is_the_same_in_inbox_and_owner_feed(app, db, owner_api) -> None:  # type: ignore[no-untyped-def]
    from tests.test_owner_v0155 import _org
    from tests.test_owner_v0159 import ago, unit

    org = await _org(db)
    for k, et in enumerate(["AskedPrice", "OfferedSupply", "RequestedPartnership", "RequestedSample", "SentQuotation",
                            "DealWon", "AskedStatus", "Complained"]):
        await unit(db, org, ago(hours=k + 1), et, f"Tin {et}")
    await db.commit()
    inbox = (await owner_api.get("/inbox")).json()["counts"]["opportunity"]
    feed_counts = (await owner_api.get("/owner/feed?view=opportunity")).json()["counts"]["opportunity"]
    assert inbox == feed_counts == 3, (inbox, feed_counts)


# ─── P3-10 — vai dưới Owner không đọc được OTP / mật khẩu ở Kho thô, Nhật ký lắng nghe ───────────────────────────────

OTP_TEXT = "Mã xác minh của bạn là 482913, mật khẩu wifi: abc12345xyz, gọi 0912 345 678"


def test_mask_text_hides_otp_and_passwords_below_owner_only() -> None:
    from gh.data.common import mask_text

    low = mask_text(OTP_TEXT, False) or ""
    assert "482913" not in low and "abc12345xyz" not in low and "0912 345 678" not in low
    assert "Mã xác minh" in low and "gọi" in low                        # phần còn lại của câu giữ nguyên
    assert mask_text(OTP_TEXT, True) == OTP_TEXT                         # Owner thấy nguyên văn
    assert mask_text("Còn 25 tấn thép, giá 14.500 đồng", False) == "Còn 25 tấn thép, giá 14.500 đồng"


async def test_raw_store_and_listen_log_hide_otp_from_auditor(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    from tests.test_rbac_api import login_as

    org = await org_id(db)
    await listen(db, org, "g1")
    [raw] = await put(sessionmaker(), org, msg(OTP_TEXT, sender="u1", name="Chị Lan"))
    aud = await login_as(owner_api.c, db, "auditor")
    for url in (f"/raw/{raw}", "/raw?since=all", "/listen-log?since=all&kind=all"):
        body = (await aud.get(url)).text
        assert "482913" not in body and "abc12345xyz" not in body, url
    assert "482913" in (await owner_api.get(f"/raw/{raw}")).text


# ─── P3-4 — chưa có bộ ghi lời hứa: không nói «không có lời hứa bị vỡ» ────────────────

def test_care_scenario_note_does_not_claim_no_broken_promises_while_untracked() -> None:
    from gh.biz.people.routes import _scenario_note

    note = _scenario_note("won", {"fast_pct": 80.0}, 0)
    assert "lời hứa" not in note and note.startswith("Kịch bản thắng")


def test_nothing_writes_biz_promises() -> None:
    """Điều kiện của cờ `PROMISES_TRACKED = False`: nếu sau này có mã ghi `biz.promises`, test này đỏ để nhớ bật lại
    cờ (máy chủ `gh.biz.queue.service` + web `src/lib/features.ts`) và gỡ Nợ #62."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "gh"
    writers = [str(p.relative_to(root)) for p in root.rglob("*.py")
               if re.search(r"INSERT\s+INTO\s+biz\.promises", p.read_text(), re.I)]
    assert writers == [], writers
