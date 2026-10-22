"""Ba cơ sở (basis) duy nhất cho tin tự gửi, và tác nhân kích hoạt. Chỉ là kiểu dữ liệu — không có đường gửi
nào ở đây."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Basis(StrEnum):
    OWNER_DM = "owner_dm"      # (1) trả lời / báo cáo cho chính Sếp qua kênh quản trị 1-1
    OWNER_TAG = "owner_tag"    # (2) Sếp (theo ID) tag bot trong nhóm ⇒ trả lời tại chỗ
    SUPPORT = "support"        # (3) thành viên hỏi trong nhóm Sếp đã bật Chế độ hỗ trợ


@dataclass(frozen=True)
class Trigger:
    """Cái gì kích hoạt tin tự gửi: tin 1-1 của Sếp (`ref` = `<kênh>:<mã tin>`), tin trong nhóm (`ref` = id
    `raw.events`),
    hay dòng báo cáo `ops.owner_outbox` (`ref` = id dòng)."""

    kind: Literal["owner_message", "raw_event", "report"]
    ref: str
