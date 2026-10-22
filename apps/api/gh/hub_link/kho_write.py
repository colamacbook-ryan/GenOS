"""Ghi Kho dữ liệu qua Gen-hub (v0.1.50, F-81, QD-18) — CHỈ bảng Phiên và Việc, CHỈ các trường liệt kê dưới đây.

Nguồn sự thật của danh sách trường (bản sao TypeScript: packages/contracts/src/gen.ts). Gen chỉ ĐỀ XUẤT; ghi thật khi
Sếp bấm Xác nhận + nhập mã PIN (gh.gen.proposals → POST /hub/kho/write → gh.hub_link.service.write_kho). Mọi giá trị
được kiểm ở ĐÂY ở cả ba bước (lúc Gen đề xuất, lúc xác nhận, lúc ghi) — một chỗ duy nhất, không tin lời model.

Không cho 'Công cụ', 'Người làm' (cột lựa chọn của Kho có tập giá trị chưa biết trước — ghi vào Nợ, xem HANDOFF).
"""

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

KHO_FIELDS: dict[str, tuple[str, ...]] = {"Phiên": ("Chủ đề", "Ngày", "Đã chốt", "Đang bàn", "Việc tiếp", "Cảnh báo"), "Việc": ("Tiêu đề", "Trạng thái", "Ưu tiên", "Hạn", "Link Issue/PR", "Ngày bắt đầu", "Ngày xong")}  # noqa: E501
REQUIRED: dict[str, str] = {"Phiên": "Chủ đề", "Việc": "Tiêu đề"}
PREFIX: dict[str, str] = {"PHIEN": "Phiên", "VIEC": "Việc"}
MA_RE = r"^(PHIEN|VIEC)-\d{1,6}$"
_MA = re.compile(MA_RE)

WRITE_TOOLS = ("kho_create", "kho_update")
DATE_FIELDS = frozenset({"Ngày", "Hạn", "Ngày bắt đầu", "Ngày xong"})
STATUSES = ("Chờ", "Đang làm", "Chờ duyệt", "Xong")
PRIORITIES = ("P1", "P2", "P3")
LINK_FIELD = "Link Issue/PR"
#: Trường không cho ghi dù Kho có cột (tập giá trị lựa chọn chưa biết).
UNSUPPORTED = ("Công cụ", "Người làm")
TITLE_MAX = 200
TEXT_MAX = 2000
WARNING_MAX = 1000
WARNING_FIELD = "Cảnh báo"
MAX_KEYS = 8
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def vn_today(now: datetime | None = None) -> date:
    """Ngày hiện tại theo giờ VN (`now` có múi giờ; naive coi là giờ VN)."""
    n = now or datetime.now(VN_TZ)
    return (n.astimezone(VN_TZ) if n.tzinfo else n.replace(tzinfo=VN_TZ)).date()


def bang_of_ma(ma: str) -> str | None:
    """'VIEC-12' → 'Việc'; mã lạ → None."""
    m = _MA.fullmatch(str(ma or ""))
    return PREFIX[str(ma).split("-", 1)[0]] if m else None


def is_ma(ma: Any) -> bool:
    return isinstance(ma, str) and _MA.fullmatch(ma) is not None


def _max_len(bang: str, key: str) -> int:
    if key == REQUIRED[bang]:
        return TITLE_MAX
    return WARNING_MAX if key == WARNING_FIELD else TEXT_MAX


def _check_value(bang: str, key: str, raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"Trường '{key}' phải là chuỗi")
    value = _CTRL.sub("", raw).strip()
    if key == REQUIRED[bang]:
        value = " ".join(value.split())  # tiêu đề / chủ đề: một dòng
    if not value:
        raise ValueError(f"Trường '{key}' không được để trống")
    if len(value) > _max_len(bang, key):
        raise ValueError(f"Trường '{key}' tối đa {_max_len(bang, key)} ký tự")
    if key in DATE_FIELDS:
        try:
            if not _DATE_RE.fullmatch(value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError as e:
            raise ValueError(f"Trường '{key}' phải là ngày dạng YYYY-MM-DD hợp lệ") from e
    elif key == "Trạng thái" and value not in STATUSES:
        raise ValueError(f"Trạng thái phải là một trong: {', '.join(STATUSES)}")
    elif key == "Ưu tiên" and value not in PRIORITIES:
        raise ValueError(f"Ưu tiên phải là một trong: {', '.join(PRIORITIES)}")
    elif key == LINK_FIELD:
        u = urlparse(value)
        if u.scheme != "https" or not u.hostname or any(c.isspace() for c in value):
            raise ValueError(f"Trường '{key}' phải là đường dẫn https://")
    return value


def validate_record(bang: str, record: Any, *, create: bool) -> dict[str, str]:
    """Kiểm + chuẩn hoá `record` của bảng `bang`. Lỗi → ValueError (thông điệp tiếng Việt, hiện được cho Sếp).
    Trả bản sạch theo thứ tự trường của KHO_FIELDS. `create=True` cần trường bắt buộc; `create=False` cần ≥ 1 trường."""
    if bang not in KHO_FIELDS:
        raise ValueError("Chỉ ghi được vào bảng Phiên hoặc Việc")
    if not isinstance(record, dict):
        raise ValueError("Dữ liệu ghi Kho phải là một đối tượng các trường")
    if len(record) > MAX_KEYS:
        raise ValueError(f"Tối đa {MAX_KEYS} trường mỗi lần ghi")
    allowed = KHO_FIELDS[bang]
    for k in record:
        if k in UNSUPPORTED:
            raise ValueError(f"Chưa hỗ trợ ghi trường '{k}' (giá trị lựa chọn của Kho chưa được xác nhận)")
        if k not in allowed:
            raise ValueError(f"Trường '{k}' không được phép ghi vào bảng {bang} (chỉ: {', '.join(allowed)})")
    clean = {k: _check_value(bang, k, record[k]) for k in allowed if k in record}
    if create and REQUIRED[bang] not in clean:
        raise ValueError(f"Thiếu trường bắt buộc '{REQUIRED[bang]}'")
    if not create and not clean:
        raise ValueError("Cần ít nhất một trường để cập nhật")
    return clean


def fill_defaults(bang: str, record: dict[str, str], now: datetime | None = None) -> dict[str, str]:
    """Khi TẠO bản ghi Phiên mà chưa có 'Ngày' → hôm nay (giờ VN). Việc không có mặc định."""
    if bang == "Phiên" and "Ngày" not in record:
        full = {**record, "Ngày": vn_today(now).isoformat()}
        return {k: full[k] for k in KHO_FIELDS[bang] if k in full}  # giữ thứ tự trường của KHO_FIELDS
    return record


def tool_args(tool: str, key: str, record: dict[str, str]) -> dict[str, Any]:
    """Tham số gửi Kho: kho_create → {'bang', 'fields'}; kho_update → {'id', 'fields'} (`key` là bảng hoặc mã)."""
    if tool == "kho_create":
        return {"bang": key, "fields": record}
    if tool == "kho_update":
        return {"id": key, "fields": record}
    raise ValueError("Tool ghi Kho không hợp lệ")


def parse_args(tool: str, args: Any) -> tuple[str, str, dict[str, str]]:
    """Ngược của `tool_args`, kiểm lại từ đầu: trả (bảng, mã|'' khi tạo, record sạch). Khoá thừa/thiếu → ValueError."""
    if not isinstance(args, dict):
        raise ValueError("Tham số ghi Kho không hợp lệ")
    if tool == "kho_create":
        if set(args) != {"bang", "fields"}:
            raise ValueError("Tham số kho_create phải gồm đúng 'bang' và 'fields'")
        bang = args["bang"]
        if not isinstance(bang, str) or bang not in KHO_FIELDS:
            raise ValueError("Chỉ ghi được vào bảng Phiên hoặc Việc")
        return bang, "", validate_record(bang, args["fields"], create=True)
    if tool == "kho_update":
        if set(args) != {"id", "fields"}:
            raise ValueError("Tham số kho_update phải gồm đúng 'id' và 'fields'")
        ma = args["id"]
        bang_ = bang_of_ma(ma) if isinstance(ma, str) else None
        if bang_ is None:
            raise ValueError("Mã bản ghi phải dạng PHIEN-12 hoặc VIEC-12")
        return bang_, str(ma), validate_record(bang_, args["fields"], create=False)
    raise ValueError("Tool ghi Kho không hợp lệ")
