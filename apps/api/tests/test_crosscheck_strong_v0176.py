# ruff: noqa: E501
"""(d2) `crosscheck.verify_strong` + `apply_d2`: BẤT ĐỐI XỨNG (chỉ nâng; luật cứng ⇒ giữ 🔴 và 1,00), FAIL-HIGH khi lỗi/quá 60 s/hết hạn mức."""

import asyncio
from typing import Any

import orjson

from gh.providers.clients import Message
from gh.refinery import crosscheck, extract
from tests.test_crosscheck_v0176 import Seq, evs, unit

AGREE = {"gate": "lua_dao_dang_dien_ra", "flags_confirmed": ["credentials_entered", "money_request"],
         "harm": {"what": "mất 15 triệu", "amount_text": "15 triệu", "irreversible": True}, "verdict": "agree", "reason": "đã nhập OTP"}


async def strong(reply: Any, **kw: Any) -> crosscheck.StrongResult:
    return await crosscheck.verify_strong(unit(), "rui_ro_lua_dao", crosscheck.RawContext(evs()), call=Seq(reply), **kw)


async def test_crosscheck_strong_agree_returns_flags_harm_and_model() -> None:
    r = await strong(AGREE)
    assert r["verdict"] == "agree" and r["error"] is None and r["model"] == "m-main"
    assert r["gate"] == "lua_dao_dang_dien_ra" and r["flags_confirmed"] == ["credentials_entered", "money_request"]
    assert r["harm_what"] == "mất 15 triệu" and r["harm"] == {"what": "mất 15 triệu", "amount_text": "15 triệu", "irreversible": True}


async def test_crosscheck_strong_prompt_is_specialised_and_hides_main_conclusion() -> None:
    f = Seq(AGREE)
    await crosscheck.verify_strong(unit(), "rui_ro_lua_dao", crosscheck.RawContext(evs()), call=f, secrets=())
    blob = "\n".join(m.content for m in f.calls[0])
    for must in ("credentials_entered", "money_request", "account_takeover", "emergency", "lua_dao_dang_dien_ra", "an_toan", "DẤU HIỆU lừa đảo"):
        assert must in blob, must
    for secret in ("TÓM-TẮT-BÍ-MẬT", "GIẢ-THUYẾT-BÍ-MẬT", "level_hint", "loi_hua_khach_sap_vo"):
        assert secret not in blob


async def test_crosscheck_strong_only_raises_safety_flags_and_valid_gates() -> None:
    r = await strong({"gate": "cong_bia", "flags_confirmed": ["emergency", "public", "nudged", "ongoing", "emergency"], "harm": {"what": "x"},
                      "verdict": "agree"})
    assert r["flags_confirmed"] == ["emergency"] and r["gate"] is None             # không nâng cờ ngoài 4 cờ an toàn; cổng bịa bị bỏ


async def test_crosscheck_strong_disagree_is_reported_not_hidden() -> None:
    r = await strong({"gate": None, "flags_confirmed": [], "harm": {"what": ""}, "verdict": "disagree", "reason": "chưa ai nhập"})
    assert r["verdict"] == "disagree" and r["harm_what"] is None and r["error"] is None


async def test_crosscheck_strong_failure_modes_are_fail_high_errors_never_raise() -> None:
    from gh.providers.router import ModelUnavailable
    for reply, code in (("không phải json", "bad_reply"), ({"verdict": "có lẽ"}, "bad_reply"), (RuntimeError("x"), "call_failed"),
                        (ModelUnavailable(["hết hạn mức"]), "model_unavailable")):
        r = await strong(reply)
        assert r["verdict"] == "error" and r["error"] == code and r["flags_confirmed"] == [] and r["harm"] is None and r["gate"] is None

    async def slow(messages: list[Message]) -> extract.ModelReply:
        await asyncio.sleep(5)
        return extract.ModelReply(orjson.dumps(AGREE).decode())

    r = await crosscheck.verify_strong(unit(), "rui_ro_lua_dao", None, call=slow, time_limit_s=0.05)
    assert r["verdict"] == "error" and r["error"] == "timeout"
    assert crosscheck.STRONG_TIMEOUT_S == 60.0                                    # spec: quá 60 s ⇒ giữ mức cao hơn


def test_crosscheck_strong_apply_d2_asymmetry_hard_rule_keeps_red() -> None:
    dis = {"verdict": "disagree", "flags_confirmed": [], "harm_what": None, "error": None}
    hard = crosscheck.apply_d2(dis, model_flags={"money_request": True}, hard_rule_hit=["credentials_entered"])
    assert hard.confidence_exist == 1.0 and hard.d2_disagree and not hard.may_demote_red_to_amber and hard.flags["credentials_entered"]
    assert "d2_disagree" in hard.reasons
    soft = crosscheck.apply_d2(dis, model_flags={})
    assert soft.confidence_exist == 0.5 and soft.may_demote_red_to_amber and soft.d2_disagree


def test_crosscheck_strong_apply_d2_agree_raises_and_unions_flags() -> None:
    agree = {"verdict": "agree", "flags_confirmed": ["credentials_entered"], "harm_what": "mất 3tr", "error": None}
    e = crosscheck.apply_d2(agree, model_flags={"nudged": True}, rule_flags={"money_request": True})
    assert e.confidence_exist == 1.0 and not e.may_demote_red_to_amber and not e.keep_higher
    assert e.flags == {"nudged": True, "money_request": True, "credentials_entered": True} and e.harm_what == "mất 3tr"


def test_crosscheck_strong_apply_d2_error_keeps_higher_and_ignores_d2_content() -> None:
    err = {"verdict": "error", "error": "timeout", "flags_confirmed": ["emergency"], "harm_what": "bịa"}
    e = crosscheck.apply_d2(err, model_flags={"money_request": False}, caller_confidence_exist=0.8)
    assert e.keep_higher and e.confidence_exist == 0.8 and not e.may_demote_red_to_amber and not e.d2_disagree
    assert e.flags == {"money_request": False} and e.harm_what is None                 # lỗi ⇒ không lấy cờ/harm từ (d2)
    none = crosscheck.apply_d2(None, model_flags={}, caller_confidence_exist=1.0)
    assert none.keep_higher and none.confidence_exist == 1.0
    # luật cứng + (d2) lỗi: vẫn 1,00 và cờ do luật kéo theo
    h = crosscheck.apply_d2(err, model_flags={}, hard_rule_hit=["emergency", "credentials_entered"], caller_confidence_exist=0.8)
    assert h.confidence_exist == 1.0 and h.flags == {"emergency": True, "credentials_entered": True}


def test_crosscheck_strong_d2_never_lowers_flags() -> None:
    e = crosscheck.apply_d2({"verdict": "disagree", "flags_confirmed": [], "error": None}, model_flags={"credentials_entered": True, "emergency": True})
    assert e.flags["credentials_entered"] and e.flags["emergency"]
