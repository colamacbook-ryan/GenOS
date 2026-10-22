"""Policy engine: thang tự trị 0–6, luật cứng, permit dùng một lần (ARCHITECTURE §7).

Cờ rủi ro (ghi ra ngoài, liên quan nhân sự, có số tiền) gắn ở registry loại hành động do chassis quản,
không do model tự khai — model không thể tự hạ cờ hay tự cấp quyền.
"""

import base64
import hashlib
import time
import uuid
from dataclasses import dataclass, field

from gh.crypto import hmac_sign, hmac_verify

LEVELS = {
    0: "Chỉ ghi nhận",
    1: "Tóm tắt",
    2: "Chấm điểm + giải thích",
    3: "Gợi ý hành động",
    4: "Soạn sẵn chờ duyệt",
    5: "Tự làm việc thấp rủi ro",
    6: "Tự làm việc đã whitelist",
}
DEFAULT_AUTONOMY = 4
DEFAULT_APPROVAL_THRESHOLD_VND = 50_000_000
PERMIT_TTL_S = 300

AUTO, HELD, SUGGEST, BLOCKED = "auto", "held", "suggest", "blocked"


@dataclass(frozen=True)
class ActionType:
    key: str
    label: str
    writes_external: bool = False
    personnel_related: bool = False
    has_amount: bool = False
    low_risk: bool = False          # được tự làm ở mức 5


# Registry loại hành động. Thêm loại mới = thêm một dòng ở đây (hoặc plugin đăng ký qua register_action_type).
_REGISTRY: dict[str, ActionType] = {}


def register_action_type(t: ActionType) -> None:
    if t.key in _REGISTRY and _REGISTRY[t.key] != t:
        raise ValueError(f"Loại hành động {t.key} đã đăng ký với cờ khác")
    _REGISTRY[t.key] = t


for _t in (
    ActionType("message.send", "Gửi tin nhắn ra kênh", writes_external=True),
    ActionType("quotation.send", "Gửi báo giá", writes_external=True, has_amount=True),
    ActionType("contract.send", "Gửi hợp đồng / biên bản", writes_external=True, has_amount=True),
    ActionType("crm.write", "Ghi CRM", writes_external=True),
    ActionType("erp.draft_order", "Tạo đơn nháp ERP", writes_external=True, has_amount=True),
    ActionType("mcp.write", "Gọi tool MCP có ghi", writes_external=True),
    ActionType("people.review_update", "Cập nhật đánh giá nhân sự", personnel_related=True),
    ActionType("people.alert", "Cảnh báo nhân sự", personnel_related=True),
    ActionType("task.create", "Tạo việc", low_risk=True),
    ActionType("reminder.create", "Tạo nhắc", low_risk=True),
    ActionType("note.write", "Ghi chú / sổ tay", low_risk=True),
    ActionType("label.apply", "Gắn nhãn", low_risk=True),
    ActionType("owner.assign", "Gán người phụ trách"),
    ActionType("report.create", "Tạo báo cáo nội bộ"),
):
    register_action_type(_t)


def action_type(key: str) -> ActionType:
    try:
        return _REGISTRY[key]
    except KeyError as e:
        raise KeyError(f"Loại hành động chưa đăng ký: {key}") from e


def effective_autonomy(*layers: int | None) -> int:
    """Mức hiệu lực = mức thấp nhất trong các lớp đã đặt (tổ chức → kênh → nhóm → người → loại việc → agent)."""
    values = [v for v in layers if v is not None]
    if not values:
        return DEFAULT_AUTONOMY
    level = min(values)
    if not 0 <= level <= 6:
        raise ValueError("Mức tự trị phải trong 0–6")
    return level


@dataclass
class Decision:
    outcome: str
    level: int
    reasons: list[str] = field(default_factory=list)

    @property
    def hold_reason(self) -> str | None:
        return "; ".join(self.reasons) if self.outcome == HELD else None


def evaluate(action_key: str, level: int, *, amount_vnd: int = 0,
             approval_threshold_vnd: int = DEFAULT_APPROVAL_THRESHOLD_VND,
             whitelist: frozenset[str] = frozenset()) -> Decision:
    """Quyết định cho một hành động do agent đề xuất."""
    t = action_type(action_key)
    if level <= 2:
        return Decision(BLOCKED, level, [f"mức {level} ({LEVELS[level]}) không hành động"])
    if level == 3:
        return Decision(SUGGEST, level, ["mức 3 chỉ gợi ý hành động"])

    reasons: list[str] = []
    if t.writes_external:
        reasons.append("ghi ra ngoài phải chờ duyệt")
    if t.personnel_related:
        reasons.append("liên quan nhân sự phải chờ duyệt")
    if t.has_amount and amount_vnd > approval_threshold_vnd:
        reasons.append(f"vượt ngưỡng {approval_threshold_vnd:,} ₫".replace(",", "."))
    if reasons:  # Luật cứng: áp ở mọi mức tự trị.
        return Decision(HELD, level, reasons)
    if level == 4:
        return Decision(HELD, level, ["mức 4 soạn sẵn chờ duyệt"])
    if level == 5 and t.low_risk:
        return Decision(AUTO, level, ["việc nội bộ thấp rủi ro"])
    if level == 6 and (t.low_risk or t.key in whitelist):
        return Decision(AUTO, level, ["việc nội bộ đã whitelist" if t.key in whitelist else "việc nội bộ thấp rủi ro"])
    return Decision(HELD, level, [f"'{t.label}' chưa nằm trong phạm vi tự làm ở mức {level}"])


# ─── Permit ────────────────────────────────────────────────────────────────────

def body_digest(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def issue_permit(draft_id: uuid.UUID, body: bytes, target: str, now: float | None = None,
                 ttl_s: int = PERMIT_TTL_S) -> tuple[str, bytes, float]:
    """Trả (permit_token, permit_hash để lưu DB, expires_at_epoch)."""
    exp = int((now or time.time()) + ttl_s)
    msg = b"|".join([draft_id.bytes, body_digest(body), target.encode(), str(exp).encode()])
    sig = hmac_sign(msg)
    token = base64.urlsafe_b64encode(b"|".join([str(draft_id).encode(), str(exp).encode(), sig])).decode()
    return token, hashlib.sha256(token.encode()).digest(), float(exp)


def check_permit(token: str, draft_id: uuid.UUID, body: bytes, target: str, now: float | None = None) -> bool:
    """Kiểm chữ ký, đúng bản nháp, đúng nội dung, đúng đích, còn hạn. Việc 'dùng một lần' kiểm ở DB."""
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        did, exp_s, sig = raw.split(b"|", 2)
        if uuid.UUID(did.decode()) != draft_id:
            return False
        exp = int(exp_s)
    except (ValueError, TypeError):
        return False
    if (now or time.time()) > exp:
        return False
    msg = b"|".join([draft_id.bytes, body_digest(body), target.encode(), str(exp).encode()])
    return hmac_verify(msg, sig)
