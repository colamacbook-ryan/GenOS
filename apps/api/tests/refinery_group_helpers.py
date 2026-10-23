# ruff: noqa: E501
"""Tiện ích test đường phân tích THEO LUỒNG (v0.1.76, gói L2): bật đường theo luồng, router giả ghi `agent.model_calls`,
bộ phân tích giả (thay `l3.analyze_group_detailed`), dựng `BrainOut` từ cửa sổ."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.providers.clients import Message
from gh.providers.router import ModelUnavailable, Routed
from gh.refinery import _l3_stub as l3
from gh.refinery.brain_contract import BrainOut, FactObject, FactProposed, FactSubject, PersonMention, Quote, Unit
from tests.phase2 import FakeRouter


async def enable_groups(db: AsyncSession, org: uuid.UUID, **intel: Any) -> None:
    """`settings.intel.group_pipeline = 'on'` (+ khoá intel khác, vd cadence_min) và nhớ các khoá `ai_cost` cũ."""
    cfg = {"group_pipeline": "on", **intel}
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{intel}',
                             COALESCE(settings->'intel', '{}'::jsonb) || CAST(:v AS jsonb), true) WHERE id = :o"""),
                     {"o": org, "v": orjson.dumps(cfg).decode()})
    await db.commit()


async def set_ai_cost(db: AsyncSession, org: uuid.UUID, **vals: int | None) -> None:
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{ai_cost}',
                             COALESCE(settings->'ai_cost', '{}'::jsonb) || CAST(:v AS jsonb), true) WHERE id = :o"""),
                     {"o": org, "v": orjson.dumps(vals).decode()})
    await db.commit()


async def mk_model(db: AsyncSession, org: uuid.UUID, *, kind: str = "claude_code_cli", price: tuple[float, float] | None = None) -> uuid.UUID:
    """Nguồn + model để dòng `agent.model_calls` có `model_id` (CLI thuê bao ⇒ 0 ₫; kind khác + giá ⇒ tính tiền theo token)."""
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, auth_state) VALUES (:o, :k, :n, 'ok')
                                    RETURNING id"""), {"o": org, "k": kind, "n": f"{kind}-{uuid.uuid4().hex[:6]}"})).scalar_one()
    mid = (await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'm') RETURNING id"), {"p": pid})).scalar_one()
    if price is not None:
        await db.execute(text("""INSERT INTO agent.model_prices (model_id, org_id, in_vnd_per_mtok, out_vnd_per_mtok)
                                 VALUES (:m, :o, :i, :x)"""), {"m": mid, "o": org, "i": price[0], "x": price[1]})
    await db.commit()
    return mid  # type: ignore[no-any-return]


async def add_call(db: AsyncSession, org: uuid.UUID, model: uuid.UUID, purpose: str, *, at: datetime | None = None,
                   thread_key: str | None = None, tin: int = 1000, tout: int = 100, status: str = "ok") -> None:
    await db.execute(text("""INSERT INTO agent.model_calls (org_id, at, model_id, agent_key, purpose, tokens_in, tokens_out, latency_ms,
                                                            status, thread_key)
                             VALUES (:o, COALESCE(:at, now()), :m, 'core.refinery', :p, :ti, :to, 5, :s, :t)"""),
                     {"o": org, "at": at, "m": model, "p": purpose, "ti": tin, "to": tout, "s": status, "t": thread_key})
    await db.commit()


class LoggingRouter(FakeRouter):
    """Router giả GHI `agent.model_calls` như router thật (để đếm lượt/gắn thẻ luồng); `purposes` lưu `purpose` từng lượt gọi."""

    def __init__(self, sm: async_sessionmaker[AsyncSession], model_id: uuid.UUID, *, down: bool = False,
                 reply: Callable[[list[Message]], Any] | None = None):
        super().__init__(reply, down=down)
        self.sm, self.model_id = sm, model_id
        self.purposes: list[str] = []

    async def generate(self, org_id: uuid.UUID, *, agent_key: str, purpose: str, messages: list[Message],  # type: ignore[override]
                       json_mode: bool = True, temperature: float = 0.2, **kw: Any) -> Routed:
        self.purposes.append(purpose)
        self.calls.append(messages)
        if self.down:
            raise ModelUnavailable(["claude_code_cli: hết hạn mức"], no_chain=False)
        async with self.sm() as db:
            await db.execute(text("""INSERT INTO agent.model_calls (org_id, model_id, agent_key, purpose, tokens_in, tokens_out, latency_ms, status)
                                     VALUES (:o, :m, :a, :p, 1200, 300, 5, 'ok')"""),
                             {"o": org_id, "m": self.model_id, "a": agent_key, "p": purpose})
            await db.commit()
        return Routed("{}", "fake", "fake-model", 1200, 300)


Builder = Callable[[Any], BrainOut]


class Analyzer:
    """Thay `l3.analyze_group_detailed`: ghi lại các cửa sổ nhận được; `build(window)` dựng `BrainOut`; gọi `call` đúng một lần
    (để router giả ghi `model_calls`) trừ khi `use_call=False`."""

    def __init__(self, build: Builder | None = None, *, use_call: bool = True, raises: Exception | None = None):
        self.build = build or (lambda w: BrainOut(thread_ref=w.thread_ref))
        self.use_call, self.raises = use_call, raises
        self.windows: list[Any] = []
        self.packs: list[Any] = []

    async def __call__(self, window: Any, pack: Any, profile: Any, policy: Any, *, call: Any = None, secrets: Any = ()) -> l3.Analysis:
        self.windows.append(window)
        self.packs.append(pack)
        if self.use_call and call is not None:
            await call([Message("user", "x")])
        if self.raises is not None:
            raise self.raises
        return l3.Analysis(self.build(window), "fake-model", 1200, 300, 1, 100)

    @property
    def threads(self) -> list[str]:
        return [w.thread_ref for w in self.windows]

    def bodies(self, i: int = 0, *, context: bool = False) -> list[str]:
        w = self.windows[i]
        return [e.body or "" for e in (w.context_events if context else w.new_events)]


def find_msg(window: Any, needle: str) -> Any:
    for e in [*window.new_events, *window.context_events]:
        if e.body and needle in e.body:
            return e
    raise AssertionError(f"không thấy tin chứa {needle!r} trong cửa sổ")


def unit_for(window: Any, needle: str, *, kind: str | None = None, unit_ref: str = "u1", summary: str = "Tóm tắt thử",
             people: list[PersonMention] | None = None, quote: str | None = None, **flags: Any) -> Unit:
    e = find_msg(window, needle)
    from gh.refinery.brain_contract import Flags

    return Unit(unit_ref=unit_ref, event_ref="e1", kind=kind, quotes=[Quote(message_id=e.message_id, field="body", text=quote or needle)],
                summary=summary, people=people or [], flags=Flags(**flags))


def out_with(window: Any, *units: Unit, facts: list[FactProposed] | None = None) -> BrainOut:
    return BrainOut(thread_ref=window.thread_ref, units=list(units), facts_proposed=facts or [])


def phone_fact(window: Any, needle: str, person_id: uuid.UUID, value: str = "0901234567", unit_ref: str = "u1") -> FactProposed:
    e = find_msg(window, needle)
    return FactProposed(unit_ref=unit_ref, subject=FactSubject(surface="Lan", person_ref=f"u_{person_id}"), predicate="contact_phone",
                        object=FactObject(value=value), quote_refs=[e.message_id])


class Bus:
    """Bus giả: ghi lại các lần `publish`."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any], uuid.UUID | None]] = []

    async def publish(self, topic: str, entity: str, payload: dict[str, Any], *, actor: str, org_id: uuid.UUID | None = None, **kw: Any) -> None:
        self.sent.append((topic, entity, payload, org_id))


async def pending_n(db: AsyncSession, org: uuid.UUID, state: str = "pending") -> int:
    return int((await db.execute(text("SELECT count(*) FROM refinery.event_state WHERE org_id = :o AND state = :s"),
                                 {"o": org, "s": state})).scalar_one())


def now_plus(minutes: float) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


async def settle(awaitable: Awaitable[Any]) -> Any:
    return await awaitable
