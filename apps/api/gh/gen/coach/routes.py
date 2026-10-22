"""/gen/coach — API của Gen hướng dẫn (v0.1.54, g1-api). CHỈ Owner (người khác → 403 FORBIDDEN).

- `GET /today[?mark_shown=1]` — thẻ "Hôm nay của Sếp". Không `mark_shown` thì KHÔNG ghi mục nào (ngoại lệ duy nhất: mốc
  `stable_since` được cập nhật ở mọi lần gọi). `mark_shown=1` ghi mục đã hiện + `prefs.last_seen_at`.
- `POST /items/{item_key}` — understood / snooze (1, 3, 7 ngày) / done / dismiss (cần confirm, chỉ P1/P3) / restore;
  việc cần làm (`todo:*`) chỉ nhận snooze / dismiss / restore (understood, done → 422 COACH_ACTION_NOT_ALLOWED).
- `GET|PATCH /prefs` — tuỳ chọn (bật/tắt, chuông, số bài mỗi ngày, giờ yên lặng, hoãn tất cả).
- `GET /curriculum` — 19 bài + trạng thái.

Không gọi model, không ghi agent.gen_messages. Payload chỉ có khoá, tiêu đề tĩnh và số đếm.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import current_user
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, field_errors
from gh.gen.coach import engine, lessons, store
from gh.gen.coach import signals as sg
from gh.hub_link import KHO_LABEL, load_kho_label, relabel

log = logging.getLogger("gh.gen.coach")
router = APIRouter(prefix="/gen/coach", tags=["gen-coach"])

NOT_OWNER_TITLE = "Chỉ Sếp (Owner) dùng được Gen hướng dẫn"
UNKNOWN_TITLE = "Em không biết việc này"
DISMISS_P0_TITLE = "Việc khẩn cấp không tắt được — Sếp xử lý giúp em nhé"
DISMISS_P2_TITLE = "Việc này không tắt được — Sếp chọn Để mai giúp em nhé"
CONFIRM_TITLE = "Sếp xác nhận giúp em trước khi tắt việc này"
ACTION_TITLE = "Thao tác này không dùng được cho mục đó"
CARD_FOLLOWUP = sg.CARD_FOLLOWUP


def _now() -> datetime:
    """Đồng hồ của route — test thay bằng monkeypatch để giả lập ngày."""
    return datetime.now(UTC)


async def coach_owner(user: service.CurrentUser = Depends(current_user)) -> service.CurrentUser:
    if user.role_code != rbac.OWNER:
        raise ApiError(403, "FORBIDDEN", NOT_OWNER_TITLE)
    return user


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _relabel_rows(rows: list[dict[str, Any]], kho: str) -> list[dict[str, Any]]:
    """Thay tên Kho mặc định trong chữ hiển thị (title, body, try.label) bằng tên Kho hiệu lực (v0.1.57, Nợ #30)."""
    if kho == KHO_LABEL:
        return rows
    out = []
    for r in rows:
        r = {**r, **{k: relabel(r[k], kho) for k in ("title", "body") if isinstance(r.get(k), str)}}
        if isinstance(r.get("try"), dict) and isinstance(r["try"].get("label"), str):
            r["try"] = {**r["try"], "label": relabel(r["try"]["label"], kho)}
        out.append(r)
    return out


def _relabel_todos(todos: list[dict[str, Any]], kho: str) -> list[dict[str, Any]]:
    """Như `_relabel_rows` nhưng cho các việc tĩnh trên thẻ Hôm nay (title, why, dismiss_warning — thẻ "Nối Gen-hub"
    nhắc "Kho dữ liệu"): đổi sang tên Kho hiệu lực (v0.1.57, Nợ #30)."""
    if kho == KHO_LABEL:
        return todos
    return [{**t, **{k: relabel(t[k], kho) for k in ("title", "why", "dismiss_warning") if isinstance(t.get(k), str)}}
            for t in todos]


def load_content(kho: str = KHO_LABEL) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(mẹo, lộ trình). Nội dung hỏng / thiếu ⇒ ghi log lỗi rõ ràng rồi chạy tiếp KHÔNG có phần đó — thẻ việc cần làm
    không được chết vì tệp nội dung. `kho` = tên Kho hiệu lực của tổ chức: chữ "Kho dữ liệu" trong bài / mẹo dựng
    theo tên đó (v0.1.57)."""
    try:
        tips = [dict(t) for t in lessons.load_tips()]
    except ValueError as e:
        log.error("%s", e)
        tips = []
    try:
        curr = lessons.curriculum()
    except ValueError as e:
        log.error("%s", e)
        curr = [{**g, "k": k} for k, g in enumerate(lessons.guide_lessons(), start=1)]
    return _relabel_rows(tips, kho), _relabel_rows(curr, kho)


# ─── GET /today ──────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/today")
async def today(request: Request, mark_shown: bool = Query(False), user: service.CurrentUser = Depends(coach_owner),
                db: AsyncSession = DB) -> dict[str, Any]:
    now = _now()
    tz = await store.org_timezone(db, user.org_id)
    sig = await sg.collect(db, request.app.state.redis, user.org_id)
    prefs = await store.get_prefs(db, user.id)
    items = await store.list_items(db, user.id)
    kho = await load_kho_label(db, user.org_id)
    tips, curr = load_content(kho)
    plan = engine.plan_today(sig, prefs, items, now, tz, tips=tips, curr=curr)
    plan.payload["todos"] = _relabel_todos(plan.payload["todos"], kho)     # v0.1.57: việc tĩnh theo tên Kho hiệu lực
    if plan.stable_since != prefs.stable_since:
        await store.set_stable(db, user.org_id, user.id, plan.stable_since)
    if mark_shown and prefs.enabled and plan.payload["snoozed_until"] is None:
        await store.mark_shown(db, user.org_id, user.id, now, plan.shown, items)
    return plan.payload


# ─── POST /items/{item_key} ──────────────────────────────────────────────────────────────────────────────────

class CoachItemAction(BaseModel):
    action: Literal["understood", "snooze", "done", "dismiss", "restore"]
    days: Literal[1, 3, 7] | None = None
    confirm: bool | None = None


def _known(item_key: str, tips: list[dict[str, Any]], curr: list[dict[str, Any]]) -> str | None:
    """Loại mục ('todo' | 'tip' | 'lesson' | 'card') nếu khoá thuộc tập đã biết, ngược lại None."""
    kind, _, rest = item_key.partition(":")
    if kind == "todo" and sg.todo_key_known(rest):
        return kind
    if kind == "tip" and any(t["key"] == rest for t in tips):
        return kind
    if kind == "lesson" and any(x["id"] == rest for x in curr):
        return kind
    if item_key == CARD_FOLLOWUP:
        return "card"
    return None


async def _todo_level(db: AsyncSession, request: Request, org_id: uuid.UUID, key: str) -> str:
    """Mức hiện tại của việc: bảng cố định; health.<kind> là P0 khi nhóm sự cố đang 'bad', còn lại P1."""
    if level := sg.static_level(key):
        return level
    sig = await sg.collect(db, request.app.state.redis, org_id)
    for g in sg.group_alerts(sig.alerts):
        if f"health.{g['kind']}" == key:
            return "P0" if g["severity"] == "bad" else "P1"
    return "P1"


@router.post("/items/{item_key}", status_code=204)
async def item_action(item_key: str, body: CoachItemAction, request: Request,
                      user: service.CurrentUser = Depends(coach_owner), db: AsyncSession = DB) -> Response:
    tips, curr = load_content()
    kind = _known(item_key, tips, curr)
    if kind is None:
        raise ApiError(404, "COACH_ITEM_UNKNOWN", UNKNOWN_TITLE)
    if kind == "card" and body.action not in ("snooze", "restore"):
        raise ApiError(422, "COACH_ACTION_NOT_ALLOWED", ACTION_TITLE)
    # Việc cần làm (todo) chỉ hoãn / tắt (qua kiểm mức + xác nhận) / bật lại: "đã hiểu"/"đã làm" sẽ giải quyết vĩnh
    # viễn việc không phải P0 mà không qua kiểm xác nhận, và danh sách "Bật lại" không liệt kê ⇒ lách luật P2.
    if kind == "todo" and body.action in ("understood", "done"):
        raise ApiError(422, "COACH_ACTION_NOT_ALLOWED", ACTION_TITLE)
    if body.action == "snooze" and body.days is None:
        raise field_errors({"days": "Chọn hoãn 1, 3 hoặc 7 ngày"})
    if body.action == "dismiss":
        if kind != "todo":
            raise ApiError(422, "COACH_ACTION_NOT_ALLOWED", ACTION_TITLE)
        level = await _todo_level(db, request, user.org_id, item_key.removeprefix("todo:"))
        if level == "P0":
            raise ApiError(422, "COACH_DISMISS_NOT_ALLOWED", DISMISS_P0_TITLE)
        if level not in ("P1", "P3"):
            raise ApiError(422, "COACH_DISMISS_NOT_ALLOWED", DISMISS_P2_TITLE)
        if body.confirm is not True:
            raise ApiError(422, "COACH_CONFIRM_REQUIRED", CONFIRM_TITLE)
    await store.apply_action(db, user.org_id, user.id, item_key, body.action, body.days, _now())
    if body.action in ("dismiss", "restore"):
        # target_id = khoá mục, KHÔNG kèm chữ nào khác (không tiêu đề, không lý do).
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="gen.coach_item_dismissed" if body.action == "dismiss"
                               else "gen.coach_item_restored",
                               target_type="gen_coach_item", target_id=item_key, ip=user.ip)
    else:
        actionlog.exempt()      # hoãn / đã hiểu / đã làm: thao tác riêng tư của Sếp, không phải hành động nghiệp vụ
    return Response(status_code=204)


# ─── GET|PATCH /prefs ────────────────────────────────────────────────────────────────────────────────────────

def _dismissed_rows(items: dict[str, engine.Item]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in sorted(items):
        it = items[key]
        if not key.startswith("todo:") or it.status != "dismissed":
            continue
        todo = key.removeprefix("todo:")
        if todo.startswith("health."):
            title, level = sg.health_title(todo.removeprefix("health.")), "P1"
        else:
            title = sg.todo_copy(todo)[0]
            level = sg.static_level(todo) or "P1"
        out.append({"key": todo, "level": level, "title": title})
    return out


def prefs_payload(prefs: engine.Prefs, items: dict[str, engine.Item], now: datetime) -> dict[str, Any]:
    follow = items.get(CARD_FOLLOWUP)
    follow_until = follow.snooze_until if follow is not None and engine.active_snooze(follow, now) else None
    snooze = prefs.snooze_until if prefs.snooze_until is not None and prefs.snooze_until > now else None
    return {"enabled": prefs.enabled, "bell": prefs.bell, "lessons_per_day": prefs.lessons_per_day,
            "quiet_start": prefs.quiet_start, "quiet_end": prefs.quiet_end, "snooze_until": _iso(snooze),
            "followup_snoozed_until": _iso(follow_until), "dismissed": _dismissed_rows(items)}


@router.get("/prefs")
async def get_prefs(user: service.CurrentUser = Depends(coach_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return prefs_payload(await store.get_prefs(db, user.id), await store.list_items(db, user.id), _now())


class PrefsPatch(BaseModel):
    enabled: bool | None = None
    bell: bool | None = None
    lessons_per_day: int | None = Field(default=None, ge=0, le=2)
    quiet_start: int | None = Field(default=None, ge=0, le=23)
    quiet_end: int | None = Field(default=None, ge=0, le=23)
    snooze_all_days: Literal[0, 1, 3, 7] | None = None


@router.patch("/prefs")
async def patch_prefs(body: PrefsPatch, user: service.CurrentUser = Depends(coach_owner),
                      db: AsyncSession = DB) -> dict[str, Any]:
    now = _now()
    cur = await store.get_prefs(db, user.id)
    sent = body.model_dump(exclude_none=True)
    changes: dict[str, Any] = {}
    for name in ("enabled", "bell", "lessons_per_day", "quiet_start", "quiet_end"):
        if name in sent and sent[name] != getattr(cur, name):
            changes[name] = sent[name]
    if "snooze_all_days" in sent:
        days = sent["snooze_all_days"]
        if days > 0:
            changes["snooze_until"] = now + timedelta(days=days)
        elif cur.snooze_until is not None:
            changes["snooze_until"] = None
    if changes:
        prefs = await store.update_prefs(db, user.org_id, user.id, changes)
        names = sorted("snooze_all_days" if k == "snooze_until" else k for k in changes)
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="gen.coach_prefs_changed", target_type="settings", target_id="gen_coach",
                               detail={"fields": names}, ip=user.ip)
    else:
        prefs = cur
        actionlog.exempt()      # không có gì đổi ⇒ không có hành động để ghi
    return prefs_payload(prefs, await store.list_items(db, user.id), now)


# ─── GET /curriculum ─────────────────────────────────────────────────────────────────────────────────────────

@router.get("/curriculum")
async def get_curriculum(request: Request, user: service.CurrentUser = Depends(coach_owner),
                         db: AsyncSession = DB) -> dict[str, Any]:
    _, curr = load_content(await load_kho_label(db, user.org_id))
    sig = await sg.collect(db, request.app.state.redis, user.org_id)
    items = await store.list_items(db, user.id)
    out = []
    for lesson, status in engine.lesson_statuses(curr, items, sig.state, _now()):
        row: dict[str, Any] = {"id": lesson["id"], "k": lesson["k"], "title": lesson["title"], "body": lesson["body"]}
        if lesson.get("try"):
            row["try"] = lesson["try"]
        row["status"] = status
        out.append(row)
    return {"total": len(curr), "lessons": out}
