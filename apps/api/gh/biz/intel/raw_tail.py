# ruff: noqa: E501
"""Đường nhanh `raw_tail` (0 token, r4 §7.1 + r6 §4D.2 (a), §4D.5): ứng viên LUẬT CỨNG của luật (a) mà `runner.ensure_scanned` (L2) đã lưu ở
`refinery.event_state.detail.cands` ⇒ tín hiệu chỉ-có-luật NGAY, không chờ lượt model — để lừa đảo/an toàn vẫn báo khi model chậm / hết
lượt (Q14: "hết lượt thì chỉ luật, lừa đảo/an toàn vẫn đi đường luật cứng").

- Chỉ đọc `event_state` (không ghi vào bảng của refinery). Chạy lại an toàn: tin đã có tín hiệu (`raw_event_ids` chứa tin) bị bỏ qua; khi
  lượt model tới, `signals.upsert` gộp vào CÙNG tín hiệu theo tin gốc (một sự việc, một tín hiệu).
- Bằng chứng `{type:'raw_event', source_field, span_start, span_end}` do CODE cắt ⇒ `verified` theo cấu tạo (CNS-070). Không lưu chữ tin.
- Người: luật cứng ngôi thứ nhất ⇒ `victims = [người gửi]` (CNS-111); quan hệ tra Hồ sơ (`owner_key_relation`) + `mem.facts customer_of`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals

log = logging.getLogger("gh.intel.raw_tail")
WINDOW = timedelta(minutes=30)
FLAG_OF = {"credentials_entered": "credentials_entered", "money_request": "money_request", "account_takeover": "account_takeover",
           "emergency": "emergency"}


async def _victim(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID | None, key_rel: frozenset[uuid.UUID]) -> list[levels.PersonRel]:
    if person_id is None:
        return []
    ent = (await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND person_id = :p AND merged_into IS NULL"),
                            {"o": org_id, "p": person_id})).scalar_one_or_none()
    customer = ent is not None and (await db.execute(text("""
        SELECT 1 FROM mem.facts WHERE org_id = :o AND subject_id = :e AND predicate = 'customer_of' AND valid_to IS NULL
          AND status IN ('asserted', 'owner_confirmed') LIMIT 1"""), {"o": org_id, "e": ent})).first() is not None
    return [levels.PersonRel(ref=f"u_{person_id}", owner_key_relation=ent in key_rel if ent else False, customer=customer,
                             thread_member=True, present_in_thread=True)]


async def tail(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, limit: int = 100) -> list[signals.Signal]:
    from gh.biz.mem import profile_read

    rows = (await db.execute(text("""
        SELECT e.id, e.received_at, e.occurred_at, e.group_id, s.detail, pi.person_id, g.member_count
        FROM refinery.event_state s JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at
        LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
        LEFT JOIN core.groups g ON g.id = e.group_id
        WHERE s.org_id = :o AND e.received_at >= :since AND e.direction = 'inbound'
          AND s.detail ->> 'gate_class' IN ('safety', 'hard', 'gate')
          AND EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(s.detail -> 'cands', '[]')) c WHERE c ->> 'hard' IS NOT NULL)
          AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.raw_event_ids @> ARRAY[e.id])
        ORDER BY e.received_at LIMIT :n"""), {"o": org_id, "since": now - WINDOW, "n": limit})).all()
    if not rows:
        return []
    prof = await profile_read.snapshot(db, org_id)
    key_rel = prof.key_relation_entity_ids()
    out: list[signals.Signal] = []
    for r in rows:
        cands = [c for c in (r.detail or {}).get("cands") or [] if isinstance(c, dict)]
        hard = [c for c in cands if c.get("hard")]
        if not hard:
            continue
        kind = next((c.get("kind") for c in hard if c.get("kind")), None) or next((c.get("kind") for c in cands if c.get("kind")), None)
        if kind not in levels.KINDS:
            continue                                       # luật không gán loại (vd money_request một mình) ⇒ chờ model
        flags = {FLAG_OF[c["flag"]]: True for c in cands if c.get("flag") in FLAG_OF}
        first_person = any(c.get("first_person") for c in hard)
        victims = await _victim(db, org_id, r.person_id, key_rel) if first_person else []
        tk = (r.detail or {}).get("thread_key") or (f"g:{r.group_id}" if r.group_id else None)
        size = int(r.member_count or 0)
        li = levels.SignalIn(kind=kind, msg_at=r.occurred_at, event_at=r.occurred_at, hard_rule_hit=sorted({str(c["hard"]) for c in hard}),
                             flags=levels.Flags(**flags), source_class="rule", confidence_exist=1.0, confidence_content=1.0,
                             victims=victims, impact_base=3 if victims else 2,
                             thread=levels.Thread(type="group" if r.group_id else "dm_agent", size=size,
                                                  size_known=r.group_id is None or r.member_count is not None, owner_in_thread=False,
                                                  agent_in_thread=True),
                             txn_state="pending" if flags.get("money_request") else "none")
        ev = [signals.EvidenceIn(type="raw_event", id=r.id, received_at=r.received_at, source_field=str(c.get("field") or "body"),  # type: ignore[arg-type]
                                 span_start=int((c.get("span") or [0, 0])[0]), span_end=int((c.get("span") or [0, 0])[1]))
              for c in hard if (c.get("span") or [0, 0])[1] > (c.get("span") or [0, 0])[0]]
        if not ev:
            continue
        up = signals.UpsertIn(kind=kind, title="Cảnh báo theo luật cứng" if kind == "rui_ro_lua_dao" else "Sự cố an toàn theo luật cứng",
                              detector="raw_tail", detector_version="1", level_input=li, evidence=ev,
                              case_key=levels.case_key(kind, at=r.occurred_at, raw_event_id=str(r.id)), thread_key=tk,
                              group_id=r.group_id, subject_type="group" if r.group_id else None, subject_id=r.group_id,
                              person_ids=[r.person_id] if r.person_id else [])
        try:
            async with db.begin_nested():
                out.append(await signals.upsert(db, org_id, up, now))
        except signals.SignalError as e:
            log.warning("raw_tail: bỏ qua tin %s: %s", r.id, type(e).__name__)
    return out
