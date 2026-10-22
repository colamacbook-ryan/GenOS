"""v0.1.70 (dọn sót F029): `GET /failover-rules` có thêm hai luật việc nền, và CÂU CHỮ ĐÚNG với mã chạy thật.

- Luật hộp riêng: vai chạy Antigravity qua hộp (`placement`) mà hộp chưa chạy ⇒ bỏ qua Antigravity (`agy_policy.decide`
  từ chối, không bao giờ rơi về chạy trong tiến trình), chuyển nguồn kế tiếp.
- Luật Claude Code ở việc nền: `router.BACKGROUND_PURPOSES` bỏ qua Claude Code CLI trừ khi Owner cho phép; Antigravity
  không cần cho phép đó."""

from gh.providers import agy_policy, placement, router
from gh.system_api.routes import FAILOVER_RULES
from tests.conftest import Api

BOX_KEY = "hộp Antigravity"
BG_KEY = "việc nền"


def rules() -> dict[str, str]:
    return {r["key"]: r["value"] for r in FAILOVER_RULES}


async def test_api_lists_the_two_background_rules(owner_api: Api) -> None:
    got = {i["key"]: i["value"] for i in (await owner_api.get("/failover-rules")).json()}
    assert list(got)[:3] == ["hết hạn mức", "ngắt mạch", BOX_KEY]
    assert {BOX_KEY, BG_KEY} <= set(got) and "hết chuỗi" in got and "ngưỡng cảnh báo" in got
    assert all(isinstance(v, str) and v for v in got.values())


def test_box_rule_matches_placement_and_agy_policy() -> None:
    text = rules()[BOX_KEY]
    # Vai ghi trong câu đúng là vai qua hộp theo bảng placement …
    assert placement.box_required("core.reply") and placement.box_required("core.refinery")
    assert placement.box_required("core.gen", owner=False) and placement.box_required("agent:x", True)
    assert placement.box_required("core.briefing")      # Bản tin có tên khách / tiêu đề việc ⇒ qua hộp
    for phrase in ("soạn lại", "dịch nháp", "sàng lọc tin", "Bản tin Gen", "agent ngoại giao", "Gen của nhân viên"):
        assert phrase in text
    # … vai chạy trong tiến trình (Gen của Sếp, agent nội bộ) KHÔNG cần hộp, và câu nói đúng điều đó …
    assert not placement.box_required("core.gen") and not placement.box_required("agent:x")
    assert "chỉ Gen của Sếp và agent nội bộ không cần hộp" in text
    # … và hộp chưa chạy thì quyết định cuối đúng là "không dùng Antigravity", không rơi về tiến trình.
    for role in ("core.reply", "core.refinery"):
        for box in (agy_policy.BOX_DOWN, agy_policy.BOX_NOT_INSTALLED):
            verdict = agy_policy.decide(role, box=box)
            assert verdict.allowed is False and verdict.where == placement.BOX
        assert agy_policy.decide(role, box=agy_policy.BOX_OK).allowed is True
    assert agy_policy.decide("core.gen", box=agy_policy.BOX_DOWN).allowed is True   # vai trong tiến trình không cần hộp
    assert agy_policy.decide("core.briefing", box=agy_policy.BOX_DOWN).allowed is False   # Bản tin qua hộp
    assert "không" in text and "tiến trình" in text


def test_background_rule_matches_router() -> None:
    text = rules()[BG_KEY]
    assert router.BACKGROUND_CLI_KINDS_ALLOWED == ("claude_code_cli",)              # chỉ Claude Code cần Owner cho phép
    assert all(router.is_background(p) for p in ("refinery", "duty_decide", "gen.briefing"))
    assert not router.is_background("gen.chat")
    for phrase in ("sàng lọc tin", "trực việc", "Bản tin Gen", "Claude Code CLI", "Sếp đã cho phép"):
        assert phrase in text
    assert "Antigravity" in text and "không cần" in text                             # agy luôn chạy được việc nền
    assert [p.lower() for p in router.BACKGROUND_PURPOSE_LABELS] == ["sàng lọc tin", "trực việc (agent soạn nháp)",
                                                                       "bản tin gen"]
