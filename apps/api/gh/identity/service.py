"""Hợp nhất danh tính (spec G2, ARCHITECTURE §9): đề xuất có % + cơ sở, gộp/tách tay, lịch sử, đảo ngược.

Hệ thống không tự gộp. Gộp: mọi tài khoản kênh, thành viên nhóm, đơn vị ý nghĩa, sổ tay VÀ mọi dòng nghiệp vụ trỏ về
hồ sơ bị gộp (cơ hội, deal, việc, cảnh báo, lời hứa, điểm…; xem `PERSON_REFS`) chuyển sang hồ sơ giữ lại; hồ sơ bị gộp
trỏ `merged_into_id` (không xoá). Nhật ký lưu đủ ID để đảo ngược chính xác. "Không phải" quyết theo cặp NGƯỜI.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.data.common import CHANNEL_NAME
from gh.errors import ApiError, conflict, not_found
from gh.refinery import scoring

HIGH, MID = 0.85, 0.65
MAX_NEW = 200


def level(conf: float) -> str:
    return "high" if conf >= HIGH else "mid" if conf >= MID else "low"


# ─── Đề xuất ────────────────────────────────────────────────────────────────

WATERMARK_JOB = "identity.detect"
DETECT_LIMIT = 2000
NAME_THRESHOLD = 0.55
#: Quét đủ (không theo mốc) tối đa mỗi chừng này một lần — lưới an toàn cho thay đổi không làm "mới" định danh/hồ sơ
#: (vd sửa trực tiếp core.person_identities, transaction ingest dài hơn biên an toàn).
FULL_SCAN_EVERY = "24 hours"
#: Biên an toàn của mốc định danh: chỉ tiến `last_id` tới định danh tạo trước `now - biên` (uuid_v7 = giờ INSERT, không
#: phải giờ COMMIT) ⇒ định danh của transaction commit trễ (id nhỏ hơn định danh đã commit khác) vẫn được xét lại.
ID_MARGIN = "5 minutes"

#: Cặp ứng viên: vế a = định danh MỚI (id > mốc — uuid_v7 tăng theo thời gian) hoặc hồ sơ sửa sau mốc; vế b = mọi
#: định danh còn sống. Trùng SĐT (chỉ mục phone_e164) hoặc tên gần giống (`display_name % f.name` — pg_trgm tự gộp
#: hoa/thường; dùng chỉ mục gin trên display_name vì dưới RLS của gh_app chỉ phép so LEAKPROOF mới vào được điều
#: kiện chỉ mục, lower() thì không — xem 0026). Ngưỡng `pg_trgm.similarity_threshold` đặt bằng SET LOCAL; LATERAL …
#: OFFSET 0 giữ dạng "dò chỉ mục cho từng định danh mới". Mọi CTE MATERIALIZED: cặp ứng viên tính xong mới nối với
#: bảng gốc theo khoá chính — không để planner trải tích Đề-các định danh × định danh. Cặp đã có trong
#: identity_merge_candidates bị loại TRƯỚC LIMIT (NOT EXISTS khớp unique index LEAST/GREATEST của 0003) — trước
#: v0.1.40 LIMIT lấy cả cặp cũ rồi ON CONFLICT bỏ qua ⇒ đủ 2000 cặp cũ là không bao giờ ra đề xuất mới.
DETECT_SQL = """
WITH fresh AS MATERIALIZED (
  SELECT pi.id, pi.person_id, pi.phone_e164, lower(p.display_name) AS name
  FROM core.person_identities pi JOIN core.persons p ON p.id = pi.person_id
  WHERE p.org_id = :o AND p.merged_into_id IS NULL AND p.deleted_at IS NULL
    AND (CAST(:full AS boolean)
         OR pi.id > COALESCE(CAST(:last_id AS uuid), '00000000-0000-0000-0000-000000000000'::uuid)
         OR p.updated_at > CAST(:last_at AS timestamptz))),
cand AS MATERIALIZED (
  SELECT f.id AS a_id, pib.id AS b_id
  FROM fresh f JOIN core.person_identities pib ON pib.phone_e164 = f.phone_e164
  WHERE f.phone_e164 IS NOT NULL AND pib.person_id <> f.person_id
  UNION
  SELECT f.id, pib.id
  FROM fresh f
  CROSS JOIN LATERAL (SELECT pb.id FROM core.persons pb
                      WHERE pb.display_name % f.name AND pb.id <> f.person_id OFFSET 0) pb
  JOIN core.person_identities pib ON pib.person_id = pb.id),
pairs AS MATERIALIZED (
  SELECT DISTINCT LEAST(a_id, b_id) AS a, GREATEST(a_id, b_id) AS b FROM cand)
SELECT x.a, x.b, ia.phone_e164 IS NOT NULL AND ia.phone_e164 = ib.phone_e164 AS phone,
       similarity(lower(pa.display_name), lower(pb.display_name)) AS sim, ca.type AS ch_a, cb.type AS ch_b,
       EXISTS (SELECT 1 FROM core.group_members ga JOIN core.group_members gb ON gb.group_id = ga.group_id
               WHERE ga.person_id = pa.id AND gb.person_id = pb.id) AS cogroup
FROM pairs x
JOIN core.person_identities ia ON ia.id = x.a JOIN core.persons pa ON pa.id = ia.person_id
JOIN core.channels ca ON ca.id = ia.channel_id
JOIN core.person_identities ib ON ib.id = x.b JOIN core.persons pb ON pb.id = ib.person_id
JOIN core.channels cb ON cb.id = ib.channel_id
WHERE pa.id <> pb.id
  AND pa.org_id = :o AND pa.merged_into_id IS NULL AND pa.deleted_at IS NULL
  AND pb.org_id = :o AND pb.merged_into_id IS NULL AND pb.deleted_at IS NULL
  AND ((ia.phone_e164 IS NOT NULL AND ia.phone_e164 = ib.phone_e164)
       OR similarity(lower(pa.display_name), lower(pb.display_name)) >= :th)
  AND NOT EXISTS (SELECT 1 FROM core.identity_merge_candidates m
                  WHERE LEAST(m.identity_a, m.identity_b) = x.a AND GREATEST(m.identity_a, m.identity_b) = x.b)
  -- v0.1.61 (F059): Sếp đã trả lời "Không phải" cho cặp NGƯỜI này (qua bất kỳ cặp tài khoản nào) ⇒ không hỏi lại,
  -- kể cả với tài khoản anh em mới. Tra theo khoá LEAST/GREATEST của chỉ mục duy nhất 0003.
  AND NOT EXISTS (SELECT 1 FROM core.person_identities xa
                  JOIN core.person_identities xb ON xb.person_id = pb.id
                  JOIN core.identity_merge_candidates m ON m.status = 'rejected'
                   AND LEAST(m.identity_a, m.identity_b) = LEAST(xa.id, xb.id)
                   AND GREATEST(m.identity_a, m.identity_b) = GREATEST(xa.id, xb.id)
                  WHERE xa.person_id = pa.id)
ORDER BY 3 DESC, 4 DESC LIMIT :lim
"""


async def detect(db: AsyncSession, org_id: uuid.UUID) -> int:
    """Sinh đề xuất mới (không trùng cặp): trùng số điện thoại, tên gần giống, cùng nhóm.

    v0.1.40 (F-16): có mốc tiến độ (`ops.job_watermarks`, job 'identity.detect') — lượt sau chỉ xét định danh mới/hồ
    sơ vừa sửa so với mọi định danh; lần đầu và mỗi `FULL_SCAN_EVERY` quét đủ một lần. Mốc CHỈ tiến khi lượt không
    bị cắt bởi LIMIT/MAX_NEW; `last_id` lùi một biên `ID_MARGIN` (định danh của transaction commit trễ vẫn được xét).
    Định danh cũ có thêm SĐT (`ingest.upsert_identity`) chạm `persons.updated_at` ⇒ thành "mới" ở lượt sau."""
    wm = (await db.execute(text("""
        SELECT last_id, last_at, full_at IS NULL OR full_at < now() - CAST(CAST(:every AS text) AS interval) AS full_due
        FROM ops.job_watermarks WHERE org_id = :o AND job = :j"""),
        {"o": org_id, "j": WATERMARK_JOB, "every": FULL_SCAN_EVERY})).one_or_none()
    # Mốc định danh: id lớn nhất trong các định danh TẠO trước now - biên (so sánh uuid_v7 với id nhỏ nhất của mili-giây
    # đó: 48 bit đầu = epoch ms, phần còn lại 0 — xem core.uuid_v7()).
    mark = (await db.execute(text("""
        WITH b AS (SELECT clock_timestamp() AS at,
                          CAST(rpad(lpad(to_hex(CAST(floor(extract(epoch FROM clock_timestamp()
                                                                - CAST(CAST(:margin AS text) AS interval))
                                                         * 1000) AS bigint)),
                                         12, '0'), 32, '0') AS uuid) AS floor_id)
        SELECT b.at,
               (SELECT pi.id FROM core.person_identities pi JOIN core.persons p ON p.id = pi.person_id
                WHERE p.org_id = :o AND pi.id < b.floor_id ORDER BY pi.id DESC LIMIT 1) AS max_id
        FROM b"""), {"o": org_id, "margin": ID_MARGIN})).one()
    # SET LOCAL: chỉ trong transaction này, không rò sang kết nối khác của pool (khác set_limit()).
    await db.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {NAME_THRESHOLD}"))
    # Planner coi `%` là phép rẻ ⇒ hay chọn quét tuần tự core.persons cho TỪNG định danh mới (N×M phép so trigram —
    # 4000 định danh mất hơn một phút). Tắt seqscan riêng cho câu này để luôn dò qua chỉ mục (mọi bước đều có chỉ mục).
    await db.execute(text("SET LOCAL enable_seqscan = off"))
    full = wm is None or (wm.last_id is None and wm.last_at is None) or bool(wm.full_due)
    rows = (await db.execute(text(DETECT_SQL), {
        "o": org_id, "full": full, "last_id": None if wm is None else wm.last_id,
        "last_at": None if wm is None else wm.last_at, "th": NAME_THRESHOLD, "lim": DETECT_LIMIT})).all()
    await db.execute(text("SET LOCAL enable_seqscan = on"))
    truncated = len(rows) >= DETECT_LIMIT
    n = 0
    for r in rows:
        conf, parts = 0.0, []
        basis: dict[str, Any] = {}
        if r.phone:
            conf = 0.90
            basis["phone"] = True
            parts.append("trùng số điện thoại")
            if r.sim >= 0.5:
                conf += 0.06
                parts.append("tên gần giống")
        else:
            conf = 0.30 + 0.45 * float(r.sim)
            basis["name"] = round(float(r.sim), 2)
            parts.append("trùng cách viết tên" if r.sim >= 0.8 else "tên gần giống")
        if r.cogroup:
            conf += 0.08
            basis["co_group"] = True
            parts.append("cùng nhóm chat")
        if r.ch_a == r.ch_b and not r.phone:
            basis["same_channel"] = True
            parts.append(f"cùng kênh {CHANNEL_NAME.get(r.ch_a, r.ch_a)}")
        conf = round(min(0.99, conf), 3)
        text_basis = " và ".join(parts[:2]) + (", " + ", ".join(parts[2:]) if len(parts) > 2 else "")
        if not r.phone and r.sim < 0.8 and not r.cogroup:
            text_basis = "chỉ trùng tên, chưa có tín hiệu khác"
        res = await db.execute(text("""
            INSERT INTO core.identity_merge_candidates (org_id, identity_a, identity_b, confidence, basis, basis_text)
            VALUES (:o, :a, :b, :c, CAST(:bj AS jsonb), :bt) ON CONFLICT DO NOTHING"""),
            {"o": org_id, "a": r.a, "b": r.b, "c": conf, "bj": orjson.dumps(basis).decode(), "bt": text_basis})
        n += res.rowcount or 0  # type: ignore[attr-defined]
        if n >= MAX_NEW:
            truncated = truncated or r is not rows[-1]
            break
    if not truncated:
        # Mốc lùi 5 phút: hồ sơ sửa trong transaction khác commit SAU khi lượt này bắt đầu vẫn được xét lại lượt sau
        # (cặp đã đề xuất bị NOT EXISTS loại nên không trùng).
        # full_at chỉ đặt khi lượt QUÉT ĐỦ không bị cắt — bị cắt thì lượt sau quét đủ tiếp.
        await db.execute(text("""
            INSERT INTO ops.job_watermarks (org_id, job, last_id, last_at, full_at, updated_at)
            VALUES (:o, :j, :id, CAST(:at AS timestamptz) - interval '5 minutes',
                    CASE WHEN CAST(:full AS boolean) THEN CAST(:at AS timestamptz) END, now())
            ON CONFLICT (org_id, job) DO UPDATE SET
              last_id = GREATEST(ops.job_watermarks.last_id, EXCLUDED.last_id),
              last_at = EXCLUDED.last_at,
              full_at = COALESCE(EXCLUDED.full_at, ops.job_watermarks.full_at), updated_at = now()"""),
            {"o": org_id, "j": WATERMARK_JOB, "id": mark.max_id, "at": mark.at, "full": full})
    return n


def _side(r: Any, p: str) -> dict[str, Any]:
    ch = getattr(r, f"{p}_ch")
    meta = CHANNEL_NAME.get(ch, ch)
    phone = getattr(r, f"{p}_phone")
    handle = getattr(r, f"{p}_handle")
    if phone:
        meta += f" · {phone[:-6]} xxx {phone[-3:]}" if len(phone) > 8 else f" · {phone}"
    elif handle:
        meta += f" · {handle}"
    return {"identity_id": str(getattr(r, f"{p}_id")), "channel": ch, "meta": meta,
            "person": {"id": str(getattr(r, f"{p}_pid")), "code": getattr(r, f"{p}_pcode"),
                       "name": getattr(r, f"{p}_pname")}}


CANDIDATE_SQL = """
SELECT m.id, m.confidence, m.basis, m.basis_text, m.status, m.created_at,
       ia.id AS a_id, ca.type AS a_ch, ia.phone_e164 AS a_phone, ia.handle AS a_handle,
       pa.id AS a_pid, pa.code AS a_pcode, pa.display_name AS a_pname,
       ib.id AS b_id, cb.type AS b_ch, ib.phone_e164 AS b_phone, ib.handle AS b_handle,
       pb.id AS b_pid, pb.code AS b_pcode, pb.display_name AS b_pname
FROM core.identity_merge_candidates m
JOIN core.person_identities ia ON ia.id = m.identity_a JOIN core.channels ca ON ca.id = ia.channel_id
JOIN core.persons pa ON pa.id = ia.person_id
JOIN core.person_identities ib ON ib.id = m.identity_b JOIN core.channels cb ON cb.id = ib.channel_id
JOIN core.persons pb ON pb.id = ib.person_id
WHERE m.org_id = :o
"""


def candidate_payload(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "confidence": float(r.confidence), "level": level(float(r.confidence)),
            "basis": r.basis_text or "", "basis_detail": r.basis or {}, "status": r.status,
            "a": _side(r, "a"), "b": _side(r, "b")}


async def candidates(db: AsyncSession, org_id: uuid.UUID, status: str = "pending") -> list[dict[str, Any]]:
    rows = (await db.execute(text(CANDIDATE_SQL + """ AND m.status = :s
        AND (m.status <> 'pending' OR ia.person_id <> ib.person_id)
        ORDER BY m.confidence DESC, m.created_at LIMIT 200"""), {"o": org_id, "s": status})).all()
    return [candidate_payload(r) for r in rows]


async def stats(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    r = (await db.execute(text("""
        SELECT
          (SELECT count(*) FROM core.persons p WHERE p.org_id = :o AND p.merged_into_id IS NULL AND p.deleted_at IS NULL
             AND NOT COALESCE((p.attrs->>'auto')::boolean, false)
             AND (SELECT count(*) FROM core.person_identities i WHERE i.person_id = p.id) >= 2) AS merged_people,
          (SELECT count(*) FROM core.persons p WHERE p.org_id = :o AND p.merged_into_id IS NULL AND p.deleted_at IS NULL
             AND NOT COALESCE((p.attrs->>'auto')::boolean, false)) AS live_profiles,
          (SELECT count(*) FROM core.identity_merge_candidates m
             JOIN core.person_identities a ON a.id = m.identity_a JOIN core.person_identities b ON b.id = m.identity_b
             WHERE m.org_id = :o AND m.status = 'pending' AND a.person_id <> b.person_id) AS pending_pairs,
          (SELECT count(*) FROM core.identity_merge_log l WHERE l.org_id = :o AND l.op = 'split'
             AND l.reverted_at IS NULL) AS manual_splits,
          (SELECT count(*) FROM core.person_identities i JOIN core.persons p ON p.id = i.person_id
             WHERE p.org_id = :o AND p.merged_into_id IS NULL
               AND COALESCE((p.attrs->>'auto')::boolean, false)) AS unlinked_accounts"""), {"o": org_id})).one()
    return dict(r._mapping)


# ─── Gộp / tách / đảo ngược ────────────────────────────────────────────────

@dataclass(frozen=True)
class PersonRef:
    """Một cột nghiệp vụ trỏ về hồ sơ người. `subject_type` ≠ None: cột là `subject_id` đa hình, chỉ dòng
    `subject_type = <giá trị>` mới là người. `guard`: điều kiện thêm (bí danh bảng `t`) để dòng KHÔNG va chạm
    ràng buộc duy nhất khi dời; dòng không đạt ở lại hồ sơ cũ."""
    table: str
    col: str
    subject_type: str | None = None
    guard: str | None = None


#: Mọi bảng nghiệp vụ trỏ về người, dời theo `merged_into_id` khi gộp (v0.1.61, F054). Thêm bảng mới trỏ về người ⇒ thêm
#: vào đây hoặc vào PERSON_REFS_EXCLUDED kèm lý do — `test_gop_nguoi_v016x` dò information_schema để không sót.
PERSON_REFS: tuple[PersonRef, ...] = (
    PersonRef("biz.opportunities", "person_id"),
    PersonRef("biz.deals", "person_id"),
    PersonRef("biz.market_signals", "person_id"),
    # Chỉ mục duy nhất theo (tổ chức, người, kỳ) cho đánh giá hệ thống chưa bị ghi đè.
    PersonRef("biz.people_reviews", "person_id", guard="""(t.overridden_by IS NOT NULL OR NOT EXISTS (
        SELECT 1 FROM biz.people_reviews x WHERE x.org_id = t.org_id AND x.person_id = :d
          AND x.period_start = t.period_start AND x.period_end = t.period_end AND x.overridden_by IS NULL))"""),
    PersonRef("biz.promises", "promiser_person_id"),
    PersonRef("biz.promises", "to_person_id"),
    PersonRef("biz.documents", "owner_person_id"),
    PersonRef("biz.tasks", "subject_id", "person"),
    PersonRef("biz.alerts", "subject_id", "person"),
    PersonRef("biz.cases", "subject_id", "person"),
    PersonRef("biz.action_drafts", "subject_id", "person"),
    PersonRef("core.assignments", "subject_id", "person"),
    # v0.1.68: sự kiện «người lạ nhắn riêng lần đầu» của Nhật ký lắng nghe — gộp xong hiện đúng hồ sơ giữ lại.
    PersonRef("ops.listen_events", "person_id"),
)
PERSON_REFS_COLUMNS: tuple[tuple[str, str], ...] = tuple((r.table, r.col) for r in PERSON_REFS)
#: Cột trỏ về người KHÔNG dời bằng PERSON_REFS (kèm lý do).
PERSON_REFS_EXCLUDED: dict[str, str] = {
    "core.person_identities.person_id": "tài khoản kênh — `_move` dời riêng",
    "clean.meaning_units.person_id": "đơn vị ý nghĩa — `_move` dời riêng",
    "core.group_members.person_id": "thành viên nhóm — `_move` sao sang hồ sơ giữ",
    "memory.notebooks.subject_id": "sổ tay — `_move` chuyển mục sang sổ hồ sơ giữ",
    "clean.current_scores.subject_id": "điểm hiện tại — `_move` lưu rồi xoá, gộp xong tính lại cho hồ sơ giữ",
    "clean.score_snapshots.subject_id": "lịch sử điểm bất biến (chỉ thêm, không sửa)",
    "refinery.item_marks.subject_id": "dấu khử trùng/chất lượng của tin — dữ liệu máy, không hiển thị",
    "core.users.person_id": "liên kết tài khoản nhân sự nội bộ — không phải khách để gộp",
    "ops.data_requests.person_id": "yêu cầu xuất/xoá dữ liệu — bằng chứng pháp lý giữ nguyên hồ sơ gốc",
    "core.identity_merge_log.from_person": "chính nhật ký gộp",
    "core.identity_merge_log.to_person": "chính nhật ký gộp",
    "ops.owner_questions.subject_id": "câu hỏi chờ Owner (v0.1.71): luôn trỏ về NHÓM (group_id), không phải người",
}
_REFS_BY_KEY = {f"{r.table}.{r.col}": r for r in PERSON_REFS}


def _ref_where(r: PersonRef) -> str:
    return f"t.{r.col} = :s" + (f" AND t.subject_type = '{r.subject_type}'" if r.subject_type else "")


async def _move_refs(db: AsyncSession, src: uuid.UUID, dst: uuid.UUID) -> dict[str, list[str]]:
    """Dời mọi dòng nghiệp vụ trỏ về `src` sang `dst`; trả {"bảng.cột": [id dòng đã dời]} để đảo ngược chính xác."""
    out: dict[str, list[str]] = {}
    for r in PERSON_REFS:
        guard = f" AND {r.guard}" if r.guard else ""
        ids = (await db.execute(text(f"""UPDATE {r.table} t SET {r.col} = :d WHERE {_ref_where(r)}{guard}
                                         RETURNING t.id"""),  # noqa: S608
                                {"s": src, "d": dst})).scalars().all()
        if ids:
            out[f"{r.table}.{r.col}"] = [str(i) for i in ids]
    return out


async def _pin_dm_targets(db: AsyncSession, *people: uuid.UUID) -> None:
    """Bản nháp 1-1 đang chờ duyệt chỉ ghi `person_id` + kênh trong đích gửi. Sau gộp, hồ sơ giữ có thể có 2 tài
    khoản cùng kênh nên "tài khoản cũ nhất" có thể không phải tài khoản người đó vốn dùng. Trước khi dời, ghi cố định
    `identity_id` (tài khoản cũ nhất của đúng người đó trên kênh đó) vào đích; tài khoản đi theo người nên đích vẫn
    đúng sau gộp VÀ sau đảo ngược.
    Chạy lại an toàn: chỉ đụng nháp chưa có `identity_id`. Nháp đã duyệt/gửi không đụng (permit đã chốt thread)."""
    await db.execute(text("""
        UPDATE biz.action_drafts d SET body = jsonb_set(d.body, '{target,identity_id}', to_jsonb(pi.id::text))
        FROM (SELECT DISTINCT ON (i.person_id, c.type) i.id, i.person_id, c.type AS channel
              FROM core.person_identities i JOIN core.channels c ON c.id = i.channel_id
              WHERE i.person_id = ANY(:ps) ORDER BY i.person_id, c.type, i.first_seen_at, i.id) pi
        WHERE d.status = 'pending' AND d.body->'target'->>'thread_type' = 'user'
          AND d.body->'target'->>'identity_id' IS NULL
          AND d.body->'target'->>'person_id' = pi.person_id::text AND d.body->'target'->>'channel' = pi.channel"""),
        {"ps": list(people)})


async def _move_scores(db: AsyncSession, src: uuid.UUID) -> list[dict[str, Any]]:
    """Điểm hiện tại của hồ sơ bị gộp: lưu vào snapshot rồi xoá (hồ sơ giữ được tính lại từ đơn vị ý nghĩa đã dời)."""
    rows = (await db.execute(text("""DELETE FROM clean.current_scores WHERE subject_type = 'person' AND subject_id = :s
                                     RETURNING dimension, value, trend, snapshot_id, updated_at"""),
                             {"s": src})).all()
    return [{"dimension": r.dimension, "value": str(r.value), "trend": r.trend, "snapshot_id": str(r.snapshot_id),
             "updated_at": r.updated_at.isoformat()} for r in rows]


async def _refresh_scores(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID) -> None:
    await scoring.refresh_subject(db, org_id, "person", person_id, await scoring.weights(db, org_id))


async def _move(db: AsyncSession, src: uuid.UUID, dst: uuid.UUID, identity_ids: list[uuid.UUID] | None,
                unit_ids: list[uuid.UUID] | None = None) -> dict[str, Any]:
    """Chuyển tài khoản kênh (tất cả hoặc danh sách), đơn vị ý nghĩa, thành viên nhóm, sổ tay và mọi dòng nghiệp vụ
    (`PERSON_REFS`) từ src sang dst; điểm của src được lưu rồi xoá."""
    if identity_ids is None:
        identity_ids = list((await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p"),
                                              {"p": src})).scalars().all())
    await db.execute(text("UPDATE core.person_identities SET person_id = :d WHERE id = ANY(:ids)"),
                     {"d": dst, "ids": identity_ids})
    if unit_ids is None:
        unit_ids = list((await db.execute(text("SELECT id FROM clean.meaning_units WHERE person_id = :p"),
                                          {"p": src})).scalars().all())
    await db.execute(text("UPDATE clean.meaning_units SET person_id = :d WHERE id = ANY(:ids)"),
                     {"d": dst, "ids": unit_ids})
    groups = list((await db.execute(text("""
        INSERT INTO core.group_members (group_id, person_id, role, joined_at)
        SELECT group_id, :d, role, joined_at FROM core.group_members WHERE person_id = :s
        ON CONFLICT DO NOTHING RETURNING group_id"""), {"s": src, "d": dst})).scalars().all())
    # Sổ tay: chuyển mục sang sổ của hồ sơ đích (tạo nếu chưa có).
    nb_src = (await db.execute(text("""SELECT id, org_id FROM memory.notebooks
                                       WHERE subject_type = 'person' AND subject_id = :s"""), {"s": src})).one_or_none()
    entry_ids: list[uuid.UUID] = []
    if nb_src is not None:
        from gh.memory import notebook

        nb_dst = await notebook.ensure(db, nb_src.org_id, "person", dst)
        entry_ids = list((await db.execute(text("""UPDATE memory.entries SET notebook_id = :d WHERE notebook_id = :s
                                                   RETURNING id"""), {"d": nb_dst.id, "s": nb_src.id})).scalars().all())
        await notebook.recount(db, nb_src.id)
        await notebook.recount(db, nb_dst.id)
    refs = await _move_refs(db, src, dst)
    scores = await _move_scores(db, src)
    return {"identities": [str(i) for i in identity_ids], "units": [str(u) for u in unit_ids],
            "groups_added": [str(g) for g in groups], "entries": [str(e) for e in entry_ids],
            "refs": refs, "scores": scores}


async def merge(db: AsyncSession, org_id: uuid.UUID, candidate_id: uuid.UUID, actor: uuid.UUID) -> dict[str, Any]:
    c = (await db.execute(text(CANDIDATE_SQL + " AND m.id = :i FOR UPDATE OF m"),
                          {"o": org_id, "i": candidate_id})).one_or_none()
    if c is None:
        raise not_found("Cặp đề xuất")
    if c.status != "pending":
        raise conflict("CANDIDATE_DECIDED", "Cặp này đã được xử lý")
    if c.a_pid == c.b_pid:
        raise conflict("ALREADY_MERGED", "Hai tài khoản đã thuộc cùng một người")
    keep, drop = await _pick_keep(db, c.a_pid, c.b_pid)
    await _pin_dm_targets(db, keep, drop)      # bản nháp 1-1 đang chờ giữ đúng tài khoản nhận qua gộp/đảo ngược
    moved = await _move(db, drop, keep, None)
    await _refresh_scores(db, org_id, keep)
    await db.execute(text("""UPDATE core.persons SET merged_into_id = :k,
                             attrs = attrs || '{"auto": false}' WHERE id = :d"""), {"k": keep, "d": drop})
    await db.execute(text("""UPDATE core.persons SET attrs = attrs || '{"auto": false}' WHERE id = :k"""), {"k": keep})
    await db.execute(text("""UPDATE core.identity_merge_candidates SET status = 'merged', decided_by = :u,
                             decided_at = now() WHERE id = :i"""), {"u": actor, "i": candidate_id})
    log_id = (await db.execute(text("""
        INSERT INTO core.identity_merge_log (org_id, op, from_person, to_person, identities, actor_user, candidate_id,
                                             snapshot)
        VALUES (:o, 'merge', :f, :t, :ids, :u, :c, CAST(:s AS jsonb)) RETURNING id"""),
        {"o": org_id, "f": drop, "t": keep, "ids": [uuid.UUID(i) for i in moved["identities"]], "u": actor,
         "c": candidate_id, "s": orjson.dumps(moved).decode()})).scalar_one()
    return {"person": await person_ref(db, keep), "merged": await person_ref(db, drop), "log_id": str(log_id)}


async def reject(db: AsyncSession, org_id: uuid.UUID, candidate_id: uuid.UUID, actor: uuid.UUID) -> int:
    """"Không phải": quyết cho cả cặp NGƯỜI — mọi đề xuất đang chờ giữa hai hồ sơ này (các cặp tài khoản anh em) cùng bị
    từ chối; lượt dò sau không đề xuất lại (DETECT_SQL). Trả số đề xuất đã từ chối."""
    c = (await db.execute(text("""
        SELECT ia.person_id AS a_pid, ib.person_id AS b_pid FROM core.identity_merge_candidates m
        JOIN core.person_identities ia ON ia.id = m.identity_a JOIN core.person_identities ib ON ib.id = m.identity_b
        WHERE m.id = :i AND m.org_id = :o AND m.status = 'pending' FOR UPDATE OF m"""),
        {"i": candidate_id, "o": org_id})).one_or_none()
    if c is None:
        raise not_found("Cặp đề xuất đang chờ")
    n = (await db.execute(text("""
        UPDATE core.identity_merge_candidates m SET status = 'rejected', decided_by = :u, decided_at = now()
        FROM core.person_identities ia, core.person_identities ib
        WHERE ia.id = m.identity_a AND ib.id = m.identity_b AND m.org_id = :o AND m.status = 'pending'
          AND LEAST(ia.person_id, ib.person_id) = LEAST(CAST(:pa AS uuid), CAST(:pb AS uuid))
          AND GREATEST(ia.person_id, ib.person_id) = GREATEST(CAST(:pa AS uuid), CAST(:pb AS uuid))"""),
        {"u": actor, "o": org_id, "pa": c.a_pid, "pb": c.b_pid})).rowcount  # type: ignore[attr-defined]
    return int(n)


async def _pick_keep(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Giữ hồ sơ do người xác nhận (không tự tạo) hoặc có nhiều dữ liệu hơn; hoà thì giữ hồ sơ cũ hơn."""
    rows = {r.id: r for r in (await db.execute(text("""
        SELECT p.id, COALESCE((p.attrs->>'auto')::boolean, false) AS auto, p.created_at,
               (SELECT count(*) FROM clean.meaning_units m WHERE m.person_id = p.id) AS units
        FROM core.persons p WHERE p.id IN (:a, :b)"""), {"a": a, "b": b})).all()}
    ra, rb = rows[a], rows[b]
    key_a = (not ra.auto, ra.units, -ra.created_at.timestamp())
    key_b = (not rb.auto, rb.units, -rb.created_at.timestamp())
    return (a, b) if key_a >= key_b else (b, a)


async def split(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, identity_ids: list[uuid.UUID],
                actor: uuid.UUID) -> dict[str, Any]:
    p = (await db.execute(text("""SELECT id, display_name FROM core.persons WHERE id = :p AND org_id = :o
                                  AND merged_into_id IS NULL AND deleted_at IS NULL FOR UPDATE"""),
                          {"p": person_id, "o": org_id})).one_or_none()
    if p is None:
        raise not_found("Hồ sơ")
    owned = set((await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p"),
                                  {"p": person_id})).scalars().all())
    ids = list(dict.fromkeys(identity_ids))
    if not ids or not set(ids) <= owned:
        raise ApiError(422, "VALIDATION", "Dữ liệu chưa hợp lệ", errors={"identity_ids": "Tài khoản không thuộc hồ sơ"})
    if set(ids) == owned:
        raise ApiError(422, "VALIDATION", "Dữ liệu chưa hợp lệ",
                       errors={"identity_ids": "Phải giữ lại ít nhất một tài khoản ở hồ sơ gốc"})
    handle = (await db.execute(text("SELECT handle FROM core.person_identities WHERE id = :i"),
                               {"i": ids[0]})).scalar_one()
    code = (await db.execute(text("SELECT core.next_code('PER')"))).scalar_one()
    new_id = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name, attrs)
                                       VALUES (:o, :c, :n, '{"auto": false, "split_from": true}') RETURNING id"""),
                               {"o": org_id, "c": code, "n": handle or f"{p.display_name} (tách)"})).scalar_one()
    # Đơn vị ý nghĩa đi theo tài khoản đã nói ra chúng (qua chứng cứ).
    unit_ids = list((await db.execute(text("""
        SELECT DISTINCT m.id FROM clean.meaning_units m JOIN clean.evidence x ON x.meaning_unit_id = m.id
        JOIN raw.events e ON e.id = x.raw_event_id AND e.received_at = x.raw_received_at
        WHERE m.person_id = :p AND e.sender_identity_id = ANY(:ids)"""), {"p": person_id, "ids": ids})).scalars().all())
    await db.execute(text("UPDATE core.person_identities SET person_id = :d WHERE id = ANY(:ids)"),
                     {"d": new_id, "ids": ids})
    await db.execute(text("UPDATE clean.meaning_units SET person_id = :d WHERE id = ANY(:ids)"),
                     {"d": new_id, "ids": unit_ids})
    snapshot = {"identities": [str(i) for i in ids], "units": [str(u) for u in unit_ids]}
    log_id = (await db.execute(text("""
        INSERT INTO core.identity_merge_log (org_id, op, from_person, to_person, identities, actor_user, snapshot)
        VALUES (:o, 'split', :f, :t, :ids, :u, CAST(:s AS jsonb)) RETURNING id"""),
        {"o": org_id, "f": person_id, "t": new_id, "ids": ids, "u": actor,
         "s": orjson.dumps(snapshot).decode()})).scalar_one()
    return {"person": await person_ref(db, new_id), "from": await person_ref(db, person_id), "log_id": str(log_id)}


async def revert(db: AsyncSession, org_id: uuid.UUID, log_id: uuid.UUID, actor: uuid.UUID) -> dict[str, Any]:
    lg = (await db.execute(text("""SELECT id, op, from_person, to_person, snapshot, reverted_at, at, candidate_id
                                   FROM core.identity_merge_log WHERE id = :i AND org_id = :o FOR UPDATE"""),
                           {"i": log_id, "o": org_id})).one_or_none()
    if lg is None:
        raise not_found("Thao tác")
    if lg.reverted_at is not None:
        raise conflict("ALREADY_REVERTED", "Thao tác này đã được đảo ngược")
    later = (await db.execute(text("""
        SELECT 1 FROM core.identity_merge_log WHERE org_id = :o AND reverted_at IS NULL AND at > :t AND id <> :i
          AND (from_person IN (:f, :to) OR to_person IN (:f, :to)) LIMIT 1"""),
        {"o": org_id, "t": lg.at, "i": lg.id, "f": lg.from_person, "to": lg.to_person})).scalar()
    if later:
        raise conflict("REVERT_ORDER", "Có thao tác gộp/tách mới hơn trên hồ sơ này — hãy đảo ngược thao tác mới trước")
    snap = lg.snapshot or {}
    ids = [uuid.UUID(i) for i in snap.get("identities", [])]
    units = [uuid.UUID(u) for u in snap.get("units", [])]
    if lg.op == "merge":
        # to_person đang giữ mọi thứ; trả phần của from_person về.
        await db.execute(text("UPDATE core.persons SET merged_into_id = NULL WHERE id = :f"), {"f": lg.from_person})
        await db.execute(text("UPDATE core.person_identities SET person_id = :f WHERE id = ANY(:ids)"),
                         {"f": lg.from_person, "ids": ids})
        await db.execute(text("UPDATE clean.meaning_units SET person_id = :f WHERE id = ANY(:ids)"),
                         {"f": lg.from_person, "ids": units})
        added = [uuid.UUID(g) for g in snap.get("groups_added", [])]
        if added:
            await db.execute(text("DELETE FROM core.group_members WHERE person_id = :t AND group_id = ANY(:g)"),
                             {"t": lg.to_person, "g": added})
        # Dòng nghiệp vụ đã dời (v0.1.61): trả về hồ sơ cũ — chỉ dòng còn trỏ vào hồ sơ giữ. Khoá lạ bị bỏ qua.
        for key, row_ids in (snap.get("refs") or {}).items():
            r = _REFS_BY_KEY.get(key)
            if r is not None and row_ids:
                await db.execute(text(f"""UPDATE {r.table} t SET {r.col} = :f
                                          WHERE t.id = ANY(:ids) AND t.{r.col} = :t"""),  # noqa: S608
                                 {"f": lg.from_person, "t": lg.to_person, "ids": [uuid.UUID(i) for i in row_ids]})
        for sc in snap.get("scores") or []:
            await db.execute(text("""
                INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value, trend, snapshot_id,
                                                  updated_at)
                VALUES ('person', :f, :d, CAST(:v AS numeric), :tr, CAST(:sid AS uuid), :at)
                ON CONFLICT (subject_type, subject_id, dimension) DO NOTHING"""),
                {"f": lg.from_person, "d": sc["dimension"], "v": sc["value"], "tr": sc.get("trend"),
                 "sid": sc["snapshot_id"], "at": datetime.fromisoformat(sc["updated_at"])})
        entries = [uuid.UUID(e) for e in snap.get("entries", [])]
        if entries:
            nb = (await db.execute(text("""SELECT id FROM memory.notebooks WHERE subject_type = 'person'
                                           AND subject_id = :f"""), {"f": lg.from_person})).scalar_one_or_none()
            if nb is not None:
                await db.execute(text("UPDATE memory.entries SET notebook_id = :n WHERE id = ANY(:ids)"),
                                 {"n": nb, "ids": entries})
        if lg.candidate_id:
            await db.execute(text("""UPDATE core.identity_merge_candidates SET status = 'pending', decided_by = NULL,
                                     decided_at = NULL WHERE id = :c"""), {"c": lg.candidate_id})
        restored = lg.from_person
        await _refresh_scores(db, org_id, lg.to_person)  # hồ sơ giữ mất các đơn vị đã trả ⇒ tính lại điểm
    else:
        # Tách: trả tài khoản + đơn vị về hồ sơ gốc; hồ sơ tách ra trỏ về gốc.
        await db.execute(text("UPDATE core.person_identities SET person_id = :f WHERE id = ANY(:ids)"),
                         {"f": lg.from_person, "ids": ids})
        await db.execute(text("UPDATE clean.meaning_units SET person_id = :f WHERE id = ANY(:ids)"),
                         {"f": lg.from_person, "ids": units})
        await db.execute(text("UPDATE core.persons SET merged_into_id = :f WHERE id = :t"),
                         {"f": lg.from_person, "t": lg.to_person})
        restored = lg.from_person
    await db.execute(text("UPDATE core.identity_merge_log SET reverted_at = now(), reverted_by = :u WHERE id = :i"),
                     {"u": actor, "i": lg.id})
    return {"person": await person_ref(db, restored), "log_id": str(lg.id), "op": lg.op}


async def history(db: AsyncSession, org_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT l.id, l.op, l.at, l.identities, l.reverted_at, u.display_name AS actor,
               pf.id AS f_id, pf.code AS f_code, pf.display_name AS f_name,
               pt.id AS t_id, pt.code AS t_code, pt.display_name AS t_name
        FROM core.identity_merge_log l
        JOIN core.persons pf ON pf.id = l.from_person JOIN core.persons pt ON pt.id = l.to_person
        LEFT JOIN core.users u ON u.id = l.actor_user
        WHERE l.org_id = :o ORDER BY l.at DESC LIMIT :n"""), {"o": org_id, "n": limit})).all()
    return [{"id": str(r.id), "op": r.op, "at": r.at.isoformat().replace("+00:00", "Z"), "actor": r.actor,
             "from": {"id": str(r.f_id), "code": r.f_code, "name": r.f_name},
             "to": {"id": str(r.t_id), "code": r.t_code, "name": r.t_name},
             "identities": len(r.identities or []), "reverted": r.reverted_at is not None} for r in rows]


async def person_ref(db: AsyncSession, person_id: uuid.UUID) -> dict[str, Any]:
    r = (await db.execute(text("SELECT id, code, display_name FROM core.persons WHERE id = :i"),
                          {"i": person_id})).one()
    return {"id": str(r.id), "code": r.code, "name": r.display_name}


async def evidence(db: AsyncSession, org_id: uuid.UUID, candidate_id: uuid.UUID) -> list[dict[str, Any]]:
    """Chứng cứ của một cặp: tin gần nhất của mỗi tài khoản (ai nói gì, ở đâu) để Sếp tự so."""
    from gh.data.common import RAW_SELECT, raw_item

    c = (await db.execute(text("""SELECT identity_a, identity_b, basis_text FROM core.identity_merge_candidates
                                  WHERE id = :i AND org_id = :o"""), {"i": candidate_id, "o": org_id})).one_or_none()
    if c is None:
        raise not_found("Cặp đề xuất")
    out: list[dict[str, Any]] = []
    for ident, note in ((c.identity_a, "Tài khoản A"), (c.identity_b, "Tài khoản B")):
        rows = (await db.execute(text(RAW_SELECT + """ WHERE e.sender_identity_id = :i
                                                     ORDER BY e.received_at DESC LIMIT 3"""), {"i": ident})).all()
        out.extend({"raw": raw_item(r), "note": note} for r in rows)
    return out
