# ruff: noqa: E501
"""Quên người (CNS-065): đường xoá THẬT của lõi trí nhớ. Chỉ gọi từ `system_api/routes.py::create_data_request(kind='erase')`
(Sếp yêu cầu qua Console + PIN `data.export_delete`, §8.1) — gọi từ nơi khác là sai luật.

`SET LOCAL gh.mem_erase = 'on'` rồi xoá cứng: bí danh, nhắc tới, tóm tắt, độ nóng quan hệ, bằng chứng, nhật ký trạng thái và mọi
`mem.facts` mà người đó (kể cả thực thể đã gộp vào) là chủ thể hoặc đối tượng. Dòng thực thể được giữ nhưng ẩn danh
(`forgotten_at`) để khoá ngoài còn hợp lệ. `raw.events` KHÔNG đụng (chỉ-INSERT, khoá cứng #5).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import entities

ERASED_NAME = "Người dùng đã xoá"


async def erase_entity(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID) -> dict[str, int]:
    """Xoá thật dữ liệu trí nhớ của một hồ sơ người. Trả số dòng đã xoá theo bảng (chạy lại ⇒ toàn 0)."""
    ent = (await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND person_id = :p"),
                            {"o": org_id, "p": person_id})).scalar_one_or_none()
    counts: dict[str, int] = {k: 0 for k in ("entities", "aliases", "mentions", "summaries", "heat", "evidence",
                                             "status_events", "facts", "disputes", "retry")}
    if ent is None:
        return counts
    targets = list((await db.execute(text("SELECT id FROM mem.entities WHERE id = :e OR merged_into = :e"),
                                     {"e": ent})).scalars().all())
    # Khoá dòng thực thể FOR UPDATE (theo thứ tự id) TRƯỚC khi xoá: chờ các lượt ghi đang chạy (FOR SHARE ở assert_fact),
    # lượt ghi đến sau thấy `forgotten_at` và bị từ chối — không còn sự thật mồ côi sau khi quên.
    await entities.lock_rows(db, targets, "update")
    await db.execute(text("UPDATE mem.entities SET forgotten_at = coalesce(forgotten_at, now()) WHERE id = ANY(:t)"), {"t": targets})
    await db.execute(text("SELECT set_config('gh.mem_erase', 'on', true), set_config('gh.mem_by', 'erase', true)"))
    try:
        # sự thật có người này làm chủ thể, đối tượng HOẶC nguồn lời ("theo lời X" — attributed_to): xoá hết
        fact_ids = list((await db.execute(text("""
            SELECT id FROM mem.facts WHERE org_id = :o AND (subject_id = ANY(:t) OR object_entity_id = ANY(:t)
                                                            OR attributed_to = ANY(:t))"""),
                                          {"o": org_id, "t": targets})).scalars().all())
        # nhắc tới chưa nối (candidate/unresolved) mà cách gọi trùng bí danh của người này: xoá trước khi xoá bí danh
        n_mention_surface = 0
        n_mention_surface += int((await db.execute(text("""
            DELETE FROM mem.mentions m WHERE m.org_id = :o AND m.status <> 'linked'
              AND mem.fold(m.surface) IN (SELECT a.alias_folded FROM mem.aliases a WHERE a.entity_id = ANY(:t))"""),
                                                    {"o": org_id, "t": targets})).rowcount)  # type: ignore[attr-defined]

        def n(r: Any) -> int:
            return int(r.rowcount)

        counts["aliases"] = n(await db.execute(text("DELETE FROM mem.aliases WHERE org_id = :o AND entity_id = ANY(:t)"),
                                               {"o": org_id, "t": targets}))
        counts["mentions"] = n_mention_surface + n(await db.execute(
            text("DELETE FROM mem.mentions WHERE org_id = :o AND entity_id = ANY(:t)"), {"o": org_id, "t": targets}))
        # tóm tắt dựng lại được: bỏ của người đó và mọi bản tóm tắt có câu dựa trên sự thật bị xoá
        counts["summaries"] = n(await db.execute(text("""
            DELETE FROM mem.entity_summaries WHERE org_id = :o AND (entity_id = ANY(:t) OR fact_ids && CAST(:f AS uuid[]))"""),
                                                  {"o": org_id, "t": targets, "f": fact_ids}))
        counts["heat"] = n(await db.execute(text("""
            DELETE FROM mem.relation_heat_weekly WHERE org_id = :o AND (entity_a = ANY(:t) OR entity_b = ANY(:t))"""),
                                            {"o": org_id, "t": targets}))
        counts["evidence"] = n(await db.execute(text("DELETE FROM mem.fact_evidence WHERE org_id = :o AND fact_id = ANY(:f)"),
                                                {"o": org_id, "f": fact_ids}))
        counts["status_events"] = n(await db.execute(text(
            "DELETE FROM mem.fact_status_events WHERE org_id = :o AND fact_id = ANY(:f)"), {"o": org_id, "f": fact_ids}))
        # tranh chấp: của người đó xoá hẳn; tranh chấp khác chỉ bỏ id sự thật đã xoá khỏi danh sách
        counts["disputes"] = n(await db.execute(text("DELETE FROM mem.disputes WHERE org_id = :o AND subject_id = ANY(:t)"),
                                                {"o": org_id, "t": targets}))
        await db.execute(text("""
            UPDATE mem.disputes SET fact_ids = (SELECT coalesce(array_agg(x), '{}') FROM unnest(fact_ids) x
                                                WHERE NOT (x = ANY(CAST(:f AS uuid[]))))
            WHERE org_id = :o AND fact_ids && CAST(:f AS uuid[])"""), {"o": org_id, "f": fact_ids})
        counts["facts"] = n(await db.execute(text("DELETE FROM mem.facts WHERE org_id = :o AND id = ANY(:f)"),
                                             {"o": org_id, "f": fact_ids}))
        tstr = [str(x) for x in targets]
        counts["retry"] = n(await db.execute(text("""
            DELETE FROM refinery.fact_retry WHERE org_id = :o AND (fact_in->>'subject_id' = ANY(:t)
                OR fact_in->>'object_entity_id' = ANY(:t) OR fact_in->>'speaker_entity_id' = ANY(:t)
                OR fact_in->>'attributed_to' = ANY(:t))"""),
                                              {"o": org_id, "t": tstr}))
        counts["entities"] = n(await db.execute(text("""
            UPDATE mem.entities SET display_name = :nm, attrs = '{}'::jsonb, forgotten_at = coalesce(forgotten_at, now())
            WHERE org_id = :o AND id = ANY(:t)"""), {"o": org_id, "t": targets, "nm": ERASED_NAME}))
    finally:
        await db.execute(text("SELECT set_config('gh.mem_erase', 'off', true), set_config('gh.mem_by', '', true)"))
    return counts
