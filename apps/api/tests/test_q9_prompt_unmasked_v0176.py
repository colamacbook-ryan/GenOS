# ruff: noqa: E501
"""Q9 (CNS-081): chữ chat tới model NGUYÊN VĂN, không che — STK/SĐT/OTP/email trong tin mẫu xuất hiện nguyên trong prompt ở từng điểm
của L3 (extract theo luồng, extract lô cũ, crosscheck d1/d2, tiền lọc triage); token hệ thống (`secrets`) thì KHÔNG."""

import uuid
from datetime import UTC, datetime
from typing import Any

from gh.biz.mem import contexts
from gh.biz.mem.profile_read import ProfileSnapshot
from gh.biz.mem.retrieve import ContextPack
from gh.gen.decider import Decision
from gh.providers.clients import Message
from gh.refinery import brain_contract as bc
from gh.refinery import crosscheck, extract, triage

THREAD = "g:6f1c7e2a-0000-4000-8000-000000000001"
NOW = datetime(2026, 10, 23, 3, 30, tzinfo=UTC)
SECRET = "sk-genos-SYSTEM-TOKEN-0123456789abcdef"
RAW = ("Chuyển giúp em 15 triệu vào STK 1234 5678 9012 3456 Vietcombank, gọi em số 0912 345 678, "
       "OTP em vừa nhận là 482913, mail em lan.nguyen@example.com, mật khẩu wifi: abc12345xyz")
LEAKS = ("1234 5678 9012 3456", "0912 345 678", "482913", "lan.nguyen@example.com", "abc12345xyz", "0988777666")


def _all_present(prompt: str) -> None:
    for s in LEAKS[:5]:
        assert s in prompt, s


def _win() -> extract.GroupWindow:
    ev = extract.GroupEvent("m_1", NOW, RAW, uuid.uuid4(), "Chị Lan 0988777666")
    return extract.GroupWindow(THREAD, [ev], now=NOW, group_name=f"Nhóm có token {SECRET}")


async def test_q9_prompt_unmasked_extract_group() -> None:
    pol = contexts.unconfirmed(THREAD)
    msgs = extract.build_group_messages(_win(), ContextPack(THREAD, pol, 0), ProfileSnapshot(uuid.uuid4(), 0, None, ()), pol,
                                        secrets=(SECRET,))
    prompt = "\n".join(m.content for m in msgs)
    _all_present(prompt)
    assert "0988777666" in prompt and "15 triệu" in prompt
    assert SECRET not in prompt and "[đã che]" in prompt          # bí mật HỆ THỐNG vẫn bị loại


def test_q9_prompt_unmasked_extract_legacy_batch() -> None:
    ev = [extract.EventIn("E1", "id1", "Nhóm 0912 345 678", "Chị Lan 0988777666", "2026-10-23T10:00", "text", RAW + " " + SECRET)]
    prompt = "\n".join(m.content for m in extract.build_messages(ev, [], ["AskedPrice"], secrets=(SECRET,)))
    _all_present(prompt)
    assert "0988777666" in prompt and SECRET not in prompt


class Spy:
    def __init__(self, reply: str) -> None:
        self.reply, self.calls = reply, []

    async def __call__(self, messages: list[Message]) -> extract.ModelReply:
        self.calls.append(messages)
        return extract.ModelReply(self.reply, "m", 1, 1)


def _unit() -> bc.Unit:
    return bc.Unit(unit_ref="u1", event_ref="e1", kind="rui_ro_lua_dao", summary="xin chuyển tiền",
                   quotes=[bc.Quote(message_id="m_1", field="body", text="Chuyển giúp em 15 triệu")])


async def test_q9_prompt_unmasked_crosscheck_d1_and_d2() -> None:
    ctx = crosscheck.RawContext([extract.GroupEvent("m_1", NOW, RAW + " " + SECRET, uuid.uuid4(), "Chị Lan 0988777666")])
    d1 = Spy('{"verdict": "yes", "reason": "ok"}')
    await crosscheck.verify(_unit(), "rui_ro_lua_dao", ctx, call=d1, secrets=(SECRET,))
    for calls in d1.calls:
        _all_present("\n".join(m.content for m in calls))
    d2 = Spy('{"gate": "lua_dao_dang_dien_ra", "flags_confirmed": [], "harm": {"what": "mất 15 triệu"}, "verdict": "agree", "reason": "x"}')
    await crosscheck.verify_strong(_unit(), "rui_ro_lua_dao", ctx, call=d2, secrets=(SECRET,))
    p2 = "\n".join(m.content for m in d2.calls[0])
    _all_present(p2)
    assert "0988777666" in p2 and SECRET not in p2
    assert SECRET not in "\n".join(m.content for calls in d1.calls for m in calls)


class SpyDecider:
    name = "jev"

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def classify(self, question: str, options: dict[str, str], context: str) -> Decision | None:
        self.questions.append(question)
        return None


async def test_q9_prompt_unmasked_triage_prefilter() -> None:
    dec: Any = SpyDecider()
    await triage.ask_jev_batch(dec, [(RAW, "AskedPrice")])
    assert dec.questions and all(s in dec.questions[0] for s in LEAKS[:5])
    assert len(dec.questions[0]) <= triage.JEV_TEXT_MAX
