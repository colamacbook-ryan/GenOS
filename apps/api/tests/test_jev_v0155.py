"""v0.1.55 (G4) — Jev che dữ liệu + lọc J1/J2 + bật Jev 1 chạm + bộ 12 câu + value_summary.

- Mọi payload gửi Jev (J1 lọc tin, J3 ý định Gen, thử 12 câu, J2 lọc trước) KHÔNG chứa SĐT / email / số tài khoản
  (bắt payload bằng `httpx.MockTransport`);
- J2 (`gh.refinery.prefilter` + runner): trùng hẳn / quy tắc + Jev cùng rác / quy tắc khi chưa có Jev ⇒ bỏ qua (không
  xoá, đọc lại qua `GET /refinery/triage/skipped`); Jev một mình chấm rác ⇒ vẫn trích xuất; Jev lỗi ⇒ luồng cũ;
  `triage.prefilter = false` ⇒ không bỏ tin nào;
- `POST /jev/benchmark|enable`, `GET /jev/value-summary` (chỉ Owner; /enable cần PIN, không trả khoá, Action Log không
  chứa khoá).
Chỉ dùng Jev/router giả."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh import crypto
from gh.chassis import actionlog
from gh.chassis.masking import mask_for_prompt
from gh.db import sessionmaker
from gh.gen import decider as decmod
from gh.gen import jev, jev_bench
from gh.providers.router import KEY_AAD
from gh.refinery import jev_routes, prefilter, triage
from gh.refinery.runner import Refinery
from tests.conftest import Api, verify_pin
from tests.phase2 import FakeRouter, listen, msg, org_id, put, texts_in
from tests.test_rbac_api import login_as
from tests.test_triage import SPAM, _seed, _unit

PHONE = "0912345678"
EMAIL = "a@b.vn"
ACCOUNT = "123456789012"
PII = (PHONE, EMAIL, ACCOUNT)
KEY = "sk-or-v1-khoa-thu-nghiem-g4-0001"
OR_KEY = "sk-or-v1-khoa-openrouter-co-san-77"
PASTED = "sk-or-v1-khoa-dan-moi-g4-0002"
LAB = triage.JEV_OPTIONS
LONG_BUY = "Cần 3 container thép cuộn giao Bình Dương trong tháng này, báo giá giúp em nhé anh"
PROMO = "Anh ơi bên em có sản phẩm mới ra mắt, anh xem catalogue giúp em nhé, cảm ơn anh nhiều"


def chat(label: str, conf: float = 0.9) -> dict[str, Any]:
    return {"choices": [{"message": {"content": json.dumps({"choice": label, "confidence": conf})}}]}


def _user_part(body: dict[str, Any]) -> dict[str, Any]:
    """Phần người dùng của payload: {input, context, options} (chat OpenAI-like hoặc systemone)."""
    if "messages" in body:
        return json.loads(body["messages"][1]["content"])  # type: ignore[no-any-return]
    return body


class JevSpy:
    """Jev giả trên `httpx.MockTransport`: ghi MỌI payload gửi đi; `decide(input, options)` trả nhãn (None = lỗi)."""

    def __init__(self, decide: Callable[[str, list[str]], str | None] | None = None, *, fail: str | None = None):
        self.payloads: list[dict[str, Any]] = []
        self.decide = decide or (lambda _i, o: o[0])
        self.fail = fail                       # "http" | "timeout"
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.payloads.append(body)
        if self.fail == "timeout":
            raise httpx.ReadTimeout("chậm quá", request=request)
        if self.fail == "http":
            return httpx.Response(500, text="hỏng")
        user = _user_part(body)
        label = self.decide(user["input"], user["options"])
        if label is None:
            return httpx.Response(500, text="hỏng")
        if "messages" not in body:                      # đường TypeSafe trực tiếp (`/v1/systemone`)
            return httpx.Response(200, json={"choice": label, "confidence": 0.9})
        return httpx.Response(200, json=chat(label))

    @property
    def wire(self) -> str:
        return json.dumps(self.payloads, ensure_ascii=False)

    def inputs(self) -> list[str]:
        return [_user_part(p)["input"] for p in self.payloads]


def client_for(spy: JevSpy) -> jev.JevClient:
    return jev.JevClient("https://openrouter.ai/api/v1", KEY, transport=spy.transport)


def assert_clean(wire: str) -> None:
    for p in PII:
        assert p not in wire, p
    assert KEY not in wire


# ─── J-che: JevClient / J3 / J1 ──────────────────────────────────────────────

def test_preset_keeps_openrouter_defaults() -> None:
    assert jev.PRESET == {"endpoint": jev.DEFAULT_BASE_URL, "model": jev.DEFAULT_MODEL}
    assert jev.DEFAULT_BASE_URL == "https://openrouter.ai/api/v1" and jev.DEFAULT_MODEL == "typesafe/jev-1.13"


async def test_choose_masks_question_context_and_labels() -> None:
    spy = JevSpy(lambda _i, o: o[1])
    c = client_for(spy)
    q = f"Khách {PHONE} email {EMAIL} chuyển khoản {ACCOUNT} khoá {KEY} — hỏi giá"
    opts = [f"một {PHONE}", f"hai {EMAIL}", "ba"]
    got = await c.choose(q, opts, f"ngữ cảnh {PHONE} {ACCOUNT}")
    assert_clean(spy.wire)
    assert got.label == opts[1]                  # trả về nhãn GỐC, dù model chỉ thấy bản đã che
    # đường systemone cũng che
    s1 = JevSpy(lambda _i, o: o[0])
    c1 = jev.JevClient("https://api.typesafe.ai", KEY, transport=s1.transport)
    assert (await c1.choose(q, opts, f"ngữ cảnh {PHONE}")).label == opts[0]
    assert s1.payloads and "options" in s1.payloads[0]
    assert_clean(s1.wire)
    assert c.build_request(q, opts, f"x {ACCOUNT}")[1] is not None
    assert_clean(json.dumps(c.build_request(q, opts, f"x {ACCOUNT}"), ensure_ascii=False))
    # chữ không nhạy cảm giữ nguyên (ngày tháng, số ngắn)
    spy2 = JevSpy()
    await client_for(spy2).choose("Đơn 12 giao ngày 2026-10-10 lúc 9 giờ", ["a", "b"], "")
    assert "2026-10-10" in spy2.wire and "Đơn 12" in spy2.wire


async def test_j3_intent_and_filter_payloads_have_no_pii() -> None:
    spy = JevSpy(lambda _i, o: o[0])
    d = decmod.JevDecider(client_for(spy))
    got = await d.intent(f"Khách {PHONE} (email {EMAIL}, tk {ACCOUNT}) hỏi gì rồi?")
    assert got is not None and got.source == "jev"
    assert spy.payloads
    assert_clean(spy.wire)
    await d.classify(f"Giá thế nào {PHONE}", dict(LAB), f"ngữ cảnh {EMAIL}")
    assert_clean(spy.wire)


async def test_j1_ask_jev_sends_masked_text_only(app: Any, db: Any, owner_api: Api) -> None:
    org = await org_id(db)
    raw = f"Gọi {PHONE}, mail {EMAIL}, chuyển khoản {ACCOUNT} giúp em nhé, cần 3 container thép cuộn"
    uid = await _unit(db, org, raw)
    await db.commit()
    spy = JevSpy(lambda _i, _o: LAB["high"])
    n = await triage.mark_units(db, org, [uid], decider=decmod.JevDecider(client_for(spy)))
    assert n == 1 and spy.payloads
    assert_clean(spy.wire)
    mark = (await db.execute(text("SELECT source, is_spam FROM refinery.item_marks"))).one()
    assert mark.source == "jev" and not mark.is_spam
    # lớp 1 (triage) che trước khi giao cho Decider — kiểm bằng Decider giả ghi lại đúng thứ nhận được
    seen: list[str] = []

    class Rec:
        name = "jev"

        async def classify(self, question: str, options: dict[str, str], context: str) -> None:
            seen.append(question + context)

    got = await triage.ask_jev_batch(Rec(), [(raw, "AskedPrice")])  # type: ignore[arg-type]
    assert got == [None] and seen and all(p not in seen[0] for p in PII)


async def test_j1_three_consecutive_failures_stop_the_pass(app: Any, db: Any, owner_api: Api) -> None:
    spy = JevSpy(fail="http")
    d = decmod.JevDecider(client_for(spy))
    jobs = [(f"Tin số {i}: hỏi giá container thép cuộn giao Bình Dương", "AskedPrice") for i in range(12)]
    res = await triage.ask_jev_batch(d, jobs)
    assert res == [None] * 12
    assert len(spy.payloads) <= triage.JEV_MAX_FAILS + triage.JEV_PARALLEL      # ngắt cả lượt, không hỏi hết 12
    assert decmod.MIN_CONFIDENCE == 0.5 and decmod.TIMEOUT_S == 1.5


# ─── J2: prefilter.decide thuần ──────────────────────────────────────────────

LONG_A = "Bên em cần mua 3 container ván MDF E1 17mm giao Bình Dương trong tháng 10, báo giá giúp em nhé"
LONG_B = "Kho còn 20 tấn thép tấm dày 6 ly, anh chị cần thì liên hệ em để lấy hàng sớm nhé"


def test_decide_table() -> None:
    kw = {"dup_index": frozenset[bytes]()}
    R = prefilter.PrefilterResult
    # chưa có Jev (None): quy tắc rác ⇒ bỏ; quy tắc không ⇒ giữ
    assert prefilter.decide([SPAM, LONG_A], rules_spam=[True, False], jev_labels=None, **kw) == [
        R(True, "spam_rule_nojev", False), R()]
    # có Jev: cùng rác ⇒ bỏ; Jev một mình rác ⇒ chỉ hạ ưu tiên; quy tắc một mình rác + Jev nói không ⇒ giữ
    assert prefilter.decide([SPAM, LONG_A, LONG_B, "ok"], rules_spam=[True, False, True, False],
                            jev_labels=["spam", "spam", "high", "low"], **kw) == [
        R(True, "spam_rule_jev", False), R(False, None, True), R(), R()]
    # Jev lỗi (nhãn None) ⇒ thận trọng: KHÔNG bỏ dù quy tắc chấm rác; chỉ hạ ưu tiên
    assert prefilter.decide([SPAM, LONG_A], rules_spam=[True, False], jev_labels=[None, None], **kw) == [
        R(False, None, True), R()]
    with pytest.raises(ValueError):
        prefilter.decide(["a"], rules_spam=[], jev_labels=None, **kw)


def test_decide_exact_duplicates() -> None:
    R = prefilter.PrefilterResult
    key = prefilter.exact_key(LONG_A)
    assert key is not None
    # trùng với tin đã thấy trước lô, và lần thứ hai trong lô (lần đầu giữ) — dù Jev lỗi / không có Jev
    for labels in (None, [None, None, None]):
        got = prefilter.decide([LONG_B, LONG_A, LONG_A], rules_spam=[False] * 3, jev_labels=labels,
                               dup_index={key})
        assert got == [R(), R(True, "exact_dup", False), R(True, "exact_dup", False)]
    got = prefilter.decide([LONG_A, "  " + LONG_A.upper() + "!! ", LONG_B], rules_spam=[False] * 3, jev_labels=None,
                           dup_index=frozenset())
    assert [g.reason for g in got] == [None, "exact_dup", None]            # chuẩn hoá: hoa/thường, dấu, khoảng trắng
    # tin ngắn / chỉ biểu tượng không bao giờ là "trùng hẳn"
    short = prefilter.decide(["giá bao nhiêu?", "giá bao nhiêu?", "👍", "👍"], rules_spam=[False] * 4,
                             jev_labels=None, dup_index=frozenset())
    assert not any(r.skip for r in short)
    # trùng hẳn thắng quy tắc rác
    both = prefilter.decide([SPAM, SPAM], rules_spam=[True, True], jev_labels=["spam", "spam"],
                            dup_index=frozenset())
    assert [b.reason for b in both] == ["spam_rule_jev", "exact_dup"]


def test_decide_exact_dup_needs_same_sender_and_place_and_never_hits_tags() -> None:
    P = prefilter.PrefilterItem
    kw = {"rules_spam": [False] * 6, "jev_labels": None, "dup_index": frozenset()}
    a1, a2 = uuid.uuid4(), uuid.uuid4()
    g1, g2 = uuid.uuid4(), uuid.uuid4()
    got = prefilter.decide([P(LONG_A, a1, g1), P(LONG_A, a1, g1),          # cùng người, cùng nhóm ⇒ trùng hẳn
                            P(LONG_A, a2, g1),                              # người khác ⇒ giữ
                            P(LONG_A, a1, g2),                              # nhóm khác ⇒ giữ
                            P(LONG_A, None, g1),                            # không rõ người gửi ⇒ không bao giờ trùng
                            P(LONG_A, a1, g1, tagged=True)],                # tag trực tiếp ⇒ luôn giữ
                           **kw)
    assert [r.reason for r in got] == [None, "exact_dup", None, None, None, None]
    # khoá từ dấu item_marks cũng theo (băm, người, nhóm)
    key = prefilter.exact_key(P(LONG_A, a1, g1))
    assert key is not None and key[0] == prefilter.exact_key(LONG_A)[0]     # type: ignore[index]
    got2 = prefilter.decide([P(LONG_A, a1, g1), P(LONG_A, a2, g1)], rules_spam=[False] * 2, jev_labels=None,
                            dup_index={key})
    assert [r.reason for r in got2] == ["exact_dup", None]


def test_decide_exact_dup_in_batch_respects_the_short_window() -> None:
    """F-N1: cùng người gửi lặp y hệt SAU nhiều ngày là tín hiệu nhắc việc (chưa ai trả lời) ⇒ KHÔNG bỏ; lặp trong vài
    giờ (bấm gửi hai lần) mới là bản trùng. Thiếu thời điểm ⇒ giữ hành vi chặt cũ."""
    P = prefilter.PrefilterItem
    a, g = uuid.uuid4(), uuid.uuid4()
    t0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    kw = {"rules_spam": [False] * 4, "jev_labels": None, "dup_index": frozenset()}
    got = prefilter.decide([P(LONG_A, a, g, at=t0),
                            P(LONG_A, a, g, at=t0 + timedelta(minutes=2)),          # bấm gửi hai lần ⇒ trùng
                            P(LONG_A, a, g, at=t0 + timedelta(days=3)),             # nhắc lại sau 3 ngày ⇒ giữ
                            P(LONG_A, a, g, at=t0 + timedelta(days=3, minutes=1))],  # lại bấm hai lần ⇒ trùng
                           **kw)
    assert [r.reason for r in got] == [None, "exact_dup", None, "exact_dup"]
    got2 = prefilter.decide([P(LONG_A, a, g), P(LONG_A, a, g, at=t0)], rules_spam=[False] * 2, jev_labels=None,
                            dup_index=frozenset())
    assert [r.reason for r in got2] == [None, "exact_dup"]
    assert 0 < prefilter.EXACT_DUP_HOURS < 24                     # cửa sổ tính bằng giờ, không phải 14 ngày


def test_lower_last_keeps_order_and_drops_skipped() -> None:
    R = prefilter.PrefilterResult
    res = [R(), R(False, None, True), R(True, "exact_dup", False), R(), R(False, None, True)]
    assert prefilter.lower_last(["a", "b", "c", "d", "e"], res) == ["a", "d", "b", "e"]


def test_rule_spam_threshold_depends_on_jev() -> None:
    weak = "Xem catalogue tại www.abc.vn nhé, miễn phí vận chuyển"             # link + "mien phi" = 2 tín hiệu
    assert prefilter.rule_spam(weak, jev_present=True) and not prefilter.rule_spam(weak, jev_present=False)
    assert prefilter.rule_spam(SPAM, jev_present=False) and not prefilter.rule_spam(LONG_A, jev_present=True)


# ─── J2: runner ──────────────────────────────────────────────────────────────

def router_taking(seen: list[str]) -> FakeRouter:
    """Model giả: mọi tin chứa "container" → một đơn vị ý nghĩa; ghi lại thứ tự văn bản được gửi."""
    from tests.test_refinery import unit

    def reply(messages: Any) -> dict[str, Any]:
        units, noise = [], []
        for ref, body in texts_in(messages).items():
            seen.append(body)
            if "container" in body:
                units.append(unit(ref))
            else:
                noise.append(ref)
        return {"units": units, "noise": noise}

    return FakeRouter(reply)


async def add_jev_source(db: Any, org: uuid.UUID, key: str = KEY) -> uuid.UUID:
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                    VALUES (:o, 'system_one', 'Jev (System One)', :e, 99) RETURNING id"""),
                            {"o": org, "e": jev.PRESET["endpoint"]})).scalar_one()
    await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, :m)"),
                     {"p": pid, "m": jev.PRESET["model"]})
    await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4, rotation_order)
                             VALUES (:p, 'JEV-KEY-01', :s, :f, 0)"""),
                     {"p": pid, "s": crypto.encrypt(key.encode(), KEY_AAD), "f": key[-4:]})
    await db.commit()
    return pid  # type: ignore[no-any-return]


async def states_detail(db: Any) -> dict[str, dict[str, Any]]:
    rows = (await db.execute(text("""SELECT e.body_text, s.state, s.detail FROM refinery.event_state s
                                     JOIN raw.events e ON e.id = s.event_id"""))).all()
    return {r.body_text: {"state": r.state, **r.detail} for r in rows}


async def setup_listen(db: Any) -> uuid.UUID:
    org = await org_id(db)
    await listen(db, org, "g1")
    return org


async def test_jev_alone_spam_still_extracted_and_lowered(app: Any, db: Any, redis: Any, owner_api: Api) -> None:
    org = await setup_listen(db)
    await add_jev_source(db, org)
    sm = sessionmaker()
    spy = JevSpy(lambda i, _o: LAB["spam"] if "catalogue" in i else LAB["medium"])
    seen: list[str] = []
    promo_container = PROMO + ", có cả container hàng mẫu"          # Jev chấm rác, quy tắc KHÔNG ⇒ vẫn trích xuất
    await put(sm, org, msg(promo_container), msg(LONG_BUY, sender="u2"))
    st = await Refinery(sm, redis, router_taking(seen), jev_transport=spy.transport).run(org, "manual")  # type: ignore[arg-type]
    assert st.prefilter_skipped == 0 and st.clean == 2
    assert len(seen) == 2 and seen[-1] == promo_container            # tin hạ ưu tiên xếp CUỐI lô
    assert seen[0] == LONG_BUY
    assert (await db.execute(text("SELECT count(*) FROM clean.meaning_units"))).scalar() == 2
    assert_clean(spy.wire)


async def test_rules_and_jev_both_spam_skipped_and_readable(app: Any, db: Any, redis: Any, owner_api: Api,
                                                             client: httpx.AsyncClient) -> None:
    org = await setup_listen(db)
    await add_jev_source(db, org)
    sm = sessionmaker()
    spy = JevSpy(lambda i, _o: LAB["spam"] if "KHUYẾN MÃI" in i else LAB["high"])
    seen: list[str] = []
    spam = f"{SPAM} — liên hệ {PHONE}"
    await put(sm, org, msg(spam), msg(LONG_BUY, sender="u2"))
    st = await Refinery(sm, redis, router_taking(seen), jev_transport=spy.transport).run(org, "manual")  # type: ignore[arg-type]
    assert st.prefilter_skipped == 1 and st.noise == 1 and st.clean == 1 and st.processed == 2
    assert seen == [LONG_BUY]                                             # tin rác KHÔNG tới model
    d = (await states_detail(db))[spam]
    assert d["state"] == "discarded" and d["discarded_by"] == "prefilter" and d["reason"] == "spam_rule_jev"
    assert (await db.execute(text("SELECT count(*) FROM raw.events WHERE body_text = :t"), {"t": spam})).scalar() == 1
    assert_clean(spy.wire)                                                # Jev chỉ thấy bản đã che
    # đọc lại được (không xoá): Owner thấy nguyên văn, Operator thấy đã che số
    r = await owner_api.get("/refinery/triage/skipped")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["items"][0]["reason"] == "spam_rule_jev"
    assert body["items"][0]["text"] == spam and body["items"][0]["reason_text"].startswith("Rác")
    op = await login_as(client, db, "operator")
    masked = (await op.get("/refinery/triage/skipped")).json()["items"][0]["text"]
    assert PHONE not in masked and "678" in masked
    assert (await op.get("/refinery/triage/skipped", params={"limit": 0})).status_code == 422


async def test_no_jev_rule_spam_skipped_and_exact_dups_skipped(app: Any, db: Any, redis: Any,
                                                                owner_api: Api) -> None:
    org = await setup_listen(db)
    sm = sessionmaker()
    seen: list[str] = []
    await put(sm, org, msg(SPAM), msg(LONG_BUY), msg(LONG_BUY), msg(LONG_BUY, sender="u2"), msg(LONG_B, sender="u3"))
    st = await Refinery(sm, redis, router_taking(seen)).run(org, "manual")  # type: ignore[arg-type]
    d = await states_detail(db)
    assert d[SPAM]["reason"] == "spam_rule_nojev" and d[SPAM]["discarded_by"] == "prefilter"
    # chỉ lần gửi lặp CỦA CÙNG NGƯỜI ở cùng nhóm bị bỏ; người khác gửi cùng câu vẫn được trích xuất
    assert st.prefilter_skipped == 2 and seen.count(LONG_BUY) == 2 and LONG_B in seen
    reasons = {x["reason"] for x in (await owner_api.get("/refinery/triage/skipped")).json()["items"]}
    assert reasons == {"spam_rule_nojev", "exact_dup"}
    # trùng hẳn với tin ĐÃ xử lý ở lượt trước (qua dấu item_marks) cũng bị bỏ; tin của chính nó thì không
    await triage.run_org(sm, org)
    await put(sm, org, msg(LONG_BUY), msg(LONG_BUY, sender="u4"))
    seen.clear()
    st2 = await Refinery(sm, redis, router_taking(seen)).run(org, "manual")  # type: ignore[arg-type]
    assert st2.prefilter_skipped == 1 and seen == [LONG_BUY]               # u1 lặp lại bị bỏ; u4 (người mới) vẫn đi


async def test_exact_dup_against_marks_uses_the_short_window(app: Any, db: Any, redis: Any,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """F-N1: dấu item_marks chỉ tính là "đã thấy" trong EXACT_DUP_HOURS giờ; ngoài cửa sổ, người đó gửi lại y hệt vẫn
    được trích xuất (tín hiệu theo dõi)."""
    org = await setup_listen(db)
    sm = sessionmaker()
    seen: list[str] = []
    await put(sm, org, msg(LONG_BUY))
    await Refinery(sm, redis, router_taking(seen)).run(org, "manual")  # type: ignore[arg-type]
    await triage.run_org(sm, org)
    hashes = {prefilter.exact_key(LONG_BUY)[0]}                        # type: ignore[index]
    async with sm() as s:
        assert await prefilter.dup_index(s, org, hashes, [])           # trong cửa sổ mặc định: đã thấy
        assert await prefilter.dup_index(s, org, hashes, [], window_hours=0) == set()
    monkeypatch.setattr(prefilter, "EXACT_DUP_HOURS", 0.0)             # coi như tin đầu đã quá cũ
    await put(sm, org, msg(LONG_BUY))
    seen.clear()
    st = await Refinery(sm, redis, router_taking(seen)).run(org, "manual")  # type: ignore[arg-type]
    assert st.prefilter_skipped == 0 and seen == [LONG_BUY]


async def test_jev_error_keeps_old_flow_and_loses_nothing(app: Any, db: Any, redis: Any, owner_api: Api,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    org = await setup_listen(db)
    await add_jev_source(db, org)
    sm = sessionmaker()
    for fail in ("http", "timeout"):
        spy = JevSpy(fail=fail)
        seen: list[str] = []
        spam_c = f"{SPAM} container {fail} {PHONE}"             # quy tắc chấm rác nhưng Jev lỗi ⇒ KHÔNG bỏ
        await put(sm, org, msg(spam_c), msg(LONG_BUY + fail, sender="u2"), msg(LONG_BUY + fail, sender="u2"))
        st = await Refinery(sm, redis, router_taking(seen), jev_transport=spy.transport).run(org, "manual")  # type: ignore[arg-type]
        # prompt gửi model là bản đã che SĐT (v0.1.71, P2-3) nên so với bản đã che
        assert mask_for_prompt(spam_c) in seen and seen.count(LONG_BUY + fail) == 1       # chỉ trùng hẳn mới bị bỏ
        assert st.prefilter_skipped == 1 and st.status == "done"
        assert (await states_detail(db))[spam_c]["state"] == "clean"
        assert_clean(spy.wire)
    text_log = caplog.text
    assert KEY not in text_log and PHONE not in text_log
    # lỗi bất ngờ trong chính bước lọc trước ⇒ chạy như cũ, không mất tin
    await put(sm, org, msg(SPAM + " container x"), msg(LONG_BUY + "x", sender="u9"))
    seen = []
    r = Refinery(sm, redis, router_taking(seen))

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("hỏng lọc trước")

    r._prefilter_apply = boom                                    # type: ignore[method-assign]
    st = await r.run(org, "manual")
    assert st.prefilter_skipped == 0 and len(seen) == 2 and st.status == "done"


async def test_tagged_messages_and_other_senders_are_never_dropped(app: Any, db: Any, redis: Any,
                                                                   owner_api: Api) -> None:
    org = await setup_listen(db)
    sm = sessionmaker()
    seen: list[str] = []
    tagged_spam = SPAM + " container"
    await put(sm, org, msg(LONG_BUY), msg(LONG_BUY, mentions=True), msg(tagged_spam, mentions=True),
              msg(LONG_BUY, sender="u2"))
    st = await Refinery(sm, redis, router_taking(seen)).run(org, "manual")  # type: ignore[arg-type]
    assert st.prefilter_skipped == 0 and seen.count(LONG_BUY) == 3 and tagged_spam in seen


async def test_jev_too_slow_for_the_batch_budget_counts_as_jev_error(app: Any, db: Any, redis: Any, owner_api: Api,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from gh.refinery import runner

    monkeypatch.setattr(runner, "PREFILTER_JEV_BUDGET_S", 0.2)
    org = await setup_listen(db)
    await add_jev_source(db, org)
    sm = sessionmaker()

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json=chat(LAB["spam"]))

    seen: list[str] = []
    spam_c = SPAM + " container chậm"
    await put(sm, org, msg(spam_c), msg(LONG_BUY, sender="u2"), msg(LONG_BUY, sender="u2"))
    t0 = time.monotonic()
    st = await Refinery(sm, redis, router_taking(seen), jev_transport=httpx.MockTransport(slow)).run(org, "manual")  # type: ignore[arg-type]
    assert time.monotonic() - t0 < 1.8                       # không chờ hết các lượt gọi chậm
    assert spam_c in seen and st.prefilter_skipped == 1      # Jev "lỗi" ⇒ chỉ bỏ tin trùng hẳn, tin rác vẫn đi tiếp


async def test_prefilter_off_drops_nothing(app: Any, db: Any, redis: Any, owner_api: Api) -> None:
    org = await setup_listen(db)
    await triage.save_settings(db, org, {**triage.DEFAULTS, "prefilter": False})
    await db.commit()
    sm = sessionmaker()
    seen: list[str] = []
    spam_c = SPAM + " container"
    await put(sm, org, msg(spam_c), msg(LONG_BUY), msg(LONG_BUY))
    st = await Refinery(sm, redis, router_taking(seen)).run(org, "manual")  # type: ignore[arg-type]
    assert st.prefilter_skipped == 0 and len(seen) == 3 and spam_c in seen
    assert (await owner_api.get("/refinery/triage/skipped")).json() == {"items": [], "total": 0, "days": 30}


async def test_settings_patch_prefilter(app: Any, db: Any, owner_api: Api) -> None:
    assert (await owner_api.get("/refinery/triage/settings")).json()["prefilter"] is True
    r = await owner_api.send("PATCH", "/refinery/triage/settings", {"prefilter": False})
    assert r.status_code == 200 and r.json() == {**triage.DEFAULTS, "prefilter": False}
    row = (await db.execute(text("""SELECT detail FROM ops.action_log
                                    WHERE action = 'refinery.triage_settings_changed'"""))).scalar_one()
    assert row["after"] == {"prefilter": False} and row["before"] == {"prefilter": True}
    assert (await owner_api.get("/refinery/triage/summary")).json()["prefilter"] is False


# ─── value_summary ───────────────────────────────────────────────────────────

async def test_value_summary_counts_marks_prefilter_and_out_of_scope(app: Any, db: Any, redis: Any,
                                                                     owner_api: Api, client: httpx.AsyncClient) -> None:
    jev_api = _mounted(app, owner_api)
    w = await _seed(db)                                       # 6 mục: 1 trùng hẳn + 1 trùng gần + 1 rác
    org = w["org"]
    sm = sessionmaker()
    await triage.run_org(sm, org)
    assert await triage.value_summary(db, org) == {"filtered": 3, "spam_blocked": 1, "calls_saved": 0, "jev_on": False}
    await listen(db, org, "g1")
    spam_a = "VAY TIỀN NHANH!!! Giải ngân ngay, click http://vay.top hoặc www.vay.xyz đăng ký ngay"
    spam_b = "TRÚNG THƯỞNG!!! Nhận quà miễn phí, click http://qua.top và www.qua.xyz để nhận ngay"
    await put(sm, org, msg(spam_a), msg(spam_b), msg(LONG_BUY), msg(LONG_BUY))
    st = await Refinery(sm, redis, router_taking([])).run(org, "manual")  # type: ignore[arg-type]
    assert st.prefilter_skipped == 3                          # 2 rác (quy tắc, chưa có Jev) + 1 trùng hẳn
    for value in ("out_of_scope", "out_of_scope", "data"):
        await actionlog.record(db, org_id=org, actor_type="agent", actor_id="gen", action="gen.decide",
                               detail={"value": value, "decider": "jev"})
    await actionlog.record(db, org_id=org, actor_type="agent", actor_id="gen", action="gen.other",
                           detail={"value": "out_of_scope"})
    await db.commit()
    want = {"filtered": 6, "spam_blocked": 3, "calls_saved": 5, "jev_on": False}
    assert await triage.value_summary(db, org, days=7) == want
    r = await jev_api.get("/jev/value-summary")
    assert r.status_code == 200 and r.json() == want
    await add_jev_source(db, org)
    assert (await triage.value_summary(db, org))["jev_on"] is True
    # ngoài cửa sổ days: không đếm
    await db.execute(text("UPDATE refinery.event_state SET updated_at = now() - interval '30 days'"))
    await db.commit()
    assert (await triage.value_summary(db, org, days=7))["calls_saved"] == 2
    assert (await jev_api.get("/jev/value-summary", params={"days": 0})).status_code == 422
    for role in ("manager", "operator"):
        assert (await (await login_as(client, db, role)).get("/jev/value-summary")).status_code == 403


# ─── bộ 12 câu ───────────────────────────────────────────────────────────────

def test_bench_items_fixed_and_synthetic() -> None:
    items = jev_bench.BENCH_ITEMS
    assert len(items) == 12 and sum(i.kind == "intent" for i in items) == 6
    assert all(i.expected in decmod.INTENTS for i in items if i.kind == "intent")
    assert all(i.expected in triage.JEV_OPTIONS for i in items if i.kind == "filter")
    assert {i.expected for i in items if i.kind == "filter"} == set(triage.JEV_OPTIONS)
    blob = " ".join(i.question for i in items)
    assert not any(p in blob for p in PII) and "@" not in blob
    assert len({i.question for i in items}) == 12


async def test_benchmark_payloads_are_masked_even_for_pii_items() -> None:
    spy = JevSpy(lambda _i, o: o[0])
    item = jev_bench.BenchItem("intent", f"Khách {PHONE} email {EMAIL} tk {ACCOUNT} hỏi gì?", "data")
    flt = jev_bench.BenchItem("filter", f"Gọi {PHONE} mail {EMAIL} chuyển {ACCOUNT}", "high")
    d = decmod.JevDecider(client_for(spy))
    out = await jev_bench.run(d, filter_options=dict(LAB), filter_context=triage.jev_context("x"), items=(item, flt))
    assert out["total"] == 2 and len(spy.payloads) == 2
    assert_clean(spy.wire)


def oracle(wrong_for: tuple[str, ...] = ()) -> Callable[[str, list[str]], str | None]:
    """Jev giả luôn trả đúng nhãn kỳ vọng, trừ câu chứa chuỗi trong `wrong_for`."""
    expect = {i.question: i for i in jev_bench.BENCH_ITEMS}

    def f(question: str, options: list[str]) -> str | None:
        it = expect[question]
        labels = decmod.INTENTS if it.kind == "intent" else LAB
        want = labels[it.expected]
        if any(w in question for w in wrong_for):
            return next(o for o in options if o != want)
        return want

    return f


def _mounted(app: Any, api: Api) -> Api:
    """Router /jev phải được `gh/app.py` gắn sẵn (prefix /api/v1)."""
    assert any(getattr(r, "original_router", None) is jev_routes.router
               or getattr(r, "path", "") == "/api/v1/jev/enable" for r in app.routes)
    return api


async def test_benchmark_without_key_is_409_friendly(app: Any, db: Any, owner_api: Api) -> None:
    api = _mounted(app, owner_api)
    r = await api.send("POST", "/jev/benchmark")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "JEV_KEY_MISSING" and body["title"] == "Chưa có khoá OpenRouter cho Jev"
    assert body["detail"] is None                                                   # chuỗi thân thiện nằm ở title
    assert isinstance(body["reasons"], list) and all(isinstance(r, str) and r for r in body["reasons"])  # kỹ thuật
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_benchmark_with_fake_key_runs_12_sequentially(app: Any, db: Any, owner_api: Api,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = _mounted(app, owner_api)
    org = await org_id(db)
    await add_jev_source(db, org)
    spy = JevSpy(oracle(wrong_for=("Python", "ok em nhé")))
    app.state.model_router.transport = spy.transport
    r = await api.send("POST", "/jev/benchmark")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["total"] == 12 and out["correct"] == 10 and len(out["items"]) == 12
    assert isinstance(out["avg_latency_ms"], int) and out["avg_latency_ms"] >= 0
    bad = [i for i in out["items"] if not i["ok"]]
    assert {b["question"].split()[0] for b in bad} == {"Viết", "ok"}
    assert all(set(i) >= {"question", "expected", "got", "ok", "latency_ms"} for i in out["items"])
    assert all(isinstance(i["got"], str) and isinstance(i["expected"], str) for i in out["items"])
    assert spy.inputs() == [i.question for i in jev_bench.BENCH_ITEMS]                 # tuần tự, đúng thứ tự
    assert_clean(spy.wire)
    assert KEY not in r.text and KEY not in caplog.text
    row = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'jev.benchmark'"))).scalar_one()
    assert row == {"total": 12, "correct": 10, "avg_latency_ms": out["avg_latency_ms"]}


async def test_benchmark_errors_are_per_item_strings(app: Any, db: Any, owner_api: Api) -> None:
    api = _mounted(app, owner_api)
    await add_jev_source(db, await org_id(db))
    app.state.model_router.transport = JevSpy(fail="http").transport
    out = (await api.send("POST", "/jev/benchmark")).json()
    assert out["total"] == 12 and out["correct"] == 0 and out["avg_latency_ms"] is None
    assert all(isinstance(i["error_text"], str) and i["error_text"] and i["got"] is None for i in out["items"])
    assert "[object" not in json.dumps(out)


async def test_benchmark_and_enable_are_owner_only(app: Any, db: Any, owner_api: Api,
                                                   client: httpx.AsyncClient) -> None:
    _mounted(app, owner_api)
    await add_jev_source(db, await org_id(db))
    app.state.model_router.transport = JevSpy().transport
    for role in ("manager", "operator", "agent_staff", "auditor"):
        staff = await login_as(client, db, role)
        assert (await staff.send("POST", "/jev/benchmark")).status_code == 403
        assert (await staff.send("POST", "/jev/enable", {"key": PASTED})).status_code == 403


# ─── bật Jev 1 chạm ──────────────────────────────────────────────────────────

async def make_openrouter(api: Api, key: str = OR_KEY) -> None:
    r = await api.send("POST", "/providers", {"kind": "openai_compat", "name": "OpenRouter",
                                              "endpoint": "https://openrouter.ai/api/v1", "keys": [key]})
    assert r.status_code == 201, r.text


async def jev_keys(db: Any) -> list[Any]:
    return list((await db.execute(text("""SELECT k.secret_enc, k.last4, k.label FROM agent.provider_keys k
                                          JOIN agent.providers p ON p.id = k.provider_id
                                          WHERE p.kind = 'system_one' ORDER BY k.rotation_order"""))).all())


async def test_enable_requires_pin_and_a_key(app: Any, db: Any, owner_api: Api) -> None:
    api = _mounted(app, owner_api)
    r = await api.send("POST", "/jev/enable", {"key": PASTED})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"           # giữ rào PIN của việc tạo nguồn
    await verify_pin(api)
    for body, code in (({}, "JEV_KEY_MISSING"), ({"use_existing_openrouter": True}, "JEV_KEY_MISSING")):
        r = await api.send("POST", "/jev/enable", body)
        assert r.status_code == 409 and r.json()["code"] == code, r.text
    assert (await api.send("POST", "/jev/enable", {"key": "ngan"})).status_code == 422
    assert (await api.send("POST", "/jev/enable", {"key": PASTED, "use_existing_openrouter": True})).status_code == 422
    assert (await db.execute(text("SELECT count(*) FROM agent.providers WHERE kind = 'system_one'"))).scalar() == 0


async def test_enable_with_existing_openrouter_key_never_returns_key(app: Any, db: Any, owner_api: Api,
                                                                      caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = _mounted(app, owner_api)
    await verify_pin(api)
    await make_openrouter(api)
    r = await api.send("POST", "/jev/enable", {"use_existing_openrouter": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] is True and out["key_source"] == "existing_openrouter"
    assert out["endpoint"] == jev.PRESET["endpoint"] and out["model"] == jev.PRESET["model"]
    assert OR_KEY not in r.text and "secret" not in r.text
    (k,) = await jev_keys(db)
    src = (await db.execute(text("""SELECT k.secret_enc FROM agent.provider_keys k JOIN agent.providers p
                                    ON p.id = k.provider_id WHERE p.kind = 'openai_compat'"""))).scalar_one()
    assert bytes(k.secret_enc) == bytes(src)                 # chép khoá ĐÃ MÃ HÓA, phía máy chủ
    assert crypto.decrypt(bytes(k.secret_enc), KEY_AAD).decode() == OR_KEY
    p = (await db.execute(text("SELECT endpoint, is_enabled FROM agent.providers WHERE kind = 'system_one'"))).one()
    assert (p.endpoint, p.is_enabled) == (jev.PRESET["endpoint"], True)
    model = (await db.execute(text("""SELECT m.model_name FROM agent.models m JOIN agent.providers p
                                      ON p.id = m.provider_id WHERE p.kind = 'system_one'"""))).scalar_one()
    assert model == jev.PRESET["model"]
    # Action Log: có 'jev.enable', KHÔNG chứa khoá (kể cả 4 ký tự cuối) ở bất cứ đâu
    detail = (await db.execute(text("SELECT detail::text FROM ops.action_log WHERE action='jev.enable'"))).scalar_one()
    assert "existing_openrouter" in detail and OR_KEY not in detail and OR_KEY[-4:] not in detail
    everything = (await db.execute(text("SELECT string_agg(detail::text || coalesce(target_label, ''), ' ') "
                                        "FROM ops.action_log"))).scalar_one()
    assert OR_KEY not in everything and OR_KEY not in caplog.text
    assert OR_KEY not in (await api.get("/providers")).text
    # bấm lại: idempotent, không nhân đôi khoá / nguồn
    again = await api.send("POST", "/jev/enable", {"use_existing_openrouter": True})
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["provider_id"] == out["provider_id"]
    assert len(await jev_keys(db)) == 1
    # nguồn Jev chạy được: thử 12 câu
    app.state.model_router.transport = JevSpy(oracle()).transport
    bench = (await api.send("POST", "/jev/benchmark")).json()
    assert bench["correct"] == 12


async def test_enable_with_pasted_key_and_reenable(app: Any, db: Any, owner_api: Api) -> None:
    api = _mounted(app, owner_api)
    await verify_pin(api)
    r = await api.send("POST", "/jev/enable", {"key": PASTED})
    assert r.status_code == 200 and r.json()["key_source"] == "pasted" and PASTED not in r.text
    (k,) = await jev_keys(db)
    assert crypto.decrypt(bytes(k.secret_enc), KEY_AAD).decode() == PASTED and k.last4 == PASTED[-4:]
    logs = (await db.execute(text("SELECT string_agg(detail::text, ' ') FROM ops.action_log"))).scalar_one()
    assert PASTED not in logs
    # tắt nguồn rồi bật lại không cần dán khoá nữa (khoá cũ còn)
    await db.execute(text("UPDATE agent.providers SET is_enabled = false WHERE kind = 'system_one'"))
    await db.commit()
    r2 = await api.send("POST", "/jev/enable", {})
    assert r2.status_code == 200 and r2.json()["key_source"] == "kept" and r2.json()["created"] is False
    assert (await db.execute(text("SELECT is_enabled FROM agent.providers WHERE kind = 'system_one'"))).scalar() is True
    assert (await api.send("POST", "/jev/enable", {"key": PASTED})).status_code == 200
    assert len(await jev_keys(db)) == 1                               # cùng khoá dán lại: không nhân đôi
    # khoá dán KHÁC được thêm (xoay vòng)
    assert (await api.send("POST", "/jev/enable", {"key": PASTED + "-b"})).status_code == 200
    assert len(await jev_keys(db)) == 2


def test_key_missing_error_shape() -> None:
    e = jev_routes.key_missing()
    assert (e.status, e.code, e.title) == (409, "JEV_KEY_MISSING", "Chưa có khoá OpenRouter cho Jev")
    assert e.detail is None and "system_one" in e.extra["reasons"][0]
