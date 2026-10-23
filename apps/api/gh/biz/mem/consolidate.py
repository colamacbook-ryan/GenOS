# ruff: noqa: E501
"""Job đêm hợp nhất (L4, spec §4A.3, kiểu sleep-time; 02:00–05:00 giờ VN). Mỗi lần chạy ghi Action Log `mem.consolidate` (số đếm, KHÔNG nội dung).

Việc làm (mỗi việc chạy lại an toàn):
1. dựng lại `mem.entity_summaries` cho thực thể có sự thật mới (xác định, không gọi model; mỗi dòng ↔ một `fact_ids[i]`; bỏ sự thật nhạy cảm);
2. đối soát sau gộp người: sự thật đơn trị trái nhau giữa thực thể giữ lại và thực thể bị gộp ⇒ `disputed` (`facts.reconcile_entity`);
3. đóng tranh chấp theo bằng chứng mới (CNS-107 ii; không áp cho `payment_account`) và cho hết hạn 30 ngày (iii);
4. đề xuất Hồ sơ Sếp từ tin CHÍNH Sếp viết trong nhóm ⇒ `proposed` (qua `profile.propose`, trần số đề xuất mỗi đêm và số đang chờ);
5. đếm lời hứa sắp tới hạn (chiếu sang tín hiệu là việc của Đợt 2).
Chưa làm ở đây (ngoài phạm vi L4/Đợt 1): hàng câu hỏi bản tin (`ops.asks` chưa có), replay có trần ngân sách (L2/L3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import disputes, entities, facts, profile, profile_read
from gh.chassis import actionlog

VN = ZoneInfo("Asia/Ho_Chi_Minh")
SUMMARY_VERSION = 1
SUMMARY_BATCH = 200
SUMMARY_LINES = 12
WINDOW_HOURS = (2, 5)                    # 02:00–05:00 giờ VN
MIN_WINNER_EVIDENCE = 2
MIN_WINNER_DAYS = 2
PROPOSALS_PER_RUN = 2
PROPOSALS_OPEN_CAP = 5
OWNER_MSG_LOOKBACK = timedelta(hours=36)
DUE_SOON = timedelta(hours=48)

PREDICATE_LABEL = {
    "promised_to": "Đã hứa", "owes": "Đang nợ", "asked_waiting": "Đang chờ trả lời", "deadline": "Hạn chót",
    "scheduled_event": "Lịch hẹn", "conflict_with": "Căng thẳng với", "complained_about": "Phàn nàn",
    "needs": "Cần", "offers": "Có/bán", "works_at": "Làm tại", "role_in_group": "Vai trò trong nhóm", "friend_of": "Bạn của",
    "staff_of": "Nhân viên của", "customer_of": "Khách của", "supplier_of": "Nhà cung cấp của", "prefers": "Thích",
    "lives_in": "Ở", "display_name_changed": "Đổi tên hiển thị"}
#: Vị từ nhạy cảm/nhân sự không vào tóm tắt (tóm tắt hiện cho người xem không PIN).


def in_window(now: datetime | None = None) -> bool:
    h = (now or datetime.now(UTC)).astimezone(VN).hour
    return WINDOW_HOURS[0] <= h < WINDOW_HOURS[1]


# ─── 1. tóm tắt thực thể ───────────────────────────────────────────────────

async def rebuild_summaries(db: AsyncSession, org_id: uuid.UUID, *, limit: int = SUMMARY_BATCH) -> dict[str, int]:
    """Thực thể đích (chưa gộp, chưa quên) có sự thật đổi sau `built_at` (hoặc chưa có tóm tắt) ⇒ dựng lại. Hết sự thật ⇒ xoá tóm tắt."""
    todo = (await db.execute(text("""
        SELECT c.id FROM mem.entities c
        CROSS JOIN LATERAL (SELECT coalesce((SELECT built_at FROM mem.entity_summaries x WHERE x.entity_id = c.id), '-infinity'::timestamptz) AS at) b
        WHERE c.org_id = :o AND c.merged_into IS NULL AND c.forgotten_at IS NULL AND NOT c.is_owner
          AND EXISTS (SELECT 1 FROM mem.facts f JOIN mem.entities s ON s.id = f.subject_id
                      WHERE f.org_id = :o AND coalesce(s.merged_into, s.id) = c.id AND f.predicate NOT LIKE 'owner\\_%'
                        AND (f.recorded_at > b.at OR (f.valid_to > b.at AND f.valid_to <= now())
                             OR EXISTS (SELECT 1 FROM mem.fact_status_events x WHERE x.fact_id = f.id AND x.at > b.at)))
        ORDER BY c.id LIMIT :n"""), {"o": org_id, "n": limit})).scalars().all()
    built = removed = 0
    for eid in todo:
        members = await entities.members(db, eid)
        rows = (await db.execute(text("""
            SELECT f.id, f.predicate, f.status, f.object_entity_id, f.object_value, f.source_class, f.valid_from,
                   o.display_name AS obj_name, a.display_name AS attr_name
            FROM mem.facts f JOIN mem.predicates p ON p.code = f.predicate
            LEFT JOIN mem.entities o ON o.id = f.object_entity_id LEFT JOIN mem.entities a ON a.id = f.attributed_to
            WHERE f.org_id = :o AND f.subject_id = ANY(:m) AND f.status IN ('owner_confirmed', 'asserted', 'disputed')
              AND f.evidence_n >= 1 AND p.sensitivity = 'normal' AND f.predicate NOT LIKE 'owner\\_%'
              AND f.valid_from <= now() AND (f.valid_to IS NULL OR f.valid_to > now())
            ORDER BY (f.status = 'owner_confirmed') DESC, (f.status = 'disputed') DESC, f.valid_from DESC, f.id LIMIT :n"""),
                                  {"o": org_id, "m": members, "n": SUMMARY_LINES})).all()
        if not rows:
            gone = await db.execute(text("DELETE FROM mem.entity_summaries WHERE entity_id = :e"), {"e": eid})
            removed += int(gone.rowcount)  # type: ignore[attr-defined]
            continue
        lines, ids = [], []
        for r in rows:
            val = r.obj_name if r.object_entity_id is not None else profile_read.value_text(r.object_value)
            line = f"{PREDICATE_LABEL.get(r.predicate, r.predicate)}: {' '.join(val.split())[:200]}"
            if r.source_class in facts.EXTERNAL_CLASSES:
                line += f" (theo lời {r.attr_name or 'người ngoài'}, chưa kiểm)"
            if r.status == "disputed":
                line += " (đang tranh chấp)"
            lines.append(line)
            ids.append(r.id)
        await db.execute(text("""
            INSERT INTO mem.entity_summaries (entity_id, org_id, summary, fact_ids, projection_version, built_at)
            VALUES (:e, :o, :s, :f, :v, now())
            ON CONFLICT (entity_id) DO UPDATE SET summary = EXCLUDED.summary, fact_ids = EXCLUDED.fact_ids,
              projection_version = EXCLUDED.projection_version, built_at = now()"""),
                         {"e": eid, "o": org_id, "s": "\n".join(lines), "f": ids, "v": SUMMARY_VERSION})
        built += 1
    return {"summaries_built": built, "summaries_removed": removed}


# ─── 2. đối soát sau gộp ───────────────────────────────────────────────────

async def reconcile_merged(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    targets = (await db.execute(text("""SELECT DISTINCT merged_into FROM mem.entities WHERE org_id = :o AND merged_into IS NOT NULL"""),
                                {"o": org_id})).scalars().all()
    created = 0
    for t in targets:
        created += await facts.reconcile_entity(db, org_id, t)
    return {"merged_targets": len(targets), "reconcile_disputes": created}


# ─── 3. tranh chấp ─────────────────────────────────────────────────────────

async def resolve_by_evidence(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    """CNS-107 (ii): một bên có ≥ 2 bằng chứng `insider`/`owner` MỚI HƠN (ghi sau lúc mở tranh chấp) từ ≥ 2 ngày khác nhau và bên kia
    không có bằng chứng mới ⇒ bên đó thắng (`resolved_by='consolidator'`). Không áp cho `payment_account` và tranh chấp có sự thật Sếp xác nhận."""
    open_ = [d for d in await disputes.list(db, org_id, open_only=True, limit=500) if d.predicate not in disputes.ONLY_OWNER_PREDICATES]
    resolved = 0
    for d in open_:
        ids = list(d.fact_ids)
        if len(ids) < 2:
            continue
        rows = (await db.execute(text("""
            SELECT f.id, f.status, f.source_class,
                   count(e.id) FILTER (WHERE e.created_at > :t AND e.quote_status IN ('verified') ) AS n_new,
                   count(DISTINCT (e.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) FILTER (WHERE e.created_at > :t AND e.quote_status = 'verified') AS days_new
            FROM mem.facts f LEFT JOIN mem.fact_evidence e ON e.fact_id = f.id
            WHERE f.id = ANY(:i) AND f.org_id = :o GROUP BY f.id, f.status, f.source_class"""),
                                  {"i": ids, "o": org_id, "t": d.opened_at})).all()
        if len(rows) < 2 or any(r.status == "owner_confirmed" for r in rows):
            continue
        winners = [r for r in rows if r.source_class in ("insider", "owner") and r.n_new >= MIN_WINNER_EVIDENCE and r.days_new >= MIN_WINNER_DAYS]
        if len(winners) == 1 and all(r.n_new == 0 for r in rows if r.id != winners[0].id):
            await disputes.resolve(db, org_id, d.id, winners[0].id, by="consolidator")
            resolved += 1
    return {"disputes_resolved": resolved}


# ─── 4. đề xuất Hồ sơ ──────────────────────────────────────────────────────

async def propose_profile(db: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, int]:
    """Tin chữ thường do CHÍNH Sếp viết trong nhóm (36 giờ qua, không phải tin chuyển tiếp) ⇒ tối đa `PROPOSALS_PER_RUN` đề xuất mới,
    và không quá `PROPOSALS_OPEN_CAP` đề xuất đang chờ. Sếp từng từ chối đúng mục đó ⇒ bỏ qua."""
    now = now or datetime.now(UTC)
    stats = {"profile_proposed": 0, "profile_skipped_rejected": 0}
    people = await profile.owner_person_ids(db, org_id)
    if not people:
        return stats
    open_n = int((await db.execute(text("SELECT count(*) FROM mem.v_owner_profile WHERE org_id = :o AND status = 'proposed'"),
                                   {"o": org_id})).scalar_one())
    budget = min(PROPOSALS_PER_RUN, PROPOSALS_OPEN_CAP - open_n)
    if budget <= 0:
        return stats
    msgs = (await db.execute(text("""
        SELECT e.id, e.body_text FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
        WHERE e.org_id = :o AND e.group_id IS NOT NULL AND e.direction = 'inbound' AND e.kind = 'text' AND pi.person_id = ANY(:p)
          AND e.occurred_at >= :s AND e.occurred_at < :n AND e.received_at >= :s - interval '3 days' AND e.body_text IS NOT NULL
        ORDER BY e.occurred_at DESC, e.id LIMIT 300"""), {"o": org_id, "p": people, "s": now - OWNER_MSG_LOOKBACK, "n": now})).all()
    for m in msgs:
        for section, sentence in profile.candidates_from_message(m.body_text):
            if budget <= 0:
                return stats
            try:
                res = await profile.propose(db, org_id, section=section, text_value=sentence, raw_event_id=m.id)
            except profile.ProfileError:
                continue                                        # tin không đạt kiểm (chuyển tiếp, quote lệch…) ⇒ bỏ qua, không dừng cả đêm
            if res is None:
                stats["profile_skipped_rejected"] += 1
            elif res.outcome == "proposed":
                stats["profile_proposed"] += 1
                budget -= 1
    return stats


# ─── 5. lời hứa sắp tới hạn ────────────────────────────────────────────────

async def count_promises_due_soon(db: AsyncSession, org_id: uuid.UUID, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    n = (await db.execute(text("""
        SELECT count(*) FROM mem.facts f
        WHERE f.org_id = :o AND f.predicate IN ('promised_to', 'owes') AND f.status IN ('owner_confirmed', 'asserted')
          AND (f.valid_to IS NULL OR f.valid_to > :n) AND jsonb_typeof(f.object_value) = 'object'
          AND (f.object_value->>'due_at') ~ '^\\d{4}-\\d\\d-\\d\\d'
          AND (f.object_value->>'due_at')::timestamptz BETWEEN :n AND :e"""), {"o": org_id, "n": now, "e": now + DUE_SOON})).scalar_one()
    return {"promises_due_soon": int(n)}


# ─── chạy một tổ chức ──────────────────────────────────────────────────────

async def run_org(db: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, int]:
    """Chạy đủ các việc cho một tổ chức rồi ghi Action Log `mem.consolidate` (chỉ số đếm). Bên gọi commit."""
    stats: dict[str, Any] = {}
    stats |= await reconcile_merged(db, org_id)
    stats["disputes_expired"] = await disputes.expire_stale(db, org_id)
    stats |= await resolve_by_evidence(db, org_id)
    stats |= await rebuild_summaries(db, org_id)
    stats |= await propose_profile(db, org_id, now=now)
    stats |= await count_promises_due_soon(db, org_id, now)
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:mem.consolidate", action="mem.consolidate",
                           target_type="org", target_id=str(org_id), detail=dict(stats))
    return {k: int(v) for k, v in stats.items()}
