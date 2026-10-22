"""Tiện ích test giai đoạn 2: tổ chức đã khởi tạo, router model giả, gửi tin vào Kho thô."""

import uuid
from collections.abc import Callable
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.data.ingest import channel_row, ingest_message, upsert_group
from gh.providers.clients import Message
from gh.providers.router import ModelUnavailable, Routed


class FakeRouter:
    """Thay ModelRouter: `reply(messages)` trả chuỗi JSON; `down=True` → ModelUnavailable."""

    def __init__(self, reply: Callable[[list[Message]], Any] | None = None, *, down: bool = False,
                 no_chain: bool | None = None):
        self.reply = reply or (lambda _m: {"units": [], "noise": []})
        self.down = down
        # v0.1.67: False = có model nhưng lượt gọi lỗi (mạng/hạn mức); True/None = chưa có model.
        self.no_chain = no_chain
        self.calls: list[list[Message]] = []

    async def generate(self, org_id: uuid.UUID, *, agent_key: str, purpose: str, messages: list[Message],
                       json_mode: bool = True, temperature: float = 0.2) -> Routed:
        self.calls.append(messages)
        if self.down:
            raise ModelUnavailable(["gemini: 429", "deepseek: đang ngắt mạch"], no_chain=self.no_chain)
        out = self.reply(messages)
        return Routed(out if isinstance(out, str) else orjson.dumps(out).decode(), "fake", "fake-model", 10, 10)

    async def embed(self, org_id: uuid.UUID, texts: list[str]) -> list[list[float]] | None:
        return None


def refs_in(messages: list[Message]) -> list[str]:
    """Các mã E… của lô gửi cho model (mỗi dòng JSON sau phần "Tin nhắn")."""
    body = messages[-1].content.split("Tin nhắn (mỗi dòng một JSON):\n", 1)[1]
    return [orjson.loads(line)["ref"] for line in body.splitlines() if line.strip()]


def texts_in(messages: list[Message]) -> dict[str, str]:
    body = messages[-1].content.split("Tin nhắn (mỗi dòng một JSON):\n", 1)[1]
    return {d["ref"]: d["text"] for d in (orjson.loads(line) for line in body.splitlines() if line.strip())}


async def install_presets(db: AsyncSession, org: uuid.UUID, codes: list[str] | None = None) -> None:
    from gh.data_api.routes import RuleIn, create_rule
    from gh.refinery.presets import PRESETS

    for p in PRESETS:
        rin = RuleIn(name=p["name"], kind=p["kind"], conditions=p["conditions"], outputs=p["outputs"],
                     threshold=p["threshold"], prompt_hint=p.get("prompt_hint"))
        await create_rule(db, org, rin, None, code=p["code"], enabled=codes is None or p["code"] in codes)
    await db.commit()


async def org_id(db: AsyncSession) -> uuid.UUID:
    return (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()  # type: ignore[no-any-return]


async def listen(db: AsyncSession, org: uuid.UUID, external_group_id: str, mode: str = "silent",
                 channel: str = "zalo") -> uuid.UUID:
    """Tạo nhóm (qua đường tạo nhóm của ingest, KHÔNG có tin nào) rồi đặt chế độ nghe. Từ QD 17/10 nhóm mới mặc định
    `silent` và lưu tin ngay, nên không còn dùng tin dò. Từ v0.1.71 «Quyền xem» của nhóm được áp thật (mặc định ngoài
    đời là «Chỉ Sếp») ⇒ nhóm test mặc định `all_members` để các test vai thấp (Vận hành, Kiểm soát…) không phụ thuộc
    nó; test về «Quyền xem» tự đặt giá trị (xem `test_quyen_xem_nhom_v0171.py`)."""
    await upsert_group(db, org, await channel_row(db, org, channel), external_group_id, f"Nhóm {external_group_id}")
    gid = (await db.execute(text("""UPDATE core.groups SET listen_mode = :m, view_scope = 'all_members'
                                    WHERE external_id = :x RETURNING id"""),
                            {"m": mode, "x": external_group_id})).scalar_one()
    await db.commit()
    return gid  # type: ignore[no-any-return]


_seq = 0


def msg(body: str, *, group: str | None = "g1", sender: str = "u1", name: str = "Chị Lan", mentions: bool = False,
        channel: str = "zalo", msg_id: str | None = None, phone: str | None = None) -> dict[str, Any]:
    global _seq
    _seq += 1
    p: dict[str, Any] = {"channel": channel, "external_msg_id": msg_id or f"m-{_seq}-{uuid.uuid4().hex[:6]}",
                         "sender_external_id": sender, "sender_name": name, "body_text": body, "kind": "text",
                         "mentions_self": mentions}
    if group:
        p |= {"external_group_id": group, "group_name": f"Nhóm {group}"}
    if phone:
        p["sender_phone"] = phone
    return p


async def put(sm: async_sessionmaker[AsyncSession], org: uuid.UUID, *payloads: dict[str, Any]) -> list[Any]:
    out = []
    async with sm() as db:
        for p in payloads:
            out.append(await ingest_message(db, org, p))
        await db.commit()
    return out


async def states(db: AsyncSession, org: uuid.UUID) -> dict[str, int]:
    rows = (await db.execute(text("SELECT state, count(*) FROM refinery.event_state WHERE org_id = :o GROUP BY 1"),
                             {"o": org})).all()
    return {r[0]: r[1] for r in rows}
