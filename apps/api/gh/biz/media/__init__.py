# ruff: noqa: E501
"""Cụm `media` (L5) — voice/ảnh vào trí nhớ: tải qua bridge → lưu `objects` + `raw.attachments` → STT/OCR/mô tả →
`clean.media_texts` (L3 `verify` đọc từ đây) → luật (a) `prefilter.scan_text` trên chữ lấy ra.

Mô-đun: `fetch` (tải an toàn + giới hạn + MIME), `transcribe` (voice), `ocr` (ảnh), `providers` (engine: bộ định tuyến /
faster-whisper tại máy / giả cho test), `prefilter_port` (điểm cắm sang L2), `status` (trạng thái đọc được),
`jobs` (việc nền), `routes` (API trạng thái + độ phủ), `meter` (đo S5).
"""
