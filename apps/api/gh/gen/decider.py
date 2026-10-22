"""`Decider` — bộ quyết định nhanh cho Gen (docs/design/gen-v1.md §5, quyết định §9.6).

- `JevDecider`: gọi Jev (nguồn model kind `system_one`) chọn ý định câu hỏi và mục tiêu UI kế tiếp từ danh sách hữu
  hạn. Có trần thời gian (~1,5 s); lỗi/chậm/độ tin cậy thấp → trả None = bên gọi đi đường LLM.
- `LlmDecider`: mặc định khi chưa cấu hình Jev — không gọi thêm model nào, để planner LLM (`core.gen`) tự quyết
  (tránh cộng thêm một lượt gọi model vào độ trễ mỗi câu).
- `classify` (v0.1.25, Đợt C1): lọc đầu Hộp thư (rác / chất lượng) — `gh.refinery.triage`; None → quy tắc tất định.

v0.1.55 (G3, J3): `rule_intent(text)` — quy tắc TẤT ĐỊNH dự phòng khi không có Jev (hoặc Jev lỗi / chậm / độ tin thấp):
yêu cầu viết / sửa code hoặc chuyện ngoài lề RÕ RÀNG (động từ + đối tượng) ⇒ `out_of_scope`; câu hỏi số liệu ngắn ⇒
`data`. Không gọi mạng, không gọi model. `is_simple_question(text)` cho biết câu có đủ "đơn giản" để Gen dùng tầng
Nhanh (chế độ Tự động).
"""

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto
from gh.gen import jev
from gh.gen.registry import Target
from gh.textnorm import strip_accents

log = logging.getLogger("gh.gen.decider")

TIMEOUT_S = 1.5
MIN_CONFIDENCE = 0.5
INTENTS: dict[str, str] = {
    "data": "hỏi dữ liệu / tóm tắt tình hình",
    "guide": "hỏi cách làm / cần dẫn đường trên giao diện",
    "report": "xin báo cáo có số liệu",
    "out_of_scope": "ngoài phạm vi quản trị app (code, máy chủ, nói chuyện với khách)",
}


@dataclass
class Decision:
    value: str
    confidence: float | None
    latency_ms: int
    source: str


class Decider(Protocol):
    name: str

    async def intent(self, question: str) -> Decision | None: ...

    async def next_target(self, question: str, candidates: list[Target]) -> Decision | None: ...

    async def classify(self, question: str, options: dict[str, str], context: str) -> Decision | None: ...


class LlmDecider:
    name = "llm"

    async def intent(self, question: str) -> Decision | None:
        return None

    async def next_target(self, question: str, candidates: list[Target]) -> Decision | None:
        return None

    async def classify(self, question: str, options: dict[str, str], context: str) -> Decision | None:
        return None


class JevDecider:
    name = "jev"

    def __init__(self, client: jev.JevClient, timeout: float = TIMEOUT_S):
        self.client = client
        self.timeout = timeout
        self.last_error: str | None = None

    async def _choose(self, question: str, options: dict[str, str], context: str) -> Decision | None:
        labels = list(options.values())
        started = time.monotonic()
        try:
            c = await asyncio.wait_for(self.client.choose(question, labels, context), timeout=self.timeout)
        except (TimeoutError, jev.JevError) as e:
            self.last_error = str(e) or "timeout"
            log.info("Jev không quyết định được (%s) — dùng LLM", self.last_error)
            return None
        if c.confidence is not None and c.confidence < MIN_CONFIDENCE:
            self.last_error = f"độ tin cậy thấp {c.confidence:.2f}"
            return None
        key = next(k for k, v in options.items() if v == c.label)
        return Decision(key, c.confidence, int((time.monotonic() - started) * 1000), self.name)

    async def intent(self, question: str) -> Decision | None:
        return await self._choose(question, INTENTS, "Phân loại câu hỏi của chủ doanh nghiệp trong Console.")

    async def next_target(self, question: str, candidates: list[Target]) -> Decision | None:
        static = [t for t in candidates if t.dynamic is None][:40]
        if not static:
            return None
        return await self._choose(question, {t.id: f"{t.label} — {t.description}" for t in static},
                                  "Chọn phần tử giao diện cần làm sáng để dẫn người dùng.")

    async def classify(self, question: str, options: dict[str, str], context: str) -> Decision | None:
        """Phân loại tự do (Đợt C1 — lọc đầu Hộp thư): khoá → nhãn; None = bên gọi tự rơi về quy tắc/LLM."""
        return await self._choose(question, options, context)


async def load_decider(db: AsyncSession, org_id: uuid.UUID, *,
                       transport: httpx.AsyncBaseTransport | None = None) -> Decider:
    """Nguồn Jev đầu tiên đang bật và có khoá → JevDecider; không có → LlmDecider (chỉ LLM)."""
    from gh.providers.router import KEY_AAD

    row: Any = (await db.execute(text("""
        SELECT p.endpoint, k.secret_enc,
               (SELECT m.model_name FROM agent.models m WHERE m.provider_id = p.id AND m.is_enabled
                ORDER BY m.id LIMIT 1) AS model_name
        FROM agent.providers p JOIN agent.provider_keys k ON k.provider_id = p.id AND k.is_enabled
        WHERE p.org_id = :o AND p.kind = 'system_one' AND p.is_enabled
        ORDER BY p.failover_rank NULLS LAST, k.rotation_order LIMIT 1"""), {"o": org_id})).one_or_none()
    if row is None:
        return LlmDecider()
    try:
        secret = crypto.decrypt(bytes(row.secret_enc), KEY_AAD).decode()
    except Exception:  # noqa: BLE001 — khoá master đổi: coi như chưa có Jev
        log.error("Không giải mã được khoá Jev")
        return LlmDecider()
    return JevDecider(jev.JevClient(row.endpoint, secret, row.model_name, transport=transport))


# ─── v0.1.55 (G3, J3): quy tắc tất định dự phòng ─────────────────────────────

_NON_WORD = re.compile(r"[^a-z0-9+#]+")

# Cụm (sau khi bỏ dấu, chữ thường, bỏ dấu câu) cho thấy câu hỏi ngoài phạm vi quản trị Console. Chỉ giữ YÊU CẦU RÕ
# RÀNG mà ĐỘNG TỪ đi kèm ĐỐI TƯỢNG ("viết code", "sửa code", "viết hàm", "kể chuyện cười", "làm thơ"). TUYỆT ĐỐI không
# đưa vào từ chủ đề / công cụ đứng một mình (python, java, ssh, sudo, systemctl, crontab, regex, bóng đá, tử vi, thời
# tiết, "kể chuyện"…): khách hỏi mua cà phê Java, khoá học Python, bóng đá, "khách kể chuyện gì" là câu quản trị THẬT,
# và chính Console dặn Sếp chạy `sudo loginctl enable-linger` — hỏi lệnh đó là hỏi cách dùng Console. Quy tắc này chạy
# ở MỌI cài đặt (kể cả không có Jev) và khớp là trả câu mẫu, KHÔNG gọi model ⇒ thà để model quyết còn hơn từ chối oan.
_OUT_OF_SCOPE_PHRASES: tuple[str, ...] = (
    # viết / sửa code
    "viet code", "viet giup code", "viet ma nguon", "sua code", "sua loi code", "debug code",
    "lap trinh giup", "lap trinh ho", "viet ham", "viet script", "cau lenh sql", "viet sql",
    # chuyện ngoài lề, không liên quan quản trị doanh nghiệp
    "ke chuyen cuoi", "lam tho", "viet tho",
)

# Câu hỏi số liệu: có cụm đếm … và KHÔNG có từ chỉ việc ghi / phân tích / hướng dẫn.
_COUNT_PHRASES: tuple[str, ...] = ("bao nhieu", "co may", "may khach", "may viec", "may don", "so luong", "tong so")
_NOT_SIMPLE_WORDS: tuple[str, ...] = (
    "so sanh", "phan tich", "vi sao", "tai sao", "de xuat", "soan", "viet", "gui", "nhac", "giao", "tao", "bao cao",
    "tom tat", "huong dan", "lam sao", "lam the nao", "cach", "chi cho", "chi toi", "o dau", "ghi nho", "ghi vao",
    "xoa", "sua", "doi", "dat lai", "duyet giup", "duyet luon", "tra loi", "nho giup",
)
SIMPLE_MAX_WORDS = 14
SIMPLE_MAX_CHARS = 100


def _norm(text: str) -> str:
    return _NON_WORD.sub(" ", strip_accents(text or "").lower()).strip()


def _has_phrase(norm: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {norm} "
    return any(f" {p} " in padded or (not p[-1].isalnum() and f" {p}" in padded) for p in phrases)


def is_simple_question(text: str) -> bool:
    """Câu ngắn (≤ 14 từ, ≤ 100 ký tự), không có từ chỉ việc ghi / phân tích / hướng dẫn — đủ nhẹ cho tầng Nhanh."""
    t = (text or "").strip()
    norm = _norm(t)
    if not norm or len(t) > SIMPLE_MAX_CHARS or len(norm.split()) > SIMPLE_MAX_WORDS:
        return False
    return not _has_phrase(norm, _NOT_SIMPLE_WORDS)


def rule_intent(text: str) -> Decision | None:
    """Ý định bằng quy tắc TẤT ĐỊNH (không mạng, không model) — dự phòng khi không có Jev hoặc Jev lỗi / chậm / độ tin
    thấp. `out_of_scope`: câu có yêu cầu viết / sửa code hoặc chuyện ngoài lề rõ ràng (động từ + đối tượng). `data`: câu
    số liệu NGẮN ("có bao nhiêu khách mới?").
    Không khớp ⇒ None (đi đường LLM như cũ). `Decision.source == "rule"`, độ tin 1.0, độ trễ 0."""
    norm = _norm(text)
    if not norm:
        return None
    if _has_phrase(norm, _OUT_OF_SCOPE_PHRASES):
        return Decision("out_of_scope", 1.0, 0, "rule")
    if _has_phrase(norm, _COUNT_PHRASES) and is_simple_question(text):
        return Decision("data", 1.0, 0, "rule")
    return None
