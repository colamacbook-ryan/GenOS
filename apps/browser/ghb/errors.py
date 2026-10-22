"""Lỗi dùng chung của worker (tách riêng để runner và permit không import vòng)."""


class JobError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
