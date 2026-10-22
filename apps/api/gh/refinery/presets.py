"""Bộ quy tắc khởi đầu R-01…R-06 (thiết kế `refineryRules`) — điều kiện chạy được, nhãn giữ đúng chữ thiết kế.

Owner chọn bộ này ở bước 7 của trình thiết lập và sửa được sau đó (mỗi lần sửa là một phiên bản mới).
Danh sách đối thủ của R-04 để trống: Owner điền tên đối thủ thật; hệ thống không tự đoán.
"""

from typing import Any

PRESETS: list[dict[str, Any]] = [
    {
        "code": "R-01", "name": "Nhận diện nhu cầu mua", "kind": "intent", "threshold": 0.70, "enabled": True,
        "conditions": [
            {"type": "has_entity", "entity": "qty", "label": "có từ khoá số lượng + đơn vị"},
            {"type": "has_entity", "entity": "price", "label": "có mức giá hoặc ngân sách"},
            {"type": "is_question", "label": "câu hỏi trực tiếp"},
        ],
        "outputs": [
            {"set": "intent", "value": "AskedPrice", "label": "ý định: hỏi giá"},
            {"set": "side", "value": "demand", "label": "phía mua (cầu)"},
            {"add": "heat", "value": 30, "label": "độ nóng +30"},
        ],
        "prompt_hint": "Người nói cần mua / hỏi giá một mặt hàng cụ thể.",
    },
    {
        "code": "R-02", "name": "Nhận diện nguồn cung", "kind": "intent", "threshold": 0.70, "enabled": True,
        "conditions": [
            {"type": "keyword_any",
             "values": ["còn tồn", "có sẵn", "kho", "sẵn hàng", "cần bán", "thanh lý", "xả hàng"],
             "label": "có từ \"còn tồn\", \"có sẵn\", \"kho\""},
            {"type": "has_entity", "entity": "product", "label": "nêu mặt hàng cụ thể"},
        ],
        "outputs": [
            {"set": "intent", "value": "OfferedSupply", "label": "ý định: chào bán"},
            {"set": "side", "value": "supply", "label": "phía bán (cung)"},
            {"add": "potential", "value": 20, "label": "vào danh sách cần bán"},
        ],
        "prompt_hint": "Người nói đang chào bán / có sẵn hàng.",
    },
    {
        "code": "R-03", "name": "Tín hiệu bất mãn", "kind": "risk", "threshold": 0.75, "enabled": True,
        "conditions": [
            {"type": "repeat_unanswered", "n": 2, "label": "nhắc lại ≥ 2 lần chưa được trả lời"},
            {"type": "keyword_any", "values": ["không ai trả lời", "chậm", "trễ", "thất vọng", "bực", "tệ", "kém",
                                               "không làm được", "nói thẳng", "phàn nàn", "không hài lòng", "chán"],
             "label": "giọng điệu tiêu cực"},
            {"type": "keyword_any", "values": ["chỗ khác", "bên khác", "nhà cung cấp khác", "đơn vị khác",
                                               "đổi nhà", "chuyển sang", "tìm chỗ khác"],
             "label": "nêu phương án thay thế"},
        ],
        "outputs": [
            {"set": "intent", "value": "Complained", "label": "ý định: than phiền"},
            {"add": "churn_risk", "value": 40, "label": "nguy cơ mất khách +40"},
            {"alert": "P1", "label": "báo ngay cho Sếp (ưu tiên cao)"},
        ],
        "prompt_hint": "Khách phàn nàn, bực bội hoặc doạ chuyển sang nhà cung cấp khác.",
    },
    {
        "code": "R-04", "name": "Đối thủ xuất hiện", "kind": "competition", "threshold": 0.65, "enabled": True,
        "conditions": [
            {"type": "llm", "hint": "Tin nhắc tên một đối thủ cạnh tranh của tổ chức",
             "label": "có tên trong danh sách đối thủ"},
            {"type": "regex", "pattern": r"(rẻ|thấp|cao|tốt) hơn|\d+\s?%|so với|chào giá|điều khoản|bảo hành",
             "label": "kèm so sánh giá hoặc điều khoản"},
        ],
        "outputs": [
            {"set": "intent", "value": "MentionsCompetitor", "label": "sự kiện: nhắc tới đối thủ"},
            {"add": "churn_risk", "value": 15, "label": "nguy cơ mất khách +15"},
        ],
        "prompt_hint": "Có nhắc tới đối thủ và so sánh giá / điều khoản.",
    },
    {
        "code": "R-05", "name": "Tín hiệu tìm việc", "kind": "hr", "threshold": 0.60, "enabled": True,
        "conditions": [
            {"type": "regex", "pattern": r"\d+\s*năm(\s+kinh nghiệm)?|kinh nghiệm|từng làm|chuyên (về|ngành)",
             "label": "nêu kinh nghiệm + ngành"},
            {"type": "keyword_any", "values": ["tìm việc", "cơ hội mới", "đổi hướng", "chuyển ngành", "ứng tuyển",
                                               "open to work", "tìm hướng", "nhận việc", "cần việc"],
             "label": "có ý đổi hướng hoặc tìm cơ hội"},
        ],
        "outputs": [
            {"set": "person_type", "value": "candidate", "label": "loại = Ứng viên"},
            {"add": "fit", "value": 20, "label": "độ phù hợp theo vị trí trống"},
        ],
        "prompt_hint": "Người nói giới thiệu kinh nghiệm và đang tìm cơ hội việc làm (chỉ ghi nhận tín hiệu).",
    },
    {
        "code": "R-06", "name": "Loại nhiễu", "kind": "hygiene", "threshold": 0.40, "enabled": True,
        "conditions": [
            {"type": "max_words", "n": 3, "no_entity": True, "label": "dưới 4 từ và không có thực thể"},
            {"type": "kind_in", "values": ["sticker", "image", "reaction", "system"],
             "label": "sticker, ảnh không chú thích"},
            {"type": "regex",
             "pattern": r"^\s*(chào|xin chào|hi|hello|ok|oke|okie|okay|dạ|vâng|ừ|uh|cảm ơn|cám ơn|thanks|tks|"
                        r"thank you|haha|hihi|hehe|👍|🙏|❤️)(\s+(cả nhà|mọi người|anh|chị|em|các bạn|ạ|nhé|nha))*"
                        r"[\s!.,]*$",
             "label": "chào hỏi thuần"},
        ],
        "outputs": [
            {"set": "label", "value": "Noise", "label": "đánh dấu là tin nhiễu"},
            {"discard": True, "label": "không ghi vào kho sạch"},
        ],
        "prompt_hint": None,
    },
]

DEFAULT_WEIGHTS = (("heat", 30), ("potential", 25), ("churn_risk", 20), ("fit", 12), ("engagement", 8),
                   ("data_confidence", 5))
