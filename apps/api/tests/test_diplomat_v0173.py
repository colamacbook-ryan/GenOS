# ruff: noqa: E501
"""v0.1.73 (việc D) — Phân vai NỘI BỘ vs NGOẠI GIAO: Gen chỉ ở Console; mọi kênh ngoài Console nói bằng agent ngoại giao Sếp chọn
(tên + giọng + vai), chưa chọn ⇒ «trợ lý». Giọng KHÔNG nới luật an toàn: bộ lọc `guard` vẫn chặn giá dù giọng «làm nũng»."""

import uuid
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker
from gh.gen import diplomat
from gh.gen.engine import TurnInput, system_prompt
from gh.owner_channel import prompts
from gh.owner_channel.basis import Basis
from gh.owner_channel.guard import GuardCtx, check
from gh.owner_cmd import core
from gh.providers.clients import Message
from tests.conftest import OWNER, Api
from tests.test_owner_cmd_v0171 import ctx_for, ocmd
from tests.test_telegram_commands_v0164 import Router, _reminder

VOICE = "vui vẻ, hay tấu hài, thích làm nũng và gọi Sếp là anh iu"


async def make_agent(name: str = "Heo", voice: str = VOICE, enabled: bool = True) -> uuid.UUID:
    async with admin_sessionmaker()() as s:
        org = (await s.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        aid = (await s.execute(text("""INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when, is_enabled)
            VALUES (:o, :n, 'Trợ lý ngoại giao của Sếp', 'custom', '{}'::jsonb, :v, 'khi được gọi', :e) RETURNING id"""),
                                 {"o": org, "n": name, "v": voice, "e": enabled})).scalar_one()
        await s.commit()
    return aid  # type: ignore[no-any-return]


async def choose(aid: uuid.UUID | None) -> None:
    async with admin_sessionmaker()() as s:
        org = (await s.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        await diplomat.save(s, org, aid)
        await s.commit()


def sys_of(router: Router) -> str:
    return str(router.calls[-1]["messages"][0].content)


async def test_unset_persona_is_neutral_and_never_says_gen_outside_console(owner_api: Api, redis: Any) -> None:
    router = Router("Em chưa biết ạ.")
    ctx = await ctx_for(owner_api, redis, router)
    await core.handle(ocmd("hoi", "tình hình sao?", via="zalo"), ctx)
    s = sys_of(router)
    assert "Bạn là trợ lý — trợ lý quản trị" in s and "Bạn là Gen" not in s and "Tên của bạn là" not in s
    assert ctx.persona == diplomat.NO_PERSONA
    assert core.help_text("zalo").startswith("Em là trợ lý.") and core.help_text("telegram").startswith("Em là trợ lý.")
    assert core.HELP.startswith("Em là trợ lý.") and core.GEN_OFF.startswith("Trợ lý đang tắt")


@pytest.mark.parametrize("via", ["zalo", "whatsapp", "telegram"])
async def test_chat_question_prompt_uses_diplomat_name_and_voice_not_gen(owner_api: Api, redis: Any, via: str) -> None:
    await choose(await make_agent())
    router = Router("Dạ anh iu ơi!")
    ctx = await ctx_for(owner_api, redis, router)
    await core.handle(ocmd("hoi", "tình hình sao?", via=via), ctx)
    s = sys_of(router)
    assert "Bạn là Heo" in s and "Tên của bạn là Heo." in s and VOICE in s and "Trợ lý ngoại giao của Sếp" in s
    assert "Bạn là Gen" not in s and diplomat.SAFETY in s
    assert "KHÔNG bao giờ xin mã PIN" in s and "Không bịa số liệu" in s             # luật an toàn của kênh vẫn còn nguyên


async def test_nhap_prompt_renames_gen_but_keeps_product_terms(owner_api: Api, redis: Any) -> None:
    await choose(await make_agent())
    router = Router(_reminder())
    ctx = await ctx_for(owner_api, redis, router)
    await core.handle(ocmd("nhap", "nhắc tôi gọi anh Nam", via="zalo"), ctx)
    s = sys_of(router)
    assert "Bạn là Heo — trợ lý quản trị." in s and "Bạn là Gen" not in s and "Tên của bạn là Heo." in s
    assert "Nguyên tắc: Heo ĐỌC" in s and "Gen-Harness" not in s.split("Bạn là Heo")[1][:60]
    assert "Gen nhớ" in s and "Gen-hub" in s                                          # tên tính năng / sản phẩm giữ nguyên


async def test_nhap_prompt_unset_is_neutral(owner_api: Api, redis: Any) -> None:
    router = Router(_reminder())
    ctx = await ctx_for(owner_api, redis, router)
    await core.handle(ocmd("nhap", "nhắc tôi gọi anh Nam", via="telegram"), ctx)
    s = sys_of(router)
    assert "Bạn là trợ lý — trợ lý quản trị." in s and "Bạn là Gen" not in s


async def test_console_gen_prompt_is_unchanged() -> None:
    from gh.auth import service
    from gh.auth.rbac import OWNER as OWNER_ROLE

    user = service.CurrentUser(id=uuid.uuid4(), org_id=uuid.uuid4(), email="a@b.c", display_name="Sếp", role_code=OWNER_ROLE,
                               role_name="Owner", role_id=uuid.uuid4(), team_id=None, session_id=uuid.uuid4(),
                               pin_verified_until=None, addressing={}, permissions={})
    p = system_prompt(user, TurnInput(turn_id=uuid.uuid4(), conversation_id=uuid.uuid4(), text="x", route="/", screen_key=None), [])
    assert p.startswith("Bạn là Gen — trợ lý quản trị trong Gen-Harness Console.") and "Heo" not in p


def test_fixed_texts_use_the_given_name() -> None:
    assert core.help_text("zalo", "Heo").startswith("Em là Heo.") and "Gen" not in core.help_text("zalo", "Heo").split("\n")[0]
    assert core.help_text("telegram", "Heo").startswith("Em là Heo.") and "Console bấm Xác nhận" in core.help_text("telegram", "Heo")
    assert core.gen_off("Heo").startswith("Heo đang tắt")


async def test_disabled_or_unknown_agent_falls_back_to_neutral(owner_api: Api) -> None:
    aid = await make_agent("Heo", enabled=False)
    await choose(aid)
    async with admin_sessionmaker()() as s:
        org = (await s.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        assert await diplomat.load(s, org) == diplomat.NO_PERSONA
        await diplomat.save(s, org, uuid.uuid4())
        assert (await diplomat.load(s, org)).configured is False


async def test_group_prompts_carry_the_persona_and_guard_still_blocks_price(owner_api: Api) -> None:
    aid = await make_agent()
    await choose(aid)
    async with admin_sessionmaker()() as s:
        org = (await s.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        block = (await diplomat.load(s, org)).block()
    sup = prompts.support_messages(bot_name="Heo", org_name="Cty", history="", question="giá bao nhiêu", canary="CNR-x", max_len=300,
                                   intro=False, persona=block)
    own = prompts.owner_reply_messages(bot_name="Heo", addr="Sếp", group_name="G", history="", question="?", units="", canary="CNR-x",
                                       persona=block)
    for m in (sup, own):
        assert isinstance(m[0], Message) and "Tên của bạn là Heo." in m[0].content and VOICE in m[0].content
        assert "KHÔNG" in m[0].content and "không nói về" in m[0].content
    # giọng «làm nũng» KHÔNG qua mặt bộ lọc: câu trả lời có giá / tiền vẫn bị chặn ở mọi cơ sở
    naughty = ["Dạ anh iu ơi, giá bên em chỉ 150k thôi à~", "Hihi báo giá cho anh iu nhé, 2 triệu thôi!", "Em cam kết giao đúng hẹn nha anh iu"]
    for basis in (Basis.SUPPORT, Basis.OWNER_TAG):
        for t in naughty:
            assert check(t, GuardCtx(basis=basis)).codes, t
    # không chọn agent ⇒ không có khối nhập vai (prompt cũ)
    plain = prompts.support_messages(bot_name="Trợ lý", org_name="Cty", history="", question="?", canary="CNR-x", max_len=300, intro=False)
    assert "Tên của bạn là" not in plain[0].content


async def test_group_reply_uses_diplomat_name_even_without_assigned_agent(owner_api: Api) -> None:
    from gh.db import sessionmaker
    from gh.owner_channel import group_reply

    async with admin_sessionmaker()() as s:
        org = (await s.execute(text("SELECT id FROM core.organizations"))).scalar_one()
    async with sessionmaker()() as db:
        await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org)})
        assert (await group_reply._bot_name(db, org, None))[0] == "Trợ lý"
    await choose(await make_agent())
    async with sessionmaker()() as db:
        await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org)})
        assert (await group_reply._bot_name(db, org, None))[0] == "Heo"
        assert "Giọng nói:" in await group_reply._persona_block(db, org)


# ─── API cấu hình ─────────────────────────────────────────────────────────────

async def test_api_get_put_needs_pin_owner_and_valid_agent(owner_api: Api) -> None:
    r = await owner_api.get("/agents/diplomat")
    assert r.status_code == 200 and r.json() == {"agent_id": None, "name": None, "neutral_name": "trợ lý"}
    aid = await make_agent()
    r = await owner_api.send("PUT", "/agents/diplomat", {"agent_id": str(aid)})
    assert r.status_code == 423, r.text                                              # đổi cấu hình agent cần mã PIN
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await owner_api.send("PUT", "/agents/diplomat", {"agent_id": str(uuid.uuid4())})
    assert r.status_code == 422
    off = await make_agent("Tắt", enabled=False)
    assert (await owner_api.send("PUT", "/agents/diplomat", {"agent_id": str(off)})).status_code == 422
    r = await owner_api.send("PUT", "/agents/diplomat", {"agent_id": str(aid)})
    assert r.status_code == 200 and r.json()["name"] == "Heo" and r.json()["agent_id"] == str(aid)
    assert (await owner_api.get("/agents/diplomat")).json()["name"] == "Heo"
    async with admin_sessionmaker()() as s:
        row = (await s.execute(text("SELECT actor_type, target_label FROM ops.action_log WHERE action = 'agent.diplomat_set'"))).one()
    assert row.actor_type == "user" and row.target_label == "Heo"
    r = await owner_api.send("PUT", "/agents/diplomat", {"agent_id": None})
    assert r.status_code == 200 and r.json()["agent_id"] is None
    assert orjson.loads(r.content)["name"] is None
