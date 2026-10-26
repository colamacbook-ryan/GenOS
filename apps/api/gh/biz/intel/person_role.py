# ruff: noqa: E501
"""Vai người MỘT nơi (v0.1.77 P2; spec r7.2 §4.2.1 (c′), bản 3 §5 dòng P2, L-96): từ một người/thực thể + luồng ⇒ `levels.PersonRel`.

Trước đây vai người dựng ở ba chỗ (`generic.who_rel`, `fraud.Who.rel()`, `raw_tail._victim`) và lệch nhau từng chút. Module này giữ:
- `rel_from_who(who, sets, ...)` — hàm THUẦN, thân cũ của `generic.who_rel` (generic gọi lại, hành vi không đổi);
- `person_role(db, org, person | entity, thread)` — tra DB rồi dựng `PersonRel` cùng nguồn với `fraud.Who.rel()` (test so khớp, không chạm fraud.py);
- `is_customer(...)` — MỘT nguồn "khách của Sếp" (CNS-180 `customer_of_owner`): `core.persons.person_type = customer` HOẶC fact `customer_of`;
- `audience_n(...)` — `Thread.customer_audience_n`: thành viên KHÁC người nói là khách của Sếp; không đếm được ⇒ None (không suy);
- `staff_owner_ref(...)` — `staff_owned`/`staff_ref` (CNS-179): NV đã xác nhận nhận việc bằng tin SAU tin kích hoạt, khách chưa nhắn lại.
Import `generic` chỉ ở trong hàm (generic import module này ở đầu file ⇒ tránh vòng).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels

if TYPE_CHECKING:
    from gh.biz.intel.detectors.generic import OrgSets, Who


def rel_from_who(w: Who, sets: OrgSets, *, thread_member: bool = False) -> levels.PersonRel:
    """Vai người từ dữ kiện đã tra: `OrgSets` (fact) CỘNG loại người Sếp đã đánh dấu ở `core.persons.person_type` (khách ⇒ customer, nhân viên ⇒ team).
    P-T1 (G-KB sau T14): bỏ qua person_type ⇒ khách mất cổng an toàn. (Dời nguyên văn từ `generic.who_rel`.)"""
    ent = w.entity_id
    return levels.PersonRel(
        ref=w.ref, is_owner=sets.is_owner(ent), owner_key_relation=ent in sets.key_relations,
        customer=ent in sets.customers or w.person_type == "customer", team=ent in sets.staff or w.person_type == "staff",
        thread_member=thread_member, kin_of_member=False, present_in_thread=thread_member)


async def person_role(db: AsyncSession, org_id: uuid.UUID, *, person_id: uuid.UUID | None = None, entity_id: uuid.UUID | None = None,
                      group_id: uuid.UUID | None = None, thread_member: bool = True, sets: OrgSets | None = None) -> levels.PersonRel:
    """Vai của MỘT người (`person_id` hoặc `entity_id`) trong luồng `group_id` (None = tin riêng). Cùng kết quả `fraud.Who.rel(thread_member=...)`
    cho cùng người: `present_in_thread` = đang là thành viên nhóm (hoặc là Sếp), không phải bản sao của `thread_member`."""
    from gh.biz.intel.detectors import generic as g

    sets = sets or await g.org_sets(db, org_id)
    w = await g.who_person(db, org_id, person_id) if person_id is not None else await g.who(db, entity_id)
    if w is None:
        return levels.PersonRel(ref=f"u_{person_id}" if person_id else f"e_{entity_id}", thread_member=thread_member)
    rel = rel_from_who(w, sets, thread_member=thread_member)
    present = False
    if group_id is not None and w.person_id is not None:
        present = (await db.execute(text("SELECT 1 FROM core.group_members WHERE group_id = :g AND person_id = :p AND left_at IS NULL LIMIT 1"),
                                    {"g": group_id, "p": w.person_id})).first() is not None
    return rel.model_copy(update={"present_in_thread": present or rel.is_owner})


async def is_customer(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID | None, entity_id: uuid.UUID | None) -> bool:
    """MỘT nguồn `customer_of_owner` (CNS-180): người có `person_type = customer` hoặc fact `customer_of` còn hiệu lực (asserted | owner_confirmed)."""
    if person_id is None and entity_id is None:
        return False
    return (await db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM core.persons WHERE id = :p AND org_id = :o AND person_type = 'customer')
            OR EXISTS (SELECT 1 FROM mem.facts WHERE org_id = :o AND subject_id = :e AND predicate = 'customer_of' AND valid_to IS NULL
                       AND status IN ('asserted', 'owner_confirmed'))"""), {"p": person_id, "o": org_id, "e": entity_id})).scalar_one() is True


async def audience_n(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID | None, *, speaker_person_id: uuid.UUID | None = None,
                     speaker_entity_id: uuid.UUID | None = None) -> int | None:
    """`Thread.customer_audience_n`: số thành viên (đang ở nhóm) KHÁC người nói là khách của Sếp (một câu SQL). Tin riêng, hoặc danh sách thành viên
    chưa chắc đủ ⇒ None ("vắng" không chặn `venue = group`, r7.1 R3; đếm thiếu mà trả số nhỏ sẽ chặn nhầm 🔴 ⇒ fail-low). "Chắc đủ" = nhóm báo
    `core.groups.member_count` và số dòng đang ở nhóm ≥ số đó (`members_complete` của bridge không được lưu nên không dùng được; `member_count` NULL ⇒ None).
    Chỉ gọi cho `xung_dot_khieu_nai` / `quan_he_nong_nguoi` (đường G3) và heat — không cho lừa đảo/an toàn (Bước 0 không đọc, B2)."""
    if group_id is None:
        return None
    r = (await db.execute(text("""
        SELECT g.member_count AS mc,
               count(gm.person_id) AS total,
               count(gm.person_id) FILTER (WHERE
                   gm.person_id IS DISTINCT FROM CAST(:sp AS uuid)
                   AND (CAST(:se AS uuid) IS NULL OR CAST(:se AS uuid) NOT IN (e.id, COALESCE(e.merged_into, e.id)))
                   AND COALESCE(p.attrs ->> 'is_owner', '') <> 'true' AND NOT COALESCE(e.is_owner, false)
                   AND (p.person_type = 'customer' OR EXISTS (
                        SELECT 1 FROM mem.facts f WHERE f.org_id = :o AND f.predicate = 'customer_of' AND f.valid_to IS NULL
                          AND f.status IN ('asserted', 'owner_confirmed') AND f.subject_id = COALESCE(e.merged_into, e.id)))) AS customers
        FROM core.groups g
        LEFT JOIN core.group_members gm ON gm.group_id = g.id AND gm.left_at IS NULL
        LEFT JOIN core.persons p ON p.id = gm.person_id
        LEFT JOIN mem.entities e ON e.person_id = gm.person_id AND e.org_id = :o AND e.merged_into IS NULL
        WHERE g.id = :g AND g.org_id = :o GROUP BY g.member_count"""),
                          {"o": org_id, "g": group_id, "sp": speaker_person_id, "se": speaker_entity_id})).one_or_none()
    if r is None or r.mc is None or r.total == 0 or r.total < r.mc:
        return None
    return int(r.customers)


async def staff_owner_ref(db: AsyncSession, org_id: uuid.UUID, *, status_hint: str | None, status_by_person_id: uuid.UUID | None,
                          customer_person_id: uuid.UUID, group_id: uuid.UUID | None, trigger_at: datetime, status_at: datetime | None,
                          asks_for_owner: bool = False) -> str | None:
    """`staff_ref` khi `staff_owned` (CNS-179), không thì None. Điều kiện (mọi cái phải đúng):
    `status_hint ∈ {dang_xu_ly, da_xu_ly}` (model — `dang_xu_ly` ⇒ bên gọi đặt `staff_owned`, `da_xu_ly` ⇒ `closure = staff`, một khái niệm, N1; kèm `status_message_id` do verify P3a kiểm) · người gửi tin trả lời ∈ NV ĐÃ XÁC NHẬN (person_type `staff`
    + khoá loại + `staff_marked_at`, D3 — `generic.staff_trusted`) · tin NV gửi SAU `trigger_at` (tin kích hoạt) · khách KHÔNG nhắn lại sau tin NV
    trong cùng luồng · `asks_for_owner` thắng (khách đòi gặp chủ ⇒ không ai "đang lo"). `status_at` = giờ CỦA CHÍNH tin `dang_xu_ly` (verify ghi ở `entities.v.status_at`), thiếu ⇒ None; mốc "khách nhắn sau" cũng tính từ đó
    (không phải tin NV mới nhất: NV nhắn "dạ" sau khi khách doạ lần hai không được xoá tin doạ)."""
    if status_hint not in ("dang_xu_ly", "da_xu_ly") or status_by_person_id is None or status_at is None or asks_for_owner:
        return None
    from gh.biz.intel.detectors import generic as g

    w = await g.who_person(db, org_id, status_by_person_id)
    if w is None or not g.staff_trusted(w.person_type, locked=w.locked, marked_at=w.staff_marked_at):
        return None
    sent = levels._aware(status_at)
    if sent <= levels._aware(trigger_at):
        return None
    later = (await db.execute(text("""SELECT 1 FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                      WHERE e.org_id = :o AND pi.person_id = :c AND e.direction = 'inbound' AND e.occurred_at > :t
                                        AND e.group_id IS NOT DISTINCT FROM :g LIMIT 1"""),
                              {"o": org_id, "c": customer_person_id, "t": sent, "g": group_id})).first()
    return None if later is not None else w.ref
