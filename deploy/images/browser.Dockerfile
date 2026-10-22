# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# browser-worker + proxy ra ngoài (v0.1.29, docs/design/gen-browser-agent.md §3.1). Build từ gốc repo:
#   docker build -f deploy/images/browser.Dockerfile .
# Ảnh gốc Playwright chính thức (Chromium + Xvfb + phông), GHIM DIGEST của danh sách đa kiến trúc (amd64 + arm64) —
# thư viện `playwright` phải cùng phiên bản (apps/browser/pyproject.toml) để khớp bản Chromium đi kèm.
# Đổi phiên bản: lấy digest mới bằng
#   curl -sI -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' \
#     https://mcr.microsoft.com/v2/playwright/python/manifests/v<phiên bản>-noble | grep -i docker-content-digest
# Không cài plugin "stealth", không đổi user agent/vân tay trình duyệt (luật cứng — xem gh/social/platforms.py).
# ghim digest (F-19) — Renovate tự nâng
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble@sha256:a7f6cf3ae520c9d670ad956572c13747ed5abdbba5123a01526f873ed1662528
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright HOME=/tmp
# F-36 (v0.1.48): cài từ uv.lock vào /opt/venv (không dùng --system-site-packages; playwright==1.56.0 nằm trong venv, trình duyệt
# vẫn ở /ms-playwright của ảnh gốc). uv chỉ gắn tạm lúc build, KHÔNG nằm lại trong ảnh. `python3` trên PATH = python của venv.
# UV_COMPILE_BYTECODE=1: uv không tự sinh .pyc; rootfs chỉ-đọc + USER pwuser ⇒ thiếu .pyc thì mỗi lần khởi động dịch lại
# cả cây thư viện (xem api.Dockerfile; CI kiểm bằng .github/scripts/check_bytecode.py).
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/bin/python3 \
    UV_COMPILE_BYTECODE=1 PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY apps/browser/pyproject.toml apps/browser/uv.lock ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21,source=/uv,target=/usr/local/bin/uv --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY apps/browser/ghb ./ghb
RUN --mount=from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21,source=/uv,target=/usr/local/bin/uv --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable \
 && python3 -c "import ghb, ghb.worker, ghb.egress, playwright, redis, orjson; print('ghb', ghb.__version__)"
# Sandbox Chromium (F-85, v0.1.47): BẬT bằng user namespace + seccomp-bpf của chính Chromium (GH_BROWSER_SANDBOX=auto|on|off,
# xem ghb/sandbox.py) — KHÔNG dùng chrome-sandbox setuid vì trái no-new-privileges. Cần profile seccomp riêng
# (deploy/browser/chromium-seccomp.json, đặt ở compose) cho phép clone/unshare/setns/chroot khi cap_drop ALL. Ảnh gốc không đặt
# biến/cờ nào ép --no-sandbox; Playwright chỉ thêm cờ đó khi chromium_sandbox=False (xem ghb/worker.py).
# Tự kiểm: python3 -m ghb.sandbox --probe (in JSON enabled/reason).
# Không root. rootfs chỉ-đọc + tmpfs /tmp đặt ở compose (hồ sơ trình duyệt chỉ sống trong RAM, xoá khi xong việc).
USER pwuser
# Trình duyệt chạy CÓ giao diện trong màn hình ảo Xvfb (như trình duyệt thường), không phải chế độ headless.
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1280x800x24 -nolisten tcp", "python3", "-m", "ghb.worker"]
