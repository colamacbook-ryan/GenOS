"""v0.1.59 (g5-thiet-lap) — việc `model.missing` của Gen hướng dẫn dẫn thẳng tới màn "Thiết lập nguồn AI"
(`/setup?buoc=nguon-ai`) bằng `link`, KHÔNG còn `target` (web ưu tiên `target` hơn `link` — coachModel.showMe — nên nếu
cả hai cùng có thì nút "Chỉ cho em" lại làm sáng ô cũ ở Agent & Model thay vì mở màn thiết lập).

Thuần (không DB): dùng tín hiệu giả của test_gen_coach_v0154, chạy bằng `pytest -q tests/test_coach_link_v0159.py`.
"""

from gh.gen.coach import engine
from gh.gen.coach import signals as sg
from tests.test_gen_coach_v0154 import T0, keys_of, mk_sig, today_of

AI_SOURCE_PATH = "/setup?buoc=nguon-ai"


def test_todo_links_table_has_only_the_ai_source_keys() -> None:
    # v0.1.68 (F029): `boss.ai` cũng dẫn màn Thiết lập nguồn AI — MỘT đích duy nhất cho việc thêm / kiểm nguồn AI.
    assert sg.TODO_LINKS == {"model.missing": AI_SOURCE_PATH, "boss.ai": AI_SOURCE_PATH}
    # một việc tĩnh chỉ có MỘT cách chỉ đường: target hoặc link, không cả hai.
    assert not set(sg.TODO_TARGETS) & set(sg.TODO_LINKS)
    # đích link là đường dẫn trong app (một dấu "/" đầu, không phải URL ngoài) — web chỉ nhận dạng này.
    for link in sg.TODO_LINKS.values():
        assert link.startswith("/") and not link.startswith("//")


def test_model_missing_has_link_and_no_target() -> None:
    todo = next(t for t in engine.candidate_todos(mk_sig(state={"model.bound": False})) if t.key == "model.missing")
    assert todo.link == AI_SOURCE_PATH
    assert todo.target is None
    assert todo.level == "P0"


def test_model_missing_payload_carries_link_without_target() -> None:
    plan = today_of(mk_sig(state={"model.bound": False, "boss.ai.done": False}))
    assert keys_of(plan) == ["model.missing"]
    row = plan.payload["todos"][0]
    assert row["link"] == AI_SOURCE_PATH
    assert "target" not in row
    # việc khẩn P0 không tắt được — không đổi sau khi đổi cách chỉ đường.
    assert row["can_dismiss"] is False and "dismiss_warning" not in row
    # Todo.payload() cũng mang link (nguồn của hàng trên).
    todo = engine.candidate_todos(mk_sig(state={"model.bound": False}))[0]
    assert todo.payload()["link"] == AI_SOURCE_PATH and "target" not in todo.payload()


def test_other_static_todos_keep_their_target() -> None:
    sig = mk_sig(state={"boss.ai.done": False, "boss.hub.done": False})
    todos = {t.key: t for t in engine.candidate_todos(sig)}
    assert todos["boss.ai"].link == AI_SOURCE_PATH and todos["boss.ai"].target is None
    assert todos["boss.hub"].target == sg.BOSS_TARGET.format(key="hub") and todos["boss.hub"].link is None
    # mọi khoá có đích làm sáng vẫn giữ nguyên bảng cũ trừ model.missing và boss.ai (v0.1.68 → link).
    assert "model.missing" not in sg.TODO_TARGETS and "boss.ai" not in sg.TODO_TARGETS
    assert sg.TODO_TARGETS["backup.unset"] == "system.backup.schedule"


def test_health_todos_still_use_the_incident_link() -> None:
    # sự cố sức khoẻ: đích là `link` của chính sự cố (không nằm trong TODO_LINKS).
    sig = mk_sig(alerts=[{"kind": "disk.low", "severity": "warn", "link": "/system?tab=storage&focus=health",
                          "raised_at": T0.isoformat()}])
    health = [t for t in engine.candidate_todos(sig) if t.key.startswith("health.")]
    assert health and all(t.target is None for t in health)
    assert health[0].link == "/system?tab=storage&focus=health"
    assert sg.TODO_LINKS.get(health[0].key) is None
