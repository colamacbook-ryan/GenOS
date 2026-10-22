"""Mặt tiền Owner v2 (v0.1.59, G1) — `GET /owner/proposals`: đề xuất của Gen còn hiệu lực của CHÍNH Owner đang gọi.

CHỈ ĐỌC. Mã đề xuất (pid) lấy từ bước `proposal` của tin trả lời Gen trong 24 giờ (cùng truy vấn `service.needs_review`
dùng, `PROPOSAL_WINDOW_H`), rồi XÁC THỰC cả danh sách bằng MỘT lượt Redis (`gh.gen.proposals.load_many`: pipeline GET +
TTL): tin nhắn đã lưu có thể vẫn ghi "pending" khi đề xuất đã hết hạn / đã xác nhận / đã huỷ ở Redis, nên chỉ giữ
đề xuất còn trong Redis, status `pending`, thuộc đúng Owner này. Xác nhận / huỷ làm ở luồng sẵn có
`/gen/proposals/{id}/confirm|cancel` (loại kho_*, social_* cần mã PIN — client tự hỏi PIN khi gặp 423).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gh.data.common import iso
from gh.gen import proposals
from gh.hub_link import load_kho_label, relabel
from gh.owner import service as svc

#: Tối đa bấy nhiêu đề xuất trả về (màn Việc / Tình báo chỉ hiện vài dòng đầu).
MAX_ITEMS = 20
#: Số tin trả lời Gen quét tối đa (mỗi tin có thể mang vài đề xuất).
SCAN_MESSAGES = 100
SUMMARY_MAX = 400


def _clip(v: Any, n: int) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


async def list_proposals(db: AsyncSession, redis: Any, org_id: uuid.UUID, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await svc.proposal_messages(db, org_id, user_id, SCAN_MESSAGES)
    kho = await load_kho_label(db, org_id)
    now = datetime.now(UTC)
    seen: set[str] = set()
    cands: list[tuple[str, Any]] = []             # (pid, tin chứa đề xuất) — chưa xác thực
    for r in rows:
        content = r.content if isinstance(r.content, dict) else {}
        for st in content.get("steps") or []:
            p = st.get("proposal") if isinstance(st, dict) and st.get("kind") == "proposal" else None
            if not isinstance(p, dict) or p.get("status") != "pending":
                continue
            pid = str(p.get("id"))
            if pid not in seen:
                seen.add(pid)
                cands.append((pid, r))
    # nguồn sự thật: còn trong Redis, chưa xác nhận/huỷ, của Owner này — MỘT lượt Redis cho cả danh sách (GET + TTL)
    fetched = await proposals.load_many(redis, [pid for pid, _ in cands])
    out: list[dict[str, Any]] = []
    for (pid, r), (live, ttl) in zip(cands, fetched, strict=True):
        if (live is None or live.get("status") != "pending" or live.get("user_id") != str(user_id)
                or live.get("org_id") != str(org_id)):
            continue
        expires = now + timedelta(seconds=ttl) if ttl > 0 else r.created_at + timedelta(hours=svc.PROPOSAL_WINDOW_H)
        ptype = str(live.get("type"))
        out.append({"id": pid, "type": ptype,
                    "type_label": relabel(proposals.TYPE_LABELS.get(ptype, "Một thao tác"), kho),
                    "summary": _clip(live.get("summary"), SUMMARY_MAX),
                    "needs_pin": bool(live.get("requires_pin")),
                    "created_at": iso(r.created_at), "expires_at": iso(expires),
                    "conversation_id": str(live.get("conversation_id") or r.cid)})
    out.sort(key=lambda x: x["expires_at"] or "")     # sắp hết hạn trước
    return out[:MAX_ITEMS]
