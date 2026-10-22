# Ảnh tái lập (F-19, F-36) — bảng "đã kiểm" v0.1.48

Đo lúc thi công: 2026-10-03 22:03 UTC (gói `anh-tai-lap`). Digest là của **danh sách đa kiến trúc** (OCI image index / manifest list),
KHÔNG phải manifest một kiến trúc — ảnh arm64 sẽ hỏng nếu dùng nhầm.

Cách đo (registry API, tương đương `docker buildx imagetools inspect <ảnh>`): lấy token
(`https://auth.docker.io/token?service=registry.docker.io&scope=repository:<repo>:pull`; ghcr.io:
`https://ghcr.io/token?scope=repository:astral-sh/uv:pull`; mcr.microsoft.com không cần token), rồi
`curl -sI -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' <registry>/v2/<repo>/manifests/<tag>`
→ header `docker-content-digest`; tải thân manifest để đếm `platform` (linux/amd64 + linux/arm64).

## Ảnh nền

| Ảnh:tag | Digest (index) | MediaType | amd64 + arm64 | Dùng ở |
|---|---|---|---|---|
| caddy:2-alpine | `sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f` | oci.image.index | có | deploy/compose.yaml (proxy), bản nhúng genh |
| redis:7-alpine | `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` | oci.image.index | có | compose (redis, browser-redis), bản nhúng |
| python:3.11-slim | `sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce` | oci.image.index | có | api.Dockerfile |
| node:22-slim | `sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392` | oci.image.index | có | bridge.Dockerfile |
| node:22-alpine | `sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402` | oci.image.index | có | apps/web/Dockerfile (build) |
| nginxinc/nginx-unprivileged:1.27-alpine | `sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0` | oci.image.index | có | apps/web/Dockerfile (runtime) |
| pgvector/pgvector:pg16 | `sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a` | oci.image.index | có (chỉ 2 kiến trúc này) | db.Dockerfile |
| docker/dockerfile:1 | `sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e` | oci.image.index | có | dòng `# syntax=` của cả 5 Dockerfile |
| ghcr.io/astral-sh/uv:0.12.23 | `sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21` | oci.image.index | có | `RUN --mount=from=` ở api và browser |
| mcr.microsoft.com/playwright/python:v1.56.0-noble | `sha256:a7f6cf3ae520c9d670ad956572c13747ed5abdbba5123a01526f873ed1662528` | docker manifest.list.v2 | có | browser.Dockerfile (đã ghim sẵn, đo lại khớp) |

Cả 10 giá trị đo lại trùng giá trị lúc lập kế hoạch (2026-10-03).

**Đo lại 2026-10-09 (sửa sau review, trước khi phát hành)** — tag đã dời sang bản dựng mới, đã cập nhật 3 dòng trên (kiểm
qua `hub.docker.com/v2/repositories/library/<ảnh>/tags/<tag>` và registry `mirror.gcr.io`, đọc `ENV *_VERSION` + `created`
của ảnh amd64; vẫn là index đa kiến trúc có amd64 + arm64):

| Ảnh:tag | Digest cũ (03/10) | Digest mới | Lý do |
|---|---|---|---|
| caddy:2-alpine | `sha256:881bbc60…` (Caddy v2.11.6, dựng 2026-10-02) | `sha256:d8542f48…` | Caddy v2.11.7 (dựng 2026-10-05) — giữ cũ thì Owner đã kéo tag sau 05/10 bị hạ bản khi cập nhật |
| python:3.11-slim | `sha256:bab1b7ef…` (dựng 2026-10-01) | `sha256:0dd364ba…` | cùng 3.11.17, dựng lại 2026-10-06 (gói Debian mới hơn) |
| node:22-slim | `sha256:43ac6c60…` (dựng 2026-09-23) | `sha256:c3de60bf…` | cùng 22.23.3, dựng lại 2026-10-06 (gói Debian mới hơn) |

redis:7-alpine, node:22-alpine, nginx-unprivileged:1.27-alpine, pgvector:pg16 đo lại vẫn trùng. Sau khi merge, Renovate mở PR
nhóm "ảnh Docker" mỗi tuần (không tự merge — xem renovate.json).

Gói apt `postgresql-16-partman` (db.Dockerfile) và các gói apt/`postgresql-client-16` (api.Dockerfile) **chưa ghim phiên bản** — ngoài phạm vi gói này.
Bản tải `agy`/`claude` vẫn ghim bằng SHA-256 như cũ.

## Công cụ và phụ thuộc

| Mục | Phiên bản đã kiểm | Nguồn / lệnh |
|---|---|---|
| uv | 0.12.23 (sinh uv.lock bằng đúng bản này: `pip install uv==0.12.23` trong venv tạm; `uv self update` không nâng được vì bản cài sẵn do hệ thống quản lý) | ghcr.io (digest ở bảng trên) |
| hatchling | 1.32.4 (ghim `requires = ["hatchling==1.32.4"]` ở api và browser) | https://pypi.org/pypi/hatchling/json |
| fastapi | ràng buộc `>=0.121`; lock chọn 0.142.2 (starlette 1.7.0) | https://pypi.org/pypi/fastapi/json |
| renovate | 44.132.5 (bản mới nhất trên npm); khoá `managerFilePatterns` hợp lệ ở bản này (bản cũ dùng `fileMatch`) | https://registry.npmjs.org/renovate/latest |
| playwright (pip) | 1.56.0 trong uv.lock, cùng phiên bản ảnh mcr | apps/browser/uv.lock |

Lưu ý cho pipeline-ci: `renovate@44.132.5` khai báo engines `node >=24.10` (một số phụ thuộc phụ) — chạy trên node 22 chỉ báo
`EBADENGINE` (cảnh báo) nhưng `renovate-config-validator --strict` vẫn chạy được; nên dùng setup-node 24 cho bước này nếu muốn sạch cảnh báo.

## Kết quả kiểm

| Việc | Kết quả |
|---|---|
| `uv lock --check` (apps/api, apps/browser) | xanh (58 và 24 gói) |
| ruff + mypy (api: 151 tệp; browser: 13 tệp) | xanh |
| `renovate-config-validator --strict renovate.json` (renovate 44.132.5) | "Config validated successfully"; khoá lạ bị từ chối (thử bằng tệp có khoá sai) |
| `cd apps/genh && go vet ./... && go test -count=1 ./...` | xanh, gồm 3 test mới (TestDeployCompose_/TestEmbeddedCompose_EveryImagePinnedByDigest, TestImagePinErrors_RejectsTagOnly) |
| `cmp deploy/compose.yaml apps/genh/internal/compose/embedded_compose.yaml` | trùng từng byte |
| pytest api (Postgres 16 + Redis cục bộ) | 1813 passed, 3 deselected (slow) — lượt thường; lượt GH_TEST_APP_ROLE=1 cũng 1813 passed, 3 deselected |
| pytest browser (Chromium thật) | 43 passed |
| Build ảnh bằng Docker | CHƯA chạy được ở môi trường thi công (proxy chặn blob ghcr.io, apt và PyPI trong container) — CI (build-images, e2e cài thật) quyết |

## Ghi chú môi trường

Môi trường thi công không có đường ra ghcr.io/PyPI/apt từ trong container Docker, nên không build được ảnh thật; đã kiểm riêng
cú pháp `RUN --mount=from=...` được buildx chấp nhận (tới bước tải wheel thì dừng vì TLS proxy).
Hợp đồng cho pipeline-ci: ảnh api có `python` = /opt/venv/bin/python (gói từ uv.lock + gen-harness-api); ảnh browser có `python3` = /opt/venv/bin/python3.
