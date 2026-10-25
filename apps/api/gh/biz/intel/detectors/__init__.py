# ruff: noqa: E501
"""Bộ phát hiện theo loại (T4–T7 thêm module con: `promises.py`, `generic.py`, `fraud.py`…). `intel/jobs.py` TỰ GOM `HOOKS`/`JOBS` của
mọi module con ở đây — gói khác chỉ thêm tệp, không sửa `jobs.py`. Detector KHÔNG gán mức: chỉ dựng `levels.SignalIn` rồi gọi
`signals.upsert`."""
