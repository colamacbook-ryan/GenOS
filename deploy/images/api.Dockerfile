# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Một image cho api, worker và bước migrate. Build từ gốc repo: docker build -f deploy/images/api.Dockerfile .
# ghim digest (F-19) — Renovate tự nâng
FROM python:3.11-slim@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/apps/api

# Antigravity CLI chính hãng của Google (quyết định Q6): tải đúng bản phát hành, kiểm SHA-256 ghim sẵn, không đóng gói lại.
# Nguồn: https://github.com/google-antigravity/antigravity-cli/releases (tệp agy_cli_linux_<arch>.tar.gz chứa `antigravity`).
ARG AGY_VERSION=1.2.9
ARG AGY_SHA256_AMD64=d9850373f3df866011024a961fa9740cc4adaac060eebe9c70fbf263ac6b2624
ARG AGY_SHA256_ARM64=8a63cf4c4f559e2ff91bd46fbdf015ca7937415805d0cff82015b9cb9dbbdfcd
# v0.1.31 — Claude Code CLI chính hãng của Anthropic (tuỳ chọn, TẮT tới khi Owner đăng nhập gói Claude; QD-12): bản dựng
# native trong gói npm theo nền tảng @anthropic-ai/claude-code-linux-<arch> (không cần Node), ghim phiên bản + SHA-256
# của tệp .tgz (đã đối chiếu với dist.integrity sha512 của npm). Tắt tự cập nhật (DISABLE_AUTOUPDATER).
ARG CLAUDE_CODE_VERSION=2.1.285
ARG CLAUDE_SHA256_AMD64=3fea1abf2d5f42236ebf7e59126698e347ac80437a145dd6e85b87c8c3341ffe
ARG CLAUDE_SHA256_ARM64=f8a0dc539db3c860bdd12a345a51db798908b089a1696f9720c57776dace4cbf
ARG TARGETARCH
# Làm mới gói apt mỗi tuần (sửa sau review v0.1.48): ảnh nền ghim digest nên khoá cache tầng apt không đổi — release.yml
# dùng cache gha mode=max sẽ dùng lại gói apt cũ mãi, lỡ bản vá bảo mật Debian/PGDG. release.yml truyền tuần ISO
# (vd 2026-W41) ⇒ tầng này dựng lại ít nhất mỗi tuần; build tay/CI để trống (không ảnh hưởng).
ARG APT_REFRESH=
RUN set -eux; \
    echo "apt refresh: ${APT_REFRESH:-thủ công}"; \
    apt-get update; apt-get install -y --no-install-recommends ca-certificates curl; \
    case "${TARGETARCH:-amd64}" in \
      amd64) arch=x64; sum="$AGY_SHA256_AMD64"; csum="$CLAUDE_SHA256_AMD64" ;; \
      arm64) arch=arm64; sum="$AGY_SHA256_ARM64"; csum="$CLAUDE_SHA256_ARM64" ;; \
      *) echo "Kiến trúc chưa hỗ trợ: $TARGETARCH"; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/agy.tgz "https://github.com/google-antigravity/antigravity-cli/releases/download/${AGY_VERSION}/agy_cli_linux_${arch}.tar.gz"; \
    echo "$sum  /tmp/agy.tgz" | sha256sum -c -; \
    tar -xzf /tmp/agy.tgz -C /usr/local/bin antigravity; \
    chmod 0755 /usr/local/bin/antigravity; ln -s /usr/local/bin/antigravity /usr/local/bin/agy; \
    rm -f /tmp/agy.tgz; \
    curl -fsSL -o /tmp/claude.tgz "https://registry.npmjs.org/@anthropic-ai/claude-code-linux-${arch}/-/claude-code-linux-${arch}-${CLAUDE_CODE_VERSION}.tgz"; \
    echo "$csum  /tmp/claude.tgz" | sha256sum -c -; \
    tar -xzf /tmp/claude.tgz -C /tmp package/claude; \
    install -m 0755 /tmp/package/claude /usr/local/bin/claude; \
    rm -rf /tmp/claude.tgz /tmp/package; \
    DISABLE_AUTOUPDATER=1 HOME=/tmp claude --version; \
    # pg_dump/pg_restore cho gh.backup và gh.bundle (genh backup/update/export/import) — thiếu nó
    # mọi luồng sao lưu chết với FileNotFoundError: 'pg_dump' (phát hiện ở e2e cài thật). Phải
    # CÙNG major với server (db.Dockerfile: Postgres 16): lấy từ kho chính thức apt.postgresql.org
    # vì bản trong kho Debian của ảnh python có thể cũ hơn server (pg_dump từ chối dump server mới hơn).
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    . /etc/os-release; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; apt-get install -y --no-install-recommends postgresql-client-16; \
    pg_dump --version; \
    apt-get purge -y curl; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*
# F-36 (v0.1.48): cài từ uv.lock (uv sync --frozen) vào /opt/venv; uv chỉ gắn tạm lúc build, KHÔNG nằm lại trong ảnh.
# PATH đặt bằng ENV: alembic, arq, gh-api, `python` đều ra /opt/venv/bin.
# UV_COMPILE_BYTECODE=1: uv KHÔNG tự sinh .pyc (pip thì có). /opt/venv thuộc root, tiến trình chạy USER gh và
# PYTHONDONTWRITEBYTECODE=1 ⇒ thiếu .pyc thì MỌI tiến trình python (api, worker, migrate, `python -m gh.backup`…) dịch lại
# cả cây thư viện mỗi lần khởi động (~+1,3 giây CPU, chậm hơn trên máy arm64 yếu). CI (job images) kiểm không module nào
# dưới /opt/venv phải dịch lại lúc chạy (.github/scripts/check_bytecode.py).
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3.11 \
    UV_COMPILE_BYTECODE=1 PATH=/opt/venv/bin:$PATH
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21,source=/uv,target=/usr/local/bin/uv --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY apps/api/gh ./gh
# Gói R (rà soát v0.1.62): uv đánh khoá cache wheel của gói cục bộ theo pyproject.toml (không theo mã), nên khi VERSION/
# pyproject không đổi mà mã `gh` đổi, cache mount dùng lại wheel `gen-harness-api` CŨ ⇒ ảnh chạy mã cũ (đã gặp trên dev:
# site-packages lệch 14 tệp). `--reinstall-package` (kèm làm mới cache của gói đó) buộc dựng lại wheel từ mã vừa COPY.
RUN --mount=from=ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21,source=/uv,target=/usr/local/bin/uv --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --reinstall-package gen-harness-api
COPY apps/api/alembic.ini ./
COPY apps/api/migrations ./migrations
COPY db/sql /app/db/sql
COPY plugins /app/plugins
# v0.1.36 (F-46): phiên bản của ảnh — release.yml/ci.yml truyền `--build-arg VERSION=<VERSION>`; đặt SAU các bước cài
# nặng để đổi phiên bản không phá cache. gh.__version__ đọc GH_VERSION (thiếu ⇒ "dev").
ARG VERSION=dev
ENV GH_VERSION=${VERSION}
LABEL org.opencontainers.image.version=${VERSION}
# Tạo sẵn /var/lib/gh/objects (volume gh_objects của deploy/compose.yaml gắn
# vào đây cho cả api và worker, GH_OBJECTS_DIR) trước khi chown -R: Docker
# sao chép nội dung + QUYỀN của thư mục này từ image sang volume ở lần mount
# đầu tiên, nên phải có sẵn ở đây với đúng chủ sở hữu gh:gh — nếu không,
# volume sẽ được tạo với quyền root, tiến trình chạy dưới USER gh (dòng dưới)
# sẽ không ghi được tài liệu/backup.
# v0.1.45: nhóm gh CỐ ĐỊNH gid 10001 (trước đây useradd --system tự chọn gid hệ thống) — hộp thư run/ trên máy chủ
# để 2770 nhóm 10001 (genh: ops.EnsureRunDirPerms), nên api/worker/migrate (chung image) phải chạy `id` = 10001:10001.
# Tệp cũ trong volume vẫn thuộc uid 10001 nên không ảnh hưởng.
RUN groupadd --system --gid 10001 gh \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/gh --create-home gh \
    && mkdir -p /var/lib/gh/agy/.gemini/antigravity-cli /var/lib/gh/claude/.claude /var/lib/gh/objects \
    && chown -R gh:gh /var/lib/gh
# F-22 (v0.1.38): phiên Claude Code nằm ở volume RIÊNG claude_state (/var/lib/gh/claude), KHÔNG còn trong agy_state —
# HOME của Antigravity CLI là /var/lib/gh/agy và agy có công cụ đọc tệp, nên để `.credentials.json` của Claude dưới
# HOME đó là cho agy đọc được phiên Claude. /var/lib/gh/claude/.claude tạo sẵn (chown gh:gh ở trên) để Docker chép
# đúng quyền vào volume mới ở lần mount đầu. Review v0.1.63 (P1-1): bỏ bước api tự chuyển tệp từ đường dẫn cũ
# (GH_CLAUDE_LEGACY_HOME) — mọi bản cài đã qua v0.1.38; api không còn chép gì từ volume agy_state lúc khởi động.
ENV GH_CLI_HOME=/var/lib/gh/agy/.gemini/antigravity-cli GH_CLI_BINARY=agy AGY_CLI_DISABLE_AUTO_UPDATE=1 \
    GH_CLAUDE_HOME=/var/lib/gh/claude/.claude GH_CLAUDE_BINARY=claude DISABLE_AUTOUPDATER=1
USER gh
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"
CMD ["gh-api"]
