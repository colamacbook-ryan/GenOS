"""Liên kết Gen-hub — Gen đọc Kho dữ liệu chỉ-đọc (v0.1.26, docs/design/gen-hub-link.md)."""

import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Tên hiển thị MẶC ĐỊNH của Kho dữ liệu nối qua Gen-hub — MỘT chỗ duy nhất ở API (bản sao web: `KHO_LABEL` ở
#: packages/contracts/src/gen.ts). v0.1.56: tên chung, không mang tên riêng của chủ Gen-hub (mọi Owner nhận bản này).
#: Chuỗi tĩnh (lessons.json, tips.json, registry.json) ghi cùng chữ — tests/test_no_personal_info_v0156.py kiểm khớp.
KHO_LABEL = "Kho dữ liệu"

#: v0.1.57 (Nợ #30): Owner tự đặt "Tên Kho" cho tổ chức mình — khoá `kho_label` trong `core.organizations.settings`
#: (jsonb), tối đa 40 ký tự, rỗng/vắng ⇒ dùng KHO_LABEL. Mọi chuỗi chạy thật hiển thị tên Kho phải đi qua `kho_label()`.
KHO_LABEL_KEY = "kho_label"
KHO_LABEL_MAX = 40

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
#: "Kho dữ liệu" đứng riêng (không phải đầu của "Kho dữ liệu thô" — tên một tầng dữ liệu khác của Gen-Harness).
_DEFAULT_NAME = re.compile(re.escape(KHO_LABEL) + r"(?! thô)")


def clean_kho_label(raw: Any) -> str:
    """Chuẩn hoá tên Kho do Owner nhập: bỏ ký tự điều khiển, gộp khoảng trắng, cắt hai đầu. Không phải chuỗi ⇒ ''."""
    if not isinstance(raw, str):
        return ""
    return " ".join(_CTRL.sub(" ", raw).split())


def kho_label(settings: Mapping[str, Any] | None) -> str:
    """Tên Kho HIỆU LỰC của một tổ chức từ `core.organizations.settings`: tên Owner tự đặt (≤ 40 ký tự) nếu có,
    ngược lại KHO_LABEL. Giá trị hỏng (không phải chuỗi, quá dài) ⇒ bỏ qua, dùng mặc định."""
    if not isinstance(settings, Mapping):
        return KHO_LABEL
    name = clean_kho_label(settings.get(KHO_LABEL_KEY))
    return name if name and len(name) <= KHO_LABEL_MAX else KHO_LABEL


async def load_kho_label(db: AsyncSession, org_id: uuid.UUID) -> str:
    """`kho_label(settings)` của tổ chức `org_id` (một truy vấn nhỏ)."""
    raw = (await db.execute(text("SELECT settings FROM core.organizations WHERE id = :o"),
                            {"o": org_id})).scalar_one_or_none()
    return kho_label(raw if isinstance(raw, Mapping) else None)


def relabel(text_: str, label: str) -> str:
    """Thay tên Kho MẶC ĐỊNH trong một chuỗi dựng sẵn từ KHO_LABEL bằng `label` (tên hiệu lực của tổ chức). Giữ nguyên
    "Kho dữ liệu thô" (tầng dữ liệu khác). `label` là mặc định ⇒ trả nguyên chuỗi."""
    if label == KHO_LABEL:
        return text_
    return _DEFAULT_NAME.sub(lambda _m: label, text_)
