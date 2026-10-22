"""v0.1.54 (g1-api) — Gen hướng dẫn: thẻ "Hôm nay của Sếp" (việc cần làm, mẹo, bài học), chuông `gen.coach`, dòng
"Việc bắt buộc x/N" trong Bản tin, tool `coach.status` + khối việc vận hành trong prompt Gen.

Nội dung mẹo / bài học là NỘI DUNG GIẢ (CONTENT_DIR → tmp_path) đúng schema — test không phụ thuộc tệp thật của gói nội
dung. Đồng hồ là đồng hồ giả truyền vào (`now`) hoặc `routes._now` được monkeypatch. Chạy cả quyền superuser và
`GH_TEST_APP_ROLE=1` (gh_app): `pytest -q tests/test_gen_coach_v0154.py` và `GH_TEST_APP_ROLE=1 pytest -q …`.
"""

import asyncio
import inspect
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args
from zoneinfo import ZoneInfo

import orjson
import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from gh import health, worker
from gh.boss_checks import service as boss_service
from gh.chassis.masking import _RE_EMAIL, _RE_SECRET, mask_for_model
from gh.db import admin_sessionmaker, sessionmaker
from gh.gen import briefing, envelope, tools
from gh.gen import engine as gen_engine
from gh.gen import registry as gen_registry
from gh.gen import store as gen_store
from gh.gen.coach import cron, engine, lessons, routes, store
from gh.gen.coach import signals as sg
from gh.gen.coach.engine import Item, Prefs
from gh.gen.tools import ToolRunner
from gh.providers.router import ModelRouter
from gh.setup import routes as setup_routes
from gh.worker import JOB_LABELS, WorkerSettings
from tests.conftest import PG, Api
from tests.phase2 import org_id
from tests.test_background_cli_v0141 import FAKE_KEY
from tests.test_briefing_v0141 import _bells as briefing_bells
from tests.test_briefing_v0141 import _messages as briefing_messages
from tests.test_briefing_v0141 import _router as briefing_router
from tests.test_briefing_v0141 import _today as briefing_today
from tests.test_gen import FakeRouter, _user_of, ask, kinds
from tests.test_model_router import provider as api_provider
from tests.test_no_personal_info_v0156 import OLD_NAME
from tests.test_rbac_api import login_as
from tests.test_rls import _as_low_priv

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
T0 = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)     # 10:00 giờ VN, thứ Hai


def at(days: int = 0, hour: int = 3, minute: int = 0) -> datetime:
    """Mốc UTC của ngày T0 + `days` lúc `hour:minute` UTC (03:00 UTC = 10:00 giờ VN)."""
    return datetime(2026, 10, 12, hour, minute, tzinfo=UTC) + timedelta(days=days)


# ─── nội dung giả ────────────────────────────────────────────────────────────────────────────────────────────

BODY3 = "Câu một của bài. Câu hai của bài. Câu ba của bài."
#: Thứ tự lộ trình giả: N01 10, N02 20, N03 30, N04 40, N05 45, G05..G11 50..110, N06 115, N07 120, N08 125,
#: G13 130, G14 140, N09 150, N10 160 ⇒ đúng 19 bài.
FAKE_ORDERS = {"N01": 10, "N02": 20, "N03": 30, "N04": 40, "N05": 45, "N06": 115, "N07": 120, "N08": 125,
               "N09": 150, "N10": 160}


def fake_lessons() -> list[dict[str, Any]]:
    out = [{"id": lid, "order": order, "title": f"Bài giả {lid}", "body": BODY3,
            "try": {"label": "Thử ngay", "target": "api.bindings"}} for lid, order in FAKE_ORDERS.items()]
    for x in out:
        if x["id"] == "N02":
            x["unlock"] = ["model.bound"]
        if x["id"] == "N04":
            x["done_signal"] = "pin.set"
    return out


def fake_tips() -> list[dict[str, Any]]:
    return [
        {"key": "tip-model", "topic": "model", "when": ["!model.bound"], "title": "Mẹo giả về model",
         "body": "Chọn model cho Gen ở API & Model.", "try": {"label": "Thử ngay", "target": "api.bindings"}},
        {"key": "tip-memory", "topic": "memory", "when": ["memory.empty"], "title": "Mẹo giả về Gen nhớ",
         "body": "Bảo Gen nhớ sở thích của Sếp."},
        {"key": "tip-budget", "topic": "ai_cost", "when": ["!ai_budget.set"], "title": "Mẹo giả về trần chi phí",
         "body": "Đặt trần chi phí AI mỗi ngày."},
    ]


def install_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lesson_rows: list[dict[str, Any]] | None = None,
                    tip_rows: list[dict[str, Any]] | None = None) -> None:
    (tmp_path / "lessons.json").write_text(json.dumps(fake_lessons() if lesson_rows is None else lesson_rows),
                                           encoding="utf-8")
    (tmp_path / "tips.json").write_text(json.dumps(fake_tips() if tip_rows is None else tip_rows), encoding="utf-8")
    monkeypatch.setattr(lessons, "CONTENT_DIR", tmp_path)
    lessons.load_lessons.cache_clear()
    lessons.load_tips.cache_clear()


@pytest.fixture(autouse=True)
def fake_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    install_content(monkeypatch, tmp_path)
    yield
    lessons.load_lessons.cache_clear()
    lessons.load_tips.cache_clear()


# ─── tín hiệu giả (thuần) ────────────────────────────────────────────────────────────────────────────────────

def mk_sig(*, state: dict[str, bool] | None = None, alerts: list[dict[str, Any]] | None = None,
           last_alert: datetime | None = None, required: tuple[int, int] = (1, 1), drafts: int = 0,
           hub_expiring: bool = False) -> sg.Signals:
    """Hệ thống 'ổn': mọi tín hiệu tốt → không có việc nào; ghi đè từng tín hiệu qua `state`."""
    st = dict.fromkeys(sg.STATE_SIGNALS, True)
    st.update({"hub.kho_write_missing": False, "memory.empty": False, "drafts.any": drafts > 0})
    st.update(state or {})
    return sg.Signals(state=st, alerts=alerts or [],
                      last_alert_raised_at=last_alert.isoformat() if last_alert else None,
                      required_done=required[0], required_total=required[1], drafts_pending=drafts,
                      hub_expiring=hub_expiring)


def alert(kind: str, severity: str, raised: datetime, link: str | None = "/system?tab=storage") -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "link": link, "raised_at": raised.isoformat()}


def today_of(sig: sg.Signals, prefs: Prefs | None = None, items: dict[str, Item] | None = None,
             now: datetime = T0) -> engine.Plan:
    return engine.plan_today(sig, prefs or Prefs(), items or {}, now, TZ, tips=list(lessons.load_tips()),
                             curr=lessons.curriculum())


def keys_of(plan: engine.Plan) -> list[str]:
    return [t["key"] for t in plan.payload["todos"]]


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (i) quy tắc, đích, tiêu đề, từ vựng
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def test_state_signal_vocabulary_is_exact() -> None:
    expected = {"model.bound", "api_key.present", "ai_budget.set", "pin.set", "telegram.briefing_on",
                "hub.kho_write_missing", "memory.empty", "backup.scheduled", "offsite.chosen", "drafts.any"}
    expected |= {"staff.active"}                                     # v0.1.55: đã mời nhân viên (điều kiện nhắc từ xa)
    expected |= {f"boss.{k}.done" for k in ("ai", "hub", "facebook", "agy", "claude", "jev", "telegram", "remote",
                                             "facebook_reply", "kho_write")}
    expected |= {f"followup.{n}.done" for n in (5, 6, 7, 8, 9, 10, 11, 13, 14)}
    assert expected == sg.STATE_SIGNALS and len(sg.STATE_SIGNALS) == 30
    assert tuple(r["key"] for r in boss_service.ROWS) == sg.BOSS_KEYS
    assert sg.TOPICS == {"model", "hub", "facebook", "agy", "claude", "telegram", "remote", "backup", "offsite",
                         "drafts", "memory", "ai_cost", "setup", "health"}
    assert set(sg.TOPIC.values()) <= sg.TOPICS


def test_each_required_boss_row_has_exactly_one_rule() -> None:
    required = [r for r in boss_service.ROWS if not r["optional"]]
    assert len(required) == boss_service.REQUIRED_TOTAL == 1 and required[0]["key"] == "ai"
    for row in required:
        rules = [r for r in sg.TODO_RULES if r.key == f"boss.{row['key']}"]
        assert len(rules) == 1, row["key"]
        # boss.ai chỉ khi ĐÃ có model (dedupe với model.missing P0)
        assert rules[0].level == "P1" and rules[0].when == ("model.bound", f"!boss.{row['key']}.done")
    # v0.1.55: Gen-hub / Facebook / Telegram / Google / Claude / từ xa KHÔNG còn là P1; chỉ Gen-hub (gợi ý) và
    # Truy cập từ xa (khi có nhân viên) còn quy tắc P3; các dòng tuỳ chọn khác không phải việc.
    for row in boss_service.ROWS:
        if row["optional"]:
            rules = [r for r in sg.TODO_RULES if r.key == f"boss.{row['key']}"]
            assert all(r.level == "P3" for r in rules), row["key"]
            assert bool(rules) == (row["key"] in ("hub", "remote")), row["key"]
    p1 = [r.key for r in sg.TODO_RULES if r.level == "P1" and r.key.startswith("boss.")]
    assert p1 == ["boss.ai"]
    remote = next(r for r in sg.TODO_RULES if r.key == "boss.remote")
    assert remote.when == ("staff.active", "!boss.remote.done")
    assert next(r for r in sg.TODO_RULES if r.key == "boss.hub").when == ("!boss.hub.done",)


def test_rule_table_order_and_levels() -> None:
    assert [(r.key, r.level) for r in sg.TODO_RULES] == [
        ("health:bad", "P0"), ("model.missing", "P0"), ("boss.ai", "P1"), ("health:warn", "P1"),
        ("backup.unset", "P1"), ("hub.token_expiring", "P1"), ("drafts.pending", "P2"), ("boss.hub", "P3"),
        ("boss.remote", "P3"), *((f"followup.{n}", "P3") for n in (5, 6, 7, 8, 9, 10))]
    assert {r.key for r in sg.TODO_RULES if r.key.startswith("followup.")} == {f"followup.{n}" for n in range(5, 11)}


def test_every_target_resolves_in_registry() -> None:
    """Đích làm sáng của mọi việc phải có trong registry (registry.json do gói web-coach sinh; id
    `boss_checks.row.<key>` chỉ có sau khi gộp web-coach → api-coach)."""
    assert sg.ALL_TARGETS == frozenset(sg.TODO_TARGETS.values())
    # v0.1.59: `model.missing` chuyển từ đích làm sáng `api.bindings` sang `link` /setup?buoc=nguon-ai (TODO_LINKS).
    assert "api.bindings" not in sg.ALL_TARGETS
    assert sg.ALL_TARGETS >= {"system.backup.schedule", "mcp.hub_link.token", "workbench.drafts",
                              "guide.item.do:5", "guide.item.do:10"}
    # v0.1.55: chỉ Gen-hub (gợi ý) và Truy cập từ xa còn đích ở dòng "Việc Sếp cần làm"; boss.ai (v0.1.68) dẫn màn
    # Thiết lập nguồn AI.
    assert {t for t in sg.ALL_TARGETS if t.startswith("boss_checks.row.")} == {
        "boss_checks.row.hub", "boss_checks.row.remote"}
    assert "boss.ai" not in sg.TODO_TARGETS and sg.TODO_LINKS["boss.ai"] == "/setup?buoc=nguon-ai"
    for target in sorted(sg.ALL_TARGETS):
        assert gen_registry.resolve_target(target) is not None, target
    # mỗi việc tĩnh có ĐÚNG một cách chỉ đường: đích làm sáng (TODO_TARGETS) hoặc liên kết trang (TODO_LINKS).
    # v0.1.70: + việc gộp `followup` (không phải một dòng TODO_RULES — nó gộp các `followup.5`–`followup.10`).
    assert set(sg.TODO_TARGETS) | set(sg.TODO_LINKS) == {r.key for r in sg.TODO_RULES if r.kind != "health"} | {
        sg.MERGED_FOLLOWUP_KEY}
    assert not set(sg.TODO_TARGETS) & set(sg.TODO_LINKS)


def test_health_titles_cover_every_action_kind() -> None:
    assert set(health.ACTIONS) <= set(sg.COACH_HEALTH_TITLES)
    assert "host.nightly" in sg.COACH_HEALTH_TITLES              # v0.1.53 thêm kind này vào ACTIONS
    assert sg.health_title("kind.la.chua.ai.biet") == "Có sự cố cần Sếp xem"
    for kind, title in sg.COACH_HEALTH_TITLES.items():
        assert title and "Sếp" in title, kind


def test_dismiss_warnings_cover_every_p1_p3_key() -> None:
    assert sg.DISMISS_WARNINGS["boss.hub"] == ("Không nối Gen-hub thì Gen không đọc được lịch, mail và Kho dữ liệu "
                                               "của Sếp.")
    assert OLD_NAME not in " ".join(sg.DISMISS_WARNINGS.values())            # thẻ dùng tên chung "Kho dữ liệu"
    for rule in sg.TODO_RULES:
        if rule.level in ("P1", "P3"):
            key = "health" if rule.kind == "health" else rule.key
            assert key in sg.DISMISS_WARNINGS, rule.key
    assert sg.dismiss_warning_for("health.channel.down") == sg.DISMISS_WARNINGS["health"]
    assert sg.dismiss_warning_for("khoa.la") == sg.DEFAULT_DISMISS_WARNING == sg.DISMISS_WARNINGS["_default"]


def test_topic_of_and_eval_cond() -> None:
    assert sg.topic_of("health.channel.down") == "health" and sg.topic_of("model.missing") == "model"
    assert sg.topic_of("boss.hub") == "hub" and sg.topic_of("hub.token_expiring") == "hub"
    assert sg.topic_of("boss.ai") == "model"
    assert sg.topic_of("followup.7") == "setup" and sg.topic_of("backup.unset") == "backup"
    assert sg.topic_of("drafts.pending") == "drafts" and sg.topic_of("la.hoac") is None
    st = {"a.x": True, "b.y": False}
    assert sg.eval_cond([], st) and sg.eval_cond(["a.x"], st) and sg.eval_cond(["a.x", "!b.y"], st)
    assert not sg.eval_cond(["b.y"], st) and not sg.eval_cond(["!a.x"], st)
    assert not sg.eval_cond(["chua.biet"], st) and not sg.eval_cond(["!chua.biet"], st)   # chưa đọc được ⇒ sai
    assert lessons.eval_cond is sg.eval_cond


def test_todo_key_known_and_levels() -> None:
    assert sg.todo_key_known("model.missing") and sg.todo_key_known("health.channel.down")
    assert sg.todo_key_known("health.host.nightly") and sg.todo_key_known("followup.5")
    assert not sg.todo_key_known("followup.11") and not sg.todo_key_known("followup.13")
    assert not sg.todo_key_known("health.kind.la") and not sg.todo_key_known("boss.jev")
    assert sg.static_level("model.missing") == "P0" and sg.static_level("drafts.pending") == "P2"
    assert sg.static_level("followup.6") == "P3" and sg.static_level("health.channel.down") is None


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (a) xếp hạng, cắt 3, followup chỉ 5..10
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def test_rank_p0_p1_p2_p3_group_health_and_cut_three() -> None:
    sig = mk_sig(
        state={"boss.ai.done": False, "boss.hub.done": False, "backup.scheduled": False, "followup.5.done": False},
        drafts=3, hub_expiring=True,
        alerts=[alert("channel.down", "bad", at(0, 1), "/connections"), alert("channel.down", "warn", at(0, 2), "/moi"),
                alert("offsite.stale", "warn", at(0, 1), "/system?tab=storage&focus=offsite")])
    todos = engine.candidate_todos(sig)
    assert [t.key for t in todos] == ["health.channel.down", "boss.ai", "health.offsite.stale", "backup.unset",
                                      "hub.token_expiring", "drafts.pending", "boss.hub", "followup.5"]
    assert [t.level for t in todos] == ["P0", "P1", "P1", "P1", "P1", "P2", "P3", "P3"]
    grouped = todos[0]                            # hai sự cố channel.down gộp một việc, liên kết của sự cố mới nhất
    assert grouped.link == "/moi" and grouped.title == sg.COACH_HEALTH_TITLES["channel.down"]
    assert next(t for t in todos if t.key == "drafts.pending").title == "Có 3 bản nháp chờ Sếp duyệt"
    plan = today_of(sig)
    assert keys_of(plan) == ["health.channel.down", "boss.ai", "health.offsite.stale"]          # cắt 3
    rows = plan.payload["todos"]
    assert [r["can_dismiss"] for r in rows] == [False, True, True]
    assert "dismiss_warning" not in rows[0] and rows[1]["dismiss_warning"] == sg.DISMISS_WARNINGS["boss.ai"]
    assert rows[1]["link"] == "/setup?buoc=nguon-ai" and "target" not in rows[1]
    assert rows[0]["link"] == "/moi" and "target" not in rows[0]
    assert plan.p01_keys == ["health.channel.down", "boss.ai", "health.offsite.stale"]


def test_boss_ai_is_deduped_with_model_missing_and_gen_hub_is_only_a_p3_hint() -> None:
    # Chưa có model: chỉ model.missing (P0) — boss.ai không nhắc hai lần một chuyện.
    no_model = mk_sig(state={"model.bound": False, "boss.ai.done": False})
    assert [t.key for t in engine.candidate_todos(no_model)] == ["model.missing"]
    # Có model mà nguồn AI chưa chạy thật/chưa Kiểm tra: boss.ai (P1, dẫn màn Thiết lập nguồn AI).
    has_model = mk_sig(state={"boss.ai.done": False})
    todo = engine.candidate_todos(has_model)[0]
    assert (todo.key, todo.level, todo.target, todo.link) == ("boss.ai", "P1", None, "/setup?buoc=nguon-ai")
    assert todo.title == "Kiểm tra nguồn AI chạy được"
    # Gen-hub / Facebook / Telegram / Google / Claude chưa nối: KHÔNG có P1; chỉ một gợi ý P3 (Gen-hub), không chuông.
    optional_off = mk_sig(state={f"boss.{k}.done": False for k in ("hub", "facebook", "agy", "claude", "telegram")})
    todos = engine.candidate_todos(optional_off)
    assert [(t.key, t.level) for t in todos] == [("boss.hub", "P3")]
    plan = today_of(optional_off)
    assert plan.p01_keys == [] and keys_of(plan) == ["boss.hub"]
    assert plan.payload["todos"][0]["title"] == "Nối Gen-hub"       # v0.1.70 (F138): một tên ở ba nơi
    assert OLD_NAME not in todos[0].why and "Kho dữ liệu" in todos[0].why


def test_remote_access_is_only_nudged_after_staff_were_invited() -> None:
    alone = mk_sig(state={"boss.remote.done": False, "staff.active": False})
    assert engine.candidate_todos(alone) == []                               # chưa mời ai ⇒ không nhắc
    team = mk_sig(state={"boss.remote.done": False, "staff.active": True})
    todos = engine.candidate_todos(team)
    assert [(t.key, t.level, t.target) for t in todos] == [("boss.remote", "P3", "boss_checks.row.remote")]
    assert today_of(team).p01_keys == []                                      # P3: không chuông
    done = mk_sig(state={"boss.remote.done": True, "staff.active": True})
    assert engine.candidate_todos(done) == []


def test_equal_level_follows_table_order_and_p2_p3_flags() -> None:
    sig = mk_sig(state={"boss.ai.done": False, "boss.hub.done": False, "boss.remote.done": False,
                        "backup.scheduled": False, "followup.6.done": False, "followup.7.done": False}, drafts=1)
    todos = engine.candidate_todos(sig)
    assert [t.key for t in todos] == ["boss.ai", "backup.unset", "drafts.pending", "boss.hub", "boss.remote",
                                      "followup.6", "followup.7"]
    by = {t.key: t.payload() for t in todos}
    assert by["drafts.pending"]["can_dismiss"] is False                    # P2 không tắt được
    assert by["followup.6"]["can_dismiss"] is True and by["followup.6"]["target"] == "guide.item.do:6"
    assert by["boss.hub"]["can_dismiss"] is True and by["boss.hub"]["target"] == "boss_checks.row.hub"


def test_followup_11_13_14_are_never_todos() -> None:
    state = {f"followup.{n}.done": False for n in (5, 6, 7, 8, 9, 10, 11, 13, 14)}
    keys = [t.key for t in engine.candidate_todos(mk_sig(state=state))]
    assert keys == [f"followup.{n}" for n in (5, 6, 7, 8, 9, 10)]
    assert not {"followup.11", "followup.13", "followup.14"} & set(keys)


def test_unknown_source_signals_never_create_todos() -> None:
    sig = sg.Signals()                       # mọi nguồn lỗi ⇒ không có tín hiệu nào ⇒ không khẳng định điều gì
    assert engine.candidate_todos(sig) == []
    plan = today_of(sig)
    assert plan.payload["todos"] == [] and plan.payload["tip"] is None


def test_snoozed_and_dismissed_are_dropped_but_p0_is_never_dismissed() -> None:
    sig = mk_sig(state={"model.bound": False, "backup.scheduled": False}, hub_expiring=True,
                 alerts=[alert("disk.low", "warn", T0)])
    items = {"todo:backup.unset": Item("todo:backup.unset", "snoozed", T0 + timedelta(hours=5), 1, T0, T0),
             "todo:hub.token_expiring": Item("todo:hub.token_expiring", "dismissed", None, 1, T0, T0),
             "todo:model.missing": Item("todo:model.missing", "dismissed", None, 1, T0, T0)}
    plan = today_of(sig, items=items)
    assert keys_of(plan) == ["model.missing", "health.disk.low"]                    # P0 dismissed vẫn hiện
    later = today_of(sig, items=items, now=T0 + timedelta(hours=6))                  # hết hoãn ⇒ quay lại
    assert keys_of(later) == ["model.missing", "health.disk.low", "backup.unset"]
    tp = engine.plan_todos(sig, items, T0)
    assert tp.pending_p01 is True


def test_disabled_and_snoozed_all_hide_everything() -> None:
    sig = mk_sig(state={"model.bound": False})
    off = today_of(sig, Prefs(enabled=False))
    assert off.payload["todos"] == [] and off.payload["tip"] is None and off.payload["lesson"] is None
    assert off.payload["unseen"] is False and off.payload["enabled"] is False and off.shown == []
    assert off.payload["progress"]["lessons_total"] == 19
    snoozed = today_of(sig, Prefs(snooze_until=T0 + timedelta(days=1)))
    assert snoozed.payload["snoozed_until"] == "2026-10-13T03:00:00Z" and snoozed.payload["todos"] == []
    assert snoozed.payload["lesson"] is None and snoozed.payload["unseen"] is False
    expired = today_of(sig, Prefs(snooze_until=T0 - timedelta(minutes=1)))
    assert expired.payload["snoozed_until"] is None and expired.payload["todos"]


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (c) bài học
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def lesson_ids(plan: engine.Plan) -> str | None:
    return plan.payload["lesson"]["id"] if plan.payload["lesson"] else None


def test_curriculum_has_19_lessons_in_order() -> None:
    curr = lessons.curriculum()
    assert len(curr) == 19 and [x["k"] for x in curr] == list(range(1, 20))
    assert [x["id"] for x in curr] == ["N01", "N02", "N03", "N04", "N05", "G05", "G06", "G07", "G08", "G09", "G10",
                                       "G11", "N06", "N07", "N08", "G13", "G14", "N09", "N10"]
    g = {x["id"]: x for x in lessons.guide_lessons()}
    assert sorted(g) == ["G05", "G06", "G07", "G08", "G09", "G10", "G11", "G13", "G14"]
    reg = {x["n"]: x for x in gen_registry.load().guide}
    for n in (5, 6, 7, 8, 9, 10, 11, 13, 14):
        x = g[f"G{n:02d}"]
        assert x["order"] == n * 10 and x["title"] == reg[n]["title"] and reg[n]["why"] in x["body"]
        assert reg[n]["console"]["label"] in x["body"]
        assert x["try"] == {"label": "Làm thử", "target": f"guide.item.do:{n}"}
        assert x["done_signal"] == f"followup.{n}.done"
        assert gen_registry.resolve_target(x["try"]["target"]) is not None


def test_next_lesson_respects_unlock_done_signal_and_quota() -> None:
    sig = mk_sig(state={"model.bound": False})
    plan = today_of(sig)
    assert lesson_ids(plan) == "N01"                       # N01 đứng trước; N02 cần model.bound nên chưa mở khoá
    assert plan.payload["lesson"] == {"id": "N01", "k": 1, "total": 19, "title": "Bài giả N01", "body": BODY3,
                                      "try": {"label": "Thử ngay", "target": "api.bindings"}, "status": "new"}
    assert "lesson:N01" in plan.shown
    items = {"lesson:N01": Item("lesson:N01", "understood", None, 1, T0, T0)}
    p2 = today_of(sig, items=items, now=T0 + timedelta(days=1))
    assert lesson_ids(p2) == "N03"                                    # N02 chưa mở khoá ⇒ nhảy qua
    p3 = today_of(mk_sig(), items=items, now=T0 + timedelta(days=1))
    assert lesson_ids(p3) == "N02"                                    # có model ⇒ N02 mở khoá
    done_items = {f"lesson:N0{i}": Item(f"lesson:N0{i}", "understood", None, 1, T0, T0) for i in (1, 2, 3)}
    p4 = today_of(mk_sig(), items=done_items, now=T0 + timedelta(days=1))
    assert lesson_ids(p4) == "N05"                                    # N04 có done_signal pin.set đã đạt ⇒ bỏ qua


def test_understood_lesson_never_returns() -> None:
    items = {"lesson:N01": Item("lesson:N01", "understood", None, 1, T0, T0)}
    for d in (0, 1, 5, 30):
        assert lesson_ids(today_of(mk_sig(), items=items, now=T0 + timedelta(days=d))) not in ("N01",)
    rows = {row["id"]: s for row, s in engine.lesson_statuses(lessons.curriculum(), items, {}, T0)}
    assert rows["N01"] == "understood" and rows["N02"] == "new"


def test_lesson_shown_three_days_without_reply_is_snoozed_seven() -> None:
    first = T0
    items = {"lesson:N01": Item("lesson:N01", "shown", None, 1, first, first)}
    assert lesson_ids(today_of(mk_sig(), items=items, now=first + timedelta(days=1))) == "N01"   # vẫn giữ bài đó
    assert lesson_ids(today_of(mk_sig(), items=items, now=first + timedelta(days=2, hours=23))) == "N01"
    after3 = today_of(mk_sig(), items=items, now=first + timedelta(days=3))
    assert lesson_ids(after3) == "N02"                                # ≥ 3 ngày không phản hồi ⇒ hoãn ⇒ bài kế tiếp
    day5 = first + timedelta(days=5)
    rows = {row["id"]: s for row, s in engine.lesson_statuses(lessons.curriculum(), items, {}, day5)}
    assert rows["N01"] == "snoozed"
    assert lesson_ids(today_of(mk_sig(), items=items, now=first + timedelta(days=9, hours=23))) == "N02"
    back = today_of(mk_sig(), items=items, now=first + timedelta(days=10))       # hết 7 ngày hoãn ⇒ quay lại
    assert lesson_ids(back) == "N01"
    # store ghi thật khi mark_shown: stale ⇒ snoozed tới (lần hiện đầu + 10 ngày)
    stale = engine.stale_lessons(items, first + timedelta(days=4))
    assert [(i.key, i.status, i.snooze_until) for i in stale] == [("lesson:N01", "snoozed", first + timedelta(days=10))]
    assert engine.stale_lessons(items, first + timedelta(days=2)) == []
    assert engine.stale_lessons(items, first + timedelta(days=11)) == []
    # hiện lại sau khi hết hạn ⇒ đồng hồ 3 ngày chạy lại từ đầu
    again = engine.after_shown(items["lesson:N01"], "lesson:N01", first + timedelta(days=12))
    assert again.first_shown_at == first + timedelta(days=12) and again.shown_count == 2


def test_guide_lesson_dropped_when_followup_todo_shown_or_dismissed() -> None:
    state = {f"followup.{n}.done": False for n in (5, 6, 7, 8, 9, 10, 11, 13, 14)}
    old = T0 - timedelta(days=5)             # các bài N01..N05 đã hiểu từ lâu (không tính vào hạn mức của hôm nay)
    done = {f"lesson:{x}": Item(f"lesson:{x}", "understood", None, 1, old, old)
            for x in ("N01", "N02", "N03", "N04", "N05")}
    crowded = mk_sig(state={**state, "boss.ai.done": False, "backup.scheduled": False}, hub_expiring=True)
    p = today_of(crowded, items=done)
    assert keys_of(p) == ["boss.ai", "backup.unset", "hub.token_expiring"]  # 3 việc P1 chiếm hết ⇒ followup không hiện
    assert lesson_ids(p) == "G05"                                             # nên bài G05 vẫn còn
    # v0.1.70 (F031/F138): các việc followup.5–10 gộp thành MỘT việc ⇒ bài G05–G10 đều bị bỏ (việc của chúng nằm
    # trong thẻ gộp), bài kế tiếp là G11 (11 không phải việc của Gen).
    p5 = today_of(mk_sig(state=state), items=done)
    assert keys_of(p5) == ["followup"]
    assert lesson_ids(p5) == "G11"
    dismissed = {**done, "todo:followup.8": Item("todo:followup.8", "dismissed", None, 1, T0, T0)}
    p6 = today_of(mk_sig(state=state), items=dismissed)
    assert keys_of(p6) == ["followup"] and lesson_ids(p6) == "G11"


def test_lessons_per_day_zero_one_two() -> None:
    sig = mk_sig()
    assert today_of(sig, Prefs(lessons_per_day=0)).payload["lesson"] is None
    assert today_of(sig, Prefs(lessons_per_day=0)).payload["progress"]["lessons_total"] == 19
    # 1 bài/ngày: bài đã hiện hôm nay được giữ; xong bài (Đã hiểu) trong ngày ⇒ không mở bài mới
    items = {"lesson:N01": Item("lesson:N01", "shown", None, 1, T0, T0)}
    assert lesson_ids(today_of(sig, Prefs(lessons_per_day=1), items, T0 + timedelta(hours=2))) == "N01"
    understood = {"lesson:N01": Item("lesson:N01", "understood", None, 1, T0, T0)}
    assert today_of(sig, Prefs(lessons_per_day=1), understood, T0 + timedelta(hours=2)).payload["lesson"] is None
    assert lesson_ids(today_of(sig, Prefs(lessons_per_day=2), understood, T0 + timedelta(hours=2))) == "N02"
    both = {**understood, "lesson:N02": Item("lesson:N02", "understood", None, 1, T0, T0)}
    assert today_of(sig, Prefs(lessons_per_day=2), both, T0 + timedelta(hours=2)).payload["lesson"] is None
    assert lesson_ids(today_of(sig, Prefs(lessons_per_day=2), both, T0 + timedelta(days=1))) == "N03"   # ngày mới


def test_lesson_progress_counts_done_signal_as_done() -> None:
    sig = mk_sig()          # mọi followup.n.done đạt ⇒ 9 bài G đã xong; N04 có done_signal pin.set cũng đã đạt
    p = today_of(sig, items={"lesson:N01": Item("lesson:N01", "understood", None, 1, T0, T0)})
    assert p.payload["progress"]["lessons_done"] == 9 + 1 + 1 and p.payload["progress"]["lessons_total"] == 19


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (d) mẹo, unseen
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def test_tip_shows_once_and_next_day_moves_on() -> None:
    sig = mk_sig(state={"memory.empty": True, "ai_budget.set": False})
    p0 = today_of(sig)
    assert p0.payload["tip"] == {"key": "tip-memory", "title": "Mẹo giả về Gen nhớ",
                                 "body": "Bảo Gen nhớ sở thích của Sếp."}
    assert "tip:tip-memory" in p0.shown
    items = {"tip:tip-memory": engine.after_shown(None, "tip:tip-memory", T0)}
    p_same_day = today_of(sig, items=items, now=T0 + timedelta(hours=5))               # cùng ngày VN: giữ nguyên
    assert p_same_day.payload["tip"]["key"] == "tip-memory"
    p_next = today_of(sig, items=items, now=T0 + timedelta(days=1))          # hôm sau: mẹo đó không quay lại
    assert p_next.payload["tip"]["key"] == "tip-budget"
    items2 = {**items, "tip:tip-budget": engine.after_shown(None, "tip:tip-budget", T0 + timedelta(days=1))}
    assert today_of(sig, items=items2, now=T0 + timedelta(days=2)).payload["tip"] is None
    # "cùng ngày" theo múi giờ tổ chức: 23:30 VN và 00:30 VN hôm sau là hai ngày khác nhau
    late = datetime(2026, 10, 12, 16, 30, tzinfo=UTC)          # 23:30 VN ngày 12
    shown_late = {"tip:tip-memory": engine.after_shown(None, "tip:tip-memory", late)}
    assert today_of(sig, items=shown_late, now=late + timedelta(hours=2)).payload["tip"]["key"] == "tip-budget"


def test_tip_snoozed_returns_after_expiry_and_dismiss_states_hide() -> None:
    sig = mk_sig(state={"memory.empty": True})
    snooze = {"tip:tip-memory": Item("tip:tip-memory", "snoozed", T0 + timedelta(days=1), 1, T0, T0)}
    assert today_of(sig, items=snooze, now=T0 + timedelta(hours=1)).payload["tip"] is None          # còn đang hoãn
    assert today_of(sig, items=snooze, now=T0 + timedelta(days=1)).payload["tip"]["key"] == "tip-memory"
    understood = {"tip:tip-memory": Item("tip:tip-memory", "understood", None, 1, T0, T0)}
    assert today_of(sig, items=understood, now=T0 + timedelta(days=3)).payload["tip"] is None        # Đã hiểu ⇒ thôi


def test_tip_dropped_when_topic_overlaps_a_shown_todo() -> None:
    sig = mk_sig(state={"model.bound": False, "memory.empty": True})
    plan = today_of(sig)
    assert keys_of(plan)[0] == "model.missing"
    assert plan.payload["tip"]["key"] == "tip-memory"                    # tip-model (topic model) bị bỏ vì trùng việc
    only_model_tip = [fake_tips()[0]]
    p2 = engine.plan_today(sig, Prefs(), {}, T0, TZ, tips=only_model_tip, curr=lessons.curriculum())
    assert p2.payload["tip"] is None
    ok = engine.plan_today(mk_sig(state={"model.bound": False}), Prefs(), {"todo:model.missing": Item(
        "todo:model.missing", "snoozed", T0 + timedelta(days=1), 1, T0, T0)}, T0, TZ, tips=only_model_tip,
        curr=lessons.curriculum())
    assert ok.payload["tip"]["key"] == "tip-model"                       # việc đang hoãn không còn trên thẻ ⇒ mẹo hiện


def test_unseen_rules() -> None:
    # chỉ có bài học mới ⇒ không bật chấm
    calm = today_of(mk_sig())
    assert calm.payload["lesson"] is not None and calm.payload["unseen"] is False
    # P0/P1 chưa từng hiện ⇒ true; đã hiện (shown_count ≥ 1) ⇒ false
    sig = mk_sig(state={"model.bound": False})
    assert today_of(sig).payload["unseen"] is True
    seen = {"todo:model.missing": Item("todo:model.missing", "shown", None, 1, T0, T0)}
    assert today_of(sig, items=seen).payload["unseen"] is False
    # tip chưa hiện ⇒ true
    tipped = mk_sig(state={"memory.empty": True})
    assert today_of(tipped).payload["unseen"] is True
    shown_tip = {"tip:tip-memory": Item("tip:tip-memory", "shown", None, 1, T0, T0)}
    assert today_of(tipped, items=shown_tip).payload["unseen"] is False
    # sự cố sức khoẻ mở lại sau lần hiện cuối ⇒ true
    h = mk_sig(alerts=[alert("disk.low", "bad", T0 + timedelta(hours=2))])
    shown_h = {"todo:health.disk.low": Item("todo:health.disk.low", "shown", None, 2, T0 - timedelta(days=1), T0)}
    assert today_of(h, items=shown_h, now=T0 + timedelta(hours=3)).payload["unseen"] is True
    old_h = mk_sig(alerts=[alert("disk.low", "bad", T0 - timedelta(hours=2))])
    assert today_of(old_h, items=shown_h, now=T0 + timedelta(hours=3)).payload["unseen"] is False
    # tắt hướng dẫn ⇒ luôn false
    assert today_of(sig, Prefs(enabled=False)).payload["unseen"] is False


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (h) ổn định
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def test_update_stable_rules() -> None:
    day1, day3 = T0 + timedelta(days=1), T0 + timedelta(days=3)
    assert engine.update_stable(Prefs(stable_since=T0), True, None, day1) is None        # có P0/P1 ⇒ NULL
    assert engine.update_stable(Prefs(), False, None, T0) == T0                          # chưa có mốc ⇒ now
    assert engine.update_stable(Prefs(stable_since=T0), False, None, day3) == T0
    raised = T0 + timedelta(days=2)
    assert engine.update_stable(Prefs(stable_since=T0), False, raised, day3) == raised   # sự cố mới ⇒ đặt lại
    assert engine.update_stable(Prefs(stable_since=T0), False, T0 - timedelta(days=1), T0 + timedelta(days=3)) == T0
    assert engine.is_stable(T0, T0 + timedelta(days=7)) and not engine.is_stable(T0, T0 + timedelta(days=7) - timedelta(
        seconds=1))
    assert not engine.is_stable(None, T0)


def test_seven_quiet_days_then_stable_and_a_p1_resets_the_count() -> None:
    quiet = mk_sig(state={"memory.empty": True})       # có mẹo chờ để thấy mẹo biến mất khi ổn định
    prefs = Prefs()
    since: datetime | None = None
    for day in range(0, 7):
        plan = today_of(quiet, prefs, now=T0 + timedelta(days=day))
        assert plan.stable is False and plan.payload["progress"]["stable"] is False
        since = plan.stable_since
        prefs = Prefs(stable_since=since)
    assert since == T0
    plan7 = today_of(quiet, prefs, now=T0 + timedelta(days=7))
    assert plan7.stable is True and plan7.payload["progress"]["stable"] is True
    assert plan7.payload["progress"]["stable_since"] == "2026-10-12T03:00:00Z"
    assert plan7.payload["tip"] is None and plan7.payload["unseen"] is False and plan7.payload["lesson"] is not None
    assert today_of(quiet, prefs, now=T0 + timedelta(days=6)).payload["tip"] is not None
    # P1 xuất hiện ngày 5 ⇒ mốc về NULL; rồi đếm lại từ lúc hết việc
    prefs = Prefs(stable_since=T0)
    busy = mk_sig(state={"backup.scheduled": False})
    p5 = today_of(busy, prefs, now=T0 + timedelta(days=5))
    assert p5.stable_since is None and p5.stable is False
    p6 = today_of(quiet, Prefs(stable_since=None), now=T0 + timedelta(days=6))
    assert p6.stable_since == T0 + timedelta(days=6)
    assert today_of(quiet, Prefs(stable_since=p6.stable_since), now=T0 + timedelta(days=12)).stable is False
    assert today_of(quiet, Prefs(stable_since=p6.stable_since), now=T0 + timedelta(days=13)).stable is True


def test_alert_raised_and_cleared_between_two_views_resets_the_mark() -> None:
    prefs = Prefs(stable_since=T0)
    # lần xem thứ hai không còn sự cố nào đang mở, nhưng một sự cố warn đã mở rồi đóng ở ngày 3 ⇒ mốc đặt lại
    sig = mk_sig(last_alert=T0 + timedelta(days=3))
    plan = today_of(sig, prefs, now=T0 + timedelta(days=5))
    assert plan.stable_since == T0 + timedelta(days=3)
    assert today_of(sig, Prefs(stable_since=plan.stable_since), now=T0 + timedelta(days=9)).stable is False
    assert today_of(sig, Prefs(stable_since=plan.stable_since), now=T0 + timedelta(days=10)).stable is True


def test_bad_or_warn_health_keeps_it_unstable_even_for_p1_only_dismissed_state() -> None:
    sig = mk_sig(alerts=[alert("offsite.stale", "warn", T0)])
    plan = today_of(sig, Prefs(stable_since=T0 - timedelta(days=30)), now=T0 + timedelta(days=1))
    assert plan.stable_since is None and plan.stable is False and keys_of(plan) == ["health.offsite.stale"]
    dismissed = {"todo:health.offsite.stale": Item("todo:health.offsite.stale", "dismissed", None, 1, T0, T0)}
    p2 = today_of(sig, Prefs(stable_since=None), dismissed, now=T0 + timedelta(days=1))
    assert keys_of(p2) == [] and p2.stable_since == T0 + timedelta(days=1)       # Sếp đã chọn bỏ ⇒ không còn việc P1


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (g) điều kiện chuông (thuần) + ý định hỏi Gen
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def test_quiet_hours_wrap_midnight_and_empty_window() -> None:
    p = Prefs(quiet_start=8, quiet_end=16)
    assert engine.in_quiet_hours(p, datetime(2026, 10, 12, 2, 5, tzinfo=UTC), TZ)            # 09:05 VN
    assert not engine.in_quiet_hours(p, datetime(2026, 10, 12, 9, 5, tzinfo=UTC), TZ)        # 16:05 VN
    night = Prefs(quiet_start=22, quiet_end=7)
    assert engine.in_quiet_hours(night, datetime(2026, 10, 12, 16, 0, tzinfo=UTC), TZ)       # 23:00 VN
    assert engine.in_quiet_hours(night, datetime(2026, 10, 12, 21, 0, tzinfo=UTC), TZ)       # 04:00 VN
    assert not engine.in_quiet_hours(night, datetime(2026, 10, 12, 2, 5, tzinfo=UTC), TZ)    # 09:05 VN
    assert not engine.in_quiet_hours(Prefs(quiet_start=9, quiet_end=9), datetime(2026, 10, 12, 2, 5, tzinfo=UTC), TZ)


def test_bell_due_rules() -> None:
    now = at(0, 2, 5)                       # 09:05 giờ VN
    keys = ["boss.hub", "health.disk.low"]
    assert engine.bell_due(Prefs(), keys, False, now, TZ)
    assert not engine.bell_due(Prefs(enabled=False), keys, False, now, TZ)
    assert not engine.bell_due(Prefs(bell=False), keys, False, now, TZ)
    assert not engine.bell_due(Prefs(snooze_until=now + timedelta(days=1)), keys, False, now, TZ)
    assert engine.bell_due(Prefs(snooze_until=now - timedelta(minutes=1)), keys, False, now, TZ)
    assert not engine.bell_due(Prefs(), keys, True, now, TZ)                       # ổn định
    assert not engine.bell_due(Prefs(), [], False, now, TZ)                        # không có P0/P1
    assert not engine.bell_due(Prefs(quiet_start=8, quiet_end=16), keys, False, now, TZ)
    # khoá chỉ health.* / hub.token_expiring không tính là 'mới'
    assert not engine.bell_due(Prefs(), ["health.disk.low", "hub.token_expiring"], False, now, TZ)
    # có lần chuông trước: khoá đã chuông không mới; khoá mới ⇒ chuông; cùng tập ≥ 3 ngày ⇒ chuông lại
    last = Prefs(last_bell_at=now - timedelta(days=1), last_bell_keys=("boss.hub",))
    assert not engine.bell_due(last, ["boss.hub"], False, now, TZ)
    assert engine.bell_due(last, ["boss.hub", "backup.unset"], False, now, TZ)
    old = Prefs(last_bell_at=now - timedelta(days=3), last_bell_keys=("boss.hub",))
    assert engine.bell_due(old, ["boss.hub"], False, now, TZ)
    assert not engine.bell_due(Prefs(last_bell_at=now - timedelta(days=3) + timedelta(seconds=1),
                                     last_bell_keys=("boss.hub",)), ["boss.hub"], False, now, TZ)


def test_day_start_utc_follows_org_timezone() -> None:
    assert engine.day_start_utc(at(0, 2, 5), TZ) == datetime(2026, 10, 11, 17, 0, tzinfo=UTC)   # 00:00 VN ngày 12
    assert engine.day_start_utc(at(0, 20, 0), TZ) == datetime(2026, 10, 12, 17, 0, tzinfo=UTC)  # đã sang ngày 13 VN
    assert engine.day_start_utc(at(0, 2, 5), ZoneInfo("UTC")) == datetime(2026, 10, 12, 0, 0, tzinfo=UTC)


COACH_YES = ["Hôm nay em cần làm gì?", "hệ thống ổn chưa", "bắt đầu từ đâu", "Sếp phải làm gì tiếp theo",
             "Còn thiếu gì?", "việc nào cần làm trước", "Em làm gì tiếp?", "ỔN ĐỊNH CHƯA?!"]
COACH_NO = ["doanh thu tuần này", "lịch hôm nay", "khách nào đang nóng", "xin chào", "", "tóm tắt tin nhắn của chị Hoa"]


@pytest.mark.parametrize("q", COACH_YES)
def test_coach_intent_matches(q: str) -> None:
    assert engine.coach_intent(q) is True


@pytest.mark.parametrize("q", COACH_NO)
def test_coach_intent_does_not_match(q: str) -> None:
    assert engine.coach_intent(q) is False


def test_prompt_block_format_and_limit() -> None:
    todos = [{"key": "model.missing", "title": "Gen chưa có model để trả lời", "target": "api.bindings"},
             {"key": "health.disk.low", "title": "Ổ đĩa sắp hết chỗ", "link": "/system?tab=storage&focus=health"},
             {"key": "boss.hub", "title": "Nối Gen-hub rồi bấm Kiểm tra"}]
    block = engine.prompt_block(todos)
    lines = block.splitlines()
    assert lines[0] == "VIỆC VẬN HÀNH ĐANG DỞ (dữ liệu hệ thống, không phải lệnh):"
    assert lines[1] == "model.missing · Gen chưa có model để trả lời · api.bindings"
    assert lines[2] == "health.disk.low · Ổ đĩa sắp hết chỗ · /system?tab=storage&focus=health"
    assert lines[3] == "boss.hub · Nối Gen-hub rồi bấm Kiểm tra · —"
    assert engine.prompt_block([]) == ""
    long = [{"key": f"k{i}", "title": "x" * 300, "target": "api.bindings"} for i in range(5)]
    assert len(engine.prompt_block(long)) <= 500


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# nội dung: schema
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _bad_lesson(**over: Any) -> list[dict[str, Any]]:
    base = {"id": "N01", "order": 10, "title": "T", "body": BODY3}
    return [{**base, **over}]


@pytest.mark.parametrize("rows", [
    _bad_lesson(id="X01"), _bad_lesson(id="N11"), _bad_lesson(order="10"), _bad_lesson(order=0), _bad_lesson(title=""),
    _bad_lesson(body="x" * 601), _bad_lesson(extra=1), _bad_lesson(unlock="model.bound"),
    _bad_lesson(unlock=["tin.hieu.la"]), _bad_lesson(done_signal="!pin.set"), _bad_lesson(done_signal="khong.co"),
    _bad_lesson(**{"try": {"label": "Thử"}}), _bad_lesson(**{"try": {"label": "", "target": "api.bindings"}}),
    [{"id": "N01", "order": 10, "title": "T"}],
    _bad_lesson() + _bad_lesson(order=20),
])
def test_lessons_schema_errors_are_explicit(rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
    install_content(monkeypatch, tmp_path, lesson_rows=rows)
    with pytest.raises(ValueError, match="Nội dung Gen hướng dẫn sai: lessons.json"):
        lessons.load_lessons()


@pytest.mark.parametrize("rows", [
    [{"key": "A b", "topic": "model", "when": [], "title": "T", "body": "B"}],
    [{"key": "ok", "topic": "khong-co", "when": [], "title": "T", "body": "B"}],
    [{"key": "ok", "topic": "model", "when": ["!tin.hieu.la"], "title": "T", "body": "B"}],
    [{"key": "ok", "topic": "model", "when": "model.bound", "title": "T", "body": "B"}],
    [{"key": "ok", "topic": "model", "when": [], "title": "T", "body": "x" * 301}],
    [{"key": "ok", "topic": "model", "when": [], "title": "T"}],
    [{"key": "ok", "topic": "model", "when": [], "title": "T", "body": "B", "xx": 1}],
    [{"key": "ok", "topic": "model", "when": [], "title": "T", "body": "B"},
     {"key": "ok", "topic": "hub", "when": [], "title": "T", "body": "B"}],
])
def test_tips_schema_errors_are_explicit(rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
                                         tmp_path: Path) -> None:
    install_content(monkeypatch, tmp_path, tip_rows=rows)
    with pytest.raises(ValueError, match="Nội dung Gen hướng dẫn sai: tips.json"):
        lessons.load_tips()


def test_content_loader_accepts_notes_and_reports_missing_or_broken_files(monkeypatch: pytest.MonkeyPatch,
                                                                         tmp_path: Path) -> None:
    rows = fake_lessons()
    rows[0]["_ghi_chu"] = "khoá bắt đầu bằng _ bị bỏ qua"
    install_content(monkeypatch, tmp_path, lesson_rows=rows)
    assert len(lessons.load_lessons()) == 10 and lessons.load_lessons()[0]["id"] == "N01"
    (tmp_path / "tips.json").unlink()
    lessons.load_tips.cache_clear()
    with pytest.raises(ValueError, match="không thấy tệp"):
        lessons.load_tips()
    (tmp_path / "tips.json").write_text("{không phải json", encoding="utf-8")
    lessons.load_tips.cache_clear()
    with pytest.raises(ValueError, match="không đọc được JSON"):
        lessons.load_tips()
    (tmp_path / "tips.json").write_text("{}", encoding="utf-8")
    lessons.load_tips.cache_clear()
    with pytest.raises(ValueError, match="danh sách"):
        lessons.load_tips()


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# API: hạ tầng test
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Đồng hồ giả của route: `clock[0] = …` để nhảy ngày."""
    box = [T0]
    monkeypatch.setattr(routes, "_now", lambda: box[0])
    return box


@pytest.fixture
def sigbox(monkeypatch: pytest.MonkeyPatch) -> list[sg.Signals]:
    """Thay `signals.collect` bằng tín hiệu giả (mặc định: hệ thống ổn)."""
    box = [mk_sig()]

    async def fake(db: Any, redis: Any, org_id_: Any) -> sg.Signals:
        return box[0]

    monkeypatch.setattr(sg, "collect", fake)
    return box


async def today(api: Api, mark: bool = False) -> dict[str, Any]:
    r = await api.get("/gen/coach/today" + ("?mark_shown=1" if mark else ""))
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def todo_keys(d: dict[str, Any]) -> list[str]:
    return [t["key"] for t in d["todos"]]


async def post_item(api: Api, key: str, **body: Any) -> Any:
    return await api.send("POST", f"/gen/coach/items/{key}", body)


async def owner_of(db: Any) -> tuple[uuid.UUID, uuid.UUID]:
    await db.rollback()
    org = await org_id(db)
    uid = (await db.execute(text("""SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
                                    JOIN core.roles ro ON ro.id = ur.role_id WHERE ro.code = 'owner'"""))).scalar_one()
    return org, uid


async def coach_log() -> list[Any]:
    async with admin_sessionmaker()() as adm:
        return (await adm.execute(text("""SELECT action, actor_type, target_type, target_id, detail
                                          FROM ops.action_log WHERE action LIKE 'gen.coach\\_%'
                                          ORDER BY at, id"""))).all()


async def http_logs() -> list[Any]:
    async with admin_sessionmaker()() as adm:
        return (await adm.execute(text("""SELECT action, target_id FROM ops.action_log
                                          WHERE action LIKE 'http.%' AND target_id LIKE '%/gen/coach%'"""))).all()


async def rows(sql: str, **params: Any) -> list[Any]:
    async with admin_sessionmaker()() as adm:
        return list((await adm.execute(text(sql), params)).all())


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (a) máy mới 0/N, việc đạt thì biến mất
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

async def test_fresh_machine_shows_first_three_by_table_then_done_row_vanishes(
        owner_api: Api, db: Any, redis: Any, clock: list[datetime]) -> None:
    org, _uid = await owner_of(db)
    d = await today(owner_api)
    # v0.1.55: máy mới chỉ có 1 việc bắt buộc (nguồn AI). Chưa có model ⇒ model.missing (P0) nói thay boss.ai;
    # Gen-hub chỉ còn là gợi ý P3 (không chuông), Truy cập từ xa chưa nhắc vì chưa mời ai.
    assert todo_keys(d) == ["model.missing", "backup.unset", "boss.hub"]
    assert [t["level"] for t in d["todos"]] == ["P0", "P1", "P3"]
    assert d["todos"][0]["link"] == "/setup?buoc=nguon-ai" and "target" not in d["todos"][0]
    assert d["todos"][2]["target"] == "boss_checks.row.hub"
    assert d["progress"]["required_done"] == 0 and d["progress"]["required_total"] == 1
    assert d["progress"]["lessons_total"] == 19 and d["progress"]["stable"] is False
    assert d["date"] == "2026-10-12" and d["enabled"] is True and d["snoozed_until"] is None
    assert d["lesson"]["id"] == "N01" and d["lesson"]["k"] == 1 and d["lesson"]["total"] == 19
    assert d["unseen"] is True
    # giả lập Gen-hub đạt + nguồn AI đạt ⇒ lần tải sau (xoá cache tín hiệu) gợi ý boss.hub biến mất, x/N tăng
    async with admin_sessionmaker()() as adm:
        await boss_service.record(adm, org, "hub", "pass")
        # B2: "nguồn AI đạt" chỉ đúng khi có nguồn dùng được (khoá bật + model) — dựng nguồn thật rồi ghi lần kiểm.
        pid = (await adm.execute(text("""INSERT INTO agent.providers (org_id, kind, name) VALUES (:o, 'gemini', 'G')
                                         RETURNING id"""), {"o": org})).scalar_one()
        await adm.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4)
                                  VALUES (:p, 'K', :s, '1234')"""), {"p": pid, "s": b"\x00e"})
        await adm.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'm')"), {"p": pid})
        await boss_service.record(adm, org, "ai_source", "pass")
        await adm.commit()
    assert 0 < await redis.ttl(sg.CACHE_KEY.format(org)) <= 60
    assert todo_keys(await today(owner_api)) == ["model.missing", "backup.unset", "boss.hub"]   # còn trong cache
    await redis.delete(sg.CACHE_KEY.format(org))
    d2 = await today(owner_api)
    assert todo_keys(d2) == ["backup.unset", "followup"]   # có nguồn dùng được ⇒ model.missing hết; followup.5–10 gộp
    assert d2["progress"]["required_done"] == 1 and d2["progress"]["required_total"] == 1


async def test_remote_access_nudge_appears_via_the_api_only_with_staff(
        owner_api: Api, client: Any, db: Any, redis: Any, clock: list[datetime]) -> None:
    org, _uid = await owner_of(db)
    assert "boss.remote" not in todo_keys(await today(owner_api))
    other = await login_as(client, db, "manager")                      # đã có người dùng khác Owner đang hoạt động
    await other.c.aclose()
    await redis.delete(sg.CACHE_KEY.format(org))
    sig = await sg.collect(db, redis, org)
    assert sig.state["staff.active"] is True
    assert "boss.remote" in [t.key for t in engine.candidate_todos(sig)]
    await redis.delete(sg.CACHE_KEY.format(org))
    d = await today(owner_api)
    assert all(t["level"] != "P1" or t["key"] in ("backup.unset",) for t in d["todos"])      # từ xa không bao giờ là P1


async def test_followup_11_13_14_never_appear_through_the_api(owner_api: Api, sigbox: list[sg.Signals],
                                                              clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(state={f"followup.{n}.done": False for n in (5, 6, 7, 8, 9, 10, 11, 13, 14)})
    d = await today(owner_api)
    assert todo_keys(d) == ["followup"]                       # v0.1.70: gộp một; 11/13/14 chỉ góp vào số "Còn N việc"
    assert all(t["level"] == "P3" and t["can_dismiss"] for t in d["todos"])


async def test_get_setup_follow_up_output_is_unchanged_and_helper_matches(owner_api: Api, db: Any) -> None:
    r = await owner_api.get("/setup/follow-up")
    assert r.status_code == 200
    data = r.json()
    assert [i["n"] for i in data] == [4, 5, 6, 7, 8, 9, 10, 11, 13, 14]
    assert all(set(i) == {"n", "key", "title", "status", "done"} for i in data)
    org, _ = await owner_of(db)
    row = (await db.execute(text("SELECT org_id, completed FROM ops.setup_state WHERE org_id = :o"), {"o": org})).one()
    assert await setup_routes.follow_up_status(db, row) == data


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (b) hành động trên mục
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

BUSY = {"model.bound": False, "backup.scheduled": False}


def busy(state: dict[str, bool] | None = None, **kw: Any) -> sg.Signals:
    """P0 model.missing + 3 việc P1: sự cố mức warn, chưa đặt lịch sao lưu, token Gen-hub sắp hết hạn."""
    return mk_sig(state={**BUSY, **(state or {})}, hub_expiring=True, alerts=[alert("worker.silent", "warn", T0)],
                  **kw)



@pytest.mark.parametrize("days", [1, 3, 7])
async def test_snooze_hides_until_expiry(owner_api: Api, sigbox: list[sg.Signals], clock: list[datetime],
                                         days: int) -> None:
    sigbox[0] = busy()
    assert todo_keys(await today(owner_api)) == ["model.missing", "health.worker.silent", "backup.unset"]
    r = await post_item(owner_api, "todo:backup.unset", action="snooze", days=days)
    assert r.status_code == 204 and r.content == b""
    assert todo_keys(await today(owner_api)) == ["model.missing", "health.worker.silent", "hub.token_expiring"]
    clock[0] = T0 + timedelta(days=days) - timedelta(minutes=1)
    assert "backup.unset" not in todo_keys(await today(owner_api))
    clock[0] = T0 + timedelta(days=days)
    assert todo_keys(await today(owner_api)) == ["model.missing", "health.worker.silent", "backup.unset"]
    assert await http_logs() == [] and await coach_log() == []      # hoãn là thao tác riêng tư, không ghi Nhật ký


async def test_snooze_days_must_be_1_3_or_7(owner_api: Api, sigbox: list[sg.Signals]) -> None:
    for body in ({"action": "snooze", "days": 2}, {"action": "snooze", "days": 0}, {"action": "snooze"},
                 {"action": "bogus"}, {"action": "snooze", "days": "3 ngày"}):
        r = await owner_api.send("POST", "/gen/coach/items/todo:boss.hub", body)
        assert r.status_code == 422 and r.json()["code"] == "VALIDATION", body
    assert (await rows("SELECT count(*) FROM agent.gen_coach_items"))[0][0] == 0


async def test_unknown_item_keys_are_404(owner_api: Api, sigbox: list[sg.Signals]) -> None:
    for key in ("todo:khong.co", "todo:followup.11", "todo:followup.13", "todo:boss.jev", "todo:health.kind.la",
                "tip:khong-co", "lesson:N99", "lesson:", "card:khac", "xyz", "todo:"):
        r = await post_item(owner_api, key, action="snooze", days=1)
        assert r.status_code == 404, key
        assert r.json()["code"] == "COACH_ITEM_UNKNOWN" and r.json()["title"] == "Em không biết việc này", key
    for key in ("todo:health.channel.down", "todo:health.host.nightly", "todo:followup.5", "tip:tip-memory",
                "lesson:G13", "lesson:N10", "card:setup_followup"):
        r = await post_item(owner_api, key, action="snooze", days=1)
        assert r.status_code == 204, (key, r.text)


async def test_dismiss_rules_confirm_log_restore_and_progress_unchanged(
        owner_api: Api, sigbox: list[sg.Signals], clock: list[datetime]) -> None:
    sig = mk_sig(state={**BUSY, "followup.6.done": False}, drafts=2, required=(0, 1),
                 alerts=[alert("disk.low", "bad", T0, "/system?tab=storage&focus=health"),
                         alert("offsite.stale", "warn", T0, "/system?tab=storage&focus=offsite")])
    sigbox[0] = sig
    before = await today(owner_api)
    assert todo_keys(before) == ["health.disk.low", "model.missing", "health.offsite.stale"]
    # P0 (model.missing, sự cố bad) ⇒ 422 dù đã xác nhận
    for key in ("todo:model.missing", "todo:health.disk.low"):
        r = await post_item(owner_api, key, action="dismiss", confirm=True)
        assert r.status_code == 422 and r.json()["code"] == "COACH_DISMISS_NOT_ALLOWED", key
        assert r.json()["title"] == "Việc khẩn cấp không tắt được — Sếp xử lý giúp em nhé"
    # P2 (nháp chờ duyệt) cũng không tắt được
    r = await post_item(owner_api, "todo:drafts.pending", action="dismiss", confirm=True)
    assert r.status_code == 422 and r.json()["code"] == "COACH_DISMISS_NOT_ALLOWED"
    # mẹo / bài học không có "Không dùng việc này"
    r = await post_item(owner_api, "lesson:N01", action="dismiss", confirm=True)
    assert r.status_code == 422 and r.json()["code"] == "COACH_ACTION_NOT_ALLOWED"
    # thiếu confirm ⇒ 422 COACH_CONFIRM_REQUIRED
    for body in ({"action": "dismiss"}, {"action": "dismiss", "confirm": False}):
        r = await owner_api.send("POST", "/gen/coach/items/todo:backup.unset", body)
        assert r.status_code == 422 and r.json()["code"] == "COACH_CONFIRM_REQUIRED"
        assert r.json()["title"] == "Sếp xác nhận giúp em trước khi tắt việc này"
    assert await coach_log() == []
    # có confirm ⇒ 204 + đúng một hàng Nhật ký, chỉ có khoá
    r = await post_item(owner_api, "todo:backup.unset", action="dismiss", confirm=True)
    assert r.status_code == 204
    log = await coach_log()
    assert len(log) == 1 and log[0].action == "gen.coach_item_dismissed" and log[0].target_id == "todo:backup.unset"
    assert log[0].actor_type == "user" and log[0].detail == {}
    after = await today(owner_api)
    assert todo_keys(after) == ["health.disk.low", "model.missing", "health.offsite.stale"]
    assert "backup.unset" not in todo_keys(after)
    assert after["progress"]["required_done"] == before["progress"]["required_done"] == 0          # x/N không đổi (NT6)
    assert after["progress"]["required_total"] == 1
    sigbox[0] = mk_sig(state={**BUSY, "followup.6.done": True}, required=(0, 1))
    assert "backup.unset" not in todo_keys(await today(owner_api))
    prefs = (await owner_api.get("/gen/coach/prefs")).json()
    assert prefs["dismissed"] == [{"key": "backup.unset", "level": "P1", "title": "Đặt lịch sao lưu"}]
    # việc sức khoẻ mức warn (P1) tắt được; kind lạ chưa có trong bảng cũng có khoá hợp lệ nếu thuộc health.ACTIONS
    sigbox[0] = sig
    r = await post_item(owner_api, "todo:health.offsite.stale", action="dismiss", confirm=True)
    assert r.status_code == 204
    # khôi phục ⇒ việc quay lại + Nhật ký restored
    r = await post_item(owner_api, "todo:backup.unset", action="restore")
    assert r.status_code == 204
    assert "backup.unset" in todo_keys(await today(owner_api))
    names = [(x.action, x.target_id) for x in await coach_log()]
    assert names == [("gen.coach_item_dismissed", "todo:backup.unset"),
                     ("gen.coach_item_dismissed", "todo:health.offsite.stale"),
                     ("gen.coach_item_restored", "todo:backup.unset")]
    assert all(x.detail == {} for x in await coach_log())
    assert await http_logs() == []          # đã ghi Nhật ký nghiệp vụ ⇒ không có dòng http.* chung
    assert (await owner_api.get("/gen/coach/prefs")).json()["dismissed"][0]["key"] == "health.offsite.stale"


async def test_dismissing_the_gen_hub_hint_and_the_removed_p1_keys(
        owner_api: Api, sigbox: list[sg.Signals], clock: list[datetime]) -> None:
    """boss.hub là gợi ý P3 tắt được (kèm xác nhận); khoá P1 cũ của dòng không còn bắt buộc (facebook, agy, claude,
    telegram) không còn là việc ⇒ 404."""
    sigbox[0] = mk_sig(state={"boss.hub.done": False})
    assert todo_keys(await today(owner_api)) == ["boss.hub"]
    r = await post_item(owner_api, "todo:boss.hub", action="dismiss", confirm=True)
    assert r.status_code == 204
    assert todo_keys(await today(owner_api)) == []
    for key in ("todo:boss.facebook", "todo:boss.agy", "todo:boss.claude", "todo:boss.telegram"):
        r = await post_item(owner_api, key, action="snooze", days=1)
        assert r.status_code == 404 and r.json()["code"] == "COACH_ITEM_UNKNOWN", key


async def test_static_todo_copy_uses_the_effective_kho_label(
        owner_api: Api, sigbox: list[sg.Signals], clock: list[datetime]) -> None:
    """v0.1.57 (Nợ #30, F-R2): thẻ "Nối Gen-hub" ở Hôm nay (lý do + câu hậu quả khi tắt) nhắc đúng Tên Kho Owner đã đặt;
    chưa đặt ⇒ giữ nguyên "Kho dữ liệu" (mặc định không đổi chữ nào)."""
    sigbox[0] = mk_sig(state={"boss.hub.done": False})
    todo = (await today(owner_api))["todos"][0]
    assert todo["key"] == "boss.hub" and "Kho dữ liệu" in todo["why"] and "Kho dữ liệu" in todo["dismiss_warning"]
    name = "Sổ tay Công ty"
    async with admin_sessionmaker()() as adm:
        await adm.execute(text("UPDATE core.organizations SET settings = settings || CAST(:s AS jsonb) WHERE id = :o"),
                          {"o": await org_id(adm), "s": json.dumps({"kho_label": name})})
        await adm.commit()
    todo = (await today(owner_api))["todos"][0]
    assert todo["key"] == "boss.hub" and name in todo["why"] and name in todo["dismiss_warning"]
    assert "Kho dữ liệu" not in todo["why"] + todo["dismiss_warning"] + todo["title"]


async def test_p0_stays_visible_even_if_a_dismissed_row_exists_and_health_escalation(
        owner_api: Api, sigbox: list[sg.Signals], clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(alerts=[alert("worker.silent", "warn", T0)])
    r = await post_item(owner_api, "todo:health.worker.silent", action="dismiss", confirm=True)
    assert r.status_code == 204
    assert todo_keys(await today(owner_api)) == []
    sigbox[0] = mk_sig(alerts=[alert("worker.silent", "bad", T0 + timedelta(hours=1))])   # leo thang thành P0
    d = await today(owner_api)
    assert todo_keys(d) == ["health.worker.silent"] and d["todos"][0]["level"] == "P0"
    assert d["todos"][0]["can_dismiss"] is False


async def test_card_setup_followup_accepts_only_snooze_and_restore(
        owner_api: Api, sigbox: list[sg.Signals], clock: list[datetime]) -> None:
    assert (await owner_api.get("/gen/coach/prefs")).json()["followup_snoozed_until"] is None
    r = await post_item(owner_api, "card:setup_followup", action="snooze", days=3)
    assert r.status_code == 204
    assert (await owner_api.get("/gen/coach/prefs")).json()["followup_snoozed_until"] == "2026-10-15T03:00:00Z"
    clock[0] = T0 + timedelta(days=3)
    assert (await owner_api.get("/gen/coach/prefs")).json()["followup_snoozed_until"] is None      # hết hạn
    clock[0] = T0
    for action, extra in (("dismiss", {"confirm": True}), ("done", {}), ("understood", {})):
        r = await post_item(owner_api, "card:setup_followup", action=action, **extra)
        assert r.status_code == 422 and r.json()["code"] == "COACH_ACTION_NOT_ALLOWED", action
    r = await post_item(owner_api, "card:setup_followup", action="restore")
    assert r.status_code == 204
    assert (await owner_api.get("/gen/coach/prefs")).json()["followup_snoozed_until"] is None


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# mark_shown: chỉ khi xem thẻ mới ghi
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

async def test_only_mark_shown_writes_items_and_last_seen(owner_api: Api, sigbox: list[sg.Signals],
                                                           clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(state={"model.bound": False, "memory.empty": True})
    d = await today(owner_api)
    assert d["unseen"] is True
    await today(owner_api)
    assert (await rows("SELECT count(*) FROM agent.gen_coach_items"))[0][0] == 0
    assert await rows("SELECT * FROM agent.gen_coach_prefs") == []        # model.missing ⇒ mốc vẫn NULL ⇒ không ghi gì
    d1 = await today(owner_api, mark=True)
    assert todo_keys(d1) == ["model.missing"] and d1["tip"]["key"] == "tip-memory" and d1["lesson"]["id"] == "N01"
    items = {r.item_key: r for r in await rows("""SELECT item_key, status, shown_count, first_shown_at, last_shown_at
                                                  FROM agent.gen_coach_items""")}
    assert set(items) == {"todo:model.missing", "tip:tip-memory", "lesson:N01"}
    assert all(r.status == "shown" and r.shown_count == 1 and r.first_shown_at == T0 and r.last_shown_at == T0
               for r in items.values())
    assert (await rows("SELECT last_seen_at FROM agent.gen_coach_prefs"))[0].last_seen_at == T0
    assert (await today(owner_api))["unseen"] is False                      # đã xem ⇒ hết chấm "mới"
    clock[0] = T0 + timedelta(hours=2)
    await today(owner_api, mark=True)
    again = {r.item_key: r for r in await rows("""SELECT item_key, shown_count, first_shown_at, last_shown_at
                                                  FROM agent.gen_coach_items""")}
    assert all(r.shown_count == 2 and r.first_shown_at == T0 and r.last_shown_at == T0 + timedelta(hours=2)
               for r in again.values())


async def test_tip_appears_once_across_days_via_api(owner_api: Api, sigbox: list[sg.Signals],
                                                    clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(state={"memory.empty": True})
    assert (await today(owner_api, mark=True))["tip"]["key"] == "tip-memory"
    clock[0] = T0 + timedelta(hours=3)
    assert (await today(owner_api))["tip"]["key"] == "tip-memory"            # cùng ngày vẫn giữ
    clock[0] = T0 + timedelta(days=1)
    assert (await today(owner_api))["tip"] is None                           # hôm sau: không còn


async def test_lessons_via_api_understood_snooze_and_return(owner_api: Api, sigbox: list[sg.Signals],
                                                            clock: list[datetime]) -> None:
    sigbox[0] = mk_sig()
    assert (await today(owner_api, mark=True))["lesson"]["id"] == "N01"
    r = await post_item(owner_api, "lesson:N01", action="understood")
    assert r.status_code == 204
    clock[0] = T0 + timedelta(hours=1)
    assert (await today(owner_api))["lesson"] is None                        # 1 bài/ngày, đã hiểu bài hôm nay
    clock[0] = T0 + timedelta(days=1)
    d = await today(owner_api, mark=True)
    assert d["lesson"]["id"] == "N02" and d["lesson"]["status"] == "new"
    # N02 hiện từ ngày 1, không phản hồi: ngày 3 vẫn giữ, ngày 4 (≥ 3 ngày) hoãn 7 ngày
    clock[0] = T0 + timedelta(days=3)
    assert (await today(owner_api, mark=True))["lesson"]["id"] == "N02"
    clock[0] = T0 + timedelta(days=4)
    d4 = await today(owner_api, mark=True)
    assert d4["lesson"]["id"] == "N03"
    row = (await rows("SELECT status, snooze_until FROM agent.gen_coach_items WHERE item_key = 'lesson:N02'"))[0]
    assert row.status == "snoozed" and row.snooze_until == T0 + timedelta(days=1) + timedelta(days=10)
    cur = {x["id"]: x["status"] for x in (await owner_api.get("/gen/coach/curriculum")).json()["lessons"]}
    assert cur["N01"] == "understood" and cur["N02"] == "snoozed" and cur["N03"] == "shown"
    r = await post_item(owner_api, "lesson:N03", action="snooze", days=1)       # "Để mai" cho một bài
    assert r.status_code == 204
    clock[0] = T0 + timedelta(days=5)
    assert (await today(owner_api))["lesson"]["id"] == "N03"                    # qua ngày: bài quay lại
    clock[0] = T0 + timedelta(days=11, hours=1)
    d11 = await today(owner_api, mark=True)
    assert d11["lesson"]["id"] in {"N02", "N03"} and d11["lesson"]["status"] in {"shown", "new"}


async def test_curriculum_endpoint_has_19_lessons_with_status(owner_api: Api, sigbox: list[sg.Signals],
                                                              clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(state={"followup.5.done": False})
    await post_item(owner_api, "lesson:N01", action="understood")
    await post_item(owner_api, "lesson:N02", action="done")
    c = (await owner_api.get("/gen/coach/curriculum")).json()
    assert c["total"] == 19 and len(c["lessons"]) == 19
    assert [x["k"] for x in c["lessons"]] == list(range(1, 20))
    by = {x["id"]: x for x in c["lessons"]}
    assert by["N01"]["status"] == "understood" and by["N02"]["status"] == "done"
    assert by["N03"]["status"] == "new" and by["G05"]["status"] == "new" and by["G06"]["status"] == "done"
    assert by["G05"]["try"] == {"label": "Làm thử", "target": "guide.item.do:5"}
    assert set(by["N03"]) == {"id", "k", "title", "body", "try", "status"}


async def test_lessons_per_day_via_prefs(owner_api: Api, sigbox: list[sg.Signals], clock: list[datetime]) -> None:
    r = await owner_api.send("PATCH", "/gen/coach/prefs", {"lessons_per_day": 0})
    assert r.status_code == 200 and r.json()["lessons_per_day"] == 0
    assert (await today(owner_api, mark=True))["lesson"] is None
    await owner_api.send("PATCH", "/gen/coach/prefs", {"lessons_per_day": 2})
    assert (await today(owner_api, mark=True))["lesson"]["id"] == "N01"
    await post_item(owner_api, "lesson:N01", action="understood")
    assert (await today(owner_api, mark=True))["lesson"]["id"] == "N02"
    await post_item(owner_api, "lesson:N02", action="understood")
    assert (await today(owner_api))["lesson"] is None


async def test_prefs_get_patch_and_audit(owner_api: Api, sigbox: list[sg.Signals], clock: list[datetime]) -> None:
    p = (await owner_api.get("/gen/coach/prefs")).json()
    assert p == {"enabled": True, "bell": True, "lessons_per_day": 1, "quiet_start": 21, "quiet_end": 7,
                 "snooze_until": None, "followup_snoozed_until": None, "dismissed": []}
    r = await owner_api.send("PATCH", "/gen/coach/prefs", {"bell": False, "quiet_start": 8, "quiet_end": 16,
                                                           "snooze_all_days": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["bell"] is False and body["quiet_start"] == 8 and body["quiet_end"] == 16
    assert body["snooze_until"] == "2026-10-15T03:00:00Z"
    d = await today(owner_api)
    assert d["snoozed_until"] == "2026-10-15T03:00:00Z" and d["todos"] == [] and d["lesson"] is None
    log = await coach_log()
    assert [x.action for x in log] == ["gen.coach_prefs_changed"]
    assert log[0].detail == {"fields": ["bell", "quiet_end", "quiet_start", "snooze_all_days"]}
    # 0 = bỏ hoãn; gửi lại giá trị cũ ⇒ không đổi gì, không ghi Nhật ký
    r = await owner_api.send("PATCH", "/gen/coach/prefs", {"snooze_all_days": 0, "bell": False})
    assert r.json()["snooze_until"] is None
    assert [x.detail for x in await coach_log()][1] == {"fields": ["snooze_all_days"]}
    await owner_api.send("PATCH", "/gen/coach/prefs", {"bell": False})
    assert len(await coach_log()) == 2
    assert await http_logs() == []
    r = await owner_api.send("PATCH", "/gen/coach/prefs", {"enabled": False})
    assert r.json()["enabled"] is False
    d = await today(owner_api)
    assert d["enabled"] is False and d["todos"] == [] and d["tip"] is None and d["lesson"] is None
    assert d["unseen"] is False
    await today(owner_api, mark=True)                       # tắt hướng dẫn ⇒ xem thẻ cũng không ghi gì
    assert (await rows("SELECT count(*) FROM agent.gen_coach_items"))[0][0] == 0


@pytest.mark.parametrize("body", [{"lessons_per_day": 3}, {"lessons_per_day": -1}, {"quiet_start": 24},
                                  {"quiet_end": -1}, {"snooze_all_days": 2}, {"snooze_all_days": 30},
                                  {"enabled": "có"}, {"quiet_start": "tối"}])
async def test_prefs_out_of_range_is_422(owner_api: Api, body: dict[str, Any]) -> None:
    r = await owner_api.send("PATCH", "/gen/coach/prefs", body)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION"


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (d) chỉ Owner
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("role", ["manager", "operator", "agent_staff", "auditor"])
async def test_non_owner_gets_403_on_every_route(owner_api: Api, client: Any, db: Any, role: str) -> None:
    other = await login_as(client, db, role)
    try:
        calls = [other.get("/gen/coach/today"), other.get("/gen/coach/today?mark_shown=1"),
                 other.get("/gen/coach/prefs"), other.send("PATCH", "/gen/coach/prefs", {"bell": False}),
                 other.get("/gen/coach/curriculum"),
                 other.send("POST", "/gen/coach/items/todo:boss.hub", {"action": "snooze", "days": 1}),
                 other.send("POST", "/gen/coach/items/todo:khong.co", {"action": "snooze", "days": 1})]
        for call in calls:
            r = await call
            assert r.status_code == 403, r.text
            assert r.json()["code"] == "FORBIDDEN" and r.json()["title"] == "Chỉ Sếp (Owner) dùng được Gen hướng dẫn"
    finally:
        await other.c.aclose()
    assert (await rows("SELECT count(*) FROM agent.gen_coach_prefs"))[0][0] == 0
    assert (await rows("SELECT count(*) FROM agent.gen_coach_items"))[0][0] == 0


async def test_anonymous_gets_401(client: Any, app: Any) -> None:
    for path in ("/gen/coach/today", "/gen/coach/prefs", "/gen/coach/curriculum"):
        r = await client.get(f"/api/v1{path}")
        assert r.status_code in (401, 428), path


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (e) không rò dữ liệu, cache tín hiệu, nguồn lỗi
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

SECRET_TITLE = "Kênh rớt: token sk-live-abcdef0123456789xyz của owner@secret-corp.vn"
SECRET_BODY = "detail nội bộ: mật khẩu Pa55w0rd-9988776655 và /home/boss/.ssh/id_rsa"


async def test_payloads_never_carry_detail_message_email_or_token(
        owner_api: Api, db: Any, redis: Any, clock: list[datetime]) -> None:
    org, _ = await owner_of(db)
    await health.raise_once(db, org, key="channel.down:abc", kind="channel.down", severity="bad", title=SECRET_TITLE,
                            body=SECRET_BODY, link="/connections", fingerprint="f1")
    await health.raise_once(db, org, key="offsite.stale", kind="offsite.stale", severity="warn", title=SECRET_TITLE,
                            body=SECRET_BODY, link="/system?tab=storage&focus=offsite", fingerprint="f2")
    async with admin_sessionmaker()() as adm:                 # kết quả kiểm có thông điệp / chi tiết nhạy cảm
        await boss_service.record(adm, org, "hub", "fail", error_code="HUB_X", message=SECRET_TITLE,
                                  detail={"write_missing": ["kho"], "account_masked": "b***@secret-corp.vn"})
        await adm.commit()
    await db.commit()
    await redis.delete(sg.CACHE_KEY.format(org))
    d = await today(owner_api, mark=True)
    await post_item(owner_api, "todo:backup.unset", action="dismiss", confirm=True)
    texts = [orjson.dumps(d).decode(),
             (await owner_api.get("/gen/coach/prefs")).text, (await owner_api.get("/gen/coach/curriculum")).text,
             (await owner_api.get("/gen/coach/today")).text]
    assert "health.channel.down" in texts[0] and "health.offsite.stale" in texts[0]
    for t in texts:
        assert _RE_EMAIL.search(t) is None and _RE_SECRET.search(t) is None
        for banned in ("secret-corp", "sk-live", "Pa55w0rd", "id_rsa", "owner@", '"detail"', '"message"', "mật khẩu",
                       "b***"):
            assert banned not in t, banned
    for t in texts:
        assert mask_for_model(json.loads(t)) == json.loads(t)       # lớp che của hệ thống không phát hiện gì để che
    assert d["todos"][0]["key"] == "health.channel.down" and d["todos"][0]["title"] == sg.COACH_HEALTH_TITLES[
        "channel.down"]
    sig = await sg.collect(db, redis, org)
    assert sig.state["hub.kho_write_missing"] is True          # chỉ đọc cờ write_missing, không giữ nội dung
    cached = (await redis.get(sg.CACHE_KEY.format(org))).decode()
    for banned in ("secret-corp", "sk-live", "Pa55w0rd", "id_rsa", "owner@", "b***"):
        assert banned not in cached, banned


async def test_two_loads_within_60s_read_each_source_once(owner_api: Api, db: Any, redis: Any,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    org, _ = await owner_of(db)
    calls: Counter[str] = Counter()
    for name, fn in sg._sources():
        def make(n: str, f: Any) -> Any:
            async def wrapper(db_: Any, redis_: Any, org_: Any) -> Any:
                calls[n] += 1
                return await f(db_, redis_, org_)
            return wrapper
        monkeypatch.setattr(sg, f"_src_{name}", make(name, fn))
    real_active, real_overview, real_follow = health.active_issues, boss_service.overview, setup_routes.follow_up_status

    async def active(*a: Any, **kw: Any) -> Any:
        calls["health.active_issues"] += 1
        return await real_active(*a, **kw)

    async def overview(*a: Any, **kw: Any) -> Any:
        calls["boss_checks.overview"] += 1
        return await real_overview(*a, **kw)

    async def follow(*a: Any, **kw: Any) -> Any:
        calls["follow_up_status"] += 1
        return await real_follow(*a, **kw)

    monkeypatch.setattr(health, "active_issues", active)
    monkeypatch.setattr(boss_service, "overview", overview)
    monkeypatch.setattr(setup_routes, "follow_up_status", follow)
    first = await today(owner_api)
    second = await today(owner_api)
    assert second["todos"] == first["todos"]
    expected = {"health", "boss", "followup", "settings", "offsite", "hub", "drafts", "telegram", "memory", "api_key",
                "pin"}
    assert {n for n, _ in sg._sources()} == expected
    assert dict(calls) == {**dict.fromkeys(expected, 1), "health.active_issues": 1, "boss_checks.overview": 1,
                           "follow_up_status": 1}
    await redis.delete(sg.CACHE_KEY.format(org))
    await today(owner_api)
    assert calls["health"] == 2 and calls["followup"] == 2


async def test_failed_or_slow_sources_are_dropped_but_the_card_is_returned(
        owner_api: Api, db: Any, redis: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    org, _ = await owner_of(db)

    async def broken(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("DỮ-LIỆU-NHẠY-CẢM-trong-lỗi")

    async def slow(db_: Any, redis_: Any, org_: Any) -> Any:
        await asyncio.sleep(5)
        return {}

    async def fake_pin(db_: Any, redis_: Any, org_: Any) -> dict[str, Any]:
        return {"state": {"pin.set": True}}

    # Chỉ 3 nguồn (hỏng · quá giờ · bình thường), không câu SQL nào chạy dưới hạn 0,05 giây ⇒ không phụ thuộc tải máy.
    real_sources = sg._sources
    monkeypatch.setattr(health, "active_issues", broken)
    monkeypatch.setattr(sg, "_sources", lambda: [("health", sg._src_health), ("boss", slow), ("pin", fake_pin)])
    monkeypatch.setattr(sg, "SOURCE_TIMEOUT_S", 0.05)
    with caplog.at_level("WARNING", logger="gh.gen.coach"):
        sig = await sg.collect(db, redis, org)
    assert sorted(sig.failed) == ["boss", "health"]
    assert sig.alerts == [] and not any(k.startswith("boss.") for k in sig.state)
    assert sig.state == {"pin.set": True}                                    # nguồn khác vẫn có
    assert "DỮ-LIỆU-NHẠY-CẢM" not in caplog.text                            # log cảnh báo không kèm dữ liệu
    assert sig.p01_known() is False
    # cả thẻ: nguồn thật, chỉ riêng health lỗi ⇒ thẻ vẫn trả, thiếu đúng mục health
    monkeypatch.setattr(sg, "_sources", real_sources)
    monkeypatch.setattr(sg, "SOURCE_TIMEOUT_S", 2.0)
    await redis.delete(sg.CACHE_KEY.format(org))
    d = await today(owner_api)
    assert todo_keys(d)[0] == "model.missing" and not any(k.startswith("health.") for k in todo_keys(d))


async def test_failed_source_heals_a_broken_session_for_the_next_sources(db: Any, owner_api: Api) -> None:
    org, _ = await owner_of(db)
    with pytest.raises(DBAPIError):
        await db.execute(text("SELECT * FROM bang_khong_ton_tai"))
    with pytest.raises(DBAPIError):
        await db.execute(text("SELECT 1"))                              # phiên đang ở trạng thái chờ rollback
    await sg._heal(db, org)
    assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
    assert (await db.execute(text("SELECT current_setting('app.org_id', true)"))).scalar_one() == str(org)


async def test_real_sources_produce_expected_state_on_fresh_machine(owner_api: Api, db: Any, redis: Any) -> None:
    org, _ = await owner_of(db)
    sig = await sg.collect(db, redis, org)
    assert sig.failed == [], sig.failed
    assert set(sig.state) <= sg.STATE_SIGNALS
    st = sig.state
    assert st["model.bound"] is False and st["pin.set"] is True and st["api_key.present"] is False
    assert st["memory.empty"] is True and st["drafts.any"] is False and st["backup.scheduled"] is False
    assert st["ai_budget.set"] is False and st["telegram.briefing_on"] is False
    assert st["hub.kho_write_missing"] is False
    assert all(st[f"boss.{k}.done"] is False for k in sg.BOSS_KEYS)
    assert st["staff.active"] is False                                       # mới có mình Owner
    assert all(st[f"followup.{n}.done"] is False for n in (5, 6, 7, 8, 9, 10, 11, 13, 14))
    assert sig.required_total == boss_service.REQUIRED_TOTAL == 1 and sig.required_done == 0
    assert sig.hub_expiring is False
    assert sig.alerts == [] and sig.drafts_pending == 0


async def test_real_sources_drafts_memory_budget_backup_telegram_hub_expiry(
        owner_api: Api, db: Any, redis: Any) -> None:
    org, uid = await owner_of(db)
    await db.execute(text("""INSERT INTO agent.gen_memory_notes (org_id, text, source)
                             VALUES (:o, 'ghi nhớ', 'owner')"""), {"o": org})
    cfg = {"backup": {"frequency": "daily"}, "ai_cost": {"daily_budget_vnd": 50000}}
    await db.execute(text("""UPDATE core.organizations SET settings = settings || CAST(:s AS jsonb) WHERE id = :o"""),
                     {"o": org, "s": json.dumps(cfg)})
    await db.execute(text("""INSERT INTO biz.action_drafts (org_id, code, kind, body, autonomy_level, status) VALUES
                             (:o, 'ACT-9001', 'message', '{"text": "a"}', 2, 'pending'),
                             (:o, 'ACT-9002', 'message', '{"text": "b"}', 2, 'pending'),
                             (:o, 'ACT-9003', 'message', '{"text": "c"}', 2, 'sent')"""), {"o": org})
    await db.commit()
    sig = await sg.collect(db, redis, org)
    assert sig.failed == [], sig.failed
    assert sig.state["memory.empty"] is False and sig.state["backup.scheduled"] is True
    assert sig.state["ai_budget.set"] is True and sig.state["drafts.any"] is True and sig.drafts_pending == 2
    assert "backup.unset" not in [t.key for t in engine.candidate_todos(sig)]
    drafts = next(t for t in engine.candidate_todos(sig) if t.key == "drafts.pending")
    assert drafts.title == "Có 2 bản nháp chờ Sếp duyệt" and drafts.level == "P2"


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (f) 0 lời gọi model + 0 ghi gen_messages
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

async def test_no_model_calls_and_no_gen_messages_anywhere(
        owner_api: Api, db: Any, redis: Any, monkeypatch: pytest.MonkeyPatch, clock: list[datetime],
        sigbox: list[sg.Signals]) -> None:
    called: list[str] = []

    def boom(name: str) -> Any:
        def fail(*a: Any, **kw: Any) -> Any:
            called.append(name)
            raise AssertionError(f"ModelRouter.{name} không được gọi")
        return fail

    for name, _member in inspect.getmembers(ModelRouter, inspect.isfunction):
        if not name.startswith("__"):
            monkeypatch.setattr(ModelRouter, name, boom(name))
    before = (await rows("SELECT count(*) FROM agent.gen_messages"))[0][0]
    sigbox[0] = mk_sig(state={**BUSY, "memory.empty": True}, drafts=1)
    await today(owner_api)
    await today(owner_api, mark=True)
    assert (await post_item(owner_api, "todo:boss.hub", action="snooze", days=1)).status_code == 204
    assert (await post_item(owner_api, "todo:backup.unset", action="dismiss", confirm=True)).status_code == 204
    assert (await post_item(owner_api, "todo:backup.unset", action="restore")).status_code == 204
    assert (await post_item(owner_api, "lesson:N01", action="understood")).status_code == 204
    assert (await post_item(owner_api, "tip:tip-memory", action="snooze", days=1)).status_code == 204
    assert (await owner_api.get("/gen/coach/prefs")).status_code == 200
    assert (await owner_api.send("PATCH", "/gen/coach/prefs", {"bell": False})).status_code == 200
    assert (await owner_api.get("/gen/coach/curriculum")).status_code == 200
    await owner_api.send("PATCH", "/gen/coach/prefs", {"bell": True})
    out = await cron.run_coach(sessionmaker(), redis, at(1, 2, 5))
    assert out["errors"] == 0
    assert called == []
    assert (await rows("SELECT count(*) FROM agent.gen_messages"))[0][0] == before
    assert (await rows("SELECT count(*) FROM agent.model_calls"))[0][0] == 0


def test_coach_modules_never_import_the_model_router_or_write_gen_messages() -> None:
    for mod in (routes, cron, engine, sg, store, lessons):
        src = Path(mod.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
        assert "ModelRouter" not in src and "model_router" not in src, mod.__name__
        assert "INSERT INTO agent.gen_messages" not in src and "add_message" not in src, mod.__name__


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (g) cron chuông
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

P1_AI = {"boss.ai.done": False}      # P1 duy nhất từ dòng bắt buộc (có model, nguồn AI chưa đạt)


async def run(redis: Any, now: datetime) -> dict[str, Any]:
    return await cron.run_coach(sessionmaker(), redis, now)


async def bell_rows() -> list[Any]:
    return await rows("""SELECT user_id, title, body, link FROM core.notifications WHERE kind = 'gen.coach'
                         ORDER BY created_at, id""")


async def reset_bells() -> None:
    async with admin_sessionmaker()() as adm:
        await adm.execute(text("""UPDATE agent.gen_coach_prefs SET last_bell_at = NULL, last_bell_keys = '{}',
                                  last_seen_at = NULL"""))
        await adm.commit()


def test_cron_is_registered_in_the_worker() -> None:
    cj = next(c for c in WorkerSettings.cron_jobs if c.name == "cron:gen_coach")
    assert cj.hour == {9, 11, 14} and cj.minute == {5}
    assert JOB_LABELS["gen_coach"] == "Gen hướng dẫn"
    assert worker.gen_coach in WorkerSettings.functions


async def test_three_slots_twice_on_one_day_ring_at_most_once(owner_api: Api, redis: Any,
                                                              sigbox: list[sg.Signals]) -> None:
    sigbox[0] = mk_sig(state=P1_AI)
    outs = []
    for hh, mm in ((2, 5), (4, 5), (7, 5)):            # 09:05, 11:05, 14:05 giờ VN
        for _ in range(2):
            outs.append(await run(redis, at(0, hh, mm)))
    assert [o["bells"] for o in outs] == [1, 0, 0, 0, 0, 0]
    assert all(o["orgs"] == 1 and o["owners"] == 1 and o["errors"] == 0 for o in outs)
    b = await bell_rows()
    assert len(b) == 1
    assert b[0].title == "Hôm nay Sếp còn 1 việc cần làm" and b[0].body == cron.BELL_BODY
    assert b[0].link == "/overview?gen=coach"
    prefs = (await rows("SELECT last_bell_at, last_bell_keys, stable_since FROM agent.gen_coach_prefs"))[0]
    assert prefs.last_bell_at == at(0, 2, 5) and list(prefs.last_bell_keys) == ["boss.ai"]
    assert prefs.stable_since is None
    assert (await rows("SELECT count(*) FROM ops.telegram_outbox"))[0][0] == 0            # không đẩy Telegram


async def test_same_set_rings_again_after_three_days_only(owner_api: Api, redis: Any,
                                                          sigbox: list[sg.Signals]) -> None:
    sigbox[0] = mk_sig(state=P1_AI)
    assert (await run(redis, at(0, 2, 5)))["bells"] == 1
    assert (await run(redis, at(1, 2, 5)))["bells"] == 0
    assert (await run(redis, at(2, 2, 5)))["bells"] == 0
    assert (await run(redis, at(3, 2, 5)))["bells"] == 1           # cách lần trước đúng 3 ngày
    assert len(await bell_rows()) == 2


async def test_new_p1_key_rings_the_next_day(owner_api: Api, redis: Any, sigbox: list[sg.Signals]) -> None:
    sigbox[0] = mk_sig(state=P1_AI)
    assert (await run(redis, at(0, 2, 5)))["bells"] == 1
    sigbox[0] = mk_sig(state={**P1_AI, "backup.scheduled": False})
    assert (await run(redis, at(1, 2, 5)))["bells"] == 1           # backup.unset là khoá mới
    b = await bell_rows()
    assert [x.title for x in b] == ["Hôm nay Sếp còn 1 việc cần làm", "Hôm nay Sếp còn 2 việc cần làm"]
    assert (await run(redis, at(2, 2, 5)))["bells"] == 0
    keys = list((await rows("SELECT last_bell_keys FROM agent.gen_coach_prefs"))[0].last_bell_keys)
    assert keys == ["boss.ai", "backup.unset"]


async def test_health_and_hub_token_keys_never_count_as_new(owner_api: Api, redis: Any,
                                                            sigbox: list[sg.Signals]) -> None:
    sigbox[0] = mk_sig(alerts=[alert("channel.down", "bad", at(0, 1))], hub_expiring=True)
    assert (await run(redis, at(0, 2, 5)))["bells"] == 0          # chỉ health.* / hub.token_expiring ⇒ không phải 'mới'
    assert (await run(redis, at(5, 2, 5)))["bells"] == 0
    sigbox[0] = mk_sig(state=P1_AI)
    assert (await run(redis, at(6, 2, 5)))["bells"] == 1
    sigbox[0] = mk_sig(state=P1_AI, alerts=[alert("disk.low", "bad", at(6, 5))], hub_expiring=True)
    assert (await run(redis, at(7, 2, 5)))["bells"] == 0          # thêm health.* + hub.token_expiring: vẫn không mới
    assert (await run(redis, at(9, 2, 5)))["bells"] == 1          # nhưng cùng tập đã ≥ 3 ngày ⇒ nhắc lại
    assert list((await rows("SELECT last_bell_keys FROM agent.gen_coach_prefs"))[0].last_bell_keys) == [
        "health.disk.low", "boss.ai", "hub.token_expiring"]


async def test_p3_hints_never_ring_the_bell(owner_api: Api, redis: Any, sigbox: list[sg.Signals]) -> None:
    """Gen-hub / Truy cập từ xa chưa nối chỉ là gợi ý P3 (không chuông); chỉ dòng bắt buộc (P1) mới rung."""
    sigbox[0] = mk_sig(state={f"boss.{k}.done": False for k in ("hub", "facebook", "agy", "claude", "telegram",
                                                                  "remote")})
    assert todo_keys(await today(owner_api)) == ["boss.hub", "boss.remote"]
    assert (await run(redis, at(0, 2, 5)))["bells"] == 0
    assert (await run(redis, at(5, 2, 5)))["bells"] == 0
    assert await bell_rows() == []
    sigbox[0] = mk_sig(state={"boss.ai.done": False, "boss.hub.done": False})
    assert (await run(redis, at(6, 2, 5)))["bells"] == 1


async def test_quiet_hours_block_the_bell_and_wrap_midnight(owner_api: Api, redis: Any, sigbox: list[sg.Signals],
                                                            clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(state=P1_AI)
    r = await owner_api.send("PATCH", "/gen/coach/prefs", {"quiet_start": 8, "quiet_end": 16})
    assert r.status_code == 200
    assert (await run(redis, at(0, 2, 5)))["bells"] == 0           # 09:05 giờ VN nằm trong 08–16
    assert (await run(redis, at(0, 10, 5)))["bells"] == 1          # 17:05 giờ VN ngoài giờ yên lặng
    await owner_api.send("PATCH", "/gen/coach/prefs", {"quiet_start": 22, "quiet_end": 7})
    await reset_bells()
    assert (await run(redis, at(1, 16, 0)))["bells"] == 0          # 23:00 giờ VN (vắt qua nửa đêm)
    assert (await run(redis, at(1, 21, 30)))["bells"] == 0         # 04:30 giờ VN
    assert (await run(redis, at(2, 2, 5)))["bells"] == 1           # 09:05 giờ VN


async def test_disabled_bell_off_snoozed_and_stable_ring_nothing(owner_api: Api, redis: Any,
                                                                 sigbox: list[sg.Signals],
                                                                 clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(state=P1_AI)
    for field in ("enabled", "bell"):
        await owner_api.send("PATCH", "/gen/coach/prefs", {field: False})
        assert (await run(redis, at(0, 2, 5)))["bells"] == 0, field
        await owner_api.send("PATCH", "/gen/coach/prefs", {field: True})
    await owner_api.send("PATCH", "/gen/coach/prefs", {"snooze_all_days": 1})    # clock = T0 ⇒ hoãn tới mai 10:00 VN
    assert (await run(redis, at(0, 4, 5)))["bells"] == 0
    assert (await run(redis, at(1, 2, 5)))["bells"] == 0           # chưa hết hạn hoãn (03:00 UTC)
    assert (await run(redis, at(1, 4, 5)))["bells"] == 1           # hết hạn hoãn ⇒ chuông
    # ổn định: không có P0/P1 suốt 8 ngày ⇒ không chuông (chỉ còn việc P3)
    await reset_bells()
    sigbox[0] = mk_sig(state={"followup.5.done": False})
    old = at(1, 4, 5) - timedelta(days=8)
    async with admin_sessionmaker()() as adm:
        await adm.execute(text("UPDATE agent.gen_coach_prefs SET stable_since = :s"), {"s": old})
        await adm.commit()
    assert (await run(redis, at(2, 2, 5)))["bells"] == 0
    assert (await rows("SELECT stable_since FROM agent.gen_coach_prefs"))[0].stable_since == old
    assert len(await bell_rows()) == 1


async def test_stable_system_with_stale_p1_history_does_not_ring(owner_api: Api, redis: Any,
                                                                 sigbox: list[sg.Signals]) -> None:
    sigbox[0] = mk_sig()                                      # không việc nào
    assert (await run(redis, at(0, 2, 5)))["bells"] == 0
    assert (await rows("SELECT stable_since FROM agent.gen_coach_prefs"))[0].stable_since == at(0, 2, 5)
    sigbox[0] = mk_sig(state=P1_AI)                         # P1 xuất hiện ⇒ mốc về NULL ⇒ chuông được (không ổn định)
    assert (await run(redis, at(8, 2, 5)))["bells"] == 1
    assert (await rows("SELECT stable_since FROM agent.gen_coach_prefs"))[0].stable_since is None


async def test_card_seen_today_blocks_the_bell_but_not_tomorrow(owner_api: Api, redis: Any,
                                                                sigbox: list[sg.Signals],
                                                                clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(state=P1_AI)
    clock[0] = at(0, 1, 0)                                     # 08:00 giờ VN, Sếp mở thẻ
    await today(owner_api, mark=True)
    assert (await run(redis, at(0, 2, 5)))["bells"] == 0       # đã xem thẻ hôm nay
    assert (await run(redis, at(0, 7, 5)))["bells"] == 0
    assert (await run(redis, at(1, 2, 5)))["bells"] == 1       # ngày mai chưa xem ⇒ chuông


async def test_bell_link_follows_gen_availability(owner_api: Api, redis: Any, sigbox: list[sg.Signals]) -> None:
    sigbox[0] = mk_sig(state=P1_AI)
    await run(redis, at(0, 2, 5))
    assert (await bell_rows())[-1].link == "/overview?gen=coach"
    r = await owner_api.send("PATCH", "/gen/settings", {"enabled": False})
    assert r.status_code == 200 and r.json()["available"] is False
    await reset_bells()
    await run(redis, at(1, 2, 5))
    assert (await bell_rows())[-1].link == "/guide/viec-sep"
    await owner_api.send("PATCH", "/gen/settings", {"enabled": True})
    await reset_bells()
    await run(redis, at(2, 2, 5))
    assert (await bell_rows())[-1].link == "/overview?gen=coach"


async def test_every_active_owner_gets_one_bell_and_others_none(owner_api: Api, client: Any, db: Any, redis: Any,
                                                               sigbox: list[sg.Signals]) -> None:
    org, first = await owner_of(db)
    async with admin_sessionmaker()() as adm:
        second = (await adm.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                            VALUES (:o, 'owner2@example.vn', 'Owner hai', 'x') RETURNING id"""),
                                    {"o": org})).scalar_one()
        gone = (await adm.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash, is_active)
                                          VALUES (:o, 'owner3@example.vn', 'Owner nghỉ', 'x', false) RETURNING id"""),
                                  {"o": org})).scalar_one()
        for uid in (second, gone):
            await adm.execute(text("""INSERT INTO core.user_roles (user_id, role_id)
                                      SELECT :u, id FROM core.roles WHERE code = 'owner'"""), {"u": uid})
        await adm.commit()
    other = await login_as(client, db, "manager")
    await other.c.aclose()
    sigbox[0] = mk_sig(state=P1_AI)
    out = await run(redis, at(0, 2, 5))
    assert out["owners"] == 2 and out["bells"] == 2
    assert {b.user_id for b in await bell_rows()} == {first, second}
    assert (await run(redis, at(0, 4, 5)))["bells"] == 0
    assert (await rows("SELECT count(*) FROM agent.gen_coach_prefs"))[0][0] == 2


async def test_claim_bell_is_one_atomic_update(owner_api: Api, db: Any) -> None:
    org, uid = await owner_of(db)
    src = inspect.getsource(store.claim_bell)
    assert src.count("UPDATE agent.gen_coach_prefs") == 1 and "RETURNING user_id" in src
    assert "last_seen_at IS NULL OR last_seen_at < :today_start" in src
    assert "last_bell_at IS NULL OR last_bell_at < :today_start" in src
    async with sessionmaker()() as s:
        await store.ensure_prefs(s, org, uid)
        await s.commit()
    start = at(0, 0, 0) - timedelta(hours=7)
    sm = sessionmaker()

    async def go(i: int) -> bool:
        async with sm() as s:
            ok = await store.claim_bell(s, uid, start, [f"k{i}"], at(0, 2, 5))
            await s.commit()
            return ok

    got = await asyncio.gather(*(go(i) for i in range(6)))
    assert sorted(got) == [False] * 5 + [True]
    async with sm() as s:                                 # đã xem thẻ hôm nay ⇒ không claim được dù chưa chuông
        await s.execute(text("UPDATE agent.gen_coach_prefs SET last_bell_at = NULL, last_seen_at = :n"),
                        {"n": at(0, 1)})
        await s.commit()
        assert await store.claim_bell(s, uid, start, ["x"], at(0, 2, 5)) is False


async def test_worker_function_runs_the_whole_job(owner_api: Api, redis: Any, sigbox: list[sg.Signals]) -> None:
    out = await worker.gen_coach({"redis_bus": redis})
    assert out["orgs"] == 1 and out["errors"] == 0 and set(out) == {"orgs", "owners", "bells", "errors"}


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (j) Bản tin: dòng x/N
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

REQ_LINE = "Việc bắt buộc: đã đạt {x}/{n}, xem Việc Sếp cần làm"
SECS = [{"key": "tasks_due", "title": "Việc đến hạn", "count": 1, "lines": ["Gọi lại anh Bình"], "link": "/tasks"},
        {"key": "calendar_today", "title": "Lịch hôm nay", "count": 0, "lines": [], "link": "/connections#genhub",
         "state": "empty", "external": True, "detail": None},
        {"key": "drafts_pending", "title": "Nháp chờ duyệt", "count": 2, "lines": ["ACT-1", "ACT-2"],
         "link": "/workbench"}]


def _content(**kw: Any) -> dict[str, Any]:
    slot = briefing.Slot(datetime(2026, 10, 12, 7, 30, tzinfo=briefing.VN_TZ), "sáng")
    base: dict[str, Any] = {"summary": "Tóm tắt thử", "summary_source": "model", "needs_api_key": False,
                            "summary_failed": False}
    return briefing.build_content(slot, SECS, **{**base, **kw})


def test_build_content_adds_exactly_one_line_only_below_total() -> None:
    base = _content()
    line = {"kind": "say", "text": REQ_LINE.format(x=1, n=3)}
    with_ = _content(required=(1, 3))
    assert with_["steps"] == base["steps"][:2] + [line] + base["steps"][2:]       # ngay sau bước tóm tắt, trước các mục
    assert with_["sections"] == base["sections"] and base["hub_at"] == 3 and with_["hub_at"] == 4
    assert with_["steps"][with_["hub_at"]] == base["steps"][base["hub_at"]]          # hub_at vẫn trỏ đúng chỗ
    for same in (None, (3, 3), (4, 3)):
        assert _content(required=same)["steps"] == base["steps"], same
    assert sum(1 for s in _content(required=(0, 3))["steps"] if str(s.get("text", "")).startswith("Việc bắt buộc")) == 1
    no_sum = _content(summary=None, summary_source="none", needs_api_key=True, required=(0, 3))
    assert no_sum["steps"][:2] == [{"kind": "tool", "name": "briefing.sources"},
                                   {"kind": "say", "text": REQ_LINE.format(x=0, n=3)}]
    failed = _content(summary=None, summary_source="none", summary_failed=True, required=(1, 3))
    assert failed["steps"][1] == {"kind": "say", "text": briefing.SUMMARY_FAILED}
    assert failed["steps"][2] == {"kind": "say", "text": REQ_LINE.format(x=1, n=3)}
    # chuông + Telegram chỉ tính từ `sections` + tóm tắt ⇒ không đổi
    from gh.telegram import service as telegram
    assert briefing.body_text(with_["sections"], False) == briefing.body_text(base["sections"], False)
    assert telegram.briefing_text("sáng 12/10", "Tóm tắt thử", with_["sections"]) == telegram.briefing_text(
        "sáng 12/10", "Tóm tắt thử", base["sections"])


async def test_briefing_run_adds_the_required_line_without_changing_anything_else(
        owner_api: Api, db: Any, redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    org, _ = await owner_of(db)
    await api_provider(db, org, "openrouter", 2, [FAKE_KEY])              # có nguồn AI ⇒ _summarize chạy
    captured: list[str] = []
    box: dict[str, Any] = {"required": None}

    async def fake_summary(router: Any, org_: Any, sections: list[dict[str, Any]], notes: Any = None) -> str:
        captured.append(orjson.dumps(sections).decode())
        return "Tóm tắt thử"

    async def fake_overview(db_: Any, org_: Any) -> dict[str, Any]:
        if box["required"] is None:
            raise RuntimeError("không đọc được")
        return {"required_done": box["required"][0], "required_total": box["required"][1], "rows": [], "results": {}}

    monkeypatch.setattr(briefing, "_summarize", fake_summary)
    monkeypatch.setattr(boss_service, "overview", fake_overview)
    for req, when in ((None, briefing_today(7, 31)), ((0, 1), briefing_today(17, 31)),
                      ((1, 1), briefing_today(7, 31) + timedelta(days=1))):
        box["required"] = req
        await briefing.run_briefing(sessionmaker(), redis, briefing_router(redis), now=when)
    msgs = await briefing_messages(db)
    assert len(msgs) == 3 and len(captured) == 3

    def req_lines(c: dict[str, Any]) -> list[int]:
        return [i for i, s in enumerate(c["steps"]) if str(s.get("text", "")).startswith("Việc bắt buộc")]

    assert req_lines(msgs[0]) == [] and req_lines(msgs[2]) == []         # không đọc được / đã đạt đủ ⇒ không dòng
    assert req_lines(msgs[1]) == [2]                                     # steps: tool, tóm tắt, dòng x/N
    assert msgs[1]["steps"][2] == {"kind": "say", "text": REQ_LINE.format(x=0, n=1)}
    assert msgs[0]["steps"][:2] == [{"kind": "tool", "name": "briefing.sources"},
                                    {"kind": "say", "text": "Tóm tắt thử"}]
    assert msgs[0]["sections"] == msgs[1]["sections"] == msgs[2]["sections"]
    assert captured[0] == captured[1] == captured[2]                     # đầu vào của tóm tắt không có dòng x/N
    b = await briefing_bells(db)
    assert len(b) == 3 and b[0].body == b[1].body == b[2].body
    non_req = [[s for s in c["steps"] if not str(s.get("text", "")).startswith("Việc bắt buộc")] for c in msgs]
    assert non_req[0] == non_req[1] == non_req[2]


async def test_briefing_survives_an_unreadable_boss_overview(owner_api: Api, db: Any, redis: Any,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(db_: Any, org_: Any) -> Any:
        raise RuntimeError("hỏng")

    monkeypatch.setattr(boss_service, "overview", boom)
    out = await briefing.run_briefing(sessionmaker(), redis, briefing_router(redis), now=briefing_today(7, 31))
    assert list(out.values())[-1] == "sent"
    msgs = await briefing_messages(db)
    assert len(msgs) == 1 and not any("Việc bắt buộc" in str(s.get("text", "")) for s in msgs[0]["steps"])


async def test_briefing_on_a_fresh_machine_shows_zero_of_one(owner_api: Api, db: Any, redis: Any) -> None:
    await briefing.run_briefing(sessionmaker(), redis, briefing_router(redis), now=briefing_today(7, 31))
    msgs = await briefing_messages(db)
    says = [s["text"] for s in msgs[0]["steps"] if s["kind"] == "say"]
    assert REQ_LINE.format(x=0, n=1) in says and says.count(REQ_LINE.format(x=0, n=1)) == 1
    # chưa có nguồn AI ⇒ không có bước tóm tắt ⇒ dòng x/N nằm ngay sau bước tool
    assert msgs[0]["steps"][1] == {"kind": "say", "text": REQ_LINE.format(x=0, n=1)}


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (k) Gen: tool coach.status + khối prompt
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def test_coach_status_is_registered_everywhere() -> None:
    assert "coach.status" in envelope.DATA_TOOL_NAMES
    assert "coach.status" in get_args(envelope.DataToolName)
    assert set(envelope.DATA_TOOL_NAMES) == set(get_args(envelope.DataToolName))
    assert "coach.status" in gen_engine.AGY_SAFE_TOOLS
    t = tools.TOOLS["coach.status"]
    assert t.owner_only is True and t.permissions == ("system.manage",) and t.path is None
    assert t.description == "Việc vận hành Sếp cần làm ngay, tiến độ x/N, bài học hôm nay và danh sách bài"
    assert "5–11, 13, 14" in tools.TOOLS["guide.list"].description
    step = envelope.parse('{"steps":[{"kind":"tool","name":"coach.status","args":{}}]}').steps[0]
    assert isinstance(step, envelope.ToolCall) and step.name == "coach.status"


async def test_coach_status_tool_result_is_small_static_and_does_not_mark_shown(
        owner_api: Api, app: Any, sigbox: list[sg.Signals], clock: list[datetime]) -> None:
    sigbox[0] = mk_sig(state={"model.bound": False, "followup.5.done": False}, required=(0, 1))
    o, tok = await _user_of(owner_api)
    runner = ToolRunner(app, o, tok)
    res = await runner.run("coach.status", {})
    assert res.ok and len(res.text.encode()) <= 4000
    data = res.data
    assert set(data) == {"enabled", "todos", "progress", "lesson", "lessons"}
    assert [t["key"] for t in data["todos"]] == ["model.missing", "followup"]
    # v0.1.59: model.missing chỉ đường bằng `link` (màn Thiết lập nguồn AI), không còn `target`.
    assert data["todos"][0]["link"] == "/setup?buoc=nguon-ai" and "target" not in data["todos"][0]
    assert data["todos"][1]["target"] == "overview.setup_followup" and "n" not in data["todos"][1]   # v0.1.70: gộp
    assert data["progress"]["required_done"] == 0 and data["progress"]["required_total"] == 1
    assert data["lesson"]["id"] == "N01" and set(data["lesson"]) == {"id", "k", "total", "title", "status"}
    assert len(data["lessons"]) == 19 and set(data["lessons"][0]) == {"id", "title", "status"}
    assert '"detail"' not in res.text and '"message"' not in res.text
    assert (await rows("SELECT count(*) FROM agent.gen_coach_items"))[0][0] == 0          # hỏi Gen ≠ đã xem thẻ
    assert await rows("SELECT * FROM agent.gen_coach_prefs") == []
    g = await runner.run("guide.list", {})
    assert g.ok and len(g.text.encode()) <= 4000 and len(g.data) == 9


async def test_non_owner_cannot_use_coach_status(owner_api: Api, client: Any, app: Any, db: Any) -> None:
    mgr = await login_as(client, db, "manager")
    try:
        u, tok = await _user_of(mgr)
        assert (await ToolRunner(app, u, tok).run("coach.status", {})).error == "FORBIDDEN"
        assert "coach.status" not in {t.name for t in tools.tools_for(u)}
    finally:
        await mgr.c.aclose()


async def test_gen_turn_with_coach_question_gets_the_block_and_runs_the_tool(
        owner_api: Api, app: Any, db: Any) -> None:
    router = FakeRouter([
        {"steps": [{"kind": "tool", "name": "coach.status", "args": {}}]},
        {"steps": [{"kind": "say", "text": "Sếp cần chọn model cho Gen trước ạ."},
                   {"kind": "suggest", "items": [{"label": "Chỉ cho em", "action": {"type": "tour", "steps": [
                       {"screen": "api", "target": "api.bindings", "message": "Chọn model cho Gen ở đây"}]}}]},
                   {"kind": "done"}]},
    ])
    t = await ask(owner_api, app, router, "Hôm nay em cần làm gì?")
    assert t["status"] == "done"
    system = router.calls[0][0].content
    assert system.count(engine.BLOCK_HEADER) == 1
    assert "model.missing · Gen chưa có model để trả lời · /setup?buoc=nguon-ai" in system
    assert "boss.hub · Nối Gen-hub · boss_checks.row.hub" in system
    block = system[system.index(engine.BLOCK_HEADER):].split("\n\n")[0]
    assert len(block) <= 500
    assert "Việc vận hành (tool coach.status" in system and "coach.status: Việc vận hành Sếp cần làm ngay" in system
    assert kinds(t) == ["tool", "say", "suggest"]
    obs = router.calls[1][-1].content
    assert obs.startswith("Kết quả / phản hồi của hệ thống:") and "[kết quả coach.status]" in obs
    assert "model.missing" in obs
    sug = next(s["step"] for s in t["steps"] if s["step"]["kind"] == "suggest")
    target = sug["items"][0]["action"]["steps"][0]["target"]
    assert gen_registry.resolve_target(target) is not None and target == "api.bindings"
    # chỉ đọc: không đánh dấu đã xem, không ghi mốc, không ghi hội thoại ngoài lượt chat
    assert (await rows("SELECT count(*) FROM agent.gen_coach_items"))[0][0] == 0
    assert await rows("SELECT * FROM agent.gen_coach_prefs") == []


async def test_other_questions_get_no_coach_block(owner_api: Api, app: Any) -> None:
    for q in ("doanh thu tuần này", "lịch hôm nay"):
        router = FakeRouter([{"steps": [{"kind": "say", "text": "Dạ."}]}])
        t = await ask(owner_api, app, router, q)
        assert t["status"] == "done"
        assert engine.BLOCK_HEADER not in router.calls[0][0].content, q


async def test_staff_question_never_gets_the_coach_block(owner_api: Api, client: Any, app: Any, db: Any) -> None:
    org, _ = await owner_of(db)
    await gen_store.save_settings(db, org, {"enabled": True, "roles": ["owner", "manager"], "retention_days": 90})
    await db.commit()
    mgr = await login_as(client, db, "manager")
    try:
        router = FakeRouter([{"steps": [{"kind": "say", "text": "Dạ."}]}])
        t = await ask(mgr, app, router, "Hôm nay em cần làm gì?")
        assert t["status"] == "done"
        system = router.calls[0][0].content
        assert engine.BLOCK_HEADER not in system and "coach.status:" not in system
    finally:
        await mgr.c.aclose()


async def test_coach_block_failure_does_not_break_the_turn(owner_api: Api, app: Any,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("hỏng")

    monkeypatch.setattr(sg, "collect", boom)
    router = FakeRouter([{"steps": [{"kind": "say", "text": "Dạ."}]}])
    t = await ask(owner_api, app, router, "em cần làm gì?")
    assert t["status"] == "done" and kinds(t) == ["say"]
    assert engine.BLOCK_HEADER not in router.calls[0][0].content


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# (l) migration + RLS
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def test_migration_0033_follows_0032() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "migrations"))
    rev = ScriptDirectory.from_config(cfg).get_revision("0033")
    assert rev is not None and rev.down_revision == "0032"


async def test_migration_0033_is_rerunnable_and_constrained(owner_api: Api, fresh_db: str, db: Any) -> None:
    from migrations import sqlfile  # type: ignore[import-not-found]

    sql = (sqlfile.sql_dir() / "0033_v0154_gen_coach.sql").read_text(encoding="utf-8")
    org, uid = await owner_of(db)
    await db.execute(text("""INSERT INTO agent.gen_coach_prefs (user_id, org_id, lessons_per_day)
                             VALUES (:u, :o, 2)"""), {"u": uid, "o": org})
    await db.execute(text("""INSERT INTO agent.gen_coach_items (user_id, item_key, org_id)
                             VALUES (:u, 'lesson:N01', :o)"""), {"u": uid, "o": org})
    await db.commit()
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:        # đã migrate một lần; chạy thêm hai lần nữa
        c.execute(sql)  # type: ignore[arg-type]
        c.execute(sql)  # type: ignore[arg-type]
    assert (await rows("SELECT lessons_per_day FROM agent.gen_coach_prefs"))[0].lessons_per_day == 2  # còn nguyên
    assert (await rows("SELECT count(*) FROM agent.gen_coach_items"))[0][0] == 1
    idx = await rows("""SELECT indexdef FROM pg_indexes
                        WHERE schemaname = 'agent' AND indexname = 'gen_coach_items_org_idx'""")
    assert idx and "(org_id, user_id, status)" in idx[0].indexdef
    pols = await rows("""SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'agent'
                         AND tablename IN ('gen_coach_prefs', 'gen_coach_items') ORDER BY 1""")
    assert [(p.tablename, p.policyname) for p in pols] == [("gen_coach_items", "org_isolation"),
                                                          ("gen_coach_prefs", "org_isolation")]
    grants = await rows("""SELECT table_name, privilege_type FROM information_schema.role_table_grants
                           WHERE grantee = 'gh_app' AND table_schema = 'agent'
                           AND table_name IN ('gen_coach_prefs', 'gen_coach_items')""")
    assert {(g.table_name, g.privilege_type) for g in grants} == {
        (t, p) for t in ("gen_coach_prefs", "gen_coach_items") for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    ok_org, ok_uid = org, uid
    bad = [
        "INSERT INTO agent.gen_coach_prefs (user_id, org_id) VALUES (:u, :o)",                                # PK trùng
        "UPDATE agent.gen_coach_prefs SET lessons_per_day = 3",
        "UPDATE agent.gen_coach_prefs SET lessons_per_day = -1",
        "UPDATE agent.gen_coach_prefs SET quiet_start = 24",
        "UPDATE agent.gen_coach_prefs SET quiet_end = -1",
        "INSERT INTO agent.gen_coach_items (user_id, item_key, org_id) VALUES (:u, 'lesson:N01', :o)",   # PK trùng
        "INSERT INTO agent.gen_coach_items (user_id, item_key, org_id) VALUES (:u, 'ab', :o)",           # khoá quá ngắn
        "INSERT INTO agent.gen_coach_items (user_id, item_key, org_id, status) VALUES (:u, 'tip:x', :o, 'zzz')",
        "INSERT INTO agent.gen_coach_items (user_id, item_key, org_id, shown_count) VALUES (:u, 'tip:y', :o, -1)",
    ]
    for q in bad:
        with pytest.raises(DBAPIError):
            await db.execute(text(q), {"u": ok_uid, "o": ok_org})
        await db.rollback()
    for q in ("UPDATE agent.gen_coach_prefs SET quiet_start = 0, quiet_end = 23, lessons_per_day = 0",
              "INSERT INTO agent.gen_coach_items (user_id, item_key, org_id, status) "
              "VALUES (:u, 'tip:ok', :o, 'dismissed')"):
        await db.execute(text(q), {"u": ok_uid, "o": ok_org})
    await db.commit()


@pytest.mark.parametrize("table", ["agent.gen_coach_prefs", "agent.gen_coach_items"])
async def test_new_tables_rls_isolate_orgs(owner_api: Api, db: Any, table: str) -> None:
    org_a, user_a = await owner_of(db)
    async with admin_sessionmaker()() as adm:
        org_b = (await adm.execute(text("""INSERT INTO core.organizations (name) VALUES ('Tổ chức B (RLS 0033)')
                                           RETURNING id"""))).scalar_one()
        org_c = (await adm.execute(text("""INSERT INTO core.organizations (name) VALUES ('Tổ chức C (RLS 0033)')
                                           RETURNING id"""))).scalar_one()
        mk_user = """INSERT INTO core.users (org_id, email, display_name, password_hash)
                     VALUES (:o, :e, 'Người dùng', 'x') RETURNING id"""
        user_b = (await adm.execute(text(mk_user), {"o": org_b, "e": "b@example.vn"})).scalar_one()
        user_c = (await adm.execute(text(mk_user), {"o": org_c, "e": "c@example.vn"})).scalar_one()
        if table.endswith("prefs"):
            ins = "INSERT INTO agent.gen_coach_prefs (user_id, org_id) VALUES (:u, :o)"
        else:
            ins = "INSERT INTO agent.gen_coach_items (user_id, item_key, org_id) VALUES (:u, 'tip:rls', :o)"
        for u, o in ((user_a, org_a), (user_b, org_b)):
            await adm.execute(text(ins), {"u": u, "o": o})
        await adm.commit()
    await db.rollback()
    await _as_low_priv(db, table)
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_a)})
    assert (await db.execute(text(f"SELECT org_id FROM {table}"))).scalars().all() == [org_a]      # noqa: S608
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_b)})
    assert (await db.execute(text(f"SELECT org_id FROM {table}"))).scalars().all() == [org_b]      # noqa: S608
    with pytest.raises(Exception, match="row-level security|row_level_security"):
        await db.execute(text(ins), {"u": user_c, "o": org_c})


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
# bổ sung: hình dạng nội dung, câu tĩnh dự phòng
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def test_loaded_content_keeps_the_shape_of_the_files() -> None:
    n01 = lessons.load_lessons()[0]
    assert n01["id"] == "N01" and n01["kind"] == "N" and n01["try"] == {"label": "Thử ngay", "target": "api.bindings"}
    n03 = next(x for x in lessons.load_lessons() if x["id"] == "N03")
    assert set(n03) == {"id", "order", "title", "body", "kind", "try"}            # không bịa trường tuỳ chọn
    assert next(x for x in lessons.load_lessons() if x["id"] == "N02")["unlock"] == ["model.bound"]
    assert next(x for x in lessons.load_lessons() if x["id"] == "N04")["done_signal"] == "pin.set"
    memory = next(t for t in lessons.load_tips() if t["key"] == "tip-memory")
    assert set(memory) == {"key", "topic", "when", "title", "body"}
    assert lessons.load_lessons() is lessons.load_lessons() and lessons.load_tips() is lessons.load_tips()   # lru_cache


def test_every_static_rule_has_its_own_copy_and_unknown_rows_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    for rule in sg.TODO_RULES:
        if rule.kind != "health":
            assert rule.key in sg.TODO_COPY, rule.key
            title, why = sg.todo_copy(rule.key)
            assert title and why and "message" not in why.lower() and "detail" not in why.lower()
    new_row = {"row": 10, "key": "moi", "title": "Dòng mới", "optional": False, "checks": ["moi"]}
    monkeypatch.setattr(boss_service, "ROWS", (*boss_service.ROWS, new_row))
    title, why = sg.todo_copy("boss.moi")
    assert "Dòng mới" in title and why
    assert sg.todo_copy("khoa.la")[0] == sg.GENERIC_HEALTH_TITLE


def test_a_failed_p01_source_never_starts_or_extends_the_stable_count() -> None:
    quiet = mk_sig()
    assert quiet.p01_known() is True
    for name in ("health", "boss", "followup", "settings", "hub"):
        broken = mk_sig()
        broken.failed = [name]
        assert broken.p01_known() is False
        assert today_of(broken, Prefs()).stable_since is None                       # không bắt đầu đếm
        kept = today_of(broken, Prefs(stable_since=T0 - timedelta(days=30)), now=T0)
        assert kept.stable_since == T0 - timedelta(days=30) and kept.stable is False    # giữ mốc, không báo ổn định
    harmless = mk_sig()
    harmless.failed = ["offsite", "telegram"]                                         # nguồn không sinh việc P0/P1
    assert harmless.p01_known() is True and today_of(harmless, Prefs()).stable_since == T0


async def test_cron_with_a_failed_source_does_not_touch_the_stable_mark(owner_api: Api, redis: Any,
                                                                        sigbox: list[sg.Signals]) -> None:
    broken = mk_sig()
    broken.failed = ["boss"]
    sigbox[0] = broken
    assert (await run(redis, at(0, 2, 5)))["bells"] == 0
    assert (await rows("SELECT stable_since FROM agent.gen_coach_prefs"))[0].stable_since is None
    sigbox[0] = mk_sig()
    await run(redis, at(1, 2, 5))
    assert (await rows("SELECT stable_since FROM agent.gen_coach_prefs"))[0].stable_since == at(1, 2, 5)
