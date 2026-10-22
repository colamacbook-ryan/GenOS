# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Gen-Harness web console. Build from the REPO ROOT (the app builds from the
# shared packages/* workspaces):
#   docker build -f apps/web/Dockerfile -t gen-harness/web .
# Serves the static bundle on :8080 (nginx-unprivileged, non-root) with SPA fallback.

# ── build ──────────────────────────────────────────────────────────────────
# --platform=$BUILDPLATFORM: bundle tĩnh (HTML/JS/CSS) giống hệt nhau trên mọi
# kiến trúc, nên build một lần trên kiến trúc máy build (amd64) thay vì chạy
# npm/vite dưới QEMU arm64 — bước đó từng treo job Release (v0.1.1, v0.1.6).
# Chỉ tầng nginx phía dưới là theo TARGETPLATFORM (chỉ COPY, không RUN).
# ghim digest (F-19) — Renovate tự nâng
FROM --platform=$BUILDPLATFORM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /repo
ENV CI=1 NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false
# manifests first for layer caching
COPY package.json package-lock.json* ./
COPY apps/web/package.json apps/web/
COPY packages/tokens/package.json packages/tokens/
COPY packages/ui/package.json packages/ui/
COPY packages/contracts/package.json packages/contracts/
RUN --mount=type=cache,target=/root/.npm if [ -f package-lock.json ]; then \
      npm ci --workspace @gen-harness/web --include-workspace-root=false --ignore-scripts --fetch-retries=5 --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000; \
    else \
      npm install --workspace @gen-harness/web --ignore-scripts --fetch-retries=5 --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000; \
    fi
COPY packages/ packages/
COPY apps/web/ apps/web/
# vite.config gói kèm mock (test/mock-*.ts), mock đọc dữ liệu mẫu của thiết kế.
COPY docs/design/seed-data.json docs/design/seed-data.json
RUN npm run build --workspace @gen-harness/web

# ── serve ──────────────────────────────────────────────────────────────────
# ghim digest (F-19) — Renovate tự nâng
FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0 AS runtime
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html
EXPOSE 8080
USER 101
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
