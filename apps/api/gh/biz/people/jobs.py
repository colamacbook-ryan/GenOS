"""Việc nền của cụm Con người & Chất lượng:

- `recompute_people_reviews_org` (gọi tay được, và qua cron `people_review_recompute` mỗi ngày): với mỗi kỳ
  (7 ngày gần nhất đã trọn vẹn, kết thúc hôm qua — `period_for`), tính một dòng `biz.people_reviews` cho từng
  nhân viên (`person_type = 'staff'`) đã trả lời ít nhất một tin trong kỳ, từ lưới phản hồi
  (`gh.biz.people.service.PAIR_CTE`/`BUCKET_SELECT` — cùng cách ghép "tin đến → tin đi cùng luồng" của
  `gh.biz.queue.jobs._scan_slow_response`) và số lời hứa bị vỡ (`biz.promises.broken`, do cụm Hàng đợi ghi).
  Không có agent trực kênh nào tạo dữ liệu này (khác các cụm khác) — đây là việc quét định kỳ theo tổ chức nên
  không đăng ký `HOOKS`.

  **Quyết định tự đưa ra** (spec không cho công thức điểm — cùng tinh thần `market.service.score_match` tự định
  nghĩa cách chấm điểm ghép, có lý do đi kèm để kiểm chứng lại được):
  - Điểm bắt đầu từ 70 (trung tính), cộng theo tỉ lệ phản hồi nhanh (< 15 phút), trừ theo tỉ lệ phản hồi chậm
    (> 60 phút) và trừ theo số lời hứa bị vỡ (tối đa trừ 20 điểm) — rồi kẹp về 0–100.
  - `trend` so điểm kỳ này với dòng gần nhất **trước** kỳ này của cùng người (chênh > 2 điểm mới tính lên/xuống,
    tránh nhiễu do làm tròn).
  - Chứng cứ (khoá cứng 7 — không chứng cứ thì không ghi điểm) là tối đa 5 đơn vị ý nghĩa mới nhất của những
    khách mà nhân viên này đã trả lời trong kỳ (`clean.meaning_units.person_id` = khách, không phải nhân viên —
    hệ thống không sinh đơn vị ý nghĩa cho tin nhân viên gửi). Không có đơn vị nào (nhân viên chỉ nói chuyện qua
    kênh không sàng lọc được thành đơn vị) → bỏ qua người đó trong lượt tính này, không ghi dòng thiếu chứng cứ.
  - Chạy lại (cron mỗi ngày, hoặc gọi tay) chỉ UPDATE tại-chỗ dòng **hệ thống** (`overridden_by IS NULL`) của
    đúng kỳ đó (`ON CONFLICT` trên chỉ mục riêng phần `people_reviews_system_period`) — không sinh thêm lịch sử
    mỗi lần chạy lại, và không bao giờ đè lên một bản Owner đã sửa tay (`gh/biz/people/routes.py`).
  - v0.1.45 (F-60) cờ **'Đáng ngờ'**: nhân viên có thể chèn câu lệnh cho AI / xin điểm vào tin nhắn để lách điểm.
    Job quét tối đa `MAX_SCAN_TEXTS` tin ĐI do chính nhân viên gửi (KHÔNG quét tin khách — nhân viên không bị gắn
    cờ vì chữ khách gõ) bằng `gh.suspicious.REVIEW_MANIPULATION`; khớp → `suspicious = true` kèm lý do ngắn (chỉ
    đoạn khớp ≤ 60 ký tự, không chép nguyên tin). KHÔNG đổi điểm — chỉ gắn cờ để Sếp xem chứng cứ trước khi dùng.
    Owner bỏ cờ được (`PATCH /people/reviews/{id}/suspicious`, có lý do, ghi Nhật ký); chạy lại job KHÔNG gắn lại
    cờ đã được bỏ (giữ nguyên `suspicious_cleared_*`).
  - **Không có hành động kỷ luật tự động** (PLAN §3.11, khoá cứng 2): job chỉ ghi điểm + tín hiệu + khuyến nghị
    coaching bằng chữ; không tạo cảnh báo, không đổi quyền, không đổi trạng thái công việc của ai.

`HOOKS`/`JOBS`: xem `gh.biz.hooks`.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob, Hook
from gh.biz.people.service import BUCKET_SELECT, PAIR_CTE
from gh.suspicious import scan_texts

PERIOD_DAYS = 7
MAX_BROKEN_PENALTY = 20.0
FAST_WEIGHT, SLOW_WEIGHT, BROKEN_WEIGHT = 25.0, 15.0, 7.0
TREND_EPSILON = 2.0
MAX_EVIDENCE_UNITS = 5
MAX_SCAN_TEXTS = 2000

HOOKS: list[Hook] = []


def period_for(today: date) -> tuple[date, date]:
    """Kỳ 7 ngày gần nhất đã trọn vẹn — kết thúc hôm qua để không tính một ngày đang dở."""
    end = today - timedelta(days=1)
    return end - timedelta(days=PERIOD_DAYS - 1), end


def _score_and_signal(fast: int, normal: int, slow: int, broken: int) -> tuple[float, str]:
    total = fast + normal + slow
    score = 70.0
    if total > 0:
        score += FAST_WEIGHT * (fast / total) - SLOW_WEIGHT * (slow / total)
    score -= min(MAX_BROKEN_PENALTY, broken * BROKEN_WEIGHT)
    score = round(max(0.0, min(100.0, score)), 2)
    if broken > 0:
        signal = f"{broken} lời hứa bị vỡ trong kỳ"
    elif total == 0:
        signal = "Không có tin khách cần phản hồi trong kỳ"
    elif slow > 0:
        signal = f"{slow}/{total} lượt phản hồi chậm hơn 60 phút"
    elif fast / total >= 0.8:
        signal = f"Phản hồi nhanh (dưới 15 phút) {fast}/{total} lượt"
    else:
        signal = f"{total} lượt phản hồi trong kỳ, tốc độ ở mức trung bình"
    return score, signal


def _recommendation(score: float) -> str:
    if score >= 80:
        return "Duy trì phong độ — có thể chia sẻ cách làm cho các bạn khác trong team"
    if score >= 60:
        return "Theo dõi thêm: nhắc trả lời khách trong 15 phút đầu và giữ đúng lời đã hứa"
    return "Cần coaching trực tiếp: ưu tiên tốc độ phản hồi và không hứa những điều không giữ được"


async def _trend(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, period_start: date,
                 score: float) -> str:
    prev = (await db.execute(text("""SELECT score FROM biz.people_reviews
                                     WHERE org_id = :o AND person_id = :p AND period_end < :ps
                                     ORDER BY period_end DESC, created_at DESC LIMIT 1"""),
                             {"o": org_id, "p": person_id, "ps": period_start})).scalar_one_or_none()
    if prev is None:
        return "flat"
    diff = score - float(prev)
    if diff > TREND_EPSILON:
        return "up"
    if diff < -TREND_EPSILON:
        return "down"
    return "flat"


async def _evidence_units(db: AsyncSession, org_id: uuid.UUID, staff_id: uuid.UUID, df: datetime,
                          dt: datetime) -> list[dict[str, Any]]:
    rows = (await db.execute(text(PAIR_CTE + """
        SELECT DISTINCT mu.id, mu.observed_at FROM paired p
        JOIN clean.meaning_units mu ON mu.person_id = p.customer_id AND mu.org_id = :o
             AND mu.observed_at >= :df AND mu.observed_at <= :dt AND mu.superseded_by IS NULL
        WHERE p.staff_id = :sid AND p.customer_id IS NOT NULL
        ORDER BY mu.observed_at DESC LIMIT :n"""),
        {"o": org_id, "df": df, "dt": dt, "sid": staff_id, "n": MAX_EVIDENCE_UNITS})).all()
    return [{"type": "meaning_unit", "id": str(r.id)} for r in rows]


async def _outbound_texts_by_staff(db: AsyncSession, org_id: uuid.UUID, staff_ids: list[uuid.UUID], df: datetime,
                                   dt: datetime) -> dict[uuid.UUID, list[str]]:
    """Tối đa `MAX_SCAN_TEXTS` tin ĐI mới nhất do CHÍNH từng nhân viên gửi trong kỳ — một truy vấn cho cả tổ chức
    (không chạy lại lưới ghép cho từng người). Không lấy tin khách: nhân viên không bị gắn cờ vì chữ khách gõ."""
    if not staff_ids:
        return {}
    rows = (await db.execute(text("""
        SELECT person_id, body_text FROM (
          SELECT pi.person_id, e.body_text,
                 row_number() OVER (PARTITION BY pi.person_id ORDER BY e.occurred_at DESC) AS rn
          FROM raw.events e
          JOIN core.person_identities pi ON pi.id = e.sender_identity_id
          WHERE e.org_id = :o AND e.direction = 'outbound' AND e.body_text IS NOT NULL
            AND e.occurred_at >= :df AND e.occurred_at <= :dt AND pi.person_id = ANY(:ids)
        ) t WHERE rn <= :n"""),
        {"o": org_id, "df": df, "dt": dt, "ids": staff_ids, "n": MAX_SCAN_TEXTS})).all()
    out: dict[uuid.UUID, list[str]] = {}
    for r in rows:
        out.setdefault(r.person_id, []).append(r.body_text)
    return out


def _suspicious(texts: list[str]) -> tuple[bool, str | None]:
    """Cờ 'Đáng ngờ' (F-60): tin nhân viên gửi giống lệnh cho AI hoặc xin điểm."""
    hits, sample = scan_texts(texts)
    if hits == 0:
        return False, None
    return True, (f"Có {hits} tin nhân viên gửi giống lệnh cho AI hoặc xin điểm (vd: “{sample}”) — "
                  "kiểm tra chứng cứ trước khi dùng điểm này.")


async def recompute_people_reviews_org(db: AsyncSession, org_id: uuid.UUID, *, today: date | None = None) -> int:
    period_start, period_end = period_for(today or datetime.now(UTC).date())
    df = datetime.combine(period_start, time.min, tzinfo=UTC)
    dt = datetime.combine(period_end, time.max, tzinfo=UTC)

    rows = (await db.execute(text(PAIR_CTE + f"""
        SELECT staff_id, {BUCKET_SELECT} FROM paired WHERE staff_id IS NOT NULL
        GROUP BY staff_id"""), {"o": org_id, "df": df, "dt": dt})).all()  # noqa: S608 — BUCKET_SELECT là hằng nội bộ

    broken_by = {r.promiser_person_id: r.n for r in (await db.execute(text("""
        SELECT promiser_person_id, count(*) AS n FROM biz.promises
        WHERE org_id = :o AND broken = true AND due_at >= :df AND due_at <= :dt
        GROUP BY promiser_person_id"""), {"o": org_id, "df": df, "dt": dt})).all()}
    texts_by = await _outbound_texts_by_staff(db, org_id, [r.staff_id for r in rows], df, dt)

    n = 0
    for r in rows:
        evidence = await _evidence_units(db, org_id, r.staff_id, df, dt)
        if not evidence:
            continue
        broken = broken_by.get(r.staff_id, 0)
        score, signal = _score_and_signal(r.fast, r.normal, r.slow, broken)
        trend = await _trend(db, org_id, r.staff_id, period_start, score)
        suspicious, reason = _suspicious(texts_by.get(r.staff_id, []))
        await db.execute(text("""
            INSERT INTO biz.people_reviews (org_id, person_id, period_start, period_end, score, trend, signal,
                                            recommendation, evidence, visibility, suspicious, suspicious_reason)
            VALUES (:o, :p, :ps, :pe, :sc, :tr, :sig, :rec, CAST(:ev AS jsonb), 'owner', :sus, :sus_r)
            ON CONFLICT (org_id, person_id, period_start, period_end) WHERE overridden_by IS NULL
            DO UPDATE SET score = EXCLUDED.score, trend = EXCLUDED.trend, signal = EXCLUDED.signal,
                          recommendation = EXCLUDED.recommendation, evidence = EXCLUDED.evidence,
                          suspicious = EXCLUDED.suspicious, suspicious_reason = EXCLUDED.suspicious_reason"""),
            {"o": org_id, "p": r.staff_id, "ps": period_start, "pe": period_end, "sc": score, "tr": trend,
             "sig": signal, "rec": _recommendation(score), "ev": orjson.dumps(evidence).decode(),
             "sus": suspicious, "sus_r": reason})
        # Dòng sửa tay cùng kỳ (dòng Sếp đang xem) cũng nhận cờ mới — tin đáng ngờ phát sinh SAU lúc sửa tay vẫn hiện
        # cờ. Giữ nguyên suspicious_cleared_* (đã bỏ cờ thì vẫn là đã bỏ).
        await db.execute(text("""
            UPDATE biz.people_reviews SET suspicious = :sus, suspicious_reason = :sus_r
            WHERE org_id = :o AND person_id = :p AND period_start = :ps AND period_end = :pe
              AND overridden_by IS NOT NULL
              AND (suspicious IS DISTINCT FROM :sus OR suspicious_reason IS DISTINCT FROM :sus_r)"""),
            {"o": org_id, "p": r.staff_id, "ps": period_start, "pe": period_end, "sus": suspicious,
             "sus_r": reason})
        n += 1
    return n


async def people_review_recompute(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    out: dict[str, int] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        async with sm() as db:
            n = await recompute_people_reviews_org(db, org)
            await db.commit()
        out[str(org)] = n
    return out


# v0.1.36 (F-45): 04:40 giờ VN (WorkerSettings.timezone) — ngoài giờ làm việc và cửa sổ cập nhật genh 02:30–03:30.
JOBS: list[CronJob] = [(people_review_recompute, {"hour": {4}, "minute": {40}})]
