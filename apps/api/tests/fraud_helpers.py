# ruff: noqa: E501
"""Tiện ích test W5 (gói T7): dựng tổ chức có Sếp, nhóm, người (định danh kênh, ngày vào nhóm), tin thô, chữ OCR."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels
from gh.biz.mem import entities
from gh.refinery.extract import ModelReply
from tests.mem_helpers import mk_org

ACCT_OLD = "0987654321"
ACCT_NEW = "0123456789"
OTP = "482913"


def now_vn() -> datetime:
    return datetime.now(levels.VN).replace(microsecond=0)


@dataclass
class World:
    db: AsyncSession
    org: uuid.UUID
    channel: uuid.UUID
    now: datetime
    owner_person: uuid.UUID | None = None
    groups: dict[str, uuid.UUID] = field(default_factory=dict)

    async def person(self, name: str, *, ptype: str = "unknown", group: str | None = None, joined_days_ago: float | None = None,
                     history_days_ago: float | None = None) -> tuple[uuid.UUID, uuid.UUID]:
        """(person_id, identity_id). `joined_days_ago` đặt ngày vào nhóm ĐÁNG TIN (sau lúc thấy nhóm + 1 ngày); `history_days_ago` ghi
        một tin cũ để người này "đã biết lâu"."""
        db = self.db
        pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name, person_type)
                                        VALUES (:o, core.next_code('PER'), :n, :t) RETURNING id"""), {"o": self.org, "n": name, "t": ptype})).scalar_one()
        iid = (await db.execute(text("""INSERT INTO core.person_identities (person_id, channel_id, external_id) VALUES (:p, :c, :x) RETURNING id"""),
                                {"p": pid, "c": self.channel, "x": uuid.uuid4().hex})).scalar_one()
        await entities.ensure_person(db, self.org, pid)
        if group is not None:
            joined = self.now - timedelta(days=joined_days_ago) if joined_days_ago is not None else None
            await db.execute(text("INSERT INTO core.group_members (group_id, person_id, joined_at) VALUES (:g, :p, :j)"),
                             {"g": self.groups[group], "p": pid, "j": joined})
        if history_days_ago is not None:
            await self.raw("chào cả nhà", sender=iid, group=group, at=self.now - timedelta(days=history_days_ago))
        return pid, iid

    async def group(self, key: str, name: str, size: int, *, created_days_ago: float = 90) -> uuid.UUID:
        gid = (await self.db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name, member_count, created_at)
                                             VALUES (:o, core.next_code('GRP'), :c, :x, :n, :s, :ca) RETURNING id"""),
                                     {"o": self.org, "c": self.channel, "x": uuid.uuid4().hex, "n": name, "s": size,
                                      "ca": self.now - timedelta(days=created_days_ago)})).scalar_one()
        self.groups[key] = gid
        if self.owner_person:
            await self.db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": gid, "p": self.owner_person})
        return gid

    async def raw(self, body: str, *, sender: uuid.UUID | None, group: str | None, at: datetime | None = None,
                  kind: str = "text") -> tuple[uuid.UUID, datetime]:
        at = at or self.now - timedelta(minutes=1)
        r = (await self.db.execute(text("""
            INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash, group_id, sender_identity_id)
            VALUES (:o, :at, :c, :x, :k, :b, '{}', '\\x01', :g, :s) RETURNING id, received_at"""),
            {"o": self.org, "at": at, "c": self.channel, "x": uuid.uuid4().hex, "k": kind, "b": body,
             "g": self.groups.get(group) if group else None, "s": sender})).one()
        return r.id, r.received_at

    async def ocr(self, raw: tuple[uuid.UUID, datetime], body: str, kind: str = "ocr") -> None:
        att = (await self.db.execute(text("""INSERT INTO raw.attachments (event_id, event_received_at, storage_key, mime, bytes, sha256, org_id)
                                             VALUES (:e, :r, 'k', 'image/jpeg', 1, '\\x01', :o) RETURNING id"""),
                                     {"e": raw[0], "r": raw[1], "o": self.org})).scalar_one()
        await self.db.execute(text("""INSERT INTO clean.media_texts (org_id, raw_event_id, raw_received_at, attachment_id, kind, text, engine,
                                                                     engine_version, status)
                                      VALUES (:o, :e, :r, :a, :k, :t, 'fake', '1', 'ok')"""),
                              {"o": self.org, "e": raw[0], "r": raw[1], "a": att, "k": kind, "t": body})


async def world(db: AsyncSession, *, owner_name: str = "Minh") -> World:
    org = await mk_org(db)
    s = {"installed_at": "2026-09-01T00:00:00+07:00", "m1_passed_gates": list(levels.REGULAR_GATES)}
    await db.execute(text("UPDATE core.organizations SET settings = jsonb_build_object('intel', CAST(:s AS jsonb)) WHERE id = :o"),
                     {"s": orjson.dumps(s).decode(), "o": org})
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    w = World(db, org, ch, now_vn())
    pid, _ = await w.person(owner_name)
    ent = await entities.ensure_person(db, org, pid)
    await db.execute(text("UPDATE mem.entities SET is_owner = true WHERE id = :e"), {"e": ent.id})
    w.owner_person = pid
    return w


def fake_call(verdict: str = "disagree", *, error: bool = False) -> Any:
    calls: list[Any] = []

    async def call(messages: list[Any]) -> ModelReply:
        calls.append(messages)
        if error:
            raise TimeoutError("hết giờ")
        return ModelReply(orjson.dumps({"gate": None, "flags_confirmed": [], "harm": {"what": ""}, "verdict": verdict,
                                        "reason": "thử"}).decode(), "fake-strong")

    call.calls = calls  # type: ignore[attr-defined]
    return call
