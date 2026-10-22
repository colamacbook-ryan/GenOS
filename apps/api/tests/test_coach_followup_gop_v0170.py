"""v0.1.70 (dọn sót v0.1.68, F031 / F138): MỘT cách đếm việc thiết lập tuỳ chọn.

- Các việc P3 `followup.5`–`followup.10` của Gen KHÔNG đứng riêng trên thẻ "Hôm nay của Sếp" nữa: gộp thành MỘT việc
  `followup` "Việc thiết lập tiếp" (đếm đúng như thẻ cùng tên ở Tổng quan: mọi việc tuỳ chọn 5–11, 13, 14 chưa xong),
  đích làm sáng = chính thẻ đó (`overview.setup_followup`).
- Nút "Để sau 7 ngày" của thẻ (`card:setup_followup`) ẩn luôn các việc P3 của Gen (mọi mức P0/P1/P2 giữ nguyên).
- Tên "Nối Gen-hub" thống nhất ở ba nơi: việc của Gen, dòng ở Việc Sếp cần làm, việc thiết lập 14.
"""

from datetime import timedelta
from typing import Any

from gh.boss_checks import service as boss_service
from gh.gen.coach import engine
from gh.gen.coach import signals as sg
from gh.gen.coach.engine import Item
from gh.setup import routes as setup_routes
from tests.conftest import Api
from tests.test_gen_coach_v0154 import (  # noqa: F401  (fixture: fake_content tự dùng; clock, sigbox dùng theo tên)
    T0,
    clock,
    fake_content,
    keys_of,
    lesson_ids,
    mk_sig,
    post_item,
    sigbox,
    today,
    today_of,
    todo_keys,
)

FOLLOW = {f"followup.{n}.done": False for n in (5, 6, 7, 8, 9, 10, 11, 13, 14)}
CARD = "card:setup_followup"


def card_snoozed(days: int = 7) -> dict[str, Item]:
    return {CARD: Item(CARD, "snoozed", T0 + timedelta(days=days), 1, T0, T0)}


def by_key(plan: engine.Plan, key: str) -> dict[str, Any]:
    return next(t for t in plan.payload["todos"] if t["key"] == key)


def test_followups_5_to_10_merge_into_one_todo_counted_like_the_card() -> None:
    p = today_of(mk_sig(state=FOLLOW))
    assert keys_of(p) == ["followup"]
    t = by_key(p, "followup")
    assert t["level"] == "P3" and t["can_dismiss"] is True and t["title"] == "Việc thiết lập tiếp"
    assert t["target"] == "overview.setup_followup" and "link" not in t
    assert "9 việc" in t["why"] and "không bắt buộc" in t["why"]       # 5–11, 13, 14 — đúng số của thẻ ở Tổng quan
    assert t["dismiss_warning"] == sg.DISMISS_WARNINGS["followup"]
    only_five = today_of(mk_sig(state={"followup.5.done": False}))
    assert keys_of(only_five) == ["followup"] and "1 việc" in by_key(only_five, "followup")["why"]
    assert keys_of(today_of(mk_sig())) == []                           # xong hết ⇒ không còn việc nào


def test_merged_followup_takes_one_slot_of_three() -> None:
    sig = mk_sig(state={**FOLLOW, "boss.ai.done": False, "backup.scheduled": False})
    assert keys_of(today_of(sig)) == ["boss.ai", "backup.unset", "followup"]


def test_dismissed_members_leave_the_merge_and_dismissing_the_merge_hides_it() -> None:
    sig = mk_sig(state=FOLLOW)
    all_dismissed = {f"todo:followup.{n}": Item(f"todo:followup.{n}", "dismissed", None, 1, T0, T0)
                     for n in (5, 6, 7, 8, 9, 10)}
    assert keys_of(today_of(sig, items=all_dismissed)) == []           # 11/13/14 chưa bao giờ là việc của Gen
    some = {k: v for k, v in all_dismissed.items() if k != "todo:followup.8"}
    assert keys_of(today_of(sig, items=some)) == ["followup"]
    merged = {"todo:followup": Item("todo:followup", "dismissed", None, 1, T0, T0)}
    assert keys_of(today_of(sig, items=merged)) == []
    snoozed = {"todo:followup": Item("todo:followup", "snoozed", T0 + timedelta(days=1), 1, T0, T0)}
    assert keys_of(today_of(sig, items=snoozed)) == []
    assert keys_of(today_of(sig, items=snoozed, now=T0 + timedelta(days=2))) == ["followup"]


def test_card_snooze_hides_every_p3_todo_but_not_p0_p1_p2() -> None:
    state = {**FOLLOW, "boss.hub.done": False, "staff.active": True, "boss.remote.done": False}
    assert keys_of(today_of(mk_sig(state=state))) == ["boss.hub", "boss.remote", "followup"]
    assert keys_of(today_of(mk_sig(state=state), items=card_snoozed())) == []
    busy = mk_sig(state={**state, "backup.scheduled": False}, drafts=2)
    assert keys_of(today_of(busy, items=card_snoozed())) == ["backup.unset", "drafts.pending"]
    # hết hạn hoãn ⇒ việc P3 hiện lại; thẻ chỉ "snooze" mới ẩn (restore / hàng khác thì không)
    assert keys_of(today_of(mk_sig(state=state), items=card_snoozed(), now=T0 + timedelta(days=8))) == [
        "boss.hub", "boss.remote", "followup"]


def test_guide_lessons_of_merged_members_are_dropped_while_the_merge_shows() -> None:
    old = T0 - timedelta(days=5)
    done = {f"lesson:{x}": Item(f"lesson:{x}", "understood", None, 1, old, old)
            for x in ("N01", "N02", "N03", "N04", "N05")}
    p = today_of(mk_sig(state=FOLLOW), items=done)
    assert keys_of(p) == ["followup"] and lesson_ids(p) == "G11"       # G05–G10 bị bỏ: việc của chúng nằm trong thẻ gộp
    crowded = mk_sig(state={**FOLLOW, "boss.ai.done": False, "backup.scheduled": False}, hub_expiring=True)
    assert lesson_ids(today_of(crowded, items=done)) == "G05"          # thẻ gộp bị cắt khỏi 3 việc ⇒ bài vẫn còn


def test_merged_key_is_a_known_p3_todo_but_individual_ones_stay_known_for_old_rows() -> None:
    assert sg.todo_key_known("followup") and sg.static_level("followup") == "P3"
    assert sg.todo_key_known("followup.5")                              # hàng "dismissed" cũ vẫn bật lại được
    assert not sg.todo_key_known("followup.11") and not sg.todo_key_known("followup.13")
    assert sg.TODO_TARGETS["followup"] == "overview.setup_followup"
    assert sg.topic_of("followup") == "setup"
    assert sg.todo_copy("followup")[0] == "Việc thiết lập tiếp"


def test_hub_name_is_the_same_in_three_places() -> None:
    row = next(r for r in boss_service.ROWS if r["key"] == "hub")
    follow = next(t for n, _k, t, _sql in setup_routes.FOLLOW_UP_EXTRA if n == 14)
    assert sg.todo_copy("boss.hub")[0] == row["title"] == follow == "Nối Gen-hub"
    assert "không bắt buộc" in sg.todo_copy("boss.hub")[1].lower()            # "nếu Sếp muốn" chuyển vào lý do


async def test_api_merged_todo_dismiss_and_card_snooze(
        owner_api: Api, sigbox: list[sg.Signals], clock: Any) -> None:  # noqa: F811  (fixture nhập từ tệp v0154)
    sigbox[0] = mk_sig(state={**FOLLOW, "boss.hub.done": False})
    d = await today(owner_api)
    assert todo_keys(d) == ["boss.hub", "followup"] and d["todos"][1]["target"] == "overview.setup_followup"
    r = await post_item(owner_api, CARD, action="snooze", days=7)
    assert r.status_code == 204, r.text
    assert todo_keys(await today(owner_api)) == []
    assert (await post_item(owner_api, CARD, action="restore")).status_code == 204
    assert todo_keys(await today(owner_api)) == ["boss.hub", "followup"]
    assert (await post_item(owner_api, "todo:followup", action="dismiss")).status_code == 422   # cần xác nhận
    assert (await post_item(owner_api, "todo:followup", action="dismiss", confirm=True)).status_code == 204
    assert todo_keys(await today(owner_api)) == ["boss.hub"]
    rows = (await owner_api.get("/gen/coach/prefs")).json()["dismissed"]
    assert rows == [{"key": "followup", "level": "P3", "title": "Việc thiết lập tiếp"}]
    assert (await post_item(owner_api, "todo:followup", action="restore")).status_code == 204
    assert todo_keys(await today(owner_api)) == ["boss.hub", "followup"]
