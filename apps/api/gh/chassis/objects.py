"""Kho tệp cho Tài liệu (`biz.documents`) — ARCHITECTURE §2 định nghĩa MinIO (S3-compatible).

Chưa có client MinIO/S3 nào được nối dây ở giai đoạn 1/2 (không gói `boto3`/`minio`, không biến cấu hình
endpoint/khoá — chỉ có dịch vụ `objects` trong `deploy/compose.yaml`). Dựng một client MinIO thật (xác thực,
bucket, policy, retry, đa kiến trúc) là việc của một cụm hạ tầng riêng, ngoài phạm vi cụm `relations`.

**Quyết định tự đưa ra**: cắm `ObjectStore` qua một điểm nối duy nhất (`get_object_store()`), tối thiểu đúng
"storage_key + endpoint upload/download" mà nhiệm vụ yêu cầu — vẫn là storage_key trỏ tới một blob, không phải
một khái niệm lưu trữ mới. Cài đặt mặc định (`LocalObjectStore`) ghi xuống đĩa ở một thư mục ngoài repo (thư mục
tạm hệ thống, cấu hình được qua `GH_OBJECTS_DIR`) để chạy được ngay cả khi không có MinIO/Docker (đúng môi
trường test của nhiệm vụ này). Khi một cụm hạ tầng khác nối dây MinIO thật, chỉ cần thêm một lớp
`ObjectStore` mới (vd `MinioObjectStore`) và đổi `get_object_store()` — `gh/biz/relations/routes.py` (endpoint
upload/download) không cần đổi vì chỉ gọi qua giao diện `ObjectStore`.
"""

import hashlib
import re
import tempfile
import uuid
from pathlib import Path
from typing import Protocol

from gh.config import get_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_key(*parts: str) -> str:
    """Ghép `storage_key` an toàn từ nhiều phần: lọc ký tự lạ, không cho thoát khỏi thư mục gốc (`..`)."""
    cleaned = []
    for part in parts:
        p = _SAFE.sub("_", part).strip("._") or "x"
        cleaned.append(p)
    return "/".join(cleaned)


def new_key(org_id: uuid.UUID, filename: str) -> str:
    return safe_key(f"org-{org_id}", uuid.uuid4().hex, filename or "file")


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectNotFound(Exception):
    pass


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class LocalObjectStore:
    """Cài đặt mặc định: đĩa cục bộ dưới `root` (`GH_OBJECTS_DIR`, mặc định một thư mục tạm hệ thống)."""

    def __init__(self, root: str | None = None):
        self.root = Path(root or get_settings().objects_dir or Path(tempfile.gettempdir()) / "gh-objects")

    def _path(self, key: str) -> Path:
        base = self.root.resolve()
        path = (base / key).resolve()
        if base != path and base not in path.parents:
            raise ValueError("storage_key không hợp lệ")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = LocalObjectStore()
    return _store


def reset_object_store() -> None:
    """Chỉ dùng cho test: buộc dựng lại singleton (thư mục có thể đã đổi qua biến môi trường)."""
    global _store
    _store = None
