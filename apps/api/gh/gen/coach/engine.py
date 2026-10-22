"""Bộ dựng thẻ "Hôm nay của Sếp" (v0.1.54, g1-api) — THUẦN: không I/O, không đọc đồng hồ (`now`, `tz` truyền vào).

Đầu vào: `Signals` (gh.gen.coach.signals), `Prefs` + `Item` (trạng thái theo Owner, store đọc từ CSDL), `now`,
múi giờ tổ chức. Đầu ra: `Plan` — payload của `GET /gen/coach/today` + danh sách mục cần ghi "đã hiện" + mốc ổn
định mới.

Luật chính:
- Việc: xếp P0 > P1 > P2 > P3, cùng mức theo thứ tự bảng `TODO_RULES`; bỏ việc đang hoãn / Sếp đã chọn không dùng
  (việc P0 KHÔNG tắt được — dù hàng cũ ghi 'dismissed' thì vẫn hiện); cắt 3 việc. `can_dismiss` = P1 hoặc P3.
- Mẹo "Sếp biết chưa?": điều kiện `when` đúng, chưa hiện hoặc đã hiện HÔM NAY (giữ ổn định trong ngày), không trùng chủ
  đề với việc đang hiện; chọn 1 theo thứ tự tips.json. Hệ thống ổn định thì không có mẹo.
- Bài học: giữ bài đã hiện hôm nay; không thì bài kế tiếp trong lộ trình chưa hiểu / chưa xong / chưa hoãn, đã mở khoá,
  chưa đạt `done_signal`, và số bài đã hiện hôm nay < `lessons_per_day` (0 ⇒ không bài). Bài đã hiện ≥ 3 ngày mà Sếp
  chưa phản hồi coi như hoãn 7 ngày (store ghi thật khi `mark_shown`).
- Ổn định: 7 ngày liên tục không có việc P0/P1 và không có sự cố mới ⇒ "Hệ thống đã ổn định" (không mẹo, không chấm
  "mới", không chuông; bài học vẫn có).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from gh.gen.coach import signals as sg
from gh.textnorm import strip_accents

MAX_TODOS = 3
STABLE_AFTER = timedelta(days=7)
LESSON_STALE_AFTER = timedelta(days=3)
LESSON_SNOOZE = timedelta(days=7)
#: Bài đã hiện ≥ 3 ngày không phản hồi ⇒ hoãn 7 ngày kể từ lúc đó (3 + 7 ngày sau lần hiện đầu).
LESSON_RETURN_AFTER = LESSON_STALE_AFTER + LESSON_SNOOZE
BELL_REPEAT_AFTER = timedelta(days=3)
#: Khoá không tính là "mới" khi xét chuông: sự cố sức khoẻ đã có chuông riêng; token sắp hết hạn có chuông của hub_link.
BELL_SKIP_NEW_PREFIX = "health."
BELL_SKIP_NEW_KEYS = frozenset({"hub.token_expiring"})
ITEM_STATUSES = ("shown", "understood", "snoozed", "done", "dismissed")
LESSON_STATUSES = ("new", "shown", "understood", "snoozed", "done")


@dataclass(frozen=True)
class Prefs:
    enabled: bool = True
    bell: bool = True
    lessons_per_day: int = 1
    quiet_start: int = 21   # QD-20 (Sếp duyệt 10/10): yên lặng 21:00–07:00 (v0.1.54 từng đặt 22 — F121)
    quiet_end: int = 7
    snooze_until: datetime | None = None
    stable_since: datetime | None = None
    last_seen_at: datetime | None = None
    last_bell_at: datetime | None = None
    last_bell_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    """Trạng thái một mục theo Owner (khoá 'todo:<khoá>' | 'tip:<key>' | 'lesson:<id>' | 'card:setup_followup')."""
    key: str
    status: str = "shown"
    snooze_until: datetime | None = None
    shown_count: int = 0
    first_shown_at: datetime | None = None
    last_shown_at: datetime | None = None


@dataclass(frozen=True)
class Todo:
    key: str
    level: str
    title: str
    why: str
    target: str | None = None
    link: str | None = None
    raised_at: datetime | None = None
    order: int = 0
    #: Việc gộp (`followup`): khoá các `followup.<n>` đã gộp vào — để bài G<n> bị bỏ đúng và không đếm hai lần.
    members: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        can = self.level in ("P1", "P3")
        out: dict[str, Any] = {"key": self.key, "level": self.level, "title": self.title, "why": self.why}
        if self.target:
            out["target"] = self.target
        if self.link:
            out["link"] = self.link
        out["can_dismiss"] = can
        if can:
            out["dismiss_warning"] = sg.dismiss_warning_for(self.key)
        return out


@dataclass
class Plan:
    payload: dict[str, Any]
    #: item_key cần ghi "đã hiện" khi `mark_shown` (việc đang hiện, mẹo, bài học).
    shown: list[str] = field(default_factory=list)
    stable_since: datetime | None = None
    stable: bool = False
    #: Khoá P0/P1 đang hiện trên thẻ (đã lọc hoãn/không dùng, đã cắt 3) — cổng chuông.
    p01_keys: list[str] = field(default_factory=list)


# ─── thời gian ────────────────────────────────────────────────────────────────────────────────────────────────

def local_date(dt: datetime, tz: tzinfo) -> date:
    return dt.astimezone(tz).date()


def day_start_utc(now: datetime, tz: tzinfo) -> datetime:
    """0 giờ của ngày HIỆN TẠI theo múi giờ tổ chức, đổi sang UTC (mốc 'hôm nay' của cổng chuông)."""
    local = now.astimezone(tz)
    start = datetime(local.year, local.month, local.day, tzinfo=tz)
    return start.astimezone(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def active_snooze(item: Item | None, now: datetime) -> bool:
    return (item is not None and item.status == "snoozed" and item.snooze_until is not None
            and item.snooze_until > now)


# ─── việc ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _safe_link(link: Any) -> str | None:
    return link if isinstance(link, str) and link.startswith("/") and len(link) <= 300 else None


def candidate_todos(sig: sg.Signals) -> list[Todo]:
    """Mọi việc đang áp dụng theo `TODO_RULES`, đã xếp P0 > P1 > P2 > P3 (cùng mức theo thứ tự bảng)."""
    out: list[Todo] = []
    groups = sg.group_alerts(sig.alerts)
    for idx, rule in enumerate(sg.TODO_RULES):
        if rule.kind == "health":
            for g in groups:
                if g["severity"] != rule.severity:
                    continue
                out.append(Todo(key=f"health.{g['kind']}", level=rule.level, title=sg.health_title(g["kind"]),
                                why=sg.HEALTH_WHY[g["severity"]], link=_safe_link(g.get("link")),
                                raised_at=sg.parse_ts(g.get("raised_at")), order=idx))
            continue
        if rule.kind == "hub_token":
            applies = sig.hub_expiring
        else:
            applies = sg.eval_cond(rule.when, sig.state)
        if not applies:
            continue
        title, why = sg.todo_copy(rule.key)
        if rule.key == "drafts.pending" and sig.drafts_pending > 0:
            title = sg.drafts_title(sig.drafts_pending)
        out.append(Todo(key=rule.key, level=rule.level, title=title, why=why,
                        target=sg.TODO_TARGETS.get(rule.key), link=sg.TODO_LINKS.get(rule.key), order=idx))
    return sorted(out, key=lambda t: (sg.LEVELS.index(t.level), t.order))


@dataclass
class TodoPlan:
    visible: list[Todo]      # đã lọc hoãn / không dùng, CHƯA cắt
    pending_p01: bool        # còn việc P0/P1 chưa được Sếp chọn bỏ (hoãn vẫn tính là còn)
    last_alert_raised_at: datetime | None


def merge_followups(visible: Sequence[Todo], sig: sg.Signals, items: Mapping[str, Item], now: datetime) -> list[Todo]:
    """v0.1.70 (F031/F138): các việc P3 `followup.5`–`followup.10` còn hiện gộp thành MỘT việc `followup` đặt đúng chỗ
    của việc đầu tiên. Số "Còn N việc" đếm như thẻ "Việc thiết lập tiếp" ở Tổng quan (mọi việc tuỳ chọn chưa xong theo
    tín hiệu `followup.<n>.done`). Sếp tắt / hoãn chính việc gộp thì nó ẩn."""
    members = [t for t in visible if t.key.startswith("followup.")]
    if not members:
        return list(visible)
    item = items.get(f"todo:{sg.MERGED_FOLLOWUP_KEY}")
    if (item is not None and item.status in ("dismissed", "done", "understood")) or active_snooze(item, now):
        return [t for t in visible if t not in members]
    left = sum(1 for n in sg.FOLLOWUP_NS if sig.state.get(f"followup.{n}.done") is False)
    merged = Todo(key=sg.MERGED_FOLLOWUP_KEY, level="P3", title=sg.todo_copy(sg.MERGED_FOLLOWUP_KEY)[0],
                  why=sg.followup_why(max(left, len(members))), target=sg.TODO_TARGETS.get(sg.MERGED_FOLLOWUP_KEY),
                  order=members[0].order, members=tuple(t.key for t in members))
    out: list[Todo] = []
    for t in visible:
        if t is members[0]:
            out.append(merged)
        elif t not in members:
            out.append(t)
    return out


def plan_todos(sig: sg.Signals, items: Mapping[str, Item], now: datetime) -> TodoPlan:
    """Việc hiện (đã lọc hoãn / không dùng, đã gộp followup, CHƯA cắt 3). Thẻ "Việc thiết lập tiếp" đang hoãn ("Để sau
    7 ngày") ⇒ ẩn luôn MỌI việc P3 của Gen (P0/P1/P2 không bị ảnh hưởng)."""
    visible: list[Todo] = []
    pending = False
    p3_hidden = active_snooze(items.get(sg.CARD_FOLLOWUP), now)
    for t in candidate_todos(sig):
        item = items.get(f"todo:{t.key}")
        resolved = item is not None and item.status in ("dismissed", "done", "understood") and t.level != "P0"
        if resolved:
            continue
        if t.level in ("P0", "P1"):
            pending = True
        if active_snooze(item, now) or (p3_hidden and t.level == "P3"):
            continue
        visible.append(t)
    return TodoPlan(merge_followups(visible, sig, items, now), pending, sg.parse_ts(sig.last_alert_raised_at))


# ─── ổn định ──────────────────────────────────────────────────────────────────────────────────────────────────

def update_stable(prefs: Prefs, has_p0p1: bool, last_alert_raised_at: datetime | None,
                  now: datetime) -> datetime | None:
    """Mốc bắt đầu chuỗi ổn định: còn việc P0/P1 ⇒ NULL; chưa có mốc mà không còn ⇒ `now`; có sự cố bad/warn mở SAU mốc
    (kể cả đã đóng) ⇒ đặt lại = lúc đó."""
    if has_p0p1:
        return None
    since = prefs.stable_since
    if since is None:
        return now
    if last_alert_raised_at is not None and last_alert_raised_at > since:
        return last_alert_raised_at
    return since


def is_stable(stable_since: datetime | None, now: datetime) -> bool:
    return stable_since is not None and stable_since <= now - STABLE_AFTER


def settle(sig: sg.Signals, prefs: Prefs, tp: TodoPlan, now: datetime) -> tuple[datetime | None, bool]:
    """(mốc ổn định mới, đang ổn định?). Một nguồn sinh việc P0/P1 đọc lỗi ⇒ chưa biết có việc hay không: giữ nguyên mốc
    đang lưu và KHÔNG báo 'ổn định' (không để lỗi đọc nguồn thành 7 ngày 'yên')."""
    if not sig.p01_known():
        return prefs.stable_since, False
    since = update_stable(prefs, tp.pending_p01, tp.last_alert_raised_at, now)
    return since, is_stable(since, now)


# ─── bài học ──────────────────────────────────────────────────────────────────────────────────────────────────

def lesson_status(lesson: Mapping[str, Any], item: Item | None, state: Mapping[str, bool], now: datetime) -> str:
    """Trạng thái HIỆU LỰC của bài: new · shown · understood · snoozed · done (done_signal đạt = xong thật)."""
    if item is not None and item.status in ("understood", "done"):
        return item.status
    ds = lesson.get("done_signal")
    if ds and state.get(ds):
        return "done"
    if item is None:
        return "new"
    if item.status == "snoozed":
        if item.snooze_until is not None and item.snooze_until > now:
            return "snoozed"
        return "shown" if item.shown_count else "new"
    if item.status == "shown" and item.first_shown_at is not None:
        age = now - item.first_shown_at
        if age >= LESSON_STALE_AFTER:
            return "snoozed" if age < LESSON_RETURN_AFTER else "shown"
    return "shown"


def lesson_statuses(curr: Sequence[Mapping[str, Any]], items: Mapping[str, Item], state: Mapping[str, bool],
                    now: datetime) -> list[tuple[Mapping[str, Any], str]]:
    return [(lesson, lesson_status(lesson, items.get(f"lesson:{lesson['id']}"), state, now)) for lesson in curr]


def _dropped_guide_lessons(items: Mapping[str, Item], shown_todo_keys: set[str]) -> set[str]:
    """Bài G<n> bị bỏ khi việc `followup.<n>` đang nằm trong 3 việc hôm nay hoặc Sếp đã chọn không dùng việc đó."""
    out: set[str] = set()
    for n in sg.FOLLOWUP_TODO_NS:
        item = items.get(f"todo:followup.{n}")
        if f"followup.{n}" in shown_todo_keys or (item is not None and item.status == "dismissed"):
            out.add(f"G{n:02d}")
    return out


def pick_lesson(curr: Sequence[Mapping[str, Any]], items: Mapping[str, Item], state: Mapping[str, bool],
                prefs: Prefs, now: datetime, tz: tzinfo, dropped: set[str]) -> tuple[Mapping[str, Any], str] | None:
    if prefs.lessons_per_day <= 0:
        return None
    today = local_date(now, tz)
    rows = lesson_statuses(curr, items, state, now)

    def usable(lesson: Mapping[str, Any], status: str) -> bool:
        return (status in ("new", "shown") and lesson["id"] not in dropped
                and sg.eval_cond(lesson.get("unlock") or [], state))

    def shown_today(lesson: Mapping[str, Any]) -> bool:
        item = items.get(f"lesson:{lesson['id']}")
        return item is not None and item.last_shown_at is not None and local_date(item.last_shown_at, tz) == today

    for lesson, status in rows:                    # giữ bài đã hiện hôm nay (chưa phản hồi) — không đổi bài giữa ngày
        if shown_today(lesson) and usable(lesson, status):
            return lesson, status
    if sum(1 for lesson, _ in rows if shown_today(lesson)) >= prefs.lessons_per_day:
        return None
    for lesson, status in rows:
        if usable(lesson, status):
            return lesson, status
    return None


# ─── mẹo ──────────────────────────────────────────────────────────────────────────────────────────────────────

def pick_tip(tips: Sequence[Mapping[str, Any]], items: Mapping[str, Item], state: Mapping[str, bool],
             now: datetime, tz: tzinfo, busy_topics: set[str]) -> Mapping[str, Any] | None:
    today = local_date(now, tz)
    eligible: list[tuple[Mapping[str, Any], bool]] = []   # (mẹo, đã hiện hôm nay)
    for tip in tips:
        if not sg.eval_cond(tip.get("when") or [], state) or tip.get("topic") in busy_topics:
            continue
        item = items.get(f"tip:{tip['key']}")
        if item is None:
            eligible.append((tip, False))
        elif item.status == "shown" and item.last_shown_at is not None and local_date(item.last_shown_at, tz) == today:
            eligible.append((tip, True))
        elif _expired_snooze(item, now):
            eligible.append((tip, False))
    for tip, today_shown in eligible:       # mẹo đã hiện hôm nay giữ nguyên trong ngày
        if today_shown:
            return tip
    return eligible[0][0] if eligible else None


def _expired_snooze(item: Item, now: datetime) -> bool:
    return item.status == "snoozed" and (item.snooze_until is None or item.snooze_until <= now)


# ─── dựng thẻ ─────────────────────────────────────────────────────────────────────────────────────────────────

def _content(tips: Sequence[Mapping[str, Any]] | None,
             curr: Sequence[Mapping[str, Any]] | None) -> tuple[Sequence[Mapping[str, Any]],
                                                                Sequence[Mapping[str, Any]]]:
    from gh.gen.coach import lessons

    return (lessons.load_tips() if tips is None else tips), (lessons.curriculum() if curr is None else curr)


def plan_today(sig: sg.Signals, prefs: Prefs, items: Mapping[str, Item], now: datetime, tz: tzinfo, *,
               tips: Sequence[Mapping[str, Any]] | None = None,
               curr: Sequence[Mapping[str, Any]] | None = None) -> Plan:
    tips, curr = _content(tips, curr)
    tp = plan_todos(sig, items, now)
    shown_todos = tp.visible[:MAX_TODOS]
    stable_since, stable = settle(sig, prefs, tp, now)
    rows = lesson_statuses(curr, items, sig.state, now)
    progress = {"required_done": sig.required_done, "required_total": sig.required_total,
                "lessons_done": sum(1 for _, s in rows if s in ("understood", "done")),
                "lessons_total": len(curr), "stable": stable, "stable_since": _iso(stable_since)}
    snoozed_until = prefs.snooze_until if prefs.snooze_until is not None and prefs.snooze_until > now else None
    payload: dict[str, Any] = {"date": local_date(now, tz).isoformat(), "enabled": prefs.enabled,
                               "snoozed_until": _iso(snoozed_until), "todos": [], "tip": None, "lesson": None,
                               "progress": progress, "unseen": False}
    plan = Plan(payload=payload, stable_since=stable_since, stable=stable)
    if not prefs.enabled or snoozed_until is not None:
        return plan

    keys = {t.key for t in shown_todos} | {m for t in shown_todos for m in t.members}
    payload["todos"] = [t.payload() for t in shown_todos]
    plan.p01_keys = [t.key for t in shown_todos if t.level in ("P0", "P1")]
    plan.shown = [f"todo:{t.key}" for t in shown_todos]

    tip = None
    if not stable:
        busy = {tp_ for t in shown_todos if (tp_ := sg.topic_of(t.key))}
        tip = pick_tip(tips, items, sig.state, now, tz, busy)
    if tip is not None:
        payload["tip"] = {k: tip[k] for k in ("key", "title", "body")} | ({"try": tip["try"]} if tip.get("try") else {})
        plan.shown.append(f"tip:{tip['key']}")

    picked = pick_lesson(curr, items, sig.state, prefs, now, tz, _dropped_guide_lessons(items, keys))
    if picked is not None:
        lesson, status = picked
        out = {"id": lesson["id"], "k": lesson["k"], "total": len(curr), "title": lesson["title"],
               "body": lesson["body"]}
        if lesson.get("try"):
            out["try"] = lesson["try"]
        out["status"] = status
        payload["lesson"] = out
        plan.shown.append(f"lesson:{lesson['id']}")

    if not stable:
        payload["unseen"] = _unseen(shown_todos, tip, items)
    return plan


def _unseen(todos: Sequence[Todo], tip: Mapping[str, Any] | None, items: Mapping[str, Item]) -> bool:
    """Có điều Sếp CHƯA thấy: việc P0/P1 chưa từng hiện, sự cố sức khoẻ mở lại sau lần hiện cuối, hoặc mẹo chưa hiện.
    Bài học KHÔNG bật chấm "mới"."""
    for t in todos:
        item = items.get(f"todo:{t.key}")
        if t.level in ("P0", "P1") and (item is None or item.shown_count == 0):
            return True
        if (t.key.startswith("health.") and item is not None and item.last_shown_at is not None
                and t.raised_at is not None and t.raised_at > item.last_shown_at):
            return True
    if tip is not None:
        item = items.get(f"tip:{tip['key']}")
        if item is None or item.shown_count == 0:
            return True
    return False


def build_today(sig: sg.Signals, prefs: Prefs, items: Mapping[str, Item], now: datetime, tz: tzinfo, *,
                tips: Sequence[Mapping[str, Any]] | None = None,
                curr: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Payload của `GET /gen/coach/today` (xem `plan_today` cho phần kèm theo để ghi)."""
    return plan_today(sig, prefs, items, now, tz, tips=tips, curr=curr).payload


# ─── cập nhật trạng thái mục (thuần — store chỉ việc ghi) ──────────────────────────────────────────────────────

def after_shown(item: Item | None, key: str, now: datetime) -> Item:
    """Mục vừa được hiện trên thẻ (mark_shown). Bài học hết hạn hoãn / quá 10 ngày không phản hồi được tính lại từ đầu
    (đồng hồ 3 ngày chạy lại)."""
    if item is None:
        return Item(key, "shown", None, 1, now, now)
    status, snooze, first = item.status, item.snooze_until, item.first_shown_at
    lesson = key.startswith("lesson:")
    if status == "snoozed" and (snooze is None or snooze <= now):
        status, snooze = "shown", None
        if lesson:
            first = now
    elif status == "shown" and lesson and first is not None and now - first >= LESSON_RETURN_AFTER:
        first = now
    return Item(key, status, snooze, item.shown_count + 1, first or now, now)


def stale_lessons(items: Mapping[str, Item], now: datetime) -> list[Item]:
    """Bài đã hiện ≥ 3 ngày mà chưa phản hồi ⇒ ghi 'snoozed' tới (lần hiện đầu + 10 ngày)."""
    out: list[Item] = []
    for it in items.values():
        if (it.key.startswith("lesson:") and it.status == "shown" and it.first_shown_at is not None
                and LESSON_STALE_AFTER <= now - it.first_shown_at < LESSON_RETURN_AFTER):
            out.append(replace(it, status="snoozed", snooze_until=it.first_shown_at + LESSON_RETURN_AFTER))
    return out


def after_action(item: Item | None, key: str, action: str, days: int | None, now: datetime) -> Item | None:
    """Kết quả của POST /items/{item_key}. `restore` trên mục chưa có hàng ⇒ None (không có gì để ghi)."""
    base = item or Item(key, "shown", None, 0, None, None)
    if action == "restore":
        return None if item is None else replace(base, status="shown", snooze_until=None)
    if action == "snooze":
        return replace(base, status="snoozed", snooze_until=now + timedelta(days=days or 1))
    if action == "understood":
        return replace(base, status="understood", snooze_until=None)
    if action == "done":
        return replace(base, status="done", snooze_until=None)
    if action == "dismiss":
        return replace(base, status="dismissed", snooze_until=None)
    raise ValueError(f"Hành động lạ: {action}")


# ─── chuông ───────────────────────────────────────────────────────────────────────────────────────────────────

def in_quiet_hours(prefs: Prefs, now: datetime, tz: tzinfo) -> bool:
    """Giờ yên lặng theo múi giờ tổ chức; start > end ⇒ khoảng vắt qua nửa đêm; start == end ⇒ không có giờ yên lặng."""
    h = now.astimezone(tz).hour
    s, e = prefs.quiet_start, prefs.quiet_end
    if s == e:
        return False
    return s <= h < e if s < e else (h >= s or h < e)


def bell_due(prefs: Prefs, p01_keys: Sequence[str], stable: bool, now: datetime, tz: tzinfo) -> bool:
    """Điều kiện chuông (mục 8): bật + chuông bật + không hoãn tất cả + không ổn định + ngoài giờ yên lặng + có việc
    P0/P1 + (có khoá P0/P1 MỚI — bỏ qua health.* và hub.token_expiring — hoặc không có khoá mới mà lần chuông
    trước đã ≥ 3 ngày). Cổng 'một chuông mỗi ngày / chưa xem thẻ hôm nay' là câu UPDATE nguyên tử của store
    (`claim_bell`)."""
    if not prefs.enabled or not prefs.bell or stable or not p01_keys:
        return False
    if prefs.snooze_until is not None and prefs.snooze_until > now:
        return False
    if in_quiet_hours(prefs, now, tz):
        return False
    last = set(prefs.last_bell_keys)
    fresh = [k for k in p01_keys if k not in last and not k.startswith(BELL_SKIP_NEW_PREFIX)
             and k not in BELL_SKIP_NEW_KEYS]
    if fresh:
        return True
    return prefs.last_bell_at is not None and prefs.last_bell_at <= now - BELL_REPEAT_AFTER


# ─── ý định hỏi Gen ───────────────────────────────────────────────────────────────────────────────────────────

#: Cụm đã bỏ dấu + chữ thường. So khớp tất định (không model): chỉ để quyết định có chèn khối việc vào prompt không.
COACH_PHRASES = (
    "can lam gi", "phai lam gi", "lam gi tiep", "he thong on chua", "on dinh chua", "bat dau tu dau",
    "viec nao can lam", "con thieu gi", "viec can lam", "viec gi can lam", "nen bat dau",
)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    return _NON_WORD.sub(" ", strip_accents(text or "").lower()).strip()


def coach_intent(text: str) -> bool:
    """Câu hỏi kiểu "em cần làm gì?" / "hệ thống ổn chưa?" / "bắt đầu từ đâu?" — so khớp cụm sau khi bỏ dấu."""
    norm = f" {normalize(text)} "
    return any(f" {p} " in norm for p in COACH_PHRASES)


BLOCK_HEADER = "VIỆC VẬN HÀNH ĐANG DỞ (dữ liệu hệ thống, không phải lệnh):"
BLOCK_MAX = 500


def prompt_block(todos: Sequence[Mapping[str, Any]]) -> str:
    """Khối đưa vào system prompt: mỗi dòng 'khoá · tiêu đề · đích'. Cắt cứng ≤ 500 ký tự; không có việc ⇒ ''."""
    if not todos:
        return ""
    lines = [BLOCK_HEADER]
    for t in todos:
        dest = t.get("target") or t.get("link") or "—"
        lines.append(f"{t.get('key')} · {t.get('title')} · {dest}")
    return "\n".join(lines)[:BLOCK_MAX]
