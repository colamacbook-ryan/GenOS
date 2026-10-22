"""CLI giả cho test hộp riêng agy (v0.1.63 H1): hành vi chọn theo dòng đầu của prompt (stdin).

SLEEP → ngủ 30 giây (để thử hết giờ) · ENV → trả danh sách TÊN biến môi trường của chính tiến trình agy
· RATE → thoát 1 kiểu hết hạn mức · BOOM → thoát 1 lỗi lạ · STRAY → (review lần 2 v0.1.63, P2-1 — CHỈ dùng trong
container hộp ở kiểm thật) để lại tiến trình `setsid nohup sleep 600 &` thoát nhóm + tệp trong /tmp và /dev/shm · khác →
"echo:<prompt>"."""

import json
import os
import sys
import time

if sys.argv[1:2] == ["--version"]:
    print("1.2.9")
    sys.exit(0)
if sys.argv[1:2] == ["models"]:
    print("Available models:")
    print("  gemini-3.8-flash")
    sys.exit(0)
prompt = sys.stdin.read()
head = prompt.split("\n", 1)[0].strip()
if head == "SLEEP":
    time.sleep(30)
if head == "ENV":
    print(json.dumps({"response": json.dumps(sorted(os.environ))}))
    sys.exit(0)
if head == "RATE":
    print(json.dumps({"status": "ERROR", "error": "429 RESOURCE_EXHAUSTED: quota"}))
    sys.exit(1)
if head == "STRAY":
    import subprocess

    subprocess.run(["sh", "-c", "setsid nohup sleep 600 >/dev/null 2>&1 &"], check=True)
    for d in ("/tmp", "/dev/shm"):
        with open(os.path.join(d, "gh-stray-" + d.strip("/").replace("/", "-")), "w") as fh:
            fh.write("lượt trước để lại")
        os.makedirs(os.path.join(d, "gh-stray-dir", "sau"), exist_ok=True)
if head == "BOOM":
    print("boom", file=sys.stderr)
    sys.exit(1)
print(json.dumps({"response": "echo:" + prompt, "usage": {"input_tokens": 3, "output_tokens": 4}}))
