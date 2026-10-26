# ruff: noqa: E501, F811
"""v0.1.76 — Che thông tin đăng nhập (Q9) KHÔNG được phá UUID trong kết quả tool của Owner.

Gốc test đỏ chập chờn `test_natural_group_message_goes_through_outbound_ok_rule` (CI run 40265954261): id nhóm UUIDv7
`01a17668-4746-7979-…` có đoạn `17668-4746-7979` = 13 chữ số qua Luhn ⇒ `mask_credentials` che thành `[thẻ …7979]`; model
không thấy id thật ⇒ đề xuất `draft_message` bị chặn «id không có trong kết quả tool» ⇒ Sếp nhận câu trả lời lạc. UUID sinh theo
giờ + ngẫu nhiên nên chỉ vài % số lần chạy dính. Test dưới dùng ĐÚNG id của lần đỏ để tái hiện chắc chắn."""

from typing import Any

from sqlalchemy import text

from gh.gen import tools
from gh.refinery.verify import mask_credentials
from tests.test_chat_confirm_v0173 import Chat, code_in
from tests.test_gen_chat_v0174 import chat, tool  # noqa: F401 — fixture `chat` dùng chung
from tests.test_p3_core import world  # noqa: F401 — fixture `world` dùng chung
from tests.test_telegram_commands_v0164 import Router

CI_GID = "01a17668-4746-7979-a9c2-e927f8c0f9aa"     # id nhóm của lần đỏ trên CI
CARD = "4111 1111 1111 1111"


def test_mask_credentials_keeps_uuid_but_still_masks_real_card() -> None:
    s = f"nhóm {CI_GID} — thẻ {CARD}"
    out = mask_credentials(s)
    assert out is not None and CI_GID in out, out
    assert CARD not in out and "[thẻ …1111]" in out, out
    # UUID chữ hoa / không có chữ cái nào cũng giữ nguyên
    for u in (CI_GID.upper(), "12345678-1234-7234-8234-123456789012"):
        assert mask_credentials(u) == u


def test_card_adjacent_to_uuid_is_still_masked() -> None:
    """Security review: không được bỏ qua cả cụm khớp `_CARD_RE` chỉ vì chồng lên UUID — thẻ thật đứng sát UUID vẫn phải bị che."""
    for s in (f"{CARD} {CI_GID}", f"{CI_GID} {CARD}"):
        out = mask_credentials(s)
        assert out is not None and "4111" not in out.replace(CI_GID, "") and CI_GID in out, (s, out)
    # dính liền bằng gạch nối: chuỗi không còn là UUID chuẩn ⇒ che (có thể che cả cụm) — chỉ cần số thẻ không lộ
    for s in (f"4111111111111111-{CI_GID}", f"{CI_GID}-4111111111111111"):
        out = mask_credentials(s)
        assert out is not None and "4111111111111111" not in out, (s, out)


def test_owner_tool_scrub_keeps_group_id() -> None:
    data = {"items": [{"id": CI_GID, "name": "Nhóm g1", "channel": "zalo"}], "total": 1}
    assert tools.scrub_credentials(data)["items"][0]["id"] == CI_GID


async def test_group_with_card_like_uuid_still_gets_proposal(chat: Chat, world: dict[str, Any], db: Any) -> None:
    # nhóm thứ hai sao từ g1, mang ĐÚNG id của lần đỏ (không đổi id g1: đã có bảng con tham chiếu)
    await db.execute(text("""INSERT INTO core.groups SELECT (jsonb_populate_record(NULL::core.groups, to_jsonb(g)
                                 || jsonb_build_object('id', CAST(:n AS text), 'external_id', 'g2', 'name', 'Nhóm g2', 'code', 'GRP-ZL-0999'))).*
                             FROM core.groups g WHERE g.id = :o"""), {"n": CI_GID, "o": world["group"]})
    await db.commit()
    propose = {"steps": [{"kind": "propose", "proposal": {"type": "draft_message", "fields": {
        "title": "Báo lịch", "text": "Chiều nay 3h họp nhé cả nhà", "subject": {"type": "group", "id": CI_GID}}}}]}
    router = Router(tool("group.list", q=""), propose)
    reply = await chat.say("soạn tin cho nhóm g1 báo chiều nay 3h họp", router)
    [obs] = [str(m.content) for m in router.calls[1]["messages"] if "[kết quả group.list]" in str(m.content)]
    assert CI_GID in obs and "[thẻ" not in obs, obs        # model thấy id thật, không phải «01a[thẻ …7979]-…»
    code = code_in(reply)
    assert "GỬI TIN" in reply and f"OK {code}" in reply
