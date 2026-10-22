"""Bộ 12 câu mẫu cố định để Sếp tự thử Jev (v0.1.55, G4) — nút "Thử 12 câu mẫu" ở thẻ Jev.

6 câu **ý định** (`decider.INTENTS`: data / guide / report / out_of_scope — đúng việc J3 làm với câu hỏi trong chat Gen)
và 6 câu **lọc tin** (`triage.JEV_OPTIONS`: spam / low / medium / high — đúng việc J1/J2 làm với tin khách). Mỗi câu có
nhãn kỳ vọng; chữ mẫu TỰ VIẾT, không chứa dữ liệu thật (không tên người, SĐT, email, số tài khoản).

`run` chạy TUẦN TỰ từng câu qua `JevDecider` (trần 1,5 s mỗi câu, độ tin ≥ 0,5 — cùng luật như khi Gen dùng thật),
nên mọi câu cũng đi qua lớp che dữ liệu của `JevClient`. Lỗi/chậm/độ tin thấp ⇒ câu đó tính SAI kèm `error_text`
(chuỗi), không làm hỏng cả lượt thử.
"""

import time
from dataclasses import dataclass
from typing import Literal

from gh.chassis.masking import mask_error
from gh.gen import decider as decmod

Kind = Literal["intent", "filter"]
TIMEOUT_S = decmod.TIMEOUT_S


@dataclass(frozen=True)
class BenchItem:
    kind: Kind
    question: str
    expected: str       # khoá nhãn: INTENTS (kind=intent) hoặc JEV_OPTIONS (kind=filter)


BENCH_ITEMS: tuple[BenchItem, ...] = (
    # ── 6 câu ý định (Gen) ──
    BenchItem("intent", "Tuần này khách nào hỏi giá nhiều nhất vậy em?", "data"),
    BenchItem("intent", "Hôm nay có việc nào quá hạn chưa xử lý không?", "data"),
    BenchItem("intent", "Chỉ anh cách thêm một nguồn model mới ở màn nào với?", "guide"),
    BenchItem("intent", "Làm sao để bật sao lưu tự động hằng đêm?", "guide"),
    BenchItem("intent", "Cho anh báo cáo tóm tắt doanh số tháng này, có số liệu cụ thể nhé.", "report"),
    BenchItem("intent", "Viết giúp anh đoạn mã Python đọc một tệp CSV rồi cộng cột cuối.", "out_of_scope"),
    # ── 6 câu lọc tin (Hộp thư) ──
    BenchItem("filter", "KHUYẾN MÃI SỐC!!! Click ngay để nhận quà miễn phí, đăng ký ngay hôm nay", "spam"),
    BenchItem("filter", "Vay tiền nhanh giải ngân trong 5 phút, không cần thế chấp, inbox ngay", "spam"),
    BenchItem("filter", "ok em nhé", "low"),
    BenchItem("filter", "Anh gửi em danh sách hàng tồn kho tháng này để em xem trước nhé", "medium"),
    BenchItem("filter", "Bên em cần mua 3 container ván MDF giao Bình Dương trong tháng 10, báo giá giúp em", "high"),
    BenchItem("filter", "Khách phàn nàn đơn giao trễ 5 ngày, đòi hoàn tiền, cần xử lý gấp trong hôm nay", "high"),
)


async def run(decider: decmod.JevDecider, *, filter_options: dict[str, str], filter_context: str,
              items: tuple[BenchItem, ...] = BENCH_ITEMS) -> dict[str, object]:
    """Chạy tuần tự `items` qua `decider`; trả `{total, correct, avg_latency_ms, items: [...]}`.

    `avg_latency_ms` = trung bình các câu Jev TRẢ LỜI được (câu lỗi/chậm không có độ trễ đáng tin); None nếu không câu
    nào trả lời. Mỗi phần tử: `{question, expected, got, ok, latency_ms, error_text?}` — `got`/`expected` là NHÃN
    tiếng Việt cho người đọc (không phải khoá nội bộ)."""
    rows: list[dict[str, object]] = []
    answered: list[int] = []
    correct = 0
    for it in items:
        started = time.monotonic()
        decider.last_error = None
        if it.kind == "intent":
            d = await decider.intent(it.question)
            labels = decmod.INTENTS
        else:
            d = await decider.classify(it.question, filter_options, filter_context)
            labels = filter_options
        wall = int((time.monotonic() - started) * 1000)
        ok = d is not None and d.value == it.expected
        correct += int(ok)
        row: dict[str, object] = {"question": it.question, "expected": labels.get(it.expected, it.expected),
                                  "got": labels.get(d.value, d.value) if d is not None else None, "ok": ok,
                                  "latency_ms": d.latency_ms if d is not None else wall}
        if d is None:
            row["error_text"] = mask_error(decider.last_error or "Jev không trả lời", limit=200)
        else:
            answered.append(d.latency_ms)
        rows.append(row)
    return {"total": len(items), "correct": correct,
            "avg_latency_ms": round(sum(answered) / len(answered)) if answered else None, "items": rows}
