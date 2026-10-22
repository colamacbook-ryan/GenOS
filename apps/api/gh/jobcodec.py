"""Mã hoá việc/kết quả arq bằng JSON (orjson) thay cho pickle mặc định của arq.

pickle.loads trên dữ liệu đọc từ Redis = ai ghi được vào Redis là chạy được mã trong worker (tiến trình giữ khoá
master). JSON chỉ ra dữ liệu. Tham số việc của hệ thống chỉ là chuỗi/số (`backup_now(trigger="manual")`, cron không
tham số); kết quả không mã hoá được (ngoại lệ, đối tượng lạ) thì ghi dạng chuỗi.

Nâng cấp: việc arq còn nằm trong Redis từ bản cũ (pickle) không giải được → arq ghi lỗi "unable to deserialize job"
và bỏ việc đó (không chạy mã nào). Việc định kỳ tự xếp lại ở lượt sau; "Sao lưu ngay" đang chờ đúng lúc nâng cấp thì
hiện "stalled" rồi Owner bấm lại (gh/system_api/backups.py).
"""

from typing import Any

import orjson


def _fallback(obj: Any) -> Any:
    if isinstance(obj, tuple | set | frozenset):
        return list(obj)
    return str(obj)


def dumps(obj: Any) -> bytes:
    return orjson.dumps(obj, default=_fallback)


def loads(raw: bytes) -> Any:
    return orjson.loads(raw)


__all__ = ["dumps", "loads"]
